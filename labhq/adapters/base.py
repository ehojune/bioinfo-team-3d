from __future__ import annotations

import asyncio
import json
import math
import ntpath
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

from ..facilities import signatures as env_signatures
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


@dataclass(frozen=True)
class _ProcessInfo:
    pid: int
    ppid: int
    name: str
    command_line: str | None


def _windows_process_snapshot() -> list[_ProcessInfo] | None:
    """Read parent, image and command line together. Missing command lines stay unknown, never idle."""
    script = (
        "$ErrorActionPreference='Stop';"
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = json.loads(completed.stdout.decode("utf-8-sig"))
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError):
        return None
    rows = payload if isinstance(payload, list) else [payload]
    processes = []
    for row in rows:
        try:
            processes.append(_ProcessInfo(pid=int(row["ProcessId"]), ppid=int(row["ParentProcessId"]),
                                          name=str(row.get("Name") or ""),
                                          command_line=row.get("CommandLine")))
        except (KeyError, TypeError, ValueError):
            continue
    return processes


def _procfs_process_snapshot() -> list[_ProcessInfo] | None:
    root = Path("/proc")
    if not root.is_dir():
        return None
    processes = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_line = (entry / "stat").read_text(encoding="utf-8", errors="surrogateescape")
            close = stat_line.rfind(")")
            if close < 0:
                continue
            fields = stat_line[close + 2:].split()
            pid, ppid = int(entry.name), int(fields[1])
            name = stat_line[stat_line.find("(") + 1:close]
            try:
                raw = (entry / "cmdline").read_bytes()
                command_line = " ".join(part.decode("utf-8", errors="replace")
                                        for part in raw.split(b"\0") if part)
            except OSError:
                command_line = None
            processes.append(_ProcessInfo(pid, ppid, name, command_line))
        except (OSError, IndexError, ValueError):
            continue  # processes can exit while /proc is being read
    return processes


def _ps_process_snapshot() -> list[_ProcessInfo] | None:
    """BSD/macOS fallback where /proc is absent."""
    try:
        completed = subprocess.run(
            ["ps", "-ww", "-axo", "pid=,ppid=,comm=,args="], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    processes = []
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) < 3:
            continue
        try:
            processes.append(_ProcessInfo(int(fields[0]), int(fields[1]), fields[2],
                                          fields[3] if len(fields) == 4 else None))
        except ValueError:
            continue
    return processes


def _process_snapshot() -> list[_ProcessInfo] | None:
    if os.name == "nt":
        return _windows_process_snapshot()
    if os.name == "posix":
        return _procfs_process_snapshot() or _ps_process_snapshot()
    return None


def _executable_name(value: str) -> str:
    return ntpath.basename(value.strip("\"'").replace("/", "\\")).casefold().removesuffix(".exe")


def _command_tokens(command_line: str | None) -> list[str]:
    if not command_line:
        return []
    try:
        return [token.strip("\"'") for token in shlex.split(command_line, posix=os.name != "nt")]
    except ValueError:
        return []


def _mcp_helper(process: _ProcessInfo, servers: list[McpServerSpec]) -> bool:
    """Match only a configured stdio server's exact command and args; a loose name match is not enough."""
    actual = _command_tokens(process.command_line)
    for server in servers:
        if server.type != "stdio" or not server.command:
            continue
        expected_name = _executable_name(os.path.expandvars(os.path.expanduser(server.command)))
        expected_args = [os.path.expandvars(os.path.expanduser(arg)) for arg in server.args]
        for index, token in enumerate(actual):
            if _executable_name(token) != expected_name or len(actual) < index + 1 + len(expected_args):
                continue
            found = actual[index + 1:index + 1 + len(expected_args)]
            if all((left.casefold() == right.casefold()) if os.name == "nt" else (left == right)
                   for left, right in zip(found, expected_args)):
                return True
    return False


