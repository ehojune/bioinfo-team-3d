from __future__ import annotations

import json
import os
import re
import socket
import tempfile
from pathlib import Path
from typing import Any


CLAUDE_ENV_PASSTHROUGH = frozenset({
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
})


def parent_claude_markers(env: dict[str, str]) -> list[str]:
    """Claude host-session variables that must not reach staff subprocesses."""
    return [key for key in env if (key.upper().startswith("CLAUDE_") or
                                    key.upper().startswith("CLAUDECODE"))
            and key.upper() not in CLAUDE_ENV_PASSTHROUGH]


def strip_parent_claude_env(env: dict[str, str]) -> dict[str, str]:
    blocked = set(parent_claude_markers(env))
    return {key: value for key, value in env.items() if key not in blocked}


def atomic_write_text(path: str | Path, text: str) -> None:
    """Replace a text file from a same-directory temporary file."""
    target = Path(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as out:
            temporary = Path(out.name)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def short(obj: Any, n: int = 300) -> str:
    """Compact one-line preview of any object, for event payloads and logs."""
    if obj is None:
        return ""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def clip(text: str | None, n: int) -> str:
    """Keep head and tail of long text (tails usually hold the conclusion)."""
    if not text:
        return ""
    if len(text) <= n:
        return text
    head = int(n * 0.6)
    tail = n - head - 40
    return f"{text[:head]}\n\n[… {len(text) - head - tail} chars clipped …]\n\n{text[-tail:]}"


def slugify(s: str, max_len: int = 40) -> str:
    s = re.sub(r"\.git$", "", s.strip().rstrip("/"))
    s = s.split("/")[-1] or s
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s or "contract")[:max_len]


def extract_json(text: str | None) -> Any | None:
    """Best-effort: parse a JSON object from model output (whole text, fenced block, or last object)."""
    if not text:
        return None
    t = text.strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    for block in reversed(re.findall(r"```(?:json)?\s*(.*?)```", t, flags=re.S)):
        try:
            return json.loads(block)
        except ValueError:
            continue
    dec = json.JSONDecoder()
    best = None
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, end = dec.raw_decode(t[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and (best is None or end > best[1]):
            best = (obj, end)
    return best[0] if best else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def output_relpath(name: str) -> str | None:
    """A declared step output as `outputs/...` (POSIX, lexically normalized), or None if it leaves outputs/.

    Runner and orchestrator both use this, so `./t.tsv`, `outputs\t.tsv` and `outputs/x/../t.tsv` match.
    """
    s = str(name).replace("\\", "/")
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        return None
    import posixpath

    n = posixpath.normpath(s)
    if n in (".", "..") or n.startswith("../"):
        return None
    return n if n == "outputs" or n.startswith("outputs/") else f"outputs/{n}"
