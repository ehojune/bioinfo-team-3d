"""Gateway: the one reachable endpoint. Runners dial in; desktop/phone clients connect here.

Run it on a tiny VM or at home behind Tailscale. State stays on the gateway host.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import hashlib
import json
import logging
import math
import mimetypes
import os
import time
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal
from weakref import WeakValueDictionary

import anyio
import httpx
import yaml
from fastapi import BackgroundTasks, Request, Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

from ..integrations.github import ProjectReporter
from ..intake import MAX_REFERENCES, Reference, effective_references
from ..instance_guard import public_identity
from ..interruption import ShutdownInterrupted
from ..integrations.rounds import RoundRecorder, environment_snapshot, is_research_event, is_research_task
from ..integrations.visibility import dev_log_visibility
from ..login import login_command
from ..ask_results import ask_result, read_ask_results, rejected_step
from ..bioinfo_policy import PIPELINE_AUTHORS
from ..costs import (outcome_unknown_item, request_cost_summary, request_cost_totals,
                     request_session_cost_totals, task_cost_item)
from ..models import ApprovalRequest, AskRequest, RunnerNotSent, RunnerUnavailable, Task, TaskResult, new_id, waiting
from ..pi_text import with_approval_copy
from ..recruit.provenance import not_yet_active, probation_refusal
from ..recruit import paper2agent
from ..adapters import enforces_read_only, get_adapter, read_only_refusal
from ..hooks.control import STEER_LIMIT
from ..orchestrator.cso import Orchestrator, _terminal_reports, environment_problem, holds_session, solo_phase
from ..research import continuation as research_continuation
from ..research import final_checkpoint as cp4
from ..research.contract import classify_intake, plan_sha256
from ..research.packs import check_configured_packs
from ..request_status import (has_active_followup, is_active_followup, is_active_request,
                              is_office_request, is_terminal_request)
from ..request_persistence import apply_changes
from ..settings import Settings
from ..security import token_matches
from .. import durable
from ..store import StateStore
from ..util import short
from .shutdown import ShutdownIn, WebShutdown

log = logging.getLogger(__name__)
# #126: a snapshot goes to every client on each connect, so a long follow-up answer travels as its head only.
# The full answer stays on the request (GET /api/requests/{id}); the web loads it when the PI opens it.
SNAPSHOT_ANSWER_CHARS = 2000
CLIENT_SEND_TIMEOUT_S = 10.0
# Terminal reports can be as large as 20,000 characters and repeat once per finished request.
SNAPSHOT_REPORT_CHARS = 2000
# A step card shows this much of a task's result text; a replayed task.result needs no more.
SNAPSHOT_RESULT_CHARS = 500
MAX_PI_NOTES = 20
# A kept task cancel outlives any runner turn (runner.task_timeout_s defaults to 6 h) before it is dropped.
TASK_CANCEL_KEEP_S = 2 * 86400
JOBS_CANCEL_KEEP_S = 8 * 86400  # a local job may run up to 7 days: keep its cancel longer than that
def external_wait_view(wait: object, *, now: float | None = None) -> dict | None:
    """The wait of a step's open public lookup, with the time since its last progress event added (#676)."""
    if not isinstance(wait, dict):
        return None
    try:
        elapsed, limit = float(wait["elapsed_s"]), float(wait["timeout_s"])
        since = max(0.0, (time.time() if now is None else now) - float(wait.get("at") or 0))
    except (KeyError, TypeError, ValueError):
        return None
    return {"tool": str(wait.get("tool") or ""), "elapsed_s": int(elapsed + since), "timeout_s": int(limit)}


FINISHING_STUCK_S = 600  # a request-bundle worker older than this no longer holds `down` back (#658)
# Cards that decide the request itself (PI 점검 R5). They wait policy.approvals.pi_decision_timeout_s instead of the gate
# timeout, the request waits as waiting_pi meanwhile, and an unanswered card ends as *_timed_out, never as a deny.
PI_DECISION_KINDS = frozenset({"clarify", "question", "scope", "budget", "research_plan", "research_evidence",
                               "research_continue", "resume", "result_storage", "result_publish", "recruit_plan",
                               "literature_pdf"})
# Runner cards that only inform: no hook waits on them, so their task's result does not close them (#521).
RUNNER_NOTICE_KINDS = frozenset({"codex_sandbox_setup"})
# Steer, pause, resume and halt of a running task (#59 ①); a steer message is at most this long. The gateway keeps each
# step's steer queue (STEER_LIMIT, oldest dropped first), pause flag and halt requests until they are used.
CONTROL_ACTIONS = ("steer", "pause", "resume", "halt")
STEER_MAX_CHARS = 2000
HALTED_ERROR = "PI가 멈춤(halt): 세션을 보존했습니다. 재개하면 같은 세션으로 이어 갑니다"
# Only a plan step's turns and a direct request's turn take controls; consults, follow-ups, reviews and the rest share
# no step that a control could wait on (PR #532 verification).
NOT_CONTROLLABLE = "이 작업({kind})은 실행 중 제어를 받지 않습니다. 방향·정지·멈춤은 계획 단계 작업과 직접 맡긴 작업에만 됩니다"
HALT_WHILE_WAITING = "HPC 잡·질문 대기 중에는 멈출 수 없습니다 — 일시 정지를 쓰세요"
# How long request cancel waits for the cancelled turns to report, so their cost is counted (R13).
CANCEL_RESULT_WAIT_S = 10.0
# A PDF upload succeeds only after the runner confirms its workspace write.
ATTACHMENT_ACK_TIMEOUT_S = 30.0
# Statuses a hold sets and _sync_hold_status may lift again.
WSL_BOUND_AGENTS = frozenset({"bioinfo-agent"})  # staff that only run in the local WSL runner (#612)
HOLD_STATES = frozenset({"waiting_login", "waiting_quota", "waiting_facilities_fix", "waiting_pi"})


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
        for field in ("report", "report_appendix", "execution_record"):
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
    data = {key: request[key] for key in ("report", "report_appendix", "execution_record")
            if isinstance(request.get(key), str)}
    if isinstance(request.get("report_package"), dict):
        data["report_package"] = request["report_package"]
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
    route: Literal["auto", "team"] = "auto"
    meta: dict[str, str] = {}  # benchmark case id 등 요청 출처


