"""Publish retry (#607): a failed upload is retried from the local draft, a refused token says what to change, and doctor
warns about a fine-grained token. Mock editor, mock GitHub and no process: nothing here reaches the network."""
import asyncio
import base64
import json
import logging
import subprocess
from pathlib import Path

import httpx
import pytest

from labhq import cli, doctor, github_token
from labhq.result_storage import (PublishAccessError, ResultStorage, create_denied_text, upload_failed_text,
                                  write_denied_text)
from labhq.settings import DataZone, Settings
from tests.test_doctor import _settings as doctor_settings
from tests.test_edition_quality_607 import publish_card, redraft_hub
from tests.test_result_storage import Hub

TOKEN = "fake-token-XYZ-never-print"


# ----- republish: a failed upload opens the publish card again from the local draft -----

async def failed_edition(tmp_path, monkeypatch, replies=("# 검토한 정리본",), app=False):
    """The PI approved the card and the upload failed: status `failed`, the reviewed draft still on disk."""
    made = redraft_hub(tmp_path, monkeypatch, list(replies), app=app)
    hub, upload = made[0], made[2]
    upload.side_effect = [RuntimeError("boom"), None]
    hub.result_storage.schedule("req_test")
    card = await publish_card(hub)
    await hub.resolve_approval(card["id"], True)
    await hub.result_storage.tasks["req_test"]
    assert hub.requests["req_test"]["result_storage"]["status"] == "failed"
    return (*made, card)


async def test_a_failed_upload_is_retried_from_the_local_draft_without_the_editor(tmp_path, monkeypatch):
    hub, editor, upload, first = await failed_edition(tmp_path, monkeypatch)
    service, saved = hub.result_storage, hub.requests["req_test"]["result_storage"]
    assert saved["error_type"] == "RuntimeError" and "labhq storage req_test --republish" in saved["warning"]
    (Path(saved["draft_path"]) / "kept.txt").write_text("로컬 초안에만 있는 파일", encoding="utf-8")  # a redraft would drop it

    reply = await service.republish("req_test")
    assert reply == {"request_id": "req_test", "status": "republishing", "closed_cards": []}
    second = await publish_card(hub, not_id=first["id"])
    documents = second["detail"]["documents"]
    assert "검토한 정리본" in documents["README.md"] and documents["kept.txt"] == "로컬 초안에만 있는 파일"
    assert editor.await_count == 1  # the editor did not run again
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "draft" and "warning" not in saved and "error_type" not in saved
    assert upload.await_count == 1  # nothing is uploaded before the PI answers the new card

    await hub.resolve_approval(second["id"], True)
    await service.tasks["req_test"]
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    assert upload.await_count == 2 and upload.await_args.args[1]["README.md"].startswith("# 검토한 정리본")
    await service.close()


async def test_redraft_is_allowed_after_a_failed_upload_and_runs_the_editor_again(tmp_path, monkeypatch):
    hub, editor, upload, first = await failed_edition(tmp_path, monkeypatch, replies=("# 첫 정리본", "# 다시 만든 정리본"))
    reply = await hub.result_storage.redraft("req_test")
    assert reply["status"] == "redrafting"
    second = await publish_card(hub, not_id=first["id"])
    assert "다시 만든 정리본" in second["detail"]["documents"]["README.md"]
    assert editor.await_count == 2
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft"
    await hub.result_storage.close()


