"""Third review regressions: local stores and fake conversion only."""
import asyncio
import copy
import time

import pytest

from labhq.models import AgentSpec, ContractInfo, Employment, Engine, Task, TaskResult
from labhq.orchestrator.cso import BudgetExceeded, Orchestrator
from labhq.recruit import planning
from labhq.settings import Settings
from tests.test_cso import FakeHub
from tests.test_paper_skill_attach import runner
from tests.test_recruit_before_plan import proposal, hire_task
from tests.test_research_protocol import MiniHub, valid_plan


@pytest.mark.asyncio
async def test_conversion_cost_reaches_budget_once_even_after_recovery(runner, monkeypatch):
    from labhq.gateway.server import Hub
    r, _ = runner
    original = r.run_task

    async def local_run(task, **kwargs):
        if task.meta.get("kind") == "recruit":
            res = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                             cost_usd=11, error="fake paid conversion failed")
        else:
            res = await original(task, **kwargs)
        Hub._record_task_cost(hub, task.request_id, task.id, res)
        return res

    hub = FakeHub(local_run)
    # The gateway and the runner read the same setting (#1139): the runner requires the held allowance that only a
    # reserving gateway puts on the task. FakeHub pins the older check, so match the runner (default: on).
    hub.s.policy.budget.reserve_before_dispatch = r.s.policy.budget.reserve_before_dispatch
    hub.agents["recruiter"] = {"id": "recruiter", "engine": "mock"}
    r.s.runner.force_engine = "mock"
    monkeypatch.setattr(r, "run_task", local_run)
    monkeypatch.setattr(r, "_send_roster", lambda: None)
    orch = Orchestrator(hub)
    task = hire_task()
    receipt = await orch.run_step(task)
    assert receipt.structured["status"] == "failed"
    assert orch.cost["r"] == hub.requests["r"]["cost_usd"] == 11
    assert "step_id" not in next(iter(hub.requests["r"]["cost_items"].values()))
    # Replay the durable result and account for both task IDs again.
    for tid in hub.requests["r"]["cost_by_task"]:
        orch._count_cost("r", tid)
    assert orch.cost["r"] == 11
    with pytest.raises(BudgetExceeded):
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt="next"))
    assert [a["kind"] for a in hub.approvals] == ["budget"]
    assert len(hub.calls) == 1

    restarted = Orchestrator(hub)
    restarted.cost["r"] = hub.requests["r"]["cost_usd"]
    restarted.cost_tasks["r"] = set(hub.requests["r"]["cost_by_task"])
    restarted._count_recorded_costs("r")
    assert restarted.cost["r"] == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("repo", "https://github.com/example/revised"),
                                         ("focus", "use the revised method")])
async def test_changed_conversion_payload_builds_new_talent(runner, monkeypatch, field, value):
    r, _ = runner
    converted = []

    async def convert(recruitment):
        converted.append((recruitment.repo, recruitment.focus, recruitment.slug))
        spec = AgentSpec(id="c_" + recruitment.slug, name="Paper", role="method", engine=Engine.mock,
                         employment=Employment.contract,
                         contract=ContractInfo(paper=recruitment.paper, repo=recruitment.repo,
                             kind="technician", hired_at=1, expires_at=time.time() + 1000, status="active",
                             verification={"probe_tools": ["reproduce"], "offer_status": "verified"}))
        r.registry.save_contract(spec)
        return spec

    monkeypatch.setattr("labhq.recruit.paper2agent.Recruitment.run", convert)
    monkeypatch.setattr(r, "_send_roster", lambda: None)
    first = await r.run_task(hire_task())
    changed = hire_task()
    changed.meta["recruit"][field] = value
    second = await r.run_task(changed)
    assert len(converted) == 2
    assert first.structured["agent_id"] != second.structured["agent_id"]
    again = await r.run_task(changed)
    assert again.structured["reused"] is True
    assert len(converted) == 2


BAD_ROWS = [
    {"step_ids": ["missing"]}, {"step_ids": ["s1", "missing"]}, {"step_ids": ["s1", "s1"]},
    {"step_ids": []}, {"step_ids": None}, {"step_ids": "s1"}, {"step_ids": [12]},
    {"paper": " ", "repo": ""}, {"focus": " "}, {"reason": ""}, {"kind": "consultant"},
    {"repo": 123}, {"unexpected": True},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("bad", BAD_ROWS)
async def test_invalid_recruit_proposal_is_corrected_before_hiring(research, bad):
    s = Settings()
    s.research.enabled = research
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.reviewer_agent = None
    plan = valid_plan()
    plan["recruit"] = [{**proposal(), **bad}]
    if not research:
        plan = {k: plan[k] for k in ("steps", "recruit", "notes", "clarifying_questions")}
    plans = []
    hires = []

    async def reply(task):
        kind = task.meta["kind"]
        data = {}
        if kind == "plan":
            plans.append(task.prompt)
            data = copy.deepcopy(plan)
            if len(plans) > 1:
                data["recruit"] = []
        elif kind in {"recruit_check", "plan_recruit"}:
            hires.append(kind)
            data = {"status": "unavailable"}
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, cost_usd=0, structured=data)

    hub = MiniHub(s, reply, mode="plan_only", work_kind="research" if research else "simple")
    await Orchestrator(hub).run_request("r")
    assert len(plans) == 2, hub.requests["r"].get("report")
    assert "recruit" in plans[1]
    assert not hires
    assert hub.requests["r"]["status"] == "done"


