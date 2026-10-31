"""Request and approval lifecycle for a PI working alone (PI 점검 R2 R5 R13 R15)."""
import asyncio
import copy
import time

import pytest

from labhq.gateway.server import PI_DECISION_KINDS, Hub
from labhq.models import ApprovalRequest, Task, TaskResult
from labhq.orchestrator.cso import BudgetExceeded, Orchestrator
from labhq.research import continuation
from labhq.settings import Settings
from tests.test_research_protocol import valid_plan


def _settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    return s


def _request(hub, rid, status="running", **extra):
    hub.requests[rid] = {"id": rid, "text": f"{rid} 요청 본문", "mode": "orchestrate", "status": status,
                         "created_at": time.time(), **extra}
    hub.save_request(rid)
    return hub.requests[rid]


def result(task, ok=True, **kwargs):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=ok, **kwargs)


class FakeHub:
    """The orchestrator's view of the gateway, with the PI's answer scripted."""

    def __init__(self, dispatch, decision):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.reviewer_agent = None
        self.s.orchestrator.step_retry_backoff_s = 0
        self.requests = {"r": {"text": "question", "mode": "team", "budget_usd": 10}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "worker")}
        self.events, self.calls, self.approvals = [], [], []
        self.reply, self.decision = dispatch, decision

    async def dispatch(self, task):
        self.calls.append(task)
        return await self.reply(task)

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        self.approvals.append(kwargs)
        return dict(self.decision)

    def supports_resume(self, agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass


TIMED_OUT = {"approved": False, "note": "timed out", "state": "timed_out"}
DENIED = {"approved": False, "note": ""}


# ----- R5: an unanswered PI decision parks the request; it never ends as a deny -----

@pytest.mark.asyncio
async def test_pi_decision_cards_outlive_the_gate_timeout_and_park_active_requests(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    s.policy.approvals.timeout_s = 1  # the gate timeout; it used to end every card below after one second
    hub = Hub(s)
    kinds = sorted(PI_DECISION_KINDS - {"resume"})  # a resume card has no waiter; tested below
    parked = asyncio.Event()
    seen = set()
    publish = hub.publish

    async def status_published(event):
        await publish(event)
        if event["type"] == "approval.requested":
            seen.add(event["data"]["kind"])
            if seen == set(kinds):
                parked.set()

    monkeypatch.setattr(hub, "publish", status_published)
    for kind in kinds:
        _request(hub, f"r_{kind}")
        if kind == "result_publish":
            hub.requests[f"r_{kind}"]["status"] = "done"
            hub.review_done(f"r_{kind}")
    waits = {kind: asyncio.create_task(hub.request_approval(kind, f"{kind}?", f"r_{kind}")) for kind in kinds}
    await asyncio.wait_for(parked.wait(), 3)
    await asyncio.sleep(1.4)
    assert not [kind for kind, wait in waits.items() if wait.done()]
    assert {kind: hub.requests[f"r_{kind}"]["status"] for kind in kinds} == {
        kind: "done" if kind == "result_publish" else "waiting_pi" for kind in kinds}
    cards = {entry["approval"]["request_id"]: aid for aid, entry in hub.approvals.items()}
    for kind in kinds:
        assert hub.approvals[cards[f"r_{kind}"]]["approval"]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s
        await hub.resolve_approval(cards[f"r_{kind}"], True, "진행")
    decisions = {kind: await asyncio.wait_for(wait, 1) for kind, wait in waits.items()}
    assert all(decision["approved"] and "state" not in decision for decision in decisions.values())
    assert {kind: hub.requests[f"r_{kind}"]["status"] for kind in kinds} == {
        kind: "done" if kind == "result_publish" else "running" for kind in kinds}
    assert not any(hub.requests[f"r_{kind}"].get("pi_waits") for kind in kinds)
    statuses = [e["data"]["status"] for e in hub.events if e["type"] == "request.status"]
    assert statuses.count("waiting_pi") == len(kinds) - 1 and statuses.count("running") == len(kinds) - 1


@pytest.mark.asyncio
async def test_a_gate_card_keeps_the_gate_timeout_and_does_not_park(tmp_path):
    s = _settings(tmp_path)
    s.policy.approvals.timeout_s = 1
    hub = Hub(s)
    _request(hub, "r")
    decision = await hub.request_approval("download", "big file?", "r")
    assert decision["state"] == "timed_out" and hub.requests["r"]["status"] == "running"
    assert not [e for e in hub.events if e["type"] == "request.status"]


@pytest.mark.asyncio
async def test_the_pi_decision_bound_returns_timed_out_and_lifts_the_hold(tmp_path):
    s = _settings(tmp_path)
    s.policy.approvals.pi_decision_timeout_s = 1
    hub = Hub(s)
    _request(hub, "r")
    decision = await hub.request_approval("clarify", "which cohort?", "r")
    assert decision["state"] == "timed_out" and not decision["approved"]
    assert hub.requests["r"]["status"] == "running" and "pi_waits" not in hub.requests["r"]
    stored = hub.store.get("approval_decision", decision["approval_id"])
    assert stored["state"] == "timed_out"


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "outcome", "report"), [
    (TIMED_OUT, "clarify_timed_out", "PI 질문 카드에 기한 안에 답이 없어"),
    (DENIED, None, "PI 확인 답변을 받지 못해 요청을 멈췄습니다."),
])
async def test_an_unanswered_clarify_card_is_not_a_denied_one(decision, outcome, report):
    async def dispatch(task):
        return result(task, structured={"steps": [{"id": "A", "agent_id": "worker", "instruction": "A",
                                                   "depends_on": []}],
                                        "clarifying_questions": ["Which cohort?"]})

    hub = FakeHub(dispatch, decision)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed" and req.get("outcome") == outcome
    assert report in req["report"]
    assert [t.meta["kind"] for t in hub.calls] == ["plan"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "timed_out"), [(TIMED_OUT, True), (DENIED, False)])
async def test_an_unanswered_budget_card_is_recorded_as_timed_out(decision, timed_out):
    async def dispatch(task):
        return result(task, text="done", cost_usd=0.8)

    hub = FakeHub(dispatch, decision)
    hub.requests["r"]["budget_usd"] = 0.5
    orch = Orchestrator(hub)
    assert (await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work"))).ok
    with pytest.raises(BudgetExceeded) as stopped:
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt="next"))
    assert bool(orch.budget_outcomes["r"][-1].get("timed_out")) is timed_out
    assert ("timed out" in str(stopped.value)) is timed_out and ("denied" in str(stopped.value)) is not timed_out
    assert hub.requests["r"].get("outcome") == ("budget_timed_out" if timed_out else None)


@pytest.mark.asyncio
async def test_a_resume_card_past_the_bound_ends_its_request_as_resume_timed_out(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "old", status="interrupted")
    _request(hub, "fresh", status="interrupted")
    old = hub.new_resume_approval("old")
    fresh = hub.new_resume_approval("fresh")
    assert old.timeout_s == s.policy.approvals.pi_decision_timeout_s
    stale = hub.approvals[old.id]["approval"]
    stale.update(created_at=time.time() - s.policy.approvals.pi_decision_timeout_s - 60, timeout_s=3600)
    hub.save_approval(old.id)
    await hub.expire_stale_resume_cards()
    assert old.id not in hub.approvals and fresh.id in hub.approvals
    assert hub.requests["old"]["status"] == "failed" and hub.requests["old"]["outcome"] == "resume_timed_out"
    assert hub.store.get("approval_decision", old.id)["state"] == "timed_out"
    assert hub.requests["fresh"]["status"] == "interrupted" and fresh.id in hub.resume_timers
    await hub.resolve_approval(fresh.id, False)  # a deny is still a deny
    assert hub.requests["fresh"]["error"] == "resume declined" and fresh.id not in hub.resume_timers


# ----- R13: request cancel -----

class CaptureSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, body):
        import json
        self.sent.append(json.loads(body))


ROSTER = [{"id": name, "name": name, "role": "test", "engine": "mock"} for name in ("cso", "worker")]


def _team_settings(tmp_path):
    s = _settings(tmp_path)
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.precedent_agent = None
    return s


