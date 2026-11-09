"""Count every HPC login and stop at the limits the user chose (#1295).

A scheduler command over ``hpc.ssh_host`` opens one ssh connection, and the site's IT team records each connection as a
login. The numbers are the user's: ``hpc.login_limit_per_request`` and ``hpc.login_limit_per_day`` are asked at ``labhq
init`` and kept in the local config; nothing here knows any institution's value, and empty means no limit.

One SQLite file under the runner state folder holds the rows, so the runner, the staff MCP processes and a restarted
runner share one count. Every login is counted, limit or not, so the usage is always visible. ``BEGIN IMMEDIATE``
makes the count-and-insert one step for concurrent callers. The count is the PC's own: a second runner with its own
state folder keeps its own.

A caller that knows the request (the runner, for its own submit, poll and cancel) opens ``request_scope``; a login with
no request counts against the day only, never against a request's limit.

A site rule like "at most N logins in any 60 minutes" is a sliding window: the site profile's ``login_limit_per_window``
and ``login_window_minutes`` (asked when the profile is written), checked against the same rows by login time.
"""
from __future__ import annotations

import contextlib
import contextvars
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterator

FILE = "hpc_logins.sqlite3"
_REQUEST: contextvars.ContextVar[str | None] = contextvars.ContextVar("labhq_hpc_login_request", default=None)


class LoginLimitReached(RuntimeError):
    """A new HPC login was refused: the user's request or day limit is used up. Nothing was sent."""

    def __init__(self, scope: str, limit: int, used: int, request_id: str | None = None, *,
                 window_minutes: int | None = None, retry_after_s: float | None = None):
        self.scope, self.limit, self.used, self.request_id = scope, limit, used, request_id
        self.retry_after_s = retry_after_s  # window only: seconds until the oldest login in the window drops out
        if scope == "window":
            wait = max(1, round((retry_after_s or 0) / 60))
            super().__init__(f"HPC 로그인 상한에 닿았습니다: 최근 {window_minutes}분 동안 로그인 {used}/{limit}회를 썼습니다. "
                             f"새 접속을 하지 않았습니다. 약 {wait}분 뒤에 다시 접속할 수 있습니다. "
                             "상한은 사이트 프로필의 login_limit_per_window입니다")
            return
        where = "이 요청의" if scope == "request" else "오늘의"
        super().__init__(f"HPC 로그인 상한에 닿았습니다: {where} 로그인 {used}/{limit}회를 썼습니다. "
                         "새 접속을 하지 않았습니다. 상한은 hpc.login_limit_per_request·hpc.login_limit_per_day입니다")


@contextlib.contextmanager
def request_scope(request_id: str | None) -> Iterator[None]:
    """The logins opened inside this block count toward ``request_id``'s limit as well as the day's."""
    token = _REQUEST.set(request_id or None)
    try:
        yield
    finally:
        _REQUEST.reset(token)


def current_request() -> str | None:
    return _REQUEST.get()


def parse_limit(text: object) -> int | None:
    """A limit typed by the user: a positive whole number, or empty for no limit. Anything else is an error."""
    value = str(text if text is not None else "").strip()
    if value == "" or value.lower() in {"none", "null", "no", "없음", "제한 없음"}:
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValueError("로그인 상한은 1 이상의 정수이거나, 제한이 없으면 비워 두세요")
    return int(value)


def ask_limits(hpc: dict, ask: Callable[[str, str], str]) -> dict:
    """Ask for the two limits and put the answers in ``hpc`` (the config's ``hpc:`` dict). Empty means no limit and is
    final: ``login_limits_decided`` records that the user was asked, so a blank is not asked about again."""
    for key, label in (("login_limit_per_request", "HPC 로그인 상한: 요청당 몇 번까지 (비우면 제한 없음)"),
                       ("login_limit_per_day", "HPC 로그인 상한: 하루에 몇 번까지 (비우면 제한 없음)")):
        while True:
            try:
                limit = parse_limit(ask(label, "" if hpc.get(key) is None else str(hpc.get(key))))
                break
            except ValueError as error:
                print(error)
        if limit is None:
            hpc.pop(key, None)
        else:
            hpc[key] = limit
    hpc["login_limits_decided"] = True
    return hpc


