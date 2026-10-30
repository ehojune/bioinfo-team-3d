"""Request-wide admission for public lookups through labhq_annot (#858, small trial B of #739).

Opt-in with ``annot.request_lookup_limit``. One counted attempt is one outbound HTTP request that a labhq_annot tool
sends to a public service. Each one is admitted here immediately before it leaves, from the httpx ``request`` hook
of the tool's client. httpx runs that hook before every send, including each redirect hop it follows, and
annot's own retries, pagination, per-item fan-out and OA redirect hops each call the client again. So every failed,
retried or redirected request is one attempt, and a refused one is not sent and not counted.

The runner's broker calls this with the request id bound to the task token it issued, never one a caller names.
Rows live in one SQLite file under the runner state folder, so every agent and step of the request and a restarted
runner share the count. ``BEGIN IMMEDIATE`` makes the count-and-insert one step for concurrent callers, including
other processes on the same file. A second runner with its own state folder keeps its own count.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

FILE = "lookup_admission.sqlite3"
ENV = "LABHQ_LOOKUP_ADMISSION"  # set to "1" by the runner for every task while the limit is on
OUTCOMES = ("ok", "failed", "timeout")
_TOOL = re.compile(r"[a-z][a-z0-9_]{0,39}")
# Only the host is kept: query strings can carry identifiers or the Unpaywall contact email.
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


def admission_for(settings) -> "LookupAdmission | None":
    """The request-wide counter when annot.request_lookup_limit is set, else None (no counting, no file).

    The gateway's source check (#891) and `labhq verify` open the same SQLite file the runner counts in, so they share
    one count per request on a PC whose config they share. A runner on another PC keeps its own count, as above."""
    limit = settings.annot.request_lookup_limit
    if limit is None:
        return None
    return LookupAdmission(settings.path(settings.runner.state_dir) / FILE, limit)


def usage_note_for(settings, request_id: object, *, now=None) -> str:
    """The prompt paragraph for a request, or "" when no cap is configured (nothing is counted, so there is nothing to
    plan around). A configured cap whose counter cannot be opened or read is UNKNOWN, never zero."""
    if settings.annot.request_lookup_limit is None:
        return ""
    try:
        admission = admission_for(settings)
    except (sqlite3.Error, OSError):
        return usage_note(None, request_id, now=now, why="the lookup counter could not be opened")
    return usage_note(admission, request_id, now=now)


def usage_note(admission: "LookupAdmission | None", request_id: object, *, now=None, why: str | None = None) -> str:
    """One prompt paragraph with this request's public lookup usage as a fact, or an explicit "unknown" (#1088).

    A counter that is off, a request id that is missing or a file that cannot be read is unknown, never zero: a
    reader must not plan as if nothing had been spent. The count is this PC's runner count; a runner on another PC
    keeps its own, and the request text may state a different number, which labhq does not enforce."""
    unknown = ("Public lookup usage of this request: UNKNOWN ({why}). Do not assume it is zero; "
               "if the number matters, say that it could not be read.")
    if admission is None:
        return unknown.format(why=why or "annot.request_lookup_limit is not set, so labhq does not count")
    try:
        usage = admission.usage(request_id)
    except (sqlite3.Error, OSError):
        return unknown.format(why="the lookup counter could not be read")
    if usage is None:
        return unknown.format(why="the request has no identity")
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now if now is not None else time.time()))
    who = ", ".join(f"{agent} {count}" for agent, count in usage["by_agent"].items()) or "none yet"
    return (f"Public lookup usage of this request (labhq_annot and source-check HTTP requests; every retry and "
            f"redirect counts): {usage['used']} of {usage['limit']} used, {usage['remaining']} left at {stamp} "
            f"(by agent: {who}). This is the count of this PC's runner; a number written in the request text is a "
            f"separate statement that labhq does not enforce.")


class LookupAdmission:
    def __init__(self, path: str | Path, limit: int, *, clock=time.time):
        self.path, self.limit, self.clock = Path(path), limit, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS sends (request_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                       "task_id TEXT, agent_id TEXT, tool TEXT NOT NULL, host TEXT NOT NULL, "
                       "admitted_at REAL NOT NULL, http_status INTEGER, outcome TEXT, "
                       "PRIMARY KEY (request_id, seq))")
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def admit(self, request_id: object, task_id: object, agent_id: object, tool: object, host: object) -> dict:
        """Count one outbound request and allow it to be sent, or refuse it without counting when none is left."""
        if not isinstance(request_id, str) or not request_id:
            return {"admitted": False, "limit": self.limit, "reason": "no trusted request identity"}
        if not isinstance(tool, str) or not _TOOL.fullmatch(tool):
            return {"admitted": False, "limit": self.limit, "reason": "unknown lookup tool"}
        if not isinstance(host, str) or not _HOST.fullmatch(host):
            return {"admitted": False, "limit": self.limit, "reason": "invalid request host"}
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                used = db.execute("SELECT COUNT(*) FROM sends WHERE request_id = ?", (request_id,)).fetchone()[0]
                if used >= self.limit:
                    db.execute("ROLLBACK")
                    return {"admitted": False, "used": used, "limit": self.limit, "reason": "exhausted"}
                db.execute("INSERT INTO sends (request_id, seq, task_id, agent_id, tool, host, admitted_at) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (request_id, used + 1, str(task_id or ""), str(agent_id or ""), tool, host, self.clock()))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        finally:
            db.close()
        return {"admitted": True, "seq": used + 1, "used": used + 1, "limit": self.limit}

    def finish(self, request_id: object, task_id: object, sends: object, outcome: object) -> int:
        """Record each admitted send's HTTP status and the tool call's outcome; the sends stay counted regardless."""
        if not isinstance(request_id, str) or outcome not in OUTCOMES or not isinstance(sends, list):
            return 0
        rows = []
        for item in sends:
            if not (isinstance(item, list) and len(item) == 2 and type(item[0]) is int
                    and (item[1] is None or (type(item[1]) is int and 100 <= item[1] <= 599))):
                return 0
            rows.append((item[1], outcome, request_id, item[0], str(task_id or "")))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = sum(db.execute("UPDATE sends SET http_status = ?, outcome = ? WHERE request_id = ? "
                                         "AND seq = ? AND task_id = ? AND outcome IS NULL", row).rowcount
                              for row in rows)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        finally:
            db.close()
        return changed

    def usage(self, request_id: object) -> dict | None:
        """What this request has used: ``used``, ``limit``, ``remaining`` and the counts per agent and per tool.

        Read only. None for a request id that is not a non-empty string, so a caller never reads "no identity" as zero."""
        if not isinstance(request_id, str) or not request_id:
            return None
        db = self._connect()
        try:
            rows = db.execute("SELECT agent_id, tool, COUNT(*) FROM sends WHERE request_id = ? "
                              "GROUP BY agent_id, tool", (request_id,)).fetchall()
        finally:
            db.close()
        by_agent: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        for agent, tool, count in rows:
            by_agent[agent or "?"] = by_agent.get(agent or "?", 0) + count
            by_tool[tool] = by_tool.get(tool, 0) + count
        used = sum(by_tool.values())
        return {"used": used, "limit": self.limit, "remaining": max(0, self.limit - used),
                "by_agent": dict(sorted(by_agent.items())), "by_tool": dict(sorted(by_tool.items()))}

    def sends(self, request_id: str) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("SELECT seq, task_id, agent_id, tool, host, admitted_at, http_status, outcome "
                              "FROM sends WHERE request_id = ? ORDER BY seq", (request_id,)).fetchall()
        finally:
            db.close()
        keys = ("seq", "task_id", "agent_id", "tool", "host", "admitted_at", "http_status", "outcome")
        return [dict(zip(keys, row)) for row in rows]
