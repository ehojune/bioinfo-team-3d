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
from pathlib import Path

import websockets

from ..adapters import get_adapter
from ..adapters.base import RunContext
from ..models import AgentSpec, ApprovalRequest, Engine, Event, McpServerSpec, Task, TaskResult
from ..policy import claude_settings
from ..registry import Registry
from ..settings import Settings
from ..tools.scheduler import TERMINAL, Scheduler
from ..util import short
from .approvals import Broker
from .workspace import TaskWorkspace

log = logging.getLogger("labhq.runner")
REPO_ROOT = Path(__file__).resolve().parents[2]


class Runner:
    def __init__(self, settings: Settings):
        self.s = settings
        self.registry = Registry(settings.path(settings.runner.agents_dir), settings.path(settings.runner.talent_dir))
        self.ws_root = settings.path(settings.runner.workspace_root)
        self.sem = asyncio.Semaphore(settings.runner.max_parallel)
        self.outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=20000)
        self.tasks: dict[str, asyncio.Task] = {}
        self.workspaces: dict[str, TaskWorkspace] = {}
        self.task_req: dict[str, str | None] = {}
        self.approval_tasks: dict[str, tuple[str | None, str | None]] = {}
        self.jobs: dict[str, dict] = {}
        self.notified: set[str] = set()
        self.broker = Broker(settings.runner.broker_port, self._on_approval, self._on_tool_event, self._on_track)
        self.scheduler = Scheduler(settings.hpc)
        self.connected = asyncio.Event()
        self._stopping = False

    # ---------------- lifecycle ----------------
    def _check_data_boundary(self) -> None:
        zones = [z.path for z in self.s.policy.data_zones if z.level == "restricted"]
        if not zones:
            return
        if os.name == "nt":
            raise RuntimeError("restricted data zones have no enforced guard on Windows; run the runner on Linux")
        if any(p.startswith(("\\\\", "//")) for p in zones):
            raise RuntimeError("UNC restricted zones have no verified Claude deny rule; run with a Linux path")
        readable = [p for p in zones if os.path.exists(p) and
                    (os.access(p, os.R_OK) or self._can_list(p))]
        if readable:
            if not self.s.policy.allow_runner_read_restricted:
                raise RuntimeError("runner account can read restricted data; use a separate Linux runner account")
            log.critical("UNSAFE OVERRIDE: runner account can read restricted data (%d zone(s)); "
                         "policy.allow_runner_read_restricted is enabled", len(readable))

    @staticmethod
    def _can_list(path: str) -> bool:
        try:
            os.listdir(path)
            return True
        except OSError:
            return False

    async def run_forever(self) -> None:
        self._check_data_boundary()
        self.registry.load()
        log.info("runner %s: %d agents", self.s.runner.id, len(self.registry.agents))
        await asyncio.gather(self.broker.serve(), self._connection_loop(), self._job_watch_loop())

    def stop(self) -> None:
        self._stopping = True
        self.broker.stop()
        for t in self.tasks.values():
            t.cancel()

    def send(self, obj: dict) -> None:
        msg = json.dumps(obj, ensure_ascii=False, default=str)
        try:
            self.outbox.put_nowait(msg)
        except asyncio.QueueFull:  # drop the oldest event rather than block agents
            self.outbox.get_nowait()
            self.outbox.put_nowait(msg)

    async def emit(self, ev: Event) -> None:
        d = ev.model_dump(mode="json")
        ws = self.workspaces.get(ev.task_id or "")
        if ws:
            ws.append_event(d)
        self.send(d)

    async def _connection_loop(self) -> None:
        url = f"{self.s.gateway.url.rstrip('/')}/ws/runner?token={self.s.gateway.runner_token}"
        backoff = 1.0
        while not self._stopping:
            try:
                async with websockets.connect(url, max_size=64 * 2**20, ping_interval=20, ping_timeout=60) as ws:
                    backoff = 1.0
                    await ws.send(json.dumps({"type": "runner.hello", "runner_id": self.s.runner.id,
                                              "agents": self.registry.roster()}))
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
        self.send({"type": "runner.roster", "runner_id": self.s.runner.id, "agents": self.registry.roster()})

    # ---------------- gateway → runner ----------------
    async def _on_message(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "task.dispatch":
            task = Task.model_validate(msg["task"])
            self.tasks[task.id] = asyncio.create_task(self._run_guarded(task))
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

    def _mcp_servers(self, agent: AgentSpec, env: dict[str, str]) -> list[McpServerSpec]:
        env = {**env, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")]))}
        servers: list[McpServerSpec] = []
        if "approval" in agent.builtin_mcp:
            servers.append(McpServerSpec(name="labhq_approval", command=sys.executable,
                                         args=["-m", "labhq.tools.approval_mcp"], env=env))
        if "hpc" in agent.builtin_mcp and self.s.hpc.scheduler != "none":
            servers.append(McpServerSpec(name="labhq_hpc", command=sys.executable,
                                         args=["-m", "labhq.tools.hpc_mcp"], env=env))
        return servers + list(agent.mcp)

    async def run_task(self, task: Task, workdir_override: Path | None = None) -> TaskResult:
        agent = self._resolve_agent(task)
        override = workdir_override or (Path(task.meta["workdir"]) if task.meta.get("workdir") else None)
        ws = TaskWorkspace(self.ws_root, task, agent, override)
        self.workspaces[task.id] = ws
        self.task_req[task.id] = task.request_id

        async def emit(typ: str, data: dict) -> None:
            await self.emit(Event(type=typ, task_id=task.id, agent_id=agent.id, request_id=task.request_id, data=data))

        await emit("agent.status", {"state": "queued"})
        async with self.sem:
            await emit("agent.status", {"state": "working", "task": task.meta.get("title") or short(task.prompt, 120)})
            prompt = ws.write_task_md()
            if agent.contract and agent.contract.skill_dir:
                ws.install_skill(Path(agent.contract.skill_dir))
            extra_dirs = [str(self.s.path(d)) for d in [*agent.project_dirs, *task.meta.get("project_dirs", [])]]
            env = {
                "LABHQ_BROKER_URL": self.broker.url, "LABHQ_BROKER_TOKEN": self.broker.token,
                "LABHQ_TASK_ID": task.id, "LABHQ_AGENT_ID": agent.id, "LABHQ_WORKDIR": str(ws.dir),
                "LABHQ_EXTRA_ROOTS": os.pathsep.join(extra_dirs),
            }
            if self.s.config_path:
                env["LABHQ_CONFIG"] = self.s.config_path
            ctx = RunContext(
                task=task, agent=agent, workdir=ws.dir, settings=self.s,
                mcp_servers=self._mcp_servers(agent, env),
                env={**env, "MCP_TOOL_TIMEOUT": str((self.s.policy.approvals.timeout_s + 120) * 1000)},
                emit=emit, prompt=prompt, extra_dirs=extra_dirs,
                claude_settings=claude_settings(self.s.policy),
                use_permission_tool="approval" in agent.builtin_mcp,
            )
            ws.update_run(task.id, started_at=time.time(), engine=agent.engine.value, model=agent.model,
                          resume_of=task.resume_session_id, kind=task.meta.get("kind"))
            result = await get_adapter(agent.engine, self.s).run(ctx)

        pending = [jid for jid, j in self.jobs.items() if j["task_id"] == task.id and not j["terminal"]]
        for jid in pending:
            self.jobs[jid].update(session_id=result.session_id, workdir=str(ws.dir))
        result.pending_jobs, result.workdir = pending, str(ws.dir)
        (ws.dir / "outputs" / f"RESULT_{task.id}.md").write_text(result.text or "")
        (ws.dir / "outputs" / "RESULT.md").write_text(result.text or "")
        ws.update_run(task.id, ended_at=time.time(), ok=result.ok, error=result.error, cost_usd=result.cost_usd,
                      session_id=result.session_id, pending_jobs=pending)
        state = "hibernating" if pending else ("done" if result.ok else "error")
        extra = {"jobs": pending} if pending else ({"error": short(result.error, 200)} if result.error else {})
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

    async def _on_tool_event(self, body: dict) -> None:
        tid = body.get("task_id")
        await self.emit(Event(type=body.get("type", "agent.log"), task_id=tid, agent_id=body.get("agent_id"),
                              request_id=self.task_req.get(tid or ""), data=body.get("data") or {}))

    async def _on_track(self, body: dict) -> None:
        jid, tid = str(body["job_id"]), body.get("task_id")
        self.jobs[jid] = {"job_id": jid, "task_id": tid, "agent_id": body.get("agent_id"),
                          "name": body.get("name", ""), "state": "queued", "missing": 0, "terminal": False,
                          "exit_status": None, "submitted_at": time.time()}
        ws = self.workspaces.get(tid or "")
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

        by_task: dict[str, list[dict]] = {}
        for j in self.jobs.values():
            by_task.setdefault(j["task_id"], []).append(j)
        for tid, js in by_task.items():
            if tid in self.notified or not all(x["terminal"] for x in js):
                continue
            t = self.tasks.get(tid)
            if t is not None and not t.done():  # the agent is still talking; wake it after it ends
                continue
            self.notified.add(tid)
            await self.emit(Event(
                type="jobs.finished", task_id=tid, agent_id=js[0]["agent_id"], request_id=self.task_req.get(tid),
                data={"jobs": [{k: x.get(k) for k in ("job_id", "name", "state", "exit_status")} for x in js],
                      "session_id": js[0].get("session_id"), "workdir": js[0].get("workdir")},
            ))
