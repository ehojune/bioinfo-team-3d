from __future__ import annotations

import asyncio
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..models import AgentSpec, McpServerSpec, Task, TaskResult
from ..settings import Settings
from ..util import extract_json, merge_staff_env, short
from .owned import OwnedPathError, plain_directory, write_owned
from .read_only import (labhq_workspace_paths, read_only_engine_env, read_only_launch_error, read_only_mismatch,
                        read_only_workspace_error)

Emit = Callable[[str, dict], Awaitable[None]]  # (event_type, data)

_NPM_NODE_LINE = re.compile(
    r'(?P<launcher>"%_prog%"|"%(?:dp0%|~dp0)[\\/]node\.exe"|node(?:\.exe)?)'
    r'\s+"%(?:dp0%|~dp0)[\\/](?P<script>[^"%\r\n]+?\.(?:js|mjs|cjs))"\s+%\*\s*$',
    re.IGNORECASE,
)


GITHUB_TOKEN_NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")  # gh / git credentials


def _windows_descendants(root_pid: int) -> list[int]:
    """Snapshot descendants before taskkill; some sandboxed Windows hosts deny taskkill /T."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return []
    children: dict[int, list[int]] = {}
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    found, pending = [], list(children.get(root_pid, []))
    while pending:
        pid = pending.pop()
        found.append(pid)
        pending.extend(children.get(pid, []))
    return found


def _npm_script(shim: Path) -> tuple[Path, bool] | None:
    try:
        lines = shim.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return None
    if not any("%dp0%" in line.lower() or "%~dp0" in line.lower() for line in lines):
        return None
    for line in lines:
        match = _NPM_NODE_LINE.search(line.strip())
        if match:
            script = (shim.parent / match.group("script").replace("\\", "/")).resolve()
            if script.is_file():
                launcher = match.group("launcher").lower()
                local_node = "%" in launcher and (launcher != '"%_prog%"' or any(
                    re.search(r'set\s+"?_prog=%(?:dp0%|~dp0)[\\/]node\.exe"?\s*$', line.strip(), re.IGNORECASE)
                    for line in lines))
                return script, local_node
    return None


def _is_windows() -> bool:
    return os.name == "nt"


def _codex_app_executable(env: dict[str, str]) -> str | None:
    """Updates replace the hash directory; select by directory mtime, not its name."""
    local = env.get("LOCALAPPDATA")
    if not _is_windows() or not local:
        return None
    candidates = []
    try:
        for directory in (Path(local) / "OpenAI" / "Codex" / "bin").iterdir():
            try:
                executable = directory / "codex.exe"
                if directory.is_dir() and executable.is_file():
                    candidates.append((directory.stat().st_mtime_ns, str(executable)))
            except OSError:
                continue
    except OSError:
        return None
    return max(candidates)[1] if candidates else None


def _resolve_command(cmd: list[str], env: dict[str, str], engine: str) -> list[str]:
    requested = cmd[0]
    if engine == "codex" and requested.strip().lower() in ("", "auto"):
        requested = _codex_app_executable(env) or "codex"
    executable = shutil.which(requested, path=env.get("PATH")) or requested
    if Path(executable).suffix.lower() not in (".cmd", ".bat"):
        return [executable, *cmd[1:]]
    shim = Path(executable)
    target = _npm_script(shim)
    if target:
        script, local_node = target
        adjacent = (shim.parent / "node.exe").resolve()
        node = str(adjacent) if local_node and adjacent.is_file() else (
            shutil.which("node.exe", path=env.get("PATH")) or shutil.which("node", path=env.get("PATH")))
        if node and Path(node).suffix.lower() not in (".cmd", ".bat"):
            return [node, str(script), *cmd[1:]]
    raise ValueError(f"{engine}: cannot run {executable!r} with agent arguments; "
                     "set engines.<engine>.bin to a native executable or use bin: node with prefix_args")

def command_line_limit() -> int | None:
    """Characters a spawned command line may have. Windows CreateProcess stops at 32,767; POSIX allows far more."""
    return 32_000 if os.name == "nt" else None


def _command_too_long(cmd: list[str]) -> int | None:
    limit = command_line_limit()
    if limit is None:
        return None
    length = len(subprocess.list2cmdline(cmd).encode("utf-16-le")) // 2  # CreateProcessW counts UTF-16 units
    return length if length > limit else None


ROLE_FOOTER = """
## Lab rules (all agents)
- Work inside your task workspace; write deliverables to ./outputs/ and cite their paths.
- Heavy compute or anything touching restricted data goes through the labhq_hpc tools, never inline.
- If environment, installation, login or tool errors block you, use labhq_ask(to="facilities").
- For a method or scope decision use to="cso"; ask a colleague only for a fact only that colleague can answer.
- Use to="pi" only for the hard stops configured for your role.
- bioinfo-agent sends every gate question to the CSO first. Build a missing reusable pipeline when the CSO says to;
  put it under outputs/pipeline/<name>/ with manifest.json so labhq can open the upstream PR.
