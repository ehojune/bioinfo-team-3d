"""Consults that were running when the gateway restarted (#93, #112, #113)."""

import asyncio
import json
import time

import pytest

from labhq.gateway.server import Hub
from labhq.models import AskRequest, Task, TaskResult
from labhq.settings import Settings

ROSTER = [{"id": "cso", "engine": "claude_code"}, {"id": "worker", "engine": "claude_code"}]


class CaptureSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, body):
        self.sent.append(json.loads(body))

    async def close(self, code=1000):
        pass


def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.gateway.resume_wait_s = 2
    s.orchestrator.step_max_attempts = 1
    s.orchestrator.step_retry_backoff_s = 0
    return s


def question(ask_id=None):
    ask = AskRequest(task_id="source", agent_id="worker", request_id="r", to="cso",
                     question="Which cohort should the comparison use?", why_blocked="cohort missing")
    return ask.model_copy(update={"id": ask_id}) if ask_id else ask


def consults(socket):
    return [frame["task"] for frame in socket.sent
            if frame["type"] == "task.dispatch" and frame["task"]["meta"].get("kind") == "consult"]


async def eventually(predicate, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def settle(hub):
    if hub.ask_tasks:
        await asyncio.wait_for(asyncio.gather(*list(hub.ask_tasks.values())), 3)
    await asyncio.sleep(0)


def result_frame(task_id, text, session):
    result = TaskResult(task_id=task_id, agent_id="cso", ok=True, text=text, session_id=session)
    return {"type": "task.result", "task_id": task_id, "request_id": "r", "data": result.model_dump(mode="json")}


async def start_consult_then_crash(s, workdir):
    """Gateway 1 routes an ask; the runner accepts the consult; the gateway dies mid-consult."""
    first = Hub(s)
    first.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running", "mode": "direct",
                           "agent_id": "worker", "cso_session_id": "shared-session", "cso_workdir": workdir}
    first.save_request("r")
    socket = CaptureSocket()
    first.register_runner("local", socket, ROSTER, "inc-1")
    ask = question()
    await first.on_runner_message("local", {"type": "ask.requested", "data": ask.model_dump(mode="json")})
    await eventually(lambda: consults(socket))
    running = consults(socket)[0]
    assert running["resume_session_id"] == "shared-session" and running["meta"]["workdir"] == workdir
    await first.on_runner_message("local", {"type": "task.accepted", "task_id": running["id"], "request_id": "r"})
    tasks = list(first.ask_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert first.store.get("ask", ask.id)["state"] == "working"
    first.store.close()
    return ask, running


async def approve_resume(hub):
    async def wait_for_worker(rid):
        assert hub.requests[rid]["status"] == "waiting_for_runner"

    hub.resume_when_ready = wait_for_worker
    aid = next(aid for aid, entry in hub.approvals.items() if entry["approval"]["kind"] == "resume")
    await hub.resolve_approval(aid, True)


@pytest.mark.parametrize("runner", ["same_generation", "late_reconnect"])
async def test_restart_adopts_running_consult_instead_of_overlapping(tmp_path, runner):
    s = settings(tmp_path)
    workdir = str(tmp_path / "cso-workdir")
    ask, running = await start_consult_then_crash(s, workdir)

    second = Hub(s)
    socket = CaptureSocket()
    try:
        assert second.requests["r"]["status"] == "interrupted"
        if runner == "same_generation":
            second.register_runner("local", socket, ROSTER, "inc-1")
        await approve_resume(second)
        await asyncio.sleep(0.1)
        if runner == "late_reconnect":
            assert second.store.get("ask", ask.id)["state"] == "working", "wait for the runner, do not fail"
            second.register_runner("local", socket, ROSTER, "inc-1")
            await asyncio.sleep(0.1)
        # The old consult still owns the CSO session and workdir on the runner.
        for task in consults(socket):
            assert task["id"] == running["id"] or (
                task["resume_session_id"] != "shared-session" and task["meta"].get("workdir") != workdir)
        assert [task for task in consults(socket) if task["id"] != running["id"]] == []

        await second.on_runner_message("local", result_frame(running["id"], "Use cohort C7", "rotated-session"))
        await settle(second)
        entry = second.store.get("ask", ask.id)
        assert entry["state"] == "resolved"
        assert entry["answer"]["status"] == "answered" and entry["answer"]["answer"] == "Use cohort C7"
        assert second.requests["r"]["cso_session_id"] == "rotated-session"
        assert [frame["type"] for frame in socket.sent].count("task.dispatch") == 0
    finally:
        second.store.close()


async def test_restart_isolates_consult_when_runner_generation_changed(tmp_path):
    s = settings(tmp_path)
    workdir = str(tmp_path / "cso-workdir")
    ask, running = await start_consult_then_crash(s, workdir)

    second = Hub(s)
    socket = CaptureSocket()
    try:
        # The runner restarted too: the old consult's outcome is unknown, so it is not adopted.
        second.register_runner("local", socket, ROSTER, "inc-2")
        await approve_resume(second)
        await eventually(lambda: consults(socket))
        retry = consults(socket)[0]
        assert retry["id"] != running["id"]
        assert retry["resume_session_id"] is None and "workdir" not in retry["meta"]
        await second.on_runner_message("local", result_frame(retry["id"], "Use cohort C9", "isolated-session"))
        await settle(second)
        answer = second.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "answered" and answer["answer"] == "Use cohort C9"
    finally:
        second.store.close()


async def test_unfinished_consult_in_ledger_keeps_its_session_busy(tmp_path):
    hub = Hub(settings(tmp_path))
    workdir = str(tmp_path / "cso-workdir")
    try:
        hub.agents = {aid["id"]: aid for aid in ROSTER}
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running",
                             "cso_session_id": "shared-session", "cso_workdir": workdir}
        old = Task(id="old-consult", agent_id="cso", request_id="r", prompt="answer another ask",
                   resume_session_id="shared-session",
                   meta={"kind": "consult", "ask_id": "another-ask", "workdir": workdir})
        hub.store.put("task", old.id, {"request_id": "r", "kind": "consult", "accepted": True,
                                       "completed": False, "payload": old.model_dump(mode="json")})
        calls = []

        async def dispatch(task):
            calls.append(task)
            return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="Use cohort C7")

        hub.dispatch = dispatch
        await hub.orchestrator.answer_ask(question(), "origin")
        assert len(calls) == 1
        assert calls[0].resume_session_id is None and "workdir" not in calls[0].meta
    finally:
        hub.store.close()


