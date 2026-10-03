"""Read-only capability checks for the runner host (apart from optional manifest output)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from .adapters.base import _resolve_command, codex_app_choice, expand_env
from .adapters import adapter_preflight_error, get_adapter
from .models import AgentSpec, Engine
from . import private_paths as private_path_module
from .private_paths import (PrivatePaths, configured_claude_config_dir, in_pi_claude, inside_any,
                            plugin_keep_dirs, resolve_private_paths, staff_claude_config_dir, staff_codex_homes)
from .recruit.paper2agent import skill_installed
from .runner.daemon import check_data_boundary, check_job_group
from .runner import codex_sandbox
from .runner.versions import _probe, _version
from .settings import Settings
from .tools.scheduler import COMMANDS as SCHEDULER_COMMANDS
from .util import parent_claude_markers

SOURCES = {
    "ENA": "https://www.ebi.ac.uk/ena/browser/home",
    "NCBI E-utilities": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi",
    "GEO": "https://www.ncbi.nlm.nih.gov/geo/",
}
LOGIN = {"claude_code": ["auth", "status"], "codex": ["login", "status"]}
LOCAL_PYTHON_PACKAGES = {
    "pandas": "pandas", "numpy": "numpy", "scipy": "scipy", "matplotlib": "matplotlib",
    "statsmodels": "statsmodels", "scikit-learn": "sklearn", "gseapy": "gseapy", "pydeseq2": "pydeseq2",
}
LOCAL_TOOLS = ("docker", "nextflow", "java", "wsl")


def _safe_path(path: str | Path) -> str:
    """Show home paths as ~ and avoid disclosing other machine-specific parents."""
    raw = str(path)
    home = str(Path.home())
    if raw.casefold() == home.casefold():
        return "~"
    if raw.casefold().startswith(home.casefold() + os.sep):
        shown = "~" + raw[len(home):].replace("\\", "/")
        if len(shown) > 80:
            shown = "~/.../" + "/".join(shown.split("/")[-2:])
        return re.sub(re.escape(Path.home().name), "<user>", shown, flags=re.IGNORECASE)
    if Path(raw).is_absolute():
        raw = "<external>/" + Path(raw).name
    return re.sub(re.escape(Path.home().name), "<user>", raw, flags=re.IGNORECASE)


def _row(group: str, name: str, status: str, detail: str, hint: str) -> dict:
    return {"group": group, "name": name, "status": status, "detail": detail, "hint": hint}


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
        return True
    except OSError:
        return False


def _would_be_writable(path: Path) -> bool:
    """Dry-run estimate without creating directories or temporary files."""
    while not path.exists() and path.parent != path:
        path = path.parent
    return path.is_dir() and os.access(path, os.W_OK)


def _windows_config_owner_is_current_user(path: Path) -> bool | None:
    """Compare the file owner SID with the process token SID, without naming either account."""
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        owner_sid = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        token = wintypes.HANDLE()

        advapi32.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        advapi32.EqualSid.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]

        result = advapi32.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner_sid),
                                                None, None, None, ctypes.byref(descriptor))
        if result:
            return None
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return None
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            return None

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

        current_sid = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents.sid
        return bool(advapi32.EqualSid(owner_sid, current_sid))
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if "token" in locals() and token:
            kernel32.CloseHandle(token)
        if "descriptor" in locals() and descriptor:
            kernel32.LocalFree(descriptor)


def _current_os_account() -> str | None:
    """The process token's account (DOMAIN\\user), from `whoami`, not the spoofable USERNAME variable (#304)."""
    try:
        out = subprocess.run(["whoami"], capture_output=True, text=True, timeout=10).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001 - doctor never fails on an unavailable identity
        return None


def _same_account(current: str, expected: str) -> bool:
    current, expected = current.strip().lower(), expected.strip().lower()
    if "\\" not in expected and "\\" in current:  # a bare name means a local account on this machine
        expected = os.environ.get("COMPUTERNAME", "").lower() + "\\" + expected
    return current == expected


def _config_owner_is_current_user(path: Path) -> bool | None:
    """Return an OS-backed owner comparison, or None when the OS cannot supply it."""
    if os.name == "nt":
        return _windows_config_owner_is_current_user(path)
    try:
        return path.stat().st_uid == os.geteuid()
    except (AttributeError, OSError):
        return None