- Separate observed results from hypotheses. Record tool versions and parameters.
- Report in Korean; keep technical terms, gene names and commands in English.
- On GitHub PRs, every comment that contains @codex starts a separate Codex review session. Reply to
  Codex review findings without @codex; after pushing all fixes, post `@codex review` once at the top
  of the PR, and not again while a review is still running.
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
    # A short prompt that names the task file, used when `prompt` would make the command line too long (#222).
    prompt_pointer: str | None = None
    # Readable, never writable (#36 path references). Claude gets --add-dir plus deny rules; Codex reads
    # outside its workspace without --add-dir, which would grant write access.
    read_dirs: list[str] = field(default_factory=list)
    claude_settings: dict = field(default_factory=dict)
    use_permission_tool: bool = False
    plugin_provenance: list[dict] = field(default_factory=list)  # set by preflight; recorded in the run manifest
    record_run: Callable[..., None] | None = None  # runner hook: persist run fields before the CLI starts
    resume_baseline: dict | None = None  # runner-local snapshot, taken before this invocation
    # A consult or follow-up: the agent is `read_only_profile(...)` and the adapter adds the engine's own off switches.
    read_only: bool = False
    # Runner hook, called after prepare() wrote the adapter's files and just before the CLI starts. A non-empty
    # return refuses the run (the read-only file check could not take its baseline).
    before_spawn: Callable[[], str | None] | None = None

    @property
    def meta_dir(self) -> Path:
        d = plain_directory(self.workdir, ".labhq")  # never a link an earlier run left (#165)
        if d is None:
            raise OwnedPathError("labhq does not write in .labhq: it is a link or not a folder in the workspace")
        return d

    def write_meta(self, name: str, text: str) -> Path:
        """Write one file in the adapter's scratch folder `.labhq/`, never through a link (#165)."""
        return write_owned(self.workdir, f".labhq/{name}", text)


@dataclass
class RunState:
    text_parts: list[str] = field(default_factory=list)
    final_text: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    structured: Any = None
    error: str | None = None
    usage: dict = field(default_factory=dict)
    usage_known: bool = True
    session_cost_total: float | None = None
    session_usage_total: dict | None = None
    result_seen: bool = False
    model_id: str | None = None


def token_counts(raw: dict | None, fields: tuple[str, ...]) -> dict[str, int]:
    """Keep only numeric counters observed in an engine's stream."""
    source = raw if isinstance(raw, dict) else {}
    return {key: value for key in fields if isinstance((value := source.get(key)), int)
            and not isinstance(value, bool) and value >= 0}


def session_baseline(st: RunState, ctx: RunContext) -> dict | None:
    baseline = ctx.resume_baseline
    if baseline and baseline.get("session_id") == st.session_id == ctx.task.resume_session_id:
        return baseline
    return None


def cumulative_cost(total: Any, st: RunState, ctx: RunContext) -> None:
    """Unknown resume deltas stay unknown; never bill a session total as a new call."""
    valid = isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total) and total >= 0
    st.session_cost_total = total if valid else None
    baseline = session_baseline(st, ctx)
    before = baseline.get("session_cost_total") if baseline else None
    if not ctx.task.resume_session_id:
        before = 0.0
    known = (valid and isinstance(before, (int, float)) and not isinstance(before, bool)
             and math.isfinite(before) and 0 <= before <= total)
    st.cost_usd = total - before if known else None


def cumulative_usage(total: dict[str, int], st: RunState, ctx: RunContext) -> dict[str, int]:
    """Return an event delta and keep the full invocation delta in RunState."""
    previous = st.session_usage_total
    baseline = session_baseline(st, ctx)
    before = baseline.get("session_usage_total") if baseline else None
    if not ctx.task.resume_session_id:
        before = {key: 0 for key in total}
    known = bool(total) and isinstance(before, dict) and all(
        type(before.get(key)) is int and 0 <= before[key] <= value for key, value in total.items())
    st.usage_known = known
    st.usage = {key: value - before[key] for key, value in total.items()} if known else {}
    event_before = previous if previous is not None else before
    event_known = known and isinstance(event_before, dict) and all(
        type(event_before.get(key)) is int and 0 <= event_before[key] <= value for key, value in total.items())
    st.session_usage_total = total
    return {key: value - event_before[key] for key, value in total.items()} if event_known else {}