async def test_recovery_never_answers_an_ask_with_another_asks_consult(tmp_path):
    hub = Hub(settings(tmp_path))
    socket = CaptureSocket()
    try:
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running"}
        hub.register_runner("local", socket, ROSTER, "inc-1")
        earlier = Task(id="earlier-consult", agent_id="cso", request_id="r", prompt="answer the first ask",
                       meta={"kind": "consult", "ask_id": "first-ask"})
        done = TaskResult(task_id=earlier.id, agent_id="cso", ok=True, text="answer for the first ask")
        hub.store.put("task", earlier.id, {"request_id": "r", "kind": "consult", "accepted": True,
                                           "completed": True, "runner_id": "local",
                                           "runner_incarnation": "inc-1", "dispatched_at": 1,
                                           "payload": earlier.model_dump(mode="json"),
                                           "result": done.model_dump(mode="json")})
        hub.recovery_steps.add("r")
        ask = question("second-ask")
        routed = asyncio.create_task(hub.orchestrator.answer_ask(ask, "local"))
        await eventually(lambda: consults(socket) or routed.done())
        assert consults(socket), "the second ask needs its own consult"
        fresh = consults(socket)[0]
        assert fresh["meta"]["ask_id"] == "second-ask"
        await hub.on_runner_message("local", result_frame(fresh["id"], "answer for the second ask", None))
        await asyncio.wait_for(routed, 2)
        assert hub.store.get("ask", ask.id)["answer"]["answer"] == "answer for the second ask"
    finally:
        hub.store.close()


