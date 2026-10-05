"""Runaway detector for one task (#59 ②), in shadow mode: it reports a loop once and never stops the agent.

Codex and Antigravity have no max_turns or budget stop, so the only cap on a looping agent was the task timeout.
The signals come from Munder Difflin's field notes: the same tool with the same input many times in a row, or
tool errors in a row. Nothing here has side effects; the runner turns a trip into events.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

REPEAT_LIMIT = 8  # the same tool call (name + input) back to back
ERROR_LIMIT = 5  # tool calls in a row that each failed
INPUT_CHARS = 4096


def _call_key(data: dict[str, Any]) -> str:
    text = f"{data.get('name') or ''}\0{str(data.get('input') or '')[:INPUT_CHARS]}"
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


@dataclass
class Breaker:
    repeat_limit: int = REPEAT_LIMIT
    error_limit: int = ERROR_LIMIT
    _last_call: str | None = None
    _last_tool: str = ""
    _repeats: int = 0
    _errors: int = 0
    _last_failed: bool = False
    _tripped: set[str] = field(default_factory=set)

    def observe(self, kind: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Feed one agent event; return a trip record the first time a signal crosses its limit."""
        if kind == "agent.tool":
            key = _call_key(data)
            self._repeats = self._repeats + 1 if key == self._last_call else 1
            self._last_call, self._last_tool = key, str(data.get("name") or "")
            # Engines report failures but not successes: a tool call that follows one without an error ends the
            # error streak.
            if not self._last_failed:
                self._errors = 0
            self._last_failed = False
            if self._repeats >= self.repeat_limit:
                return self._trip("repeat", self._repeats)
        elif kind == "agent.tool_error":
            if not self._last_failed:
                self._errors += 1
            self._last_failed = True
            if self._errors >= self.error_limit:
                return self._trip("tool_errors", self._errors)
        return None

    def _trip(self, signal: str, count: int) -> dict[str, Any] | None:
        if signal in self._tripped:
            return None
        self._tripped.add(signal)
        return {"signal": signal, "count": count, "tool": self._last_tool, "mode": "shadow"}


def trip_text(trip: dict[str, Any]) -> str:
    tool = trip.get("tool") or "도구"
    if trip.get("signal") == "repeat":
        return f"폭주 의심: {tool}를 같은 입력으로 {trip.get('count')}번 연달아 불렀어요 (경고만, 멈추지 않음)"
    return f"폭주 의심: 도구 호출이 {trip.get('count')}번 연달아 실패했어요 (마지막 {tool}, 경고만, 멈추지 않음)"
