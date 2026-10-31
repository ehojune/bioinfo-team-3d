"""A request that chose this PC's WSL gets the WSL runner even when another request's unfinished work keeps a native
runner as the agent's owner (#1204, follow-up of #612).

``agent_runner`` still holds one owner per agent. What changes is that a task pinned to WSL (`meta.wsl_pin`) is sent
to, resent to, recovered on and waited for at the WSL runner itself, and that its pin never decides the owner."""
import asyncio

import pytest

from labhq.gateway.server import Hub, _wsl_pin
from labhq.models import Task, TaskResult
from labhq.settings import Settings
from labhq.wsl_readiness import REQUIRED

from test_staff_control_steps import FakeWs

AGENT = "bioinfo-agent"
ROSTER = [{"id": AGENT, "engine": "claude_code"}]
WSL_CAPS = {"runtime_env": "wsl", "wsl_readiness": {key: True for key in REQUIRED}}


@pytest.fixture
def hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.runner_wait_s = 5
    hub = Hub(settings)
    for rid in ("r", "other"):
        hub.requests[rid] = {"id": rid, "text": "분석", "mode": "direct", "status": "running"}
        hub.save_request(rid)

    async def publish(event, **kwargs):
        pass

    hub.publish = publish
    return hub


def connect(hub, runner_id, wsl=False, ws=None, incarnation=None):
    ws = ws or FakeWs()
    hub.register_runner(runner_id, ws, ROSTER, incarnation or f"inc-{runner_id}",
                        WSL_CAPS if wsl else {"runtime_env": "windows"}, f"proc-{runner_id}")
    return ws


def native_keeps_the_owner(hub):
    """Another request's bioinfo-agent task is still unfinished on the native runner: it pins the owner there."""
    hub.store.put("task", "old", {"request_id": "other", "runner_id": "win", "runner_incarnation": "inc-win",
                                  "payload": {"agent_id": AGENT, "meta": {}}, "accepted": True, "kind": "direct",
                                  "dispatched_at": 1.0})
    hub._refresh_roster()
    assert hub.agent_runner[AGENT] == "win"


def both(hub, wsl_ws=None, wsl_incarnation=None):
    """The native runner is connected and its unfinished task keeps the owner; then the ready WSL runner connects."""
    win = connect(hub, "win")
    native_keeps_the_owner(hub)
    wsl = connect(hub, hub.s.runner.wsl.id, wsl=True, ws=wsl_ws, incarnation=wsl_incarnation)
    assert hub.agent_runner[AGENT] == "win", "the unfinished native task still decides the owner"
    return win, wsl


def task(request="r", **meta):
    return Task(agent_id=AGENT, request_id=request, prompt="p", meta={"kind": "direct", **meta})


def dispatched(ws):
    return [f for f in ws.frames if f.get("type") == "task.dispatch"]


