import asyncio
import base64
import json
import shutil
from pathlib import Path

import httpx
import pytest

from labhq.settings import Settings, ProjectSettings
from labhq.result_storage import ResultStorage, clean_document, draft_documents


class Hub:
    def __init__(self, tmp_path, transport=None):
        self.s = Settings()
        self.s.runner.workspace_root = str(tmp_path / "runs")
        self.requests = {"req_test": {"id": "req_test", "text": "Compare public studies",
                                     "intake": {"work_kind": "research"}}}
        self.agents = {}
        self.transport = transport
        self.saved = []

    def save_request(self, rid):
        self.saved.append(rid)

    async def publish(self, event):
        pass


async def test_card_conditions_and_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    hub = Hub(tmp_path)
    service = ResultStorage(hub)
    card = await service.card("req_test")
    assert card["default"] == "local"
    assert "GITHUB_TOKEN" in card["warning"]
    assert not card["github_available"]
    hub.requests["req_test"]["intake"] = {"work_kind": "general"}
    assert await service.card("req_test") is None
    hub.s.github.ask_storage = True
    assert await service.card("req_test")
    await service.select("req_test", {"mode": "new", "repo": "study", "visibility": "private"})
    assert hub.requests["req_test"]["result_storage"]["mode"] == "local"
    assert await service.card("req_test") is None


async def test_project_skips_question_and_inherits_choice(tmp_path):
    hub = Hub(tmp_path)
    hub.s.projects = [ProjectSettings(id="study", repo="owner/study")]
    hub.requests["req_test"]["project_id"] = "study"
    service = ResultStorage(hub)
    assert await service.card("req_test") is None
    assert hub.requests["req_test"]["result_storage"]["repo"] == "owner/study"
    hub.requests["req_next"] = {"id": "req_next", "project_id": "study"}
    assert await service.card("req_next") is None
    assert hub.requests["req_next"]["result_storage"]["repo"] == "owner/study"


def fake_github(calls, *, forbidden=False, exists=False):
    def handle(req):
        body = json.loads(req.content) if req.content else {}
        calls.append((req.method, req.url.path, body))
        if forbidden:
            return httpx.Response(403, json={"message": "forbidden"})
        if req.url.path == "/user":
            return httpx.Response(200, json={"login": "owner"})
        if req.url.path == "/user/repos":
            return httpx.Response(201 if req.method == "POST" else 200,
                                  json={"full_name": "owner/study", "private": True,
                                        "default_branch": "main"} if req.method == "POST" else [])
        if "/contents/" in req.url.path:
            return httpx.Response(404 if req.method == "GET" else 201,
                                  json={} if req.method == "GET" else {"content": {"html_url": "https://github.com/owner/study"}})
        return httpx.Response(200 if exists or any(c[0] == "POST" for c in calls) else 404,
                              json={"full_name": "owner/study", "private": True,
                                    "permissions": {"push": True}, "default_branch": "main"})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize("exists", [False, True])
async def test_new_repo_and_upload_only_after_review(tmp_path, monkeypatch, exists):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    calls = []
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github(calls, exists=exists))
    await service.select("req_test", {"mode": "new", "repo": "study", "visibility": "private"})
    assert not any(method in {"POST", "PUT"} for method, _, _ in calls)
    hub.requests["req_test"].update(status="done", report="Observed difference; mechanism remains unknown.")
    async def approve(**kw):
        assert kw["kind"] == "result_publish"
        assert "README.md" in kw["detail"]["documents"]
        assert not any(c[0] == "PUT" for c in calls)
        return {"approved": True}
    hub.request_approval = approve
    await service.finish("req_test")
    posts = [c for c in calls if c[0] == "POST"]
    assert len(posts) == (0 if exists else 1)
    if posts:
        assert posts[0][2]["private"] is True
    files = {path.split("/contents/")[1]: base64.b64decode(body["content"]).decode()
             for method, path, body in calls if method == "PUT"}
    assert any(path.endswith("README.md") for path in files)
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    await service.close()