def _idle_process_tree(root_pid: int, servers: list[McpServerSpec]) -> bool | None:
    """True only when the CLI has no descendants except known resident helpers; None means unreadable."""
    snapshot = _process_snapshot()
    if snapshot is None:
        return None
    by_pid = {process.pid: process for process in snapshot}
    if root_pid not in by_pid:
        return None
    children: dict[int, list[_ProcessInfo]] = {}
    for process in snapshot:
        children.setdefault(process.ppid, []).append(process)
    pending = list(children.get(root_pid, []))
    descendants = []
    seen = set()
    while pending:
        process = pending.pop()
        if process.pid in seen:
            continue
        seen.add(process.pid)
        descendants.append(process)
        pending.extend(children.get(process.pid, []))
    for process in descendants:
        name = _executable_name(process.name)
        if name in {"conhost", "codex-code-mode-host"} or _mcp_helper(process, servers):
            continue
        if not name or process.command_line is None:
            return None
        return False
    return True


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


_VERSION_TEXT = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?")
_APP_VERSIONS: dict[tuple[str, int, int], str | None] = {}


def version_key(text: str | None) -> tuple | None:
    """Semver order for a `--version` string (0.159.0-alpha.12 < 0.159.0); None when it names no version."""
    match = _VERSION_TEXT.search(text or "")
    if not match:
        return None
    release = tuple(int(part or 0) for part in match.group(1, 2, 3))
    pre = match.group(4)
    if not pre:
        return release, (1,)
    return release, (0, *((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))


def _app_version(executable: Path) -> str | None:
    """`codex.exe --version` of one app folder, read once per binary per process."""
    try:
        info = executable.stat()
    except OSError:
        return None
    key = (str(executable), info.st_mtime_ns, info.st_size)
    if key not in _APP_VERSIONS:
        try:
            done = subprocess.run([str(executable), "--version"], capture_output=True, text=True, errors="replace",
                                  timeout=5, stdin=subprocess.DEVNULL)
            _APP_VERSIONS[key] = (done.stdout or done.stderr).strip()[:300] if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            _APP_VERSIONS[key] = None
    return _APP_VERSIONS[key]


def codex_app_choice(env: dict[str, str]) -> dict | None:
    """The Codex app folder `bin: auto` runs (#328). Only a folder holding codex.exe counts: an update replaces the
    hash folder and can leave the old one without it. Among those, the highest `--version`; when any version cannot
    be compared, the newest folder (directory mtime, never the name)."""
    local = env.get("LOCALAPPDATA")
    if not _is_windows() or not local:
        return None
    candidates = []
    try:
        for directory in (Path(local) / "OpenAI" / "Codex" / "bin").iterdir():
            try:
                executable = directory / "codex.exe"
                if directory.is_dir() and executable.is_file():
                    candidates.append((directory.stat().st_mtime_ns, executable))
            except OSError:
                continue
    except OSError:
        return None
    if not candidates:
        return None
    keyed = [(version_key(_app_version(exe)), mtime, exe) for mtime, exe in candidates]
    if all(key is not None for key, _, _ in keyed):
        chosen, by = max(keyed, key=lambda item: (item[0], item[1]))[2], "version"
    else:
        chosen, by = max(candidates, key=lambda item: item[0])[1], "mtime"
    return {"path": str(chosen), "folder": chosen.parent.name, "candidates": len(candidates), "by": by}


def _codex_app_executable(env: dict[str, str]) -> str | None:
    choice = codex_app_choice(env)
    return choice["path"] if choice else None


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


def argument_byte_limit() -> int | None:
    """UTF-8 bytes allowed in one argv element; Linux MAX_ARG_STRLEN is 128 KiB."""
    return 131_072 if os.name != "nt" else None


def _command_too_long(cmd: list[str]) -> tuple[int, int, str] | None:
    limit = command_line_limit()
    if limit is not None:
        length = len(subprocess.list2cmdline(cmd).encode("utf-16-le")) // 2  # CreateProcessW counts UTF-16 units
        if length > limit:
            return length, limit, "Windows command-line UTF-16 units"
    limit = argument_byte_limit()
    if limit is not None:
        length = max((len(arg.encode("utf-8")) for arg in cmd), default=0)
        if length > limit:
            return length, limit, "POSIX argument UTF-8 bytes"
    return None


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
- A lookup that failed (error, timeout, refused access) is a failure, never a negative result; report it as one.
  A search that found nothing goes in the report with what was searched: source, query, scope and filters.
- If you fall back to a weaker method (a substitute tool, a subsample, a simpler model), state it and why in
  your report; never switch silently.
- Report in Korean; keep technical terms, gene names and commands in English.
- On GitHub PRs, every comment that contains @codex starts a separate Codex review session. Reply to
  Codex review findings without @codex; after pushing all fixes, post `@codex review` once at the top
  of the PR, and not again while a review is still running.
"""

GENERAL_RESULT_RULES = (
    "- Save a factual claim to a file before stating it, and cite that path under ## Evidence.\n"
)


# The rest of the lab rules for staff that write (not read-only tasks). Writes outside the workspace and paths
# built from shell variables each raised a PI approval card in the 2nd mock trial (2026-10-03).
WORKSPACE_WRITE_RULES = (
    "- Put temporary files and scripts under ./.tmp/ in your workspace; writing to /tmp or %TEMP% outside it needs PI "
    "approval.\n"
    "- In shell commands, write paths relative to your workspace, not built from shell variables; the approval gate "
    "cannot resolve a variable path and asks the PI.\n"
    "- Wait for every command to finish and verify its result before ending your turn; ending the turn stops "
    "background work and finishes the step with only the outputs present then. Use labhq_hpc tools for long-running "
    "compute, or ask the PI before proceeding.\n"
    "- If the plan has an environment step, run packages from its interpreter, by the path it reported, but never "
    "install into that shared environment. For an extra package the PI approved, install it into ./.pylib in your "
    "own workspace (python -m pip install --target ./.pylib ...) and put ./.pylib first on PYTHONPATH. For R use "
    "./.rlib explicitly; never write into another step's workspace. Record task-local Python packages with "
    "`python -m pip freeze --path ./.pylib` (or the package table from ./.rlib) in outputs/env/<step id>.txt.\n")


def private_paths_section(labels: list[str] | tuple[str, ...], saved_output_open: bool = False) -> str:
    """The staff rule for PI personal paths (policy.private_paths). Only `~` and role labels: role files and
    prompts can reach round records and public reports, so no absolute home path goes in. `saved_output_open`:
    a Claude task whose own project folder in the staff config folder is open for reading (#298 ⑤)."""
    if not labels:
        return ""
    lines = ["", "## PI 개인 파일 (모든 직원)",
             "- 직원은 PI 계정으로 실행되지만 PI 개인 파일은 작업 범위 밖입니다. 아래 경로는 읽지도 쓰지도 목록을 보지도 않고, "
             "셸·스크립트로 돌아가 열지도 않습니다.",
             '- 작업에 꼭 필요해 보이면 손대기 전에 labhq_ask(to="cso")로 묻습니다.',
             "- 대상: " + ", ".join(f"`{label}`" for label in labels)]
    if saved_output_open:
        lines.append("- Claude가 긴 도구 출력을 따로 저장한 파일(직원 설정 폴더 `projects/` 아래 이 작업 몫)은 Read로 다시 읽을 수 "
                     "있습니다. 그 폴더의 다른 파일은 열지 않습니다.")
    elif "~/.claude" in labels:
        lines.append("- 긴 명령 출력은 작업 폴더 안 파일로 남겨 읽습니다. Claude가 `~/.claude` 아래에 따로 저장한 긴 출력은 다시 열리지 않을 수 있습니다.")
    return "\n".join(lines) + "\n"


def role_footer(ctx: "RunContext") -> str:
    """ROLE_FOOTER, the write rules unless the task is read-only, and this task's personal-path section."""
    return (ROLE_FOOTER + (GENERAL_RESULT_RULES if ctx.task.meta.get("general_result_contract") else "") +
            ("" if ctx.read_only else WORKSPACE_WRITE_RULES) +
            private_paths_section(ctx.private_labels, bool(ctx.private_open_reads)))


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
    # `~` labels of the PI personal paths closed to this task (policy.private_paths); they go in the role footer.
    private_labels: list[str] = field(default_factory=list)
    # The active paths themselves; while any is set Claude pre-approves no shell command and no outside read.
    private_paths: list[str] = field(default_factory=list)
    # Private paths on (not an explicit `policy.private_paths: []`), even with no active path: the same narrowing
    # holds so the gate's registry check sees every shell command (PR #327).
    private_enabled: bool = False
    # Folders inside an active path that this Claude task may read (its project folder in the staff config folder).
    private_open_reads: list[str] = field(default_factory=list)
    # A request with one shared environment sends non-owner install-capable shell rules through approval.
    shared_environment_protected: bool = False
    environment_step: bool = False
    use_permission_tool: bool = False
    plugin_provenance: list[dict] = field(default_factory=list)  # set by preflight; recorded in the run manifest
    record_run: Callable[..., None] | None = None  # runner hook: persist run fields before the CLI starts
    resume_baseline: dict | None = None  # runner-local snapshot, taken before this invocation
    # A consult or follow-up: the agent is `read_only_profile(...)` and the adapter adds the engine's own off switches.
    read_only: bool = False
    # Runner hook, called after prepare() wrote the adapter's files and just before the CLI starts. A non-empty
    # return refuses the run (the read-only file check could not take its baseline).
    before_spawn: Callable[[], str | None] | None = None
    # Set by run(), runner-local: the launcher it started (executable plus prefix args, resolved) and whether the
    # engine reported a shell command that exited 0. The Codex sandbox record reads both (#328).
    started_command: list[str] | None = None
    commands_ran: bool = False

    @property
    def meta_dir(self) -> Path:
        d = plain_directory(self.workdir, ".labhq")  # never a link an earlier run left (#165)
        if d is None:
            raise OwnedPathError("labhq does not write in .labhq: it is a link or not a folder in the workspace")
        return d

    def write_meta(self, name: str, text: str) -> Path:
        """Write one file in the adapter's scratch folder `.labhq/`, never through a link (#165)."""
        return write_owned(self.workdir, f".labhq/{name}", text)



async def pending_uac_prompts() -> bool | None:
    """Whether Windows shows UAC consent prompts now (consent.exe), which can hold a sandboxed CLI (#382)."""
    if os.name != "nt":
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "tasklist", "/FI", "IMAGENAME eq consent.exe", "/NH", "/FO", "CSV",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 15)
    except (OSError, asyncio.TimeoutError):
        return None
    return b"consent.exe" in out.lower()

FAILED_OUTPUTS_KEPT = 1  # only the last failed command counts, and only until a later command succeeds (PR #447)
FAILED_OUTPUT_CHARS = 4000
SHELL_TOOLS = frozenset({"Bash", "PowerShell", "run_shell_command", "run_command", "shell"})


def note_failed_output(st: "RunState", output: Any) -> None:
    """Keep the end of a failed tool call's output for the environment signatures (#35). Claude sends a tool result's
    content as text blocks; other engines send a string."""
    if isinstance(output, list):
        output = "\n".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in output)
    text = "" if output is None else str(output)
    if text.strip():
        st.failed_outputs.append(text[-FAILED_OUTPUT_CHARS:])