async def test_republish_refuses_what_is_not_a_failed_reviewed_uncontrolled_edition_with_a_draft(tmp_path, monkeypatch):
    hub, editor, _ = redraft_hub(tmp_path, monkeypatch, [])
    service, req = hub.result_storage, hub.requests["req_test"]
    saved = req["result_storage"]

    saved["status"] = "published"
    with pytest.raises(ValueError, match="이미 게시한 정리본은 다시 올릴 수 없습니다"):
        await service.republish("req_test")
    for status in ("selected", "local", "draft", "declined"):
        saved["status"] = status
        with pytest.raises(ValueError, match="게시에 실패한 정리본만") as refused:
            await service.republish("req_test")
        assert "--redraft" in str(refused.value)

    saved["status"] = "failed"
    with pytest.raises(ValueError, match="다시 올릴 로컬 정리본이 없습니다") as refused:  # no draft was ever saved
        await service.republish("req_test")
    assert "labhq storage req_test --redraft" in str(refused.value)
    saved["draft_path"] = str(tmp_path / "gone")  # saved, but the folder is not there
    with pytest.raises(ValueError, match="다시 올릴 로컬 정리본이 없습니다"):
        await service.republish("req_test")

    req["status"] = "running"
    with pytest.raises(ValueError, match="끝난 요청만"):
        await service.republish("req_test")
    req["status"] = "done"

    reviewed = req.pop("pi_reviewed_at")  # an unreviewed request
    with pytest.raises(ValueError, match="보고서 검토 완료"):
        await service.republish("req_test")
    req["pi_reviewed_at"] = reviewed
    req["bundle_pending"] = True  # reviewed, but the bundle is still being rebuilt
    with pytest.raises(ValueError, match="묶음 갱신"):
        await service.republish("req_test")
    del req["bundle_pending"]

    zones, text = hub.s.policy.data_zones, req["text"]
    hub.s.policy.data_zones = [DataZone(path="/controlled", level="restricted")]
    req["text"] = "Analyze /controlled/cohort.tsv"
    with pytest.raises(ValueError, match="통제 데이터"):
        await service.republish("req_test")
    hub.s.policy.data_zones, req["text"] = zones, text

    service.uploading.add("req_test")  # an upload that is going right now
    with pytest.raises(ValueError, match="게시가 진행 중"):
        await service.republish("req_test")
    assert editor.await_count == 0 and saved["status"] == "failed" and "req_test" not in service.tasks


async def test_republish_waits_for_the_lock_a_redraft_and_a_card_answer_share(tmp_path, monkeypatch):
    hub, editor, upload, first = await failed_edition(tmp_path, monkeypatch)
    service = hub.result_storage
    async with service.card_lock:
        retry = asyncio.create_task(service.republish("req_test"))
        await asyncio.sleep(0.05)
        assert not retry.done() and hub.requests["req_test"]["result_storage"]["status"] == "failed"
    assert (await retry)["status"] == "republishing"
    await publish_card(hub, not_id=first["id"])
    await service.close()


async def test_republish_api_and_cli(tmp_path, monkeypatch, capsys):
    hub, editor, upload, app, first = await failed_edition(tmp_path, monkeypatch, app=True)
    hub.requests["req_pub"] = {**hub.requests["req_test"], "id": "req_pub",
                               "result_storage": {"mode": "existing", "repo": "o/r", "status": "published"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/requests/req_test/storage/republish")).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.post("/api/requests/missing/storage/republish")).status_code == 404
        refused = await client.post("/api/requests/req_pub/storage/republish")
        assert refused.status_code == 409 and "이미 게시한 정리본" in refused.json()["detail"]
        ok = await client.post("/api/requests/req_test/storage/republish")
        assert ok.status_code == 200 and ok.json()["status"] == "republishing"
    card = await publish_card(hub, not_id=first["id"])
    assert "검토한 정리본" in card["detail"]["documents"]["README.md"] and editor.await_count == 1
    await hub.result_storage.close()

    calls = []
    monkeypatch.setattr(cli, "_api", lambda s, method, path, **kw: calls.append((method, path)) or
                        {"request_id": "req_x", "status": "republishing", "closed_cards": []})
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: hub.s))
    cli.main(["storage", "req_x", "--republish"])
    assert calls == [("POST", "/api/requests/req_x/storage/republish")]
    assert "로컬 정리본으로 다시 올립니다" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # one of the two, not both
        cli.main(["storage", "req_x", "--republish", "--redraft"])
    assert len(calls) == 1


# ----- plain-Korean permission errors from upload() -----

