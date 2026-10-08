"""Request and approval lifecycle for a PI working alone (PI 점검 R2 R5 R13 R15)."""
import asyncio
import time

import pytest

from labhq.gateway.server import PI_DECISION_KINDS, Hub
from labhq.models import ApprovalRequest, Task, TaskResult
from labhq.orchestrator.cso import BudgetExceeded, Orchestrator
from labhq.settings import Settings


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
async def test_pi_decision_cards_outlive_the_gate_timeout_and_park_the_request(tmp_path):
    s = _settings(tmp_path)
    s.policy.approvals.timeout_s = 1  # the gate timeout; it used to end every card below after one second
    hub = Hub(s)
    kinds = sorted(PI_DECISION_KINDS - {"resume"})  # a resume card has no waiter; tested below
    for kind in kinds:
        _request(hub, f"r_{kind}")
    waits = {kind: asyncio.create_task(hub.request_approval(kind, f"{kind}?", f"r_{kind}")) for kind in kinds}
    await asyncio.sleep(1.4)
    assert not [kind for kind, wait in waits.items() if wait.done()]
    assert {kind: hub.requests[f"r_{kind}"]["status"] for kind in kinds} == {kind: "waiting_pi" for kind in kinds}
    cards = {entry["approval"]["request_id"]: aid for aid, entry in hub.approvals.items()}
    for kind in kinds:
        assert hub.approvals[cards[f"r_{kind}"]]["approval"]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s
        await hub.resolve_approval(cards[f"r_{kind}"], True, "진행")
    decisions = {kind: await asyncio.wait_for(wait, 1) for kind, wait in waits.items()}
    assert all(decision["approved"] and "state" not in decision for decision in decisions.values())
    assert {hub.requests[f"r_{kind}"]["status"] for kind in kinds} == {"running"}
    assert not any(hub.requests[f"r_{kind}"].get("pi_waits") for kind in kinds)
    statuses = [e["data"]["status"] for e in hub.events if e["type"] == "request.status"]
    assert statuses.count("waiting_pi") == len(kinds) and statuses.count("running") == len(kinds)


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
