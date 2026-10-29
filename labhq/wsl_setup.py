"""Windows entry points for the instance's local WSL runner (#612).

Only the bootstrap's JSON stdin carries credentials. Never echo subprocess output or exceptions:
installer errors can include config content. The Linux bootstrap owns its private config and PID records.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import yaml

from . import instance_guard
from . import wsl_readiness
from .settings import Settings
from .util import atomic_write_text
from .wsl_paths import shared_path


class WslError(RuntimeError):
    pass


def distributions() -> list[str]:
    try:
        result = subprocess.run(["wsl.exe", "--list", "--quiet"], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=15)
        raw = result.stdout
        text = raw.decode("utf-16-le" if b"\0" in raw else "utf-8", errors="replace")
        return [line.strip() for line in text.splitlines() if line.strip()] if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def _prefix(s: Settings) -> list[str]:
    args = ["wsl.exe", "--distribution", s.runner.wsl.distro]
    if s.runner.wsl.user:
        args += ["--user", s.runner.wsl.user]
    # Do not inherit a Windows working directory: WSL can stall while entering a busy drvfs checkout.
    return args + ["--cd", "~", "--exec"]


def _linux_path(s: Settings, path: Path) -> str:
    try:
        result = subprocess.run(_prefix(s) + ["wslpath", "-a", "-u", path.resolve().as_posix()],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode == 0 and result.stdout.strip().startswith("/"):
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise WslError("WSL에서 개발 폴더를 읽지 못했습니다.")


def _root(s: Settings) -> str:
    key = hashlib.sha256(str(s.config_path).encode()).hexdigest()[:12]
    return s.runner.wsl.root or f"~/.local/share/labhq/wsl-{key}"


def configured(s: Settings) -> bool:
    """Whether this config opted into WSL settings, as distinct from model defaults."""
    if not s.config_path:
        return False
    try:
        data = yaml.safe_load(Path(s.config_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    runner = data.get("runner") if isinstance(data, dict) else None
    return isinstance(runner, dict) and isinstance(runner.get("wsl"), dict)


def _remote(s: Settings, action: str, **extra) -> dict:
    checkout = Path(__file__).resolve().parents[1]
    source = _linux_path(s, checkout)
    payload = {"action": action, "root": _root(s),
               "source": source, "id": s.runner.wsl.id, "bioinfo_agent": s.runner.wsl.bioinfo_agent,
               "gateway_url": s.runner.wsl.gateway_url or s.gateway.url,
               "identity": instance_guard.public_identity(s), **extra}
    try:
        result = subprocess.run(_prefix(s) + ["python3", f"{source}/labhq/wsl_bootstrap.py"],
                                input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                                timeout=600 if action == "install" else 90)
        answer = json.loads(result.stdout) if result.returncode == 0 else {}
        if not isinstance(answer, dict):
            answer = {}
        if isinstance(answer, dict) and answer.get("ok"):
            return answer
        if answer.get("reason") == "venv":
            raise WslError("Python venv가 없습니다. WSL에서 직접 실행: sudo apt install python3-venv")
        if answer.get("reason") == "busy":
            raise WslError("WSL runner가 실행 중입니다. 설정을 바꾸려면 먼저 wsl-runner stop을 실행하세요.")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    raise WslError("WSL 작업이 끝나지 않았습니다. 배포판·Python·폴더 권한을 확인하세요.")


LABELS = {"distro": "WSL 배포판", "java": "Java 실행", "nextflow": "Nextflow 실행",
          "docker": "Docker 실행", "bioinfo_agent": "bioinfo-agent manifest", "bioinfo_skill": "bioinfo-analyze skill",
          "claude_cli": "WSL Claude CLI", "claude_login": "WSL Claude 로그인", "ready": "WSL 분석 준비",
          "labhq": "WSL labhq 설치",
          "registered": "WSL runner 등록", "connected": "WSL runner 연결"}
ADMIN_HINTS = {"distro": "Windows 관리자 터미널: wsl --install -d Ubuntu-22.04",
               "claude_cli": "WSL 안에 Linux용 Claude Code CLI를 설치하고 setup-wsl --check를 다시 실행하세요.",
               "claude_login": "WSL runner의 claude-staff 폴더를 CLAUDE_CONFIG_DIR로 지정한 뒤 claude auth login을 실행하세요.",
               "java": "WSL에서 직접 실행: sudo apt install openjdk-17-jre-headless",
               "docker": "WSL에서 직접 실행: sudo apt install docker.io && sudo service docker start\n"
                         "  Docker 사용 권한: sudo usermod -aG docker $USER (다시 로그인)"}


SETUP_LIMIT_S = 900.0  # one setup run started from a PI card (#612); installers can take minutes


def pending_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("status") != "ok"]


def card_detail(rows: list[dict]) -> dict:
    """What a PI card shows about WSL: names of what is missing and the commands only an administrator can run. Names
    and fixed hints only, never subprocess output (it can carry config content)."""
    pending = pending_rows(rows)
    admin = set(ADMIN_HINTS.values())
    return {"missing": [str(row["name"]) for row in pending],
            "admin_commands": [str(row["hint"]) for row in pending if row.get("hint") in admin]}


def check(s: Settings) -> list[dict]:
    present = s.runner.wsl.distro in distributions()
    facts = {}
    if present:
        try:
            facts = _remote(s, "probe")
        except WslError:
            pass
    # Authenticated health belongs to this Windows config, not a server that happens to share its port.
    from .cli import _health
    try:
        health = _health(s) if present else None
        connected = bool(health and instance_guard.check_health(s, health, started_here=False)[1]
                         and s.runner.wsl.id in health.get("runners", []))
    except (SystemExit, OSError):
        connected = False
    values = {**facts, "ready": wsl_readiness.wsl_readiness(facts), "distro": present, "connected": connected}
    hints = {**ADMIN_HINTS, "claude_login":
             f"WSL runner 경로 {_root(s).rstrip('/')}/claude-staff를 CLAUDE_CONFIG_DIR로 지정한 뒤 "
             "claude auth login을 실행하세요."}
    return [{"group": "WSL", "name": name, "status": "ok" if values.get(key) is True else "warn",
             "detail": "ok" if values.get(key) is True else "없음",
             "hint": hints.get(key, "labhq setup-wsl을 실행하세요.")}
            for key, name in LABELS.items()]


def render(rows: list[dict]) -> str:
    lines = [f"{r['name']}: {r['detail']}" for r in rows]
    lines.extend(r["hint"] for r in rows if r["status"] != "ok" and r["name"] in
                 {LABELS[k] for k in ADMIN_HINTS})
    return "\n".join(lines)


def _save_enabled(s: Settings) -> None:
    path = Path(s.config_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    wsl = s.runner.wsl.model_dump(exclude_none=True)
    wsl["enabled"] = True
    data.setdefault("runner", {})["wsl"] = wsl
    atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    s.runner.wsl.enabled = True


def _shared_settings(s: Settings) -> dict:
    """Snapshot the Windows policy on each setup; refuse anything we cannot map."""
    from .private_paths import configured_private_paths

    def convert(raw):
        expanded = os.path.expandvars(os.path.expanduser(str(raw)))
        # Settings-relative paths and home defaults are resolved on Windows, never in WSL.
        if not expanded.startswith(("/", "\\")) and not (len(expanded) > 1 and expanded[1] == ":"):
            expanded = str(s.path(expanded).resolve())
        return shared_path(expanded, "wsl")

    try:
        policy = s.policy.model_dump()
        for zone in policy["data_zones"]:
            zone["path"] = convert(zone["path"])
        private = policy["private_paths"]
        if private is None:
            private = [path for path, _label in configured_private_paths(s, str(Path.home()))]
        policy["private_paths"] = [convert(path) for path in private]
        roots = [*s.runner.reference_roots, *(p.local_dir for p in s.projects if p.local_dir)]
        return {"workspace_root": convert(s.runner.workspace_root), "policy": policy,
                "reference_roots": list(dict.fromkeys(convert(root) for root in roots)),
                "private_defaults": s.policy.private_paths is None}
    except ValueError as exc:
        raise WslError("WSL shared 경로로 바꾸지 못해 설정을 거부했습니다.") from exc


def setup(s: Settings) -> list[dict]:
    if not s.config_path:
        raise WslError("먼저 labhq init으로 설정을 만들고 -c로 선택하세요.")
    if s.runner.id == s.runner.wsl.id:
        raise WslError("Windows와 WSL의 runner.id는 달라야 합니다.")
    if s.runner.wsl.distro not in distributions():
        return check(s)  # administrator commands are advice only
    shared = _shared_settings(s)
    facts = _remote(s, "probe")
    _remote(s, "install", install_nextflow=not facts.get("nextflow"))
    # Re-probe after installation; a successful connection alone never means analysis is ready.
    if not wsl_readiness.wsl_readiness(_remote(s, "probe")):
        return check(s)
    # Verify the reachable gateway by its identity before sending the runner token anywhere over the network.
    network = _remote(s, "gateway")
    if not network.get("url"):
        raise WslError("WSL에서 이 gateway에 닿지 못했습니다. 먼저 up을 실행하세요. "
                       "NAT 방식이면 gateway.host와 방화벽을 확인하고 runner.wsl.gateway_url에 Windows 주소를 넣으세요.")
    _remote(s, "configure", url=network["url"], token=s.gateway.runner_token,
            supervisor=s.supervisor.model_dump(), force_engine=s.runner.force_engine, **shared)
    control(s, "start")
    if not wsl_readiness.wsl_readiness(_remote(s, "probe")):
        return check(s)
    _save_enabled(s)
    return check(s)


def control(s: Settings, action: str) -> None:
    if action == "start" and s.runner.id == s.runner.wsl.id:
        raise WslError("Windows와 WSL의 runner.id는 달라야 합니다.")
    _remote(s, action)
    if action == "start":
        from .cli import _health
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            health = _health(s)
            if (health and instance_guard.check_health(s, health, started_here=False)[1]
                    and s.runner.wsl.id in health.get("runners", [])):
                return
            time.sleep(0.5)
        raise WslError("WSL runner를 시작했지만 gateway 연결은 확인하지 못했습니다.")
