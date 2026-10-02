"""Subscription-quota error detection and bounded reset-time parsing."""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class QuotaWait:
    resume_at: float
    parsed: bool


_MONTHS = {name.casefold(): number for number, name in enumerate(calendar.month_abbr) if name}
_MONTHS.update({name.casefold(): number for number, name in enumerate(calendar.month_name) if name})
_WEEKDAYS = {name.casefold(): number for number, name in enumerate(calendar.day_abbr)}
_WEEKDAYS.update({name.casefold(): number for number, name in enumerate(calendar.day_name)})


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
            target = target.replace(year=year + 1)
        return target
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    weekday = match.group("weekday")
    if weekday:
        wanted = _WEEKDAYS[weekday.casefold()]
        target += timedelta(days=(wanted - target.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7 if weekday else 1)
    return target


def _relative_reset(text: str, now: datetime) -> datetime | None:
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
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def parse_quota_wait(engine: str, error: str | None, *, now: float | None = None,
                     default_wait_s: float = 3600) -> QuotaWait | None:
    """Return an account-quota reset, not an ordinary burst/server rate limit."""
    text = error or ""
    folded = text.casefold()
    name = str(engine or "").casefold()
    if "not your usage limit" in folded:
        return None
    if name in {"claude", "claude_code"}:
        detected = bool(re.search(r"(?:usage|session|weekly) limit", folded) and "reset" in folded)
    elif name == "codex":
        detected = "hit your usage limit" in folded
    elif name in {"agy", "antigravity"}:
        detected = (("exhausted your capacity" in folded or "quota exhausted" in folded
                     or "usage limit" in folded) and "reset" in folded)
    else:
        detected = ("hit your usage limit" in folded or
                    bool(re.search(r"(?:usage|session|weekly) limit", folded) and "reset" in folded) or
                    bool(("exhausted your capacity" in folded or "quota exhausted" in folded)
                         and "reset" in folded))
    if not detected:
        return None
    current = datetime.fromtimestamp(time.time() if now is None else now).astimezone()
    target = _relative_reset(text, current) or _absolute_reset(text, current)
    return QuotaWait((target.timestamp() if target else current.timestamp() + default_wait_s), target is not None)
