import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub as RealHub, create_app
from labhq.evidence.audit import verify_request
from labhq.integrations.github import GitHubClient
from labhq.request_bundle import build_request_bundle
from labhq.result_storage import ResultStorage
from labhq.ro_crate import verify_bundle_copy
from labhq.settings import DataZone, ProjectSettings, Settings
from tests.test_request_bundle import pending_bundle_fixture
from tests.test_result_storage import Hub


async def test_editor_preserves_verified_bundle(tmp_path):
    settings, req, _ = pending_bundle_fixture(tmp_path, "req_test")
    bundle = Path(build_request_bundle(req, settings)["path"])
    before = {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    assert not verify_bundle_copy(bundle)["problems"]
    assert verify_request(req, settings)["exit_code"] == 0
    hub = Hub(tmp_path)
    hub.s, hub.requests = settings, {req["id"]: req}
    req["result_storage"] = {"mode": "local", "status": "selected"}
    await ResultStorage(hub).finish(req["id"])
    assert not verify_bundle_copy(bundle)["problems"]
    assert verify_request(req, settings)["exit_code"] == 0
    assert before == {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    assert Path(req["result_storage"]["draft_path"], "README.md").is_file()


@pytest.mark.parametrize("channel", ["http", "websocket"])
def test_publish_approval_requires_pi_review(tmp_path, channel):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "test-token"
    app = create_app(settings)
    hub = app.state.hub
    with TestClient(app) as client:
        hub.requests["req_test"] = {"id": "req_test", "status": "done", "pi_review_pending": False}
        hub.approvals["a"] = {"approval": {"id": "a", "kind": "result_publish", "request_id": "req_test"},
                              "origin": None}
        headers = {"Authorization": "Bearer test-token"}
        if channel == "http":
            response = client.post("/api/approvals/a", json={"approved": True}, headers=headers)
            assert response.status_code == 422
        else:
            with client.websocket_connect("/ws/client?token=test-token") as ws:
                ws.receive_json()
                ws.send_json({"type": "approval.resolve", "id": "a", "approved": True})
                assert ws.receive_json()["type"] == "error"
        assert "a" in hub.approvals
        assert hub.store.get("approval_decision", "a") is None
        assert client.post("/api/requests/req_test/review-done", headers=headers).status_code == 200
        assert client.post("/api/approvals/a", json={"approved": True}, headers=headers).status_code == 200


async def test_restart_reuses_saved_draft_without_editor(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    hub = RealHub(settings)
    hub.requests["req_test"] = {"id": "req_test", "status": "done", "report": "original report",
        "pi_reviewed_at": 1, "result_storage": {"mode": "existing", "repo": "owner/study", "status": "selected"}}
    hub.agents["editor"] = {}
    editor = AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "original edition"}))
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    monkeypatch.setattr("labhq.result_storage.edition_assets", lambda *args: (
        {"steps/s1/outputs/table.tsv": "gene\tvalue\r\nA\t1\r\n"},
        {"steps/s1/outputs/plot.png": b"original-image"}))
    reached = asyncio.Event()
    original = {}

    async def wait_for_pi(**kw):
        original.update(kw["detail"])
        reached.set()
        await asyncio.Future()

    monkeypatch.setattr(hub, "request_approval", wait_for_pi)
    hub.result_storage.schedule("req_test")
    await asyncio.wait_for(reached.wait(), 5)
    await hub.result_storage.close()
    draft = Path(hub.requests["req_test"]["result_storage"]["draft_path"])
    before = {p.relative_to(draft): (p.read_bytes(), p.stat().st_mtime_ns) for p in draft.rglob("*") if p.is_file()}
    restarted = RealHub(settings)
    restarted.agents["editor"] = {}
    monkeypatch.setattr(restarted.orchestrator, "run_step", editor)
    restarted.requests["req_test"]["report"] = "must not generate another edition"
    approval = AsyncMock(return_value={"approved": True})
    upload = AsyncMock()
    monkeypatch.setattr(restarted, "request_approval", approval)
    monkeypatch.setattr(restarted.result_storage, "upload", upload)
    restarted.result_storage.schedule("req_test")
    await restarted.result_storage.tasks["req_test"]
    assert editor.await_count == 1
    assert approval.call_args.kwargs["detail"] == original
    assert upload.call_args.args[1] == original["documents"]
    uploaded_images = upload.call_args.args[2]
    assert uploaded_images["steps/s1/outputs/plot.png"] == b"original-image"
    assert uploaded_images["graphical_abstract.svg"].startswith(b"<svg")
    assert before == {p.relative_to(draft): (p.read_bytes(), p.stat().st_mtime_ns) for p in draft.rglob("*") if p.is_file()}
    await restarted.result_storage.close()


