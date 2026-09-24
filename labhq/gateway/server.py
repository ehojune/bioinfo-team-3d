"""Gateway: the one reachable endpoint. Runners dial in; desktop/phone clients connect here.

Run it on a tiny VM or at home behind Tailscale. It holds no data, only events and routing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ..integrations.github import ProjectReporter
from ..models import ApprovalRequest, Task, TaskResult, new_id
from ..orchestrator.cso import Orchestrator
from ..settings import Settings

WEB = Path(__file__).resolve().parents[1] / "web"


class RequestIn(BaseModel):
    text: str
    mode: str = "orchestrate"  # orchestrate | direct
    agent_id: str | None = None
    project_dirs: list[str] = []
    budget_usd: float | None = None
    project_id: str | None = None  # → updates go to that project's GitHub repo


class CodexReviewIn(BaseModel):
    note: str = ""


class DecisionIn(BaseModel):
    approved: bool
    note: str = ""


class RecruitIn(BaseModel):
    paper: str | None = None
    repo: str | None = None
    focus: str | None = None
    ttl_days: float | None = None
    name: str | None = None
    request_id: str | None = None


class ContractIn(BaseModel):
    action: str  # extend | release | activate | rehire
    days: float | None = None
    slug: str | None = None


class Hub:
    def __init__(self, settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.runners: dict[str, WebSocket] = {}
        self.runner_locks: dict[str, asyncio.Lock] = {}
        self.runner_agents: dict[str, list[dict]] = {}
        self.agents: dict[str, dict] = {}
        self.agent_runner: dict[str, str] = {}
        self.task_runner: dict[str, str] = {}
        self.clients: set[WebSocket] = set()
        self.futures: dict[str, asyncio.Future] = {}
        self.jobs_waiters: dict[str, asyncio.Future] = {}
        self.jobs_done: dict[str, dict] = {}
        self.approvals: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}
        self.events: deque = deque(maxlen=settings.gateway.event_buffer)
        self.orchestrator = Orchestrator(self)
        self.reporter = ProjectReporter(self, settings, github_transport)

    # ----- runners -----
    def register_runner(self, runner_id: str, ws: WebSocket, agents: list[dict]) -> None:
        self.runners[runner_id] = ws
        self.runner_locks.setdefault(runner_id, asyncio.Lock())
        self.set_roster(runner_id, agents)

    def set_roster(self, runner_id: str, agents: list[dict]) -> None:
        for aid in [a for a, r in self.agent_runner.items() if r == runner_id]:
            self.agent_runner.pop(aid, None)
            self.agents.pop(aid, None)
        self.runner_agents[runner_id] = agents
        for a in agents:
            self.agents[a["id"]] = {**a, "runner_id": runner_id}
            self.agent_runner[a["id"]] = runner_id

    def unregister_runner(self, runner_id: str, ws: WebSocket) -> None:
        if self.runners.get(runner_id) is ws:
            self.runners.pop(runner_id, None)  # keep roster + pending futures: the runner will reconnect

    async def send_runner(self, runner_id: str, msg: dict) -> None:
        ws = self.runners.get(runner_id)
        if ws is None:
            raise RuntimeError(f"runner {runner_id} is offline")
        async with self.runner_locks[runner_id]:
            await ws.send_text(json.dumps(msg, ensure_ascii=False, default=str))

    def supports_resume(self, agent_id: str) -> bool:
        return self.agents.get(agent_id, {}).get("engine") != "gemini"

    # ----- events -----
    async def publish(self, ev: dict) -> None:
        self.events.append(ev)
        dead = []
        for c in list(self.clients):
            try:
                await c.send_text(json.dumps(ev, ensure_ascii=False, default=str))
            except Exception:
                dead.append(c)
        for c in dead:
            self.clients.discard(c)
        if self.reporter.enabled():
            self.reporter.submit(ev)

    async def on_runner_message(self, runner_id: str, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "runner.roster":
            self.set_roster(runner_id, msg.get("agents", []))
            await self.publish({"type": "roster.updated", "ts": time.time(), "data": {"agents": list(self.agents.values())}})
            return
        if typ == "task.result":
            fut = self.futures.pop(msg.get("task_id") or "", None)
            if fut and not fut.done():
                fut.set_result(TaskResult.model_validate(msg["data"]))
        elif typ == "approval.requested":
            a = msg["data"]
            self.approvals[a["id"]] = {"approval": a, "origin": runner_id}
        elif typ == "jobs.finished":
            tid = msg.get("task_id") or ""
            fut = self.jobs_waiters.pop(tid, None)
            if fut and not fut.done():
                fut.set_result(msg["data"])
            else:
                self.jobs_done[tid] = msg["data"]  # the waiter may register a moment later
        await self.publish(msg)

    # ----- tasks -----
    async def dispatch(self, task: Task) -> TaskResult:
        rid = self.agent_runner.get(task.agent_id)
        if not rid:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error=f"no runner hosts agent {task.agent_id!r}")
        fut = asyncio.get_running_loop().create_future()
        self.futures[task.id] = fut
        self.task_runner[task.id] = rid
        await self.publish({"type": "task.dispatched", "ts": time.time(), "task_id": task.id,
                            "agent_id": task.agent_id, "request_id": task.request_id,
                            "data": {"kind": task.meta.get("kind"), "step_id": task.meta.get("step_id"),
                                     "title": task.meta.get("title"), "prompt": task.prompt[:300]}})
        await self.send_runner(rid, {"type": "task.dispatch", "task": task.model_dump(mode="json")})
        return await fut

    async def wait_jobs(self, task_id: str) -> dict:
        if task_id in self.jobs_done:
            return self.jobs_done.pop(task_id)
        fut = self.jobs_waiters.setdefault(task_id, asyncio.get_running_loop().create_future())
        return await fut

    # ----- approvals -----
    async def request_approval(self, kind: str, summary: str, request_id: str | None = None,
                               detail: dict | None = None, timeout_s: int | None = None) -> dict:
        req = ApprovalRequest(kind=kind, summary=summary, request_id=request_id, detail=detail or {},
                              timeout_s=timeout_s or self.s.policy.approvals.timeout_s)
        fut = asyncio.get_running_loop().create_future()
        self.approvals[req.id] = {"approval": req.model_dump(mode="json"), "origin": None, "future": fut}
        await self.publish({"type": "approval.requested", "ts": time.time(), "request_id": request_id,
                            "data": req.model_dump(mode="json")})
        try:
            return await asyncio.wait_for(fut, req.timeout_s)
        except asyncio.TimeoutError:
            self.approvals.pop(req.id, None)
            return {"approved": False, "note": "timed out"}

    async def resolve_approval(self, approval_id: str, approved: bool, note: str = "") -> None:
        entry = self.approvals.pop(approval_id, None)
        if entry is None:
            raise KeyError(approval_id)
        if entry["origin"]:
            await self.send_runner(entry["origin"], {"type": "approval.resolved", "id": approval_id,
                                                     "approved": approved, "note": note})
        elif entry.get("future") and not entry["future"].done():
            entry["future"].set_result({"approved": approved, "note": note})
        a = entry["approval"]
        await self.publish({"type": "approval.resolved", "ts": time.time(), "task_id": a.get("task_id"),
                            "agent_id": a.get("agent_id"), "request_id": a.get("request_id"),
                            "data": {"id": approval_id, "approved": approved, "note": note}})

    # ----- requests -----
    def create_request(self, body: RequestIn) -> str:
        rid = new_id("req")
        req = {"id": rid, "status": "running", "created_at": time.time(), **body.model_dump()}
        proj = self.s.project(body.project_id)
        if body.project_id and proj is None:
            raise KeyError(f"unknown project {body.project_id!r}")
        if proj and proj.local_dir and proj.local_dir not in req["project_dirs"]:
            req["project_dirs"] = [*req["project_dirs"], proj.local_dir]  # agents work in the project clone
        self.requests[rid] = req
        asyncio.get_running_loop().create_task(self._start_request(rid))
        return rid

    async def _start_request(self, rid: str) -> None:
        r = self.requests[rid]
        await self.publish({"type": "request.created", "ts": time.time(), "request_id": rid,
                            "data": {k: r.get(k) for k in ("text", "mode", "agent_id", "project_id")}})
        await self.orchestrator.run_request(rid)

    def snapshot(self) -> dict[str, Any]:
        return {"type": "snapshot", "ts": time.time(), "data": {
            "agents": list(self.agents.values()),
            "runners": list(self.runners),
            "approvals": [e["approval"] for e in self.approvals.values()],
            "requests": [{k: v for k, v in r.items() if k in ("id", "text", "status", "mode", "created_at",
                                                              "project_id", "plan", "cost_usd", "agent_id")}
                         for r in self.requests.values()],
            "projects": [{"id": p.id, "name": p.name or p.id, "repo": p.repo, "visibility": p.visibility}
                         for p in self.s.projects],
            "recent_events": list(self.events)[-200:],
        }}


def create_app(settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    hub = Hub(settings, github_transport)
    app = FastAPI(title="labhq gateway", version="0.1.0")
    app.state.hub = hub

    def auth(authorization: str = Header(default="")) -> None:
        if authorization.removeprefix("Bearer ").strip() != settings.gateway.client_token:
            raise HTTPException(401, "bad client token")

    @app.websocket("/ws/runner")
    async def ws_runner(ws: WebSocket, token: str = "") -> None:
        if token != settings.gateway.runner_token:
            await ws.close(code=1008)
            return
        await ws.accept()
        runner_id = None
        try:
            hello = json.loads(await ws.receive_text())
            runner_id = hello["runner_id"]
            hub.register_runner(runner_id, ws, hello.get("agents", []))
            await hub.publish({"type": "runner.online", "ts": time.time(),
                               "data": {"runner_id": runner_id, "agents": len(hello.get("agents", []))}})
            while True:
                await hub.on_runner_message(runner_id, json.loads(await ws.receive_text()))
        except WebSocketDisconnect:
            pass
        finally:
            if runner_id:
                hub.unregister_runner(runner_id, ws)
                await hub.publish({"type": "runner.offline", "ts": time.time(), "data": {"runner_id": runner_id}})

    @app.websocket("/ws/client")
    async def ws_client(ws: WebSocket, token: str = "") -> None:
        if token != settings.gateway.client_token:
            await ws.close(code=1008)
            return
        await ws.accept()
        await ws.send_text(json.dumps(hub.snapshot(), ensure_ascii=False, default=str))
        hub.clients.add(ws)
        try:
            while True:
                try:
                    msg = json.loads(await ws.receive_text())
                except ValueError:
                    continue
                if msg.get("type") == "approval.resolve":  # phone taps 승인/거절
                    try:
                        await hub.resolve_approval(str(msg.get("id")), bool(msg.get("approved")), msg.get("note", ""))
                    except KeyError:  # already decided elsewhere (another device, timeout) — keep the socket
                        await ws.send_text(json.dumps({"type": "approval.stale", "ts": time.time(),
                                                       "data": {"id": msg.get("id")}}))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    @app.get("/api/agents", dependencies=[Depends(auth)])
    async def agents() -> list[dict]:
        return list(hub.agents.values())

    @app.post("/api/requests", dependencies=[Depends(auth)])
    async def create_request(body: RequestIn) -> dict:
        if body.mode == "direct" and body.agent_id not in hub.agents:
            raise HTTPException(404, f"unknown agent {body.agent_id!r}")
        try:
            return {"request_id": hub.create_request(body)}
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/projects", dependencies=[Depends(auth)])
    async def projects() -> list[dict]:
        return [p.model_dump(exclude={"local_dir"}) for p in settings.projects]

    @app.post("/api/projects/{pid}/prs/{number}/codex-review", dependencies=[Depends(auth)])
    async def codex_review(pid: str, number: int, body: CodexReviewIn) -> dict:
        proj = settings.project(pid)
        gh = hub.reporter.client()
        if not proj or not proj.repo or gh is None:
            raise HTTPException(409, "project has no repo or GitHub token is not set")
        c = await gh.request_codex_review(proj.repo, number, body.note, settings.github.codex_mention)
        return {"ok": True, "url": c.get("html_url")}

    @app.get("/api/requests/{rid}", dependencies=[Depends(auth)])
    async def get_request(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        return hub.requests[rid]

    @app.get("/api/approvals", dependencies=[Depends(auth)])
    async def approvals() -> list[dict]:
        return [e["approval"] for e in hub.approvals.values()]

    @app.post("/api/approvals/{aid}", dependencies=[Depends(auth)])
    async def decide(aid: str, body: DecisionIn) -> dict:
        try:
            await hub.resolve_approval(aid, body.approved, body.note)
        except KeyError:
            raise HTTPException(404, "no such pending approval")
        return {"ok": True}

    @app.post("/api/tasks/{tid}/cancel", dependencies=[Depends(auth)])
    async def cancel(tid: str) -> dict:
        rid = hub.task_runner.get(tid)
        if not rid:
            raise HTTPException(404)
        await hub.send_runner(rid, {"type": "task.cancel", "task_id": tid})
        return {"ok": True}

    @app.post("/api/recruit", dependencies=[Depends(auth)])
    async def recruit(body: RecruitIn) -> dict:
        if not (body.paper or body.repo):
            raise HTTPException(400, "paper or repo required")
        rid = hub.agent_runner.get(settings.recruit.agent_id)
        if not rid:
            raise HTTPException(409, f"no runner hosts the recruiter agent {settings.recruit.agent_id!r}")
        await hub.send_runner(rid, {"type": "recruit.start", **body.model_dump()})
        return {"ok": True, "runner_id": rid}

    @app.post("/api/contracts/{agent_id}", dependencies=[Depends(auth)])
    async def contract(agent_id: str, body: ContractIn) -> dict:
        rid = hub.agent_runner.get(agent_id) or hub.agent_runner.get(settings.recruit.agent_id)
        if not rid:
            raise HTTPException(404)
        await hub.send_runner(rid, {"type": "contract.update", "agent_id": agent_id, **body.model_dump()})
        return {"ok": True}

    @app.get("/api/events", dependencies=[Depends(auth)])
    async def events(limit: int = 200) -> list[dict]:
        return list(hub.events)[-limit:]

    @app.get("/api/health")
    async def health() -> dict:
        return {"service": "labhq gateway", "runners": list(hub.runners), "agents": len(hub.agents)}

    @app.get("/", response_class=HTMLResponse)
    async def office() -> HTMLResponse:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        boot = '<script>window.LABHQ_BOOT={"mode":"live"}</script>'
        return HTMLResponse(html.replace("<!--LABHQ_BOOT-->", boot), headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    async def manifest() -> Response:
        return Response((WEB / "manifest.webmanifest").read_text(encoding="utf-8"),
                        media_type="application/manifest+json")

    @app.get("/icon.svg")
    async def icon() -> Response:
        return Response((WEB / "icon.svg").read_text(encoding="utf-8"), media_type="image/svg+xml")

    return app
