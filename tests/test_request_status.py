from labhq.request_status import is_active_request, is_terminal_request


def test_request_status_classification_keeps_quota_wait_active_until_terminal():
    for status in ("running", "waiting_for_runner", "waiting_quota"):
        assert is_active_request(status)
        assert not is_terminal_request(status)
    for status in ("done", "failed", "cancelled", "rejected"):
        assert not is_active_request(status)
        assert is_terminal_request(status)
    assert not is_active_request("interrupted")
    assert not is_terminal_request("interrupted")