def record_accounting(st: RunState, ctx: RunContext) -> None:
    if ctx.record_run:
        ctx.record_run(session_id=st.session_id, session_cost_total=st.session_cost_total,
                       session_usage_total=st.session_usage_total, accounting_at=time.time(),
                       cost_usd=st.cost_usd, cost_known=st.cost_usd is not None,
                       usage=st.usage, usage_known=st.usage_known)


def record_model_id(st: RunState, ctx: RunContext, value: Any) -> None:
    """Persist a resolved model only when the CLI reports one."""
    if not isinstance(value, str) or not value.strip() or value == st.model_id:
        return
    st.model_id = value
    if ctx.record_run:
        ctx.record_run(model_id=value)


def wrap_cwd(spec: McpServerSpec) -> tuple[str, list[str]]:
    """Not every CLI supports a per-server cwd; wrap with `bash -lc 'cd … && exec …'`."""
    if not spec.cwd:
        return spec.command or "", list(spec.args)
    inner = f"cd {shlex.quote(spec.cwd)} && exec {shlex.join([spec.command or '', *spec.args])}"
    return "bash", ["-lc", inner]


def expand_env(env: dict[str, str], source: dict[str, str] | None = None) -> dict[str, str]:
    if source is None:
        return {k: os.path.expandvars(v) for k, v in env.items()}
    # Match the environment passed to the child, including engine and task overrides.
    return {k: re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                      lambda m: source.get(m.group(1), m.group(0)), v) for k, v in env.items()}


def child_config_dirs(env: dict[str, str], cwd: Path, var: str, default_name: str) -> list[Path]:
    """Every config dir the child CLI might use, so checks and excludes fail closed.

    `var` in the child's env (relative → its cwd) is authoritative. Otherwise the home is resolved
    differently per CLI and OS (HOME vs USERPROFILE), so every candidate from the merged env counts.
    """
    raw = env.get(var)
    if raw:
        p = Path(os.path.expanduser(raw))
        return [p if p.is_absolute() else cwd / p]
    homes = [Path(h) for h in (env.get("HOME"), env.get("USERPROFILE")) if h] or [Path.home()]
    return list(dict.fromkeys(h / default_name for h in homes))


