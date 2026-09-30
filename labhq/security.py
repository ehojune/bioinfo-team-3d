"""Token comparisons and gateway log redaction."""

import hmac
import logging
import re
import traceback
from copy import deepcopy
from urllib.parse import unquote_plus


def token_matches(provided: str | None, expected: str | None) -> bool:
    """Reject absent/empty tokens and compare UTF-8 bytes in constant time."""
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    left, right = provided.encode("utf-8"), expected.encode("utf-8")
    return bool(left and right) and hmac.compare_digest(left, right)


_TOKEN = re.compile(r"(?i)(\btoken=)[^\s&#'\"<>,)]*")
_QUERY_PARAM = re.compile(r"([?&])([^=\s&#'\"<>]+)=([^\s&#'\"<>]*)")


def redact_tokens(value: str) -> str:
    def redact(match):
        # Decode keys once, as Starlette does; leave other parameters untouched.
        if unquote_plus(match[2]).casefold() == "token":
            return f"{match[1]}{match[2]}=[REDACTED]"
        return match[0]

    return _TOKEN.sub(r"\1[REDACTED]", _QUERY_PARAM.sub(redact, value))


class TokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_tokens(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.msg += "\n" + redact_tokens("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact_tokens(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_tokens(record.stack_info)
        return True


def gateway_log_config() -> dict:
    """Install on root handlers and every uvicorn handler, including access logs."""
    from uvicorn.config import LOGGING_CONFIG

    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, TokenRedactionFilter) for f in handler.filters):
            handler.addFilter(TokenRedactionFilter())
    config = deepcopy(LOGGING_CONFIG)
    config["filters"] = {"tokens": {"()": TokenRedactionFilter}}
    for handler in config["handlers"].values():
        handler.setdefault("filters", []).append("tokens")
    return config
