"""The runner lives where the compute and data live (your workstation or an HPC login node).

It dials the gateway (outbound WebSocket → no inbound ports, works behind NAT/firewalls), receives
task.dispatch, runs the agent's CLI in a per-task workspace, and streams normalized events back.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import copy
from dataclasses import replace
import inspect
import json
import logging
import os
import re
import stat
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import websockets
import yaml

from .. import disk_space, durable
from ..adapters import (adapter_preflight_error, get_adapter, is_read_only_task, read_only_profile,
                        read_only_refusal)
from ..adapters.base import RunContext
from ..adapters.held_dir import HeldDir
from ..adapters.owned import (OwnedPathError, is_link, owned_link_error, plain_directory, read_owned,
                              awrite_owned_bytes, awrite_owned_many, remove_entry, write_owned)
from ..ask_results import read_ask_results, rejected_step
from ..artifact_policy import unreported_warning_paths
from .. import output_checks
from ..facilities import fixes as facility_fixes
from ..login import LOGIN_PATH_ENV
from ..models import (ASK_MAX_WAIT_S, AgentSpec, ApprovalRequest, AskRequest, Engine, Event, McpServerSpec, Task,
                      TaskResult, ToolCallCount, waiting)
from .breaker import Breaker, BreakerPolicy, Escalation, stage_text, trip_text
from ..hooks.control import CONTROL_ENV, add_control_hooks
from .versions import engine_cli_versions
from ..intake import (expand_home_references, overlaps_restricted, overlaps_zone, reference_roots,
                      scan_reference_dir, withhold_reference_paths, zone_links)
from ..policy import (SHELL_TOOLS, claude_ask_internal_links, claude_deny_links, claude_deny_private,
                      claude_read_only, claude_rule_path, claude_settings, evaluate_tool, rule_tool)
from ..private_paths import ENABLED_ENV_VAR as PRIVATE_ENABLED_ENV, ENV_VAR as PRIVATE_PATHS_ENV, \
    OPEN_READS_ENV_VAR as PRIVATE_OPEN_READS_ENV, plugin_keep_dirs, resolve_private_paths, \
    staff_claude_project_dirs, scoped_open_reads
from ..pipeline_pr import collect_pipeline_submission, pipeline_rejection
from ..bioinfo_policy import PIPELINE_AUTHORS
from ..quota import quota_reset_instant
from ..recruit.provenance import external_service_refusal, not_yet_active, probation_refusal, restricted_task
from ..recruit import paper2agent
from ..registry import Registry
from ..settings import MODEL_NAME_PATTERN, Settings, write_staff_config
from ..staff_skills import install_required_skills
from ..store import StateStore
from ..tools.scheduler import (MISSING_POLLS_BEFORE_FINISHED, TERMINAL, Scheduler, command_env, job_in_family,
                               sanitize_job_name)
from ..util import INPUT_STEP_ID, output_relpath, short, tool_input_fingerprint
from .. import vocab as output_vocab
from ..vocab import declare as output_types
from .approvals import Broker
from .codex_sandbox import SandboxWatch
from .control import HALT_GRACE_S, ControlNotReady, Controls, steer_prompt
from . import local_jobs
from .hpc_jobs import submit_job
from .integrity import ReadOnlyWatch, watch_roots
from .lookup_admission import ENV as LOOKUP_ADMISSION_ENV, FILE as LOOKUP_ADMISSION_FILE, LookupAdmission
from .system_ca import CA_ENV, system_ca_pem
from .step_inputs import folder_files, linked_files, stage_inputs, stage_precedent_texts, staging_problem
from ..research.precedent_texts import step_note as precedent_step_note
from .workspace import (InputHashCache, TaskWorkspace, nearest_tool_use_id, portable_input_path, resolved_zones,
                        restricted_zones)

log = logging.getLogger("labhq.runner")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONSULT_REF_MAX_FILE_BYTES = 8 * 1024 * 1024
CONSULT_REFS_MAX_TOTAL_BYTES = 32 * 1024 * 1024
CONSULT_REF_COPY_CHUNK = 1024 * 1024


def _recorded_tool_name(value: object) -> str:
    """Return one bounded display name, normalizing Claude's MCP spelling to server.tool."""
    name = str(value or "unknown").replace("\r", " ").replace("\n", " ").replace("|", " ").strip()
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            name = f"mcp:{parts[1]}.{parts[2]}"
    return name[:160] or "unknown"


class ToolCallCounter:
    """Count normalized runner events without retaining any tool input or output."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.failures: dict[str, int] = {}
        self.by_id: dict[str, str] = {}

    def observe(self, typ: str, data: dict[str, Any]) -> None:
        if typ == "agent.tool":
            name = _recorded_tool_name(data.get("name"))
            self.calls[name] = self.calls.get(name, 0) + 1
            if data.get("id") is not None:
                self.by_id[str(data["id"])] = name
        elif typ == "agent.tool_error":
            tool_id = data.get("id")
            name = self.by_id.get(str(tool_id)) if tool_id is not None else None
            if name is None:
                name = "도구 미상 오류"
                self.calls[name] = self.calls.get(name, 0) + 1
                self.failures[name] = self.failures.get(name, 0) + 1
            elif name in self.calls:
                self.failures[name] = self.failures.get(name, 0) + 1

    def summary(self) -> dict[str, ToolCallCount]:
        return {name: ToolCallCount(calls=calls, failures=min(self.failures.get(name, 0), calls))
                for name, calls in sorted(self.calls.items())}


@contextlib.asynccontextmanager
async def _holding(semaphore: asyncio.Semaphore):
    """Release a slot `Runner._slot_or_halt` already took."""
    try:
        yield
    finally:
        semaphore.release()


def labhq_mcp_timeout_s(settings: Settings) -> int:
    """How long a staff CLI may wait on one labhq MCP call: the longest approval or ask wait, plus 120 s for the
    tool's own work. Claude gets it as each server's `timeout` and MCP_TOOL_TIMEOUT, Codex as `tool_timeout_sec`."""
    return max(settings.policy.approvals.timeout_s, ASK_MAX_WAIT_S) + 120


def literature_mcp_timeout_s(settings: Settings) -> int:
    """Only the literature MCP may remain blocked on the two-hour PI PDF card."""
    return max(labhq_mcp_timeout_s(settings), settings.literature.pdf_request_timeout_s + 120)


def checked_identity(identity: object) -> dict[str, str]:
    """The engine and model a task's `agent_identity` names (#272); anything else in it refuses the task."""
    if not isinstance(identity, dict) or set(identity) != {"engine", "model"}:
        keys = sorted(identity) if isinstance(identity, dict) else type(identity).__name__
        raise ValueError(f"agent_identity must name exactly engine and model, got {keys}")
    model = identity["model"]
    if not isinstance(model, str) or not re.fullmatch(MODEL_NAME_PATTERN, model):
        raise ValueError(f"agent_identity model {model!r} is not a valid model name")
    return {"engine": identity["engine"], "model": model}


def switched_engine_limits(agent: AgentSpec, engine: Engine) -> dict[str, str]:
    """What an `agent_identity` engine switch runs with (#272): read-only on the new engine.

    The roles it serves, the CSO and the science reviewer, never write. Each engine's own limit means nothing to the
    other (Claude's --tools list to Codex, Codex's sandbox to Claude), so carrying the spec over unchanged would hand
    a Claude CSO's Codex run shell writes, or a Codex reviewer's Claude run Bash and Edit.
    """
    if engine is Engine.codex:
        return {"sandbox": "read-only"}
    if engine is Engine.claude_code:
        return {"builtin_tools": ",".join(["Read", "Glob", "Grep", *(["WebSearch"] if "WebSearch" in agent.tools else [])])}
    raise ValueError(f"agent_identity cannot switch {agent.id} to engine {engine.value}")


def check_job_group(settings: Settings) -> None:
    if not settings.hpc.submit_prefix:
        return
    group = settings.hpc.job_group
    if not group:
        raise RuntimeError("hpc.job_group is required when hpc.submit_prefix is set")
    user = settings.hpc.user
    if not user:
        raise RuntimeError("hpc.user is required when hpc.submit_prefix is set")
    if os.name == "nt":
        return  # POSIX group lookup is unavailable here; restricted zones are refused below.
    import grp
    import pwd

    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError as e:
        raise RuntimeError(f"hpc.job_group does not exist: {group}") from e
    if gid not in {os.getgid(), *os.getgroups()}:
        raise RuntimeError(f"runner account is not a member of hpc.job_group: {group}")
    try:
        account = pwd.getpwnam(user)
    except KeyError as e:
        raise RuntimeError(f"hpc.user does not exist: {user}") from e
    if gid not in os.getgrouplist(user, account.pw_gid):
        raise RuntimeError(f"hpc.user is not a member of hpc.job_group: {user}, {group}")


def claude_rule_ready(path: str) -> bool:
    """Whether Claude can be given a permission rule for this path (#177). The rule syntax for a UNC path
    is unverified, so a UNC path gets none and a folder that needs one is not opened to Claude."""
    try:
        claude_rule_path(str(path))
    except ValueError:
        return False
    return True


def _can_list(path: str) -> bool:
    try:
        os.listdir(path)
        return True
    except OSError:
        return False


def check_data_boundary(settings: Settings) -> bool:
    """Refuse unsafe zones; return whether the explicit unsafe override was used."""
    zones = [z.path for z in settings.policy.data_zones if z.level == "restricted"]
    if not zones:
        return False
    if os.name == "nt":
        raise RuntimeError("restricted data zones have no enforced guard on Windows; run the runner on Linux")
    if any(p.startswith(("\\\\", "//")) for p in zones):
        raise RuntimeError("UNC restricted zones have no verified Claude deny rule; run with a Linux path")
    readable = [p for p in zones if os.path.exists(p) and
                (os.access(p, os.R_OK) or _can_list(p) or (os.path.isdir(p) and os.access(p, os.X_OK)))]
    if readable:
        if not settings.policy.allow_runner_read_restricted:
            raise RuntimeError("runner account can read restricted data; use a separate Linux runner account")
        log.critical("UNSAFE OVERRIDE: runner account can read restricted data (%d zone(s)); "
                     "policy.allow_runner_read_restricted is enabled", len(readable))
    return bool(readable)



# Record fields that only tell a rewritten output from an untouched one (#58 ②); never published.
IDENTITY_ONLY = ("ino", "ctime_ns")


def output_identity(row: dict[str, Any]) -> tuple:
    """What a run baseline compares: a file kept at its size and mtime but replaced (new inode) or rewritten in
    place (new POSIX ctime) still counts as changed by the run."""
    return (bool(row.get("link")), row.get("size"), row.get("mtime_ns"), row.get("ino"), row.get("ctime_ns"))


def _should_check_declared_outputs(result: TaskResult) -> bool:
    """Whether this successful turn finishes the step and can be held to its declared outputs."""
    if not result.ok or waiting(result):
        return False
    question = result.blocking_decision
    if not question and isinstance(result.structured, dict):
        question = result.structured.get("blocking_decision")
    return not (isinstance(question, str) and question.strip())


ENVIRONMENT_LOCK = "outputs/env/requirements.lock.txt"


async def _maybe_await(value: Any) -> None:
    """`await` for the result of `stage_run` (None) or `aupdate_run` (a coroutine) alike."""
    if inspect.isawaitable(value):
        await value


async def _settled_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """`asyncio.to_thread` that a cancel cannot leave running (#606).

    The loop is free while `fn` runs, but a thread cannot be interrupted: after a plain cancelled `to_thread` the
    step is reported cancelled while `fn` still changes the workspace, and a retry in the same folder races it. So the
    cancel waits for `fn` to finish, then goes on. Used for what writes in the workspace; a read may stay unawaited.
    """
    work = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.wait([work])
            except asyncio.CancelledError:
                pass  # a second cancel still waits for the worker
        if not work.cancelled():
            work.exception()  # retrieved: the cancel, not the worker's error, is what propagates
        raise