class AgentAdapter(ABC):
    engine = "base"
    supports_resume = True
    # True only when the engine itself (sandbox, tool list, hooks off) keeps `read_only_profile` read-only,
    # not just the prompt. Such an adapter must honour `ctx.read_only` in build_command.
    enforces_read_only = False

    def __init__(self, settings: Settings):
        self.settings = settings

    def prepare(self, ctx: RunContext) -> None:
        """Write engine-specific config files into the workspace."""

    def engine_env(self) -> dict[str, str]:
        b = getattr(self.settings.engines, self.engine, None)
        return expand_env(b.env) if b is not None else {}

    def staff_env(self, ctx: RunContext) -> dict[str, str]:
        """The CLI's env: the runner's own without parent session markers, then engine env, then task env.
        A read-only run takes only READ_ONLY_ENV_KEEP from the engine env (#145)."""
        engine = self.engine_env()
        if ctx.read_only:
            engine = read_only_engine_env(engine)[0]
        env = merge_staff_env(dict(os.environ), engine, ctx.env)
        # Staff never hold a GitHub credential: gh reads GH_TOKEN before GITHUB_TOKEN, so drop every name it uses (#301).
        drop = {self.settings.github.token_env.casefold(), *(name.casefold() for name in GITHUB_TOKEN_NAMES)}
        return {key: value for key, value in env.items() if key.casefold() not in drop}

    def stdin_payload(self, ctx: RunContext) -> bytes | None:
        """Bytes to write to the agent's stdin (then closed); None → stdin is /dev/null."""
        return None

    def stderr_error(self, stderr: str) -> str | None:
        return None

    def preflight_error(self, ctx: RunContext, env: dict[str, str]) -> str | None:
        """Reason to refuse the run before anything is written or spawned."""
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
        if returncode == 0 and not st.error and not st.result_seen and not text.strip():
            st.error = "empty CLI stream: no result event or text"
            ok = False
        return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=ok, text=text or "",
                          structured=structured, session_id=st.session_id, cost_usd=st.cost_usd,
                          cost_known=st.cost_usd is not None, usage=st.usage, usage_known=st.usage_known,
                          error=st.error, error_kind=getattr(st, "error_kind", None))

    async def run(self, ctx: RunContext) -> TaskResult:
        env = self.staff_env(ctx)
        engine_bin = getattr(self.settings.engines, self.engine, None)
        prefix = [os.path.expandvars(os.path.expanduser(arg)) for arg in (engine_bin.prefix_args if engine_bin else [])]
        refused = ((read_only_mismatch(ctx.agent, ctx.mcp_servers) or read_only_launch_error(self.engine, prefix)
                    or read_only_workspace_error(
                        self.engine, ctx.workdir, labhq_workspace_paths(ctx.agent, self.engine, ctx.workdir)))
                   if ctx.read_only else None) or self.preflight_error(ctx, env)
        if refused:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, error=refused)
        dropped = read_only_engine_env(self.engine_env())[1] if ctx.read_only else []
        if dropped:  # names only: values can be secrets
            await ctx.emit("agent.log", {"level": "warn", "text": (
                f"읽기 전용 실행이라 engines.{self.engine}.env에서 {', '.join(dropped)}를 뺐습니다. "
                "로그인·설정 위치·API 접속 변수만 씁니다")})
        self.prepare(ctx)  # may add to ctx.env (engine: cli puts CliSpec.env there)
        cmd = self.build_command(ctx)
        env = self.staff_env(ctx)

        def resolved(command: list[str]) -> list[str]:
            if engine_bin is not None:
                command = [os.path.expandvars(os.path.expanduser(command[0])), *prefix, *command[1:]]
            return _resolve_command(command, env, self.engine)

        try:
            cmd = resolved(cmd)
            if _command_too_long(cmd) and ctx.prompt_pointer and ctx.prompt != ctx.prompt_pointer:
                # Windows refuses the process and Python reports a missing executable (#222): name the task file.
                ctx.prompt = ctx.prompt_pointer
                cmd = resolved(self.build_command(ctx))
        except ValueError as exc:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, error=str(exc))
        too_long = _command_too_long(cmd)
        if too_long:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False,
                              error=f"command line is {too_long:,} UTF-16 units, over the Windows limit of "
                                    f"{command_line_limit():,}; shorten the prompt, output schema or settings")
        ctx.write_meta("command.txt", shlex.join(short(a, 200) if len(a) > 200 else a for a in cmd))
        await ctx.emit("agent.log", {"level": "debug", "text": f"$ {ctx.agent.engine.value} ({len(cmd)} args)"})

        payload = self.stdin_payload(ctx)
        refused = ctx.before_spawn() if ctx.before_spawn else None
        if refused:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, error=refused)
        try:
            group_args = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                          if os.name == "nt" else {"start_new_session": True})
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ctx.workdir), env=env,
                stdin=asyncio.subprocess.PIPE if payload is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=32 * 1024 * 1024, **group_args,
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

        drain = asyncio.gather(read_out(), read_err(), proc.wait())
        try:
            await asyncio.wait_for(asyncio.shield(drain), timeout=self.settings.runner.task_timeout_s)
        except asyncio.TimeoutError:
            st.error = f"timeout after {self.settings.runner.task_timeout_s}s"
            await self._kill(proc)
            await drain
        except asyncio.CancelledError:
            await self._kill(proc)
            await asyncio.shield(drain)
            raise
        try:
            ctx.write_meta("stderr_tail.txt", "\n".join(stderr_tail))
        except OwnedPathError:  # the agent replaced .labhq with a link while it ran: keep the result, drop the tail
            await ctx.emit("agent.log", {"level": "warn", "text": (
                "작업 폴더의 .labhq가 실행 중에 링크로 바뀌어 stderr 기록을 남기지 않았습니다")})
        stderr = " | ".join(x for x in list(stderr_tail)[-5:] if x)
        st.error = st.error or self.stderr_error(stderr)
        res = self.finalize(st, ctx, proc.returncode)
        if res.error and stderr and ("empty CLI stream" in res.error or "IneligibleTierError" in stderr):
            res.error = f"{res.error}: {short(stderr, 500)}"
        if proc.returncode not in (0, None) and not res.error:
            res.error = f"exit {proc.returncode}: " + " | ".join(list(stderr_tail)[-5:])
        return res

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if os.name == "nt":
            if proc.returncode is not None:
                return
            descendants = _windows_descendants(proc.pid)
            returncode = None
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/T", "/F", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(killer.wait(), 5)
                returncode = killer.returncode
            # Windows can report ERROR_NOT_ENOUGH_MEMORY as MemoryError while
            # starting taskkill.  The direct PID fallback must still reap the
            # already-snapshotted tree instead of abandoning cancellation.
            except (OSError, MemoryError, asyncio.TimeoutError):
                pass
            if returncode != 0:
                for pid in reversed(descendants):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
            return

        pgid = proc.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                pass

        def group_exists() -> bool:
            try:
                os.killpg(pgid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True

        deadline = asyncio.get_running_loop().time() + 0.2
        while group_exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        if group_exists():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
