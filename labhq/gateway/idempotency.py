"""A repeated POST with the same `Idempotency-Key` is applied once (#1164).

A double click or an HTTP retry of `contract extend`, `recruit`, a new request or a Codex review comment would apply
it twice. A caller that sends a key gets the first call's response again; a caller without one is served as before.
The memory is in this process, so it covers the seconds of a double click or a retry, not a gateway restart.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

TTL_S = 600.0
MAX_ENTRIES = 512
MAX_KEY_CHARS = 64


class Idempotency:
    def __init__(self, ttl_s: float = TTL_S, max_entries: int = MAX_ENTRIES, clock: Callable[[], float] = time.monotonic):
        self.ttl_s, self.max_entries, self.clock = ttl_s, max_entries, clock
        self._done: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._running: dict[tuple, asyncio.Task] = {}

    async def run(self, scope: str, key: str | None, body: Any, work: Callable[[], Awaitable[Any]]) -> Any:
        """`work()` once per (scope, key, body); a call without a key just runs it.

        The same key with a different body is a different request, not a replay. An exception is not remembered, so
        the caller may retry. The work is not cancelled when the first caller goes away: a retry then waits for it.
        """
        key = (key or "").strip()[:MAX_KEY_CHARS]
        if not key:
            return await work()
        ident = (scope, key, json.dumps(body, sort_keys=True, default=str))
        self._expire()
        if ident in self._done:
            return self._done[ident][1]
        task = self._running.get(ident)
        if task is None:
            task = self._running[ident] = asyncio.ensure_future(self._finish(ident, work))
        return await asyncio.shield(task)

    async def _finish(self, ident: tuple, work: Callable[[], Awaitable[Any]]) -> Any:
        try:
            result = await work()
            self._done[ident] = (self.clock() + self.ttl_s, result)
            while len(self._done) > self.max_entries:
                self._done.popitem(last=False)
            return result
        finally:
            self._running.pop(ident, None)

    def _expire(self) -> None:
        now = self.clock()
        for ident in [i for i, (until, _) in self._done.items() if until <= now]:
            del self._done[ident]
