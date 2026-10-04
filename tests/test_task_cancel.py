"""A task cancel is kept until the task ends, and resent when its runner reconnects (PR #396 review)."""
import time

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import TASK_CANCEL_KEEP_S, Hub, create_app
from labhq.models import RunnerUnavailable
from labhq.settings import Settings


def _hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "lit_scout"}})
    return hub


@pytest.mark.asyncio
async def test_a_cancel_sent_while_the_runner_is_away_is_resent_when_it_reconnects(tmp_path):
    hub = _hub(tmp_path)
    sent, online = [], {"pc-a": False}

    async def send_runner(runner_id, message):
        if not online[runner_id]:
            raise RunnerUnavailable(f"runner {runner_id} is offline")
        sent.append((runner_id, message))

    hub.send_runner = send_runner
    assert await hub.cancel_task("t1") is True
    assert sent == [] and hub.store.get("task_cancel", "t1")["runner_id"] == "pc-a"

    online["pc-a"] = True
    await hub.flush_task_cancels("pc-a")
    assert sent == [("pc-a", {"type": "task.cancel", "task_id": "t1"})]
    await hub.flush_task_cancels("pc-b")  # another runner's reconnect does not get it
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_kept_cancel_ends_with_its_task(tmp_path):
    hub = _hub(tmp_path)
    sent = []

    async def send_runner(runner_id, message):
        sent.append(message["task_id"])

    hub.send_runner = send_runner
    await hub.cancel_task("t1")
    await hub.on_runner_message("pc-a", {"type": "task.result", "request_id": "r", "task_id": "t1",
                                         "data": {"task_id": "t1", "agent_id": "lit_scout", "ok": False,
                                                  "error": "cancelled"}}, None, None)
    assert hub.store.get("task_cancel", "t1") is None

    # A task that reported its result drops a kept cancel on the next flush; so does one kept past its age limit.
    now = time.time()
    hub.store.put("task_cancel", "t2", {"runner_id": "pc-a", "requested_at": now})
    hub.store.put("task", "t2", {"request_id": "r", "runner_id": "pc-a", "completed": True, "result": {}})
    hub.store.put("task_cancel", "t4", {"runner_id": "pc-a", "requested_at": now - TASK_CANCEL_KEEP_S - 1})
    hub.store.put("task", "t4", {"request_id": "r", "runner_id": "pc-a", "completed": True, "abandoned": True})
    sent.clear()
    await hub.flush_task_cancels("pc-a")
    assert sent == [] and hub.store.get("task_cancel", "t2") is None and hub.store.get("task_cancel", "t4") is None


@pytest.mark.asyncio
async def test_an_abandoned_task_keeps_its_cancel_for_an_older_runner_process(tmp_path):
    # PR #397 review: a newer process of runner pc-a marks the task abandoned while the older process may still run
    # it and reconnect; the cancel must survive until a result arrives.
    hub = _hub(tmp_path)
    sent = []

    async def send_runner(runner_id, message):
        sent.append(message["task_id"])

    hub.send_runner = send_runner
    hub.store.put("task", "t1", {**hub.store.get("task", "t1"), "completed": True, "abandoned": True})
    assert await hub.cancel_task("t1") is True  # the PI may cancel an abandoned task too
    assert hub.store.get("task_cancel", "t1") is not None
    sent.clear()
    await hub.flush_task_cancels("pc-a")
    await hub.flush_task_cancels("pc-a")
    assert sent == ["t1", "t1"] and hub.store.get("task_cancel", "t1") is not None


@pytest.mark.asyncio
async def test_cancel_of_an_unknown_or_finished_task(tmp_path):
    hub = _hub(tmp_path)
    assert await hub.cancel_task("nope") is False
    hub.store.put("task", "t3", {"request_id": "r", "runner_id": "pc-a", "completed": True})
    assert await hub.cancel_task("t3") is True
    assert hub.store.get("task_cancel", "t3") is None


def test_cancel_endpoint_finds_a_task_from_the_ledger_after_a_restart(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub  # task_runner is empty, as after a gateway restart; the ledger still names the runner
    hub.store.put("task", "t1", {"request_id": "r", "runner_id": "pc-a", "payload": {"agent_id": "lit_scout"}})
    sent = []

    async def send_runner(runner_id, message):
        sent.append((runner_id, message["task_id"]))

    hub.send_runner = send_runner
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    assert client.post("/api/tasks/t1/cancel", headers=headers).json() == {"ok": True}
    assert sent == [("pc-a", "t1")]
    assert client.post("/api/tasks/nope/cancel", headers=headers).status_code == 404


@pytest.mark.asyncio
async def test_a_failed_briefing_keeps_the_scout_cancel_for_an_away_runner(monkeypatch):
    # PR #396 review: the scout's runner dropped right after accepting; the early-finish cancel must be kept and
    # resent on reconnect instead of being lost with the first RunnerUnavailable.
    from labhq.orchestrator import cso
    from tests.test_topic_checklists_precedents import EarlyEndHub
    monkeypatch.setattr(cso, "PRECEDENT_CANCEL_GRACE_S", 0.05)
    hub = EarlyEndHub(runner_answers_cancel=False)
    kept = []

    async def cancel_task(tid):
        kept.append(tid)
        return True

    hub.cancel_task = cancel_task
    await cso.Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "failed"
    assert len(kept) == 1 and hub.scout_reaped
