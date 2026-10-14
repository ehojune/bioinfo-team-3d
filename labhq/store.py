"""Small, versioned SQLite state stores for the gateway and runner."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .test_guard import guard_test_path


SCHEMA_VERSION = 1

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


class StateStore:
    def __init__(self, path: Path):
        guard_test_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def close(self) -> None:
        self.db.close()

    def put(self, kind: str, key: str, body: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)",
                            (kind, key, json.dumps(body, ensure_ascii=False, default=str)))

    def delete(self, kind: str, key: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM state WHERE kind=? AND key=?", (kind, key))

    def all(self, kind: str) -> dict[str, dict]:
        return {key: json.loads(body) for key, body in
                self.db.execute("SELECT key, body FROM state WHERE kind=?", (kind,))}

    def get(self, kind: str, key: str) -> dict | None:
        row = self.db.execute("SELECT body FROM state WHERE kind=? AND key=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def page(self, kind: str, after: str = "", limit: int = 25) -> list[tuple[str, dict]]:
        """Bounded keyset scan; recall must not load the task/event ledger."""
        return [(key, json.loads(body)) for key, body in self.db.execute(
            "SELECT key, body FROM state WHERE kind=? AND key>? ORDER BY key LIMIT ?",
            (kind, after, max(1, min(limit, 100))))]

    def append_event(self, body: dict, limit: int, runner_id: str | None = None,
                     runner_seq: int | None = None, reporter_delivery: bool = False) -> dict:
        with self.db:
            body = self._insert_event(body, limit)
            if reporter_delivery:
                self.db.execute("INSERT OR REPLACE INTO state VALUES ('reporter_delivery', ?, ?)",
                                (str(body["seq"]), json.dumps(body, ensure_ascii=False, default=str)))
            if runner_id is not None and runner_seq is not None:
                self.db.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq",
                                (runner_id, runner_seq))
        return body

    def _insert_event(self, body: dict, limit: int) -> dict:
        cursor = self.db.execute("INSERT INTO events(body) VALUES (?)", ("{}",))
        body = {**body, "schema_version": SCHEMA_VERSION, "seq": cursor.lastrowid}
        self.db.execute("UPDATE events SET body=? WHERE seq=?",
                        (json.dumps(body, ensure_ascii=False, default=str), cursor.lastrowid))
        self._archive_message(body)
        self.db.execute("DELETE FROM events WHERE seq <= (SELECT COALESCE(MAX(seq), 0) - ? FROM events)",
                        (max(1, limit),))
        return body

    def _archive_message(self, body: dict) -> None:
        if messenger_event(body):
            # Reports have their own request endpoint; this archive holds the notification.
            data = {k: v for k, v in body.get("data", {}).items()
                    if k in {"text", "level", "text_truncated", "text_chars", "question", "answer",
                             "reason", "status", "from", "to", "agent_id", "id", "ask_id", "ok",
                             "error", "answer_truncated", "answer_chars", "url", "kind",
                             "questions", "pi_summary", "summary", "approved", "note", "state", "choice"}}
            if body.get("type") == "request.followup_done" and not data.get("agent_id"):
                request = self.get("request", body.get("request_id")) or {}
                previous = next((f for f in request.get("followups", []) if f.get("id") == data.get("id")), {})
                data["agent_id"] = previous.get("agent_id")
            archived = {**body, "data": data}
            self.db.execute("INSERT OR IGNORE INTO messenger_events VALUES (?, ?, ?)",
                            (body["seq"], body.get("request_id"), json.dumps(archived, ensure_ascii=False, default=str)))

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

    def commit_terminal(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        """Checkpoint terminal state, event and reporter delivery in one transaction."""
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)",
                            (rid, json.dumps(request, ensure_ascii=False, default=str)))
            event = self._insert_event(event, limit)
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('knowledge_pending', ?, ?)",
                            (rid, json.dumps({"seq": event["seq"]})))
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('terminal_delivery', ?, ?)",
                            (str(event["seq"]), json.dumps(event, ensure_ascii=False, default=str)))
        return event

    def commit_request_event(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        """Checkpoint a request update and its single event in one transaction."""
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)",
                            (rid, json.dumps(request, ensure_ascii=False, default=str)))
            return self._insert_event(event, limit)

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

    def enqueue(self, body: dict, limit: int) -> dict:
        with self.db:
            cursor = self.db.execute("INSERT INTO outbox(body) VALUES (?)", ("{}",))
            body = {**body, "runner_seq": cursor.lastrowid}
            self.db.execute("UPDATE outbox SET body=? WHERE seq=?",
                            (json.dumps(body, ensure_ascii=False, default=str), cursor.lastrowid))
            excess = self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] - max(1, limit)
            if excess > 0:
                for seq, raw in self.db.execute("SELECT seq, body FROM outbox ORDER BY seq"):
                    if json.loads(raw).get("type") in {"agent.log", "agent.output", "agent.thinking"}:
                        self.db.execute("DELETE FROM outbox WHERE seq=?", (seq,))
                        excess -= 1
                        if not excess:
                            break
        return body

    def pending(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM outbox ORDER BY seq")]

    def ack(self, seq: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE seq<=?", (seq,))

    def runner_seen(self, runner_id: str) -> int:
        row = self.db.execute("SELECT seq FROM runner_cursor WHERE runner_id=?", (runner_id,)).fetchone()
        return row[0] if row else 0

    def runner_incarnation(self, runner_id: str, incarnation: str) -> None:
        """A replacement runner may reuse its name but starts a fresh outbox sequence."""
        with self.db:
            previous = self.db.execute("SELECT body FROM state WHERE kind='runner_incarnation' AND key=?",
                                       (runner_id,)).fetchone()
            if previous is None or json.loads(previous[0])["id"] != incarnation:
                self.db.execute("DELETE FROM runner_cursor WHERE runner_id=?", (runner_id,))
                self.db.execute("INSERT OR REPLACE INTO state VALUES ('runner_incarnation', ?, ?)",
                                (runner_id, json.dumps({"id": incarnation})))

    def mark_runner(self, runner_id: str, seq: int) -> None:
        with self.db:
            self.db.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq",
                            (runner_id, seq))