class LoginBudget:
    def __init__(self, path: str | Path, per_request: int | None = None, per_day: int | None = None, *,
                 per_window: int | None = None, window_minutes: int = 60, clock: Callable[[], float] = time.time):
        self.path, self.per_request, self.per_day, self.clock = Path(path), per_request, per_day, clock
        self.per_window, self.window_minutes = per_window, int(window_minutes or 60)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS logins (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, "
                       "request_id TEXT, at REAL NOT NULL, command TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS logins_day ON logins (day)")
            db.execute("CREATE INDEX IF NOT EXISTS logins_request ON logins (request_id)")
            db.execute("COMMIT")
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def _day(self, now: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(now))

    def _window(self, db: sqlite3.Connection, now: float) -> tuple[int, float | None]:
        """Logins in the window ending now, and when the oldest of them drops out (None: none in the window)."""
        since = now - self.window_minutes * 60
        count, oldest = db.execute("SELECT COUNT(*), MIN(at) FROM logins WHERE at > ? AND at <= ?", (since, now)).fetchone()
        return count, (None if oldest is None else oldest - since)

    def _window_stop(self, db: sqlite3.Connection, now: float, request_id: str | None) -> LoginLimitReached | None:
        if self.per_window is None:
            return None
        count, retry_after = self._window(db, now)
        if count < self.per_window:
            return None
        return LoginLimitReached("window", self.per_window, count, request_id, window_minutes=self.window_minutes,
                                 retry_after_s=retry_after)

    def admit(self, command: str, request_id: str | None = None) -> dict:
        """Count one login and allow it, or raise ``LoginLimitReached`` without counting. A login with no request is
        checked against the day's limit only."""
        request_id = request_id if request_id is not None else current_request()
        now = self.clock()
        day = self._day(now)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                today = db.execute("SELECT COUNT(*) FROM logins WHERE day = ?", (day,)).fetchone()[0]
                mine = (db.execute("SELECT COUNT(*) FROM logins WHERE request_id = ?", (request_id,)).fetchone()[0]
                        if request_id else None)
                if self.per_request is not None and mine is not None and mine >= self.per_request:
                    raise LoginLimitReached("request", self.per_request, mine, request_id)
                if self.per_day is not None and today >= self.per_day:
                    raise LoginLimitReached("day", self.per_day, today, request_id)
                stop = self._window_stop(db, now, request_id)
                if stop is not None:
                    raise stop
                db.execute("INSERT INTO logins (day, request_id, at, command) VALUES (?, ?, ?, ?)",
                           (day, request_id, now, str(command)[:40]))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        finally:
            db.close()
        return {"day": today + 1, "request": None if mine is None else mine + 1}

    def usage(self, request_id: str | None = None) -> dict:
        """What has been used, with the limits. Read only."""
        now = self.clock()
        db = self._connect()
        try:
            today = db.execute("SELECT COUNT(*) FROM logins WHERE day = ?", (self._day(now),)).fetchone()[0]
            mine = (db.execute("SELECT COUNT(*) FROM logins WHERE request_id = ?", (request_id,)).fetchone()[0]
                    if request_id else None)
            requests = db.execute("SELECT request_id, COUNT(*) FROM logins WHERE day = ? AND request_id IS NOT NULL "
                                  "GROUP BY request_id ORDER BY 2 DESC", (self._day(now),)).fetchall()
            window, _ = self._window(db, now)
            window_stop = self._window_stop(db, now, request_id)
        finally:
            db.close()
        return {"day": self._day(now), "today": today, "per_day": self.per_day, "request": mine,
                "per_request": self.per_request, "requests_today": dict(requests),
                "window": window, "per_window": self.per_window, "window_minutes": self.window_minutes,
                "window_retry_after_s": None if window_stop is None else window_stop.retry_after_s}

    def exhausted(self, request_id: str | None = None) -> LoginLimitReached | None:
        """The limit a new login would hit right now, else None. Read only: it counts nothing."""
        usage = self.usage(request_id if request_id is not None else current_request())
        if self.per_request is not None and usage["request"] is not None and usage["request"] >= self.per_request:
            return LoginLimitReached("request", self.per_request, usage["request"], request_id)
        if self.per_day is not None and usage["today"] >= self.per_day:
            return LoginLimitReached("day", self.per_day, usage["today"], request_id)
        if usage["window_retry_after_s"] is not None:
            return LoginLimitReached("window", self.per_window, usage["window"], request_id,
                                     window_minutes=self.window_minutes, retry_after_s=usage["window_retry_after_s"])
        return None


def window_limit(hpc) -> tuple[int | None, int]:
    """The site profile's window limit for an ``HpcSettings``: (logins, minutes). None when no profile is named or it
    sets none. A profile that cannot be read sets none here; the session path refuses such a profile on its own."""
    name = getattr(hpc, "site_profile", None)
    if not name:
        return None, 60
    from . import hpc_site

    try:
        profile = hpc_site.load_profile(name)
    except hpc_site.SiteProfileError:
        return None, 60
    return profile.login_limit_per_window, profile.login_window_minutes or 60


def budget_for(hpc) -> LoginBudget | None:
    """The counter for an ``HpcSettings`` that knows its ledger file (set from the runner state folder), else None."""
    ledger = getattr(hpc, "_login_ledger", None)
    if not ledger:
        return None
    per_window, minutes = window_limit(hpc)
    return LoginBudget(ledger, hpc.login_limit_per_request, hpc.login_limit_per_day, per_window=per_window,
                       window_minutes=minutes)
