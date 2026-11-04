"""Small stdlib bootstrap, run by WSL's python3 before the labhq venv exists.

Input is JSON on stdin; output is bounded facts, never installer output or credentials.
State, virtualenv and PID records stay on Linux. Work uses the Windows shared drive.
"""
from __future__ import annotations

import hashlib
import copy
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, build_opener


def run(args, timeout=20):
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 1, b"")


def private_write(path: Path, content: str):
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def gateway_url(data):
    parsed = urlsplit(data["gateway_url"])
    if parsed.scheme not in ("ws", "wss") or parsed.username or parsed.password:
        return None
    candidates = [data["gateway_url"]]
    # WSL2 NAT: localhost is not always forwarded from Linux to Windows.
    if parsed.hostname in ("localhost", "127.0.0.1"):
        route = run(["ip", "route", "show", "default"]).stdout.decode().split()
        if "via" in route:
            host = route[route.index("via") + 1]
            port = parsed.port or (443 if parsed.scheme == "wss" else 80)
            candidates.append(urlunsplit(parsed._replace(netloc=f"{host}:{port}")))
    opener = build_opener(ProxyHandler({}))
    fetch = opener.open
    for candidate in candidates:
        url = urlsplit(candidate)
        health_url = urlunsplit(url._replace(scheme="https" if url.scheme == "wss" else "http",
                                             path="/api/health", query="", fragment=""))
        try:
            with fetch(health_url, timeout=3) as response:
                health = json.load(response)
            if (health.get("service") == "labhq gateway" and data.get("identity")
                    and health.get("identity") == data["identity"]):
                return candidate
        except Exception:
            continue
    return None


def lifecycle(root: Path, action: str):
    from labhq import cli
    from labhq.settings import Settings

    s = Settings.load(str(root / "labhq.yaml"))
    with cli._operation_lock(s):
        for role in ("runner", cli._supervisor_role("runner")):
            state, _, reason = cli._process_record(s, role)
            if state == "unknown":
                raise RuntimeError("identity")
        if action == "start":
            # The child claims its own runner state through the regular CLI identity guard.
            state, _, _ = cli._process_record(s, "runner")
            if state != "alive" and not cli._supervised(s, "runner"):
                cli._start_background(s, "runner")
        else:
            cli._stop_background(s, "runner")
    return {"ok": True}


