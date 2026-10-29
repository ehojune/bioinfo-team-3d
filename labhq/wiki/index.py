"""Incremental local index of a linked wiki folder (#827). Disposable: deleting it loses nothing the wiki holds.

Only documents whose (size, mtime, file id) changed are read again; a vanished document loses its rows. Every row
keeps what a citation needs: relative path, line range, content hash, modification time and the origin (DOI, PMID or
URL) the note names. Note text is data: nothing here executes or follows it.
"""
from __future__ import annotations

from contextlib import closing
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any

import yaml

from ..test_guard import guard_test_path
from .walker import list_files, root_path, root_problem

CHUNK_LINES = 60
SNIPPET_CHARS = 600
FRONTMATTER_LIMIT = 16 * 1024
DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"')\]]+")
PMID = re.compile(r"\bPMID:?\s*(\d{1,9})\b", re.IGNORECASE)
URL = re.compile(r"https?://[^\s<>\"')\]]+")
FM_KEYS = ("title", "status", "source", "sources", "doi", "pmid", "url", "date", "updated")
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, rel_path TEXT UNIQUE NOT NULL, size INTEGER, mtime_ns INTEGER,
  file_id TEXT, sha256 TEXT, title TEXT, status TEXT, frontmatter TEXT, lines INTEGER);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, ord INTEGER, heading TEXT,
  line_start INTEGER, line_end INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS origins(doc_id INTEGER NOT NULL, kind TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS origins_doc ON origins(doc_id);
"""


def index_path(settings: Any) -> Path:
    path = settings.path(settings.runner.state_dir) / "wiki" / "index.sqlite3"
    guard_test_path(path)
    return path


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.executescript(SCHEMA)
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='trigram')")
        db.execute("INSERT OR REPLACE INTO meta VALUES('trigram', '1')")
    except sqlite3.OperationalError:
        db.execute("INSERT OR REPLACE INTO meta VALUES('trigram', '0')")
    return db


def _has_fts(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_fts'").fetchone() is not None


def _read(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(limit + 1)
    finally:
        os.close(fd)


def _frontmatter(lines: list[str]) -> tuple[dict, int]:
    """(selected frontmatter keys, number of lines the block takes). A block that does not parse is plain text."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    size = 0
    for number, line in enumerate(lines[1:200], start=1):
        size += len(line)
        if size > FRONTMATTER_LIMIT:
            break
        if line.strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:number]))
            except yaml.YAMLError:
                return {}, 0
            if isinstance(data, dict):
                return {k: data[k] for k in FM_KEYS if k in data}, number + 1
            return {}, number + 1
    return {}, 0


def _origins(frontmatter: dict, text: str) -> list[tuple[str, str]]:
    found: dict[tuple[str, str], None] = {}

    def add(kind: str, value: str) -> None:
        value = value.strip().rstrip(".,;:")
        if value:
            found.setdefault((kind, value), None)

    declared: list[str] = []
    for key in ("source", "sources", "doi", "pmid", "url"):
        value = frontmatter.get(key)
        declared += [str(v) for v in (value if isinstance(value, list) else [value]) if v is not None]
    for item in declared:
        for match in DOI.finditer(item):
            add("doi", match.group(0).lower())
        for match in URL.finditer(item):
            if "doi.org/10." not in match.group(0):
                add("url", match.group(0))
        if re.fullmatch(r"\s*\d{1,9}\s*", item):
            add("pmid", item)
    for match in DOI.finditer(text):
        add("doi", match.group(0).lower())
    for match in PMID.finditer(text):
        add("pmid", match.group(1))
    for match in URL.finditer(text):
        if "doi.org/10." not in match.group(0):
            add("url", match.group(0))
    return list(found)[:50]


def _chunks(lines: list[str], start: int) -> list[tuple[str, int, int, str]]:
    """(heading path, first line, last line, text); a chunk ends at the next heading or after CHUNK_LINES lines."""
    out: list[tuple[str, int, int, str]] = []
    stack: list[str] = []
    fence = False
    current: list[str] = []
    first = start + 1

    def flush(last: int) -> None:
        body = "\n".join(current).strip()
        if body:
            out.append((" > ".join(stack), first, last, body))

    for index in range(start, len(lines)):
        line = lines[index]
        number = index + 1
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        heading = None if fence else re.match(r"(#{1,6})\s+(.*\S)\s*$", line)
        if heading or len(current) >= CHUNK_LINES:
            flush(number - 1)
            current, first = [], number
            if heading:
                level = len(heading.group(1))
                stack[:] = stack[:level - 1] + [heading.group(2)]
        current.append(line)
    flush(len(lines))
    return out


