"""A failure while labhq builds the edition on this PC is not reported as a token problem (#1336)."""
import pytest

from labhq import result_storage
from labhq.result_storage import prepare_failed_text, upload_failed_text
from tests.test_publish_retry_607 import EXISTING, fake_github, run_finish


async def test_a_local_preparation_failure_names_the_pc_not_the_token(tmp_path, monkeypatch):
    calls = []

    def broken(*args, **kwargs):
        raise ValueError("synthetic local check")

    monkeypatch.setattr(result_storage, "build_portfolio_files", broken)
    hub, saved = await run_finish(tmp_path, monkeypatch, EXISTING, fake_github(calls))
    assert saved["status"] == "failed" and saved["error_type"] == "ValueError"
    assert saved["warning"] == prepare_failed_text("req_test", "ValueError")
    assert "토큰" in saved["warning"] and "문제가 아닙니다" in saved["warning"]
    assert saved["warning"] != upload_failed_text("req_test")
    assert not any(method == "PUT" for method, _path, _auth in calls)  # nothing reached the repository


@pytest.mark.parametrize("kwargs", [{"put": 500}, {"get_file": 500}])
async def test_a_github_failure_keeps_the_token_and_permission_message(tmp_path, monkeypatch, kwargs):
    hub, saved = await run_finish(tmp_path, monkeypatch, EXISTING, fake_github([], **kwargs))
    assert saved["status"] == "failed" and saved["warning"] == upload_failed_text("req_test")
