"""Read-only visibility diagnostics; never a cached authorization to publish."""

from __future__ import annotations

import asyncio
import os

import httpx

from ..settings import Settings
from .github import GitHubClient


async def dev_log_visibility(settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                             *, timeout_s: float = 5.0) -> str:
    if not settings.dev_log.enabled or not settings.dev_log.repo:
        return "skipped"
    token = os.environ.get(settings.github.token_env, "")
    if not token:
        return "unknown"

    async def lookup() -> str:
        gh = GitHubClient(token, settings.github.api_url, transport)
        async with gh.http:
            meta = await gh._req("GET", f"/repos/{settings.dev_log.repo}")
        if not isinstance(meta, dict):
            return "unknown"
        visibility = meta.get("visibility")
        if visibility == "public":
            return "public"
        if visibility == "private" or visibility is None and meta.get("private") is True:
            return "private"
        if visibility is None and meta.get("private") is False:
            return "public"
        return "unknown"

    try:
        # httpx's per-operation timeout does not bound a slow streamed response.
        return await asyncio.wait_for(lookup(), timeout=timeout_s)
    except Exception:
        # URLs, exception messages and remote bodies may contain credentials.
        return "unknown"
