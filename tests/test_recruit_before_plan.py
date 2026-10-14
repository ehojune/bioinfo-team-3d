"""Request-scoped hiring: fake recruiter only, never a real Paper2Agent conversion."""
import copy
import asyncio
import time

import pytest

from labhq.models import AgentSpec, ContractInfo, Employment, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator, PLAN_PROMPT, RESEARCH_PLAN_PROMPT
from labhq.settings import Settings
from tests.test_research_protocol import MiniHub, valid_plan
from tests.test_paper_skill_attach import runner


def proposal(paper="PMID:12345678", steps=None):
    return {"paper": paper, "repo": "", "focus": "reproduce the paper", "reason": "PI asks for reproduction",
            "step_ids": steps or ["s1"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["active", "denied", "failed", "timed_out", "unavailable", "probation"])
@pytest.mark.parametrize("research", [True, False])
async def test_hire_before_cp1_and_assign_this_plan(outcome, research):
    s = Settings()
    s.research.enabled = research
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.reviewer_agent = None
    plan = valid_plan(steps=3)
    plan["steps"][1]["agent_id"] = "bioinfo-agent"
    plan["steps"][2]["depends_on"] = ["s1", "s2"]
    plan["recruit"] = [proposal(), proposal("PMID:87654321", ["s3"])]
    if not research:
        plan = {k: plan[k] for k in ("steps", "recruit", "notes", "clarifying_questions")}
    order = []

    async def reply(task):
        kind = task.meta["kind"]
        order.append(kind)
        if kind == "plan":
            data = copy.deepcopy(plan)
        elif kind == "recruit_check":
            data = {"status": "unavailable" if outcome == "unavailable" else "ready"}
        elif kind == "plan_recruit":
            data = {"status": outcome, "agent_id": "c_paper"}
            if outcome == "active":
                hub.agents["c_paper"] = {"id": "c_paper", "name": "Paper", "role": "method", "engine": "mock",
                                         "employment": "contract", "contract_status": "active"}
        else:
            data = {}
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=data, cost_usd=0)

    hub = MiniHub(s, reply, mode="plan_only", work_kind="research" if research else "simple",
                  text="Reproduce the paper and compare a new implementation")
    hub.agents.update({a: {"id": a, "name": a, "role": "test", "engine": "mock"}
                       for a in ("bioinfo-agent", "recruiter")})

    async def approve(**card):
        order.append(card["kind"])
        hub.approvals.append(card)
        return {"approved": outcome != "denied" or card["kind"] != "recruit_plan",
                "approval_id": "test", "decided_at": 1.0}

    hub.request_approval = approve
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    cards = [a for a in hub.approvals if a["kind"] == "recruit_plan"]
    assert len(cards) == (0 if outcome == "unavailable" else 1)
    if cards:
        assert len(cards[0]["detail"]["recruits"]) == 2
        assert "수습 통과하면 바로 투입" in cards[0]["summary"]
    final = req["plan"]
    assert final["steps"][0]["agent_id"] == ("c_paper" if outcome == "active" else "worker")
    assert final["steps"][0]["depends_on"] == final["steps"][1]["depends_on"] == []
    assert final["steps"][1]["agent_id"] == "bioinfo-agent"
    assert final["steps"][2]["depends_on"] == ["s1", "s2"]
    if outcome != "active":
        assert final["warnings"] and "재배정" in final["warnings"][-1]
    if outcome in {"denied", "unavailable"}:
        assert "plan_recruit" not in order
    if research:
        assert order.index("research_plan") > order.index("recruit_check")
        if outcome == "active":
            assert order.index("research_plan") > order.index("plan_recruit")
        cp1 = hub.approvals[-1]
        assert final["steps"][0]["agent_id"] in cp1["detail"]["plan_canonical"]
        if outcome != "active":
            assert "재배정" in cp1["summary"]


def test_both_planners_require_reproduction_parallel_comparison_and_joint_nextflow():
    for prompt in (PLAN_PROMPT, RESEARCH_PLAN_PROMPT):
        for phrase in ("technician/full", "step_ids", "parallel", "comparison", "Nextflow", "bioinfo-agent"):
            assert phrase in prompt


