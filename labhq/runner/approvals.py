"""Local broker (127.0.0.1) between per-task MCP servers and the runner.

MCP tools (hpc_submit, approval_prompt) POST here and block until the PI answers on the phone.
The runner relays approval.requested → gateway → clients, and approval.resolved back here.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from ..models import ApprovalRequest

Handler = Callable[[Any], Awaitable[None]]


class Broker:
    def __init__(self, port: int, on_approval: Handler, on_event: Handler, on_track: Handler):
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.pending: dict[str, asyncio.Future] = {}
        self._on_approval, self._on_event, self._on_track = on_approval, on_event, on_track
        self.server: uvicorn.Server | None = None
        self.app = self._build_app()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _check(self, token: str) -> None:
        if not secrets.compare_digest(token or "", self.token):
            raise HTTPException(401, "bad broker token")

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="labhq-broker")

        @app.post("/approval")
        async def approval(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            self._check(x_labhq_token)
            return await self.request_approval(ApprovalRequest.model_validate(body))

        @app.post("/event")
        async def event(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            self._check(x_labhq_token)
            await self._on_event(body)
            return {"ok": True}

        @app.post("/jobs/track")
        async def track(body: dict, x_labhq_token: str = Header(default="")) -> dict:
            self._check(x_labhq_token)
            await self._on_track(body)
            return {"ok": True}

        return app

    async def request_approval(self, req: ApprovalRequest) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self.pending[req.id] = fut
        try:
            await self._on_approval(req)
            return await asyncio.wait_for(fut, req.timeout_s)
        except asyncio.TimeoutError:
            return {"approved": False, "note": "approval timed out"}
        finally:
            self.pending.pop(req.id, None)

    def resolve(self, approval_id: str, approved: bool, note: str = "") -> bool:
        fut = self.pending.get(approval_id)
        if fut and not fut.done():
            fut.set_result({"approved": approved, "note": note})
            return True
        return False

    async def serve(self) -> None:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        await self.server.serve()

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
