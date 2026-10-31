"""A repeated POST with the same `Idempotency-Key` is applied once (#1164).

A double click or an HTTP retry of `contract extend`, `recruit`, a new request or a Codex review comment would apply
it twice. A caller that sends a key gets the first call's response again; a caller without one is served as before.
The memory is in this process, so it covers the seconds of a double click or a retry, not a gateway restart.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional, get_type_hints

from fastapi import HTTPException

TTL_S = 600.0
MAX_ENTRIES = 512
MAX_KEY_CHARS = 64
_AMBIGUOUS = object()


class Idempotency:
    def __init__(self, ttl_s: float = TTL_S, max_entries: int = MAX_ENTRIES, clock: Callable[[], float] = time.monotonic):
        self.ttl_s, self.max_entries, self.clock = ttl_s, max_entries, clock
        self._done: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._running: dict[tuple, asyncio.Task] = {}

    async def run(self, scope: str, key: str | None, body: Any, work: Callable[[], Awaitable[Any]]) -> Any:
        """`work()` once per (scope, key, body); a call without a key just runs it.

        The same key with a different body is a different request, not a replay. A refusal (`HTTPException`: nothing was
        applied) is not remembered, so the caller may retry. Any other failure is ambiguous (the action may have reached
        the runner or GitHub before the answer failed): it is remembered, and the same key gets a 409 instead of a
        second run. The work is not cancelled when the first caller goes away: a retry then waits for it.
        """
        key = (key or "").strip()
        if not key:
            return await work()
        if len(key) > MAX_KEY_CHARS:  # cutting it would make two different keys one
            raise HTTPException(422, f"Idempotency-Key는 {MAX_KEY_CHARS}자까지입니다")
        ident = (scope, key, json.dumps(body, sort_keys=True, default=str))
        self._expire()
        if ident in self._done:
            return self._replay(self._done[ident][1])
        task = self._running.get(ident)
        if task is None:
            task = self._running[ident] = asyncio.ensure_future(self._finish(ident, work))
        return await asyncio.shield(task)  # a waiter sees the first call's outcome, a failure included

    async def _finish(self, ident: tuple, work: Callable[[], Awaitable[Any]]) -> Any:
        try:
            result = await work()
        except HTTPException:
            raise
        except Exception:
            self._remember(ident, _AMBIGUOUS)
            raise
        finally:
            self._running.pop(ident, None)
        self._remember(ident, result)
        return result

    def _remember(self, ident: tuple, result: Any) -> None:
        self._done[ident] = (self.clock() + self.ttl_s, result)
        while len(self._done) > self.max_entries:
            self._done.popitem(last=False)

    @staticmethod
    def _replay(result: Any) -> Any:
        if result is _AMBIGUOUS:
            raise HTTPException(409, "이 키의 이전 요청이 어디까지 적용됐는지 알 수 없습니다. 상태를 확인한 뒤 새 키로 다시 보내세요")
        return result

    def _expire(self) -> None:
        now = self.clock()
        for ident in [i for i, (until, _) in self._done.items() if until <= now]:
            del self._done[ident]


def idempotent_routes(memory: Idempotency):
    """A route decorator: the route takes an optional `Idempotency-Key` header and runs once per (route, key, arguments).

    The route function itself is unchanged, so it stays testable on its own and sees no extra argument.
    """
    from fastapi import Header

    def decorate(route):
        signature, hints = inspect.signature(route), get_type_hints(route)
        # FastAPI resolves a string annotation in the wrapper's module, so the route's own are resolved here.
        params = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in signature.parameters.values()]
        params.append(inspect.Parameter("idempotency_key", inspect.Parameter.KEYWORD_ONLY, default=Header(default=None),
                                        annotation=Optional[str]))

        @functools.wraps(route)
        async def wrapper(*args, idempotency_key: str | None = None, **kwargs):
            call = {name: (v.model_dump() if hasattr(v, "model_dump") else v) for name, v in kwargs.items()}
            return await memory.run(route.__name__, idempotency_key, call, lambda: route(*args, **kwargs))

        wrapper.__signature__ = signature.replace(parameters=params, return_annotation=hints.get("return", inspect.Signature.empty))
        return wrapper

    return decorate
