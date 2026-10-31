"""Read-only capability checks for the runner host (apart from optional manifest output)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from .adapters.base import _resolve_command, codex_app_choice, expand_env
from .adapters import adapter_preflight_error, get_adapter
from . import bioinfo_link, disk_space, github_token, instance_guard
from .models import AgentSpec, Engine
from .registry import is_employed, iter_agent_specs
from . import private_paths as private_path_module
from .private_paths import (PrivatePaths, configured_claude_config_dir, in_pi_claude, inside_any,
                            plugin_keep_dirs, resolve_private_paths, staff_claude_config_dir)
from .recruit import paper2agent
from .runner.daemon import check_data_boundary, check_job_group
from .runner import codex_sandbox
from .runner.versions import _probe, _version
from .research.packs import check_configured_packs
from .settings import Settings
from .integrations.visibility import dev_log_visibility
from .test_guard import guard_test_path
from .tools.scheduler import COMMANDS as SCHEDULER_COMMANDS
from .util import is_windows_app_alias_dir, parent_claude_markers

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


def wsl_tool_status() -> dict[str, bool]:
    """Called inside the WSL runner: installed Docker alone does not prove its daemon answers."""
    commands = {"docker": ["docker", "info"], "nextflow": ["nextflow", "-version"],
                "java": ["java", "-version"]}
    return {name: _probe(argv, dict(os.environ), timeout=15)[0] == 0 for name, argv in commands.items()}


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


def _research_row(settings: Settings) -> dict:
    """Whether the v0.5 research lane runs end to end; `enabled` alone freezes the plan at CP1 and stops there
    (readiness R1, 2026-10-08)."""
    research = settings.research
    try:
        check_configured_packs(settings)  # the gateway refuses to start on the same error (#492 review)
    except (ValueError, OSError) as exc:
        return _row("config", "research lane", "fail", f"pack 설정 오류: {exc}",
                    "research.active_packs를 고치세요. gateway는 이 설정으로 시작하지 않습니다.")
    if not research.enabled:
        return _row("config", "research lane", "ok", "꺼짐: 요청은 일반 lane으로 갑니다",
                    "켜려면 research.enabled·evidence_checkpoint를 true로 하고 active_packs를 넣은 뒤 다시 시작하세요"
                    " (docs/research_protocol.md).")
    if not research.evidence_checkpoint:
        return _row("config", "research lane", "warn",
                    "research.enabled만 켜짐: CP1 승인 뒤 단계를 실행하지 않고 끝납니다",
                    "research.evidence_checkpoint: true를 함께 켜세요.")
    packs = ", ".join(research.active_packs) or "없음"
    return _row("config", "research lane", "ok", f"켜짐: CP1 → 단계 → CP2 → 리뷰 → 보고서, pack {packs}", "")


def _semantics_row(settings: Settings) -> dict:  # semantics-hook
    from .research.semantics_shadow import doctor_row  # semantics-hook
    return doctor_row(settings)  # semantics-hook


def _semantic_device_rows(settings: Settings) -> list[dict]:
    from .research.semantic_devices import inspect

    record = inspect(settings, phase="doctor")
    vocab = record["vocabulary"]
    checklist = record["checklist"]
    packs = record["packs"]
    return [
        _row("config", "meaning vocabulary", "ok" if vocab["status"] == "ok" else "warn",
             (f"hash {vocab.get('sha256', '-')[:12]}; keys {vocab.get('counts', {}).get('keys', 0)}; "
              f"EDAM {vocab.get('counts', {}).get('edam_terms', 0)}") if vocab["status"] == "ok" else
             str(vocab.get("error_code") or vocab.get("edam_problem") or "불러오기 실패"),
             "단어장·EDAM 파일을 고친 뒤 gateway를 다시 시작하세요."),
        _row("config", "topic checklists", "ok" if checklist["status"] == "ok" else "warn",
             (f"hash {checklist.get('sha256', '-')[:12]}; topic {checklist.get('topics', 0)}; "
              f"items {checklist.get('items', 0)}") if checklist["status"] == "ok" else
             str(checklist.get("error_code") or "불러오기 실패"),
             "체크리스트 파일을 고친 뒤 gateway를 다시 시작하세요."),
        _row("config", "research packs", "ok" if packs["status"] == "ok" else "warn",
             (f"loaded {packs.get('count', 0)}; hashes " +
              (", ".join(f"{key}:{value[:12]}" for key, value in packs.get("sha256", {}).items()) or "없음"))
              if packs["status"] == "ok" else str(packs.get("error_code") or "불러오기 실패"),
             "pack 설정·파일을 고친 뒤 gateway를 다시 시작하세요."),
    ]


def _wiki_row(settings: Settings) -> dict:
    """The linked LLM-wiki (#827): off by default; read-only, it never creates or refreshes the index."""
    if not settings.wiki.enabled:
        return _row("knowledge", "wiki", "skip", "not linked (wiki.enabled is false)",
                    "Optional: `labhq wiki connect <folder>` checks a folder and prints the config to add.")
    from .wiki.index import status

    info = status(settings)
    detail = (f"root={info['root']}; docs={info['docs']}; chunks={info['chunks']}; stale={info['stale']}; "
              f"index={'present' if info['available'] else 'absent'}")
    if info["root_problem"]:
        return _row("knowledge", "wiki", "warn", f"{detail}; problem: {info['root_problem']}",
                    "Fix wiki.root: it must be a plain folder inside runner.reference_roots, outside private paths.")
    if not info["available"]:
        return _row("knowledge", "wiki", "warn", detail, "Run `labhq wiki reindex` to build the index.")
    return _row("knowledge", "wiki", "ok", detail, "")


def _row(group: str, name: str, status: str, detail: str, hint: str) -> dict:
    return {"group": group, "name": name, "status": status, "detail": detail, "hint": hint}


def _volume(path: Path):
    """Device of the nearest existing ancestor, the folder disk_space.usage probes; nothing is created."""
    path = path.expanduser().absolute()
    while not path.exists():
        if path.parent == path:
            raise OSError("disk unavailable")
        path = path.parent
    device = os.stat(path).st_dev
    if not device:  # 0 names no volume (UNC or special volumes); equal zeros must not hide a separate drive
        raise OSError("volume not identified")
    return device


def _disk_rows(settings: Settings) -> list[dict]:
    workspace = settings.path(settings.runner.workspace_root)
    paths = [("workspace disk", workspace), ("temporary disk", Path(tempfile.gettempdir()))]
    for engine, variable, fallback in (("codex", "CODEX_HOME", ".codex"),
                                       ("claude_code", "CLAUDE_CONFIG_DIR", ".claude")):
        env = {**os.environ, **expand_env(getattr(settings.engines, engine).env, os.environ)}
        raw = os.path.expanduser(env.get(variable) or str(Path.home() / fallback))
        path = Path(raw)
        paths.append((f"{engine} staff home disk", path if path.is_absolute() else workspace / path))
    # The gateway's SQLite state and events (#678) can fill another volume and stop the gateway. On a volume a row
    # above already shows it would only repeat that row; a volume that cannot be told apart keeps its own row.
    state = settings.path(settings.gateway.state_dir)
    try:
        separate = _volume(state) not in {_volume(path) for _name, path in paths}
    except (OSError, ValueError):
        separate = True
    if separate:
        paths.append(("gateway state disk", state))
    rows = []
    runner = settings.runner
    for name, path in paths:
        try:
            space = disk_space.usage(path)
            status = "warn" if disk_space.low(space, runner.disk_warning_free_gb,
                                               runner.disk_warning_free_percent) else "ok"
            # The drives the runner and the gateway write to: below the level the runner asks the PI before starting a
            # task on (runner.disk_fail_free_gb) a run is likely to fill them, so doctor fails here (#678). Temporary
            # and staff-home drives stay warnings.
            if (name in {"workspace disk", "gateway state disk"} and runner.disk_fail_free_gb
                    and space.free < runner.disk_fail_free_gb * disk_space.GIB):
                status = "fail"
            detail = (f"여유 {space.free / disk_space.GIB:.1f} GiB / {space.total / disk_space.GIB:.1f} GiB "
                      f"({100 * space.free / space.total:.1f}%)" if space.total else "용량 확인 불가")
        except (OSError, ValueError):
            status, detail = "warn", "드라이브 여유 공간을 확인하지 못했습니다"
        rows.append(_row("disk", name, status, detail,
                         "runner.disk_warning_free_gb·disk_warning_free_percent·disk_fail_free_gb를 확인하고 여유 있는 "
                         "드라이브를 고르세요. 자동 이동·삭제는 하지 않습니다."))
    return rows


def _memory_limit_row(settings: Settings) -> dict:
    from .runner.memory_limit import configured_limit_bytes, memory_limit_mechanism, memory_limit_supported

    memory_bytes = configured_limit_bytes(settings.runner)
    enabled = memory_bytes > 0
    supported = memory_limit_supported()
    return _row(
        "config",
        "step memory limit",
        "skip" if not enabled else ("ok" if supported else "fail"),
        "꺼짐" if not enabled else f"{memory_bytes / (1024 ** 3):.2f} GiB · {memory_limit_mechanism()}",
        ("runner.step_memory_limit_gb에 0보다 큰 값을 두세요." if not enabled else
         "이 OS에서 지원하는 runner를 쓰세요." if not supported else ""),
    )


def _bioinfo_row(settings: Settings) -> dict:
    """Whether the bioinfo-agent plugin is connected (#613): the folder a runner would hand to claude_code."""
    env = {**os.environ, **expand_env(settings.engines.claude_code.env, os.environ)}
    folder = bioinfo_link.configured_dir(env)
    if not folder:
        return _row("config", "bioinfo-agent", "warn",
                    bioinfo_link.NOT_CONNECTED + "BIOINFO_AGENT_DIR이 비어 있어 bioinfo-agent 직원(Nextflow 분석)을 쓸 수 없습니다",
                    bioinfo_link.FIX)
    if not os.path.isabs(os.path.expandvars(os.path.expanduser(folder))):
        return _row("config", "bioinfo-agent", "warn",
                    bioinfo_link.NOT_CONNECTED + "BIOINFO_AGENT_DIR이 상대 경로라 task마다 다른 폴더로 읽혀 연결에 실패합니다",
                    "BIOINFO_AGENT_DIR을 절대 경로로 바꾸세요(예: D:/bioinfo-agent). 새 설정이면 `labhq init --bioinfo-agent <폴더>`가 "
                    "절대 경로로 저장합니다.")
    if not bioinfo_link.is_plugin_dir(folder):
        return _row("config", "bioinfo-agent", "warn",
                    bioinfo_link.NOT_CONNECTED + "BIOINFO_AGENT_DIR 폴더에서 plugin(.claude-plugin/plugin.json, "
                    "skills/bioinfo-analyze)을 찾지 못했습니다", bioinfo_link.FIX)
    return _row("config", "bioinfo-agent", "ok", f"연결됨: {_safe_path(folder)}", "")


FINE_GRAINED_PREFIX = "github_pat_"


def _github_token_row(settings: Settings, dry_run: bool = False) -> dict:
    """What the GitHub token may do for result repositories (#687), judged by probes that create nothing, not by its
    prefix (#607 warned every fine-grained token, even one that could create repositories). No token is a valid setup:
    results stay local. Nothing of the token is shown."""
    token = github_token.read_token(settings)
    if not token:
        return _row("config", "github token", "ok", "GitHub 토큰 없음: 결과는 로컬에 저장합니다",
                    "결과를 GitHub 저장소로 정리하려면 `labhq init`으로 토큰을 넣으세요. " + github_token.NEEDED_TEXT)
    if dry_run:
        return _row("config", "github token", "ok", "GitHub 토큰 있음: dry-run이라 권한은 확인하지 않았습니다",
                    github_token.NEEDED_TEXT)
    status, detail, hint = github_token.describe(github_token.check_permissions(settings, token))
    if status == "warn" and token.startswith(FINE_GRAINED_PREFIX):
        hint += " 게시가 실패했다면 권한을 고친 뒤 `labhq storage <요청 id> --republish`."
    return _row("config", "github token", status, detail, hint)


def _instance_rows(settings: Settings) -> list[dict]:
    """Other configs that share this one's state folder or port, or a primary config that differs (#613)."""
    return [_row("instance", instance_guard.DOCTOR_NAME[notice.key], "warn", notice.text, notice.fix)
            for notice in instance_guard.check(settings, show=_safe_path)]


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
        out = subprocess.run(["whoami"], capture_output=True, text=True, timeout=10,
                             encoding="oem" if os.name == "nt" else None, errors="replace").stdout.strip()
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
    guard_test_path(path)
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
    contract = settings.path(settings.runner.contract_dir) if settings.runner.contract_dir else None
    agents = {}
    for spec, _ in iter_agent_specs(base, contract, check_path=guard_test_path):
        if is_employed(spec, time.time()):
            agents[spec.id] = spec
        elif spec.contract and spec.contract.status not in ("expired", "archived"):
            # Registry archives elapsed contracts and removes any earlier entry with the same ID.
            # Mirror that roster result without archiving or changing the source files.
            agents.pop(spec.id, None)
    return list(agents.values())


def _adapter_check(settings: Settings, agent: AgentSpec) -> str | None:
    return adapter_preflight_error(settings, agent)


PRIVATE_PATHS_HINT = "See docs/manual.md 'PI 개인 경로' (policy.private_paths)."


def _doctor_private(settings: Settings, agents: list[AgentSpec], forced: Engine | None) -> PrivatePaths:
    workspace = settings.path(settings.runner.workspace_root)
    keep = [workspace, *(settings.path(r) for r in settings.runner.reference_roots),
            *(settings.path(p.local_dir) for p in settings.projects if p.local_dir)]
    reads = [d for a in agents for d in plugin_keep_dirs(settings, a.plugin_dirs, workspace)]
    return resolve_private_paths(settings, keep, open_reads=reads, cwd=workspace)


def _private_paths_row(private: PrivatePaths) -> dict:
    """How many PI personal paths are closed to staff, and which configured ones hold a work folder."""
    if not private.enabled:
        return _row("staff", "private paths", "warn", "off (policy.private_paths: [])", PRIVATE_PATHS_HINT)
    detail = f"{len(private.labels)} active"
    if private.skipped:
        detail += "; skipped, holds a work folder: " + ", ".join(private.skipped)
    return _row("staff", "private paths", "ok" if private.labels else "warn", detail, PRIVATE_PATHS_HINT)


CLAUDE_STAFF_DIR = ".labhq/claude-staff"


def _alphagenome_row(settings: Settings) -> dict:
    """Whether labhq_annot registers its alphagenome tool: only "키 있음/없음", never the key or its path (#435)."""
    from .tools.annot_keys import has_key
    from .tools.annot_regulatory import alphagenome_client_available

    if not has_key(settings):
        return _row("annot", "AlphaGenome", "skip", "키 없음 (도구 미등록)",
                    "키가 생기면 labhq init을 다시 실행해 붙여 넣으세요.")
    if not alphagenome_client_available():
        return _row("annot", "AlphaGenome", "warn", "키 있음, alphagenome 패키지 없음 (도구 미등록)",
                    "runner의 Python에 pip install 'labhq[alphagenome]' 또는 pip install alphagenome")
    return _row("annot", "AlphaGenome", "ok", "키 있음", "")


def _alphagenome_atlas_row(settings: Settings | None = None, agents: list[AgentSpec] | None = None) -> dict:
    from fnmatch import fnmatchcase
    from .tools.annot_alphagenome_atlas import KEY_ENV, credential, sdk_available

    parent = dict(os.environ)
    present = {"runner": bool(credential(parent))}
    if settings is not None:
        agents = _roster(settings) if agents is None else agents
        names = {settings.runner.force_engine or agent.engine.value for agent in agents
                 if "annot" in agent.builtin_mcp and not any(
                     rule == "mcp__labhq_annot" or fnmatchcase("mcp__labhq_annot__alphagenome_atlas", rule)
                     for rule in agent.disallowed_tools)}
        if not names:
            return _row("annot", "AlphaGenome Atlas", "skip", "Atlas 사용 직원 없음", "")
        present = {}
        for name in sorted(names):
            config = getattr(settings.engines, name, None)
            engine_env = expand_env(config.env) if config is not None else {}
            # Same resolver as the runner; task-specific overrides do not exist during doctor.
            present[name] = bool(credential(parent, engine_env))
    if not any(present.values()):
        return _row("annot", "AlphaGenome Atlas", "skip", "환경변수 키 없음", f"runner 환경에 {KEY_ENV}를 설정하세요.")
    if not sdk_available():
        return _row("annot", "AlphaGenome Atlas", "warn", "환경변수 키 있음, Atlas SDK 없음",
                    "runner의 Python에 pip install 'labhq[alphagenome]' (alphagenome>=0.10.0)")
    if not all(present.values()):
        missing = ", ".join(name for name, has_key in present.items() if not has_key)
        return _row("annot", "AlphaGenome Atlas", "warn", f"일부 engine 환경변수 키 없음: {missing}; 실제 접근 미검증",
                    f"해당 engine 환경의 {KEY_ENV} 설정을 확인하세요.")
    return _row("annot", "AlphaGenome Atlas", "ok", "환경변수 키·SDK 있음, 실제 접근 미검증", "")


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


def _sandbox_homes_row(settings: Settings, agent: AgentSpec, seen: set[Path]) -> list[dict]:
    """The runner account's own Codex home is elevated too (#382). Checked even when the staff preflight failed: a
    stale staff home is exactly what that other home causes."""
    env = {**os.environ, **get_adapter(agent.engine, settings).engine_env()}
    homes = codex_sandbox.elevated_homes(settings, env, settings.path(settings.runner.workspace_root))
    other = codex_sandbox.other_elevated_home(homes) if homes else None
    if other is None or other[0] in seen:
        return []
    own, value = other
    seen.add(own)
    state = "elevated sandbox" if value == "elevated" else "[windows] sandbox 키가 없음(Codex 앱이 elevated로 다시 설정)"
    return [_row("staff", "codex sandbox homes", "warn",
                 f"{codex_sandbox.home_label(own)}의 Codex 설정도 {state} — 직원 홈과 같은 sandbox 계정 비밀번호를 "
                 "번갈아 바꿔 승인 창(UAC)이 반복됩니다 (#382)",
                 f"{codex_sandbox.home_label(own)}/config.toml의 [windows] sandbox를 \"unelevated\"로 바꾸고(키를 "
                 "지우면 Codex 앱이 elevated 설정을 다시 시작) Codex 앱을 다시 켠 뒤, 직원 홈 sandbox 준비를 한 번 하세요")]


def _sandbox_version_rows(settings: Settings, agent: AgentSpec, codex_now: dict, seen: set[Path],
                          dry_run: bool) -> list[dict]:
    """The elevated-setup check, continued (#328): setup_marker.json exists, but did it work with this Codex?"""
    adapter = get_adapter(agent.engine, settings)
    env = {**os.environ, **adapter.engine_env()}
    rows = []
    homes = codex_sandbox.elevated_homes(settings, env, settings.path(settings.runner.workspace_root))
    for home in homes:
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


def staff_python(environ: Mapping[str, str] | None = None) -> str:
    """The interpreter a staff shell gets for `python3`/`python`/`py` on PATH, not labhq's own venv.

    The 2026-10-04 rerun summarized labhq's venv (no pandas) while staff ran the PATH Python 3.12 that has it, so
    the CSO asked to install numpy and pandas. Falls back to labhq's interpreter only when PATH has none."""
    path = (environ if environ is not None else os.environ).get("PATH")
    for name in ("python3", "python", "py"):
        found = shutil.which(name, path=path)
        if found:
            return found
    return sys.executable


def _python3_alias_row(environ: Mapping[str, str] | None = None) -> dict | None:
    path = (environ if environ is not None else os.environ).get("PATH")
    found = shutil.which("python3", path=path)
    if not found or not is_windows_app_alias_dir(found):
        return None
    return _row(
        "staff", "python3 PATH", "warn", "Windows 앱 실행 별칭",
        "Windows 설정 > 앱 > 고급 앱 설정 > 앱 실행 별칭에서 python3를 끄세요.",
    )


def staff_python_summary(environ: Mapping[str, str] | None = None) -> dict:
    """`local_software_summary` of the PATH interpreter staff should use, with the command that runs it.

    One PC can answer `python3` and `python` with different interpreters: on the PI's PC `python3` is a 3.14 with no
    analysis packages and `python` is the 3.12 that has pandas. Every candidate is probed and the one with the most
    analysis packages wins (ties keep the python3, python, py order), so the CSO can tell staff which command to run."""
    path = (environ if environ is not None else os.environ).get("PATH")
    best: dict | None = None
    seen: set[str] = set()
    for name in ("python3", "python", "py"):
        found = shutil.which(name, path=path)
        if not found or os.path.normcase(found) in seen:
            continue
        seen.add(os.path.normcase(found))
        summary = local_software_summary(found)
        summary["python"]["command"] = name
        if best is None or _package_count(summary) > _package_count(best):
            best = summary
    if best is None:
        best = local_software_summary(sys.executable)
        best["python"]["command"] = None
    return best


def _package_count(summary: dict) -> int:
    return sum(1 for available in summary["python"]["packages"].values() if available)


def local_software_summary(python_executable: str) -> dict:
    """Bounded runner facts for planning. Executable paths and probe output never leave this function."""
    rscript = shutil.which("Rscript")
    r_code, r_raw = _probe([rscript, "--version"], dict(os.environ), timeout=3) if rscript else (None, "")
    py_code, py_raw = _probe([python_executable, "--version"], dict(os.environ), timeout=3)
    if py_code != 0:
        # No package probes for an interpreter that does not answer `--version`: the Store `python3` alias that hung
        # the runners (2026-10-08) would cost every import probe its full timeout before the runner connects.
        packages = {name: False for name in LOCAL_PYTHON_PACKAGES}
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(_import_available, python_executable, import_name)
                       for name, import_name in LOCAL_PYTHON_PACKAGES.items()}
            packages = {name: future.result() for name, future in futures.items()}
    return {
        "r": {"available": r_code == 0, "version": _version(r_raw) if r_code == 0 else None},
        "python": {"version": _version(py_raw) if py_code == 0 else "unreported", "packages": packages},
        "tools": {name: bool(shutil.which(name)) for name in LOCAL_TOOLS},
    }


