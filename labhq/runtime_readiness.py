"""Read-only local daemon evidence. Missing identity is never evidence of a running process."""
from __future__ import annotations

import json


def inspect(settings) -> dict:
    from . import cli

    root = cli._launch_state(settings)
    roles = {}
    failures = []
    for role in ("gateway", "runner"):
        process = cli._process_record(settings, role, cleanup=False)[0]
        supervisor = cli._process_record(settings, cli._supervisor_role(role), cleanup=False)[0]
        supervised = supervisor != "missing" or (root / "logs" / f"{role}-supervisor.log").exists()
        history = supervisor != "missing" or process != "missing"
        journal = root / "logs" / "detached-launch.jsonl"
        try:
            with journal.open(mode="rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 65536))
                if size > 65536:
                    stream.readline()  # The bounded tail may start in the middle of a JSON line.
                entries = [json.loads(line) for line in stream.read().splitlines() if line.strip()]
                history = history or any(row.get("role") in (role, cli._supervisor_role(role)) for row in entries)
                supervised = supervised or any(row.get("role") == cli._supervisor_role(role) for row in entries)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            failures.append(f"{role}: launch history unknown")
        # Foreground role diagnostics alone do not mean this host owns a managed daemon.
        history = history or (root / "logs" / f"{role}-supervisor.log").exists()
        stopped = cli._stopping_file(settings, role).is_file()
        if process == "unknown" or supervisor == "unknown":
            state = "unknown"
        elif process == "alive" and (supervisor == "alive" or not supervised):
            state = "running"
        elif history and stopped and process != "alive" and supervisor != "alive":
            state = "down"
        elif history:
            state = "missing" if process == supervisor == "missing" else "stale"
        else:
            state = "unmanaged"
        roles[role] = {"state": state, "process": process, "supervisor": supervisor}
        if state in ("unknown", "stale", "missing"):
            failures.append(f"{role}: {state} process identity")
    states = [r["state"] for r in roles.values()]
    foreground_evidence = any((root / "logs" / f"{role}-diagnostics.jsonl").exists() for role in roles)
    status = "unhealthy" if failures else "down" if "down" in states else (
        "running" if all(s == "running" for s in states) else "unmanaged" if foreground_evidence else "pristine")
    return {"ready": False if failures or status == "down" else True if status == "running" else None,
            "status": status, "roles": roles, "reasons": failures}


def deployment(settings, local: dict) -> dict:
    """Authenticate foreground/remote roles without inventing local PIDs. Called only for --runtime."""
    from . import cli, instance_guard

    result = {**local, "reasons": list(local["reasons"])}
    try:
        health = cli._health(settings)
        confirmed = bool(health) and instance_guard.check_health(settings, health, started_here=False)[1]
    except (OSError, SystemExit):
        confirmed, health = False, None
    if not confirmed:
        result["reasons"].append("gateway: authenticated identity unavailable")
    elif settings.runner.id not in (health.get("runners") or []):
        result["reasons"].append("runner: not connected to authenticated gateway")
    if confirmed and settings.runner.wsl.enabled and settings.runner.wsl.id not in (health.get("runners") or []):
        result["reasons"].append("WSL runner: not connected to authenticated gateway")
    result["ready"] = not result["reasons"] and local["status"] != "down"
    result["status"] = "verified" if result["ready"] else local["status"]
    return result
