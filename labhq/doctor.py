"""Read-only capability checks for the runner host (apart from optional manifest output)."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from .adapters.base import RunContext, _resolve_command, expand_env
from .adapters import get_adapter
from .models import AgentSpec, Engine, Task
from .recruit.paper2agent import skill_installed
from .runner.daemon import check_data_boundary, check_job_group
from .runner.versions import _probe, _version
from .settings import Settings
from .util import parent_claude_markers

SOURCES = {
    "ENA": "https://www.ebi.ac.uk/ena/browser/home",
    "NCBI E-utilities": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi",
    "GEO": "https://www.ncbi.nlm.nih.gov/geo/",
}
LOGIN = {"claude_code": ["auth", "status"], "codex": ["login", "status"]}


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
    ctx = RunContext(task=Task(agent_id=agent.id, prompt=""), agent=agent,
                     workdir=settings.path(settings.runner.workspace_root), settings=settings,
                     mcp_servers=[], env={}, emit=lambda *_: None, prompt="")
    adapter = get_adapter(agent.engine, settings)
    return adapter.preflight_error(ctx, {**os.environ, **adapter.engine_env(), **ctx.env})


def _network_check(url: str) -> bool:
    for method in ("HEAD", "GET"):
        try:
            with urlopen(Request(url, method=method, headers={"User-Agent": "labhq-doctor/1"}), timeout=4) as response:
                return 200 <= response.status < 400
        except Exception:
            continue
    return False


def collect(settings: Settings, *, requested_config: str | None = None, network: bool = False) -> dict:
    rows: list[dict] = []
    config = settings.config_path
    if requested_config and not config:
        rows.append(_row("config", "file", "fail", "configured file missing", "Set --config to an existing YAML file."))
    else:
        rows.append(_row("config", "file", "ok" if config else "warn",
                         _safe_path(config) if config else "defaults", "Set --config for this host."))
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
        good = _writable(path)
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
        code, raw = _probe([*resolved, "--version"], env)
        detail = _safe_path(resolved[0]) + " " + _version(raw)
        rows.append(_row("engine", name, "ok" if code == 0 else "warn", detail,
                         "Check the executable and prefix_args if version fails."))
        if name in LOGIN:
            code, _ = _probe([*resolved, *LOGIN[name]], env)
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
        rows.append(_row("staff", "roster", "warn", "no active agents found", "Set runner.agents_dir."))
    for agent in agents:
        if settings.runner.force_engine:
            agent = agent.model_copy(update={"engine": Engine(settings.runner.force_engine)})
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
        hint = ("직원 전용 CODEX_HOME에서 codex login한 뒤 engines.codex.env.CODEX_HOME에 지정하세요."
                if engine == "codex" and error and "CODEX_HOME" in error else
                f"Resolve the {engine} adapter preflight or install/configure its executable.")
        rows.append(_row("staff", agent.id, status, error or f"engine={engine}", hint))
        if plugin:
            rows.append(_row("plugin", agent.id, "warn" if error else "ok", error or "plugin ready",
                             "Set the plugin directory and install its required skill."))
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
    if scheduler in ("sge", "pbs"):
        tools = ("qsub", "qstat")
        present = all(shutil.which(tool) for tool in tools)
        remote = bool(settings.hpc.ssh_host)
        rows.append(_row("compute", "scheduler", "ok" if present else "warn",
                         f"{scheduler}; local qsub/qstat {'present' if present else 'missing'}" +
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
        reachable = _network_check(url) if network else None
        rows.append(_row("data", name, "ok" if reachable else "warn",
                         "reachable" if reachable else "unreachable" if network else "skipped",
                         "Use --network to test connectivity." if not network else "Check network access."))
    external_hpc = any(any(m.name == "labhq_hpc" for m in agent.mcp) for agent in agents)
    runner_capabilities = {"scheduler": scheduler,
                           "compute_backends": ["local CLI"] + ([scheduler] if scheduler != "none" else []) +
                                               (["external labhq_hpc MCP"] if external_hpc else []),
                           "hpc_tools": scheduler != "none" or external_hpc}
    return {"schema_version": 1, "runner_capabilities": runner_capabilities, "checks": rows,
            "summary": {status: sum(r["status"] == status for r in rows) for status in ("ok", "warn", "fail")}}


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