async def test_denied_permissions_keep_local(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    service = ResultStorage(Hub(tmp_path), fake_github([], forbidden=True))
    card = await service.card("req_test")
    assert not card["github_available"] and "권한" in card["warning"]
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    assert service.hub.requests["req_test"]["result_storage"]["mode"] == "local"
    await service.close()


def test_editor_documents_remove_secrets_and_personal_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-private-token")
    hub = Hub(tmp_path)
    req = hub.requests["req_test"]
    req["report"] = "Result. synthetic-private-token C:/Users/alice/project/a.txt /home/alice/result.csv"
    docs = draft_documents(req, hub.s)
    assert {"README.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md", "report.md"} <= docs.keys()
    joined = "\n".join(docs.values())
    assert "synthetic-private-token" not in joined
    assert "alice" not in joined
    assert "report.md" in docs["README.md"]
    table = "gene\tvalue\n" + "A\t2\n" * 20_000
    assert clean_document(table, hub.s, req) == table  # files are not GitHub comments; never truncate rows


async def test_clarify_and_storage_share_one_card(tmp_path, monkeypatch):
    from labhq.orchestrator.cso import Orchestrator
    from labhq.gateway.server import Hub as RealHub, RequestIn
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = RealHub(s)
    rid = hub.create_request(RequestIn(text="research question", work_kind="research"))
    if isinstance(rid, dict):
        rid = rid["request_id"]
    hub.requests[rid]["intake"] = {"work_kind": "research"}
    calls = []
    async def answer(**kw):
        calls.append(kw)
        return {"approved": True, "note": "public studies", "storage": {"mode": "local"}}
    monkeypatch.setattr(hub, "request_approval", answer)
    monkeypatch.setattr(hub.orchestrator, "_emit", lambda *a, **k: asyncio.sleep(0))
    status, _ = await hub.orchestrator._plan_clarification(rid, {"clarifying_questions": ["Which studies?"]})
    assert status == "answered" and len(calls) == 1
    assert "storage" in calls[0]["detail"]
    assert hub.requests[rid]["result_storage"]["mode"] == "local"


async def test_mock_request_reaches_editor_and_fake_github(tmp_path, monkeypatch):
    import uvicorn
    from labhq.gateway.server import create_app, RequestIn
    from labhq.runner.daemon import Runner
    from labhq.util import free_port
    from tests.test_github_reporter import _until
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    shutil.copytree(Path(__file__).resolve().parents[1] / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.gateway.port = free_port()
    s.gateway.url = f"ws://127.0.0.1:{s.gateway.port}"
    s.runner.broker_port = free_port()
    s.runner.force_engine, s.runner.job_poll_s = "mock", 1
    s.runner.workspace_root, s.runner.talent_dir = str(tmp_path / "runs"), str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.hpc.scheduler = "mock"
    s.github.ask_storage = True
    calls, cards = [], []
    app = create_app(s, github_transport=fake_github(calls))
    hub = app.state.hub
    publish = hub.publish
    async def tap(ev, **kwargs):
        await publish(ev, **kwargs)
        if ev.get("type") == "approval.requested":
            card = ev["data"]
            cards.append(card)
            storage = {"mode": "new", "repo": "study", "visibility": "private"} if card.get("detail", {}).get("storage") else None
            asyncio.create_task(hub.resolve_approval(card["id"], True, "ok", storage=storage))
    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=s.gateway.port, log_level="warning"))
    jobs = [asyncio.create_task(server.serve())]
    runner = Runner(s)
    try:
        await _until(lambda: server.started, 10)
        jobs.append(asyncio.create_task(runner.run_forever()))
        await _until(lambda: "editor" in hub.agents, 15)
        rid = hub.create_request(RequestIn(text="Compare public studies"))
        await _until(lambda: (hub.requests[rid].get("result_storage") or {}).get("status") in {"published", "failed"}, 60)
        saved = hub.requests[rid]["result_storage"]
        assert saved["status"] == "published", saved
        assert Path(saved["draft_path"], "README.md").is_file()
        assert [c["kind"] for c in cards].count("result_storage") == 1
        assert [c["kind"] for c in cards].count("result_publish") == 1
        assert any(t.get("payload", {}).get("agent_id") == "editor" and t.get("completed")
                   for t in hub.store.all("task").values())
        assert any(method == "PUT" for method, _, _ in calls)
    finally:
        await hub.result_storage.close()
        runner.stop()
        server.should_exit = True
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


async def test_decline_and_restricted_request_never_upload(tmp_path, monkeypatch):
    from labhq.settings import DataZone
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    calls = []
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    hub.requests["req_test"].update(status="done", report="result")
    async def decline(**kw):
        return {"approved": False}
    hub.request_approval = decline
    await service.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "declined"
    hub.s.policy.data_zones = [DataZone(path="/controlled", level="restricted")]
    hub.requests["req_test"]["text"] = "Analyze /controlled/cohort.tsv"
    await service.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "local"
    assert not any(c[0] in {"POST", "PUT"} for c in calls)
    await service.close()


def test_assets_only_reported_outputs_and_clean_text(tmp_path):
    from labhq.result_storage import edition_assets
    hub = Hub(tmp_path)
    bundle = Path(hub.s.runner.workspace_root) / "requests/req_test"
    output = bundle / "steps/s1/outputs"
    output.mkdir(parents=True)
    (output / "table.tsv").write_text("gene\tvalue\nA\t2\n", encoding="utf-8")
    (output / "plot.png").write_bytes(b"synthetic-image")
    (output / "secret.txt").write_text("raw input", encoding="utf-8")
    (bundle / "MANIFEST.tsv").write_text("relative_path\tstatus\nsteps/s1/outputs/table.tsv\tcopied\n"
        "steps/s1/outputs/plot.png\tcopied\nsteps/s1/outputs/secret.txt\tcopied (unreported)\n", encoding="utf-8")
    text, images = edition_assets(hub.requests["req_test"], hub.s)
    assert list(text) == ["steps/s1/outputs/table.tsv"]
    assert list(images) == ["steps/s1/outputs/plot.png"]
