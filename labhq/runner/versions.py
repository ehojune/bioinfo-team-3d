"""Engine CLI versions a runner reports with its capabilities, for round records."""
from __future__ import annotations

import os

from ..adapters.base import _resolve_command, expand_env
from ..doctor import _probe, _version
from ..settings import Settings


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
