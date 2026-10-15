"""One lexical boundary for the local Windows/WSL shared filesystem.

Keep component case (drvfs can contain case-sensitive directories); normalize the
drive letter and separators. UNC and Linux-only absolute paths cannot be shared.
"""
from __future__ import annotations

import re


def shared_path(value: str, target: str) -> str:
    path = value.replace("\\", "/")
    match = re.fullmatch(r"([A-Za-z]):/(.*)", path) or re.fullmatch(r"/mnt/([A-Za-z])(?:/(.*))?", path)
    if not match:
        raise ValueError("path is not on a shared Windows drive")
    drive, tail = match.group(1), match.group(2) or ""
    parts = []
    for part in tail.split("/"):
        if part == "..":
            if not parts:
                raise ValueError("path escapes shared drive")
            parts.pop()
        elif part and part != ".":
            parts.append(part)
    prefix = f"/mnt/{drive.lower()}/" if target == "wsl" else f"{drive.upper()}:/"
    return prefix + "/".join(parts)


def message_paths(value, target: str, key: str = ""):
    """Translate structured path fields, never prompts, shell commands or result text."""
    singles = {"workdir", "source_workdir", "cwd", "workspace_root"}
    lists = {"upstream_dirs", "outputs", "missing_outputs", "unreported_outputs"}
    mappings = {"upstream_steps", "output_sha256", "output_types", "output_tool_use_ids"}

    def path(raw):
        if not isinstance(raw, str):
            return raw
        if raw.startswith("/") or re.match(r"^[A-Za-z]:|^\\", raw):
            return shared_path(raw, target)
        return raw  # outputs are normally relative to the workdir

    if isinstance(value, dict):
        if key == "upstream_steps":
            return {k: path(v) for k, v in value.items()}
        if key in mappings:
            return {path(k): v for k, v in value.items()}
        return {k: message_paths(v, target, k) for k, v in value.items()}
    if isinstance(value, list):
        return [path(v) if key in lists else message_paths(v, target) for v in value]
    return path(value) if key in singles else value