def _paper2agent_row(settings: Settings, agents: list[AgentSpec], fix: bool = False) -> dict:
    if not paper2agent.enabled(settings):
        return _row("staff", "paper2agent", "skip", "disabled by recruit.enabled=false", "")
    hint = "labhq doctor --fix"
    recruiter = next((a for a in agents if a.id == settings.recruit.agent_id), None)
    if settings.runner.force_engine == "mock" or (agents and all(a.engine == Engine.mock for a in agents)
                                                 and not settings.runner.force_engine):
        return _row("staff", "paper2agent", "skip", "mock/demo engines", "")
    try:
        engine = paper2agent.recruiter_engine(settings, recruiter)
        if engine == "mock":
            return _row("staff", "paper2agent", "skip", "mock recruiter", "")
        path = paper2agent.skill_path(engine, settings)
        def ready():
            if not paper2agent.skill_installed(engine, settings):
                return False
            if (settings.recruit.skill_commit is not None
                    and paper2agent.installed_commit(engine, settings) != settings.recruit.skill_commit):
                return False
            if not paper2agent.skill_patched(path):
                return False  # an older install still carries a known upstream defect; setup patches it
            if engine == "claude_code":
                from .adapters.claude_code import read_plugin_manifest
                return (read_plugin_manifest(path.parent.parent) or {}).get("name") == "labhq-paper2agent"
            from .adapters.base import expand_env
            return bool(expand_env(settings.engines.codex.env).get("CODEX_HOME"))
        if not ready() and fix:
            paper2agent.setup_paper2agent(settings)
        visible = ready()
        return _row("staff", "paper2agent", "ok" if visible else "fail",
                    "installed and configured visible to recruiter" if visible else
                    "not installed, not visible to recruiter, or configured commit does not match",
                    hint)
    except subprocess.CalledProcessError:
        detail = "setup-paper2agent git download failed (network unavailable or source/ref inaccessible)"
    except (OSError, ValueError, RuntimeError) as exc:
        detail = f"setup-paper2agent: {exc}" if isinstance(exc, ValueError) else f"setup-paper2agent failed ({type(exc).__name__})"
    return _row("staff", "paper2agent", "fail", detail, hint)