def _safe_executable_path(path: str, env: dict[str, str]) -> str:
    local = env.get("LOCALAPPDATA")
    if local:
        try:
            relative = Path(path).relative_to(Path(local) / "OpenAI" / "Codex" / "bin")
            return "%LOCALAPPDATA%/OpenAI/Codex/bin/" + relative.as_posix()
        except ValueError:
            pass
    return _safe_path(path)


def _roster(settings: Settings) -> list[AgentSpec]:
    base = settings.path(settings.runner.agents_dir)
    agents = {}
    for kind in ("core", "contract"):
        for path in sorted((base / kind).glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            spec = AgentSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            if spec.contract and (spec.contract.status in ("expired", "archived") or
                                  time.time() > spec.contract.expires_at):
                continue
            agents[spec.id] = spec
    return list(agents.values())


def _adapter_check(settings: Settings, agent: AgentSpec) -> str | None:
    return adapter_preflight_error(settings, agent)


PRIVATE_PATHS_HINT = "See docs/manual.md 'PI 개인 경로' (policy.private_paths)."


def _doctor_private(settings: Settings, agents: list[AgentSpec], forced: Engine | None) -> PrivatePaths:
    workspace = settings.path(settings.runner.workspace_root)
    codex = forced == Engine.codex or (forced is None and any(a.engine == Engine.codex for a in agents))
    keep = [workspace, *(settings.path(r) for r in settings.runner.reference_roots),
            *(settings.path(p.local_dir) for p in settings.projects if p.local_dir),
            *(d for a in agents for d in plugin_keep_dirs(settings, a.plugin_dirs, workspace)),
            *staff_codex_homes(settings, workspace, codex)]
    return resolve_private_paths(settings, keep, cwd=workspace)


def _private_paths_row(private: PrivatePaths) -> dict:
    """How many PI personal paths are closed to staff, and which configured ones hold a work folder."""
    if not private.enabled:
        return _row("staff", "private paths", "warn", "off (policy.private_paths: [])", PRIVATE_PATHS_HINT)
    detail = f"{len(private.labels)} active"
    if private.skipped:
        detail += "; skipped, holds a work folder: " + ", ".join(private.skipped)
    return _row("staff", "private paths", "ok" if private.labels else "warn", detail, PRIVATE_PATHS_HINT)


CLAUDE_STAFF_DIR = ".labhq/claude-staff"


def claude_login_command(windows: bool) -> str:
    """What the PI runs once to sign the staff Claude in to ~/.labhq/claude-staff. Home variables only, so no
    user name or path is printed."""
    if windows:
        return f"$env:CLAUDE_CONFIG_DIR = Join-Path $HOME '{CLAUDE_STAFF_DIR}'; claude 실행 뒤 /login (끝나면 그 창은 닫기)"
    return f'CLAUDE_CONFIG_DIR="$HOME/{CLAUDE_STAFF_DIR}" claude 실행 뒤 /login'


def _claude_staff_row(settings: Settings, agents: list[AgentSpec], forced: Engine | None, private: PrivatePaths,
                      login_code: int | None) -> dict | None:
    """Claude staff with the PI's ~/.claude closed to them (#298 ⑤): Claude saves long tool output under its config
    folder and reads it back, so they need their own (engines.claude_code.env.CLAUDE_CONFIG_DIR)."""
    claude = forced == Engine.claude_code or (forced is None and any(a.engine == Engine.claude_code for a in agents))
    home = private_path_module.host_home()
    pi = os.path.join(home, ".claude")
    if not claude or not os.path.lexists(pi) or not inside_any(pi, private.paths):
        return None
    windows = os.name == "nt"
    standard = os.path.join(home, *CLAUDE_STAFF_DIR.split("/"))
    login = claude_login_command(windows)
    configured = configured_claude_config_dir(settings)
    if not configured:
        var = "${USERPROFILE}" if windows else "${HOME}"
        return _row("staff", "claude staff config", "warn",
                    "Claude 직원이 PI ~/.claude를 설정 폴더로 씀: 그 아래 저장된 긴 출력을 다시 읽지 못할 수 있음",
                    f"engines.claude_code.env.CLAUDE_CONFIG_DIR: {var}/{CLAUDE_STAFF_DIR} 로 두고 로그인: {login}")
    if in_pi_claude(configured):
        return _row("staff", "claude staff config", "fail",
                    "CLAUDE_CONFIG_DIR가 PI ~/.claude이거나 그 안: 직원이 PI 로그인을 쓰고 긴 출력도 막힘",
                    f"직원 전용 폴더(~/{CLAUDE_STAFF_DIR})로 바꾸고 로그인: {login}")
    staff = staff_claude_config_dir(settings)
    if not staff:
        return _row("staff", "claude staff config", "warn", "CLAUDE_CONFIG_DIR가 상대 경로: task 폴더마다 달라짐",
                    f"절대 경로(~/{CLAUDE_STAFF_DIR} 등)로 두세요.")
    if not inside_any(staff, [standard]) or not inside_any(standard, [staff]):
        login = "CLAUDE_CONFIG_DIR를 engines.claude_code.env 값으로 두고 claude 실행 뒤 /login"
    if login_code == 0:
        return _row("staff", "claude staff config", "ok", "직원 전용 설정 폴더, 로그인됨", "")
    if login_code is None and os.path.isdir(staff):
        return _row("staff", "claude staff config", "skip", "직원 전용 설정 폴더, 로그인 확인 생략", "")
    return _row("staff", "claude staff config", "warn", "직원 전용 설정 폴더에 로그인 없음", f"로그인: {login}")


def _sandbox_version_rows(settings: Settings, agent: AgentSpec, codex_now: dict, seen: set[Path],
                          dry_run: bool) -> list[dict]:
    """The elevated-setup check, continued (#328): setup_marker.json exists, but did it work with this Codex?"""
    adapter = get_adapter(agent.engine, settings)
    env = {**os.environ, **adapter.engine_env()}
    rows = []
    for home in codex_sandbox.elevated_homes(settings, env, settings.path(settings.runner.workspace_root)):
        if home in seen or not codex_sandbox.has_setup_marker(home):
            continue
        seen.add(home)
        if dry_run:
            rows.append(_row("staff", "codex sandbox version", "skip", "probe skipped: dry-run", ""))
            continue
        status, detail = codex_sandbox.check(home, codex_now["version"])
        command = codex_now["command"]
        rows.append(_row("staff", "codex sandbox version", status, detail,
                         codex_sandbox.setup_hint(home, command, env)))
    return rows


def _network_check(url: str) -> bool:
    for method in ("HEAD", "GET"):
        try:
            with urlopen(Request(url, method=method, headers={"User-Agent": "labhq-doctor/1"}), timeout=4) as response:
                return 200 <= response.status < 400
        except Exception:
            continue
    return False


def _import_available(python_executable: str, import_name: str) -> bool:
    code, _ = _probe(
        [python_executable, "-I", "-c", f"import importlib; importlib.import_module({import_name!r})"],
        dict(os.environ), timeout=3,
    )
    return code == 0


def local_software_summary(python_executable: str) -> dict:
    """Bounded runner facts for planning. Executable paths and probe output never leave this function."""
    rscript = shutil.which("Rscript")
    r_code, r_raw = _probe([rscript, "--version"], dict(os.environ), timeout=3) if rscript else (None, "")
    py_code, py_raw = _probe([python_executable, "--version"], dict(os.environ), timeout=3)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(_import_available, python_executable, import_name)
                   for name, import_name in LOCAL_PYTHON_PACKAGES.items()}
        packages = {name: future.result() for name, future in futures.items()}
    return {
        "r": {"available": r_code == 0, "version": _version(r_raw) if r_code == 0 else None},
        "python": {"version": _version(py_raw) if py_code == 0 else "unreported", "packages": packages},
        "tools": {name: bool(shutil.which(name)) for name in LOCAL_TOOLS},
    }


