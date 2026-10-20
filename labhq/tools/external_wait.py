"""Bound automatic public lookups without touching staff processes or PI decisions."""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

from ..adapters.owned import append_owned, read_owned, write_owned

TIMEOUT_S = 20 * 60
BUDGET_FILE = ".labhq/external_lookup_budget.json"
STATUS_FILE = "outputs/external_lookup_status.jsonl"


class LookupTimeout(TimeoutError):
    pass


async def exclude_human_wait(operation, *, workdir: Path | None, clock=time.time):
    """Exclude only the explicit PDF decision wait, retaining the spent automatic budget."""
    started = clock()
    try:
        return await operation()
    finally:
        if workdir is not None:
            saved = read_owned(workdir, BUDGET_FILE)
            if saved is not None:
                state = json.loads(saved)
                stamp = float(state["started_at"])
                if not math.isfinite(stamp) or stamp > clock():
                    raise ValueError("외부 조회 대기 기록이 올바르지 않습니다")
                state["started_at"] = stamp + max(0, clock() - started)
                write_owned(workdir, BUDGET_FILE, json.dumps(state) + "\n")


async def run_lookup(operation, *, tool: str, workdir: Path | None, notify=None,
                     timeout_s: float = TIMEOUT_S, progress_s: float = 60,
                     clock=time.time):
    """One task-wide deadline, retained across retries/MCP restarts; human approval is excluded."""
    started = clock()
    if workdir is not None:
        saved = read_owned(workdir, BUDGET_FILE)
        if saved is not None:
            state = json.loads(saved)
            started = float(state["started_at"])
            if not math.isfinite(started) or started > clock():
                raise ValueError("외부 조회 대기 기록이 올바르지 않습니다")
        else:
            write_owned(workdir, BUDGET_FILE, json.dumps({"started_at": started}) + "\n")

    async def record(status):
        elapsed = max(0, clock() - started)
        label = {"waiting": "외부 조회 대기 중", "ok": "외부 조회 완료",
                 "timeout": "조회 불가(시간 초과)", "failed": "외부 조회 실패",
                 "cancelled": "외부 조회 취소"}[status]
        data = {"tool": tool, "status": status, "elapsed_s": elapsed, "timeout_s": timeout_s,
                "text": f"{tool}: {label} · 경과 {elapsed:.0f}초 / 상한 {timeout_s:.0f}초"}
        if workdir is not None:
            append_owned(workdir, STATUS_FILE, json.dumps(data, ensure_ascii=False) + "\n")
        if notify is not None:
            try:
                await asyncio.wait_for(notify(data), min(5, timeout_s))
            except Exception:
                pass  # Display outages must not extend or reset the local deadline.

    stopped = False

    async def progress():
        # The flag, not only the cancel, ends this loop: Python 3.10's wait_for drops a cancel that lands while
        # `record`'s notify completes, and the loop then ran forever (PR #800 3.10 CI hang).
        while not stopped:
            await asyncio.sleep(progress_s)
            if not stopped:
                await record("waiting")

    await record("waiting")
    remaining = min(timeout_s, timeout_s - (clock() - started))
    if remaining <= 0:
        await record("timeout")
        raise LookupTimeout(f"조회 불가(시간 초과): 단계 외부 조회 상한 {timeout_s:.0f}초. "
                            f"재시도하지 말고 남은 작업을 이어 가며 보고서에 빠진 조회와 {STATUS_FILE}을 명시하세요.")
    watch = asyncio.create_task(progress())
    try:
        try:
            result = await asyncio.wait_for(operation(), remaining)
        finally:
            stopped = True
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
    except asyncio.TimeoutError as exc:
        await record("timeout")
        raise LookupTimeout(f"조회 불가(시간 초과): {tool}. 재시도하지 말고 남은 작업을 이어 가며 "
                            f"보고서에 빠진 조회와 {STATUS_FILE}을 명시하세요.") from exc
    except asyncio.CancelledError:
        await record("cancelled")
        raise
    except Exception as exc:
        await record("timeout" if getattr(exc, "error_kind", None) == "timeout" else "failed")
        raise
    else:
        await record("ok")
        return result