def _parse(raw: bytes, rel_path: str) -> dict:
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    frontmatter, taken = _frontmatter(lines)
    title = str(frontmatter.get("title") or "").strip()
    if not title:
        match = next((re.match(r"#\s+(.*\S)", ln) for ln in lines[taken:] if ln.startswith("# ")), None)
        title = match.group(1) if match else Path(rel_path).stem
    status = str(frontmatter.get("status") or "").strip().casefold()
    return dict(title=title[:300], status=status, lines=len(lines),
                frontmatter=json.dumps(frontmatter, ensure_ascii=False, default=str)[:4096],
                chunks=_chunks(lines, taken), origins=_origins(frontmatter, text))


def _delete_rows(db: sqlite3.Connection, doc_id: int) -> None:
    if _has_fts(db):
        db.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
    db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    db.execute("DELETE FROM origins WHERE doc_id=?", (doc_id,))


def reindex(settings: Any) -> dict:
    """Bring the index up to date with the folder; only changed documents are read. Returns counts."""
    files, skipped = list_files(settings)
    root = str(root_path(settings).resolve())
    stats: dict[str, Any] = dict(files=len(files), added=0, updated=0, touched=0, unchanged=0, removed=0,
                                 skipped=dict(skipped), unreadable=0)
    with closing(_connect(index_path(settings))) as db:
        stored_root = (db.execute("SELECT value FROM meta WHERE key='root'").fetchone() or [None])[0]
        if stored_root is not None and stored_root != root:
            for (doc_id,) in db.execute("SELECT id FROM docs").fetchall():
                _delete_rows(db, doc_id)
            db.execute("DELETE FROM docs")
        known = {row[1]: row for row in db.execute(
            "SELECT id, rel_path, size, mtime_ns, file_id, sha256 FROM docs")}
        seen = set()
        for item in files:
            row = known.get(item.rel_path)
            if row and (row[2], row[3], row[4]) == (item.size, item.mtime_ns, item.file_id):
                seen.add(item.rel_path)
                stats["unchanged"] += 1
                continue
            try:
                raw = _read(item.path, settings.wiki.max_file_bytes)
            except OSError:
                stats["unreadable"] += 1  # not marked seen: an earlier copy of a note that can no longer be read goes
                continue
            seen.add(item.rel_path)
            if len(raw) > settings.wiki.max_file_bytes:
                stats["skipped"]["too_large"] = stats["skipped"].get("too_large", 0) + 1
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if row and row[5] == digest:
                db.execute("UPDATE docs SET size=?, mtime_ns=?, file_id=? WHERE id=?",
                           (item.size, item.mtime_ns, item.file_id, row[0]))
                stats["touched"] += 1
                continue
            parsed = _parse(raw, item.rel_path)
            if row:
                doc_id = row[0]
                _delete_rows(db, doc_id)
                db.execute("UPDATE docs SET size=?, mtime_ns=?, file_id=?, sha256=?, title=?, status=?, "
                           "frontmatter=?, lines=? WHERE id=?",
                           (item.size, item.mtime_ns, item.file_id, digest, parsed["title"], parsed["status"],
                            parsed["frontmatter"], parsed["lines"], doc_id))
                stats["updated"] += 1
            else:
                doc_id = db.execute(
                    "INSERT INTO docs(rel_path,size,mtime_ns,file_id,sha256,title,status,frontmatter,lines) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (item.rel_path, item.size, item.mtime_ns, item.file_id, digest, parsed["title"],
                     parsed["status"], parsed["frontmatter"], parsed["lines"])).lastrowid
                stats["added"] += 1
            fts = _has_fts(db)
            for order, (heading, first, last, body) in enumerate(parsed["chunks"]):
                chunk_id = db.execute(
                    "INSERT INTO chunks(doc_id,ord,heading,line_start,line_end,text) VALUES(?,?,?,?,?,?)",
                    (doc_id, order, heading, first, last, body)).lastrowid
                if fts:
                    db.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (chunk_id, body))
            db.executemany("INSERT INTO origins VALUES(?,?,?)", [(doc_id, k, v) for k, v in parsed["origins"]])
        for rel, row in known.items():
            if rel not in seen:
                _delete_rows(db, row[0])
                db.execute("DELETE FROM docs WHERE id=?", (row[0],))
                stats["removed"] += 1
        db.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)",
                       [("root", root), ("last_reindex", str(time.time())),
                        ("skipped", json.dumps(stats["skipped"]))])
        db.commit()
    return stats


