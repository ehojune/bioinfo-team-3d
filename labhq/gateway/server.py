"""Gateway: the one reachable endpoint. Runners dial in; desktop/phone clients connect here.

Run it on a tiny VM or at home behind Tailscale. State stays on the gateway host.
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
from ..orchestrator.cso import Orchestrator, SYNTH_PROMPT
from ..settings import Settings
from ..store import StateStore
from ..util import short

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


class SavedResults(dict):
    def __init__(self, hub: "Hub", rid: str):
        self.hub, self.rid = hub, rid
        super().__init__({k: TaskResult.model_validate(v)
                          for k, v in (hub.requests[rid].get("results") or {}).items()})

    def __setitem__(self, key: str, value: TaskResult) -> None:
        super().__setitem__(key, value)
        self.hub.requests[self.rid]["results"] = {k: v.model_dump(mode="json") for k, v in self.items()}
        self.hub.save_request(self.rid)


class Hub:
    def __init__(self, settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.store = StateStore(settings.path(settings.gateway.state_dir) / "gateway.sqlite3")
        self.event_lock = asyncio.Lock()
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
        self.approvals: dict[str, dict] = self.store.all("approval")
        self.requests: dict[str, dict] = self.store.all("request")
        self.events: deque = deque(maxlen=settings.gateway.event_buffer)
        self.events.extend(self.store.events_since(max(0, self.store.event_bounds()[1] - settings.gateway.event_buffer)))
        for rid, req in self.requests.items():
            if req.get("status") == "running":
                req["status"] = "interrupted"
                self.save_request(rid)
            if req.get("status") == "interrupted" and not any(
                a["approval"].get("kind") == "resume" and a["approval"].get("request_id") == rid
                for a in self.approvals.values()
            ):
                done = set(req.get("results") or {})
                steps = [s["id"] for s in req.get("plan", {}).get("steps", []) if s["id"] not in done]
                approval = ApprovalRequest(kind="resume", request_id=rid,
                                           summary=f"중단된 단계 {steps or ['요청']}를 다시 돌릴까요?")
                self.approvals[approval.id] = {"approval": approval.model_dump(mode="json"), "origin": None}
                self.save_approval(approval.id)
                self.events.append(self.store.append_event({"type": "approval.requested", "ts": time.time(),
                                                             "request_id": rid, "data": approval.model_dump(mode="json")},
                                                            settings.gateway.event_buffer))
        self.orchestrator = Orchestrator(self)
        self.reporter = ProjectReporter(self, settings, github_transport)

    def save_request(self, rid: str) -> None:
        self.store.put("request", rid, self.requests[rid])

    def result_map(self, rid: str) -> SavedResults:
        return SavedResults(self, rid)

    async def resume_request(self, rid: str) -> None:
        req = self.requests[rid]
        if req.get("mode") == "direct" or not req.get("plan", {}).get("steps"):
            await self.orchestrator.run_request(rid)
            return
        try:
            steps = req["plan"]["steps"]
            results = self.result_map(rid)
            self.orchestrator.cost[rid] = float(req.get("cost_usd") or 0)
            remaining = {s["id"] for s in steps} - set(results)
            if remaining:
                await self.orchestrator.run_dag(rid, req["text"], steps, results, only=remaining)
            final = await self.orchestrator.run_step(Task(
                agent_id=self.s.orchestrator.cso_agent, request_id=rid,
                prompt=SYNTH_PROMPT.format(
                    request=req["text"],
                    results=self.orchestrator.format_results(
                        steps, results, self.s.orchestrator.context_chars_per_step),
                    review=short(req.get("review") or {}, 3000)),
                meta={"kind": "synthesis", "request": req["text"], "title": "최종 보고서 작성"}))
            self.orchestrator._finish(rid, final.text,
                                      {k: v.model_dump(mode="json") for k, v in results.items()},
                                      ok=final.ok, review=req.get("review"))
        except Exception as e:
            req.update(status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            self.save_request(rid)
            await self.publish({"type": "request.failed", "ts": time.time(), "request_id": rid,
                                "data": {"error": req["error"]}})

    def save_approval(self, aid: str) -> None:
        entry = self.approvals[aid]
        self.store.put("approval", aid, {k: v for k, v in entry.items() if k != "future"})

    # ----- runners -----
    def register_runner(self, runner_id: str, ws: WebSocket, agents: list[dict]) -> None:
        self.runners[runner_id] = ws
        self.runner_locks.setdefault(runner_id, asyncio.Lock())
        self.set_roster(runner_id, agents)

    async def flush_decisions(self, runner_id: str) -> None:
        for aid, entry in self.store.all("decision").items():
            if entry["origin"] == runner_id:
                await self.send_runner(runner_id, {"type": "approval.resolved", "id": aid,
                                                   "approved": entry["approved"], "note": entry["note"]})

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
    async def publish(self, ev: dict, runner_id: str | None = None, runner_seq: int | None = None) -> None:
        async with self.event_lock:
            ev = self.store.append_event(ev, self.s.gateway.event_buffer, runner_id, runner_seq)
            self.events.append(ev)
            dead = []
            for c in list(self.clients):
                try:
                    await c.send_text(json.dumps(ev, ensure_ascii=False, default=str))
                except Exception:
                    dead.append(c)
            for c in dead:
                self.clients.discard(c)
            rid = ev.get("request_id")
            if rid in self.requests:
                self.save_request(rid)
        if self.reporter.enabled():
            self.reporter.submit(ev)

    async def on_runner_message(self, runner_id: str, msg: dict) -> None:
        runner_seq = msg.get("runner_seq")
        if runner_seq is not None and runner_seq <= self.store.runner_seen(runner_id):
            await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})
            return
        typ = msg.get("type")
        if typ == "runner.roster":
            self.set_roster(runner_id, msg.get("agents", []))
            await self.publish({"type": "roster.updated", "ts": time.time(), "data": {"agents": list(self.agents.values())}})
            return
        if typ == "approval.ack":
            self.store.delete("decision", str(msg["id"]))
        if typ == "task.result":
            fut = self.futures.pop(msg.get("task_id") or "", None)
            if fut and not fut.done():
                fut.set_result(TaskResult.model_validate(msg["data"]))
        elif typ == "approval.requested":
            a = msg["data"]
            self.approvals[a["id"]] = {"approval": a, "origin": runner_id}
            self.save_approval(a["id"])
        elif typ == "jobs.finished":
            tid = msg.get("task_id") or ""
            fut = self.jobs_waiters.pop(tid, None)
            if fut and not fut.done():
                fut.set_result(msg["data"])
            else:
                self.jobs_done[tid] = msg["data"]  # the waiter may register a moment later
        await self.publish(msg, runner_id=runner_id, runner_seq=runner_seq)
        if runner_seq is not None:
            await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})

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
        self.save_approval(req.id)
        await self.publish({"type": "approval.requested", "ts": time.time(), "request_id": request_id,
                            "data": req.model_dump(mode="json")})
        try:
            return await asyncio.wait_for(fut, req.timeout_s)
        except asyncio.TimeoutError:
            self.approvals.pop(req.id, None)
            self.store.delete("approval", req.id)
            return {"approved": False, "note": "timed out"}

    async def resolve_approval(self, approval_id: str, approved: bool, note: str = "") -> None:
        entry = self.approvals.pop(approval_id, None)
        if entry is None:
            raise KeyError(approval_id)
        self.store.delete("approval", approval_id)
        self.store.put("approval_decision", approval_id,
                       {"approval": entry["approval"], "approved": approved,
                        "note": note, "decided_at": time.time()})
        if entry["origin"]:
            self.store.put("decision", approval_id, {"origin": entry["origin"],
                                                      "approved": approved, "note": note})
            if entry["origin"] in self.runners:
                await self.flush_decisions(entry["origin"])
        elif entry.get("future") and not entry["future"].done():
            entry["future"].set_result({"approved": approved, "note": note})
        elif entry["approval"].get("kind") == "resume":
            rid = entry["approval"]["request_id"]
            if approved:
                self.requests[rid]["status"] = "running"
                self.save_request(rid)
                asyncio.get_running_loop().create_task(self.resume_request(rid))
            else:
                self.requests[rid].update(status="failed", error="resume declined", finished_at=time.time())
                self.save_request(rid)
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
        self.save_request(rid)
        asyncio.get_running_loop().create_task(self._start_request(rid))
        return rid

    async def _start_request(self, rid: str) -> None:
        r = self.requests[rid]
        await self.publish({"type": "request.created", "ts": time.time(), "request_id": rid,
                            "data": {k: r.get(k) for k in ("text", "mode", "agent_id", "project_id")}})
        await self.orchestrator.run_request(rid)

    def snapshot(self) -> dict[str, Any]:
        return {"type": "snapshot", "schema_version": 1, "seq": self.store.event_bounds()[1],
                "ts": time.time(), "data": {
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
            await hub.flush_decisions(runner_id)
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
    async def ws_client(ws: WebSocket, token: str = "", since: int | None = None) -> None:
        if token != settings.gateway.client_token:
            await ws.close(code=1008)
            return
        await ws.accept()
        async with hub.event_lock:
            oldest, latest = hub.store.event_bounds()
            if since is None or since < oldest - 1 or since > latest:
                snap = hub.snapshot()
                if since is not None:
                    snap["replay_gap"] = {"requested_since": since, "oldest_seq": oldest}
                await ws.send_text(json.dumps(snap, ensure_ascii=False, default=str))
            else:
                for ev in hub.store.events_since(since):
                    await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))
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
                        await hub.publish({"type": "approval.stale", "ts": time.time(),
                                           "data": {"id": msg.get("id")}})
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
    async def events(limit: int = 200, since: int | None = None) -> list[dict]:
        if since is None:
            return list(hub.events)[-limit:]
        oldest, latest = hub.store.event_bounds()
        if since < oldest - 1 or since > latest:
            snap = hub.snapshot()
            snap["replay_gap"] = {"requested_since": since, "oldest_seq": oldest}
            return [snap]
        return hub.store.events_since(since, max(1, limit))

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
