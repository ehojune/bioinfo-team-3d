"""Small, versioned SQLite state stores for the gateway and runner."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from . import durable
from .test_guard import guard_test_path

T = TypeVar("T")


SCHEMA_VERSION = 1
RUNNER_INBOX_COMPLETED_LIMIT = 256

# Only conversation and report notifications, never tool output or debug traces.
MESSENGER_TYPES = (
    "agent.ask", "agent.answer", "request.created", "request.followup",
    "request.followup_done", "request.note", "request.completed", "request.failed",
    "github.posted", "github.failed",
    "request.questions", "approval.requested", "approval.resolved",
)


def messenger_event(body: dict) -> bool:
    return body.get("type") in MESSENGER_TYPES or (
        body.get("type") == "task.dispatched" and body.get("data", {}).get("kind")
        in {None, "step", "direct", "briefing", "review"}) or (
        body.get("type") == "agent.log" and bool(body.get("data", {}).get("text"))
        and body.get("data", {}).get("level") not in {"debug", "thinking"})


def _dumps(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, default=str)


class StateStore:
    """The gateway's and the runner's SQLite state.

    Two connections on one WAL database (#606). `db` serves every read, on whatever thread asks, and a read never
    waits for a commit: WAL readers do not block on the writer, which is what keeps the event loop free while a
    commit is stuck in fsync. All writes go through one write connection, one transaction at a time under
    `_write_lock`, and run through `labhq.durable` on this database's own writer thread (its `lane`). The
    `a`-prefixed methods are the awaitable ones for coroutines. The plain ones queue behind them and wait, and with
    no writer thread running they run on the calling thread as before. A write has finished, committed with the
    default `synchronous=FULL`, before its caller continues, so a read after it sees it. Reads made inside a write
    transaction use the write connection."""

    def __init__(self, path: Path):
        guard_test_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # This database's durable writer lane: its writes keep their order and do not wait for other targets.
        self.lane = f"sqlite:{os.path.normcase(os.path.abspath(path))}"
        self._write_lock = threading.RLock()
        self._wdb: sqlite3.Connection | None = None
        self._batch_conn: sqlite3.Connection | None = None  # set while `batch()` holds the open transaction
        self._lane_owner: threading.Thread | None = None  # the thread running `_batch_now` for this store
        self._closed = False
        self.db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.db.close()
            raise ValueError(f"unsupported state schema: {version}")
        if version == 0:
            self.db.execute("PRAGMA journal_mode=WAL")
            with self.db:
                self.db.executescript("""
                    CREATE TABLE state (kind TEXT NOT NULL, key TEXT NOT NULL,
                                        body TEXT NOT NULL, PRIMARY KEY(kind, key));
                    CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
                    CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
                    CREATE TABLE runner_cursor (runner_id TEXT PRIMARY KEY, seq INTEGER NOT NULL);
                """)
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        # Additive archive: replay retention remains unchanged. Older installations
        # can backfill only the events still retained at their first upgrade.
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='messenger_events'").fetchone():
            with self.db:
                self.db.execute("CREATE TABLE messenger_events (seq INTEGER PRIMARY KEY, request_id TEXT, body TEXT NOT NULL)")
                self.db.execute("CREATE INDEX messenger_request_seq ON messenger_events(request_id, seq)")
                for (raw,) in self.db.execute("SELECT body FROM events ORDER BY seq"):
                    self._archive_message(json.loads(raw))
        with self.db:
            self.db.execute("CREATE INDEX IF NOT EXISTS runner_inbox_status_idx "
                            "ON state(json_extract(body, '$.state')) WHERE kind='runner_inbox'")

    # ----- connections -----
    def _write_connection(self) -> sqlite3.Connection:
        # Opened on first write, on whichever thread writes first; `_write_lock` keeps one user at a time.
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        if self._wdb is None:
            self._wdb = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        return self._wdb

    @contextmanager
    def _transaction(self):
        """One write transaction on the write connection, committed (fsynced) on exit, rolled back on error. Inside
        `batch()` it is a savepoint of the batch's transaction instead: its own error undoes only its own rows, and
        the commit is the batch's."""
        with self._write_lock:
            conn = self._write_connection()
            if self._batch_conn is conn:
                conn.execute("SAVEPOINT labhq_write")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK TO labhq_write")
                    conn.execute("RELEASE labhq_write")
                    raise
                conn.execute("RELEASE labhq_write")
                return
            with conn:
                yield conn

    @contextmanager
    def batch(self):
        """Every write made inside is ONE transaction: one commit and one fsync when the block ends, all of it or none
        of it (#1125). The writes keep their order and each is still whole before the block returns, so a batch is as
        durable as the same writes made one by one, minus the fsyncs in between. Nothing is delayed: the commit is at
        the end of the block, before the caller continues. A batch inside a batch joins the outer one.

        Only inside this store's durable lane (a `durable.run` job, or `run_batch`): its writes then run inline, in
        order behind every earlier one. On any other thread the block would hold the write lock while its own `put()`
        queued behind a live writer thread that waits for that lock."""
        if not (durable.lane(self.lane).in_writer() or self._lane_owner is threading.current_thread()):
            raise RuntimeError("StateStore.batch() runs inside the store's durable lane: use run_batch()")
        with self._write_lock:
            if self._batch_conn is not None:
                yield
                return
            conn = self._write_connection()
            # An explicit BEGIN: the first SAVEPOINT of a write inside would otherwise start the transaction itself,
            # and its RELEASE would commit it.
            conn.execute("BEGIN IMMEDIATE")
            self._batch_conn = conn
            try:
                with conn:  # commits on exit, rolls back on error: the same exit a single write has
                    yield
            finally:
                self._batch_conn = None

    def run_batch(self, fn: Callable[[], T]) -> T:
        """`fn()` on this store's lane with its writes as one transaction (`batch()`): in order behind the writes
        already queued, and its result is returned once the commit is on disk."""
        return durable.call(self.lane, self._batch_now, fn)

    async def arun_batch(self, fn: Callable[[], T]) -> T:
        """`run_batch` for a coroutine: the loop keeps serving while the batch commits."""
        return await durable.run(self.lane, self._batch_now, fn)

    def _batch_now(self, fn: Callable[[], T]) -> T:
        previous, self._lane_owner = self._lane_owner, threading.current_thread()
        try:
            with self.batch():
                return fn()
        finally:
            self._lane_owner = previous

    def close(self) -> None:
        durable.lane(self.lane).drain()  # queued writes finish before their connection goes
        with self._write_lock:
            self._closed = True
            if self._wdb is not None:
                self._wdb.close()
                self._wdb = None
        self.db.close()

    # ----- state rows -----
    def put(self, kind: str, key: str, body: dict) -> None:
        durable.call(self.lane, self._put_now, kind, key, _dumps(body))

    async def aput(self, kind: str, key: str, body: dict) -> None:
        """`put` for a coroutine: the body is serialized now, the commit runs on the durable writer thread."""
        await durable.run(self.lane, self._put_now, kind, key, _dumps(body))

    async def acontrol_state(self, key: str, body: dict | None, *, reopen: bool = False) -> bool:
        """Commit a control row or its deletion marker in one transaction.

        Only a fresh PI control may reopen a closed row; delayed runner updates
        and replayed results cannot restore it after completion or halt expiry.
        """
        raw = _dumps(body) if body is not None else None
        return await durable.run(self.lane, self._control_state_now, key, raw, reopen)

    def _control_state_now(self, key: str, raw: str | None, reopen: bool) -> bool:
        with self._transaction() as conn:
            if raw is None:
                conn.execute("DELETE FROM state WHERE kind='task_control_state' AND key=?", (key,))
                conn.execute("INSERT OR REPLACE INTO state VALUES ('task_control_closed', ?, ?)",
                             (key, _dumps({"closed_at_ns": time.time_ns()})))
                return True
            if not reopen and self._get_on(conn, "task_control_closed", key) is not None:
                return False
            if reopen:
                conn.execute("DELETE FROM state WHERE kind='task_control_closed' AND key=?", (key,))
            conn.execute("INSERT OR REPLACE INTO state VALUES ('task_control_state', ?, ?)", (key, raw))
        return True

    async def amark_runner(self, runner_id: str, seq: int) -> None:
        await durable.run(self.lane, self._mark_runner_now, runner_id, seq)

    async def aupdate_task_activity(self, key: str, update: Callable[[dict], dict]) -> None:
        """Apply a pure activity reducer to the latest live task on its writer lane.

        The reducer runs on the writer thread and must own its captured inputs.
        Reading inside the transaction prevents a queued activity from restoring
        a stale task snapshot over completion, control, or another activity.
        """
        await durable.run(self.lane, self._update_task_activity_now, key, update)

    def _update_task_activity_now(self, key: str, update: Callable[[dict], dict]) -> None:
        with self._transaction() as conn:
            self._apply_activity(conn, key, update)

    def _apply_activity(self, conn: sqlite3.Connection, key: str, update: Callable[[dict], dict]) -> None:
        task = self._get_on(conn, "task", key)
        if not task or task.get("completed"):
            return
        previous = task.get("activity") or {}
        activity = update(dict(previous))
        if activity != previous:
            conn.execute("UPDATE state SET body=? WHERE kind='task' AND key=?",
                         (_dumps({**task, "activity": activity}), key))

    async def aupdate_task_control(self, key: str, action: str) -> dict:
        """Set only a halt/resume intent on the current eligible row, in writer order."""
        if action not in {"halt", "resume"}:
            raise ValueError(action)
        return await durable.run(self.lane, self._update_task_control_now, key, action)

    def _update_task_control_now(self, key: str, action: str) -> dict:
        with self._transaction() as conn:
            task = self._get_on(conn, "task", key)
            if not task:
                raise KeyError(key)
            if action == "halt":
                if task.get("completed") and not task.get("abandoned"):
                    raise ValueError("task already completed")
                field = "halt_requested"
            else:
                if ((task.get("result") or {}).get("error_kind") != "halted"
                        or task.get("halt_wait_finished")):
                    raise ValueError("task is no longer waiting after halt")
                field = "halt_resume_requested"
            updated = {**task, field: True}
            conn.execute("UPDATE state SET body=? WHERE kind='task' AND key=?", (_dumps(updated), key))
        return updated

    def _put_now(self, kind: str, key: str, raw: str) -> None:
        with self._transaction() as conn:
            # Display translations and sync recovery callers can queue a stale
            # card snapshot behind its answer. Never resurrect a decided card.
            if kind == "approval" and conn.execute(
                    "SELECT 1 FROM state WHERE kind='approval_decision' AND key=?", (key,)).fetchone():
                return
            if kind == "task_cancel":
                task = self._get_on(conn, "task", key) or {}
                if task.get("completed") and not task.get("abandoned"):
                    return  # A queued cancel cannot resurrect intent after the result's cleanup.
            conn.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)", (kind, key, raw))

    def delete(self, kind: str, key: str) -> None:
        durable.call(self.lane, self._delete_now, kind, key)

    def _delete_now(self, kind: str, key: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM state WHERE kind=? AND key=?", (kind, key))

    async def adelete(self, kind: str, key: str) -> None:
        await durable.run(self.lane, self._delete_now, kind, key)

    def commit_approval(self, aid: str, decision: dict, delivery: dict | None = None) -> None:
        """Replace a pending card with its answer and optional runner delivery atomically."""
        durable.call(self.lane, self._commit_approval_now, aid, _dumps(decision),
                     _dumps(delivery) if delivery is not None else None)

    async def acommit_approval(self, aid: str, decision: dict, delivery: dict | None = None) -> None:
        # Serialize before the first await: the writer never sees mutable caller state.
        raw = _dumps(decision)
        queued = _dumps(delivery) if delivery is not None else None
        await durable.run(self.lane, self._commit_approval_now, aid, raw, queued)

    def _commit_approval_now(self, aid: str, raw: str, queued: str | None) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM state WHERE kind='approval' AND key=?", (aid,))
            conn.execute("INSERT OR REPLACE INTO state VALUES ('approval_decision', ?, ?)", (aid, raw))
            conn.execute("DELETE FROM state WHERE kind='decision' AND key=?", (aid,))
            if queued is not None:
                conn.execute("INSERT INTO state VALUES ('decision', ?, ?)", (aid, queued))

    def all(self, kind: str) -> dict[str, dict]:
        return {key: json.loads(body) for key, body in
                self.db.execute("SELECT key, body FROM state WHERE kind=?", (kind,))}

    def get(self, kind: str, key: str) -> dict | None:
        return self._get_on(self.db, kind, key)

    @staticmethod
    def _get_on(conn: sqlite3.Connection, kind: str, key: str) -> dict | None:
        row = conn.execute("SELECT body FROM state WHERE kind=? AND key=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def page(self, kind: str, after: str = "", limit: int = 25) -> list[tuple[str, dict]]:
        """Bounded keyset scan; recall must not load the task/event ledger."""
        return [(key, json.loads(body)) for key, body in self.db.execute(
            "SELECT key, body FROM state WHERE kind=? AND key>? ORDER BY key LIMIT ?",
            (kind, after, max(1, min(limit, 100))))]

    # ----- events (the gateway's sequence) -----
    def append_event(self, body: dict, limit: int, runner_id: str | None = None,
                     runner_seq: int | None = None, reporter_delivery: bool = False,
                     request: tuple[str, dict] | None = None,
                     activity: tuple[str, Callable[[dict], dict]] | None = None, *, inbox_key: str | None = None) -> dict:
        return durable.call(self.lane, self._append_event_now, body, limit, runner_id, runner_seq, reporter_delivery,
                            request, activity, inbox_key)

    async def aappend_event(self, body: dict, limit: int, runner_id: str | None = None,
                            runner_seq: int | None = None, reporter_delivery: bool = False,
                            request: tuple[str, dict] | None = None,
                            activity: tuple[str, Callable[[dict], dict]] | None = None, *, inbox_key: str | None = None) -> dict:
        # Serialize before yielding: the writer must never inspect mutable gateway state.
        snapshot = json.loads(_dumps(body))
        request = None if request is None else (request[0], json.loads(_dumps(request[1])))
        return await durable.run(self.lane, self._append_event_now, snapshot, limit,
                                 runner_id, runner_seq, reporter_delivery, request, activity, inbox_key)

    def _append_event_now(self, body: dict, limit: int, runner_id: str | None, runner_seq: int | None,
                          reporter_delivery: bool, request: tuple[str, dict] | None = None,
                          activity: tuple[str, Callable[[dict], dict]] | None = None,
                          inbox_key: str | None = None) -> dict | None:
        """The event, the request snapshot and the task activity it belongs to, and the runner cursor commit together
        (#1129, #1132): all or none. The activity reducer runs here, on the writer thread, against the latest task row.

        None when the runner's seq is already at or below the stored cursor: that message was stored by another copy of
        itself that committed first (a resend racing its original), so nothing is added again, activity included
        (#1143)."""
        with self._transaction() as conn:
            ask_id = (body.get("data") or {}).get("id") if body.get("type") == "agent.ask" else None
            if ask_id and conn.execute("SELECT 1 FROM state WHERE kind='ask_publication' AND key=?",
                                       (str(ask_id),)).fetchone():
                return None
            if inbox_key is not None:
                row = conn.execute("SELECT body FROM state WHERE kind='runner_inbox' AND key=?", (inbox_key,)).fetchone()
                record = json.loads(row[0]) if row else None
                if not record or record["runner_id"] != runner_id or record["seq"] != runner_seq:
                    raise ValueError("runner inbox identity mismatch")
                if conn.execute("SELECT 1 FROM state WHERE kind='runner_inbox_event' AND key=?", (inbox_key,)).fetchone():
                    return None
            elif runner_id is not None and runner_seq is not None:
                seen = conn.execute("SELECT seq FROM runner_cursor WHERE runner_id=?", (runner_id,)).fetchone()
                if seen is not None and runner_seq <= seen[0]:
                    return None
            if request is not None:
                conn.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)", (request[0], _dumps(request[1])))
            if activity is not None:
                self._apply_activity(conn, activity[0], activity[1])
            body = self._insert_event(conn, body, limit)
            if inbox_key is not None:
                conn.execute("INSERT INTO state VALUES ('runner_inbox_event', ?, ?)",
                             (inbox_key, _dumps({"seq": body["seq"]})))
            if ask_id:
                conn.execute("INSERT INTO state VALUES ('ask_publication', ?, ?)",
                             (str(ask_id), _dumps({"seq": body["seq"], "event": body})))
            if reporter_delivery:
                conn.execute("INSERT OR REPLACE INTO state VALUES ('reporter_delivery', ?, ?)",
                             (str(body["seq"]), _dumps(body)))
            if runner_id is not None and runner_seq is not None:
                conn.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq "
                             "WHERE excluded.seq > runner_cursor.seq", (runner_id, runner_seq))
        return body

    def _insert_event(self, conn: sqlite3.Connection, body: dict, limit: int) -> dict:
        cursor = conn.execute("INSERT INTO events(body) VALUES (?)", ("{}",))
        body = {**body, "schema_version": SCHEMA_VERSION, "seq": cursor.lastrowid}
        conn.execute("UPDATE events SET body=? WHERE seq=?", (_dumps(body), cursor.lastrowid))
        self._archive_message(body, conn)
        conn.execute("DELETE FROM events WHERE seq <= (SELECT COALESCE(MAX(seq), 0) - ? FROM events)",
                     (max(1, limit),))
        return body

    def _archive_message(self, body: dict, conn: sqlite3.Connection | None = None) -> None:
        conn = conn or self.db
        if messenger_event(body):
            # Reports have their own request endpoint; this archive holds the notification.
            data = {k: v for k, v in body.get("data", {}).items()
                    if k in {"text", "level", "text_truncated", "text_chars", "question", "answer",
                             "reason", "status", "from", "to", "agent_id", "id", "ask_id", "ok",
                             "error", "answer_truncated", "answer_chars", "url", "kind",
                             "questions", "pi_summary", "summary", "approved", "note", "state", "choice",
                             "pi_display", "pi_explainer", "title", "prompt", "source_restricted"}}
            if body.get("type") == "request.followup_done" and not data.get("agent_id"):
                # Read on the connection that is writing: inside a transaction it sees the request row just saved.
                request = self._get_on(conn, "request", body.get("request_id")) or {}
                previous = next((f for f in request.get("followups", []) if f.get("id") == data.get("id")), {})
                data["agent_id"] = previous.get("agent_id")
            archived = {**body, "data": data}
            conn.execute("INSERT OR IGNORE INTO messenger_events VALUES (?, ?, ?)",
                         (body["seq"], body.get("request_id"), _dumps(archived)))

    def messenger_history(self, before: int | None = None, limit: int = 40,
                          request_id: str | None = None) -> dict:
        limit = max(1, min(limit, 200))
        sql, args = "SELECT body FROM messenger_events WHERE 1=1", []
        if before is not None:
            sql += " AND seq < ?"
            args.append(before)
        if request_id is not None:
            sql += " AND request_id = ?"
            args.append(request_id)
        rows = [json.loads(row[0]) for row in self.db.execute(
            sql + " ORDER BY seq DESC LIMIT ?", (*args, limit + 1))]
        more = len(rows) > limit
        events = list(reversed(rows[:limit]))
        return {"events": events, "has_more": more,
                "next_before": events[0]["seq"] if more else None}

    def set_message_display(self, seq: int, display: dict) -> None:
        durable.call(self.lane, self._set_message_display_now, seq, display)

    def _set_message_display_now(self, seq: int, display: dict) -> None:
        with self._transaction() as conn:
            for table in ("events", "messenger_events"):
                row = conn.execute(f"SELECT body FROM {table} WHERE seq=?", (seq,)).fetchone()
                if row:
                    event = json.loads(row[0])
                    event.setdefault("data", {})["pi_display"] = display
                    conn.execute(f"UPDATE {table} SET body=? WHERE seq=?",
                                 (json.dumps(event, ensure_ascii=False), seq))

    def commit_terminal(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        """Checkpoint terminal state, event and reporter delivery in one transaction."""
        return durable.call(self.lane, self._commit_terminal_now, rid, request, event, limit)

    def _commit_terminal_now(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        with self._transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)", (rid, _dumps(request)))
            event = self._insert_event(conn, event, limit)
            conn.execute("INSERT OR REPLACE INTO state VALUES ('knowledge_pending', ?, ?)",
                         (rid, json.dumps({"seq": event["seq"]})))
            conn.execute("INSERT OR REPLACE INTO state VALUES ('terminal_delivery', ?, ?)",
                         (str(event["seq"]), _dumps(event)))
        return event

    async def acommit_terminal(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        request, event = json.loads(_dumps(request)), json.loads(_dumps(event))
        return await durable.run(self.lane, self._commit_terminal_now, rid, request, event, limit)

    def commit_request_event(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        """Checkpoint a request update and its single event in one transaction."""
        return durable.call(self.lane, self._commit_request_event_now, rid, request, event, limit)

    def _commit_request_event_now(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        with self._transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)", (rid, _dumps(request)))
            return self._insert_event(conn, event, limit)

    async def acommit_request_event(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        request, event = json.loads(_dumps(request)), json.loads(_dumps(event))
        return await durable.run(self.lane, self._commit_request_event_now, rid, request, event, limit)

    def event_bounds(self) -> tuple[int, int]:
        row = self.db.execute("SELECT MIN(seq), MAX(seq) FROM events").fetchone()
        return row[0] or 0, row[1] or 0

    def events_since(self, since: int, limit: int | None = None) -> list[dict]:
        sql = "SELECT body FROM events WHERE seq>? ORDER BY seq"
        args: tuple = (since,)
        if limit is not None:
            sql += " LIMIT ?"
            args = (since, limit)
        return [json.loads(row[0]) for row in self.db.execute(sql, args)]

    # ----- the runner's outbox -----
    def enqueue(self, body: dict, limit: int) -> dict:
        return durable.call(self.lane, self._enqueue_now, body, limit)

    async def aenqueue(self, body: dict, limit: int) -> dict:
        """`enqueue` for a coroutine. The body is copied now (as the JSON that is stored), so what the caller does
        with it next cannot change the row."""
        return await durable.run(self.lane, self._enqueue_now, json.loads(_dumps(body)), limit)

    def _enqueue_now(self, body: dict, limit: int) -> dict:
        with self._transaction() as conn:
            cursor = conn.execute("INSERT INTO outbox(body) VALUES (?)", ("{}",))
            body = {**body, "runner_seq": cursor.lastrowid}
            conn.execute("UPDATE outbox SET body=? WHERE seq=?", (_dumps(body), cursor.lastrowid))
            excess = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] - max(1, limit)
            if excess > 0:
                for seq, raw in conn.execute("SELECT seq, body FROM outbox ORDER BY seq").fetchall():
                    if json.loads(raw).get("type") in {"agent.log", "agent.output", "agent.thinking"}:
                        conn.execute("DELETE FROM outbox WHERE seq=?", (seq,))
                        excess -= 1
                        if not excess:
                            break
        return body

    def pending(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM outbox ORDER BY seq")]

    def ack(self, seq: int) -> None:
        durable.call(self.lane, self._ack_now, seq)

    async def aack(self, seq: int) -> None:
        await durable.run(self.lane, self._ack_now, seq)

    def _ack_now(self, seq: int) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM outbox WHERE seq<=?", (seq,))

    @staticmethod
    def _runner_inbox_key(runner_id: str, incarnation: str | None, seq: int) -> str:
        if not runner_id or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("invalid runner inbox identity")
        return _dumps([runner_id, incarnation, seq])

    def runner_inbox(self, runner_id: str, incarnation: str | None, seq: int) -> dict | None:
        return self.get("runner_inbox", self._runner_inbox_key(runner_id, incarnation, seq))

    def active_runner_inbox(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT body FROM state INDEXED BY runner_inbox_status_idx WHERE kind='runner_inbox' "
            "AND json_extract(body, '$.state') IN ('pending','processing','failed','blocked')")]

    def _prune_completed_runner_inbox(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT key, body FROM state INDEXED BY runner_inbox_status_idx WHERE kind='runner_inbox' "
                            "AND json_extract(body, '$.state')='completed' ORDER BY rowid DESC LIMIT -1 OFFSET ?",
                            (RUNNER_INBOX_COMPLETED_LIMIT,)).fetchall()
        for key, raw in rows:
            inbox = json.loads(raw)
            generation = conn.execute("SELECT body FROM state WHERE kind='runner_incarnation' AND key=?",
                                      (inbox["runner_id"],)).fetchone()
            current = json.loads(generation[0])["id"] if generation else None
            cursor = conn.execute("SELECT seq FROM runner_cursor WHERE runner_id=?", (inbox["runner_id"],)).fetchone()
            if (generation is None or current == inbox["incarnation"]) and (not cursor or cursor[0] < inbox["seq"]):
                continue
            publications = []
            for name in ("message", "agent.ask", "agent.answer", "pipeline.ready"):
                pubkey = self._runner_publication_key(key, name)
                pub = conn.execute("SELECT body FROM state WHERE kind='runner_publication' AND key=?", (pubkey,)).fetchone()
                if pub:
                    publications.append((pubkey, json.loads(pub[0])))
            if any(p.get("state") != "published" or p.get("delivery_state") not in {"done", "shared"} or
                   p.get("interpreter_state") not in {"done", "shared"} for _, p in publications):
                continue
            if inbox["message"].get("type") == "ask.requested":
                ask_id = (inbox["message"].get("data") or {}).get("id")
                row = conn.execute("SELECT body FROM state WHERE kind='ask' AND key=?", (str(ask_id),)).fetchone()
                if not row:
                    continue
                ask = json.loads(row[0])
                if ask.get("state") != "resolved":
                    continue
                if not any(p.get("name") == "agent.answer" for _, p in publications):
                    continue
                ask["answer_publish_intent"] = False
                conn.execute("UPDATE state SET body=? WHERE kind='ask' AND key=?", (_dumps(ask), str(ask_id)))
                conn.execute("DELETE FROM state WHERE kind='ask_publication' AND key=?", (str(ask_id),))
            for pubkey, _ in publications:
                conn.execute("DELETE FROM state WHERE kind='runner_publication' AND key=?", (pubkey,))
            conn.execute("DELETE FROM state WHERE kind='runner_inbox_event' AND key=?", (key,))
            conn.execute("DELETE FROM state WHERE kind='runner_inbox' AND key=?", (key,))

    @staticmethod
    def _runner_publication_key(inbox_key: str, name: str) -> str:
        if not inbox_key or not name:
            raise ValueError("invalid runner publication identity")
        return _dumps([inbox_key, name])

    def runner_publication(self, inbox_key: str, name: str) -> dict | None:
        return self.get("runner_publication", self._runner_publication_key(inbox_key, name))

    async def amark_runner_publication_done(self, inbox_key: str, name: str, field: str) -> None:
        await durable.run(self.lane, self._mark_runner_publication_done_now, inbox_key, name, field)

    def _mark_runner_publication_done_now(self, inbox_key: str, name: str, field: str) -> None:
        if field not in {"delivery_state", "interpreter_state"}:
            raise ValueError("invalid publication effect")
        key = self._runner_publication_key(inbox_key, name)
        with self._transaction() as conn:
            row = conn.execute("SELECT body FROM state WHERE kind='runner_publication' AND key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            record = json.loads(row[0])
            if record.get("state") != "published" or record.get(field) not in {"pending", "done"}:
                raise ValueError("publication effect is not pending")
            record[field] = "done"
            conn.execute("UPDATE state SET body=? WHERE kind='runner_publication' AND key=?", (_dumps(record), key))
            self._prune_completed_runner_inbox(conn)

    def prepare_runner_publication(self, inbox_key: str, name: str, event: dict) -> dict:
        snapshot = json.loads(_dumps(event))
        return durable.call(self.lane, self._prepare_runner_publication_now, inbox_key, name, snapshot)

    async def aprepare_runner_publication(self, inbox_key: str, name: str, event: dict) -> dict:
        snapshot = json.loads(_dumps(event))
        return await durable.run(self.lane, self._prepare_runner_publication_now, inbox_key, name, snapshot)

    def _prepare_runner_publication_now(self, inbox_key: str, name: str, event: dict) -> dict:
        key = self._runner_publication_key(inbox_key, name)
        with self._transaction() as conn:
            if not conn.execute("SELECT 1 FROM state WHERE kind='runner_inbox' AND key=?", (inbox_key,)).fetchone():
                raise ValueError("runner inbox identity missing")
            row = conn.execute("SELECT body FROM state WHERE kind='runner_publication' AND key=?", (key,)).fetchone()
            if row:
                record = json.loads(row[0])
                if record["event"] != event:
                    raise ValueError("runner publication identity conflict")
                return record
            record = {"inbox_key": inbox_key, "name": name, "event": event, "state": "prepared"}
            conn.execute("INSERT INTO state VALUES ('runner_publication', ?, ?)", (key, _dumps(record)))
            return record

    def commit_runner_publication(self, inbox_key: str, name: str, limit: int, *,
                                  reporter_delivery: bool = False, request: tuple[str, dict] | None = None,
                                  activity: tuple[str, Callable[[dict], dict]] | None = None) -> dict:
        return durable.call(self.lane, self._commit_runner_publication_now, inbox_key, name, limit,
                            reporter_delivery, request, activity)

    async def acommit_runner_publication(self, inbox_key: str, name: str, limit: int, *,
                                         reporter_delivery: bool = False, request: tuple[str, dict] | None = None,
                                         activity: tuple[str, Callable[[dict], dict]] | None = None) -> dict:
        request = None if request is None else (request[0], json.loads(_dumps(request[1])))
        return await durable.run(self.lane, self._commit_runner_publication_now, inbox_key, name, limit,
                                 reporter_delivery, request, activity)

    def _commit_runner_publication_now(self, inbox_key: str, name: str, limit: int,
                                       reporter_delivery: bool, request: tuple[str, dict] | None,
                                       activity: tuple[str, Callable[[dict], dict]] | None) -> dict:
        key = self._runner_publication_key(inbox_key, name)
        with self._transaction() as conn:
            row = conn.execute("SELECT body FROM state WHERE kind='runner_publication' AND key=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            record = json.loads(row[0])
            if record["state"] == "published":
                return record["published_event"]
            if record["state"] != "prepared":
                raise ValueError("invalid runner publication state")
            ask_id = ((record["event"].get("data") or {}).get("id")
                      if record["event"].get("type") == "agent.ask" else None)
            prior = (conn.execute("SELECT body FROM state WHERE kind='ask_publication' AND key=?", (str(ask_id),)).fetchone()
                     if ask_id else None)
            if prior:
                previous = json.loads(prior[0]).get("event")
                if not previous or previous.get("data") != record["event"].get("data"):
                    raise ValueError("ask publication identity conflict")
                record.update(state="published", published_event=previous,
                              delivery_state="shared", interpreter_state="shared")
                conn.execute("UPDATE state SET body=? WHERE kind='runner_publication' AND key=?", (_dumps(record), key))
                return previous
            if request is not None:
                conn.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)", (request[0], _dumps(request[1])))
            if activity is not None:
                self._apply_activity(conn, activity[0], activity[1])
            event = self._insert_event(conn, record["event"], limit)
            if ask_id:
                conn.execute("INSERT INTO state VALUES ('ask_publication', ?, ?)",
                             (str(ask_id), _dumps({"seq": event["seq"], "event": event})))
            if reporter_delivery:
                conn.execute("INSERT OR REPLACE INTO state VALUES ('reporter_delivery', ?, ?)",
                             (str(event["seq"]), _dumps(event)))
            if name == "message":
                row = conn.execute("SELECT body FROM state WHERE kind='runner_inbox' AND key=?", (inbox_key,)).fetchone()
                if row is None:
                    raise ValueError("runner inbox identity missing")
                inbox = json.loads(row[0])
                conn.execute("INSERT INTO state VALUES ('runner_inbox_event', ?, ?)",
                             (inbox_key, _dumps({"seq": event["seq"]})))
                generation = conn.execute("SELECT body FROM state WHERE kind='runner_incarnation' AND key=?",
                                          (inbox["runner_id"],)).fetchone()
                if generation is None or json.loads(generation[0])["id"] == inbox["incarnation"]:
                    conn.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq "
                                 "WHERE excluded.seq > runner_cursor.seq", (inbox["runner_id"], inbox["seq"]))
            record.update(state="published", published_event=event,
                          delivery_state="pending", interpreter_state="pending")
            conn.execute("UPDATE state SET body=? WHERE kind='runner_publication' AND key=?", (_dumps(record), key))
            return event

    def runner_inbox_entries(self, runner_id: str, incarnation: str | None) -> list[dict]:
        """Inspect retained entries in delivery order, including uncertain executions."""
        return sorted((entry for entry in self.active_runner_inbox()
                       if entry["runner_id"] == runner_id and entry["incarnation"] == incarnation),
                      key=lambda entry: entry["seq"])

    def accept_runner_inbox(self, runner_id: str, incarnation: str | None, seq: int, message: dict) -> dict:
        snapshot = json.loads(_dumps(message))
        return durable.call(self.lane, self._accept_runner_inbox_now, runner_id, incarnation, seq, snapshot)

    async def aaccept_runner_inbox(self, runner_id: str, incarnation: str | None, seq: int, message: dict) -> dict:
        snapshot = json.loads(_dumps(message))
        return await durable.run(self.lane, self._accept_runner_inbox_now, runner_id, incarnation, seq, snapshot)

    def _accept_runner_inbox_now(self, runner_id: str, incarnation: str | None, seq: int, message: dict) -> dict:
        key = self._runner_inbox_key(runner_id, incarnation, seq)
        if message.get("runner_seq") != seq:
            raise ValueError("runner inbox sequence mismatch")
        with self._transaction() as conn:
            row = conn.execute("SELECT body FROM state WHERE kind='runner_inbox' AND key=?", (key,)).fetchone()
            if row:
                record = json.loads(row[0])
                if record["message"] != message:
                    raise ValueError("conflicting runner inbox delivery")
                return record
            seen = conn.execute("SELECT seq FROM runner_cursor WHERE runner_id=?", (runner_id,)).fetchone()
            # A previously processed delivery may be resent after upgrading from the cursor-only gateway.
            state = "completed" if seen is not None and seq <= seen[0] else "pending"
            record = {"runner_id": runner_id, "incarnation": incarnation, "seq": seq,
                      "message": message, "state": state, "error": None}
            cursor = conn.execute("INSERT INTO state VALUES ('runner_inbox', ?, ?)", (key, _dumps(record)))
            record["admission_order"] = cursor.lastrowid
            conn.execute("UPDATE state SET body=? WHERE kind='runner_inbox' AND key=?", (_dumps(record), key))
            if state == "completed":
                self._prune_completed_runner_inbox(conn)
            return record

    def set_runner_inbox_state(self, runner_id: str, incarnation: str | None, seq: int,
                               state: str, error: str | None = None, *, expected: str | None = None) -> dict:
        return durable.call(self.lane, self._set_runner_inbox_state_now, runner_id, incarnation, seq, state, error,
                            expected)

    async def aset_runner_inbox_state(self, runner_id: str, incarnation: str | None, seq: int,
                                      state: str, error: str | None = None, *, expected: str | None = None) -> dict:
        return await durable.run(self.lane, self._set_runner_inbox_state_now, runner_id, incarnation, seq,
                                 state, error, expected)

    async def aclaim_runner_publication_retry(self, runner_id: str, incarnation: str | None, seq: int) -> dict:
        return await durable.run(self.lane, self._claim_runner_publication_retry_now, runner_id, incarnation, seq)

    async def aclaim_blocked_runner_inbox(self, runner_id: str, incarnation: str | None, seq: int) -> dict:
        return await durable.run(self.lane, self._claim_blocked_runner_inbox_now, runner_id, incarnation, seq)

    def _claim_blocked_runner_inbox_now(self, runner_id: str, incarnation: str | None, seq: int) -> dict:
        key = self._runner_inbox_key(runner_id, incarnation, seq)
        with self._transaction() as conn:
            row = conn.execute("SELECT body FROM state WHERE kind='runner_inbox' AND key=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            record = json.loads(row[0])
            if record["state"] != "blocked":
                raise ValueError("runner inbox entry is not blocked")
            record.update(state="processing", error=None)
            conn.execute("UPDATE state SET body=? WHERE kind='runner_inbox' AND key=?", (_dumps(record), key))
            return record

    def _claim_runner_publication_retry_now(self, runner_id: str, incarnation: str | None, seq: int) -> dict:
        key = self._runner_inbox_key(runner_id, incarnation, seq)
        with self._transaction() as conn:
            row = conn.execute("SELECT body FROM state WHERE kind='runner_inbox' AND key=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            record = json.loads(row[0])
            typ = record["message"].get("type")
            safe = (typ in {"agent.log", "agent.status", "agent.tool", "agent.tool_result"} or
                    (typ == "ask.requested" and record["error"] == "ask_publication_incomplete"))
            if record["state"] != "failed" or not safe:
                raise ValueError("runner inbox outcome is not safe to retry")
            record.update(state="processing", error=None)
            conn.execute("UPDATE state SET body=? WHERE kind='runner_inbox' AND key=?", (_dumps(record), key))
            return record

    def _set_runner_inbox_state_now(self, runner_id: str, incarnation: str | None, seq: int,
                                    state: str, error: str | None, expected: str | None) -> dict:
        if state not in {"pending", "processing", "failed", "blocked", "completed"}:
            raise ValueError("invalid runner inbox state")
        key = self._runner_inbox_key(runner_id, incarnation, seq)
        with self._transaction() as conn:
            row = conn.execute("SELECT body FROM state WHERE kind='runner_inbox' AND key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            record = json.loads(row[0])
            if expected is not None and record["state"] != expected:
                raise ValueError("runner inbox state changed")
            if state == "processing" and (record["state"] != "pending" or expected != "pending"):
                raise ValueError("runner inbox processing requires a pending claim")
            transitions = {"pending": {"processing", "blocked", "failed"},
                           "blocked": {"pending"}, "processing": {"completed", "failed"},
                           "failed": set(), "completed": set()}
            if state != record["state"] and state not in transitions[record["state"]]:
                raise ValueError("unsafe runner inbox state transition")
            if record["state"] in {"blocked", "processing"} and state != record["state"] and expected is None:
                raise ValueError("runner inbox transition requires expected state")
            record.update(state=state, error=error)
            conn.execute("UPDATE state SET body=? WHERE kind='runner_inbox' AND key=?", (_dumps(record), key))
            if state == "completed":
                generation = conn.execute("SELECT body FROM state WHERE kind='runner_incarnation' AND key=?",
                                          (runner_id,)).fetchone()
                if generation is None or json.loads(generation[0])["id"] == incarnation:
                    conn.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq "
                                 "WHERE excluded.seq > runner_cursor.seq", (runner_id, seq))
                self._prune_completed_runner_inbox(conn)
            return record

    def runner_seen(self, runner_id: str) -> int:
        row = self.db.execute("SELECT seq FROM runner_cursor WHERE runner_id=?", (runner_id,)).fetchone()
        return row[0] if row else 0

    def runner_incarnation(self, runner_id: str, incarnation: str) -> None:
        """A replacement runner may reuse its name but starts a fresh outbox sequence."""
        durable.call(self.lane, self._runner_incarnation_now, runner_id, incarnation)

    def _runner_incarnation_now(self, runner_id: str, incarnation: str) -> None:
        with self._transaction() as conn:
            previous = conn.execute("SELECT body FROM state WHERE kind='runner_incarnation' AND key=?",
                                    (runner_id,)).fetchone()
            if previous is None or json.loads(previous[0])["id"] != incarnation:
                conn.execute("DELETE FROM runner_cursor WHERE runner_id=?", (runner_id,))
                conn.execute("INSERT OR REPLACE INTO state VALUES ('runner_incarnation', ?, ?)",
                             (runner_id, json.dumps({"id": incarnation})))

    def mark_runner(self, runner_id: str, seq: int) -> None:
        durable.call(self.lane, self._mark_runner_now, runner_id, seq)

    def _mark_runner_now(self, runner_id: str, seq: int) -> None:
        with self._transaction() as conn:  # the cursor only moves forward (#1143)
            conn.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq "
                         "WHERE excluded.seq > runner_cursor.seq", (runner_id, seq))
