import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from labhq import cli
from labhq.gateway.server import create_app
from labhq.result_storage import ResultStorage, STORAGE_QUESTION
from labhq.settings import Settings
from tests.test_late_github_688 import github, publish_card
from tests.test_research_cp4 import draft_hub, target


def make(tmp_path, monkeypatch, *, create=422):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.gateway.client_token = "test-token"
    calls = []
    app = create_app(settings, github_transport=github(calls, create=create))
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "text": "Public study", "report": "Result"}
    hub.agents["editor"] = {}
    editor = AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "# Edited summary"}))
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    monkeypatch.setattr(hub.result_storage, "upload", AsyncMock())
    return app, hub, calls, editor


async def finish_request(hub):
    hub.requests["r"]["status"] = "done"
    hub.save_request("r")


async def end_card(client, hub):
    reply = await client.post("/api/requests/r/review-done")
    assert reply.status_code == 200
    for _ in range(100):
        card = next((entry["approval"] for entry in hub.approvals.values()
                     if entry["approval"]["kind"] == "result_storage"), None)
        if card:
            return card
        await asyncio.sleep(0.01)
    raise AssertionError("no end storage card")


async def test_local_plan_choice_can_change_to_github_only_after_end_answer(tmp_path, monkeypatch):
    app, hub, calls, editor = make(tmp_path, monkeypatch)
    await hub.result_storage.select("r", {"mode": "local"})
    await finish_request(hub)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        card = await end_card(client, hub)
        assert card["summary"] == STORAGE_QUESTION
        assert card["detail"]["storage"]["initial_label"] == "로컬"
        assert card["detail"]["storage"]["default"] == "keep"
        editor.assert_not_awaited()
        assert not hub.requests["r"]["result_storage"].get("draft_path")
        answer = await client.post(f"/api/approvals/{card['id']}", json={
            "approved": True, "storage": {"mode": "new", "repo": "study", "visibility": "private"}})
        assert answer.status_code == 200
    publish = await publish_card(hub)
    assert editor.await_count == 1
    assert ("POST", "/user/repos", "{}") in calls
    assert publish["detail"]["repo"] == "owner/study"
    await hub.result_storage.close()


async def test_github_plan_choice_can_change_to_local_without_repository_probe(tmp_path, monkeypatch):
    app, hub, calls, editor = make(tmp_path, monkeypatch)
    await hub.result_storage.select("r", {"mode": "new", "repo": "study", "visibility": "private"})
    calls.clear()
    await finish_request(hub)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        card = await end_card(client, hub)
        assert "owner/study" in card["detail"]["storage"]["initial_label"]
        assert (await client.post(f"/api/approvals/{card['id']}", json={
            "approved": True, "storage": {"mode": "local"}})).status_code == 200
    await hub.result_storage.tasks["r"]
    assert hub.requests["r"]["result_storage"]["status"] == "local"
    assert not any(method == "POST" and path == "/user/repos" for method, path, _ in calls)
    assert not [entry for entry in hub.approvals.values() if entry["approval"]["kind"] == "result_publish"]
    editor.assert_awaited_once()
    await hub.result_storage.close()


async def test_keep_restores_the_complete_initial_choice_for_upload(tmp_path, monkeypatch):
    app, hub, calls, editor = make(tmp_path, monkeypatch)
    gh = AsyncMock()
    gh.repo_info.return_value = {"private": True, "permissions": {"push": True}, "default_branch": "main"}
    gh.repository_is_empty.return_value = False
    hub.result_storage.client = lambda: gh
    initial = {"mode": "existing", "repo": "owner/study", "visibility": "private",
               "allow_public": False, "branch": "release"}
    hub.requests["r"].update(status="done", pi_reviewed_at=1,
                             result_storage={**initial, "status": "selected"},
                             storage_end_ask={"state": "pending", "initial": initial})

    saved = await hub.result_storage.select("r", {"mode": "keep"}, end=True)
    await ResultStorage.upload(hub.result_storage, "r", {"README.md": "# Result"})

    assert saved["branch"] == "release"
    assert gh.put_file.await_args.args[-1] == "release"
    await hub.result_storage.close()


