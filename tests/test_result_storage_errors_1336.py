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


async def test_a_must_zero_korean_marker_is_recorded_as_a_warning_and_publishing_goes_on(tmp_path, monkeypatch):
    from tests.test_publish_retry_607 import TOKEN, Hub
    from labhq.result_storage import ResultStorage
    from labhq import portfolio
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    real_report = portfolio.portfolio_report
    monkeypatch.setattr(portfolio, "portfolio_report",
                        lambda *a, **kw: real_report(*a, **kw) + "\n\n이 세포는 변이를 가지고 있다.\n")
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github([]))
    hub.requests["req_test"].update(status="done", pi_reviewed_at=1, report="Observed difference.",
                                    result_storage={**EXISTING, "status": "selected"})

    async def approve(**kw):
        return {"approved": True}

    hub.request_approval = approve
    await service.finish("req_test")
    await service.close()
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "published", saved.get("warning")
    qc = saved["korean_qc"]
    assert any(row["status"] == "WARN" and "가지고 있다" in row["must_zero"] for row in qc.values())