def _login_evidence(code: int | None, raw: str) -> str:
    """Keep unavailable status separate from an explicit signed-out result; never publish raw auth output."""
    if code is None:
        return "unverified"
    text = raw.strip().lower()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("loggedIn") is False:
        return "blocked"
    if text in {"not logged in", "not logged in.", "signed out", "not authenticated"}:
        return "blocked"
    authenticated = (isinstance(payload, dict) and payload.get("loggedIn") is True) or (
        text == "logged in" or text.startswith("logged in using ") or text == "signed in")
    return "ready" if code == 0 and authenticated else "unverified"


def _facilities_support_row(settings: Settings, agents: list[AgentSpec], forced: Engine | None,
                            invalid_force: bool, versions: dict[str, str], logins: dict[str, str],
                            preflights: dict[str, bool], dry_run: bool) -> dict:
    """Assess only the effective facilities agent using evidence already collected by doctor."""
    policy = settings.runner.facilities_support
    agent = next((agent for agent in agents if agent.id == "facilities"), None)
    reasons = []
    if agent is None:
        state = "absent"
        reasons.append("시설팀 지원 없음; 대체 담당 CSO (실응답 준비 미검증)")
    else:
        engine = (forced or agent.engine).value
        if invalid_force or preflights.get(agent.id) is False:
            state = "blocked"
            reasons.append("invalid force_engine or facilities preflight failed")
        elif engine == "mock":
            state = "mock_only"
            reasons.append("mock evidence only")
        elif versions.get(engine) == "blocked" or logins.get(engine) == "blocked":
            state = "blocked"
            reasons.append("executable/version or login failed")
        elif dry_run or versions.get(engine) != "ready" or logins.get(engine) != "ready":
            state = "unverified"
            reasons.append("dry-run: probes skipped" if dry_run else "version/login evidence unavailable")
        else:
            state = "ready"
            reasons.append("executable/version, facilities preflight and login checked; response/quota/recovery unverified")
    status = "fail" if policy == "required" and state != "ready" else (
        "ok" if policy != "unspecified" and state == "ready" else "warn")
    declaration = "; 시설팀 지원 요구 미선언" if policy == "unspecified" else ""
    return _row("support", "facilities", status,
                f"policy={policy}; state={state}{declaration}; " + "; ".join(reasons),
                "runner.facilities_support: required/optional/unspecified; optional은 직원 비활성화 명령이 아닙니다.")


