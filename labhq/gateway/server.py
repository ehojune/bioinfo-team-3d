"""Gateway: the one reachable endpoint. Runners dial in; desktop/phone clients connect here.

Run it on a tiny VM or at home behind Tailscale. State stays on the gateway host.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Request, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

from ..integrations.github import ProjectReporter
from ..intake import MAX_REFERENCES, Reference, effective_references
from ..integrations.rounds import RoundRecorder, environment_snapshot
from ..ask_results import ask_result, read_ask_results, rejected_step
from ..models import ApprovalRequest, AskRequest, RunnerUnavailable, Task, TaskResult, new_id, waiting
from ..adapters import get_adapter, read_only_refusal
from ..orchestrator.cso import Orchestrator, holds_session
from ..research.packs import check_configured_packs
from ..settings import Settings
from ..security import token_matches
from ..store import StateStore
from ..util import short

log = logging.getLogger(__name__)
TERMINAL_REQUEST_STATES = {"done", "failed", "cancelled", "rejected"}
# #126: a snapshot goes to every client on each connect, so a long follow-up answer travels as its head only.
# The full answer stays on the request (GET /api/requests/{id}); the web loads it when the PI opens it.
SNAPSHOT_ANSWER_CHARS = 2000
# A step card shows this much of a task's result text; a replayed task.result needs no more.
SNAPSHOT_RESULT_CHARS = 500


def snapshot_followup(entry: dict) -> dict:
    """The snapshot copy of a follow-up or of its `request.followup_done` data: one rule for both (#126)."""
    answer = entry.get("answer")
    if not isinstance(answer, str) or len(answer) <= SNAPSHOT_ANSWER_CHARS:
        return entry
    return {**entry, "answer": answer[:SNAPSHOT_ANSWER_CHARS], "answer_truncated": True, "answer_chars": len(answer)}


def snapshot_event(event: dict) -> dict:
    """The snapshot copy of a replayed event. A follow-up's answer is also the text of its task's `task.result`,
    sent by the runner unclipped, so that text is cut too. A replay never shows it whole: the web keeps
    task.result text only for plan steps, and the snapshot's `step_details` replace it with the same head."""
    data = event.get("data")
    if not isinstance(data, dict):
        return event
    if event.get("type") == "request.followup_done":
        return {**event, "data": snapshot_followup(data)}
    text = data.get("text")
    if event.get("type") == "task.result" and isinstance(text, str) and len(text) > SNAPSHOT_RESULT_CHARS:
        return {**event, "data": {**data, "text": text[:SNAPSHOT_RESULT_CHARS], "text_truncated": True,
                                  "text_chars": len(text)}}
    return event


def _semantics_wanted(raw: Any) -> bool:  # semantics-hook: off in any spelling, options or not, skips the import
    mode = raw.get("mode", "off") if isinstance(raw, dict) else raw  # semantics-hook
    return mode not in (None, False, "off")  # semantics-hook


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

WEB = Path(__file__).resolve().parents[1] / "web"


class RequestIn(BaseModel):
    text: str
    mode: Literal["orchestrate", "direct", "plan_only"] = "orchestrate"
    agent_id: str | None = None
    work_kind: Literal["auto", "simple", "research"] = "auto"
    scope_status: Literal["in_scope", "needs_pi_confirmation"] = "in_scope"
    project_dirs: list[str] = []
    references: list[Reference] = Field(default_factory=list, max_length=MAX_REFERENCES)  # pointers only (#36)
    default_references: bool = True  # add pi_profile.references
    budget_usd: float | None = None
    project_id: str | None = None  # → updates go to that project's GitHub repo
    meta: dict[str, str] = {}  # benchmark case id 등 요청 출처


class FollowupIn(BaseModel):
    text: str = Field(max_length=4000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("follow-up text is empty")
        return value.strip()


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
        req = self.hub.requests[self.rid]
        req.setdefault("results", {})[key] = value.model_dump(mode="json")
        req.get("pending_revisions", {}).pop(key, None)
        self.hub.save_request(self.rid)
        self.hub.clear_step_jobs(self.rid, key)

    def pop(self, key: str, *default):
        """Forget a result durably too (a blocked step re-runs after a restart instead of counting as done)."""
        value = super().pop(key, *default)
        if (self.hub.requests[self.rid].get("results") or {}).pop(key, None) is not None:
            self.hub.save_request(self.rid)
        return value

    def __delitem__(self, key: str) -> None:
        self.pop(key)


class Hub:
    def __init__(self, settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.store = StateStore(settings.path(settings.gateway.state_dir) / "gateway.sqlite3")
        self.event_lock = asyncio.Lock()
        self.runners: dict[str, WebSocket] = {}
        self.runner_incarnations: dict[str, str | None] = {}
        self.runner_locks: dict[str, asyncio.Lock] = {}
        self.runner_agents: dict[str, list[dict]] = {}
        self.runner_capabilities: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        self.agent_runner: dict[str, str] = {}
        self.agent_online: dict[str, asyncio.Event] = {}
        self.task_runner: dict[str, str] = {}
        self.clients: set[WebSocket] = set()
        self.futures: dict[str, asyncio.Future] = {}
        self.jobs_waiters: dict[str, asyncio.Future] = {}
        self.jobs_done: dict[str, dict] = self.store.all("jobs_done")
        self.ask_waiters: dict[str, asyncio.Future] = {}
        self.ask_tasks: dict[str, asyncio.Task] = {}
        self.terminal_queued: set[int] = set()
        self.pending_committed: deque[tuple[dict, tuple[WebSocket, ...]]] = deque()
        self.recovery_steps: set[str] = set()
        self.recovered_tasks: set[str] = set()
        self.approvals: dict[str, dict] = self.store.all("approval")
        self.requests: dict[str, dict] = self.store.all("request")
        self.events: deque = deque(maxlen=settings.gateway.event_buffer)
        self.events.extend(self.store.events_since(max(0, self.store.event_bounds()[1] - settings.gateway.event_buffer)))
        for aid, entry in list(self.approvals.items()):
            if entry.get("origin") is None and entry["approval"].get("kind") != "resume":
                self.approvals.pop(aid)
                self.store.delete("approval", aid)
                self.store.put("approval_decision", aid, {"approval": entry["approval"],
                                                         "approved": False, "note": "gateway restarted",
                                                         "state": "expired", "decided_at": time.time()})
                self.events.append(self.store.append_event({"type": "approval.expired", "ts": time.time(),
                    "request_id": entry["approval"].get("request_id"), "data": {"id": aid}},
                    settings.gateway.event_buffer))
        for rid, req in self.requests.items():
            if req.get("status") in {"running", "waiting_for_runner"}:
                req["status"] = "interrupted"
                self.save_request(rid)
            stale = [f for f in req.get("followups") or [] if f.get("status") == "running"]
            for followup in stale:  # its task future died with the old process; the PI can ask again
                followup.update(status="interrupted", error="gateway restarted before the answer arrived")
            if stale:
                self.save_request(rid)
            if req.get("status") == "interrupted" and not any(
                a["approval"].get("kind") == "resume" and a["approval"].get("request_id") == rid
                for a in self.approvals.values()
            ):
                approval = self.new_resume_approval(rid)
                self.events.append(self.store.append_event({"type": "approval.requested", "ts": time.time(),
                                                             "request_id": rid, "data": approval.model_dump(mode="json")},
                                                            settings.gateway.event_buffer))
        self.orchestrator = Orchestrator(self)
        self.reporter = ProjectReporter(self, settings, github_transport)
        self.rounds = RoundRecorder(self, github_transport)
        self.semantics_shadow = None  # semantics-hook
        if _semantics_wanted(getattr(settings, "semantics", None)):  # semantics-hook: any spelling of off never imports it
            from ..research.semantics_shadow import ShadowService  # semantics-hook
            self.semantics_shadow = ShadowService.start(self)  # semantics-hook
        for rid, req in self.requests.items():
            if req.get("status") == "interrupted":
                self.rounds.write(rid)

    def save_request(self, rid: str) -> None:
        self.requests[rid]["updated_at"] = time.time()
        self.store.put("request", rid, self.requests[rid])

    def running_tasks(self) -> list[dict]:
        """Accepted tasks of running requests, including steps waiting for jobs or ask answers."""
        selected: dict[tuple[str, str], tuple[tuple[int, float], dict]] = {}
        for tid, entry in self.store.all("task").items():
            req = self.requests.get(entry.get("request_id"), {})
            if not entry.get("accepted") or req.get("status") != "running":
                continue
            if not entry.get("completed"):
                state = "running"
            elif (waiting(entry.get("result") or {}, jobs_finished=tid in self.jobs_done) and
                  (entry.get("step_id") or entry.get("kind")) not in (req.get("results") or {})):
                state = "hibernating"
            else:
                continue
            step_id = entry.get("step_id") or entry.get("kind")
            task = {"id": tid, "request_id": entry.get("request_id"), "state": state,
                    "step_id": step_id, "agent_id": (entry.get("payload") or {}).get("agent_id"),
                    "dispatched_at": entry.get("dispatched_at", 0)}
            key = (task["request_id"], step_id)
            rank = (1 if state == "running" else 0, float(task["dispatched_at"] or 0))
            if key not in selected or rank > selected[key][0]:
                selected[key] = (rank, task)
        return [selected[key][1] for key in sorted(selected)]

    def request_summary(self, req: dict) -> dict:
        rid = req["id"]
        steps = (req.get("plan") or {}).get("steps") or []
        states = {step["id"]: "pending" for step in steps}
        if req.get("mode") == "direct":
            states["direct"] = "pending"
        states.update({sid: outcome.get("status") or ("done" if outcome.get("ok") else "failed")
                       for sid, outcome in (req.get("results") or {}).items()})
        for task in self.running_tasks():
            if task["request_id"] == rid:
                entry = self.store.get("task", task["id"]) or {}
                sid = entry.get("step_id") or entry.get("kind")
                if sid and states.get(sid, "pending") == "pending":
                    states[sid] = task["state"]
        return {"id": rid, "status": req.get("status"), "text": short(req.get("text"), 120),
                "created_at": req.get("created_at"), "updated_at": req.get("updated_at", req.get("created_at")),
                "step_progress": {"done": sum(v in {"done", "failed", "skipped"} for v in states.values()),
                                  "total": len(steps) if steps else (1 if req.get("mode") == "direct" else 0),
                                  "steps": states}, "cost_usd": req.get("cost_usd", 0),
                "cost_known": req.get("cost_known", True), "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}

    def clear_step_jobs(self, rid: str, step_id: str) -> None:
        # A jobs.finished checkpoint remains useful until the resulting step is adopted.
        for tid, task in self.store.all("task").items():
            if task.get("request_id") == rid and (task.get("step_id") or task.get("kind")) == step_id:
                if tid in self.jobs_done:
                    self.store.delete("jobs_done", tid)
                    self.jobs_done.pop(tid, None)

    def commit_terminal(self, rid: str, typ: str, data: dict) -> None:
        event = self.store.commit_terminal(rid, self.requests[rid],
                                           {"type": typ, "ts": time.time(), "request_id": rid, "data": data},
                                           self.s.gateway.event_buffer)
        self.events.append(event)
        self._restart_request_asks(rid)
        try:
            self.rounds.write(rid)
            self.rounds.submit(rid)
        except OSError as exc:
            log.warning("round record write failed for %s: %s", rid, exc)
        if self.requests[rid].get("mode") == "direct":
            self.clear_step_jobs(rid, "direct")
        # A client joining after this checkpoint replays it from SQLite.
        self.pending_committed.append((event, tuple(self.clients)))
        asyncio.get_running_loop().create_task(self._send_committed_event())
        self._queue_terminal_delivery(event)
        if getattr(self, "semantics_shadow", None) is not None:  # semantics-hook: after the request ended
            self.semantics_shadow.after_request(rid)  # semantics-hook: copies rows, queues, never raises

    def _queue_terminal_delivery(self, event: dict) -> None:
        seq = event["seq"]
        if seq in self.terminal_queued:
            return
        if self.reporter.enabled():
            self.terminal_queued.add(seq)
            self.reporter.submit(event)
        else:
            self.store.delete("terminal_delivery", str(seq))

    def recover_terminal_deliveries(self) -> None:
        self.rounds.recover()
        # The request row itself is the durable source for an issue that was not opened
        # before a crash. Queue it ahead of any terminal report for the same request.
        pending = sorted(self.store.all("reporter_delivery").values(), key=lambda event: event["seq"])
        pending_created = {event["request_id"] for event in pending if event["type"] == "request.created"}
        for rid, request in self.requests.items():
            project = self.s.project(request.get("project_id"))
            if (project and project.repo and project.issues and rid not in self.reporter.issues
                    and rid not in pending_created and
                    (project.visibility != "public" or project.allow_public_reports)):
                self.reporter.submit({"type": "request.created", "ts": request.get("created_at"),
                                       "request_id": rid, "data": {}})
        for event in pending:
            self.reporter.submit(event)
        for event in sorted(self.store.all("terminal_delivery").values(), key=lambda ev: ev["seq"]):
            self._queue_terminal_delivery(event)

    def ack_reporter_delivery(self, event: dict) -> None:
        seq = event.get("seq")
        if seq is not None:
            self.store.delete("terminal_delivery", str(seq))
            self.store.delete("reporter_delivery", str(seq))
            self.terminal_queued.discard(seq)

    async def _send_committed_event(self) -> None:
        async with self.event_lock:
            await self._flush_committed_events()

    async def _flush_committed_events(self) -> None:
        # Called under event_lock, before allocating any later live event sequence.
        while self.pending_committed:
            event, clients = self.pending_committed.popleft()
            dead = []
            for client in clients:
                try:
                    await client.send_text(json.dumps(event, ensure_ascii=False, default=str))
                except Exception:
                    dead.append(client)
            for client in dead:
                self.clients.discard(client)

    def result_map(self, rid: str) -> SavedResults:
        return SavedResults(self, rid)

    def recovery_attempt(self, task: Task) -> int:
        if task.request_id not in self.recovery_steps:
            return 1
        matches = [(tid, entry) for tid, entry in self.store.all("task").items()
                   if self._matches_recovery(task, entry) and tid not in self.recovered_tasks]
        return max((int(entry.get("attempt") or 1) for _, entry in matches), default=1)

    @staticmethod
    def _matches_recovery(task: Task, entry: dict) -> bool:
        kind = task.meta.get("kind")
        prior_meta = (entry.get("payload") or {}).get("meta") or {}
        entry_step = entry.get("step_id")
        if kind == "direct" and entry_step == "direct":  # records written before control-phase recovery
            entry_step = None
        return bool(kind and entry.get("request_id") == task.request_id and
                    entry.get("kind") == kind and entry_step == task.meta.get("step_id") and
                    int(entry.get("revision") or 0) == int(task.meta.get("revision") or 0) and
                    int(entry.get("parse_attempt", prior_meta.get("parse_attempt")) or 0) ==
                    int(task.meta.get("parse_attempt") or 0) and
                    entry.get("parent_task") == task.meta.get("parent_task") and
                    # A consult belongs to one ask; another ask's consult is not its prior attempt.
                    prior_meta.get("ask_id") == task.meta.get("ask_id"))

    @staticmethod
    def _same_runner_generation(entry: dict, runner_id: str | None, incarnation: str | None) -> bool:
        return bool(runner_id and incarnation and entry.get("runner_id") == runner_id and
                    entry.get("runner_incarnation") == incarnation)

    def new_resume_approval(self, rid: str) -> ApprovalRequest:
        req = self.requests[rid]
        done = set(req.get("results") or {})
        steps = [s["id"] for s in req.get("plan", {}).get("steps", []) if s["id"] not in done]
        approval = ApprovalRequest(kind="resume", request_id=rid,
                                   summary=f"중단된 단계 {steps or ['요청']}를 다시 돌릴까요?")
        self.approvals[approval.id] = {"approval": approval.model_dump(mode="json"), "origin": None}
        self.save_approval(approval.id)
        return approval

    def resume_agents(self, rid: str) -> set[str]:
        req = self.requests[rid]
        if req.get("mode") == "direct":
            return set() if self.completed_direct_result(rid) else {req["agent_id"]}
        steps = req.get("plan", {}).get("steps") or []
        done = set(req.get("results") or {})
        needed = {s["agent_id"] for s in steps if s["id"] not in done}
        phase = (req.get("review_progress") or {}).get("phase")
        if phase != "unresolved":
            needed.add(self.s.orchestrator.cso_agent)
        if self.s.orchestrator.reviewer_agent and phase not in {"synthesis", "unresolved"}:
            needed.add(self.s.orchestrator.reviewer_agent)
        if not steps and self.s.orchestrator.chief_of_staff_agent:
            needed.add(self.s.orchestrator.chief_of_staff_agent)
        return needed

    def completed_direct_result(self, rid: str) -> TaskResult | None:
        checkpoint = self.store.get("step_checkpoint", f"{rid}:direct")
        candidates = [entry.get("result") for entry in self.store.all("task").values()
                      if entry.get("request_id") == rid and entry.get("kind") == "direct" and
                      entry.get("completed")]
        if checkpoint:
            candidates.insert(0, checkpoint.get("result"))
        for body in candidates:
            if body:
                result = TaskResult.model_validate(body)
                outcome = read_ask_results(self.ask_results_for_task(result.task_id))
                if outcome["status"] == "rejected":
                    return rejected_step(result, outcome["reason"])
                if result.ok and not waiting(result):
                    return result
        return None

    async def resume_when_ready(self, rid: str) -> None:
        req = self.requests[rid]
        req["status"] = "waiting_for_runner"
        self.save_request(rid)
        deadline = asyncio.get_running_loop().time() + self.s.gateway.resume_wait_s
        previous: set[str] | None = None
        while True:
            missing = {aid for aid in self.resume_agents(rid)
                       if self.agent_runner.get(aid) not in self.runners}
            if not missing:
                req["status"] = "running"
                self.save_request(rid)
                await self.publish({"type": "request.resumed", "ts": time.time(), "request_id": rid,
                                    "data": {"agents": sorted(self.resume_agents(rid))}})
                self.recovery_steps.add(rid)
                try:
                    await self.orchestrator.run_request(rid, resume=True)
                finally:
                    self.recovery_steps.discard(rid)
                return
            if missing != previous:
                await self.publish({"type": "request.resume_waiting", "ts": time.time(), "request_id": rid,
                                    "data": {"missing_agents": sorted(missing)}})
                previous = missing
            if asyncio.get_running_loop().time() >= deadline:
                req["status"] = "interrupted"
                self.save_request(rid)
                await self.publish({"type": "request.resume_timeout", "ts": time.time(), "request_id": rid,
                                    "data": {"missing_agents": sorted(missing)}})
                approval = self.new_resume_approval(rid)
                await self.publish({"type": "approval.requested", "ts": time.time(), "request_id": rid,
                                    "data": approval.model_dump(mode="json")})
                return
            await asyncio.sleep(min(0.1, max(0, deadline - asyncio.get_running_loop().time())))

    def save_approval(self, aid: str) -> None:
        entry = self.approvals[aid]
        self.store.put("approval", aid, {k: v for k, v in entry.items() if k != "future"})

    # ----- runners -----
    def register_runner(self, runner_id: str, ws: WebSocket, agents: list[dict],
                        incarnation: str | None = None, capabilities: dict | None = None) -> None:
        old = self.runners.get(runner_id)
        if old is not None and old is not ws and hasattr(old, "close"):
            async def close_old() -> None:
                try:
                    await old.close(code=1000)
                except RuntimeError:  # it may already be closed while the replacement connects
                    pass
            asyncio.get_running_loop().create_task(close_old())
        if incarnation:
            self.store.runner_incarnation(runner_id, incarnation)
        self.runners[runner_id] = ws
        self.runner_incarnations[runner_id] = incarnation
        self.runner_locks.setdefault(runner_id, asyncio.Lock())
        self.set_roster(runner_id, agents, capabilities)
        hosted_agents = {a["id"] for a in agents}
        for tid, entry in self.store.all("task").items():
            if (not entry.get("completed") and
                    (entry.get("payload") or {}).get("agent_id") in hosted_agents and
                    not self._same_runner_generation(entry, runner_id, incarnation)):
                self._abandon_previous_generation(tid, entry)

    def _abandon_previous_generation(self, tid: str, entry: dict) -> TaskResult:
        agent_id = (entry.get("payload") or {}).get("agent_id") or "unknown"
        result = TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                            error="runner generation changed; prior task delivery or outcome unknown; "
                                  "manual recovery required")
        self.store.put("task", tid, {**entry, "completed": True, "abandoned": True,
                                     "result": result.model_dump(mode="json")})
        future = self.futures.get(tid)
        if future and not future.done():
            future.set_result(result)
        return result

    async def flush_decisions(self, runner_id: str) -> None:
        for aid, entry in self.store.all("decision").items():
            if entry["origin"] == runner_id:
                await self.send_runner(runner_id, {"type": "approval.resolved", "id": aid,
                                                   "approved": entry["approved"], "note": entry["note"]})

    async def flush_ask_answers(self, runner_id: str) -> None:
        for ask_id, entry in self.store.all("ask").items():
            if (entry.get("origin") == runner_id and entry.get("state") == "resolved"
                    and not entry.get("delivered")):
                await self.send_runner(runner_id, {"type": "ask.resolved", "id": ask_id,
                                                   "answer": entry["answer"]})
            elif entry.get("origin") == runner_id and entry.get("state") in {"pending", "working"}:
                self._start_ask(AskRequest.model_validate(entry["ask"]), runner_id)

    def _restart_request_asks(self, rid: str) -> None:
        for entry in self.store.all("ask").values():
            if (entry.get("ask") or {}).get("request_id") == rid and entry.get("state") in {"pending", "working"}:
                self._start_ask(AskRequest.model_validate(entry["ask"]), entry.get("origin"))

    def set_roster(self, runner_id: str, agents: list[dict], capabilities: dict | None = None) -> None:
        if capabilities is not None:
            self.runner_capabilities[runner_id] = capabilities
        for aid in [a for a, r in self.agent_runner.items() if r == runner_id]:
            self.agent_runner.pop(aid, None)
            self.agents.pop(aid, None)
            self.agent_online.setdefault(aid, asyncio.Event()).clear()
        self.runner_agents[runner_id] = agents
        for a in agents:
            caps = self.runner_capabilities.get(runner_id, {})
            hpc_tools = bool(a.get("hpc_tools", caps.get("hpc_tools") and
                                                 "hpc" in a.get("builtin_mcp", [])))
            self.agents[a["id"]] = {**a, "runner_id": runner_id,
                                    "scheduler": caps.get("scheduler", "none"),
                                    "task_timeout_s": a.get("task_timeout_s", self.s.runner.task_timeout_s),
                                    "compute_backends": (["local CLI"] +
                                                         [b for b in caps.get("compute_backends", [])
                                                          if b != "local CLI" and hpc_tools]),
                                    "hpc_tools": hpc_tools}
            self.agent_runner[a["id"]] = runner_id
            if runner_id in self.runners:
                self.agent_online.setdefault(a["id"], asyncio.Event()).set()

    def unregister_runner(self, runner_id: str, ws: WebSocket) -> bool:
        if self.runners.get(runner_id) is ws:
            self.runners.pop(runner_id, None)  # keep roster + pending futures: the runner will reconnect
            self.runner_incarnations.pop(runner_id, None)
            for aid, host in self.agent_runner.items():
                if host == runner_id:
                    self.agent_online.setdefault(aid, asyncio.Event()).clear()
            return True
        return False

    async def wait_agent_online(self, agent_id: str, timeout_s: float) -> bool:
        signal = self.agent_online.setdefault(agent_id, asyncio.Event())
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if self.agent_runner.get(agent_id) in self.runners:
                return True
            signal.clear()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(signal.wait(), remaining)
            except asyncio.TimeoutError:
                return False

    async def send_runner(self, runner_id: str, msg: dict) -> None:
        ws = self.runners.get(runner_id)
        if ws is None:
            raise RunnerUnavailable(f"runner {runner_id} is offline")
        payload = json.dumps(msg, ensure_ascii=False, default=str)
        async with self.runner_locks[runner_id]:
            if self.runners.get(runner_id) is not ws:
                raise RunnerUnavailable(f"runner {runner_id} connection was replaced")
            try:
                await ws.send_text(payload)
            except Exception as exc:
                self.unregister_runner(runner_id, ws)
                raise RunnerUnavailable(f"runner {runner_id} WebSocket send failed") from exc

    def supports_resume(self, agent_id: str) -> bool:
        engine = self.agents.get(agent_id, {}).get("engine")
        if not engine:
            return False
        if engine == "cli":
            return bool(self.agents[agent_id].get("cli_resume"))
        try:
            return get_adapter(engine, self.s).supports_resume
        except ValueError:
            return False

    # ----- events -----
    async def publish(self, ev: dict, runner_id: str | None = None, runner_seq: int | None = None) -> None:
        async with self.event_lock:
            await self._flush_committed_events()
            durable_report = (self.reporter.enabled() and bool(ev.get("request_id")) and
                              ev.get("type") in ProjectReporter.HANDLED)
            ev = self.store.append_event(ev, self.s.gateway.event_buffer, runner_id, runner_seq,
                                         reporter_delivery=durable_report)
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

    async def on_runner_message(self, runner_id: str, msg: dict, ws: WebSocket | None = None,
                                incarnation: str | None = None) -> None:
        if ws is not None and (self.runners.get(runner_id) is not ws or
                               self.runner_incarnations.get(runner_id) != incarnation):
            return
        runner_seq = msg.get("runner_seq")
        if runner_seq is not None and runner_seq <= self.store.runner_seen(runner_id):
            await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})
            return
        typ = msg.get("type")
        if typ == "runner.roster":
            self.set_roster(runner_id, msg.get("agents", []), msg.get("capabilities"))
            await self.publish({"type": "roster.updated", "ts": time.time(), "data": {"agents": list(self.agents.values())}})
            return
        if typ == "approval.ack":
            self.store.delete("decision", str(msg["id"]))
        if typ == "ask.ack":
            entry = self.store.get("ask", str(msg["id"]))
            if entry:
                self.store.put("ask", str(msg["id"]), {**entry, "delivered": True})
        if typ == "task.result":
            rid = msg.get("request_id")
            tid = msg.get("task_id") or ""
            task = self.store.get("task", tid) if tid else None
            result = TaskResult.model_validate(msg["data"])
            if task:
                # A reported result supersedes an abandonment: the task did finish (#112).
                self.store.put("task", tid, {**{k: v for k, v in task.items() if k != "abandoned"},
                                              "completed": True, "result": result.model_dump(mode="json")})
            if task and task.get("request_id") in self.requests and (task.get("step_id") or task.get("kind") == "direct"):
                rid = task["request_id"]
                sid = task.get("step_id") or "direct"
                self.store.put("step_checkpoint", f"{rid}:{sid}",
                               {"task_id": tid, "attempt": task.get("attempt", 1),
                                "revision": task.get("revision", 0),
                                "result": result.model_dump(mode="json")})
            if rid in self.requests and tid:
                req = self.requests[rid]
                costs = req.setdefault("cost_by_task", {})
                if tid not in costs:
                    amount = float((msg.get("data") or {}).get("cost_usd") or 0)
                    costs[tid] = amount
                    req["cost_usd"] = float(req.get("cost_usd") or 0) + amount
                    known = result.cost_known if result.cost_known is not None else result.cost_usd is not None
                    req["cost_known"] = req.get("cost_known", True) and known
                    req["usage_known"] = req.get("usage_known", True) and result.usage_known
                    req.setdefault("usage_by_task", {})[tid] = result.usage
                    totals: dict[str, int] = {}
                    for usage in req["usage_by_task"].values():
                        for key, count in usage.items():
                            totals[key] = totals.get(key, 0) + count
                    req["usage"] = totals
                    self.save_request(rid)
            fut = self.futures.pop(msg.get("task_id") or "", None)
            if fut and not fut.done():
                fut.set_result(result)
        elif typ == "approval.requested":
            a = msg["data"]
            self.approvals[a["id"]] = {"approval": a, "origin": runner_id}
            self.save_approval(a["id"])
        elif typ == "ask.requested":
            ask = AskRequest.model_validate(msg["data"])
            existing = self.store.get("ask", ask.id)
            if existing and existing.get("state") == "resolved":
                await self.send_runner(runner_id, {"type": "ask.resolved", "id": ask.id,
                                                   "answer": existing["answer"]})
            elif existing is None:
                self.store.put("ask", ask.id, {"state": "pending", "origin": runner_id,
                                                "ask": ask.model_dump(mode="json")})
                await self.publish({"type": "agent.ask", "ts": time.time(), "task_id": ask.task_id,
                                    "agent_id": ask.agent_id, "request_id": ask.request_id,
                                    "data": ask.model_dump(mode="json")})
                self._start_ask(ask, runner_id)
        elif typ == "jobs.finished":
            tid = msg.get("task_id") or ""
            self.store.put("jobs_done", tid, msg["data"])
            self.jobs_done[tid] = msg["data"]
            fut = self.jobs_waiters.pop(tid, None)
            if fut and not fut.done():
                fut.set_result(msg["data"])
        elif typ == "task.accepted":
            tid = msg.get("task_id") or ""
            task = self.store.get("task", tid)
            if task:
                self.store.put("task", tid, {**task, "accepted": True, "runner_id": runner_id,
                                               "runner_incarnation": self.runner_incarnations.get(runner_id)})
        await self.publish(msg, runner_id=runner_id, runner_seq=runner_seq)
        if runner_seq is not None:
            await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})

    # ----- tasks -----
    async def dispatch(self, task: Task) -> TaskResult:
        sid = task.meta.get("step_id") or task.meta.get("kind")
        if sid and task.request_id in self.recovery_steps:
            matches = [(tid, entry) for tid, entry in self.store.all("task").items()
                       if self._matches_recovery(task, entry) and tid not in self.recovered_tasks]
            if not matches and task.meta.get("kind") == "direct":
                checkpoint = self.store.get("step_checkpoint", f"{task.request_id}:direct")
                if checkpoint and checkpoint.get("result"):
                    return TaskResult.model_validate(checkpoint["result"]).model_copy(update={"cost_usd": 0.0})
            if matches:
                tid, entry = max(matches, key=lambda pair: (int(pair[1].get("attempt") or 1),
                                                            pair[1].get("dispatched_at", 0)))
                if entry.get("completed") and entry.get("result"):
                    self.recovered_tasks.add(tid)
                    return TaskResult.model_validate(entry["result"]).model_copy(update={"cost_usd": 0.0})
                checkpoint = self.store.get("step_checkpoint", f"{task.request_id}:{sid}")
                if checkpoint and checkpoint.get("task_id") == tid:
                    self.recovered_tasks.add(tid)
                    return TaskResult.model_validate(checkpoint["result"]).model_copy(update={"cost_usd": 0.0})
                current_runner = self.agent_runner.get(task.agent_id)
                if not self._same_runner_generation(entry, current_runner,
                                                    self.runner_incarnations.get(current_runner)):
                    self.recovered_tasks.add(tid)
                    return self._abandon_previous_generation(tid, entry)
                return await self._await_prior_task(tid, entry, task.agent_id, task.request_id, sid)
        rid = self.agent_runner.get(task.agent_id)
        if not rid:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                              error=f"no runner hosts agent {task.agent_id!r}")
        fut = asyncio.get_running_loop().create_future()
        self.futures[task.id] = fut
        self.task_runner[task.id] = rid
        self.store.put("task", task.id, {"request_id": task.request_id,
                                          "runner_id": rid,
                                          "runner_incarnation": self.runner_incarnations.get(rid),
                                          "step_id": task.meta.get("step_id"), "kind": task.meta.get("kind"),
                                          "attempt": task.meta.get("attempt", 1),
                                          "revision": task.meta.get("revision", 0),
                                          "parse_attempt": task.meta.get("parse_attempt", 0),
                                          "parent_task": task.meta.get("parent_task"),
                                          "payload": task.model_dump(mode="json"),
                                          "accepted": False, "dispatched_at": time.time()})
        await self.publish({"type": "task.dispatched", "ts": time.time(), "task_id": task.id,
                            "agent_id": task.agent_id, "request_id": task.request_id,
                            "data": {"kind": task.meta.get("kind"), "step_id": task.meta.get("step_id"),
                                     "attempt": task.meta.get("attempt", 1), "outputs": task.meta.get("outputs", []),
                                     "title": task.meta.get("title"), "prompt": task.prompt[:300]}})
        try:
            await self.send_runner(rid, {"type": "task.dispatch", "task": task.model_dump(mode="json")})
        except RunnerUnavailable:
            # The frame may already have reached the runner. Keep its identity and let
            # the runner's accepted-task ledger deduplicate the resend.
            await self.publish({"type": "request.step_wait", "ts": time.time(),
                                "request_id": task.request_id,
                                "data": {"step_id": sid, "reason": "runner reconnect before task delivery"}})
            deadline = asyncio.get_running_loop().time() + self.s.orchestrator.runner_reconnect_timeout_s
            while not fut.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0 or not await self.wait_agent_online(task.agent_id, remaining):
                    if fut.done():
                        break
                    self.futures.pop(task.id, None)
                    self.task_runner.pop(task.id, None)
                    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                                      error="task delivery uncertain; manual recovery required")
                if fut.done():
                    break
                target = self.agent_runner[task.agent_id]
                try:
                    await self.send_runner(target, {"type": "task.dispatch", "task": task.model_dump(mode="json")})
                except RunnerUnavailable:
                    continue
                self.task_runner[task.id] = target
                break
        except Exception:
            self.futures.pop(task.id, None)
            self.task_runner.pop(task.id, None)
            self.store.delete("task", task.id)
            raise
        return await fut

    async def _await_prior_task(self, tid: str, entry: dict, agent_id: str, request_id: str | None,
                                sid: str | None, reason: str = "recovering prior task result") -> TaskResult:
        """Wait for a task that a previous gateway dispatched to the current runner generation."""
        future = asyncio.get_running_loop().create_future()
        self.futures[tid] = future
        await self.publish({"type": "request.step_wait", "ts": time.time(), "request_id": request_id,
                            "data": {"step_id": sid, "reason": reason}})
        try:
            if not entry.get("accepted") and entry.get("payload"):
                runner_id = self.agent_runner.get(agent_id)
                if runner_id:
                    try:
                        await self.send_runner(runner_id, {"type": "task.dispatch", "task": entry["payload"]})
                    except RunnerUnavailable:
                        pass  # delivery is uncertain; never retry this work under a new task ID
            # resume_wait_s bounds connection/acceptance, not a running task. The
            # runner's own task_timeout governs work after task.accepted.
            deadline = None
            while not future.done():
                accepted = bool((self.store.get("task", tid) or {}).get("accepted"))
                online = self.agent_runner.get(agent_id) in self.runners
                if accepted and online:
                    deadline = None
                elif deadline is None:
                    deadline = asyncio.get_running_loop().time() + self.s.gateway.resume_wait_s
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    return TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                                      error="recovery runner unavailable; manual restart required")
                try:
                    await asyncio.wait_for(asyncio.shield(future), 0.1)
                except asyncio.TimeoutError:
                    pass
            result = future.result()
            self.recovered_tasks.add(tid)
            return result
        finally:
            self.futures.pop(tid, None)

    def consult_attempts(self, ask_id: str, agent_id: str) -> list[tuple[str, dict]]:
        """Consults one agent ran for one ask, from any gateway generation.

        Another agent's consult is never adopted: its session and workdir would replace the
        routed agent's (a facilities ask falls back to the CSO while the roster is empty).
        """
        return [(tid, entry) for tid, entry in self.store.all("task").items()
                if entry.get("kind") == "consult" and
                (entry.get("payload") or {}).get("agent_id") == agent_id and
                ((entry.get("payload") or {}).get("meta") or {}).get("ask_id") == ask_id]

    async def adopt_consult(self, ask_id: str, agent_id: str) -> tuple[int, TaskResult | None]:
        """Adopt the consult a previous gateway started for this ask (#93).

        Returns ``(attempt, result)`` for the latest prior consult, or ``(0, None)`` without one.
        ``result`` is ``None`` when its outcome is unknown (another runner generation, or
        abandoned). The caller then runs a new consult in a separate session and workdir,
        because the old one may still hold them.
        """
        prior = self.consult_attempts(ask_id, agent_id)
        if not prior:
            return 0, None
        for tid, _ in prior:
            self.recovered_tasks.add(tid)  # handled here; dispatch recovery must not re-adopt it
        # Latest dispatch first: an isolated rerun restarts its attempt numbering.
        tid, entry = max(prior, key=lambda pair: (pair[1].get("dispatched_at", 0),
                                                  int(pair[1].get("attempt") or 1)))
        attempt = int(entry.get("attempt") or 1)
        if not entry.get("completed") and self.agent_runner.get(agent_id) not in self.runners:
            # Resume approval can precede the runner's reconnect; its generation decides adoption.
            await self.wait_agent_online(agent_id, self.s.gateway.resume_wait_s)
            entry = self.store.get("task", tid) or entry
        if not entry.get("completed"):
            runner = self.agent_runner.get(agent_id)
            if not self._same_runner_generation(entry, runner, self.runner_incarnations.get(runner)):
                return attempt, None
            await self._await_prior_task(tid, entry, agent_id, entry.get("request_id"), "consult",
                                         reason="adopting consult started before gateway restart")
            entry = self.store.get("task", tid) or entry
        if not entry.get("completed") or entry.get("abandoned") or not entry.get("result"):
            return attempt, None
        # The task.result handler already added its cost to the durable request total; the
        # orchestrator adds it by task ID, which this ledger entry owns.
        return attempt, TaskResult.model_validate(entry["result"]).model_copy(update={"cost_usd": 0.0,
                                                                                      "task_id": tid})

    async def wait_session_free(self, agent_id: str, session_id: str | None, workdir: str | None, *,
                                request_id: str | None = None, step_id: str | None = None
                                ) -> tuple[str | None, str | None]:
        """The session and workdir a task may resume once no earlier task still uses them (#112, #144).

        An earlier task, such as a consult whose ask a restart left unanswered, keeps running on its
        runner. While the agent's connected runner generation accepted it, or it is in flight to that
        runner from this gateway, wait for its result; its turn may rotate the session, so the latest
        turn is resumed. No clock releases such a holder: the runner accepts a task before it leaves
        the queue and starts its task timeout only when the CLI spawns, and it always reports a result
        (#204). While the agent's runner is offline, wait up to ``resume_wait_s`` from the disconnect
        for it to reconnect. Otherwise its outcome is unknown: return ``(None, None)`` so the task
        opens a new session and workdir.
        """
        loop = asyncio.get_running_loop()
        offline_deadline: float | None = None
        resumed: set[str] = set()
        announced = False
        while True:
            holders = [(tid, entry) for tid, entry in self.store.all("task").items()
                       if holds_session(entry, agent_id, session_id, workdir)]
            if not holders:
                turns = [self.store.get("task", tid) or {} for tid in resumed]
                latest = max(((float(e.get("dispatched_at") or 0), (e.get("result") or {}).get("session_id"))
                              for e in turns if (e.get("result") or {}).get("session_id")), default=(0.0, None))[1]
                if latest and latest != session_id:
                    session_id, resumed = latest, set()
                    continue
                return session_id, workdir
            runner = self.agent_runner.get(agent_id)
            online = runner in self.runners
            # Same rule as _await_prior_task: resume_wait_s bounds a reconnect, measured from the
            # disconnect, never the work of a task the runner accepted.
            if online:
                offline_deadline = None
            elif offline_deadline is None:
                offline_deadline = loop.time() + self.s.gateway.resume_wait_s
            for tid, entry in holders:
                running = (online and bool(entry.get("accepted")) and
                           self._same_runner_generation(entry, runner, self.runner_incarnations.get(runner)))
                if entry.get("abandoned"):
                    return None, None
                # An unaccepted task the connected runner does not run (a delivery this gateway gave
                # up on, or one a previous gateway sent) has an unknown outcome.
                if not (running or (online and tid in self.futures) or
                        (not online and loop.time() < offline_deadline)):
                    return None, None
                if session_id and (entry.get("payload") or {}).get("resume_session_id") == session_id:
                    resumed.add(tid)
            if not announced:
                announced = True
                await self.publish({"type": "request.step_wait", "ts": time.time(), "request_id": request_id,
                                    "data": {"step_id": step_id, "agent_id": agent_id,
                                             "reason": "an earlier task still uses this session or workdir"}})
            await asyncio.sleep(0.1)

    async def wait_jobs(self, task_id: str) -> dict:
        if task_id in self.jobs_done:
            return self.jobs_done[task_id]
        fut = self.jobs_waiters.setdefault(task_id, asyncio.get_running_loop().create_future())
        return await fut

    def _start_ask(self, ask: AskRequest, runner_id: str) -> None:
        def request_status():
            return self.requests.get(ask.request_id, {}).get("status") if ask.request_id else "running"

        status = request_status()
        if status not in {"waiting_for_runner", "running"} | TERMINAL_REQUEST_STATES:
            return
        previous = self.ask_tasks.get(ask.id)
        if previous and not previous.done():
            if status not in TERMINAL_REQUEST_STATES:
                return
            previous.cancel()

        async def route() -> None:
            # Recheck after scheduling: a resume decision or terminal checkpoint may intervene.
            status = request_status()
            if status in TERMINAL_REQUEST_STATES:
                request = self.requests.get(ask.request_id, {})
                await self.resolve_ask(ask, runner_id, ask_result(
                    reason=f"request {status}: {request.get('error') or 'request ended'}", **{"from": "labhq"}))
                return
            if status not in {"waiting_for_runner", "running"}:
                return
            entry = self.store.get("ask", ask.id) or {}
            self.store.put("ask", ask.id, {**entry, "state": "working"})
            try:
                await self.orchestrator.answer_ask(ask, runner_id)
            except Exception as exc:
                await self.resolve_ask(ask, runner_id, ask_result(
                    reason=f"질의 처리 실패: {exc}", **{"from": "labhq"}))

        task = asyncio.create_task(route())
        self.ask_tasks[ask.id] = task
        def finished(done):
            if self.ask_tasks.get(ask.id) is done:
                self.ask_tasks.pop(ask.id, None)
        task.add_done_callback(finished)

    async def resolve_ask(self, ask: AskRequest, runner_id: str, answer: dict) -> None:
        answer = ask_result(**{**answer, "ask_id": ask.id})
        entry = self.store.get("ask", ask.id) or {"ask": ask.model_dump(mode="json"), "origin": runner_id}
        self.store.put("ask", ask.id, {**entry, "state": "resolved", "answer": answer,
                                       "resolved_at": time.time()})
        waiter = self.ask_waiters.pop(ask.id, None)
        if waiter and not waiter.done():
            waiter.set_result(answer)
        await self.publish({"type": "agent.answer", "ts": time.time(), "task_id": ask.task_id,
                            "agent_id": answer.get("from"), "request_id": ask.request_id,
                            "data": {**answer, "to": ask.agent_id, "question": ask.question}})
        if runner_id in self.runners:
            await self.send_runner(runner_id, {"type": "ask.resolved", "id": ask.id, "answer": answer})

    async def wait_asks(self, ask_ids: list[str]) -> list[dict]:
        answers = []
        for ask_id in ask_ids:
            entry = self.store.get("ask", ask_id)
            if entry and entry.get("state") == "resolved":
                answers.append(entry["answer"])
                continue
            future = self.ask_waiters.setdefault(ask_id, asyncio.get_running_loop().create_future())
            answers.append(await future)
        return answers

    def ask_results_for_task(self, task_id: str) -> list[dict]:
        return [entry["answer"] for entry in self.store.all("ask").values()
                if (entry.get("ask") or {}).get("task_id") == task_id
                and entry.get("state") == "resolved" and entry.get("answer")]

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
            decision = await asyncio.wait_for(fut, req.timeout_s)
            return {**decision, "approval_id": req.id, "decided_at": time.time()}
        except asyncio.TimeoutError:
            self.approvals.pop(req.id, None)
            self.store.delete("approval", req.id)
            self.store.put("approval_decision", req.id,
                           {"approval": req.model_dump(mode="json"), "approved": False,
                            "note": "timed out", "state": "timed_out", "decided_at": time.time()})
            await self.publish({"type": "approval.resolved", "ts": time.time(), "request_id": request_id,
                               "data": {"id": req.id, "approved": False, "note": "timed out",
                                        "state": "timed_out"}})
            return {"approved": False, "note": "timed out", "state": "timed_out", "approval_id": req.id,
                    "decided_at": time.time()}

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
                self.requests[rid]["status"] = "waiting_for_runner"
                self.save_request(rid)
                self._restart_request_asks(rid)
                asyncio.get_running_loop().create_task(self.resume_when_ready(rid))
        a = entry["approval"]
        await self.publish({"type": "approval.resolved", "ts": time.time(), "task_id": a.get("task_id"),
                            "agent_id": a.get("agent_id"), "request_id": a.get("request_id"),
                            "data": {"id": approval_id, "approved": approved, "note": note}})
        if a.get("kind") == "resume" and not approved:
            rid = a["request_id"]
            self.requests[rid].update(status="failed", error="resume declined", finished_at=time.time())
            self.commit_terminal(rid, "request.failed", {"error": "resume declined"})

    # ----- requests -----
    def create_request(self, body: RequestIn) -> str:
        rid = new_id("req")
        req = {"id": rid, "status": "running", "created_at": time.time(),
               "cost_known": True, "usage": {}, **body.model_dump()}
        proj = self.s.project(body.project_id)
        if body.project_id and proj is None:
            raise KeyError(f"unknown project {body.project_id!r}")
        if proj and proj.local_dir and proj.local_dir not in req["project_dirs"]:
            req["project_dirs"] = [*req["project_dirs"], proj.local_dir]  # agents work in the project clone
        # Fixed at creation: a later config edit must not change what a running or resumed request points at.
        req["references"] = effective_references(body.references, body.default_references, self.s)
        # A dropped query may be a credential: kept only in this internal store, never in the request record,
        # snapshot, prompts or reports.
        originals = [{"value": r.value, "original": r.original} for r in body.references if r.original]
        req["environment"] = environment_snapshot(self)
        self.requests[rid] = req
        if originals:
            self.store.put("reference_original", rid, {"references": originals})
        self.save_request(rid)
        asyncio.get_running_loop().create_task(self._start_request(rid))
        return rid

    def start_followup(self, rid: str, text: str) -> dict:
        """Ask a finished request one more question in the same session and workspace (#36). Not a new request."""
        req = self.requests[rid]
        if req.get("status") not in TERMINAL_REQUEST_STATES:
            raise ValueError(f"request is {req.get('status')}; ask a follow-up after it finishes")
        if any(f.get("status") == "running" for f in req.get("followups") or []):
            raise ValueError("a follow-up for this request is still running")
        agent = req.get("agent_id") if req.get("mode") == "direct" else self.s.orchestrator.cso_agent
        if agent not in self.agents:
            raise ValueError(f"agent {agent!r} is not on any connected runner")
        refusal = read_only_refusal(agent, self.agents[agent].get("engine"))
        if refusal:
            raise ValueError(refusal)
        entry = {"id": new_id("fu"), "text": text.strip(), "agent_id": agent, "status": "running",
                 "asked_at": time.time()}
        req.setdefault("followups", []).append(entry)
        self.save_request(rid)
        asyncio.get_running_loop().create_task(self.orchestrator.run_followup(rid, entry["id"]))
        return entry

    async def _start_request(self, rid: str) -> None:
        r = self.requests[rid]
        await self.publish({"type": "request.created", "ts": time.time(), "request_id": rid,
                            "data": {k: r.get(k) for k in ("text", "mode", "agent_id", "project_id", "references")}})
        await self.orchestrator.run_request(rid)

    def snapshot(self) -> dict[str, Any]:
        return {"type": "snapshot", "schema_version": 1, "seq": self.store.event_bounds()[1],
                "ts": time.time(), "data": {
            "agents": list(self.agents.values()),
            "runners": list(self.runners),
            "approvals": [e["approval"] for e in self.approvals.values()],
            "requests": [{**{k: v for k, v in r.items() if k in ("id", "text", "status", "mode", "created_at",
                                                                  "project_id", "plan", "cost_usd", "cost_known",
                                                                  "usage", "usage_known", "agent_id", "references")},
                          # the full list and full answers stay on the request (GET /api/requests/{id})
                          "followups": [snapshot_followup(f) for f in (r.get("followups") or [])[-20:]],
                          "step_status": {sid: outcome.get("status") or ("done" if outcome.get("ok") else "failed")
                                          for sid, outcome in (r.get("results") or {}).items()},
                          "step_details": self.request_step_details(r.get("id", ""), r),
                          "review": r.get("review") or (r.get("review_progress") or {}).get("review")}
                         for r in self.requests.values()],
            "projects": [{"id": p.id, "name": p.name or p.id, "repo": p.repo, "visibility": p.visibility}
                         for p in self.s.projects],
            "default_references": [r.model_dump() for r in self.s.pi_profile.references],
            "running_tasks": self.running_tasks(),
            "recent_events": [snapshot_event(e) for e in list(self.events)[-200:]],
        }}

    def request_step_details(self, rid: str, request: dict) -> dict[str, dict]:
        tasks = [(tid, entry) for tid, entry in self.store.all("task").items()
                 if entry.get("request_id") == rid and entry.get("step_id")]
        review = request.get("review") or (request.get("review_progress") or {}).get("review") or {}
        details: dict[str, dict] = {}
        for step in (request.get("plan") or {}).get("steps", []):
            sid = step.get("id")
            matches = [(tid, entry) for tid, entry in tasks if entry.get("step_id") == sid]
            result = (request.get("results") or {}).get(sid) or {}
            latest = max(matches, key=lambda pair: pair[1].get("dispatched_at", 0), default=(None, {}))
            details[sid] = {
                "task_id": result.get("task_id") or latest[0],
                "attempts": max((int(entry.get("attempt") or 1) for _, entry in matches), default=0),
                "outputs": result.get("outputs") or [],
                "missing_outputs": result.get("missing_outputs") or [],
                "text": short(result.get("text") or "", SNAPSHOT_RESULT_CHARS),
                "error": result.get("error") or "",
                "review_issues": [issue for issue in review.get("issues", []) if issue.get("step_id") == sid],
            }
        return details


