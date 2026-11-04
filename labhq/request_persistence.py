"""Awaitable persistence for the gateway and its lightweight bench/test adapters."""
from __future__ import annotations

import copy


_MISSING = object()


def apply_changes(current: dict, before: dict, after: dict) -> None:
    """Apply only this checkpoint's changed fields; retain newer live mutations."""
    for key in before.keys() | after.keys():
        old, new = before.get(key, _MISSING), after.get(key, _MISSING)
        if old == new:
            continue
        live = current.get(key, _MISSING)
        if isinstance(old, dict) and isinstance(new, dict) and isinstance(live, dict):
            apply_changes(live, old, new)
        elif live == old:
            if new is _MISSING:
                current.pop(key, None)
            else:
                current[key] = copy.deepcopy(new)


async def persist_request(hub, rid: str) -> None:
    asynchronous = getattr(hub, "asave_request", None)
    if asynchronous is not None:
        await asynchronous(rid)
    else:
        # In-memory bench/test hubs have no SQLite writer.
        hub.save_request(rid)


async def save_result(results, key, value):
    asynchronous = getattr(results, "aset", None)
    if asynchronous is not None:
        await asynchronous(key, value)
    else:
        results[key] = value


async def update_request(hub, rid, update):
    asynchronous = getattr(hub, "aupdate_request", None)
    if asynchronous is not None:
        return await asynchronous(rid, update)
    answer = update(hub.requests[rid])
    await persist_request(hub, rid)
    return answer


async def remove_result(results, key, *default):
    asynchronous = getattr(results, "apop", None)
    if asynchronous is not None:
        return await asynchronous(key, *default)
    return results.pop(key, *default)

async def record_task_cost(hub, *args, **kwargs):
    asynchronous = getattr(hub, "_arecord_task_cost", None)
    if asynchronous is not None:
        return await asynchronous(*args, **kwargs)
    return hub._record_task_cost(*args, **kwargs)


async def create_revised_request(hub, *args, **kwargs):
    asynchronous = getattr(hub, "acreate_revised_request", None)
    if asynchronous is not None:
        return await asynchronous(*args, **kwargs)
    return hub.create_revised_request(*args, **kwargs)
