"""Engine CLI versions a runner reports with its capabilities, for round records."""
from __future__ import annotations

import os
import re
import subprocess

from ..adapters.base import _resolve_command, expand_env
from ..settings import Settings


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