def fake_github(calls, *, repo=200, create=201, get_file=404, put=201, put_headers=None, empty=False):
    """GitHub as a fine-grained token sees it. `repo`, `create`, `get_file` and `put` are the status of
    GET /repos/owner/study, POST /user/repos, GET and PUT of a file."""
    def handle(req):
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        path = req.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "owner"})
        if path == "/user/repos":
            return httpx.Response(create, json={"message": "Resource not accessible by personal access token"}
                                  if create >= 400 else {"full_name": "owner/study", "private": True, "default_branch": "main"})
        if path == "/repos/owner/study":
            return httpx.Response(repo, json={"message": "Not Found"} if repo >= 400 else
                                  {"full_name": "owner/study", "private": True, "permissions": {"push": True},
                                   "default_branch": "main"})
        if path == "/repos/owner/study/contents/" and req.method == "GET":
            return httpx.Response(404, json={"message": "Git Repository is empty."}) if empty else httpx.Response(200, json=[])
        if "/contents/" in path and req.method == "GET":
            return httpx.Response(get_file, json={"message": "no such file" if get_file == 404 else "Forbidden"})
        if "/contents/" in path:
            return httpx.Response(put, headers=put_headers or {}, json={"message": "Resource not accessible by personal access token"}
                                  if put >= 400 else {"content": {"html_url": "https://github.com/owner/study"}})
        raise AssertionError(f"unexpected request {req.method} {path}")
    return httpx.MockTransport(handle)


NEW = {"mode": "new", "repo": "owner/study", "visibility": "private", "allow_public": False}
EXISTING = {"mode": "existing", "repo": "owner/study", "visibility": "private", "allow_public": False}


async def run_finish(tmp_path, monkeypatch, selection, transport):
    """The upload through finish(): the stored `result_storage` is what the web office and `labhq status` show."""
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    hub = Hub(tmp_path)
    service = ResultStorage(hub, transport)
    hub.requests["req_test"].update(status="done", pi_reviewed_at=1, report="Observed difference.",
                                    result_storage={**selection, "status": "selected"})

    async def approve(**kw):
        return {"approved": True}

    hub.request_approval = approve
    await service.finish("req_test")
    await service.close()
    return hub, hub.requests["req_test"]["result_storage"]


def assert_no_token(hub, caplog, calls):
    assert any(auth == f"Bearer {TOKEN}" for _, _, auth in calls)  # the token was in use, so its absence below means something
    assert TOKEN not in json.dumps(hub.requests, ensure_ascii=False) and TOKEN not in caplog.text


