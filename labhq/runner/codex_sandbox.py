"""Whether a staff CODEX_HOME's elevated sandbox setup still fits the Codex in use (#328).

Codex keeps its own `.sandbox/setup_marker.json`, but an app update can leave that setup incompatible ("sandbox users
missing or incompatible with marker version") and only an administrator prompt fixes it. labhq cannot ask Codex
unattended, so the runner writes `.labhq-sandbox-ok.json` beside it after an elevated run in which a shell command
ran, with the version of the Codex build that run started. doctor compares that version with the current `codex --version`, and the first run that fails for
this reason alerts the PI once per runner process with the commands to set it up again.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from ..adapters import codex as codex_adapter
from ..adapters.base import _resolve_command, child_config_dirs
from ..models import ApprovalRequest
from ..settings import Settings
from .versions import _probe, _version

OK_FILE = ".labhq-sandbox-ok.json"
APP_BIN = ("OpenAI", "Codex", "bin")
_VERSIONS: dict[tuple, str | None] = {}


def elevated_homes(settings: Settings, env: dict[str, str], workdir: Path, read_only: bool = False) -> list[Path]:
    """The CODEX_HOMEs a run with `windows.sandbox: elevated` uses; empty when that sandbox is not in play."""
    b = settings.engines.codex
    if not (codex_adapter._is_windows() and b.windows_sandbox == "elevated" and (b.isolate_user_config or read_only)):
        return []
    return child_config_dirs(env, workdir, "CODEX_HOME", ".codex")


def codex_command(settings: Settings, env: dict[str, str]) -> list[str] | None:
    """The command the adapter would start, resolved the same way (`auto` → Windows app folder)."""
    b = settings.engines.codex
    cmd = [os.path.expandvars(os.path.expanduser(b.bin)),
           *(os.path.expandvars(os.path.expanduser(a)) for a in b.prefix_args)]
    try:
        return _resolve_command(cmd, env, "codex")
    except (ValueError, OSError):
        return None


def codex_version(cmd: list[str], env: dict[str, str]) -> str | None:
    """`--version` of that command, read once per binary per runner process."""
    try:
        stamp = Path(cmd[0]).stat().st_mtime_ns
    except OSError:
        stamp = None
    key = (*cmd, stamp)
    if key not in _VERSIONS:
        code, raw = _probe([*cmd, "--version"], env)
        version = _version(raw) if code == 0 else "unreported"
        _VERSIONS[key] = None if version == "unreported" else version
    return _VERSIONS[key]


def app_relative(executable: str, env: dict[str, str]) -> str | None:
    """`<hash>/codex.exe` when the executable is in the Windows app folder, else None."""
    local = env.get("LOCALAPPDATA")
    if not local:
        return None
    try:
        return Path(executable).relative_to(Path(local).joinpath(*APP_BIN)).as_posix()
    except ValueError:
        return None


def executable_label(executable: str, env: dict[str, str]) -> str:
    relative = app_relative(executable, env)
    return "%LOCALAPPDATA%/OpenAI/Codex/bin/" + relative if relative else Path(executable).name


def _same_command(a: list[str], b: list[str]) -> bool:
    return len(a) == len(b) and all(os.path.normcase(os.path.abspath(x)) == os.path.normcase(os.path.abspath(y))
                                    for x, y in zip(a, b))


def powershell_executable(command: list[str] | str | None, env: dict[str, str]) -> str:
    """The resolved launcher (executable plus prefix args) as PowerShell can call it without printing the user's
    home path. Bare `codex` only when PATH resolves to that same launcher; any other absolute path or prefix args
    get a placeholder, since `codex` there may be another build."""
    launcher = [command] if isinstance(command, str) else list(command or [])
    relative = app_relative(launcher[0], env) if len(launcher) == 1 else None
    if relative:
        return '"$env:LOCALAPPDATA/OpenAI/Codex/bin/' + relative + '"'
    if not launcher or (len(launcher) == 1 and not Path(launcher[0]).is_absolute()):
        return "codex"
    try:
        on_path = _resolve_command(["codex"], env, "codex")
    except (ValueError, OSError):
        on_path = None
    return "codex" if on_path and _same_command(on_path, launcher) else "'<engines.codex.bin>'"


def _powershell_home(home: Path) -> str:
    try:
        return "Join-Path $HOME '" + home.relative_to(Path.home()).as_posix() + "'"
    except ValueError:
        return "'<engines.codex.env.CODEX_HOME>'"


def setup_hint(home: Path, command: list[str] | str | None, env: dict[str, str]) -> str:
    """PowerShell commands the PI runs once (it shows a UAC prompt): an elevated workspace-write run in a probe dir."""
    return ("PowerShell에서 직접 실행하고 UAC를 승인하세요: "
            f"$env:CODEX_HOME = {_powershell_home(home)}; "
            "$probe = Join-Path $env:TEMP 'labhq-sandbox-probe'; New-Item -ItemType Directory -Force $probe | Out-Null; "
            "Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $probe 'ok.txt'); "
            f"& {powershell_executable(command, env)} exec --skip-git-repo-check -C $probe -s workspace-write "
            "-c 'windows.sandbox=\"elevated\"' 'Create ok.txt containing ok'; "
            "Test-Path (Join-Path $probe 'ok.txt')  # True면 준비 완료, 다음 Codex 직원 작업이 판본을 기록합니다")


def read_ok(home: Path) -> dict | None:
    try:
        data = json.loads((home / OK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_ok(home: Path, version: str, label: str) -> bool:
    """Write the ok record when it changes; the time is when this version was first seen working."""
    current = read_ok(home) or {}
    if current.get("codex_version") == version and current.get("bin") == label:
        return False
    data = {"codex_version": version, "bin": label, "recorded_at": time.time()}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=home, prefix=OK_FILE, suffix=".tmp",
                                     delete=False) as out:
        json.dump(data, out)
        temp = Path(out.name)
    temp.replace(home / OK_FILE)
    return True


def has_setup_marker(home: Path) -> bool:
    return (home / ".sandbox" / "setup_marker.json").is_file()


def check(home: Path, current: str | None) -> tuple[str, str]:
    """doctor status and one-line reason for one staff CODEX_HOME."""
    if current is None:
        return "skip", "현재 codex 판본을 읽지 못해 sandbox 준비 판본을 비교하지 않음"
    recorded = (read_ok(home) or {}).get("codex_version")
    if not recorded:
        return "warn", (f"sandbox 준비가 Codex {current}에서 성공한 기록 없음 — "
                        "무인 실행 전에 sandbox 준비를 다시 확인")
    if recorded != current:
        return "warn", f"Codex가 {recorded}에서 {current}로 바뀜 — 무인 실행 전에 sandbox 준비를 다시 확인"
    return "ok", f"sandbox 준비가 Codex {current}에서 확인됨"


def is_setup_failure(result) -> bool:
    return result.error_kind == "sandbox_setup_required" or codex_adapter.ELEVATED_SETUP_ERROR in (result.error or "")


class SandboxWatch:
    """Per runner process: records versions that worked and alerts the PI once about a needed setup."""

    def __init__(self) -> None:
        self.alerted = False

    def _record(self, homes: list[Path], cmd: list[str], env: dict[str, str]) -> None:
        """`cmd` is the launcher the run started, never a fresh resolution: with `bin: auto` an app update during
        the run would otherwise record the new build as working."""
        homes = [home for home in homes if has_setup_marker(home)]
        version = codex_version(cmd, env) if homes else None
        if version:
            for home in homes:
                try:
                    record_ok(home, version, executable_label(cmd[0], env))
                except OSError:
                    continue

    async def after_run(self, settings: Settings, ctx, env: dict[str, str], result, emit) -> None:
        homes = elevated_homes(settings, env, ctx.workdir, ctx.read_only)
        if not homes:
            return
        if is_setup_failure(result):
            if self.alerted:  # every later task fails the same way; its own result already says so
                return
            self.alerted = True
            cmd = await asyncio.to_thread(codex_command, settings, env)
            hint = setup_hint(homes[0], cmd, env)
            summary = ("Codex sandbox 다시 준비 필요: 직원 CODEX_HOME의 elevated sandbox 준비가 없거나 "
                       "지금 Codex와 맞지 않아 단계가 멈췄습니다")
            await emit("agent.log", {"level": "alert", "text": f"{summary}. {hint}"})
            # The decisions tab is where the PI looks; this card only informs, either answer just closes it.
            notice = ApprovalRequest(task_id=ctx.task.id, agent_id=ctx.agent.id, request_id=ctx.task.request_id,
                                     kind="codex_sandbox_setup", summary=summary + " (확인하면 닫힙니다)",
                                     detail={"command": hint}, timeout_s=7 * 24 * 3600)
            await emit("approval.requested", notice.model_dump(mode="json"))
        elif result.ok and ctx.commands_ran and ctx.started_command:
            # Only a run whose sandbox started a command proves the setup; text-only, MCP-only and output_schema
            # turns never touch it.
            await asyncio.to_thread(self._record, homes, ctx.started_command, env)
