"""Local recall uses synthetic state only, never a runner's files."""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, create_app
from labhq.settings import DataZone, Settings


def config(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "gateway")
    s.gateway.client_token = "test-client-token"
    return s


def request(hub, rid="req_one", text="한국어 유전자 분석", **extra):
    req = {"id": rid, "text": text, "status": "done", "results": {
        "analysis": {"status": "failed", "failure_kind": "environment"}}, **extra}
    hub.requests[rid] = req
    hub.save_request(rid)
    return req


@pytest.mark.asyncio
async def test_korean_literal_limits_and_restart(tmp_path):
    hub = Hub(config(tmp_path))
    for i in range(8):
        request(hub, f"req_{i}", "유전자 분석 100% _literal OR \"별\" " + "설명" * 500)
    await hub.knowledge.drain()
    for query in ("분석", "유전자", "100%", "_literal", 'OR "별"'):
        found = hub.knowledge.search(query)
        assert len(found) == 5
        assert sum(len(x["body"]) for x in found) <= 2000
        assert all(x["source"].startswith("request:") for x in found)
    assert hub.knowledge.search("없는단어") == []
    restarted = Hub(hub.s)
    await restarted.knowledge.drain()
    assert restarted.knowledge.health()["count"] == 8
    assert restarted.knowledge.search("유전자") == hub.knowledge.search("유전자")
    assert Hub(config(tmp_path / "other")).knowledge.search("유전자") == []


@pytest.mark.asyncio
async def test_fallback_is_bounded_and_wildcards_are_literal(tmp_path, monkeypatch):
    from labhq.knowledge import recall
    monkeypatch.setattr(recall, "_enable_trigram", lambda db: False)
    monkeypatch.setattr(recall, "SCAN_LIMIT", 3)
    hub = Hub(config(tmp_path))
    request(hub, "req_0", "오래된항목")
    for i in range(1, 5):
        request(hub, f"req_{i}", "새로운항목 100%")
    await hub.knowledge.drain()
    assert hub.knowledge.health()["fallback"] is True
    assert hub.knowledge.search("오래된") == []
    assert hub.knowledge.search("100%")
    assert hub.knowledge.search("100_") == []


@pytest.mark.asyncio
async def test_redaction_at_write_and_read_and_unknown_exclusion(tmp_path):
    s = config(tmp_path)
    s.policy.data_zones = [DataZone(path="/controlled")]
    hub = Hub(s)
    request(hub, "req_zone", "제한자료 /controlled/sample")
    request(hub, "req_private", "검색 owner/private-repo /private/ref 비밀 " + s.gateway.client_token,
            references=[{"kind": "github", "value": "owner/private-repo", "source": "request"},
                        {"kind": "path", "value": "/private/ref"}],
            reference_original="never-index-this")
    request(hub, "req_unknown", "판정불명", references=[{"kind": "unknown", "value": "hidden"}])
    request(hub, "req_changed", "새구역 /later/private")
    await hub.knowledge.drain()
    with sqlite3.connect(hub.knowledge.path) as db:
        stored = str(db.execute("SELECT body FROM documents").fetchall())
    for secret in ("/controlled", "private-repo", "/private/ref", s.gateway.client_token,
                   "never-index-this", "판정불명"):
        assert secret not in stored
    assert "<restricted-zone>" in stored
    assert "<private-reference>" in stored
    assert hub.knowledge.search("private-repo") == []
    s.policy.data_zones.append(DataZone(path="/later"))
    assert hub.knowledge.search("새구역") == []
    assert hub.knowledge.search("/later/private") == []


@pytest.mark.asyncio
async def test_checkpoint_batches_and_idempotent_terminal_retry(tmp_path, monkeypatch):
    from labhq.knowledge import recall
    monkeypatch.setattr(recall, "BATCH_SIZE", 2)
    hub = Hub(config(tmp_path))
    for i in range(5):
        request(hub, f"req_{i}")
    assert await hub.knowledge.batch()
    assert hub.knowledge.health()["count"] == 2
    restarted = Hub(hub.s)
    await restarted.knowledge.drain()
    assert restarted.knowledge.health()["count"] == 5
    req = restarted.requests["req_0"]
    req["text"] = "재처리 완료"
    for _ in range(2):
        restarted.store.commit_terminal("req_0", req, {"type": "request.completed"}, 100)
    await restarted.knowledge.drain()
    assert restarted.knowledge.health()["count"] == 5
    assert restarted.knowledge.search("재처리")[0]["request_id"] == "req_0"
    assert restarted.store.all("knowledge_pending") == {}