async def test_a_token_that_cannot_create_a_repository_says_how_to_make_one(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    hub, saved = await run_finish(tmp_path, monkeypatch, NEW, fake_github(calls, repo=404, create=403))
    assert saved["status"] == "failed" and saved["error_type"] == "PublishAccessError"
    warning = saved["warning"]
    assert "새 저장소를 만들 수 없습니다" in warning and "fine-grained" in warning
    assert "$env:GITHUB_TOKEN=$null; gh repo create owner/study --private --add-readme" in warning
    assert "env -u GITHUB_TOKEN -u GH_TOKEN gh repo create owner/study --private --add-readme" in warning
    assert "GitHub 웹" in warning and "README" in warning
    assert "Contents: Read and write" in warning and "labhq storage req_test --republish" in warning
    assert not any(method == "PUT" for method, _, _ in calls)
    assert_no_token(hub, caplog, calls)


def test_the_create_command_follows_the_chosen_visibility():
    assert "gh repo create owner/study --public --add-readme" in create_denied_text("owner/study", "req_a", "public")
    assert "gh repo create owner/study --private --add-readme" in create_denied_text("owner/study", "req_a", "private")
    assert "labhq storage req_a --republish" in write_denied_text("owner/study", "req_a")


async def test_an_empty_repository_is_bootstrapped_without_a_branch_then_uses_the_default_branch(tmp_path, monkeypatch):
    requests = []

    def handle(req):
        body = json.loads(req.content) if req.content else None
        requests.append((req.method, req.url.path, dict(req.url.params), body))
        if req.url.path == "/repos/owner/study":
            return httpx.Response(200, json={"private": True, "default_branch": "main"})
        if req.url.path == "/repos/owner/study/contents/":
            return httpx.Response(404, json={"message": "Git Repository is empty."})
        if req.method == "GET" and "/contents/" in req.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "PUT" and "/contents/" in req.url.path:
            return httpx.Response(201, json={"content": {"html_url": "https://example.test/file"}})
        raise AssertionError(f"unexpected request {req.method} {req.url.path}")

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    hub = Hub(tmp_path)
    hub.requests["req_test"].update(pi_reviewed_at=1, result_storage={**EXISTING, "status": "draft"})
    service = ResultStorage(hub, httpx.MockTransport(handle))
    await service.upload("req_test", {"README.md": "first", "report.md": "second"})
    puts = [entry for entry in requests if entry[0] == "PUT"]
    assert "branch" not in puts[0][3]
    assert puts[1][3]["branch"] == "main"
    await service.close()


async def test_an_initialized_repository_updates_an_existing_file_with_its_sha(tmp_path, monkeypatch):
    puts = []

    def handle(req):
        if req.url.path == "/repos/owner/study":
            return httpx.Response(200, json={"private": True, "default_branch": "main"})
        if req.url.path == "/repos/owner/study/contents/":
            return httpx.Response(200, json=[{"name": "README.md"}])
        if req.method == "GET" and req.url.path.endswith("/contents/labhq/results/req_test/README.md"):
            return httpx.Response(200, json={"sha": "old-sha", "encoding": "base64",
                                             "content": base64.b64encode(b"old").decode(),
                                             "html_url": "https://example.test/old"})
        if req.method == "PUT" and "/contents/" in req.url.path:
            puts.append(json.loads(req.content))
            return httpx.Response(200, json={"content": {"html_url": "https://example.test/new"}})
        raise AssertionError(f"unexpected request {req.method} {req.url.path}")

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    hub = Hub(tmp_path)
    hub.requests["req_test"].update(pi_reviewed_at=1, result_storage={**EXISTING, "status": "draft"})
    service = ResultStorage(hub, httpx.MockTransport(handle))
    await service.upload("req_test", {"README.md": "new"})
    assert puts == [{"message": "연구 결과 정리: labhq/results/req_test/README.md — Compare public studies", "branch": "main",
                     "content": base64.b64encode(b"new").decode(), "sha": "old-sha"}]
    await service.close()


@pytest.mark.parametrize("where,kwargs", [
    ("a file write answered 403", {"put": 403}),
    ("a file write answered 404", {"put": 404}),
    ("the check of an existing file answered 403", {"get_file": 403}),
    ("the repository is not visible to the token (404)", {"repo": 404}),
    ("the repository answered 403", {"repo": 403}),
])
async def test_a_token_that_cannot_write_to_the_repository_says_how_to_add_it(tmp_path, monkeypatch, caplog, where, kwargs):
    caplog.set_level(logging.DEBUG)
    calls = []
    hub, saved = await run_finish(tmp_path, monkeypatch, EXISTING, fake_github(calls, **kwargs))
    assert saved["status"] == "failed" and saved["error_type"] == "PublishAccessError", where
    warning = saved["warning"]
    assert "owner/study에 파일을 쓸 수 없습니다" in warning
    assert "저장소 접근" in warning and "Contents: Read and write" in warning
    assert "labhq storage req_test --republish" in warning
    assert "새 저장소를 만들 수 없습니다" not in warning
    assert_no_token(hub, caplog, calls)


async def test_a_new_repository_that_exists_but_is_not_visible_to_the_token_gets_the_write_reason(tmp_path, monkeypatch):
    def handle(req):
        if req.url.path == "/user/repos":
            return httpx.Response(422, json={"message": "name already exists on this account"})
        return httpx.Response(404, json={"message": "Not Found"})

    hub, saved = await run_finish(tmp_path, monkeypatch, NEW, httpx.MockTransport(handle))
    assert saved["status"] == "failed" and "owner/study에 파일을 쓸 수 없습니다" in saved["warning"]


async def test_an_image_write_the_token_refuses_gets_the_same_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    hub = Hub(tmp_path)
    hub.requests["req_test"].update(pi_reviewed_at=1, result_storage={**EXISTING, "status": "draft"})
    service = ResultStorage(hub, fake_github([], put=403))
    with pytest.raises(PublishAccessError, match="owner/study에 파일을 쓸 수 없습니다"):
        await service.upload("req_test", {}, {"fig.png": b"\x89PNG\r\n"})
    await service.close()


@pytest.mark.parametrize("kwargs", [
    {"put": 403, "put_headers": {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}},  # rate limited
    {"put": 500},
    {"get_file": 500},
])
async def test_other_failures_keep_the_general_message_and_still_name_the_retry(tmp_path, monkeypatch, kwargs):
    hub, saved = await run_finish(tmp_path, monkeypatch, EXISTING, fake_github([], **kwargs))
    assert saved["status"] == "failed" and saved["warning"] == upload_failed_text("req_test")
    assert "labhq storage req_test --republish" in saved["warning"] and "파일을 쓸 수 없습니다" not in saved["warning"]
    assert saved["error_type"] != "PublishAccessError"


# ----- doctor -----

def fine_grained(extra="Z9"):
    return "github_pat_" + extra * 10  # built here so the file holds no token-shaped literal


@pytest.fixture
def quiet_doctor(monkeypatch):
    """collect() without a process or the network: no tool is found, paper2agent and the network are stubbed."""
    def refuse(*args, **kwargs):
        raise AssertionError("doctor started a process or reached the network")

    monkeypatch.setattr(doctor, "_paper2agent_row", lambda *a, **kw: doctor._row("staff", "paper2agent", "ok", "stub", ""))
    monkeypatch.setattr(doctor, "_network_check", refuse)
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def test_doctor_judges_a_fine_grained_token_by_probes_in_one_line_without_showing_it(tmp_path, monkeypatch,
                                                                                    quiet_doctor):
    """#607 warned on the prefix alone; #687 asks GitHub what the token may do. A token that may create repositories
    but not write contents or issues gets one warning naming what is missing and how to resume a failed publish."""
    token = fine_grained()
    monkeypatch.setenv("GITHUB_TOKEN", token)
    seen = []

    def probe(settings, value, **kwargs):
        seen.append(value == token)
        return github_token.Permissions(kind="fine_grained", create=True, contents=False, issues=False,
                                        probe_repo="me/notes")

    monkeypatch.setattr(doctor.github_token, "check_permissions", probe)
    result = doctor.collect(doctor_settings(tmp_path))
    rows = [row for row in result["checks"] if row["name"] == "github token"]
    assert seen == [True] and len(rows) == 1 and rows[0]["status"] == "warn"
    assert "\n" not in rows[0]["detail"] and "Contents 쓰기" in rows[0]["detail"] and "Issues 쓰기" in rows[0]["detail"]
    assert "All repositories" in rows[0]["hint"] and "--republish" in rows[0]["hint"]
    shown = json.dumps(result, ensure_ascii=False) + doctor.render(result)
    assert token not in shown and "Z9Z9" not in shown and "github_pat_" not in shown  # not even the prefix


@pytest.mark.parametrize("token", ["ghp_" + "a1" * 10, "gho_" + "b2" * 10, "fake-token-for-tests"])
def test_doctor_shows_a_token_with_every_permission_as_ok(tmp_path, monkeypatch, quiet_doctor, token):
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setattr(doctor.github_token, "check_permissions", lambda *a, **kw: github_token.Permissions(
        kind="classic", create=True, contents=True, issues=True))
    rows = [row for row in doctor.collect(doctor_settings(tmp_path))["checks"] if row["name"] == "github token"]
    assert [row["status"] for row in rows] == ["ok"] and "권한 확인됨" in rows[0]["detail"]


def test_without_a_token_doctor_says_results_stay_local_and_asks_nothing(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(doctor.github_token, "check_permissions",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("probed without a token")))
    row = doctor._github_token_row(Settings())
    assert row["status"] == "ok" and "로컬에 저장" in row["detail"] and "labhq init" in row["hint"]


def test_doctor_reads_the_token_from_the_configured_variable(monkeypatch):
    settings = Settings()
    settings.github.token_env = "LABHQ_TEST_PUBLISH_TOKEN"
    monkeypatch.setenv("GITHUB_TOKEN", fine_grained())  # not the variable this installation uses
    monkeypatch.delenv("LABHQ_TEST_PUBLISH_TOKEN", raising=False)
    monkeypatch.setattr(doctor.github_token, "check_permissions", lambda *a, **kw: github_token.Permissions(
        kind="fine_grained", create=False, contents=True, issues=True, probe_repo="me/notes"))
    assert "토큰 없음" in doctor._github_token_row(settings)["detail"]
    monkeypatch.setenv("LABHQ_TEST_PUBLISH_TOKEN", fine_grained())
    assert doctor._github_token_row(settings)["status"] == "warn"
