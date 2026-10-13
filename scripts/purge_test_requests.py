"""Offline, exact-match cleanup. Dry-run is read-only; never starts/stops a gateway.

Example: python scripts/purge_test_requests.py --database /tmp/state/gateway.sqlite3
Apply additionally requires --apply --gateway-stopped --config CONFIG --backup NEW_FILE.
The operator must keep all gateways using this database stopped for the entire operation.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def connect_readonly(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def candidates(db, texts, agent_id="worker", mode="direct"):
    return [key for key, raw in db.execute("SELECT key, body FROM state WHERE kind='request'")
            if (body := json.loads(raw)).get("text") in texts
            and body.get("agent_id") == agent_id and body.get("mode") == mode]


def assert_gateway_stopped(settings):
    from labhq.cli import _process_created

    pid_file = settings.path(settings.gateway.state_dir) / "labhq-gateway.pid"
    if pid_file.exists():
        record = json.loads(pid_file.read_text(encoding="utf-8"))
        # A malformed/unknown record is not evidence that the gateway stopped.
        if not isinstance(record, dict) or not isinstance(record.get("pid"), int) or record["pid"] <= 0:
            raise RuntimeError("cannot establish gateway process state")
        if _process_created(record["pid"]) is not None:
            raise RuntimeError("gateway PID is still alive")
    endpoint = urlsplit(settings.gateway.url)
    host, port = endpoint.hostname, endpoint.port
    if not host or not port:
        raise RuntimeError("config must name a gateway host and port")
    try:
        connection = socket.create_connection((host, port), timeout=5)
    except ConnectionRefusedError:
        return
    except OSError as exc:
        raise RuntimeError("cannot establish gateway endpoint state") from exc
    else:
        connection.close()
        raise RuntimeError("gateway endpoint is still listening")


def purge(path, backup, texts, agent_id, mode):
    # mode=rw never creates an accidentally misspelled database.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            ids = candidates(db, texts, agent_id, mode)
            # Create exclusively: never overwrite an earlier backup (or the source).
            with backup.open(mode="xb"):
                pass
            with closing(connect_readonly(path)) as source, closing(sqlite3.connect(backup)) as dest:
                source.backup(dest)
                if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("backup integrity check failed")
            deleted_events = 0
            for rid in ids:
                deleted_events += db.execute(
                    "DELETE FROM events WHERE json_extract(body, '$.request_id')=?", (rid,)).rowcount
                db.execute("DELETE FROM state WHERE kind='request' AND key=?", (rid,))
                # These are queued copies of those events, not unrelated request state.
                db.execute("DELETE FROM state WHERE kind IN ('terminal_delivery', 'reporter_delivery') "
                           "AND json_extract(body, '$.request_id')=?", (rid,))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"requests_deleted": len(ids), "events_deleted": deleted_events, "backup": str(backup)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--text", action="append", help="exact text, repeatable; default: save outputs/counts.tsv")
    parser.add_argument("--agent-id", default="worker", help="exact agent; empty string selects null")
    parser.add_argument("--mode", choices=("direct", "orchestrate", "plan_only"), default="direct")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--gateway-stopped", action="store_true", help="confirm offline maintenance window")
    parser.add_argument("--config")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args(argv)
    texts, agent = args.text or ["save outputs/counts.tsv"], args.agent_id or None
    if not args.apply:
        with closing(connect_readonly(args.database)) as db:
            ids = candidates(db, texts, agent, args.mode)
        print(json.dumps({"dry_run": True, "request_count": len(ids), "request_ids": ids}))
        return 0
    if not (args.gateway_stopped and args.config and args.backup):
        parser.error("apply requires --gateway-stopped, --config and --backup")
    from labhq.cli import _operation_lock
    from labhq.settings import Settings

    settings = Settings.load(args.config)
    if (settings.path(settings.gateway.state_dir) / "gateway.sqlite3").resolve() != args.database.resolve():
        parser.error("config and database must name the same gateway store")
    with _operation_lock(settings):
        assert_gateway_stopped(settings)
        result = purge(args.database, args.backup, texts, agent, args.mode)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