def ledger_consult(hub, tid, *, attempt, dispatched_at, completed, result=None, abandoned=False, workdir=None,
                   agent="cso"):
    task = Task(id=tid, agent_id=agent, request_id="r", prompt="answer", resume_session_id=None,
                meta={"kind": "consult", "ask_id": "the-ask", "attempt": attempt,
                      **({"workdir": workdir} if workdir else {})})
    hub.store.put("task", tid, {"request_id": "r", "kind": "consult", "attempt": attempt, "accepted": True,
                                "completed": completed, "dispatched_at": dispatched_at,
                                "runner_id": "local", "runner_incarnation": "inc-1",
                                **({"abandoned": True} if abandoned else {}),
                                "payload": task.model_dump(mode="json"),
                                **({"result": result.model_dump(mode="json")} if result else {})})


async def test_adoption_prefers_the_latest_consult_over_a_higher_attempt(tmp_path):
    hub = Hub(settings(tmp_path))
    try:
        hub.register_runner("local", CaptureSocket(), ROSTER, "inc-1")
        lost = TaskResult(task_id="old", agent_id="cso", ok=False, error="runner generation changed")
        ledger_consult(hub, "old", attempt=2, dispatched_at=1, completed=True, result=lost, abandoned=True)
        # After an earlier restart the isolated rerun started again at attempt 1 and finished.
        rerun = TaskResult(task_id="rerun", agent_id="cso", ok=True, text="Use cohort C7")
        ledger_consult(hub, "rerun", attempt=1, dispatched_at=2, completed=True, result=rerun)
        attempt, result = await hub.adopt_consult("the-ask", "cso")
        assert attempt == 1 and result is not None and result.text == "Use cohort C7"
    finally:
        hub.store.close()


async def test_adopted_consult_cost_reaches_the_request_budget_total(tmp_path):
    hub = Hub(settings(tmp_path))
    try:
        hub.agents = {aid["id"]: aid for aid in ROSTER}
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running", "cost_usd": 0.0}
        hub.orchestrator.cost["r"] = 0.0  # run_request baseline taken before the late result
        task = Task(id="running", agent_id="cso", request_id="r", prompt="answer",
                    meta={"kind": "consult", "ask_id": "the-ask"})
        hub.store.put("task", "running", {"request_id": "r", "kind": "consult", "accepted": True,
                                          "completed": False, "dispatched_at": 1, "runner_id": "local",
                                          "runner_incarnation": "inc-1", "payload": task.model_dump(mode="json")})
        hub.register_runner("local", CaptureSocket(), ROSTER, "inc-1")
        routed = asyncio.create_task(hub.orchestrator.answer_ask(question("the-ask"), "local"))
        await asyncio.sleep(0.05)
        done = TaskResult(task_id="running", agent_id="cso", ok=True, text="Use cohort C7", cost_usd=0.75)
        await hub.on_runner_message("local", {"type": "task.result", "task_id": "running", "request_id": "r",
                                              "data": done.model_dump(mode="json")})
        await asyncio.wait_for(routed, 2)
        assert hub.requests["r"]["cost_usd"] == 0.75
        assert hub.orchestrator.cost["r"] == 0.75, "budget checks and _finish use this total"
    finally:
        hub.store.close()