@pytest.mark.parametrize("rows", [
    [proposal(), proposal("PMID:87654321")],
    [proposal(), {**proposal(), "focus": "different"}],
    [{k: v for k, v in proposal().items() if k != "step_ids"}],
])
def test_conflicting_or_missing_assignments_are_not_silently_discarded(rows):
    with pytest.raises(ValueError, match="recruit"):
        planning._proposals({"steps": [{"id": "s1"}], "recruit": rows})


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen", ["research_contract", "results"])
@pytest.mark.parametrize("ended", ["expired", "released"])
@pytest.mark.parametrize("receipt", [True, False])
async def test_ended_assigned_contract_terminates_resume_once(tmp_path, frozen, ended, receipt):
    from labhq.gateway.server import Hub
    from labhq.registry import Registry
    from pathlib import Path
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.gateway.resume_wait_s = 0.01
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.contract_dir = str(tmp_path / "contracts")
    s.runner.talent_dir = str(tmp_path / "talent")
    reg = Registry(Path(s.runner.agents_dir), Path(s.runner.talent_dir), Path(s.runner.contract_dir))
    spec = AgentSpec(id="c_paper", name="Paper", role="method", engine=Engine.mock,
                     employment=Employment.contract,
                     contract=ContractInfo(paper=proposal()["paper"], kind="technician", hired_at=1,
                                           expires_at=time.time() - 1 if ended == "expired" else time.time() + 100,
                                           status="active"))
    reg.save_contract(spec)
    if ended == "released":
        reg.release(spec.id)
    plan = {"steps": [{"id": "done", "agent_id": "worker"}, {"id": "s1", "agent_id": "c_paper"}],
            "recruit": [proposal()]}
    hub = Hub(s)
    req = {"id": "r", "text": "reproduce", "mode": "team", "status": "running", "plan": plan,
           frozen: {"done": {"ok": True}}}
    if receipt:
        req["plan_recruitment"] = {planning._batch_key([proposal()]): {
            "results": {"paper": {"status": "active", "agent_id": "c_paper"}},
            "fallback_agents": {"s1": "worker"}}}
    hub.requests["r"] = req
    hub.save_request("r")
    hub.store.close()
    restored = Hub(s)
    async def forbidden(*args, **kwargs):
        pytest.fail("ended contract must not dispatch")
    restored.orchestrator.run_request = forbidden
    try:
        for _ in range(2):
            await restored.resume_when_ready("r")
            assert restored.requests["r"]["status"] == "failed"
            assert restored.requests["r"]["outcome"] == "recruit_contract_ended"
            assert restored.requests["r"]["plan"] == plan
        assert not restored.approvals
    finally:
        await asyncio.sleep(0)
        for task in restored.terminal_tasks.values():
            task.cancel()
        await asyncio.gather(*restored.terminal_tasks.values(), return_exceptions=True)
        for task in restored.resume_timers.values():
            task.cancel()
        restored.store.close()


@pytest.mark.parametrize("completed", [False, True])
def test_resume_checks_remaining_assignments_without_receipts(tmp_path, completed):
    from types import SimpleNamespace
    import yaml
    s = Settings()
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.contract_dir = str(tmp_path / "contracts")
    s.runner.talent_dir = str(tmp_path / "talent")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "paper.yaml").write_text(yaml.safe_dump({"id": "c_paper", "contract": {
        "status": "active", "expires_at": time.time() - 1}}), encoding="utf-8")
    req = {"id": "r", "plan": {"steps": [{"id": "s1", "agent_id": "c_paper"}],
                                "recruit": [proposal()]}}
    if completed:
        req["results"] = {"s1": {"ok": True}}
    else:
        req["plan_recruitment"] = {planning._batch_key([proposal()]): {
            "fallback_agents": {"s1": "worker"}, "results": {}}}
    hub = SimpleNamespace(s=s, agents={}, save_request=lambda rid: None)
    assert planning.pending_receipt_agents(req, hub) == set()
    assert "recruit_contract_failure" not in req
    assert req["plan"]["steps"][0]["agent_id"] == ("c_paper" if completed else "worker")
