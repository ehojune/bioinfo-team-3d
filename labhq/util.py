from __future__ import annotations

import json
import re
import socket
from typing import Any


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
