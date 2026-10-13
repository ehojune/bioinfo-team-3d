from contextlib import closing
import json
import socket

import pytest

from labhq.settings import Settings
from labhq.store import StateStore
from scripts import purge_test_requests as cleanup


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "gateway.sqlite3"
    store = StateStore(path)
    for rid, text, agent, mode in [
        ("test", "save outputs/counts.tsv", "worker", "direct"),
        ("real-agent", "save outputs/counts.tsv", "analyst", "direct"),
        ("real-mode", "save outputs/counts.tsv", "worker", "orchestrate"),
        ("real-text", "save outputs/counts.tsv please", "worker", "direct"),
    ]:
        store.put("request", rid, {"text": text, "agent_id": agent, "mode": mode})
        event = {"request_id": rid, "type": "request.completed"}
        store.append_event(event, 100)
        store.put("terminal_delivery", rid, event)
    store.put("other", "test", {"request_id": "test"})
    store.close()
    return path


def test_dry_run_changes_nothing_and_requires_all_markers(database, capsys):
    before = database.read_bytes()
    cleanup.main(["--database", str(database)])
    assert json.loads(capsys.readouterr().out)["request_ids"] == ["test"]
    assert database.read_bytes() == before


def test_apply_requires_offline_confirmation_and_backup(database):
    with pytest.raises(SystemExit):
        cleanup.main(["--database", str(database), "--apply"])


def test_purge_backs_up_and_removes_only_matching_requests_and_events(database, tmp_path):
    backup = tmp_path / "before.sqlite3"
    result = cleanup.purge(database, backup, ["save outputs/counts.tsv"], "worker", "direct")
    assert result["requests_deleted"] == result["events_deleted"] == 1
    with closing(cleanup.connect_readonly(backup)) as db:
        assert db.execute("SELECT COUNT(*) FROM state WHERE kind='request'").fetchone()[0] == 4
    with closing(cleanup.connect_readonly(database)) as db:
        assert db.execute("SELECT COUNT(*) FROM state WHERE kind='request'").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM state WHERE kind='terminal_delivery'").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM state WHERE kind='other'").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        cleanup.purge(database, backup, ["save outputs/counts.tsv"], "worker", "direct")


def test_live_endpoint_refuses_cleanup(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        s.gateway.url = f"ws://127.0.0.1:{listener.getsockname()[1]}"
        with pytest.raises(RuntimeError, match="listening"):
            cleanup.assert_gateway_stopped(s)


def test_live_pid_refuses_cleanup(tmp_path, monkeypatch):
    s = Settings()
    s.gateway.state_dir = str(tmp_path)
    (tmp_path / "labhq-gateway.pid").write_text('{"pid": 123}', encoding="utf-8")
    monkeypatch.setattr("labhq.cli._process_created", lambda _: "alive")
    with pytest.raises(RuntimeError, match="PID"):
        cleanup.assert_gateway_stopped(s)


def test_apply_cli_and_backup_failure(database, tmp_path, capsys, monkeypatch):
    def refused(*args, **kwargs):
        raise ConnectionRefusedError()
    monkeypatch.setattr(cleanup.socket, "create_connection", refused)
    config = tmp_path / "config.yaml"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config.write_text(f'gateway:\n  state_dir: "{tmp_path.as_posix()}"\n'
                      f'  url: "ws://127.0.0.1:{port}"\n', encoding="utf-8")
    backup = tmp_path / "backup.sqlite3"
    backup.write_bytes(b"must survive")
    args = ["--database", str(database), "--apply", "--gateway-stopped",
            "--config", str(config), "--backup", str(backup)]
    with pytest.raises(FileExistsError):
        cleanup.main(args)
    assert backup.read_bytes() == b"must survive"
    with closing(cleanup.connect_readonly(database)) as db:
        assert cleanup.candidates(db, ["save outputs/counts.tsv"]) == ["test"]
    backup.unlink()
    cleanup.main(args)
    assert json.loads(capsys.readouterr().out)["requests_deleted"] == 1


def test_uncertain_endpoint_refuses_cleanup(tmp_path, monkeypatch):
    s = Settings()
    s.gateway.state_dir = str(tmp_path)
    def timed_out(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(cleanup.socket, "create_connection", timed_out)
    with pytest.raises(RuntimeError, match="cannot establish"):
        cleanup.assert_gateway_stopped(s)