def note_command_ok(st: "RunState") -> None:
    """A shell command exited 0 after the failed one: the agent got past it, so the failed output is not evidence."""
    st.failed_outputs.clear()


@dataclass
class RunState:
    text_parts: list[str] = field(default_factory=list)
    last_message: str | None = None  # the turn's last agent message: Codex's own final answer (#330)
    ended_by_guard: bool = False  # labhq ended the process after its final event (exit_grace_s)
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
    commands_ran: bool = False  # a shell command ran to exit 0 (engines that report commands)
    stop_reason: str | None = None  # the adapter saw a failure more turns cannot fix: labhq ends the process tree
    # The tail of the last failed tool call's output (#35), cleared when a later shell command succeeds: environment
    # signatures read it, never successful output.
    failed_outputs: deque = field(default_factory=lambda: deque(maxlen=FAILED_OUTPUTS_KEPT))
    shell_calls: set = field(default_factory=set)  # tool call ids of shell tools, for engines that pair by id


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

    def prompt_pointer_error(self, ctx: RunContext) -> str | None:
        """Reason this engine cannot follow the final task-file pointer prompt."""
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
        ctx.started_command, ctx.commands_ran = None, False
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

        def resolved(command: list[str]) -> tuple[list[str], list[str]]:
            """The full command and its launcher: everything before the adapter's own arguments."""
            arguments = command[1:]
            if engine_bin is not None:
                command = [os.path.expandvars(os.path.expanduser(command[0])), *prefix, *arguments]
            full = _resolve_command(command, env, self.engine)
            return full, full[:len(full) - len(arguments)]

        try:
            cmd, launcher = resolved(cmd)
            if _command_too_long(cmd) and ctx.prompt_pointer and ctx.prompt != ctx.prompt_pointer:
                # Windows refuses the process and Python reports a missing executable (#222): name the task file.
                ctx.prompt = ctx.prompt_pointer
                cmd, launcher = resolved(self.build_command(ctx))
        except ValueError as exc:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, error=str(exc))
        too_long = _command_too_long(cmd)
        if too_long:
            length, limit, unit = too_long
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False,
                              error=f"command line has {length:,} {unit}, over the limit of {limit:,}; "
                                    "shorten the prompt, output schema or settings")
        refused = self.prompt_pointer_error(ctx)
        if refused:
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, error=refused)
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
        except OSError as exc:
            detail = exc.strerror or str(exc)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False,
                              error=f"could not start executable {cmd[0]!r}: {detail}")
        ctx.started_command = launcher
        st = RunState()
        stderr_tail: deque[str] = deque(maxlen=60)
        result_arrived = asyncio.Event()
        ended_after_result = False

        last_output = time.monotonic()

        async def read_out() -> None:
            nonlocal last_output
            assert proc.stdout
            async for raw in proc.stdout:
                last_output = time.monotonic()
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    await self.handle_line(line, st, ctx)
                except Exception as e:  # never let one odd line kill the run
                    await ctx.emit("agent.log", {"level": "debug", "text": f"[unparsed] {short(line)} ({e})"})
                if st.stop_reason and proc.returncode is None:
                    reason, st.stop_reason = st.stop_reason, None
                    await ctx.emit("agent.log", {"level": "warn", "text": reason})
                    await self._kill(proc)
                if st.result_seen:
                    result_arrived.set()

        async def exit_guard() -> None:
            """The final turn event arrived but the CLI stays up (#330): end its tree and keep the result."""
            nonlocal ended_after_result
            await result_arrived.wait()
            grace = self.settings.runner.exit_grace_s
            try:
                await asyncio.wait_for(proc.wait(), grace)
                return
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    return
            ended_after_result = True
            await ctx.emit("agent.log", {"level": "warn", "text": (
                f"turn이 끝났는데 {self.engine} 프로세스가 {grace:g}s 안에 종료하지 않아 labhq가 프로세스 트리를 "
                "끝냈습니다. 받은 결과로 단계를 마칩니다")})
            await self._kill(proc)

        async def read_err() -> None:
            nonlocal last_output
            assert proc.stderr
            async for raw in proc.stderr:
                last_output = time.monotonic()
                stderr_tail.append(raw.decode(errors="replace").rstrip())

        async def feed_stdin() -> None:
            """Write the prompt inside the timed, watched run: a CLI that never reads stdin must not hold labhq before
            task_timeout_s and the stall warning start (PR #433 review)."""
            if payload is None or not proc.stdin:
                return
            try:
                proc.stdin.write(payload)
                await proc.stdin.drain()
                proc.stdin.close()
            except OSError:  # broken pipe or reset, also after a timeout kill
                pass  # the CLI ended or closed stdin; its exit and output say what happened

        async def stall_watch() -> None:
            """Warn once, then end a certainly idle CLI tree so orchestration can retry it (#473)."""
            warn_limit = self.settings.runner.stall_warn_s
            configured_retry = self.settings.runner.stall_retry_s
            retry_limit = max(configured_retry, warn_limit) if configured_retry > 0 else 0
            if warn_limit <= 0 and retry_limit <= 0:
                return
            warned = False
            while True:
                quiet = time.monotonic() - last_output
                if result_arrived.is_set() or proc.returncode is not None:
                    return
                if not warned and warn_limit > 0 and quiet >= warn_limit:
                    minutes = max(1, int(quiet // 60))
                    if await pending_uac_prompts():
                        retry_note = ((" UAC가 사라진 뒤 실행 중인 명령이 없으면 labhq가 끊고 다시 시도합니다. "
                                       "연구 lane에서 단계를 취소하면 요청이 실패로 끝납니다")
                                      if retry_limit > 0 else "")
                        await ctx.emit("agent.log", {"level": "alert", "text": (
                            f"{self.engine} 출력이 {minutes}분째 없고 이 PC에 Windows 권한 알림(UAC)이 승인을 기다립니다. "
                            "Codex sandbox 설정이면 승인 전까지 셸이 멈춥니다 — PC에서 알림을 확인하세요 (#382)."
                            f"{retry_note}")})
                    elif retry_limit > 0:
                        remaining = max(1, math.ceil(max(0, retry_limit - quiet) / 60))
                        await ctx.emit("agent.log", {"level": "warn", "text": (
                            f"{self.engine} 출력이 {minutes}분째 없습니다. 약 {remaining}분 뒤 실행 중인 명령이 없으면 "
                            "labhq가 끊고 다시 시도합니다. 연구 lane에서 단계를 취소하면 요청이 실패로 끝납니다")})
                    else:
                        await ctx.emit("agent.log", {"level": "warn", "text": (
                            f"{self.engine} 출력이 {minutes}분째 없습니다. 긴 명령을 돌리는 중일 수 있고, 멈췄다면 "
                            "단계를 취소하세요")})
                    warned = True
                    if retry_limit <= 0:
                        return
                if retry_limit > 0 and quiet >= retry_limit:
                    if await pending_uac_prompts() is not False:
                        return
                    idle = await asyncio.to_thread(_idle_process_tree, proc.pid, ctx.mcp_servers)
                    if idle is not True:
                        return
                    observed = last_output
                    await asyncio.sleep(0.1)  # close the common command-start race before ending the tree
                    if last_output != observed or result_arrived.is_set() or proc.returncode is not None:
                        continue
                    if await pending_uac_prompts() is not False:
                        return
                    if await asyncio.to_thread(_idle_process_tree, proc.pid, ctx.mcp_servers) is not True:
                        return
                    minutes = max(1, math.ceil((time.monotonic() - last_output) / 60))
                    st.error = f"engine stream timed out: no output for {minutes} min and no running command"
                    await self._kill(proc)
                    return
                due = []
                if not warned and warn_limit > 0:
                    due.append(warn_limit)
                if retry_limit > 0:
                    due.append(retry_limit)
                delay = max(0.01, min(30.0, min(due) - quiet))
                await asyncio.sleep(delay)

        drain = asyncio.gather(feed_stdin(), read_out(), read_err(), proc.wait())
        guard = asyncio.create_task(exit_guard())
        watch = asyncio.create_task(stall_watch())
        try:
            await asyncio.wait_for(asyncio.shield(drain), timeout=self.settings.runner.task_timeout_s)
        except asyncio.TimeoutError:
            st.error = f"timeout after {self.settings.runner.task_timeout_s}s"
            await self._kill(proc)
            await drain
        except asyncio.CancelledError:
            guard.cancel()
            await self._kill(proc)
            await asyncio.shield(drain)
            raise
        finally:
            watch.cancel()
            if not ended_after_result:
                guard.cancel()
        await asyncio.gather(guard, watch, return_exceptions=True)  # a guard that is ending the tree finishes first
        returncode = 0 if ended_after_result else proc.returncode  # ended by labhq after the result: not a failure
        try:
            ctx.write_meta("stderr_tail.txt", "\n".join(stderr_tail))
        except OwnedPathError:  # the agent replaced .labhq with a link while it ran: keep the result, drop the tail
            await ctx.emit("agent.log", {"level": "warn", "text": (
                "작업 폴더의 .labhq가 실행 중에 링크로 바뀌어 stderr 기록을 남기지 않았습니다")})
        stderr = " | ".join(x for x in list(stderr_tail)[-5:] if x)
        st.error = st.error or self.stderr_error(stderr)
        st.ended_by_guard = ended_after_result
        res = self.finalize(st, ctx, returncode)
        ctx.commands_ran = st.commands_ran
        if res.error and stderr and ("empty CLI stream" in res.error or "IneligibleTierError" in stderr):
            res.error = f"{res.error}: {short(stderr, 500)}"
        if returncode not in (0, None) and not res.error:
            res.error = f"exit {returncode}: " + " | ".join(list(stderr_tail)[-5:])
        # The CLI's own stderr counts only when the run failed; a failed command's output counts either way, because
        # an agent that gave up after "No module named x" can still exit 0 with its outputs missing (#35).
        res.environment = env_signatures.scan_run(self.engine, stderr_tail if not res.ok else (), st.failed_outputs)
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
