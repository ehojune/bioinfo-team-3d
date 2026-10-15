"""One readiness contract for setup, diagnostics and runner selection (stdlib only)."""
import json
from pathlib import Path
import shutil
import subprocess

from .plugin_checks import read_plugin_manifest, skill_file

REQUIRED = ("bioinfo_agent", "bioinfo_skill", "java", "nextflow", "claude_cli", "claude_login")


def wsl_readiness(facts: dict) -> bool:
    return all(facts.get(key) is True for key in REQUIRED)


def probe(plugin_dirs: list[str], env: dict[str, str], claude: str = "claude") -> dict:
    def run(args):
        try:
            return subprocess.run(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return subprocess.CompletedProcess(args, 1, b"")

    plugins = {str(manifest.get("name") or ""): Path(raw) for raw in plugin_dirs
               if (manifest := read_plugin_manifest(Path(raw))) is not None}
    plugin = plugins.get("bioinfo")
    executable = shutil.which(claude, path=env.get("PATH", ""))
    # WSL must run its Linux CLI, not an inherited Windows executable or npm shim.
    cli = bool(executable and not executable.lower().endswith((".exe", ".cmd", ".bat"))
               and run([executable, "--version"]).returncode == 0)
    login = False
    if cli:
        result = run([executable, "auth", "status"])
        try:
            status = json.loads(result.stdout)
            login = result.returncode == 0 and isinstance(status, dict) and status.get("loggedIn") is True
        except (ValueError, UnicodeError):
            pass
    return {"bioinfo_agent": plugin is not None,
            "bioinfo_skill": plugin is not None and skill_file(plugin, "bioinfo-analyze").is_file(),
            "java": run(["java", "-version"]).returncode == 0,
            "nextflow": run(["nextflow", "-version"]).returncode == 0,
            "claude_cli": cli, "claude_login": login}
