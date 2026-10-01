"""The runner lives where the compute and data live (your workstation or an HPC login node).

It dials the gateway (outbound WebSocket → no inbound ports, works behind NAT/firewalls), receives
task.dispatch, runs the agent's CLI in a per-task workspace, and streams normalized events back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import websockets

from ..adapters import get_adapter
from ..adapters.base import RunContext
from ..ask_results import read_ask_results, rejected_step
from ..models import ASK_MAX_WAIT_S, AgentSpec, ApprovalRequest, AskRequest, Engine, Event, McpServerSpec, Task, TaskResult, waiting
from .versions import engine_cli_versions
from ..intake import overlaps_restricted, reference_roots
from ..policy import claude_read_only, claude_settings
from ..registry import Registry
from ..settings import Settings
from ..store import StateStore
from ..tools.scheduler import TERMINAL, Scheduler
from ..util import output_relpath, short
from .approvals import Broker
from .workspace import TaskWorkspace

log = logging.getLogger("labhq.runner")
REPO_ROOT = Path(__file__).resolve().parents[2]


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


class Runner:
    def __init__(self, settings: Settings):
        self.s = settings
        self.store = StateStore(settings.path(settings.runner.state_dir) / f"runner-{settings.runner.id}.sqlite3")
        saved_incarnation = self.store.all("runner_meta").get("incarnation")
        self.incarnation = saved_incarnation["id"] if saved_incarnation else uuid.uuid4().hex
        if not saved_incarnation:
            self.store.put("runner_meta", "incarnation", {"id": self.incarnation})
        self.registry = Registry(settings.path(settings.runner.agents_dir), settings.path(settings.runner.talent_dir))
        self.ws_root = settings.path(settings.runner.workspace_root)
        self.sem = asyncio.Semaphore(settings.runner.max_parallel)
        self.consult_sem = asyncio.Semaphore(settings.runner.consult_parallel)
        self.outbox: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: dict[str, asyncio.Task] = {}
        self.workspaces: dict[str, TaskWorkspace] = {}
        self.task_req: dict[str, str | None] = {}
        self.approval_tasks: dict[str, tuple[str | None, str | None]] = {}
        self.jobs: dict[str, dict] = self.store.all("job")
        self.notified: set[str] = set(self.store.all("notified"))
        self.task_req.update({j["task_id"]: j.get("request_id") for j in self.jobs.values() if j.get("task_id")})
        self.broker = Broker(settings.runner.broker_port, self._on_approval, self._on_tool_event,
                             self._on_track, self._on_ask)
        self.scheduler = Scheduler(settings.hpc)
        self.connected = asyncio.Event()
        self._stopping = False
        self.engine_versions: dict[str, str] | None = None  # probed once, off the event loop, before connecting
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
        self.send(d)
        if ev.type == "task.result" and ev.task_id:
            self.store.put("accepted_task", ev.task_id, {"state": "finished"})

    async def _connection_loop(self) -> None:
        url = f"{self.s.gateway.url.rstrip('/')}/ws/runner?token={self.s.gateway.runner_token}"
        backoff = 1.0
        if self.engine_versions is None:
            engines = {a["engine"] for a in self.roster()}  # effective engines (force_engine applies)
            self.engine_versions = await asyncio.to_thread(engine_cli_versions, self.s, engines)
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
        agents = self.registry.roster()
        if self.s.runner.force_engine:
            agents = [{**a, "engine": self.s.runner.force_engine} for a in agents]
        return [{**a, "hpc_tools": ("hpc" in a.get("builtin_mcp", []) and
                                     self.s.hpc.scheduler != "none") or "labhq_hpc" in a.get("mcp", [])}
                for a in agents]

    def capabilities(self) -> dict:
        external_hpc = any("labhq_hpc" in a.get("mcp", []) for a in self.registry.roster())
        return {"scheduler": self.s.hpc.scheduler,
                "compute_backends": ["local CLI"] + ([self.s.hpc.scheduler] if self.s.hpc.scheduler != "none" else []) +
                                    (["external labhq_hpc MCP"] if external_hpc else []),
                "hpc_tools": self.s.hpc.scheduler != "none" or external_hpc,
                "engine_cli_versions": dict(self.engine_versions or {})}

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
        if task.meta.get("agent_overrides"):
            agent = agent.model_copy(update=task.meta["agent_overrides"])
        if self.s.runner.force_engine:
            agent = agent.model_copy(update={"engine": Engine(self.s.runner.force_engine)})
        return agent

    def _mcp_servers(self, agent: AgentSpec, env: dict[str, str], allow_ask: bool = True) -> list[McpServerSpec]:
        env = {**env, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")]))}
        servers: list[McpServerSpec] = []
        timeout_s = max(self.s.policy.approvals.timeout_s, ASK_MAX_WAIT_S) + 120
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
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
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

    def _reference_dirs(self, task: Task, writable: list[str]) -> tuple[list[str], list[str]]:
        """Re-check path references with resolved paths on this runner (#36): (read-only dirs, skip notes)."""
        roots = [Path(root).resolve() for root in reference_roots(self.s)]
        open_dirs = [Path(d).resolve() for d in writable]
        kept: list[str] = []
        skipped: list[str] = []
        for raw in task.meta.get("reference_dirs") or []:
            try:
                path = Path(os.path.expanduser(str(raw))).resolve()
            except (OSError, RuntimeError, ValueError):
                skipped.append(f"{raw} (경로를 읽을 수 없음)")
                continue
            if not path.exists():
                skipped.append(f"{raw} (없음)")
                continue
            directory = path if path.is_dir() else path.parent
            if not any(directory == root or directory.is_relative_to(root) for root in roots):
                skipped.append(f"{raw} (runner.reference_roots 밖)")
            elif overlaps_restricted(str(directory), self.s):
                skipped.append(f"{raw} (통제 데이터 구역)")
            elif any(directory == d or directory.is_relative_to(d) for d in open_dirs):
                continue  # already reachable through a writable project dir; keep that dir writable
            elif str(directory) not in kept:
                kept.append(str(directory))
        return kept, skipped

    async def run_task(self, task: Task, workdir_override: Path | None = None) -> TaskResult:
        agent = self._resolve_agent(task)
        override = workdir_override or (Path(task.meta["workdir"]) if task.meta.get("workdir") else None)
        ws = TaskWorkspace(self.ws_root, task, agent, override)
        self.workspaces[task.id] = ws
        self.task_req[task.id] = task.request_id

        async def emit(typ: str, data: dict) -> None:
            await self.emit(Event(type=typ, task_id=task.id, agent_id=agent.id, request_id=task.request_id, data=data))

        await emit("agent.status", {"state": "queued"})
        consult = task.meta.get("kind") == "consult"
        semaphore = self.consult_sem if consult else self.sem
        async with semaphore:
            await emit("agent.status", {"state": "working", "task": task.meta.get("title") or short(task.prompt, 120)})
            prompt = ws.write_task_md()
            if agent.contract and agent.contract.skill_dir:
                ws.install_skill(Path(agent.contract.skill_dir))
            extra_dirs = [str(self.s.path(d)) for d in [*agent.project_dirs, *task.meta.get("project_dirs", [])]]
            root = self.ws_root.resolve()
            for directory in task.meta.get("upstream_dirs", []):
                upstream = Path(directory).resolve()
                if upstream.is_dir() and upstream.is_relative_to(root):
                    extra_dirs.append(str(upstream))
            # Reference paths are readable but never write roots: not in LABHQ_EXTRA_ROOTS, Claude denies edits.
            read_dirs, skipped = self._reference_dirs(task, [str(ws.dir), *extra_dirs])
            for note in skipped:
                await emit("agent.log", {"level": "warn", "text": f"참고 경로 제외: {note}"})
            broker_token = self.broker.issue_task_token(task.id, agent.id, task.request_id)
            env = {
                "LABHQ_BROKER_URL": self.broker.url, "LABHQ_BROKER_TOKEN": broker_token,
                "LABHQ_TASK_ID": task.id, "LABHQ_AGENT_ID": agent.id, "LABHQ_WORKDIR": str(ws.dir),
                "LABHQ_EXTRA_ROOTS": os.pathsep.join(extra_dirs),
            }
            if self.s.config_path:
                env["LABHQ_CONFIG"] = self.s.config_path
            ctx = RunContext(
                task=task, agent=agent, workdir=ws.dir, settings=self.s,
                # A consult or a follow-up answers once from existing work; it does not ask anyone in turn.
                mcp_servers=self._mcp_servers(agent, env, allow_ask=task.meta.get("kind") not in {"consult", "followup"}),
                env={**env, "MCP_TOOL_TIMEOUT": str((max(self.s.policy.approvals.timeout_s,
                                                          ASK_MAX_WAIT_S) + 120) * 1000)},
                emit=emit, prompt=prompt, extra_dirs=extra_dirs, read_dirs=read_dirs,
                claude_settings=claude_read_only(claude_settings(self.s.policy), read_dirs),
                use_permission_tool="approval" in agent.builtin_mcp,
                record_run=lambda **fields: ws.update_run(task.id, **fields),
                resume_baseline=self._resume_baseline(task, agent, ws),
            )
            ws.update_run(task.id, started_at=time.time(), runner_id=self.s.runner.id,
                          engine=agent.engine.value, model=agent.model,
                          engine_cli_version=(self.engine_versions or {}).get(agent.engine.value),
                          resume_of=task.resume_session_id, kind=task.meta.get("kind"),
                          **({"reference_dirs": read_dirs} if read_dirs else {}))
            try:
                result = await get_adapter(agent.engine, self.s).run(ctx)
                result.pending_asks = self.broker.pending_for_task(task.id)
                outcome = read_ask_results(self.broker.task_ask_results.get(task.id, []))
                if outcome["status"] == "rejected":
                    result = rejected_step(result, outcome["reason"])
            finally:
                self.broker.revoke_task_token(broker_token)
                self.broker.finish_task(task.id)

        pending = [jid for jid, j in self.jobs.items() if j["task_id"] == task.id and not j["terminal"]]
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
        result.outputs = list(dict.fromkeys([*result.outputs, *found]))
        (ws.dir / "outputs" / f"RESULT_{task.id}.md").write_text(result.text or "", encoding="utf-8")
        (ws.dir / "outputs" / "RESULT.md").write_text(result.text or "", encoding="utf-8")
        ws.update_run(task.id, ended_at=time.time(), ok=result.ok, error=result.error, cost_usd=result.cost_usd,
                      cost_known=result.cost_known if result.cost_known is not None else result.cost_usd is not None,
                      usage=result.usage, usage_known=result.usage_known,
                      session_id=result.session_id, pending_jobs=pending)
        result.provenance = ws.provenance()
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

    async def _on_track(self, body: dict) -> None:
        jid, tid = str(body["job_id"]), body.get("task_id")
        ws = self.workspaces.get(tid or "")
        self.jobs[jid] = {"job_id": jid, "task_id": tid, "agent_id": body.get("agent_id"),
                           "name": body.get("name", ""), "state": "queued", "missing": 0, "terminal": False,
                           "exit_status": None, "submitted_at": time.time(),
                           "scheduler": self.s.hpc.scheduler, "request_id": self.task_req.get(tid or ""),
                           "workdir": str(ws.dir) if ws else None}
        self.store.put("job", jid, self.jobs[jid])
        if ws:
            ws.append_job({**body, "submitted_at": time.time()})
        await self.emit(Event(type="job.submitted", task_id=tid, agent_id=body.get("agent_id"),
                              request_id=self.task_req.get(tid or ""),
                              data={"job_id": jid, "name": body.get("name"), "core_hours": body.get("core_hours")}))

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
                if j["missing"] < 3:
                    continue
                state = "unknown_finished"
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