def hire_task():
    return Task(agent_id="recruiter", request_id="r", prompt="hire",
                meta={"kind": "plan_recruit", "recruit": proposal(), "activate_on_pass": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "probation", "failed", "timed_out", "unavailable"])
async def test_runner_waits_for_probation_and_handles_failure(runner, monkeypatch, state):
    r, _ = runner
    r.s.runner.task_timeout_s = 1
    events = []
    monkeypatch.setattr(r, "_send_roster", lambda: events.append("roster"))

    async def fake_run(recruitment):
        events.append("convert")
        if state == "timed_out":
            await asyncio.sleep(10)
        if state == "failed":
            raise RuntimeError("fake conversion failed")
        events.append("probation")
        spec = AgentSpec(id="c_paper", name="Paper", role="method", engine=Engine.mock,
                         employment=Employment.contract,
                         contract=ContractInfo(paper="PMID:12345678", kind="technician", hired_at=1,
                                               expires_at=time.time() + 1000, status=state))
        r.registry.save_contract(spec)
        return spec

    monkeypatch.setattr("labhq.recruit.paper2agent.Recruitment.run", fake_run)
    if state == "unavailable":
        monkeypatch.setattr("labhq.doctor._paper2agent_row", lambda *a: {"status": "fail"})
    result = await r.run_task(hire_task())
    expected = "failed" if state == "probation" else state
    assert result.structured["status"] == expected
    if state == "active":
        assert events == ["convert", "probation", "roster"]
        assert r.registry.get("c_paper").contract.status == "active"
    if state == "unavailable":
        assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_talent_rehire_without_conversion(runner, monkeypatch, tmp_path, changed):
    from labhq.recruit.provenance import folder_sha256
    r, seen = runner
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("paper method", encoding="utf-8")
    spec = AgentSpec(id="c_paper", name="Paper", role="method", engine=Engine.mock,
                     employment=Employment.contract,
                     contract=ContractInfo(paper="pmid:12345678", kind="full", hired_at=1, expires_at=2,
                                           status="active", skill_dir=str(skill), skill_sha256=folder_sha256(skill),
                                           verification={"probe_tools": ["reproduce"], "offer_status": "verified"}))
    r.registry.save_contract(spec)
    r.registry.release(spec.id)
    if changed:
        (skill / "SKILL.md").write_text("modified", encoding="utf-8")

    async def forbidden(*args):
        pytest.fail("talent must not be converted again")

    monkeypatch.setattr("labhq.recruit.paper2agent.Recruitment.run", forbidden)
    result = await r.run_task(hire_task())
    assert result.structured["status"] == ("failed" if changed else "active")
    if not changed:
        assert result.structured["reused"] is True
        assert r.registry.get(spec.id).contract.expires_at > time.time()
    assert not seen


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
async def test_nextflow_hire_spec_implementation_hire_validation_and_resume(parallel):
    from labhq.recruit.planning import prepare_plan
    from tests.test_cso import FakeHub, result
    calls = []
    started = set()
    both = asyncio.Event()

    async def reply(task):
        calls.append((task.meta["kind"], task.agent_id))
        if task.meta["kind"] == "recruit_check":
            return result(task, structured={"status": "ready"}, cost_usd=0)
        if task.meta["kind"] == "plan_recruit":
            return result(task, structured={"status": "active", "agent_id": "c_paper"}, cost_usd=0)
        if parallel and task.meta["kind"] == "step":
            sid = task.meta["step_id"]
            if sid != "validate":
                started.add(sid)
                if len(started) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 2)
            else:
                assert both.is_set()
        return result(task, text="done", cost_usd=0)

    hub = FakeHub(reply)
    hub.approve_budget = True
    hub.agents.update({"c_paper": {"id": "c_paper", "name": "Paper", "engine": "mock", "role": "method",
                                     "employment": "contract", "contract_status": "active"},
                       "bioinfo-agent": {"id": "bioinfo-agent", "engine": "mock", "name": "B", "role": "code"}})
    draft = {"recruit": [proposal(steps=["spec", "validate"])], "steps": [
        {"id": "spec", "agent_id": "worker", "instruction": "method and tools", "depends_on": []},
        {"id": "workflow", "agent_id": "bioinfo-agent", "instruction": "Nextflow", "depends_on": ["spec"]},
        {"id": "validate", "agent_id": "worker", "instruction": "validate workflow", "depends_on": ["workflow"]}]}
    if parallel:
        draft["recruit"][0]["step_ids"] = ["spec"]
        draft["steps"][1]["depends_on"] = []
        draft["steps"][2]["depends_on"] = ["spec", "workflow"]
        draft["steps"][2]["instruction"] = "compare both methods"
    plan = await prepare_plan(draft, "r", hub, reply)
    resumed = await prepare_plan(draft, "r", hub, reply)
    assert resumed == plan and len(hub.approvals) == 1
    assert calls.count(("plan_recruit", "recruiter")) == 1
    hub.requests["r"]["plan"] = plan
    await Orchestrator(hub).run_dag("r", "Nextflow reproduction", plan["steps"], {})
    agents = [agent for kind, agent in calls if kind == "step"]
    if parallel:
        assert set(agents[:2]) == {"c_paper", "bioinfo-agent"} and agents[2] == "worker"
    else:
        assert agents == ["c_paper", "bioinfo-agent", "c_paper"]