def main(data):
    root = Path(data["root"]).expanduser().absolute()
    # chmod on drvfs is not proof that a credential is private. Use Linux storage.
    if not root.is_relative_to(Path.home()) or root.is_symlink():
        return {"ok": False, "reason": "root"}
    if root.resolve() != root or root == Path.home():
        return {"ok": False, "reason": "root"}
    os.environ["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["PYTHONUTF8"] = "1"
    os.environ.pop("LABHQ_CONFIG", None)
    os.environ.pop("LABHQ_STATE_DIR", None)
    os.umask(0o077)
    python = root / "venv/bin/python"
    config = root / "labhq.yaml"
    action = data["action"]
    if action == "probe":
        # Direct script invocation must also work before the venv/package is installed.
        if not __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from labhq.wsl_readiness import probe

        facts = probe([data["bioinfo_agent"]],
                      {**os.environ, "CLAUDE_CONFIG_DIR": str(root / "claude-staff")})
        return {"ok": True, **facts,
                "docker": run(["docker", "info"], timeout=10).returncode == 0,
                "labhq": run([str(python), "-c", "import labhq.cli"]).returncode == 0,
                "registered": config.is_file() and run([str(python), "-c",
                    "from labhq.settings import Settings; import sys; "
                    "s=Settings.load(sys.argv[1]); sys.exit(s.runner.id != sys.argv[2])",
                    str(config), data["id"]]).returncode == 0}
    if action == "gateway":
        return {"ok": True, "url": gateway_url(data)}
    if action == "install":
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        if not python.exists() and run(["python3", "-m", "venv", str(root / "venv")], 60).returncode:
            return {"ok": False, "reason": "venv"}
        source = Path(data["source"])
        stamp = hashlib.sha256((str(source) + (source / "pyproject.toml").read_text(encoding="utf-8")).encode()).hexdigest()
        marker = root / "installed-source"
        if not marker.exists() or marker.read_text(encoding="utf-8") != stamp:
            if run([str(python), "-m", "pip", "install", "-e", str(source)], 480).returncode:
                return {"ok": False, "reason": "install"}
            private_write(marker, stamp)
        if data.get("install_nextflow") and not shutil.which("nextflow"):
            target = Path.home() / ".local/bin/nextflow"
            # Never replace an existing installation, even when Java is missing.
            if not target.exists():
                from urllib.request import urlopen
                with urlopen("https://github.com/nextflow-io/nextflow/releases/download/v24.10.5/nextflow", timeout=30) as r:
                    private_write(target, r.read().decode("utf-8"))
                target.chmod(0o700)
        return {"ok": True}
    # Re-enter through the installed interpreter for config validation and the existing supervisor.
    if Path(sys.prefix) != root / "venv":
        result = subprocess.run([str(python), str(Path(__file__).resolve())], input=json.dumps(data),
                                capture_output=True, text=True, timeout=70)
        return json.loads(result.stdout) if result.returncode == 0 else {"ok": False}
    if action in ("start", "stop"):
        return lifecycle(root, action)
    if action == "configure":
        import yaml
        from labhq import cli
        from labhq.settings import Settings

        old = yaml.safe_load(config.read_text(encoding="utf-8")) if config.exists() else None
        port = (old or {}).get("runner", {}).get("broker_port")
        if not port:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        settings = {"instance": "wsl-runner", "gateway": {"url": data["url"],
                    "runner_token": data["token"], "client_token": "", "state_dir": str(root / "supervisor")},
                    "runner": {"id": data["id"], "environment": "wsl", "state_dir": str(root / "state"),
                               "workspace_root": data["workspace_root"], "agents_dir": str(root / "agents"),
                               "reference_roots": data.get("reference_roots", []),
                               "talent_dir": str(root / "talent"), "contract_dir": str(root / "contracts"),
                               "broker_port": port, "force_engine": data.get("force_engine")},
                    "hpc": {"scheduler": "none"}, "supervisor": data["supervisor"],
                    "policy": copy.deepcopy(data["policy"]),
                    # The tier -> model table decides a model-less agent's model here too (#625).
                    "model_tiers": data.get("model_tiers") or {}}
        settings["engines"] = {"claude_code": {"bin": "claude", "env": {
            "CLAUDE_CONFIG_DIR": str(root / "claude-staff")}},
            "codex": {"bin": "codex", "env": {"CODEX_HOME": str(root / "codex-staff")}}}
        settings["annot"] = {"alphagenome_key_file": str(root / "secrets/alphagenome_api_key")}
        if data.get("private_defaults"):
            from labhq.private_paths import DEFAULT_HOME_ENTRIES
            # Keep Linux credentials protected as well as the inherited Windows paths.
            settings["policy"]["private_paths"] = [*settings["policy"]["private_paths"],
                *(str(Path.home() / name) for name in DEFAULT_HOME_ENTRIES),
                str(config), str(root / "supervisor"), str(root / "claude-staff"),
                str(root / "codex-staff"), str(root / "secrets")]
        Settings.model_validate(settings)
        agent = yaml.safe_load((Path(data["source"]) / "agents/core/bioinfo-agent.yaml").read_text(encoding="utf-8"))
        agent["plugin_dirs"] = [data["bioinfo_agent"]]
        target = root / "agents/core/bioinfo-agent.yaml"
        agent_text = yaml.safe_dump(agent, allow_unicode=True, sort_keys=False)
        if config.exists():
            s = Settings.load(str(config))
            with cli._operation_lock(s):
                live = cli._supervised(s, "runner") or cli._process_record(s, "runner")[0] != "missing"
                changed = old != settings or not target.exists() or target.read_text(encoding="utf-8") != agent_text
                if live and changed:
                    return {"ok": False, "reason": "busy"}
                if not changed:
                    return {"ok": True}
        private_write(target, agent_text)
        private_write(config, yaml.safe_dump(settings, allow_unicode=True, sort_keys=False))
        return {"ok": True}
    return {"ok": False}


if __name__ == "__main__":
    try:
        answer = main(json.load(sys.stdin))
    except BaseException:
        answer = {"ok": False}  # no traceback: even a validation error may contain a token
    print(json.dumps(answer))
