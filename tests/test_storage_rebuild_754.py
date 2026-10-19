"""Rebuild a published edition with the current editor (#754): `labhq storage <id> --rebuild [--name]`.
Mock editor, mock GitHub and no process: nothing here reaches the network or runs git."""
import httpx
import pytest

from labhq import cli, result_storage
from labhq.result_storage import place_rebuild_note, rebuild_note
from labhq.settings import DataZone, Settings
from tests.test_edition_quality_607 import publish_card, redraft_hub

EDITOR = {"version": "0.5.0", "commit": "abc1234"}


async def published(tmp_path, monkeypatch, replies, app=False):
    """The first edition went through its publish card to owner/study."""
    monkeypatch.setattr(result_storage, "editor_edition", lambda: dict(EDITOR))
    made = redraft_hub(tmp_path, monkeypatch, list(replies), app=app)
    hub, upload = made[0], made[2]
    hub.result_storage.schedule("req_test")
    card = await publish_card(hub)
    await hub.resolve_approval(card["id"], True)
    await hub.result_storage.tasks["req_test"]
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "published" and upload.await_count == 1
    return (*made, card)


async def test_rebuild_makes_a_new_edition_in_the_same_repository_through_the_publish_card(tmp_path, monkeypatch):
    hub, editor, upload, first = await published(tmp_path, monkeypatch, ["# 첫 정리본", "# 고친 편집기 정리본"])
    service = hub.result_storage
    branch = hub.requests["req_test"]["result_storage"]["branch"] = "main"
    old_url = hub.requests["req_test"]["result_storage"]["report_url"]

    reply = await service.rebuild("req_test")
    assert reply == {"request_id": "req_test", "status": "rebuilding", "repo": "owner/study",
                     "new_repository": False, "note": ""}
    second = await publish_card(hub, not_id=first["id"])
    readme = second["detail"]["documents"]["README.md"]
    assert readme.startswith("# 고친 편집기 정리본\n\n> 이 판은 labhq 0.5.0(commit abc1234) 편집기로 ")
    assert "이전 판은 이 저장소의 커밋 기록에 있습니다." in readme and readme.count("> 이 판은") == 1
    assert second["detail"]["repo"] == "owner/study"
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "draft" and "report_url" not in saved and saved["branch"] == branch
    assert saved["editions"] == [{"mode": "existing", "repo": "owner/study", "branch": "main", "report_url": old_url}]
    assert saved["rebuild"]["from_repo"] == saved["rebuild"]["repo"] == "owner/study"
    assert saved["rebuild"]["editor"] == EDITOR
    assert editor.await_count == 2 and upload.await_count == 1  # nothing goes up before the PI answers

    await hub.resolve_approval(second["id"], True)
    await service.tasks["req_test"]
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "published" and saved["report_url"] == old_url
    assert upload.await_count == 2 and "> 이 판은 labhq 0.5.0" in upload.await_args.args[1]["README.md"]
    await service.close()


async def test_rebuild_with_a_name_goes_to_a_new_repository_and_names_the_original(tmp_path, monkeypatch):
    hub, editor, upload, first = await published(tmp_path, monkeypatch, ["# 첫 정리본", "# 새 저장소 정리본"])
    service = hub.result_storage
    hub.requests["req_test"]["result_storage"]["branch"] = "results"  # a branch of the old repository only
    probed = []

    async def choice(rid, name, visibility):
        probed.append((rid, name, visibility))
        return {"mode": "new", "repo": f"owner/{name}", "visibility": visibility,
                "allow_public": visibility == "public", "status": "selected"}, "토큰 안내"

    monkeypatch.setattr(service, "_new_github_choice", choice)
    reply = await service.rebuild("req_test", " study-v2 ", "private")
    assert probed == [("req_test", "study-v2", "private")]
    assert reply["repo"] == "owner/study-v2" and reply["new_repository"] and reply["note"] == "토큰 안내"
    card = await publish_card(hub, not_id=first["id"])
    assert card["detail"]["repo"] == "owner/study-v2" and "owner/study-v2" in card["summary"]
    readme = card["detail"]["documents"]["README.md"]
    assert "원본 저장소: [owner/study](https://github.com/owner/study)." in readme
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["mode"] == "new" and "branch" not in saved and saved["editions"][0]["repo"] == "owner/study"

    await hub.resolve_approval(card["id"], True)
    await service.tasks["req_test"]
    assert hub.requests["req_test"]["result_storage"]["repo"] == "owner/study-v2"
    assert upload.await_count == 2
    await service.close()


async def test_a_rebuilt_edition_asks_even_when_results_publish_automatically(tmp_path, monkeypatch):
    hub, editor, upload, first = await published(tmp_path, monkeypatch, ["# 첫 정리본", "# 다시"])
    hub.s.github.auto_publish_results = True
    await hub.result_storage.rebuild("req_test")
    card = await publish_card(hub, not_id=first["id"])
    assert upload.await_count == 1 and hub.requests["req_test"]["result_storage"]["status"] == "draft"
    await hub.resolve_approval(card["id"], False)
    await hub.result_storage.tasks["req_test"]
    assert hub.requests["req_test"]["result_storage"]["status"] == "declined" and upload.await_count == 1
    await hub.result_storage.close()


