"""Request-wide admission for public lookups through labhq_annot (#858, small trial B of #739).

Opt-in with ``annot.request_lookup_limit``. One counted attempt is one labhq_annot tool invocation: it is admitted
here before the tool sends anything, and it stays counted whatever happens next (an answer, an item error, a
timeout, a cached answer, or the agent calling again). Network retries, pagination and redirects inside one
admitted invocation are not further attempts; their number is recorded on the same row when the tool finishes.

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


class LookupAdmission:
    def __init__(self, path: str | Path, limit: int, *, clock=time.time):
        self.path, self.limit, self.clock = Path(path), limit, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (request_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                       "task_id TEXT, agent_id TEXT, tool TEXT NOT NULL, admitted_at REAL NOT NULL, "
                       "outbound_requests INTEGER, outcome TEXT, PRIMARY KEY (request_id, seq))")
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def admit(self, request_id: object, task_id: object, agent_id: object, tool: object) -> dict:
        """Count one attempt and allow it, or refuse it without counting when none is left."""
        if not isinstance(request_id, str) or not request_id:
            return {"admitted": False, "limit": self.limit, "reason": "no trusted request identity"}
        if not isinstance(tool, str) or not _TOOL.fullmatch(tool):
            return {"admitted": False, "limit": self.limit, "reason": "unknown lookup tool"}
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                used = db.execute("SELECT COUNT(*) FROM attempts WHERE request_id = ?", (request_id,)).fetchone()[0]
                if used >= self.limit:
                    db.execute("ROLLBACK")
                    return {"admitted": False, "used": used, "limit": self.limit, "reason": "exhausted"}
                db.execute("INSERT INTO attempts (request_id, seq, task_id, agent_id, tool, admitted_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (request_id, used + 1, str(task_id or ""), str(agent_id or ""), tool, self.clock()))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        finally:
            db.close()
        return {"admitted": True, "seq": used + 1, "used": used + 1, "limit": self.limit}

    def finish(self, request_id: object, task_id: object, seq: object, outbound: object, outcome: object) -> bool:
        """Record what an admitted attempt sent; the attempt stays counted whether or not this is recorded."""
        if (not isinstance(request_id, str) or type(seq) is not int or type(outbound) is not int or outbound < 0
                or outcome not in OUTCOMES):
            return False
        db = self._connect()
        try:
            changed = db.execute("UPDATE attempts SET outbound_requests = ?, outcome = ? WHERE request_id = ? "
                                 "AND seq = ? AND task_id = ? AND outcome IS NULL",
                                 (outbound, outcome, request_id, seq, str(task_id or ""))).rowcount
        finally:
            db.close()
        return changed == 1

    def attempts(self, request_id: str) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("SELECT seq, task_id, agent_id, tool, admitted_at, outbound_requests, outcome "
                              "FROM attempts WHERE request_id = ? ORDER BY seq", (request_id,)).fetchall()
        finally:
            db.close()
        keys = ("seq", "task_id", "agent_id", "tool", "admitted_at", "outbound_requests", "outcome")
        return [dict(zip(keys, row)) for row in rows]
