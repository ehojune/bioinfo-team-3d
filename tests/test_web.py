"""The gateway serves the web office and keeps phone sockets alive on stale approvals."""

import json

from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.models import ApprovalRequest
from labhq.settings import Settings


def test_office_page_manifest_and_stale_approval(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    app = create_app(s)
    hub = app.state.hub
    published = []
    original_publish = hub.publish

    async def record_publish(event, *args, **kwargs):
        published.append(event)
        await original_publish(event, *args, **kwargs)

    hub.publish = record_publish
    client = TestClient(app)
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
    assert not [event for event in published if event["type"] == "approval.stale"]


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
    state = (server.WEB / "state.js").read_text(encoding="utf-8")
    reset = state.split("function resetSnapshotState() {", 1)[1].split("\n}", 1)[0]
    for field in ("agents", "approvals", "requests", "jobs", "taskStep"):
        assert f"S.{field}.clear()" in reset
    for field in ("suggestions", "feed", "cost", "lastSay", "seq", "doorUntil", "current", "projects"):
        assert f"S.{field} = " in reset
    snapshot = state.split("case 'snapshot': {", 1)[1].split("case 'roster.updated':", 1)[0]
    assert snapshot.index("resetSnapshotState();") < snapshot.index("(d.recent_events || []).forEach")
    assert snapshot.index("(d.recent_events || []).forEach") < snapshot.index("S.cost = (d.requests || [])")
    assert "Object.assign(q.steps, r.step_status || {})" in snapshot

def test_skipped_steps_and_unparsed_reviews_have_explicit_ui_states():
    from labhq.gateway import server

    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    state = (server.WEB / "state.js").read_text(encoding="utf-8")
    assert "case 'request.step_skipped':" in state
    assert "q.steps[d.step_id] = 'skipped'" in state
    assert ".chip.st-skipped" in html and ".dag-node.st-skipped" in html
    assert "skipped: '건너뜀'" in html
    assert "d.status === 'review_unparsed'" in state
    assert "리뷰 판정 실패. PI 확인이 필요해요" in state


def test_quota_wait_card_has_a_working_manual_resume_action():
    from labhq.gateway import server

    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    state = (server.WEB / "state.js").read_text(encoding="utf-8")
    tasks = (server.WEB / "ui" / "tasks.js").read_text(encoding="utf-8")
    assert "case 'request.step_quota_wait':" in state
    assert "한도 대기 · ${when}${until}" in tasks and "지금 재개" in tasks  # R21: date and deadline in the text
    assert "options.onQuotaResume(step.id)" in tasks
    assert "resumeQuota: (rid, sid) => post(" in html
    assert "/resume-quota`" in html


def test_login_wait_has_one_engine_card_and_manual_resume_action():
    from labhq.gateway import server

    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    state = (server.WEB / "state.js").read_text(encoding="utf-8")
    tasks = (server.WEB / "ui" / "tasks.js").read_text(encoding="utf-8")
    live = (server.WEB / "lab3d" / "src" / "live.js").read_text(encoding="utf-8")
    assert "case 'request.step_login_wait':" in state and "case 'engine.login_wait':" in state
    assert "S.engineHolds.set(d.engine" in state and 'id="engine-holds"' in html
    assert "로그인했어요 · 다시 시도" in html and "로그인했어요 · 다시 시도" in tasks
    assert "login-holds" in live and "/resume-quota" in live


def test_clarify_approval_takes_a_typed_answer():
    """A PI question is answered in the approval note; an empty note would stop the request."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "labhq" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    decide = (web / "ui" / "decide.js").read_text(encoding="utf-8")
    assert "add(body, 'textarea', '', 'ans')" in decide and "approved: ok, note }" in html
    assert "li.dataset.kind === 'clarify'" in html
    live = (web / "lab3d" / "src" / "live.js").read_text(encoding="utf-8")
    assert "a.kind === 'clarify' && approved" in live and "approved, note}" in live
    assert "clarify: 'PI 질문'" in (web / "state.js").read_text(encoding="utf-8")


def test_ui_modules_are_bounded_static_assets(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    client = TestClient(create_app(s))
    for name in ("decide.js", "strip.js", "tasks.js", "shell.js"):
        response = client.get(f"/ui/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
    for path in ("/ui/missing.js", "/ui/private.txt", "/ui/%2e%2e/state.js", "/ui/C:%5cWindows%5cwin.ini"):
        assert client.get(path).status_code in (403, 404)


def test_approval_history_is_authenticated_and_newest_first(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    app = create_app(s)
    hub = app.state.hub
    first = ApprovalRequest(id="appr_first", kind="budget", summary="first", created_at=10)
    second = ApprovalRequest(id="appr_second", kind="tool_permission", summary="second", created_at=20)
    for request in (first, second):
        hub.approvals[request.id] = {"approval": request.model_dump(mode="json"), "origin": None}
        hub.save_approval(request.id)
    headers = {"Authorization": f"Bearer {s.gateway.client_token}"}
    with TestClient(app) as client:
        assert client.get("/api/approvals/history").status_code == 401
        assert client.get("/api/approvals/history", headers=headers).status_code == 200
        assert client.post("/api/approvals/appr_first", headers=headers,
                           json={"approved": True, "note": "ok"}).status_code == 200
        assert client.post("/api/approvals/appr_second", headers=headers,
                           json={"approved": False, "note": "later"}).status_code == 200
        history = client.get("/api/approvals/history?limit=2", headers=headers).json()
        assert [item["approval"]["id"] for item in history] == ["appr_second", "appr_first"]
        assert history[0]["note"] == "later"
        assert client.get("/api/approvals/history?limit=0", headers=headers).status_code == 422