async def test_rebuild_refuses_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(result_storage, "editor_edition", lambda: dict(EDITOR))
    hub, editor, _ = redraft_hub(tmp_path, monkeypatch, [])
    service, req = hub.result_storage, hub.requests["req_test"]
    saved = req["result_storage"]
    for status in (None, "selected", "local", "draft", "declined", "failed"):
        saved["status"] = status
        with pytest.raises(ValueError, match="게시한 정리본만 다시 만듭니다") as refused:
            await service.rebuild("req_test")
        assert "--redraft" in str(refused.value) and "--to github" in str(refused.value)
    saved["status"] = "published"
    snapshot = dict(saved)

    cases = [("status", "running", "끝난 요청만"), ("cancelling", True, "끝난 요청만"),
             ("pi_reviewed_at", None, "보고서 검토 완료"), ("bundle_pending", True, "묶음 갱신"),
             ("revised_to", "req_next", "req_next"), ("storage_end_ask", {"state": "asked"}, "끝 저장 위치")]
    for key, value, message in cases:
        before = req.get(key)
        req[key] = value
        with pytest.raises(ValueError, match=message):
            await service.rebuild("req_test")
        req[key] = before

    zones, text = hub.s.policy.data_zones, req["text"]
    hub.s.policy.data_zones = [DataZone(path="/controlled", level="restricted")]
    req["text"] = "Analyze /controlled/cohort.tsv"
    with pytest.raises(ValueError, match="통제 데이터"):
        await service.rebuild("req_test")
    hub.s.policy.data_zones, req["text"] = zones, text

    service.uploading.add("req_test")
    with pytest.raises(ValueError, match="진행 중"):
        await service.rebuild("req_test")
    service.uploading.discard("req_test")

    async def same(rid, name, visibility):
        return {"mode": "new", "repo": "owner/study", "visibility": "private", "status": "selected"}, ""

    monkeypatch.setattr(service, "_new_github_choice", same)
    with pytest.raises(ValueError, match="지금 저장소와 같습니다"):
        await service.rebuild("req_test", "study")

    async def no_token(rid, name, visibility):
        raise ValueError("GitHub 토큰이 없습니다.")

    monkeypatch.setattr(service, "_new_github_choice", no_token)
    with pytest.raises(ValueError, match="토큰이 없습니다"):
        await service.rebuild("req_test", "study-v2")
    assert saved == snapshot and editor.await_count == 0 and "req_test" not in service.tasks

    with pytest.raises(ValueError, match="--rebuild"):  # the published refusals name the new command
        await service.redraft("req_test")
    with pytest.raises(ValueError, match="--rebuild"):
        await service.republish("req_test")


def test_the_rebuild_line_sits_under_the_title_once():
    note = rebuild_note({"from_repo": "o/old", "repo": "o/new", "editor": EDITOR, "date": "2026-10-20"})
    assert note == ("> 이 판은 labhq 0.5.0(commit abc1234) 편집기로 2026-10-20에 다시 만든 판입니다. "
                    "원본 저장소: [o/old](https://github.com/o/old).")
    placed = place_rebuild_note("# 제목\n\n본문\n", note)
    assert placed == f"# 제목\n\n{note}\n\n본문\n" and place_rebuild_note(placed, note) == placed
    assert place_rebuild_note("본문만", note) == f"{note}\n\n본문만"
    assert "버전 기록 없음" in rebuild_note({"repo": "o/r", "from_repo": "o/r"})


async def test_rebuild_api_and_cli(tmp_path, monkeypatch, capsys):
    hub, editor, upload, app, first = await published(tmp_path, monkeypatch, ["# 첫", "# 둘"], app=True)
    hub.requests["req_draft"] = {**hub.requests["req_test"], "id": "req_draft",
                                 "result_storage": {"mode": "existing", "repo": "o/r", "status": "draft"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/requests/req_test/storage/rebuild")).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.post("/api/requests/missing/storage/rebuild")).status_code == 404
        refused = await client.post("/api/requests/req_draft/storage/rebuild", json={})
        assert refused.status_code == 409 and "--redraft" in refused.json()["detail"]
        ok = await client.post("/api/requests/req_test/storage/rebuild", json={"name": None, "visibility": "private"})
        assert ok.status_code == 200 and ok.json()["status"] == "rebuilding"
    card = await publish_card(hub, not_id=first["id"])
    assert "> 이 판은" in card["detail"]["documents"]["README.md"]
    await hub.result_storage.close()

    calls = []
    monkeypatch.setattr(cli, "_api", lambda s, method, path, **kw: calls.append((method, path, kw.get("json"))) or
                        {"request_id": "req_x", "status": "rebuilding", "repo": "me/v2", "new_repository": True,
                         "note": ""})
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: hub.s))
    cli.main(["storage", "req_x", "--rebuild", "--name", "v2"])
    assert calls == [("POST", "/api/requests/req_x/storage/rebuild", {"name": "v2", "visibility": "private"})]
    out = capsys.readouterr().out
    assert "새 저장소 me/v2에 올립니다" in out and "이전 판에만 있던 파일은 지우지 않습니다" in out
    with pytest.raises(SystemExit):  # one of the storage actions, not two
        cli.main(["storage", "req_x", "--rebuild", "--redraft"])
    assert len(calls) == 1