@pytest.mark.parametrize(("prior_attempt", "expected_calls"), [(1, 2), (3, 0)])
async def test_adopted_transient_failure_uses_only_the_remaining_retries(tmp_path, prior_attempt, expected_calls):
    s = settings(tmp_path)
    s.orchestrator.step_max_attempts = 3
    hub = Hub(s)
    try:
        hub.agents = {aid["id"]: aid for aid in ROSTER}
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running"}
        failed = TaskResult(task_id="old", agent_id="cso", ok=False, error="rate limit; try again")
        ledger_consult(hub, "old", attempt=prior_attempt, dispatched_at=1, completed=True, result=failed)
        calls = []

        async def dispatch(task):
            calls.append(task)
            ok = len(calls) == 2
            return TaskResult(task_id=task.id, agent_id="cso", ok=ok, text="Use cohort C7" if ok else "",
                              error=None if ok else "rate limit; try again")

        hub.dispatch = dispatch
        await hub.orchestrator.answer_ask(question("the-ask"), "local")
        assert len(calls) == expected_calls
        assert [task.meta["attempt"] for task in calls] == list(range(prior_attempt + 1, 4))[:expected_calls]
        assert all(task.resume_session_id is None and "workdir" not in task.meta for task in calls)
        answer = hub.store.get("ask", "the-ask")["answer"]
        assert answer["status"] == ("answered" if expected_calls else "rejected")
    finally:
        hub.store.close()


@pytest.mark.parametrize("baseline_has_consult", [False, True])
async def test_adopted_consult_cost_is_counted_once_beside_uncounted_parallel_results(tmp_path,
                                                                                    baseline_has_consult):
    hub = Hub(settings(tmp_path))
    try:
        hub.agents = {aid["id"]: aid for aid in ROSTER}
        # A parallel step's result already reached the durable total; its run_step adds it on resume.
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running",
                             "cost_usd": 0.5, "cost_by_task": {"parallel": 0.5}}
        hub.orchestrator.cost["r"] = 0.0
        done = TaskResult(task_id="old", agent_id="cso", ok=True, text="Use cohort C7", cost_usd=0.75)
        ledger_consult(hub, "old", attempt=1, dispatched_at=1, completed=True, result=done)
        hub.requests["r"]["cost_by_task"]["old"] = 0.75
        hub.requests["r"]["cost_usd"] = 1.25
        if baseline_has_consult:  # run_request took its baseline after the consult result arrived
            hub.orchestrator.cost["r"] = 0.75
            vars(hub.orchestrator).setdefault("cost_tasks", {})["r"] = {"old"}

        async def dispatch(task):
            raise AssertionError("the finished consult is adopted, not rerun")

        hub.dispatch = dispatch
        await hub.orchestrator.answer_ask(question("the-ask"), "local")
        assert hub.store.get("ask", "the-ask")["answer"]["answer"] == "Use cohort C7"
        assert hub.orchestrator.cost["r"] == 0.75
        hub.orchestrator.cost["r"] += 0.5  # the parallel run_step resumes and adds its own result
        assert hub.orchestrator.cost["r"] == hub.requests["r"]["cost_usd"]
    finally:
        hub.store.close()


async def test_adoption_never_takes_another_agents_consult_or_session(tmp_path):
    hub = Hub(settings(tmp_path))
    try:
        # The roster is still empty of facilities, so this facilities ask now routes to the CSO.
        hub.agents = {aid["id"]: aid for aid in ROSTER}
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running",
                             "cso_session_id": "cso-session"}
        done = TaskResult(task_id="facilities-consult", agent_id="facilities", ok=True, text="Bay 3 is free",
                          session_id="facilities-session")
        ledger_consult(hub, "facilities-consult", attempt=1, dispatched_at=1, completed=True, result=done,
                       agent="facilities")
        assert await hub.adopt_consult("the-ask", "cso") == (0, None)
        calls = []

        async def dispatch(task):
            calls.append(task)
            return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="Use bay 2", session_id="cso-next")

        hub.dispatch = dispatch
        ask = AskRequest(id="the-ask", task_id="source", agent_id="worker", request_id="r", to="facilities",
                         question="Which bay is free?", why_blocked="bay unknown")
        await hub.orchestrator.answer_ask(ask, "local")
        assert [task.agent_id for task in calls] == ["cso"]
        assert hub.requests["r"]["cso_session_id"] == "cso-next"
        assert hub.store.get("ask", "the-ask")["answer"]["answer"] == "Use bay 2"
    finally:
        hub.store.close()


