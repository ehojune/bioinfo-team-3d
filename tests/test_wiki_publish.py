"""Synthetic regressions for the wiki exposure publication boundary (#1037)."""

import pytest

from labhq.result_storage import ResultStorage
from tests.test_step_progress_push import Hub, github_transport, write_bundle


@pytest.mark.parametrize("enabled", [False, True], ids=["missing-history", "wiki-enabled"])
async def test_progress_with_unverified_history_never_uploads(tmp_path, monkeypatch, enabled):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    hub = Hub(tmp_path)
    hub.s.wiki.enabled = enabled
    write_bundle(hub, {"steps/s1/outputs/result.md": "synthetic private excerpt"})
    calls = []
    service = ResultStorage(hub, github_transport(calls))
    try:
        await service.progress_step("req_progress", "s1")
        assert not [c for c in calls if c[0] == "PUT" and "/contents/" in c[1]]
    finally:
        await service.close()
