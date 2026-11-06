"""Synthetic regressions for the wiki exposure publication boundary (#1037)."""

import pytest

from labhq.models import TaskResult
from labhq.result_storage import ResultStorage
from tests.test_step_progress_push import Hub, github_transport, write_bundle


def contents_puts(calls):
    return [c for c in calls if c[0] == "PUT" and "/contents/" in c[1]]


async def progress(tmp_path, monkeypatch, *, enabled, exposure):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    hub = Hub(tmp_path)
    hub.s.wiki.enabled = enabled
    request = hub.requests["req_progress"]
    request.pop("wiki_exposure", None)  # the fixture is a recorded `none`; each case sets its own history
    if exposure is not None:
        request["wiki_exposure"] = exposure
    write_bundle(hub, {"steps/s1/outputs/result.md": "synthetic private excerpt"})
    calls = []
    service = ResultStorage(hub, github_transport(calls))
    try:
        await service.progress_step("req_progress", "s1")
    finally:
        await service.close()
    return hub, calls


@pytest.mark.parametrize("enabled", [False, True], ids=["wiki-off", "wiki-enabled"])
@pytest.mark.parametrize("exposure", [None, "possible", "unknown", "", True],
                         ids=["missing-history", "possible", "unknown", "empty", "not-a-string"])
async def test_progress_with_unverified_or_exposed_history_never_uploads(tmp_path, monkeypatch, enabled, exposure):
    hub, calls = await progress(tmp_path, monkeypatch, enabled=enabled, exposure=exposure)
    assert not contents_puts(calls)
    warning = hub.requests["req_progress"]["result_storage"].get("progress_warning", "")
    assert "wiki" in warning and hub.requests["req_progress"]["result_storage"]["progress_push"] is False


@pytest.mark.parametrize("enabled", [False, True], ids=["wiki-off", "wiki-enabled"])
async def test_a_recorded_none_still_uploads_so_the_block_is_not_a_blanket_refusal(tmp_path, monkeypatch, enabled):
    hub, calls = await progress(tmp_path, monkeypatch, enabled=enabled, exposure="none")
    assert contents_puts(calls)
    assert hub.requests["req_progress"]["result_storage"].get("progress_status") == "pushed"


async def test_an_exposure_raised_between_two_files_stops_the_remaining_uploads(tmp_path, monkeypatch):
    """A dispatch that reaches the wiki while a step is being published must stop the files not yet sent (#1037)."""
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    hub = Hub(tmp_path)
    write_bundle(hub, {"steps/s1/outputs/a.md": "synthetic one", "steps/s1/outputs/b.md": "synthetic two"})
    calls = []
    service = ResultStorage(hub, github_transport(calls))
    sent = []
    real_client = service.client

    def client():
        gh = real_client()
        put = gh.put_file

        async def put_then_expose(*args, **kwargs):
            sent.append(args[1])
            hub.requests["req_progress"]["wiki_exposure"] = "possible"  # a dispatch lands during the first write
            return await put(*args, **kwargs)

        gh.put_file = put_then_expose
        return gh

    service.client = client
    result = TaskResult(task_id="task-s1", agent_id="worker", ok=True, text="s1 done", outputs=["outputs/a.md"])
    hub.requests["req_progress"]["results"]["s1"] = result.model_dump(mode="json")
    try:
        await service.progress_step("req_progress", "s1", result)
    finally:
        await service.close()
    assert len(sent) == 1, "no file may be sent after the exposure became possible"
    assert hub.requests["req_progress"]["result_storage"]["progress_status"] == "disabled"
    assert hub.requests["req_progress"]["result_storage"]["progress_push"] is False


# ---- the other publishing paths: report and comments, round issue, final upload, pipeline PR, CP2 (#1037) ----
import asyncio  # noqa: E402
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402

from labhq.evidence import wiki_exposure  # noqa: E402
from labhq.integrations.github import GitHubClient, ProjectReporter  # noqa: E402
from labhq.integrations.rounds import RoundRecorder  # noqa: E402
from labhq.settings import ProjectSettings, Settings  # noqa: E402
from tests.test_github_reporter import fake_github  # noqa: E402

BLOCKED = [None, "possible", "unknown", "", "NONE"]
IDS = ["missing-history", "possible", "unknown", "empty", "wrong-case"]


class Store:
    def __init__(self):
        self.rows = {}

    def all(self, kind):
        return {k: v for (kd, k), v in self.rows.items() if kd == kind}

    def get(self, kind, key):
        return self.rows.get((kind, key))

    def put(self, kind, key, value):
        self.rows[(kind, key)] = value

    def delete(self, kind, key):
        self.rows.pop((kind, key), None)