# ---- #112: CSO plan and synthesis respect a consult the restarted gateway lost track of ----

def synthesis_ready(workdir):
    """A request whose steps and review are done; only the CSO synthesis remains."""
    done = TaskResult(task_id="t-s1", agent_id="worker", ok=True, text="cohort table")
    return {"id": "r", "text": "compare cohorts", "status": "running", "mode": "orchestrate",
            "plan": {"steps": [{"id": "s1", "agent_id": "worker", "instruction": "compare", "depends_on": []}]},
            "results": {"s1": done.model_dump(mode="json")}, "review_progress": {"phase": "synthesis"},
            "cso_session_id": "shared-session", "cso_workdir": workdir}


def orphan_consult(hub, workdir, *, tid="orphan"):
    """The consult of an ask that ended unanswered; the CSO runner still runs it (#112)."""
    task = Task(id=tid, agent_id="cso", request_id="r", prompt="answer the lost ask",
                resume_session_id="shared-session",
                meta={"kind": "consult", "ask_id": "lost-ask", "workdir": workdir})
    hub.store.put("task", tid, {"request_id": "r", "kind": "consult", "attempt": 1, "accepted": True,
                                "completed": False, "dispatched_at": time.time(), "runner_id": "local",
                                "runner_incarnation": "inc-1", "payload": task.model_dump(mode="json")})


def dispatched(socket, kind):
    return [frame["task"] for frame in socket.sent
            if frame["type"] == "task.dispatch" and frame["task"]["meta"].get("kind") == kind]


def tap_events(hub):
    seen, publish = [], hub.publish

    async def tap(event, **kwargs):
        seen.append(event)
        await publish(event, **kwargs)

    hub.publish = tap
    return seen


def waited_for_session(seen, step):
    return any(e["type"] == "request.step_wait" and (e.get("data") or {}).get("step_id") == step for e in seen)


async def restart_with_orphan_consult(s, workdir):
    first = Hub(s)
    first.requests["r"] = synthesis_ready(workdir)
    first.save_request("r")
    orphan_consult(first, workdir)
    first.store.close()
    second = Hub(s)
    assert second.requests["r"]["status"] == "interrupted"
    return second


async def test_synthesis_after_restart_waits_for_the_consult_still_using_its_session(tmp_path):
    s = settings(tmp_path)
    workdir = str(tmp_path / "cso-workdir")
    hub = await restart_with_orphan_consult(s, workdir)
    socket = CaptureSocket()
    seen = tap_events(hub)
    try:
        aid = next(aid for aid, entry in hub.approvals.items() if entry["approval"]["kind"] == "resume")
        await hub.resolve_approval(aid, True)
        # The CSO runner reconnects late, same generation: the consult it accepted is still running.
        hub.register_runner("local", socket, ROSTER, "inc-1")
        await eventually(lambda: dispatched(socket, "synthesis") or waited_for_session(seen, "synthesis"))
        assert dispatched(socket, "synthesis") == [], "synthesis must not run beside the consult in its session"
        await hub.on_runner_message("local", result_frame("orphan", "late answer", "after-consult"))
        await eventually(lambda: dispatched(socket, "synthesis"))
        final = dispatched(socket, "synthesis")[0]
        # The consult's turn is the latest one in that session, so synthesis continues from it.
        assert final["resume_session_id"] == "after-consult" and final["meta"]["workdir"] == workdir
        await hub.on_runner_message("local", result_frame(final["id"], "Final report", "final-session"))
        await eventually(lambda: hub.requests["r"]["status"] == "done")
        assert hub.requests["r"]["report"].startswith("Final report")
    finally:
        hub.store.close()