@pytest.mark.asyncio
async def test_index_failure_does_not_undo_terminal_and_retries(tmp_path, monkeypatch):
    hub = Hub(config(tmp_path))
    request(hub, results={})
    original = hub.knowledge._write
    monkeypatch.setattr(hub.knowledge, "_write", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    hub.commit_terminal("req_one", "request.completed", {"ok": True})
    await hub.knowledge.drain()
    assert hub.store.get("request", "req_one")["status"] == "done"
    assert any(e["type"] == "request.completed" for e in hub.store.events_since(0))
    assert hub.store.all("knowledge_pending")
    monkeypatch.setattr(hub.knowledge, "_write", original)
    await hub.knowledge.drain()
    assert hub.knowledge.search("분석")


def test_authenticated_api_and_no_writer_for_runner(tmp_path):
    app = create_app(config(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/knowledge/search", params={"q": "분석"}).status_code == 401
        assert client.get("/api/knowledge/search", params={"q": "분석"},
                          headers={"Authorization": "Bearer " + app.state.hub.s.gateway.runner_token}).status_code == 401
        auth = {"Authorization": "Bearer test-client-token"}
        assert client.get("/api/knowledge/search", params={"q": "분석"}, headers=auth).json() == []
        assert client.get("/api/knowledge/search", params={"q": "한"}, headers=auth).status_code == 422
        assert client.post("/api/knowledge/search", headers=auth, json={}).status_code == 405


def test_cli_uses_gateway_and_doctor_is_read_only(tmp_path, monkeypatch, capsys):
    from labhq import cli
    from labhq.knowledge.recall import health
    s = config(tmp_path)
    monkeypatch.setattr(cli.Settings, "load", lambda _: s)
    calls = []
    monkeypatch.setattr(cli, "_api", lambda *args, **kw: calls.append((args, kw)) or [
        {"request_id": "req_one", "body": "분석 완료", "source": "request:req_one"}])
    cli.main(["recall", "분석"])
    assert calls[0][0][1:] == ("GET", "/api/knowledge/search")
    assert calls[0][1] == {"params": {"q": "분석"}}
    assert "분석 완료" in capsys.readouterr().out
    assert health(s)["count"] == 0
    assert not (tmp_path / "gateway" / "knowledge.sqlite3").exists()


@pytest.mark.asyncio
async def test_worker_indexes_new_terminal_and_records_step_failure(tmp_path):
    hub = Hub(config(tmp_path))
    hub.knowledge.start()
    try:
        request(hub)
        hub.commit_terminal("req_one", "request.completed", {"ok": True})
        for _ in range(100):
            if hub.knowledge.search("environment"):
                break
            await asyncio.sleep(0.01)
        hit = hub.knowledge.search("environment")[0]
        assert "analysis: failed; failure_kind=environment" in hit["body"]
        assert hub.knowledge.health()["last_success"]
    finally:
        await hub.knowledge.stop()


def test_projection_never_reads_original_or_files(tmp_path, monkeypatch):
    from pathlib import Path
    from labhq.knowledge.recall import project

    class Metadata(dict):
        def get(self, key, default=None):
            assert key not in {"reference_original", "report", "error", "text_original"}
            return super().get(key, default)

    req = Metadata(text="안전한 분석", status="done", results={"step": {
        "status": "done", "text": "raw-secret", "error": "raw-error", "workdir": "/never/open"}})
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("file read"))
    record = project(config(tmp_path), "req_safe", req)
    assert record and "raw" not in record["body"]


@pytest.mark.asyncio
async def test_saved_error_metadata_and_incomplete_status(tmp_path):
    hub = Hub(config(tmp_path))
    request(hub, results={"analysis": {"ok": False, "error": "connection timed out",
                                     "missing_outputs": ["unread.txt"], "text": "never indexed"}})
    await hub.knowledge.drain()
    body = hub.knowledge.search("transient")[0]["body"]
    assert "INCOMPLETE" in body and "never indexed" not in body and "connection timed out" not in body


def test_doctor_reports_index_health_without_creating_it(tmp_path, monkeypatch):
    from labhq.doctor import collect
    s = config(tmp_path)
    manifest = collect(s, dry_run=True)
    row = next(r for r in manifest["checks"] if r["name"] == "local recall")
    assert "count=0" in row["detail"] and "last_success=never" in row["detail"]
    assert "fallback=unknown" in row["detail"]
    assert not (tmp_path / "gateway" / "knowledge.sqlite3").exists()