async def _until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_cancel_stops_a_planning_request_and_its_live_task(tmp_path, monkeypatch):
    from labhq.gateway.server import RequestIn

    hub = Hub(_team_settings(tmp_path))
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    plan_task = next(m["task"]["id"] for m in ws.sent if m.get("type") == "task.dispatch")
    orchestration = hub.request_tasks[rid]

    cancelling = asyncio.create_task(hub.cancel_request(rid))
    await _until(lambda: {"type": "task.cancel", "task_id": plan_task} in ws.sent)
    # The cancelled turn reports back (with its cost) before the terminal record is written.
    cost_entered, release_cost = asyncio.Event(), asyncio.Event()
    record_cost = hub._arecord_task_cost

    async def delayed_cost(*args, **kwargs):
        cost_entered.set()
        await release_cost.wait()
        await record_cost(*args, **kwargs)

    monkeypatch.setattr(hub, "_arecord_task_cost", delayed_cost)
    reporting = asyncio.create_task(hub.on_runner_message("local", {"type": "task.result", "task_id": plan_task, "request_id": rid,
                                          "data": TaskResult(task_id=plan_task, agent_id="cso", ok=False,
                                                             error="cancelled", cost_usd=0.4).model_dump(mode="json")}))
    await asyncio.wait_for(cost_entered.wait(), 1)
    try:
        assert not hub.store.get("task", plan_task).get("completed")
        assert not cancelling.done()
    finally:
        release_cost.set()
        await reporting
    out = await asyncio.wait_for(cancelling, 3)
    assert out == {"request_id": rid, "status": "cancelled", "already": False, "cancelled_tasks": [plan_task]}
    assert orchestration.done() and rid not in hub.request_tasks
    req = hub.requests[rid]
    assert req["status"] == "cancelled" and req["outcome"] == "cancelled" and "취소했습니다" in req["report"]
    final = [e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == rid]
    assert len(final) == 1 and final[0]["data"]["status"] == "cancelled" and final[0]["data"]["ok"] is False
    assert final[0]["data"]["cost_usd"] == pytest.approx(0.4) and final[0]["data"]["cost_known"] is True
    assert hub.store.get("request", rid)["cost_usd"] == pytest.approx(0.4)
    restarted = Hub(hub.s)
    assert restarted.requests[rid]["cost_usd"] == pytest.approx(0.4)
    restarted.store.close()
    # A runner replay must not add the same turn twice.
    await record_cost(rid, plan_task, TaskResult(task_id=plan_task, agent_id="cso", ok=False, cost_usd=0.4))
    assert hub.requests[rid]["cost_usd"] == pytest.approx(0.4)

    # Nothing else is sent, and a second cancel answers the same.
    sent = len(ws.sent)
    await asyncio.sleep(0.05)
    assert not [m for m in ws.sent[sent:] if m.get("type") == "task.dispatch"]
    assert (await hub.cancel_request(rid)) == {"request_id": rid, "status": "cancelled", "already": True,
                                               "cancelled_tasks": [plan_task]}
    assert hub.requests[rid]["status"] == "cancelled"
    assert len([e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == rid]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("save_number", [1, 2])
async def test_caller_cancel_during_cancel_save_still_stops_runner(tmp_path, monkeypatch, save_number):
    hub = Hub(_settings(tmp_path))
    hub.cancel_result_wait_s = 0
    _request(hub, "r")
    _request(hub, "other")
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    task = Task(agent_id="worker", request_id="r", prompt="inert")
    hub.store.put("task", task.id, {"request_id": "r", "runner_id": "local",
                                  "payload": task.model_dump(mode="json")})
    hub.task_runner[task.id] = "local"
    entered, release = asyncio.Event(), asyncio.Event()
    save = hub.asave_request
    calls = 0

    async def blocked_save(rid):
        nonlocal calls
        calls += 1
        if calls == save_number:
            entered.set()
            await release.wait()
        await save(rid)

    monkeypatch.setattr(hub, "asave_request", blocked_save)
    cancelling = asyncio.create_task(hub.cancel_request("r"))
    await asyncio.wait_for(entered.wait(), 1)
    cancelling.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await cancelling
    assert {"type": "task.cancel", "task_id": task.id} in ws.sent
    assert hub.store.get("request", "r")["cancelled_tasks"] == [task.id]
    assert "cancelling" not in hub.requests["r"]
    assert len([e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == "r"]) == 1
    assert hub.requests["other"]["status"] == "running"
    restarted = Hub(hub.s)
    assert restarted.requests["r"]["cancelled_tasks"] == [task.id]
    assert "cancelling" not in restarted.requests["r"]
    restarted.store.close()


@pytest.mark.asyncio
async def test_a_cancelled_turn_that_never_reports_is_unaccounted_not_free(tmp_path):
    from labhq.gateway.server import RequestIn

    hub = Hub(_team_settings(tmp_path))
    hub.cancel_result_wait_s = 0.2
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    await hub.cancel_request(rid)
    final = next(e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == rid)
    assert final["data"]["cost_known"] is False and hub.requests[rid]["cost_known"] is False


@pytest.mark.asyncio
async def test_cancel_stops_parallel_steps_and_a_late_card_from_the_runner_is_closed(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r")
    parked = asyncio.Event()

    async def step():
        parked.set()
        await asyncio.sleep(3600)  # a step parked on quota or an answer

    sibling = hub.orchestrator._step_task("r", step())
    await asyncio.wait_for(parked.wait(), 1)
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    await hub.cancel_request("r")
    await asyncio.sleep(0)
    assert sibling.cancelled()

    late = ApprovalRequest(kind="tool_permission", summary="Bash: rm -rf tmp/", request_id="r", agent_id="worker",
                           task_id="t1").model_dump(mode="json")
    await hub.on_runner_message("local", {"type": "approval.requested", "data": late})
    assert late["id"] not in hub.approvals and hub.store.get("approval_decision", late["id"])["state"] == "expired"
    assert {"type": "approval.resolved", "id": late["id"], "approved": False, "note": "요청 취소로 닫힘"} in ws.sent
    assert not [e for e in hub.events if e["type"] == "approval.requested" and e["data"].get("id") == late["id"]]


@pytest.mark.asyncio
async def test_cancel_releases_a_quota_wait_and_closes_the_requests_cards(tmp_path, monkeypatch):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r", results={"s1": TaskResult(task_id="t1", agent_id="worker", ok=True, text="표 완성",
                                                 outputs=["outputs/qc.tsv"], workdir_id="w1").model_dump(mode="json")},
             plan={"steps": [{"id": "s1", "agent_id": "worker"}, {"id": "s2", "agent_id": "worker"}]})
    _request(hub, "other")
    quota_entered, release_quota = asyncio.Event(), asyncio.Event()
    publish, save = hub.publish, hub.asave_request

    async def parked_publish(event):
        await publish(event)
        if event["type"] == "request.step_quota_wait":
            quota_entered.set()
            await release_quota.wait()

    async def cancellation_save(rid):
        if rid == "r" and hub.requests[rid]["status"] == "cancelled":
            release_quota.set()
            await asyncio.wait({held}, timeout=1)
            assert held.cancelled(), "cancel must reach the waiter before persistence yields"
        await save(rid)

    monkeypatch.setattr(hub, "publish", parked_publish)
    monkeypatch.setattr(hub, "asave_request", cancellation_save)
    now = time.time()
    held = hub.track_request("r", hub.wait_quota("r", "s2", "codex", resume_at=now + 3600, deadline_at=now + 7200,
                                                  reason="usage limit"))
    card = asyncio.create_task(hub.request_approval("budget", "계속할까요?", "r"))
    others = asyncio.create_task(hub.request_approval("clarify", "다른 요청", "other"))
    await _until(lambda: hub.requests["r"].get("quota_waits") and len(hub.approvals) == 2)
    await asyncio.wait_for(quota_entered.wait(), 1)

    await hub.cancel_request("r")
    assert held.cancelled() and hub.quota_hold("codex") is None
    decision = await asyncio.wait_for(card, 1)
    assert decision["state"] == "expired" and not decision["approved"]
    assert [entry["approval"]["request_id"] for entry in hub.approvals.values()] == ["other"]
    req = hub.requests["r"]
    assert req["status"] == "cancelled" and "quota_waits" not in req and "pi_waits" not in req
    assert "s1 (worker): 완료" in req["report"] and "w1/outputs/qc.tsv" in req["report"]
    assert "s2 (worker): 실행하지 않음" in req["report"] and "한도 대기 중이던 단계: s2" in req["report"]
    assert hub.requests["other"]["status"] == "waiting_pi"
    others.cancel()


