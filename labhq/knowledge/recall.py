"""Bounded local recall. Only the gateway worker writes this disposable index."""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from contextlib import closing

from ..integrations.github import publish_clean
from ..integrations.rounds import metadata_failure_kind, step_status
from ..policy import mentions_zone, restricted_paths
from ..request_status import is_terminal_request
from ..test_guard import guard_test_path

BATCH_SIZE = 25
SCAN_LIMIT = 2000
log = logging.getLogger("labhq.knowledge")


def _path(settings):
    path = settings.path(settings.gateway.state_dir) / "knowledge.sqlite3"
    guard_test_path(path)
    return path


def _read(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)


def _enable_trigram(db):
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS recall_fts USING fts5(body, tokenize='trigram')")
        return True
    except sqlite3.OperationalError:
        return False


def health(settings):
    """Doctor is a read-only observer, including when the index is absent."""
    result = {"count": 0, "last_success": None, "fallback": None, "available": False}
    path = _path(settings)
    if not path.exists():
        return result
    try:
        with closing(_read(path)) as db:
            meta = dict(db.execute("SELECT key, value FROM metadata"))
            result.update(count=db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                          last_success=meta.get("last_success"), fallback=meta.get("trigram") != "1",
                          available=True)
    except (OSError, sqlite3.Error):
        pass
    return result


def _references(request):
    refs = request.get("references") or []
    if not isinstance(refs, list) or len(refs) > 100:
        raise ValueError("unknown references")
    safe = []
    for ref in refs:
        if (not isinstance(ref, dict) or ref.get("kind") not in {"path", "github", "url", "doi", "pmid"}
                or not isinstance(ref.get("value"), str) or not ref["value"] or len(ref["value"]) > 2000):
            raise ValueError("unknown reference")
        # No visibility lookup: every repo/URL pointer is conservatively private.
        # reference_original, notes and files are never consulted.
        safe.append({"kind": ref["kind"], "value": ref["value"], "source": "pi_profile"})
    return {"references": safe}


def clean(text, settings, request):
    if not isinstance(text, str) or len(text) > 64000:
        raise ValueError("unclassifiable text")
    refs = _references(request)
    if mentions_zone(text, restricted_paths(settings.policy)):
        return "<restricted-zone>"
    return publish_clean(text, settings, [refs])


def project(settings, rid, request):
    """Allowlist request metadata, not reports, result text, logs or manifests."""
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
        return None
    try:
        if not is_terminal_request(request.get("status")):
            return None
        if clean(rid, settings, request) != rid:
            return None
        summary = clean(request.get("text"), settings, request)
        results = request.get("results") or {}
        plan = request.get("plan") or {}
        if not isinstance(results, dict) or not isinstance(plan, dict):
            return None
        steps = plan.get("steps") or []
        if not isinstance(steps, list) or len(steps) + len(results) > 100:
            return None
        ids = [s["id"] for s in steps if isinstance(s, dict) and isinstance(s.get("id"), str)]
        ids += [sid for sid in results if sid not in ids]
        lines = []
        for sid in ids:
            result = results.get(sid) or {}
            if not isinstance(result, dict):
                return None
            status = step_status(result)
            kind = metadata_failure_kind(result) or "none"
            if not all(isinstance(v, str) and len(v) <= 100 for v in (sid, status, kind)):
                return None
            lines.append(clean(f"{sid}: {status}; failure_kind={kind}", settings, request))
        # Clean before clipping so a truncation never hides a reference boundary.
        body = summary[:1200] + ("\n" + "\n".join(lines) if lines else "")
        return {"request_id": rid, "status": request["status"], "body": body[:2000], "source": f"request:{rid}"}
    except (ValueError, TypeError, KeyError):
        return None