def collect(settings: Settings, *, requested_config: str | None = None, network: bool = False,
            dry_run: bool = False, require_roster: bool = False) -> dict:
    rows: list[dict] = []
    config = settings.config_path
    if requested_config and not config:
        rows.append(_row("config", "file", "fail", "configured file missing", "Set --config to an existing YAML file."))
    else:
        rows.append(_row("config", "file", "ok" if config else "warn",
                         _safe_path(config) if config else "defaults", "Set --config for this host."))
    # Only a named runner account proves isolation; an owner mismatch alone is not evidence (#304 review).
    expected = (settings.runner.os_account or "").strip()
    if expected:
        current = _current_os_account()
        if current is None:
            owner_status, owner_detail = "skip", "current OS account unavailable"
        elif _same_account(current, expected):
            owner_status, owner_detail = "ok", "runner runs as runner.os_account"
        else:
            owner_status, owner_detail = "warn", "runner is not running as runner.os_account"
    else:
        same_owner = _config_owner_is_current_user(Path(config)) if config else None
        owner_status = "warn" if same_owner is True else "skip"
        owner_detail = ("runner and config owner are the same OS account" if same_owner is True else
                        "not verified: set runner.os_account to the dedicated account" if same_owner is False else
                        "OS account comparison unavailable")
    rows.append(_row("config", "runner account isolation", owner_status, owner_detail,
                     "Default guard is policy.private_paths (docs/manual.md 'PI 개인 경로'). Optional, advanced: a dedicated runner "
                     "account, docs/runner-account.md."))
    if settings.gateway.client_token == "change-me-client":
        # The published default is a working client token for anyone while a gateway accepts it, and a runner
        # config that drops the key falls back to it (#304 review).
        rows.append(_row("config", "default client token", "warn",
                         "gateway.client_token is the published default",
                         'Set a random gateway.client_token for the gateway and client_token: "" for the runner; '
                         "see docs/runner-account.md."))
    elif owner_status == "ok" and settings.gateway.client_token:
        # The runner never needs the client token; staff run as this account and could approve as the PI (#304).
        rows.append(_row("config", "runner config holds client token", "warn",
                         "gateway.client_token is set in the runner's config",
                         "Give the runner a config without gateway.client_token; see docs/runner-account.md."))
    markers = parent_claude_markers(dict(os.environ))
    rows.append(_row("staff", "claude_parent_session_env", "warn" if markers else "ok",
                     f"부모 Claude 세션 마커 {len(markers)}개를 직원 subprocess에서 제거"
                     if markers else "부모 Claude 세션 마커 없음",
                     "직원 CLI는 labhq runner를 통해 시작하세요."))
    rows.append(_row("staff", "codex_user_skills_leak", "warn",
                     "직원 세션이 PI 개인 skill·규칙을 읽을 수 있음",
                     "PI 결정: 현재는 격리하지 않으며 재현성 해석에 반영하세요."))
    for name, raw in (("gateway state", settings.gateway.state_dir), ("runner state", settings.runner.state_dir),
                      ("workspace", settings.runner.workspace_root)):
        path = settings.path(raw)
        good = _would_be_writable(path) if dry_run else _writable(path)
        rows.append(_row("config", name, "ok" if good else "fail", _safe_path(path),
                         "Choose a writable directory."))
    restricted = [z for z in settings.policy.data_zones if z.level == "restricted"]
    try:
        override = check_data_boundary(settings)
        status, detail = ("warn", "unsafe read override enabled") if override else ("ok", f"{len(restricted)} configured")
    except (RuntimeError, OSError) as exc:
        status, detail = "fail", str(exc) if isinstance(exc, RuntimeError) else "data guard check unavailable"
    rows.append(_row("config", "restricted data zones", status, detail,
                     "Run on a supported POSIX runner or remove restricted zones for a safe test."))
    try:
        check_job_group(settings)
        status, detail = "ok", "runner account check passed" if settings.hpc.submit_prefix else "not required"
        if settings.hpc.submit_prefix and os.name == "nt":
            status, detail = "warn", "POSIX account lookup unavailable on Windows"
    except (RuntimeError, OSError) as exc:
        # Guard errors can contain account/group names; show only the reason.
        status, detail = "fail", str(exc).split(":", 1)[0] if isinstance(exc, RuntimeError) else "account check unavailable"
    rows.append(_row("config", "HPC job group", status, detail,
                     "Check hpc.user and hpc.job_group membership for the runner and job accounts."))
    if settings.dev_log.enabled:
        repo = settings.dev_log.repo or "로컬만 사용(dev_log.repo 미설정)"
        status = "ok" if settings.dev_log.repo else "warn"
        detail = f"{repo}; 종료: v1.0, #40 bench 뒤 20 rounds 새 교훈 없음, 또는 보고 채널 이전 (#69)"
        rows.append(_row("config", "dev log", status, detail,
                         "dev_log.repo에 비공개 기록 저장소를 지정하세요."))

    available: dict[str, bool] = {}
    codex_now: dict[str, str | list[str] | None] = {"version": None, "command": None}
    login_codes: dict[str, int | None] = {}  # the CLI's own status command, with engines.<name>.env applied
    for name in settings.engines.__class__.model_fields:
        spec = getattr(settings.engines, name)
        env = {**os.environ, **expand_env(spec.env, os.environ)}
        cmd = [os.path.expandvars(os.path.expanduser(spec.bin)),
               *(os.path.expandvars(os.path.expanduser(a)) for a in spec.prefix_args)]
        try:
            resolved = _resolve_command(cmd, env, name)
            found = Path(resolved[0]).is_file() or bool(shutil.which(resolved[0], path=env.get("PATH")))
        except (ValueError, OSError):
            resolved, found = [], False
        available[name] = found
        if not found:
            rows.append(_row("engine", name, "warn", "executable missing or unsupported shim",
                             f"Install {name} or set engines.{name}.bin and prefix_args."))
            continue
        code, raw = (None, "") if dry_run else _probe([*resolved, "--version"], env)
        detail = _safe_executable_path(resolved[0], env) + (" (probe skipped: dry-run)" if dry_run else " " + _version(raw))
        if name == "codex":
            codex_now.update(version=_version(raw) if code == 0 and _version(raw) != "unreported" else None,
                             command=resolved)
            choice = codex_app_choice(env) if spec.bin.strip().lower() in ("", "auto") else None
            if choice and choice["path"] == resolved[0]:
                detail += (f"; auto: 앱 폴더 {choice['folder']} 선택 (codex.exe 있는 폴더 {choice['candidates']}개 중 " +
                           ("판본이 가장 높은 폴더)" if choice["by"] == "version" else "판본 비교 불가, 가장 최근 폴더)"))
        rows.append(_row("engine", name, "ok" if code == 0 else "warn", detail,
                         "Check the executable and prefix_args if version fails."))
        if name in LOGIN:
            code, _ = (None, "") if dry_run else _probe([*resolved, *LOGIN[name]], env)
            login_codes[name] = code
            rows.append(_row("login", name, "ok" if code == 0 else "warn",
                             "status command succeeded" if code == 0 else "status unavailable or signed out",
                             f"Check {name} login locally; doctor never starts login."))
        else:
            rows.append(_row("login", name, "warn", "non-interactive status skipped",
                             "Check login manually before a paid run."))

    try:
        agents = _roster(settings)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        agents = []
        rows.append(_row("staff", "roster", "fail", type(exc).__name__, "Fix the agent YAML files."))
    if not agents:
        rows.append(_row("staff", "roster", "fail" if require_roster else "warn",
                         "no active agents found", "Set runner.agents_dir."))
    forced = None
    invalid_force = False
    sandbox_homes: set[Path] = set()
    if settings.runner.force_engine:
        try:
            forced = Engine(settings.runner.force_engine)
        except ValueError:
            invalid_force = True
            rows.append(_row("config", "runner.force_engine", "fail", "unknown engine",
                             "Set runner.force_engine to a supported engine or remove it."))
    for agent in agents:
        if invalid_force:
            rows.append(_row("staff", agent.id, "fail", "invalid runner.force_engine",
                             "Fix runner.force_engine before running staff."))
            continue
        if forced:
            agent = agent.model_copy(update={"engine": forced})
        engine = agent.engine.value
        ready = available.get(engine, engine == "mock")
        plugin = engine == "claude_code" and (agent.plugin_dirs or agent.required_skills)
        try:
            error = _adapter_check(settings, agent)
        except OSError:
            error = "adapter preflight files inaccessible"
        if error:
            for raw in agent.plugin_dirs:
                error = error.replace(raw, _safe_path(raw))
        status = ("warn" if plugin else "fail") if error else "ok" if ready else "warn"
        hint = ("무인 실행 전에 직원 CODEX_HOME의 elevated sandbox setup을 대화형으로 마치세요."
                if engine == "codex" and error and "elevated sandbox setup" in error else
                "직원 전용 CODEX_HOME에서 codex login한 뒤 engines.codex.env.CODEX_HOME에 지정하세요."
                if engine == "codex" and error and "CODEX_HOME" in error else
                f"Resolve the {engine} adapter preflight or install/configure its executable.")
        rows.append(_row("staff", agent.id, status, error or f"engine={engine}", hint))
        if engine == "codex" and not error:
            rows += _sandbox_version_rows(settings, agent, codex_now, sandbox_homes, dry_run)
        if plugin:
            rows.append(_row("plugin", agent.id, "warn" if error else "ok", error or "plugin ready",
                             "Set the plugin directory and install its required skill."))
    private = _doctor_private(settings, agents, forced)
    rows.append(_private_paths_row(private))
    claude_staff = _claude_staff_row(settings, agents, forced, private, login_codes.get("claude_code"))
    if claude_staff:
        rows.append(claude_staff)
    try:
        paper = skill_installed(settings.recruit.contract_engine)
    except OSError:
        paper = False
    recruiter = next((a for a in agents if a.id == settings.recruit.agent_id), None)
    visible = bool(recruiter and not settings.engines.claude_code.isolate_user_config)
    rows.append(_row("staff", "paper2agent", "ok" if paper and visible else "warn",
                     "installed and configured visible" if paper and visible else
                     ("installed but recruiter session may hide user skills" if paper else "not installed"),
                     "Run labhq setup-paper2agent and enable recruiter skill visibility."))

    scheduler = settings.hpc.scheduler
    if scheduler in SCHEDULER_COMMANDS:
        tools = SCHEDULER_COMMANDS[scheduler]
        present = all(shutil.which(tool) for tool in tools)
        remote = bool(settings.hpc.ssh_host)
        rows.append(_row("compute", "scheduler", "ok" if present else "warn",
                         f"{scheduler}; local {'/'.join(tools)} {'present' if present else 'missing'}" +
                         ("; remote host configured, not contacted" if remote else ""),
                         "Check scheduler tools on the runner or configured login host."))
    else:
        rows.append(_row("compute", "scheduler", "ok" if scheduler == "mock" else "warn", scheduler,
                         "Configure hpc.scheduler when HPC submission is needed."))
    for tool in ("wsl", "docker", "nextflow", "java"):
        found = bool(shutil.which(tool))
        rows.append(_row("compute", tool, "ok" if found else "warn", "on PATH" if found else "missing",
                         f"Install or configure {tool} if bioinfo-agent needs it."))
    for name, url in SOURCES.items():
        reachable = _network_check(url) if network and not dry_run else None
        rows.append(_row("data", name, "ok" if reachable else "warn",
                         "reachable" if reachable else "unreachable" if network else "skipped",
                         "Use --network to test connectivity." if not network else "Check network access."))
    external_hpc = any(any(m.name == "labhq_hpc" for m in agent.mcp) for agent in agents)
    runner_capabilities = {"scheduler": scheduler,
                           "compute_backends": ["local CLI"] + ([scheduler] if scheduler != "none" else []) +
                                               (["external labhq_hpc MCP"] if external_hpc else []),
                           "hpc_tools": scheduler != "none" or external_hpc}
    return {"schema_version": 1, "runner_capabilities": runner_capabilities, "checks": rows,
            "summary": {status: sum(r["status"] == status for r in rows)
                        for status in ("ok", "warn", "fail", "skip")}}


def render(manifest: dict) -> str:
    lines = [f"{'AREA':<9} {'CHECK':<22} {'STATE':<5} DETAIL", "-" * 78]
    for row in manifest["checks"]:
        hint = f" | fix: {row['hint']}" if row["status"] != "ok" and row.get("hint") else ""  # hints only where needed
        lines.append(f"{row['group']:<9} {row['name']:<22} {row['status']:<5} {row['detail']}{hint}")
    return "\n".join(lines)


def save(manifest: dict, settings: Settings) -> Path:
    path = settings.path(settings.runner.state_dir) / "capabilities.json"
    repo = Path(__file__).resolve().parents[1]
    if repo == path.resolve() or repo in path.resolve().parents:
        raise OSError("manifest must be outside the repository")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temp = Path(out.name)
        json.dump(manifest, out, ensure_ascii=False, indent=2)
        out.write("\n")
    temp.replace(path)
    return path
