"""Read-only capability checks for the runner host (apart from optional manifest output)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from .adapters.base import RunContext, _resolve_command, expand_env
from .adapters.claude_code import ClaudeCodeAdapter
from .models import AgentSpec, Task
from .recruit.paper2agent import skill_installed
from .settings import Settings

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


def _probe(argv: list[str], env: dict[str, str]) -> tuple[int | None, str]:
    try:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, errors="replace", timeout=5,
                              stdin=subprocess.DEVNULL)
        return done.returncode, (done.stdout or done.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _version(raw: str) -> str:
    match = re.search(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-.][A-Za-z0-9]+)*", raw[:300])
    return match.group(0) if match else "unreported"


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


def _plugin_check(settings: Settings, agent: AgentSpec, env: dict[str, str]) -> str | None:
    ctx = RunContext(task=Task(agent_id=agent.id, prompt=""), agent=agent,
                     workdir=settings.path(settings.runner.workspace_root), settings=settings,
                     mcp_servers=[], env={}, emit=lambda *_: None, prompt="")
    return ClaudeCodeAdapter(settings).preflight_error(ctx, env)


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
    for name, raw in (("gateway state", settings.gateway.state_dir), ("runner state", settings.runner.state_dir),
                      ("workspace", settings.runner.workspace_root)):
        path = settings.path(raw)
        good = _writable(path)
        rows.append(_row("config", name, "ok" if good else "fail", _safe_path(path),
                         "Choose a writable directory."))
    restricted = [z for z in settings.policy.data_zones if z.level == "restricted"]
    refused = bool(restricted) and sys.platform == "win32"
    rows.append(_row("config", "restricted data zones", "fail" if refused else "ok",
                     f"{len(restricted)} configured" + ("; Windows runner refuses them" if refused else ""),
                     "Run on a supported POSIX runner or remove restricted zones for a safe test."))

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
        engine = agent.engine.value
        ready = available.get(engine, engine == "mock")
        rows.append(_row("staff", agent.id, "ok" if ready else "warn", f"engine={engine}",
                         f"Install or configure the {engine} engine."))
        if agent.plugin_dirs or agent.required_skills:
            try:
                error = _plugin_check(settings, agent,
                                      {**os.environ, **expand_env(settings.engines.claude_code.env, os.environ)})
            except OSError:
                error = "plugin files inaccessible"
            if error:
                for raw in agent.plugin_dirs:
                    error = error.replace(raw, _safe_path(raw))
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
        lines.append(f"{row['group']:<9} {row['name']:<22} {row['status']:<5} {row['detail']} | fix: {row['hint']}")
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
