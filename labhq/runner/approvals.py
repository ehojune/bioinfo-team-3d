"""Local broker (127.0.0.1) between per-task MCP servers and the runner.

MCP tools (hpc_submit, approval_prompt) POST here and block until the PI answers on the phone.
The runner relays approval.requested → gateway → clients, and approval.resolved back here.

A task token is held by the agent's own process, so every body is a claim. The broker sets identity and ids
itself, passes on log lines only, and keeps no job record from a caller: jobs are submitted by the runner
(/jobs/submit), which records the id its own scheduler call returned.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from ..models import ASK_WAIT_SECONDS, ApprovalRequest, AskRequest, hard_stop_kind
from ..ask_results import ask_result, read_ask_results
from ..security import token_matches

Handler = Callable[[Any], Awaitable[None]]
Submit = Callable[[dict], Awaitable[dict]]
Predicate = Callable[[dict], Awaitable[bool]]


class Broker:
    def __init__(self, port: int, on_approval: Handler, on_event: Handler, on_submit: Submit,
                 on_ask: Handler | None = None, owns_job: Predicate | None = None,
                 on_approval_timeout: Handler | None = None):
        self.port = port
        self.pending: dict[str, asyncio.Future] = {}
        self.pending_asks: dict[str, asyncio.Future] = {}
        self.hibernate_asks: dict[str, str] = {}
        self.task_ask_results: dict[str, list[dict]] = {}
        self.identities: dict[str, dict[str, str | None]] = {}
        self._on_approval, self._on_event, self._on_submit = on_approval, on_event, on_submit
        self._on_ask = on_ask
        self._owns_job = owns_job  # without it no job counts as owned, so hpc_cancel refuses all
        self._on_approval_timeout = on_approval_timeout
        self.server: uvicorn.Server | None = None
        self.app = self._build_app()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def issue_task_token(self, task_id: str, agent_id: str, request_id: str | None, *,
                         workdir: str | None = None) -> str:
        token = secrets.token_urlsafe(24)
        self.identities[token] = {"task_id": task_id, "agent_id": agent_id, "request_id": request_id,
                                  "source_workdir": workdir}
        return token

    def revoke_task_token(self, token: str) -> None:
        self.identities.pop(token, None)

    def _identity(self, token: str, body: dict) -> dict[str, str | None]:
        identity = next((value for key, value in self.identities.items() if token_matches(token, key)), None)
        if identity is None:
            raise HTTPException(401, "bad broker token")
        for field in ("task_id", "agent_id", "request_id"):
            supplied = body.get(field)
            if supplied is not None and supplied != identity[field]:
                raise HTTPException(403, f"broker token does not grant {field}")
        # Approval and ask ids key the runner's pending futures: the broker makes them, a caller cannot pick one.
        return {**{key: value for key, value in body.items() if key != "id"}, **identity}

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="labhq-broker")

        @app.post("/approval")
        async def approval(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            return await self.request_approval(ApprovalRequest.model_validate(
                self._identity(x_labhq_token, body)))

        @app.post("/ask")
        async def ask(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            return await self.request_ask(AskRequest.model_validate(self._identity(x_labhq_token, body)))

        @app.post("/event")
        async def event(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            identity = self._identity(x_labhq_token, body)
            if identity.get("type", "agent.log") != "agent.log":
                # task.result, jobs.finished, approval.* … are the runner's facts, never a task's report.
                raise HTTPException(403, "task tokens may send agent.log events only")
            await self._on_event(identity)
            return {"ok": True}

        @app.post("/jobs/submit")
        async def submit(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            return await self._on_submit(self._identity(x_labhq_token, body))

        @app.post("/jobs/owned")
        async def owned(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            identity = self._identity(x_labhq_token, body)
            return {"owned": bool(self._owns_job and await self._owns_job(identity))}

        return app

    async def request_approval(self, req: ApprovalRequest) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self.pending[req.id] = fut
        try:
            await self._on_approval(req)
            return await asyncio.wait_for(fut, req.timeout_s)
        except asyncio.TimeoutError:
            if self._on_approval_timeout is not None:
                await self._on_approval_timeout(req)
            return {"approved": False, "note": "approval timed out", "state": "timed_out"}
        finally:
            self.pending.pop(req.id, None)

    async def request_ask(self, req: AskRequest) -> dict:
        def terminal(answer: dict) -> dict:
            result = ask_result(**{**answer, "ask_id": req.id})
            outcome = read_ask_results([result])
            self.task_ask_results.setdefault(req.task_id or "", []).append(result)
            return result if outcome["status"] == "answered" else {**result, "reason": outcome["reason"]}

        if self._on_ask is None:
            return terminal(ask_result(reason="ask routing is unavailable"))
        fut = asyncio.get_running_loop().create_future()
        self.pending_asks[req.id] = fut
        await self._on_ask(req)
        target = "colleague" if req.to.startswith("colleague:") else req.to
        if target == "pi" and hard_stop_kind(req) is None:
            target = "cso"
        wait_s = 0 if req.wait == "hibernate" else ASK_WAIT_SECONDS[target]
        if hard_stop_kind(req):
            wait_s = 0
        if wait_s <= 0:
            self.hibernate_asks[req.id] = req.task_id or ""
            self.pending_asks.pop(req.id, None)
            return {"status": "pending", "ask_id": req.id,
                    "instruction": "Turn을 끝내세요. 답이 오면 같은 session으로 resume합니다."}
        try:
            answer = await asyncio.wait_for(fut, wait_s)
            return terminal(answer)
        except asyncio.TimeoutError:
            self.hibernate_asks[req.id] = req.task_id or ""
            return {"status": "pending", "ask_id": req.id,
                    "instruction": "대기 상한을 넘었습니다. Turn을 끝내면 답이 올 때 resume합니다."}
        finally:
            self.pending_asks.pop(req.id, None)

    def resolve(self, approval_id: str, approved: bool, note: str = "") -> bool:
        fut = self.pending.get(approval_id)
        if fut and not fut.done():
            fut.set_result({"approved": approved, "note": note})
            return True
        return False

    def resolve_ask(self, ask_id: str, answer: dict) -> bool:
        fut = self.pending_asks.get(ask_id)
        if fut and not fut.done():
            fut.set_result(answer)
            return True
        return ask_id in self.hibernate_asks

    def pending_for_task(self, task_id: str) -> list[str]:
        return [ask_id for ask_id, owner in self.hibernate_asks.items() if owner == task_id]

    def finish_task(self, task_id: str) -> None:
        self.task_ask_results.pop(task_id, None)
        for ask_id in self.pending_for_task(task_id):
            self.hibernate_asks.pop(ask_id, None)

    async def serve(self) -> None:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        await self.server.serve()

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
