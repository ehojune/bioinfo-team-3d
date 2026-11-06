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
