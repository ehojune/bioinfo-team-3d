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

    def __init__(self, scope: str, limit: int, used: int, request_id: str | None = None):
        self.scope, self.limit, self.used, self.request_id = scope, limit, used, request_id
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
                 clock: Callable[[], float] = time.time):
        self.path, self.per_request, self.per_day, self.clock = Path(path), per_request, per_day, clock
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
        finally:
            db.close()
        return {"day": self._day(now), "today": today, "per_day": self.per_day, "request": mine,
                "per_request": self.per_request, "requests_today": dict(requests)}

    def exhausted(self, request_id: str | None = None) -> LoginLimitReached | None:
        """The limit a new login would hit right now, else None. Read only: it counts nothing."""
        usage = self.usage(request_id if request_id is not None else current_request())
        if self.per_request is not None and usage["request"] is not None and usage["request"] >= self.per_request:
            return LoginLimitReached("request", self.per_request, usage["request"], request_id)
        if self.per_day is not None and usage["today"] >= self.per_day:
            return LoginLimitReached("day", self.per_day, usage["today"], request_id)
        return None


def budget_for(hpc) -> LoginBudget | None:
    """The counter for an ``HpcSettings`` that knows its ledger file (set from the runner state folder), else None."""
    ledger = getattr(hpc, "_login_ledger", None)
    if not ledger:
        return None
    return LoginBudget(ledger, hpc.login_limit_per_request, hpc.login_limit_per_day)
