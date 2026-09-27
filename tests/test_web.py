"""The gateway serves the web office and keeps phone sockets alive on stale approvals."""

import json

from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.settings import Settings


def test_office_page_manifest_and_stale_approval(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    client = TestClient(create_app(s))
    page = client.get("/")
    assert page.status_code == 200 and 'window.LABHQ_BOOT={"mode":"live"}' in page.text
    assert "<!--LABHQ_BOOT-->" not in page.text and 'id="zones"' in page.text
    assert client.get("/manifest.webmanifest").json()["short_name"] == "labhq"
    assert client.get("/icon.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/health").json()["service"] == "labhq gateway"
    with client.websocket_connect(f"/ws/client?token={s.gateway.client_token}") as ws:
        assert json.loads(ws.receive_text())["type"] == "snapshot"
        ws.send_text(json.dumps({"type": "approval.resolve", "id": "appr_gone", "approved": True}))
        assert json.loads(ws.receive_text())["type"] == "approval.stale"  # still connected


def test_demo_page_without_gateway_boot_marker():
    from labhq.gateway import server
    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    assert "<!--LABHQ_BOOT-->" in html  # published/static copies fall back to demo mode


def test_live_reconnect_seq_lives_only_in_page_memory():
    from labhq.gateway import server
    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    live = html.split("function startLive() {", 1)[1].split("/* ----------------", 1)[0]
    assert "let lastSeq = 0;" in live  # a newly opened page asks for a snapshot
    assert "labhq_last_seq" not in html  # ignore any key left by older versions
    assert "${lastSeq ? `&since=${lastSeq}` : ''}" in live  # only reconnections send since


def test_replay_gap_snapshot_replaces_office_state():
    from labhq.gateway import server
    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    reset = html.split("function resetSnapshotState() {", 1)[1].split("\n}", 1)[0]
    for field in ("agents", "approvals", "requests", "jobs", "taskStep"):
        assert f"S.{field}.clear()" in reset
    for field in ("suggestions", "feed", "cost", "lastSay", "seq", "doorUntil", "current", "projects"):
        assert f"S.{field} = " in reset
    snapshot = html.split("case 'snapshot': {", 1)[1].split("case 'roster.updated':", 1)[0]
    assert snapshot.index("resetSnapshotState();") < snapshot.index("(d.recent_events || []).forEach")
    assert snapshot.index("(d.recent_events || []).forEach") < snapshot.index("S.cost = (d.requests || [])")
    assert "Object.assign(q.steps, r.step_status || {})" in snapshot

def test_skipped_steps_and_unparsed_reviews_have_explicit_ui_states():
    from labhq.gateway import server

    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    assert "case 'request.step_skipped':" in html
    assert "q.steps[d.step_id] = 'skipped'" in html
    assert ".chip.st-skipped" in html and ".dag-node.st-skipped" in html
    assert "skipped: '건너뜀'" in html
    assert "d.status === 'review_unparsed'" in html
    assert "리뷰 판정 실패. PI 확인이 필요해요" in html
