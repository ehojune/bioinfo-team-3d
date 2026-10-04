"""Gateway: the one reachable endpoint. Runners dial in; desktop/phone clients connect here.

Run it on a tiny VM or at home behind Tailscale. State stays on the gateway host.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import mimetypes
import time
from collections import deque
from collections.abc import Mapping
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
from ..costs import outcome_unknown_item, request_cost_summary, task_cost_item
from ..models import ApprovalRequest, AskRequest, RunnerUnavailable, Task, TaskResult, new_id, waiting
from ..adapters import get_adapter, read_only_refusal
from ..orchestrator.cso import Orchestrator, holds_session
from ..research.packs import check_configured_packs
from ..request_status import is_active_request, is_terminal_request
from ..settings import Settings
from ..security import token_matches
from ..store import StateStore
from ..util import short

log = logging.getLogger(__name__)
# #126: a snapshot goes to every client on each connect, so a long follow-up answer travels as its head only.
# The full answer stays on the request (GET /api/requests/{id}); the web loads it when the PI opens it.
SNAPSHOT_ANSWER_CHARS = 2000
# Terminal reports can be as large as 20,000 characters and repeat once per finished request.
SNAPSHOT_REPORT_CHARS = 2000
# A step card shows this much of a task's result text; a replayed task.result needs no more.
SNAPSHOT_RESULT_CHARS = 500
MAX_PI_NOTES = 20


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
    if event.get("type") in {"request.completed", "request.failed"}:
        shortened, changed = dict(data), False
        for field in ("report", "report_appendix"):
            value = data.get(field)
            if isinstance(value, str) and len(value) > SNAPSHOT_REPORT_CHARS:
                shortened[field] = value[:SNAPSHOT_REPORT_CHARS]
                shortened[f"{field}_truncated"] = True
                shortened.setdefault(f"{field}_chars", len(value))
                if event.get("request_id"):
                    shortened.setdefault(f"{field}_api", f"/api/requests/{event['request_id']}")
                changed = True
        if changed:
            return {**event, "data": shortened}
    text = data.get("text")
    if event.get("type") == "task.result" and isinstance(text, str) and len(text) > SNAPSHOT_RESULT_CHARS:
        return {**event, "data": {**data, "text": text[:SNAPSHOT_RESULT_CHARS], "text_truncated": True,
                                  "text_chars": len(text)}}
    return event


def snapshot_reports(request: dict) -> dict:
    """Stored terminal reports for snapshots, bounded exactly like replayed terminal events."""
    data = {key: request[key] for key in ("report", "report_appendix")
            if isinstance(request.get(key), str)}
    if not data:
        return {}
    return snapshot_event({"type": "request.completed", "request_id": request.get("id"), "data": data})["data"]


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
    cso_model: str | None = None  # request-local; checked against orchestrator.cso_models
    meta: dict[str, str] = {}  # benchmark case id 등 요청 출처


class FollowupIn(BaseModel):
    text: str = Field(max_length=4000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("follow-up text is empty")
        return value.strip()


class NoteIn(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note text is empty")
        return value.strip()


class CodexReviewIn(BaseModel):
    note: str = ""


DECISION_CHOICES = ("approve", "revise", "deny")


class DecisionIn(BaseModel):
    approved: bool
    note: str = ""
    # CP2 evidence review reads only this; the note never decides (#90). Other approvals ignore it.
    choice: Literal["approve", "revise", "deny"] | None = None


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
        self.session_revision = 0
        self.session_changed = asyncio.Event()
        self.task_runner: dict[str, str] = {}
        self.clients: set[WebSocket] = set()
        self.futures: dict[str, asyncio.Future] = {}
        self.jobs_waiters: dict[str, asyncio.Future] = {}
        self.jobs_done: dict[str, dict] = self.store.all("jobs_done")
        self.ask_waiters: dict[str, asyncio.Future] = {}
        self.ask_tasks: dict[str, asyncio.Task] = {}
        self.terminal_queued: set[int] = set()
        self.terminal_tasks: dict[str, asyncio.Task] = {}
        self.pending_committed: deque[tuple[dict, tuple[WebSocket, ...]]] = deque()
        self.recovery_steps: set[str] = set()
        self.recovered_tasks: set[str] = set()
        self.quota_events: dict[str, asyncio.Event] = {}
        self.approvals: dict[str, dict] = self.store.all("approval")
        self.requests: dict[str, dict] = self.store.all("request")
        self.last_runner_rosters: dict[str, dict] = self.store.all("runner_roster")
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
            if is_active_request(req.get("status")) and req.get("status") != "waiting_quota":
                req["status"] = "interrupted"
                self.save_request(rid)
            stale = [f for f in req.get("followups") or [] if f.get("status") == "running"]
            for followup in stale:  # its task future died with the old process; the PI can ask again
                followup.update(status="interrupted", error="gateway restarted before the answer arrived")
            if stale:
                self.save_request(rid)
            if not is_active_request(req.get("status")) and req.get("quota_waits"):
                # Only a follow-up parks on a finished request, and it was interrupted above; its wait must not
                # hold the engine or send the finished request back through run_request (#302 review).
                req.pop("quota_waits")
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

    def quota_hold(self, engine: str) -> dict | None:
        """The account-wide hold for an engine, derived from durable per-request waits."""
        waits = [entry for req in self.requests.values() for entry in (req.get("quota_waits") or {}).values()
                 if entry.get("engine") == engine]
        if not waits:
            return None
        return {"engine": engine, "resume_at": max(float(item["resume_at"]) for item in waits),
                "deadline_at": min(float(item["deadline_at"]) for item in waits)}

    def _remove_quota_wait(self, rid: str, step_id: str) -> dict | None:
        req = self.requests[rid]
        entry = (req.get("quota_waits") or {}).pop(step_id, None)
        if not req.get("quota_waits"):
            req.pop("quota_waits", None)
            if req.get("status") == "waiting_quota":
                req["status"] = "running"
        self.save_request(rid)
        return entry

    async def release_quota(self, engine: str, *, manual: bool) -> list[tuple[str, str]]:
        """Release an account hold and every step sharing that engine."""
        released = []
        # publish() yields, and a new request may arrive meanwhile: walk a snapshot (#302 review).
        for rid, req in list(self.requests.items()):
            for step_id, entry in list((req.get("quota_waits") or {}).items()):
                if entry.get("engine") != engine:
                    continue
                self._remove_quota_wait(rid, step_id)
                released.append((rid, step_id))
                await self.publish({"type": "request.step_quota_resumed", "ts": time.time(),
                                    "request_id": rid, "data": {"step_id": step_id, "engine": engine,
                                                                  "manual": manual}})
        event = self.quota_events.pop(engine, None)
        if event:
            event.set()
        return released

    async def wait_quota(self, rid: str, step_id: str, engine: str, *, resume_at: float,
                         deadline_at: float, reason: str) -> bool:
        """Park like an HPC wait. False means the bounded wait expired."""
        req = self.requests[rid]
        entry = {"engine": engine, "resume_at": resume_at, "deadline_at": deadline_at,
                 "reason": short(reason, 500), "waiting_since": time.time()}
        previous = (req.get("quota_waits") or {}).get(step_id)
        req.setdefault("quota_waits", {})[step_id] = entry
        if is_active_request(req.get("status")):
            # A follow-up on a finished request parks without reopening it: done/failed stays (#302 review).
            req["status"] = "waiting_quota"
        self.save_request(rid)
        if previous != entry:
            await self.publish({"type": "request.step_quota_wait", "ts": time.time(), "request_id": rid,
                                "data": {"step_id": step_id, "engine": engine,
                                         "resume_at": resume_at, "deadline_at": deadline_at}})
        while True:
            hold = self.quota_hold(engine)
            if hold is None:
                return True
            now = time.time()
            if now >= deadline_at:
                self._remove_quota_wait(rid, step_id)
                return False
            if now >= hold["resume_at"]:
                await self.release_quota(engine, manual=False)
                return True
            event = self.quota_events.setdefault(engine, asyncio.Event())
            try:
                await asyncio.wait_for(event.wait(), min(hold["resume_at"], deadline_at) - now)
            except asyncio.TimeoutError:
                continue

    async def force_quota_resume(self, rid: str, step_id: str) -> list[tuple[str, str]]:
        entry = (self.requests[rid].get("quota_waits") or {}).get(step_id)
        if not entry:
            raise KeyError(step_id)
        return await self.release_quota(entry["engine"], manual=True)

    async def resume_quota_request(self, rid: str) -> None:
        """After a gateway restart, recover at once without a PI approval.

        The durable waits stay. Each recovered step meets its own engine's hold in run_step, so steps on
        other engines and checkpoints that already arrived do not wait for that quota.
        """
        req = self.requests[rid]
        if not req.get("quota_waits"):
            req["status"] = "interrupted"
            self.save_request(rid)
            return
        await self.resume_when_ready(rid)

    def _session_state_changed(self) -> None:
        self.session_revision += 1
        self.session_changed.set()

    async def _wait_session_change(self, revision: int, timeout: float | None = None) -> bool:
        if self.session_revision != revision:
            return True
        self.session_changed.clear()
        if self.session_revision != revision:
            return True
        try:
            await asyncio.wait_for(self.session_changed.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def has_seen_agent(self, agent_id: str) -> bool:
        return any(any(agent.get("id") == agent_id for agent in entry.get("agents", []))
                   for entry in self.last_runner_rosters.values())

    def running_tasks(self) -> list[dict]:
        """Accepted tasks of running requests, including steps waiting for jobs or ask answers."""
        selected: dict[tuple[str, str], tuple[tuple[int, float], dict]] = {}
        for tid, entry in self.store.all("task").items():
            req = self.requests.get(entry.get("request_id"), {})
            if not entry.get("accepted") or not is_active_request(req.get("status")):
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
        for sid in (req.get("quota_waits") or {}):
            if states.get(sid, "pending") == "pending":
                states[sid] = "waiting_quota"
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
                "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary"),
                "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True), "bundle_path": req.get("bundle_path"),
                "bundle_status": req.get("bundle_status"), "bundle_warning": req.get("bundle_warning")}

    def clear_step_jobs(self, rid: str, step_id: str) -> None:
        # A jobs.finished checkpoint remains useful until the resulting step is adopted.
        for tid, task in self.store.all("task").items():
            if task.get("request_id") == rid and (task.get("step_id") or task.get("kind")) == step_id:
                if tid in self.jobs_done:
                    self.store.delete("jobs_done", tid)
                    self.jobs_done.pop(tid, None)

    def _bundle_outcome(self, rid: str, request: dict | None = None, tasks: dict | None = None,
                        runner_capabilities: dict | None = None) -> dict:
        """Build against a terminal snapshot; safe to call in a worker thread."""
        try:
            from ..request_bundle import RemoteRunnerBundle, build_request_bundle

            bundled = build_request_bundle(
                copy.deepcopy(self.requests[rid]) if request is None else request,
                self.s,
                copy.deepcopy(self.store.all("task")) if tasks is None else tasks,
                copy.deepcopy(self.runner_capabilities) if runner_capabilities is None else runner_capabilities,
            )
            return {"bundle": bundled}
        except RemoteRunnerBundle:
            return {"warning": "runner가 다른 PC라 묶음을 만들지 않음", "appendix": True}
        except Exception as exc:
            return {"warning": f"요청 묶음을 만들지 못했습니다: {exc}"}

    def _apply_bundle_outcome(self, rid: str, data: dict, outcome: dict) -> None:
        bundled = outcome.get("bundle")
        if bundled:
            self.requests[rid]["bundle_path"] = data["bundle_path"] = bundled["path"]
            self.requests[rid]["bundle_status"] = data["bundle_status"] = bundled["status"]
            self.requests[rid].pop("bundle_warning", None)
            return
        warning = str(outcome["warning"])
        if outcome.get("appendix"):
            appendix = str(self.requests[rid].get("report_appendix") or "")
            if warning not in appendix:
                appendix = appendix.rstrip() + ("\n\n" if appendix.strip() else "") + warning
                self.requests[rid]["report_appendix"] = appendix
                if isinstance(data.get("report_appendix"), str) and not data.get("report_appendix_truncated"):
                    data["report_appendix"] = appendix
        self.requests[rid].pop("bundle_path", None)
        self.requests[rid].pop("bundle_status", None)
        data.pop("bundle_path", None)
        data.pop("bundle_status", None)
        self.requests[rid]["bundle_warning"] = data["bundle_warning"] = warning
        log.warning("request bundle unavailable for %s: %s", rid, warning)

    def _commit_bundle_record(self, rid: str, data: dict) -> None:
        """Checkpoint the post-terminal bundle outcome with exactly one separate event."""
        self.requests[rid]["updated_at"] = time.time()
        event = self.store.commit_request_event(
            rid, self.requests[rid],
            {"type": "request.bundle", "ts": time.time(), "request_id": rid,
             "data": {key: data[key] for key in ("bundle_path", "bundle_status", "bundle_warning") if key in data}},
            self.s.gateway.event_buffer,
        )
        self.events.append(event)
        self.pending_committed.append((event, tuple(self.clients)))
        asyncio.get_running_loop().create_task(self._send_committed_event())
        try:
            self.rounds.write(rid)
        except OSError as exc:
            log.warning("round record bundle update failed for %s: %s", rid, exc)

    def commit_terminal(self, rid: str, typ: str, data: dict) -> None:
        """Synchronous checkpoint used by direct callers and tests."""
        if not self._has_bundle_workdir(rid):
            self._apply_bundle_outcome(rid, data, self._bundle_outcome(rid))
            self._commit_terminal_record(rid, typ, data)
            return
        self._commit_terminal_record(rid, typ, data)
        self._apply_bundle_outcome(rid, data, self._bundle_outcome(rid))
        self._commit_bundle_record(rid, data)

    def _has_bundle_workdir(self, rid: str) -> bool:
        results = (self.requests[rid].get("results") or {}).values()
        return any(isinstance(result, Mapping) and isinstance(result.get("workdir_id"), str)
                   and result["workdir_id"] for result in results)

    def schedule_terminal(self, rid: str, typ: str, data: dict) -> asyncio.Task | None:
        """Create a terminal bundle off the gateway event loop, once per request."""
        prior = self.terminal_tasks.get(rid)
        if prior is not None:
            return prior
        if not self._has_bundle_workdir(rid):
            self.commit_terminal(rid, typ, data)
            return None
        self._commit_terminal_record(rid, typ, data)
        request = copy.deepcopy(self.requests[rid])
        tasks = copy.deepcopy(self.store.all("task"))
        runner_capabilities = copy.deepcopy(self.runner_capabilities)
        task = asyncio.get_running_loop().create_task(
            self._commit_terminal_async(rid, data, request, tasks, runner_capabilities))
        self.terminal_tasks[rid] = task
        task.add_done_callback(self._terminal_task_done)
        return task

    @staticmethod
    def _terminal_task_done(task: asyncio.Task) -> None:
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("terminal checkpoint failed: %s", exc)

    async def _commit_terminal_async(self, rid: str, data: dict, request: dict, tasks: dict,
                                     runner_capabilities: dict) -> None:
        outcome = await asyncio.to_thread(
            self._bundle_outcome, rid, request, tasks, runner_capabilities)
        self._apply_bundle_outcome(rid, data, outcome)
        self._commit_bundle_record(rid, data)

    def _commit_terminal_record(self, rid: str, typ: str, data: dict) -> None:
        # Failure never changes the request outcome, and terminal requests loaded after a restart do not pass this
        # checkpoint again.
        event = self.store.commit_terminal(rid, self.requests[rid],
                                           {"type": typ, "ts": time.time(), "request_id": rid,
                                            "data": copy.deepcopy(data)},
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
        if req.get("mode") == "plan_only":
            if "plan" in req:
                return set()
            needed = {self.s.orchestrator.cso_agent}
            if self.s.orchestrator.chief_of_staff_agent:
                needed.add(self.s.orchestrator.chief_of_staff_agent)
            return needed
        steps = req.get("plan", {}).get("steps") or []
        done = set(req.get("results") or {})
        needed = {s["agent_id"] for s in steps if s["id"] not in done}
        phase = (req.get("review_progress") or {}).get("phase")
        needed.add(self.s.orchestrator.cso_agent)  # an unresolved review still gets the CSO's report (F5)
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
        self._session_state_changed()

    def _record_task_cost(self, rid: str | None, tid: str, result: TaskResult, *,
                          outcome_unknown: bool = False) -> None:
        """Count a task's cost on its request once (#270).

        An unreported amount is estimated from a dated price table or kept as unaccounted, never added as $0.
        A task whose outcome the gateway gave up on is unaccounted even though no result reported a cost.
        """
        if rid not in self.requests or not tid:
            return
        req = self.requests[rid]
        costs = req.setdefault("cost_by_task", {})
        if tid in costs:
            return
        agent = self.agents.get(result.agent_id)
        item = outcome_unknown_item(result, agent) if outcome_unknown else task_cost_item(result, agent)
        amount = float(item["usd"]) if item["status"] != "unknown" else 0.0
        costs[tid] = amount
        req.setdefault("cost_items", {})[tid] = item
        req["cost_usd"] = float(req.get("cost_usd") or 0) + amount
        req["cost_known"] = req.get("cost_known", True) and item["status"] != "unknown"
        summary = request_cost_summary(req)
        if summary is None:
            req.pop("cost_summary", None)
        else:
            req["cost_summary"] = summary
        req["usage_known"] = req.get("usage_known", True) and result.usage_known and not outcome_unknown
        req.setdefault("usage_by_task", {})[tid] = result.usage
        totals: dict[str, int] = {}
        for usage in req["usage_by_task"].values():
            for key, count in usage.items():
                totals[key] = totals.get(key, 0) + count
        req["usage"] = totals
        self.save_request(rid)

    def _abandon_previous_generation(self, tid: str, entry: dict) -> TaskResult:
        agent_id = (entry.get("payload") or {}).get("agent_id") or "unknown"
        result = TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                            error="runner generation changed; prior task delivery or outcome unknown; "
                                  "manual recovery required")
        self.store.put("task", tid, {**entry, "completed": True, "abandoned": True,
                                     "result": result.model_dump(mode="json")})
        self._record_task_cost(entry.get("request_id"), tid, result, outcome_unknown=True)
        future = self.futures.get(tid)
        if future and not future.done():
            future.set_result(result)
        self._session_state_changed()
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
        roster = {"agents": agents, "updated_at": time.time()}
        self.last_runner_rosters[runner_id] = roster
        self.store.put("runner_roster", runner_id, roster)
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
        self._session_state_changed()

    def unregister_runner(self, runner_id: str, ws: WebSocket) -> bool:
        if self.runners.get(runner_id) is ws:
            self.runners.pop(runner_id, None)  # keep roster + pending futures: the runner will reconnect
            self.runner_incarnations.pop(runner_id, None)
            for aid, host in self.agent_runner.items():
                if host == runner_id:
                    self.agent_online.setdefault(aid, asyncio.Event()).clear()
            self._session_state_changed()
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
        pipeline_event = None
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
            if (result.pipeline_submission is not None and self.s.policy.bioinfo_agent.pipeline_pr
                    and result.ok and not waiting(result)):
                self.store.put("pipeline_submission", tid, {
                    "request_id": rid, "task_id": tid, "agent_id": result.agent_id,
                    "state": "ready", "submission": result.pipeline_submission,
                })
                pipeline_event = {"type": "pipeline.ready", "ts": time.time(), "task_id": tid,
                                  "agent_id": result.agent_id, "request_id": rid,
                                  "data": {"name": result.pipeline_submission.get("name")}}
            if result.pipeline_submission is not None:  # never published to web clients, on or off
                result = result.model_copy(update={"pipeline_submission": None})
                msg = {**msg, "data": result.model_dump(mode="json")}
            if task:
                # A reported result supersedes an abandonment: the task did finish (#112).
                self.store.put("task", tid, {**{k: v for k, v in task.items() if k != "abandoned"},
                                              "completed": True, "result": result.model_dump(mode="json")})
                payload = task.get("payload") or {}
                meta = payload.get("meta") or {}
                request = self.requests.get(task.get("request_id") or "")
                if (request is not None and payload.get("agent_id") == self.s.orchestrator.cso_agent and
                        result.session_id and
                        (payload.get("resume_session_id") == request.get("cso_session_id") or
                         (meta.get("workdir") and meta.get("workdir") == request.get("cso_workdir")))):
                    request["cso_session_id"] = result.session_id
                    request["cso_workdir"] = result.workdir or meta.get("workdir") or request.get("cso_workdir")
                    self.save_request(task["request_id"])
                self._session_state_changed()
            if task and task.get("request_id") in self.requests and (task.get("step_id") or task.get("kind") == "direct"):
                rid = task["request_id"]
                sid = task.get("step_id") or "direct"
                self.store.put("step_checkpoint", f"{rid}:{sid}",
                               {"task_id": tid, "attempt": task.get("attempt", 1),
                                "revision": task.get("revision", 0),
                                "result": result.model_dump(mode="json")})
            self._record_task_cost(rid, tid, result)
            fut = self.futures.pop(msg.get("task_id") or "", None)
            if fut and not fut.done():
                fut.set_result(result)
        elif typ == "approval.requested":
            a = msg["data"]
            self.approvals[a["id"]] = {"approval": a, "origin": runner_id}
            self.save_approval(a["id"])
        elif typ == "approval.timed_out":
            aid = str((msg.get("data") or {}).get("id") or "")
            entry = self.approvals.get(aid)
            prior = self.store.get("approval_decision", aid)
            queued = self.store.get("decision", aid)
            approval = (entry or {}).get("approval")
            same_origin = ((entry or {}).get("origin") == runner_id or
                           (queued or {}).get("origin") == runner_id or
                           (prior or {}).get("origin") == runner_id)
            if approval is None and prior:
                approval = prior.get("approval")
            if approval is None or not same_origin:
                if runner_seq is not None:
                    await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})
                return
            self.approvals.pop(aid, None)
            self.store.delete("approval", aid)
            self.store.delete("decision", aid)
            self.store.put("approval_decision", aid,
                           {"approval": approval, "origin": runner_id, "approved": False,
                            "note": "approval timed out", "state": "timed_out",
                            "decided_at": time.time()})
            msg = {**msg, "type": "approval.resolved",
                   "data": {"id": aid, "approved": False, "note": "approval timed out",
                            "state": "timed_out"}}
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
                self._session_state_changed()
        await self.publish(msg, runner_id=runner_id, runner_seq=runner_seq)
        if pipeline_event is not None:
            await self.publish(pipeline_event)
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
            # Nothing was sent, so nothing was spent: a real $0, not an unaccounted cost (#270).
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                              error=f"no runner hosts agent {task.agent_id!r}")
        if task.agent_id == "bioinfo-agent" and self.s.policy.bioinfo_agent.pipeline_pr:
            # Off by default (#300). The gateway's one setting decides; a runner sends pipeline files only when asked.
            task.meta["pipeline_pr"] = True
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
        self._session_state_changed()
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
                    result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                                        error="task delivery uncertain; manual recovery required")
                    self._record_task_cost(task.request_id, task.id, result, outcome_unknown=True)
                    return result
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
                    result = TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                                        error="recovery runner unavailable; manual restart required")
                    # Dispatched before the restart and never answered: the runner may have spent anything (#270).
                    self._record_task_cost(request_id, tid, result, outcome_unknown=True)
                    return result
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
        announced = False
        while True:
            # One ledger scan discovers both an already-finished turn and the holders for this
            # session. Once holders are known, only their rows are read until a turn rotates the
            # session; task and runner events wake the waiter (#205, #207).
            ledger = self.store.all("task")
            completed = []
            for entry in ledger.values():
                payload = entry.get("payload") or {}
                result = entry.get("result") or {}
                held_workdir = (payload.get("meta") or {}).get("workdir")
                same_request = request_id is None or entry.get("request_id") == request_id
                if (same_request and payload.get("agent_id") == agent_id and entry.get("completed") and
                        not entry.get("abandoned") and result.get("session_id") and
                        ((session_id and payload.get("resume_session_id") == session_id) or
                         (workdir and held_workdir and Path(workdir).resolve() == Path(held_workdir).resolve()))):
                    completed.append(entry)
            if completed:
                latest = max(completed, key=lambda entry: float(entry.get("dispatched_at") or 0))
                result = latest.get("result") or {}
                turned = result.get("session_id")
                turned_workdir = result.get("workdir") or ((latest.get("payload") or {}).get("meta") or {}).get(
                    "workdir") or workdir
                if turned and turned != session_id:
                    session_id, workdir = turned, turned_workdir
                    offline_deadline = None
                    continue
            holder_ids = [tid for tid, entry in ledger.items()
                          if holds_session(entry, agent_id, session_id, workdir)]
            while holder_ids:
                revision = self.session_revision
                holders = [(tid, self.store.get("task", tid) or {}) for tid in holder_ids]
                holders = [(tid, entry) for tid, entry in holders
                           if holds_session(entry, agent_id, session_id, workdir)]
                if not holders:
                    # The known holders finished, but a continuation (ask, job, retry) may already hold
                    # the session under a new task id: rescan the ledger once before releasing it.
                    break
                runner = self.agent_runner.get(agent_id)
                online = runner in self.runners
                if online:
                    offline_deadline = None
                elif offline_deadline is None:
                    offline_deadline = loop.time() + self.s.gateway.resume_wait_s
                for tid, entry in holders:
                    running = (online and bool(entry.get("accepted")) and
                               self._same_runner_generation(entry, runner, self.runner_incarnations.get(runner)))
                    if entry.get("abandoned"):
                        return None, None
                    if not (running or (online and tid in self.futures) or
                            (not online and loop.time() < offline_deadline)):
                        return None, None
                if not announced:
                    announced = True
                    await self.publish({"type": "request.step_wait", "ts": time.time(),
                                        "request_id": request_id,
                                        "data": {"step_id": step_id, "agent_id": agent_id,
                                                 "reason": "an earlier task still uses this session or workdir"}})
                timeout = None if online else max(0.0, offline_deadline - loop.time())
                if not await self._wait_session_change(revision, timeout):
                    return None, None

                turns = [entry for _, entry in ((tid, self.store.get("task", tid) or {})
                                                 for tid in holder_ids)
                         if entry.get("completed") and not entry.get("abandoned") and
                         (entry.get("result") or {}).get("session_id")]
                if turns:
                    latest = max(turns, key=lambda entry: float(entry.get("dispatched_at") or 0))
                    result = latest.get("result") or {}
                    turned = result.get("session_id")
                    if turned and turned != session_id:
                        session_id = turned
                        workdir = result.get("workdir") or workdir
                        offline_deadline = None
                        break
            else:
                return session_id, workdir

    async def wait_jobs(self, task_id: str) -> dict:
        if task_id in self.jobs_done:
            return self.jobs_done[task_id]
        fut = self.jobs_waiters.setdefault(task_id, asyncio.get_running_loop().create_future())
        return await fut

    def _start_ask(self, ask: AskRequest, runner_id: str) -> None:
        def request_status():
            return self.requests.get(ask.request_id, {}).get("status") if ask.request_id else "running"

        status = request_status()
        if not is_active_request(status) and not is_terminal_request(status):
            return
        previous = self.ask_tasks.get(ask.id)
        if previous and not previous.done():
            if not is_terminal_request(status):
                return
            previous.cancel()

        async def route() -> None:
            # Recheck after scheduling: a resume decision or terminal checkpoint may intervene.
            status = request_status()
            if is_terminal_request(status):
                request = self.requests.get(ask.request_id, {})
                await self.resolve_ask(ask, runner_id, ask_result(
                    reason=f"request {status}: {request.get('error') or 'request ended'}", **{"from": "labhq"}))
                return
            if not is_active_request(status):
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

    async def resolve_approval(self, approval_id: str, approved: bool, note: str = "",
                               choice: str | None = None) -> None:
        entry = self.approvals.pop(approval_id, None)
        if entry is None:
            raise KeyError(approval_id)
        chosen = {"choice": choice} if choice in DECISION_CHOICES else {}
        self.store.delete("approval", approval_id)
        self.store.put("approval_decision", approval_id,
                       {"approval": entry["approval"], "origin": entry["origin"], "approved": approved,
                        "note": note, **chosen, "decided_at": time.time()})
        if entry["origin"]:
            self.store.put("decision", approval_id, {"origin": entry["origin"],
                                                      "approved": approved, "note": note})
            if entry["origin"] in self.runners:
                await self.flush_decisions(entry["origin"])
        elif entry.get("future") and not entry["future"].done():
            entry["future"].set_result({"approved": approved, "note": note, **chosen})
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
                            "data": {"id": approval_id, "approved": approved, "note": note, **chosen}})
        if a.get("kind") == "resume" and not approved:
            rid = a["request_id"]
            self.requests[rid].update(status="failed", error="resume declined", finished_at=time.time())
            self.schedule_terminal(rid, "request.failed", {"error": "resume declined"})

    # ----- requests -----
    def create_request(self, body: RequestIn) -> str:
        if body.mode == "direct" and body.cso_model:
            raise ValueError("cso_model is only valid for orchestrate or plan_only requests")
        if body.cso_model and body.cso_model not in self.s.orchestrator.cso_models:
            allowed = ", ".join(self.s.orchestrator.cso_models) or "(none)"
            raise ValueError(f"cso_model {body.cso_model!r} is not allowed; allowed models: {allowed}")
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
        if not is_terminal_request(req.get("status")):
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

    async def add_note(self, rid: str, text: str) -> dict:
        """Store a PI note for later turns without interrupting the turn already running."""
        req = self.requests[rid]
        if is_terminal_request(req.get("status")):
            raise ValueError("요청이 끝났습니다. 이어 묻기를 쓰세요")
        if req.get("mode") == "direct":
            raise ValueError("직접 맡긴 요청에는 실행 중 메모를 보낼 수 없습니다. 끝난 뒤 이어 묻기를 쓰세요")
        notes = req.setdefault("pi_notes", [])
        if len(notes) >= MAX_PI_NOTES:
            raise ValueError(f"메모는 요청마다 {MAX_PI_NOTES}개까지 보낼 수 있습니다")
        entry = {"id": new_id("note"), "text": text.strip(), "at": time.time()}
        notes.append(entry)
        self.save_request(rid)
        await self.publish({"type": "request.note", "ts": entry["at"], "request_id": rid, "data": entry})
        return entry

    async def _start_request(self, rid: str) -> None:
        r = self.requests[rid]
        await self.publish({"type": "request.created", "ts": time.time(), "request_id": rid,
                            "data": {k: r.get(k) for k in ("text", "mode", "agent_id", "project_id", "references")}})
        await self.orchestrator.run_request(rid)

    def pipeline_prs(self) -> dict[str, dict]:
        """Each request's latest pipeline PR status from its stored row, so a late client sees it (#301 review)."""
        latest: dict[str, dict] = {}
        for entry in self.store.all("pipeline_submission").values():
            rid = entry.get("request_id")
            if not rid or entry.get("state") not in {"pending", "open", "rejected"}:
                continue
            if rid not in latest or (entry.get("updated_at") or 0) >= (latest[rid].get("updated_at") or 0):
                latest[rid] = entry
        return {rid: {"status": e["state"], "name": (e.get("submission") or {}).get("name"),
                      "reason": e.get("reason"), "url": e.get("url"), "number": e.get("number")}
                for rid, e in latest.items()}

    def snapshot(self) -> dict[str, Any]:
        pipeline_prs = self.pipeline_prs()
        recent_events = list(self.events)[-200:]
        recent_terminals = {
            event.get("request_id") for event in recent_events
            if event.get("type") in {"request.completed", "request.failed"}
        }
        return {"type": "snapshot", "schema_version": 1, "seq": self.store.event_bounds()[1],
                "ts": time.time(), "data": {
            "agents": list(self.agents.values()),
            "runners": list(self.runners),
            "approvals": [e["approval"] for e in self.approvals.values()],
            "requests": [{**{k: v for k, v in r.items() if k in ("id", "text", "status", "mode", "created_at",
                                                                  "project_id", "plan", "cost_usd", "cost_known",
                                                                  "cost_summary", "usage", "usage_known", "agent_id",
                                                                  "references", "pi_notes", "bundle_path", "bundle_status",
                                                                  "bundle_warning")},
                          # the full list and full answers stay on the request (GET /api/requests/{id})
                          "followups": [snapshot_followup(f) for f in (r.get("followups") or [])[-20:]],
                          "step_status": self.request_summary(r)["step_progress"]["steps"],
                          "step_details": self.request_step_details(r.get("id", ""), r),
                          **({} if r.get("id") in recent_terminals else snapshot_reports(r)),
                          **({"pipeline_pr": pipeline_prs[r["id"]]} if r.get("id") in pipeline_prs else {}),
                          "review": r.get("review") or (r.get("review_progress") or {}).get("review")}
                         for r in self.requests.values()],
            "projects": [{"id": p.id, "name": p.name or p.id, "repo": p.repo, "visibility": p.visibility}
                         for p in self.s.projects],
            "default_references": [r.model_dump() for r in self.s.pi_profile.references],
            "running_tasks": self.running_tasks(),
            "recent_events": [snapshot_event(e) for e in recent_events],
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
            quota = (request.get("quota_waits") or {}).get(sid) or {}
            latest = max(matches, key=lambda pair: pair[1].get("dispatched_at", 0), default=(None, {}))
            details[sid] = {
                "task_id": result.get("task_id") or latest[0],
                "attempts": max((int(entry.get("attempt") or 1) for _, entry in matches), default=0),
                "outputs": result.get("outputs") or [],
                "missing_outputs": result.get("missing_outputs") or [],
                "text": short(result.get("text") or "", SNAPSHOT_RESULT_CHARS),
                "error": result.get("error") or "",
                "quota_resume_at": quota.get("resume_at"),
                "quota_engine": quota.get("engine"),
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
            if is_terminal_request(request.get("status")):
                hub._restart_request_asks(rid)
            elif request.get("status") == "waiting_quota":
                asyncio.create_task(hub.resume_quota_request(rid))

    @app.on_event("shutdown")
    async def warn_running_on_shutdown() -> None:
        loop = asyncio.get_running_loop()
        local_tasks = [task for task in hub.terminal_tasks.values() if task.get_loop() is loop]
        if local_tasks:
            await asyncio.gather(*local_tasks, return_exceptions=True)
        running = [r["id"] for r in hub.requests.values() if is_active_request(r.get("status"))]
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
                        await hub.resolve_approval(str(msg.get("id")), bool(msg.get("approved")), msg.get("note", ""),
                                                   msg.get("choice"))  # an unknown choice is dropped there
                    except KeyError:  # already decided elsewhere (another device, timeout) — keep the socket
                        await ws.send_text(json.dumps({"type": "approval.stale", "ts": time.time(),
                                                       "data": {"id": msg.get("id")}},
                                                      ensure_ascii=False, default=str))
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
        return [hub.request_summary(r) for r in requests
                if status == "all" or (status == "running" and is_active_request(r.get("status")))
                or r.get("status") == status][:limit]

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
            if hub.semantics_shadow is not None:  # semantics-hook: actions
                hub.semantics_shadow.after_followup(rid, None, "refused")  # semantics-hook: actions
            raise HTTPException(409, str(e))
        if hub.semantics_shadow is not None:  # semantics-hook: actions
            hub.semantics_shadow.after_followup(rid, entry["id"], "asked")  # semantics-hook: actions
        return {"request_id": rid, "followup_id": entry["id"]}

    @app.post("/api/requests/{rid}/notes", dependencies=[Depends(auth)])
    async def add_note(rid: str, body: NoteIn) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            entry = await hub.add_note(rid, body.text)
        except ValueError as error:
            raise HTTPException(409, str(error))
        return {"request_id": rid, "note": entry}

    @app.get("/api/requests/{rid}", dependencies=[Depends(auth)])
    async def get_request(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        return hub.requests[rid]

    @app.get("/api/requests/{rid}/audit-bundle", dependencies=[Depends(auth)])
    async def audit_bundle(rid: str) -> Response:
        if rid not in hub.requests:
            raise HTTPException(404)
        from ..evidence.audit import bundle_bytes, verify_request

        report = await asyncio.to_thread(verify_request, hub.requests[rid], settings)
        payload = await asyncio.to_thread(bundle_bytes, report, hub.requests[rid])
        return Response(payload, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="labhq-audit.zip"'})

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
            await hub.resolve_approval(aid, body.approved, body.note, body.choice)
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

    @app.post("/api/requests/{rid}/steps/{step_id}/resume-quota", dependencies=[Depends(auth)])
    async def resume_quota(rid: str, step_id: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            released = await hub.force_quota_resume(rid, step_id)
        except KeyError:
            raise HTTPException(409, "step is not waiting for quota")
        return {"ok": True, "released": [{"request_id": request_id, "step_id": sid}
                                           for request_id, sid in released]}

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
                "active_requests": sum(is_active_request(r.get("status")) for r in hub.requests.values()),
                "running_tasks": len(hub.running_tasks())}

    @app.get("/", response_class=HTMLResponse)
    async def office() -> HTMLResponse:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        boot_data = {"mode": "live"}
        if settings.instance:
            boot_data["instance"] = settings.instance
        boot = f'<script>window.LABHQ_BOOT={json.dumps(boot_data, separators=(",", ":"))}</script>'
        if settings.instance:
            html = html.replace("<title>labhq 사무실</title>", f"<title>labhq {settings.instance} 사무실</title>")
        return HTMLResponse(html.replace("<!--LABHQ_BOOT-->", boot), headers={"Cache-Control": "no-cache"})

    # Like /, static shells are public; data and decisions require the client token.
    @app.get("/3d")
    async def office3d_redirect(request: Request) -> RedirectResponse:
        query = str(request.url.query)
        return RedirectResponse("/3d/" + ("?" + query if query else ""))

    @app.get("/3d/", response_class=HTMLResponse)
    async def office3d() -> HTMLResponse:
        html = (WEB / "lab3d" / "index.html").read_text(encoding="utf-8")
        boot_data = {"mode": "live"}
        if settings.instance:
            boot_data["instance"] = settings.instance
        boot = f'<script>window.LABHQ_BOOT={json.dumps(boot_data, separators=(",", ":"))}</script>'
        if settings.instance:
            html = html.replace("<title>labhq · 종이숲 연구소</title>",
                                f"<title>labhq {settings.instance} · 종이숲 연구소</title>")
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
        data = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
        if settings.instance:
            data.update({"name": f"labhq {settings.instance} 사무실", "short_name": f"labhq {settings.instance}"})
        return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json")

    @app.get("/icon.svg")
    async def icon() -> Response:
        return Response((WEB / "icon.svg").read_text(encoding="utf-8"), media_type="image/svg+xml")

    return app
