"""Conservative engine-login error detection and manual login instructions."""

from __future__ import annotations

import ntpath
import os
import posixpath
import re

from .adapters.base import expand_env

LOGIN_PATH_ENV = ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME")


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


def _runner_windows(capabilities: dict | None) -> bool:
    environment = None
    if isinstance(capabilities, dict):
        environment = (capabilities.get("environment") if "environment" in capabilities
                       else capabilities.get("env"))
    environment = environment if isinstance(environment, dict) else {}
    values = [capabilities.get(key) for key in ("os", "platform", "system")
              if isinstance(capabilities, dict)]
    values.extend(environment.get(key) for key in ("OS", "os", "platform"))
    for value in values:
        folded = str(value or "").casefold()
        if folded.startswith(("win", "nt")):
            return True
        if folded.startswith(("linux", "darwin", "posix", "mac")):
            return False
    return os.name == "nt"


def _runner_environment(capabilities: dict | None) -> dict[str, str]:
    if isinstance(capabilities, dict):
        environment = (capabilities.get("environment") if "environment" in capabilities
                       else capabilities.get("env"))
        if isinstance(environment, dict):
            return {str(key): str(value) for key, value in environment.items()
                    if isinstance(value, (str, os.PathLike))}
        if any(key in capabilities for key in ("os", "platform", "system")):
            return {}
    return dict(os.environ)


def _expanded_path(raw: str, capabilities: dict | None) -> str | None:
    """Expand a configured staff path with the runner's environment and path rules."""
    environment = _runner_environment(capabilities)
    lookup = {key.casefold(): value for key, value in environment.items()}
    expanded = expand_env({"path": raw}, environment)["path"]
    unknown = False

    def replace(match: re.Match) -> str:
        nonlocal unknown
        name = next(group for group in match.groups() if group is not None)
        value = lookup.get(name.casefold())
        if value is None:
            unknown = True
            return match.group(0)
        return value

    expanded = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%|\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)", replace, expanded)
    if expanded == "~" or expanded.startswith(("~/", "~\\")):
        home_key = "userprofile" if _runner_windows(capabilities) else "home"
        home = lookup.get(home_key) or lookup.get("home" if home_key == "userprofile" else "userprofile")
        if home:
            expanded = home + expanded[1:]
        else:
            unknown = True
    if unknown or re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|%[A-Za-z_][A-Za-z0-9_]*%|"
                            r"\$(?!\{)[A-Za-z_][A-Za-z0-9_]*", expanded):
        return None
    path_module = ntpath if _runner_windows(capabilities) else posixpath
    return path_module.normpath(expanded)


def _powershell_quote(value: str) -> str:
    return value.replace("`", "``").replace("$", "`$").replace('"', '`"')


def _posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def login_command(engine: str, settings, capabilities: dict | None = None) -> str:
    """Return a display-only command for the configured staff account; never include credentials."""
    name = str(engine or "").casefold()
    if name in {"claude", "claude_code"}:
        name, key, executable, suffix = "claude_code", "CLAUDE_CONFIG_DIR", "claude", "\n/login"
    elif name == "codex":
        key, executable, suffix = "CODEX_HOME", "codex login", ""
    else:
        return ""
    raw = getattr(settings.engines, name).env.get(key)
    if not raw:
        return executable + suffix
    path = _expanded_path(str(raw), capabilities)
    if path is None:
        return f"설정의 engines.{name}.env.{key} 경로로 로그인하세요\n원래 값: {raw}"
    if _runner_windows(capabilities):
        prefix = f'$env:{key}="{_powershell_quote(path)}"; '
    else:
        prefix = f"{key}={_posix_quote(path)} "
    return prefix + executable + suffix
