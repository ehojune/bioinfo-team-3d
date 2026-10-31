"""A request that chose this PC's WSL gets the WSL runner even when another request's unfinished work keeps a native
runner as the agent's owner (#1204, follow-up of #612).

``agent_runner`` still holds one owner per agent. What changes is that a task pinned to WSL (`meta.wsl_pin`) is sent
to, resent to, recovered on and waited for at the WSL runner itself, and that its pin never decides the owner."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from labhq.gateway.server import Hub, _request_ready, _wsl_pin
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator, format_capabilities, hub_agents, hub_runner
from labhq.models import Task, TaskResult
from labhq.settings import Settings
from labhq.wsl_readiness import REQUIRED

from test_staff_control_steps import FakeWs

AGENT = "bioinfo-agent"
ROSTER = [{"id": AGENT, "engine": "claude_code"}]
WSL_CAPS = {"runtime_env": "wsl", "wsl_readiness": {key: True for key in REQUIRED}, "scheduler": "sge"}


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


# ---- the sweep: every place that picks a runner, staff entry or capability for a request goes through one helper ----
# (Hub.request_runner / request_agents / _request_ready, cso.hub_runner / hub_agents). One test per place found.

def pinned_request_with_native_owner(hub):
    both(hub)
    hub.requests["r"]["wsl_pinned"] = True  # "other" is the request that did not choose WSL


async def test_sweep_plan_roster_and_capabilities_show_the_wsl_runner_to_a_pinned_request(hub):
    pinned_request_with_native_owner(hub)
    assert hub.agents[AGENT]["runner_id"] == "win" and hub.agents[AGENT]["scheduler"] == "none"
    mine, other = hub_agents(hub, "r"), hub_agents(hub, "other")
    assert mine[AGENT]["runner_id"] == "lab-wsl" and mine[AGENT]["scheduler"] == "sge"
    assert other[AGENT]["runner_id"] == "win", "a request that did not choose WSL keeps the owner's entry"
    text = format_capabilities(list(mine.values()), hub.runner_capabilities, run_where="wsl")
    assert "runner lab-wsl" in text and "로컬 WSL" in text and "runner win" not in text


async def test_sweep_first_runner_gate_does_not_wait_for_an_owner_that_is_away(hub, monkeypatch):
    from labhq import wsl_setup

    monkeypatch.setattr(wsl_setup, "configured", lambda s: True)
    monkeypatch.setattr(wsl_setup, "check", lambda s: [])
    win, wsl = both(hub)
    hub.unregister_runner("win", win)  # the owner is pinned to the native runner by its unfinished task, and it is away
    assert hub.agent_runner[AGENT] == "win" and "win" not in hub.runners
    started = []

    async def run_request(rid):
        started.append(rid)

    hub.orchestrator = SimpleNamespace(run_request=run_request, _finish=None)
    hub.requests["r"].update(agent_id=AGENT, run_where="wsl")
    hub.s.gateway.runner_wait_s = 86400
    await asyncio.wait_for(hub._start_request("r"), 3)
    assert started == ["r"] and hub.requests["r"]["wsl_pinned"] is True, "planning started on the WSL runner"


async def test_sweep_login_wait_names_the_runner_the_request_uses(hub):
    pinned_request_with_native_owner(hub)
    now = time.time()
    parked = asyncio.ensure_future(hub.wait_login("r", "s1", "claude", resume_at=now + 0.3, deadline_at=now + 0.6,
                                                  reason="login", agent_id=AGENT))
    await asyncio.sleep(0.1)
    assert hub.requests["r"]["login_waits"]["s1"]["runner_id"] == "lab-wsl"
    parked.cancel()
    await asyncio.gather(parked, return_exceptions=True)


async def test_sweep_resume_counts_a_pinned_agent_as_missing_while_its_wsl_runner_is_away(hub):
    win, wsl = both(hub)
    hub.requests["r"]["wsl_pinned"] = True
    assert _request_ready(hub, AGENT, "r") and _request_ready(hub, AGENT, "other")
    hub.unregister_runner("lab-wsl", wsl)
    assert _request_ready(hub, AGENT, "other"), "the native owner is up for a request that did not choose WSL"
    assert not _request_ready(hub, AGENT, "r"), "the pinned request waits for WSL, the owner does not stand in"


async def test_sweep_environment_binding_sees_the_wsl_runner_for_a_pinned_request(hub, monkeypatch):
    pinned_request_with_native_owner(hub)
    seen = {}
    monkeypatch.setattr(cso, "split_runner_environments", lambda steps, live, runtimes, can_create:
                        seen.update(live=dict(live), runtimes=dict(runtimes)) or steps)
    monkeypatch.setattr(cso, "validate_environment_bindings", lambda result: None)
    fake = SimpleNamespace(hub=hub, _environment_rid="r")
    Orchestrator._prepare_runner_environments(fake, [])
    assert seen["live"][AGENT] == "lab-wsl" and seen["runtimes"]["lab-wsl"] == "wsl"
    fake._environment_rid = "other"
    Orchestrator._prepare_runner_environments(fake, [])
    assert seen["live"][AGENT] == "win"


async def test_sweep_ask_origin_and_consult_runner_follow_the_request(hub):
    pinned_request_with_native_owner(hub)
    assert hub_runner(hub, AGENT, "r") == "lab-wsl", "the ask origin and the consult same-runner check read this"
    assert hub_runner(hub, AGENT, "other") == "win"
    assert hub_runner(hub, "analyst", "r") is None, "only the WSL-bound agent is pinned"


async def test_sweep_helpers_fall_back_for_a_hub_that_has_no_request_view():
    plain = SimpleNamespace(agents={AGENT: {"id": AGENT}}, agent_runner={AGENT: "x"})
    assert hub_agents(plain, "r") is plain.agents and hub_runner(plain, AGENT, "r") == "x"


async def test_sweep_adopting_a_pinned_consult_waits_for_its_own_runner_not_the_owner(hub):
    win, wsl = both(hub)
    hub.unregister_runner("win", win)
    held = {"request_id": "r", "runner_id": "lab-wsl", "runner_incarnation": "inc-lab-wsl", "accepted": True,
            "kind": "consult", "dispatched_at": 3.0,
            "payload": {"agent_id": AGENT, "meta": {"ask_id": "a1", "session_runner": "lab-wsl", "wsl_pin": True}}}
    hub.store.put("task", "held", held)
    hub.s.gateway.resume_wait_s = 30
    adopting = asyncio.ensure_future(hub.adopt_consult("a1", AGENT))
    await until(lambda: "held" in hub.futures)  # past the guard: it did not sit out resume_wait_s for the away owner
    hub.futures["held"].set_result(TaskResult(task_id="held", agent_id=AGENT, ok=True))
    await asyncio.wait_for(adopting, 3)


async def test_a_pin_is_what_the_request_chose_not_what_is_connected_plan_and_runner_stay_wsl_while_it_is_away(hub):
    """The definition (#1204): the WSL runner drops after the pin and before the plan, a native runner is up."""
    win, wsl = both(hub)
    hub.requests["r"]["wsl_pinned"] = True
    hub.unregister_runner("lab-wsl", wsl)
    assert "lab-wsl" not in hub.runners and "win" in hub.runners
    mine = hub_agents(hub, "r")
    assert mine[AGENT]["runner_id"] == "lab-wsl" and mine[AGENT]["scheduler"] == "sge", "not the native owner's entry"
    assert hub_runner(hub, AGENT, "r") == "lab-wsl" and hub.request_runner(AGENT, "r") == "lab-wsl"
    assert hub_agents(hub, "other")[AGENT]["runner_id"] == "win"
    assert not _request_ready(hub, AGENT, "r"), "away is for sending, not for what the request chose"
    assert dispatched(win) == []