class FollowupIn(BaseModel):
    text: str = Field(max_length=4000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("follow-up text is empty")
        return value.strip()


class ActiveCancelIn(BaseModel):
    expected_created_at: float = Field(gt=0, allow_inf_nan=False)


class NoteIn(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note text is empty")
        return value.strip()


class SteerIn(BaseModel):
    text: str = Field(max_length=STEER_MAX_CHARS)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("steer text is empty")
        return value.strip()


class CodexReviewIn(BaseModel):
    note: str = ""


DECISION_CHOICES = ("approve", "revise", "deny")


class DecisionIn(BaseModel):
    approved: bool
    note: str = ""
    storage: dict | None = None
    model_refusal: dict | None = None
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

    async def aset(self, key: str, value: TaskResult) -> None:
        hub, rid = self.hub, self.rid
        if not hasattr(hub, "asave_request"):  # In-memory bench/test adapter.
            self[key] = value
            return
        value = value.model_copy(deep=True)
        hub._request_changed(rid)
        async with hub._request_lock(rid):
            before = copy.deepcopy(hub.requests[rid])
            updated = copy.deepcopy(before)
            updated.setdefault("results", {})[key] = value.model_dump(mode="json")
            updated.get("pending_revisions", {}).pop(key, None)
            cancelled = await hub._save_request_snapshot(rid, before, updated)
            super().__setitem__(key, value)
        await hub.aclear_step_jobs(rid, key)
        if cancelled:
            raise asyncio.CancelledError

    async def apop(self, key: str, *default):
        hub, rid = self.hub, self.rid
        if not hasattr(hub, "asave_request"):
            return self.pop(key, *default)
        hub._request_changed(rid)
        async with hub._request_lock(rid):
            value = dict(self).pop(key, *default)  # Validate dict.pop's arity and missing-key contract first.
            before = copy.deepcopy(hub.requests[rid])
            updated = copy.deepcopy(before)
            removed = (updated.get("results") or {}).pop(key, None)
            cancelled = False
            if removed is not None:
                cancelled = await hub._save_request_snapshot(rid, before, updated)
            super().pop(key, *default)
        if cancelled:
            raise asyncio.CancelledError
        return value

    async def adelete(self, key: str) -> None:
        await self.apop(key)


# "the caller did not say what the runner reports": only the runner ID is compared (Hub._session_pin_refusal).
_RUNTIME_UNCHECKED = object()

class Hub:
    result_review_required = True

    def __init__(self, settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.session_started_at = time.time()
        self.stopping = False
        self.store = StateStore(settings.path(settings.gateway.state_dir) / "gateway.sqlite3")
        from ..knowledge.recall import KnowledgeRecall
        self.knowledge = KnowledgeRecall(self)
        self.event_lock = asyncio.Lock()
        self.request_locks: dict[str, asyncio.Lock] = {}
        self.request_versions: dict[str, int] = {}
        self.runners: dict[str, WebSocket] = {}
        self.runner_incarnations: dict[str, str | None] = {}
        # Per runner process (hello "process"): the incarnation is kept on disk across restarts, this is not (#521).
        self.runner_processes: dict[str, str | None] = {}
        self.runner_locks: dict[str, asyncio.Lock] = {}
        self.runner_agents: dict[str, list[dict]] = {}
        self.runner_capabilities: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        self.agent_runner: dict[str, str] = {}
        self.agent_online: dict[str, asyncio.Event] = {}
        self.session_revision = 0
        self.session_changed = asyncio.Event()
        self.task_runner: dict[str, str] = {}
        self.task_breaker: dict[str, dict] = {}  # the restriction (episode, stage) each task's runner last reported
        self.clients: set[WebSocket] = set()
        self.futures: dict[str, asyncio.Future] = {}
        self.attachment_waiters: dict[str, tuple[str, int, str, asyncio.Future]] = {}
        self.jobs_waiters: dict[str, asyncio.Future] = {}
        self.jobs_done: dict[str, dict] = self.store.all("jobs_done")
        self.ask_waiters: dict[str, asyncio.Future] = {}
        self.ask_tasks: dict[str, asyncio.Task] = {}
        self.terminal_queued: set[int] = set()
        self.terminal_tasks: dict[str, asyncio.Task] = {}
        self.terminal_started: dict[str, float] = {}  # when each bundle worker began, for a stuck finish (#658)
        self.pending_committed: deque[tuple[dict, tuple[WebSocket, ...]]] = deque()
        self.recovery_steps: set[str] = set()
        # When each resume began: only a task dispatched before it is a prior attempt to adopt. A task this run
        # dispatched itself (the re-plan after a clarify answer) must not replay its own earlier call.
        self.recovery_started: dict[str, float] = {}
        self.recovered_tasks: set[str] = set()
        self.quota_events: dict[str, asyncio.Event] = {}
        self.login_events: dict[str, asyncio.Event] = {}
        self.facilities_timeout_tasks: dict[str, asyncio.Task] = {}
        self.resume_timers: dict[str, asyncio.Task] = {}  # a resume card has no waiter; this ends it (R5)
        self.request_tasks: dict[str, asyncio.Task] = {}  # each request's orchestration, for request cancel (R13)
        # #59 ①: steps the PI halted, parked until the PI resumes them, and each task's last control state.
        self.halted_waits: dict[str, asyncio.Event] = {}
        self.task_controls: dict[str, dict] = self.store.all("task_control_state")
        # Steps whose next turn waits for the PI's resume, and the events that release them (#59 ①).
        self.step_resume_events: dict[str, asyncio.Event] = {}
        self.step_pause_waits: dict[str, dict] = {}
        # Recruitments a runner process is running: {runner_id: {"incarnation", "process", "count"}}. Started here,
        # ended by recruit.done/failed or by a different runner process; a reconnect of the same process keeps them
        # (#515). The incarnation survives a restart, so the process id decides when the runner sends one (#521).
        self.recruits_running: dict[str, dict[str, Any]] = {}
        self.cancel_result_wait_s = CANCEL_RESULT_WAIT_S
        self.approvals: dict[str, dict] = self.store.all("approval")
        self.requests: dict[str, dict] = self.store.all("request")
        from ..pi_language import PiInterpreter
        self.pi_interpreter = PiInterpreter(self)
        self.final_review_timers: dict[str, asyncio.Task] = {}
        self.login_notices: set[str] = set()  # computed after stale login waits are dropped below
        self.last_runner_rosters: dict[str, dict] = self.store.all("runner_roster")
        self.events: deque = deque(maxlen=settings.gateway.event_buffer)
        self.events.extend(self.store.events_since(max(0, self.store.event_bounds()[1] - settings.gateway.event_buffer)))
        # No runner is connected to a fresh process. An open page reconnects with `since` and replays events, not a
        # snapshot, so a runner the old process saw online, or one it may hold from an earlier snapshot, needs a
        # runner.offline here or the page keeps hiding 러너 꺼짐 (R15, #521). A runner whose last event is that is done.
        last_seen = {str((ev.get("data") or {}).get("runner_id")): ev["type"] for ev in self.events
                     if ev.get("type") in {"runner.online", "runner.offline"}}
        for runner_id in sorted(set(last_seen) | set(self.last_runner_rosters)):
            if last_seen.get(runner_id) != "runner.offline":
                self.events.append(self.store.append_event({"type": "runner.offline", "ts": time.time(),
                    "data": {"runner_id": runner_id, "reason": "gateway_restarted"}}, settings.gateway.event_buffer))
        # A finished request is never resumed: its resume card (made before it finished) goes, with a record (#607).
        for rid in {e["approval"].get("request_id") for e in self.approvals.values() if e["approval"].get("kind") == "resume"}:
            if is_terminal_request((self.requests.get(rid) or {}).get("status")):
                self.retire_resume_cards(rid, "재시작 때 이미 끝난 요청이라 재개 카드를 닫았습니다")
        for aid, entry in list(self.approvals.items()):
            card = entry["approval"]
            rid = card.get("request_id")
            saved = (self.requests.get(rid) or {}).get("result_storage")
            selection = (self.requests.get(rid) or {}).get("storage_selection_receipt") or {}
            if (card.get("kind") == "result_storage" and saved and
                    ((card.get("detail") or {}).get("late_storage") or selection.get("approval_id") == aid)):
                # select() may have committed before the old process removed its card. Replay both updates
                # before deleting it, so another interruption can safely retry this reconciliation.
                note = "저장된 선택으로 저장 위치 카드를 복구했습니다."
                answer = {"id": aid, "state": "reconciled", "note": note, "storage": saved}
                self.store.put("approval_decision", aid, {"approval": card, "origin": entry.get("origin"),
                    **answer, "decided_at": time.time()})
                for event in ({"type": "request.storage", "data": {"result_storage": saved}},
                              {"type": "approval.resolved", "data": answer}):
                    self.events.append(self.store.append_event({**event, "ts": time.time(), "request_id": rid},
                                                               settings.gateway.event_buffer))
                self.store.delete("approval", aid)
                self.approvals.pop(aid)
                continue
            if (entry.get("origin") is None and entry["approval"].get("kind") not in {
                    "resume", "facilities_fix", "pipeline_contribute", "model_refused"}
                    and not (entry["approval"].get("detail") or {}).get("late_storage")):
                self.approvals.pop(aid)
                self.store.delete("approval", aid)
                self.store.put("approval_decision", aid, {"approval": entry["approval"],
                                                         "approved": False, "note": "gateway restarted",
                                                         "state": "expired", "decided_at": time.time()})
                self.events.append(self.store.append_event({"type": "approval.expired", "ts": time.time(),
                    "request_id": entry["approval"].get("request_id"), "data": {"id": aid}},
                    settings.gateway.event_buffer))
        bound = settings.policy.approvals.pi_decision_timeout_s
        for aid, entry in self.approvals.items():
            card = entry["approval"]
            if (card.get("detail") or {}).get("late_storage") and "timeout_s" in card:
                card.pop("timeout_s")  # PI-opened choices remain answerable, including older saved cards.
                self.save_approval(aid)
                self.events.append(self.store.append_event({"type": "approval.requested", "ts": time.time(),
                    "request_id": card.get("request_id"), "data": card}, settings.gateway.event_buffer))
            if card.get("kind") == "resume" and card.get("timeout_s") != bound:
                card["timeout_s"] = bound  # a card saved before it had this bound; the web counts it down
                self.save_approval(aid)
        for rid, req in self.requests.items():
            cp4.reconcile(req)
            if cp4.enabled(req):
                self.save_request(rid)
            if (req.pop("pi_waits", None) is not None) | (req.pop("runner_waits", None) is not None):
                self.save_request(rid)  # their cards and waiters died with the old process
            if is_active_request(req.get("status")) and req.get("status") not in {
                    "waiting_quota", "waiting_login", "waiting_facilities_fix"}:
                self.interrupt_request(rid)
            stale = [f for f in req.get("followups") or [] if is_active_followup(f)]
            for followup in stale:  # its task future died with the old process; the PI can ask again
                followup.update(status="interrupted", error="gateway restarted before the answer arrived")
            if stale:
                self.save_request(rid)
            if not is_active_request(req.get("status")) and req.get("quota_waits"):
                # Only a follow-up parks on a finished request, and it was interrupted above; its wait must not
                # hold the engine or send the finished request back through run_request (#302 review).
                req.pop("quota_waits")
                self.save_request(rid)
            if not is_active_request(req.get("status")) and req.get("login_waits"):
                req.pop("login_waits")
                self.save_request(rid)
        # Only waits that survived the cleanup above hold an engine notice; a finished request's interrupted
        # follow-up must not keep the next request from getting its first login notice (PR #398 review).
        self.login_notices = {str(entry.get("engine") or "") for req in self.requests.values()
                              for entry in (req.get("login_waits") or {}).values()}
        for rid, req in self.requests.items():
            if req.get("status") == "interrupted":
                self.interrupt_request(rid)
        self.orchestrator = Orchestrator(self)
        self._attach_resume_plans()
        self.reporter = ProjectReporter(self, settings, github_transport)
        from ..result_storage import ResultStorage
        self.result_storage = ResultStorage(self, github_transport)
        self.rounds = RoundRecorder(self, github_transport)
        self.semantics_shadow = None  # semantics-hook
        if _semantics_wanted(getattr(settings, "semantics", None)):  # semantics-hook: any spelling of off never imports it
            from ..research.semantics_shadow import ShadowService  # semantics-hook
            self.semantics_shadow = ShadowService.start(self)  # semantics-hook
        for rid, req in self.requests.items():
            if req.get("status") == "interrupted":
                self.rounds.write(rid)

    def save_request(self, rid: str) -> None:
        self._request_changed(rid)
        cp4.reconcile(self.requests[rid])
        self.requests[rid]["updated_at"] = time.time()
        self.store.put("request", rid, self.requests[rid])

    def _request_lock(self, rid: str) -> asyncio.Lock:
        if not hasattr(self, "request_locks"):
            self.request_locks = {}
        return self.request_locks.setdefault(rid, asyncio.Lock())

    def _request_changed(self, rid: str) -> int:
        if not hasattr(self, "request_versions"):
            self.request_versions = {}
        revision = self.request_versions.get(rid, 0) + 1
        self.request_versions[rid] = revision
        return revision

    async def _save_request_snapshot(self, rid: str, before: dict, updated: dict) -> bool:
        cp4.reconcile(updated)
        updated["updated_at"] = time.time()
        _, cancelled = await self._publication_write(self.store.aput("request", rid, updated))
        apply_changes(self.requests[rid], before, updated)
        return cancelled

    async def asave_request(self, rid: str) -> None:
        revision = self._request_changed(rid)
        _, cancelled = await self._publication_write(self._asave_request(rid, revision))
        if cancelled:
            raise asyncio.CancelledError

    async def _asave_request(self, rid: str, revision: int) -> None:
        async with self._request_lock(rid):
            before = copy.deepcopy(self.requests[rid])
            previous = self.store.get("request", rid)
            try:
                cancelled = await self._save_request_snapshot(rid, before, copy.deepcopy(before))
            except Exception:
                if self.request_versions.get(rid) == revision:
                    if previous is not None:
                        apply_changes(self.requests[rid], before, previous)
                    elif self.requests[rid] == before:
                        self.requests.pop(rid, None)
                raise
        if cancelled:
            raise asyncio.CancelledError

    async def aupdate_request(self, rid: str, update: Callable[[dict], Any]):
        self._request_changed(rid)
        async with self._request_lock(rid):
            before = copy.deepcopy(self.requests[rid])
            updated = copy.deepcopy(before)
            answer = update(updated)
            cancelled = await self._save_request_snapshot(rid, before, updated)
        if cancelled:
            raise asyncio.CancelledError
        return answer

    def review_done(self, rid: str, *, decision: str = "accepted", revision: int | None = None,
                    target_sha256: str | None = None) -> dict:
        """PI acknowledgment is independent of execution and never blocks another request."""
        plan = self._review_plan(rid, decision, revision=revision, target_sha256=target_sha256)
        note = ("보고서를 검토해 재개 카드를 닫았습니다" if plan[-1] else
                "보고서를 검토 완료로 표시해 재개 카드를 닫았습니다")
        self.retire_resume_cards(rid, note)
        return self._commit_review(rid, plan)

    def _final_review(self, rid: str, decision: str, **target) -> dict:
        plan = self._review_plan(rid, decision, final=True, **target)
        self.retire_resume_cards(rid, "보고서를 검토해 재개 카드를 닫았습니다")
        return self._commit_review(rid, plan)

    def _review_plan(self, rid: str, decision: str, *, revision=None, target_sha256=None, final=False):
        before = copy.deepcopy(self.requests[rid])
        contract = before.get("research_contract") or {}
        timeout_ack = (before.get("status") == "failed" and
                       (contract.get("final_receipt") or {}).get("decision") == "timed_out"
                       and revision is None and target_sha256 is None)
        final = final or (cp4.enabled(before) and bool(contract.get("final_draft")) and not timeout_ack)
        updated = copy.deepcopy(before)
        if final:
            receipt = cp4.decide(updated, decision, revision=revision, target_sha256=target_sha256)
            answer = {"request_id": rid, "status": updated["status"], "pi_review_pending": False,
                      "pi_reviewed_at": before.get("pi_reviewed_at"), "final_checkpoint": cp4.summary(updated)}
            if before.get("pi_reviewed_at") and before == updated:
                return before, None, None, answer, True
            at = receipt["decided_at"]
            reviewed_at = None if decision == "timed_out" else at
            audit = {"actor": receipt["decider"], "at": at, "outcome": updated["status"], "receipt": receipt}
        else:
            if not is_terminal_request(before.get("status")) or before.get("cancelling"):
                raise ValueError("보고서가 끝난 요청만 검토 완료로 표시합니다")
            answer = {"request_id": rid, "pi_review_pending": False, "pi_reviewed_at": before.get("pi_reviewed_at")}
            if before.get("pi_reviewed_at"):
                return before, None, None, answer, False
            at = reviewed_at = time.time()
            audit = {"actor": "pi", "at": at, "outcome": before["status"]}
        updated.update(pi_review_pending=reviewed_at is None, pi_reviewed_at=reviewed_at, updated_at=at)
        updated.setdefault("pi_review_audit", []).append(audit)
        answer.update(pi_review_pending=reviewed_at is None, pi_reviewed_at=reviewed_at)
        if final:
            updated["bundle_pending"] = bool(before.get("bundle_path") or before.get("bundle_pending")
                                             or self._has_bundle_workdir(rid))
            answer["final_checkpoint"] = cp4.summary(updated)
            data = {**audit, **answer}
        else:
            data = {**audit, "pi_reviewed_at": at, "pi_review_pending": False}
        body = {"type": "request.reviewed", "ts": at, "request_id": rid, "data": data}
        return before, updated, body, answer, final

    def _apply_review(self, rid: str, plan, event: dict, *, owner=None, queue_delivery=True) -> bool:
        before, updated, _, _, final = plan
        apply_changes(self.requests[rid], before, updated)
        self._record_published_event(event)
        self._queue_committed_event(event)
        self._schedule_committed_events()
        if final:
            self.knowledge.notify()
            if queue_delivery:
                self._queue_terminal_delivery(event)
            timer = self.final_review_timers.pop(rid, None)
            if timer and timer is not (owner or asyncio.current_task()):
                timer.cancel()
            if updated["bundle_pending"]:
                asyncio.get_running_loop().create_task(self._refresh_final_bundle(rid))
        return not final or not updated["bundle_pending"]

    def _commit_review(self, rid: str, plan) -> dict:
        _, updated, body, answer, final = plan
        if updated is not None:
            commit = self.store.commit_terminal if final else self.store.commit_request_event
            event = commit(rid, updated, body, self.s.gateway.event_buffer)
            if self._apply_review(rid, plan, event):
                self.result_storage.schedule(rid)
        return answer

    async def areview_done(self, rid: str, *, decision="accepted", revision=None, target_sha256=None):
        return await self._areview(rid, decision, revision=revision, target_sha256=target_sha256)

    async def _afinal_review(self, rid: str, decision: str, **target):
        return await self._areview(rid, decision, final=True, **target)

    async def _areview(self, rid, decision, **target):
        answer, cancelled = await self._publication_write(
            self._areview_checkpoint(rid, decision, owner=asyncio.current_task(), **target))
        if cancelled:
            raise asyncio.CancelledError
        return answer

    async def _areview_checkpoint(self, rid: str, decision: str, *, owner=None, **target):
        self._request_changed(rid)
        cancelled, schedule_storage = False, False
        async with self._resume_card_locks(rid), self.event_lock, self._request_lock(rid):
            plan = self._review_plan(rid, decision, **target)
            note = ("보고서를 검토해 재개 카드를 닫았습니다" if plan[-1] else
                    "보고서를 검토 완료로 표시해 재개 카드를 닫았습니다")
            await self._aretire_resume_cards_locked(rid, note)
            _, updated, body, answer, final = plan
            if updated is not None:
                commit = self.store.acommit_terminal if final else self.store.acommit_request_event
                event, cancelled = await self._publication_write(commit(rid, updated, body, self.s.gateway.event_buffer))
                schedule_storage = self._apply_review(rid, plan, event, owner=owner, queue_delivery=False)
                if final:
                    await self._aqueue_terminal_delivery(event)
        if schedule_storage:
            asynchronous = getattr(self.result_storage, "aschedule", None)
            if asynchronous is not None:
                await asynchronous(rid)
            else:
                self.result_storage.schedule(rid)
        if cancelled:
            raise asyncio.CancelledError
        return answer

    async def _refresh_final_bundle(self, rid: str) -> None:
        prior = self.terminal_tasks.get(rid)
        if prior:
            await asyncio.shield(prior)
            self.terminal_tasks.pop(rid, None)
            if not self.requests[rid].get("bundle_pending"):
                return
        self.requests[rid]["bundle_pending"] = True
        await self.asave_request(rid)
        await self._schedule_bundle(rid, {})

    def recover_final_reviews(self) -> None:
        for rid, req in self.requests.items():
            if req.get("status") == "review_pending" and rid not in self.final_review_timers:
                self.final_review_timers[rid] = asyncio.create_task(self._expire_final_review(rid))

    async def _expire_final_review(self, rid: str) -> None:
        try:
            draft = self.requests[rid]["research_contract"]["final_draft"]
            await asyncio.sleep(max(0, draft["deadline_at"] - time.time()))
            if self.requests[rid].get("status") == "review_pending":
                await self._afinal_review(rid, "timed_out")
        finally:
            self.final_review_timers.pop(rid, None)

    def quota_hold(self, engine: str) -> dict | None:
        """The account-wide hold for an engine, derived from durable per-request waits."""
        waits = [entry for req in self.requests.values() for entry in (req.get("quota_waits") or {}).values()
                 if entry.get("engine") == engine]
        if not waits:
            return None
        return {"engine": engine, "resume_at": max(float(item["resume_at"]) for item in waits),
                "deadline_at": min(float(item["deadline_at"]) for item in waits)}

    def login_hold(self, engine: str) -> dict | None:
        """The account-wide login hold for an engine, derived from durable waits."""
        waits = [entry for req in self.requests.values() for entry in (req.get("login_waits") or {}).values()
                 if entry.get("engine") == engine]
        if not waits:
            return None
        return {"engine": engine, "resume_at": max(float(item["resume_at"]) for item in waits),
                "deadline_at": min(float(item["deadline_at"]) for item in waits),
                "waiting_since": min(float(item.get("waiting_since") or time.time()) for item in waits)}

    def engine_login_holds(self) -> list[dict]:
        """One web/CLI notice card per held engine, never one per waiting request."""
        by_engine: dict[str, tuple[str, str, str | None]] = {}
        for rid, req in self.requests.items():
            for step_id, entry in (req.get("login_waits") or {}).items():
                by_engine.setdefault(str(entry.get("engine") or ""),
                                     (rid, step_id, entry.get("runner_id")))
        return [{"kind": "login", "engine": engine,
                 "command": self._login_command(engine, location[2]),
                 "request_id": location[0], "step_id": location[1]}
                for engine, location in sorted(by_engine.items())]

    def _login_command(self, engine: str, runner_id: str | None = None) -> str:
        """Build guidance for the runner whose turn waits, never for another host (PR #398 review).

        A named runner gives only its own capabilities; until it reconnects the guidance names the config key.
        Without a name, the engine's runner is used only when exactly one runner hosts that engine."""
        if runner_id:
            capabilities = self.runner_capabilities.get(runner_id)
        else:
            wanted = "claude_code" if engine == "claude" else engine
            hosts = {self.agent_runner.get(agent_id) or agent.get("runner_id")
                     for agent_id, agent in self.agents.items() if str(agent.get("engine") or "") == wanted}
            hosts.discard(None)
            only = next(iter(hosts)) if len(hosts) == 1 else None
            capabilities = self.runner_capabilities.get(only) if only else None
        return login_command(engine, self.s, capabilities, runner_known=capabilities is not None)

    def _sync_hold_status(self, req: dict) -> None:
        if not is_active_request(req.get("status")):
            return
        if req.get("login_waits"):
            req["status"] = "waiting_login"
        elif req.get("quota_waits"):
            req["status"] = "waiting_quota"
        elif any(entry.get("status") in {"waiting", "approved", "running"}
                 for entry in (req.get("facilities_fixes") or {}).values()):
            req["status"] = "waiting_facilities_fix"
        elif req.get("pi_waits"):
            req["status"] = "waiting_pi"
        elif req.get("runner_waits"):
            req["status"] = "waiting_for_runner"
        elif req.get("status") in HOLD_STATES:
            req["status"] = "running"

    def interrupt_request(self, rid: str, *, evidence: dict | None = None,
                          correction: bool = False) -> None:
        """Commit interruption and one resume card, usable both at startup and on the live loop."""
        req = self.requests[rid]
        before = req.get("status")
        previous = copy.deepcopy(req) if correction else None
        updated = {**req, "status": "interrupted", "updated_at": time.time()}
        if evidence is not None:
            updated["shutdown_evidence"] = evidence
        if correction:
            updated.update(pi_review_pending=False, pi_reviewed_at=None)
            updated.pop("finished_at", None)
            storage = updated.get("result_storage") or {}
            if storage:
                updated["result_storage"] = {key: value for key, value in storage.items()
                    if key in {"mode", "repo", "visibility", "allow_public", "branch"}}
                updated["result_storage"]["status"] = "selected"
        cp4.reconcile(updated)
        data = {"status": "interrupted", "previous": before}
        if evidence is not None:
            data.update(reason="shutdown", evidence=evidence)
        if correction:
            data.update(actor="pi", previous=previous)
        if before != "interrupted" or correction:
            event = self.store.commit_request_event(rid, updated,
                {"type": "request.corrected" if correction else "request.status", "ts": time.time(),
                 "request_id": rid, "data": data}, self.s.gateway.event_buffer)
            req.clear()
            req.update(updated)
            self.events.append(event)
            self.pending_committed.append((event, tuple(self.clients)))
        if not any(a["approval"].get("kind") == "resume" and a["approval"].get("request_id") == rid
                   for a in self.approvals.values()):
            approval = self.new_resume_approval(rid)
            event = self.store.append_event({"type": "approval.requested", "ts": time.time(),
                "request_id": rid, "data": with_approval_copy(approval.model_dump(mode="json"))},
                self.s.gateway.event_buffer)
            self.events.append(event)
            self.pending_committed.append((event, tuple(self.clients)))

    async def _set_status(self, rid: str, status: str, *, evidence: dict | None = None) -> None:
        """A status the gateway sets itself (resume, restart), told to open pages as request.status."""
        if status == "interrupted":
            self.interrupt_request(rid, evidence=evidence)
            await self._send_committed_event()
            await self.close_publish_cards(rid)
            return
        req = self.requests[rid]
        before = req.get("status")
        req["status"] = status
        if status == "failed":
            await self.close_publish_cards(rid)
        await self.asave_request(rid)
        if before != status:
            await self.publish({"type": "request.status", "ts": time.time(), "request_id": rid,
                                "data": {"status": status, "previous": before}})

    async def _publish_hold_status(self, rid: str | None, before: str | None = None) -> None:
        """Recompute a request's hold status, save it, and tell clients when it changed (R5)."""
        req = self.requests.get(rid or "")
        if req is None:
            return
        before = req.get("status") if before is None else before
        self._sync_hold_status(req)
        await self.asave_request(rid)
        if req.get("status") != before:
            await self.publish({"type": "request.status", "ts": time.time(), "request_id": rid,
                                "data": {"status": req.get("status"), "previous": before}})

    def _remove_quota_wait(self, rid: str, step_id: str) -> dict | None:
        req = self.requests[rid]
        entry = (req.get("quota_waits") or {}).pop(step_id, None)
        if not req.get("quota_waits"):
            req.pop("quota_waits", None)
        self._sync_hold_status(req)
        self.save_request(rid)
        return entry

    async def _aremove_quota_wait(self, rid: str, step_id: str) -> dict | None:
        def update(req):
            entry = (req.get("quota_waits") or {}).pop(step_id, None)
            if not req.get("quota_waits"):
                req.pop("quota_waits", None)
            self._sync_hold_status(req)
            return entry
        return await self.aupdate_request(rid, update)


    def _remove_login_wait(self, rid: str, step_id: str) -> dict | None:
        req = self.requests[rid]
        entry = (req.get("login_waits") or {}).pop(step_id, None)
        if not req.get("login_waits"):
            req.pop("login_waits", None)
        self._sync_hold_status(req)
        self.save_request(rid)
        return entry

    async def _aremove_login_wait(self, rid: str, step_id: str) -> dict | None:
        def update(req):
            entry = (req.get("login_waits") or {}).pop(step_id, None)
            if not req.get("login_waits"):
                req.pop("login_waits", None)
            self._sync_hold_status(req)
            return entry
        return await self.aupdate_request(rid, update)


    async def release_quota(self, engine: str, *, manual: bool) -> list[tuple[str, str]]:
        """Release an account hold and every step sharing that engine."""
        released = []
        # publish() yields, and a new request may arrive meanwhile: walk a snapshot (#302 review).
        for rid, req in list(self.requests.items()):
            for step_id, entry in list((req.get("quota_waits") or {}).items()):
                if entry.get("engine") != engine:
                    continue
                await self._aremove_quota_wait(rid, step_id)
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
        await self.asave_request(rid)
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
                await self._aremove_quota_wait(rid, step_id)
                return False
            if now >= hold["resume_at"]:
                await self.release_quota(engine, manual=False)
                return True
            event = self.quota_events.setdefault(engine, asyncio.Event())
            try:
                await asyncio.wait_for(event.wait(), min(hold["resume_at"], deadline_at) - now)
            except asyncio.TimeoutError:
                continue

    async def release_login(self, engine: str, *, manual: bool) -> list[tuple[str, str]]:
        """Release one engine login hold and all requests sharing that account."""
        released = []
        for rid, req in list(self.requests.items()):
            for step_id, entry in list((req.get("login_waits") or {}).items()):
                if entry.get("engine") != engine:
                    continue
                await self._aremove_login_wait(rid, step_id)
                released.append((rid, step_id))
                await self.publish({"type": "request.step_login_resumed", "ts": time.time(),
                                    "request_id": rid, "data": {"step_id": step_id, "engine": engine,
                                                                   "manual": manual}})
        event = self.login_events.pop(engine, None)
        if event:
            event.set()
        if released and manual:
            self.login_notices.discard(engine)
            await self.publish({"type": "engine.login_resumed", "ts": time.time(),
                                "data": {"engine": engine, "manual": manual}})
        return released

    async def login_recovered(self, engine: str, *, reason: str | None = None) -> None:
        """End one account notice when no request is still held for that engine."""
        if engine not in self.login_notices or self.login_hold(engine) is not None:
            return
        self.login_notices.discard(engine)
        await self.publish({"type": "engine.login_resumed", "ts": time.time(),
                            "data": {"engine": engine, "manual": False,
                                     **({"reason": reason} if reason else {})}})

    async def wait_login(self, rid: str, step_id: str, engine: str, *, resume_at: float,
                         deadline_at: float, reason: str, agent_id: str | None = None) -> bool:
        """Park until the PI logs in, the retry clock fires, or the bounded wait expires."""
        req = self.requests[rid]
        started = (((req.get("login_windows") or {}).get(engine) or {}).get("started_at") or time.time())
        runner_id = self.agent_runner.get(agent_id or "")
        entry = {"engine": engine, "resume_at": resume_at, "deadline_at": deadline_at,
                 "reason": short(reason, 500), "waiting_since": started,
                 **({"runner_id": runner_id} if runner_id else {})}
        command = self._login_command(engine, runner_id)
        previous = (req.get("login_waits") or {}).get(step_id)
        req.setdefault("login_waits", {})[step_id] = entry
        self._sync_hold_status(req)
        await self.asave_request(rid)
        if previous != entry:
            await self.publish({"type": "request.step_login_wait", "ts": time.time(), "request_id": rid,
                                "data": {"step_id": step_id, "engine": engine, "resume_at": resume_at,
                                         "deadline_at": deadline_at, "reason": short(reason, 500),
                                         "command": command}})
        # publish() now yields during storage. Claim the shared notice only after
        # that await, with no yield between checking and marking the engine.
        if engine not in self.login_notices:
            self.login_notices.add(engine)
            await self.publish({"type": "engine.login_wait", "ts": time.time(), "request_id": rid,
                                "data": {"engine": engine, "command": command,
                                         "request_id": rid, "step_id": step_id}})
        while True:
            hold = self.login_hold(engine)
            if hold is None:
                return True
            now = time.time()
            if now >= deadline_at:
                await self._aremove_login_wait(rid, step_id)
                return False
            if now >= hold["resume_at"]:
                await self.release_login(engine, manual=False)
                return True
            event = self.login_events.setdefault(engine, asyncio.Event())
            try:
                await asyncio.wait_for(event.wait(), min(hold["resume_at"], deadline_at) - now)
            except asyncio.TimeoutError:
                continue

    async def force_quota_resume(self, rid: str, step_id: str) -> list[tuple[str, str]]:
        entry = (self.requests[rid].get("quota_waits") or {}).get(step_id)
        if entry:
            return await self.release_quota(entry["engine"], manual=True)
        entry = (self.requests[rid].get("login_waits") or {}).get(step_id)
        if entry:
            return await self.release_login(entry["engine"], manual=True)
        raise KeyError(step_id)

    async def resume_held_request(self, rid: str) -> None:
        """Recover a quota/login/facilities parked request without another PI resume approval."""
        req = self.requests[rid]
        facilities = req.get("facilities_fixes") or {}
        facilities_hold = req.get("status") == "waiting_facilities_fix"
        changed = False
        for entry in facilities.values():
            decision = self.store.get("approval_decision", entry.get("approval_id"))
            if entry.get("status") == "waiting" and decision:
                entry.update(status="approved" if decision.get("approved") else (
                    "timed_out" if decision.get("state") == "timed_out" else "declined"),
                    approved=bool(decision.get("approved")), note=decision.get("note") or "",
                    state=decision.get("state") or "resolved")
                changed = True
        if changed:
            self._sync_hold_status(req)
            await self.asave_request(rid)
        # A still-open phone card is durable, but its old event-loop timer is not. Restore the remaining interval;
        # the timeout path saves a decision and wakes this request just like an approve/decline response.
        waiting = [entry for entry in facilities.values() if entry.get("status") == "waiting"]
        for entry in waiting:
            approval_id = str(entry.get("approval_id") or "")
            approval_entry = self.approvals.get(approval_id)
            if not approval_entry:
                continue
            approval = ApprovalRequest.model_validate(approval_entry["approval"])
            remaining = approval.created_at + approval.timeout_s - time.time()
            if remaining <= 0:
                await self._expire_restored_facilities_approval(approval_id)
                return
            if approval_id not in self.facilities_timeout_tasks:
                self.facilities_timeout_tasks[approval_id] = asyncio.create_task(
                    self._wait_restored_facilities_timeout(approval_id, remaining))
        if waiting:
            return
        facilities_resume = bool(facilities) and facilities_hold
        if not req.get("quota_waits") and not req.get("login_waits") and not facilities_resume:
            await self._set_status(rid, "interrupted")
            return
        await self.resume_when_ready(rid)

    def track_request(self, rid: str, coro) -> asyncio.Task:
        """Run a request's orchestration as one task, so request cancel can stop it (R13)."""
        task = asyncio.get_running_loop().create_task(coro)
        self.request_tasks[rid] = task

        def finished(done: asyncio.Task) -> None:
            if self.request_tasks.get(rid) is done:
                self.request_tasks.pop(rid, None)
        task.add_done_callback(finished)
        return task

    def _cancel_facilities_timeout(self, approval_id: str) -> None:
        task = self.facilities_timeout_tasks.pop(approval_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _wait_restored_facilities_timeout(self, approval_id: str, remaining: float) -> None:
        try:
            await asyncio.sleep(remaining)
            await self._expire_restored_facilities_approval(approval_id)
        except asyncio.CancelledError:
            return
        finally:
            self.facilities_timeout_tasks.pop(approval_id, None)

    async def _expire_restored_facilities_approval(self, approval_id: str) -> None:
        entry = self.approvals.get(approval_id)
        if entry is None or entry["approval"].get("kind") != "facilities_fix":
            return
        approval = entry["approval"]
        await self._timeout_approval(approval_id)
        await self.resume_held_request(approval["request_id"])

    async def resume_quota_request(self, rid: str) -> None:
        """After a gateway restart, recover at once without a PI approval.

        The durable waits stay. Each recovered step meets its own engine's hold in run_step, so steps on
        other engines and checkpoints that already arrived do not wait for that quota.
        """
        await self.resume_held_request(rid)

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
        return agent_id in self.effective_roster(include_history=True)

    def configured_agent_ids(self) -> set[str]:
        """Staff ids in this config's runner.agents_dir (core and active contracts). Empty when the folder is on
        another PC. Read-only: an expired contract is skipped, never archived here."""
        base = self.s.path(self.s.runner.agents_dir)
        contract = self.s.path(self.s.runner.contract_dir) if self.s.runner.contract_dir else base / "contract"
        ids: set[str] = set()
        for folder in (base / "core", contract):
            for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
                if path.name.startswith("_"):
                    continue
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    continue
                terms = data.get("contract") if isinstance(data, dict) else None
                if isinstance(terms, dict) and (terms.get("status") in ("expired", "archived") or
                                                float(terms.get("expires_at") or float("inf")) < time.time()):
                    continue
                if isinstance(data, dict) and isinstance(data.get("id"), str):
                    ids.add(data["id"])
        return ids

    def knows_agent(self, agent_id: str | None) -> bool:
        """A staff member a direct request may name: hosted now, in a stored runner roster, or in agents_dir. The
        request then waits for that runner (R15); only a name none of them has is unknown."""
        return bool(agent_id) and (agent_id in self.agents or self.has_seen_agent(agent_id) or
                                   agent_id in self.configured_agent_ids())

    def shutdown_readiness(self) -> dict:
        """What `labhq down` would cut off (#500): active requests, follow-ups still running on finished requests (a
        finished request is not active, so the request list missed them), recruitments the runner is running, and
        finished requests whose record is still being written (#521): a cancel waiting for its turns to report, or
        a request bundle being copied after the terminal record. A forced stop loses that record."""
        requests = [{"id": rid, "status": req.get("status"), "text": req.get("text")}
                    for rid, req in self.requests.items() if is_active_request(req.get("status"))]
        followups = [{"request_id": rid, "followup_id": entry.get("id"), "text": entry.get("text")}
                     for rid, req in self.requests.items() for entry in req.get("followups") or []
                     if entry.get("status") == "running"]
        recruits = sum(int(entry.get("count") or 0) for entry in self.recruits_running.values())
        finishing = [{"id": rid, "status": req.get("status"), "text": req.get("text"), "stage": "cancel"}
                     for rid, req in self.requests.items() if req.get("cancelling") is not None]
        now = time.time()
        for rid, task in self.terminal_tasks.items():
            if task.done():
                continue
            row = {"id": rid, "status": (self.requests.get(rid) or {}).get("status"),
                   "text": (self.requests.get(rid) or {}).get("text"), "stage": "bundle"}
            if rid in self.terminal_started:
                elapsed = max(0, int(now - self.terminal_started[rid]))
                # A bundle worker that has run this long made no progress worth waiting for: `down` does not wait for
                # it. The request keeps bundle_pending, so the next start rebuilds the bundle (#658).
                row.update(elapsed_s=elapsed, stuck=elapsed >= FINISHING_STUCK_S)
            finishing.append(row)
        finishing += [{"id": rid, "status": (self.requests.get(rid) or {}).get("status"),
                       "text": (self.requests.get(rid) or {}).get("text"), "stage": "result_storage"}
                      for rid, task in self.result_storage.tasks.items() if not task.done()]
        finishing += [{"id": rid, "status": (self.requests.get(rid) or {}).get("status"),
                       "text": (self.requests.get(rid) or {}).get("text"), "stage": "pi_interpretation"}
                      for task, rid in self.pi_interpreter.tasks.items() if not task.done()]
        return {"requests": requests, "followups": followups, "recruits": recruits, "finishing": finishing,
                "ready": not (requests or followups or recruits or
                              [row for row in finishing if not row.get("stuck")])}

    def running_tasks(self, tasks: Mapping[str, dict] | None = None) -> list[dict]:
        """Accepted tasks of running requests, including steps waiting for jobs or ask answers.

        The task ledger keeps every result and can reach tens of MB; decoding it on the event loop blocks every
        request and socket. So skip it when nothing is active, and let a caller that needs it for many requests
        pass one read instead of decoding it once per request (2026-10-08: an 11 s stall on web office connect).
        """
        if not any(is_active_request(req.get("status")) for req in self.requests.values()):
            return []
        selected: dict[tuple[str, str], tuple[tuple[int, float], dict]] = {}
        for tid, entry in (self.store.all("task") if tasks is None else tasks).items():
            if entry.get("kind") == "pi_interpretation":
                continue  # Display work never adds a step to the request's plan/progress.
            req = self.requests.get(entry.get("request_id"), {})
            if not entry.get("accepted") or not is_active_request(req.get("status")):
                continue
            if not entry.get("completed"):
                state = "running"
            elif (waiting(entry.get("result") or {}, jobs_finished=tid in self.jobs_done) and
                  (entry.get("step_id") or entry.get("kind")) not in (req.get("results") or {})):
                state = "hibernating"
            elif tid in self.halted_waits:  # halted by the PI, parked until resumed (#59 ①)
                state = "halted"
            else:
                continue
            step_id = entry.get("step_id") or entry.get("kind")
            task = {"id": tid, "request_id": entry.get("request_id"), "state": state,
                    "step_id": step_id, "agent_id": (entry.get("payload") or {}).get("agent_id"),
                    **({"kind": entry["kind"]} if entry.get("kind") else {}),
                    "dispatched_at": entry.get("dispatched_at", 0),
                    **({"control": self.task_controls[tid]} if tid in self.task_controls else {})}
            task["activity"] = ({"state": "hibernating",
                "jobs": (entry.get("result") or {}).get("pending_jobs", []),
                "asks": (entry.get("result") or {}).get("pending_asks", [])}
                if state == "hibernating" else entry.get("activity") or {})
            task["title"] = ((entry.get("payload") or {}).get("meta") or {}).get("title", "")
            key = (task["request_id"], step_id)
            rank = (1 if state == "running" else 0, float(task["dispatched_at"] or 0))
            if key not in selected or rank > selected[key][0]:
                selected[key] = (rank, task)
        for wait in self.step_pause_waits.values():  # a paused step's next turn, not sent yet (#59 ①)
            key = (wait.get("request_id"), wait.get("step_id"))
            if key not in selected and wait.get("task_id"):
                selected[key] = ((0, 0.0), {"id": wait["task_id"], "request_id": wait.get("request_id"),
                                            "state": "paused", "step_id": wait.get("step_id"),
                                            "kind": wait.get("kind"),
                                            "agent_id": wait.get("agent_id"), "dispatched_at": wait.get("since", 0),
                                            "control": {"paused": True}})
        return [selected[key][1] for key in sorted(selected, key=lambda k: (str(k[0]), str(k[1])))]

    def request_summary(self, req: dict, running: list[dict] | None = None) -> dict:
        """One request's step progress; pass ``running`` (from running_tasks) when summarising many requests."""
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
        for sid in (req.get("login_waits") or {}):
            if states.get(sid, "pending") == "pending":
                states[sid] = "waiting_login"
        for task in self.running_tasks() if running is None else running:
            if task["request_id"] == rid:
                sid = task["step_id"]  # running_tasks already resolved step_id or kind from the ledger entry
                if sid and states.get(sid, "pending") == "pending":
                    states[sid] = task["state"]
        # Failed steps whose cause is the runner PC (#35): `labhq status` shows "환경 문제: cause — hint".
        environment = {sid: found for sid, outcome in (req.get("results") or {}).items()
                       if isinstance(outcome, dict) and not outcome.get("ok")
                       and (found := environment_problem(outcome))}
        summary = {"id": rid, "status": req.get("status"), "text": short(req.get("text"), 120),
                   "created_at": req.get("created_at"), "updated_at": req.get("updated_at", req.get("created_at")),
                   "step_progress": {"done": sum(v in {"done", "failed", "skipped"} for v in states.values()),
                                     "total": len(steps) if steps else (1 if req.get("mode") == "direct" else 0),
                                     "steps": states}, "cost_usd": req.get("cost_usd", 0),
                   "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary"),
                   "usage": req.get("usage", {}),
                   "usage_known": req.get("usage_known", True), "bundle_path": req.get("bundle_path"),
                   "bundle_status": req.get("bundle_status"), "bundle_grade": req.get("bundle_grade"),
                   "bundle_grade_reasons": req.get("bundle_grade_reasons") or [],
                   "bundle_warning": req.get("bundle_warning")}
        waits = {task["step_id"]: wait for task in (self.running_tasks() if running is None else running)
                 if task.get("request_id") == rid and task.get("step_id")
                 and (wait := external_wait_view((task.get("activity") or {}).get("external_wait")))}
        if waits:
            summary["step_waits"] = waits
        summary["office_current"] = is_office_request(req)
        if req.get("outcome") == "model_refused":
            from ..model_refusal import REASON
            summary.update(outcome="model_refused", error=REASON)
        summary["session_current"] = (summary["office_current"]
                                      or float(req.get("created_at") or 0) >= self.session_started_at)
        summary.update(self.request_session_cost_fields(req))
        if environment:
            summary["step_environment"] = environment
        summary.update(final_checkpoint=cp4.summary(req), pi_review_pending=is_terminal_request(req.get("status")) and not req.get("pi_reviewed_at")
                       and not req.get("cancelling", False), pi_reviewed_at=req.get("pi_reviewed_at"))
        return summary

    def request_session_cost_fields(self, req: dict) -> dict:
        totals = request_session_cost_totals([req], self.session_started_at)
        return {
            "session_cost_usd": totals["cost_usd"],
            "session_cost_known": not (totals["unknown_count"] or totals["legacy_unknown"]),
            "session_cost_summary": {
                "estimated_usd": totals["estimated_usd"],
                "unknown_count": totals["unknown_count"],
            },
        }

    def clear_step_jobs(self, rid: str, step_id: str) -> None:
        # A jobs.finished checkpoint remains useful until the resulting step is adopted.
        for tid, task in self.store.all("task").items():
            if task.get("request_id") == rid and (task.get("step_id") or task.get("kind")) == step_id:
                if tid in self.jobs_done:
                    self.store.delete("jobs_done", tid)
                    self.jobs_done.pop(tid, None)

    async def aclear_step_jobs(self, rid: str, step_id: str) -> None:
        cancelled = False
        for tid, task in self.store.all("task").items():
            if task.get("request_id") == rid and (task.get("step_id") or task.get("kind")) == step_id:
                if tid in self.jobs_done:
                    previous = copy.deepcopy(self.jobs_done[tid])
                    _, interrupted = await self._publication_write(self.store.adelete("jobs_done", tid))
                    cancelled = cancelled or interrupted
                    if self.jobs_done.get(tid) == previous:
                        self.jobs_done.pop(tid, None)
        if cancelled:
            raise asyncio.CancelledError

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
        self._apply_bundle_to_request(self.requests[rid], data, outcome)

    def _apply_bundle_to_request(self, request: dict, data: dict, outcome: dict) -> None:
        rid = request.get("id", "unknown")
        bundled = outcome.get("bundle")
        if bundled:
            request["bundle_conversion"] = bundled.get("conversion_record") or ""
            request["bundle_path"] = data["bundle_path"] = bundled["path"]
            request["bundle_status"] = data["bundle_status"] = bundled["status"]
            request["bundle_grade"] = data["bundle_grade"] = bundled.get("grade")
            request["bundle_grade_reasons"] = data["bundle_grade_reasons"] = bundled.get("grade_reasons") or []
            if bundled.get("crate_warning"):
                warning = str(bundled["crate_warning"])
                request["bundle_warning"] = data["bundle_warning"] = warning
                log.warning("request bundle RO-Crate unavailable for %s: %s", rid, warning)
            else:
                request.pop("bundle_warning", None)
                data.pop("bundle_warning", None)
            return
        warning = str(outcome["warning"])
        if outcome.get("appendix"):
            appendix = str(request.get("execution_record") or "")
            if warning not in appendix:
                appendix = appendix.rstrip() + ("\n\n" if appendix.strip() else "") + warning
                request["execution_record"] = appendix
                if isinstance(data.get("execution_record"), str) and not data.get("execution_record_truncated"):
                    data["execution_record"] = appendix
        request.pop("bundle_path", None)
        request.pop("bundle_status", None)
        request.pop("bundle_grade", None)
        request.pop("bundle_grade_reasons", None)
        data.pop("bundle_path", None)
        data.pop("bundle_status", None)
        data.pop("bundle_grade", None)
        data.pop("bundle_grade_reasons", None)
        request["bundle_warning"] = data["bundle_warning"] = warning
        log.warning("request bundle unavailable for %s: %s", rid, warning)

    def _commit_bundle_record(self, rid: str, data: dict) -> None:
        """Checkpoint the post-terminal bundle outcome with exactly one separate event."""
        self.requests[rid].pop("bundle_pending", None)
        self.result_storage.schedule(rid)
        self.requests[rid]["updated_at"] = time.time()
        event = self.store.commit_request_event(
            rid, self.requests[rid],
            {"type": "request.bundle", "ts": time.time(), "request_id": rid,
             "data": {key: data[key] for key in ("bundle_path", "bundle_status", "bundle_grade",
                                                   "bundle_grade_reasons", "bundle_warning")
                      if key in data}},
            self.s.gateway.event_buffer,
        )
        self.events.append(event)
        self.pending_committed.append((event, tuple(self.clients)))
        asyncio.get_running_loop().create_task(self._send_committed_event())
        try:
            self.rounds.write(rid)
            self.rounds.submit(rid)
        except OSError as exc:
            log.warning("round record bundle update failed for %s: %s", rid, exc)

    def commit_terminal(self, rid: str, typ: str, data: dict) -> None:
        """Synchronous checkpoint used by direct callers and tests."""
        if not self.requests[rid].get("results") and not self._dispatched_work_task(rid):
            # No step or direct task was ever sent (CP1 수정 요청, cancelled or failed before work): nothing to
            # bundle, and a "작업 폴더를 찾지 못했습니다" warning would only be noise on the PI's request list. A
            # request cancelled mid-step can end with empty results but a real workdir, so it still tries (PR #529).
            self._commit_terminal_record(rid, typ, data)
            return
        if not self._has_bundle_workdir(rid):
            self._apply_bundle_outcome(rid, data, self._bundle_outcome(rid))
            self._commit_terminal_record(rid, typ, data)
            return
        self.requests[rid]["bundle_pending"] = True
        self._commit_terminal_record(rid, typ, data)
        self._apply_bundle_outcome(rid, data, self._bundle_outcome(rid))
        self._commit_bundle_record(rid, data)

    def _dispatched_work_task(self, rid: str) -> bool:
        return any(isinstance(task, Mapping) and task.get("request_id") == rid
                   and task.get("kind") in ("step", "direct") for task in self.store.all("task").values())

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
        self.requests[rid]["bundle_pending"] = True
        self._commit_terminal_record(rid, typ, data)
        return self._schedule_bundle(rid, data)

    async def aschedule_terminal(self, rid, typ, data, *, request=None, before=None):
        answer, cancelled = await self._publication_write(
            self._aschedule_terminal(rid, typ, data, request=request, before=before))
        if cancelled:
            raise asyncio.CancelledError
        return answer

    async def _aschedule_terminal(self, rid: str, typ: str, data: dict, *,
                                 request: dict | None = None, before: dict | None = None):
        prior = self.terminal_tasks.get(rid)
        if prior is not None:
            return prior
        request = copy.deepcopy(self.requests[rid] if request is None else request)
        before = copy.deepcopy(self.requests[rid] if before is None else before)
        has_workdir = any(isinstance(row, Mapping) and row.get("workdir_id")
                          for row in (request.get("results") or {}).values())
        outcome = None
        if not has_workdir and (request.get("results") or self._dispatched_work_task(rid)):
            outcome = await asyncio.to_thread(self._bundle_outcome, rid, request,
                copy.deepcopy(self.store.all("task")), copy.deepcopy(self.runner_capabilities))
        cancelled = False
        async with self._resume_card_locks(rid), self.event_lock, self._request_lock(rid):
            current = copy.deepcopy(self.requests[rid])
            if current.get("status") == "cancelled" and request.get("status") != "cancelled":
                return None
            updated = copy.deepcopy(current)
            apply_changes(updated, before, request)
            cp4.reconcile(updated)
            if has_workdir:
                updated["bundle_pending"] = True
            if outcome is not None:
                self._apply_bundle_to_request(updated, data, outcome)
            if is_terminal_request(updated.get("status")):
                await self._aretire_resume_cards_locked(rid, "요청이 끝나 재개 카드를 닫았습니다")
            updated.update(pi_review_pending=True, pi_reviewed_at=None)
            payload = {**copy.deepcopy(data), "pi_review_pending": True, "pi_reviewed_at": None}
            if cp4.enabled(updated):
                payload.update(status=updated["status"], final_checkpoint=cp4.summary(updated))
            review_pending = cp4.enabled(updated) and updated.get("status") == "review_pending"
            commit = self.store.acommit_request_event if review_pending else self.store.acommit_terminal
            event, cancelled = await self._publication_write(commit(rid, updated,
                {"type": typ, "ts": time.time(), "request_id": rid, "data": payload}, self.s.gateway.event_buffer))
            apply_changes(self.requests[rid], current, updated)
            self.knowledge.notify()
            self._record_published_event(event)
            self._queue_committed_event(event)
            self._schedule_committed_events()
            self._restart_request_asks(rid)
            asyncio.get_running_loop().create_task(self._close_step_controls(rid))
            self.recover_final_reviews()
            if updated.get("status") in {"failed", "interrupted"}:
                asyncio.get_running_loop().create_task(self.close_publish_cards(rid))
            if not review_pending:
                await self._aqueue_terminal_delivery(event)
            if getattr(self, "semantics_shadow", None) is not None:  # semantics-hook
                self.semantics_shadow.after_request(rid)  # semantics-hook
        try:
            await self.rounds.awrite(rid)
            await self.rounds.asubmit(rid)
        except OSError as exc:
            log.warning("round record write failed for %s: %s", rid, exc)
        if self.requests[rid].get("mode") == "direct":
            await self.aclear_step_jobs(rid, "direct")
        if not review_pending and not has_workdir:
            await self.result_storage.aschedule(rid)
        task = self._schedule_bundle(rid, data) if has_workdir else None
        if cancelled:
            raise asyncio.CancelledError
        return task

    def _schedule_bundle(self, rid: str, data: dict) -> asyncio.Task:
        """Build one pending bundle in a worker, both after terminal commit and after restart."""
        prior = self.terminal_tasks.get(rid)
        if prior is not None:
            return prior
        request = copy.deepcopy(self.requests[rid])
        tasks = copy.deepcopy(self.store.all("task"))
        runner_capabilities = copy.deepcopy(self.runner_capabilities)
        task = asyncio.get_running_loop().create_task(
            self._commit_terminal_async(rid, data, request, tasks, runner_capabilities))
        self.terminal_tasks[rid] = task
        self.terminal_started[rid] = time.time()
        task.add_done_callback(self._terminal_task_done)
        return task

    def recover_pending_bundles(self) -> list[str]:
        """At startup, retry only bundles whose terminal commit explicitly left a pending marker."""
        recovered = []
        for rid, request in list(self.requests.items()):
            if request.get("bundle_pending") is True:
                self._schedule_bundle(rid, {})
                recovered.append(rid)
        return recovered

    @staticmethod
    def _terminal_task_done(task: asyncio.Task) -> None:
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("terminal checkpoint failed: %s", exc)

    async def _commit_terminal_async(self, rid: str, data: dict, request: dict, tasks: dict,
                                     runner_capabilities: dict) -> None:
        from ..research import semantic_device_report
        freeze_pending = semantic_device_report.freeze_pending_observation
        await freeze_pending(self.semantics_shadow, request, self.requests[rid], self.s)  # semantics-hook
        while True:
            outcome = await asyncio.to_thread(
                self._bundle_outcome, rid, request, tasks, runner_capabilities)
            async with self.event_lock, self._request_lock(rid):
                if cp4.summary(request) != cp4.summary(self.requests[rid]):
                    # A receipt can arrive while copying or waiting for the event lock.
                    request = copy.deepcopy(self.requests[rid])
                    continue
                before = copy.deepcopy(self.requests[rid])
                updated = copy.deepcopy(before)
                self._apply_bundle_to_request(updated, data, outcome)
                updated.pop("bundle_pending", None)
                updated["updated_at"] = time.time()
                event, cancelled = await self._publication_write(self.store.acommit_request_event(
                    rid, updated, {"type": "request.bundle", "ts": time.time(), "request_id": rid,
                    "data": {key: data[key] for key in ("bundle_path", "bundle_status", "bundle_grade",
                        "bundle_grade_reasons", "bundle_warning") if key in data}}, self.s.gateway.event_buffer))
                apply_changes(self.requests[rid], before, updated)
                self._record_published_event(event)
                self._queue_committed_event(event)
                self._schedule_committed_events()
                break
        await self.result_storage.aschedule(rid)
        try:
            await self.rounds.awrite(rid)
            await self.rounds.asubmit(rid)
        except OSError as exc:
            log.warning("round record bundle update failed for %s: %s", rid, exc)
        if cancelled:
            raise asyncio.CancelledError

    def _commit_terminal_record(self, rid: str, typ: str, data: dict) -> None:
        # Failure never changes the request outcome, and terminal requests loaded after a restart do not pass this
        # checkpoint again.
        cp4.reconcile(self.requests[rid])
        if is_terminal_request(self.requests[rid].get("status")):  # a finished request is never resumed (#607)
            self.retire_resume_cards(rid, "요청이 끝나 재개 카드를 닫았습니다")
        if self.requests[rid].get("status") in {"failed", "interrupted"}:
            asyncio.get_running_loop().create_task(self.close_publish_cards(rid))
        self.requests[rid].update(pi_review_pending=True, pi_reviewed_at=None)
        data = {**data, "pi_review_pending": True, "pi_reviewed_at": None}
        if cp4.enabled(self.requests[rid]):
            data.update(status=self.requests[rid]["status"], final_checkpoint=cp4.summary(self.requests[rid]))
        self.recover_final_reviews()
        review_pending = cp4.enabled(self.requests[rid]) and self.requests[rid].get("status") == "review_pending"
        commit = self.store.commit_request_event if review_pending else self.store.commit_terminal
        event = commit(rid, self.requests[rid],
                                           {"type": typ, "ts": time.time(), "request_id": rid,
                                            "data": copy.deepcopy(data)},
                                           self.s.gateway.event_buffer)
        self.knowledge.notify()
        self.events.append(event)
        self._restart_request_asks(rid)
        asyncio.get_running_loop().create_task(self._close_step_controls(rid))  # nothing more is sent for it (#59 ①)
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
        if not review_pending:
            if not self.requests[rid].get("bundle_pending"):
                self.result_storage.schedule(rid)
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

    async def _aqueue_terminal_delivery(self, event: dict) -> None:
        seq = event["seq"]
        if seq in self.terminal_queued:
            return
        if self.reporter.enabled():
            self.terminal_queued.add(seq)
            self.reporter.submit(event)
        else:
            _, cancelled = await self._publication_write(self.store.adelete("terminal_delivery", str(seq)))
            if cancelled:
                raise asyncio.CancelledError

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
        pending_pipelines = {event.get("task_id") for event in pending if event["type"] == "pipeline.ready"}
        if self.reporter.enabled():
            for tid, entry in self.store.all("pipeline_submission").items():
                if tid not in pending_pipelines and entry.get("state") not in {"open", "rejected", "declined"}:
                    self.reporter.submit({"type": "pipeline.ready", "request_id": entry.get("request_id"),
                                          "task_id": tid, "data": {}})
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

    async def _flush_committed_events(self, delivered: list[dict] | None = None) -> None:
        # Called under event_lock, before allocating any later live event sequence.
        while self.pending_committed:
            event, clients = self.pending_committed[0]
            dead = []
            while clients:
                client = clients[0]
                if client not in self.clients:
                    clients = clients[1:]
                    self.pending_committed[0] = (event, clients)
                    continue
                try:
                    await asyncio.wait_for(client.send_text(json.dumps(event, ensure_ascii=False, default=str)),
                                           CLIENT_SEND_TIMEOUT_S)
                except asyncio.CancelledError:
                    # A backpressured client must not hold the next publisher too.
                    self._disconnect_cancelled_client(client)
                    self.pending_committed[0] = (event, clients[1:])
                    raise
                except asyncio.TimeoutError:
                    self._disconnect_cancelled_client(client)
                except Exception:
                    dead.append(client)
                clients = clients[1:]
                self.pending_committed[0] = (event, clients)
            self.pending_committed.popleft()
            for client in dead:
                self.clients.discard(client)
            if delivered is None:
                self.pi_interpreter.schedule(event)
            else:
                delivered.append(event)

    def _disconnect_cancelled_client(self, client) -> None:
        self.clients.discard(client)
        close = getattr(client, "close", None)
        if close is None:
            return

        async def close_socket():
            # Close independently of the cancelled sender, with a bounded network
            # wait. The browser reconnects and replays the committed sequence.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(close(code=1012), 1.0)

        closing = getattr(self, "client_close_tasks", None)
        if closing is None:
            closing = self.client_close_tasks = set()
        task = asyncio.create_task(close_socket())
        closing.add(task)
        task.add_done_callback(closing.discard)

    def _schedule_committed_events(self) -> None:
        prior = getattr(self, "committed_sender", None)
        if prior is not None and not prior.done():
            return
        task = self.committed_sender = asyncio.create_task(self._send_committed_event())

        def finished(sender):
            if not sender.cancelled() and (exc := sender.exception()) is not None:
                log.error("committed event delivery failed: %s", exc)

        task.add_done_callback(finished)

    def result_map(self, rid: str) -> SavedResults:
        return SavedResults(self, rid)

    def recovery_attempt(self, task: Task) -> int:
        if task.request_id not in self.recovery_steps:
            return 1
        return max((int(entry.get("attempt") or 1) for _, entry in self._recovery_matches(task)), default=1)

    def _recovery_choice(self, task: Task) -> tuple[str, dict] | None:
        """The earlier dispatch that ``dispatch`` answers ``task`` with after a restart, instead of sending it."""
        sid = task.meta.get("step_id") or task.meta.get("kind")
        if not (sid and task.request_id in self.recovery_steps):
            return None
        matches = self._recovery_matches(task)
        if not matches:
            return None
        return max(matches, key=lambda pair: (int(pair[1].get("attempt") or 1), pair[1].get("dispatched_at", 0)))

    def recovery_task_id(self, task: Task) -> str | None:
        """The task id ``dispatch`` would answer with instead of sending ``task``, decided by the same rule and with no
        await before it (#862: the CSO reserves no new budget for a turn the gateway adopts). None: it may be sent."""
        choice = self._recovery_choice(task)
        if choice is not None:
            return choice[0]
        sid = task.meta.get("step_id") or task.meta.get("kind")
        if sid and task.request_id in self.recovery_steps and task.meta.get("kind") == "direct":
            checkpoint = self.store.get("step_checkpoint", f"{task.request_id}:direct")
            result = (checkpoint or {}).get("result")
            if (isinstance(result, dict) and isinstance(result.get("task_id"), str)
                    and not self._interrupted_recovery_result(result)):
                return result["task_id"]
        return None

    @staticmethod
    def _interrupted_recovery_result(result: object) -> bool:
        """PI-approved recovery must not replay the shutdown that opened its resume card (#924)."""
        return isinstance(result, dict) and result.get("ok") is False and bool(result.get("shutdown_evidence"))

    def _recovery_matches(self, task: Task) -> list[tuple[str, dict]]:
        started = self.recovery_started.get(task.request_id, float("inf"))
        return [(tid, entry) for tid, entry in self.store.all("task").items()
                if self._matches_recovery(task, entry) and tid not in self.recovered_tasks and
                not (entry.get("completed") and self._interrupted_recovery_result(entry.get("result"))) and
                float(entry.get("dispatched_at") or 0) < started]

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
                    # A research continuation re-runs step ids of an earlier round under a new CP1 (#90).
                    prior_meta.get("research_round") == task.meta.get("research_round") and
                    prior_meta.get("continuation_refusal_recovery") == task.meta.get("continuation_refusal_recovery") and
                    # A consult belongs to one ask; another ask's consult is not its prior attempt.
                    prior_meta.get("ask_id") == task.meta.get("ask_id") and
                    # The first report and its claim-check corrections are different synthesis calls (#956).
                    prior_meta.get("report_correction") == task.meta.get("report_correction") and
                    prior_meta.get("recruit_identity") == task.meta.get("recruit_identity"))

    @staticmethod
    def _same_runner_generation(entry: dict, runner_id: str | None, incarnation: str | None) -> bool:
        return bool(runner_id and incarnation and entry.get("runner_id") == runner_id and
                    entry.get("runner_incarnation") == incarnation)

    def new_resume_approval(self, rid: str) -> ApprovalRequest:
        req = self.requests[rid]
        done = set(req.get("results") or {})
        steps = [s["id"] for s in req.get("plan", {}).get("steps", []) if s["id"] not in done]
        # Which request, from when, and what is left (R2): an old card must not read like tonight's work.
        head = short(" ".join(str(req.get("text") or rid).split()), 60)
        created = req.get("created_at")
        when = time.strftime("%m-%d %H:%M", time.localtime(float(created))) if created else "접수 시각 모름"
        # A plan whose steps all finished stopped after them (CP2, review, a continue card, the report), not before.
        has_plan = bool(req.get("plan", {}).get("steps"))
        carried = (req.get("research_contract") or {}).get("continuation") or {}
        awaiting_continuation_plan = bool(carried and not carried.get("plan_sha256"))
        left = (", ".join(steps) if steps else
                f"이어 가기 {int(carried.get('round') or 2)}차: 새 계획·CP1부터 이어 갑니다"
                if awaiting_continuation_plan else
                "없음(단계는 모두 끝났고 그 뒤 검토·결정·보고서부터 이어 갑니다)" if has_plan
                else "요청 전체")
        # An unapproved first plan: this one card shows it and, when approved, records CP1 for that hash (#654).
        orchestrator = getattr(self, "orchestrator", None)  # a test Hub built without __init__ has none
        cp1 = orchestrator.resume_plan_card(rid, req) if orchestrator else None
        approval = ApprovalRequest(kind="resume", request_id=rid,
                                   summary=f"중단된 요청 \"{head}\"({when} 접수)을 다시 이어 갈까요? 남은 단계: {left}"
                                           + (cp1["text"] if cp1 else ""),
                                   # has_plan tells the web's empty steps apart: all done, or the whole request (#521).
                                   detail={"request_text": head, "created_at": created, "steps": steps,
                                           "has_plan": has_plan,
                                           **({"cp1_target_sha256": cp1["target_sha256"], **cp1["detail"]} if cp1 else {})},
                                   timeout_s=self.s.policy.approvals.pi_decision_timeout_s)
        self.approvals[approval.id] = {"approval": approval.model_dump(mode="json"), "origin": None}
        self.save_approval(approval.id)
        self._schedule_resume_expiry(approval.id)
        return approval

    def _record_resume_cp1(self, card: dict, approval_id: str, note: str = "") -> None:
        """An approved resume card that showed the plan is the PI's CP1 answer for that plan hash, once (#654)."""
        rid = card.get("request_id")
        target = (card.get("detail") or {}).get("cp1_target_sha256")
        plan = (self.requests.get(rid or "") or {}).get("plan")
        if target and isinstance(plan, dict) and plan_sha256(plan) == target:
            self.requests[rid]["resume_cp1"] = {"approved": True, "target_sha256": target, "note": note or "",
                                                "approval_id": approval_id, "decided_at": time.time()}
            self.save_request(rid)

    async def _arecord_resume_cp1(self, card: dict, approval_id: str, note: str = "") -> None:
        """An approved resume card that showed the plan is the PI's CP1 answer for that plan hash, once (#654)."""
        rid = card.get("request_id")
        target = (card.get("detail") or {}).get("cp1_target_sha256")
        if not target or rid not in self.requests:
            return
        def update(req):
            plan = req.get("plan")
            if isinstance(plan, dict) and plan_sha256(plan) == target:
                req["resume_cp1"] = {"approved": True, "target_sha256": target, "note": note or "",
                                     "approval_id": approval_id, "decided_at": time.time()}
        await self.aupdate_request(rid, update)



    def _attach_resume_plans(self) -> None:
        """Startup recovery opens resume cards before the orchestrator exists; give them the plan afterwards (#654)."""
        for aid, entry in list(self.approvals.items()):
            card = entry["approval"]
            if card.get("kind") != "resume" or (card.get("detail") or {}).get("cp1_target_sha256"):
                continue
            req = self.requests.get(card.get("request_id") or "")
            cp1 = self.orchestrator.resume_plan_card(card["request_id"], req) if req else None
            if not cp1:
                continue
            entry["approval"] = ApprovalRequest.model_validate({
                **card, "summary": card["summary"] + cp1["text"],
                "detail": {**(card.get("detail") or {}), "cp1_target_sha256": cp1["target_sha256"], **cp1["detail"]},
                "pi_summary": "", "pi_reason": "", "pi_outcome": "", "pi_explainer": "",
                "pi_needs_interpretation": False}).model_dump(mode="json")
            self.save_approval(aid)
            # A page that reconnects with a cursor replays the stored events: the plan-less card of startup recovery
            # must be superseded there too, or the PI approves CP1 for a plan the page never showed (#654 review).
            event = self.store.append_event({"type": "approval.requested", "ts": time.time(),
                "request_id": card["request_id"], "data": with_approval_copy(entry["approval"])},
                self.s.gateway.event_buffer)
            self.events.append(event)

    def retire_resume_cards(self, rid: str, note: str) -> list[str]:
        """Resolve and remove every pending resume card of a request that no longer needs resuming (#607).

        Each leaves an ``approval.expired`` event and an ``approval_decision`` record. The request is not touched."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # at startup no page is connected; one that connects replays the stored event
        if loop is not None and any(self._approval_lock(aid).locked() for aid, entry in self.approvals.items()
                if entry["approval"].get("kind") == "resume" and entry["approval"].get("request_id") == rid):
            # Sync compatibility cannot await an in-flight answer on this loop.
            # Defer retirement rather than overwrite its durable decision.
            loop.create_task(self.aretire_resume_cards(rid, note))
            return []
        closed = []
        for aid, entry in list(self.approvals.items()):
            card = entry["approval"]
            if card.get("kind") != "resume" or card.get("request_id") != rid:
                continue
            self.store.commit_approval(aid, {"approval": card, "origin": None, "approved": False, "note": note,
                                             "state": "expired", "decided_at": time.time()})
            self._cancel_resume_timer(aid)
            self.approvals.pop(aid, None)
            event = self.store.append_event({"type": "approval.expired", "ts": time.time(), "request_id": rid,
                                             "data": {"id": aid, "note": note}}, self.s.gateway.event_buffer)
            self.events.append(event)
            if loop is not None:
                self.pending_committed.append((event, tuple(self.clients)))
            closed.append(aid)
        if closed and loop is not None:
            loop.create_task(self._send_committed_event())
        return closed

    @contextlib.asynccontextmanager
    async def _resume_card_locks(self, rid: str):
        # Card locks precede event/request locks, matching a resume answer. Waiting
        # for a card while holding event_lock would deadlock its post-commit publish.
        ids = sorted(aid for aid, entry in self.approvals.items()
                     if entry["approval"].get("kind") == "resume" and entry["approval"].get("request_id") == rid)
        async with contextlib.AsyncExitStack() as stack:
            for aid in ids:
                await stack.enter_async_context(self._approval_lock(aid))
            yield

    async def aretire_resume_cards(self, rid: str, note: str) -> list[str]:
        async with self._resume_card_locks(rid), self.event_lock, self._request_lock(rid):
            return await self._aretire_resume_cards_locked(rid, note)

    async def _aretire_resume_cards_locked(self, rid: str, note: str) -> list[str]:
        closed, cancelled = [], False
        for aid, entry in list(self.approvals.items()):
            card = copy.deepcopy(entry["approval"])
            if card.get("kind") != "resume" or card.get("request_id") != rid:
                continue
            answer = {"approval": card, "origin": None, "approved": False, "note": note,
                      "state": "expired", "decided_at": time.time()}
            _, interrupted = await self._publication_write(self.store.acommit_approval(aid, answer))
            cancelled = cancelled or interrupted
            self._cancel_resume_timer(aid)
            self.approvals.pop(aid, None)
            event, interrupted = await self._publication_write(self.store.aappend_event(
                {"type": "approval.expired", "ts": time.time(), "request_id": rid, "data": {"id": aid, "note": note}},
                self.s.gateway.event_buffer))
            cancelled = cancelled or interrupted
            self._record_published_event(event)
            self._queue_committed_event(event)
            self._schedule_committed_events()
            closed.append(aid)
        if cancelled:
            raise asyncio.CancelledError
        return closed

    def _resume_deadline(self, approval: dict) -> float:
        # Every resume card gets the PI-decision bound, also one saved before it had a timer (its timeout_s was 3600).
        return float(approval.get("created_at") or 0) + self.s.policy.approvals.pi_decision_timeout_s

    def _schedule_resume_expiry(self, aid: str) -> None:
        """Nothing awaits a resume card, so a timer ends it after pi_decision_timeout_s (R5)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # a Hub built outside the event loop: the startup hook schedules it
        if aid not in self.resume_timers:
            self.resume_timers[aid] = loop.create_task(self._expire_resume_later(aid))

    def _cancel_resume_timer(self, aid: str) -> None:
        task = self.resume_timers.pop(aid, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _expire_resume_later(self, aid: str) -> None:
        try:
            entry = self.approvals.get(aid)
            if entry is not None:
                await asyncio.sleep(max(0.0, self._resume_deadline(entry["approval"]) - time.time()))
                await self.expire_resume_card(aid)
        except asyncio.CancelledError:
            return
        finally:
            if self.resume_timers.get(aid) is asyncio.current_task():
                self.resume_timers.pop(aid, None)

    async def expire_resume_card(self, aid: str) -> bool:
        """End a resume card unanswered past its bound: the request fails as resume_timed_out, not as declined."""
        return await self._approval_operation(aid, self._expire_resume_card(aid, caller=asyncio.current_task()))

    async def _expire_resume_card(self, aid: str, *, caller=None) -> bool:
        entry = self.approvals.get(aid)
        if entry is None or entry["approval"].get("kind") != "resume":
            return False
        approval = entry["approval"]
        if time.time() < self._resume_deadline(approval):
            return False
        days = round(self.s.policy.approvals.pi_decision_timeout_s / 86400, 1)
        note = f"재개 카드에 {days:g}일 동안 답이 없어 요청을 끝냈습니다"
        answer = {"approved": False, "note": note, "state": "timed_out"}
        await self.store.acommit_approval(aid, {"approval": approval, "origin": None, **answer,
                                               "decided_at": time.time()})
        self.approvals.pop(aid, None)
        self._cancel_approval_timers(aid, caller)
        rid = approval.get("request_id")
        await self.publish({"type": "approval.resolved", "ts": time.time(), "request_id": rid,
                            "data": {"id": aid, **answer}})
        req = self.requests.get(rid or "")
        if req is not None and req.get("status") == "interrupted":
            self._restore_unstarted_continuation(rid, req)
            req.update(status="failed", outcome="resume_timed_out", error=note, finished_at=time.time())
            (await self.aschedule_terminal(rid, "request.failed", {"error": note}))
        return True

    async def expire_stale_resume_cards(self) -> None:
        """At startup: end resume cards past their bound and time the rest."""
        for aid, entry in list(self.approvals.items()):
            if entry["approval"].get("kind") == "resume" and not await self.expire_resume_card(aid):
                self._schedule_resume_expiry(aid)

    def resume_agents(self, rid: str) -> set[str]:
        from ..recruit.planning import pending_receipt_agents

        pending = pending_receipt_agents(self.requests[rid], self)
        return self._resume_agents(rid) | pending

    async def aresume_agents(self, rid: str) -> set[str]:
        from ..recruit.planning import apending_receipt_agents

        pending = await apending_receipt_agents(self.requests[rid], self)
        return self._resume_agents(rid) | pending



    def _resume_agents(self, rid: str) -> set[str]:
        req = self.requests[rid]
        if req.get("mode") == "direct":
            return set() if self.completed_direct_result(rid) else {req["agent_id"]}
        if req.get("mode") == "plan_only":
            if "plan" in req:
                return set()
            needed = {self.s.orchestrator.cso_agent}
            if self.s.orchestrator.chief_of_staff_agent:
                needed.add(self.s.orchestrator.chief_of_staff_agent)
            if self.s.orchestrator.precedent_agent and "analysis_precedents" not in req:
                needed.add(self.s.orchestrator.precedent_agent)
            return needed
        phase = solo_phase(req, self.s.orchestrator)
        if phase == "solo":
            agent = (req.get("route_decision") or {}).get("agent_id")
            return {agent} if agent else set()
        if phase == "review":
            reviewer = self.s.orchestrator.reviewer_agent
            return {reviewer} if reviewer else set()
        if phase == "done":
            return set()
        return self._team_resume_agents(req)

    def _team_resume_agents(self, req: dict) -> set[str]:
        steps = req.get("plan", {}).get("steps") or []
        done = set(req.get("results") or {})
        needed = {s["agent_id"] for s in steps if s["id"] not in done}
        phase = (req.get("review_progress") or {}).get("phase")
        needed.add(self.s.orchestrator.cso_agent)  # an unresolved review still gets the CSO's report (F5)
        if self.s.orchestrator.reviewer_agent and phase not in {"synthesis", "unresolved"}:
            needed.add(self.s.orchestrator.reviewer_agent)
        if not steps and self.s.orchestrator.chief_of_staff_agent:
            needed.add(self.s.orchestrator.chief_of_staff_agent)
        if not steps and self.s.orchestrator.precedent_agent and "analysis_precedents" not in req:
            needed.add(self.s.orchestrator.precedent_agent)
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
        if req.get("status") == "cancelled" or req.get("outcome") == "recruit_contract_ended":
            return
        try:
            continuation_retry = research_continuation.refusal_retry_pending(req)
            if continuation_retry and req.get("cso_model") not in self.s.orchestrator.cso_models:
                raise ValueError("선택한 CSO 모델이 더 이상 허용되지 않습니다.")
        except ValueError as exc:
            req.update(status="failed", error=str(exc), finished_at=time.time())
            await self.asave_request(rid)
            (await self.aschedule_terminal(rid, "request.failed", {"error": req["error"]}))
            return
        await self._set_status(rid, "waiting_for_runner")
        deadline = asyncio.get_running_loop().time() + self.s.gateway.resume_wait_s
        previous: set[str] | None = None
        while True:
            if req.get("status") == "cancelled":
                return
            missing = {aid for aid in (await self.aresume_agents(rid))
                       if self.agent_runner.get(aid) not in self.runners}
            if req.get("recruit_contract_failure"):
                error = "계획에 배정된 파견직 계약이 종료되었습니다: " + ", ".join(req["recruit_contract_failure"])
                req.update(status="failed", outcome="recruit_contract_ended", error=error, finished_at=time.time())
                await self.asave_request(rid)
                for aid, entry in list(self.approvals.items()):
                    card = entry["approval"]
                    if card.get("request_id") == rid and card.get("kind") == "resume":
                        await self.close_approval(aid, error)
                (await self.aschedule_terminal(rid, "request.failed", {"error": error}))
                return
            if not missing:
                if continuation_retry:
                    try:
                        if not research_continuation.refusal_retry_pending(req):
                            raise ValueError("이어 가기 복구 선택이 더 이상 유효하지 않습니다.")
                        if req.get("cso_model") not in self.s.orchestrator.cso_models:
                            raise ValueError("선택한 CSO 모델이 더 이상 허용되지 않습니다.")
                        if self._round_dispatched(rid, req):
                            raise ValueError("이어 가기 단계가 이미 전송되어 복구 선택을 적용하지 않았습니다.")
                    except ValueError as exc:
                        req.update(status="failed", error=str(exc), finished_at=time.time())
                        await self.asave_request(rid)
                        (await self.aschedule_terminal(rid, "request.failed", {"error": req["error"]}))
                        return
                await self._set_status(rid, "running")
                await self.publish({"type": "request.resumed", "ts": time.time(), "request_id": rid,
                                    "data": {"agents": sorted((await self.aresume_agents(rid)))}})
                self.recovery_started[rid] = time.time()
                self.recovery_steps.add(rid)
                try:
                    await self.orchestrator.run_request(rid, resume=True, **({"continuation": True} if continuation_retry else {}))
                finally:
                    self.recovery_steps.discard(rid)
                    self.recovery_started.pop(rid, None)
                return
            if missing != previous:
                await self.publish({"type": "request.resume_waiting", "ts": time.time(), "request_id": rid,
                                    "data": {"missing_agents": sorted(missing)}})
                previous = missing
            if asyncio.get_running_loop().time() >= deadline:
                await self._set_status(rid, "interrupted")
                await self.publish({"type": "request.resume_timeout", "ts": time.time(), "request_id": rid,
                                    "data": {"missing_agents": sorted(missing)}})
                return
            await asyncio.sleep(min(0.1, max(0, deadline - asyncio.get_running_loop().time())))

    def save_approval(self, aid: str) -> None:
        entry = self.approvals[aid]
        self.store.put("approval", aid, {k: v for k, v in entry.items() if k != "future"})

    def _approval_lock(self, aid: str):
        if not hasattr(self, "_approval_locks"):
            self._approval_locks = WeakValueDictionary()
        return self._approval_locks.setdefault(aid, asyncio.Lock())

    async def _approval_operation(self, aid: str, operation):
        """Serialize a card's lifecycle and settle it even under repeated cancellation."""
        lock = self._approval_lock(aid)

        async def run():
            async with lock:
                return await operation

        result, cancelled = await self._publication_write(run())
        if cancelled:
            raise asyncio.CancelledError
        return result

    def _cancel_approval_timers(self, aid: str, caller=None) -> None:
        # The durable operation runs in a shielded child task. Do not cancel the
        # timer that called it before that timer can finish its own cleanup.
        for name in ("facilities_timeout_tasks", "resume_timers"):
            timers = getattr(self, name, {})
            if timers.get(aid) is caller:
                timers.pop(aid, None)
        self._cancel_facilities_timeout(aid)
        self._cancel_resume_timer(aid)

    async def _add_approval(self, aid: str, entry: dict) -> bool:
        snapshot = copy.deepcopy({k: v for k, v in entry.items() if k != "future"})
        entry = {**snapshot, **({"future": entry["future"]} if "future" in entry else {})}
        async def save():
            if self.store.get("approval_decision", aid) is not None:
                return False  # a runner replay must not reopen an answered card
            if aid in self.approvals:
                return True
            await self.store.aput("approval", aid, snapshot)
            self.approvals[aid] = entry
            return True
        return await self._approval_operation(aid, save())

    async def _timeout_approval(self, aid: str, *, runner_id: str | None = None) -> dict | None:
        caller = asyncio.current_task()
        async def expire():
            entry = self.approvals.get(aid)
            prior = self.store.get("approval_decision", aid)
            if runner_id:
                queued = self.store.get("decision", aid)
                if not any((row or {}).get("origin") == runner_id for row in (entry, prior, queued)):
                    return None
            elif entry is None:
                return prior
            approval = (entry or prior or {}).get("approval")
            if approval is None:
                return None
            answer = {"approved": False, "note": "approval timed out" if runner_id else "timed out",
                      "state": "timed_out"}
            decision = {"approval": approval, "origin": runner_id, **answer, "decided_at": time.time()}
            await self.store.acommit_approval(aid, decision)
            self.approvals.pop(aid, None)
            self._cancel_approval_timers(aid, caller)
            future = (entry or {}).get("future")
            if future is not None and not future.done():
                future.set_result(answer)
            if not runner_id:
                await self.publish({"type": "approval.resolved", "ts": time.time(),
                                    "request_id": approval.get("request_id"), "data": {"id": aid, **answer}})
            return decision
        return await self._approval_operation(aid, expire())

    # ----- runners -----
    def register_runner(self, runner_id: str, ws: WebSocket, agents: list[dict],
                        incarnation: str | None = None, capabilities: dict | None = None,
                        process: str | None = None) -> None:
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
        started = self.recruits_running.get(runner_id) or {}
        # unregister_runner drops runner_incarnations on every disconnect, so compare with the process that started them.
        # The incarnation survives a restart, so the process id decides (#521); an entry started by a runner that sent
        # none (None == None) falls to the incarnation rule, as for cards.
        if (("process" in started and started["process"] != process) or
                (incarnation and started.get("incarnation") and started["incarnation"] != incarnation)):
            self.recruits_running.pop(runner_id, None)  # a new runner process: the old one's recruitments died with it
        self.runner_incarnations[runner_id] = incarnation
        self.runner_processes[runner_id] = process
        self.runner_locks.setdefault(runner_id, asyncio.Lock())
        self.set_roster(runner_id, agents, capabilities)
        hosted_agents = {aid for aid, a in self.effective_roster().items() if a["runner_id"] == runner_id}
        for tid, entry in self.store.all("task").items():
            if (not entry.get("completed") and
                    (entry.get("payload") or {}).get("agent_id") in hosted_agents and
                    not self._same_runner_generation(entry, runner_id, incarnation)):
                self._abandon_previous_generation(tid, entry)
        self._session_state_changed()
        self.result_storage.resume_interrupted()

    async def aregister_runner(self, runner_id: str, ws: WebSocket, agents: list[dict],
                        incarnation: str | None = None, capabilities: dict | None = None,
                        process: str | None = None) -> None:
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
        started = self.recruits_running.get(runner_id) or {}
        # unregister_runner drops runner_incarnations on every disconnect, so compare with the process that started them.
        # The incarnation survives a restart, so the process id decides (#521); an entry started by a runner that sent
        # none (None == None) falls to the incarnation rule, as for cards.
        if (("process" in started and started["process"] != process) or
                (incarnation and started.get("incarnation") and started["incarnation"] != incarnation)):
            self.recruits_running.pop(runner_id, None)  # a new runner process: the old one's recruitments died with it
        self.runner_incarnations[runner_id] = incarnation
        self.runner_processes[runner_id] = process
        self.runner_locks.setdefault(runner_id, asyncio.Lock())
        self.set_roster(runner_id, agents, capabilities)
        hosted_agents = {aid for aid, a in self.effective_roster().items() if a["runner_id"] == runner_id}
        for tid, entry in self.store.all("task").items():
            if (not entry.get("completed") and
                    (entry.get("payload") or {}).get("agent_id") in hosted_agents and
                    not self._same_runner_generation(entry, runner_id, incarnation)):
                (await self._aabandon_previous_generation(tid, entry))
        self._session_state_changed()
        self.result_storage.resume_interrupted()



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
        store = getattr(self, "store", None)
        task_entry = store.get("task", tid) if store is not None else None
        task_entry = task_entry or {}
        if task_entry.get("step_id"):
            item["step_id"] = str(task_entry["step_id"])
        if outcome_unknown:
            # A call the gateway gave up on has no runner provenance: name the engine/model it was dispatched with
            # (a task's own agent_overrides), not the staff member's default (#942).
            override = (((task_entry.get("payload") or {}).get("meta") or {}).get("agent_overrides") or {})
            for key in ("engine", "model"):
                if isinstance(override.get(key), str) and override[key]:
                    item[key] = override[key]
        item["recorded_at"] = time.time()
        amount = float(item["usd"]) if item["status"] != "unknown" else 0.0
        costs[tid] = amount if item["status"] != "unknown" else None
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

    async def _arecord_task_cost(self, rid: str | None, tid: str, result: TaskResult, *,
                                 outcome_unknown: bool = False) -> None:
        if rid not in self.requests or not tid:
            return
        def update(req):
            costs = req.setdefault("cost_by_task", {})
            if tid in costs:
                return
            agent = self.agents.get(result.agent_id)
            item = outcome_unknown_item(result, agent) if outcome_unknown else task_cost_item(result, agent)
            store = getattr(self, "store", None)
            task_entry = store.get("task", tid) if store is not None else None
            task_entry = task_entry or {}
            if task_entry.get("step_id"):
                item["step_id"] = str(task_entry["step_id"])
            if outcome_unknown:
                # A call the gateway gave up on has no runner provenance: name the engine/model it was dispatched with
                # (a task's own agent_overrides), not the staff member's default (#942).
                override = (((task_entry.get("payload") or {}).get("meta") or {}).get("agent_overrides") or {})
                for key in ("engine", "model"):
                    if isinstance(override.get(key), str) and override[key]:
                        item[key] = override[key]
            item["recorded_at"] = time.time()
            amount = float(item["usd"]) if item["status"] != "unknown" else 0.0
            costs[tid] = amount if item["status"] != "unknown" else None
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
        await self.aupdate_request(rid, update)



    def _abandon_previous_generation(self, tid: str, entry: dict) -> TaskResult:
        agent_id = (entry.get("payload") or {}).get("agent_id") or "unknown"
        result = TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                            error="runner generation changed; prior task delivery or outcome unknown; "
                                  "manual recovery required")
        self.store.put("task", tid, {**entry, "completed": True, "abandoned": True,
                                     "result": result.model_dump(mode="json")})
        self._refresh_roster()
        self._record_task_cost(entry.get("request_id"), tid, result, outcome_unknown=True)
        future = self.futures.get(tid)
        if future and not future.done():
            future.set_result(result)
        self._session_state_changed()
        return result

    async def _aabandon_previous_generation(self, tid: str, entry: dict) -> TaskResult:
        agent_id = (entry.get("payload") or {}).get("agent_id") or "unknown"
        result = TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                            error="runner generation changed; prior task delivery or outcome unknown; "
                                  "manual recovery required")
        await self.store.aput("task", tid, {**entry, "completed": True, "abandoned": True,
                                     "result": result.model_dump(mode="json")})
        self._refresh_roster()
        (await self._arecord_task_cost(entry.get("request_id"), tid, result, outcome_unknown=True))
        future = self.futures.get(tid)
        if future and not future.done():
            future.set_result(result)
        self._session_state_changed()
        return result



    def _orphan_reason(self, entry: dict, runner_id: str, incarnation: str | None, hosted: set[str],
                       process: str | None = None) -> str | None:
        """Why a runner card can no longer be answered, judged when ``runner_id`` registers, or None (R2)."""
        origin = entry.get("origin")
        if not origin:
            return None
        approval = entry["approval"]
        task = self.store.get("task", str(approval.get("task_id") or "")) if approval.get("task_id") else None
        if task is not None and task.get("abandoned"):
            return "그 작업이 끝났거나 결과를 알 수 없게 되어 닫힘"
        if origin == runner_id:
            # The incarnation survives a restart, so only the process id tells a restart from a reconnect (#521).
            # A card saved before cards carried it, or two older runners without one, fall to the incarnation rule.
            if "origin_process" in entry and entry["origin_process"] != process:
                return "러너가 다시 시작되어 닫힘"
            asked_by = entry.get("origin_incarnation")
            if incarnation and asked_by and asked_by != incarnation:
                return "러너가 다시 시작되어 닫힘"
            if incarnation and not asked_by and (task is None or task.get("completed") or
                                                 task.get("runner_incarnation") != incarnation):
                return "러너가 다시 시작되어 닫힘"
            return None
        if origin not in self.runners and approval.get("agent_id") in hosted:
            return f"{approval.get('agent_id')} 직원이 다른 러너({runner_id})로 옮겨 닫힘"
        return None

    async def expire_orphan_approvals(self, runner_id: str, incarnation: str | None,
                                      process: str | None = None) -> list[str]:
        """At runner registration, close cards whose asking process is gone: the runner restarted, the agent now
        lives on this runner, or the card's task was abandoned. The old process is not told; it is gone."""
        hosted = {aid for aid, a in self.effective_roster().items() if a["runner_id"] == runner_id}
        closed = []
        for aid, entry in list(self.approvals.items()):
            reason = self._orphan_reason(entry, runner_id, incarnation, hosted, process)
            if reason and await self.close_approval(aid, reason, notify_runner=False):
                closed.append(aid)
        return closed

    async def close_task_cards(self, tid: str) -> list[str]:
        """A task that reported its result asks nothing more: close the runner cards it left open (R2, #521).
        The asking process may still be alive, so its hook hears no now instead of at its own timeout."""
        closed = []
        for aid, entry in list(self.approvals.items()):
            card = entry["approval"]
            if (entry.get("origin") and card.get("task_id") == tid and card.get("kind") not in RUNNER_NOTICE_KINDS
                    and await self.close_approval(aid, "그 작업이 끝나 닫힘")):
                closed.append(aid)
        return closed

    async def flush_decisions(self, runner_id: str) -> None:
        for aid, entry in self.store.all("decision").items():
            if entry["origin"] == runner_id:
                async with self._approval_lock(aid):
                    await self._send_decision(runner_id, aid)

    async def _send_decision(self, runner_id: str, aid: str) -> None:
        """Caller holds this card's lock; re-read after waiting for a competing ACK/timeout."""
        entry = self.store.get("decision", aid)
        if entry and entry["origin"] == runner_id:
            ws = self.runners.get(runner_id)
            try:
                await asyncio.wait_for(self.send_runner(runner_id, {
                    "type": "approval.resolved", "id": aid,
                    "approved": entry["approved"], "note": entry["note"],
                    **({'choice': entry['choice']} if entry.get('choice') in DECISION_CHOICES else {})}),
                    CLIENT_SEND_TIMEOUT_S)
            except asyncio.TimeoutError as exc:
                if ws is not None:
                    self.unregister_runner(runner_id, ws)
                # The delivery stays durable until ACK, including after backpressure.
                raise RunnerUnavailable(f"runner {runner_id} approval delivery timed out") from exc

    async def cancel_task(self, tid: str) -> bool:
        """Ask the task's runner to stop it and keep the ask until the task ends (PR #396 review).

        A runner that was away when the cancel went out gets it again when it reconnects (flush_task_cancels), so
        a turn the gateway gave up on cannot keep spending on that runner. False means the task is unknown."""
        entry = self.store.get("task", tid)
        runner_id = self.task_runner.get(tid) or (entry or {}).get("runner_id")
        if not runner_id:
            return False
        if entry and entry.get("completed") and not entry.get("abandoned"):
            return True  # it reported a result; an abandoned task may still be running in an older runner process
        _, cancelled = await self._publication_write(self.store.aput(
            "task_cancel", tid, {"runner_id": runner_id, "requested_at": time.time()}))
        if cancelled:
            # The committed intent survives cancellation and is replayed on reconnect.
            raise asyncio.CancelledError
        current = self.store.get("task", tid)
        if current and current.get("completed") and not current.get("abandoned"):
            return True  # The result overtook intent persistence; no cancel remains to send.
        try:
            await self.send_runner(runner_id, {"type": "task.cancel", "task_id": tid})
        except RunnerUnavailable:
            pass  # kept: sent again when that runner reconnects
        return True

    async def flush_task_cancels(self, runner_id: str) -> None:
        """Resend kept cancels to a reconnected runner.

        A cancel ends only with the task's reported result (task.result) or after TASK_CANCEL_KEEP_S. An abandoned
        task keeps its cancel: a newer process of the same runner id marks it abandoned while the older process may
        still run it and reconnect (PR #397 review). A runner ignores a cancel for a task it does not hold."""
        now = time.time()
        for tid, entry in self.store.all("task_cancel").items():
            if entry.get("runner_id") != runner_id:
                continue
            task = self.store.get("task", tid)
            finished = bool(task and task.get("completed") and not task.get("abandoned"))
            if not task or finished or now - float(entry.get("requested_at") or 0) > TASK_CANCEL_KEEP_S:
                self.store.delete("task_cancel", tid)
                continue
            await self.send_runner(runner_id, {"type": "task.cancel", "task_id": tid})
        for tid, entry in self.store.all("jobs_cancel").items():  # local jobs of a cancelled request (#675)
            if entry.get("runner_id") != runner_id:
                continue
            if now - float(entry.get("requested_at") or 0) > JOBS_CANCEL_KEEP_S:
                self.store.delete("jobs_cancel", tid)
                continue
            await self.send_runner(runner_id, {"type": "task.jobs_cancel", "task_id": tid})

    # ----- staff control (#59 ①) -----
    @staticmethod
    def step_key(request_id: str | None, step_id: str | None, kind: str | None, task_id: str) -> str:
        """The step a turn belongs to: its wake, retry, finish and halt-resume turns share it."""
        return json.dumps([request_id or "", step_id or kind or task_id], ensure_ascii=False)

    def step_revision_floor(self, request_id: str | None, step_id: str | None) -> int:
        """The lowest revision the step's next turn can be dispatched at (#1053).

        A closed step (a continuation re-runs a step id its earlier round ended) needs one above the revision its ended
        turns reached, or the closed-step check refuses it. A step already reopened keeps the revision it was reopened
        at, so a question rerun or a retry in the same round is not refused as older. A turn of an earlier round, still
        at a lower revision, stays refused either way."""
        key = self.step_key(request_id, step_id, None, "")
        closed = getattr(self, "_closed_steps", None)
        if isinstance(closed, dict) and key in closed:
            return int(closed[key]) + 1
        return self._step(key)["revision"]

    def _entry_step(self, tid: str, entry: dict) -> str:
        return self.step_key(entry.get("request_id"), entry.get("step_id"), entry.get("kind"), tid)

    @staticmethod
    def controllable(kind: str | None, step_id: str | None, request_id: str | None) -> bool:
        """A plan step's turn (kind step with its step id) or a direct request's turn, inside a request."""
        return bool(request_id) and (kind == "direct" or (kind == "step" and bool(step_id)))

    def _turn_waits(self, tid: str, entry: dict) -> bool:
        """The turn submitted HPC jobs or asked a question that is not answered yet."""
        if entry.get("jobs_submitted"):
            return True
        return any((ask.get("ask") or {}).get("task_id") == tid and ask.get("state") != "resolved"
                   for ask in self.store.all("ask").values())

    def _step(self, key: str) -> dict:
        state = self.store.get("step_control", key) or {}
        return {"steers": [dict(steer) for steer in state.get("steers") or [] if isinstance(steer, dict)],
                "paused": bool(state.get("paused")), "dropped": int(state.get("dropped") or 0),
                "dropped_told": int(state.get("dropped_told") or 0), "agent_id": state.get("agent_id"),
                "drop_reservations": dict(state.get("drop_reservations") or {}),
                "last_task": state.get("last_task"), "ops": int(state.get("ops") or 0),
                "pause_op": int(state.get("pause_op") or 0), "halt_op": int(state.get("halt_op") or 0),
                "revision": int(state.get("revision") or 0)}

    async def _save_step(self, key: str, state: dict) -> None:
        if state["steers"] or state["paused"] or state.get("last_task"):
            await self.store.aput("step_control", key, {**state, "updated_at": time.time()})
        else:
            await self.store.adelete("step_control", key)

    def _step_lock(self, key: str) -> asyncio.Lock:
        if not hasattr(self, "_step_locks"):
            self._step_locks = WeakValueDictionary()
        return self._step_locks.setdefault(key, asyncio.Lock())

    async def _update_step(self, key: str, update, *, wake: bool = False, reopen: int | None = None):
        """Read/modify/commit the latest row under its lock, including cancellation.

        Callbacks run on the loop without yielding. Sends remain outside this
        lock: runner feedback and closure must progress during a blocked send.
        Closed keys guard in-flight work; restored request/results guard reloads.
        """
        async def commit():
            async with self._step_lock(key):
                closed = getattr(self, "_closed_steps", {})
                if key in closed and (reopen is None or reopen <= closed[key]):
                    return None
                state = self._step(key)
                if update(state) is False:
                    return None
                await self._save_step(key, state)
                closed.pop(key, None)
                if wake:
                    event = self.step_resume_events.get(key)
                    if event:
                        event.set()
                return state

        return await self._publication_write(commit())

    def _step_alive(self, entry: dict) -> bool:
        """Whether the step can still get another turn: its request is active and its result is not in yet."""
        req = self.requests.get(entry.get("request_id") or "")
        if not req or not is_active_request(req.get("status")):
            return False
        sid = entry.get("step_id") or entry.get("kind")
        return not sid or sid not in (req.get("results") or {})

    def _live_task(self, key: str) -> str | None:
        """The step's turn that is out now (dispatched and not finished), newest first."""
        found = [(entry.get("dispatched_at", 0), tid) for tid, entry in self.store.all("task").items()
                 if (not entry.get("completed") or entry.get("abandoned"))
                 and tid not in getattr(self, "_finishing_step_tasks", set()) and self._entry_step(tid, entry) == key]
        return max(found)[1] if found else None

    async def _push(self, tid: str, message: dict) -> bool:
        entry = self.store.get("task", tid) or {}
        runner_id = self.task_runner.get(tid) or entry.get("runner_id")
        if not runner_id:
            return False
        try:
            await self.send_runner(runner_id, {"type": "task.control", "task_id": tid,
                                               "agent_id": (entry.get("payload") or {}).get("agent_id"),
                                               "request_id": entry.get("request_id"), **message})
            return True
        except RunnerUnavailable:
            return False  # sent again with the task's state when its runner reconnects or the task is re-sent

    @staticmethod
    def _cap_steers(state: dict) -> None:
        """At most STEER_LIMIT steers wait for a turn; the oldest waiting ones go first and are counted. Steers already
        given to a turn that is out now do not count: it may have delivered them (PR #532 verification)."""
        waiting_ids = [steer["id"] for steer in state["steers"] if not steer.get("task")]
        over = len(waiting_ids) - STEER_LIMIT
        if over > 0:
            dropped = set(waiting_ids[:over])
            state["steers"] = [steer for steer in state["steers"] if steer["id"] not in dropped]
            state["dropped"] += over

    def _steer_message(self, state: dict, steer: dict) -> dict:
        dropped = max(0, state["dropped"] - state["dropped_told"])
        state["dropped_told"] = state["dropped"]
        return {"action": "steer", "steer": {"id": steer["id"], "text": steer["text"]}, "dropped": dropped}

    async def _publish_control(self, tid: str, entry: dict, data: dict,
                               *, lifecycle: tuple[dict | None] | None = None) -> None:
        """What the PI's request did, for the staff sheet. A halted task stays halted until resumed or expired."""
        key = self._entry_step(tid, entry)
        async with self._step_lock(key):
            if key in getattr(self, "_closed_steps", set()):
                return
            await self._control_state(tid, data, lifecycle=lifecycle, event={
                "type": "agent.control", "ts": time.time(), "task_id": tid,
                "agent_id": (entry.get("payload") or {}).get("agent_id"),
                "request_id": entry.get("request_id"), "data": data})

    async def _deliver_step_control(self, key: str, tid: str, target: str | None,
                                    message: dict | None, entry: dict, data: dict, lifecycle):
        """Settle the committed intent's send before propagating caller cancellation."""
        async def deliver():
            if key in getattr(self, "_closed_steps", {}) or not self._step_alive(entry):
                raise ValueError("step ended")
            current = self.store.get("task", target) if target else None
            if target and (target in getattr(self, "_finishing_step_tasks", set()) or
                           not current or (current.get("completed") and not current.get("abandoned"))):
                if (message or {}).get("action") == "steer":
                    steer_id = message["steer"]["id"]

                    def release(state):
                        # Existing reservations belong to the result's delivered report.
                        for steer in state["steers"]:
                            if steer["id"] == steer_id and steer.get("task") == target:
                                steer["task"] = None
                        self._cap_steers(state)

                    await self._update_step(key, release)
                if (message or {}).get("action") == "halt":
                    raise ValueError("task ended before halt delivery")
                return False
            sent = bool(target and message) and await self._push(target, message)
            if key in getattr(self, "_closed_steps", {}) or not self._step_alive(entry):
                raise ValueError("step ended")
            await self._publish_control(target or tid, entry, {**data, "sent": sent}, lifecycle=lifecycle)
            return sent

        return await self._publication_write(deliver())

    async def _control_state(self, tid: str, data: dict | None, *, replace: bool = False,
                             lifecycle: tuple[dict | None] | None = None,
                             event: dict | None = None, runner_id=None, runner_seq=None) -> None:
        """Serialize this row's PI/runner/result/expiry writers through publication.

        Deletion blocks late runner updates even after reload. A fresh PI action
        can still control a live step through its previous, completed turn.
        """
        if not hasattr(self, "_control_locks"):
            self._control_locks = WeakValueDictionary()
        lock = self._control_locks.setdefault(tid, asyncio.Lock())
        data, event = copy.deepcopy(data), copy.deepcopy(event)

        async def commit():
            async with lock:
                if data is None:
                    await self.store.acontrol_state(tid, None)
                    self.task_controls.pop(tid, None)
                    return
                if lifecycle is not None and self.store.get("task_control_closed", tid) != lifecycle[0]:
                    return  # This PI operation began before completion/expiry deleted its target.
                task = self.store.get("task", tid) or {}
                if runner_id is not None and task.get("completed") and not task.get("abandoned"):
                    # A fresh PI queue may use this finished turn's id. Feedback
                    # from that old runner turn cannot overwrite the new intent.
                    if runner_seq is not None:
                        await self.store.amark_runner(runner_id, runner_seq)
                    return
                prev = self.task_controls.get(tid) or {}
                merged = {**({} if replace else prev), **data, "ts": time.time()}
                if not replace and prev.get("halted") and tid in self.halted_waits:
                    merged["halted"] = True
                if not await self.store.acontrol_state(tid, merged, reopen=lifecycle is not None):
                    if runner_seq is not None:
                        await self.store.amark_runner(runner_id, runner_seq)
                    return
                self.task_controls[tid] = merged
                if event is not None:
                    await self.publish(event, runner_id=runner_id, runner_seq=runner_seq)

        _, cancelled = await self._publication_write(commit())
        if cancelled:
            raise asyncio.CancelledError

    def _pi_control_lock(self, key: str):
        if not hasattr(self, "_pi_control_locks"):
            self._pi_control_locks = WeakValueDictionary()
        return self._pi_control_locks.setdefault(key, asyncio.Lock())

    def _halt_result_lock(self, tid: str):
        if not hasattr(self, "_halt_result_locks"):
            self._halt_result_locks = WeakValueDictionary()
        return self._halt_result_locks.setdefault(tid, asyncio.Lock())

    async def control_task(self, tid: str, action: str, text: str | None = None) -> dict:
        """Keep PI actions on one step in invocation order, including runner sends."""
        key = self._entry_step(tid, self.store.get("task", tid) or {})
        async with self._pi_control_lock(key):
            if action == "halt":
                async with self._halt_result_lock(tid):
                    return await self._control_task(tid, action, text)
            return await self._control_task(tid, action, text)

    async def _note_runner_halt(self, tid: str) -> None:
        """The runner halted the task itself (breaker stop, #59). Record it as a PI halt, so a reconnecting runner is
        sent the halt again and a result that ends without finishing is parked as halted (#1008)."""
        entry = self.store.get("task", tid)
        if not entry or not self._step_alive(entry):
            return
        try:
            entry, cancelled = await self._publication_write(self.store.aupdate_task_control(tid, "halt"))
        except (KeyError, ValueError):
            return  # the task ended meanwhile

        def halt(state):
            state["ops"] = int(state.get("ops") or 0) + 1
            state["halt_op"] = state["ops"]

        _, interrupted = await self._update_step(self._entry_step(tid, entry), halt)
        if cancelled or interrupted:
            raise asyncio.CancelledError

    async def release_breaker(self, tid: str) -> dict:
        """The PI lifts a runaway-breaker restriction (#59). Sent to the runner holding the task; an unreachable runner
        keeps the restriction, so ``sent`` says whether it was delivered."""
        entry = self.store.get("task", tid)
        if not entry:
            raise KeyError(tid)
        if entry.get("completed") and not entry.get("abandoned"):
            raise ValueError("이 작업은 이미 끝났어요")
        # The command is stored before it is sent and stays until the runner acknowledges it (``agent.breaker`` with its
        # ``command_id``), so a send that did not arrive, an acknowledgement that got lost, or a gateway restart all
        # resend the same command (#1008). ``sent`` only says the frame went out; "applied" is the runner's word.
        command = {"command_id": new_id("brel"), "episode": self.breaker_state(tid).get("episode"),
                   "at": time.time()}
        self.store.put("breaker_release", tid, command)
        sent = await self._push(tid, self._release_message(command))
        return {"task_id": tid, "action": "release_breaker", "sent": sent, "queued": not sent,
                "command_id": command["command_id"]}

    @staticmethod
    def _release_message(command: dict) -> dict:
        episode = command.get("episode")
        if episode is not None and not isinstance(episode, str):
            episode = "legacy"  # saved by an earlier release as a bare number: it can match nothing, so it is answered stale
        return {"action": "release_constraint", "command_id": command["command_id"],
                **({"episode": episode} if episode is not None else {})}

    def breaker_state(self, tid: str) -> dict:
        """The restriction (episode, stage) this task's runner last reported, also after a gateway restart."""
        return self.task_breaker.get(tid) or self.store.get("task_breaker", tid) or {}

    def _note_breaker_event(self, tid: str, data: dict) -> None:
        """Remember which restriction the task is in, and settle the release command its acknowledgement names."""
        if isinstance(data.get("episode"), str):
            self.task_breaker[tid] = {"episode": data["episode"], "stage": data.get("stage")}
            self.store.put("task_breaker", tid, self.task_breaker[tid])  # the runner does not resend an acknowledged event
        command = self.store.get("breaker_release", tid)
        if command and data.get("command_id") == command.get("command_id"):
            self.store.delete("breaker_release", tid)

    async def _control_task(self, tid: str, action: str, text: str | None = None) -> dict:
        """Steer, pause, resume or halt through task ``tid`` (#59 ①). The step it belongs to owns the request: steers wait
        in the step's queue until a turn delivers them, a pause holds the step's next dispatch, and the step's turn that
        is out now (``tid`` or a later one) gets the request at once. Resume of a halted step releases its park.
        KeyError: no such task. ValueError: halt of a finished task, or any request to a step that has ended."""
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"unknown control {action!r}")
        entry = self.store.get("task", tid)
        if not entry and tid not in self.halted_waits:
            raise KeyError(tid)
        entry = entry or {}
        if entry and not self.controllable(entry.get("kind"), entry.get("step_id"), entry.get("request_id")):
            raise ValueError(NOT_CONTROLLABLE.format(kind=entry.get("kind") or "?"))
        if (not self._step_alive(entry) or
                self._entry_step(tid, entry) in getattr(self, "_closed_steps", set())):
            raise ValueError("이 단계는 끝나서 전달할 수 없어요")
        key = self._entry_step(tid, entry)
        lifecycle = (self.store.get("task_control_closed", tid),)
        stored_halt = ((entry.get("result") or {}).get("error_kind") == "halted"
                       and not entry.get("halt_wait_finished"))
        if action == "resume" and (tid in self.halted_waits or stored_halt):
            cancelled = False
            if tid not in self.halted_waits:
                entry, cancelled = await self._publication_write(self.store.aupdate_task_control(tid, "resume"))
                current = self.store.get("task", tid)
                if not current or current.get("halt_wait_finished") or not self._step_alive(current):
                    raise ValueError("이 단계는 끝나서 전달할 수 없어요")
            def resume(state):
                # A pause after the halt stays; an earlier pause is lifted.
                if state["paused"] and int(state.get("pause_op") or 0) < int(state.get("halt_op") or 0):
                    state["paused"] = False
                else:
                    return False

            _, interrupted = await self._update_step(key, resume, wake=True)
            cancelled |= interrupted
            if tid in self.halted_waits:
                self.halted_waits[tid].set()
            if cancelled:
                raise asyncio.CancelledError
            return {"task_id": tid, "action": action, "resumed_halted": True, "sent": False, "queued": False}
        live = ((not entry.get("completed") or bool(entry.get("abandoned")))
                and tid not in getattr(self, "_finishing_step_tasks", set()))
        if action == "halt":
            if not live:
                raise ValueError("이 작업은 이미 끝나 멈출 것이 없어요")
            if self._turn_waits(tid, entry):
                raise ValueError(HALT_WHILE_WAITING)
            entry, cancelled = await self._publication_write(self.store.aupdate_task_control(tid, "halt"))
            if cancelled:
                raise asyncio.CancelledError
            current = self.store.get("task", tid)
            if (not current or (current.get("completed") and not current.get("abandoned"))
                    or not self._step_alive(current)):
                raise ValueError("이 작업은 이미 끝나 멈출 것이 없어요")
            if not current.get("halt_requested"):
                # A queued whole-row writer may have overtaken this commit.
                # Never promise offline replay unless the intent still exists.
                if self._turn_waits(tid, current):
                    raise ValueError(HALT_WHILE_WAITING)
                raise ValueError("작업 상태가 바뀌었어요. 다시 시도해 주세요")
            entry = current
            def halt(state):
                if not self._step_alive(entry):
                    return False
                state["ops"] = int(state.get("ops") or 0) + 1
                state["halt_op"] = state["ops"]

            state, cancelled = await self._update_step(key, halt)
            if state is None or not self._step_alive(entry) or key in getattr(self, "_closed_steps", set()):
                raise ValueError("step ended")
            sent, interrupted = await self._deliver_step_control(
                key, tid, tid, {"action": "halt"}, entry,
                {"action": "halt", "halt": True, "source": "gateway"}, lifecycle)
            if cancelled or interrupted:
                raise asyncio.CancelledError
            return {"task_id": tid, "action": action, "sent": sent, "queued": not sent}
        target, message = None, None

        def change(state):
            nonlocal target, message
            if not self._step_alive(entry):
                return False
            state["agent_id"] = state["agent_id"] or (entry.get("payload") or {}).get("agent_id")
            state["last_task"] = state["last_task"] or tid
            current = self.store.get("task", tid) or {}
            target = (tid if current and (not current.get("completed") or current.get("abandoned"))
                      and tid not in getattr(self, "_finishing_step_tasks", set()) else self._live_task(key))
            if action == "steer":
                steer = {"id": new_id("steer"), "text": text or "", "at": time.time(), "task": target}
                state["steers"].append(steer)
                self._cap_steers(state)
                if target:
                    message = self._steer_message(state, steer)
            elif action == "pause":
                state["paused"] = True
                state["ops"] += 1
                state["pause_op"] = state["ops"]
                message = {"action": "pause"}
            else:
                state["paused"] = False
                message = {"action": "resume"}

        state, cancelled = await self._update_step(key, change, wake=action == "resume")
        if state is None or not self._step_alive(entry) or key in getattr(self, "_closed_steps", set()):
            raise ValueError("step ended")
        lifecycle = (self.store.get("task_control_closed", target or tid),)
        sent, interrupted = await self._deliver_step_control(key, tid, target, message, entry, {
            "action": action, "paused": state["paused"], "source": "gateway",
            "steers_pending": sum(1 for steer in state["steers"] if not steer.get("task")),
            "steers_dropped": state["dropped"]}, lifecycle)
        if cancelled or interrupted:
            raise asyncio.CancelledError
        return {"task_id": tid, "action": action, "sent": sent, "queued": not sent}

    async def _push_task_state(self, tid: str, *, steers: bool = True) -> None:
        """Send a task that is out now everything the PI asked of it or its step: halt, pause, and the steers marked
        for it. The runner applies a steer id once."""
        entry = self.store.get("task", tid)
        if (not entry or (entry.get("completed") and not entry.get("abandoned"))
                or tid in getattr(self, "_finishing_step_tasks", set())):
            return
        if entry.get("halt_requested"):
            await self._push(tid, {"action": "halt"})
        command = self.store.get("breaker_release", tid)
        if command:
            await self._push(tid, self._release_message(command))  # kept until the runner acknowledges it
        key = self._entry_step(tid, entry)
        state = self._step(key)
        # Send both values: a resume sent while disconnected must clear the runner's old pause on reconnect.
        if state["paused"] or state["pause_op"]:
            await self._push(tid, {"action": "pause" if state["paused"] else "resume"})
        if steers:
            for steer_id in [s["id"] for s in self._step(key)["steers"] if s.get("task") == tid]:
                # Every send yields. Re-read the queue, and commit before sending, so a concurrent control, result,
                # or step closure is never overwritten by the snapshot this flush started with.
                message = None

                def prepare(state):
                    nonlocal message
                    steer = next((s for s in state["steers"] if s["id"] == steer_id and s.get("task") == tid), None)
                    if steer is None or not self._step_alive(entry):
                        return False
                    message = self._steer_message(state, steer)

                _, cancelled = await self._update_step(key, prepare)
                if cancelled:
                    raise asyncio.CancelledError
                if (message is not None and self._step_alive(entry)
                        and key not in getattr(self, "_closed_steps", set())):
                    await self._push(tid, message)

    async def flush_task_controls(self, runner_id: str) -> None:
        """On reconnect, only tasks the runner has accepted get their controls. A task not delivered yet gets them from
        its own dispatch, which sends the task first (PR #532 review: a control sent before its task was NACKed as
        unknown and lost)."""
        for tid, entry in list(self.store.all("task").items()):
            if (entry.get("runner_id") == runner_id and entry.get("accepted")
                    and (not entry.get("completed") or entry.get("abandoned"))):
                await self._push_task_state(tid)

    async def _attach_steers(self, task: Task) -> Task:
        """Reserve undelivered steer IDs before constructing this turn's payload."""
        if not self.controllable(task.meta.get("kind"), task.meta.get("step_id"), task.request_id):
            return task
        key = self.step_key(task.request_id, task.meta.get("step_id"), task.meta.get("kind"), task.id)
        steers, dropped = [], 0

        def attach(state):
            nonlocal dropped
            revision = int(task.meta.get("revision") or 0)
            if revision < state["revision"]:
                return False
            state["revision"] = revision
            state["last_task"] = task.id
            state["agent_id"] = state["agent_id"] or task.agent_id
            for steer in state["steers"]:
                if not steer.get("task") or steer.get("task") == task.id:
                    steer["task"] = task.id
                    steers.append({"id": steer["id"], "text": steer["text"]})
            dropped = max(0, state["dropped"] - state["dropped_told"])
            state["dropped_told"] = state["dropped"]
            if dropped:
                # Ownership survives revision advances, but not closing the row.
                state["drop_reservations"][task.id] = dropped

        _, cancelled = await self._update_step(key, attach, reopen=int(task.meta.get("revision") or 0))
        if cancelled:
            # No payload reached dispatch. Return both IDs and its drop notice,
            # preserving any newer queue/counter changes under the same lock.
            await self._release_unpublished_attachment(key, task.id)
            raise asyncio.CancelledError
        if not steers and not dropped:
            return task
        return task.model_copy(update={"meta": {**task.meta, "steers": steers, "steer_dropped": dropped}})

    async def _release_unpublished_attachment(self, key: str, tid: str) -> None:
        """Return only this unsent attachment's reservations on the latest row."""
        def release(state):
            if not self.store.get("step_control", key):
                return False
            for steer in state["steers"]:
                if steer.get("task") == tid:
                    steer["task"] = None
            reserved = state["drop_reservations"].pop(tid, 0)
            state["dropped_told"] = max(0, state["dropped_told"] - reserved)
            self._cap_steers(state)

        _, cancelled = await self._update_step(key, release)
        if cancelled:
            raise asyncio.CancelledError

    async def _settle_steers(self, tid: str, entry: dict, report: dict | None) -> None:
        """Remove delivered IDs and release the rest against the latest step row."""
        key = self._entry_step(tid, entry)
        delivered = set((report or {}).get("delivered") or [])

        def settle(state):
            if not self.store.get("step_control", key):
                return False
            kept = []
            for steer in state["steers"]:
                if steer.get("task") == tid:
                    if steer["id"] in delivered:
                        continue
                    steer["task"] = None
                kept.append(steer)
            state["steers"] = kept
            state["drop_reservations"].pop(tid, None)
            self._cap_steers(state)

        _, cancelled = await self._update_step(key, settle)
        if cancelled:
            raise asyncio.CancelledError

    async def _release_steers(self, task: Task) -> None:
        """An uncertain dispatch keeps its IDs for replay; the runner deduplicates."""
        await self._settle_steers(task.id, {"request_id": task.request_id, "step_id": task.meta.get("step_id"),
                                            "kind": task.meta.get("kind")}, None)

    async def _wait_step_resumed(self, task: Task) -> TaskResult | None:
        """Hold the step's next turn while the PI keeps it paused. None: send it. A result: the PI-decision bound passed
        first, and nothing was sent."""
        if not self.controllable(task.meta.get("kind"), task.meta.get("step_id"), task.request_id):
            return None
        key = self.step_key(task.request_id, task.meta.get("step_id"), task.meta.get("kind"), task.id)
        if not self._step(key)["paused"]:
            return None
        rid, sid = task.request_id, task.meta.get("step_id") or task.meta.get("kind")
        event = self.step_resume_events.setdefault(key, asyncio.Event())
        req = self.requests.get(rid or "")
        parked = req is not None and is_active_request(req.get("status"))
        wait_key, bound = f"pause:{key}", self.s.policy.approvals.pi_decision_timeout_s
        last_task = self._step(key)["last_task"]
        self.step_pause_waits[key] = {"request_id": rid, "step_id": sid, "agent_id": task.agent_id,
                                      "kind": task.meta.get("kind"),
                                      "task_id": last_task, "since": time.time()}
        if parked:
            req.setdefault("pi_waits", {})[wait_key] = {"kind": "pause", "since": time.time(),
                                                        "deadline_at": time.time() + bound, "step_id": sid}
        await self.publish({"type": "request.step_paused", "ts": time.time(), "request_id": rid, "task_id": last_task,
                            "agent_id": task.agent_id, "data": {"step_id": sid}})
        if parked:
            await self._publish_hold_status(rid)
        deadline = asyncio.get_running_loop().time() + bound
        try:
            while self._step(key)["paused"]:
                event.clear()
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0,
                                      cost_known=True, error="PI 일시 정지가 결정 기한을 넘겨 이 단계의 다음 turn을 보내지 않았습니다")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(event.wait(), remaining)
            return None
        finally:
            self.step_pause_waits.pop(key, None)
            self.step_resume_events.pop(key, None)
            if parked and (req.get("pi_waits") or {}).pop(wait_key, None) is not None:
                if not req.get("pi_waits"):
                    req.pop("pi_waits", None)
                await self._publish_hold_status(rid)
            await self.publish({"type": "request.step_unpaused", "ts": time.time(), "request_id": rid,
                                "task_id": last_task, "agent_id": task.agent_id, "data": {"step_id": sid}})

    async def _close_step_controls(self, rid: str, step_id: str | None = None) -> None:
        """The step (or the whole request) ended: steers it never delivered are reported to the PI as not delivered."""
        keys = set(self.store.all("step_control")) | set(getattr(self, "_step_locks", {}))
        if step_id is not None:
            keys.add(self.step_key(rid, step_id, None, ""))
        cancelled = False
        for key in keys:
            try:
                request_id, step = json.loads(key)
            except (ValueError, TypeError):
                continue
            if request_id != rid or (step_id is not None and step != step_id):
                continue

            async def close():
                async with self._step_lock(key):
                    stored = self.store.get("step_control", key) or {}
                    await self.store.adelete("step_control", key)
                    if not hasattr(self, "_closed_steps"):
                        self._closed_steps = {}
                    revision = max([int(stored.get("revision") or 0)] + [
                        int(row.get("revision") or 0) for tid, row in self.store.all("task").items()
                        if self._entry_step(tid, row) == key])
                    self._closed_steps[key] = max(revision, self._closed_steps.get(key, 0))
                undelivered = [s.get("text") for s in stored.get("steers") or [] if isinstance(s, dict)]
                if undelivered:
                    await self.publish({"type": "agent.control", "ts": time.time(), "request_id": rid,
                                        "task_id": stored.get("last_task"), "agent_id": stored.get("agent_id"),
                                        "data": {"action": "undelivered", "undelivered": undelivered, "step_id": step,
                                                 "source": "gateway"}})

            _, interrupted = await self._publication_write(close())
            cancelled |= interrupted
        if cancelled:
            raise asyncio.CancelledError

    async def wait_halted(self, rid: str, step_id: str, result: TaskResult) -> bool:
        """Park a step the PI halted until the PI resumes it (POST /api/tasks/{id}/resume) (#59 ①).

        The request waits as ``waiting_pi`` meanwhile, like a PI-decision card. False when that bound passes first."""
        tid = result.task_id
        event = self.halted_waits.setdefault(tid, asyncio.Event())
        if (self.store.get("task", tid) or {}).get("halt_resume_requested"):
            event.set()
        req = self.requests.get(rid)
        parked = req is not None and is_active_request(req.get("status"))
        key, bound = f"halt:{tid}", self.s.policy.approvals.pi_decision_timeout_s
        if parked:
            req.setdefault("pi_waits", {})[key] = {"kind": "halt", "since": time.time(),
                                                   "deadline_at": time.time() + bound, "task_id": tid,
                                                   "step_id": step_id}
        resumed, reason = False, "timed_out"
        try:
            await self.publish({"type": "request.step_halted", "ts": time.time(), "request_id": rid, "task_id": tid,
                                "agent_id": result.agent_id,
                                "data": {"step_id": step_id, "session_id": result.session_id}})
            if parked:
                await self._publish_hold_status(rid)
            await asyncio.wait_for(event.wait(), bound)
            resumed = True
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            reason = "cancelled"  # request cancel stopped the orchestration
            raise
        finally:
            async def finish_wait():
                self.halted_waits.pop(tid, None)
                entry = self.store.get("task", tid)
                if entry:
                    entry.pop("halt_resume_requested", None)
                    self.store.put("task", tid, {**entry, "halt_wait_finished": True})
                await self._control_state(tid, None)
                if parked and (req.get("pi_waits") or {}).pop(key, None) is not None:
                    if not req.get("pi_waits"):
                        req.pop("pi_waits", None)
                    await self._publish_hold_status(rid)
                # Clear the resume button even if cancellation repeats during the durable deletion.
                await self.publish({"type": "request.step_resumed" if resumed else "request.step_halt_expired",
                                    "ts": time.time(), "request_id": rid, "task_id": tid, "agent_id": result.agent_id,
                                    "data": {"step_id": step_id, **({} if resumed else {"reason": reason})}})

            _, cancelled = await self._publication_write(finish_wait())
            if cancelled:
                raise asyncio.CancelledError
        return resumed

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

    def effective_roster(self, *, include_history: bool = False) -> dict[str, dict]:
        """Resolve one owner per agent. Unfinished deliveries pin ownership even while offline.

        Advertised rosters are input only here; routing, recovery and registration use this view.
        History is for discovery/waiting, never for dispatch to a disconnected runner.
        """
        from ..wsl_readiness import wsl_readiness

        # Resume planning can query a Hub before any runner state has been loaded.
        rosters = ({rid: entry.get("agents", []) for rid, entry in getattr(self, "last_runner_rosters", {}).items()}
                   if include_history else {})
        rosters.update(getattr(self, "runner_agents", {}))
        candidates: dict[str, dict[str, dict]] = {}
        for rid, staff in rosters.items():
            caps = self.runner_capabilities.get(rid, {})
            for agent in staff:
                aid = agent["id"]
                if (aid == "bioinfo-agent" and caps.get("runtime_env") == "wsl" and
                        not wsl_readiness(caps.get("wsl_readiness", {}))):
                    continue
                candidates.setdefault(aid, {})[rid] = agent
        owners = {}
        for aid, hosts in candidates.items():
            online = [rid for rid in hosts if rid in self.runners]
            ready_wsl = [rid for rid in online if aid == "bioinfo-agent" and
                         self.runner_capabilities.get(rid, {}).get("runtime_env") == "wsl"]
            owners[aid] = (ready_wsl or online or list(hosts))[-1]
        # Persisted dispatch identity survives both socket loss and gateway restart. An uncertain
        # outcome stays unfinished: a timeout cannot prove that the original work stopped.
        # No reassignment means no ledger scan (in particular, ordinary result/session wakes).
        if owners != getattr(self, "agent_runner", {}):
            for entry in self.store.all("task").values():
                aid = (entry.get("payload") or {}).get("agent_id")
                rid = entry.get("runner_id")
                if aid and rid and not entry.get("completed"):
                    owners[aid] = rid
                    prior = next((a for a in self.last_runner_rosters.get(rid, {}).get("agents", [])
                                  if a.get("id") == aid), {"id": aid})
                    candidates.setdefault(aid, {}).setdefault(rid, prior)
        return {aid: {**candidates[aid][rid], "runner_id": rid} for aid, rid in owners.items()}

    def _refresh_roster(self) -> None:
        roster = self.effective_roster()
        for aid in set(self.agent_runner) | set(roster):
            signal = self.agent_online.setdefault(aid, asyncio.Event())
            if aid in roster and roster[aid]["runner_id"] in self.runners:
                signal.set()
            else:
                signal.clear()
        self.agent_runner = {aid: a["runner_id"] for aid, a in roster.items()}
        self.agents = {}
        for aid, a in roster.items():
            caps = self.runner_capabilities.get(a["runner_id"], {})
            hpc_tools = bool(a.get("hpc_tools", caps.get("hpc_tools") and
                                                 "hpc" in a.get("builtin_mcp", [])))
            self.agents[aid] = {**a, "scheduler": caps.get("scheduler", "none"),
                                "task_timeout_s": a.get("task_timeout_s", self.s.runner.task_timeout_s),
                                "compute_backends": (["local CLI"] +
                                                     [b for b in caps.get("compute_backends", [])
                                                      if b != "local CLI" and hpc_tools]),
                                "hpc_tools": hpc_tools}
            self.agents[aid]["capabilities"] = self._agent_capabilities(self.agents[aid])

    def set_roster(self, runner_id: str, agents: list[dict], capabilities: dict | None = None) -> None:
        roster = {"agents": agents, "updated_at": time.time()}
        self.last_runner_rosters[runner_id] = roster
        self.store.put("runner_roster", runner_id, roster)
        self.runner_agents[runner_id] = agents
        if capabilities is not None:
            self.runner_capabilities[runner_id] = capabilities
        self._refresh_roster()
        # Every step waiting for a runner looks again (wait_for_runner) and ends now if nothing is left to wait for,
        # instead of after gateway.runner_wait_s with a false "runner never connected" (#521): an agent this runner
        # dropped (a contract that expired while it was off), or a first agent that waited only because no runner
        # was connected and that this runner does not host either (PR #530 review).
        for req in self.requests.values():
            for wait in (req.get("runner_waits") or {}).values():
                self.agent_online.setdefault(str(wait.get("agent_id") or ""), asyncio.Event()).set()
        self._session_state_changed()

    def unregister_runner(self, runner_id: str, ws: WebSocket) -> bool:
        if self.runners.get(runner_id) is ws:
            self.runners.pop(runner_id, None)  # keep roster + pending futures: the runner will reconnect
            self.runner_incarnations.pop(runner_id, None)
            self.runner_processes.pop(runner_id, None)
            self._refresh_roster()
            self._session_state_changed()
            return True
        return False

    def _waits_for_runner(self, task: Task, *, first: bool = False) -> bool:
        """A task of a live request waits for its runner instead of failing at once (R15) when the agent is known
        from a roster whose runner is away. The request's first agent (CSO, or the direct agent) also waits while no
        runner is connected at all, as on a fresh gateway. Any other agent no runner hosts fails at once."""
        req = self.requests.get(task.request_id or "")
        if req is None or not is_active_request(req.get("status")):
            return False
        if (first and not self.runners) or task.agent_id in self.agent_runner:
            return True
        # Known from the roster of a runner that is away; a connected runner that dropped the agent does not count.
        owner = self.effective_roster(include_history=True).get(task.agent_id)
        return bool(owner and owner["runner_id"] not in self.runners)

    def _runner_wait_failure(self, agent_id: str) -> str:
        bound = float(self.s.gateway.runner_wait_s)
        span = f"{round(bound / 3600, 1):g}시간" if bound >= 3600 else f"{max(1, round(bound / 60))}분"
        return (f"러너가 {span} 안에 연결되지 않아 {agent_id} 작업을 보내지 못했습니다. labhq up(또는 labhq runner)으로 "
                "러너를 켠 뒤 같은 요청을 다시 보내세요(gateway.runner_wait_s).")

    @staticmethod
    def _no_runner_hosts(agent_id: str) -> str:
        return f"연결된 러너 중 {agent_id} 직원을 맡은 곳이 없습니다 (no runner hosts agent {agent_id!r})"

    @staticmethod
    def _wsl_not_ready(task: Task, note: str) -> TaskResult:
        # Nothing was sent, so nothing was spent: a real $0.
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                          error_kind="wsl_not_ready", error=note)

    def _native_hosts(self, agent_id: str) -> bool:
        """Some runner other than the WSL one has this agent on its roster, now or last time it connected."""
        wsl_id = self.s.runner.wsl.id
        rosters = {rid: entry.get("agents", []) for rid, entry in getattr(self, "last_runner_rosters", {}).items()}
        rosters.update(getattr(self, "runner_agents", {}))
        return any(rid != wsl_id and any(agent.get("id") == agent_id for agent in staff)
                   for rid, staff in rosters.items())

    async def offer_wsl_setup(self, task: Task) -> TaskResult | None:
        """The agent runs only in the local WSL runner and that runner is not ready (#612): ask the PI once per request
        whether to set it up. None: nothing to say, carry on as before. A result: the task stops here, without waiting
        for a runner that will not come on its own."""
        from .. import wsl_setup
        from ..cli import _gateway_is_local

        rid = task.request_id or ""
        if task.agent_id not in WSL_BOUND_AGENTS or rid not in self.requests or not wsl_setup.configured(self.s):
            return None
        if self._native_hosts(task.agent_id):
            return None  # a native runner hosts it and is only away: the normal wait for it applies, not a WSL setup
        async with self._approval_lock(f"wslsetup:{rid}"):
            state = self.requests[rid].setdefault("wsl_setup", {})
            if state.get("status") in {"declined", "timed_out", "failed", "blocked"}:
                return self._wsl_not_ready(task, state.get("note") or "WSL가 준비되지 않았습니다")
            if state.get("status") == "ready":
                return None
            try:
                rows = await asyncio.wait_for(asyncio.to_thread(wsl_setup.check, self.s), 60)
            except Exception:  # a check that cannot run says nothing about readiness: keep the old behaviour
                return None
            if not wsl_setup.pending_rows(rows):
                return None
            detail = wsl_setup.card_detail(rows)
            if not _gateway_is_local(self.s):
                state.update(status="blocked", note=(
                    "WSL 셋업은 gateway와 같은 PC에서만 실행합니다. WSL이 있는 PC에서 `labhq setup-wsl`을 실행하세요."))
                await self.asave_request(rid)
                return self._wsl_not_ready(task, state["note"])
            approval = ApprovalRequest(kind="wsl_setup", request_id=rid, summary="WSL 준비가 덜 됐어요. 셋업할까요?",
                                       detail={"agent_id": task.agent_id, **detail},
                                       timeout_s=self.s.policy.approvals.timeout_s)
            fut = asyncio.get_running_loop().create_future()
            await self._add_approval(approval.id, {"approval": approval.model_dump(mode="json"), "origin": None,
                                                   "future": fut})
            await self.publish({"type": "approval.requested", "ts": time.time(), "request_id": rid,
                                "data": approval.model_dump(mode="json")})
            remaining = max(0.0, approval.created_at + approval.timeout_s - time.time())
            try:
                answer = await asyncio.wait_for(asyncio.shield(fut), remaining)
            except asyncio.TimeoutError:
                answer = await self._timeout_approval(approval.id)
            if not answer.get("approved"):
                timed_out = answer.get("state") == "timed_out"
                state.update(status="timed_out" if timed_out else "declined", note=(
                    "WSL 셋업 승인이 없어 bioinfo-agent를 쓸 수 없습니다. 직접 준비하려면 `labhq setup-wsl`을 실행하세요."))
                await self.asave_request(rid)
                return self._wsl_not_ready(task, state["note"])
            if getattr(self, "_wsl_setup_running", False):
                state.update(status="failed", note="다른 WSL 셋업이 아직 진행 중입니다. 끝난 뒤 `labhq setup-wsl --check`로 확인하세요.")
                await self.asave_request(rid)
                return self._wsl_not_ready(task, state["note"])
            self._wsl_setup_running = True
            work = asyncio.ensure_future(asyncio.to_thread(wsl_setup.setup, self.s))
            # A running thread cannot be stopped: the flag stays up until it ends, whatever the wait below does.
            work.add_done_callback(lambda done: (setattr(self, "_wsl_setup_running", False),
                                                 done.cancelled() or done.exception()))  # a late failure is read, not lost
            try:
                rows = await asyncio.wait_for(asyncio.shield(work), wsl_setup.SETUP_LIMIT_S)
            except Exception as exc:  # the exception text can carry installer output: only its type is kept
                log.warning("WSL setup from card failed: %s", type(exc).__name__)
                still = isinstance(exc, asyncio.TimeoutError)
                state.update(status="failed", note=(
                    "WSL 셋업이 15분 안에 끝나지 않았습니다. 준비가 백그라운드에서 계속될 수 있으니 끝난 뒤 "
                    "`labhq setup-wsl --check`로 확인하세요." if still else
                    f"WSL 셋업을 마치지 못했습니다({type(exc).__name__}). `labhq setup-wsl`로 직접 확인하세요."))
                await self.asave_request(rid)
                return self._wsl_not_ready(task, state["note"])
            left = wsl_setup.card_detail(rows)
            if wsl_setup.pending_rows(rows):
                commands = left["admin_commands"][:5]
                state.update(status="failed", note="WSL 셋업을 했지만 아직 준비되지 않았습니다. 남은 것: "
                             + ", ".join(left["missing"][:8]) + (". 관리자 명령: " + " / ".join(commands) if commands else ""))
                await self.asave_request(rid)
                await self.publish({"type": "agent.log", "ts": time.time(), "task_id": task.id,
                                    "agent_id": task.agent_id, "request_id": rid,
                                    "data": {"level": "warn", "text": state["note"]}})
                return self._wsl_not_ready(task, state["note"])
            state.update(status="ready")
            await self.asave_request(rid)
            await self.publish({"type": "agent.log", "ts": time.time(), "task_id": task.id,
                                "agent_id": task.agent_id, "request_id": rid,
                                "data": {"level": "info", "text": "WSL 셋업을 마쳤어요. runner가 연결되면 이어 갑니다"}})
            return None

    async def wait_for_runner(self, task: Task, *, first: bool = False) -> bool:
        """Park the task's request as waiting_for_runner until a runner hosting the agent connects, at most
        gateway.runner_wait_s. Nothing was sent yet, so nothing is uncertain or spent. False also when a roster
        change leaves no runner to wait for (#521): the caller then reports that no runner hosts the agent."""
        rid = task.request_id or ""
        req = self.requests[rid]
        now = time.time()
        bound = float(self.s.gateway.runner_wait_s)
        req.setdefault("runner_waits", {})[task.id] = {"agent_id": task.agent_id, "since": now,
                                                       "deadline_at": now + bound}
        await self._publish_hold_status(rid)
        await self.publish({"type": "request.runner_wait", "ts": now, "request_id": rid,
                            "data": {"agent_id": task.agent_id, "missing_agents": [task.agent_id],
                                     "deadline_at": now + bound,
                                     "message": f"러너가 꺼져 있어 기다립니다. labhq up(또는 labhq runner)으로 켜면 "
                                                f"{task.agent_id} 작업부터 이어 갑니다."}})
        try:
            return await self.wait_agent_online(task.agent_id, bound,
                                                still_waiting=lambda: self._waits_for_runner(task, first=first))
        finally:
            before = req.get("status")
            waits = req.get("runner_waits") or {}
            waits.pop(task.id, None)
            if not waits:
                req.pop("runner_waits", None)
                if req.get("status") == "waiting_for_runner":
                    req["status"] = "running"
            await self._publish_hold_status(rid, before)  # open pages see the request move on

    async def wait_agent_online(self, agent_id: str, timeout_s: float,
                                still_waiting: Callable[[], bool] | None = None) -> bool:
        signal = self.agent_online.setdefault(agent_id, asyncio.Event())
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if self.agent_runner.get(agent_id) in self.runners:
                return True
            if still_waiting is not None and not still_waiting():
                return False
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
            raise RunnerNotSent(f"runner {runner_id} is offline")
        from ..wsl_paths import message_paths
        msg = message_paths(msg, self.runner_capabilities.get(runner_id, {}).get("runtime_env"))
        payload = json.dumps(msg, ensure_ascii=False, default=str)
        async with self.runner_locks[runner_id]:
            if self.runners.get(runner_id) is not ws:
                raise RunnerNotSent(f"runner {runner_id} connection was replaced")
            try:
                await ws.send_text(payload)
            except Exception as exc:
                self.unregister_runner(runner_id, ws)
                raise RunnerUnavailable(f"runner {runner_id} WebSocket send failed") from exc

    def supports_resume(self, agent_id: str) -> bool:
        return self._agent_resumes(self.agents.get(agent_id, {}))

    def _agent_resumes(self, agent: dict) -> bool:
        engine = agent.get("engine")
        if not engine:
            return False
        if engine == "cli":
            return bool(agent.get("cli_resume"))
        try:
            return get_adapter(engine, self.s).supports_resume
        except ValueError:
            return False

    def _agent_capabilities(self, agent: dict) -> dict:
        """The staff sheet's capability card (#57 ⑦): what the roster and the engine adapter say, nothing guessed."""
        return {"resume": self._agent_resumes(agent),
                "read_only": enforces_read_only(agent.get("engine")),
                "effort": agent.get("effort"),
                "permission": agent.get("sandbox") if agent.get("engine") == "codex" else agent.get("permission_mode"),
                "max_turns": agent.get("max_turns"),
                "mcp": self._agent_mcp(agent)}

    @staticmethod
    def _agent_mcp(agent: dict) -> list[str]:
        """The servers the runner wires (`RunnerDaemon._mcp_servers`, PR #410 review): HPC only with a scheduler,
        labhq_annot when listed, labhq_wiki when listed and the runner has the opt-in on, labhq_ask for every engine but Antigravity, then the agent's own servers."""
        builtin = agent.get("builtin_mcp", [])
        return [*(["labhq_approval"] if "approval" in builtin else []),
                *(["labhq_hpc"] if "hpc" in builtin and agent.get("scheduler", "none") != "none" else []),
                *(["labhq_job"] if "job" in builtin and agent.get("engine", "claude_code") == "claude_code" else []),
                *(["labhq_annot"] if "annot" in builtin else []),
                *(["labhq_wiki"] if "wiki" in builtin and agent.get("wiki_tools") else []),
                *(["labhq_ask"] if agent.get("engine") != "antigravity" else []),
                *agent.get("mcp", [])]

    # ----- events -----
    def _queue_committed_event(self, event: dict) -> None:
        queued = sorted([*self.pending_committed, (event, tuple(self.clients))], key=lambda item: item[0]["seq"])
        self.pending_committed.clear()
        self.pending_committed.extend(queued)

    def _record_published_event(self, ev: dict) -> None:
        # A synchronous checkpoint can commit a higher sequence while publish
        # awaits its writer. Keep the newest buffer entries in database order.
        if not self.events or self.events[-1]["seq"] < ev["seq"]:
            self.events.append(ev)
            return
        ordered = sorted([*self.events, ev], key=lambda entry: entry["seq"])
        self.events.clear()
        self.events.extend(ordered)

    async def publish(self, ev: dict, runner_id: str | None = None, runner_seq: int | None = None) -> None:
        ev = copy.deepcopy(ev)
        internal = self.store.get("task", ev.get("task_id")) if ev.get("task_id") else None
        if (internal or {}).get("kind") == "pi_interpretation" and ev.get("type") == "agent.log":
            ev = {**ev, "data": {**ev.get("data", {}), "level": "debug"}}
        if ev.get('type') == 'approval.requested':
            ev = {**ev, 'data': with_approval_copy(ev.get('data') or {})}
        cancelled = False
        delivered = []
        try:
            async with self.event_lock:
                await self._flush_committed_events()
                durable_report = (self.reporter.enabled() and bool(ev.get("request_id")) and
                                  ev.get("type") in ProjectReporter.HANDLED)
                ev, cancelled = await self._publication_write(self.store.aappend_event(
                    ev, self.s.gateway.event_buffer, runner_id, runner_seq, reporter_delivery=durable_report))
                self._record_published_event(ev)
                self._queue_committed_event(ev)
                try:
                    if not cancelled:
                        await self._flush_committed_events(delivered)
                except asyncio.CancelledError:
                    cancelled = True
                rid = ev.get("request_id")
                if rid in self.requests:
                    try:
                        await self.asave_request(rid)
                    except asyncio.CancelledError:
                        cancelled = True
        finally:
            if self.pending_committed:
                self._schedule_committed_events()
        if self.reporter.enabled():
            self.reporter.submit(ev)
        for event in delivered:
            self.pi_interpreter.schedule(event)
        if cancelled:
            raise asyncio.CancelledError
        if ev.get("type") in ("request.step_done", "request.step_skipped") and ev.get("request_id"):
            await self._close_step_controls(ev["request_id"], (ev.get("data") or {}).get("step_id"))

    @staticmethod
    async def _publication_write(write):
        """Settle a durable operation and its required cleanup; preserve errors on cancel."""
        cancelled = False
        with anyio.CancelScope(shield=True):
            pending = asyncio.create_task(write)
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = True
            result = pending.result()
        # Level cancellation may have been hidden throughout the shield, without
        # injecting CancelledError into the commit wait (including no-client events).
        try:
            await anyio.lowlevel.checkpoint_if_cancelled()
        except asyncio.CancelledError:
            cancelled = True
        return result, cancelled

    async def on_runner_message(self, runner_id: str, msg: dict, ws: WebSocket | None = None,
                                incarnation: str | None = None) -> None:
        pipeline_event = None
        if ws is not None and (self.runners.get(runner_id) is not ws or
                               self.runner_incarnations.get(runner_id) != incarnation):
            return
        if self.runner_capabilities.get(runner_id, {}).get("runtime_env") in {"wsl", "windows"}:
            from ..wsl_paths import message_paths
            msg = message_paths(msg, "windows")
        runner_seq = msg.get("runner_seq")
        if runner_seq is not None and runner_seq <= self.store.runner_seen(runner_id):
            await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})
            return
        typ = msg.get("type")
        # Keep current staff activity in the task ledger: the 200-event snapshot window
        # can evict a tool start while a quiet command is still running.
        if typ in {"agent.status", "agent.tool", "agent.tool_result", "agent.log"}:
            tid = msg.get("task_id")
            if tid:
                # Own inputs before yielding; the reducer only touches the latest
                # activity on the writer thread, never a pre-await task snapshot.
                data = copy.deepcopy(msg.get("data") or {})
                activity_at = msg.get("ts") or time.time()

                def update_activity(activity: dict) -> dict:
                    if typ == "agent.status":
                        activity.update(data)
                        for key in ("ask", "approval", "jobs", "asks"):
                            activity[key] = data.get(key)
                        if data.get("state") == "working":
                            activity.pop("warning", None)
                    elif typ == "agent.tool":
                        if activity.get("state") in {None, "queued", "waiting"}:
                            activity["state"] = "working"
                        tools = dict(activity.get("tools") or {})
                        if not data.get("completed"):
                            tools[data.get("id") or "legacy"] = {**data, "at": activity_at}
                        activity["tools"] = tools
                        activity.pop("warning", None)
                    elif typ == "agent.tool_result":
                        tools = dict(activity.get("tools") or {})
                        tools.pop(data.get("id") or "legacy", None)
                        activity["tools"] = tools
                        activity["state"] = "working"
                        activity.pop("ask", None)
                        activity.pop("approval", None)
                        activity.pop("warning", None)
                    elif data.get("level") in {"warn", "alert"}:
                        activity["warning"] = data.get("text")
                    elif data.get("level") not in {"debug", "thinking"}:
                        activity.pop("warning", None)
                    lookup = data.get("external_lookup") if typ == "agent.log" else None
                    if isinstance(lookup, dict):
                        # #676: one line on the task board while a public lookup waits; the final status clears it.
                        if lookup.get("status") == "waiting":
                            activity["external_wait"] = {
                                "tool": str(lookup.get("tool") or ""), "elapsed_s": lookup.get("elapsed_s"),
                                "timeout_s": lookup.get("timeout_s"), "at": activity_at}
                        else:
                            activity.pop("external_wait", None)
                    return activity

                await self.store.aupdate_task_activity(tid, update_activity)
        if typ == "runner.roster":
            self.set_roster(runner_id, msg.get("agents", []), msg.get("capabilities"))
            await self.publish({"type": "roster.updated", "ts": time.time(), "data": {"agents": list(self.agents.values())}})
            return
        if typ == "approval.attachment.ack":
            aid = str(msg.get("id") or "")
            transfer_id = str(msg.get("transfer_id") or "")
            pending = self.attachment_waiters.get(transfer_id)
            waiter = pending[3] if pending else None
            expected = (aid, msg.get("paper_index"), runner_id)
            if pending and pending[:3] == expected and waiter and not waiter.done():
                waiter.set_result({"ok": bool(msg.get("ok")), "note": str(msg.get("note") or "")})
        if typ == "approval.ack":
            aid = str(msg["id"])
            async def acknowledge():
                queued = self.store.get("decision", aid)
                if queued and queued.get("origin") == runner_id:
                    await self.store.adelete("decision", aid)
            await self._approval_operation(aid, acknowledge())
        if typ == "control.nack":  # the runner does not hold that task: its steer waits for the step's next turn
            tid = str(msg.get("task_id") or "")
            entry = self.store.get("task", tid)
            live = bool(entry) and (not entry.get("completed") or bool(entry.get("abandoned")))
            # A live task the runner has not received yet (the control overtook its dispatch) keeps the steer: it is
            # sent again when the runner accepts the task. An ended one gives it back to the step.
            if entry and not live and msg.get("action") == "steer" and msg.get("steer_id"):
                key = self._entry_step(tid, entry)
                def nack(state):
                    if not self.store.get("step_control", key):
                        return False
                    for steer in state["steers"]:
                        if steer["id"] == msg["steer_id"] and steer.get("task") == tid:
                            steer["task"] = None

                _, cancelled = await self._update_step(key, nack)
                if cancelled:
                    raise asyncio.CancelledError
        if typ == "agent.breaker" and msg.get("task_id"):
            self._note_breaker_event(msg["task_id"], msg.get("data") or {})
            if (msg.get("data") or {}).get("enforcement") == "halt":
                await self._note_runner_halt(msg["task_id"])
        if typ == "agent.control" and msg.get("task_id"):  # the staff sheet's paused/halted line survives a reload
            await self._control_state(msg["task_id"], msg.get("data") or {}, event=msg,
                                      runner_id=runner_id, runner_seq=runner_seq)
        if typ == "ask.ack":
            entry = self.store.get("ask", str(msg["id"]))
            if entry:
                self.store.put("ask", str(msg["id"]), {**entry, "delivered": True})
        if typ == "task.result":
            rid = msg.get("request_id")
            tid = msg.get("task_id") or ""
            result = TaskResult.model_validate(msg["data"])
            # Snapshot and finishing admission are atomic with halt intent/send.
            # Release before settlement, which takes the step/control locks.
            async with self._halt_result_lock(tid):
                task = self.store.get("task", tid) if tid else None
                if not hasattr(self, "_finishing_step_tasks"):
                    self._finishing_step_tasks = set()
                self._finishing_step_tasks.add(tid)
            halt_at_wait = bool(task) and waiting(result) and (result.error_kind == "halted"
                                                              or bool(task.get("halt_requested")))
            if halt_at_wait:
                # The turn ended into HPC jobs or a question when the halt came: the jobs, the asks and the wake turn
                # stay, and the halt becomes a pause of the step, so the wake turn waits for the PI (PR #532).
                if result.error_kind == "halted":
                    result = result.model_copy(update={"ok": True, "error_kind": None, "error": None})
                    msg = {**msg, "data": result.model_dump(mode="json")}
                key = self._entry_step(tid, task)
                def pause(state):
                    if not self._step_alive(task):
                        return False
                    state["paused"] = True
                    state["ops"] += 1
                    state["pause_op"] = state["ops"]
                    state["last_task"] = state["last_task"] or tid

                _, cancelled = await self._update_step(key, pause)
                if cancelled:
                    raise asyncio.CancelledError
                await self._publish_control(tid, task, {"action": "halt_as_pause", "paused": True, "halt": False,
                                                        "source": "gateway"})
                await self.publish({"type": "agent.log", "ts": time.time(), "task_id": tid,
                                    "agent_id": (task.get("payload") or {}).get("agent_id"), "request_id": rid,
                                    "data": {"level": "alert", "text": "HPC 잡·질문을 기다리는 중이라 멈추지 않고 일시 정지했어요. "
                                                                       "재개하면 대기 뒤 turn이 이어집니다"}})
            elif task and task.get("halt_requested") and not result.ok and result.error_kind != "halted" \
                    and not waiting(result):
                # The PI halted it and it ended without finishing (a runner restart, a kill): park it, do not fail it.
                result = result.model_copy(update={"error_kind": "halted",
                                                   "error": f"{HALTED_ERROR} ({result.error or 'interrupted'})"})
                msg = {**msg, "data": result.model_dump(mode="json")}
            if task:  # steers this turn did not deliver go back to its step (#59 ①)
                await self._settle_steers(tid, task, result.control)
            if tid:
                self.task_breaker.pop(tid, None)
                self.store.delete("task_breaker", tid)
                self.orchestrator.restricted_calls.pop(tid, None)
                self.store.delete("restricted_ledger", tid)
                self.store.delete("breaker_release", tid)  # the task ended: nothing is left to release
                if result.error_kind == "halted":
                    await self._control_state(tid, {"paused": False, "halt": True, "halted": True}, replace=True)
                elif waiting(result):
                    pass  # an HPC or answer wait keeps its pause: the wake turn still waits for resume (PR #532 review)
                else:
                    await self._control_state(tid, None)
            if (result.pipeline_submission is not None and result.agent_id in PIPELINE_AUTHORS
                    and self.s.policy.bioinfo_agent.pipeline_pr
                    and result.ok and not waiting(result)):
                if self.store.get("pipeline_submission", tid) is None:
                    # A replayed result cannot replace the candidate or erase its PI decision.
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
                payload = task.get("payload") or {}
                meta = payload.get("meta") or {}
                request = self.requests.get(task.get("request_id") or "")
                if (request is not None and payload.get("agent_id") == self.s.orchestrator.cso_agent and
                        result.session_id and
                        (payload.get("resume_session_id") == request.get("cso_session_id") or
                         (meta.get("workdir") and meta.get("workdir") == request.get("cso_workdir")))):
                    request["cso_session_id"] = result.session_id
                    request["cso_workdir"] = result.workdir or meta.get("workdir") or request.get("cso_workdir")
                    await self.asave_request(task["request_id"])
            if task and task.get("request_id") in self.requests and (task.get("step_id") or task.get("kind") == "direct"):
                rid = task["request_id"]
                sid = task.get("step_id") or "direct"
                self.store.put("step_checkpoint", f"{rid}:{sid}",
                               {"task_id": tid, "attempt": task.get("attempt", 1),
                                "revision": task.get("revision", 0),
                                "result": result.model_dump(mode="json")})
            (await self._arecord_task_cost(rid, tid, result))
            if task:
                # Cancel waits on completed: publish it only after the cost snapshot commits.
                # A reported result also supersedes an abandonment (#112).
                self.store.put("task", tid, {**{k: v for k, v in task.items() if k != "abandoned"},
                                              "completed": True, "result": result.model_dump(mode="json")})
                self._refresh_roster()
                self._session_state_changed()
            self._finishing_step_tasks.discard(tid)
            if tid:
                # Completion must be visible before cleanup so a queued cancel cannot recreate the intent.
                self.store.delete("task_cancel", tid)
            fut = self.futures.pop(msg.get("task_id") or "", None)
            if fut and not fut.done():
                fut.set_result(result)
            if tid:
                await self.close_task_cards(tid)
        elif typ == "approval.requested" and (self.requests.get(msg["data"].get("request_id") or "") or {}).get(
                "status") == "cancelled":
            # A card queued before request cancel arrives after it: the hook hears no, the PI sees nothing (R13).
            a = msg["data"]
            note = "요청 취소로 닫힘"
            async def decline_cancelled():
                await self.store.acommit_approval(a["id"],
                    {"approval": a, "origin": runner_id, "approved": False,
                     "note": note, "state": "expired", "decided_at": time.time()},
                    {"origin": runner_id, "approved": False, "note": note})
                entry = self.approvals.pop(a["id"], None)
                self._cancel_approval_timers(a["id"])
                future = (entry or {}).get("future")
                if future is not None and not future.done():
                    future.set_result({"approved": False, "note": note, "state": "expired"})
            await self._approval_operation(a["id"], decline_cancelled())
            try:
                await self.flush_decisions(runner_id)
            except RunnerUnavailable:
                pass  # kept: sent again when that runner reconnects
            msg = {**msg, "type": "approval.expired", "data": {"id": a["id"], "note": note}}
        elif typ == "approval.requested":
            a = with_approval_copy(msg["data"])
            msg = {**msg, 'data': a}
            # The asking process: a card outlives neither it nor the agent's move to another runner (R2).
            added = await self._add_approval(a["id"], {"approval": a, "origin": runner_id,
                "origin_incarnation": self.runner_incarnations.get(runner_id),
                "origin_process": self.runner_processes.get(runner_id)})
            if not added:
                prior = self.store.get("approval_decision", a["id"])
                if prior.get("origin") == runner_id:
                    await self.flush_decisions(runner_id)
                msg = {**msg, "type": "approval.resolved", "data": {"id": a["id"],
                    **{k: prior[k] for k in ("approved", "note", "state") if k in prior}}}
        elif typ == "approval.timed_out":
            aid = str((msg.get("data") or {}).get("id") or "")
            decision = await self._timeout_approval(aid, runner_id=runner_id)
            if decision is None:
                if runner_seq is not None:
                    await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})
                return
            msg = {**msg, "type": "approval.resolved", "data": {"id": aid,
                **{k: decision[k] for k in ("approved", "note", "state")}}}
        elif typ == "restricted.requested":
            data = msg.get("data") or {}
            approved, note = self.orchestrator.decide_restricted_call({**data, "task_id": msg.get("task_id")})
            with contextlib.suppress(RunnerUnavailable):  # an unreachable runner's call has already timed out: a no
                await self.send_runner(runner_id, {"type": "restricted.resolved", "id": data.get("id"),
                                                   "approved": approved, "note": note})
            # What the staff sheet and the log show is the decision, not the call's input.
            msg = {"type": "agent.log", "ts": time.time(), "task_id": msg.get("task_id"),
                   "agent_id": msg.get("agent_id"), "request_id": msg.get("request_id"),
                   "data": {"level": "info" if approved else "warn",
                            "text": f"제한 중 {data.get('tool_name') or '도구'} 호출을 CSO가 "
                                    f"{'허용' if approved else '거절'}했어요: {note}"}}
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
        elif typ == "task.jobs_cancelled":  # the runner ended the local jobs of that step: stop resending
            self.store.delete("jobs_cancel", str(msg.get("task_id") or ""))
        elif typ == "task.accepted":
            tid = msg.get("task_id") or ""
            task = self.store.get("task", tid)
            if task:
                self.store.put("task", tid, {**task, "accepted": True, "runner_id": runner_id,
                                               "runner_incarnation": self.runner_incarnations.get(runner_id)})
                self._session_state_changed()
                if self.controllable(task.get("kind"), task.get("step_id"), task.get("request_id")):
                    await self._push_task_state(tid)  # a control that overtook the dispatch, once more
        elif typ == "job.submitted" and msg.get("task_id"):
            task = self.store.get("task", msg["task_id"])
            if task and not task.get("jobs_submitted"):
                self.store.put("task", msg["task_id"], {**task, "jobs_submitted": True})
        entry = (self.recruits_running.get(runner_id)
                 if typ in ("recruit.done", "recruit.failed") and not (msg.get("data") or {}).get("plan_task_id")
                 else None)
        if entry and entry.get("count"):
            entry["count"] -= 1
        if typ != "agent.control" or not msg.get("task_id"):
            await self.publish(msg, runner_id=runner_id, runner_seq=runner_seq)
        if pipeline_event is not None:
            await self.publish(pipeline_event)
        if runner_seq is not None:
            await self.send_runner(runner_id, {"type": "runner.ack", "runner_seq": runner_seq})

    # ----- tasks -----
    @staticmethod
    def _session_pin_refusal(task: Task, target: str, runtime: Any = _RUNTIME_UNCHECKED) -> TaskResult | None:
        """A resumed session and its absolute workdir live on the runner that made them (task meta
        ``session_runner``). Every place that picks a send target asks this, the first send and the re-send after a
        lost runner alike, so no target chosen later can receive them (PR #393 review). Nothing is sent: a real $0.

        With ``runtime`` (what the target runner reports now, ``runtime_env``), a runner-local environment binding must
        still match it: the same runner ID reconfigured native <-> WSL, or registered from another OS host, would
        otherwise receive a binding whose interpreter path belongs to the old runtime (#674). A runner that reports
        no runtime cannot prove the match and is refused. Without ``runtime`` only the runner ID is compared."""
        pinned = task.meta.get("session_runner")
        if pinned and pinned != target:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                              error=f"session belongs to runner {pinned}; {task.agent_id!r} is now on runner {target}")
        if runtime is not _RUNTIME_UNCHECKED:
            single = task.meta.get("environment_binding")
            bindings = [*(task.meta.get("environment_bindings") or []), *([single] if single else [])]
            stale = next((b for b in bindings if isinstance(b, dict) and b.get("runner_id") == target
                          and b.get("runtime") != runtime), None)
            if stale is not None:
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                                  error=(f"environment binding {stale.get('step_id')} expects runtime "
                                         f"{stale.get('runtime')} on runner {target}; the runner now reports "
                                         f"{runtime or 'no runtime'}"))
        return None

    async def dispatch_with_identity(self, task: Task, bind_identity) -> TaskResult:
        """Bind an internal caller's reservation/cancel handle before awaiting recovered work."""
        return await self.dispatch(task, bind_identity=bind_identity)

    async def dispatch(self, task: Task, *, bind_identity=None) -> TaskResult:
        if bind_identity is not None:
            bind_identity(task.id)
        if paper2agent.disabled_task(self.s, task):
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                              error="recruitment and Paper2Agent are disabled (recruit.enabled=false)")
        # Roster/lifecycle changes refresh agent_runner; dispatch consumes that resolved
        # owner without replacing it before checking the session's runner pin.
        if (self.requests.get(task.request_id or "") or {}).get("status") == "cancelled":
            # A step still unwinding after request cancel retries or moves on; nothing more is sent (R13).
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                              error="request cancelled")
        sid = task.meta.get("step_id") or task.meta.get("kind")
        if sid and task.request_id in self.recovery_steps:
            choice = self._recovery_choice(task)
            if choice is None and task.meta.get("kind") == "direct":
                checkpoint = self.store.get("step_checkpoint", f"{task.request_id}:direct")
                if (checkpoint and checkpoint.get("result")
                        and not self._interrupted_recovery_result(checkpoint["result"])):
                    return TaskResult.model_validate(checkpoint["result"]).model_copy(update={"cost_usd": 0.0})
            if choice is not None:
                tid, entry = choice
                if bind_identity is not None:
                    bind_identity(tid)
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
                    return (await self._aabandon_previous_generation(tid, entry))
                return await self._await_prior_task(tid, entry, task.agent_id, task.request_id, sid)
        paused = await self._wait_step_resumed(task)  # the PI paused this step: its next turn waits (#59 ①)
        if paused is not None:
            return paused
        if (self.requests.get(task.request_id or "") or {}).get("status") == "cancelled":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                              error="request cancelled")
        if self.agent_runner.get(task.agent_id) not in self.runners:
            stopped = await self.offer_wsl_setup(task)  # the local WSL runner is not ready: one PI card, not a wait
            if stopped is not None:
                return stopped
        if self.agent_runner.get(task.agent_id) not in self.runners and self._waits_for_runner(task):
            arrived = await self.wait_for_runner(task)
            if (self.requests.get(task.request_id or "") or {}).get("status") == "cancelled":
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                                  error="request cancelled")
            if not arrived and self._waits_for_runner(task):
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                                  error_kind="runner_offline", error=self._runner_wait_failure(task.agent_id))
            # Not arrived and nothing left to wait for: a runner came back without the agent (#521), below.
        rid = self.agent_runner.get(task.agent_id)
        if not rid:
            # Nothing was sent, so nothing was spent: a real $0, not an unaccounted cost (#270).
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                              error=self._no_runner_hosts(task.agent_id))
        refused = self._session_pin_refusal(task, rid, self.runner_capabilities.get(rid, {}).get("runtime_env"))
        if refused:
            return refused
        if task.agent_id in PIPELINE_AUTHORS and self.s.policy.bioinfo_agent.pipeline_pr:
            # Off by default (#300). The gateway's one setting decides; a runner sends pipeline files only when asked.
            task.meta["pipeline_pr"] = True
        task = await self._attach_steers(task)
        if self.controllable(task.meta.get("kind"), task.meta.get("step_id"), task.request_id):
            key = self.step_key(task.request_id, task.meta.get("step_id"), task.meta.get("kind"), task.id)
            if (key in getattr(self, "_closed_steps", set()) or
                    int(task.meta.get("revision") or 0) < self._step(key)["revision"]):
                await self._release_unpublished_attachment(key, task.id)
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0,
                                  cost_known=True, error="step ended before dispatch")
        # The awaits above (steer attachment) can let the runner re-register with another runtime: ask again just
        # before the task is recorded and sent, so the binding judged is the one that would be delivered (#674).
        refused = self._session_pin_refusal(task, rid, self.runner_capabilities.get(rid, {}).get("runtime_env"))
        if refused:
            if self.controllable(task.meta.get("kind"), task.meta.get("step_id"), task.request_id):
                await self._release_unpublished_attachment(
                    self.step_key(task.request_id, task.meta.get("step_id"), task.meta.get("kind"), task.id), task.id)
            return refused
        fut = asyncio.get_running_loop().create_future()
        self.futures[task.id] = fut
        self.task_runner[task.id] = rid
        original_incarnation = self.runner_incarnations.get(rid)
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
            await self._push_task_state(task.id)  # includes steers sent after payload attachment, before the frame
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
                    await self._release_steers(task)
                    result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                                        error="task delivery uncertain; manual recovery required")
                    (await self._arecord_task_cost(task.request_id, task.id, result, outcome_unknown=True))
                    return result
                if fut.done():
                    break
                target = self.agent_runner[task.agent_id]
                refused = self._session_pin_refusal(task, target,
                                                    self.runner_capabilities.get(target, {}).get("runtime_env"))
                if (refused or target != rid or
                        self.runner_incarnations.get(target) != original_incarnation):
                    # The first frame may have reached the pinned runner, so this is the uncertain-delivery outcome,
                    # not a certain $0 (same bookkeeping as the timeout above).
                    self.futures.pop(task.id, None)
                    self.task_runner.pop(task.id, None)
                    await self._release_steers(task)
                    result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                                        error=f"{refused.error + '; ' if refused else ''}"
                                              f"delivery to runner {rid} uncertain; manual recovery required")
                    (await self._arecord_task_cost(task.request_id, task.id, result, outcome_unknown=True))
                    return result
                try:
                    await self.send_runner(target, {"type": "task.dispatch", "task": task.model_dump(mode="json")})
                except RunnerUnavailable:
                    continue
                self.task_runner[task.id] = target
                await self._push_task_state(task.id)  # the task first, then what the PI sent meanwhile
                break
        except Exception:
            self.futures.pop(task.id, None)
            self.task_runner.pop(task.id, None)
            await self._release_steers(task)
            # The frame may already have reached the runner: an unaccounted cost, never a silent $0 (#270). The CSO's
            # pre-dispatch hold (#862) settles on this record instead of outliving the deleted task entry.
            (await self._arecord_task_cost(task.request_id, task.id,
                                   TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                                              error="task delivery failed; outcome unknown"), outcome_unknown=True))
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
                pinned = ((entry["payload"].get("meta") or {}).get("session_runner")
                          if isinstance(entry["payload"], dict) else None)
                if runner_id and (not pinned or pinned == runner_id):  # a pinned session never goes elsewhere
                    try:
                        await self.send_runner(runner_id, {"type": "task.dispatch", "task": entry["payload"]})
                        await self._push_task_state(tid)
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
                    await self._settle_steers(tid, entry, None)  # its steers wait for the step's next turn
                    result = TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                                        error="recovery runner unavailable; manual restart required")
                    # Dispatched before the restart and never answered: the runner may have spent anything (#270).
                    (await self._arecord_task_cost(request_id, tid, result, outcome_unknown=True))
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
        abandoned) or shutdown interrupted it. The caller then runs a new consult in a
        separate session and workdir, because the old one may still hold them.
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
        if (not entry.get("completed") or entry.get("abandoned") or not entry.get("result")
                or self._interrupted_recovery_result(entry.get("result"))):
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
                if entry.get("facilities_diagnosis"):
                    remaining = max(0, float(entry["deadline_at"]) - time.time())
                    await asyncio.wait_for(self.orchestrator.answer_ask(ask, runner_id), remaining)
                else:
                    await self.orchestrator.answer_ask(ask, runner_id)
            except ShutdownInterrupted as exc:
                await self.resolve_ask(ask, runner_id, ask_result(
                    reason="runner 종료로 상담이 중단됐습니다. 연결된 뒤 다시 요청해 주세요.",
                    interrupted=True, retryable=True, shutdown_evidence=exc.evidence, **{"from": "labhq"}))
            except asyncio.TimeoutError as exc:
                if entry.get("facilities_diagnosis"):
                    for tid, prior in self.consult_attempts(ask.id, "facilities"):
                        if not prior.get("completed"):
                            await self.cancel_task(tid)
                await self.resolve_ask(ask, runner_id, ask_result(
                    reason="시설팀 진단 시간 초과" if entry.get("facilities_diagnosis") else f"질의 처리 실패: {exc}",
                    **{"from": "labhq"}))
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
                and not entry.get("facilities_diagnosis")
                and entry.get("state") == "resolved" and entry.get("answer")]

    # ----- approvals -----
    async def request_facilities_diagnosis(self, task: Task, result: TaskResult, key: str) -> None:
        """Claim one automatic ask per request/logical turn before any asynchronous work."""
        from ..facilities.signatures import diagnosis_evidence

        rid = task.request_id or ""
        req = self.requests.get(rid, {})
        if (not is_active_request(req.get("status")) or req.get("cancelling") or
                key in (req.get("facilities_fixes") or {})):
            return
        ask_id = "ask_diag_" + hashlib.sha256(f"{rid}\0{key}".encode()).hexdigest()[:24]
        entry = self.store.get("ask", ask_id)
        if entry is None:
            # An offline/missing facility must not turn this optional diagnosis into a CSO call or runner wait.
            runner = self.agent_runner.get("facilities")
            available = "facilities" in self.agents and runner in self.runners
            ask = AskRequest(id=ask_id, task_id=result.task_id, request_id=rid, agent_id=task.agent_id,
                             to="facilities", question="Diagnose this environment failure; provide advice only.",
                             why_blocked=diagnosis_evidence(result.environment, result.error, result.tool_errors),
                             source_restricted=bool(task.meta.get("restricted_inputs")))
            scope = ((rid, "direct") if task.meta.get("kind") == "direct" else
                     (rid, "step", task.meta["step_id"]) if task.meta.get("step_id") else
                     (rid, "task", task.id))
            entry = {"ask": ask.model_dump(mode="json"), "origin": None, "scope": scope,
                     "facilities_diagnosis": True,
                     "deadline_at": time.time() + self.s.runner.task_timeout_s,
                     "state": "pending" if available else "resolved"}
            if not available:
                entry["answer"] = ask_result(reason="시설팀이 없거나 offline입니다", **{"from": "labhq"})
            self.store.put("ask", ask_id, entry)
        req.setdefault("facilities_diagnoses", {})[key] = ask_id
        await self.asave_request(rid)
        if entry.get("state") == "resolved":
            return
        ask = AskRequest.model_validate(entry["ask"])
        self._start_ask(ask, None)
        running = self.ask_tasks.get(ask_id)
        if running:
            await running

    async def request_approval(self, kind: str, summary: str, request_id: str | None = None,
                               detail: dict | None = None, timeout_s: int | None = None) -> dict:
        """Open a gateway card and wait for the PI.

        A PI-decision card (PI_DECISION_KINDS) waits ``pi_decision_timeout_s`` (default 7 days) and parks its request
        as ``waiting_pi`` until it is answered (R5); the card stays answerable the whole time. Only when even that
        bound passes does it return ``state: timed_out``, which callers record as ``*_timed_out``, never as a deny."""
        request = self.requests.get(request_id or "") or {}
        reviewed_result = (kind == "result_publish" and request.get("pi_reviewed_at")
                           and str(request.get("report") or "").strip())
        if request.get("status") == "cancelled" and not reviewed_result:
            return {"approved": False, "note": "request cancelled", "state": "cancelled", "approval_id": None,
                    "decided_at": time.time()}  # only reviewed partial results may open a post-cancel card
        rules = self.s.policy.approvals
        pi_decision = kind in PI_DECISION_KINDS
        req = ApprovalRequest(kind=kind, summary=summary, request_id=request_id, detail=detail or {},
                              timeout_s=timeout_s or (rules.pi_decision_timeout_s if pi_decision else rules.timeout_s))
        fut = asyncio.get_running_loop().create_future()
        state, parked = None, False
        try:
            await self._add_approval(req.id, {"approval": req.model_dump(mode="json"), "origin": None, "future": fut})
            state = self.requests.get(request_id or "")
            if state and state.get("status") == "cancelled" and not reviewed_result:
                await self.close_approval(req.id, "request cancelled")
                return {"approved": False, "note": "request cancelled", "state": "cancelled",
                        "approval_id": req.id, "decided_at": time.time()}
            parked = pi_decision and state is not None and is_active_request(state.get("status"))
            if parked:
                state.setdefault("pi_waits", {})[req.id] = {"kind": kind, "since": req.created_at,
                                                            "deadline_at": req.created_at + req.timeout_s}
            await self.publish({"type": "approval.requested", "ts": time.time(), "request_id": request_id,
                                "data": req.model_dump(mode="json")})
            if parked:
                await self._publish_hold_status(request_id)
            try:
                decision = await asyncio.wait_for(asyncio.shield(fut), req.timeout_s)
                return {**decision, "approval_id": req.id, "decided_at": time.time()}
            except asyncio.TimeoutError:
                decision = await self._timeout_approval(req.id)
                return {**{k: v for k, v in decision.items() if k not in {"approval", "origin"}}, "approval_id": req.id}
        except asyncio.CancelledError:
            # Creation may have committed while the request cancellation scanned the
            # live cards. Settle that new card too before propagating cancellation.
            await self.close_approval(req.id, "approval wait cancelled")
            raise
        finally:
            if parked and (state.get("pi_waits") or {}).pop(req.id, None) is not None:
                if not state.get("pi_waits"):
                    state.pop("pi_waits", None)
                await self._publish_hold_status(request_id)

    async def request_facilities_fix(self, rid: str, step_id: str, proposal: dict) -> dict:
        """Request one durable repair approval per step and reuse its decision after a restart."""
        async with self._approval_lock(f"facilities:{rid}:{step_id}"):
            return await self._request_facilities_fix(rid, step_id, proposal)

    async def _request_facilities_fix(self, rid: str, step_id: str, proposal: dict) -> dict:
        req_state = self.requests[rid]
        if req_state.get("status") == "cancelled":
            return {"approved": False, "status": "cancelled", "note": "request cancelled", "state": "cancelled"}
        fixes = req_state.setdefault("facilities_fixes", {})
        saved = fixes.get(step_id)
        if saved and saved.get("status") in {"declined", "timed_out", "failed", "applied", "succeeded"}:
            return {**saved, "approved": saved.get("status") in {"applied", "succeeded"}}
        approval_id = saved.get("approval_id") if saved else None
        decision = self.store.get("approval_decision", approval_id) if approval_id else None
        if decision:
            status = "approved" if decision.get("approved") else (
                "timed_out" if decision.get("state") == "timed_out" else "declined")
            saved = {**(saved or {}), "status": status, "approved": bool(decision.get("approved")),
                     "note": decision.get("note") or "", "state": decision.get("state") or "resolved"}
            fixes[step_id] = saved
            self._sync_hold_status(req_state)
            await self.asave_request(rid)
            return {"approved": bool(decision.get("approved")), **saved}

        if saved:
            entry = self.approvals.get(str(approval_id))
            if entry is None:
                raise RuntimeError("facilities fix approval record is missing")
            fut = entry.get("future")
            if fut is None or fut.done():
                fut = asyncio.get_running_loop().create_future()
                entry["future"] = fut
            approval = ApprovalRequest.model_validate(entry["approval"])
        else:
            detail = {key: proposal[key] for key in
                      ("action", "reason", "signature_id", "fix_id", "import_name", "package",
                       "repository", "command")
                      if key in proposal}
            package = detail.get("package")
            summary = (f"이 단계 전용 폴더에 {package} 설치 후 한 번 다시 실행할까요?" if package else
                       f"{step_id} 환경 문제를 고친 뒤 같은 단계를 한 번 다시 돌릴까요?")
            approval = ApprovalRequest(kind="facilities_fix", request_id=rid,
                                       summary=summary,
                                       detail={"step_id": step_id, **detail},
                                       timeout_s=self.s.policy.approvals.timeout_s)
            fut = asyncio.get_running_loop().create_future()
            async def open_fix():
                nonlocal saved
                await self._add_approval(approval.id, {"approval": approval.model_dump(mode="json"),
                                                      "origin": None, "future": fut})
                saved = {"status": "waiting", "approval_id": approval.id,
                         "proposal": {key: proposal[key] for key in
                                      ("fix_id", "signature_id", "action", "execution", "import_name", "package",
                                       "repository", "command")
                                      if key in proposal}}
                fixes[step_id] = saved
                req_state["status"] = "waiting_facilities_fix"
                await self.asave_request(rid)
                await self.publish({"type": "approval.requested", "ts": time.time(), "request_id": rid,
                                    "data": approval.model_dump(mode="json")})
            _, cancelled = await self._publication_write(open_fix())
            if cancelled:
                raise asyncio.CancelledError

        remaining = max(0.0, approval.created_at + approval.timeout_s - time.time())
        try:
            answer = await asyncio.wait_for(asyncio.shield(fut), remaining)
        except asyncio.TimeoutError:
            answer = await self._timeout_approval(approval.id)
        status = "approved" if answer.get("approved") else (
            "timed_out" if answer.get("state") == "timed_out" else "declined")
        saved.update(status=status, approved=bool(answer.get("approved")), note=answer.get("note") or "",
                     state=answer.get("state") or "resolved")
        self._sync_hold_status(req_state)
        await self.asave_request(rid)
        return {"approved": bool(answer.get("approved")), **saved}

    async def record_facilities_fix(self, rid: str, step_id: str, record: dict) -> None:
        """Persist the execution beside the request and leave one concise work-feed event."""
        req = self.requests[rid]
        entry = req.setdefault("facilities_fixes", {}).setdefault(step_id, {})
        status = record.get("status")
        if status not in {"applied", "succeeded", "failed"}:
            status = "succeeded" if record.get("ok") else "failed"
        entry.update(status=status, execution=record)
        self._sync_hold_status(req)
        await self.asave_request(rid)
        await self.publish({"type": "request.facilities_fix", "ts": time.time(), "request_id": rid,
                            "data": {"step_id": step_id, "ok": record.get("ok"), "status": status,
                                     "fix_id": record.get("fix_id"), "action": record.get("action"),
                                     "error": record.get("error")}})

    def _continuation_refusal_context(self, rid: str) -> dict | None:
        req = self.requests[rid]
        identity = research_continuation.refusal_context(req)
        if identity is None:
            return None
        if self._round_dispatched(rid, {"research_contract": {"round": identity["round"]}}):
            return None
        return identity

    async def offer_model_refusal(self, rid: str) -> None:
        """A durable PI choice after a failed plan; never launch a fallback automatically."""
        async with self._approval_lock(f"model_refusal:{rid}"):
            _, cancelled = await self._publication_write(self._offer_model_refusal(rid))
            if cancelled:
                raise asyncio.CancelledError

    async def _offer_model_refusal(self, rid: str) -> None:
        from ..model_refusal import REASON
        req = self.requests[rid]
        continuation = self._continuation_refusal_context(rid)
        if req.get("status") != "failed" or (req.get("outcome") != "model_refused" and continuation is None):
            return
        if req.get("model_refusal_approval_id"):
            return
        current = req.get("cso_model") or (self.agents.get(self.s.orchestrator.cso_agent) or {}).get("model")
        card = ApprovalRequest(kind="model_refused", request_id=rid, summary="계획 거부 뒤 다음 조치를 고르세요.",
                               detail={"reason": REASON, "current_model": current,
                           "models": [m for m in self.s.orchestrator.cso_models if m != current],
                           **({"continuation": continuation} if continuation else {})},
                               timeout_s=0,
                               pi_summary="모델 안전장치가 계획을 거부했습니다. 다음 조치를 고르세요.",
                               pi_reason=REASON,
                                pi_outcome=("다른 모델은 같은 이어 가기를 새 계획·CP1부터 재개합니다. 문구 수정은 새 요청입니다. 종료하면 실행하지 않습니다."
                                            if continuation else "새 모델 선택 또는 문구 수정은 새 요청으로 계획합니다. 종료하면 실행하지 않습니다."))
        await self._add_approval(card.id, {"approval": card.model_dump(mode="json"), "origin": None, "future": None})
        req["model_refusal_approval_id"] = card.id
        await self.asave_request(rid)
        await self.publish({"type": "approval.requested", "request_id": rid, "data": card.model_dump(mode="json")})

    def _model_refusal_request(self, rid: str, decision: dict | None, *,
                               approval_id: str | None = None, continuation: dict | None = None) -> str:
        old = self.requests[rid]
        retry = ((old.get("research_contract") or {}).get("continuation") or {}).get("refusal_retry") or {}
        if approval_id and retry.get("approval_id") == approval_id:
            if (retry.get("identity") != continuation or retry.get("decision") != decision
                    or old.get("status") not in {"waiting_for_runner", "running", "interrupted"}
                    or not research_continuation.refusal_retry_pending(old)
                    or self._round_dispatched(rid, old)
                    or old.get("cso_model") not in self.s.orchestrator.cso_models):
                raise ValueError("이어 가기 복구 선택이 바뀌었거나 이미 끝났습니다.")
            return rid  # Saved before card resolution: replay never allocates another round/request.
        context = self._continuation_refusal_context(rid)
        if old.get("status") != "failed" or (old.get("outcome") != "model_refused" and context is None):
            raise ValueError("모델 거부로 끝난 요청에서만 새 계획을 선택할 수 있습니다.")
        if context is not None and (context != continuation or not approval_id
                                    or old.get("model_refusal_approval_id") != approval_id):
            raise ValueError("이어 가기 복구 카드의 요청이나 계획이 바뀌었습니다.")
        if continuation is not None and context is None:
            raise ValueError("이어 가기 복구 카드가 더 이상 현재 계획을 가리키지 않습니다.")
        if not isinstance(decision, dict):
            raise ValueError("다른 CSO 모델 또는 수정한 요청을 명시적으로 선택해 주세요.")
        action = decision.get("action")
        text, model = old["text"], old.get("cso_model")
        if action == "model" and set(decision) == {"action", "cso_model"}:
            model = decision["cso_model"]
            current = old.get("cso_model") or (self.agents.get(self.s.orchestrator.cso_agent) or {}).get("model")
            if not isinstance(model, str) or model not in self.s.orchestrator.cso_models or model == current:
                raise ValueError("허용된 다른 CSO 모델을 선택해 주세요.")
        elif action == "edit" and set(decision) == {"action", "text"}:
            text = decision["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 4000 or text.strip() == old["text"].strip():
                raise ValueError("수정한 요청을 1~4000자로 적어 주세요.")
            text = text.strip()
        else:
            raise ValueError("알 수 없는 계획 거부 후속 선택입니다.")
        if context is not None and action == "model":
            research_continuation.prepare_refusal_retry(old, context, decision, approval_id)
            self.save_request(rid)
            return rid
        # A crash after creation but before recording the answer must not open a second request.
        found = next((key for key, req in self.requests.items() if req.get("model_refusal_from") == rid), None)
        if found:
            return found
        fields = {key: old[key] for key in ("mode", "work_kind", "scope_status", "project_dirs",
                                          "project_id", "route") if key in old}
        # Stored provenance is not input: revalidate only the public Reference fields.
        fields["references"] = [{key: ref[key] for key in ("kind", "value", "note") if key in ref}
                                for ref in old.get("references") or []]
        # Provenance such as a bench case_id stays with the new request (#779). revised_from is CP1 lineage: the CP1
        # 수정 요청 lookup and stored results read it, and this request links back through model_refusal_from instead.
        fields["meta"] = {key: value for key, value in (old.get("meta") or {}).items() if key != "revised_from"}
        body = RequestIn(**fields, text=text, cso_model=model, default_references=False,
                         budget_usd=old.get("requested_budget_usd", old.get("budget_usd")))
        return self.create_request(body, extra={"model_refusal_from": rid})

    async def _amodel_refusal_request(self, rid: str, decision: dict | None, *,
                               approval_id: str | None = None, continuation: dict | None = None) -> str:
        old = self.requests[rid]
        retry = ((old.get("research_contract") or {}).get("continuation") or {}).get("refusal_retry") or {}
        if approval_id and retry.get("approval_id") == approval_id:
            if (retry.get("identity") != continuation or retry.get("decision") != decision
                    or old.get("status") not in {"waiting_for_runner", "running", "interrupted"}
                    or not research_continuation.refusal_retry_pending(old)
                    or self._round_dispatched(rid, old)
                    or old.get("cso_model") not in self.s.orchestrator.cso_models):
                raise ValueError("이어 가기 복구 선택이 바뀌었거나 이미 끝났습니다.")
            return rid  # Saved before card resolution: replay never allocates another round/request.
        context = self._continuation_refusal_context(rid)
        if old.get("status") != "failed" or (old.get("outcome") != "model_refused" and context is None):
            raise ValueError("모델 거부로 끝난 요청에서만 새 계획을 선택할 수 있습니다.")
        if context is not None and (context != continuation or not approval_id
                                    or old.get("model_refusal_approval_id") != approval_id):
            raise ValueError("이어 가기 복구 카드의 요청이나 계획이 바뀌었습니다.")
        if continuation is not None and context is None:
            raise ValueError("이어 가기 복구 카드가 더 이상 현재 계획을 가리키지 않습니다.")
        if not isinstance(decision, dict):
            raise ValueError("다른 CSO 모델 또는 수정한 요청을 명시적으로 선택해 주세요.")
        action = decision.get("action")
        text, model = old["text"], old.get("cso_model")
        if action == "model" and set(decision) == {"action", "cso_model"}:
            model = decision["cso_model"]
            current = old.get("cso_model") or (self.agents.get(self.s.orchestrator.cso_agent) or {}).get("model")
            if not isinstance(model, str) or model not in self.s.orchestrator.cso_models or model == current:
                raise ValueError("허용된 다른 CSO 모델을 선택해 주세요.")
        elif action == "edit" and set(decision) == {"action", "text"}:
            text = decision["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 4000 or text.strip() == old["text"].strip():
                raise ValueError("수정한 요청을 1~4000자로 적어 주세요.")
            text = text.strip()
        else:
            raise ValueError("알 수 없는 계획 거부 후속 선택입니다.")
        if context is not None and action == "model":
            research_continuation.prepare_refusal_retry(old, context, decision, approval_id)
            (await self.asave_request(rid))
            return rid
        # A crash after creation but before recording the answer must not open a second request.
        found = next((key for key, req in self.requests.items() if req.get("model_refusal_from") == rid), None)
        if found:
            return found
        fields = {key: old[key] for key in ("mode", "work_kind", "scope_status", "project_dirs",
                                          "project_id", "route") if key in old}
        # Stored provenance is not input: revalidate only the public Reference fields.
        fields["references"] = [{key: ref[key] for key in ("kind", "value", "note") if key in ref}
                                for ref in old.get("references") or []]
        # Provenance such as a bench case_id stays with the new request (#779). revised_from is CP1 lineage: the CP1
        # 수정 요청 lookup and stored results read it, and this request links back through model_refusal_from instead.
        fields["meta"] = {key: value for key, value in (old.get("meta") or {}).items() if key != "revised_from"}
        body = RequestIn(**fields, text=text, cso_model=model, default_references=False,
                         budget_usd=old.get("requested_budget_usd", old.get("budget_usd")))
        return (await self.acreate_request(body, extra={"model_refusal_from": rid}))



    async def resolve_approval(self, approval_id: str, approved: bool, note: str = "",
                               choice: str | None = None, storage: dict | None = None,
                               model_refusal: dict | None = None) -> None:
        pending = self.approvals.get(approval_id)
        if pending and pending["approval"].get("kind") == "result_publish":
            # The answer to a publish card and a redraft of that edition never interleave (#607): a redraft that came
            # first has closed this card (KeyError below), one that comes after sees the upload started and is refused.
            async with self.result_storage.card_lock:
                async with self._approval_lock(approval_id):
                    return await self._resolve_approval(approval_id, approved, note, choice, storage)
        async with self._approval_lock(approval_id):
            return await self._resolve_approval(approval_id, approved, note, choice, storage, model_refusal)

    async def _resolve_approval(self, approval_id: str, approved: bool, note: str = "",
                                choice: str | None = None, storage: dict | None = None,
                                model_refusal: dict | None = None) -> None:
        pending = self.approvals.get(approval_id)
        retry_id = None
        if pending and pending["approval"].get("kind") == "model_refused" and approved:
            retry_id = (await self._amodel_refusal_request(pending["approval"]["request_id"], model_refusal,
                approval_id=approval_id, continuation=(pending["approval"].get("detail") or {}).get("continuation")))
        if pending and approved and pending["approval"].get("kind") == "result_publish":
            req = self.requests.get(pending["approval"].get("request_id")) or {}
            if not req.get("pi_reviewed_at"):
                raise ValueError("보고서 검토 완료 뒤 게시해 주세요.")
        if pending and (pending["approval"].get("detail") or {}).get("storage"):
            rid = pending["approval"]["request_id"]
            detail = pending["approval"]["detail"]
            storage = await self.result_storage.select(rid, storage if approved and storage else {"mode": "local"},
                late=bool(detail.get("late_storage")), end=bool(detail.get("storage_end")), approval_id=approval_id)
            await self.publish({"type": "request.storage", "request_id": rid, "data": {"result_storage": storage}})
        entry = self.approvals.get(approval_id)
        if entry is None:
            raise KeyError(approval_id)
        chosen = {"choice": choice} if choice in DECISION_CHOICES else {}
        if retry_id:
            chosen.update(model_refusal=model_refusal,
                          **({"continued_request_id": retry_id} if retry_id == pending["approval"]["request_id"]
                             else {"new_request_id": retry_id}))
        if storage is not None:
            chosen["storage"] = storage
        if approved and entry["approval"].get("kind") == "resume":
            (await self._arecord_resume_cp1(entry["approval"], approval_id, note))  # before the card is resolved (#654 review)
        chosen = copy.deepcopy(chosen)
        decision = copy.deepcopy({"approval": entry["approval"], "origin": entry["origin"], "approved": approved,
                                  "note": note, **chosen, "decided_at": time.time()})
        delivery = (copy.deepcopy({"origin": entry["origin"], "approved": approved, "note": note, **chosen})
                    if entry["origin"] else None)
        async def commit_and_deliver():
            await self.store.acommit_approval(approval_id, decision, delivery)
            self.approvals.pop(approval_id, None)
            self._cancel_facilities_timeout(approval_id)
            self._cancel_resume_timer(approval_id)
            if entry["origin"]:
                if entry["origin"] in self.runners:
                    try:
                        await self._send_decision(entry["origin"], approval_id)
                    except RunnerUnavailable:
                        pass  # committed decision is replayed on reconnect
            elif entry.get("future") and not entry["future"].done():
                entry["future"].set_result({"approved": approved, "note": note, **chosen})
            elif entry["approval"].get("kind") == "resume":
                rid = entry["approval"]["request_id"]
                if approved and not is_terminal_request(self.requests[rid].get("status")):
                    await self._set_status(rid, "waiting_for_runner")
                    self._restart_request_asks(rid)
                    self.track_request(rid, self.resume_when_ready(rid))
            elif entry["approval"].get("kind") == "facilities_fix":
                rid = entry["approval"]["request_id"]
                step_id = (entry["approval"].get("detail") or {}).get("step_id")
                facilities_hold = self.requests[rid].get("status") == "waiting_facilities_fix"
                saved = (self.requests[rid].get("facilities_fixes") or {}).get(step_id)
                if saved is not None:
                    saved.update(status="approved" if approved else "declined", approved=approved, note=note)
                    self._sync_hold_status(self.requests[rid])
                    await self.asave_request(rid)
                if facilities_hold:
                    self.track_request(rid, self.resume_when_ready(rid))
            a = entry["approval"]
            await self.publish({"type": "approval.resolved", "ts": time.time(), "task_id": a.get("task_id"),
                                "agent_id": a.get("agent_id"), "request_id": a.get("request_id"),
                                "data": {"id": approval_id, "approved": approved, "note": note, **chosen}})
            if retry_id and retry_id == a.get("request_id"):
                self.track_request(retry_id, self.resume_when_ready(retry_id))
            if a.get("kind") == "resume" and not approved and not is_terminal_request(self.requests[a["request_id"]].get("status")):
                rid = a["request_id"]
                self._restore_unstarted_continuation(rid, self.requests[rid])
                self.requests[rid].update(status="failed", error="resume declined", finished_at=time.time())
                (await self.aschedule_terminal(rid, "request.failed", {"error": "resume declined"}))
            if a.get("kind") == "pipeline_contribute":
                await self.publish({"type": "pipeline.ready", "ts": time.time(), "task_id": a.get("task_id"),
                                    "request_id": a.get("request_id"), "data": {}})
        _, cancelled = await self._publication_write(commit_and_deliver())
        if cancelled:
            raise asyncio.CancelledError

    async def close_publish_cards(self, rid: str) -> None:
        for aid in self.result_storage.publish_cards(rid):
            await self.close_approval(aid, "요청이 중단되거나 대체되어 게시 카드를 닫았습니다")

    async def mark_interrupted(self, rid: str) -> dict:
        from ..interruption import recorded_shutdown_evidence, shutdown_interruption
        req = self.requests[rid]
        if (req.get("result_storage") or {}).get("status") == "published":
            raise ValueError("이미 게시한 결과가 있어 interrupted로 정정할 수 없습니다. 게시된 결과를 이 명령이 바꾸지 않습니다")
        evidence = recorded_shutdown_evidence(self.s, req)
        if req.get("status") != "failed" or req.get("revised_to") or not shutdown_interruption(
                exit_code=evidence.get("exit_code"), error=evidence.get("error"), stopping=evidence.get("stopping"),
                cancelled=evidence.get("cancelled")):
            raise ValueError("종료 당시의 종료 코드와 종료 중 표시가 확인된 failed 요청만 정정합니다")
        self.interrupt_request(rid, evidence=evidence, correction=True)
        await self._send_committed_event()
        await self.close_publish_cards(rid)
        return {"request_id": rid, "status": "interrupted"}

    async def close_approval(self, aid: str, note: str, *, notify_runner: bool = True) -> bool:
        """Close a pending card without a PI answer: whoever waits on it hears "no" with state ``expired``.

        A runner-origin card's hook is told too unless that runner process is gone (``notify_runner=False``)."""
        return await self._approval_operation(aid, self._close_approval(
            aid, note, notify_runner=notify_runner, caller=asyncio.current_task()))

    async def _close_approval(self, aid: str, note: str, *, notify_runner: bool, caller=None) -> bool:
        entry = self.approvals.get(aid)
        if entry is None:
            return False
        origin = entry.get("origin")
        answer = {"approved": False, "note": note, "state": "expired"}
        decision = {"approval": entry["approval"], "origin": origin, **answer, "decided_at": time.time()}
        delivery = {"origin": origin, "approved": False, "note": note} if origin and notify_runner else None
        await self.store.acommit_approval(aid, decision, delivery)
        self.approvals.pop(aid, None)
        self._cancel_approval_timers(aid, caller)
        if origin and notify_runner:
            if origin in self.runners:
                try:
                    await self._send_decision(origin, aid)
                except RunnerUnavailable:
                    pass  # kept: sent again when that runner reconnects
        future = entry.get("future")
        if future is not None and not future.done():
            future.set_result(answer)
        await self.publish({"type": "approval.expired", "ts": time.time(),
                            "request_id": entry["approval"].get("request_id"), "data": {"id": aid, "note": note}})
        return True

    async def cancel_request(self, rid: str, *, active_only: bool = False,
                             expected_created_at: float | None = None) -> dict:
        """Stop a request for good (R13) and report what it finished.

        Its orchestration stops (no retry, re-plan or card after this), every live task gets a cancel on its
        runner, its quota/login waits and pending cards are released, and it ends as ``cancelled`` with a short
        report of the partial results. Calling it again returns the same answer."""
        req = self.requests[rid]
        status = req.get("status")
        # No await separates this identity/state check from the cancellation transition below.
        # A monitor must never turn a CP4 review into a fabricated PI decision.
        if active_only:
            if expected_created_at is None or req.get("created_at") != expected_created_at:
                raise ValueError("request creation identity changed")
            if not is_active_request(status):
                return {"request_id": rid, "status": status, "skipped": True,
                        "reason": "request is not active"}
        if status == "cancelled":
            return {"request_id": rid, "status": "cancelled", "already": True,
                    "cancelled_tasks": list(req.get("cancelled_tasks") or [])}
        if not active_only and status == "review_pending":
            return {**(await self._afinal_review(rid, "cancelled")), "cancelled_tasks": []}
        if is_terminal_request(status):
            raise ValueError(f"이미 끝난 요청입니다({status})")
        self._restore_unstarted_continuation(rid, req)
        now = time.time()
        req.update(status="cancelled", outcome="cancelled", error="PI가 요청을 취소했습니다", cancelled_at=now,
                   finished_at=now)
        req.pop("pi_waits", None)
        req.pop("runner_waits", None)
        quota = req.pop("quota_waits", None) or {}
        login = req.pop("login_waits", None) or {}
        # Kept until the terminal record commits (#521): `labhq down` waits for it, and a gateway that dies first
        # leaves it for the next start to finish (finish_interrupted_cancels). cancelled is not an active status.
        req["cancelling"] = {"quota_steps": sorted(quota)}
        for fix in (req.get("facilities_fixes") or {}).values():
            if fix.get("status") in {"waiting", "approved"}:
                fix["status"] = "cancelled"
        orchestration = self.request_tasks.pop(rid, None)
        unwinding = []
        if orchestration is not None and orchestration is not asyncio.current_task() and not orchestration.done():
            orchestration.cancel()
            unwinding.append(orchestration)
        stop_steps = getattr(self.orchestrator, "cancel_steps", None)
        if stop_steps is not None:
            unwinding.extend(stop_steps(rid))  # parallel steps and the briefing run beside it
        # The quota consumers above are cancelled before this first yield.
        async def finish():
            # Finish runner stops and bookkeeping before propagating caller cancellation.
            await self.asave_request(rid)
            cancelled = []
            for tid, entry in self.store.all("task").items():
                if entry.get("request_id") != rid:
                    continue
                waiter = self.jobs_waiters.pop(tid, None)  # a step parked on HPC jobs stops waiting for them
                if waiter is not None and not waiter.done():
                    waiter.cancel()
                if (not entry.get("completed") or entry.get("abandoned")) and await self.cancel_task(tid):
                    cancelled.append(tid)
                elif entry.get("completed"):
                    # A step parked on its jobs has no running task to cancel: its runner ends the local jobs it started,
                    # so no process outlives the request (#675).
                    runner_id = self.task_runner.get(tid) or entry.get("runner_id")
                    if runner_id:
                        # Kept until that runner confirms (task.jobs_cancelled): a runner that is away or restarting
                        # gets it again when it reconnects (flush_task_cancels).
                        self.store.put("jobs_cancel", tid, {"runner_id": runner_id, "requested_at": time.time()})
                        try:
                            await self.send_runner(runner_id, {"type": "task.jobs_cancel", "task_id": tid})
                        except RunnerUnavailable:
                            pass
            req["cancelled_tasks"] = cancelled
            await self.asave_request(rid)  # a restart before the terminal record still reports these
            for aid, entry in list(self.approvals.items()):
                if entry["approval"].get("request_id") == rid:
                    await self.close_approval(aid, "요청 취소로 닫힘")
            for engine in sorted({str(entry.get("engine") or "") for entry in login.values()}):
                await self.login_recovered(engine, reason="request cancelled")
            if unwinding:
                await asyncio.wait(unwinding, timeout=5)  # let them unwind before the terminal record
            await self._await_cancelled_results(rid, cancelled)
            await self._afinish_cancel(rid)
            return {"request_id": rid, "status": "cancelled", "already": False, "cancelled_tasks": cancelled}

        result, cancelled = await self._publication_write(finish())
        if cancelled:
            raise asyncio.CancelledError
        return result

    def _finish_cancel(self, rid: str) -> None:
        """Write a cancelled request's report and terminal record. ``cancelling`` leaves in that same commit."""
        req = self.requests[rid]
        pending = req.pop("cancelling", None) or {}
        report, appendix = self._cancel_report(req, list(req.get("cancelled_tasks") or []),
                                               dict.fromkeys(pending.get("quota_steps") or []))
        from ..report_format import split_appendix
        from ..report_contract import (available_result_files, build_report_package, reader_markdown,
                                       validate_report_package, with_report_front)
        appendix, execution = split_appendix(appendix)
        req.update(report=report, report_appendix=appendix, execution_record=execution)
        package = build_report_package(req, report, dict(req.get("results") or {}))
        req["report_package"] = package.to_dict()
        req["report_contract_check"] = validate_report_package(
            package,
            available_files=available_result_files(dict(req.get("results") or {})),
            reader_documents={"web·bundle·PDF": reader_markdown(with_report_front(report, package))},
        )
        data = {"ok": False, "status": "cancelled", "error": req["error"],
                "report_package": req["report_package"], **_terminal_reports(rid, report, appendix, execution),
                "cost_usd": req.get("cost_usd", 0), "cost_known": req.get("cost_known", True),
                "cost_summary": req.get("cost_summary"), "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}
        self.schedule_terminal(rid, "request.completed", data)

    async def _afinish_cancel(self, rid: str) -> None:
        """Write a cancelled request's report and terminal record. ``cancelling`` leaves in that same commit."""
        before = copy.deepcopy(self.requests[rid])
        req = copy.deepcopy(before)
        pending = req.pop("cancelling", None) or {}
        report, appendix = self._cancel_report(req, list(req.get("cancelled_tasks") or []),
                                               dict.fromkeys(pending.get("quota_steps") or []))
        from ..report_format import split_appendix
        from ..report_contract import (available_result_files, build_report_package, reader_markdown,
                                       validate_report_package, with_report_front)
        appendix, execution = split_appendix(appendix)
        req.update(report=report, report_appendix=appendix, execution_record=execution)
        package = build_report_package(req, report, dict(req.get("results") or {}))
        req["report_package"] = package.to_dict()
        req["report_contract_check"] = validate_report_package(
            package,
            available_files=available_result_files(dict(req.get("results") or {})),
            reader_documents={"web·bundle·PDF": reader_markdown(with_report_front(report, package))},
        )
        data = {"ok": False, "status": "cancelled", "error": req["error"],
                "report_package": req["report_package"], **_terminal_reports(rid, report, appendix, execution),
                "cost_usd": req.get("cost_usd", 0), "cost_known": req.get("cost_known", True),
                "cost_summary": req.get("cost_summary"), "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}
        await self.aschedule_terminal(rid, "request.completed", data, request=req, before=before)

    def _round_dispatched(self, rid: str, req: dict) -> bool:
        """Whether a step task of the request's current research round is in the task ledger (#90).

        A round's tasks carry ``research_round`` from round 2 on; the ledger keeps every dispatched task."""
        round_no = int((req.get("research_contract") or {}).get("round") or 1)
        return any(entry.get("request_id") == rid and entry.get("step_id") and
                   int(((entry.get("payload") or {}).get("meta") or {}).get("research_round") or 1) == round_no
                   for entry in self.store.all("task").values())

    def _restore_unstarted_continuation(self, rid: str, req: dict) -> bool:
        """Put the last approved round back before a gateway path writes a terminal continuation record.

        Judged by the task ledger, not ``continuation.verified``: the gateway can stop after the new CP1 saved that
        flag and before the first step went out (PR #527 review)."""
        if not (req.get("research_contract") or {}).get("continuation"):
            return False  # no continuation round, so no ledger read on every cancel
        if not Orchestrator._continuation_unstarted(req, self._round_dispatched(rid, req)):
            return False
        contract = req["research_contract"]
        carried = contract["continuation"]
        previous = contract["rounds"][-1]
        draft = research_continuation.restore_round(req, previous)
        if draft:
            carried["declined_plan"] = draft
        req["results"] = copy.deepcopy(previous.get("results") or {})
        return True

    async def finish_interrupted_cancels(self) -> list[str]:
        """At startup: finish a cancel whose gateway stopped before its terminal record (#521). The wait for the
        cancelled turns died with that gateway, so a turn that has not reported is counted as unaccounted now."""
        finished = []
        for rid, req in list(self.requests.items()):
            if req.get("status") == "cancelled" and req.get("cancelling") is not None:
                await self._await_cancelled_results(rid, list(req.get("cancelled_tasks") or []), wait_s=0)
                await self._afinish_cancel(rid)
                finished.append(rid)
        return finished

    async def _await_cancelled_results(self, rid: str, cancelled: list[str], wait_s: float | None = None) -> None:
        """Give each cancelled turn a short time to report, so its cost is counted; a turn that does not report is
        counted as unaccounted, never as $0 (#270)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (self.cancel_result_wait_s if wait_s is None else wait_s)
        def silent() -> list[str]:
            return [tid for tid in cancelled
                    if not ((self.store.get("task", tid) or {}).get("completed") and
                            not (self.store.get("task", tid) or {}).get("abandoned"))]
        while silent() and loop.time() < deadline:
            await asyncio.sleep(0.05)
        for tid in silent():
            entry = self.store.get("task", tid) or {}
            agent_id = (entry.get("payload") or {}).get("agent_id") or "unknown"
            (await self._arecord_task_cost(rid, tid, TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                                                        error="request cancelled before the turn reported"),
                                   outcome_unknown=True))

    @staticmethod
    def _cancel_report(req: dict, cancelled: list[str], quota: dict) -> tuple[str, str]:
        """The PI's short report of a cancelled request: what finished, what did not."""
        when = time.strftime("%m-%d %H:%M", time.localtime(float(req.get("cancelled_at") or time.time())))
        results = req.get("results") or {}
        steps = (req.get("plan") or {}).get("steps") or []
        rows = []
        for step in steps or [{"id": sid} for sid in results]:
            sid = step.get("id")
            outcome = results.get(sid)
            who = f" ({step['agent_id']})" if step.get("agent_id") else ""
            if not isinstance(outcome, dict):
                rows.append(f"- {sid}{who}: 실행하지 않음")
            elif outcome.get("ok"):
                outputs = ", ".join(f"{outcome.get('workdir_id') or 'workdir'}/{path}"
                                    for path in (outcome.get("outputs") or [])[:5])
                text = short(" ".join(str(outcome.get("text") or "").split()), 300)
                rows.append(f"- {sid}{who}: 완료" + (f" — 산출 {outputs}" if outputs else "") +
                            (f"\n  {text}" if text else ""))
            else:
                rows.append(f"- {sid}{who}: 실패 — {short(str(outcome.get('error') or '원인 미기록'), 200)}")
        done = sum(1 for outcome in results.values() if isinstance(outcome, dict) and outcome.get("ok"))
        total = len(steps) or len(results)
        report = "\n".join([
            "## 결론과 권고",
            f"PI가 {when}에 이 요청을 취소했습니다. 진행 중이던 작업 {len(cancelled)}개에 중단을 보냈고 남은 단계는 "
            "실행하지 않았습니다. 이어서 하려면 새 요청을 보내세요.", "",
            "## 결과",
            f"끝난 단계 {done}개 / 전체 {total}개" if total else "끝난 단계가 없습니다.",
            *rows, "",
            "## 한계",
            "취소 시점까지의 부분 결과입니다. HPC에 이미 낸 작업은 labhq가 멈추지 않으니 클러스터에서 확인하세요."
            + (f" 한도 대기 중이던 단계: {', '.join(sorted(quota))}." if quota else "")])
        appendix = "취소한 task: " + (", ".join(cancelled) if cancelled else "없음")
        return report, appendix

    # ----- requests -----
    def create_request(self, body: RequestIn, extra: dict[str, Any] | None = None) -> str:
        """``extra``: request fields saved in the same first write (a CP1 수정 요청's links, plan and answers)."""
        if body.mode == "direct" and body.route == "team":
            raise ValueError("route=team cannot be used with direct mode")
        if body.mode == "direct" and body.cso_model:
            raise ValueError("cso_model is only valid for orchestrate or plan_only requests")
        if body.cso_model and body.cso_model not in self.s.orchestrator.cso_models:
            allowed = ", ".join(self.s.orchestrator.cso_models) or "(none)"
            raise ValueError(f"cso_model {body.cso_model!r} is not allowed; allowed models: {allowed}")
        rid = new_id("req")
        req = {"id": rid, "status": "running", "created_at": time.time(),
               "cost_known": True, "usage": {}, **body.model_dump(),
               # The budget the PI asked for; budget_usd may later rise on a budget card (a CP1 수정 요청 carries this).
               "requested_budget_usd": body.budget_usd, **(extra or {})}
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
        research = self.s.research
        intake = classify_intake(body.text, body.work_kind, mode=body.mode)
        req["research_options"] = {"contract_version": 2, "final_checkpoint": bool(
            research.enabled and research.evidence_checkpoint and research.final_checkpoint
            and body.mode == "orchestrate" and intake.work_kind == "research")}
        self.requests[rid] = req
        if proj:
            self.result_storage.inherited(rid)
        if originals:
            self.store.put("reference_original", rid, {"references": originals})
        self.save_request(rid)
        self.track_request(rid, self._start_request(rid))
        return rid

    async def acreate_request(self, body, extra=None):
        answer, cancelled = await self._publication_write(self._acreate_request(body, extra))
        if cancelled:
            raise asyncio.CancelledError
        return answer

    async def _acreate_request(self, body: RequestIn, extra: dict[str, Any] | None = None) -> str:
        """``extra``: request fields saved in the same first write (a CP1 수정 요청's links, plan and answers)."""
        if body.mode == "direct" and body.route == "team":
            raise ValueError("route=team cannot be used with direct mode")
        if body.mode == "direct" and body.cso_model:
            raise ValueError("cso_model is only valid for orchestrate or plan_only requests")
        if body.cso_model and body.cso_model not in self.s.orchestrator.cso_models:
            allowed = ", ".join(self.s.orchestrator.cso_models) or "(none)"
            raise ValueError(f"cso_model {body.cso_model!r} is not allowed; allowed models: {allowed}")
        rid = new_id("req")
        req = {"id": rid, "status": "running", "created_at": time.time(),
               "cost_known": True, "usage": {}, **body.model_dump(),
               # The budget the PI asked for; budget_usd may later rise on a budget card (a CP1 수정 요청 carries this).
               "requested_budget_usd": body.budget_usd, **(extra or {})}
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
        research = self.s.research
        intake = classify_intake(body.text, body.work_kind, mode=body.mode)
        req["research_options"] = {"contract_version": 2, "final_checkpoint": bool(
            research.enabled and research.evidence_checkpoint and research.final_checkpoint
            and body.mode == "orchestrate" and intake.work_kind == "research")}
        self.requests[rid] = req
        if proj:
            await self.result_storage.ainherited(rid)
        if originals:
            await self.store.aput("reference_original", rid, {"references": originals})
        (await self.asave_request(rid))
        self.track_request(rid, self._start_request(rid))
        return rid



    def revised_request_of(self, rid: str) -> str | None:
        """The request a CP1 수정 요청 of ``rid`` opened, found by meta.revised_from (saved in its first write)."""
        return next((other for other, req in self.requests.items()
                     if (req.get("meta") or {}).get("revised_from") == rid), None)

    def create_revised_request(self, rid: str, note: str) -> str:
        """CP1 수정 요청: a new research request with the PI's note, the previous plan to revise (``revision_of``), the
        answers and PI notes already given, the same references, project, requested budget and CSO model. It plans again
        from the note and asks a new CP1; the old request ends (no step ran). Everything the new request reads is in
        its first save, and a request already opened for ``rid`` is returned, so a restart never opens a second one.
        Raises what create_request raises (a removed project or CSO model, a reference no longer allowed)."""
        old = self.requests[rid]
        existing = self.revised_request_of(rid)
        if existing:
            if old.get("revised_to") != existing:
                old["revised_to"] = existing
                self.save_request(rid)
            if self.result_storage.publish_cards(rid):
                asyncio.get_running_loop().create_task(self.close_publish_cards(rid))
            return existing
        text = (str(old.get("text") or "").rstrip() +
                f"\n\n[CP1 수정 요청 · {rid}] PI가 이전 계획에 남긴 고칠 점:\n{note.strip()}")
        # A URL whose query was dropped is rebuilt from its original, so it is cleaned (and flagged) the same way again.
        originals = {item.get("value"): item.get("original")
                     for item in (self.store.get("reference_original", rid) or {}).get("references") or []
                     if item.get("original")}
        # Only the request's own pointers: the PI's defaults come back through default_references (#36). Plain dicts:
        # RequestIn re-runs Reference.normalize on an instance, which would clear the original kept above.
        references = [{"kind": ref["kind"], "note": ref.get("note"),
                       "value": originals.get(ref["value"], ref["value"]) if ref["kind"] == "url" else ref["value"]}
                      for ref in old.get("references") or [] if ref.get("source", "request") == "request"]
        contract = old.get("research_contract") or {}
        extra: dict[str, Any] = {
            "revised_from": rid,
            "revision_of": {"request_id": rid, "plan_sha256": contract.get("plan_sha256"),
                            "plan": copy.deepcopy(old.get("plan")), "note": note.strip()}}
        if old.get("clarifications"):  # answered once; not counted against this request's own clarify cards
            extra["clarifications"] = [{**copy.deepcopy(entry), "inherited": True} for entry in old["clarifications"]]
        if old.get("pi_notes"):  # notes sent before CP1 shaped the old plan; the latest ones, so a chain stays bounded
            extra["pi_notes"] = [{**copy.deepcopy(note), "inherited": True} for note in old["pi_notes"][-MAX_PI_NOTES:]]
        body = RequestIn(text=text, mode=old.get("mode", "orchestrate"), work_kind="research",
                         scope_status=old.get("scope_status", "in_scope"),
                         project_dirs=list(old.get("project_dirs") or []), references=references,
                         default_references=bool(old.get("default_references", True)),
                         budget_usd=old.get("requested_budget_usd", old.get("budget_usd")),
                         project_id=old.get("project_id"), cso_model=old.get("cso_model"),
                         route=old.get("route", "auto"), meta={**(old.get("meta") or {}), "revised_from": rid})
        new_rid = self.create_request(body, extra=extra)
        old["revised_to"] = new_rid
        self.save_request(rid)
        if self.result_storage.publish_cards(rid):
            asyncio.get_running_loop().create_task(self.close_publish_cards(rid))
        return new_rid

    async def acreate_revised_request(self, rid: str, note: str) -> str:
        """CP1 수정 요청: a new research request with the PI's note, the previous plan to revise (``revision_of``), the
        answers and PI notes already given, the same references, project, requested budget and CSO model. It plans again
        from the note and asks a new CP1; the old request ends (no step ran). Everything the new request reads is in
        its first save, and a request already opened for ``rid`` is returned, so a restart never opens a second one.
        Raises what create_request raises (a removed project or CSO model, a reference no longer allowed)."""
        old = self.requests[rid]
        existing = self.revised_request_of(rid)
        if existing:
            if old.get("revised_to") != existing:
                old["revised_to"] = existing
                (await self.asave_request(rid))
            if self.result_storage.publish_cards(rid):
                asyncio.get_running_loop().create_task(self.close_publish_cards(rid))
            return existing
        text = (str(old.get("text") or "").rstrip() +
                f"\n\n[CP1 수정 요청 · {rid}] PI가 이전 계획에 남긴 고칠 점:\n{note.strip()}")
        # A URL whose query was dropped is rebuilt from its original, so it is cleaned (and flagged) the same way again.
        originals = {item.get("value"): item.get("original")
                     for item in (self.store.get("reference_original", rid) or {}).get("references") or []
                     if item.get("original")}
        # Only the request's own pointers: the PI's defaults come back through default_references (#36). Plain dicts:
        # RequestIn re-runs Reference.normalize on an instance, which would clear the original kept above.
        references = [{"kind": ref["kind"], "note": ref.get("note"),
                       "value": originals.get(ref["value"], ref["value"]) if ref["kind"] == "url" else ref["value"]}
                      for ref in old.get("references") or [] if ref.get("source", "request") == "request"]
        contract = old.get("research_contract") or {}
        extra: dict[str, Any] = {
            "revised_from": rid,
            "revision_of": {"request_id": rid, "plan_sha256": contract.get("plan_sha256"),
                            "plan": copy.deepcopy(old.get("plan")), "note": note.strip()}}
        if old.get("clarifications"):  # answered once; not counted against this request's own clarify cards
            extra["clarifications"] = [{**copy.deepcopy(entry), "inherited": True} for entry in old["clarifications"]]
        if old.get("pi_notes"):  # notes sent before CP1 shaped the old plan; the latest ones, so a chain stays bounded
            extra["pi_notes"] = [{**copy.deepcopy(note), "inherited": True} for note in old["pi_notes"][-MAX_PI_NOTES:]]
        body = RequestIn(text=text, mode=old.get("mode", "orchestrate"), work_kind="research",
                         scope_status=old.get("scope_status", "in_scope"),
                         project_dirs=list(old.get("project_dirs") or []), references=references,
                         default_references=bool(old.get("default_references", True)),
                         budget_usd=old.get("requested_budget_usd", old.get("budget_usd")),
                         project_id=old.get("project_id"), cso_model=old.get("cso_model"),
                         route=old.get("route", "auto"), meta={**(old.get("meta") or {}), "revised_from": rid})
        new_rid = (await self.acreate_request(body, extra=extra))
        old["revised_to"] = new_rid
        (await self.asave_request(rid))
        if self.result_storage.publish_cards(rid):
            asyncio.get_running_loop().create_task(self.close_publish_cards(rid))
        return new_rid



    def start_followup(self, rid: str, text: str) -> dict:
        """Ask a finished request one more question in the same session and workspace (#36). Not a new request."""
        req = self.requests[rid]
        if not is_terminal_request(req.get("status")):
            raise ValueError(f"request is {req.get('status')}; ask a follow-up after it finishes")
        if req.get("status") == "cancelled":  # every turn of a cancelled request is refused, so it could only fail
            raise ValueError("취소한 요청에는 이어 묻기를 보낼 수 없습니다. 새 요청을 보내세요")
        if has_active_followup(req):
            raise ValueError("a follow-up for this request is still running")
        if req.get("followup_agent_id"):
            agent = req["followup_agent_id"]
        elif req.get("mode") == "direct":
            agent = req.get("agent_id")
        else:
            agent = self.s.orchestrator.cso_agent
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

    async def astart_followup(self, rid, text):
        answer, cancelled = await self._publication_write(self._astart_followup(rid, text))
        if cancelled:
            raise asyncio.CancelledError
        return answer

    async def _astart_followup(self, rid: str, text: str) -> dict:
        """Ask a finished request one more question in the same session and workspace (#36). Not a new request."""
        req = self.requests[rid]
        if not is_terminal_request(req.get("status")):
            raise ValueError(f"request is {req.get('status')}; ask a follow-up after it finishes")
        if req.get("status") == "cancelled":  # every turn of a cancelled request is refused, so it could only fail
            raise ValueError("취소한 요청에는 이어 묻기를 보낼 수 없습니다. 새 요청을 보내세요")
        if has_active_followup(req):
            raise ValueError("a follow-up for this request is still running")
        if req.get("followup_agent_id"):
            agent = req["followup_agent_id"]
        elif req.get("mode") == "direct":
            agent = req.get("agent_id")
        else:
            agent = self.s.orchestrator.cso_agent
        if agent not in self.agents:
            raise ValueError(f"agent {agent!r} is not on any connected runner")
        refusal = read_only_refusal(agent, self.agents[agent].get("engine"))
        if refusal:
            raise ValueError(refusal)
        entry = {"id": new_id("fu"), "text": text.strip(), "agent_id": agent, "status": "running",
                 "asked_at": time.time()}
        req.setdefault("followups", []).append(entry)
        (await self.asave_request(rid))
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
        # Notes a CP1 수정 요청 carried over from the old request do not use this request's own allowance.
        if len([note for note in notes if not note.get("inherited")]) >= MAX_PI_NOTES:
            raise ValueError(f"메모는 요청마다 {MAX_PI_NOTES}개까지 보낼 수 있습니다")
        entry = {"id": new_id("note"), "text": text.strip(), "at": time.time()}
        notes.append(entry)
        await self.asave_request(rid)
        await self.publish({"type": "request.note", "ts": entry["at"], "request_id": rid, "data": entry})
        return entry

    async def _start_request(self, rid: str) -> None:
        r = self.requests[rid]
        await self.publish({"type": "request.created", "ts": time.time(), "request_id": rid,
                            "data": {k: r.get(k) for k in ("text", "mode", "agent_id", "project_id", "references",
                                                           "route")}})
        # The plan reads the roster before its first dispatch, so wait for the first agent's runner here (R15).
        first = r.get("agent_id") if r.get("mode") == "direct" else self.s.orchestrator.cso_agent
        probe = Task(agent_id=str(first or ""), request_id=rid, prompt="")
        if (first and self.agent_runner.get(first) not in self.runners and
                self._waits_for_runner(probe, first=True)):
            if not await self.wait_for_runner(probe, first=True):
                if self.requests[rid].get("status") != "cancelled":
                    # A runner that came back without the agent ends the wait early; that is not a timeout (#521).
                    reason = (self._runner_wait_failure(first) if self._waits_for_runner(probe, first=True)
                              else self._no_runner_hosts(first))
                    (await self.orchestrator._finish(rid, reason, {}, ok=False, error=reason))
                return
            if self.requests[rid].get("status") == "cancelled":
                return
        await self.orchestrator.run_request(rid)

    def pipeline_prs(self) -> dict[str, dict]:
        """Each request's latest pipeline PR status from its stored row, so a late client sees it (#301 review)."""
        latest: dict[str, dict] = {}
        for entry in self.store.all("pipeline_submission").values():
            rid = entry.get("request_id")
            if not rid or entry.get("state") not in {"pending", "awaiting_approval", "open", "rejected", "declined"}:
                continue
            if rid not in latest or (entry.get("updated_at") or 0) >= (latest[rid].get("updated_at") or 0):
                latest[rid] = entry
        return {rid: {"status": e["state"], "name": (e.get("submission") or {}).get("name"),
                      "reason": e.get("reason"), "url": e.get("url"), "number": e.get("number")}
                for rid, e in latest.items()}

    def snapshot(self) -> dict[str, Any]:
        # Read the task ledger once for every request below; one read per request stalled the loop (2026-10-08).
        tasks = self.store.all("task")
        running = self.running_tasks(tasks)
        # Staff can also be queued, consulting, or answering a finished request.
        # Keep this separate from the request board's accepted-step selection.
        staff_tasks = {task["id"]: task for task in running}
        for tid, entry in tasks.items():
            if entry.get("completed") or not is_research_task(entry):
                continue
            payload = entry.get("payload") or {}
            staff_tasks[tid] = {"id": tid, "request_id": entry.get("request_id"),
                "agent_id": payload.get("agent_id"), "step_id": entry.get("step_id") or entry.get("kind"),
                "title": (payload.get("meta") or {}).get("title", ""),
                "state": "running" if entry.get("accepted") else "queued",
                "dispatched_at": entry.get("dispatched_at", 0), "activity": entry.get("activity") or {}}
        pipeline_prs = self.pipeline_prs()
        display_task_ids = {tid for tid, entry in tasks.items() if not is_research_task(entry)}
        recent_events = [event for event in self.events
                         if is_research_event(event, display_task_ids)][-200:]
        recent_terminals = {
            event.get("request_id") for event in recent_events
            if event.get("type") in {"request.completed", "request.failed"}
        }
        return {"type": "snapshot", "schema_version": 1, "seq": self.store.event_bounds()[1],
                "ts": time.time(), "data": {
            "session_started_at": self.session_started_at,
            **({"demo_status": self.demo_status} if getattr(self, "demo_status", None) is not None else {}),
            "agents": list(self.agents.values()),
            "runners": list(self.runners),
            "approvals": [e["approval"] for e in self.approvals.values()],
            "requests": [{**{k: v for k, v in r.items() if k in ("id", "text", "status", "mode", "created_at", "updated_at",
                                                                  "project_id", "plan", "cost_usd", "cost_known",
                                                                  "cost_summary", "usage", "usage_known", "agent_id",
                                                                  "references", "pi_notes", "route", "route_decision",
                                                                  "bundle_path", "bundle_status", "bundle_grade",
                                                                  "bundle_grade_reasons", "bundle_warning", "result_storage")},
                          "runner_waits": r.get("runner_waits") or {},
                          **({"semantic_devices": r["semantic_devices"]} if r.get("semantic_devices") else {}),
                          # the full list and full answers stay on the request (GET /api/requests/{id})
                          "followups": [snapshot_followup(f) for f in (r.get("followups") or [])[-20:]],
                          "step_status": self.request_summary(r, running)["step_progress"]["steps"],
                          **self.request_session_cost_fields(r),
                          "office_current": is_office_request(r),
                          "session_current": (is_office_request(r)
                                              or float(r.get("created_at") or 0) >= self.session_started_at),
                          "pi_review_pending": is_terminal_request(r.get("status")) and not r.get("pi_reviewed_at") and not r.get("cancelling", False),
                          "pi_reviewed_at": r.get("pi_reviewed_at"), "final_checkpoint": cp4.summary(r),
                          "error": short(r.get("error") or "", 2000),
                          "step_details": self.request_step_details(r.get("id", ""), r, tasks),
                          **({} if r.get("id") in recent_terminals else snapshot_reports(r)),
                          **({"pipeline_pr": pipeline_prs[r["id"]]} if r.get("id") in pipeline_prs else {}),
                          "review": r.get("review") or (r.get("review_progress") or {}).get("review")}
                         for r in self.requests.values()],
            "projects": [{"id": p.id, "name": p.name or p.id, "repo": p.repo, "visibility": p.visibility}
                         for p in self.s.projects],
            "default_references": [r.model_dump() for r in self.s.pi_profile.references],
            "running_tasks": running,
            "staff_tasks": list(staff_tasks.values()),
            "engine_holds": self.engine_login_holds(),
            "recent_events": [snapshot_event(e) for e in recent_events],
        }}

    def request_task_attempts(self, rid: str, expected_created_at: float, limit: int = 1000) -> dict:
        """Bounded, content-free durable task/cost snapshot, without step/kind filtering.

        Synchronous on the gateway event loop: no request mutation can interleave its reads.
        A content revision binds the exact returned projection, not an OS/process assertion.
        """
        request = copy.deepcopy(self.requests[rid])
        if request.get("created_at") != expected_created_at:
            raise ValueError("request creation identity changed")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid attempt limit")
        problems: list[str] = []

        def amount(value):
            return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

        def identifier(value):
            return value if isinstance(value, str) and 0 < len(value) <= 128 and all(
                c.isascii() and (c.isalnum() or c in "_.:-") for c in value) else None

        def cleanup(raw):
            if not isinstance(raw, dict) or not raw:
                return None
            # Never forward arbitrary strings, kill diagnostics, argv, workdir, or nested runner data.
            enums = {"status": {"not_started", "UNKNOWN", "job_close_contract_enforced"},
                     "job": {"active", "pending", "disabled", "unconfirmed"},
                     "spawn": {"failed", "cancelled"}, "wait": {"completed", "not_finished"}}
            out = {key: value if value in values else None for key, values in enums.items()
                   if isinstance((value := raw.get(key)), str)}
            for key in ("wrapper_pid", "wrapper_created"):
                out[key] = raw.get(key) if type(raw.get(key)) is int and raw[key] > 0 else None
            out["returncode"] = raw.get("returncode") if type(raw.get("returncode")) is int else None
            out["active_processes_measured"] = raw.get("active_processes_measured") if type(raw.get("active_processes_measured")) is bool else None
            return out

        ledger = self.store.all("task")
        if any(not isinstance(row, dict) for row in ledger.values()):
            problems.append("task_ledger_row_invalid")
        owned = sorted((tid, row) for tid, row in ledger.items() if isinstance(row, dict) and row.get("request_id") == rid)
        attempts = []
        for tid, row in owned[:limit]:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            result = row.get("result")
            identity = bool(identifier(tid) and payload.get("id") == tid and payload.get("request_id") == rid and identifier(payload.get("agent_id")))
            result_identity = bool(isinstance(result, dict) and result.get("task_id") == tid
                                   and result.get("agent_id") == payload.get("agent_id"))
            if not identity or (result is not None and not result_identity):
                problems.append("task_identity_unknown")
            if row.get("completed") is True and result is None:
                problems.append("completed_result_missing")
            attempts.append({"task_id": identifier(tid), "request_id": rid,
                "agent_id": identifier(payload.get("agent_id")), "identity_verified": identity,
                **{key: identifier(row.get(key)) for key in ("runner_id", "runner_incarnation", "step_id", "kind", "parent_task")},
                **{key: row.get(key) if type(row.get(key)) is int and row[key] >= 0 else None
                   for key in ("attempt", "revision", "parse_attempt")},
                **{key: row.get(key) if type(row.get(key)) is bool else None
                   for key in ("accepted", "completed", "abandoned")},
                "result": {"task_id": tid, "agent_id": identifier(result.get("agent_id")),
                    "ok": result.get("ok") if type(result.get("ok")) is bool else None,
                    "cleanup_evidence": cleanup(result.get("cleanup_evidence"))} if result_identity else None})

        def cost_map(name):
            raw = request.get(name)
            if raw is None:
                return None  # Missing is unknown, never an invented empty ledger.
            if not isinstance(raw, dict):
                problems.append("accounting_map_invalid")
                return None
            if len(raw) > limit:
                problems.append("accounting_truncated")
            out = {}
            for tid, value in list(sorted(raw.items()))[:limit]:
                key = identifier(tid)
                if key is None:
                    problems.append("accounting_identity_unknown")
                    continue
                if name == "cost_items":
                    value = value if isinstance(value, dict) else {}
                    status = value.get("status")
                    out[key] = {"status": status if isinstance(status, str) and status in {"actual", "estimated", "unknown"} else None,
                                "usd": amount(value.get("usd")), "recorded_at": amount(value.get("recorded_at"))}
                else:
                    out[key] = amount(value)
            return out

        accounting = {key: cost_map(key) for key in ("cost_items", "cost_by_task", "budget_holds")}
        reservations = {}
        reservation_ids = set()
        reservation_rows = self.store.get("pi_translation_reservations", rid)
        if reservation_rows is None:
            reservation_rows = {}
        if not isinstance(reservation_rows, dict):
            problems.append("translation_reservation_invalid")
            reservation_rows = {}
        for key, raw in sorted(reservation_rows.items()):
            if not isinstance(raw, dict):
                problems.append("translation_reservation_invalid")
                continue
            if raw.get("request_id") != rid:
                problems.append("translation_reservation_identity_unknown")
                continue
            tid = identifier(key)
            if tid is None:
                problems.append("translation_reservation_identity_unknown")
                continue
            reservation_ids.add(tid)
            if len(reservations) >= limit:
                problems.append("translation_reservations_truncated")
                continue
            value = {name: identifier(raw.get(name)) for name in
                     ("reservation_id", "task_id", "request_id", "agent_id")}
            value.update(ceiling_usd=amount(raw.get("ceiling_usd")),
                         attempt=1 if type(raw.get("attempt")) is int and raw["attempt"] == 1 else None,
                         state="reserved" if raw.get("state") == "reserved" else None,
                         source="pi_translation" if raw.get("source") == "pi_translation" else None)
            task_row = ledger.get(tid)
            task_row = task_row if isinstance(task_row, dict) else {}
            payload = task_row.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            valid = (value["reservation_id"] == value["task_id"] == tid
                     and value["request_id"] == rid and value["agent_id"] is not None
                     and value["attempt"] == 1 and value["state"] and value["source"]
                     and value["ceiling_usd"] is not None and value["ceiling_usd"] > 0)
            if task_row:
                valid = valid and (payload.get("agent_id") == value["agent_id"]
                    and amount(payload.get("budget_usd")) == value["ceiling_usd"]
                    and task_row.get("kind") == "pi_interpretation"
                    and type(task_row.get("attempt")) is int and task_row["attempt"] == value["attempt"])
            if not valid:
                problems.append("translation_reservation_invalid")
            reservations[tid] = value
        accounting["translation_reservations"] = reservations
        per_task = amount(self.s.policy.budget.per_task_usd)
        effective_limit = amount(request.get("budget_usd")) or amount(self.s.policy.budget.per_request_usd)
        accounting.update(reported_cost_usd=amount(request.get("cost_usd")),
                          cost_known=request.get("cost_known") if type(request.get("cost_known")) is bool else None,
                          budget_usd=amount(request.get("budget_usd")),
                          unknown_reserve_usd=per_task if per_task and per_task > 0 else effective_limit,
                          reserve_before_dispatch=getattr(self.s.policy.budget, "reserve_before_dispatch", None))
        if len(owned) > limit:
            problems.append("attempts_truncated")
        costs = set(accounting["cost_items"] or {}) | set(accounting["cost_by_task"] or {})
        if costs - {tid for tid, _ in owned}:
            problems.append("cost_task_missing_from_durable_ledger")
        expected = set(reservation_ids)
        for name in ("cost_items", "cost_by_task", "budget_holds", "usage_by_task"):
            raw = request.get(name)
            if isinstance(raw, dict):
                expected.update(key for key in raw if identifier(key))
                if any(identifier(key) is None for key in raw):
                    problems.append("expected_task_identity_unknown")
        cancelled = request.get("cancelled_tasks")
        if isinstance(cancelled, list):
            expected.update(tid for tid in cancelled if identifier(tid))
        elif cancelled is not None:
            problems.append("cancelled_task_ids_unknown")
        results = request.get("results")
        for result in [*(results.values() if isinstance(results, dict) else []), request.get("solo_result")]:
            if isinstance(result, dict) and identifier(result.get("task_id")):
                expected.add(result["task_id"])
        owned_ids = {tid for tid, _ in owned}
        missing = sorted(expected - owned_ids)
        if missing:
            problems.append("expected_task_missing_from_durable_ledger")
            for tid in missing[:max(0, limit - len(attempts))]:
                attempts.append({"task_id": tid, "request_id": rid, "agent_id": None,
                                 "identity_verified": False, "record_status": "MISSING_DURABLE_ROW",
                                 "accepted": None, "completed": None, "result": None})
        total = len(owned_ids | expected)
        if total > limit:
            problems.append("attempts_truncated")
        data = {"schema_version": 1, "capabilities": ["translation_reservations_v1"],
                "scope": "all_request_task_attempts", "request_id": rid,
                "created_at": request.get("created_at"), "request_status": request.get("status"),
                "completeness_scope": "durable_rows_plus_request_accounting_ids",
                "cancelling": request.get("cancelling") is not None,
                "total_attempts": total, "total_durable_attempts": len(owned),
                "missing_attempt_count": len(missing), "missing_attempt_ids": missing[:limit],
                "returned_attempts": len(attempts), "attempts": attempts,
                "accounting": accounting, "complete": not problems, "problems": sorted(set(problems))}
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > 1024 * 1024:
            data.update(attempts=[], returned_attempts=0, accounting=None, complete=False, problems=["response_size_limit"])
            encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        data["snapshot_revision"] = hashlib.sha256(encoded).hexdigest()
        return data

    def request_step_details(self, rid: str, request: dict,
                             ledger: Mapping[str, dict] | None = None) -> dict[str, dict]:
        tasks = [(tid, entry) for tid, entry in (self.store.all("task") if ledger is None else ledger).items()
                 if entry.get("request_id") == rid and entry.get("step_id")]
        review = request.get("review") or (request.get("review_progress") or {}).get("review") or {}
        details: dict[str, dict] = {}
        for step in (request.get("plan") or {}).get("steps", []):
            sid = step.get("id")
            matches = [(tid, entry) for tid, entry in tasks if entry.get("step_id") == sid]
            result = (request.get("results") or {}).get(sid) or {}
            quota = (request.get("quota_waits") or {}).get(sid) or {}
            login = (request.get("login_waits") or {}).get(sid) or {}
            latest = max(matches, key=lambda pair: pair[1].get("dispatched_at", 0), default=(None, {}))
            details[sid] = {
                "task_id": result.get("task_id") or latest[0],
                "attempts": max((int(entry.get("attempt") or 1) for _, entry in matches), default=0),
                "outputs": result.get("outputs") or [],
                "missing_outputs": result.get("missing_outputs") or [],
                "text": short(result.get("text") or "", SNAPSHOT_RESULT_CHARS),
                "error": result.get("error") or "",
                "quota_resume_at": quota.get("resume_at"),
                "quota_deadline_at": quota.get("deadline_at"),  # the longest wait, kept across a reload (#494)
                "quota_engine": quota.get("engine"),
                "login_resume_at": login.get("resume_at"),
                "login_engine": login.get("engine"),
                "login_reason": login.get("reason"),
                "review_issues": [issue for issue in review.get("issues", []) if issue.get("step_id") == sid],
            }
            environment = environment_problem(result) if result and not result.get("ok") else None
            if environment:  # the task card's "환경 문제" line survives a reconnect (#35)
                details[sid]["environment"] = environment
            if result and result.get("facilities_fix"):
                details[sid]["facilities_fix"] = result["facilities_fix"]
        return details


def create_app(settings: Settings, github_transport: httpx.AsyncBaseTransport | None = None,
               *, demo: bool = False) -> FastAPI:
    check_configured_packs(settings)  # before any state opens: a stale pack key stops the start (#170)
    hub = Hub(settings, github_transport)
    if demo:
        hub.demo_status = {"phase": "starting", "remaining_s": 0}
    app = FastAPI(title="labhq gateway", version="0.1.0", default_response_class=UTF8JSONResponse)
    app.state.hub = hub
    shutdown = WebShutdown(settings, hub)
    app.state.shutdown = shutdown

    visibility_checked = False

    @app.on_event("startup")
    async def check_dev_log_visibility() -> None:
        nonlocal visibility_checked
        if visibility_checked:
            return
        visibility_checked = True
        visibility = await dev_log_visibility(settings, github_transport)
        if visibility not in {"public", "unknown"}:
            return
        message = ("dev_log 저장소가 public입니다. 이미 게시한 기록도 노출될 수 있으니 비공개 설정을 확인하세요."
                   if visibility == "public" else
                   "dev_log 저장소의 private 여부를 확인하지 못했습니다(unknown). 토큰·접근 권한·연결을 확인하세요.")
        log.warning("%s", message)
        await hub.publish({"type": "agent.log", "agent_id": settings.orchestrator.cso_agent,
                           "data": {"level": "warn", "code": "dev_log_visibility", "text": message}})

    @app.on_event("startup")
    async def recover_terminal_deliveries() -> None:
        hub.knowledge.start()
        hub.recover_terminal_deliveries()
        await hub.finish_interrupted_cancels()
        for rid, req in hub.requests.items():
            if (req.get("status") == "interrupted" or req.get("revised_to")
                    or (req.get("status") == "failed" and not req.get("pi_reviewed_at"))):
                await hub.close_publish_cards(rid)
        hub.recover_pending_bundles()
        await hub.expire_stale_resume_cards()
        hub.recover_final_reviews()
        for rid, request in hub.requests.items():
            if is_terminal_request(request.get("status")):
                hub._restart_request_asks(rid)
                if not request.get("bundle_pending"):
                    hub.result_storage.schedule(rid)
            elif request.get("status") in {"waiting_quota", "waiting_login", "waiting_facilities_fix"}:
                hub.track_request(rid, hub.resume_held_request(rid))

    @app.on_event("shutdown")
    async def warn_running_on_shutdown() -> None:
        hub.stopping = True
        await hub.pi_interpreter.close()
        for timer in hub.final_review_timers.values():
            timer.cancel()
        await hub.result_storage.close()
        loop = asyncio.get_running_loop()
        local_tasks = [task for task in hub.terminal_tasks.values() if task.get_loop() is loop]
        if local_tasks:
            await asyncio.gather(*local_tasks, return_exceptions=True)
        await hub.knowledge.stop()
        # Every queued durable write finishes before the process goes (#606).
        if not await durable.adrain(durable.EXIT_DRAIN_S):
            log.warning("gateway shutdown: durable writes still pending after %.0f s", durable.EXIT_DRAIN_S)
        running = [r["id"] for r in hub.requests.values() if is_active_request(r.get("status"))]
        if running:
            log.warning("gateway shutdown with running requests: %s", ", ".join(running))

    def auth(authorization: str = Header(default="")) -> None:
        if not token_matches(authorization.removeprefix("Bearer ").strip(), settings.gateway.client_token):
            raise HTTPException(401, "bad client token")

    @app.middleware("http")
    async def refuse_new_work_while_stopping(request: Request, call_next):
        if shutdown.status == "stopping" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return UTF8JSONResponse({"detail": "종료 중입니다. 새 요청을 받지 않습니다."}, status_code=409)
        return await call_next(request)

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
            incarnation, process = hello.get("incarnation"), hello.get("process")
            (await hub.aregister_runner(runner_id, ws, hello.get("agents", []), incarnation,
                                hello.get("capabilities"), process))
            await hub.expire_orphan_approvals(runner_id, incarnation, process)
            await hub.flush_decisions(runner_id)
            await hub.flush_ask_answers(runner_id)
            await hub.flush_task_cancels(runner_id)
            await hub.flush_task_controls(runner_id)
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
                    # Storage settles under its own shield; a disconnected ASGI
                    # caller may cancel network delivery after that commit.
                    with contextlib.suppress(asyncio.CancelledError):
                        await hub.publish({"type": "runner.offline", "ts": time.time(), "data": {"runner_id": runner_id}})

    @app.websocket("/ws/client")
    async def ws_client(ws: WebSocket, token: str = "", since: int | None = None) -> None:
        if not token_matches(token, settings.gateway.client_token):
            await ws.accept()
            await ws.close(code=1008, reason="token")
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
                if shutdown.status == "stopping":
                    continue
                if msg.get("type") == "approval.resolve":  # phone taps 승인/거절
                    try:
                        await hub.resolve_approval(str(msg.get("id")), bool(msg.get("approved")), msg.get("note", ""),
                                                   msg.get("choice"), msg.get("storage"),
                                                   **({"model_refusal": msg["model_refusal"]}
                                                      if msg.get("model_refusal") is not None else {}))
                    except ValueError as exc:
                        await ws.send_text(json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False))
                    except KeyError:  # already decided elsewhere (another device, timeout) — keep the socket
                        await ws.send_text(json.dumps({"type": "approval.stale", "ts": time.time(),
                                                       "data": {"id": msg.get("id")}},
                                                      ensure_ascii=False, default=str))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    @app.get("/api/knowledge/search", dependencies=[Depends(auth)])
    async def knowledge_search(q: str) -> list[dict]:
        if not 2 <= len(q.strip()) <= 200:
            raise HTTPException(422, "query must contain 2 to 200 characters")
        return hub.knowledge.search(q)

    @app.get("/api/agents", dependencies=[Depends(auth)])
    async def agents() -> list[dict]:
        return list(hub.agents.values())

    @app.post("/api/requests", dependencies=[Depends(auth)])
    async def create_request(body: RequestIn) -> dict:
        if body.mode == "direct" and not hub.knows_agent(body.agent_id):
            raise HTTPException(404, f"unknown agent {body.agent_id!r}")
        if body.mode == "direct" and not_yet_active(hub.agents.get(body.agent_id or "") or {}):
            raise HTTPException(409, probation_refusal(str(body.agent_id)))  # still on probation (#424)
        try:
            return {"request_id": (await hub.acreate_request(body))}
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
        requests = sorted(hub.requests.values(), key=lambda r: (
            is_terminal_request(r.get("status")) and bool(r.get("pi_reviewed_at")),
            -float(r.get("updated_at") or r.get("created_at") or 0)))
        chosen = [r for r in requests if status == "all" or (status == "running" and is_active_request(r.get("status")))
                  or r.get("status") == status][:limit]
        running = hub.running_tasks() if chosen else []  # one ledger read for the whole list
        return [hub.request_summary(r, running) for r in chosen]

    @app.get("/api/requests/summary", dependencies=[Depends(auth)])
    async def request_totals() -> dict:
        requests = list(hub.requests.values())
        return {"request_count": len(requests),
                "past_count": sum(not is_office_request(request) for request in requests),
                "session": request_session_cost_totals(requests, hub.session_started_at),
                "total": request_cost_totals(requests)}

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

    @app.post("/api/requests/{rid}/storage", dependencies=[Depends(auth)])
    async def storage_card(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return await hub.result_storage.open_card(rid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/storage/redraft", dependencies=[Depends(auth)])
    async def storage_redraft(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return await hub.result_storage.redraft(rid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/storage/to-github", dependencies=[Depends(auth)])
    async def storage_to_github(rid: str, body: dict | None = None) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        body = body or {}
        try:
            return await hub.result_storage.to_github(rid, body.get("name"), body.get("visibility") or "private")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/storage/rebuild", dependencies=[Depends(auth)])
    async def storage_rebuild(rid: str, body: dict | None = None) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        body = body or {}
        try:
            return await hub.result_storage.rebuild(rid, body.get("name"), body.get("visibility") or "private")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/storage/republish", dependencies=[Depends(auth)])
    async def storage_republish(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return await hub.result_storage.republish(rid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/mark-interrupted", dependencies=[Depends(auth)])
    async def mark_interrupted(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return await hub.mark_interrupted(rid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/review-done", dependencies=[Depends(auth)])
    async def review_done(rid: str, body: dict | None = None) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            body = body or {}
            decision = body.get("decision", "accepted")
            if decision not in {"accepted", "rejected"}:
                raise ValueError("검토 결정은 accepted 또는 rejected입니다")
            choice = None
            if body.get("storage"):
                from ..result_storage import check_choice
                mode = {"github": "new", "existing": "existing"}.get(body["storage"], body["storage"])
                default_name = "labhq-" + rid.removeprefix("req_") if mode == "new" else ""
                choice = {"mode": mode, "repo": body.get("name") or default_name,
                          "visibility": body.get("visibility", "private")}
                check_choice(choice, end=True)  # before the review is saved (PR #711 review)
                hub.result_storage.check_end_choice(rid, decision=decision)
            result = await hub.areview_done(rid, decision=decision, revision=body.get("revision"),
                                     target_sha256=body.get("target_sha256"))
            card = await hub.result_storage.open_end_card(rid)
            if choice is not None:
                if card is None:
                    raise ValueError("이 요청에는 끝 저장 위치 질문이 없습니다")
                await hub.resolve_approval(card["id"], True, storage=choice)
                result["result_storage"] = hub.requests[rid].get("result_storage")
            elif card is not None:
                result["storage_card"] = card["id"]
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/requests/{rid}/followup", dependencies=[Depends(auth)])
    async def followup(rid: str, body: FollowupIn) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            entry = (await hub.astart_followup(rid, body.text))
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

    @app.get("/api/requests/{rid}/task-attempts", dependencies=[Depends(auth)])
    async def task_attempts(rid: str, expected_created_at: float = Query(..., gt=0, allow_inf_nan=False),
                            limit: int = Query(1000, ge=1, le=1000)) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return hub.request_task_attempts(rid, expected_created_at, limit)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.get("/api/requests/{rid}", dependencies=[Depends(auth)])
    async def get_request(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        from ..recruit.paper_skills import literature_view

        # The fox's paper list (#660) is derived from this stored record only; the record itself is unchanged.
        return {**hub.requests[rid], "paper_literature": literature_view(hub.requests[rid])}

    @app.get("/api/requests/{rid}/audit-bundle", dependencies=[Depends(auth)])
    async def audit_bundle(rid: str) -> Response:
        if rid not in hub.requests:
            raise HTTPException(404)
        from ..evidence.audit import bundle_bytes, verify_request

        report = await asyncio.to_thread(verify_request, hub.requests[rid], settings)
        payload = await asyncio.to_thread(bundle_bytes, report, hub.requests[rid])
        return Response(payload, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="labhq-audit.zip"'})

    @app.get("/api/requests/{rid}/report-pdf", dependencies=[Depends(auth)])
    async def report_pdf(rid: str) -> Response:
        """The finished request's report with its figures as a PDF; the HTML and a reason when no browser can print (#584)."""
        if rid not in hub.requests:
            raise HTTPException(404)
        from urllib.parse import quote

        from ..report_pdf import ReportRefused, build_report_file, pdf_cap_bytes, size_notes

        tasks = {key: copy.deepcopy(task) for key, task in hub.store.all("task").items()
                 if isinstance(task, dict) and task.get("request_id") == rid}
        try:
            built = await asyncio.to_thread(build_report_file, copy.deepcopy(hub.requests[rid]), settings, tasks)
        except ReportRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        headers = {"Content-Disposition": f'attachment; filename="{built.filename}"', "X-Labhq-Report-Kind": built.kind}
        # A warning-only configured size is shown without changing the PDF (#690).
        shown = built.reason or " ".join(size_notes(built.data, pdf_cap_bytes(settings)))
        if shown:  # header values are latin-1: the page decodes this with decodeURIComponent
            headers["X-Labhq-Reason"] = quote(shown, safe="")
        return Response(built.data, media_type=built.media_type, headers=headers)

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
            await hub.resolve_approval(aid, body.approved, body.note, body.choice, body.storage,
                                       **({"model_refusal": body.model_refusal}
                                          if body.model_refusal is not None else {}))
        except KeyError:
            raise HTTPException(404, "no such pending approval")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"ok": True}

    @app.post("/api/approvals/{aid}/literature-pdf/{paper_index}", dependencies=[Depends(auth)])
    async def upload_literature_pdf(aid: str, paper_index: int, request: Request) -> dict:
        """Relay one PDF to the card's runner without persisting its body on the gateway."""
        entry = hub.approvals.get(aid)
        approval = (entry or {}).get("approval") or {}
        detail = approval.get("detail") or {}
        papers = detail.get("papers")
        if approval.get("kind") != "literature_pdf" or not isinstance(papers, list):
            raise HTTPException(404, "no such pending PDF request")
        if not 0 <= paper_index < len(papers):
            raise HTTPException(422, "paper index is outside this card")
        origin = (entry or {}).get("origin")
        if not origin or origin not in hub.runners:
            raise HTTPException(409, "the task runner is not connected")
        maximum = int(settings.literature.pdf_max_mb * 1024 * 1024)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            raise HTTPException(400, "invalid content length") from None
        if declared > maximum:
            raise HTTPException(413, "PDF is larger than the configured limit")
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > maximum:
                raise HTTPException(413, "PDF is larger than the configured limit")
        data = bytes(payload)
        if not data.startswith(b"%PDF-"):
            raise HTTPException(415, "only a PDF file is accepted")
        transfer_id = new_id("attach")
        waiter = asyncio.get_running_loop().create_future()
        hub.attachment_waiters[transfer_id] = (aid, paper_index, origin, waiter)
        try:
            try:
                await hub.send_runner(origin, {"type": "approval.attachment", "id": aid,
                                               "paper_index": paper_index,
                                               "transfer_id": transfer_id,
                                               "content_b64": base64.b64encode(data).decode("ascii")})
            except RunnerUnavailable as exc:
                raise HTTPException(409, "the task runner disconnected") from exc
            try:
                ack = await asyncio.wait_for(waiter, ATTACHMENT_ACK_TIMEOUT_S)
            except asyncio.TimeoutError as exc:
                raise HTTPException(504, "the task runner did not confirm the PDF write") from exc
        finally:
            hub.attachment_waiters.pop(transfer_id, None)
            if not waiter.done():
                waiter.cancel()
        if not ack["ok"]:
            raise HTTPException(422, ack["note"] or "the task runner could not store the PDF")
        return {"ok": True, "bytes": len(data)}

    @app.post("/api/requests/{rid}/cancel-active", dependencies=[Depends(auth)])
    async def cancel_active_request(rid: str, body: ActiveCancelIn) -> dict:
        # Distinct route deliberately yields 404 on old gateways; never fall back to /cancel.
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return {"ok": True, **await hub.cancel_request(
                rid, active_only=True, expected_created_at=body.expected_created_at)}
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/requests/{rid}/cancel", dependencies=[Depends(auth)])
    async def cancel_request(rid: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            return {"ok": True, **await hub.cancel_request(rid)}
        except ValueError as error:  # already done or failed
            raise HTTPException(409, str(error))

    @app.post("/api/tasks/{tid}/cancel", dependencies=[Depends(auth)])
    async def cancel(tid: str) -> dict:
        if not await hub.cancel_task(tid):
            raise HTTPException(404)
        return {"ok": True}

    async def _control(tid: str, action: str, text: str | None = None) -> dict:
        try:
            return {"ok": True, **await hub.control_task(tid, action, text)}
        except KeyError:
            raise HTTPException(404)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/tasks/{tid}/steer", dependencies=[Depends(auth)])
    async def steer(tid: str, body: SteerIn) -> dict:
        return await _control(tid, "steer", body.text)

    @app.post("/api/tasks/{tid}/pause", dependencies=[Depends(auth)])
    async def pause(tid: str) -> dict:
        return await _control(tid, "pause")

    @app.post("/api/tasks/{tid}/resume", dependencies=[Depends(auth)])
    async def resume(tid: str) -> dict:
        return await _control(tid, "resume")

    @app.post("/api/tasks/{tid}/halt", dependencies=[Depends(auth)])
    async def halt(tid: str) -> dict:
        return await _control(tid, "halt")

    @app.post("/api/tasks/{tid}/breaker/release", dependencies=[Depends(auth)])
    async def release_breaker(tid: str) -> dict:
        try:
            return {"ok": True, **await hub.release_breaker(tid)}
        except KeyError:
            raise HTTPException(404)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/requests/{rid}/steps/{step_id}/resume-quota", dependencies=[Depends(auth)])
    async def resume_quota(rid: str, step_id: str) -> dict:
        if rid not in hub.requests:
            raise HTTPException(404)
        try:
            released = await hub.force_quota_resume(rid, step_id)
        except KeyError:
            raise HTTPException(409, "step is not waiting for quota or login")
        return {"ok": True, "released": [{"request_id": request_id, "step_id": sid}
                                           for request_id, sid in released]}

    @app.post("/api/recruit", dependencies=[Depends(auth)])
    async def recruit(body: RecruitIn) -> dict:
        if not paper2agent.enabled(settings):
            raise HTTPException(403, "recruitment is disabled (recruit.enabled=false)")
        if not (body.paper or body.repo):
            raise HTTPException(400, "paper or repo required")
        rid = hub.agent_runner.get(settings.recruit.agent_id)
        if not rid:
            raise HTTPException(409, f"no runner hosts the recruiter agent {settings.recruit.agent_id!r}")
        # Counted before sending: a runner that fails at once may report recruit.failed before send_runner returns.
        # Not rolled back when sending fails mid-frame: it may still have reached the runner (as Hub.dispatch assumes);
        # recruit.done/failed or a new runner process clears it, and `down --force` overrides meanwhile (#515 review).
        entry = hub.recruits_running.setdefault(rid, {"incarnation": hub.runner_incarnations.get(rid),
                                                      "process": hub.runner_processes.get(rid), "count": 0})
        entry["count"] += 1
        try:
            await hub.send_runner(rid, {"type": "recruit.start", **body.model_dump()})
        except RunnerNotSent:
            entry["count"] = max(0, entry["count"] - 1)  # the frame never left: nothing runs (#521)
            raise HTTPException(409, f"채용 담당({settings.recruit.agent_id})의 러너({rid})가 꺼져 있거나 다시 연결하는 "
                                     "중이라 채용 요청을 보내지 못했습니다. labhq up(또는 labhq runner)으로 켠 뒤 "
                                     "다시 보내세요") from None
        return {"ok": True, "runner_id": rid}

    @app.post("/api/contracts/{agent_id}", dependencies=[Depends(auth)])
    async def contract(agent_id: str, body: ContractIn) -> dict:
        if body.action != "release" and not paper2agent.enabled(settings):
            raise HTTPException(403, "recruitment is disabled (recruit.enabled=false)")
        rid = hub.agent_runner.get(agent_id) or hub.agent_runner.get(settings.recruit.agent_id)
        if not rid:
            raise HTTPException(404)
        await hub.send_runner(rid, {"type": "contract.update", "agent_id": agent_id, **body.model_dump()})
        return {"ok": True}

    @app.get("/api/messenger", dependencies=[Depends(auth)])
    async def messenger(before: int | None = Query(default=None, ge=1, le=9223372036854775807),
                        limit: int = Query(default=40, ge=1, le=200),
                        request_id: str | None = None) -> dict:
        page = hub.store.messenger_history(before, limit, request_id)
        for event in page["events"]:
            hub.pi_interpreter.schedule(event)
        return page

    @app.get("/api/events", dependencies=[Depends(auth)])
    async def events(limit: int = 200, since: int | None = None) -> list[dict]:
        # Match WebSocket replay: no partial buffer while publication awaits a commit.
        async with hub.event_lock:
            if since is None:
                return list(hub.events)[-limit:]
            oldest, latest = hub.store.event_bounds()
            if since < oldest - 1 or since > latest:
                snap = hub.snapshot()
                snap["replay_gap"] = {"requested_since": since, "oldest_seq": oldest}
                return [snap]
            return hub.store.events_since(since, max(1, limit))

    @app.get("/api/shutdown-readiness", dependencies=[Depends(auth)])
    async def shutdown_readiness() -> dict:
        return hub.shutdown_readiness()

    @app.post("/api/shutdown", dependencies=[Depends(auth)], status_code=202)
    async def stop_office(body: ShutdownIn, background: BackgroundTasks) -> dict:
        result = shutdown.prepare(body)
        background.add_task(shutdown.run, body.force)
        return result

    @app.get("/api/shutdown", dependencies=[Depends(auth)])
    async def shutdown_status() -> dict:
        return {"status": shutdown.status}

    @app.get("/api/health")
    async def health() -> dict:
        payload = {"service": "labhq gateway", "runners": list(hub.runners), "runner_online": bool(hub.runners),
                   "session_started_at": hub.session_started_at,
                   "agents": len(hub.agents),
                   "active_requests": sum(is_active_request(r.get("status")) for r in hub.requests.values()),
                   "waiting_for_runner": sum(r.get("status") == "waiting_for_runner" for r in hub.requests.values()),
                   "running_tasks": len(hub.running_tasks())}
        # Which config this gateway runs from, as hashes: `labhq up` never adopts another config's gateway (#613).
        # No path and no token: this endpoint needs no login.
        identity = public_identity(settings)
        if identity:
            payload["identity"] = identity
        return payload

    @app.get("/", response_class=HTMLResponse)
    async def office() -> HTMLResponse:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        boot_data = {"mode": "live", **({"demo": True} if demo else {})}
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
        boot_data = {"mode": "live", **({"demo": True} if demo else {})}
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