async def test_auto_publish_waits_for_review_and_resumes_draft(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.github.auto_publish_results = True
    hub = RealHub(settings)
    hub.requests["req_test"] = {"id": "req_test", "status": "done", "report": "original report",
        "result_storage": {"mode": "existing", "repo": "owner/study", "status": "selected"}}
    upload = AsyncMock()
    monkeypatch.setattr(hub.result_storage, "upload", upload)
    await hub.result_storage.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft"
    upload.assert_not_awaited()
    hub.review_done("req_test")
    await hub.result_storage.tasks["req_test"]
    upload.assert_awaited_once()
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    await hub.result_storage.close()


@pytest.mark.parametrize("failure_point", ["write", "submit"])
async def test_round_refresh_failure_does_not_turn_a_completed_upload_into_failure(
        tmp_path, monkeypatch, failure_point):
    hub = Hub(tmp_path)
    hub.s.github.auto_publish_results = True
    hub.requests["req_test"].update(
        status="done", report="original report", pi_reviewed_at=1,
        result_storage={"mode": "existing", "repo": "owner/study", "status": "selected"})
    hub.rounds = SimpleNamespace(
        write=Mock(side_effect=OSError("round state unavailable") if failure_point == "write" else None),
        submit=Mock(side_effect=OSError("round state unavailable") if failure_point == "submit" else None))
    service = ResultStorage(hub)
    monkeypatch.setattr(service, "edition_pdf", AsyncMock(return_value=(None, "test environment")))
    monkeypatch.setattr(service, "upload", AsyncMock())

    await service.finish("req_test")

    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "published"
    assert saved["round_warning"] == "게시를 마쳤지만 운전 기록을 갱신하지 못했습니다(OSError)."
    service.upload.assert_awaited_once()
    if failure_point == "write":
        hub.rounds.submit.assert_not_called()
    else:
        hub.rounds.submit.assert_called_once_with("req_test")


async def test_direct_upload_rejects_unreviewed_request(tmp_path):
    hub = Hub(tmp_path)
    hub.requests["req_test"]["result_storage"] = {"mode": "new", "repo": "owner/study"}
    with pytest.raises(ValueError, match="검토 완료"):
        await ResultStorage(hub).upload("req_test", {"README.md": "result"})


async def test_new_repository_root_and_compatibility_payload_share_table_selection(tmp_path):
    hub = Hub(tmp_path)
    internal = tmp_path / "internal"
    internal.mkdir()
    hub.s.policy.data_zones = [DataZone(path=str(internal), level="internal")]
    hub.requests["req_test"].update(
        pi_reviewed_at=1,
        project_dirs=[str(internal)],
        result_storage={"mode": "new", "repo": "owner/study", "allow_public": True},
    )
    calls = []

    class GitHub:
        async def repo_info(self, _repo):
            return {"private": True, "default_branch": "main"}

        async def repository_is_empty(self, _repo, _branch):
            return False

        async def put_file(self, _repo, name, body, _message, branch):
            calls.append((name, body, branch))

        async def put_image(self, _repo, name, body, branch):
            calls.append((name, body, branch))

        async def put_binary(self, _repo, name, body, branch, _message):
            calls.append((name, body, branch))

    service = ResultStorage(hub)
    service.client = lambda: GitHub()
    docs = {
        "README.md": "portfolio",
        "report/report.md": "report",
        "tables/result_summary.tsv": "group\tcount\nall\t42\n",
        "report.md": "compatibility",
        "steps/s1/outputs/notes.txt": "allowed compatibility output",
        "steps/s1/outputs/sample_metadata.tsv": "sample\tgroup\ns1\tcase\n",
        "steps/s1/outputs/by_group.tsv": "group\tcount\ncase\t9\n",
    }

    await service.upload("req_test", docs)

    names = {name for name, _body, _branch in calls}
    assert {"README.md", "report/report.md", "tables/result_summary.tsv"} <= names
    assert "report.md" not in names and not any(name.startswith("steps/") for name in names)
    compat = "labhq/results/req_test/"
    assert compat + "report.md" in names
    assert compat + "steps/s1/outputs/notes.txt" in names
    assert not any("sample_metadata.tsv" in name or "by_group.tsv" in name for name in names)


async def test_public_project_without_permission_stays_local(tmp_path):
    hub = Hub(tmp_path)
    hub.s.projects = [ProjectSettings(id="study", repo="owner/study", visibility="public", allow_public_reports=False)]
    req = hub.requests["req_test"]
    req.update(project_id="study", status="done")
    hub.request_approval = AsyncMock(return_value={"approved": True})
    service = ResultStorage(hub)
    assert await service.card("req_test") is None
    assert req["result_storage"]["mode"] == "local"
    await service.finish("req_test")
    assert req["result_storage"]["status"] == "local"
    hub.request_approval.assert_not_awaited()


@pytest.mark.parametrize("image", [False, True])
async def test_contents_paths_encode_special_filenames(image):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(404 if request.method == "GET" else 201, json={})
    gh = GitHubClient("fake-token", "https://api.github.com", httpx.MockTransport(respond))
    path = "labhq/results/req_test/steps/s1/outputs/한글 plot#1%20?(a)." + ("png" if image else "txt")
    try:
        if image:
            await gh.put_image("owner/study", path, b"image", "main")
        else:
            await gh.put_file("owner/study", path, "text", "result", "main")
        expected = ("/repos/owner/study/contents/" + quote(path, safe="/")).encode()
        assert len(calls) == 2
        for request in calls:
            assert request.url.raw_path.split(b"?", 1)[0] == expected
            assert not request.url.fragment
            assert dict(request.url.params) == ({"ref": "main"} if request.method == "GET" else {})
    finally:
        await gh.http.aclose()


async def test_edition_links_encode_special_filenames(tmp_path):
    hub = Hub(tmp_path)
    req = hub.requests["req_test"]
    req.update(status="done", result_storage={"mode": "local", "status": "selected"})
    name = "steps/s1/outputs/한글 plot#1%20(a).png"
    bundle = Path(hub.s.runner.workspace_root) / "requests/req_test"
    output = bundle / name
    output.parent.mkdir(parents=True)
    output.write_bytes(b"image")
    (bundle / "MANIFEST.tsv").write_text("relative_path\tstatus\n" + name + "\tcopied\n", encoding="utf-8")
    await ResultStorage(hub).finish("req_test")
    draft = Path(req["result_storage"]["draft_path"])
    for document in ["README.md", "REPRODUCE.md"]:
        assert f"]({quote(name, safe='/')})" in (draft / document).read_text(encoding="utf-8")