async def until(condition, seconds=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while not condition():
        assert loop.time() < end, "condition never became true"
        await asyncio.sleep(0.01)


def finish(hub, t):
    hub.futures[t.id].set_result(TaskResult(task_id=t.id, agent_id=t.agent_id, ok=True))


async def test_a_wsl_request_gets_the_wsl_runner_while_a_native_task_keeps_the_owner(hub, monkeypatch):
    from labhq import wsl_setup

    monkeypatch.setattr(wsl_setup, "configured", lambda s: True)
    monkeypatch.setattr(wsl_setup, "check", lambda s: [])
    win, wsl = both(hub)
    hub.requests["r"]["run_where"] = "wsl"
    await asyncio.wait_for(hub.prepare_wsl_request("r"), 2)  # no five minute wait for an owner that will not change
    assert hub.requests["r"]["wsl_pinned"] is True and "run_where_note" not in hub.requests["r"]
    t = task()
    pending = asyncio.ensure_future(hub.dispatch(t))
    await until(lambda: dispatched(wsl))
    assert dispatched(win) == [], "the native runner never receives the pinned work"
    assert hub.task_runner[t.id] == hub.s.runner.wsl.id
    finish(hub, t)
    assert (await pending).ok is True


async def test_a_request_that_did_not_choose_wsl_still_goes_to_the_owner(hub):
    win, wsl = both(hub)
    t = task()
    pending = asyncio.ensure_future(hub.dispatch(t))
    await until(lambda: dispatched(win))
    assert dispatched(wsl) == [] and "session_runner" not in t.meta
    finish(hub, t)
    await pending


async def test_a_pinned_task_never_falls_back_to_the_native_owner_when_its_wsl_runner_is_away(hub):
    win = connect(hub, "win")
    native_keeps_the_owner(hub)
    hub.requests["r"]["wsl_pinned"] = True
    hub.s.gateway.runner_wait_s = 0.3
    result = await asyncio.wait_for(hub.dispatch(task()), 5)
    assert result.ok is False and result.error_kind == "runner_offline" and result.cost_usd == 0.0
    assert dispatched(win) == [], "it waited for the WSL runner and then stopped: nothing went native"


async def test_a_pinned_task_waits_for_the_wsl_runner_and_goes_there_when_it_connects(hub):
    win = connect(hub, "win")
    native_keeps_the_owner(hub)
    hub.requests["r"]["wsl_pinned"] = True
    t = task()
    pending = asyncio.ensure_future(hub.dispatch(t))
    await asyncio.sleep(0.2)
    assert not pending.done() and dispatched(win) == []
    wsl = connect(hub, "lab-wsl", wsl=True)
    await until(lambda: dispatched(wsl))
    assert dispatched(win) == []
    finish(hub, t)
    await pending


async def test_a_resend_after_a_lost_socket_goes_to_the_pinned_runner_not_the_owner(hub):
    hub.requests["r"]["wsl_pinned"] = True
    win, _ = both(hub, wsl_ws=FakeWs(up=False), wsl_incarnation="inc-wsl")
    t = task()
    pending = asyncio.ensure_future(hub.dispatch(t))
    await asyncio.sleep(0.3)
    assert not pending.done(), "the first frame did not leave: the task waits for the same runner to come back"
    fresh = FakeWs()
    connect(hub, "lab-wsl", wsl=True, ws=fresh, incarnation="inc-wsl")
    await until(lambda: dispatched(fresh))
    assert dispatched(win) == []
    finish(hub, t)
    await pending


async def test_a_resend_is_uncertain_not_native_when_the_pinned_runner_came_back_as_a_new_process(hub):
    hub.requests["r"]["wsl_pinned"] = True
    win, _ = both(hub, wsl_ws=FakeWs(up=False), wsl_incarnation="inc-1")
    t = task()
    pending = asyncio.ensure_future(hub.dispatch(t))
    await asyncio.sleep(0.3)
    connect(hub, "lab-wsl", wsl=True, ws=FakeWs(), incarnation="inc-2")
    result = await asyncio.wait_for(pending, 5)
    assert result.ok is False and "unknown" in result.error and dispatched(win) == []


async def test_recovery_resends_an_unaccepted_pinned_task_to_the_wsl_runner_only(hub):
    win, wsl = both(hub)
    prior = task(session_runner="lab-wsl", wsl_pin=True)
    entry = {"request_id": "r", "runner_id": "lab-wsl", "runner_incarnation": "inc-lab-wsl",
             "payload": prior.model_dump(mode="json"), "accepted": False, "kind": "direct", "dispatched_at": 2.0}
    hub.store.put("task", prior.id, entry)
    pending = asyncio.ensure_future(hub._await_prior_task(prior.id, entry, AGENT, "r", "direct"))
    await until(lambda: dispatched(wsl))
    assert dispatched(win) == []
    hub.futures[prior.id].set_result(TaskResult(task_id=prior.id, agent_id=AGENT, ok=True))
    assert (await pending).ok is True


async def test_a_pinned_unfinished_row_never_makes_its_runner_the_owner_and_an_ordinary_one_still_does(hub):
    connect(hub, "win"), connect(hub, "lab-wsl", wsl=True)
    hub.store.put("task", "pinned", {"request_id": "r", "runner_id": "win", "runner_incarnation": "inc-win",
                                     "payload": {"agent_id": AGENT, "meta": {"session_runner": "win", "wsl_pin": True}},
                                     "accepted": True, "kind": "direct", "dispatched_at": 1.0})
    hub.agent_runner = {}  # the ledger is read when the owner changes
    hub._refresh_roster()
    assert hub.agent_runner[AGENT] == "lab-wsl", "a pinned row says nothing about who owns the agent"
    hub.store.put("task", "plain", {"request_id": "r", "runner_id": "win", "runner_incarnation": "inc-win",
                                    "payload": {"agent_id": AGENT, "meta": {"session_runner": "win"}},
                                    "accepted": True, "kind": "direct", "dispatched_at": 2.0})
    hub.agent_runner = {}  # the ledger is read when the owner changes
    hub._refresh_roster()
    assert hub.agent_runner[AGENT] == "win", "a resumed session's row keeps the old rule"


async def test_the_session_of_a_pinned_task_is_waited_for_on_its_own_runner(hub):
    both(hub)
    held = {"request_id": "r", "runner_id": "lab-wsl", "runner_incarnation": "inc-lab-wsl", "accepted": True,
            "payload": {"agent_id": AGENT, "resume_session_id": "s1",
                        "meta": {"session_runner": "lab-wsl", "wsl_pin": True, "workdir": "/w"}},
            "kind": "direct", "dispatched_at": 3.0}
    hub.store.put("task", "held", held)
    assert _wsl_pin(held["payload"]["meta"]) == "lab-wsl"
    assert hub._holder_runner(AGENT, held) == "lab-wsl" and hub._holder_runner(AGENT, {"payload": {}}) == "win"
    waiter = asyncio.ensure_future(hub.wait_session_free(AGENT, "s1", "/w", request_id="r"))
    await asyncio.sleep(0.1)
    assert not waiter.done(), "accepted on the connected WSL runner of this generation: it holds the session"
    hub.store.put("task", "held", {**held, "completed": True, "result": {"session_id": "s2"}})
    hub._session_state_changed()
    assert (await asyncio.wait_for(waiter, 3))[0] == "s2"
