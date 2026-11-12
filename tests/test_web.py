"""The gateway serves the web office and keeps phone sockets alive on stale approvals."""

import json
import shutil
import subprocess

from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.models import ApprovalRequest
from labhq.settings import Settings


def test_inbox_summary_auth_snapshot_and_manual_recovery_refresh(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    app = create_app(s)
    hub = app.state.hub
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {s.gateway.client_token}"}
    try:
        message = {"type": "agent.log", "runner_seq": 1, "data": {"text": "PRIVATE_PAYLOAD", "token": "PRIVATE_TOKEN"}}
        hub.store.accept_runner_inbox("runner", None, 1, message)
        hub.store.set_runner_inbox_state("runner", None, 1, "processing", expected="pending")
        hub.store.set_runner_inbox_state("runner", None, 1, "failed", "storage_interrupted", expected="processing")
        assert client.get("/api/runner-inbox").status_code == 401
        response = client.get("/api/runner-inbox", headers=headers)
        assert response.status_code == 200 and response.json() == hub.snapshot()["data"]["runner_inbox"]
        assert "PRIVATE_PAYLOAD" not in response.text and "PRIVATE_TOKEN" not in response.text
        path = response.json()[0]["recovery_path"]
        assert client.post(path, headers=headers).status_code == 200
        assert client.get("/api/runner-inbox", headers=headers).json() == []
        assert hub.snapshot()["data"]["runner_inbox"] == []
    finally:
        hub.store.close()


def test_both_inbox_views_refresh_after_recovery_and_reconnect(tmp_path):
    from labhq.gateway import server
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    state = (server.WEB / "state.js").read_text(encoding="utf-8")
    for page in [server.WEB / "index.html", server.WEB / "lab3d/src/live.js"]:
        source = page.read_text(encoding="utf-8")
        refresh = source.split("  async function refreshInbox() {", 1)[1].split("\n  }", 1)[0]
        assert "setInterval(refreshInbox, 5000)" in source
        assert "runnerInboxText(S)" in source
        script = state + """
const assert = require('node:assert/strict');
const {S, apply} = LabHQState.createOfficeState();
const row = {runner_id:'runner', incarnation:'new', seq:2, state:'blocked', error:'ask_start_unknown', current_generation:true, action:null};
apply({type:'snapshot', seq:1, data:{runner_inbox:[row]}});
assert.match(LabHQState.runnerInboxText(S), /runner.*new.*seq 2.*blocked/);
assert.ok(!LabHQState.runnerInboxText(S).includes('POST'));
let token='PRIVATE_TOKEN', inboxLoading=false, inboxEpoch=0, demo=false, IS_DEMO=false;
let renders=0; const render=()=>renders++, schedule=render;
let replies=[[row], []], fetches=[];
let fetch=async (url, options)=>{ fetches.push([url,options]); return {ok:true,json:async()=>replies.shift()}; };
async function refreshInbox() { REFRESH }
(async()=>{
  S.conn='live'; await refreshInbox(); assert.equal(S.runnerInbox.length,1);
  await refreshInbox(); assert.equal(LabHQState.runnerInboxText(S),'');
  apply({type:'snapshot',seq:2,data:{runner_inbox:[{...row, current_generation:false}]}});
  assert.ok(!LabHQState.runnerInboxText(S).includes('POST'));
  fetch=async()=>{throw Error('unreachable')}; await refreshInbox();
  assert.match(LabHQState.runnerInboxText(S), /doctor --network/);
  assert.ok(!LabHQState.runnerInboxText(S).includes(token));
  assert.equal(fetches[0][0],'/api/runner-inbox');
  assert.equal(fetches[0][1].headers.Authorization,'Bearer PRIVATE_TOKEN');
  // A pending HTTP response cannot overwrite a newer reconnect snapshot.
  let finish; fetch=()=>new Promise(resolve=>finish=resolve);
  const pending=refreshInbox(); inboxEpoch++;
  apply({type:'snapshot',seq:3,data:{runner_inbox:[]}});
  finish({ok:true,json:async()=>[row]}); await pending;
  assert.equal(S.runnerInbox.length,0);
})().catch(e=>{console.error(e);process.exitCode=1});
""".replace("REFRESH", refresh)
        script_path = tmp_path / "inbox-view.cjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run([node, str(script_path)], capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr


def test_live_approval_surfaces_server_refusal_even_with_open_socket():
    from labhq.gateway import server
    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    approve = html.split("    approve: ", 1)[1].split("\n    uploadLiteraturePdf:", 1)[0].rstrip().removesuffix(",")
    script = """
const ws = {readyState: 1, send: () => {}};
const post = async () => { throw new Error('server refused'); };
const approve = APPROVE;
approve('appr', true).then(() => process.exit(1), e => process.exit(e.message === 'server refused' ? 0 : 2));
""".replace("APPROVE", approve)
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_snapshot_preserves_request_activity_for_initial_focus(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["older"] = {"id": "older", "text": "Continue work", "status": "running",
                             "created_at": 10, "updated_at": 90}
    hub.requests["newer"] = {"id": "newer", "text": "Finished work", "status": "done",
                             "created_at": 80, "updated_at": 80}
    rows = {row["id"]: row for row in hub.snapshot()["data"]["requests"]}
    assert rows["older"]["updated_at"] == 90
    assert rows["newer"]["updated_at"] == 80


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
    for name in ("tokens", "components"):
        css_path = f"ui/{name}.css"
        assert f'href="{css_path}"' in page.text
        css = client.get(f"/{css_path}")
        assert css.status_code == 200
        assert css.headers["content-type"].startswith("text/css")
        assert "--ds-" in css.text
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
    components = (server.WEB / "ui" / "components.css").read_text(encoding="utf-8")
    assert ".chip.st-skipped { text-decoration: line-through; }" in components
    assert ".dag-node.st-skipped" in html
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
