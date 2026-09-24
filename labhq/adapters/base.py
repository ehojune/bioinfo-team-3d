from __future__ import annotations

import asyncio
import os
import shlex
import signal
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..models import AgentSpec, McpServerSpec, Task, TaskResult
from ..settings import Settings
from ..util import extract_json, short

Emit = Callable[[str, dict], Awaitable[None]]  # (event_type, data)

ROLE_FOOTER = """
## Lab rules (all agents)
- Work inside your task workspace; write deliverables to ./outputs/ and cite their paths.
- Heavy compute or anything touching restricted data goes through the labhq_hpc tools, never inline.
- Separate observed results from hypotheses. Record tool versions and parameters.
- Report in Korean; keep technical terms, gene names and commands in English.
- When a GitHub PR comment addresses Codex, always mention @codex — even when replying directly
  under Codex's own comment.
"""


@dataclass
class RunContext:
    task: Task
    agent: AgentSpec
    workdir: Path
    settings: Settings
    mcp_servers: list[McpServerSpec]
    env: dict[str, str]
    emit: Emit
    prompt: str
    extra_dirs: list[str] = field(default_factory=list)
    claude_settings: dict = field(default_factory=dict)
    use_permission_tool: bool = False

    @property
    def meta_dir(self) -> Path:
        d = self.workdir / ".labhq"
        d.mkdir(exist_ok=True)
        return d


@dataclass
class RunState:
    text_parts: list[str] = field(default_factory=list)
    final_text: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    structured: Any = None
    error: str | None = None
    usage: dict = field(default_factory=dict)


def wrap_cwd(spec: McpServerSpec) -> tuple[str, list[str]]:
    """Not every CLI supports a per-server cwd; wrap with `bash -lc 'cd … && exec …'`."""
    if not spec.cwd:
        return spec.command or "", list(spec.args)
    inner = f"cd {shlex.quote(spec.cwd)} && exec {shlex.join([spec.command or '', *spec.args])}"
    return "bash", ["-lc", inner]


def expand_env(env: dict[str, str]) -> dict[str, str]:
    return {k: os.path.expandvars(v) for k, v in env.items()}


class AgentAdapter(ABC):
    engine = "base"
    supports_resume = True

    def __init__(self, settings: Settings):
        self.settings = settings

    def prepare(self, ctx: RunContext) -> None:
        """Write engine-specific config files into the workspace."""

    def engine_env(self) -> dict[str, str]:
        return {}

    def stdin_payload(self, ctx: RunContext) -> bytes | None:
        """Bytes to write to the agent's stdin (then closed); None → stdin is /dev/null."""
        return None

    @abstractmethod
    def build_command(self, ctx: RunContext) -> list[str]: ...

    @abstractmethod
    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None: ...

    def finalize(self, st: RunState, ctx: RunContext, returncode: int | None) -> TaskResult:
        text = st.final_text if st.final_text is not None else "".join(st.text_parts)
        structured = st.structured
        if structured is None and ctx.task.output_schema:
            structured = extract_json(text)
        ok = returncode == 0 and st.error is None
        return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=ok, text=text or "",
                          structured=structured, session_id=st.session_id, cost_usd=st.cost_usd,
                          error=st.error)

    async def run(self, ctx: RunContext) -> TaskResult:
        self.prepare(ctx)
        cmd = self.build_command(ctx)
        env = {**os.environ, **self.engine_env(), **ctx.env}
        (ctx.meta_dir / "command.txt").write_text(shlex.join(short(a, 200) if len(a) > 200 else a for a in cmd))
        await ctx.emit("agent.log", {"level": "debug", "text": f"$ {ctx.agent.engine.value} ({len(cmd)} args)"})

        payload = self.stdin_payload(ctx)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ctx.workdir), env=env,
                stdin=asyncio.subprocess.PIPE if payload is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=32 * 1024 * 1024, start_new_session=True,
            )
        except FileNotFoundError:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False,
                              error=f"executable not found: {cmd[0]!r} — install it on the runner or fix "
                                    f"engines/cli settings for agent {ctx.agent.id!r}")
        if payload is not None and proc.stdin:
            proc.stdin.write(payload)
            await proc.stdin.drain()
            proc.stdin.close()
        st = RunState()
        stderr_tail: deque[str] = deque(maxlen=60)

        async def read_out() -> None:
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    await self.handle_line(line, st, ctx)
                except Exception as e:  # never let one odd line kill the run
                    await ctx.emit("agent.log", {"level": "debug", "text": f"[unparsed] {short(line)} ({e})"})

        async def read_err() -> None:
            assert proc.stderr
            async for raw in proc.stderr:
                stderr_tail.append(raw.decode(errors="replace").rstrip())

        try:
            await asyncio.wait_for(asyncio.gather(read_out(), read_err(), proc.wait()),
                                   timeout=self.settings.runner.task_timeout_s)
        except asyncio.TimeoutError:
            st.error = f"timeout after {self.settings.runner.task_timeout_s}s"
            await self._kill(proc)
        except asyncio.CancelledError:
            await self._kill(proc)
            raise
        (ctx.meta_dir / "stderr_tail.txt").write_text("\n".join(stderr_tail))
        res = self.finalize(st, ctx, proc.returncode)
        if proc.returncode not in (0, None) and not res.error:
            res.error = f"exit {proc.returncode}: " + " | ".join(list(stderr_tail)[-5:])
        return res

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), 10)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
