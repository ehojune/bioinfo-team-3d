"""Runner-owned Atlas lookups. Staff get a task capability, never the API credential."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path

from fastapi import Header, HTTPException, Request
import httpx
import yaml

from ..tools.annot import LookupFailed
from ..tools.annot_alphagenome_atlas import AtlasBackend, KEY_ENV
from ..tools.annot_regulatory import RegulatoryAnnotator
from ..tools.external_wait import LookupTimeout, run_lookup

ENABLED_ENV = "LABHQ_ATLAS_BROKER_ENABLED"
_FIELDS = {"variants", "requested_scorers", "ontology_terms", "gene_ids", "top", "assembly"}


def refuse_credential_copy(settings, secrets: set[str]) -> None:
    """Do not reproduce literal keys in the staff config. Environment references remain supported."""
    if settings.config_path:
        from ..test_guard import guard_test_path
        guard_test_path(Path(settings.config_path))
        data = yaml.safe_load(Path(settings.config_path).read_text(encoding="utf-8"))
        def literal_key(value):
            if isinstance(value, dict):
                return any((str(k).casefold() == KEY_ENV.casefold() and isinstance(v, str) and v.strip()
                            and not (v.startswith("${") and v.endswith("}"))) or literal_key(v)
                           for k, v in value.items())
            return isinstance(value, list) and any(literal_key(v) for v in value)
        if literal_key(data):
            raise ValueError("Atlas keys must come from environment references")
        if any(key in json.dumps(data, ensure_ascii=False) for key in secrets):
            raise ValueError("Atlas keys must come from the environment, not literal staff config values")


@dataclass
class _Grant:
    ctx: object = field(repr=False)
    key: str = field(repr=False)
    pending: asyncio.Task | None = None


class AnnotBroker:
    def __init__(self, broker, settings):
        self.broker, self.settings = broker, settings
        self.grants: dict[str, _Grant] = {}
        self.pace: dict[str, float] = {}

        @broker.app.post("/annot/atlas")
        async def atlas(request: Request, x_labhq_token: str = Header(default="")):
            # Bound and authenticate before parsing. Identity and output roots never come from the caller.
            identity = broker._identity(x_labhq_token, {})
            raw = bytearray()
            async for part in request.stream():
                raw.extend(part)
                if len(raw) > 16_384:
                    raise HTTPException(413, "Atlas request too large")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                raise HTTPException(400, "invalid Atlas request") from None
            if not isinstance(body, dict):
                raise HTTPException(400, "invalid Atlas request")
            identity = broker._identity(x_labhq_token, body)
            args = {k: v for k, v in body.items() if k not in {"task_id", "agent_id", "request_id"}}
            if set(args) - _FIELDS or "variants" not in args:
                raise HTTPException(400, "only Atlas query parameters are accepted")
            grant = self.grants.get(identity["task_id"])
            if grant is None or grant.ctx.agent.id != identity["agent_id"]:
                raise HTTPException(403, "Atlas is not enabled for this active task")
            if self.broker.lookup_admission is not None or self.settings.annot.request_lookup_limit is not None:
                raise HTTPException(403, "AlphaGenome SDK requests cannot use a request-wide lookup limit")
            if grant.pending is not None:
                raise HTTPException(409, "an Atlas query is already running for this task")
            operation = asyncio.create_task(self._query(grant, args))
            grant.pending = operation

            async def disconnected():
                while not await request.is_disconnected():
                    await asyncio.sleep(0.1)
                operation.cancel()

            watch = asyncio.create_task(disconnected())
            try:
                return await operation
            except asyncio.CancelledError:
                operation.cancel()
                raise
            except (LookupFailed, LookupTimeout, ValueError):
                # No SDK exception or caller-supplied text crosses the credential boundary.
                raise HTTPException(502, "Atlas lookup failed; see the task's annotation/status records") from None
            except Exception:
                raise HTTPException(502, "Atlas lookup unavailable") from None
            finally:
                watch.cancel()
                operation.cancel()
                await asyncio.gather(watch, operation, return_exceptions=True)
                if grant.pending is operation:
                    grant.pending = None

    def register(self, ctx, key: str) -> None:
        if key and not ctx.read_only and "annot" in ctx.agent.builtin_mcp:
            self.grants[ctx.task.id] = _Grant(ctx, key)

    async def finish(self, task_id: str) -> None:
        grant = self.grants.pop(task_id, None)
        if grant is not None and grant.pending is not None:
            grant.pending.cancel()
            await asyncio.gather(grant.pending, return_exceptions=True)

    async def _query(self, grant: _Grant, args: dict) -> dict:
        backend = AtlasBackend(grant.key)
        ctx = grant.ctx

        async def notify(data):
            await ctx.emit("agent.log", {"level": "warn" if data["status"] == "timeout" else "info",
                                         "text": data["text"], "external_lookup": data})

        try:
            async with httpx.AsyncClient() as client:
                annotator = RegulatoryAnnotator(client, ctx.workdir,
                    self.settings.path(self.settings.runner.state_dir) / "annot_cache",
                    pace=self.pace, atlas=backend)

                async def query():
                    answer = await annotator.alphagenome_atlas(**args)
                    if answer.get("n_ok") == 0:
                        raise LookupFailed("Atlas lookup returned only errors")
                    return answer

                return await run_lookup(query, tool="alphagenome_atlas", workdir=ctx.workdir, notify=notify)
        finally:
            # Also covers SDK setup, deadline cancellation and task termination.
            backend.cancelled.set()
