"""Admit compact-report files on the runner-to-gateway result path."""

import asyncio

from ..models import Task, TaskResult
from ..recruit.provenance import restricted_task
from ..runner.report_artifacts import RESULT_FIELD, receive


async def admit(hub, runner_id, incarnation, ws, message):
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(data, dict) or RESULT_FIELD not in data or message.get("type") != "task.result":
        return message
    clean = {**message, "data": {key: value for key, value in data.items() if key != RESULT_FIELD}}
    if ws is not None and (hub.runners.get(runner_id) is not ws
                           or hub.runner_incarnations.get(runner_id) != incarnation):
        return clean
    tid = message.get("task_id")
    entry = hub.store.get("task", tid) if tid else None
    origin = [runner_id, incarnation or hub.runner_incarnations.get(runner_id)]
    if (not entry or entry.get("completed") or entry.get("report_runner") != origin
            or entry.get("request_id") != message.get("request_id")):
        return clean
    task = Task.model_validate(entry["payload"])
    result = TaskResult.model_validate(clean["data"])
    if (tid, task.agent_id) != (result.task_id, result.agent_id) or restricted_task(task, hub.s):
        return clean
    files = await asyncio.to_thread(receive, data[RESULT_FIELD], task.meta, result)
    if files:
        await hub.store.aput(RESULT_FIELD, tid, files)
    return clean
