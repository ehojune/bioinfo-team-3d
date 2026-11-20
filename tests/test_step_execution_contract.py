"""Versioned CP1 placement tests with in-memory workers and a fake HPC runner."""

import copy
import json

import pytest
from pydantic import ValidationError

from labhq.orchestrator.cso import Orchestrator
from labhq.recruit import planning as recruit_planning
from labhq.research import contract, continuation
from labhq.settings import Settings
from tests.test_cso import FakeHub, result
from tests.test_research_protocol import MiniHub, valid_plan


def placed_plan(mode="local", runner="mock-runner", *, count=1):
    plan = valid_plan(steps=count)
    plan["step_execution_version"] = 1
    for step in plan["steps"]:
        step.update(execution=mode, runner_id=runner, outputs=[f"outputs/{step['id']}.tsv"])
    return plan


def approved(plan):
    receipt = contract.freeze_plan(plan, {"approved": True, "approval_id": "mock-cp1"})
    return {"plan_sha256": receipt["target_sha256"], "approval": receipt}


@pytest.mark.parametrize(("mode", "sha"), [
    (None, "6b454f6784e1e68d0dc14b0a78b82cb87eafa60327834f79e94d71298f2d74a4"),
    ("hpc", "40a54d45b686ee1826f00c594b1ae3dfeb76656c5bdbe51bdaddc571736812ba"),
])
def test_legacy_placement_keeps_the_canonical_hash(mode, sha):
    plan = valid_plan()
    if mode:
        plan["steps"][0]["execution"] = mode
    canonical = contract.canonical_plan_json(plan)
    explicit_nulls = copy.deepcopy(plan)
    explicit_nulls["step_execution_version"] = None
    explicit_nulls["steps"][0]["runner_id"] = None
    assert contract.canonical_plan_json(explicit_nulls) == canonical
    assert "step_execution_version" not in canonical
    assert "runner_id" not in canonical
    assert contract.plan_sha256(plan) == sha


@pytest.mark.parametrize("field, value", [("execution", "hpc"), ("runner_id", "another-runner")])
def test_changes_to_placement_need_new_approval_and_prevent_continuation_reuse(field, value):
    original = placed_plan()
    receipt = approved(original)["approval"]
    changed = copy.deepcopy(original)
    changed["steps"][0][field] = value
    assert contract.refresh_plan_approval(changed, receipt)["status"] == "needs_reapproval"
    reuse, rerun = continuation.split_reuse(original, changed, {"s1": {"ok": True}}, [])
    assert reuse == []
    assert rerun == {"s1": "spec changed"}


@pytest.mark.parametrize("version", [None, True, 2])
def test_runner_declarations_require_a_supported_contract(version):
    plan = placed_plan()
    plan["step_execution_version"] = version
    with pytest.raises(ValidationError, match="step_execution_version"):
        contract.ResearchPlan.model_validate(plan)