@pytest.mark.asyncio
async def test_interrupted_approval_does_not_open_a_second_hiring_card():
    from labhq.recruit.planning import prepare_plan
    from tests.test_cso import FakeHub, result

    async def reply(task):
        assert task.meta["kind"] == "recruit_check"
        return result(task, structured={"status": "ready"}, cost_usd=0)

    hub = FakeHub(reply)
    cards = []

    async def interrupted(**card):
        cards.append(card)
        raise asyncio.CancelledError()

    hub.request_approval = interrupted
    draft = {"recruit": [proposal()], "steps": [
        {"id": "s1", "agent_id": "worker", "instruction": "reproduce", "depends_on": []}]}
    with pytest.raises(asyncio.CancelledError):
        await prepare_plan(draft, "r", hub, reply)
    final = await prepare_plan(draft, "r", hub, reply)
    assert len(cards) == 1
    assert final["steps"][0]["agent_id"] == "worker"
    assert final["recruit_results"][0]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_recovery_does_not_reuse_another_paper_or_batch_receipt():
    from labhq.gateway.server import Hub
    from labhq.recruit.planning import prepare_plan
    from tests.test_cso import FakeHub, result

    entries = []
    interrupted = False

    async def reply(task):
        nonlocal interrupted
        if task.meta["kind"] == "recruit_check":
            return result(task, structured={"status": "ready"})
        if entries and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        for entry in entries:
            if Hub._matches_recovery(task, entry):
                return result(task, structured=entry["receipt"])
        aid = "c_" + str(len(entries))
        hub.agents[aid] = {"id": aid, "employment": "contract", "contract_status": "active"}
        receipt = {"status": "active", "agent_id": aid}
        entries.append({"request_id": "r", "kind": task.meta["kind"], "step_id": task.meta.get("step_id"),
                        "payload": task.model_dump(mode="json"), "receipt": receipt})
        return result(task, structured=receipt)

    hub = FakeHub(reply)
    hub.approve_budget = True
    draft = {"recruit": [proposal(), proposal("PMID:87654321", ["s2"])], "steps": [
        {"id": sid, "agent_id": "worker"} for sid in ("s1", "s2")]}
    with pytest.raises(asyncio.CancelledError):
        await prepare_plan(draft, "r", hub, reply)
    final = await prepare_plan(draft, "r", hub, reply)
    assert [step["agent_id"] for step in final["steps"]] == ["c_0", "c_1"]
    draft["recruit"][0]["focus"] = "a revised method specification"
    revised = await prepare_plan(draft, "r", hub, reply)
    assert [step["agent_id"] for step in revised["steps"]] == ["c_2", "c_3"]