async def test_synthesis_after_restart_isolates_when_the_consult_outcome_is_unknown(tmp_path):
    s = settings(tmp_path)
    workdir = str(tmp_path / "cso-workdir")
    hub = await restart_with_orphan_consult(s, workdir)
    socket = CaptureSocket()
    try:
        aid = next(aid for aid, entry in hub.approvals.items() if entry["approval"]["kind"] == "resume")
        await hub.resolve_approval(aid, True)
        # The runner restarted as well: the consult is abandoned, but its CLI may still hold the session.
        hub.register_runner("local", socket, ROSTER, "inc-2")
        await eventually(lambda: dispatched(socket, "synthesis"))
        final = dispatched(socket, "synthesis")[0]
        assert final["resume_session_id"] is None and "workdir" not in final["meta"]
        await hub.on_runner_message("local", result_frame(final["id"], "Final report", "isolated"))
        await eventually(lambda: hub.requests["r"]["status"] == "done")
    finally:
        hub.store.close()


async def test_plan_continuation_isolates_from_a_task_whose_outcome_is_unknown(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    workdir = str(tmp_path / "cso-workdir")
    try:
        hub.agents = {aid["id"]: {**aid, "name": aid["id"], "role": "test"} for aid in ROSTER}
        hub.requests["r"] = {"id": "r", "text": "compare cohorts", "status": "running", "mode": "orchestrate",
                             "cso_session_id": "shared-session", "cso_workdir": workdir}
        orphan_consult(hub, workdir)
        entry = hub.store.get("task", "orphan")
        hub.store.put("task", "orphan", {**entry, "completed": True, "abandoned": True})
        calls = []

        async def dispatch(task):
            calls.append(task)
            return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="no steps")

        hub.dispatch = dispatch
        await hub.orchestrator.run_request("r")
        assert calls[0].meta["kind"] == "plan"
        assert calls[0].resume_session_id is None and "workdir" not in calls[0].meta
    finally:
        hub.store.close()


@pytest.mark.parametrize("state", ["unaccepted", "accepted_by_an_offline_runner", "in_flight_to_an_offline_runner"])
async def test_session_wait_is_bounded_when_no_connected_runner_runs_the_holder(tmp_path, state):
    s = settings(tmp_path)
    s.gateway.resume_wait_s = 30 if state == "unaccepted" else 0.3
    hub = Hub(s)
    workdir = str(tmp_path / "cso-workdir")
    try:
        orphan_consult(hub, workdir)
        if state == "unaccepted":  # this gateway gave up on its delivery; the connected runner never took it
            hub.store.put("task", "orphan", {**hub.store.get("task", "orphan"), "accepted": False})
            hub.register_runner("local", CaptureSocket(), ROSTER, "inc-1")
        elif state == "accepted_by_an_offline_runner":
            hub.agent_runner["cso"] = "local"  # roster kept, runner disconnected
        else:  # this gateway still awaits the result, but the runner dropped and does not come back
            socket = CaptureSocket()
            hub.register_runner("local", socket, ROSTER, "inc-1")
            hub.futures["orphan"] = asyncio.get_running_loop().create_future()
            hub.unregister_runner("local", socket)
        started = time.monotonic()
        freed = await asyncio.wait_for(hub.wait_session_free("cso", "shared-session", workdir, request_id="r",
                                                             step_id="synthesis"), 5)
        assert freed == (None, None)
        assert time.monotonic() - started < (2 if state == "unaccepted" else 5)
    finally:
        hub.store.close()


async def test_a_reported_result_releases_an_abandoned_tasks_session(tmp_path):
    s = settings(tmp_path)
    hub = Hub(s)
    workdir = str(tmp_path / "cso-workdir")
    try:
        orphan_consult(hub, workdir)
        hub.register_runner("local", CaptureSocket(), ROSTER, "inc-2")  # a new generation abandons it
        assert hub.store.get("task", "orphan")["abandoned"]
        assert await hub.wait_session_free("cso", "shared-session", workdir) == (None, None)
        # The old generation's unacknowledged result still arrives: the consult did finish.
        await hub.on_runner_message("local", result_frame("orphan", "late answer", "after-consult"))
        assert await asyncio.wait_for(hub.wait_session_free("cso", "shared-session", workdir), 2) == (
            "shared-session", workdir)
    finally:
        hub.store.close()
