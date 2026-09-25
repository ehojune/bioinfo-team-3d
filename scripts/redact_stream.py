"""Deterministically sanitize captured CLI JSONL or stderr before committing fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

ID_KEYS = {"session_id", "thread_id", "conversation_id", "uuid", "hook_id"}
SECRET_KEYS = {"token", "api_key", "access_token", "refresh_token", "authorization", "password", "secret", "cookie"}
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
TOKEN = re.compile(r"\b(?:sk-[\w-]{12,}|gh[pousr]_[\w-]{12,}|github_pat_[\w-]{12,}|AIza[\w-]{20,}|AKIA[A-Z0-9]{16}|xox[abpr]-[\w-]{10,})\b")
WIN_PATH = re.compile(r"(?i)\b[A-Z]:[\\/](?:[^\\/\s\"'<>]+[\\/])*[^\\/\s\"'<>]*")
UNIX_PATH = re.compile(r"(?<!\w)/(?:Users|home|tmp|var/tmp)/[^\s\"'<>]+")
MCP_TOOL = re.compile(r"mcp__(?!labhq_)[A-Za-z0-9_-]+?__")
# init events describe the capturing machine (personal skills, agents, MCP servers). Keep only
# what the parsers and version checks need; replace inventories with their size.
INIT_KEEP = {"type", "subtype", "event", "session_id", "thread_id", "conversation_id", "model", "cwd",
             "permissionMode", "claude_code_version", "apiKeySource", "output_style", "uuid"}
INIT_COUNT = {"tools", "skills", "slash_commands", "terminal_slash_commands", "agents", "plugins"}


def _prune_init(obj: dict) -> dict:
    out = {}
    for key, value in obj.items():
        if key in INIT_KEEP:
            out[key] = value
        elif key in INIT_COUNT and isinstance(value, list):
            out[key] = f"<{len(value)} items>"
        elif key == "mcp_servers" and isinstance(value, list):
            out[key] = [s for s in value if isinstance(s, dict) and str(s.get("name", "")).startswith("labhq_")]
    return out


# Other Claude `system` events (commands_changed, hook_started/hook_response, ...) can carry the
# capturing user's slash-command descriptions or hook output. Keep identifiers and outcomes only.
SYSTEM_KEEP = {"type", "subtype", "session_id", "uuid", "hook_id", "hook_event", "hook_name", "outcome", "exit_code"}


def _prune_system(ev: dict) -> dict:
    out = {}
    for key, value in ev.items():
        if key in SYSTEM_KEEP:
            out[key] = value
        elif isinstance(value, list):
            out[key] = f"<{len(value)} items>"
        elif isinstance(value, str):
            out[key] = f"<{len(value)} chars>" if value else ""
        elif isinstance(value, dict):
            out[key] = f"<{len(value)} keys>"
    return out


def prune_event(ev):
    """Drop machine inventory from init and other system events (claude system/*, agy init)."""
    if not isinstance(ev, dict):
        return ev
    if ev.get("type") == "system" and ev.get("subtype") == "init":
        return _prune_init(ev)
    if ev.get("type") == "system":
        return _prune_system(ev)
    if ev.get("event") == "init" and isinstance(ev.get("init"), dict):
        return {**{k: v for k, v in ev.items() if k != "init"}, "init": _prune_init(ev["init"])}
    return ev


class Redactor:
    def __init__(self, *, home: str, tmp: str, workdir: str, username: str):
        self.ids: dict[str, str] = {}
        self.username = username
        self.paths = [(p, label) for p, label in
                      ((workdir, "<WORKDIR>"), (tmp, "<TMP>"), (home, "<HOME>")) if p]
        self.paths.sort(key=lambda pair: len(pair[0]), reverse=True)

    def identifier(self, value: str) -> str:
        if value not in self.ids:
            self.ids[value] = f"<ID_{len(self.ids) + 1}>"
        return self.ids[value]

    def string(self, value: str) -> str:
        # JSON has already decoded backslash escapes; handle ordinary and doubled slashes.
        value = value.replace("\\\\", "\\")
        for path, label in self.paths:
            variants = {path, path.replace("\\", "/"), path.replace("/", "\\")}
            for variant in sorted(variants, key=len, reverse=True):
                value = re.sub(re.escape(variant), lambda _: label, value, flags=re.I)
        value = WIN_PATH.sub("<PATH>", value)
        value = UNIX_PATH.sub("<PATH>", value)
        value = EMAIL.sub("<EMAIL>", value)
        value = TOKEN.sub("<REDACTED>", value)
        value = MCP_TOOL.sub("mcp__<external>__", value)
        value = UUID.sub(lambda m: self.identifier(m.group()), value)
        if self.username:
            value = re.sub(re.escape(self.username), "<USER>", value, flags=re.I)
        return value

    def object(self, value, key: str = ""):
        if isinstance(value, dict):
            return {k: self.object(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.object(v, key) for v in value]
        if isinstance(value, str):
            if key.lower() in SECRET_KEYS or key.lower().endswith("_token"):
                return "<REDACTED>"
            if key.lower() in ID_KEYS:
                return self.identifier(value)
            if key.lower() in {"server", "server_name", "mcp_server"} and not value.startswith("labhq_"):
                return "<external>"
            return self.string(value)
        return value

    def line(self, line: str) -> str:
        try:
            return json.dumps(self.object(prune_event(json.loads(line))), ensure_ascii=False)
        except json.JSONDecodeError:
            return self.string(line)


def redact_file(src: Path, dst: Path, redactor: Redactor) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8-sig")
    dst.write_text("\n".join(redactor.line(line) for line in text.splitlines()) + ("\n" if text else ""), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Redact a captured JSONL or stderr file for public fixtures")
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--home", default=str(Path.home()))
    p.add_argument("--tmp", default=tempfile.gettempdir())
    p.add_argument("--workdir", default=os.getcwd())
    p.add_argument("--username", default=os.environ.get("USERNAME") or os.environ.get("USER") or "")
    a = p.parse_args()
    redact_file(a.input, a.output, Redactor(home=a.home, tmp=a.tmp, workdir=a.workdir, username=a.username))


if __name__ == "__main__":
    main()
