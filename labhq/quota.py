"""Subscription-quota error detection and bounded reset-time parsing.

A CLI prints a reset clock time without a zone, in the zone of the machine it runs on. The runner therefore
resolves it to an instant (`quota_reset_instant`) and the gateway trusts that instant (`received_quota_wait`).
"""

from __future__ import annotations

import calendar
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo


@dataclass(frozen=True)
class QuotaWait:
    resume_at: float
    parsed: bool


_MONTHS = {name.casefold(): number for number, name in enumerate(calendar.month_abbr) if name}
_MONTHS.update({name.casefold(): number for number, name in enumerate(calendar.month_name) if name})
_WEEKDAYS = {name.casefold(): number for number, name in enumerate(calendar.day_abbr)}
_WEEKDAYS.update({name.casefold(): number for number, name in enumerate(calendar.day_name)})


def local_zone() -> tzinfo | None:
    """The zone this machine's CLIs print reset clock times in.

    None reads a clock time by the OS rules for that date, DST included. A fixed offset taken from the current
    moment would put a reset past a DST switch an hour off.
    """
    return None


def _clock_reset(text: str, stamp: float, zone: tzinfo | None) -> float | None:
    """The epoch of a clock time in `text`, read in `zone` (None: this machine's own rules)."""
    target = _absolute_reset(text, datetime.fromtimestamp(stamp, zone) if zone else datetime.fromtimestamp(stamp))
    return target.timestamp() if target else None  # a naive local wall clock converts by that date's rules


def _clock(hour: str, minute: str | None, meridiem: str | None) -> tuple[int, int]:
    value = int(hour)
    if meridiem:
        value %= 12
        if meridiem.casefold() == "pm":
            value += 12
    return value, int(minute or 0)


def _absolute_reset(text: str, now: datetime) -> datetime | None:
    match = re.search(
        r"(?:try again at|resets?(?: at)?)\s+"
        r"(?:(?P<weekday>mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|"
        r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\s+)?"
        r"(?:(?P<month>[a-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?\s+)?"
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?\b",
        text, re.IGNORECASE,
    )
    if not match:
        return None
    hour, minute = _clock(match.group("hour"), match.group("minute"), match.group("meridiem"))
    if hour > 23 or minute > 59:
        return None
    if match.group("month"):
        month = _MONTHS.get(match.group("month").casefold())
        if not month:
            return None
        year = int(match.group("year") or now.year)
        try:
            target = now.replace(year=year, month=month, day=int(match.group("day")),
                                 hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if not match.group("year") and target <= now:
            try:
                target = target.replace(year=year + 1)
            except ValueError:  # "Feb 29" after this leap day: no reading, so the bounded default wait applies
                return None
        return target
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    weekday = match.group("weekday")
    if weekday:
        wanted = _WEEKDAYS[weekday.casefold()]
        target += timedelta(days=(wanted - target.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7 if weekday else 1)
    return target


def _relative_seconds(text: str) -> float | None:
    """A duration ("reset after 2h 17m") is the same instant in every zone."""
    match = re.search(r"quota will reset after\s+([^\n]+)", text, re.IGNORECASE)
    if not match:
        return None
    fragment = match.group(1)
    units = {"d": 86400, "day": 86400, "days": 86400, "h": 3600, "hour": 3600,
             "hours": 3600, "m": 60, "min": 60, "mins": 60, "minute": 60,
             "minutes": 60, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}
    seconds = sum(float(value) * units[unit.casefold()] for value, unit in re.findall(
        r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|h|minutes?|mins?|m|seconds?|secs?|s)\b",
        fragment, re.IGNORECASE))
    return seconds if seconds > 0 else None


def is_quota_error(engine: str, error: str | None) -> bool:
    """An account-quota limit, not an ordinary burst/server rate limit."""
    folded = (error or "").casefold()
    name = str(engine or "").casefold()
    if "not your usage limit" in folded:
        return False
    if name in {"claude", "claude_code"}:
        return bool(re.search(r"(?:usage|session|weekly) limit", folded) and "reset" in folded)
    if name == "codex":
        return "hit your usage limit" in folded
    if name in {"agy", "antigravity"}:
        return (("exhausted your capacity" in folded or "quota exhausted" in folded
                 or "usage limit" in folded) and "reset" in folded)
    return ("hit your usage limit" in folded or
            bool(re.search(r"(?:usage|session|weekly) limit", folded) and "reset" in folded) or
            bool(("exhausted your capacity" in folded or "quota exhausted" in folded)
                 and "reset" in folded))


def parse_quota_wait(engine: str, error: str | None, *, now: float | None = None,
                     default_wait_s: float = 3600, tz: tzinfo | None = None) -> QuotaWait | None:
    """Return an account-quota reset, reading a clock time in `tz` (default: this machine's zone).

    Only the machine that ran the CLI knows that zone, so the gateway does not call this on a result's text.
    """
    if not is_quota_error(engine, error):
        return None
    text = error or ""
    stamp = time.time() if now is None else now
    seconds = _relative_seconds(text)
    if seconds:
        return QuotaWait(stamp + seconds, True)
    target = _clock_reset(text, stamp, tz or local_zone())
    return QuotaWait(stamp + default_wait_s if target is None else target, target is not None)


def quota_reset_instant(engine: str, error: str | None, *, now: float | None = None,
                        tz: tzinfo | None = None) -> float | None:
    """Runner side: the reset as epoch seconds, or None when it is not a quota error or has no readable reset."""
    wait = parse_quota_wait(engine, error, now=now, tz=tz)
    return wait.resume_at if wait and wait.parsed else None


def received_quota_wait(engine: str, error: str | None, reset_at: object, *, now: float | None = None,
                        default_wait_s: float = 3600) -> QuotaWait | None:
    """Gateway side: trust the runner's instant; without one, never wait longer than `default_wait_s`.

    A result from a runner that sends no instant still carries the CLI's text. A duration in it is kept, but a
    clock time is in the runner's zone, which the gateway cannot know, so the wait is capped and checked again.
    The caller's `quota_max_wait_s` deadline bounds every wait.
    """
    if reset_at is not None:
        try:
            instant = float(reset_at)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            instant = math.nan
        if math.isfinite(instant):
            return QuotaWait(instant, True)
    if not is_quota_error(engine, error):
        return None
    text = error or ""
    stamp = time.time() if now is None else now
    seconds = _relative_seconds(text)
    if seconds:
        return QuotaWait(stamp + seconds, True)
    cap = stamp + default_wait_s
    target = _clock_reset(text, stamp, local_zone())
    return QuotaWait(cap if target is None else min(target, cap), False)
