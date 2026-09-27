"""Small, versioned SQLite state stores for the gateway and runner."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA_VERSION = 1


class StateStore:
    def __init__(self, path: Path):
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

    def append_event(self, body: dict, limit: int, runner_id: str | None = None,
                     runner_seq: int | None = None) -> dict:
        with self.db:
            body = self._insert_event(body, limit)
            if runner_id is not None and runner_seq is not None:
                self.db.execute("INSERT INTO runner_cursor VALUES (?, ?) ON CONFLICT(runner_id) DO UPDATE SET seq=excluded.seq",
                                (runner_id, runner_seq))
        return body

    def _insert_event(self, body: dict, limit: int) -> dict:
        cursor = self.db.execute("INSERT INTO events(body) VALUES (?)", ("{}",))
        body = {**body, "schema_version": SCHEMA_VERSION, "seq": cursor.lastrowid}
        self.db.execute("UPDATE events SET body=? WHERE seq=?",
                        (json.dumps(body, ensure_ascii=False, default=str), cursor.lastrowid))
        self.db.execute("DELETE FROM events WHERE seq <= (SELECT COALESCE(MAX(seq), 0) - ? FROM events)",
                        (max(1, limit),))
        return body

    def commit_terminal(self, rid: str, request: dict, event: dict, limit: int) -> dict:
        """Checkpoint terminal state, event and reporter delivery in one transaction."""
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('request', ?, ?)",
                            (rid, json.dumps(request, ensure_ascii=False, default=str)))
            event = self._insert_event(event, limit)
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('terminal_delivery', ?, ?)",
                            (str(event["seq"]), json.dumps(event, ensure_ascii=False, default=str)))
        return event

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
