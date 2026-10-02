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


@pytest.mark.parametrize("key", ["%74oken", "to%6ben", "%74%6F%6b%65%6e"])
def test_gateway_access_log_masks_decoded_token_keys(key):
    from starlette.datastructures import QueryParams

    path = f"/ws/client?since=7&{key}=encoded-secret&other=visible&token=plain-secret&{key}=second-secret"
    assert QueryParams(path.partition("?")[2]).getlist("token") == ["encoded-secret", "plain-secret", "second-secret"]
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d', ("client", "GET", path, "1.1", 101), None)
    assert TokenRedactionFilter().filter(record)
    line = logging.Formatter("%(message)s").format(record)
    assert all(secret not in line for secret in ("encoded-secret", "plain-secret", "second-secret"))
    assert line.count("[REDACTED]") == 3
    assert "since=7" in line and "other=visible" in line and f"{key}=[REDACTED]" in line


def test_gateway_encoded_token_keys_are_masked_in_exception_and_stack():
    import sys

    try:
        raise ValueError("/?%74oken=exception-secret")
    except ValueError:
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    record.exc_text = "/?to%6ben=cached-secret"
    record.stack_info = "/?%74oken=stack-secret"
    assert TokenRedactionFilter().filter(record)
    line = logging.Formatter("%(message)s").format(record)
    assert all(secret not in line for secret in ("exception-secret", "cached-secret", "stack-secret"))
    assert record.exc_text == "/?to%6ben=[REDACTED]"
    assert record.stack_info == "/?%74oken=[REDACTED]"
    assert "ValueError: /?%74oken=[REDACTED]" in record.msg


def test_gateway_log_query_keys_decode_once_and_preserve_other_parameters():
    from labhq.security import redact_tokens

    text = "/?%2574oken=double&other%74oken=other&to+ken=space&next=a%26token%3Db&%74oken=hidden"
    assert redact_tokens(text) == text.replace("=hidden", "=[REDACTED]")


def test_access_record_keeps_uvicorn_args_after_redaction():
    # #331: emptying args broke uvicorn's AccessFormatter ("expected 5, got 0") on every access line.
    from uvicorn.logging import AccessFormatter

    path = "/ws/client?since=7&token=plain-secret"
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5", "GET", path, "1.1", 101), None)
    assert TokenRedactionFilter().filter(record)
    assert len(record.args) == 5
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert "plain-secret" not in line and "token=[REDACTED]" in line and "since=7" in line
    assert line.startswith('127.0.0.1:5 - "GET /ws/client?') and line.endswith(" 101 Switching Protocols")