@pytest.mark.asyncio
async def test_a_cancelled_interrupted_request_gets_no_resume_card_after_a_restart(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "r", status="interrupted")
    resume = hub.new_resume_approval("r")
    await hub.cancel_request("r")
    assert resume.id not in hub.approvals and hub.requests["r"]["status"] == "cancelled"
    assert hub.store.get("approval_decision", resume.id)["state"] == "expired"
    restarted = Hub(s)
    assert restarted.requests["r"]["status"] == "cancelled" and not restarted.approvals


def test_cancel_endpoint_and_cli(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from labhq import cli
    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    for rid, status in (("r", "interrupted"), ("finished", "done")):
        hub.requests[rid] = {"id": rid, "text": rid, "mode": "orchestrate", "status": status, "created_at": 1.0}
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    with TestClient(app) as client:
        assert client.post("/api/requests/r/cancel").status_code == 401
        first = client.post("/api/requests/r/cancel", headers=auth)
        assert first.status_code == 200 and first.json()["status"] == "cancelled" and not first.json()["already"]
        again = client.post("/api/requests/r/cancel", headers=auth)
        assert again.status_code == 200 and again.json()["already"] is True
        assert client.post("/api/requests/finished/cancel", headers=auth).status_code == 409
        assert client.post("/api/requests/nope/cancel", headers=auth).status_code == 404

    calls = []

    def api(_s, method, path, **kw):
        calls.append((method, path))
        return {"request_id": "req_x", "status": "cancelled", "already": False, "cancelled_tasks": ["t1", "t2"]}

    monkeypatch.setattr(cli, "_api", api)
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: s))
    cli.main(["cancel", "req_x"])
    assert calls == [("POST", "/api/requests/req_x/cancel")]
    assert "req_x: 취소했습니다 · 멈춘 작업 2개" in capsys.readouterr().out


# ----- R15: no runner -----

@pytest.mark.asyncio
async def test_a_request_without_a_runner_waits_and_runs_when_one_registers(tmp_path):
    from labhq.gateway.server import RequestIn

    hub = Hub(_team_settings(tmp_path))
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: hub.requests[rid]["status"] == "waiting_for_runner")
    assert list(hub.requests[rid]["runner_waits"].values())[0]["agent_id"] == "cso"
    assert not hub.store.all("task")  # nothing was sent, so nothing is uncertain or spent
    # Status changes precede the now-awaitable event commit. Observe its completion,
    # retaining the same event and status assertions below.
    await _until(lambda: any(e["type"] == "request.runner_wait" for e in hub.events))
    wait = next(e for e in hub.events if e["type"] == "request.runner_wait")
    assert wait["data"]["missing_agents"] == ["cso"] and "labhq runner" in wait["data"]["message"]
    assert any(e["type"] == "request.status" and e["data"]["status"] == "waiting_for_runner" for e in hub.events)

    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    assert hub.requests[rid]["status"] == "running" and "runner_waits" not in hub.requests[rid]
    assert [e["data"]["status"] for e in hub.events if e["type"] == "request.status"] == [
        "waiting_for_runner", "running"]  # an open page sees it move on
    # The plan sees the roster that arrived with the runner, so a plan naming its staff runs.
    plan = next(m["task"] for m in ws.sent if m.get("type") == "task.dispatch")
    assert '"worker"' in plan["prompt"] or "worker" in plan["prompt"]
    steps = [{"id": "s1", "agent_id": "worker", "instruction": "QC 표 만들기", "depends_on": []}]
    await hub.on_runner_message("local", {"type": "task.result", "task_id": plan["id"], "request_id": rid,
                                          "data": TaskResult(task_id=plan["id"], agent_id="cso", ok=True,
                                                             structured={"steps": steps}).model_dump(mode="json")})
    await _until(lambda: any(m.get("type") == "task.dispatch" and m["task"]["agent_id"] == "worker"
                             for m in ws.sent))
    hub.cancel_result_wait_s = 0.1
    await hub.cancel_request(rid)


@pytest.mark.asyncio
async def test_a_runner_wait_past_its_bound_fails_with_a_korean_next_step(tmp_path):
    from labhq.gateway.server import RequestIn

    s = _team_settings(tmp_path)
    s.gateway.runner_wait_s = 0.2
    hub = Hub(s)
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: hub.requests[rid]["status"] == "failed")
    report = hub.requests[rid]["report"] + hub.requests[rid].get("execution_record", "")
    assert "러너가 1분 안에 연결되지 않아 cso 작업을 보내지 못했습니다" in report and "labhq runner" in report


@pytest.mark.asyncio
async def test_an_agent_no_runner_ever_hosted_still_fails_at_once(tmp_path):
    hub = Hub(_settings(tmp_path))
    hub.register_runner("local", CaptureSocket(), ROSTER, "inc")
    _request(hub, "r")
    outcome = await asyncio.wait_for(hub.dispatch(Task(agent_id="ghost", request_id="r", prompt="x")), 1)
    assert not outcome.ok and "no runner hosts agent 'ghost'" in outcome.error and outcome.cost_usd == 0
    # The same when the connected runner used to host the agent and dropped it from its roster.
    hub.register_runner("local", CaptureSocket(), [*ROSTER, {"id": "dropped", "name": "d", "role": "t",
                                                            "engine": "mock"}], "inc")
    hub.register_runner("local", CaptureSocket(), ROSTER, "inc")
    outcome = await asyncio.wait_for(hub.dispatch(Task(agent_id="dropped", request_id="r", prompt="x")), 1)
    assert not outcome.ok and "no runner hosts agent 'dropped'" in outcome.error


def test_health_says_whether_a_runner_is_connected(tmp_path):
    from fastapi.testclient import TestClient

    from labhq.gateway.server import create_app

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        health = client.get("/api/health").json()
    assert health["runners"] == [] and health["runner_online"] is False and health["waiting_for_runner"] == 0


# ----- R2: orphan cards and a readable resume card -----

def _runner_card(agent_id="worker", task_id="t1"):
    return ApprovalRequest(kind="tool_permission", summary="Bash: rm -rf tmp/", request_id="r", agent_id=agent_id,
                           task_id=task_id, timeout_s=90).model_dump(mode="json")