@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("step_ids", [None, []])
def test_new_recruit_schema_requires_target_steps(research, step_ids):
    from jsonschema import Draft202012Validator
    from labhq.orchestrator.cso import PLAN_SCHEMA
    from labhq.research.contract import RESEARCH_PLAN_SCHEMA, ResearchPlan

    schema = RESEARCH_PLAN_SCHEMA if research else PLAN_SCHEMA
    plan = valid_plan()
    row = proposal()
    if step_ids is None:
        row.pop("step_ids")
    else:
        row["step_ids"] = step_ids
    plan["recruit"] = [row]
    recruit_schema = {**schema["properties"]["recruit"], "$defs": schema.get("$defs", {})}
    assert list(Draft202012Validator(recruit_schema).iter_errors(plan["recruit"]))
    # Frozen legacy research plans still load without rewriting their canonical content.
    if research and step_ids is None:
        assert "step_ids" not in ResearchPlan.model_validate(plan).model_dump(mode="json")["recruit"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["team", "plan_only"])
@pytest.mark.parametrize("reconnect", [True, False])
@pytest.mark.parametrize("contract_state", ["active", "expired", "released", "assigned_expired"])
async def test_resume_waits_for_active_recruit_receipt(tmp_path, mode, reconnect, contract_state):
    from labhq.gateway.server import Hub
    from labhq.recruit.planning import prepare_plan

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.resume_wait_s = 0.2
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.contract_dir = str(tmp_path / "contracts")
    settings.runner.talent_dir = str(tmp_path / "talent")
    from pathlib import Path
    from labhq.registry import Registry
    registry = Registry(Path(settings.runner.agents_dir), Path(settings.runner.talent_dir),
                        Path(settings.runner.contract_dir))
    spec = AgentSpec(id="c_paper", name="Paper", role="method", engine=Engine.mock,
                     employment=Employment.contract,
                     contract=ContractInfo(paper=proposal()["paper"], kind="technician", hired_at=1,
                                           expires_at=time.time() + 1000, status="active"))
    registry.save_contract(spec)
    if contract_state in {"expired", "assigned_expired"}:
        spec.contract.expires_at = time.time() - 1
        registry.save_contract(spec)
    elif contract_state == "released":
        registry.release(spec.id)
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "status": "running", "mode": mode, "text": "reproduce",
                         "plan": {"recruit": [proposal()], "steps": [{"id": "s1", "agent_id": "worker"}]}}

    async def forbidden(task):
        pytest.fail("saved hiring must not dispatch again")

    import hashlib
    import json
    key = hashlib.sha256(json.dumps([proposal()], sort_keys=True).encode()).hexdigest()
    hub.requests["r"]["plan_recruitment"] = {key: {"check": {"status": "ready"},
        "approval": {"approved": True}, "results": {}}}
    from labhq.recruit.planning import paper_key
    batch = hub.requests["r"]["plan_recruitment"][key]
    batch["results"] = {paper_key(proposal()["paper"]): {"status": "active", "agent_id": "c_paper"}}
    if contract_state == "assigned_expired":
        batch["fallback_agents"] = {"s1": "worker"}
        hub.requests["r"]["plan"]["steps"][0]["agent_id"] = "c_paper"
    hub.requests["r"]["plan_recruitment"]["old-batch"] = {
        "results": {"old-paper": {"status": "active", "agent_id": "c_old"}}}
    hub.save_request("r")
    hub.store.close()
    restored = Hub(settings)
    initial_approvals = set(restored.approvals)
    resumed = None
    called = []

    async def run_request(rid, resume=False):
        called.append(rid)
        req = restored.requests[rid]
        req["plan"] = await prepare_plan(req["plan"], rid, restored, forbidden)

    restored.orchestrator.run_request = run_request
    restored.runners["core"] = object()
    for aid in ("worker", "cso", "sci_reviewer", "chief_of_staff", "precedent"):
        restored.agent_runner[aid] = "core"
    try:
        resumed = asyncio.create_task(restored.resume_when_ready("r"))
        await asyncio.sleep(0)
        if contract_state == "active":
            assert not called
            assert restored.requests["r"]["status"] == "waiting_for_runner"
        if reconnect and contract_state == "active":
            restored.runners["hire"] = object()
            restored.agent_runner["c_paper"] = "hire"
            restored.agents["c_paper"] = {"id": "c_paper", "employment": "contract", "contract_status": "active"}
        await asyncio.wait_for(resumed, 2)
        req = restored.requests["r"]
        if contract_state != "active":
            assert called == ["r"]
            assert req["plan"]["steps"][0]["agent_id"] == "worker"
            assert "재배정" in req["plan"]["warnings"][0]
            assert req["plan"]["recruit_results"][0]["status"] == "failed"
            assert restored.resume_agents("r").isdisjoint({"c_paper", "c_old"})
            assert set(restored.approvals) == initial_approvals  # no second resume card
            assert await prepare_plan(req["plan"], "r", restored, forbidden) == req["plan"]
        elif reconnect:
            assert called == ["r"]
            assert req["plan"]["steps"][0]["agent_id"] == "c_paper"
            assert not req["plan"].get("warnings")
        else:
            assert not called and req["status"] == "interrupted"
            assert req["plan_recruitment"][key]["results"][paper_key(proposal()["paper"])]["status"] == "active"
    finally:
        if resumed:
            resumed.cancel()
            await asyncio.gather(resumed, return_exceptions=True)
        for timer in restored.resume_timers.values():
            timer.cancel()
        restored.store.close()