def collect(settings: Settings, *, requested_config: str | None = None, network: bool = False,
            dry_run: bool = False, require_roster: bool = False, fix: bool = False,
            require_paper2agent: bool = True, github_transport=None, runtime: bool = False) -> dict:
    rows: list[dict] = []
    from .cli import _local_process_diagnostics

    rows.extend(_local_process_diagnostics(settings))
    config = settings.config_path
    if requested_config and not config:
        rows.append(_row("config", "file", "fail", "configured file missing", "Set --config to an existing YAML file."))
    else:
        rows.append(_row("config", "file", "ok" if config else "warn",
                         _safe_path(config) if config else "defaults", "Set --config for this host."))
    python3_alias = _python3_alias_row()
    if python3_alias:
        rows.append(python3_alias)
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
    rows.append(_memory_limit_row(settings))
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
    rows.append(_research_row(settings))
    rows.append(_semantics_row(settings))  # semantics-hook
    rows.extend(_semantic_device_rows(settings))
    rows.append(_bioinfo_row(settings))
    rows.append(_github_token_row(settings, dry_run))
    rows.extend(_instance_rows(settings))
    rows.extend(_disk_rows(settings))
    markers = parent_claude_markers(dict(os.environ))
    rows.append(_row("staff", "claude_parent_session_env", "warn" if markers else "ok",
                     f"부모 Claude 세션 마커 {len(markers)}개를 직원 subprocess에서 제거"
                     if markers else "부모 Claude 세션 마커 없음",
                     "직원 CLI는 labhq runner를 통해 시작하세요."))
    rows.append(_row("staff", "codex_user_skills_leak", "ok",
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
        visibility = "unknown"
        if settings.dev_log.repo:
            # collect is synchronous, including callers that already own an event loop.
            with ThreadPoolExecutor(max_workers=1) as pool:
                visibility = pool.submit(asyncio.run, dev_log_visibility(settings, github_transport)).result()
        status = "ok" if visibility == "private" else "warn"
        detail = f"{repo}; visibility: {visibility}; 종료: v1.0, #40 bench 뒤 20 rounds 새 교훈 없음, 또는 보고 채널 이전 (#69)"
        rows.append(_row("config", "dev log", status, detail,
                         "dev_log.repo에 비공개 기록 저장소를 지정하세요." if settings.dev_log.repo else
                         "dev_log.repo는 사람마다 달라 기본값이 없습니다. 개발 기록을 GitHub에도 남기려면 설정 파일의 dev_log.repo에 "
                         "비공개 저장소 owner/name을 적고 gateway를 다시 시작하세요. 비워 두면 이 PC에만 기록합니다."))

    try:
        agents = _roster(settings)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        agents = []
        rows.append(_row("staff", "roster", "fail", type(exc).__name__, "Fix the agent YAML files."))
    if not agents:
        rows.append(_row("staff", "roster", "fail" if require_roster else "warn",
                         "no active agents found", "Set runner.agents_dir."))
    paper_row = (_paper2agent_row(settings, agents, fix=fix and not dry_run) if require_paper2agent else
                 _row("staff", "paper2agent", "skip", "installation deferred during init", "labhq doctor --fix"))
    forced = None
    invalid_force = False
    if settings.runner.force_engine:
        try:
            forced = Engine(settings.runner.force_engine)
        except ValueError:
            invalid_force = True
            rows.append(_row("config", "runner.force_engine", "fail", "unknown engine",
                             "Set runner.force_engine to a supported engine or remove it."))
    roster_engines = ({forced.value} if forced else {agent.engine.value for agent in agents}) - {"mock"}
    available: dict[str, bool] = {}
    if "cli" in roster_engines:
        cli_agents = [agent for agent in agents if (forced or agent.engine) == Engine.cli]
        found = 0
        for agent in cli_agents:
            command = list(agent.cli.command) if agent.cli and agent.cli.command else []
            env = {**os.environ, **expand_env(agent.cli.env, os.environ)} if agent.cli else dict(os.environ)
            cmd = [os.path.expandvars(os.path.expanduser(part)) for part in command]
            # The runner starts staff through _resolve_command, which refuses shims it cannot run (#501 review).
            try:
                runnable = bool(cmd) and bool(_resolve_command(cmd, env, "cli"))
            except (ValueError, OSError):
                runnable = False
            if runnable and shutil.which(cmd[0], path=env.get("PATH")):
                found += 1
        available["cli"] = bool(cli_agents) and found == len(cli_agents)
        rows.append(_row("engine", "cli", "ok" if available["cli"] else "warn",
                         f"cli.command executable found for {found}/{len(cli_agents)} staff",
                         "Set each engine: cli staff member's cli.command and cli.env.PATH."))
    codex_now: dict[str, str | list[str] | None] = {"version": None, "command": None}
    login_codes: dict[str, int | None] = {}  # the CLI's own status command, with engines.<name>.env applied
    version_evidence: dict[str, str] = {}
    login_evidence: dict[str, str] = {}
    for name in settings.engines.__class__.model_fields:
        if name not in roster_engines:
            continue
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
            version_evidence[name] = "blocked"
            rows.append(_row("engine", name, "warn", "executable missing or unsupported shim",
                             f"Install {name} or set engines.{name}.bin and prefix_args."))
            continue
        code, raw = (None, "") if dry_run else _probe([*resolved, "--version"], env)
        version_evidence[name] = ("unverified" if code is None else "blocked" if code != 0 else
                                  "ready" if _version(raw) != "unreported" else "unverified")
        detail = _safe_executable_path(resolved[0], env) + (" (probe skipped: dry-run)" if dry_run else " " + _version(raw))
        if name == "codex":
            codex_now.update(version=_version(raw) if code == 0 and _version(raw) != "unreported" else None,
                             command=resolved)
            choice = codex_app_choice(env) if spec.bin.strip().lower() in ("", "auto") else None
            if choice and choice["path"] == resolved[0]:
                detail += (f"; auto: 앱 폴더 {choice['folder']} 선택 (codex.exe 있는 폴더 {choice['candidates']}개 중 " +
                           ("판본이 가장 높은 폴더)" if choice["by"] == "version" else "판본 비교 불가, 가장 최근 폴더)"))
        rows.append(_row("engine", name, "skip" if dry_run else "ok" if code == 0 else "warn", detail,
                         "Check the executable and prefix_args if version fails."))
        if dry_run:
            continue
        if name in LOGIN:
            code, auth_raw = _probe([*resolved, *LOGIN[name]], env)
            login_codes[name] = code
            login_evidence[name] = _login_evidence(code, auth_raw)
            rows.append(_row("login", name, "ok" if code == 0 else "warn",
                             "status command succeeded" if code == 0 else "status unavailable or signed out",
                             f"Check {name} login locally; doctor never starts login."))
        else:
            rows.append(_row("login", name, "skip", "non-interactive status skipped",
                              "Check login manually before a paid run."))
    if dry_run and roster_engines:
        rows.append(_row("login", "roster engines", "skip",
                         f"dry-run: login not checked ({len(roster_engines)} engines)",
                         "Run doctor without dry-run to check login."))
    sandbox_homes: set[Path] = set()
    preflights: dict[str, bool] = {}
    for agent in agents:
        if invalid_force:
            rows.append(_row("staff", agent.id, "fail", "invalid runner.force_engine",
                             "Fix runner.force_engine before running staff."))
            continue
        if forced:
            agent = agent.model_copy(update={"engine": forced})
        engine = agent.engine.value
        ready = available.get(engine, engine == "mock")
        plugin = engine == "claude_code" and (agent.plugin_dirs or agent.required_skills
                                              or agent.id == settings.recruit.agent_id)
        try:
            error = _adapter_check(settings, agent)
        except OSError:
            error = "adapter preflight files inaccessible"
        preflights[agent.id] = not bool(error)
        if error:
            for raw in agent.plugin_dirs:
                error = error.replace(raw, _safe_path(raw))
        status = ("warn" if plugin else "fail") if error else "ok" if ready else "warn"
        hint = ("무인 실행 전에 직원 CODEX_HOME의 elevated sandbox setup을 대화형으로 마치세요. 이 PC의 다른 "
                "Codex 홈이 elevated면 먼저 unelevated로 바꾸세요(한 PC에 elevated 홈은 하나, #382)."
                if engine == "codex" and error and "elevated sandbox setup" in error else
                "직원 전용 CODEX_HOME에서 codex login한 뒤 engines.codex.env.CODEX_HOME에 지정하세요."
                if engine == "codex" and error and "CODEX_HOME" in error else
                f"Resolve the {engine} adapter preflight or install/configure its executable.")
        rows.append(_row("staff", agent.id, status, error or f"engine={engine}", hint))
        if engine == "codex":
            rows += _sandbox_homes_row(settings, agent, sandbox_homes)
        if engine == "codex" and not error:
            rows += _sandbox_version_rows(settings, agent, codex_now, sandbox_homes, dry_run)
        if plugin:
            rows.append(_row("plugin", agent.id, "warn" if error else "ok", error or "plugin ready",
                             "Set the plugin directory and install its required skill."))
    rows.append(_facilities_support_row(settings, agents, forced, invalid_force, version_evidence,
                                        login_evidence, preflights, dry_run))
    private = _doctor_private(settings, agents, forced)
    rows.append(_private_paths_row(private))
    rows.append(_alphagenome_row(settings))
    rows.append(_alphagenome_atlas_row(settings, agents))
    from .knowledge.recall import health as recall_health
    recall = recall_health(settings)
    rows.append(_row("knowledge", "local recall", "ok" if recall["available"] else "warn",
                     f"count={recall['count']}; last_success={recall['last_success'] or 'never'}; "
                     f"fallback={recall['fallback'] if recall['fallback'] is not None else 'unknown'}",
                     "Start this instance's gateway to build or retry its local index."))
    rows.append(_wiki_row(settings))
    claude_staff = _claude_staff_row(settings, agents, forced, private, login_codes.get("claude_code"))
    if claude_staff:
        rows.append(claude_staff)
    rows.append(paper_row)

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
    if sys.platform == "win32" or settings.runner.wsl.enabled:
        from .wsl_setup import check as check_wsl, configured as wsl_configured

        if not settings.runner.wsl.enabled and not wsl_configured(settings):
            rows.append(_row("WSL", "선택 기능", "skip", "꺼짐", "labhq setup-wsl"))
        else:
            rows.extend(check_wsl(settings) if not dry_run else [
                _row("WSL", "준비 상태", "skip", "dry-run", "labhq setup-wsl --check")])
    if network and not dry_run:
        for name, url in SOURCES.items():
            reachable = _network_check(url)
            rows.append(_row("data", name, "ok" if reachable else "warn",
                             "reachable" if reachable else "unreachable", "Check network access."))
    else:
        reason = "dry-run" if dry_run else "--network not set"
        rows.append(_row("data", "public sources", "skip",
                         f"network checks skipped ({reason}; {len(SOURCES)} sources)",
                         "Use --network to test connectivity."))
    external_hpc = any(any(m.name == "labhq_hpc" for m in agent.mcp) for agent in agents)
    runner_capabilities = {"scheduler": scheduler,
                           "compute_backends": ["local CLI"] + ([scheduler] if scheduler != "none" else []) +
                                               (["external labhq_hpc MCP"] if external_hpc else []),
                           "hpc_tools": scheduler != "none" or external_hpc}
    readiness_reasons = ([] if agents else ["활성 직원 0명"])
    missing_engines = sorted(name for name in roster_engines if name != "mock" and not available.get(name, False))
    if missing_engines:
        readiness_reasons.append("roster engine 실행 파일 없음: " + ", ".join(missing_engines))
    from . import runtime_readiness, windows_launch
    config_reasons = list(readiness_reasons)
    config_failures = sum(r["status"] == "fail" for r in rows)
    if config_failures:
        config_reasons.append(f"fail {config_failures}건")
    operational = runtime_readiness.inspect(settings)
    if runtime:
        operational = runtime_readiness.deployment(settings, operational)
    rows.append(_row("runtime", "daemon identity", "fail" if operational["reasons"] or (
        runtime and operational["ready"] is not True) else "ok" if operational["ready"] else "skip",
        operational["status"] + (": " + "; ".join(operational["reasons"]) if operational["reasons"] else ""),
        "labhq doctor --runtime; inspect PID identity and detached-launch.jsonl; normal down differs from disappearance."))
    if sys.platform == "win32":
        job = windows_launch.current_job()
        rows.append(_row("runtime", "Windows Job (doctor)", "ok" if job["in_job"] is False else "warn",
                         json.dumps(job), "Limits describe only the immediate job, not the cause of a past exit."))
    readiness_reasons.extend(operational["reasons"])
    return {"schema_version": 1, "runner_capabilities": runner_capabilities, "checks": rows,
            "configuration_readiness": {"ready": not config_reasons, "reasons": config_reasons},
            "runtime_readiness": operational,
            "readiness": {"ready": not config_reasons and not operational["reasons"] and (
                not runtime or operational["ready"] is True), "reasons": readiness_reasons},
            "summary": {status: sum(r["status"] == status for r in rows)
                        for status in ("ok", "warn", "fail", "skip")}}


def render(manifest: dict) -> str:
    lines = [f"{'AREA':<9} {'CHECK':<22} {'STATE':<5} DETAIL", "-" * 78]
    for row in manifest["checks"]:
        hint = f" | fix: {row['hint']}" if row["status"] != "ok" and row.get("hint") else ""  # hints only where needed
        lines.append(f"{row['group']:<9} {row['name']:<22} {row['status']:<5} {row['detail']}{hint}")
    fail_count = manifest.get("summary", {}).get("fail")
    if fail_count is None:
        fail_count = sum(row.get("status") == "fail" for row in manifest["checks"])
    if "configuration_readiness" in manifest:
        configured = manifest["configuration_readiness"]
        runtime = manifest["runtime_readiness"]
        lines.append(f"설정 준비: {'예' if configured['ready'] else '아니오'}")
        lines.append(f"운영 준비: {'예' if runtime['ready'] is True else '아니오' if runtime['ready'] is False else '미확인'} "
                     f"({runtime['status']})")
    readiness = manifest.get("readiness")
    if readiness is None:  # manifests written before readiness reasons were recorded
        lines.append(f"실행 준비: {'예' if fail_count == 0 else f'아니오(fail {fail_count}건)'}")
    else:
        reasons = ([f"fail {fail_count}건"] if fail_count else []) + list(readiness.get("reasons", []))
        lines.append(f"실행 준비: {'예' if not reasons and readiness.get('ready', False) else '아니오'}")
        if reasons:
            lines.append("이유: " + "; ".join(reasons))
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