@pytest.mark.asyncio
async def test_a_runner_restart_expires_the_cards_its_old_process_asked(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r")
    hub.register_runner("local", CaptureSocket(), ROSTER, "first")
    card = _runner_card()
    await hub.on_runner_message("local", {"type": "approval.requested", "data": card})
    assert hub.approvals[card["id"]]["origin_incarnation"] == "first"

    assert await hub.expire_orphan_approvals("local", "first") == []  # the same process reconnecting keeps it
    assert card["id"] in hub.approvals

    replacement = CaptureSocket()
    hub.register_runner("local", replacement, ROSTER, "second")
    assert await hub.expire_orphan_approvals("local", "second") == [card["id"]]
    assert card["id"] not in hub.approvals and not hub.store.get("approval", card["id"])
    assert hub.store.get("approval_decision", card["id"])["state"] == "expired"
    assert hub.store.get("decision", card["id"]) is None  # the new process never asked; nothing is sent to it
    await hub.flush_decisions("local")
    assert not [m for m in replacement.sent if m.get("type") == "approval.resolved"]
    assert any(e["type"] == "approval.expired" and e["data"]["id"] == card["id"] for e in hub.events)


@pytest.mark.asyncio
async def test_a_card_from_a_runner_that_no_longer_hosts_the_agent_expires(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    gone, kept = _runner_card("worker", "t_old"), _runner_card("aligner", "t_hpc")
    for card, origin in ((gone, "local"), (kept, "hpc")):  # saved before cards carried the asking process
        hub.approvals[card["id"]] = {"approval": card, "origin": origin}
        hub.save_approval(card["id"])
    restarted = Hub(s)
    restarted.register_runner("lab-workstation", CaptureSocket(), ROSTER, "inc")
    assert await restarted.expire_orphan_approvals("lab-workstation", "inc") == [gone["id"]]
    assert "직원이 다른 러너(lab-workstation)로 옮겨" in restarted.store.get("approval_decision", gone["id"])["note"]
    assert list(restarted.approvals) == [kept["id"]]  # another runner's agent, still away: it may come back


def _runner_settings(tmp_path):
    s = _settings(tmp_path)
    s.gateway.runner_token = "runner-token"
    s.runner.state_dir = str(tmp_path / "runner-state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.talent_dir = str(tmp_path / "talent")
    return s


def _runner_hello(s):
    """The hello of one runner process; the next Runner on the same state is that runner restarted."""
    from labhq.runner.daemon import Runner

    runner = Runner(s)
    try:
        return runner.hello()
    finally:
        runner.store.close()


def test_a_runner_restart_that_keeps_its_incarnation_still_expires_the_old_process_cards(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from labhq.gateway.server import create_app

    s = _runner_settings(tmp_path)
    first, restarted = _runner_hello(s), _runner_hello(s)
    assert first["incarnation"] == restarted["incarnation"]  # kept on disk for task generations
    assert first["process"] != restarted["process"]
    app = create_app(s)
    hub = app.state.hub
    _request(hub, "r")
    card = _runner_card()

    with TestClient(app) as client:
        def connect(hello, frame):
            with client.websocket_connect(f"/ws/runner?token={s.gateway.runner_token}") as ws:
                ws.send_text(json.dumps(hello))
                ws.send_text(json.dumps(frame))
                assert json.loads(ws.receive_text()) == {"type": "runner.ack", "runner_seq": frame["runner_seq"]}

        connect(first, {"type": "approval.requested", "runner_seq": 1, "task_id": "t1", "agent_id": "worker",
                        "request_id": "r", "data": card})
        assert hub.approvals[card["id"]]["origin_process"] == first["process"]
        connect(first, {"type": "agent.log", "runner_seq": 2, "data": {"text": "reconnected"}})
        assert card["id"] in hub.approvals  # the same process reconnecting keeps it
        connect(restarted, {"type": "agent.log", "runner_seq": 3, "data": {"text": "restarted"}})

    assert card["id"] not in hub.approvals and not hub.store.get("approval", card["id"])
    decision = hub.store.get("approval_decision", card["id"])
    assert decision["state"] == "expired" and decision["note"] == "러너가 다시 시작되어 닫힘"
    assert hub.store.get("decision", card["id"]) is None  # the new process never asked; nothing is sent to it


@pytest.mark.asyncio
async def test_a_task_result_closes_the_cards_its_task_left_open(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r")
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc", process="p1")
    left, other = _runner_card(task_id="t1"), _runner_card(task_id="t2")
    notice = ApprovalRequest(kind="codex_sandbox_setup", summary="Codex sandbox 다시 준비 필요 (확인하면 닫힙니다)",
                             request_id="r", agent_id="worker", task_id="t1",
                             timeout_s=7 * 24 * 3600).model_dump(mode="json")
    for card in (left, other, notice):
        await hub.on_runner_message("local", {"type": "approval.requested", "data": card})

    ended = TaskResult(task_id="t1", agent_id="worker", ok=False, error="agent exited")
    await hub.on_runner_message("local", {"type": "task.result", "task_id": "t1", "request_id": "r",
                                          "data": ended.model_dump(mode="json")})
    note = "그 작업이 끝나 닫힘"
    assert left["id"] not in hub.approvals
    decision = hub.store.get("approval_decision", left["id"])
    assert decision["state"] == "expired" and decision["note"] == note
    assert any(e["type"] == "approval.expired" and e["data"] == {"id": left["id"], "note": note} for e in hub.events)
    # The process that asked is alive: its hook hears no now instead of timing out an hour later.
    assert {"type": "approval.resolved", "id": left["id"], "approved": False, "note": note} in ws.sent
    assert set(hub.approvals) == {other["id"], notice["id"]}  # another task's card; a notice nobody waits on


@pytest.mark.asyncio
async def test_an_older_runner_without_a_process_id_keeps_the_incarnation_rule(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r")
    hub.register_runner("local", CaptureSocket(), ROSTER, "first")  # an older runner's hello has no process id
    card = _runner_card()
    await hub.on_runner_message("local", {"type": "approval.requested", "data": card})
    assert await hub.expire_orphan_approvals("local", "first") == []  # it reconnects: kept, as before
    hub.register_runner("local", CaptureSocket(), ROSTER, "second")
    assert await hub.expire_orphan_approvals("local", "second") == [card["id"]]  # new incarnation: closed, as before

    asked = _runner_card(task_id="t2")
    await hub.on_runner_message("local", {"type": "approval.requested", "data": asked})
    # Updated and restarted on the same state: the process that asked sent no id, the one registering does.
    hub.register_runner("local", CaptureSocket(), ROSTER, "second", process="p1")
    assert await hub.expire_orphan_approvals("local", "second", "p1") == [asked["id"]]


@pytest.mark.asyncio
async def test_a_late_answer_for_a_finished_task_does_not_mark_its_agent_working(tmp_path):
    from labhq.runner.daemon import Runner

    runner = Runner(_runner_settings(tmp_path))
    live = asyncio.create_task(asyncio.sleep(30))
    try:
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        runner.tasks.update(t_done=finished, t_live=live)
        loop = asyncio.get_running_loop()
        for aid, tid in (("appr_done", "t_done"), ("appr_live", "t_live")):
            runner.broker.pending[aid] = loop.create_future()
            runner.approval_tasks[aid] = (tid, "worker")
            await runner._on_message({"type": "approval.resolved", "id": aid, "approved": False, "note": "closed"})
        sent = runner.store.pending()
        assert [m["task_id"] for m in sent if m["type"] == "agent.status"] == ["t_live"]
        assert [m["id"] for m in sent if m["type"] == "approval.ack"] == ["appr_done", "appr_live"]
    finally:
        live.cancel()
        runner.store.close()


def test_a_resume_card_names_the_request_its_date_and_the_steps_left(tmp_path):
    hub = Hub(_settings(tmp_path))
    created = time.mktime((2026, 9, 28, 10, 30, 0, 0, 0, -1))
    _request(hub, "r", status="interrupted", created_at=created,
             text="새로 받은 WGS 배치 표준 QC를 돌리고 결과를 요약해 주세요 " * 3,
             plan={"steps": [{"id": sid} for sid in ("s1", "s2", "s3")]},
             results={"s1": {"ok": True}})
    card = hub.new_resume_approval("r")
    assert card.summary.startswith('중단된 요청 "새로 받은 WGS 배치 표준 QC')
    assert "(09-28 10:30 접수)" in card.summary and card.summary.endswith("남은 단계: s2, s3")
    assert "[" not in card.summary
    assert card.detail == {"request_text": card.detail["request_text"], "created_at": created, "steps": ["s2", "s3"],
                           "has_plan": True}


def test_a_resume_card_tells_a_request_without_a_plan_from_one_whose_steps_all_finished(tmp_path):
    # #521: both send steps=[]; the web detail read 남은 단계 없음 for a direct request that reruns whole.
    hub = Hub(_settings(tmp_path))
    _request(hub, "direct", status="interrupted", mode="direct")
    _request(hub, "finished", status="interrupted", plan={"steps": [{"id": "s1"}]}, results={"s1": {"ok": True}})
    direct, finished = hub.new_resume_approval("direct"), hub.new_resume_approval("finished")
    assert direct.summary.endswith("남은 단계: 요청 전체")
    assert (direct.detail["steps"], direct.detail["has_plan"]) == ([], False)
    assert finished.summary.endswith("남은 단계: 없음(단계는 모두 끝났고 그 뒤 검토·결정·보고서부터 이어 갑니다)")
    assert (finished.detail["steps"], finished.detail["has_plan"]) == ([], True)


def test_a_restarted_gateway_logs_runner_offline_for_runners_the_old_process_saw_online(tmp_path):
    # #521: an open page reconnects with `since` and replays events, not a snapshot. Without this event it keeps
    # the runner it saw online and hides 러너 꺼짐 although no runner came back.
    s = _settings(tmp_path)
    hub = Hub(s)
    for runner_id in ("lab", "hpc", "old"):
        hub.set_roster(runner_id, [])
    hub.store.append_event({"type": "runner.online", "ts": 1.0, "data": {"runner_id": "lab"}}, s.gateway.event_buffer)
    hub.store.append_event({"type": "runner.online", "ts": 2.0, "data": {"runner_id": "hpc"}}, s.gateway.event_buffer)
    hub.store.append_event({"type": "runner.offline", "ts": 3.0, "data": {"runner_id": "hpc"}}, s.gateway.event_buffer)
    seq = hub.store.event_bounds()[1]
    restarted = Hub(s)
    logged = [e for e in restarted.store.events_since(seq) if e["type"].startswith("runner.")]
    # "old" left no event within the stored window: a page may still hold it from an earlier snapshot.
    assert sorted((e["type"], e["data"]["runner_id"], e["data"]["reason"]) for e in logged) == [
        ("runner.offline", "lab", "gateway_restarted"), ("runner.offline", "old", "gateway_restarted")]
    assert all(e in restarted.events for e in logged)
    seq = restarted.store.event_bounds()[1]
    Hub(s)
    assert [e for e in restarted.store.events_since(seq) if e["type"].startswith("runner.")] == [], \
        "a second restart has nothing new to tell"


# ----- what an open page needs without a reload (#494) -----

@pytest.mark.asyncio
async def test_restart_and_resume_status_changes_reach_open_pages_as_events(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "r")
    restarted = Hub(s)
    replay = [e for e in restarted.store.events_since(0) if e.get("request_id") == "r"]
    assert [(e["type"], (e["data"].get("status") or e["data"].get("kind"))) for e in replay] == [
        ("request.status", "interrupted"), ("approval.requested", "resume")]
    assert replay[0]["data"]["previous"] == "running"

    card = next(iter(restarted.approvals))
    await restarted.resolve_approval(card, True)
    await _until(lambda: any(e["type"] == "request.status" and e["data"]["status"] == "waiting_for_runner"
                             for e in restarted.events))
    await restarted.cancel_request("r")


def test_a_snapshot_keeps_the_quota_deadline_of_a_waiting_step(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r", plan={"steps": [{"id": "s1", "agent_id": "worker"}]},
             quota_waits={"s1": {"engine": "codex", "resume_at": 100.0, "deadline_at": 900.0, "reason": "limit"}})
    detail = hub.request_step_details("r", hub.requests["r"])["s1"]
    assert detail["quota_resume_at"] == 100.0 and detail["quota_deadline_at"] == 900.0


# ----- PR #502 review: direct requests to known staff, resume countdown -----

@pytest.mark.asyncio
async def test_a_direct_request_to_known_staff_waits_for_the_first_runner(tmp_path):
    import httpx

    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    core = tmp_path / "agents" / "core"
    core.mkdir(parents=True)
    (core / "analyst.yaml").write_text("id: analyst\nname: Analyst\nrole: test\nengine: mock\n", encoding="utf-8")
    s.runner.agents_dir = str(tmp_path / "agents")
    Hub(s).set_roster("lab", [{"id": "curator", "name": "c", "role": "t", "engine": "mock"}])  # an earlier runner
    app = create_app(s)
    hub = app.state.hub
    hub.runners.clear()  # set_roster above left no live connection; the fresh gateway has no runner
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        ids = {}
        for agent in ("analyst", "curator"):
            response = await client.post("/api/requests", headers=auth,
                                         json={"text": "표 정리", "mode": "direct", "agent_id": agent})
            assert response.status_code == 200, response.text
            ids[agent] = response.json()["request_id"]
        unknown = await client.post("/api/requests", headers=auth,
                                    json={"text": "x", "mode": "direct", "agent_id": "ghost"})
        assert unknown.status_code == 404
    await _until(lambda: all(hub.requests[rid]["status"] == "waiting_for_runner" for rid in ids.values()))
    assert not hub.store.all("task")

    ws = CaptureSocket()
    roster = [{"id": name, "name": name, "role": "t", "engine": "mock"} for name in ("analyst", "curator")]
    hub.register_runner("lab", ws, roster, "inc")
    await _until(lambda: {m["task"]["agent_id"] for m in ws.sent if m.get("type") == "task.dispatch"}
                 == {"analyst", "curator"})
    hub.cancel_result_wait_s = 0.1
    for rid in ids.values():
        await hub.cancel_request(rid)


def test_a_resume_card_saved_with_the_old_hour_gets_the_decision_bound(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "r", status="interrupted")
    card = hub.new_resume_approval("r")
    hub.approvals[card.id]["approval"]["timeout_s"] = 3600  # what a card saved before PR #502 carries
    hub.save_approval(card.id)
    restarted = Hub(s)
    assert restarted.approvals[card.id]["approval"]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s
    assert restarted.snapshot()["data"]["approvals"][0]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s


def test_a_resume_card_after_every_step_finished_says_no_step_is_left(tmp_path):
    # Trial 2026-10-08: a research request restarted at its continue card read "남은 단계: 요청 전체" with 12/12 done.
    hub = Hub(_settings(tmp_path))
    _request(hub, "r", status="interrupted", plan={"steps": [{"id": "s1"}, {"id": "s2"}]},
             results={"s1": {"ok": True}, "s2": {"ok": True}})
    _request(hub, "unplanned", status="interrupted")
    assert hub.new_resume_approval("r").summary.endswith("남은 단계: 없음(단계는 모두 끝났고 그 뒤 검토·결정·보고서부터 이어 갑니다)")
    assert hub.new_resume_approval("unplanned").summary.endswith("남은 단계: 요청 전체")


def test_a_resume_card_before_the_continuation_plan_names_the_new_round(tmp_path):
    hub = Hub(_settings(tmp_path))
    plan = valid_plan()
    frozen = "a" * 64
    _request(hub, "r", status="interrupted", plan=plan,
             results={step["id"]: {"ok": True} for step in plan["steps"]},
             research_contract={"plan_sha256": frozen, "round": 2,
                                "rounds": [{"round": 1, "plan_sha256": frozen}],
                                "continuation": {"round": 2, "from_plan_sha256": frozen}})

    card = hub.new_resume_approval("r")

    assert card.summary.endswith("남은 단계: 이어 가기 2차: 새 계획·CP1부터 이어 갑니다")


def _unstarted_continuation_request(hub, rid="r", *, status="interrupted"):
    old = valid_plan(steps=2)
    new = copy.deepcopy(old)
    new["steps"][0]["instruction"] = "unapproved draft"
    old_results = {step["id"]: {"ok": True, "task_id": f"old-{step['id']}", "text": "done"}
                   for step in old["steps"]}
    req = _request(hub, rid, status=status, plan=old, results=old_results,
                   budget_usd=7, research_contract={"plan_sha256": "a" * 64, "round": 1,
                                                    "approval": {"status": "approved"},
                                                    "checkpoints": {"cp2": {"decision": "approved"}},
                                                    "review": {"verdict": "revise"}})
    archived = continuation.archive_round(req, old_results)
    req["plan"] = new
    req["results"] = {"s1": {"ok": True, "task_id": "reused-s1"}}
    req["budget_usd"] = 2
    req["research_contract"].update(
        round=2, rounds=[archived], plan_sha256="b" * 64,
        continuation={"round": 2, "from_plan_sha256": "a" * 64,
                      "plan_sha256": "b" * 64, "verified": False},
        approval={"status": "pending"})
    req["research_contract"]["checkpoints"].pop("cp2", None)
    hub.save_request(rid)
    return old, old_results


@pytest.mark.asyncio
async def test_cancel_restores_an_unstarted_continuation_before_writing_its_report(tmp_path):
    hub = Hub(_settings(tmp_path))
    old, old_results = _unstarted_continuation_request(hub, status="running")

    await hub.cancel_request("r")

    req = hub.requests["r"]
    assert req["plan"] == old and req["results"] == old_results and req["budget_usd"] == 7
    assert "끝난 단계 1개" not in req["report"]
    assert "끝난 단계 2개" in req["report"]


@pytest.mark.asyncio
async def test_resume_decline_restores_an_unstarted_continuation_before_terminal_record(tmp_path):
    hub = Hub(_settings(tmp_path))
    old, old_results = _unstarted_continuation_request(hub)
    card = hub.new_resume_approval("r")

    await hub.resolve_approval(card.id, False)

    req = hub.requests["r"]
    assert req["plan"] == old and req["results"] == old_results and req["budget_usd"] == 7


@pytest.mark.asyncio
async def test_resume_timeout_restores_an_unstarted_continuation_before_terminal_record(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    old, old_results = _unstarted_continuation_request(hub)
    card = hub.new_resume_approval("r")
    hub.approvals[card.id]["approval"]["created_at"] = time.time() - s.policy.approvals.pi_decision_timeout_s - 1
    hub.save_approval(card.id)

    assert await hub.expire_resume_card(card.id)

    req = hub.requests["r"]
    assert req["plan"] == old and req["results"] == old_results and req["budget_usd"] == 7


def _ledger_task(hub, tid, step_id, research_round=None):
    """A finished step task in the gateway task ledger; ``research_round`` is absent in round 1 (#90)."""
    meta = {"kind": "step", "step_id": step_id, **({"research_round": research_round} if research_round else {})}
    hub.store.put("task", tid, {"request_id": "r", "step_id": step_id, "kind": "step", "completed": True,
                                "payload": {"id": tid, "agent_id": "worker", "request_id": "r", "meta": meta}})


async def _end_interrupted_continuation(hub, s, path):
    if path == "cancel":
        await hub.cancel_request("r")
        return
    card = hub.new_resume_approval("r")
    if path == "decline":
        await hub.resolve_approval(card.id, False)
        return
    hub.approvals[card.id]["approval"]["created_at"] = time.time() - s.policy.approvals.pi_decision_timeout_s - 1
    hub.save_approval(card.id)
    assert await hub.expire_resume_card(card.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["cancel", "decline", "timeout"])
async def test_a_continuation_stopped_between_its_reuse_check_and_first_dispatch_is_restored(tmp_path, path):
    """PR #527 review P2: the gateway stopped after the new CP1 saved ``continuation.verified`` but before any step
    task went out. No step of round 2 is in the task ledger, so ending the request puts round 1 back."""
    s = _settings(tmp_path)
    hub = Hub(s)
    old, old_results = _unstarted_continuation_request(hub)
    hub.requests["r"]["research_contract"]["continuation"]["verified"] = True
    hub.save_request("r")
    for step in old["steps"]:  # round 1's own step tasks do not start round 2
        _ledger_task(hub, f"old-{step['id']}", step["id"])

    await _end_interrupted_continuation(hub, s, path)

    req = hub.requests["r"]
    assert req["plan"] == old and req["results"] == old_results and req["budget_usd"] == 7
    assert req["research_contract"]["round"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["cancel", "decline", "timeout"])
async def test_a_continuation_whose_step_task_went_out_is_not_rolled_back(tmp_path, path):
    s = _settings(tmp_path)
    hub = Hub(s)
    old, _old_results = _unstarted_continuation_request(hub)
    req = hub.requests["r"]
    req["research_contract"]["continuation"]["verified"] = True
    new_plan, reused = copy.deepcopy(req["plan"]), copy.deepcopy(req["results"])
    hub.save_request("r")
    _ledger_task(hub, "round2-s2", "s2", research_round=2)

    await _end_interrupted_continuation(hub, s, path)

    req = hub.requests["r"]
    assert req["plan"] == new_plan != old and req["results"] == reused and req["budget_usd"] == 2
    assert req["research_contract"]["round"] == 2


@pytest.mark.asyncio
async def test_shutdown_readiness_names_follow_ups_and_recruitments_outside_active_requests(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "done", status="done", followups=[{"id": "fu_1", "text": "그림 다시", "status": "running"},
                                                    {"id": "fu_0", "text": "끝난 것", "status": "done"}])
    assert hub.shutdown_readiness() == {
        "requests": [], "recruits": 0, "finishing": [], "ready": False,
        "followups": [{"request_id": "done", "followup_id": "fu_1", "text": "그림 다시"}]}
    hub.requests["done"]["followups"][0]["status"] = "done"
    assert hub.shutdown_readiness()["ready"] is True
    hub.recruits_running["lab"] = {"incarnation": "inc-1", "count": 1}
    assert hub.shutdown_readiness()["ready"] is False and hub.shutdown_readiness()["recruits"] == 1
    await hub.on_runner_message("lab", {"type": "recruit.done", "ts": 1.0, "data": {"agent": {"id": "c_x"}}})
    assert hub.shutdown_readiness() == {"requests": [], "followups": [], "recruits": 0, "finishing": [],
                                        "ready": True}


@pytest.mark.asyncio
async def test_a_recruitment_that_fails_while_it_is_being_sent_is_not_left_running(tmp_path, monkeypatch):
    """#515 review: count before sending, so a recruit.failed handled during send_runner finds the count."""
    from fastapi.testclient import TestClient
    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    hub.agent_runner[s.recruit.agent_id] = "lab"

    async def send(runner_id, msg):
        await hub.on_runner_message("lab", {"type": "recruit.failed", "ts": 1.0, "data": {"error": "no skill"}})

    monkeypatch.setattr(hub, "send_runner", send)
    with TestClient(app) as client:
        response = client.post("/api/recruit", json={"repo": "example/tool"},
                               headers={"Authorization": f"Bearer {s.gateway.client_token}"})
    assert response.status_code == 200
    assert hub.shutdown_readiness()["recruits"] == 0 and hub.shutdown_readiness()["ready"] is True



def test_a_runner_reconnect_keeps_its_recruitments_and_a_new_process_clears_them(tmp_path):
    """#515 review: unregister_runner drops runner_incarnations on every disconnect, so a reconnect of the same runner
    process used to clear a recruitment that was still running."""
    from unittest.mock import MagicMock

    hub = Hub(_settings(tmp_path))
    hub.recruits_running["lab"] = {"incarnation": "inc-1", "count": 1}
    first, second, third = MagicMock(), MagicMock(), MagicMock()
    hub.register_runner("lab", first, [], incarnation="inc-1")
    hub.unregister_runner("lab", first)
    hub.register_runner("lab", second, [], incarnation="inc-1")  # the same process reconnects
    assert hub.shutdown_readiness()["recruits"] == 1
    hub.unregister_runner("lab", second)
    hub.register_runner("lab", third, [], incarnation="inc-2")  # a new runner process
    assert hub.shutdown_readiness()["recruits"] == 0 and hub.shutdown_readiness()["ready"] is True


# ----- #521: gateway lifecycle follow-ups -----

def _recruiter_roster(s):
    return [{"id": s.recruit.agent_id, "name": "채용 담당", "role": "test", "engine": "mock"}]


@pytest.mark.asyncio
async def test_a_runner_restart_that_keeps_its_incarnation_clears_the_old_process_recruitments(tmp_path):
    """#521 P2 11·18: the incarnation is kept on disk, so only the per-process id tells a restart from a reconnect."""
    import httpx

    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    roster = _recruiter_roster(s)
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    first = CaptureSocket()
    hub.register_runner("lab", first, roster, "inc", process="p1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        response = await client.post("/api/recruit", headers=auth, json={"repo": "example/tool"})
    assert response.status_code == 200 and any(m.get("type") == "recruit.start" for m in first.sent)
    assert hub.shutdown_readiness()["recruits"] == 1

    hub.unregister_runner("lab", first)
    again = CaptureSocket()
    hub.register_runner("lab", again, roster, "inc", process="p1")  # the same process reconnects
    assert hub.shutdown_readiness()["recruits"] == 1
    hub.unregister_runner("lab", again)
    hub.register_runner("lab", CaptureSocket(), roster, "inc", process="p2")  # restarted: same incarnation
    assert hub.shutdown_readiness()["recruits"] == 0 and hub.shutdown_readiness()["ready"] is True


@pytest.mark.asyncio
async def test_a_recruitment_asked_while_the_runner_is_offline_is_refused_not_counted(tmp_path):
    """#521 P2 18: the frame provably never left, so nothing is running and `down` must not wait for it."""
    import httpx

    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    ws = CaptureSocket()
    hub.register_runner("lab", ws, _recruiter_roster(s), "inc", process="p1")
    hub.unregister_runner("lab", ws)  # agent_runner still names the runner; it may come back
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        response = await client.post("/api/recruit", headers=auth, json={"repo": "example/tool"})
    assert response.status_code == 409 and "꺼져" in response.json()["detail"]
    assert hub.shutdown_readiness()["recruits"] == 0 and hub.shutdown_readiness()["ready"] is True


@pytest.mark.asyncio
async def test_a_late_answer_after_a_recruitment_turn_does_not_mark_the_recruiter_working(tmp_path):
    """PR #526 review: a recruitment turn runs outside task.dispatch, so the runner did not know it had ended."""
    from labhq.recruit.paper2agent import Recruitment
    from labhq.runner.daemon import Runner

    s = _runner_settings(tmp_path)
    s.runner.force_engine = "mock"  # skips the paper2agent skill check
    runner = Runner(s)
    loop = asyncio.get_running_loop()
    turns = []

    async def run_task(task, workdir_override=None):
        turns.append(task.id)
        runner.broker.pending["appr_r"] = loop.create_future()
        runner.approval_tasks["appr_r"] = (task.id, s.recruit.agent_id)  # a card the turn left open
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="no offer letter")

    runner.run_task = run_task
    try:
        assert await Recruitment(runner, {"repo": "example/tool"}).run() is None
        await runner._on_message({"type": "approval.resolved", "id": "appr_r", "approved": False, "note": "closed"})
        sent = runner.store.pending()
        assert turns and not [m for m in sent if m["type"] == "agent.status" and m.get("task_id") == turns[0]]
        assert [m["id"] for m in sent if m["type"] == "approval.ack"] == ["appr_r"]
    finally:
        runner.store.close()


@pytest.mark.asyncio
async def test_a_follow_up_on_a_cancelled_request_is_refused_up_front(tmp_path):
    """#521 P2 10: every turn of a cancelled request is refused, so its follow-up could only fail."""
    hub = Hub(_settings(tmp_path))
    hub.register_runner("local", CaptureSocket(), ROSTER, "inc")
    _request(hub, "r", status="cancelled")
    _request(hub, "done", status="done")
    with pytest.raises(ValueError, match="취소한 요청"):
        hub.start_followup("r", "부분 결과만 설명해 주세요")
    assert not hub.requests["r"].get("followups")
    entry = hub.start_followup("done", "그 표는 어디 있나요?")  # a finished request still takes one
    assert entry["status"] == "running"
    await _until(lambda: hub.requests["done"]["followups"][0]["status"] != "running" or hub.store.all("task"))


@pytest.mark.asyncio
async def test_a_cancel_still_being_recorded_holds_down_and_a_restart_finishes_it(tmp_path, monkeypatch):
    """#521 P2 12: status is cancelled before the turns report and the terminal record is written; `down` in that
    window used to pass, and a restart skipped the request because cancelled is not active."""
    from fastapi.testclient import TestClient

    from labhq.gateway.server import RequestIn, create_app

    s = _team_settings(tmp_path)
    hub = Hub(s)
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    plan_task = next(m["task"]["id"] for m in ws.sent if m.get("type") == "task.dispatch")
    crash = asyncio.Event()

    async def process_lost(*args, **kwargs):
        await crash.wait()
        raise RuntimeError("simulated gateway process loss")

    monkeypatch.setattr(hub, "_await_cancelled_results", process_lost)
    cancelling = asyncio.create_task(hub.cancel_request(rid))
    await _until(lambda: {"type": "task.cancel", "task_id": plan_task} in ws.sent)
    await _until(lambda: hub.requests[rid].get("cancelled_tasks"))  # now waiting for the turn to report
    readiness = hub.shutdown_readiness()
    assert readiness["ready"] is False and readiness["requests"] == []
    assert [(row["id"], row["stage"]) for row in readiness["finishing"]] == [(rid, "cancel")]

    crash.set()  # Process loss, unlike caller cancellation, cannot finish cleanup.
    with pytest.raises(RuntimeError, match="simulated gateway process loss"):
        await cancelling
    assert not [e for e in hub.store.events_since(0) if e["type"] == "request.completed"]
    app = create_app(s)
    with TestClient(app):
        pass  # startup finishes the cancel the old process left half written
    req = app.state.hub.requests[rid]
    assert req["status"] == "cancelled" and "취소했습니다" in req["report"] and "cancelling" not in req
    assert req["cancelled_tasks"] == [plan_task] and req["cost_known"] is False  # the turn never reported
    final = [e for e in app.state.hub.store.events_since(0)
             if e["type"] == "request.completed" and e["request_id"] == rid]
    assert len(final) == 1 and final[0]["data"]["status"] == "cancelled"
    assert app.state.hub.shutdown_readiness()["ready"] is True
    restarted = Hub(s)
    await restarted.finish_interrupted_cancels()  # once only
    assert len([e for e in restarted.store.events_since(0) if e["type"] == "request.completed"]) == 1


@pytest.mark.asyncio
async def test_a_bundle_still_being_copied_holds_down(tmp_path, monkeypatch):
    """#521 P2 14: the bundle is copied after the terminal record, and `labhq down` (taskkill /F on Windows) lost it."""
    import threading

    hub = Hub(_settings(tmp_path))
    _request(hub, "r", status="done", results={"s1": {"ok": True, "workdir_id": "w1"}})
    release = threading.Event()

    def slow_bundle(rid, request=None, tasks=None, runner_capabilities=None):
        release.wait(5)
        return {"warning": "묶음 없음"}

    monkeypatch.setattr(hub, "_bundle_outcome", slow_bundle)
    copying = hub.schedule_terminal("r", "request.completed", {"ok": True, "status": "done"})
    readiness = hub.shutdown_readiness()
    assert readiness["ready"] is False and [(row["id"], row["stage"]) for row in readiness["finishing"]] == [
        ("r", "bundle")]
    release.set()
    await asyncio.wait_for(copying, 5)
    assert hub.shutdown_readiness()["ready"] is True and hub.shutdown_readiness()["finishing"] == []


@pytest.mark.asyncio
async def test_a_runner_that_comes_back_without_the_agent_ends_the_wait_at_once(tmp_path):
    """#521 P2 13: the agent's contract expired (or its yaml went) while the runner was off; the step waited 24 h and
    then blamed a runner that had connected."""
    hub = Hub(_settings(tmp_path))
    curator = {"id": "curator", "name": "c", "role": "t", "engine": "mock"}
    first = CaptureSocket()
    hub.register_runner("lab", first, [*ROSTER, curator], "inc", process="p1")
    hub.unregister_runner("lab", first)
    _request(hub, "r")
    step = asyncio.create_task(hub.dispatch(Task(agent_id="curator", request_id="r", prompt="표 정리")))
    await _until(lambda: hub.requests["r"]["status"] == "waiting_for_runner")
    hub.register_runner("lab", CaptureSocket(), ROSTER, "inc", process="p2")
    outcome = await asyncio.wait_for(step, 2)
    assert not outcome.ok and "no runner hosts agent 'curator'" in outcome.error and outcome.cost_usd == 0
    assert outcome.error_kind != "runner_offline" and "연결되지 않아" not in outcome.error
    assert hub.requests["r"]["status"] == "running" and "runner_waits" not in hub.requests["r"]


@pytest.mark.asyncio
async def test_a_first_agent_wait_after_a_gateway_restart_ends_when_its_runner_comes_back_without_it(tmp_path):
    """#521 P2 13, the request's first agent on a fresh gateway: it waits on the stored roster, not agent_runner."""
    s = _settings(tmp_path)
    curator = {"id": "curator", "name": "c", "role": "t", "engine": "mock"}
    Hub(s).set_roster("lab", [*ROSTER, curator])  # what the runner hosted before the gateway restarted
    hub = Hub(s)
    from labhq.gateway.server import RequestIn

    rid = hub.create_request(RequestIn(text="표 정리", mode="direct", agent_id="curator"))
    await _until(lambda: hub.requests[rid]["status"] == "waiting_for_runner")
    hub.register_runner("lab", CaptureSocket(), ROSTER, "inc", process="p1")
    await _until(lambda: hub.requests[rid]["status"] == "failed", timeout=2)
    text = hub.requests[rid].get("report", "") + hub.requests[rid].get("error", "")
    assert "curator 직원을 맡은 곳이 없습니다" in text and "연결되지 않아" not in text


@pytest.mark.asyncio
async def test_a_first_agent_wait_on_a_fresh_gateway_ends_when_a_runner_without_it_connects(tmp_path):
    """PR #530 review: no saved roster, so the request waited only because no runner was connected. The first runner
    hosts other staff; nothing dropped from its roster, yet the wait has nothing left to wait for."""
    from labhq.gateway.server import RequestIn

    hub = Hub(_settings(tmp_path))
    rid = hub.create_request(RequestIn(text="표 정리", mode="direct", agent_id="curator"))
    await _until(lambda: hub.requests[rid]["status"] == "waiting_for_runner")
    hub.register_runner("lab", CaptureSocket(), ROSTER, "inc", process="p1")
    await _until(lambda: hub.requests[rid]["status"] == "failed", timeout=2)
    text = hub.requests[rid].get("report", "") + hub.requests[rid].get("error", "")
    assert "curator 직원을 맡은 곳이 없습니다" in text and "연결되지 않아" not in text


# ----- #1168: a status write that comes after an await must not bring an ended request back -----

def _guard_hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    return Hub(settings)


@pytest.mark.parametrize("revival", ["running", "waiting_for_runner"])
@pytest.mark.asyncio
async def test_set_status_does_not_bring_a_cancelled_request_back(tmp_path, revival):
    hub = _guard_hub(tmp_path)
    try:
        _request(hub, "r", "cancelled")
        await hub._set_status("r", revival)
        assert hub.requests["r"]["status"] == "cancelled"
        assert not [e for e in hub.events if e.get("type") == "request.status"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_set_status_still_resumes_a_failed_request_on_the_same_id(tmp_path):
    """A model refusal is retried on the request's own id: `failed` is not a cancel, and its resume goes through."""
    hub = _guard_hub(tmp_path)
    try:
        _request(hub, "r", "failed")
        await hub._set_status("r", "waiting_for_runner")
        assert hub.requests["r"]["status"] == "waiting_for_runner"
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_set_status_does_not_revive_a_request_whose_cancel_is_still_committing(tmp_path):
    hub = _guard_hub(tmp_path)
    try:
        _request(hub, "r", "running", cancelling={"quota_steps": []})  # cancel is under way: the terminal record is not out
        await hub._set_status("r", "waiting_for_runner")
        assert hub.requests["r"]["status"] == "running"
        assert not [e for e in hub.events if e.get("type") == "request.status"]
    finally:
        hub.store.close()


@pytest.mark.parametrize("status", ["failed", "interrupted"])
@pytest.mark.asyncio
async def test_set_status_still_ends_a_request_as_failed_or_interrupted(tmp_path, status):
    hub = _guard_hub(tmp_path)
    try:
        _request(hub, "r", "running")
        await hub._set_status("r", status)
        assert hub.requests["r"]["status"] == status
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_set_status_still_moves_a_live_request_between_live_states(tmp_path):
    hub = _guard_hub(tmp_path)
    try:
        _request(hub, "r", "interrupted")
        await hub._set_status("r", "waiting_for_runner")
        await hub._set_status("r", "running")
        assert hub.requests["r"]["status"] == "running"
        assert [e["data"]["status"] for e in hub.events if e.get("type") == "request.status"] == [
            "waiting_for_runner", "running"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_a_resume_that_untracked_code_runs_does_not_revive_a_request_cancelled_while_it_waited(tmp_path):
    """`resume_when_ready` reads the request, then waits for the agents; a caller that is not the request's tracked task
    (an expiring restored card) is not stopped by the cancel, so the resume itself checks again before it goes on."""
    hub = _guard_hub(tmp_path)
    try:
        _request(hub, "r", "interrupted")
        entered, release = asyncio.Event(), asyncio.Event()

        async def agents(rid):
            entered.set()
            await release.wait()
            return set()

        hub.aresume_agents = agents
        started = []

        class Orchestration:
            async def run_request(self, *args, **kwargs):
                started.append(args)

        hub.orchestrator = Orchestration()
        resume = asyncio.create_task(hub.resume_when_ready("r"))  # not through track_request
        await asyncio.wait_for(entered.wait(), 2)
        hub.requests["r"].update(status="cancelled", cancelling={"quota_steps": []})  # the cancel lands while it waits
        release.set()
        await asyncio.wait_for(resume, 2)
        assert hub.requests["r"]["status"] == "cancelled"
        assert not started and not [e for e in hub.events if e.get("type") == "request.resumed"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_an_error_that_arrives_after_the_cancel_does_not_turn_the_request_into_failed(tmp_path):
    """A step that raises something other than CancelledError while a cancel is committing must not write `failed`
    over `cancelled`, nor send a `request.failed` the cancel's own terminal record then contradicts."""
    hub = _guard_hub(tmp_path)
    try:
        hub.agents["worker"] = {"id": "worker", "name": "worker", "role": "test", "engine": "mock"}
        _request(hub, "r", "running", mode="direct", agent_id="worker")

        async def broken(task):
            hub.requests["r"].update(status="cancelled", cancelling={"quota_steps": []})
            raise RuntimeError("the runner went away")

        hub.orchestrator.run_step = broken
        await hub.orchestrator.run_request("r")
        assert hub.requests["r"]["status"] == "cancelled"
        assert not [e for e in hub.events if e.get("type") == "request.failed"]
    finally:
        hub.store.close()


async def _resume_with_a_cancel_at(tmp_path, point):
    """`resume_when_ready` as untracked code runs it, with the cancel landing at one await; returns (hub, started)."""
    hub = _guard_hub(tmp_path)
    _request(hub, "r", "interrupted")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = {"agents": 0}
    started = []

    async def agents(rid):
        calls["agents"] += 1
        if point == "second_agents_lookup" and calls["agents"] == 2:
            entered.set()
            await release.wait()
        return set()

    hub.aresume_agents = agents
    publish = hub.publish

    async def gated_publish(event):
        if point == "resumed_event" and event.get("type") == "request.resumed":
            entered.set()
            await release.wait()
        await publish(event)

    hub.publish = gated_publish
    save = hub.asave_request

    async def gated_save(rid):
        if point == "status_save" and hub.requests[rid].get("status") == "running" and not entered.is_set():
            entered.set()
            await release.wait()
        await save(rid)

    hub.asave_request = gated_save

    class Orchestration:
        async def run_request(self, *args, **kwargs):
            started.append(args)

    hub.orchestrator = Orchestration()
    resume = asyncio.create_task(hub.resume_when_ready("r"))
    await asyncio.wait_for(entered.wait(), 2)
    hub.requests["r"].update(status="cancelled", cancelling={"quota_steps": []})  # the cancel lands at that await
    release.set()
    await asyncio.wait_for(resume, 2)
    return hub, started


@pytest.mark.parametrize("point", ["status_save", "second_agents_lookup", "resumed_event"])
@pytest.mark.asyncio
async def test_a_cancel_at_any_await_of_the_resume_stops_it_before_the_orchestration_starts(tmp_path, point):
    """#1172 review: the guard after the first lookup was not enough; the status save, the second lookup and the
    `request.resumed` event each await too, and nothing after them looked again."""
    hub, started = await _resume_with_a_cancel_at(tmp_path, point)
    try:
        assert hub.requests["r"]["status"] == "cancelled"
        assert not started
        if point != "resumed_event":  # that event is already on its way when the cancel lands
            assert not [e for e in hub.events if e.get("type") == "request.resumed"]
        if point == "status_save":  # the cancel replaced the state that this save was about: nothing says it was entered
            assert not [e for e in hub.events if e.get("type") == "request.status" and e["data"]["status"] == "running"]
    finally:
        hub.store.close()