@pytest.mark.parametrize("recruit", [False, True], ids=["assigned-worker", "recruited-worker"])
async def test_cp1_shows_and_hashes_resolved_step_placement(monkeypatch, recruit):
    plan = valid_plan()
    plan["step_execution_version"] = 1
    plan["steps"][0]["execution"] = None
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def dispatch(task):
        assert task.meta["kind"] == "plan"
        schema = task.output_schema
        assert "step_execution_version" in schema["properties"]
        assert "step_execution_version" not in schema["required"]
        assert "runner_id" not in schema["$defs"]["ResearchStep"]["properties"]
        return result(task, structured=plan)

    hub = MiniHub(settings, dispatch, mode="plan_only", work_kind="research", text="compare conditions")
    hub.agents["worker"]["runner_id"] = "mock-runner"
    hub.requests["r"]["execution_location"] = {"mode": "local", "source": "PI selection", "approval_id": "mock-route-choice"}
    expected_agent, expected_runner = ("hire", "hire-runner") if recruit else ("worker", "mock-runner")
    if recruit:
        async def prepare(plan, request_id, hub, dispatch):
            [step] = plan["steps"]
            assert step["agent_id"] == "worker" and step["runner_id"] == "mock-runner"
            hub.agents["hire"] = {**hub.agents["worker"], "id": "hire", "runner_id": "hire-runner"}
            return {**plan, "steps": [{**step, "agent_id": "hire"}]}

        monkeypatch.setattr(recruit_planning, "prepare_plan", prepare)
    await Orchestrator(hub).run_request("r")
    row = hub.requests["r"]
    assert row["outcome"] == "plan_approved", row.get("report")
    [card] = [approval for approval in hub.approvals if approval["kind"] == "research_plan"]
    assert card["detail"]["단계 실행 위치 · runner"] == f"s1 · local · {expected_runner}"
    frozen = json.loads(card["detail"]["plan_canonical"])
    assert frozen["steps"][0]["agent_id"] == expected_agent
    assert frozen["steps"][0]["runner_id"] == expected_runner
    assert frozen["steps"][0]["execution"] == "local"
    assert frozen["step_execution_version"] == 1
    assert card["detail"]["target_sha256"] == contract.plan_sha256(frozen) == row["research_contract"]["plan_sha256"]

    # A runner change during the pause cannot reuse either the old CP1 or a resume-card receipt for its hash.
    row["resume_cp1"] = row["research_contract"]["approval"]
    row["plan"]["steps"][0]["runner_id"] = hub.agents[expected_agent]["runner_id"] = "replacement-runner"
    await Orchestrator(hub).run_request("r", resume=True)
    old, new = [approval["detail"] for approval in hub.approvals if approval["kind"] == "research_plan"]
    assert new["target_sha256"] != old["target_sha256"]
    assert new["단계 실행 위치 · runner"] == "s1 · local · replacement-runner"
    assert [task.meta["kind"] for task in hub.calls] == ["plan"]


@pytest.mark.parametrize("mode", ["local", "hpc"])
async def test_dag_dispatch_uses_the_approved_runner_pin(mode):
    plan = placed_plan(mode)

    async def dispatch(task):
        return result(task, text="mock output ready", outputs=["outputs/s1.tsv"])

    hub = FakeHub(dispatch)
    hub.agents["worker"].update(runner_id="mock-runner", hpc_tools=True, whole_request_hpc=True, builtin_mcp=["hpc"])
    hub.requests["r"].update(plan=plan, research_contract=approved(plan),
                             execution_location={"mode": mode, "runner_id": "mock-runner"})
    results = {}
    await Orchestrator(hub).run_dag("r", "request", plan["steps"], results)
    assert results["s1"].ok, results["s1"].error
    [task] = hub.calls
    assert task.meta["session_runner"] == "mock-runner"
    assert task.meta["execution_location"]["mode"] == mode


async def test_resume_cannot_dispatch_on_new_runner_under_an_old_cp1_or_mix_locations():
    original = placed_plan(count=2)
    changed = placed_plan(runner="replacement-runner", count=2)
    hub = FakeHub(None)
    hub.agents["worker"]["runner_id"] = "replacement-runner"
    hub.requests["r"].update(plan=original, research_contract=approved(original), execution_location={"mode": "local"})
    orchestrator = Orchestrator(hub)
    with pytest.raises(ValueError, match="differs from the assigned worker's runner"):
        await orchestrator.run_dag("r", "request", original["steps"], {}, only={"s2"})

    hub.requests["r"]["plan"] = changed
    with pytest.raises(ValueError, match="renewed approval required"):
        await orchestrator.run_dag("r", "request", changed["steps"], {}, only={"s2"})

    changed["steps"][1]["execution"] = "hpc"
    hub.requests["r"]["research_contract"] = approved(changed)
    with pytest.raises(ValueError, match="mixed plans are refused"):
        await orchestrator.run_dag("r", "request", changed["steps"], {}, only={"s2"})
    assert hub.calls == []
