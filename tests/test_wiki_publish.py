"""Synthetic regressions for the wiki exposure publication boundary (#1037)."""

import pytest

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
