"""Conservative engine-login error detection and manual login instructions."""

from __future__ import annotations


def is_login_error(engine: str, error: str | None) -> bool:
    """A CLI account needs an interactive login, not an ordinary auth-like failure."""
    folded = (error or "").casefold()
    name = str(engine or "").casefold()
    if name in {"claude", "claude_code"}:
        return any(phrase in folded for phrase in (
            "failed to authenticate",
            "oauth session expired",
            "could not be refreshed",
            "please run /login",
            "not logged in",
            "invalid api key",
        ))
    if name == "codex":
        return any(phrase in folded for phrase in (
            "not logged in",
            "chatgpt login is required",
            "your access token could not be refreshed",
        )) and any(phrase in folded for phrase in (
            "not logged in",
            "login is required",
            "sign in again",
            "log out and sign in again",
        ))
    return False


def login_command(engine: str, settings) -> str:
    """Return a display-only command for the configured staff account; never include credentials."""
    name = str(engine or "").casefold()
    if name in {"claude", "claude_code"}:
        path = settings.engines.claude_code.env.get("CLAUDE_CONFIG_DIR")
        prefix = f'$env:CLAUDE_CONFIG_DIR="{path}"; ' if path else ""
        return prefix + "claude\n/login"
    if name == "codex":
        path = settings.engines.codex.env.get("CODEX_HOME")
        prefix = f'$env:CODEX_HOME="{path}"; ' if path else ""
        return prefix + "codex login"
    return ""