def _iso(mtime_ns: int) -> str:
    return dt.datetime.fromtimestamp(mtime_ns / 1e9, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _freshness(settings: Any, mtime_ns: int, status: str, origins: list) -> str:
    if status in {"disputed", "superseded"}:
        return "disputed"
    if not origins:
        return "source_missing"
    if (time.time() - mtime_ns / 1e9) / 86400 > settings.wiki.stale_days:
        return "stale"
    return "ok"


def search(settings: Any, query: str, limit: int = 10) -> list[dict]:
    """Ranked chunk hits after refreshing the index. Each hit carries the fields a citation needs."""
    query = query.strip()
    if not 2 <= len(query) <= 200:
        raise ValueError("wiki query must contain 2 to 200 characters")
    reindex(settings)
    limit = max(1, min(int(limit), 10))
    columns = ("c.id, c.heading, c.line_start, c.line_end, c.text, d.id, d.rel_path, d.sha256, d.mtime_ns, "
               "d.title, d.status")
    with closing(_connect(index_path(settings))) as db:
        trigram = (db.execute("SELECT value FROM meta WHERE key='trigram'").fetchone() or ["0"])[0] == "1"
        if trigram and len(query) >= 3:
            rows = db.execute(
                f"SELECT {columns} FROM chunks_fts f JOIN chunks c ON c.id=f.rowid JOIN docs d ON d.id=c.doc_id "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                ('"' + query.replace('"', '""') + '"', limit)).fetchall()
        else:
            like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = db.execute(
                f"SELECT {columns} FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE c.text LIKE ? ESCAPE '\\' "
                "ORDER BY d.rel_path, c.ord LIMIT ?", (like, limit)).fetchall()
        hits = []
        for _id, heading, first, last, text, doc_id, rel, digest, mtime_ns, title, status in rows:
            origins = db.execute("SELECT kind, value FROM origins WHERE doc_id=? ORDER BY rowid", (doc_id,)).fetchall()
            hits.append(dict(rel_path=rel, title=title, heading=heading, line_start=first, line_end=last,
                             sha256=digest, mtime=_iso(mtime_ns), snippet=text[:SNIPPET_CHARS],
                             origin={origins[0][0]: origins[0][1]} if origins else None,
                             access_level="wiki_summary", verified_against_primary=False,
                             freshness=_freshness(settings, mtime_ns, status, origins)))
    return hits


def status(settings: Any) -> dict:
    """Read-only view for `labhq wiki status` and doctor; never creates the index."""
    wiki = settings.wiki
    result: dict[str, Any] = dict(enabled=wiki.enabled, root=wiki.root, available=False, docs=0, chunks=0,
                                  origins=0, stale=0, last_reindex=None, skipped={}, root_problem=None)
    if wiki.root:
        try:
            result["root_problem"] = root_problem(settings)
        except (OSError, ValueError) as error:
            result["root_problem"] = str(error)
    path = index_path(settings)
    if not path.exists():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            meta = dict(db.execute("SELECT key, value FROM meta"))
            cutoff = (time.time() - wiki.stale_days * 86400) * 1e9
            try:
                current = str(root_path(settings).resolve())
            except (OSError, ValueError):
                current = None
            if meta.get("root") != current:
                result["stale_root"] = True  # the index belongs to another folder: not usable until `reindex`
                return result
            result.update(available=True, docs=db.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
                          chunks=db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                          origins=db.execute("SELECT COUNT(*) FROM origins").fetchone()[0],
                          stale=db.execute("SELECT COUNT(*) FROM docs WHERE mtime_ns < ?", (cutoff,)).fetchone()[0],
                          last_reindex=float(meta["last_reindex"]) if meta.get("last_reindex") else None,
                          skipped=json.loads(meta.get("skipped") or "{}"))
    except (OSError, sqlite3.Error, ValueError):
        pass
    return result


def disconnect(settings: Any) -> bool:
    """Delete this instance's index. The wiki folder is never touched."""
    path = index_path(settings)
    removed = False
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = path.with_name(path.name + suffix)
        try:
            candidate.unlink()
            removed = True
        except FileNotFoundError:
            pass
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return removed