class KnowledgeRecall:
    def __init__(self, hub):
        self.hub = hub
        self.path = _path(hub.s)
        self.worker = None
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.initialized = False

    def health(self):
        return health(self.hub.s)

    def notify(self):
        # Startup owns the sole worker. Direct Hub users leave a durable pending marker.
        self.wake.set()

    def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self._run())

    async def stop(self):
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None

    async def _run(self):
        while True:
            self.wake.clear()
            await self.drain()
            try:
                await asyncio.wait_for(self.wake.wait(), 30)
            except asyncio.TimeoutError:
                pass

    def _checkpoint(self):
        if not self.path.exists():
            return "", False
        with closing(_read(self.path)) as db:
            meta = dict(db.execute("SELECT key, value FROM metadata"))
        return meta.get("checkpoint", ""), meta.get("backfilled") == "1"

    def _write(self, records, checkpoint, backfilled):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, request_id TEXT UNIQUE, body TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            trigram = _enable_trigram(db)
            prior = db.execute("SELECT value FROM metadata WHERE key='trigram'").fetchone()
            # A changed SQLite build replays the bounded backfill to populate trigram.
            if trigram and prior and prior[0] != "1":
                checkpoint, backfilled = "", False
            for rid, record in records:
                old = db.execute("SELECT id FROM documents WHERE request_id=?", (rid,)).fetchone()
                if old and trigram:
                    db.execute("DELETE FROM recall_fts WHERE rowid=?", old)
                if record is None:
                    db.execute("DELETE FROM documents WHERE request_id=?", (rid,))
                    continue
                body = record["body"]
                db.execute("INSERT INTO documents(request_id, body) VALUES (?, ?) "
                           "ON CONFLICT(request_id) DO UPDATE SET body=excluded.body", (rid, body))
                if trigram:
                    rowid = db.execute("SELECT id FROM documents WHERE request_id=?", (rid,)).fetchone()[0]
                    db.execute("INSERT INTO recall_fts(rowid, body) VALUES (?, ?)", (rowid, body))
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [
                ("checkpoint", checkpoint), ("backfilled", str(int(backfilled))),
                ("trigram", str(int(trigram))), ("last_success", str(time.time()))])

    async def batch(self):
        """One serialized bounded transaction. An error leaves both cursors retryable."""
        async with self.lock:
            try:
                checkpoint, done = self._checkpoint()
                pending = self.hub.store.page("knowledge_pending", limit=BATCH_SIZE)
                page = [] if done else self.hub.store.page("request", checkpoint, BATCH_SIZE)
                records = {rid: project(self.hub.s, rid, req) for rid, req in page}
                for rid, _ in pending:
                    records[rid] = project(self.hub.s, rid, self.hub.store.get("request", rid) or {})
                if done and not pending and self.initialized:
                    return False
                # The worker is the single writer; bounded work stays on this loop, so no
                # thread can outlive shutdown or race a terminal marker acknowledgement.
                self._write(list(records.items()), page[-1][0] if page else checkpoint,
                            done or len(page) < BATCH_SIZE)
                self.initialized = True
                for rid, _ in pending:
                    self.hub.store.delete("knowledge_pending", rid)
                return bool(pending) or len(page) == BATCH_SIZE or not self._checkpoint()[1]
            except Exception:  # optional index failure must never change request completion
                log.warning("local recall index unavailable; pending work retained")
                return False

    async def drain(self):
        while await self.batch():
            await asyncio.sleep(0)

    def search(self, query):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
            return []
        query = query.strip()
        try:
            if clean(query, self.hub.s, {}) != query or not self.path.exists():
                return []
            with closing(_read(self.path)) as db:
                meta = dict(db.execute("SELECT key, value FROM metadata"))
                # A VM budget also bounds pathological MATCH queries. All SQL values are parameters.
                ticks = 0

                def budget():
                    nonlocal ticks
                    ticks += 1
                    return int(ticks > 2000)

                db.set_progress_handler(budget, 1000)
                if len(query) >= 3 and meta.get("trigram") == "1":
                    literal = '"' + query.replace('"', '""') + '"'
                    rows = db.execute("SELECT d.request_id FROM recall_fts f JOIN documents d ON d.id=f.rowid "
                                      "WHERE recall_fts MATCH ? ORDER BY d.id DESC LIMIT 100", (literal,)).fetchall()
                else:
                    literal = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    rows = db.execute("SELECT request_id FROM (SELECT * FROM documents ORDER BY id DESC LIMIT ?) "
                                      "WHERE body LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 100",
                                      (SCAN_LIMIT, literal)).fetchall()
            found = []
            for (rid,) in rows:
                # Reapply current policy and references before matching, including changed settings.
                record = project(self.hub.s, rid, self.hub.store.get("request", rid) or {})
                if record is None or query.casefold() not in record["body"].casefold():
                    continue
                body = record["body"]
                start = max(0, body.casefold().find(query.casefold()) - 80)
                record["body"] = body[start:start + 400]
                found.append(record)
                if len(found) == 5:
                    break
            return found
        except Exception:
            log.warning("local recall search unavailable")
            return []