async def test_missing_create_permission_falls_back_to_local_edition(tmp_path, monkeypatch):
    app, hub, calls, editor = make(tmp_path, monkeypatch, create=403)
    await hub.result_storage.select("r", {"mode": "local"})
    await finish_request(hub)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        card = await end_card(client, hub)
        assert (await client.post(f"/api/approvals/{card['id']}", json={
            "approved": True, "storage": {"mode": "new", "repo": "study"}})).status_code == 200
    await hub.result_storage.tasks["r"]
    saved = hub.requests["r"]["result_storage"]
    assert saved["mode"] == saved["status"] == "local"
    assert "빠진 권한" in saved["warning"] and "labhq init" in saved["warning"]
    assert ("POST", "/user/repos", "{}") in calls
    editor.assert_awaited_once()
    await hub.result_storage.close()


@pytest.mark.parametrize("status, storage_status", [
    ("done", "published"), ("failed", "selected"), ("cancelled", "selected"),
])
async def test_published_or_failed_request_has_no_end_question(tmp_path, monkeypatch, status, storage_status):
    app, hub, calls, editor = make(tmp_path, monkeypatch)
    hub.requests["r"].update(status=status, result_storage={"mode": "new", "repo": "owner/study",
                                                                    "status": storage_status},
                             storage_end_ask={"state": "pending", "initial": {"mode": "new",
                                                                                "repo": "owner/study"}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        assert (await client.post("/api/requests/r/review-done")).status_code == 200
    await asyncio.sleep(0)
    assert not [entry for entry in hub.approvals.values() if entry["approval"]["kind"] == "result_storage"]
    if storage_status == "published":
        editor.assert_not_awaited()
    await hub.result_storage.close()


async def test_request_reviewed_before_upgrade_has_no_end_question(tmp_path, monkeypatch):
    app, hub, calls, editor = make(tmp_path, monkeypatch)
    hub.requests["r"].update(status="done", pi_reviewed_at=1,
                             result_storage={"mode": "local", "status": "local"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        assert (await client.post("/api/requests/r/review-done")).status_code == 200
    assert not hub.approvals
    editor.assert_not_awaited()
    await hub.result_storage.close()


def test_cli_review_done_can_answer_the_end_question(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(Settings, "load", lambda *args, **kwargs: Settings())
    monkeypatch.setattr(cli, "_api", lambda settings, method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"pi_reviewed_at": 1, "result_storage": {"mode": "new"}})
    cli.main(["review-done", "r", "--storage", "github", "--name", "study", "--visibility", "public"])
    assert calls == [("POST", "/api/requests/r/review-done", {"json": {
        "storage": "github", "name": "study", "visibility": "public"}})]
    assert "저장 위치" in capsys.readouterr().out


@pytest.mark.parametrize("storage", ["github", "local"])
async def test_cp4_review_done_keeps_storage_answer_while_bundle_refreshes(tmp_path, monkeypatch, storage):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    fake = await draft_hub()
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.gateway.client_token = "test-token"
    calls = []
    app = create_app(settings, github_transport=github(calls))
    hub = app.state.hub
    hub.requests["r"] = fake.requests["r"]
    await hub.result_storage.select("r", {"mode": "local"})
    hub.agents["editor"] = {}
    editor = AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "# Edited summary"}))
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    monkeypatch.setattr(hub.result_storage, "upload", AsyncMock())
    monkeypatch.setattr(hub, "_has_bundle_workdir", lambda _rid: True)
    monkeypatch.setattr(hub, "_bundle_outcome", lambda *_args: {"warning": "test bundle refreshed"})
    body = {**target(hub.requests["r"]), "storage": storage}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        reply = await client.post("/api/requests/r/review-done", json=body)

    assert reply.status_code == 200
    assert hub.requests["r"]["storage_end_ask"]["state"] == "answered"
    for _ in range(100):
        if not hub.requests["r"].get("bundle_pending"):
            break
        await asyncio.sleep(0.01)
    assert not hub.requests["r"].get("bundle_pending")
    if storage == "github":
        card = await publish_card(hub)
        assert card["detail"]["repo"] == "owner/labhq-r"
    else:
        await hub.result_storage.tasks["r"]
        assert hub.requests["r"]["result_storage"]["status"] == "local"
    editor.assert_awaited_once()
    await hub.result_storage.close()