@pytest.mark.parametrize("status,expires,expected", [
    ("active", 1, "failed"), ("archived", 10**12, "failed"), ("active", 10**12, "active")])
def test_remote_receipt_uses_stored_roster_contract(tmp_path, status, expires, expected):
    from types import SimpleNamespace
    from labhq.recruit.planning import _refresh_receipt

    settings = Settings()
    settings.runner.agents_dir = str(tmp_path / "absent-agents")
    settings.runner.talent_dir = str(tmp_path / "absent-talent")
    hub = SimpleNamespace(s=settings, agents={}, last_runner_rosters={"remote": {"agents": [
        {"id": "c_paper", "contract_status": status, "expires_at": expires}]}})
    receipt = {"status": "active", "agent_id": "c_paper"}
    _refresh_receipt(receipt, hub)
    assert receipt["status"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("mode", ["team", "plan_only"])
async def test_scope_decision_precedes_recruitment(resume, approved, mode):
    settings = Settings()
    settings.research.enabled = False
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    settings.orchestrator.precedent_agent = None
    plan = {"steps": [{"id": "s1", "agent_id": "worker", "instruction": "reproduce", "depends_on": []}],
            "recruit": [proposal()], "scope": {"verdict": "out", "reason": "outside the lab scope"}}
    order = []

    async def reply(task):
        kind = task.meta["kind"]
        order.append(kind)
        data = plan if kind == "plan" else {"status": "ready" if kind == "recruit_check" else "failed"}
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=data, text="done", cost_usd=0)

    hub = MiniHub(settings, reply, mode=mode, work_kind="simple")
    hub.agents["recruiter"] = {"id": "recruiter", "name": "Recruiter", "role": "hire", "engine": "mock"}
    if resume:
        hub.requests["r"].update(plan=copy.deepcopy(plan), scope_check=copy.deepcopy(plan["scope"]))

    async def approve(**card):
        order.append(card["kind"])
        return {"approved": approved if card["kind"] == "scope" else True}

    hub.request_approval = approve
    await Orchestrator(hub).run_request("r", resume=resume)
    assert "scope" in order
    if approved:
        assert order.index("scope") < order.index("recruit_check") < order.index("plan_recruit")
        assert order.count("scope") == 1
    else:
        assert not {"recruit_check", "recruit_plan", "plan_recruit", "step"}.intersection(order)
        assert "plan_recruitment" not in hub.requests["r"]
        assert hub.requests["r"]["outcome"] == "out_of_scope_declined"
