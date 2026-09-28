import logging

import pytest

from labhq.security import TokenRedactionFilter, gateway_log_config, token_matches


@pytest.mark.parametrize("provided,expected,match", [
    ("client-secret", "client-secret", True),
    ("runner-secret", "other-secret", False),
    (None, "secret", False), ("", "secret", False),
    ("secret", "", False), (None, None, False),
])
def test_token_matches(provided, expected, match):
    assert token_matches(provided, expected) is match


def test_gateway_log_filter_masks_urls_and_arguments():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               'WebSocket /ws/runner?token=%s&since=1 /?token=abc123',
                               ("runner-secret",), None)
    assert TokenRedactionFilter().filter(record)
    line = logging.Formatter("%(message)s").format(record)
    assert "runner-secret" not in line and "abc123" not in line
    assert line.count("token=[REDACTED]") == 2
    assert "&since=1" in line


def test_gateway_uvicorn_handlers_install_filter():
    config = gateway_log_config()
    assert all("tokens" in handler["filters"] for handler in config["handlers"].values())


def test_gateway_log_filter_masks_exception_text():
    try:
        raise ValueError("/?token=exception-secret")
    except ValueError:
        import sys

        record = logging.LogRecord("labhq.gateway.server", logging.ERROR, __file__, 1,
                                   "failed", (), sys.exc_info())
    assert TokenRedactionFilter().filter(record)
    line = logging.Formatter("%(message)s").format(record)
    assert "exception-secret" not in line and "token=[REDACTED]" in line