def reporter_hub(exposure, **extra):
    request = {"id": "req_1", "project_id": "demo", "text": "synthetic question", "status": "running", **extra}
    if exposure is not None:
        request[wiki_exposure.KEY] = exposure
    events = []

    async def publish(event):
        events.append(event)

    hub = SimpleNamespace(requests={"req_1": request}, store=Store(), publish=publish, events=events,
                          ack_reporter_delivery=lambda ev: None)
    return hub


def reporter(hub, calls, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    settings = Settings()
    settings.projects = [ProjectSettings(id="demo", repo="o/p")]
    return ProjectReporter(hub, settings, fake_github(calls))


EVENTS = [{"type": "request.created", "request_id": "req_1"},
          {"type": "request.plan", "request_id": "req_1", "data": {"plan": {"steps": []}}},
          {"type": "request.completed", "request_id": "req_1", "data": {"ok": True, "report": "synthetic report"}}]


@pytest.mark.parametrize("exposure", BLOCKED, ids=IDS)
async def test_the_project_reporter_posts_nothing_for_an_exposed_or_unknown_request(monkeypatch, exposure):
    hub = reporter_hub(exposure)
    calls = []
    service = reporter(hub, calls, monkeypatch)
    for event in EVENTS:
        assert await service.handle(event) is None
    assert [c for c in calls if c[0] != "GET"] == [], "no issue, comment, report file or close may be written"


async def test_the_project_reporter_still_posts_for_a_recorded_none(monkeypatch):
    hub = reporter_hub("none")
    calls = []
    service = reporter(hub, calls, monkeypatch)
    await service.handle(EVENTS[0])
    assert [c for c in calls if c[0] == "POST" and c[1].endswith("/issues")], "a clean request still gets its issue"


async def test_an_exposure_raised_while_the_reporter_waits_stops_the_next_write(monkeypatch):
    hub = reporter_hub("none")
    calls = []
    service = reporter(hub, calls, monkeypatch)
    real_client = service.client()

    async def find_then_expose(*args, **kwargs):  # the request is exposed while the reporter looks for its issue
        hub.requests["req_1"][wiki_exposure.KEY] = "possible"
        return None

    real_client.find_request_issue = find_then_expose
    await service.handle(EVENTS[0])
    assert [c for c in calls if c[0] == "POST"] == []


def test_a_pipeline_contribution_for_an_exposed_request_is_rejected_before_any_card_or_write(monkeypatch):
    for exposure in (None, "possible"):
        hub = reporter_hub(exposure)
        calls = []
        service = reporter(hub, calls, monkeypatch)
        service.s.policy.bioinfo_agent.pipeline_pr = True
        service.s.policy.bioinfo_agent.pipeline_repo = "o/pipelines"
        entry = {"task_id": "task_1", "request_id": "req_1", "agent_id": "bioinfo-agent", "state": "pending",
                 "submission": {"name": "demo", "title": "demo", "summary": "s",
                                "files": [{"path": "pipelines/demo/main.nf", "content": "process X {}"},
                                           {"path": "pipelines/demo/nextflow.config", "content": "params.x = 1"},
                                           {"path": "pipelines/demo/README.md", "content": "# demo"}]}}
        hub.store.put("pipeline_submission", "task_1", entry)
        hub.approvals = {}
        outcome = asyncio.run(service._handle_pipeline({"type": "pipeline.ready", "request_id": "req_1", "task_id": "task_1"}))
        assert outcome is True
        assert entry["state"] == "rejected" and "wiki" in entry["reason"]
        assert not hub.approvals and [c for c in calls if c[0] != "GET"] == []


def round_hub(tmp_path, exposure):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.dev_log.repo = "o/devlog"
    request = {"id": "req_1", "text": "synthetic"}
    if exposure is not None:
        request[wiki_exposure.KEY] = exposure
    return SimpleNamespace(s=settings, requests={"req_1": request}, store=Store())


@pytest.mark.parametrize("exposure", BLOCKED, ids=IDS)
async def test_the_round_recorder_publishes_nothing_for_an_exposed_or_unknown_request(tmp_path, exposure):
    calls = []
    recorder = RoundRecorder(round_hub(tmp_path, exposure), httpx.MockTransport(
        lambda request: calls.append((request.method, request.url.path)) or httpx.Response(404, json={})))
    assert await recorder.publish("req_1") is False
    assert calls == []


async def test_the_round_recorder_rechecks_right_before_its_one_write(tmp_path):
    hub = round_hub(tmp_path, "none")
    recorder = RoundRecorder(hub, httpx.MockTransport(lambda request: httpx.Response(404, json={})))
    hub.requests["req_1"][wiki_exposure.KEY] = "possible"  # raised after publish() began
    assert await recorder._may_post(gh=None, rid="req_1") is False


async def test_the_round_recorder_judges_the_exposure_again_after_its_repository_read(tmp_path):
    hub = round_hub(tmp_path, "none")

    def answer(request):
        hub.requests["req_1"][wiki_exposure.KEY] = "possible"  # raised while the repository metadata is read
        return httpx.Response(200, json={"private": True})

    recorder = RoundRecorder(hub, httpx.MockTransport(answer))
    gh = GitHubClient("synthetic-token", "https://api.github.invalid", httpx.MockTransport(answer))
    assert await recorder._may_post(gh, "req_1") is False


@pytest.mark.parametrize("expose", [False, True], ids=["control-closes", "exposed-keeps-open"])
async def test_an_exposure_raised_before_the_project_issue_is_closed_stops_the_close(monkeypatch, expose):
    hub = reporter_hub("none")
    calls = []
    service = reporter(hub, calls, monkeypatch)
    real_client = service.client()
    real_comment = real_client.comment

    async def comment_then_expose(repo, number, body, *args, **kwargs):
        result = await real_comment(repo, number, body, *args, **kwargs)
        if expose and "완료" in body:
            hub.requests["req_1"][wiki_exposure.KEY] = "possible"  # raised after the final comment, before the close
        return result

    real_client.comment = comment_then_expose
    for event in EVENTS:
        await service.handle(event)
    assert any(c[0] == "PATCH" for c in calls) is (not expose)


async def test_the_final_result_upload_refuses_an_exposed_request_and_a_clean_one_goes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    for exposure, expected in (("possible", False), (None, False), ("none", True)):
        hub = Hub(tmp_path)
        request = hub.requests["req_progress"]
        request.pop("wiki_exposure", None)
        if exposure is not None:
            request["wiki_exposure"] = exposure
        request["pi_reviewed_at"] = 1.0
        write_bundle(hub, {"steps/s1/outputs/result.md": "synthetic"})
        calls = []
        service = ResultStorage(hub, github_transport(calls))
        try:
            if expected:
                await service.upload("req_progress", {"report.md": "# synthetic report\n"})
                assert contents_puts(calls)
            else:
                with pytest.raises(ValueError, match="wiki"):
                    await service.upload("req_progress", {"report.md": "# synthetic report\n"})
                assert not contents_puts(calls)
        finally:
            await service.close()


@pytest.mark.parametrize("expose", [False, True], ids=["control-creates", "exposed-creates-nothing"])
async def test_an_exposure_raised_while_the_new_repository_is_looked_up_stops_its_creation(tmp_path, monkeypatch, expose):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    hub = Hub(tmp_path)
    request = hub.requests["req_progress"]
    request.update({"wiki_exposure": "none", "pi_reviewed_at": 1.0})
    request["result_storage"]["mode"] = "new"
    write_bundle(hub, {"steps/s1/outputs/result.md": "synthetic"})
    calls = []

    def handle(http):
        calls.append((http.method, http.url.path))
        if http.method == "GET" and http.url.path == "/repos/owner/study":
            if expose:
                request["wiki_exposure"] = "possible"  # raised while the repository is looked up
            return httpx.Response(404, json={})
        return httpx.Response(403, json={"message": "stop here"})

    service = ResultStorage(hub, httpx.MockTransport(handle))
    try:
        with pytest.raises(Exception):
            await service.upload("req_progress", {"report.md": "# synthetic report" + chr(10)})
    finally:
        await service.close()
    assert (("POST", "/user/repos") in calls) is (not expose)


def test_the_cp2_card_warns_instead_of_refusing_and_a_clean_request_gets_no_line():
    assert wiki_exposure.cp2_warning({wiki_exposure.KEY: "none"}) is None
    for request in ({wiki_exposure.KEY: "possible"}, {}, None):
        line = wiki_exposure.cp2_warning(request)
        assert line.startswith("wiki 노출") and "GitHub에 게시되지 않습니다" in line
    assert "쓸 수 있었습니다" in wiki_exposure.cp2_warning({wiki_exposure.KEY: "possible"})
    assert "확인할 수 없습니다" in wiki_exposure.cp2_warning({})
