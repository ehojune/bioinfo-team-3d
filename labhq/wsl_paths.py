"""One lexical boundary for the local Windows/WSL shared filesystem.

Keep component case (drvfs can contain case-sensitive directories); normalize the
drive letter and separators. UNC and Linux-only absolute paths cannot be shared.
"""
from __future__ import annotations

import re
from typing import get_args, get_type_hints

from .models import PathShape, RunnerPathFields


# Derived from the model, never maintain a second list at a call site.
PATH_FIELDS = {name: shape for name, hint in get_type_hints(RunnerPathFields, include_extras=True).items()
               for shape in get_args(hint) if isinstance(shape, PathShape)}


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


def message_paths(value, target: str, key: str = "", *, _parent=None):
    """Copy a wire message into the consumer's path view using the model contract.

    The gateway's canonical shared-drive view is Windows, on every gateway OS.
    Dispatch selects the actual consumer runtime; Linux-only paths and opaque
    content are preserved. Filesystem access must still enforce its own roots.
    """
    if target not in {"wsl", "windows"}:
        return value
    rule = PATH_FIELDS.get(key)
    if rule and rule.when and (_parent or {}).get(rule.when[0]) != rule.when[1]:
        return value
    shape = rule.kind if rule else None
    if shape == "opaque":
        return value

    def path(raw):
        if not isinstance(raw, str):
            return raw
        if re.match(r"^(?:[A-Za-z]:[/\\]|/mnt/[A-Za-z](?:/|$))", raw):
            return shared_path(raw, target)
        return raw  # relative outputs and paths private to a native Linux runner

    if isinstance(value, dict):
        if shape == "values":
            return {k: path(v) for k, v in value.items()}
        if shape == "keys":
            return {path(k): v for k, v in value.items()}
        return {k: message_paths(v, target, k, _parent=value) for k, v in value.items()}
    if isinstance(value, list):
        return [path(v) if shape == "items" else message_paths(v, target) for v in value]
    return path(value) if shape == "scalar" else value
