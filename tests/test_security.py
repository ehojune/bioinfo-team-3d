import logging

import pytest

from labhq.security import TokenRedactionFilter, gateway_log_config, token_matches


def test_up_rejects_default_runner_token_before_start(monkeypatch, capsys):
    from labhq import cli
    from labhq.settings import Settings

    settings = Settings()
    settings.gateway.client_token = "valid-client-token"
    monkeypatch.setattr(cli.instance_guard, "check", lambda *_args, **_kwargs: pytest.fail("started checks"))
    with pytest.raises(SystemExit) as exc:
        cli._up_unlocked(settings)
    assert exc.value.code == 2
    assert "runner_token" in capsys.readouterr().err


def test_settings_rejects_unknown_gateway_key():
    from pydantic import ValidationError
    from labhq.settings import Settings

    with pytest.raises(ValidationError, match="client_tokne"):
        Settings.model_validate({"gateway": {"client_tokne": "secret"}})


def test_gateway_command_rejects_default_runner_token(tmp_path, monkeypatch, capsys):
    from labhq import cli
    config = tmp_path / "config.yaml"
    config.write_text(f"gateway:\n  client_token: valid-client-token\n  state_dir: '{tmp_path.as_posix()}/state'\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_claim_or_exit", lambda *_args: pytest.fail("gateway tried to start"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["-c", str(config), "gateway"])
    assert exc.value.code == 2
    assert "runner_token" in capsys.readouterr().err


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


# ----- #37: the runner's and the CLI's own logs and messages -----

CLIENT_TOKEN = "FAKECLIENTTOKEN_Hs7d2f5g8j1k4l6z9x3c0v"
RUNNER_TOKEN = "FAKERUNNERTOKEN_Zk3q9v7m2x5c8b1n4w6y0p"


def _wrong_scheme_error(path, token):
    from websockets.exceptions import InvalidURI

    return InvalidURI(f"http://127.0.0.1:8787{path}?token={token}", "scheme isn't ws or wss")


def test_a_runner_connection_warning_with_the_token_url_is_masked_on_the_root_handlers(monkeypatch):
    """The runner logs `gateway connection lost/failed: %s` with the exception; a wrong scheme in gateway.url makes
    that exception the whole address, token included. Every command's root logging masks it (#37)."""
    import io

    from labhq import cli

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    for handler in saved_handlers:
        root.removeHandler(handler)
    stream = io.StringIO()
    try:
        monkeypatch.setattr(logging, "basicConfig", lambda **kw: root.addHandler(_stream_handler(stream, kw)))
        cli._configure_logging()
        logging.getLogger("labhq.runner").warning("gateway connection lost/failed: %s (retry in %.0fs)",
                                                  _wrong_scheme_error("/ws/runner", RUNNER_TOKEN), 1.0)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
    out = stream.getvalue()
    assert RUNNER_TOKEN not in out and "token=[REDACTED]" in out and "retry in 1s" in out


def _stream_handler(stream, kwargs):
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(kwargs.get("format")))
    return handler


def test_configuring_logging_twice_does_not_stack_filters():
    from labhq import cli
    from labhq.security import TokenRedactionFilter as Filter

    cli._configure_logging()
    cli._configure_logging()
    for handler in logging.getLogger().handlers:
        assert sum(isinstance(f, Filter) for f in handler.filters) <= 1


def test_watch_with_a_wrong_scheme_prints_no_address_and_exits_2(capsys):
    import types

    from labhq import cli

    async def watch():
        raise _wrong_scheme_error("/ws/client", CLIENT_TOKEN)

    settings = types.SimpleNamespace(config_path=None, gateway=types.SimpleNamespace(url="http://127.0.0.1:8787"))
    with pytest.raises(SystemExit) as stop:
        cli._run_websocket(settings, watch())
    shown = capsys.readouterr()
    assert stop.value.code == 2
    assert CLIENT_TOKEN not in shown.out + shown.err and "token" not in (shown.out + shown.err).replace("gateway.url", "")
    assert "ws://" in shown.err and "wss://" in shown.err


def test_a_traceback_with_the_token_is_masked_by_the_filter_too():
    import io
    import traceback

    from labhq.security import redact_tokens

    try:
        raise _wrong_scheme_error("/ws/client", CLIENT_TOKEN)
    except Exception:  # noqa: BLE001
        text = io.StringIO()
        traceback.print_exc(file=text)
    assert CLIENT_TOKEN in text.getvalue()  # what an unmasked traceback would print
    assert CLIENT_TOKEN not in redact_tokens(text.getvalue())