def environment_record(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the runner-observed environment lock without opening it a second time."""
    row = next((item for item in rows if item.get("path") == ENVIRONMENT_LOCK), None)
    if row is None:
        return {"status": "unavailable", "reason": f"{ENVIRONMENT_LOCK} 없음"}
    sha256 = row.get("sha256")
    if isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256):
        return {"status": "complete", "sha256": sha256, "size": row.get("size")}
    return {"status": "partial", "reason": str(row.get("reason") or "sha256를 기록하지 못함"),
            **({"size": row.get("size")} if row.get("size") is not None else {})}


# While `Runner.emit_all` runs, `emit` collects each event here instead of sending it, and emit_all sends them as one job.
_EMIT_UNIT: contextvars.ContextVar[list | None] = contextvars.ContextVar("labhq_emit_unit", default=None)


# Flip only when a protected alias hands the validated runner-local interpreter to consumers of every engine.
ENVIRONMENT_ALIAS_AVAILABLE = False
# Engines that receive a verified environment (#674): its interpreter path in `LABHQ_ENV_PYTHON` and a briefing line.
# Claude also gets the folder as a read-only root (`read_dirs`, Edit/Write denied); Codex's sandbox already reads outside
# the work folder and Gemini has no read-only grant to give, so they get the path only. Other engines stay refused.
ENVIRONMENT_ENGINES = frozenset({Engine.claude_code, Engine.codex, Engine.gemini})
ENVIRONMENT_GRANT_ENGINES = frozenset({Engine.claude_code})

ACK_DRAIN_S = 5.0  # how long a reconnect waits for the acks it already received to commit (#1125)
ACK_MERGE_S = 0.5  # at most one ack commit per this long: acks arriving between two commits share the second (#1125)


class Runner:
    # Defaults for a Runner built without __init__ (tests do): no batch open, no ack in flight.
    _batch_thread: threading.Thread | None = None
    _reload_after_batch = False
    _ack_high = 0
    _ack_done = 0
    _ack_task: asyncio.Task | None = None
    _ack_error: BaseException | None = None
    _ack_last = 0.0  # monotonic time the last ack commit finished
    _shutdown_deadline: float | None = None  # time.monotonic() when `shutdown()` must be done

    def __init__(self, settings: Settings):
        settings.runner.validate_wsl()
        self.s = settings
        # The loop and its thread, noted by `_send_durably`: a message committed on the durable writer thread asks
        # this loop to reload the outbox queue (#606).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._batch_thread: threading.Thread | None = None  # the writer thread while `_send_batch_now` holds its batch
        self._reload_after_batch = False
        # The gateway acks every outbox row. The acks are cumulative, so those that arrive while one is committing
        # share the next commit (#1125): `_ack_high` is the highest seq the gateway has stored, `_ack_done` the highest
        # one whose rows are deleted.
        self._ack_high = 0
        self._ack_done = 0
        self._ack_task: asyncio.Task | None = None
        self._ack_error: BaseException | None = None
        # Tasks whose task.result is already queued in this run. A cancel that arrives while the result commits does
        # not stop the commit, so the cancel handler must not queue a second, "cancelled" one over it (#606).
        self._result_queued: set[str] = set()
        # Actual nested conversion identity, registered by the reserved plan-hire path before dispatch. This is
        # private runner state, never accounting metadata supplied by a model or a task's caller.
        self._recruitment_conversions: dict[str, dict] = {}
        # #863: cleanup evidence an adapter reports when its run ends by cancellation (no TaskResult of its own).
        self._cleanup_reports: dict[str, dict] = {}
        self.store = StateStore(settings.path(settings.runner.state_dir) / f"runner-{settings.runner.id}.sqlite3")
        saved_incarnation = self.store.all("runner_meta").get("incarnation")
        self.incarnation = saved_incarnation["id"] if saved_incarnation else uuid.uuid4().hex
        if not saved_incarnation:
            self.store.put("runner_meta", "incarnation", {"id": self.incarnation})
        self.process = uuid.uuid4().hex  # never saved: tells the gateway a restart from a reconnect (#521)
        self.registry = Registry(settings.path(settings.runner.agents_dir), settings.path(settings.runner.talent_dir),
                                 settings.path(settings.runner.contract_dir) if settings.runner.contract_dir else None)
        self._contract_lock = asyncio.Lock()
        self.ws_root = settings.path(settings.runner.workspace_root)
        self.input_hash_cache = InputHashCache(
            settings.path(settings.runner.state_dir) / "input-sha256-cache.json")
        self.sem = asyncio.Semaphore(settings.runner.max_parallel)
        self.consult_sem = asyncio.Semaphore(settings.runner.consult_parallel)
        self.reference_write_warned: set[str] = set()
        self.private_skip_warned: set[str] = set()
        self.private_shell_warned: set[str] = set()
        self.project_link_warned: set[tuple] = set()
        self.outbox: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = {}
        self._dispatches: set[asyncio.Future] = set()  # Acceptance/result writes not yet represented by a task.
        self.workspaces: dict[str, TaskWorkspace] = {}
        # Events of a task whose reused workspace is still being checked: written to its events.jsonl, in order,
        # once the check passes (#193), and dropped with the workspace when it fails.
        self.event_buffers: dict[str, list[dict]] = {}
        # Folders each running task may write (read-only tasks: only labhq's own result files in their workspace),
        # and those of tasks that ended recently, as (ended_at, task_id, read_only, roots): a read-only check
        # leaves out what another task wrote while it ran.
        self.active_roots: dict[str, tuple[bool, list[Path]]] = {}
        self.ended_roots: list[tuple[float, str, bool, list[Path]]] = []
        self.task_req: dict[str, str | None] = {}
        self.approval_tasks: dict[str, tuple[str | None, str | None]] = {}
        self.tool_contexts: dict[str, Any] = {}
        self.submitting: dict[str, set[asyncio.Future]] = {}  # submit commands running, by task (#214 review)
        self.jobs: dict[str, dict] = self.store.all("job")
        self.notified: set[str] = set(self.store.all("notified"))
        self.task_req.update({j["task_id"]: j.get("request_id") for j in self.jobs.values() if j.get("task_id")})
        limit = settings.annot.request_lookup_limit
        self.broker = Broker(settings.runner.broker_port, self._on_approval, self._on_tool_event,
                             self._on_submit, self._on_ask, self._owns_job,
                             on_approval_timeout=self._on_approval_timeout, on_local_job=self._on_local_job,
                             on_restricted=self._on_restricted,
                             lookup_admission=None if limit is None else LookupAdmission(
                                 settings.path(settings.runner.state_dir) / LOOKUP_ADMISSION_FILE, limit))
        self.scheduler = Scheduler(settings.hpc)
        self._local_procs: dict = {}  # job id -> the child this runner started (reaped when polled), #675
        self._local_watch: asyncio.Task | None = None  # the local job poller, started with the first job
        self.connected = asyncio.Event()
        self._stopping = False
        self.started_at = time.time()
        self.engine_versions: dict[str, str] | None = None  # probed once, off the event loop, before connecting
        self.local_software: dict | None = None  # bounded doctor probes, once before the first hello
        self.codex_sandbox = SandboxWatch()  # elevated setup still fits this Codex (#328)
        # Steer, pause and halt files (#59 ①): runner state, outside every staff write root.
        self.breakers: dict[str, Escalation] = {}  # #59: running tasks' breaker stage
        self._disk_watchers: dict[str, asyncio.Task] = {}  # #678: one per running task
        self.controls = Controls(settings.path(settings.runner.state_dir) / "control")
        self._finish_interrupted_tasks()

    def _finish_interrupted_tasks(self) -> None:
        """A new process cannot finish work accepted by its predecessor."""
        pending_results = {e.get("task_id") for e in self.store.pending() if e.get("type") == "task.result"}
        for tid, entry in self.store.all("accepted_task").items():
            if entry.get("state") != "running":
                continue
            if tid not in pending_results:
                task = entry.get("task") or {}
                agent_id = task.get("agent_id") or entry.get("agent_id") or "unknown"
                request_id = task.get("request_id") or entry.get("request_id")
                self.send({"type": "task.result", "task_id": tid, "agent_id": agent_id,
                           "request_id": request_id,
                           "data": self._interrupted_result(tid, agent_id).model_dump(mode="json")})
            self.store.put("accepted_task", tid, {**entry, "state": "finished"})

    def _interrupted_result(self, tid: str, agent_id: str) -> TaskResult:
        jobs = [job for job in self.jobs.values() if job.get("task_id") == tid]
        if jobs:
            return TaskResult(task_id=tid, agent_id=agent_id, ok=True,
                              text="Runner restarted after HPC submission; inspect tracked jobs.",
                              pending_jobs=[job["job_id"] for job in jobs],
                              workdir=next((job.get("workdir") for job in jobs if job.get("workdir")), None),
                              session_id=next((job.get("session_id") for job in jobs if job.get("session_id")), None))
        # This runner never held that task's wrapper: a restart proves the old runner ended, not its staff tree (#863).
        return self._shutdown_result(TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                          error="runner restarted: accepted task interrupted",
                          cleanup_evidence={"status": "UNKNOWN", "reason": "runner_restart_only",
                                            "active_processes_measured": False}), cancelled=True, recovered=True)

    def _shutdown_result(self, result: TaskResult, *, cancelled: bool = False,
                         recovered: bool = False) -> TaskResult:
        """Attach the same evidence before live delivery, durable enqueue or restart recovery."""
        from ..interruption import shutdown_interruption, stopping_marker
        stopping = recovered or self._stopping or stopping_marker(self.s, self.started_at)
        if not result.ok and shutdown_interruption(exit_code=result.exit_code, error=result.error,
                                                   stopping=stopping, cancelled=cancelled):
            # Merged, never replaced (#863): evidence already on the result stays, and the task's cleanup evidence joins.
            result.shutdown_evidence = {**result.shutdown_evidence, "exit_code": result.exit_code,
                "error": result.error, "stopping": True, "cancelled": cancelled,
                "source": "runner-recovery" if recovered else "runner", "at": time.time(),
                **({"cleanup": result.cleanup_evidence} if result.cleanup_evidence else {})}
        return result

    # ---------------- lifecycle ----------------
    def _check_job_group(self) -> None:
        check_job_group(self.s)

    def _check_data_boundary(self) -> None:
        check_data_boundary(self.s)

    _can_list = staticmethod(_can_list)

    async def run_forever(self) -> None:
        self._check_job_group()
        self._check_data_boundary()
        if self.s.hpc.submit_prefix and os.name != "nt":
            os.umask(0o077)  # task inputs created by this runner and its agents stay private
        self.registry.load()
        log.info("runner %s: %d agents", self.s.runner.id, len(self.registry.agents))
        try:
            loops = [self.broker.serve(), self._connection_loop()]
            if self.s.hpc.scheduler != "none":
                loops.append(self._job_watch_loop())
            else:
                log.warning("HPC job polling disabled (scheduler=none); saved job records are retained, "
                            "not refreshed")
            if any(local_jobs.is_local(jid) for jid in self.jobs):  # jobs saved by the previous runner process (#675)
                self._ensure_local_watch()
            await asyncio.gather(*loops)
        finally:
            # Acks already received are not left in memory. After a signal the wait is what is left of the shutdown's
            # own deadline, so the whole exit stays inside it.
            await self._ack_drain(raise_error=False, timeout=self._ack_wait_s(durable.EXIT_DRAIN_S))
            # Writes queued by the tasks that were just cancelled still finish (#606).
            await durable.adrain(durable.EXIT_DRAIN_S)

    def stop(self) -> None:
        self._stopping = True
        watch = getattr(self, "_local_watch", None)  # a Runner built without __init__ (tests) has none
        if watch is not None:
            watch.cancel()
        self.broker.stop()
        for t in self.tasks.values():
            t.cancel()

    async def shutdown(self, timeout_s: float = 5) -> None:
        """Cancel tasks with shutdown evidence before closing the connection; retain any unacked results."""
        self.stop()

        async def finish():
            while True:
                while self._dispatches:
                    # A connection handler outlives one dispatch. Wait only for that dispatch's durable result,
                    # without cancelling its completion future when this bounded shutdown times out.
                    await asyncio.gather(*(asyncio.shield(done) for done in tuple(self._dispatches)))
                await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
                if self._dispatches:  # Another dispatch may have entered while cancelled tasks were draining.
                    continue
                if not self.connected.is_set() or not any(e.get("type") == "task.result" for e in self.store.pending()):
                    return
                await asyncio.sleep(.05)

        deadline = self._shutdown_deadline = time.monotonic() + timeout_s
        try:
            await asyncio.wait_for(finish(), timeout_s)
        except asyncio.TimeoutError:
            pass  # Durable outbox replay remains the fallback when the gateway cannot acknowledge in time.
        # Every ack received so far is committed before this returns, inside the same deadline: an ack that does not
        # finish in time leaves its rows in the outbox for replay.
        await self._ack_drain(raise_error=False, timeout=max(0.0, deadline - time.monotonic()))

    def _ack_nowait(self, seq: int) -> None:
        """The gateway has stored every outbox row up to `seq`: start deleting them. Nothing waits: the first ack
        commits at once, and the acks that arrive while it commits are merged into the next commit (cumulative, so
        only the highest matters). A commit that failed is raised here, on the next ack, as it was when the ack was
        committed inline."""
        if self._ack_error is not None:
            error, self._ack_error = self._ack_error, None
            raise error
        self._ack_high = max(self._ack_high, seq)
        if self._ack_task is None or self._ack_task.done():
            self._ack_task = asyncio.get_running_loop().create_task(self._ack_flush())

    async def _ack_flush(self) -> None:
        """Commit the highest ack. An idle runner commits at once; after a commit the next one waits until
        `ACK_MERGE_S` have passed (the acks that arrive meanwhile are cumulative and share it), unless a caller is
        waiting in `_ack_drain`. A late delete only means a few rows are resent after a crash and deduped by the
        gateway's cursor."""
        hurry = self.__dict__.setdefault("_ack_hurry", asyncio.Event())
        try:
            while self._ack_done < self._ack_high:
                wait = self._ack_last + ACK_MERGE_S - time.monotonic()
                if wait > 0 and not hurry.is_set():
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(hurry.wait(), wait)
                high = self._ack_high
                await self.store.aack(high)
                self._ack_done = max(self._ack_done, high)
                self._ack_last = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - raised to the next caller, where the inline commit raised it
            self._ack_error = exc
        finally:
            hurry.clear()

    def _ack_wait_s(self, normal: float) -> float:
        """How long a closing-down path may wait for the acks: `normal`, or what is left of an open shutdown deadline."""
        if self._shutdown_deadline is None:
            return normal
        return max(0.0, min(normal, self._shutdown_deadline - time.monotonic()))

    async def _ack_drain(self, *, raise_error: bool = True, timeout: float | None = None) -> None:
        """Every ack noted so far is committed. Errors are raised unless the caller is closing down anyway. With a
        `timeout` an unfinished commit is left to finish on its own: its rows stay in the durable outbox, are resent,
        and the gateway dedups them."""
        task = self._ack_task
        if task is not None and not task.done():
            self.__dict__.setdefault("_ack_hurry", asyncio.Event()).set()  # someone waits: do not hold the commit back
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except asyncio.TimeoutError:
                log.warning("runner %s: an ack commit is still running; its rows stay in the outbox", self.s.runner.id)
                return
        if raise_error and self._ack_error is not None:
            error, self._ack_error = self._ack_error, None
            raise error

    def send(self, obj: dict) -> None:
        if obj.get("type") not in {"runner.roster"}:
            self.store.enqueue(obj, self.s.runner.outbox_limit)
            self._reload_outbox_after_commit()
        else:
            self.outbox.put_nowait(json.dumps(obj, ensure_ascii=False, default=str))

    def _reload_outbox_after_commit(self) -> None:
        # On the loop's own thread this is the old straight call. On the durable writer thread (`_send_durably`) the
        # reload is queued for the loop, and runs before the awaiting caller resumes.
        if self._batch_thread is threading.current_thread():
            self._reload_after_batch = True  # its rows are not committed yet: once, after the batch's commit
            return
        loop = self._loop
        if loop is not None and threading.current_thread() is not self._loop_thread:
            loop.call_soon_threadsafe(self.reload_outbox)
        else:
            self.reload_outbox()

    async def _send_durably(self, obj: dict, *, before: tuple | None = None, after: tuple | None = None) -> None:
        """`send` for a coroutine (#606): `before`, the message and `after` are one job on the durable writer thread,
        in that order, so the loop is not held for their fsyncs and a cancelled caller cannot leave one of them
        undone. `before` and `after` are `(kind, key, body)` rows for the state table. The message still goes through
        `self.send`, the one place every outgoing message passes."""
        self._loop, self._loop_thread = asyncio.get_running_loop(), threading.current_thread()
        if obj.get("type") in {"runner.roster"} and before is None and after is None:
            self.send(obj)  # not stored: nothing to wait for
            return
        await self._send_batch([(obj, [before] if before else [], [after] if after else [])])

    async def _send_batch(self, items: list[tuple[dict, list[tuple], list[tuple]]]) -> None:
        """Several messages, each with its rows before and after, as ONE job: all of it is committed, in this order,
        whatever happens to the caller while it waits."""
        self._loop, self._loop_thread = asyncio.get_running_loop(), threading.current_thread()
        await durable.run(self.store.lane, self._send_batch_now, items)

    def _send_batch_now(self, items: list[tuple[dict, list[tuple], list[tuple]]]) -> None:
        """The rows and messages of `items`, in order, in ONE transaction: one commit and fsync for the batch instead
        of one per row and message (#1125). The outbox is reloaded once after that commit, because a reader sees only
        committed rows."""
        def write() -> None:
            # Runs on the store's lane thread, whichever thread asked: `send` defers its reload while this is set.
            self._batch_thread, self._reload_after_batch = threading.current_thread(), False
            try:
                for obj, befores, afters in items:
                    for row in befores:
                        self.store.put(*row)
                    self.send(obj)
                    for row in afters:
                        self.store.put(*row)
            finally:
                self._batch_thread = None

        self._reload_after_batch = False
        self.store.run_batch(write)  # from the lane's own job this is a plain inline call
        if self._reload_after_batch:
            self._reload_outbox_after_commit()

    async def asend(self, obj: dict) -> None:
        await self._send_durably(obj)

    def reload_outbox(self, preserve_roster: bool = True) -> None:
        roster = []
        while not self.outbox.empty():
            raw = self.outbox.get_nowait()
            if preserve_roster and json.loads(raw).get("type") == "runner.roster":
                roster.append(raw)
        for raw in roster:
            self.outbox.put_nowait(raw)
        for item in self.store.pending():
            self.outbox.put_nowait(json.dumps(item, ensure_ascii=False, default=str))

    async def emit(self, ev: Event) -> None:
        prepared = self._prepare_event(ev)
        if prepared is None:
            return
        unit = _EMIT_UNIT.get()
        if unit is not None:  # inside emit_all: sent with the others as one job
            unit.append(prepared)
            return
        await self._send_events([prepared])

    def _prepare_event(self, ev: Event) -> tuple[dict, list[tuple], list[tuple]] | None:
        """What `emit` does before the send: the task's own log, its buffer, and for a task.result the mark that the
        task finished and the note that its result is queued. None for a roster, which is not stored."""
        d = ev.model_dump(mode="json")
        ws = self.workspaces.get(ev.task_id or "")
        if ws:
            ws.append_event(d)
        elif ev.task_id in self.event_buffers:
            self.event_buffers[ev.task_id].append(d)
        if d.get("type") == "runner.roster":
            self.send(d)
            return None
        marks: list[tuple] = []
        if ev.type == "task.result" and ev.task_id:
            marks.append(("accepted_task", ev.task_id, {"state": "finished"}))
            self._result_queued.add(ev.task_id)
        return d, [], marks

    async def _send_events(self, items: list[tuple[dict, list[tuple], list[tuple]]]) -> None:
        try:
            await self._send_batch(items)
        except Exception:
            # Nothing was queued after all. (A cancel is not this: the job runs on and its result stays queued.)
            self._result_queued.difference_update(
                d.get("task_id") for d, _before, _after in items if d.get("type") == "task.result")
            raise

    async def emit_all(self, events: list[Event], *, before: list[tuple] | None = None,
                       after: list[tuple] | None = None) -> None:
        """Events that belong together (a closing status and the task's result; an approval and the status it
        causes) as one uncancellable unit: one durable job that commits all of them, in order, with the rows in
        `before` ahead of the first and the rows in `after` behind the last. These used to run without a pause, so a
        cancel could not land between them; it still cannot (#606). Each event still passes through `self.emit`."""
        unit: list[tuple[dict, list[tuple], list[tuple]]] = []
        token = _EMIT_UNIT.set(unit)
        try:
            for ev in events:
                await self.emit(ev)
        finally:
            _EMIT_UNIT.reset(token)
        if not unit:
            return
        # Copied now: the writer thread serializes them later, and the caller goes on changing its own objects.
        if before:
            unit[0] = (unit[0][0], [copy.deepcopy(row) for row in before], unit[0][2])
        if after:
            unit[-1] = (unit[-1][0], unit[-1][1], [*unit[-1][2], *[copy.deepcopy(row) for row in after]])
        await self._send_events(unit)

    async def _connection_loop(self) -> None:
        url = f"{self.s.gateway.url.rstrip('/')}/ws/runner?token={self.s.gateway.runner_token}"
        backoff = 1.0
        if self.engine_versions is None:
            engines = {a["engine"] for a in self.roster()}  # effective engines (force_engine applies)
            self.engine_versions = await asyncio.to_thread(engine_cli_versions, self.s, engines)
        if self.local_software is None:
            from ..doctor import staff_python_summary
            try:
                self.local_software = await asyncio.to_thread(staff_python_summary)
                if self.s.runner.environment == "wsl":
                    from ..doctor import wsl_tool_status

                    self.local_software["tools"].update(await asyncio.to_thread(wsl_tool_status))
            except Exception:  # noqa: BLE001 - a host probe must not keep the runner offline
                log.warning("runner %s could not summarize local software", self.s.runner.id, exc_info=True)
                self.local_software = {}
        while not self._stopping:
            try:
                await asyncio.to_thread(self._refresh_wsl_readiness)
                async with websockets.connect(url, max_size=64 * 2**20, ping_interval=20, ping_timeout=60) as ws:
                    backoff = 1.0
                    await ws.send(json.dumps(self.hello()))
                    self.reload_outbox(preserve_roster=False)
                    self.connected.set()
                    sender = asyncio.create_task(self._sender(ws))
                    try:
                        async for raw in ws:
                            msg = json.loads(raw)
                            if msg.get("type") == "runner.ack":
                                # Not waited for: the next messages are read while it commits, and the acks among them
                                # share the next commit instead of one fsync each (#1125).
                                self._ack_nowait(int(msg["runner_seq"]))
                                continue
                            await self._on_message(msg)
                    finally:
                        sender.cancel()
                        self.connected.clear()
                        # A reconnect reloads the outbox from the store: commit the acks first, but a stuck commit must
                        # not hold the reconnect (its rows are resent and deduped).
                        await self._ack_drain(raise_error=False, timeout=self._ack_wait_s(ACK_DRAIN_S))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._stopping:
                    log.warning("gateway connection lost/failed: %s (retry in %.0fs)", e, backoff)
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _sender(self, ws) -> None:
        while True:
            msg = await self.outbox.get()
            try:
                await ws.send(msg)
            except Exception:
                self.outbox.put_nowait(msg)  # resend after reconnect
                raise

    def _send_roster(self) -> None:
        self.send({"type": "runner.roster", "runner_id": self.s.runner.id,
                   "agents": self.roster(), "capabilities": self.capabilities()})

    def roster(self) -> list[dict]:
        agents = []
        for registered in self.registry.agents.values():
            agent = registered
            if self.s.runner.force_engine:
                agent = agent.model_copy(update={"engine": Engine(self.s.runner.force_engine)})
            try:
                refused = adapter_preflight_error(self.s, agent, self.ws_root)
            except OSError:
                refused = "adapter preflight files inaccessible"
            if refused:
                log.warning("runner %s omits unavailable staff %s: %s", self.s.runner.id, agent.id, refused)
                continue
            summary = agent.summary()
            agents.append({**summary, "hpc_tools": ("hpc" in summary.get("builtin_mcp", []) and
                                                      self.s.hpc.scheduler != "none") or
                                                     "labhq_hpc" in summary.get("mcp", []),
                           "wiki_tools": "wiki" in summary.get("builtin_mcp", []) and self._wiki_tool_ready()})
        return agents

    def _wiki_tool_ready(self) -> bool:
        """The LLM-wiki tool hands note text to a staff model, so it needs the PI's explicit opt-in (#827)."""
        wiki = self.s.wiki
        return bool(wiki.enabled and wiki.root and wiki.external_model_allowed)

    def _refresh_wsl_readiness(self) -> None:
        if self.s.runner.environment != "wsl":
            return
        from .. import wsl_readiness

        self.wsl_readiness = {}
        agent = self.registry.agents.get("bioinfo-agent")
        if agent is None:
            return
        adapter = get_adapter(Engine.claude_code, self.s)
        ctx = RunContext(task=Task(agent_id=agent.id, prompt=""), agent=agent, workdir=self.ws_root,
                         settings=self.s, mcp_servers=[], env={}, emit=lambda *_: None, prompt="")
        env = adapter.staff_env(ctx)
        try:
            directories = [str(self.ws_root / p) if not Path(p).is_absolute() else p
                           for p in adapter._plugin_dirs(ctx, env)]
            self.wsl_readiness = wsl_readiness.probe(directories, env, self.s.engines.claude_code.bin)
        except (OSError, ValueError):
            pass  # no positive facts: gateway keeps the native worker

    def capabilities(self) -> dict:
        external_hpc = any("labhq_hpc" in a.get("mcp", []) for a in self.registry.roster())
        capabilities = {"scheduler": self.s.hpc.scheduler,
                        "runtime_env": ("wsl" if self.s.runner.environment == "wsl" else
                                        "windows" if sys.platform == "win32" else "linux"),
                        "workspace_root": str(self.s.path(self.s.runner.workspace_root)),
                        "compute_backends": ["local CLI"] +
                                            ([self.s.hpc.scheduler] if self.s.hpc.scheduler != "none" else []) +
                                            (["external labhq_hpc MCP"] if external_hpc else []),
                        "hpc_tools": self.s.hpc.scheduler != "none" or external_hpc,
                        "engine_cli_versions": dict(self.engine_versions or {}),
                        "platform": sys.platform,
                        "environment": {key: os.environ[key] for key in LOGIN_PATH_ENV if os.environ.get(key)}}
        local_software = getattr(self, "local_software", None)
        if local_software is not None:
            capabilities["local_software"] = local_software
        if self.s.runner.environment == "wsl":
            capabilities["wsl_readiness"] = dict(getattr(self, "wsl_readiness", {}))
        return capabilities

    def hello(self) -> dict:
        return {"type": "runner.hello", "runner_id": self.s.runner.id,
                "incarnation": self.incarnation, "process": self.process, "agents": self.roster(),
                "capabilities": self.capabilities()}

    # ---------------- gateway → runner ----------------
    async def _on_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "task.dispatch":
            done = asyncio.get_running_loop().create_future()
            self._dispatches.add(done)  # Before the first await, including acceptance already on the writer lane.
            try:
                task = Task.model_validate(msg["task"])
                prior = self.store.get("accepted_task", task.id)
                if prior is None and self.store.get("cancelled_task", task.id) is not None:
                    # Its cancel arrived first (reconnect flush overtook this dispatch): never start it, answer it.
                    result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="cancelled",
                                        error_kind="cancelled", cost_usd=0.0, cost_known=True,
                                        cleanup_evidence={
                                            "status": "not_started", "reason": "cancelled_before_dispatch",
                                            "active_processes_measured": False})
                    await self._send_batch([
                        ({"type": "task.accepted", "task_id": task.id, "request_id": task.request_id},
                         [("accepted_task", task.id, {"state": "finished"})], []),
                        ({"type": "task.result", "task_id": task.id, "agent_id": task.agent_id,
                          "request_id": task.request_id, "data": result.model_dump(mode="json")}, [], [])])
                    await self.store.adelete("cancelled_task", task.id)
                    return
                await self._send_durably(
                    {"type": "task.accepted", "task_id": task.id, "request_id": task.request_id},
                    before=(("accepted_task", task.id, {"state": "running", "task": task.model_dump(mode="json")})
                            if prior is None else None))
                # Acceptance is durable (and may yield): check shutdown again before starting any staff work.
                if prior is None and not self._stopping:
                    self.tasks[task.id] = asyncio.create_task(self._run_guarded(task))
                elif (prior is None or prior.get("state") == "running") and task.id not in self.tasks:
                    has_result = any(e.get("type") == "task.result" and e.get("task_id") == task.id
                                     for e in self.store.pending())
                    if not has_result:
                        result = (self._shutdown_result(TaskResult(task_id=task.id, agent_id=task.agent_id,
                                      ok=False, error="runner stopping: task not started"), cancelled=True)
                                  if self._stopping else self._interrupted_result(task.id, task.agent_id))
                        await self._send_durably(
                            {"type": "task.result", "task_id": task.id, "agent_id": task.agent_id,
                             "request_id": task.request_id,
                             "data": result.model_dump(mode="json")},
                            after=("accepted_task", task.id, {"state": "finished"}))
            finally:
                self._dispatches.discard(done)
                done.set_result(None)
        elif typ == "task.cancel":
            cancel_id = str(msg.get("task_id") or "")
            t = self.tasks.get(cancel_id)
            if t:
                t.cancel()
            elif cancel_id and self.store.get("accepted_task", cancel_id) is None:
                await self._remember_cancel(cancel_id)  # not held, not seen: its dispatch may still arrive
            await self._cancel_local_jobs(cancel_id)
        elif typ == "task.jobs_cancel":  # the step is parked on its jobs and its request was cancelled (#675)
            tid = str(msg.get("task_id") or "")
            if await self._cancel_local_jobs(tid):
                self.send({"type": "task.jobs_cancelled", "task_id": tid})  # durable: the gateway stops resending on it
        elif typ == "task.control":
            await self._on_control(msg)
        elif typ == "approval.attachment":
            ok, note = await self._store_approval_attachment(msg)
            await self.asend({"type": "approval.attachment.ack", "id": msg.get("id"),
                              "paper_index": msg.get("paper_index"), "transfer_id": msg.get("transfer_id"),
                              "ok": ok, "note": note})
        elif typ == "approval.resolved":
            if self.broker.resolve(msg["id"], bool(msg.get("approved")), msg.get("note", ""),
                                   **({'choice': msg['choice']} if msg.get('choice') else {})):
                task_id, agent_id = self.approval_tasks.pop(msg["id"], (None, None))
                run = self.tasks.get(task_id or "")
                if task_id and not (run and run.done()):  # an answer after the task ended must not revive it
                    await self.emit(Event(type="agent.status", task_id=task_id, agent_id=agent_id,
                                          request_id=self.task_req.get(task_id), data={"state": "working"}))
            await self.asend({"type": "approval.ack", "id": msg["id"]})
        elif typ == "restricted.resolved":
            self.broker.resolve_restricted(str(msg.get("id") or ""), msg)
        elif typ == "ask.resolved":
            self.broker.resolve_ask(msg["id"], msg.get("answer") or {})
            await self.asend({"type": "ask.ack", "id": msg["id"]})
        elif typ == "runner.ack":
            self._ack_nowait(int(msg["runner_seq"]))
            await self._ack_drain()  # a caller that waits for the ack gets it durable, as before
        elif typ == "registry.reload":
            self.registry.load()
            await asyncio.to_thread(self._refresh_wsl_readiness)
            self._send_roster()
        elif typ == "recruit.start":
            if paper2agent.enabled(self.s):
                asyncio.create_task(self._recruit(msg))
            else:
                await self._recruit(msg)  # send the refusal without creating a background conversion
        elif typ == "contract.update":
            await self._acontract_update(msg)

    async def _store_approval_attachment(self, msg: dict) -> tuple[bool, str]:
        """Write only the PDF filename already fixed by this pending card, inside its live task workspace."""
        approval_id = str(msg.get("id") or "")
        req = self.broker.pending_requests.get(approval_id)
        task_id, _agent_id = self.approval_tasks.get(approval_id, (None, None))
        workspace = self.workspaces.get(task_id or "")
        papers = req.detail.get("papers") if req and isinstance(req.detail, dict) else None
        index = msg.get("paper_index")
        if req is None or req.kind != "literature_pdf" or workspace is None or not isinstance(papers, list):
            return False, "PDF 요청 카드의 실행 작업을 찾지 못했습니다"
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(papers):
            return False, "논문 번호가 카드와 맞지 않습니다"
        relative = str((papers[index] or {}).get("pdf_file") or "") if isinstance(papers[index], dict) else ""
        path = Path(relative)
        if (path.is_absolute() or path.suffix.lower() != ".pdf" or
                path.parts[:3] != (".tmp", "literature", "inbox") or len(path.parts) != 4):
            return False, "PDF 저장 경로가 요청 안 inbox가 아닙니다"
        try:
            data = base64.b64decode(str(msg.get("content_b64") or ""), validate=True)
        except (ValueError, TypeError):
            return False, "PDF 전송값을 읽지 못했습니다"
        maximum = int(self.s.literature.pdf_max_mb * 1024 * 1024)
        if not data.startswith(b"%PDF-") or not 5 <= len(data) <= maximum:
            return False, "PDF 형식 또는 크기가 허용 범위와 맞지 않습니다"
        try:
            await awrite_owned_bytes(workspace.dir, relative, data)
        except OwnedPathError:
            return False, "PDF를 요청 안 inbox에 안전하게 쓰지 못했습니다"
        return True, "PDF를 요청 안 inbox에 저장했습니다"

    async def _breaker_stage(self, task_id: str, agent_id: str, request_id: str | None, breaker: Breaker, stage: str,
                             hooked: bool) -> None:
        """Enforce one stage of the runaway breaker and say what really happened (#59). Never fails the run."""
        hard_stop = self.s.runner.breaker_hard_stop
        # What really happened, per stage: a warn only tells the PI and a quiet warn that cleared is a release; only
        # constrain and stop enforce anything, and they overwrite this below (#1030).
        how = {"warn": "notice", "normal": "released"}.get(stage, "applied")
        halted = None
        try:
            if stage == "constrain":
                if hooked:
                    halted = await self.controls.apply({"task_id": task_id, "action": "constrain"})
                else:
                    how = "unsupported"  # no hook on this engine: nothing was restricted, so never report it as done
            elif stage == "stop":
                if hard_stop:
                    halted = await self.controls.apply({"task_id": task_id, "action": "halt"})
                    how = "halt" if halted else "failed"
                else:
                    how = "off"
        except Exception:
            log.warning("task %s breaker stage %s not applied", task_id, stage, exc_info=True)
            how = "failed"
        ladder = self.breakers.get(task_id)
        record = breaker.stage_record(stage, how, hard_stop=hard_stop, episode=ladder.episode_id if ladder else None)
        base = dict(task_id=task_id, agent_id=agent_id, request_id=request_id)
        await self.emit(Event(type="agent.breaker", data=record, **base))
        await self.emit(Event(type="agent.log", data={"level": "alert", "text": stage_text(record)}, **base))
        if halted:  # the staff sheet reads the restriction and a breaker halt from agent.control, like the PI's own
            await self.emit(Event(type="agent.control", data={**halted, "source": "breaker"}, **base))

    async def _announce_breaker_release(self, task_id: str, command_id: str = "", *, changed: bool = True,
                                        enforcement: str = "released") -> None:
        """Say the PI lifted the restriction (the ladder was already reset by ``_on_control``). With a ``command_id``
        this is also the acknowledgement the gateway waits for: ``released`` (applied now or by an earlier send of the
        same command) or ``stale`` (the command named an earlier restriction and was not applied)."""
        ladder = self.breakers.get(task_id)
        record = {"stage": ladder.policy.stage if ladder else "normal", "enforcement": enforcement, "mode": "enforce",
                  "signal": None, **({"command_id": command_id} if command_id else {}),
                  **({"episode": ladder.episode_id} if ladder else {})}
        base = dict(task_id=task_id, agent_id=self.controls.agent_of(task_id), request_id=self.task_req.get(task_id))
        await self.emit(Event(type="agent.breaker", data=record, **base))
        if changed and enforcement == "released":
            await self.emit(Event(type="agent.log", data={"level": "info", "text": stage_text(record)}, **base))

    CONTROL_RETRY_S = (0.2, 1.0)  # waits before the second and third attempt; then the gateway's resend takes over

    async def _on_control(self, msg: dict) -> None:
        """Steer, pause, resume or halt a task this runner holds (#59 ①). A control for a task it does not hold, or one
        it could not apply after a few bounded tries, is answered with ``control.nack``: the gateway keeps it on the step
        for the next dispatch, so nothing is acknowledged that was not applied (PR #532 review)."""
        tid = str(msg.get("task_id") or "")
        base = dict(task_id=tid, agent_id=self.controls.agent_of(tid) or msg.get("agent_id"),
                    request_id=self.task_req.get(tid) or msg.get("request_id"))
        error: Exception | None = None
        # Reset the ladder before the hook is released: a tool event arriving meanwhile must not climb from constrain to
        # stop and halt the task the PI is freeing (PR #995 review).
        ladder = self.breakers.get(tid) if msg.get("action") == "release_constraint" else None
        command = str(msg.get("command_id") or "")
        if ladder is not None and command:
            if command == ladder.last_command:  # a resend of a command already applied: nothing changes, only re-ack
                await self._announce_breaker_release(tid, command, changed=False)
                return
            episode = msg.get("episode")
            if isinstance(episode, str) and episode != ladder.episode_id:  # meant for an earlier restriction or run
                await self._announce_breaker_release(tid, command, changed=False, enforcement="stale")
                return
        released = bool(ladder and ladder.release())
        for wait in (0.0, *self.CONTROL_RETRY_S):
            if wait:
                await asyncio.sleep(wait)
            running = self.tasks.get(tid)
            try:
                summary = await self.controls.apply(msg, starting=running is not None and not running.done())
                error = None
                break
            except (OSError, TimeoutError, ControlNotReady) as exc:
                error = exc
        if error is not None or summary is None:
            reason = "unknown task" if error is None else f"not applied: {type(error).__name__}"
            if error is not None:
                log.warning("task %s control %s not applied: %s", tid, msg.get("action"), error)
            await self.asend({"type": "control.nack", "task_id": tid, "action": msg.get("action"),
                              "steer_id": ((msg.get("steer") or {}).get("id")
                                           if isinstance(msg.get("steer"), dict) else None),
                              "reason": reason})
            return
        base["agent_id"] = self.controls.agent_of(tid) or base["agent_id"]
        await self.emit(Event(type="agent.control", data=summary, **base))
        if ladder is not None and command:
            ladder.last_command = command
            await self._announce_breaker_release(tid, command, changed=released)
        elif released:
            await self._announce_breaker_release(tid)
        note = self._control_note(summary)
        if note:
            await self.emit(Event(type="agent.log", data={"level": "info", "text": note}, **base))

    @staticmethod
    def _control_note(summary: dict) -> str | None:
        """What the request does on this engine, said once in the staff log."""
        action, hooked = summary.get("action"), summary.get("mode") == "hook"
        if not summary.get("applied"):
            return None
        if action == "steer":
            if not summary.get("started"):
                return "방향 수정을 받았어요. 시작할 때 프롬프트 앞에 붙입니다"
            return ("방향 수정을 받았어요. 다음 도구 호출이 끝나면 전달합니다" if hooked else
                    "방향 수정을 받았어요. 이 엔진은 실행 중에 전달할 수 없어 이 turn이 끝나면 같은 단계의 다음 turn에 붙입니다")
        if action == "pause":
            if not summary.get("started"):
                return "일시 정지를 받았어요. 재개할 때까지 이 turn을 시작하지 않습니다"
            return ("일시 정지했어요. 다음 도구 호출부터 재개할 때까지 막습니다" if hooked else
                    "일시 정지를 받았어요. 이 엔진은 실행 중에 멈출 수 없어서 이번 turn은 계속하고 같은 단계의 다음 turn을 보내지 않습니다")
        if action == "resume":
            return "재개했어요"
        if action == "halt":  # "stopped" is said by whoever actually stops it (adapter, queued or held turn)
            return ("멈춤을 받았어요. 다음 도구 호출에서 세션을 보존한 채 멈춥니다" if hooked else
                    "멈춤을 받았어요. 멈추면 따로 알립니다")
        return None

    async def _acontract_update(self, msg: dict) -> None:
        # Own the payload before yielding. Serialize snapshots through publication, including when the receiver
        # is cancelled repeatedly: the next update must see the committed version, never the pre-write object.
        msg = copy.deepcopy(msg)
        if msg.get("action") != "release" and not paper2agent.enabled(self.s):
            log.warning("contract update refused: recruit.enabled=false")
            self._send_roster()
            return
        async with self._contract_lock:
            # Recruitment publishes its new spec on the loop before its FILES job completes. Observe those earlier
            # publications, then snapshot AND submit without yielding so a later hire cannot overtake this update.
            await durable.run(durable.FILES, lambda: None)
            staged = copy.copy(self)
            staged.registry = copy.copy(self.registry)
            previous = dict(self.registry.agents)
            staged.registry.agents = {key: spec.model_copy(deep=True)
                                      for key, spec in self.registry.agents.items()}
            staged.registry.paths = dict(self.registry.paths)
            loop = asyncio.get_running_loop()
            published = loop.create_future()

            def publish(spec: AgentSpec | None) -> None:
                try:
                    agent_id = spec.id if msg.get("action") == "rehire" and spec is not None else msg.get("agent_id")
                    # A synchronous plan rehire can wait behind this job on the loop, publish a newer object,
                    # then return before this callback is serviced. Its completed write supersedes this snapshot.
                    if self.registry.agents.get(agent_id) is not previous.get(agent_id):
                        self._send_roster()
                        published.set_result(None)
                        return
                    # Only publish the affected entry; unrelated recruitment may have finished during the write.
                    if agent_id in staged.registry.agents:
                        self.registry.agents[agent_id] = staged.registry.agents[agent_id]
                        self.registry.paths[agent_id] = staged.registry.paths[agent_id]
                    elif agent_id in self.registry.agents and msg.get("action") == "release":
                        self.registry.agents.pop(agent_id, None)
                        self.registry.paths.pop(agent_id, None)
                    self._send_roster()
                except BaseException as exc:
                    published.set_exception(exc)
                else:
                    published.set_result(spec)

            def job() -> None:
                spec = staged._contract_update(msg)
                # Match Registry.asave_contract: publication is scheduled by the FIFO writer before it advances
                # to a later hire, rather than by an awaiting task that may resume after that hire's publication.
                loop.call_soon_threadsafe(publish, spec)

            write = asyncio.wrap_future(durable.submit(durable.FILES, job))

            async def commit() -> None:
                await write
                spec = await published
                if msg.get("action") == "rehire" and spec is not None and spec.contract:
                    changed = spec.contract.verification.get("rehire_changed")
                    if changed:
                        await self.emit(Event(type="contract.warning", agent_id=spec.id,
                                              data={"agent_id": spec.id, "changed": changed}))

            pending = asyncio.create_task(commit())
            cancelled = None
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError as exc:
                    cancelled = exc
            pending.result()  # A storage error takes precedence over cancellation; never announce completion.
            if cancelled is not None:
                raise cancelled

    def _contract_update(self, msg: dict) -> AgentSpec | None:
        if msg.get("action") != "release" and not paper2agent.enabled(self.s):
            log.warning("contract update refused: recruit.enabled=false")
            return
        action, days = msg.get("action"), float(msg.get("days") or self.s.recruit.default_ttl_days)
        if action == "extend":
            return self.registry.extend(msg["agent_id"], days)
        elif action == "release":
            self.registry.release(msg["agent_id"])
        elif action == "activate":
            return self.registry.activate(msg["agent_id"])
        elif action == "rehire":
            return self.registry.rehire(msg["slug"], days)

    async def _recruit(self, msg: dict) -> None:
        if not paper2agent.enabled(self.s):
            await self.emit(Event(type="recruit.failed", request_id=msg.get("request_id"),
                                  data={"error": "recruitment is disabled (recruit.enabled=false)"}))
            return
        from ..recruit.paper2agent import Recruitment

        try:
            paper2agent.require_enabled(self.s)
            await Recruitment(self, msg).run()
        except Exception as e:
            log.exception("recruitment failed")
            await self.emit(Event(type="recruit.failed", request_id=msg.get("request_id"), data={"error": str(e)}))
        self._send_roster()

    # ---------------- task execution ----------------
    def _bind_recruitment_conversion(self, parent_id: str, child: Task) -> None:
        if (not isinstance(parent_id, str) or not parent_id or parent_id in self._recruitment_conversions
                or child.id == parent_id or not child.request_id
                or child.id in self.tasks or child.id in self._result_queued
                or self.store.get("accepted_task", child.id) is not None
                or any(e.get("task_id") == child.id for e in self.store.pending())):
            raise ValueError("recruitment conversion identity is already used or missing")
        self._recruitment_conversions[parent_id] = {
            "task_id": child.id, "request_id": child.request_id, "agent_id": child.agent_id}

    async def _recruitment_cost_fields(self, task: Task) -> dict:
        """A finished receipt follows the nested result's durable outbox write, even when its waiter was cancelled.

        The FIFO barrier runs behind that job; a Python return or `_result_queued` hint is not a commit receipt.
        ACK may already have removed the outbox event, but its finished state row remains. No dollars are copied.
        """
        if not self.s.policy.budget.reserve_before_dispatch or task.meta.get("kind") != "plan_recruit":
            return {}
        binding = self._recruitment_conversions.get(task.id)
        if binding is None:
            return {}  # No nested dispatch was registered; callers preserve their ordinary result semantics.
        unknown = {"cost_usd": None, "cost_known": False}
        if (not isinstance(binding, dict) or set(binding) != {"task_id", "request_id", "agent_id"}
                or not isinstance(binding["task_id"], str) or not binding["task_id"]
                or binding["task_id"] == task.id or binding["request_id"] != task.request_id
                or binding["agent_id"] != task.agent_id):
            return unknown
        expected = dict(binding)
        try:
            await durable.run(self.store.lane, lambda: None)
            receipt = self.store.get("accepted_task", expected["task_id"])
        except Exception:
            return unknown
        if self._recruitment_conversions.get(task.id) is not binding or binding != expected:
            return unknown
        return {"cost_usd": 0, "cost_known": True} if receipt == {"state": "finished"} else unknown

    async def _run_guarded(self, task: Task) -> None:
        try:
            await self.run_task(task)
        except BaseException as e:
            self.breakers.pop(task.id, None)  # a crash or cancel skips emit_final
            # cancelled or crashed: the gateway must still get a result
            self.event_buffers.pop(task.id, None)
            cancelled = isinstance(e, asyncio.CancelledError)
            if not cancelled:
                log.exception("task %s crashed", task.id)
            if task.id in self._result_queued:
                # The real result is already on its way and commits even though this task was cancelled while it
                # waited. A second, "cancelled" one would overwrite it at the gateway (#606).
                if cancelled:
                    raise
                return
            err = "cancelled" if cancelled else f"{type(e).__name__}: {e}"
            # The adapter reported what it saw of its wrapper's end before the cancellation reached here; a run that
            # never got that far has no observation, so it stays UNKNOWN (#863).
            cleanup = self._cleanup_reports.pop(task.id, None) or {
                "status": "UNKNOWN", "reason": "no_runner_observation", "active_processes_measured": False}
            interrupted = None
            try:
                costs = await self._recruitment_cost_fields(task)
            except asyncio.CancelledError as again:
                # A second cancel can interrupt the receipt read. Still send conservative UNKNOWN, then preserve
                # cancellation; the queued barrier never mutates the binding or manufactures a finished receipt.
                costs, interrupted = {"cost_usd": None, "cost_known": False}, again
            res = self._shutdown_result(
                TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error=err, cleanup_evidence=cleanup,
                           **costs),
                cancelled=cancelled)
            base = dict(task_id=task.id, agent_id=task.agent_id, request_id=task.request_id)
            # One unit: a second cancel arriving while this commits cannot leave the status without the result.
            await self.emit_all([
                Event(type="agent.status", data={"state": "error", "error": short(err, 200)}, **base),
                Event(type="task.result", data={**res.model_dump(mode="json"),
                                                "control": self.controls.finish(task.id)}, **base)])
            if interrupted is not None:
                raise interrupted
            if cancelled:
                raise
        finally:
            self.__dict__.get("_env_grants", {}).pop(task.id, None)  # the run is over: its grant goes with it
            watcher = self.__dict__.get("_disk_watchers", {}).pop(task.id, None)
            if watcher is not None:
                watcher.cancel()
            self._recruitment_conversions.pop(task.id, None)
            self._result_queued.discard(task.id)  # the run is over: nothing left to guard
            self._cleanup_reports.pop(task.id, None)

    def _resolve_agent(self, task: Task) -> AgentSpec:
        agent = self.registry.get(task.agent_id)
        # A read-only task takes nothing from the sender: run_task rebuilds it as read_only_profile.
        if task.meta.get("agent_overrides") and not is_read_only_task(task.meta):
            agent = AgentSpec.model_validate({**agent.model_dump(), **task.meta["agent_overrides"]})
        # A request-local CSO model (#272) names who answers, not what they may do, so read-only runs keep it
        # too (read_only_profile keeps engine and model). One the runner cannot apply fails the task: running
        # the registry's model instead would resume another engine's session or switch models mid-request.
        if "agent_identity" in task.meta:
            identity = checked_identity(task.meta["agent_identity"])
            engine = Engine(identity["engine"])
            limits = switched_engine_limits(agent, engine) if engine is not agent.engine else {}
            agent = AgentSpec.model_validate({**agent.model_dump(), **identity, **limits})
        if self.s.runner.force_engine:
            agent = agent.model_copy(update={"engine": Engine(self.s.runner.force_engine)})
        # Last, so the tier means a model on the engine the agent really runs on: an override, a request identity or
        # a forced engine that moved it has already been applied, and any model they named is kept (#625).
        # A plan step's own tier (#625, step 2) is a decision of the approved plan, already capped by the gateway at
        # the staff member's declared tier, so it comes before the member's model: only a lower tier is ever sent.
        step_tier = task.meta.get("model_tier")
        if step_tier in ("fast", "balanced", "reasoning"):
            step_model = self.s.model_tiers.model_for(step_tier, agent.engine.value)
            if step_model:
                agent = agent.model_copy(update={"model": step_model, "model_tier": step_tier})
        if agent.model is None and agent.model_tier:
            tier_model = self.s.model_tiers.model_for(agent.model_tier, agent.engine.value)
            if tier_model:
                agent = agent.model_copy(update={"model": tier_model})
        return agent

    def _mcp_servers(self, agent: AgentSpec, env: dict[str, str], allow_ask: bool = True) -> list[McpServerSpec]:
        env = {**env, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")]))}
        servers: list[McpServerSpec] = []
        timeout_s = labhq_mcp_timeout_s(self.s)
        if "approval" in agent.builtin_mcp:
            servers.append(McpServerSpec(name="labhq_approval", command=sys.executable,
                                         args=["-m", "labhq.tools.approval_mcp"], env=env,
                                         timeout_s=timeout_s))
        if "hpc" in agent.builtin_mcp and self.s.hpc.scheduler != "none":
            servers.append(McpServerSpec(name="labhq_hpc", command=sys.executable,
                                         args=["-m", "labhq.tools.hpc_mcp"], env=env,
                                         timeout_s=timeout_s))
        # Local jobs the runner owns (#675); no scheduler needed. Claude staff only: the runner refuses the call for any
        # other engine, so mounting it there would offer a tool that always fails (#1003).
        if "job" in agent.builtin_mcp and agent.engine == Engine.claude_code:
            servers.append(McpServerSpec(name="labhq_job", command=sys.executable,
                                         args=["-m", "labhq.tools.local_job_mcp"], env=env, timeout_s=timeout_s))
        if "annot" in agent.builtin_mcp:
            # Read-only public lookups: no per-call approval, also for variants from restricted zones (PI 2026-10-06).
            # annot_tools limits which of its tools the server offers (#1200): the server, not each engine, enforces it.
            annot_env = env if agent.annot_tools is None else {**env, "LABHQ_ANNOT_TOOLS": ",".join(agent.annot_tools)}
            servers.append(McpServerSpec(name="labhq_annot", command=sys.executable,
                                         args=["-m", "labhq.tools.annot_mcp"], env=annot_env,
                                         auto_approve=True, timeout_s=literature_mcp_timeout_s(self.s)))
        if "wiki" in agent.builtin_mcp and self._wiki_tool_ready():
            # Read-only search over the PI's own notes; every call is logged to outputs/wiki_queries.jsonl.
            servers.append(McpServerSpec(name="labhq_wiki", command=sys.executable,
                                         args=["-m", "labhq.tools.wiki_mcp"], env=env,
                                         auto_approve=True, timeout_s=timeout_s))
        if allow_ask and agent.engine != Engine.antigravity:
            servers.append(McpServerSpec(name="labhq_ask", command=sys.executable,
                                         args=["-m", "labhq.tools.ask_mcp"], env=env,
                                         timeout_s=timeout_s))
        return servers + list(agent.mcp)

    def _resume_baseline(self, task: Task, agent: AgentSpec, ws: TaskWorkspace) -> dict | None:
        """Look across this runner's workspaces, including a reused workspace after restart."""
        if not task.resume_session_id:
            return None
        paths = set(self.ws_root.glob("*/*/manifest.json"))
        paths.update(workspace.dir / "manifest.json" for workspace in self.workspaces.values())
        paths.add(ws.dir / "manifest.json")
        latest, latest_at = None, -1.0
        for path in sorted(paths):
            try:  # a manifest.json that is a link is an agent's, not labhq's (#165)
                manifest = json.loads(read_owned(path.parent, path.name) or "")
            except ValueError:
                continue
            runs = manifest.get("runs", {}) if isinstance(manifest, dict) else {}
            if not isinstance(runs, dict):
                continue
            for tid, run in runs.items():
                if (tid == task.id or not isinstance(run, dict)
                        or run.get("session_id") != task.resume_session_id
                        or run.get("runner_id", self.s.runner.id) != self.s.runner.id
                        or run.get("engine") != agent.engine.value):
                    continue
                timestamp = max((value for key in ("accounting_at", "ended_at", "started_at")
                                 if type(value := run.get(key)) in (int, float)), default=0)
                if timestamp >= latest_at:
                    latest, latest_at = run, timestamp
        return latest

    def _zones(self) -> list[Path]:
        return restricted_zones(self.s)

    def _internal_zones(self) -> list[Path]:
        return resolved_zones(self.s, {"internal"})

    def _upstream_refusal(self, directory: Path, zones: list[Path]) -> str | None:
        """Why an earlier step's workspace is not opened to this step, or None (#132).

        Claude reads inside an `--add-dir` folder without asking the approval gate, so `outputs/link -> zone`
        made by the earlier agent would be read unchecked. A refused folder is only not opened: its files can
        still be read through the gate, which resolves real paths. A listing that cannot finish refuses too.
        """
        if not zones:
            return None
        if overlaps_zone(directory, zones):
            return "통제 데이터 구역"
        links, incomplete = zone_links(directory, zones, self.s.runner.reference_scan_max_entries,
                                       self.s.runner.reference_scan_max_depth)
        if links:
            more = f" 외 {len(links) - 1}개" if len(links) > 1 else ""
            return f"하위 링크가 통제 데이터 구역을 가리키거나 풀 수 없음: {links[0].relative_to(directory).as_posix()}{more}"
        return incomplete

    def _consult_workspace_input(
        self, task: Task,
    ) -> tuple[tuple[Path, tuple[str, ...], list[tuple[str, tuple[str, ...]]], list[tuple[str, str]]] | None,
               str | None]:
        """Validate declared runner-local refs; staging reopens every component through held folders (#86).

        A ref that escapes the folder, goes through a link or is a restricted file refuses the consultation. A ref
        that merely names nothing usable (no such file, a sentence instead of a path) is left out with its
        reason, so one wrong entry does not end the whole request (#1043)."""
        raw_source = task.meta.get("source_workdir")
        refs = task.meta.get("consult_refs") or []
        if task.meta.get("kind") != "consult" or not refs:
            return None, None
        if not raw_source:
            return None, "consult refs에 원래 작업 폴더가 없습니다"
        try:
            workspace_root = self.ws_root.resolve(strict=True)
            source = Path(str(raw_source))
            resolved_source = source.resolve(strict=True)
            if not source.is_dir() or is_link(source) or not resolved_source.is_relative_to(workspace_root):
                raise ValueError
            source_parts = resolved_source.relative_to(workspace_root).parts
            resolved: list[tuple[str, tuple[str, ...]]] = []
            left_out: list[tuple[str, str]] = []
            for raw_ref in refs:
                normalized = str(raw_ref).replace("\\", "/")
                if (not normalized or normalized.startswith(("/", "../")) or "/../" in normalized
                        or normalized.endswith("/..") or re.match(r"^[A-Za-z]:", normalized)):
                    raise ValueError
                if any(re.match(r"^[A-Za-z]:", part) for part in Path(normalized).parts):
                    raise ValueError  # a drive-qualified part anywhere, also behind a missing folder
                current = source
                missing = False
                for part in Path(normalized).parts:
                    current /= part
                    if not os.path.lexists(current):
                        missing = True
                        break
                    if is_link(current):
                        raise ValueError
                if missing:
                    left_out.append((str(raw_ref), "원래 작업 폴더에 그런 파일이 없음"))
                    continue
                if not current.resolve(strict=True).is_relative_to(resolved_source):
                    raise ValueError
                if not current.is_file():
                    raise ValueError
                if task.agent_id == "facilities" and overlaps_zone(current.resolve(strict=True), self._zones()):
                    raise ValueError
                resolved.append((normalized, tuple(Path(normalized).parts)))
        except (OSError, RuntimeError, ValueError):
            return None, "consult refs는 이 runner의 원래 작업 폴더 안에 있는 링크 아닌 파일이어야 합니다"
        return (workspace_root, source_parts, resolved, left_out), None

    @staticmethod
    def _open_consult_ref(root: Path, source_parts: tuple[str, ...], ref_parts: tuple[str, ...]) -> int:
        """Open a ref relative to handles held from the workspace root through every parent folder."""
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(HeldDir.hold(root))
            for part in (*source_parts, *ref_parts[:-1]):
                held = stack.enter_context(held.child(part))
            return held.open_read_file(ref_parts[-1])

    @staticmethod
    def _copy_consult_ref(fd: int, target: Path, limit: int) -> int | None:
        """Copy one already-held regular file; None means it grew past the limit."""
        copied = 0
        complete = False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("consult ref is not a regular file")
            with os.fdopen(fd, "rb") as opened, target.open(mode="xb") as staged:
                fd = -1
                while chunk := opened.read(CONSULT_REF_COPY_CHUNK):
                    copied += len(chunk)
                    if copied > limit:
                        return None
                    staged.write(chunk)
            complete = True
        finally:
            if fd >= 0:
                os.close(fd)
            if not complete and os.path.lexists(target):
                remove_entry(target)
        return copied

    def _stage_consult_refs(self, task: Task, ws: TaskWorkspace) -> tuple[Task, str | None]:
        """Copy declared refs into this consult's workspace under neutral filenames."""
        inputs, error = self._consult_workspace_input(task)
        if task.meta.get("kind") == "consult":
            task = task.model_copy(update={"meta": {
                key: value for key, value in task.meta.items()
                if key not in {"source_workdir", "consult_refs"}
            }})
        if error or inputs is None:
            return task, error
        workspace_root, source_parts, sources, left_out = inputs
        safe_id = "consult-" + re.sub(r"[^A-Za-z0-9_.-]", "_", task.id)[:80]
        relative_root = Path("refs") / safe_id
        staged_root = plain_directory(ws.dir, relative_root)
        if staged_root is None:
            return task, "consult refs 복사 폴더를 안전하게 만들 수 없습니다"
        try:
            for entry in staged_root.iterdir():
                remove_entry(entry)
        except OSError:
            return task, "consult refs 복사 폴더를 안전하게 비울 수 없습니다"

        total = 0
        copied: dict[str, str] = {}
        skipped: list[tuple[str, str]] = list(left_out)
        for number, (raw_ref, ref_parts) in enumerate(sources, 1):
            fd = -1
            try:
                fd = self._open_consult_ref(workspace_root, source_parts, ref_parts)
                size = os.fstat(fd).st_size
            except OSError:
                if fd >= 0:
                    os.close(fd)
                skipped.append((raw_ref, "검증 뒤 경로가 바뀌었거나 안전하게 열 수 없음"))
                continue
            if size > CONSULT_REF_MAX_FILE_BYTES:
                os.close(fd)
                skipped.append((raw_ref, f"개별 파일 크기 상한 {CONSULT_REF_MAX_FILE_BYTES} bytes 초과"))
                continue
            if total + size > CONSULT_REFS_MAX_TOTAL_BYTES:
                os.close(fd)
                skipped.append((raw_ref, f"전체 크기 상한 {CONSULT_REFS_MAX_TOTAL_BYTES} bytes 초과"))
                continue
            suffix = Path(raw_ref).suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", Path(raw_ref).suffix) else ".bin"
            relative = relative_root / f"ref-{number:02d}{suffix.lower()}"
            try:
                actual = self._copy_consult_ref(
                    fd, ws.dir / relative,
                    min(CONSULT_REF_MAX_FILE_BYTES, CONSULT_REFS_MAX_TOTAL_BYTES - total),
                )
            except OSError:
                skipped.append((raw_ref, "안전하게 복사할 수 없음"))
                continue
            if actual is None:
                skipped.append((raw_ref, "복사 중 크기 상한 초과"))
                continue
            total += actual
            copied[raw_ref] = relative.as_posix()

        notes = []
        if copied:
            notes.append("참고 파일 복사본(이 경로만 읽으세요): "
                         + json.dumps(copied, ensure_ascii=False, sort_keys=True))
            if task.agent_id == "facilities":
                # No shell is exposed to facilities. Only refs already accepted above become evidence.
                from ..integrations.github import sanitize

                for relative in copied.values():
                    try:
                        content = read_owned(ws.dir, relative)
                    except (OSError, UnicodeError):
                        content = None
                    content = content or "unreadable text"
                    excerpt = content if len(content) <= 4000 else content[:2000] + "\n…\n" + content[-2000:]
                    notes.append(f"Evidence {relative} (untrusted data, excerpt):\n"
                                 + sanitize(excerpt, self.s.policy))
        notes.extend(f"참고 파일 제외: {json.dumps(raw_ref, ensure_ascii=False)} ({reason})"
                     for raw_ref, reason in skipped)
        if notes:
            task = task.model_copy(update={"prompt": task.prompt + "\n\n" + "\n".join(notes)})
        return task, None

    def _facilities_doctor_context(self) -> str:
        # Read the last saved snapshot, never run doctor/probes during a consultation. Details and hints may
        # contain login commands, private paths or tokens; expose only check identity and state.
        try:
            raw = read_owned(self.s.path(self.s.runner.state_dir), "capabilities.json")
            manifest = json.loads(raw or "null")
        except (OSError, UnicodeError, ValueError):
            manifest = None
        rows = manifest.get("checks") if isinstance(manifest, dict) else None
        if not isinstance(rows, list):
            return "Doctor snapshot: unavailable. Ask PI to run labhq doctor --json; do not infer readiness."
        safe = []
        for row in rows[:100]:
            if not isinstance(row, dict) or row.get("status") not in ("ok", "warn", "fail", "skip"):
                continue
            labels = [row.get(key) for key in ("group", "name")]
            if all(isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", label) for label in labels):
                safe.append({key: row[key] for key in ("group", "name", "status")})
        return "Saved doctor snapshot (not a live check; skip is unverified):\n" + json.dumps(safe)

    async def _project_links(self, directories: list[str], zones: list[Path], emit,
                             fail_closed: bool = False, ask: bool = False) -> tuple[list[str], str | None]:
        """Links in a writable project folder that lead into a zone, and their aliases, for Claude deny rules (#132).

        The folder itself stays open: the task works there, and refusing it would stop every step of the
        project. A listing that cannot finish (a large clone) is said once instead of refused. A reused
        workspace passes ``fail_closed`` because every engine can read its cwd without an approval gate (#191).
        """
        denied: list[str] = []
        if not zones:
            return denied, None
        for directory in dict.fromkeys(directories):
            path = Path(directory)
            if not path.is_dir():
                if fail_closed:
                    return denied, "경로를 폴더로 열 수 없음"
                continue
            # In a thread: a large clone on a network file system must not stall the runner's connection.
            links, incomplete = await asyncio.to_thread(zone_links, path, zones, self.s.runner.reference_scan_max_entries,
                                                        self.s.runner.reference_scan_max_depth)
            denied += [str(link) for link in links]
            if fail_closed and (links or incomplete):
                if links:
                    names = ", ".join(link.relative_to(path).as_posix() for link in links[:5])
                    more = f" 외 {len(links) - 5}개" if len(links) > 5 else ""
                    return denied, f"하위 링크가 통제 데이터 구역을 가리키거나 풀 수 없음: {names}{more}"
                return denied, incomplete
            key = (str(path), tuple(map(str, links)), incomplete, ask)
            if (links or incomplete) and key not in self.project_link_warned:
                self.project_link_warned.add(key)
                if links:
                    names = ", ".join(link.relative_to(path).as_posix() for link in links[:5])
                    action = ("Claude는 그 경로를 승인 게이트로 보내고 다른 표기도 경로 판정을 거칩니다"
                              if ask else
                              "Claude는 그 경로를 막고 다른 표기와 셸 명령은 승인 게이트로 보냅니다")
                    await emit("agent.log", {"level": "warn", "text": (
                        f"프로젝트 폴더 {path.name}의 링크 {len(links)}개가 통제 데이터 구역으로 이어지거나 풀 수 없습니다: "
                        f"{names}. {action}")})
                if incomplete:
                    await emit("agent.log", {"level": "warn", "text": (
                        f"프로젝트 폴더 {path.name}의 링크를 다 확인하지 못했습니다({incomplete}). "
                        + ("별칭 Read는 Claude 승인 경로 판정으로 보냅니다" if ask else
                           "그 너머의 링크가 통제 구역을 가리켜도 막지 못합니다"))})
        return denied, None

    def _reference_dirs(self, task: Task, writable: list[str],
                        needs_rules: bool = False) -> tuple[list[str], list[str], list[str]]:
        """Re-check path references with resolved paths on this runner (#36).

        Returns (read-only dirs, skip notes, refused values). A refused value is also withheld from the prompt.
        With `needs_rules` (Claude: Edit/Write deny rules are what keeps a reference read-only), a folder that
        gets no rule, such as a mapped network drive that resolves to UNC, is refused too (#177).
        """
        roots = [Path(root).resolve() for root in reference_roots(self.s)]
        open_dirs = [Path(d).resolve() for d in writable]
        zones = self._zones()
        kept: list[str] = []
        skipped: list[str] = []
        refused: list[str] = []
        for raw in task.meta.get("reference_dirs") or []:
            reason = None
            try:
                path = Path(os.path.expanduser(str(raw))).resolve()
            except (OSError, RuntimeError, ValueError):
                reason = "경로를 읽을 수 없음"
            else:
                directory = path if path.is_dir() else path.parent
                if not path.exists():
                    reason = "없음"
                elif not any(directory == root or directory.is_relative_to(root) for root in roots):
                    reason = "runner.reference_roots 밖"
                elif overlaps_restricted(str(directory), self.s) or overlaps_zone(directory, zones):
                    reason = "통제 데이터 구역"
                else:
                    # A link or mount below the folder can still lead into a zone (reference/link/raw.tsv).
                    reason = scan_reference_dir(directory, zones, self.s.runner.reference_scan_max_entries,
                                                self.s.runner.reference_scan_max_depth)
                if reason is None and needs_rules and not claude_rule_ready(str(directory)):
                    reason = "네트워크(UNC) 경로라 Claude 쓰기 거부 규칙을 만들 수 없음"
                if reason is None:
                    if any(directory == d or directory.is_relative_to(d) for d in open_dirs):
                        continue  # already reachable through a writable project dir; keep that dir writable
                    if any(d.is_relative_to(directory) for d in open_dirs):
                        # Edit/Write deny rules on this folder would also cover the task's own workspace or project.
                        reason = "작업·프로젝트 폴더를 품고 있어 읽기 전용으로 열 수 없음"
                    elif str(directory) not in kept:
                        kept.append(str(directory))
            if reason is not None:
                skipped.append(f"{raw} ({reason})")
                refused.append(str(raw))
        return kept, skipped, refused

    def _busy_roots(self, task_id: str, since: float | None = None) -> list[Path]:
        """Folders other tasks are writing now (or wrote after `since`), and workspaces of unfinished HPC jobs."""
        entries = [(ro, roots) for tid, (ro, roots) in self.active_roots.items() if tid != task_id]
        if since is not None:
            entries += [(ro, roots) for ended, tid, ro, roots in self.ended_roots if ended >= since and tid != task_id]
        # A read-only task still gets labhq's result files in its own workspace.
        busy = [path for ro, roots in entries for path in (roots[:1] if ro else roots)]
        return busy + [Path(j["workdir"]).resolve() for j in self.jobs.values()
                       if not j.get("terminal") and j.get("workdir")]

    def _read_only_watch(self, task: Task, ws: TaskWorkspace, dirs: list[str]) -> tuple[ReadOnlyWatch, list[str]]:
        """The folders a read-only run is checked against, minus those another task is writing right now (#36)."""
        roots, skip, notes = watch_roots(ws.dir, dirs, self._busy_roots(task.id))
        skip += [ws.dir.resolve() / "outputs", ws.dir.resolve() / ".tmp"]
        owned = {Path(w.dir).resolve() for w in self.workspaces.values()}
        base = self.ws_root.resolve()
        owned |= {Path(d).resolve() for d in task.meta.get("upstream_dirs", [])
                  if Path(d).resolve().is_relative_to(base)}
        source = task.meta.get("source_workdir")
        if source and Path(source).resolve().is_relative_to(base):
            owned.add(Path(source).resolve())
        return ReadOnlyWatch(roots, self.s.runner.read_only_check_max_entries, owned, skip), notes

    async def _read_only_verdict(self, result: TaskResult, watch: ReadOnlyWatch, emit, ws: TaskWorkspace,
                                 task_id: str) -> TaskResult:
        """A read-only run that changed files fails, and the PI is told what changed. Nothing is undone."""
        changed, left_out = watch.changed(self._busy_roots(task_id, since=watch.started))
        if left_out:
            await emit("agent.log", {"level": "warn", "text": (
                f"읽기 전용 쓰기 확인에서 제외: 바뀐 항목 {left_out}개 (실행 중 다른 작업이 쓰던 폴더)")})
        if not changed:
            return result
        # Labels stand in for local paths in the error and the alert; this local manifest maps them back.
        await ws.aupdate_run(task_id, read_only_changes=changed,
                             read_only_roots={label: str(path) for label, path in watch.roots})
        shown = ", ".join(changed[:8]) + (f" 외 {len(changed) - 8}개" if len(changed) > 8 else "")
        await emit("agent.log", {"level": "alert", "text": (
            f"읽기 전용 실행이 파일을 바꿨습니다: {shown}. 되돌리지 않았으니 확인하세요. "
            "전체 목록과 폴더 이름은 작업 폴더 manifest.json의 read_only_changes·read_only_roots")})
        return result.model_copy(update={
            "ok": False, "error": f"읽기 전용 실행 중 파일 {len(changed)}개가 바뀌어 결과를 쓰지 않습니다: {shown} "
                                  "(read-only policy)"})

    def _system_ca_env(self, agent: AgentSpec) -> dict[str, str]:
        """Keep the two Python CA variables paired, or point both at the OS trust store (9th mock trial).

        The PEM sits in the workspace root: a task folder could hold a link an earlier run left, and the runner state
        folder may be closed to the Codex sandbox. Staff run as the same account and can rewrite it, so before every
        spawn the store is read again and the file rewritten when it differs: a CA one task planted is never trusted
        by the next, and a root the institution replaced reaches staff without a runner restart (PR #359 review)."""
        if not self.s.runner.system_ca_bundle:
            return {}
        engine_env = getattr(getattr(self.s.engines, agent.engine.value, None), "env", None) or {}
        configured = {name: engine_env.get(name) or os.environ.get(name) for name in CA_ENV}
        chosen = {name: value for name, value in configured.items() if value}
        if len(chosen) == 1:
            missing = next(name for name in CA_ENV if name not in chosen)
            return {missing: next(iter(chosen.values()))}
        if chosen:
            return {}
        pem = system_ca_pem()
        if not pem:
            return {}
        target = self.ws_root / ".labhq-system-ca.pem"
        expected = pem.encode("ascii")
        try:
            if not is_link(target) and target.is_file() and target.read_bytes() == expected:
                return {name: str(target) for name in CA_ENV}
        except OSError:
            pass
        tmp = None
        try:
            self.ws_root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.ws_root, prefix=".labhq-system-ca.", suffix=".tmp")
            with os.fdopen(fd, "wb") as handle:
                handle.write(expected)
            os.replace(tmp, target)  # replaces a link itself, never the file it points at
        except OSError:
            log.warning("system CA bundle not written", exc_info=True)
            if tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            return {}
        return {name: str(target) for name in CA_ENV}

    @staticmethod
    async def _slot_or_halt(semaphore: asyncio.Semaphore, halt: asyncio.Event | None) -> bool:
        """Take a run slot, or give up the moment the PI halts the waiting task. True holds the slot."""
        if halt is None:
            await semaphore.acquire()
            return True
        acquire, stop = asyncio.ensure_future(semaphore.acquire()), asyncio.ensure_future(halt.wait())

        def drop() -> None:
            if not acquire.done():
                acquire.cancel()  # a waiting acquire leaves the queue; a woken one hands its slot on
            elif not acquire.cancelled() and acquire.exception() is None:
                semaphore.release()

        try:
            await asyncio.wait({acquire, stop}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            stop.cancel()
            drop()
            raise
        stop.cancel()
        if acquire.done() and not acquire.cancelled() and acquire.exception() is None and not halt.is_set():
            return True
        drop()
        return False

    def _producer_binding_refusal(self, task: Task, binding: Any) -> str | None:
        """An environment step carries its own single binding: it must be this runner's, before anything runs."""
        prefix = "runner environment producer refused: "
        runtime = ("wsl" if self.s.runner.environment == "wsl" else
                   "windows" if sys.platform == "win32" else "linux")
        if not isinstance(binding, dict):
            return prefix + "invalid binding"
        python = {"windows": ".venv/Scripts/python.exe", "linux": ".venv/bin/python",
                  "wsl": ".venv/bin/python"}.get(binding.get("runtime") if isinstance(binding.get("runtime"), str) else None)
        runner_id = binding.get("runner_id")
        if (type(binding.get("version")) is not int or binding["version"] != 1 or python is None or
                binding.get("venv_path") != ".venv" or binding.get("python_path") != python or
                any(not isinstance(binding.get(key), str) or not INPUT_STEP_ID.fullmatch(binding[key])
                    or binding[key] != binding[key].rstrip(" .") for key in ("logical_step_id", "step_id")) or
                not isinstance(runner_id, str) or not 0 < len(runner_id) <= 256):
            return prefix + "invalid binding contract"
        session = task.meta.get("session_runner")  # compared, never hashed: it may be any JSON value
        if runner_id != self.s.runner.id or session != self.s.runner.id:  # a valid dispatch always names the runner
            return prefix + "runner mismatch"
        if binding["runtime"] != runtime:
            return prefix + "runtime mismatch"
        if task.meta.get("step_id") != binding["step_id"]:  # a valid producer dispatch always names its step
            return prefix + "step mismatch"
        return None

    def _environment_refusal_reason(self, task: Task) -> str | None:
        grants = self.__dict__.setdefault("_env_grants", {})
        grants.pop(task.id, None)  # whatever happens below, an earlier run of this id leaves no grant behind
        if "environment_binding" in task.meta:
            producer = self._producer_binding_refusal(task, task.meta["environment_binding"])
            if producer is not None:
                return producer
        bindings = task.meta.get("environment_bindings", [])
        if isinstance(bindings, list) and not bindings:
            return None
        prefix = "runner environment inputs unavailable: "
        if not isinstance(bindings, list):
            return prefix + "invalid bindings"
        steps = task.meta.get("upstream_steps")
        artifacts = task.meta.get("upstream_artifacts")
        if not isinstance(steps, dict) or not isinstance(artifacts, dict):
            return prefix + "ancestor mapping missing"
        runtime = ("wsl" if self.s.runner.environment == "wsl" else
                   "windows" if sys.platform == "win32" else "linux")
        for binding in bindings:
            if not isinstance(binding, dict):
                return prefix + "invalid binding"
            python = {"windows": ".venv/Scripts/python.exe", "linux": ".venv/bin/python",
                      "wsl": ".venv/bin/python"}.get(binding.get("runtime") if isinstance(binding.get("runtime"), str) else None)
            identities = (binding.get("logical_step_id"), binding.get("step_id"))
            runner_id = binding.get("runner_id")
            if (type(binding.get("version")) is not int or binding["version"] != 1 or
                    python is None or binding.get("venv_path") != ".venv" or
                    binding.get("python_path") != python or
                    any(not isinstance(x, str) or not INPUT_STEP_ID.fullmatch(x) or x != x.rstrip(" .")
                        for x in identities) or not isinstance(runner_id, str) or not 0 < len(runner_id) <= 256):
                return prefix + "invalid binding contract"
            if binding["runner_id"] != self.s.runner.id or task.meta.get("session_runner") != self.s.runner.id:
                return prefix + "runner mismatch"
            if binding["runtime"] != runtime:
                return prefix + "runtime mismatch"
            sid = binding["step_id"]
            artifact = artifacts.get(sid)
            if (not isinstance(steps.get(sid), str) or not steps[sid] or
                    not isinstance(artifact, dict) or not isinstance(artifact.get("task_id"), str) or
                    not artifact["task_id"] or artifact.get("workdir") != steps[sid]):
                return prefix + "ancestor mapping mismatch"
        # Metadata alone never establishes success: the runner's own run record, the hashed manifest and the
        # interpreter in the producer's workdir must all check out. No alias to the interpreter is created, so
        # the result only tells why a consumer is (still) refused (#674).
        root = getattr(self, "ws_root", None)
        if root is None or not task.request_id:
            return prefix + "workspace or request identity unavailable"
        from .step_inputs import validate_runner_env
        verified: list[dict[str, str]] = []
        for binding in bindings:
            sid = binding["step_id"]
            try:
                venv = validate_runner_env(root, steps[sid], artifacts[sid], binding, request_id=task.request_id)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                return prefix + "environment validation failed"
            verified.append({"step_id": sid, "venv": str(venv),
                             "python": str(Path(steps[sid]) / binding["python_path"])})
        read_only = False
        try:
            agent = self._resolve_agent(task)
            engine = agent.engine
            read_only = is_read_only_task(task.meta, agent)
        except Exception:  # noqa: BLE001 - an unknown agent is refused later; here it simply gets no grant
            engine = None
        if engine in ENVIRONMENT_ENGINES:
            if read_only:  # a read-only run takes no grant, and running it on the system Python would be another result
                return prefix + "a read-only step cannot be given the planned environment"
            for grant in verified:
                problem = self._environment_grant_problem(Path(grant["venv"]))
                if problem:  # Claude would follow a link out of the venv: fail closed, no grant
                    return prefix + f"environment folder cannot be shared read-only: {problem}"
            for grant in verified:
                grant["read_dir"] = engine in ENVIRONMENT_GRANT_ENGINES  # Claude opens the folder; the rest get the path
            grants[task.id] = verified
            return None
        if not ENVIRONMENT_ALIAS_AVAILABLE:
            # The validated .venv is not exposed to the consumer (no protected alias or read grant for this engine),
            # so starting it would run without the packages the plan asked for: stay closed (#674).
            return prefix + "environment verified but protected interpreter alias is not implemented"
        return None

    def _environment_grant_problem(self, venv: Path) -> str | None:
        """Why a verified .venv must not be opened to Claude, or None. A package may leave a link below it that leads
        into a controlled data zone, and `--add-dir` follows links; a listing that cannot finish is refused too (a venv
        may link out to its base interpreter, so only zones are judged, as for a project clone)."""
        try:
            directory = Path(venv).resolve()
            if not claude_rule_ready(str(directory)):  # no Edit/Write deny rule can be made (a UNC path): not read-only
                return "no Claude write-deny rule can be made for it (a network path)"
            zones = self._zones()
            if overlaps_restricted(str(directory), self.s) or overlaps_zone(directory, zones):
                return "it lies in a controlled data zone"
            links, incomplete = zone_links(directory, zones, self.s.runner.reference_scan_max_entries * 10,
                                           self.s.runner.reference_scan_max_depth)
        except Exception:  # noqa: BLE001 - not scanned means not shared
            return "it could not be scanned"
        if links:
            return "a link below it leads into a controlled data zone"
        return f"its listing was incomplete ({incomplete})" if incomplete else None

    def _apply_environment_grants(self, task: Task, read_dirs: list[str], *,
                                  read_only: bool) -> tuple[list[str], dict[str, str], str]:
        """(read dirs with the verified environments added, extra env, briefing line) for a consumer step (#674).

        The grant is the folder the runner just verified, opened read-only like a reference folder (Claude denies
        Edit/Write there); a read-only run takes none. `PYTHONDONTWRITEBYTECODE` keeps Python from writing into it.
        Engines other than Claude get the env and the briefing line without the extra read root."""
        grants = self.__dict__.get("_env_grants", {}).get(task.id) or []
        if read_only or not grants:
            return read_dirs, {}, ""
        dirs = list(dict.fromkeys([*read_dirs, *(grant["venv"] for grant in grants if grant.get("read_dir"))]))
        python = grants[0]["python"]
        note = ("[Runner environment]\n" + "\n".join(
            f"- Python with the packages the plan asked for ({grant['step_id']}): {grant['python']}" for grant in grants)
            + "\nRun scripts with that interpreter. The folder is read-only: do not install into it.")
        return dirs, {"LABHQ_ENV_PYTHON": python, "PYTHONDONTWRITEBYTECODE": "1"}, note

    async def _refuse_unprotected_environment(self, task: Task) -> TaskResult | None:
        error = await asyncio.to_thread(self._environment_refusal_reason, task)
        if error is None:
            return None
        result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error=error,
                            cost_usd=0.0, cost_known=True)
        base = dict(task_id=task.id, agent_id=task.agent_id, request_id=task.request_id)
        await self.emit_all([
            Event(type="agent.status", data={"state": "error", "error": error}, **base),
            Event(type="task.result", data=result.model_dump(mode="json"), **base)])
        return result

    async def run_task(self, task: Task, workdir_override: Path | None = None) -> TaskResult:
        refusal = await self._refuse_unprotected_environment(task)
        if refusal is not None:
            return refusal
        if paper2agent.disabled_task(self.s, task):
            result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                                error="recruitment and Paper2Agent are disabled (recruit.enabled=false)")
            await self.emit(Event(type="task.result", task_id=task.id, agent_id=task.agent_id,
                                  request_id=task.request_id, data=result.model_dump(mode="json")))
            return result
        from ..recruit.paper_skills import PaperSkillBuild

        if self.s.runner.environment == "wsl":
            from ..wsl_paths import message_paths, shared_path
            task = Task.model_validate(message_paths(task.model_dump(), "wsl"))
            shared_path(str(self.ws_root.resolve()), "wsl")
            if workdir_override is not None:
                workdir_override = Path(shared_path(str(workdir_override), "wsl"))
            override = workdir_override or task.meta.get("workdir")
            if override:
                shared_path(str(Path(override).resolve()), "wsl")

        self._result_queued.discard(task.id)  # a new run of this id

        if task.meta.get("kind") in {"recruit_check", "plan_recruit"}:
            from ..recruit.planning import run_task
            result = await run_task(self, task)
            await self.emit(Event(type="task.result", task_id=task.id, agent_id=task.agent_id,
                                  request_id=task.request_id, data=result.model_dump(mode="json")))
            return result
        build = None
        if task.meta.get("kind") == "paper_skill":
            try:
                build = PaperSkillBuild(self, task)
                task, workdir_override, cached = await asyncio.to_thread(build.prepare)
            except Exception as exc:
                cached = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                                    structured={"status": "failed", "error": f"Paper2Skill 준비 실패 ({type(exc).__name__})"})
            if cached is not None:
                await self.emit(Event(type="task.result", task_id=task.id, agent_id=task.agent_id,
                                      request_id=task.request_id, data=cached.model_dump(mode="json")))
                return cached
        try:
            return await self._run_task(task, workdir_override, paper_skill_build=build)
        finally:
            ws = self.workspaces.get(task.id)
            if ws is not None and task.meta.get("paper_skills"):
                ws.clear_paper_skills()
            if ws is not None:  # a step cut short after it staged its record: the record is still written (#1137)
                with contextlib.suppress(Exception):
                    await ws.aflush_run(task.id)

    async def _disk_gate(self, task: Task, agent: AgentSpec, workspace_dir: Path) -> str | None:
        """Free space of the drive this run's folder is on, when a task starts (#678): None to go on, else why not.

        Below ``runner.disk_fail_free_gb`` the task waits for a PI card; approving starts it, declining or no answer does
        not. Below the doctor's warning level it only warns. An unreadable drive never blocks. A facilities fix
        runs regardless: clearing a full workspace is what it is for."""
        runner = self.s.runner
        if task.meta.get("kind") == "facilities_fix":
            return None

        async def emit(kind: str, data: dict) -> None:
            await self.emit(Event(type=kind, task_id=task.id, agent_id=agent.id, request_id=task.request_id, data=data))

        try:
            space = disk_space.usage(workspace_dir)
        except (OSError, ValueError):
            return None
        if space.total <= 0:
            return None
        free_gib = space.free / disk_space.GIB
        if runner.disk_fail_free_gb and free_gib < runner.disk_fail_free_gb:
            summary = (f"작업 폴더 드라이브 여유 {free_gib:.1f} GiB(시작 기준 {runner.disk_fail_free_gb:g} GiB): "
                       "그래도 이 작업을 시작할까요?")
            await emit("agent.log", {"level": "warn", "text": f"디스크 여유가 거의 없어 PI 확인을 기다립니다: {free_gib:.1f} GiB"})
            decision = await self.broker.request_approval(ApprovalRequest(
                task_id=task.id, agent_id=agent.id, request_id=task.request_id, kind="disk_space", summary=summary,
                detail={"free_gib": round(free_gib, 2), "floor_gib": runner.disk_fail_free_gb,
                        "path": str(workspace_dir)}, timeout_s=24 * 3600))
            if decision.get("approved"):
                await emit("agent.log", {"level": "warn", "text": "PI가 디스크 여유가 적은 채로 시작을 승인했습니다"})
                return None
            return (f"작업 폴더 드라이브 여유가 {free_gib:.1f} GiB로 시작 기준 {runner.disk_fail_free_gb:g} GiB보다 적어 "
                    "시작하지 않았습니다(PI 거절 또는 무응답). 여유를 확보하거나 작업 폴더를 다른 드라이브로 옮기세요")
        if disk_space.low(space, runner.disk_warning_free_gb, runner.disk_warning_free_percent):
            await emit("agent.log", {"level": "warn", "text": (
                f"작업 폴더 드라이브 여유가 적습니다: {free_gib:.1f} GiB. 큰 다운로드 전에 확인하세요")})
        return None

    def _start_disk_watch(self, task: Task, agent: AgentSpec, workspace_dir: Path, extra_dirs=(), *,
                          read_only: bool = False) -> None:
        """Watch the drives this running task fills (#678): the work folder, the runner state folder,
        ``runner.bulk_data_root`` and its writable project folders (``extra_dirs``). A read-only run writes only its own
        workspace, so a full bulk or project drive is no reason to halt it. A facilities fix runs regardless: clearing a
        full workspace is what it is for."""
        runner = self.s.runner
        if not runner.disk_watch_s or not runner.disk_fail_free_gb or task.meta.get("kind") == "facilities_fix":
            return
        watchers = self.__dict__.setdefault("_disk_watchers", {})
        if task.id in watchers:
            return
        folders = [workspace_dir, self.s.path(runner.state_dir)]
        if runner.bulk_data_root and not read_only:
            folders.append(Path(os.path.expandvars(os.path.expanduser(str(self.s.path(runner.bulk_data_root))))))
        folders.extend(Path(d) for d in extra_dirs)
        watcher = asyncio.get_running_loop().create_task(self._disk_watch(task, agent, list(dict.fromkeys(folders))))
        watchers[task.id] = watcher
        watcher.add_done_callback(lambda done, tid=task.id: watchers.pop(tid, None) if watchers.get(tid) is done else None)

    async def _disk_watch(self, task: Task, agent: AgentSpec, folders: list[Path]) -> None:
        """Read the free space of ``folders`` every ``runner.disk_watch_s`` while the task is held; below
        ``runner.disk_fail_free_gb`` halt it once, session kept, for the PI to resume. The resume starts a new turn that
        meets ``_disk_gate`` again. An unreadable drive is skipped. Ends with the task."""
        runner = self.s.runner
        base = dict(task_id=task.id, agent_id=agent.id, request_id=task.request_id)
        while self.controls.holds(task.id):
            await asyncio.sleep(runner.disk_watch_s)
            if not self.controls.holds(task.id):
                return
            low = None
            for folder in folders:
                try:
                    space = await asyncio.to_thread(disk_space.usage, folder)
                except (OSError, ValueError):
                    continue
                if space.total > 0 and space.free < runner.disk_fail_free_gb * disk_space.GIB:
                    low = (folder, space.free / disk_space.GIB)
                    break
            if low is None:
                continue
            folder, free_gib = low
            text = (f"디스크 여유가 {free_gib:.1f} GiB로 기준 {runner.disk_fail_free_gb:g} GiB보다 적습니다"
                    f"({Path(folder).as_posix()})")
            halted = None
            if runner.disk_watch_halt:
                try:
                    halted = await self.controls.apply({"task_id": task.id, "action": "halt"})
                except Exception:  # noqa: BLE001 - the watch never fails the run; it tries again at the next beat
                    log.warning("task %s disk halt not applied; trying again", task.id, exc_info=True)
                    continue
                if halted is None:  # the controls no longer hold the task: it ended meanwhile
                    return
            record = {"signal": "disk", "mode": "enforce", "stage": "stop",
                      "enforcement": "halt" if halted else "off",
                      "free_gib": round(free_gib, 2), "floor_gib": runner.disk_fail_free_gb,
                      "path": Path(folder).as_posix()}
            await self.emit(Event(type="agent.log", data={"level": "alert", "text": text}, **base))
            if halted:
                await self.emit(Event(type="agent.control", data={**halted, "source": "disk"}, **base))
            await self.emit(Event(type="agent.breaker", data=record, **base))
            return  # once per turn: a resumed turn is a new task and is checked again at its start

    async def _bulk_data_hint(self, emit, zones: list) -> str:
        """The briefing line for ``runner.bulk_data_root``, or "" when unset or the folder is not one staff may use.

        The folder is outside the step folder, so a write there still goes through the usual approval; only a
        controlled zone keeps it out of the briefing (#678)."""
        root = self.s.runner.bulk_data_root
        if not root:
            return ""
        path = Path(os.path.expandvars(os.path.expanduser(str(self.s.path(root)))))
        try:
            resolved = path.resolve()
            if overlaps_zone(resolved, zones):
                raise ValueError("controlled zone")
        except (OSError, RuntimeError, ValueError):
            await emit("agent.log", {"level": "warn", "text": f"runner.bulk_data_root({root})를 직원 안내에 쓰지 않았습니다: "
                                                             "통제 구역이거나 경로를 확인할 수 없습니다"})
            return ""
        try:
            free = f" (여유 {disk_space.usage(resolved).free / disk_space.GIB:.0f} GiB)"
        except (OSError, ValueError):
            free = ""
        return (f"큰 원자료(수 GB 이상)는 작업 폴더가 아니라 {resolved}{free} 아래에 받으세요. "
                "작업 폴더 밖이라 쓰기는 평소처럼 승인을 거칠 수 있습니다.")

    async def _run_task(self, task: Task, workdir_override: Path | None = None, *, paper_skill_build=None) -> TaskResult:
        refusal = await self._refuse_unprotected_environment(task)
        if refusal is not None:
            return refusal
        # Recheck after asynchronous preparation, and protect internal/direct callers too.
        if paper2agent.disabled_task(self.s, task):
            result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0.0, cost_known=True,
                                error="recruitment and Paper2Agent are disabled (recruit.enabled=false)")
            await self.emit(Event(type="task.result", task_id=task.id, agent_id=task.agent_id,
                                  request_id=task.request_id, data=result.model_dump(mode="json")))
            return result
        agent = self._resolve_agent(task)
        read_only = is_read_only_task(task.meta, agent)
        refusal = read_only_refusal(agent.id, agent.engine) if read_only else None
        # A contract hire works only once active, and staff that send data to external services never take restricted
        # inputs (#424); the gateway refuses both first.
        refusal = (refusal or (probation_refusal(agent.id) if not_yet_active(agent) else None)
                   or external_service_refusal(agent, task, self.s))
        if refusal:  # the gateway refuses first; this holds for any other sender and after force_engine
            result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=refusal)
            base = dict(task_id=task.id, agent_id=agent.id, request_id=task.request_id)
            await self.emit_all([
                Event(type="agent.status", data={"state": "error", "error": short(refusal, 200)}, **base),
                Event(type="task.result", data=result.model_dump(mode="json"), **base)])
            return result
        if read_only:  # an allowlist built from the staff member's identity, never the sender's overrides
            agent = read_only_profile(agent)
        # Hooks run only in a writable Claude run; a read-only one turns every hook off (claude_code.READ_ONLY_FLAGS).
        hooked = agent.engine == Engine.claude_code and not read_only
        # The step's undelivered steers come with the dispatch; they go into the prompt when the CLI starts.
        self.controls.start(task.id, agent.id, hooks=hooked, session_id=task.resume_session_id,
                            steers=task.meta.get("steers") or [], dropped=task.meta.get("steer_dropped") or 0)
        override = workdir_override or (Path(task.meta["workdir"]) if task.meta.get("workdir") else None)
        workspace_dir = Path(override) if override else self.ws_root / time.strftime("%Y-%m-%d") / f"{task.id}_{agent.id}"
        # #678: judged on the folder this run really uses, before the workspace is created or a run slot is held, so a
        # full drive neither fails the directory creation first nor lets waiting cards take every slot.
        disk_error = await self._disk_gate(task, agent, workspace_dir)
        if disk_error:
            result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=disk_error)
            base = dict(task_id=task.id, agent_id=agent.id, request_id=task.request_id)
            await self.emit_all([
                Event(type="agent.log", data={"level": "alert", "text": disk_error}, **base),
                Event(type="agent.status", data={"state": "error", "error": short(disk_error, 200)}, **base),
                Event(type="task.result", data={**result.model_dump(mode="json"),
                                                "control": self.controls.finish(task.id)}, **base)])
            return result
        reused = await asyncio.to_thread(os.path.lexists, workspace_dir)  # file system work stays off the loop (#606)
        # Do not let TaskWorkspace create files through links left by an earlier run. Fresh workspaces keep
        # the old event ordering; reused ones are registered only after their preflight succeeds (#191).
        ws = None if reused else await _settled_thread(TaskWorkspace, self.ws_root, task, agent, workspace_dir)
        if reused:
            self.workspaces.pop(task.id, None)  # emit() must not append through the previous workspace yet
            self.event_buffers[task.id] = []  # ...but queued/working still belong in its log once it is safe
        if ws:
            self.workspaces[task.id] = ws
            self.task_req[task.id] = task.request_id

        tool_errors: list[str] = []
        tool_calls = ToolCallCounter()
        breaker = Breaker()
        escalation = Escalation(BreakerPolicy(hard_stop=self.s.runner.breaker_hard_stop), self.s.runner.breaker_beat_s)
        self.breakers[task.id] = escalation

        async def emit(typ: str, data: dict) -> None:
            tool_calls.observe(typ, data)
            if typ == "task.result":  # the last event: which steers were delivered, for the gateway's step (#59 ①)
                data = {**data, "control": self.controls.finish(task.id)}
            if typ == "agent.tool_error" and task.meta.get("general_result_contract"):
                first = str(data.get("text") or "tool failed").splitlines()[0].strip() or "tool failed"
                tool_errors.append(short(first, 200))
            await self.emit(Event(type=typ, task_id=task.id, agent_id=agent.id, request_id=task.request_id, data=data))
            if typ == "task.result":
                self.breakers.pop(task.id, None)
            if typ == "agent.compaction":
                breaker.reset_window()
                if escalation.compacted():
                    await self._announce_breaker_release(task.id)
                await self.emit(Event(type="agent.log", task_id=task.id, agent_id=agent.id, request_id=task.request_id,
                                      data={"level": "info", "text": "컨텍스트가 압축돼 폭주 감지를 처음부터 다시 셉니다"}))
            trip = breaker.observe(typ, data)
            # Once restricted, a failing call may be the restriction's own denial, so only a repeat keeps climbing (#1008).
            signalled = breaker.repeating if escalation.policy.stage in ("constrain", "stop") else breaker.active
            stage = escalation.observe(signalled) if typ in ("agent.tool", "agent.tool_error") else None
            if trip:  # the first crossing of a signal is the warn stage: tell the PI once, keep the agent running
                for kind, body in (("agent.breaker", trip), ("agent.log", {"level": "alert", "text": trip_text(trip)})):
                    await self.emit(Event(type=kind, task_id=task.id, agent_id=agent.id, request_id=task.request_id,
                                          data=body))
            if stage and (stage != "warn" or not trip):  # a first-ever warn was just announced as the trip itself
                await self._breaker_stage(task.id, agent.id, task.request_id, breaker, stage, hooked)

        async def emit_final(state: dict, result_data: dict) -> None:
            """The closing agent.status and the task.result as one uncancellable unit (see `emit_all`)."""
            base = dict(task_id=task.id, agent_id=agent.id, request_id=task.request_id)
            self.breakers.pop(task.id, None)
            await self.emit_all([
                Event(type="agent.status", data=state, **base),
                Event(type="task.result", data={**result_data, "control": self.controls.finish(task.id)}, **base)])

        await emit("agent.status", {"state": "queued"})
        # PI-text translations are short, tool-less display calls: on the research slot they queued behind a running
        # step past their 30 s timeout and were cancelled unstarted, leaving an unknown cost (#942, 9차 pi_text_20/34).
        consult = task.meta.get("kind") in ("consult", "pi_interpretation")
        semaphore = self.consult_sem if consult else self.sem
        # Not started until the PI lets it: a pause holds it before and after it waits for a run slot, and a halt while
        # it waits parks it without starting the CLI (PR #532 review).
        halted_before_start = False
        while True:
            if self.controls.paused(task.id):
                await emit("agent.log", {"level": "info", "text": "일시 정지 중이라 이 turn을 시작하지 않고 재개를 기다립니다"})
            if not await self.controls.wait_ready(task.id) or not await self._slot_or_halt(
                    semaphore, self.controls.halt_event(task.id)):
                halted_before_start = True
                break
            if not self.controls.paused(task.id):
                break
            semaphore.release()  # paused while it waited for the slot: give the slot back and hold
        if halted_before_start:
            self.event_buffers.pop(task.id, None)
            result = self.controls.halted(task.id, TaskResult(
                task_id=task.id, agent_id=agent.id, ok=False, error_kind="halted",
                error="halted before the turn started", session_id=task.resume_session_id))
            await emit("agent.log", {"level": "info", "text": "시작하기 전에 멈췄어요. 받은 방향 수정은 같은 단계의 다음 turn에 남깁니다"})
            await emit_final({"state": "idle", "halted": True}, result.model_dump(mode="json"))
            return result
        async with _holding(semaphore):
            steers, dropped = self.controls.take_prompt_steers(task.id)
            if steers:  # the PI's direction changes waiting now: the CLI starts with them
                task = task.model_copy(update={"prompt": steer_prompt(steers, dropped) + "\n\n" + task.prompt})
                if ws:
                    ws.task = task
            await emit("agent.status", {"state": "working", "task": task.meta.get("title") or short(task.prompt, 120)})
            zones = self._zones()
            internal_zones = self._internal_zones()
            preclean_started: float | None = None
            preclean_record: dict | None = None
            if reused:
                reason = None
                try:
                    resolved = workspace_dir.resolve()
                except (OSError, RuntimeError, ValueError):
                    reason = "경로를 풀 수 없음"
                if reason is None and overlaps_zone(resolved, zones):
                    reason = "폴더 자체가 통제 데이터 구역과 겹침"
                if reason is None:
                    _links, reason = await self._project_links([str(workspace_dir)], zones, emit, fail_closed=True)
                if reason is None:  # labhq writes these from outside every sandbox (#165)
                    reason = owned_link_error(workspace_dir)
                if reason:
                    self.event_buffers.pop(task.id, None)  # nothing is written into a workspace refused as unsafe
                    error = f"재사용 작업 폴더를 안전하게 열 수 없어 실행을 거부합니다: {reason}"
                    result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=error)
                    await emit("agent.log", {"level": "alert", "text": error})
                    await emit_final({"state": "error", "error": short(error, 200)}, result.model_dump(mode="json"))
                    return result
                proposal = task.meta.get("facilities_fix")
                if task.meta.get("kind") == "facilities_fix" and facility_fixes.is_workspace_cache(proposal):
                    # An ENOSPC workspace cannot accept even our event log or manifest. Keep the queued events in
                    # memory, clear the allowlisted cache first, then create/register TaskWorkspace and flush them.
                    preclean_started = time.time()
                    preclean_record = await facility_fixes.execute(proposal, workspace_dir)
                ws = await _settled_thread(TaskWorkspace, self.ws_root, task, agent, workspace_dir)
                for event in self.event_buffers.pop(task.id, []):  # before any later event (#193)
                    ws.append_event(event)
                self.workspaces[task.id] = ws
                self.task_req[task.id] = task.request_id
            assert ws is not None
            if task.meta.get("kind") == "facilities_fix":
                started = preclean_started or time.time()
                proposal = task.meta.get("facilities_fix")
                await ws.aupdate_run(task.id, started_at=started, kind="facilities_fix",
                                     fix_id=proposal.get("fix_id") if isinstance(proposal, dict) else None)
                record = preclean_record or await facility_fixes.execute(proposal, ws.dir)
                result = TaskResult(task_id=task.id, agent_id=agent.id, ok=bool(record.get("ok")),
                                    text=record.get("action") or "", cost_usd=0.0, cost_known=True,
                                    error=record.get("error"), facilities_fix=record,
                                    workdir=str(ws.dir), workdir_id=ws.dir.name)
                await ws.aupdate_run(task.id, ended_at=time.time(), ok=result.ok, error=result.error,
                                     cost_usd=0.0, cost_known=True, facilities_fix=record)
                result.provenance = ws.provenance()
                status = "완료" if result.ok else "실패"
                await emit("agent.log", {"level": "info" if result.ok else "alert",
                                         "text": f"환경 수정 {status}: {record.get('action') or record.get('error') or ''}"})
                await emit_final({"state": "done" if result.ok else "error",
                                  **({"error": short(result.error, 200)} if result.error else {})},
                                 result.model_dump(mode="json"))
                return result
            extra_dirs = [str(self.s.path(d)) for d in [*agent.project_dirs, *task.meta.get("project_dirs", [])]]
            task, consult_error = self._stage_consult_refs(task, ws)
            if agent.id == "facilities" and not consult_error:
                task = task.model_copy(update={"context": task.context + "\n\n" + self._facilities_doctor_context()})
            if bulk_hint := await self._bulk_data_hint(emit, [*zones, *internal_zones]):
                task = task.model_copy(update={"context": (task.context + "\n\n" if task.context else "") + bulk_hint})
            ws.task = task
            if consult_error:
                result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=consult_error)
                await emit("agent.log", {"level": "alert", "text": consult_error})
                await emit_final({"state": "error", "error": short(consult_error, 200)},
                                 result.model_dump(mode="json"))
                return result
            # Every folder opened to the task is judged by the same zone rule (intake.overlaps_zone) (#132).
            denied_links, _incomplete = await self._project_links(extra_dirs, zones, emit)
            unruled = [link for link in denied_links if not claude_rule_ready(link)]
            denied_links = [link for link in denied_links if link not in unruled]
            if unruled and agent.engine == Engine.claude_code:
                # Claude reads an --add-dir folder without asking the gate; a zone link there with no deny rule
                # would be read unchecked, so the task is refused instead of failing on the rule (#177).
                folders = ", ".join(dict.fromkeys(Path(d).name or "(공유 폴더 루트)" for d in extra_dirs
                                                  if any(Path(link).is_relative_to(Path(d)) for link in unruled)))
                error = (f"프로젝트 폴더 {folders}의 통제 구역 링크 {len(unruled)}개에 Claude 거부 규칙을 붙일 수 없어"
                         "(네트워크(UNC) 경로) 실행을 거부합니다. 드라이브 경로로 설정하거나 링크를 옮기세요")
                result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=error)
                await emit("agent.log", {"level": "alert", "text": error})
                await emit_final({"state": "error", "error": short(error, 200)}, result.model_dump(mode="json"))
                return result
            root = self.ws_root.resolve()
            artifact_contract = (task.meta.get("kind") in {"step", "result_correction", "slot_repair"} and
                                 isinstance(task.meta.get("upstream_artifacts"), dict))
            staged_inputs: list[str] = []
            for directory in ([] if artifact_contract else task.meta.get("upstream_dirs", [])):
                upstream = Path(directory).resolve()
                if upstream.is_dir() and upstream.is_relative_to(root):
                    reason = await asyncio.to_thread(self._upstream_refusal, upstream, zones)
                    if reason:
                        await emit("agent.log", {"level": "warn", "text": f"이전 단계 폴더 제외: {upstream.name} ({reason})"})
                        continue
                    extra_dirs.append(str(upstream))
            upstream_steps = task.meta.get("upstream_steps")
            if artifact_contract:
                artifacts = task.meta["upstream_artifacts"]
                steps = upstream_steps if isinstance(upstream_steps, dict) else {}
                accepted = {sid: row for sid, row in artifacts.items()
                            if isinstance(row, dict) and row.get("workdir") == steps.get(sid)}
                input_budget = [self.s.runner.input_hash_max_total_bytes]  # one cumulative limit for every input copy
                private_now = (await asyncio.to_thread(resolve_private_paths, self.s, [], cwd=ws.dir)).paths
                staged_inputs, unavailable = await asyncio.to_thread(
                    stage_inputs, ws, accepted, root, [*zones, *internal_zones],
                    list(private_now),
                    self.s.runner.input_hash_max_file_bytes, self.s.runner.input_hash_max_total_bytes, input_budget)
                unavailable.update({str(sid): f"inputs/{sid} unavailable (ancestor mapping mismatch)"
                                    for sid in artifacts if sid not in accepted})
                note = ("Read collected ancestor artifacts only through inputs/<step_id>/. "
                        "These copies are read-only; do not read sibling workspaces. "
                        "Save scripts and intermediate files only in outputs/ or .tmp/. "
                        "If an input is unavailable, ask the CSO; there is no direct-path fallback.")
                if unavailable:
                    note += "\n" + "\n".join(unavailable.values())
                    for reason in unavailable.values():
                        await emit("agent.log", {"level": "warn", "text": reason})
                # The precedent search's stored full texts, as read-only inputs of every research step (#951).
                precedent_rows = task.meta.get("precedent_texts")
                if isinstance(precedent_rows, list) and precedent_rows:
                    precedent_dirs, precedent_refused, precedent_listing = await asyncio.to_thread(
                        stage_precedent_texts, ws, precedent_rows, root, [*zones, *internal_zones],
                        self.s.runner.input_hash_max_file_bytes, self.s.runner.input_hash_max_total_bytes,
                        list(artifacts), input_budget)
                    staged_inputs = [*staged_inputs, *precedent_dirs]
                    precedent_note = precedent_step_note(precedent_listing, precedent_refused)
                    if precedent_note:
                        note += "\n" + precedent_note
                    for reason in precedent_refused.values():
                        await emit("agent.log", {"level": "warn", "text": reason})
                task = ws.task = task.model_copy(update={"context": task.context + "\n\n" + note})
            elif isinstance(upstream_steps, dict) and upstream_steps and not read_only:
                # The step prompt names upstream files as inputs/<step id>/... (#423); link the folders opened above.
                links: dict[str, Path] = {}
                unlinked: dict[str, str] = {}
                for step_id, directory in upstream_steps.items():
                    upstream = Path(str(directory)).resolve()
                    if self.s.hpc.submit_prefix:
                        # Shared-account jobs run from hpc_out/ as another account, and their submit check refuses
                        # every link in the folder: no link, the path below instead (PR #429 review).
                        unlinked[str(step_id)] = "shared-account HPC mode (hpc.submit_prefix) uses no links"
                    elif not INPUT_STEP_ID.fullmatch(str(step_id)):
                        unlinked[str(step_id)] = "the step id cannot name a folder"
                    elif str(upstream) in extra_dirs and (upstream / "outputs").is_dir():
                        links[str(step_id)] = upstream / "outputs"
                    else:
                        unlinked[str(step_id)] = "the folder is not opened to this step"
                unlinked.update(await asyncio.to_thread(ws.link_inputs, links))
                if unlinked:
                    for step_id, reason in unlinked.items():
                        await emit("agent.log", {"level": "warn", "text": f"inputs/{step_id} 링크 실패: {reason}"})
                    note = "\n".join(
                        f"- inputs/{step_id} is not available ({reason}); read its files at "
                        f"{Path(str(upstream_steps[step_id])) / 'outputs'} instead"
                        for step_id, reason in unlinked.items())
                    task = ws.task = task.model_copy(update={"context": (
                        f"{task.context}\n\n[Upstream inputs not linked]\n{note}")})
            # Reference paths are readable but never write roots: not in LABHQ_EXTRA_ROOTS, Claude denies edits.
            # Every folder this run may write: a read-only run writes only its own workspace.
            self._start_disk_watch(task, agent, workspace_dir, () if read_only else extra_dirs, read_only=read_only)
            read_dirs, skipped, refused = self._reference_dirs(task, [str(ws.dir), *extra_dirs],
                                                               needs_rules=agent.engine == Engine.claude_code)
            read_dirs, environment_env, environment_note = self._apply_environment_grants(
                task, read_dirs, read_only=read_only)
            if environment_note:
                task = ws.task = task.model_copy(update={"context": f"{task.context}\n\n{environment_note}".strip()})
            asked_links, _internal_incomplete = await self._project_links(
                [str(ws.dir), *extra_dirs, *read_dirs], internal_zones, emit, ask=True)
            asked_links = [link for link in asked_links if claude_rule_ready(link)]
            for note in skipped:
                await emit("agent.log", {"level": "warn", "text": f"참고 경로 제외: {note}"})
            kept = [str(r) for r in task.meta.get("reference_dirs") or [] if str(r) not in refused]
            if refused or any(r.startswith("~") for r in kept):
                # Before TASK.md is written: a refused reference must not stay named in the prompt, and a `~`
                # reference is shown as this runner's account opens it (#124).
                def rewrite(text: str) -> str:
                    return expand_home_references(withhold_reference_paths(text, refused, kept), kept)

                task = ws.task = task.model_copy(update={"prompt": rewrite(task.prompt),
                                                         "context": rewrite(task.context)})
            # Reference tables the prompt names by file (topic checklists, #420), written before TASK.md.
            for name in await ws.awrite_reference_files(task.meta.get("workspace_files")):
                await emit("agent.log", {"level": "warn", "text": f"참고 파일 거부: {name}"})
            if task.meta.get("paper_skills"):
                plugin, skill_warnings = ws.install_paper_skills(task.meta["paper_skills"], self.s.runner.id)
                if plugin:
                    agent = agent.model_copy(update={"allow_skills": True,
                                                     "plugin_dirs": [*agent.plugin_dirs, str(plugin)]})
                for note in skill_warnings:
                    await emit("agent.log", {"level": "warn", "text": note})
                task = ws.task = task.model_copy(update={"context": task.context + "\n\n" + (
                    ws.paper_skill_instructions()
                    if plugin else "논문 스킬 장착 불가; 제공된 선행 연구 요약으로 계속하세요.")})
            prompt = await ws.awrite_task_md()
            if skill_error := await _settled_thread(install_required_skills, ws, agent):
                result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=skill_error)
                await emit_final({"state": "error", "error": skill_error}, result.model_dump(mode="json"))
                return result
            if agent.contract and agent.contract.skill_dir:
                skill_error = ws.install_skill(Path(agent.contract.skill_dir))
                if skill_error:
                    result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=skill_error)
                    await emit_final({"state": "error", "error": skill_error}, result.model_dump(mode="json"))
                    return result
            for directory in read_dirs:
                # Pre-approved shell commands (e.g. Bash(python *)) are not sandboxed; only OS permissions
                # make a reference truly read-only. Say so once per directory instead of implying a guarantee.
                if directory not in self.reference_write_warned and os.access(directory, os.W_OK):
                    self.reference_write_warned.add(directory)
                    await emit("agent.log", {"level": "warn", "text": (
                        f"참고 경로가 러너 계정에 쓰기 가능합니다: {directory}. labhq는 쓰기 권한을 주지 않지만 "
                        "미리 허용된 셸 명령은 막지 못하니 OS 권한으로 읽기 전용으로 두세요")})
            watch, notes = self._read_only_watch(task, ws, [*extra_dirs, *read_dirs]) if read_only else (None, [])
            for note in notes:
                await emit("agent.log", {"level": "warn", "text": f"읽기 전용 쓰기 확인에서 제외: {note}"})
            try:  # staff read a copy without the gateway tokens, never the PI's file (#149 결정 16)
                staff_config = write_staff_config(self.s, self.s.path(self.s.runner.state_dir) / "staff")
            except (OSError, ValueError, yaml.YAMLError) as exc:
                error = f"직원용 설정 사본을 만들 수 없어 실행을 거부합니다 ({type(exc).__name__})"
                result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=error)
                await emit_final({"state": "error", "error": error}, result.model_dump(mode="json"))
                return result
            # Explicit references retain their policy exception; managed roots stay private.
            keep = [ws.dir, self.ws_root, *extra_dirs, *read_dirs,
                    *(self.s.path(r) for r in self.s.runner.reference_roots),
                    *(self.s.path(p.local_dir) for p in self.s.projects if p.local_dir)]
            # Loading code never exempts its containing config/login folder from protection.
            open_reads = plugin_keep_dirs(self.s, agent.plugin_dirs, ws.dir)
            # Only references that passed the runner's zone/link/scan checks get a shell read grant.
            open_reads.extend(read_dirs)
            open_reads.extend(staged_inputs)
            paper_inputs = ws.paper_skill_read_files() if task.meta.get('paper_skills') else []
            open_reads.extend(paper_inputs)
            if task.meta.get("kind") == "recruit" or (
                    task.meta.get("kind") == "paper_skill" and agent.id == self.s.recruit.agent_id):
                from ..recruit.paper2agent import skill_path
                if agent.engine in (Engine.claude_code, Engine.codex):
                    skill = skill_path(agent.engine.value, self.s)
                    open_reads.append(str(skill))
            # A Claude task reads back the long tool output Claude saved in the staff config folder (#298 ⑤).
            if agent.engine == Engine.claude_code:
                open_reads.extend(staff_claude_project_dirs(self.s, ws.dir))
            private = await asyncio.to_thread(resolve_private_paths, self.s, keep, open_reads=open_reads, cwd=ws.dir)
            if artifact_contract:
                # Reuse the existing private/read-root machinery; inputs never become write roots.
                private = replace(private, paths=tuple(dict.fromkeys([
                                      *private.paths, str(root), str(ws.dir / "inputs"),
                                      # Both spellings: with a junctioned root the real inputs path is otherwise
                                      # not denied and matches the resolved workdir's write allow (#946).
                                      os.path.realpath(ws.dir / "inputs")])),
                                  labels=(*private.labels, "선행 단계 읽기 전용 입력"), enabled=True,
                                  open_reads=tuple(scoped_open_reads(self.s, [
                                      *private.open_reads, str(ws.dir), *staged_inputs], cwd=ws.dir)))
            gated_read_roots: list[str] = []
            non_public_zones = [*zones, *internal_zones]
            for root_path in dict.fromkeys(map(str, [ws.dir, *extra_dirs, *read_dirs, *private.open_reads])):
                try:
                    resolved_root = Path(os.path.expandvars(os.path.expanduser(root_path))).resolve()
                except (OSError, RuntimeError, ValueError):
                    continue
                if overlaps_zone(resolved_root, non_public_zones):
                    gated_read_roots.append(root_path)
            for label in private.skipped:
                if label not in self.private_skip_warned:
                    self.private_skip_warned.add(label)
                    await emit("agent.log", {"level": "warn", "text": (
                        f"개인 경로 차단에서 제외: {label} (작업에 쓰는 폴더를 포함합니다). labhq doctor의 private paths를 보세요")})
            # With private paths active no shell rule is pre-approved; without the gate Claude refuses every command.
            if (private.enabled and agent.engine == Engine.claude_code and "approval" not in agent.builtin_mcp
                    and any(rule_tool(t) in SHELL_TOOLS for t in agent.tools)
                    and agent.id not in self.private_shell_warned):
                self.private_shell_warned.add(agent.id)
                await emit("agent.log", {"level": "warn", "text": (
                    f"{agent.id}: 개인 경로 차단 중에는 셸 명령을 미리 허용하지 않고 승인 게이트로 보냅니다. "
                    "builtin_mcp에 approval이 없어 셸 명령이 모두 거부됩니다")})
            broker_token = self.broker.issue_task_token(task.id, agent.id, task.request_id, workdir=str(ws.dir),
                                                        restricted=restricted_task(task, self.s))
            env = {
                "LABHQ_BROKER_URL": self.broker.url, "LABHQ_BROKER_TOKEN": broker_token,
                "LABHQ_TASK_ID": task.id, "LABHQ_AGENT_ID": agent.id, "LABHQ_WORKDIR": str(ws.dir),
                "LABHQ_READ_ONLY": "1" if read_only else "0",
                "LABHQ_ENVIRONMENT_STEP": "1" if task.meta.get("environment_step") is True else "0",
                "LABHQ_SHARED_ENVIRONMENT_PROTECTED": (
                    "1" if task.meta.get("shared_environment_protected") is True else "0"),
                "LABHQ_EXTRA_ROOTS": os.pathsep.join(extra_dirs),
                PRIVATE_PATHS_ENV: os.pathsep.join(private.paths),
                PRIVATE_ENABLED_ENV: "1" if private.enabled else "0",
                PRIVATE_OPEN_READS_ENV: os.pathsep.join(private.open_reads),
            }
            env.update(environment_env)
            if staff_config:
                env["LABHQ_CONFIG"] = staff_config
            if self.broker.lookup_admission is not None:
                # labhq_annot asks the broker before every tool call even if its config copy were missing (#858).
                env[LOOKUP_ADMISSION_ENV] = "1"
            if hooked:
                env[CONTROL_ENV] = str(self.controls.path(task.id))
            env.update(await _settled_thread(self._system_ca_env, agent))  # reads the OS certificate store, writes its copy (#606)
            output_before: dict[str, tuple] | None = None
            output_records: list[dict[str, Any]] = []
            observed_outputs: list[dict[str, Any]] = []
            output_scan_notes: list[str] = []
            # A declared step artifact is a future ancestor input. Hash through the same admission cap;
            # Steps without declared artifacts retain their configured observation/output cap.
            artifact_hash_limit = self.s.runner.output_hash_max_bytes
            if task.meta.get('kind') == 'step' and task.meta.get('outputs'):
                artifact_hash_limit = max(artifact_hash_limit, min(
                    self.s.runner.input_hash_max_file_bytes, self.s.runner.input_hash_max_total_bytes))

            def before_spawn() -> str | None:
                """Take both baselines after adapter preparation, immediately before its CLI spawn."""
                nonlocal output_before
                refused = watch.take_baseline() if watch else None
                records, note = ws.scan_output_records(
                    zones, self.s.runner.reference_scan_max_entries, self.s.runner.reference_scan_max_depth)
                output_before = {row["path"]: output_identity(row) for row in records}
                if note:
                    output_scan_notes.append(note)
                return refused

            async def collect_observed_outputs(*, stage: bool = True) -> None:
                """Hash the post-run view off the event loop, before labhq writes RESULT files. The record rides in the
                step's final write (`stage_run`, #1137); a step that is being cut short writes it now."""
                nonlocal output_records, observed_outputs
                record = ws.stage_run if stage else ws.aupdate_run
                if output_before is None:
                    records, _note = await asyncio.to_thread(
                        ws.scan_output_records, zones, self.s.runner.reference_scan_max_entries,
                        self.s.runner.reference_scan_max_depth, artifact_hash_limit)
                    await _maybe_await(record(task.id, environment_record=environment_record(records)))
                    return
                output_records, note = await asyncio.to_thread(
                    ws.scan_output_records, zones, self.s.runner.reference_scan_max_entries,
                    self.s.runner.reference_scan_max_depth, artifact_hash_limit)
                if note:
                    output_scan_notes.append(note)
                observed_at_ns = time.time_ns()
                tool_uses = ws.run_tool_uses(task.id) if agent.engine == Engine.claude_code else []
                observed_outputs = [
                    {**{k: v for k, v in row.items() if k not in IDENTITY_ONLY},
                     "task_id": task.id, "agent_id": agent.id,
                     "tool_use_id": nearest_tool_use_id(row.get("mtime_ns"), tool_uses, observed_at_ns)}
                    for row in output_records
                    if output_before.get(row["path"]) != output_identity(row)
                ]
                incomplete = "; ".join(dict.fromkeys(output_scan_notes)) or None
                await _maybe_await(record(task.id, observed_outputs=observed_outputs,
                                          environment_record=environment_record(output_records),
                                          **({"observed_outputs_incomplete": incomplete} if incomplete else {})))

            engine_settings = claude_deny_private(claude_ask_internal_links(claude_deny_links(claude_read_only(
                claude_settings(self.s.policy), [d for d in [*read_dirs, *(extra_dirs if read_only else [])]
                                               if claude_rule_ready(d)]), denied_links), asked_links),
                private.paths, open_reads=private.open_reads, write_roots=[str(ws.dir)])
            if hooked:
                from ..hooks.tool_use import add_claude_hook

                engine_settings = add_control_hooks(add_claude_hook(engine_settings))
            ctx = RunContext(
                task=task, agent=agent, workdir=ws.dir, settings=self.s,
                # Read-only staff get only Claude's scoped permission gate; no ask/HPC or staff MCP servers.
                mcp_servers=self._mcp_servers(agent, env, allow_ask=not read_only),
                env={**env, "MCP_TOOL_TIMEOUT": str(labhq_mcp_timeout_s(self.s) * 1000)},
                emit=emit, prompt=prompt, prompt_pointer=ws.prompt_pointer, extra_dirs=extra_dirs,
                read_dirs=read_dirs, read_only_inputs=([*staged_inputs, *paper_inputs] or
                    ([str(ws.dir / 'inputs')] if os.path.lexists(ws.dir / 'inputs') else [])),
                # Other engines never read these rules; only paths a rule can name go in (#177).
                claude_settings=engine_settings,
                private_labels=list(private.labels),
                private_paths=list(private.paths),
                private_enabled=private.enabled,
                private_open_reads=list(private.open_reads),
                denied_links=list(denied_links),
                asked_links=list(asked_links),
                gate_internal_reads=bool(internal_zones),
                gated_read_roots=gated_read_roots,
                shared_environment_protected=task.meta.get("shared_environment_protected") is True,
                environment_step=task.meta.get("environment_step") is True,
                gate_non_public_data=("approval" in agent.builtin_mcp and
                                      any(z.level in {"internal", "restricted"}
                                          for z in self.s.policy.data_zones)),
                use_permission_tool="approval" in agent.builtin_mcp,
                record_run=lambda **fields: ws.update_run(task.id, **fields),
                resume_baseline=self._resume_baseline(task, agent, ws),
                read_only=read_only,
                # Again after prepare(): the adapter's own files (Codex AGENTS.md) are not the agent's writes.
                before_spawn=before_spawn,
                halt=self.controls.halt_event(task.id),
                halt_grace_s=HALT_GRACE_S if hooked else 0.0,
                on_started=lambda: self.controls.started(task.id),
                on_cleanup=lambda evidence: self._cleanup_reports.__setitem__(task.id, evidence),
                bench_observer=getattr(self, "_bench_observer", None),
            )
            # External folders exposed to this step are its reproducibility inputs. Upstream outputs use the same
            # inputs/<step> spelling the bundle restores; other runner paths use ~/... under the runner account.
            upstream_roots: dict[str, tuple[Path, str]] = {}
            upstream_steps = task.meta.get("upstream_steps")
            upstream_steps = upstream_steps if isinstance(upstream_steps, dict) else {}
            for step_id, directory in upstream_steps.items():
                if not INPUT_STEP_ID.fullmatch(str(step_id)):
                    continue
                try:
                    upstream_roots[os.path.normcase(str(Path(str(directory)).resolve()))] = (
                        Path(str(directory)).resolve() / "outputs", f"inputs/{step_id}")
                except (OSError, RuntimeError, ValueError):
                    continue
            input_roots: list[tuple[Path, str]] = []
            input_roots.extend((Path(directory), "inputs/" + Path(directory).name)
                               for directory in staged_inputs)
            own_dir = os.path.normcase(str(ws.dir.resolve()))
            for directory in [*extra_dirs, *read_dirs]:
                try:
                    path = Path(directory).resolve()
                except (OSError, RuntimeError, ValueError):
                    path = Path(directory)
                # A follow-up that reuses this workspace lists it among its folders; the step's own files
                # (manifest, events, RESULT) are not inputs and change during every run.
                if os.path.normcase(str(path)) == own_dir or os.path.normcase(str(path)).startswith(own_dir + os.sep):
                    continue
                upstream = upstream_roots.get(os.path.normcase(str(path)))
                input_roots.append(upstream or (path, portable_input_path(path)))
            hash_private = await asyncio.to_thread(resolve_private_paths, self.s, [], cwd=ws.dir)

            async def scan_inputs() -> tuple[list[dict[str, Any]], list[str]]:
                return await asyncio.to_thread(
                    ws.scan_input_records, input_roots, zones, list(hash_private.paths),
                    self.s.runner.reference_scan_max_entries, self.s.runner.reference_scan_max_depth,
                    self.s.runner.input_hash_max_file_bytes, self.s.runner.input_hash_max_total_bytes,
                    self.input_hash_cache, [self.ws_root.resolve()])

            # Hash before the CLI starts (PR #442 review): writable inputs (project and upstream folders) may change.
            input_files, input_notes = await scan_inputs()
            from ..research.input_proof import capture as capture_reuse_inputs
            async def reuse_inputs():
                descriptor = task.meta.get('reuse_input_request')
                if not isinstance(descriptor, dict):
                    return None
                return await asyncio.to_thread(capture_reuse_inputs, descriptor, self.s,
                                               task.meta.get('reuse_input_meta'))
            reuse_before = await reuse_inputs()

            async def wait_ready() -> bool:
                """Refresh inputs after preparation/pause, before the adapter's spawn baseline."""
                nonlocal input_files, input_notes, reuse_before
                while await self.controls.wait_ready(task.id):
                    input_files, input_notes = await scan_inputs()
                    fresh = await reuse_inputs()
                    if fresh != reuse_before:
                        reuse_before = None  # preparation/pause drift is not an unchanged execution
                    # Hashing yields to the control loop. A new pause needs another wait and fresh scan.
                    live = self.controls.live.get(task.id)
                    if live is None or live.resumed.is_set():
                        return live is None or not live.halt.is_set()
                return False

            ctx.wait_ready = wait_ready
            await ws.aupdate_run(task.id, started_at=time.time(), runner_id=self.s.runner.id,
                                 request_id=task.request_id, step_id=task.meta.get('step_id'),
                                 engine=agent.engine.value, model=agent.model,
                                 engine_cli_version=(self.engine_versions or {}).get(agent.engine.value),
                                 resume_of=task.resume_session_id, kind=task.meta.get("kind"),
                                 **({"reference_dirs": read_dirs} if read_dirs else {}))
            self.active_roots[task.id] = (read_only, [ws.dir.resolve(), *(Path(d).resolve() for d in extra_dirs)])
            self.tool_contexts[task.id] = ctx
            try:
                refused = watch.take_baseline() if watch else None
                if refused:  # fail closed: a run whose writes cannot be checked does not start
                    result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=refused)
                else:
                    adapter = get_adapter(agent.engine, self.s)
                    try:
                        from .structured_output import run_with_json_retries
                        result = await run_with_json_retries(adapter, ctx)
                    except BaseException:  # cancelled or crashed after the CLI was stopped: still compare
                        # Keep what the failed step read; the after-run comparison is skipped (PR #442 review).
                        await ws.aupdate_run(task.id, input_files=input_files,
                                             **({"input_files_incomplete": "; ".join(input_notes)}
                                                if input_notes else {}))
                        await collect_observed_outputs(stage=False)
                        if watch and watch.baseline is not None:
                            await self._read_only_verdict(TaskResult(task_id=task.id, agent_id=agent.id, ok=False),
                                                          watch, emit, ws, task.id)
                        raise
                    result = self.controls.halted(task.id, result)
                    await collect_observed_outputs()
                    if watch and watch.baseline is not None:
                        result = await self._read_only_verdict(result, watch, emit, ws, task.id)
                    staff_env = getattr(adapter, "staff_env", None)
                    if agent.engine == Engine.codex and staff_env is not None:
                        try:
                            await self.codex_sandbox.after_run(self.s, ctx, staff_env(ctx), result, emit)
                        except OSError:
                            log.warning("codex sandbox record failed", exc_info=True)
                result.pending_asks = self.broker.pending_for_task(task.id)
                outcome = read_ask_results(self.broker.task_ask_results.get(task.id, []))
                if outcome["status"] == "rejected":
                    result = rejected_step(result, outcome["reason"])
            finally:
                self.tool_contexts.pop(task.id, None)
                ended = self.active_roots.pop(task.id, None)
                # A submit command already running for this run lands in pending_jobs below; later ones are refused.
                if in_flight := list(self.submitting.pop(task.id, ())):
                    await asyncio.gather(*in_flight)
                now = time.time()
                if ended is not None:
                    self.ended_roots.append((now, task.id, *ended))
                horizon = now - self.s.runner.task_timeout_s - 60  # no read-only run is older than this
                self.ended_roots = [entry for entry in self.ended_roots if entry[0] >= horizon]
                self.broker.revoke_task_token(broker_token)
                self.broker.finish_task(task.id)
                if staff_config:  # this task's own copy; the next task gets a fresh one
                    with contextlib.suppress(OSError):
                        os.unlink(staff_config)

        # Every job this run submitted, finished or not: _poll_jobs sends jobs.finished for them only after the run
        # ends, and a job the watcher saw finish while the agent still talked would otherwise never wake it (#281).
        pending = [jid for jid, j in self.jobs.items() if j["task_id"] == task.id]
        for jid in pending:
            self.jobs[jid].update(session_id=result.session_id, workdir=str(ws.dir))
            self.store.put("job", jid, self.jobs[jid])
        result.pending_jobs, result.workdir = pending, str(ws.dir)
        result.workdir_id = ws.dir.name
        # The record keeps what the step read: hashes taken before the run. A writable input the step changed gets
        # changed_during_step, and its hash stays the one from before (PR #442 review).
        after_files, after_notes = await scan_inputs()
        reuse_after = await reuse_inputs()
        result.request_input_sha256 = (reuse_before['sha256']
            if reuse_before is not None and reuse_after == reuse_before else None)
        after = {row["path"]: row.get("sha256") for row in after_files}
        for row in input_files:
            if row.get("sha256") and after.get(row["path"]) != row["sha256"]:
                row["changed_during_step"] = True
        changed = sum(bool(row.get("changed_during_step")) for row in input_files)
        if changed:
            input_notes.append(f"입력 파일 {changed}개가 단계 실행 중에 바뀌었습니다(기록된 hash는 실행 전 내용)")
        input_notes = list(dict.fromkeys([*input_notes, *after_notes]))
        # Staged: the step's final write, right before its result is sent, carries it (#1137).
        ws.stage_run(task.id, input_files=input_files,
                     **({"input_files_incomplete": "; ".join(input_notes)} if input_notes else {}))
        for note in input_notes:
            await emit("agent.log", {"level": "warn", "text": note})
        declared = task.meta.get("outputs", [])
        required_declared = task.meta.get("required_outputs", declared)
        optional_declared = task.meta.get("optional_outputs", [])
        found = []
        declared_dirs = []
        for name in declared:
            relative = output_relpath(name)
            if relative is None:
                continue
            target = (ws.dir / relative).resolve()
            if target.exists() and target.is_relative_to((ws.dir / "outputs").resolve()):
                (declared_dirs if target.is_dir() else found).append(relative)
        listed_outputs: list[str] | None = None
        output_scan_note: str | None = None
        if task.meta.get("kind") == "direct" or task.meta.get("collect_direct_outputs") is True:
            # No plan declares a direct run's outputs: its folder is listed, so the shadow sees them too (#221).
            listed_outputs, output_scan_note = await asyncio.to_thread(
                ws.scan_outputs, zones, self.s.runner.reference_scan_max_entries,
                self.s.runner.reference_scan_max_depth)
            found += listed_outputs
            if output_scan_note:
                await emit("agent.log", {"level": "warn", "text": output_scan_note})
        # A declared folder is collected as its files, each with the hash recorded after the run (#1058): a folder entry
        # has no hash, and a later step refuses an ancestor whose collected list holds one. A file that was not hashed
        # (too large, a link) is not collected; it stays unreported.
        for folder in declared_dirs:
            prefix = folder.rstrip("/") + "/"
            found += [row["path"] for row in output_records
                      if row["path"].startswith(prefix) and isinstance(row.get("sha256"), str)]
        result.outputs = list(dict.fromkeys([*result.outputs, *found]))
        hashed = {row["path"]: row["sha256"] for row in output_records
                  if isinstance(row.get("sha256"), str) and row["path"] in result.outputs}
        result.output_sha256 = hashed
        result.output_tool_use_ids = {row["path"]: row["tool_use_id"] for row in observed_outputs
                                      if isinstance(row.get("tool_use_id"), str)}
        collected = set(result.outputs)
        if _should_check_declared_outputs(result):
            def has_output(rel: str) -> bool:
                # A declared folder is there when a file with a hash is under it: a later step stages only those.
                return rel in collected or any(path.startswith(rel.rstrip("/") + "/") for path in result.output_sha256)

            missing_required = [rel for name in required_declared
                                if (rel := output_relpath(name)) and not has_output(rel)]
            missing_optional = [rel for name in optional_declared
                                if (rel := output_relpath(name)) and not has_output(rel)]
            if missing_required:
                result.ok = False
                result.missing_outputs = list(dict.fromkeys([*result.missing_outputs, *missing_required]))
                result.error = "incomplete: missing outputs: " + ", ".join(result.missing_outputs)
            if declared_dirs and result.ok and output_scan_notes:
                # The bounded walk of outputs/ skipped something (too deep, too many entries, unreadable). That only
                # matters for a declared folder the walk did not see whole: each folder is walked on its own, and an
                # unrelated subtree or a note from an earlier scan does not fail it (#1058, #1065).
                seen = {row["path"] for row in output_records}
                folder_problems = []
                for folder in declared_dirs:
                    paths, why = await asyncio.to_thread(
                        folder_files, ws.dir, folder, self.s.runner.reference_scan_max_entries,
                        self.s.runner.reference_scan_max_depth)
                    if why:
                        folder_problems.append(f"{folder}: {why}")
                    elif unseen := [path for path in paths if path not in seen]:
                        folder_problems.append(f"{folder}: the scan did not record {len(unseen)} file(s), "
                                               f"for example {unseen[0]}")
                if folder_problems:
                    result.ok = False
                    result.error = ("incomplete: the outputs scan is incomplete, so declared folder(s) "
                                    f"{', '.join(declared_dirs)} cannot be collected whole: "
                                    + "; ".join(folder_problems))
            if declared_dirs and result.ok:
                # A later step stages at most this many files and bytes of ONE ancestor, folder files and plain
                # declared files together, and refuses the whole ancestor otherwise (#1058): the producer must not
                # pass what no later step can take.
                sizes = {row["path"]: int(row.get("size") or 0) for row in output_records}
                linked = set(await asyncio.to_thread(linked_files, ws.dir, result.outputs))
                problem = staging_problem(result.outputs, result.output_sha256, sizes,
                                          self.s.runner.input_hash_max_file_bytes,
                                          self.s.runner.input_hash_max_total_bytes, linked)
                if problem:
                    result.ok = False
                    result.error = (f"incomplete: declared folder(s) {', '.join(declared_dirs)} make the step collect "
                                    f"outputs a later step would refuse: {problem}")
            if result.ok and not declared_dirs and task.meta.get("kind") != "direct":
                # A hard-linked output is hashed like any other, but a later step refuses it and with it the
                # whole ancestor; steps that declare a folder get the same check inside the staging check (#1065).
                # Every collected path, optional and unreported ones too: a later step stages the whole list.
                linked_declared = await asyncio.to_thread(linked_files, ws.dir, result.outputs)
                if linked_declared:
                    result.ok = False
                    result.error = ("incomplete: a later step refuses a hard-linked output: "
                                    + ", ".join(linked_declared))
            if missing_optional:
                result.missing_optional_outputs = list(dict.fromkeys([
                    *result.missing_optional_outputs, *missing_optional]))
                warning = "선택 산출 누락: " + ", ".join(result.missing_optional_outputs)
                result.step_warnings = list(dict.fromkeys([*result.step_warnings, warning]))
                await emit("agent.log", {"level": "warn", "text": warning})
        binding = task.meta.get("environment_binding")
        # Only the final turn: a step paused on a job or a question has not written its environment yet.
        if _should_check_declared_outputs(result) and isinstance(binding, dict):
            from .step_inputs import producer_environment_problem
            problem = await asyncio.to_thread(producer_environment_problem, self.ws_root, ws.dir,
                                              result.outputs, result.output_sha256, binding)
            if problem:
                result.ok = False
                result.error = "incomplete: runner environment does not match the pinned binding: " + problem
        # Bytecode caches are the interpreter's, not the employee's: 21 unreported files of the v0.5 trial included
        # outputs/lib/__pycache__ (#485). They stay in the observed records.
        result.unreported_outputs = sorted(row["path"] for row in observed_outputs
                                           if row["path"] not in collected
                                           and "__pycache__" not in row["path"].replace("\\", "/").split("/"))
        warning_outputs = unreported_warning_paths(result.unreported_outputs)
        if warning_outputs:
            await emit("agent.log", {"level": "warn", "text": (
                "보고하지 않은 관찰 산출물: " + ", ".join(warning_outputs))})
        # Read back from the runner's disk: the gateway may not share it (#697, #710). A check that breaks leaves a
        # warning; it never turns the finished task into a failure (PR #712 review).
        try:
            result.table_warnings = await asyncio.to_thread(output_checks.table_warnings, ws.dir, result.outputs)
            result.literature_access = await asyncio.to_thread(output_checks.literature_access, ws.dir)
            result.literature_access_failures = await asyncio.to_thread(
                output_checks.literature_access_failures, ws.dir)
            result.literature_ledger = await asyncio.to_thread(output_checks.literature_ledger, ws.dir)
        except Exception as exc:  # noqa: BLE001
            await emit("agent.log", {"level": "warn", "text": f"산출 검사를 하지 못했습니다: {type(exc).__name__}"})
        if result.table_warnings:
            await emit("agent.log", {"level": "warn", "text": (
                "표로 읽히지 않는 산출물: " + "; ".join(result.table_warnings))})
        # The shared lookup status reaches the result and its report warnings, not only the employee's prompt (#676).
        try:
            result.external_lookups = await asyncio.to_thread(output_checks.external_lookups, ws.dir)
        except Exception as exc:  # noqa: BLE001
            await emit("agent.log", {"level": "warn", "text": f"외부 조회 상태를 읽지 못했습니다: {type(exc).__name__}"})
        if lookup_lines := output_checks.external_lookup_lines(result.external_lookups):
            await emit("agent.log", {"level": "warn", "text": (
                f"외부 조회 상태({output_checks.LOOKUP_RECORD_NOTE}): " + "; ".join(lookup_lines))})
        for note in dict.fromkeys(output_scan_notes):
            await emit("agent.log", {"level": "warn", "text": note})
        # Only a finished, successful turn: a bundle written before HPC checks or an ask is not final (#301 review).
        if (agent.id in PIPELINE_AUTHORS and task.meta.get("pipeline_pr") is True  # the gateway asked (#300)
                and result.ok and not waiting(result)):
            if listed_outputs is None:
                listed_outputs, output_scan_note = await asyncio.to_thread(
                    ws.scan_outputs, zones, self.s.runner.reference_scan_max_entries,
                    self.s.runner.reference_scan_max_depth)
            result.pipeline_submission = await asyncio.to_thread(
                collect_pipeline_submission, ws.dir, listed_outputs, output_scan_note)
            if result.pipeline_submission is not None:
                reason = pipeline_rejection(result.pipeline_submission, self.s)
                if reason:
                    result.pipeline_submission = {"state": "rejected",
                                                  "name": result.pipeline_submission.get("name"),
                                                  "reason": reason}
        if "output_types_vocab" in task.meta:  # the gateway asked for type records (#221): collected outputs only
            try:
                type_meta = task.meta
                current_vocab = output_vocab.current()
                direct_result = (result.structured if isinstance(result.structured, dict)
                                 and (task.meta.get("kind") == "direct"
                                      or task.meta.get("collect_direct_outputs") is True) else None)
                if direct_result is not None:
                    response = direct_result.get("response")
                    if isinstance(response, str):
                        result.text = response
                    entries, issues = (output_types.normalize_entries(
                        found, direct_result.get("output_types"), current_vocab)
                        if current_vocab is not None and current_vocab.sha256 == task.meta.get("output_types_vocab")
                        else ([], {}))
                    type_meta = {
                        "output_types_vocab": task.meta.get("output_types_vocab"),
                        "output_types": output_types.meta_declarations(entries),
                        "output_types_source": "staff",
                    }
                    result.output_types_stats = {
                        **output_types.stats(found, entries, issues),
                        "vocab": task.meta.get("output_types_vocab"),
                    }
                    result.structured = {
                        "response": result.text,
                        "output_types": [{key: value for key, value in entry.items() if key != "vocab"}
                                         for entry in entries],
                    }
                result.output_types = output_types.runner_records(found, type_meta, current_vocab)
            except Exception:
                result.output_types = {}
                await emit("agent.log", {"level": "warn", "text": "output type records unavailable; result kept"})
        if paper_skill_build is not None:
            result = await asyncio.to_thread(paper_skill_build.finish, result)
        counted_tools = tool_calls.summary()
        result = result.model_copy(update={
            "tool_calls": counted_tools,
            "tool_call_turns": ({task.id: counted_tools} if counted_tools else {}),
        })
        try:
            # Never through a link the agent made (#165). Both files are one job: a cancel cannot write only one.
            # RESULT.md is the latest alias of RESULT_<task>.md, the same text: only the per-task file is fsynced. The
            # text is in task.result (the outbox) too, so a power cut can leave the alias old, empty or missing (#1154).
            await awrite_owned_many(ws.dir, [(f"outputs/RESULT_{task.id}.md", result.text or ""),
                                             ("outputs/RESULT.md", result.text or "", False)])
        except OwnedPathError:
            error = "작업 폴더의 outputs가 실행 중에 링크로 바뀌어 결과 파일을 쓰지 않았습니다"
            await emit("agent.log", {"level": "alert", "text": error})
            result = result.model_copy(update={"ok": False, "error": error})
        else:
            # RESULT.md is runner-owned and absent from the staff output scan. Check it only after all response
            # normalization and the owned write, so a retry never checks the preceding turn's response.
            try:
                result.markdown_warnings = await asyncio.to_thread(
                    output_checks.markdown_warnings, ws.dir,
                    [*result.outputs, *result.unreported_outputs, "outputs/RESULT.md"])
            except Exception as exc:  # noqa: BLE001 - keep an incomplete check visible to the terminal contract
                result.markdown_warnings = [f"Markdown 검사 실패({type(exc).__name__}). 문서를 다시 검사하세요."]
        await ws.aupdate_run(task.id, ended_at=time.time(), ok=result.ok, error=result.error,
                             cost_usd=result.cost_usd,
                             cost_known=result.cost_known if result.cost_known is not None
                             else result.cost_usd is not None,
                             usage=result.usage, usage_known=result.usage_known,
                             session_id=result.session_id, pending_jobs=pending)
        if task.meta.get("paper_skills"):
            ws.record_paper_skill_usage(result)
        result.provenance = ws.provenance()
        if task.meta.get("paper_skills"):
            ws.clear_paper_skills()  # before task.result can release a dependent step
        if tool_errors:
            result.tool_errors = [*result.tool_errors, *tool_errors]
        if not result.ok and result.quota_reset_at is None:  # only this machine knows the CLI's zone
            result.quota_reset_at = quota_reset_instant(agent.engine.value, result.error)
        self._shutdown_result(result)
        state = "hibernating" if waiting(result) else ("done" if result.ok else "error")
        extra = ({"jobs": pending, "asks": result.pending_asks} if waiting(result) else
                 ({"error": short(result.error, 200)} if result.error else {}))
        if result.error_kind == "halted":  # stopped by the PI and resumable, not a problem to show
            state, extra = "idle", {"halted": True}
        await emit_final({"state": state, **extra}, result.model_dump(mode="json"))
        return result

    # ---------------- broker callbacks (from MCP tools) ----------------
    async def _on_approval(self, req: ApprovalRequest) -> None:
        req.request_id = req.request_id or self.task_req.get(req.task_id or "")
        ws = self.workspaces.get(req.task_id or '')
        if req.kind == 'tool_permission' and ws:
            from ..pi_text import approval_copy
            from ..recruit.paper2agent import skill_path
            meta = ws.task.meta
            labels = {str(ws.dir): '이 작업의 빌드 폴더' if meta.get('kind') == 'paper_skill' else '이 작업의 폴더',
                      str(self.s.path(self.s.runner.talent_dir)): 'labhq 논문 스킬 보관 폴더',
                      str(self.s.path(self.s.runner.workspace_root)): 'labhq 작업 폴더'}
            if meta.get('kind') in ('paper_skill', 'recruit') and ws.agent.engine in (Engine.claude_code, Engine.codex):
                try:
                    labels[str(skill_path(ws.agent.engine.value, self.s))] = 'Paper2Agent 스킬 참고 자료'
                except ValueError:
                    pass  # Invalid install configuration must not prevent an approval from being delivered.
            req.agent_id = ws.task.agent_id
            from ..tool_explanation import internal_output_classification
            output_class = internal_output_classification(
                str(req.detail.get('tool_name') or ''),
                req.detail.get('tool_input') or req.detail.get('input') or {},
                self.s.policy,
                str(ws.dir),
            )
            detail = {**req.detail, 'requester_name': ws.agent.name,
                          'workdir': str(ws.dir),
                          'permission_mode': ws.agent.permission_mode,
                          'task_context': {'kind': meta.get('kind'), 'title': meta.get('title'),
                                           'step_id': meta.get('step_id'),
                                           'paper_title': (meta.get('paper_skill') or {}).get('title')},
                          'path_labels': labels}
            if output_class:
                detail['internal_output_classification'] = output_class
            else:
                detail.pop('internal_output_classification', None)
            if output_class == 'rows':
                detail['internal_row_output'] = True
            else:
                detail.pop('internal_row_output', None)
            req.detail = detail
            for key, value in approval_copy(req.kind, req.summary, req.detail).items():
                setattr(req, key, value)
            from ..tool_explanation import needs_interpretation
            req.pi_needs_interpretation = needs_interpretation(req.detail)
        self.approval_tasks[req.id] = (req.task_id, req.agent_id)
        base = dict(task_id=req.task_id, agent_id=req.agent_id, request_id=req.request_id)
        await self.emit_all([
            Event(type="approval.requested", data=req.model_dump(mode="json"), **base),
            *([Event(type="agent.status", data={"state": "waiting", "approval": req.id}, **base)]
              if req.task_id else [])])

    async def _on_approval_timeout(self, req: ApprovalRequest) -> None:
        self.approval_tasks.pop(req.id, None)
        base = dict(task_id=req.task_id, agent_id=req.agent_id, request_id=req.request_id)
        await self.emit_all([
            Event(type="approval.timed_out", data={"id": req.id}, **base),
            *([Event(type="agent.status", data={"state": "working"}, **base)] if req.task_id else [])])

    async def _on_ask(self, req: AskRequest) -> None:
        req.request_id = req.request_id or self.task_req.get(req.task_id or "")
        base = dict(task_id=req.task_id, agent_id=req.agent_id, request_id=req.request_id)
        await self.emit_all([
            Event(type="ask.requested", data=req.model_dump(mode="json"), **base),
            *([Event(type="agent.status", data={"state": "waiting", "ask": req.id}, **base)] if req.task_id else [])])

    async def _on_restricted(self, call: dict) -> None:
        """A restricted task wants to call a tool (#1008): the CSO decides at the gateway. A task the breaker has
        already released is answered at once; otherwise the call goes up with this restriction's episode, so an answer
        meant for an earlier one is never taken for this one."""
        tid = str(call.get("task_id") or "")
        ladder = self.breakers.get(tid)
        if ladder is None or ladder.policy.stage not in ("constrain", "stop"):
            self.broker.resolve_restricted(call["id"], {"approved": True, "note": "제한이 풀려 있습니다"})
            return
        name = str(call.get("tool_name") or "")
        tool_input = call.get("tool_input") if isinstance(call.get("tool_input"), dict) else {}
        await self.emit(Event(type="restricted.requested", task_id=tid, agent_id=call.get("agent_id"),
                              request_id=self.task_req.get(tid) or call.get("request_id"),
                              data={"id": call["id"], "tool_name": name,  # no input: this event is stored and replayed
                                    "fingerprint": tool_input_fingerprint(name, tool_input),
                                    "episode": ladder.episode_id, "at": time.time()}))

    async def _on_tool_event(self, body: dict) -> None:
        tid = body.get("task_id")
        await self.emit(Event(type=body.get("type", "agent.log"), task_id=tid, agent_id=body.get("agent_id"),
                              request_id=self.task_req.get(tid or ""), data=body.get("data") or {}))

    async def _on_submit(self, body: dict) -> dict:
        """hpc_submit runs here (#214 review): the id the runner's own scheduler call printed is the record."""
        tid, aid = body.get("task_id"), body.get("agent_id")
        ws, running = self.workspaces.get(tid or ""), self.active_roots.get(tid or "")
        if ws is None or running is None:
            return {"error": "this task is not running on the runner; the job was not submitted"}
        if running[0]:  # read-only tasks get a broker token too, but no HPC (read_only_profile)
            return {"error": "a read-only task cannot submit HPC jobs; the job was not submitted"}

        async def approve(payload: dict) -> dict:
            return await self.broker.request_approval(ApprovalRequest.model_validate(
                {**payload, "task_id": tid, "agent_id": aid, "request_id": body.get("request_id")}))

        @contextlib.asynccontextmanager
        async def gate():
            # Same run as the call: a task that ended (or resumed as a new run) while the PI decided gets no job.
            if self.active_roots.get(tid or "") is not running:
                yield False
                return
            done = asyncio.get_running_loop().create_future()
            self.submitting.setdefault(tid or "", set()).add(done)
            try:
                yield True  # run_task waits for this before it lists the task's pending jobs
            finally:
                self.submitting.get(tid or "", set()).discard(done)
                done.set_result(None)

        async def record(result: dict) -> None:
            await self._track_job({"task_id": tid, "agent_id": aid, "job_id": result["job_id"],
                                   "name": result["name"], "script": result["script"],
                                   "script_sha256": result["script_sha256"],
                                   "core_hours": result["core_hours"]})

        return await submit_job(self.s, self.scheduler, ws.dir, body, approve, gate, record)

    async def _track_job(self, body: dict, extra: dict | None = None) -> None:
        """Watch a job the runner submitted. No broker endpoint reaches this with a caller's id."""
        jid, tid = str(body["job_id"]), body.get("task_id")
        ws = self.workspaces.get(tid or "")
        self.jobs[jid] = {"job_id": jid, "task_id": tid, "agent_id": body.get("agent_id"),
                           "name": body.get("name", ""), "state": "queued", "missing": 0, "terminal": False,
                           "exit_status": None, "submitted_at": time.time(),
                           "scheduler": self.s.hpc.scheduler, "request_id": self.task_req.get(tid or ""),
                           "workdir": str(ws.dir) if ws else None,
                           "script_sha256": body.get("script_sha256"), "submitted_by_runner": True, **(extra or {})}
        if ws:
            ws.append_job({**body, "submitted_at": time.time()})
        # The job row and the event that announces it are one unit: a cancel cannot keep the row and lose the event.
        await self.emit_all([Event(type="job.submitted", task_id=tid, agent_id=body.get("agent_id"),
                                   request_id=self.task_req.get(tid or ""),
                                   data={"job_id": jid, "name": body.get("name"),
                                         "core_hours": body.get("core_hours")})],
                            before=[("job", jid, self.jobs[jid])])

    async def _owns_job(self, identity: dict) -> bool:
        """hpc_cancel scope (#172): a job the runner submitted for this agent in this task or request.

        A record without the runner's mark came from an older broker that stored ids a task reported (#214 review).
        """
        job_id = str(identity.get("job_id") or "")
        for tracked, j in self.jobs.items():
            if (not j.get("submitted_by_runner") or j.get("terminal") or not job_in_family(tracked, job_id)
                    or j.get("agent_id") != identity.get("agent_id")):
                continue
            if j.get("task_id") == identity.get("task_id") or (
                    identity.get("request_id") and j.get("request_id") == identity.get("request_id")):
                return True
        return False

    # ---------------- local jobs (#675) ----------------
    def _local_owned(self, identity: dict, job_id: str | None = None) -> list[tuple[str, dict]]:
        """The local job rows this runner started for this agent in this task or request; one when `job_id` names it."""
        rows = []
        for jid, j in self.jobs.items():
            if (not local_jobs.is_local(jid) or not j.get("submitted_by_runner")
                    or j.get("agent_id") != identity.get("agent_id")):
                continue
            if j.get("task_id") != identity.get("task_id") and not (
                    identity.get("request_id") and j.get("request_id") == identity.get("request_id")):
                continue
            if job_id is None or jid == job_id:
                rows.append((jid, j))
        return rows

    def _local_view(self, jid: str, j: dict, *, logs: bool = False) -> dict:
        local = j.get("local") or {}
        view = {"job_id": jid, "name": j.get("name"), "state": j.get("state"), "terminal": bool(j.get("terminal")),
                "exit_status": j.get("exit_status"),
                "elapsed_s": int(time.time() - float(j.get("submitted_at") or time.time())),
                "expected_minutes": local.get("expected_minutes"), "command": short(local.get("command"), 300)}
        if logs:
            view.update(stdout_tail=local_jobs.tail(local.get("out")), stderr_tail=local_jobs.tail(local.get("err")))
        return view

    async def _on_local_job(self, op: str, identity: dict) -> dict:
        if op == "list":
            return {"jobs": [self._local_view(jid, j) for jid, j in self._local_owned(identity)]}
        if op in {"status", "cancel"}:
            job_id = str(identity.get("job_id") or "")
            rows = self._local_owned(identity, job_id)
            if not rows:
                return {"error": f"job {job_id} was not submitted by you through job_submit; labhq tracks only your own"}
            jid, j = rows[0]
            if op == "cancel" and not j.get("terminal"):  # an ended job keeps how it ended
                if not await self._end_local_job(jid, j, "cancelled"):
                    return {"error": f"job {jid} could not be ended: some of its processes are still running"}
            return self._local_view(jid, j, logs=op == "status")
        return await self._submit_local_job(identity)

    async def _submit_local_job(self, identity: dict) -> dict:
        """Start one command as a job this runner owns: submitted, a PI or policy refusal, or an error.

        The command is judged by the approval gate's own rules here, so a call straight to the broker cannot skip it."""
        tid, aid = str(identity.get("task_id") or ""), identity.get("agent_id")
        ws, running = self.workspaces.get(tid), self.active_roots.get(tid)
        if ws is None or running is None:
            return {"error": "this task is not running on the runner; the job was not submitted"}
        if running[0]:
            return {"error": "a read-only task cannot submit jobs"}
        ctx, tool = self.tool_contexts.get(tid), local_jobs.shell_tool()
        if ctx is not None and ctx.agent.engine != Engine.claude_code:
            # A Codex run is held inside the engine's OS sandbox; a job the runner starts would run outside it.
            return {"error": "local jobs are for Claude staff: a job started by the runner runs outside the "
                             f"sandbox the {ctx.agent.engine.value} engine keeps its commands in. For a long computation "
                             "use the labhq_hpc tools if you have them, split it into parts that each finish, or ask the PI"}
        command = identity.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 20000:
            return {"error": "command must be a shell command of at most 20000 characters"}
        try:
            expected = None if identity.get("expected_minutes") is None else float(identity["expected_minutes"])
        except (TypeError, ValueError):
            return {"error": "expected_minutes must be a number"}
        if expected is not None and not 0 < expected <= 7 * 24 * 60:
            return {"error": "expected_minutes must be between 0 and 10080"}
        name = sanitize_job_name(str(identity.get("name") or "job"))
        if ctx is not None:  # the same rule the approval gate applies to an install into the shared environment
            from ..environment_install import shared_environment_install_denial

            install = shared_environment_install_denial(
                tool, {"command": command}, protected=ctx.shared_environment_protected,
                environment_step=ctx.environment_step, workdir=str(ws.dir))
            if install:
                return {"submitted": False, "reason": install}
        decision = evaluate_tool(
            tool, {"command": command}, self.s.policy,
            allowed_roots=[str(ws.dir), *(ctx.extra_dirs if ctx else [])], workdir=str(ws.dir),
            private_paths=ctx.private_paths if ctx else [], private_enabled=bool(ctx and ctx.private_enabled),
            private_open_reads=ctx.private_open_reads if ctx else [], read_only=False)
        if decision.action == "deny":
            return {"submitted": False, "reason": decision.reason}
        if decision.action == "ask":
            answer = await self.broker.request_approval(ApprovalRequest(
                task_id=tid, agent_id=aid, request_id=identity.get("request_id"), kind="tool_permission",
                summary=f"{tool}: {decision.reason}",
                detail={"tool_name": tool, "input": short({"command": command}, 1500),
                        "tool_input": {"command": command}, "policy_reason": decision.reason},
                timeout_s=self.s.policy.approvals.timeout_s))
            if not answer.get("approved"):
                return {"submitted": False, "reason": answer.get("note") or "denied by the PI"}
        done = asyncio.get_running_loop().create_future()
        self.submitting.setdefault(tid, set()).add(done)  # run_task waits for a submit in flight before it ends
        starting = self.__dict__.setdefault("_local_starting", 0)
        try:
            if self.active_roots.get(tid) is not running or tid in self.__dict__.get("_jobs_cancelled", ()):
                return {"error": "the task ended or was cancelled; the job was not submitted"}
            # The slot is reserved before the first await, so two submits cannot both pass the same count.
            busy = sum(1 for jid, j in self.jobs.items() if local_jobs.is_local(jid) and not j.get("terminal"))
            if busy + starting >= self.s.runner.max_parallel:
                return {"error": f"{self.s.runner.max_parallel} local jobs are already running on this runner; "
                                 "wait for one to finish before submitting another"}
            self._local_starting = starting + 1
            try:
                job_id = local_jobs.new_id()
                try:
                    facts, process = await asyncio.to_thread(
                        local_jobs.start, self.s.path(self.s.runner.state_dir), job_id, command, ws.dir,
                        command_env())
                except OSError as exc:
                    return {"error": f"could not start the job: {exc}"}
                if tid in self.__dict__.get("_jobs_cancelled", ()):
                    # The step was cancelled while the process was starting: no row existed for the cancel to find.
                    if await asyncio.to_thread(local_jobs.kill, facts, process):
                        return {"error": "the task was cancelled while the job started; the job was ended"}
                    # Its processes survived: keep it as a row so the watcher keeps ending it, not an ownerless process.
                    self._local_procs[job_id] = process
                    self._ensure_local_watch()
                    await self._track_job({"task_id": tid, "agent_id": aid, "job_id": job_id, "name": name,
                                           "core_hours": None},
                                          extra={"scheduler": "local", "state": "running", "cancel_requested": True,
                                                 "cancel_wake": False,
                                                 "local": {**facts, "command": command, "expected_minutes": expected}})
                    return {"error": "the task was cancelled while the job started; it could not be ended yet and "
                                     "the runner keeps trying"}
                self._local_procs[job_id] = process
                self._ensure_local_watch()
                await self._track_job({"task_id": tid, "agent_id": aid, "job_id": job_id, "name": name,
                                       "core_hours": None},
                                      extra={"scheduler": "local", "state": "running",  # no queue: it starts at once
                                             "local": {**facts, "command": command, "expected_minutes": expected}})
            finally:
                self._local_starting -= 1
            return {"submitted": True, "job_id": job_id, "name": name}
        finally:
            self.submitting.get(tid, set()).discard(done)
            done.set_result(None)

    async def _end_local_job(self, jid: str, j: dict, state: str, *, wake: bool = True) -> bool:
        """Kill a local job's process tree. Only when nothing of it is left is it recorded as ended (`wake` False keeps
        its step from being woken); a tree that survives leaves the job running and the watcher keeps checking it.
        The job counts as being ended from the first line to the last, so a status the watcher read meanwhile cannot
        write over it (#1144)."""
        ending = self.__dict__.setdefault("_ending_local_jobs", set())
        ending.add(jid)
        try:
            return await self._end_local_job_now(jid, j, state, wake=wake)
        finally:
            ending.discard(jid)

    async def _end_local_job_now(self, jid: str, j: dict, state: str, *, wake: bool) -> bool:
        tid = j.get("task_id")
        if not await asyncio.to_thread(local_jobs.kill, j.get("local") or {}, self._local_procs.get(jid)):
            # Remembered with the row (so it survives a restart): the watcher ends it again until nothing is left.
            j.update(cancel_requested=True, cancel_wake=wake)
            self.store.put("job", jid, j)
            return False
        self._local_procs.pop(jid, None)
        j.update(state=state, terminal=True, exit_status=None)
        after = [("job", jid, j)]
        if not wake and tid:
            self.notified.add(tid)
            after.append(("notified", tid, {"done": True}))
        await self.emit_all([Event(type="job.state", task_id=tid, agent_id=j.get("agent_id"),
                                   request_id=self.task_req.get(tid or ""),
                                   data={"job_id": jid, "name": j.get("name"), "state": state, "exit_status": None})],
                            after=after)
        return True

    def _ensure_local_watch(self) -> None:
        """Poll local jobs from the first one on. It is started lazily, so a runner without local jobs runs the same
        loops as before (the HPC watcher stays off with scheduler=none)."""
        if getattr(self, "_local_watch", None) is None or self._local_watch.done():
            self._local_watch = asyncio.get_running_loop().create_task(self._local_job_watch_loop())

    async def _local_job_watch_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.s.runner.job_poll_s)
            try:
                await self._poll_local_jobs()
            except Exception:
                log.exception("local job poll failed")

    async def _poll_local_jobs(self) -> None:
        if not any(local_jobs.is_local(jid) for jid in self.jobs):
            return
        ending = self.__dict__.setdefault("_ending_local_jobs", set())
        for jid, j in list(self.jobs.items()):
            if j["terminal"] or not local_jobs.is_local(jid) or jid in ending:
                continue
            if j.get("cancel_requested"):  # an earlier cancel could not end the whole tree: try again
                await self._end_local_job(jid, j, "cancelled", wake=bool(j.get("cancel_wake", True)))
                continue
            state, exit_status = await asyncio.to_thread(local_jobs.status, j.get("local") or {},
                                                         self._local_procs.get(jid))
            if j["terminal"] or j.get("cancel_requested") or jid in ending:
                # A cancel came in while the status was read: it is still ending the job, ended it, or could not end the
                # whole tree and left the job for the next poll to end again (the branch above). What was read is stale
                # (#1144).
                continue
            if state == j["state"]:
                continue
            j.update(state=state, exit_status=exit_status, terminal=state in TERMINAL)
            if j["terminal"]:
                self._local_procs.pop(jid, None)  # the poll that saw it end has reaped it
            await self.emit_all([Event(type="job.state", task_id=j["task_id"], agent_id=j["agent_id"],
                                       request_id=self.task_req.get(j["task_id"] or ""),
                                       data={"job_id": jid, "name": j["name"], "state": state,
                                             "exit_status": exit_status})],
                                after=[("job", jid, j)])
        await self._wake_finished_jobs()

    CANCEL_TOMBSTONE_KEEP_S = 2 * 86400  # the gateway keeps a cancel for as long (TASK_CANCEL_KEEP_S)

    async def _remember_cancel(self, task_id: str) -> None:
        """A cancel for a task this runner has not seen: keep it so the dispatch that follows is not started."""
        now = time.time()
        for tid, row in list(self.store.all("cancelled_task").items()):
            if now - float(row.get("at") or 0) > self.CANCEL_TOMBSTONE_KEEP_S:
                await self.store.adelete("cancelled_task", tid)
        await self.store.aput("cancelled_task", task_id, {"at": now})

    async def _cancel_local_jobs(self, task_id: str) -> bool:
        """The step was cancelled: end its running local jobs so no process outlives it, and do not wake the step.
        False when some job's processes could not be ended, so the cancel is not confirmed."""
        if not task_id:
            return True
        ended = True
        self.__dict__.setdefault("_jobs_cancelled", set()).add(task_id)  # a submit that is mid-start sees this
        for jid, j in list(getattr(self, "jobs", {}).items()):
            if local_jobs.is_local(jid) and j.get("task_id") == task_id and not j.get("terminal"):
                ended = await self._end_local_job(jid, j, "cancelled", wake=False) and ended
        return ended

    # ---------------- HPC job watcher ----------------
    async def _job_watch_loop(self) -> None:
        if self.s.hpc.scheduler == "none":
            return
        while not self._stopping:
            await asyncio.sleep(self.s.runner.job_poll_s)
            try:
                await self._poll_jobs()
            except Exception:
                log.exception("job poll failed")

    async def _poll_jobs(self) -> None:
        if self.s.hpc.scheduler == "none":
            return  # Retain saved state, missing counts and notifications; do not invent a final outcome.
        for jid, j in list(self.jobs.items()):
            if j["terminal"] or local_jobs.is_local(jid):  # a local job has its own watcher (#675)
                continue
            info = await asyncio.to_thread(self.scheduler.status, jid)
            state = info.state
            if state == "missing":  # SGE accounting lag: give it a few polls
                j["missing"] += 1
                self.store.put("job", jid, j)
                if j["missing"] < MISSING_POLLS_BEFORE_FINISHED:
                    continue
                state = "unknown_finished"
            else:
                j["missing"] = 0
            changed = state != j["state"]
            if changed:
                j.update(state=state, exit_status=info.exit_status)
            j["terminal"] = state in TERMINAL
            if changed:  # the event and the saved job state are one unit
                await self.emit_all([Event(type="job.state", task_id=j["task_id"], agent_id=j["agent_id"],
                                           request_id=self.task_req.get(j["task_id"] or ""),
                                           data={"job_id": jid, "name": j["name"], "state": state,
                                                 "exit_status": info.exit_status})],
                                    after=[("job", jid, j)])
            else:
                self.store.put("job", jid, j)
        await self._wake_finished_jobs()

    async def _wake_finished_jobs(self) -> None:
        """Wake each task whose jobs have all ended, once: the agent was waiting on them (#281)."""
        by_task: dict[str, list[dict]] = {}
        for j in self.jobs.values():
            by_task.setdefault(j["task_id"], []).append(j)
        for tid, js in by_task.items():
            if tid in self.notified or not all(x["terminal"] for x in js):
                continue
            t = self.tasks.get(tid)
            if t is not None and not t.done():  # the agent is still talking; wake it after it ends
                continue
            # The event and the "notified" mark are one unit: a cancel cannot send the wake-up and forget that it did.
            self.notified.add(tid)
            try:
                await self.emit_all([Event(
                    type="jobs.finished", task_id=tid, agent_id=js[0]["agent_id"], request_id=self.task_req.get(tid),
                    data={"jobs": [{k: x.get(k) for k in ("job_id", "name", "state", "exit_status")} for x in js],
                          "session_id": js[0].get("session_id"), "workdir": js[0].get("workdir")},
                )], after=[("notified", tid, {"done": True})])
            except Exception:
                self.notified.discard(tid)
                raise
