"""#36 PI decision (2026-10-01, option C): an out-of-scope general request asks the PI and runs only on "proceed"."""

import asyncio

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings

STEPS = [{"id": "A", "agent_id": "worker", "instruction": "solve", "depends_on": []},
         {"id": "B", "agent_id": "worker", "instruction": "summarize", "depends_on": ["A"]}]
REASON = "Navier-Stokes fluid dynamics is not bioinformatics."


class Hub:
    def __init__(self, verdict="out", decision=None, mode="orchestrate"):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.reviewer_agent = None
        self.requests = {"r": {"text": "나비에 스토크스 방정식 풀어줘", "mode": mode}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "worker")}
        self.events, self.calls, self.approvals = [], [], []
        self.verdict, self.decision = verdict, decision or {"approved": True}
        self.asked = asyncio.Event()

    async def dispatch(self, task):
        self.calls.append(task)
        kind = task.meta["kind"]
        if kind == "plan":
            plan = {"clarifying_questions": [], "steps": STEPS, "recruit": [], "notes": "plan"}
            if self.verdict:
                plan["scope"] = {"verdict": self.verdict, "reason": REASON}
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)
        text = "final report" if kind == "synthesis" else f"{task.meta.get('step_id')} done"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=text)

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        self.approvals.append(kwargs)
        self.asked.set()
        decision = self.decision
        if decision == "hang":  # the gateway restarts while the card waits
            await asyncio.Event().wait()
        return decision

    def supports_resume(self, agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass


def kinds(hub, kind):
    return [t for t in hub.calls if t.meta["kind"] == kind]


def step_ids(hub):
    return [t.meta["step_id"] for t in kinds(hub, "step")]


def test_scope_verdict_is_strict_in_the_plan_schema_and_absent_from_the_replan():
    for declare in (False, True):
        schema = cso.plan_schema(declare)
        scope = schema["properties"]["scope"]
        assert "scope" in schema["required"] and schema["additionalProperties"] is False
        assert scope["additionalProperties"] is False and set(scope["required"]) == set(scope["properties"])
        assert scope["properties"]["verdict"]["enum"] == ["in", "borderline", "out"]
        assert scope["properties"]["reason"]["type"] == "string"
        replan = cso.replan_schema(declare)  # a re-plan does not judge scope again
        assert "scope" not in replan["properties"] and "scope" not in replan["required"]
        # #373 keeps these additive fields optional so old plans and mock adapters remain valid.
        optional = {"assumptions", "route", "checklist", "suggested_next"}
        assert set(replan["required"]) == set(replan["properties"]) - optional


@pytest.mark.asyncio
async def test_plan_prompt_carries_the_configured_lab_scope_or_the_default():
    hub = Hub(verdict="in")
    await Orchestrator(hub).run_request("r")
    assert cso.DEFAULT_LAB_SCOPE in kinds(hub, "plan")[0].prompt
    hub = Hub(verdict="in")
    hub.s.lab.scope = "plant genomics only"
    await Orchestrator(hub).run_request("r")
    prompt = kinds(hub, "plan")[0].prompt
    assert "plant genomics only" in prompt and cso.DEFAULT_LAB_SCOPE not in prompt


@pytest.mark.asyncio
async def test_out_asks_before_any_step_and_proceed_runs_the_received_plan():
    hub = Hub()
    seen_at_card = []
    original = hub.request_approval

    async def approval(**kwargs):
        seen_at_card.append(list(step_ids(hub)))
        return await original(**kwargs)

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert seen_at_card == [[]]  # no step ran before the PI decided
    [card] = hub.approvals
    assert card["kind"] == "scope" and card["request_id"] == "r"
    assert card["summary"] == f"이 요청은 랩 범위 밖으로 보입니다: {REASON[:-1]}. 진행할까요?"
    assert card["detail"]["verdict"] == "out" and card["detail"]["reason"] == REASON
    assert len(kinds(hub, "plan")) == 1 and step_ids(hub) == ["A", "B"]  # no re-plan
    assert req["status"] == "done" and req["scope_check"]["decision"] == "proceed"
    assert "Scope verdict" not in req["report"]
    assert f"Scope verdict: out; {REASON} PI decision: proceed." in req["report_appendix"]


@pytest.mark.asyncio
async def test_out_declined_ends_without_running_a_step():
    hub = Hub(decision={"approved": False, "note": ""})
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "out_of_scope_declined" and req["status"] == "failed"
    assert kinds(hub, "step") == [] and kinds(hub, "synthesis") == []
    assert [t.meta["kind"] for t in hub.calls] == ["plan"]
    assert req["scope_check"]["decision"] == "declined"
    assert "no step was dispatched" in req["report"]


@pytest.mark.asyncio
async def test_out_unanswered_follows_the_approval_timeout_and_runs_nothing():
    hub = Hub(decision={"approved": False, "note": "timed out", "state": "timed_out"})
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    # An unanswered card is not a decline (PI 점검 R5).
    assert req["outcome"] == "out_of_scope_timed_out" and req["scope_check"]["decision"] == "timed_out"
    assert kinds(hub, "step") == []


@pytest.mark.asyncio
async def test_borderline_runs_and_leaves_one_report_line():
    hub = Hub(verdict="borderline")
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert hub.approvals == [] and step_ids(hub) == ["A", "B"] and req["status"] == "done"
    assert "Scope verdict" not in req["report"]
    lines = [line for line in req["report_appendix"].splitlines() if line.startswith("Scope verdict:")]
    assert lines == [f"Scope verdict: borderline; {REASON}"]


@pytest.mark.parametrize("verdict", ["in", None])
@pytest.mark.asyncio
async def test_in_or_a_plan_without_a_verdict_runs_as_before(verdict):
    hub = Hub(verdict=verdict)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert hub.approvals == [] and step_ids(hub) == ["A", "B"] and req["status"] == "done"
    assert "Scope verdict" not in req["report"]
    assert "Scope verdict" not in req["report_appendix"]
    assert req.get("scope_check", {}).get("verdict") == verdict


@pytest.mark.asyncio
async def test_plan_only_records_the_verdict_and_never_asks():
    hub = Hub(mode="plan_only")
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert hub.approvals == [] and kinds(hub, "step") == [] and req["outcome"] == "plan_only"
    assert req["scope_check"] == {"verdict": "out", "reason": REASON}


@pytest.mark.asyncio
async def test_clarification_waiting_off_records_out_without_asking():
    hub = Hub()
    hub.s.orchestrator.wait_for_clarification = False
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert hub.approvals == [] and step_ids(hub) == ["A", "B"]
    assert req["scope_check"]["decision"] == "not_asked"


@pytest.mark.asyncio
async def test_restart_during_the_card_asks_again_from_the_stored_plan_and_runs_once():
    hub = Hub(decision="hang")
    first = asyncio.create_task(Orchestrator(hub).run_request("r"))
    await asyncio.wait_for(hub.asked.wait(), 5)
    first.cancel()  # the gateway process dies with the card open
    with pytest.raises(asyncio.CancelledError):
        await first
    assert hub.requests["r"]["scope_check"]["decision"] == "pending" and step_ids(hub) == []

    hub.decision = {"approved": True}
    await Orchestrator(hub).run_request("r", resume=True)
    req = hub.requests["r"]
    assert [a["kind"] for a in hub.approvals] == ["scope", "scope"]
    assert len(kinds(hub, "plan")) == 1 and step_ids(hub) == ["A", "B"]
    assert req["status"] == "done"


@pytest.mark.parametrize("decision, steps", [("proceed", ["A", "B"]), ("declined", [])])
@pytest.mark.asyncio
async def test_restart_uses_a_saved_decision_without_asking(decision, steps):
    hub = Hub()
    hub.requests["r"].update(plan={"steps": STEPS, "scope": {"verdict": "out", "reason": REASON}},
                             scope_check={"verdict": "out", "reason": REASON, "decision": decision})
    await Orchestrator(hub).run_request("r", resume=True)
    assert hub.approvals == [] and step_ids(hub) == steps
    assert kinds(hub, "plan") == []


@pytest.mark.asyncio
async def test_a_later_plan_without_a_verdict_keeps_the_first_out_verdict():
    """An engine that does not enforce the schema may drop `scope` from the plan after a clarification; the first
    out verdict still stops the steps (#346 review)."""
    hub = Hub(decision={"approved": False, "note": ""})
    plans = 0
    original_dispatch = hub.dispatch

    async def dispatch(task):
        nonlocal plans
        if task.meta["kind"] != "plan":
            return await original_dispatch(task)
        plans += 1
        hub.calls.append(task)
        plan = {"clarifying_questions": [], "steps": STEPS, "recruit": [], "notes": "plan"}
        if plans == 1:
            plan["clarifying_questions"] = ["Which equation form?"]
            plan["scope"] = {"verdict": "out", "reason": REASON}
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        if kwargs["kind"] == "clarify":
            return {"approved": True, "note": "incompressible"}
        return {"approved": False, "note": ""}

    hub.dispatch, hub.request_approval = dispatch, approval
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert plans == 2 and [card["kind"] for card in hub.approvals] == ["clarify", "scope"]
    assert kinds(hub, "step") == [] and req["outcome"] == "out_of_scope_declined"


def test_bench_proceeds_on_a_scope_card():
    from labhq.bench import _scripted_answer

    assert _scripted_answer({"scripted_pi_answers": []}, "범위 밖", "scope") == (True, "bench 규칙: 범위 확인은 진행")
