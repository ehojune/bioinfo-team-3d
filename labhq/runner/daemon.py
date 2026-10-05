"""The runner lives where the compute and data live (your workstation or an HPC login node).

It dials the gateway (outbound WebSocket → no inbound ports, works behind NAT/firewalls), receives
task.dispatch, runs the agent's CLI in a per-task workspace, and streams normalized events back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import websockets
import yaml

from ..adapters import (adapter_preflight_error, get_adapter, is_read_only_task, read_only_profile,
                        read_only_refusal)
from ..adapters.base import RunContext
from ..adapters.held_dir import HeldDir
from ..adapters.owned import (OwnedPathError, is_link, owned_link_error, plain_directory, read_owned,
                              remove_entry, write_owned)
from ..ask_results import read_ask_results, rejected_step
from ..login import LOGIN_PATH_ENV
from ..models import ASK_MAX_WAIT_S, AgentSpec, ApprovalRequest, AskRequest, Engine, Event, McpServerSpec, Task, TaskResult, waiting
from .breaker import Breaker, trip_text
from .versions import engine_cli_versions
from ..intake import (expand_home_references, overlaps_restricted, overlaps_zone, reference_roots,
                      scan_reference_dir, withhold_reference_paths, zone_links)
from ..policy import (SHELL_TOOLS, claude_deny_links, claude_deny_private, claude_read_only, claude_rule_path,
                      claude_settings, rule_tool)
from ..private_paths import ENABLED_ENV_VAR as PRIVATE_ENABLED_ENV, ENV_VAR as PRIVATE_PATHS_ENV, \
    OPEN_READS_ENV_VAR as PRIVATE_OPEN_READS_ENV, plugin_keep_dirs, resolve_private_paths, \
    staff_claude_project_dirs, staff_codex_homes
from ..pipeline_pr import collect_pipeline_submission, pipeline_rejection
from ..quota import quota_reset_instant
from ..registry import Registry
from ..settings import MODEL_NAME_PATTERN, Settings, write_staff_config
from ..store import StateStore
from ..tools.scheduler import MISSING_POLLS_BEFORE_FINISHED, TERMINAL, Scheduler, job_in_family
from ..util import INPUT_STEP_ID, output_relpath, short
from .. import vocab as output_vocab
from ..vocab import declare as output_types
from .approvals import Broker
from .codex_sandbox import SandboxWatch
from .hpc_jobs import submit_job
from .integrity import ReadOnlyWatch, watch_roots
from .system_ca import CA_ENV, system_ca_pem
from .workspace import TaskWorkspace, nearest_tool_use_id, restricted_zones

log = logging.getLogger("labhq.runner")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONSULT_REF_MAX_FILE_BYTES = 8 * 1024 * 1024
CONSULT_REFS_MAX_TOTAL_BYTES = 32 * 1024 * 1024
CONSULT_REF_COPY_CHUNK = 1024 * 1024


def labhq_mcp_timeout_s(settings: Settings) -> int:
    """How long a staff CLI may wait on one labhq MCP call: the longest approval or ask wait, plus 120 s for the
    tool's own work. Claude gets it as each server's `timeout` and MCP_TOOL_TIMEOUT, Codex as `tool_timeout_sec`."""
    return max(settings.policy.approvals.timeout_s, ASK_MAX_WAIT_S) + 120


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


class Runner:
    def __init__(self, settings: Settings):
        self.s = settings
        self.store = StateStore(settings.path(settings.runner.state_dir) / f"runner-{settings.runner.id}.sqlite3")
        saved_incarnation = self.store.all("runner_meta").get("incarnation")
        self.incarnation = saved_incarnation["id"] if saved_incarnation else uuid.uuid4().hex
        if not saved_incarnation:
            self.store.put("runner_meta", "incarnation", {"id": self.incarnation})
        self.registry = Registry(settings.path(settings.runner.agents_dir), settings.path(settings.runner.talent_dir),
                                 settings.path(settings.runner.contract_dir) if settings.runner.contract_dir else None)
        self.ws_root = settings.path(settings.runner.workspace_root)
        self.sem = asyncio.Semaphore(settings.runner.max_parallel)
        self.consult_sem = asyncio.Semaphore(settings.runner.consult_parallel)
        self.reference_write_warned: set[str] = set()
        self.private_skip_warned: set[str] = set()
        self.private_shell_warned: set[str] = set()
        self.project_link_warned: set[tuple] = set()
        self.outbox: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = {}
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
        self.submitting: dict[str, set[asyncio.Future]] = {}  # submit commands running, by task (#214 review)
        self.jobs: dict[str, dict] = self.store.all("job")
        self.notified: set[str] = set(self.store.all("notified"))
        self.task_req.update({j["task_id"]: j.get("request_id") for j in self.jobs.values() if j.get("task_id")})
        self.broker = Broker(settings.runner.broker_port, self._on_approval, self._on_tool_event,
                             self._on_submit, self._on_ask, self._owns_job,
                             on_approval_timeout=self._on_approval_timeout)
        self.scheduler = Scheduler(settings.hpc)
        self.connected = asyncio.Event()
        self._stopping = False
        self.engine_versions: dict[str, str] | None = None  # probed once, off the event loop, before connecting
        self.local_software: dict | None = None  # bounded doctor probes, once before the first hello
        self.codex_sandbox = SandboxWatch()  # elevated setup still fits this Codex (#328)
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
        return TaskResult(task_id=tid, agent_id=agent_id, ok=False,
                          error="runner restarted: accepted task interrupted")

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
        await asyncio.gather(self.broker.serve(), self._connection_loop(), self._job_watch_loop())

    def stop(self) -> None:
        self._stopping = True
        self.broker.stop()
        for t in self.tasks.values():
            t.cancel()

    def send(self, obj: dict) -> None:
        if obj.get("type") not in {"runner.roster"}:
            self.store.enqueue(obj, self.s.runner.outbox_limit)
            self.reload_outbox()
        else:
            self.outbox.put_nowait(json.dumps(obj, ensure_ascii=False, default=str))

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
        d = ev.model_dump(mode="json")
        ws = self.workspaces.get(ev.task_id or "")
        if ws:
            ws.append_event(d)
        elif ev.task_id in self.event_buffers:
            self.event_buffers[ev.task_id].append(d)
        self.send(d)
        if ev.type == "task.result" and ev.task_id:
            self.store.put("accepted_task", ev.task_id, {"state": "finished"})

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
            except Exception:  # noqa: BLE001 - a host probe must not keep the runner offline
                log.warning("runner %s could not summarize local software", self.s.runner.id, exc_info=True)
                self.local_software = {}
        while not self._stopping:
            try:
                async with websockets.connect(url, max_size=64 * 2**20, ping_interval=20, ping_timeout=60) as ws:
                    backoff = 1.0
                    await ws.send(json.dumps(self.hello()))
                    self.reload_outbox(preserve_roster=False)
                    self.connected.set()
                    sender = asyncio.create_task(self._sender(ws))
                    try:
                        async for raw in ws:
                            await self._on_message(json.loads(raw))
                    finally:
                        sender.cancel()
                        self.connected.clear()
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
                                                     "labhq_hpc" in summary.get("mcp", [])})
        return agents

    def capabilities(self) -> dict:
        external_hpc = any("labhq_hpc" in a.get("mcp", []) for a in self.registry.roster())
        capabilities = {"scheduler": self.s.hpc.scheduler,
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
        return capabilities

    def hello(self) -> dict:
        return {"type": "runner.hello", "runner_id": self.s.runner.id,
                "incarnation": self.incarnation, "agents": self.roster(),
                "capabilities": self.capabilities()}

    # ---------------- gateway → runner ----------------
    async def _on_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "task.dispatch":
            task = Task.model_validate(msg["task"])
            prior = self.store.get("accepted_task", task.id)
            if prior is None:
                self.store.put("accepted_task", task.id,
                               {"state": "running", "task": task.model_dump(mode="json")})
            self.send({"type": "task.accepted", "task_id": task.id, "request_id": task.request_id})
            if prior is None:
                self.tasks[task.id] = asyncio.create_task(self._run_guarded(task))
            elif prior.get("state") == "running" and task.id not in self.tasks:
                has_result = any(e.get("type") == "task.result" and e.get("task_id") == task.id
                                 for e in self.store.pending())
                if not has_result:
                    self.send({"type": "task.result", "task_id": task.id, "agent_id": task.agent_id,
                                "request_id": task.request_id,
                                "data": self._interrupted_result(task.id, task.agent_id).model_dump(mode="json")})
                    self.store.put("accepted_task", task.id, {"state": "finished"})
        elif typ == "task.cancel":
            t = self.tasks.get(msg.get("task_id", ""))
            if t:
                t.cancel()
        elif typ == "approval.resolved":
            if self.broker.resolve(msg["id"], bool(msg.get("approved")), msg.get("note", "")):
                task_id, agent_id = self.approval_tasks.pop(msg["id"], (None, None))
                if task_id:
                    await self.emit(Event(type="agent.status", task_id=task_id, agent_id=agent_id,
                                          request_id=self.task_req.get(task_id), data={"state": "working"}))
            self.send({"type": "approval.ack", "id": msg["id"]})
        elif typ == "ask.resolved":
            self.broker.resolve_ask(msg["id"], msg.get("answer") or {})
            self.send({"type": "ask.ack", "id": msg["id"]})
        elif typ == "runner.ack":
            self.store.ack(int(msg["runner_seq"]))
        elif typ == "registry.reload":
            self.registry.load()
            self._send_roster()
        elif typ == "recruit.start":
            asyncio.create_task(self._recruit(msg))
        elif typ == "contract.update":
            self._contract_update(msg)
            self._send_roster()

    def _contract_update(self, msg: dict) -> None:
        action, days = msg.get("action"), float(msg.get("days") or self.s.recruit.default_ttl_days)
        if action == "extend":
            self.registry.extend(msg["agent_id"], days)
        elif action == "release":
            self.registry.release(msg["agent_id"])
        elif action == "activate":
            self.registry.activate(msg["agent_id"])
        elif action == "rehire":
            self.registry.rehire(msg["slug"], days)

    async def _recruit(self, msg: dict) -> None:
        from ..recruit.paper2agent import Recruitment

        try:
            await Recruitment(self, msg).run()
        except Exception as e:
            log.exception("recruitment failed")
            await self.emit(Event(type="recruit.failed", request_id=msg.get("request_id"), data={"error": str(e)}))
        self._send_roster()

    # ---------------- task execution ----------------
    async def _run_guarded(self, task: Task) -> None:
        try:
            await self.run_task(task)
        except BaseException as e:  # cancelled or crashed: the gateway must still get a result
            self.event_buffers.pop(task.id, None)
            err = "cancelled" if isinstance(e, asyncio.CancelledError) else f"{type(e).__name__}: {e}"
            if not isinstance(e, asyncio.CancelledError):
                log.exception("task %s crashed", task.id)
            res = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error=err)
            base = dict(task_id=task.id, agent_id=task.agent_id, request_id=task.request_id)
            await self.emit(Event(type="agent.status", data={"state": "error", "error": short(err, 200)}, **base))
            await self.emit(Event(type="task.result", data=res.model_dump(mode="json"), **base))
            if isinstance(e, asyncio.CancelledError):
                raise

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
    ) -> tuple[tuple[Path, tuple[str, ...], list[tuple[str, tuple[str, ...]]]] | None, str | None]:
        """Validate declared runner-local refs; staging reopens every component through held folders (#86)."""
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
            for raw_ref in refs:
                normalized = str(raw_ref).replace("\\", "/")
                if (not normalized or normalized.startswith(("/", "../")) or "/../" in normalized
                        or normalized.endswith("/..") or re.match(r"^[A-Za-z]:", normalized)):
                    raise ValueError
                current = source
                for part in Path(normalized).parts:
                    current /= part
                    if not os.path.lexists(current) or is_link(current):
                        raise ValueError
                if (not current.resolve(strict=True).is_relative_to(resolved_source)
                        or not current.is_file()):
                    raise ValueError
                resolved.append((normalized, tuple(Path(normalized).parts)))
        except (OSError, RuntimeError, ValueError):
            return None, "consult refs는 이 runner의 원래 작업 폴더 안에 있는 링크 아닌 파일이어야 합니다"
        return (workspace_root, source_parts, resolved), None

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
        workspace_root, source_parts, sources = inputs
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
        skipped: list[tuple[str, str]] = []
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
        notes.extend(f"참고 파일 제외: {json.dumps(raw_ref, ensure_ascii=False)} ({reason})"
                     for raw_ref, reason in skipped)
        if notes:
            task = task.model_copy(update={"prompt": task.prompt + "\n\n" + "\n".join(notes)})
        return task, None

    async def _project_links(self, directories: list[str], zones: list[Path], emit,
                             fail_closed: bool = False) -> tuple[list[str], str | None]:
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
            key = (str(path), tuple(map(str, links)), incomplete)
            if (links or incomplete) and key not in self.project_link_warned:
                self.project_link_warned.add(key)
                if links:
                    names = ", ".join(link.relative_to(path).as_posix() for link in links[:5])
                    await emit("agent.log", {"level": "warn", "text": (
                        f"프로젝트 폴더 {path.name}의 링크 {len(links)}개가 통제 데이터 구역으로 이어지거나 풀 수 없습니다: "
                        f"{names}. Claude는 그 경로를 읽고 쓰지 못하게 막지만 다른 엔진과 미리 허용된 셸 명령은 막지 못합니다")})
                if incomplete:
                    await emit("agent.log", {"level": "warn", "text": (
                        f"프로젝트 폴더 {path.name}의 링크를 다 확인하지 못했습니다({incomplete}). "
                        "그 너머의 링크가 통제 구역을 가리켜도 막지 못합니다")})
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
        ws.update_run(task_id, read_only_changes=changed,
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

    async def run_task(self, task: Task, workdir_override: Path | None = None) -> TaskResult:
        agent = self._resolve_agent(task)
        read_only = is_read_only_task(task.meta)
        refusal = read_only_refusal(agent.id, agent.engine) if read_only else None
        if refusal:  # the gateway refuses first; this holds for any other sender and after force_engine
            result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=refusal)
            base = dict(task_id=task.id, agent_id=agent.id, request_id=task.request_id)
            await self.emit(Event(type="agent.status", data={"state": "error", "error": short(refusal, 200)}, **base))
            await self.emit(Event(type="task.result", data=result.model_dump(mode="json"), **base))
            return result
        if read_only:  # an allowlist built from the staff member's identity, never the sender's overrides
            agent = read_only_profile(agent)
        override = workdir_override or (Path(task.meta["workdir"]) if task.meta.get("workdir") else None)
        workspace_dir = Path(override) if override else self.ws_root / time.strftime("%Y-%m-%d") / f"{task.id}_{agent.id}"
        reused = os.path.lexists(workspace_dir)
        # Do not let TaskWorkspace create files through links left by an earlier run. Fresh workspaces keep
        # the old event ordering; reused ones are registered only after their preflight succeeds (#191).
        ws = None if reused else TaskWorkspace(self.ws_root, task, agent, workspace_dir)
        if reused:
            self.workspaces.pop(task.id, None)  # emit() must not append through the previous workspace yet
            self.event_buffers[task.id] = []  # ...but queued/working still belong in its log once it is safe
        if ws:
            self.workspaces[task.id] = ws
            self.task_req[task.id] = task.request_id

        tool_errors: list[str] = []
        breaker = Breaker()

        async def emit(typ: str, data: dict) -> None:
            if typ == "agent.tool_error" and task.meta.get("general_result_contract"):
                first = str(data.get("text") or "tool failed").splitlines()[0].strip() or "tool failed"
                tool_errors.append(short(first, 200))
            await self.emit(Event(type=typ, task_id=task.id, agent_id=agent.id, request_id=task.request_id, data=data))
            trip = breaker.observe(typ, data)
            if trip:  # #59 shadow: tell the PI once per signal, keep the agent running
                for kind, body in (("agent.breaker", trip), ("agent.log", {"level": "alert", "text": trip_text(trip)})):
                    await self.emit(Event(type=kind, task_id=task.id, agent_id=agent.id, request_id=task.request_id,
                                          data=body))

        await emit("agent.status", {"state": "queued"})
        consult = task.meta.get("kind") == "consult"
        semaphore = self.consult_sem if consult else self.sem
        async with semaphore:
            await emit("agent.status", {"state": "working", "task": task.meta.get("title") or short(task.prompt, 120)})
            zones = self._zones()
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
                    await emit("agent.status", {"state": "error", "error": short(error, 200)})
                    await emit("task.result", result.model_dump(mode="json"))
                    return result
                ws = TaskWorkspace(self.ws_root, task, agent, workspace_dir)
                for event in self.event_buffers.pop(task.id, []):  # before any later event (#193)
                    ws.append_event(event)
                self.workspaces[task.id] = ws
                self.task_req[task.id] = task.request_id
            assert ws is not None
            extra_dirs = [str(self.s.path(d)) for d in [*agent.project_dirs, *task.meta.get("project_dirs", [])]]
            task, consult_error = self._stage_consult_refs(task, ws)
            ws.task = task
            if consult_error:
                result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=consult_error)
                await emit("agent.log", {"level": "alert", "text": consult_error})
                await emit("agent.status", {"state": "error", "error": short(consult_error, 200)})
                await emit("task.result", result.model_dump(mode="json"))
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
                await emit("agent.status", {"state": "error", "error": short(error, 200)})
                await emit("task.result", result.model_dump(mode="json"))
                return result
            root = self.ws_root.resolve()
            for directory in task.meta.get("upstream_dirs", []):
                upstream = Path(directory).resolve()
                if upstream.is_dir() and upstream.is_relative_to(root):
                    reason = await asyncio.to_thread(self._upstream_refusal, upstream, zones)
                    if reason:
                        await emit("agent.log", {"level": "warn", "text": f"이전 단계 폴더 제외: {upstream.name} ({reason})"})
                        continue
                    extra_dirs.append(str(upstream))
            upstream_steps = task.meta.get("upstream_steps")
            if isinstance(upstream_steps, dict) and upstream_steps and not read_only:
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
            read_dirs, skipped, refused = self._reference_dirs(task, [str(ws.dir), *extra_dirs],
                                                               needs_rules=agent.engine == Engine.claude_code)
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
            prompt = ws.write_task_md()
            if agent.contract and agent.contract.skill_dir:
                skill_error = ws.install_skill(Path(agent.contract.skill_dir))
                if skill_error:
                    result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=skill_error)
                    await emit("agent.status", {"state": "error", "error": skill_error})
                    await emit("task.result", result.model_dump(mode="json"))
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
                await emit("agent.status", {"state": "error", "error": error})
                await emit("task.result", result.model_dump(mode="json"))
                return result
            # PI personal paths (policy.private_paths): one that holds a folder this task works in is left open.
            keep = [ws.dir, self.ws_root, *extra_dirs, *read_dirs,
                    *(self.s.path(r) for r in self.s.runner.reference_roots),
                    *(self.s.path(p.local_dir) for p in self.s.projects if p.local_dir),
                    *plugin_keep_dirs(self.s, agent.plugin_dirs, ws.dir),
                    *staff_codex_homes(self.s, ws.dir, agent.engine == Engine.codex)]
            if task.meta.get("kind") == "recruit":  # the recruiter reads the Paper2Agent skill's own files
                from ..recruit.paper2agent import skill_path
                keep.append(skill_path(self.s.recruit.contract_engine))
            # A Claude task reads back the long tool output Claude saved in the staff config folder (#298 ⑤).
            open_reads = staff_claude_project_dirs(self.s, ws.dir) if agent.engine == Engine.claude_code else []
            private = resolve_private_paths(self.s, keep, open_reads=open_reads, cwd=ws.dir)
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
            broker_token = self.broker.issue_task_token(task.id, agent.id, task.request_id, workdir=str(ws.dir))
            env = {
                "LABHQ_BROKER_URL": self.broker.url, "LABHQ_BROKER_TOKEN": broker_token,
                "LABHQ_TASK_ID": task.id, "LABHQ_AGENT_ID": agent.id, "LABHQ_WORKDIR": str(ws.dir),
                "LABHQ_EXTRA_ROOTS": os.pathsep.join(extra_dirs),
                PRIVATE_PATHS_ENV: os.pathsep.join(private.paths),
                PRIVATE_ENABLED_ENV: "1" if private.enabled else "0",
                PRIVATE_OPEN_READS_ENV: os.pathsep.join(private.open_reads),
            }
            if staff_config:
                env["LABHQ_CONFIG"] = staff_config
            env.update(self._system_ca_env(agent))
            output_before: dict[str, tuple] | None = None
            output_records: list[dict[str, Any]] = []
            observed_outputs: list[dict[str, Any]] = []
            output_scan_notes: list[str] = []

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

            async def collect_observed_outputs() -> None:
                """Hash the post-run view off the event loop, before labhq writes RESULT files."""
                nonlocal output_records, observed_outputs
                if output_before is None:
                    return
                output_records, note = await asyncio.to_thread(
                    ws.scan_output_records, zones, self.s.runner.reference_scan_max_entries,
                    self.s.runner.reference_scan_max_depth, self.s.runner.output_hash_max_bytes)
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
                ws.update_run(task.id, observed_outputs=observed_outputs,
                              **({"observed_outputs_incomplete": incomplete} if incomplete else {}))

            engine_settings = claude_deny_private(claude_deny_links(claude_read_only(
                claude_settings(self.s.policy), [d for d in read_dirs if claude_rule_ready(d)]), denied_links),
                private.paths, open_reads=private.open_reads)
            if agent.engine == Engine.claude_code and not read_only:
                from ..hooks.tool_use import add_claude_hook

                engine_settings = add_claude_hook(engine_settings)
            ctx = RunContext(
                task=task, agent=agent, workdir=ws.dir, settings=self.s,
                # A read-only task answers once from existing work; it does not ask anyone in turn, and gets no
                # MCP server at all (the profile has none, and labhq_ask would be one).
                mcp_servers=[] if read_only else self._mcp_servers(agent, env),
                env={**env, "MCP_TOOL_TIMEOUT": str(labhq_mcp_timeout_s(self.s) * 1000)},
                emit=emit, prompt=prompt, prompt_pointer=ws.prompt_pointer, extra_dirs=extra_dirs,
                read_dirs=read_dirs,
                # Other engines never read these rules; only paths a rule can name go in (#177).
                claude_settings=engine_settings,
                private_labels=list(private.labels),
                private_paths=list(private.paths),
                private_enabled=private.enabled,
                private_open_reads=list(private.open_reads),
                use_permission_tool="approval" in agent.builtin_mcp,
                record_run=lambda **fields: ws.update_run(task.id, **fields),
                resume_baseline=self._resume_baseline(task, agent, ws),
                read_only=read_only,
                # Again after prepare(): the adapter's own files (Codex AGENTS.md) are not the agent's writes.
                before_spawn=before_spawn,
            )
            ws.update_run(task.id, started_at=time.time(), runner_id=self.s.runner.id,
                          engine=agent.engine.value, model=agent.model,
                          engine_cli_version=(self.engine_versions or {}).get(agent.engine.value),
                          resume_of=task.resume_session_id, kind=task.meta.get("kind"),
                          **({"reference_dirs": read_dirs} if read_dirs else {}))
            self.active_roots[task.id] = (read_only, [ws.dir.resolve(), *(Path(d).resolve() for d in extra_dirs)])
            try:
                refused = watch.take_baseline() if watch else None
                if refused:  # fail closed: a run whose writes cannot be checked does not start
                    result = TaskResult(task_id=task.id, agent_id=agent.id, ok=False, error=refused)
                else:
                    adapter = get_adapter(agent.engine, self.s)
                    try:
                        result = await adapter.run(ctx)
                    except BaseException:  # cancelled or crashed after the CLI was stopped: still compare
                        await collect_observed_outputs()
                        if watch and watch.baseline is not None:
                            await self._read_only_verdict(TaskResult(task_id=task.id, agent_id=agent.id, ok=False),
                                                          watch, emit, ws, task.id)
                        raise
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
        declared = task.meta.get("outputs", [])
        found = []
        for name in declared:
            relative = output_relpath(name)
            if relative is None:
                continue
            target = (ws.dir / relative).resolve()
            if target.exists() and target.is_relative_to((ws.dir / "outputs").resolve()):
                found.append(relative)
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
        result.outputs = list(dict.fromkeys([*result.outputs, *found]))
        hashed = {row["path"]: row["sha256"] for row in output_records
                  if isinstance(row.get("sha256"), str) and row["path"] in result.outputs}
        result.output_sha256 = hashed
        result.output_tool_use_ids = {row["path"]: row["tool_use_id"] for row in observed_outputs
                                      if isinstance(row.get("tool_use_id"), str)}
        collected = set(result.outputs)
        result.unreported_outputs = sorted(row["path"] for row in observed_outputs if row["path"] not in collected)
        if result.unreported_outputs:
            await emit("agent.log", {"level": "warn", "text": (
                "보고하지 않은 관찰 산출물: " + ", ".join(result.unreported_outputs))})
        for note in dict.fromkeys(output_scan_notes):
            await emit("agent.log", {"level": "warn", "text": note})
        # Only a finished, successful turn: a bundle written before HPC checks or an ask is not final (#301 review).
        if (agent.id == "bioinfo-agent" and task.meta.get("pipeline_pr") is True  # the gateway asked (#300)
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
                result.output_types = output_types.runner_records(found, task.meta, output_vocab.current())
            except Exception:
                result.output_types = {}
                await emit("agent.log", {"level": "warn", "text": "output type records unavailable; result kept"})
        try:
            for name in (f"RESULT_{task.id}.md", "RESULT.md"):  # never through a link the agent made (#165)
                write_owned(ws.dir, f"outputs/{name}", result.text or "")
        except OwnedPathError:
            error = "작업 폴더의 outputs가 실행 중에 링크로 바뀌어 결과 파일을 쓰지 않았습니다"
            await emit("agent.log", {"level": "alert", "text": error})
            result = result.model_copy(update={"ok": False, "error": error})
        ws.update_run(task.id, ended_at=time.time(), ok=result.ok, error=result.error, cost_usd=result.cost_usd,
                      cost_known=result.cost_known if result.cost_known is not None else result.cost_usd is not None,
                      usage=result.usage, usage_known=result.usage_known,
                      session_id=result.session_id, pending_jobs=pending)
        result.provenance = ws.provenance()
        if tool_errors:
            result.tool_errors = [*result.tool_errors, *tool_errors]
        if not result.ok and result.quota_reset_at is None:  # only this machine knows the CLI's zone
            result.quota_reset_at = quota_reset_instant(agent.engine.value, result.error)
        state = "hibernating" if waiting(result) else ("done" if result.ok else "error")
        extra = ({"jobs": pending, "asks": result.pending_asks} if waiting(result) else
                 ({"error": short(result.error, 200)} if result.error else {}))
        await emit("agent.status", {"state": state, **extra})
        await emit("task.result", result.model_dump(mode="json"))
        return result

    # ---------------- broker callbacks (from MCP tools) ----------------
    async def _on_approval(self, req: ApprovalRequest) -> None:
        req.request_id = req.request_id or self.task_req.get(req.task_id or "")
        self.approval_tasks[req.id] = (req.task_id, req.agent_id)
        base = dict(task_id=req.task_id, agent_id=req.agent_id, request_id=req.request_id)
        await self.emit(Event(type="approval.requested", data=req.model_dump(mode="json"), **base))
        if req.task_id:
            await self.emit(Event(type="agent.status", data={"state": "waiting", "approval": req.id}, **base))

    async def _on_approval_timeout(self, req: ApprovalRequest) -> None:
        self.approval_tasks.pop(req.id, None)
        base = dict(task_id=req.task_id, agent_id=req.agent_id, request_id=req.request_id)
        await self.emit(Event(type="approval.timed_out", data={"id": req.id}, **base))
        if req.task_id:
            await self.emit(Event(type="agent.status", data={"state": "working"}, **base))

    async def _on_ask(self, req: AskRequest) -> None:
        req.request_id = req.request_id or self.task_req.get(req.task_id or "")
        base = dict(task_id=req.task_id, agent_id=req.agent_id, request_id=req.request_id)
        await self.emit(Event(type="ask.requested", data=req.model_dump(mode="json"), **base))
        if req.task_id:
            await self.emit(Event(type="agent.status", data={"state": "waiting", "ask": req.id}, **base))

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
                                   "core_hours": result["core_hours"]})

        return await submit_job(self.s, self.scheduler, ws.dir, body, approve, gate, record)

    async def _track_job(self, body: dict) -> None:
        """Watch a job the runner submitted. No broker endpoint reaches this with a caller's id."""
        jid, tid = str(body["job_id"]), body.get("task_id")
        ws = self.workspaces.get(tid or "")
        self.jobs[jid] = {"job_id": jid, "task_id": tid, "agent_id": body.get("agent_id"),
                           "name": body.get("name", ""), "state": "queued", "missing": 0, "terminal": False,
                           "exit_status": None, "submitted_at": time.time(),
                           "scheduler": self.s.hpc.scheduler, "request_id": self.task_req.get(tid or ""),
                           "workdir": str(ws.dir) if ws else None, "submitted_by_runner": True}
        self.store.put("job", jid, self.jobs[jid])
        if ws:
            ws.append_job({**body, "submitted_at": time.time()})
        await self.emit(Event(type="job.submitted", task_id=tid, agent_id=body.get("agent_id"),
                              request_id=self.task_req.get(tid or ""),
                              data={"job_id": jid, "name": body.get("name"), "core_hours": body.get("core_hours")}))

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

    # ---------------- HPC job watcher ----------------
    async def _job_watch_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.s.runner.job_poll_s)
            try:
                await self._poll_jobs()
            except Exception:
                log.exception("job poll failed")

    async def _poll_jobs(self) -> None:
        for jid, j in list(self.jobs.items()):
            if j["terminal"]:
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
            if state != j["state"]:
                j.update(state=state, exit_status=info.exit_status)
                await self.emit(Event(type="job.state", task_id=j["task_id"], agent_id=j["agent_id"],
                                      request_id=self.task_req.get(j["task_id"] or ""),
                                      data={"job_id": jid, "name": j["name"], "state": state,
                                            "exit_status": info.exit_status}))
            j["terminal"] = state in TERMINAL
            self.store.put("job", jid, j)

        by_task: dict[str, list[dict]] = {}
        for j in self.jobs.values():
            by_task.setdefault(j["task_id"], []).append(j)
        for tid, js in by_task.items():
            if tid in self.notified or not all(x["terminal"] for x in js):
                continue
            t = self.tasks.get(tid)
            if t is not None and not t.done():  # the agent is still talking; wake it after it ends
                continue
            await self.emit(Event(
                type="jobs.finished", task_id=tid, agent_id=js[0]["agent_id"], request_id=self.task_req.get(tid),
                data={"jobs": [{k: x.get(k) for k in ("job_id", "name", "state", "exit_status")} for x in js],
                      "session_id": js[0].get("session_id"), "workdir": js[0].get("workdir")},
            ))
            self.notified.add(tid)
            self.store.put("notified", tid, {"done": True})
