"""`semantics:` setting of the semantics shadow (#150 B1): off by default, off or shadow in this version.

A bad value turns semantics off with one warning and never fails the settings load. The gateway imports
this module only when the setting is not off; every line that wires it in elsewhere ends with
``# semantics-hook`` so it can be taken out again.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("labhq.semantics")

MODES = ("off", "shadow")
HELD_MODES = ("advisory", "ab")  # B2: CSO advisory and A/B, held by the PI (#149)
KEYS = ("mode", "timeout_s", "history_requests")
DEFAULT_TIMEOUT_S = 5.0
STUCK_S = 10.0                   # a job running longer than this turns the shadow off


# ---------------------------------------------------------------- settings

@dataclass(frozen=True)
class ShadowConfig:
    timeout_s: float = DEFAULT_TIMEOUT_S
    history_requests: int = 200


_warned: set[str] = set()


def _mode(value: Any) -> str | None:
    if value is None or value is False:  # YAML reads a bare `off` as false
        return "off"
    return value if isinstance(value, str) else None


def _ignored(raw: Any, why: str) -> None:
    key = repr(raw)[:500]
    if key not in _warned:
        _warned.add(key)
        log.warning("semantics setting ignored (%s); semantics stays off", why)
    return None


def resolve(raw: Any) -> ShadowConfig | None:
    """The shadow config, or None for off. A bad value turns semantics off with one warning, never raises."""
    try:
        options: Mapping[str, Any] = {}
        if isinstance(raw, Mapping):
            unknown = sorted(str(k) for k in raw if k not in KEYS)
            if unknown:
                return _ignored(raw, f"unknown keys {unknown}")
            options = raw
            mode = _mode(raw.get("mode", "off"))
        else:
            mode = _mode(raw)
        if mode == "off":
            return None
        if mode in HELD_MODES:
            return _ignored(raw, f"mode {mode} is held (B2); this version supports off and shadow")
        if mode not in MODES:
            return _ignored(raw, "mode must be off or shadow")
        timeout = options.get("timeout_s", DEFAULT_TIMEOUT_S)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= STUCK_S:
            return _ignored(raw, f"timeout_s must be a number from 0.1 to {STUCK_S:g}")
        history = options.get("history_requests", 200)
        if isinstance(history, bool) or not isinstance(history, int) or not 10 <= history <= 1000:
            return _ignored(raw, "history_requests must be an integer from 10 to 1000")
        return ShadowConfig(timeout_s=float(timeout), history_requests=history)
    except Exception as exc:  # noqa: BLE001 - a setting must never break the gateway
        return _ignored(raw, type(exc).__name__)
