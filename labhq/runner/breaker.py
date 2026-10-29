"""Runaway detector for one task (#59 ②), in shadow mode: it reports a loop once and never stops the agent.

Codex and Antigravity have no max_turns or budget stop, so the only cap on a looping agent was the task timeout.
The signals come from Munder Difflin's field notes: the same tool with the same input many times in a row, or
tool errors in a row. Nothing here has side effects; the runner turns a trip into events.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..util import tool_input_fingerprint

REPEAT_LIMIT = 8  # the same tool call (name + input) back to back
ERROR_LIMIT = 5  # tool calls in a row that each failed


@dataclass
class BreakerPolicy:
    """Advance on new detector observations; enforcement is the caller's responsibility.

    A restriction stays latched until explicitly released. ``hard_stop`` governs enforcement of
    the stop stage, not whether that stage is reported.
    """

    hard_stop: bool = False
    stage: str = "normal"

    def observe(self, *, signalled: bool) -> str | None:
        if not signalled:
            return self.release() if self.stage == "warn" else None
        next_stage = {"normal": "warn", "warn": "constrain", "constrain": "stop"}.get(self.stage)
        if next_stage is None:
            return None
        self.stage = next_stage
        return next_stage

    def release(self) -> str | None:
        if self.stage == "normal":
            return None
        self.stage = "normal"
        return "normal"


@dataclass
class Escalation:
    """One task's stage ladder: a signal that stays tripped moves it up at most once per beat, only on a new observation."""

    policy: BreakerPolicy
    beat_s: float = 10.0
    clock: Callable[[], float] = time.monotonic
    _last: float | None = None
    episode: int = 0  # counts the restrictions this task entered: a release names the one it was meant for (#1008)
    run: str = field(default_factory=lambda: uuid.uuid4().hex[:8])  # one per task run, so a restart never reuses an id
    last_command: str | None = None  # the release command applied last, so a resend changes nothing

    @property
    def episode_id(self) -> str:
        """What a release command carries. A runner that restarted (or a task sent again) has a new ``run``, so a command
        saved for the earlier run can never match a restriction of the new one."""
        return f"{self.run}:{self.episode}"

    def observe(self, active: bool) -> str | None:
        if not active:
            return self.policy.observe(signalled=False)
        now = self.clock()
        if self._last is not None and now - self._last < self.beat_s:
            return None
        stage = self.policy.observe(signalled=True)
        if stage:
            self._last = now
            if stage == "constrain":
                self.episode += 1
        return stage

    def compacted(self) -> str | None:
        """A warning that rested on the old context is dropped; a restriction stays until the PI lifts it."""
        return self.release() if self.policy.stage == "warn" else None

    def release(self) -> str | None:
        self._last = None
        return self.policy.release()


def _call_key(data: dict[str, Any]) -> str | None:
    if "input_fingerprint" in data:
        return data["input_fingerprint"]
    value = data.get("input")
    # Old adapters only sent previews (some predate truncation metadata). Do not infer identity from a prefix.
    if data.get("input_truncated") or (isinstance(value, str) and value.endswith("…")):
        return None
    return tool_input_fingerprint(data.get("name"), value)


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
            # A call whose input the engine did not report cannot be told apart from the next, so it never counts
            # as a repeat (PR #414 review: Codex MCP calls without arguments).
            key = _call_key(data)
            self._repeats = self._repeats + 1 if key is not None and key == self._last_call else int(key is not None)
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

    def reset_window(self) -> None:
        """The agent's context was just compacted: it may redo what it did before, which is not a loop. Count again from
        here, so the next signal is a new observation (#1008)."""
        self._last_call, self._last_tool = None, ""
        self._repeats = self._errors = 0
        self._last_failed = False
        self._tripped.clear()

    @property
    def repeating(self) -> bool:
        return self._repeats >= self.repeat_limit

    @property
    def active(self) -> bool:
        """A signal is over its limit right now (``observe`` reports each signal's first crossing only)."""
        return self._repeats >= self.repeat_limit or self._errors >= self.error_limit

    def stage_record(self, stage: str, enforcement: str, *, hard_stop: bool = False,
                     episode: str | None = None) -> dict[str, Any]:
        """The ``agent.breaker`` body for a stage change. ``enforcement`` says what really happened: ``applied``,
        ``unsupported`` (the engine has no hook, so nothing was restricted), ``off`` (stop with hardStop off),
        ``halt`` or ``released``."""
        repeat = self._repeats >= self.repeat_limit
        return {"signal": "repeat" if repeat else "tool_errors", "count": self._repeats if repeat else self._errors,
                "tool": self._last_tool, "mode": "enforce", "stage": stage, "enforcement": enforcement,
                "hard_stop": hard_stop, **({"episode": episode} if episode is not None else {})}

    def _trip(self, signal: str, count: int) -> dict[str, Any] | None:
        if signal in self._tripped:
            return None
        self._tripped.add(signal)
        return {"signal": signal, "count": count, "tool": self._last_tool, "mode": "shadow"}


def stage_text(record: dict[str, Any]) -> str:
    tool = record.get("tool") or "도구"
    stage, how = record.get("stage"), record.get("enforcement")
    if stage == "normal":
        return "폭주 차단기를 풀었어요"
    if stage == "warn":
        return f"폭주 의심: {tool}에서 다시 감지했어요 (경고만, 멈추지 않음)"
    if stage == "constrain":
        if how == "unsupported":
            return f"폭주 의심 계속({tool}): 이 엔진은 도구를 제한할 수 없어 제한하지 못했어요 (표시만)"
        return f"폭주 의심 계속({tool}): 읽기 도구만 허용하도록 제한했어요. PI가 풀 때까지 유지됩니다"
    if how == "halt":
        return f"폭주 의심 계속({tool}): 세션을 보존한 채 멈췄어요. 재개할 수 있어요"
    return f"폭주 의심 계속({tool}): 멈출 단계지만 hardStop이 꺼져 있어 멈추지 않았어요 (표시만)"


def trip_text(trip: dict[str, Any]) -> str:
    tool = trip.get("tool") or "도구"
    if trip.get("signal") == "repeat":
        return f"폭주 의심: {tool}를 같은 입력으로 {trip.get('count')}번 연달아 불렀어요 (경고만, 멈추지 않음)"
    return f"폭주 의심: 도구 호출이 {trip.get('count')}번 연달아 실패했어요 (마지막 {tool}, 경고만, 멈추지 않음)"