def create_app(settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    check_configured_packs(settings)  # before any state opens: a stale pack key stops the start (#170)
    hub = Hub(settings, github_transport)
    app = FastAPI(title="labhq gateway", version="0.1.0", default_response_class=UTF8JSONResponse)
    app.state.hub = hub

    @app.on_event("startup")
    async def recover_terminal_deliveries() -> None:
        hub.recover_terminal_deliveries()
        for rid, request in hub.requests.items():
            if request.get("status") in TERMINAL_REQUEST_STATES:
                hub._restart_request_asks(rid)

    @app.on_event("shutdown")
    async def warn_running_on_shutdown() -> None:
        running = [r["id"] for r in hub.requests.values() if r.get("status") == "running"]
        if running:
            log.warning("gateway shutdown with running requests: %s", ", ".join(running))

    def auth(authorization: str = Header(default="")) -> None:
        if not token_matches(authorization.removeprefix("Bearer ").strip(), settings.gateway.client_token):
            raise HTTPException(401, "bad client token")

    @app.websocket("/ws/runner")
    async def ws_runner(ws: WebSocket, token: str = "") -> None:
        if not token_matches(token, settings.gateway.runner_token):
            await ws.close(code=1008)
            return
        await ws.accept()
        runner_id = None
        try:
            hello = json.loads(await ws.receive_text())
            runner_id = hello["runner_id"]
            incarnation = hello.get("incarnation")
            hub.register_runner(runner_id, ws, hello.get("agents", []), incarnation,
                                hello.get("capabilities"))
            await hub.flush_decisions(runner_id)
            await hub.flush_ask_answers(runner_id)
            await hub.publish({"type": "runner.online", "ts": time.time(),
                               "data": {"runner_id": runner_id, "agents": len(hello.get("agents", []))}})
            while True:
                if hub.runners.get(runner_id) is not ws:
                    break
                await hub.on_runner_message(runner_id, json.loads(await ws.receive_text()), ws, incarnation)
        except WebSocketDisconnect:
            pass
        finally:
            if runner_id:
                if hub.unregister_runner(runner_id, ws):
                    await hub.publish({"type": "runner.offline", "ts": time.time(), "data": {"runner_id": runner_id}})

    @app.websocket("/ws/client")
    async def ws_client(ws: WebSocket, token: str = "", since: int | None = None) -> None:
        if not token_matches(token, settings.gateway.client_token):
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
        except ValueError as e:  # a reference outside the runner's roots or in a restricted zone
            raise HTTPException(422, str(e))

    @app.get("/api/requests", dependencies=[Depends(auth)])
    async def list_requests(status: str = "running", limit: int = 20) -> list[dict]:
        if status not in {"running", "done", "failed", "all"}:
            raise HTTPException(422, "invalid status")
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be 1..200")
        requests = sorted(hub.requests.values(), key=lambda r: r.get("created_at", 0), reverse=True)
        return [hub.request_summary(r) for r in requests if status == "all" or r.get("status") == status][:limit]

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

    @app.post("/api/requests/{rid}/followup", dependencies=[Depends(auth)])
    async def followup(rid: str, body: FollowupIn) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            entry = hub.start_followup(rid, body.text)
        except ValueError as e:  # still running, one already pending, or no runner hosts the agent
            raise HTTPException(409, str(e))
        return {"request_id": rid, "followup_id": entry["id"]}

    @app.get("/api/requests/{rid}", dependencies=[Depends(auth)])
    async def get_request(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        return hub.requests[rid]

    @app.get("/api/approvals", dependencies=[Depends(auth)])
    async def approvals() -> list[dict]:
        return [e["approval"] for e in hub.approvals.values()]

    @app.get("/api/approvals/history", dependencies=[Depends(auth)])
    async def approval_history(limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be 1..200")
        decisions = hub.store.all("approval_decision").values()
        return sorted(decisions, key=lambda item: item.get("decided_at", 0), reverse=True)[:limit]

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
        return {"service": "labhq gateway", "runners": list(hub.runners), "agents": len(hub.agents),
                "active_requests": sum(r.get("status") == "running" for r in hub.requests.values()),
                "running_tasks": len(hub.running_tasks())}

    @app.get("/", response_class=HTMLResponse)
    async def office() -> HTMLResponse:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        boot = '<script>window.LABHQ_BOOT={"mode":"live"}</script>'
        return HTMLResponse(html.replace("<!--LABHQ_BOOT-->", boot), headers={"Cache-Control": "no-cache"})

    # Like /, static shells are public; data and decisions require the client token.
    @app.get("/3d")
    async def office3d_redirect(request: Request) -> RedirectResponse:
        query = str(request.url.query)
        return RedirectResponse("/3d/" + ("?" + query if query else ""))

    @app.get("/3d/", response_class=HTMLResponse)
    async def office3d() -> HTMLResponse:
        html = (WEB / "lab3d" / "index.html").read_text(encoding="utf-8")
        boot = '<script>window.LABHQ_BOOT={"mode":"live"}</script>'
        return HTMLResponse(html.replace("<!--LABHQ_BOOT-->", boot), headers={"Cache-Control": "no-cache"})

    def static_file(root: Path, path: str) -> FileResponse:
        # Reject decoded dot segments, Windows separators/drives and symlink escapes.
        if "\\" in path or ":" in path or ".." in path.split("/"):
            raise HTTPException(404)
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise HTTPException(404)
        mime = {".js": "text/javascript", ".gltf": "model/gltf+json",
                ".glb": "model/gltf-binary", ".bin": "application/octet-stream"}.get(target.suffix)
        mime = mime or mimetypes.guess_type(target.name)[0]
        if target.suffix not in {".js", ".css", ".gltf", ".glb", ".bin", ".png", ".jpg", ".jpeg", ".webp", ".svg"}:
            raise HTTPException(404)
        return FileResponse(target, media_type=mime, headers={"Cache-Control": "no-cache"})

    @app.get("/state.js")
    async def office_state() -> FileResponse:
        return static_file(WEB, "state.js")

    @app.get("/ui/{path:path}")
    async def office_ui_asset(path: str) -> FileResponse:
        return static_file(WEB / "ui", path)

    @app.get("/3d/{path:path}")
    async def office3d_asset(path: str) -> FileResponse:
        if not path.startswith(("src/", "assets/")):
            raise HTTPException(404)
        return static_file(WEB / "lab3d", path)

    @app.get("/vendor/three/{path:path}")
    async def three_asset(path: str) -> FileResponse:
        return static_file(WEB / "vendor" / "three", path)

    @app.get("/manifest.webmanifest")
    async def manifest() -> Response:
        return Response((WEB / "manifest.webmanifest").read_text(encoding="utf-8"),
                        media_type="application/manifest+json")

    @app.get("/icon.svg")
    async def icon() -> Response:
        return Response((WEB / "icon.svg").read_text(encoding="utf-8"), media_type="image/svg+xml")

    return app
