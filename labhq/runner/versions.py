"""Engine CLI versions a runner reports with its capabilities, for round records."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading

from ..adapters.base import _resolve_command, expand_env
from ..settings import Settings

# How long ending a timed-out probe may wait, inside the probe thread; the caller does not wait for it.
_KILL_GRACE = 5
# How long past the probe timeout the caller waits for the probe thread.
_JOIN_SLACK = 1


def _probe(argv: list[str], env: dict[str, str], timeout: float = 5) -> tuple[int | None, str]:
    """Run a short probe and return (exit code, stdout or else stderr); (None, "") when it fails or times out.

    The caller waits at most `timeout` plus `_JOIN_SLACK`, whatever the probe does; ending a timed-out probe goes on
    in the probe thread. On 2026-10-08 starting the Python Install Manager's `python3` app-execution alias did not
    return from CreateProcess (inside `Popen`, before any timeout applies), and both runners hung before connecting
    to their gateways. The thread is a daemon, so a call that never returns leaves only that thread behind."""
    box: list[tuple[int | None, str]] = []
    worker = threading.Thread(target=lambda: box.append(_run_probe(argv, env, timeout)), daemon=True,
                              name="labhq-probe")
    worker.start()
    worker.join(timeout + _JOIN_SLACK)
    return box[0] if box else (None, "")


def _run_probe(argv: list[str], env: dict[str, str], timeout: float) -> tuple[int | None, str]:
    """Output goes to temporary files, not pipes, so nothing waits for a pipe another process still holds."""
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                return None, ""
            out.seek(0)
            err.seek(0)
            raw = out.read() or err.read()
            return code, raw.decode(errors="replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _kill_tree(proc: subprocess.Popen) -> None:
    """End a timed-out probe and, on Windows, whatever it started; every wait here is bounded."""
    if os.name == "nt":
        try:
            killer = subprocess.Popen(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            killer.wait(timeout=_KILL_GRACE)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
        proc.wait(timeout=_KILL_GRACE)
    except (OSError, subprocess.SubprocessError):
        pass


def _version(raw: str) -> str:
    match = re.search(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-.][A-Za-z0-9]+)*", raw[:300])
    return match.group(0) if match else "unreported"


def engine_cli_versions(settings: Settings, engines: set[str]) -> dict[str, str]:
    """`--version` of each configured engine the roster uses; "unreported" when the CLI does not say."""
    out = {}
    for name in sorted(engines):
        spec = getattr(settings.engines, name, None)
        if spec is None:  # mock and custom engines have no configured binary
            continue
        env = {**os.environ, **expand_env(spec.env, os.environ)}
        cmd = [os.path.expandvars(os.path.expanduser(spec.bin)),
               *(os.path.expandvars(os.path.expanduser(a)) for a in spec.prefix_args)]
        try:
            resolved = _resolve_command(cmd, env, name)
        except (ValueError, OSError):
            continue
        code, raw = _probe([*resolved, "--version"], env)
        out[name] = _version(raw) if code == 0 else "unreported"
    return out
