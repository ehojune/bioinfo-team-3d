"""A request that ended local can go to GitHub later (#688, PI request 2026-10-16).

On 10-16 there was no way: the late card refused a request with a storage choice, so the lead copied the edition and
pushed it by hand. `labhq storage <id> --to github` checks the token first (GET /user, an empty POST /user/repos that
creates nothing), then reruns the editor for a repository layout; the publish card still asks before any upload."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from labhq import cli, github_token
from labhq.gateway.server import create_app
from labhq.result_storage import STORAGE_QUESTION
from labhq.settings import Settings


def github(calls, *, user=200, create=422):
    def handle(request):
        calls.append((request.method, request.url.path, request.content.decode() or None))
        if request.url.path == "/user":
            return httpx.Response(user, json={"login": "owner"} if user == 200 else {"message": "Bad credentials"})
        if request.url.path == "/user/repos" and request.method == "POST":
            return httpx.Response(create, json={"message": "New repository name must not be blank"}
                                  if create == 422 else {"message": "Resource not accessible by personal access token"})
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(handle)


def make(tmp_path, monkeypatch, *, token="fake-token-for-tests", reviewed=True, storage=None, **routes):
    if token:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.gateway.client_token = "test-token"
    calls = []
    app = create_app(s, github_transport=github(calls, **routes))
    hub = app.state.hub
    req = {"id": "r", "status": "done", "text": "Public study", "report": "Result",
           "result_storage": storage if storage is not None else {"mode": "local", "status": "local"}}
    if reviewed:
        req["pi_reviewed_at"] = 1
    hub.requests["r"] = req
    hub.agents["editor"] = {}
    editor = AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "# Edited summary"}))
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    upload = AsyncMock()
    monkeypatch.setattr(hub.result_storage, "upload", upload)
    return app, hub, calls, editor, upload


async def publish_card(hub):
    for _ in range(300):
        card = next((e["approval"] for e in hub.approvals.values() if e["approval"]["kind"] == "result_publish"), None)
        if card:
            return card
        await asyncio.sleep(0.01)
    raise AssertionError("no publish card")


async def test_a_local_request_goes_to_a_new_repository_after_the_publish_card(tmp_path, monkeypatch):
    app, hub, calls, editor, upload = make(tmp_path, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/requests/r/storage/to-github", json={})).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.post("/api/requests/missing/storage/to-github", json={})).status_code == 404
        reply = await client.post("/api/requests/r/storage/to-github", json={"name": "sex-ratio-study"})
    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "converting" and body["repo"] == "owner/sex-ratio-study" and body["waiting_review"] is False
    saved = hub.requests["r"]["result_storage"]
    assert (saved["mode"], saved["visibility"], saved["converted_from"]) == ("new", "private", "local")
    # The token was checked with probes that create nothing: the repository POST carried no name.
    assert ("POST", "/user/repos", "{}") in calls
    card = await publish_card(hub)
    assert card["detail"]["repo"] == "owner/sex-ratio-study" and editor.await_count == 1
    upload.assert_not_awaited()  # nothing goes up before the PI answers the card
    await hub.resolve_approval(card["id"], True)
    await hub.result_storage.tasks["r"]
    assert hub.requests["r"]["result_storage"]["status"] == "published" and upload.await_count == 1
    await hub.result_storage.close()


async def test_a_branch_inherited_from_a_project_is_not_reused_for_the_new_repository(tmp_path, monkeypatch):
    app, hub, *_ = make(tmp_path, monkeypatch, storage={"mode": "local", "status": "local", "repo": "lab/project",
                                                         "branch": "reports"})
    await hub.result_storage.to_github("r", "study")
    saved = hub.requests["r"]["result_storage"]
    assert saved["repo"] == "owner/study" and "branch" not in saved
    await hub.result_storage.close()


def test_the_planning_card_asks_the_same_question():
    from pathlib import Path
    cso = (Path(__file__).resolve().parents[1] / "labhq" / "orchestrator" / "cso.py").read_text(encoding="utf-8")
    assert f'summary="{STORAGE_QUESTION}"' in cso and "이 결과를 어디에 남길까요?" not in cso


async def test_an_unreviewed_request_waits_for_review_done(tmp_path, monkeypatch):
    app, hub, calls, editor, upload = make(tmp_path, monkeypatch, reviewed=False)
    reply = await hub.result_storage.to_github("r")
    assert reply["waiting_review"] is True and reply["repo"] == "owner/labhq-r"
    await asyncio.sleep(0.05)
    editor.assert_not_awaited()
    assert "r" not in hub.result_storage.tasks or hub.result_storage.tasks["r"].done()
    await hub.result_storage.close()


@pytest.mark.parametrize("storage, text", [
    ({}, "저장 위치 카드"),
    ({"mode": "new", "repo": "owner/x", "status": "published"}, "로컬에 남긴 정리본만"),
    ({"mode": "existing", "repo": "owner/x", "status": "failed"}, "--republish"),
    ({"mode": "local", "status": "selected"}, "로컬에 남긴 정리본만"),
])
async def test_only_a_finished_local_edition_moves(tmp_path, monkeypatch, storage, text):
    app, hub, calls, *_ = make(tmp_path, monkeypatch, storage=storage)
    if not storage:
        hub.requests["r"].pop("result_storage")
    with pytest.raises(ValueError, match=text):
        await hub.result_storage.to_github("r")
    assert not calls  # refused before GitHub is asked anything
    await hub.result_storage.close()


async def test_a_bad_name_or_visibility_is_refused(tmp_path, monkeypatch):
    app, hub, *_ = make(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="영문"):
        await hub.result_storage.to_github("r", "../escape")
    with pytest.raises(ValueError, match="공개 범위"):
        await hub.result_storage.to_github("r", "ok", "internal")
    assert hub.requests["r"]["result_storage"] == {"mode": "local", "status": "local"}
    await hub.result_storage.close()


async def test_without_a_token_it_says_how_to_add_one_and_what_it_needs(tmp_path, monkeypatch):
    app, hub, calls, *_ = make(tmp_path, monkeypatch, token=None)
    with pytest.raises(ValueError) as refused:
        await hub.result_storage.to_github("r")
    assert "labhq init" in str(refused.value) and "Issues 쓰기" in str(refused.value) and not calls
    assert hub.requests["r"]["result_storage"]["status"] == "local"
    await hub.result_storage.close()


@pytest.mark.parametrize("routes, text", [({"user": 401}, "토큰을 받지 않았습니다"),
                                          ({"create": 403}, "저장소를 만들 수 없습니다")])
async def test_a_token_that_cannot_create_repositories_leaves_the_request_local(tmp_path, monkeypatch, routes, text):
    app, hub, calls, editor, _ = make(tmp_path, monkeypatch, **routes)
    with pytest.raises(ValueError, match=text):
        await hub.result_storage.to_github("r")
    assert hub.requests["r"]["result_storage"] == {"mode": "local", "status": "local"}
    editor.assert_not_awaited()
    await hub.result_storage.close()


async def test_a_controlled_request_is_never_moved(tmp_path, monkeypatch):
    app, hub, calls, *_ = make(tmp_path, monkeypatch)
    monkeypatch.setattr("labhq.result_storage.controlled_request", lambda req, settings: True)
    with pytest.raises(ValueError, match="통제 데이터"):
        await hub.result_storage.to_github("r")
    assert not calls
    await hub.result_storage.close()


async def test_a_fine_grained_token_gets_the_repository_access_reminder(tmp_path, monkeypatch):
    app, hub, *_ = make(tmp_path, monkeypatch, token="github_pat_" + "Z9" * 12)
    reply = await hub.result_storage.to_github("r")
    assert "All repositories" in reply["note"] and "github_pat_" not in json.dumps(reply)
    await hub.result_storage.close()


async def test_the_card_asks_the_pi_question_and_says_how_to_publish_later(tmp_path, monkeypatch):
    app, hub, *_ = make(tmp_path, monkeypatch, token=None, storage={})
    hub.requests["r"].pop("result_storage")
    card = await hub.result_storage.open_card("r")
    assert card["summary"] == STORAGE_QUESTION == "결과를 GitHub 저장소로 정리할까요?"
    warning = card["detail"]["storage"]["warning"]
    assert "labhq init" in warning and "labhq storage r --to github" in warning and github_token.NEEDED_TEXT in warning
    await hub.result_storage.close()


def test_cli_to_github(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_api", lambda s, method, path, **kw: calls.append((method, path, kw.get("json"))) or
                        {"request_id": "req_x", "status": "converting", "repo": "owner/study", "waiting_review": True,
                         "note": "fine-grained 토큰이면 Repository access가 All repositories여야 새 저장소에 올릴 수 있습니다."})
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: Settings()))
    cli.main(["storage", "req_x", "--to", "github", "--name", "study"])
    assert calls == [("POST", "/api/requests/req_x/storage/to-github", {"name": "study", "visibility": "private"})]
    out = capsys.readouterr().out
    assert "owner/study로 옮깁니다" in out and "labhq review-done" in out and "All repositories" in out
    with pytest.raises(SystemExit):  # one action at a time
        cli.main(["storage", "req_x", "--to", "github", "--republish"])
    assert len(calls) == 1
