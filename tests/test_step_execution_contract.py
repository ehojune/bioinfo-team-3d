"""Versioned CP1 placement tests with in-memory workers and a fake HPC runner."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError

from labhq.models import TaskResult
from labhq.orchestrator.execution import plan_errors
from labhq.orchestrator.cso import ENV_LOCK_OUTPUT, Orchestrator
from labhq.orchestrator.step_execution import refresh_for_approval
from labhq.recruit import paper_skills, planning as recruit_planning
from labhq.research import contract, continuation
from labhq.settings import Settings
from tests.test_cso import FakeHub, result
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan


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
    hub.agents[expected_agent]["runner_id"] = "replacement-runner"
    assert row["plan"]["steps"][0]["runner_id"] == expected_runner
    await Orchestrator(hub).run_request("r", resume=True)
    old, new = [approval["detail"] for approval in hub.approvals if approval["kind"] == "research_plan"]
    assert new["target_sha256"] != old["target_sha256"]
    assert new["단계 실행 위치 · runner"] == "s1 · local · replacement-runner"
    assert [task.meta["kind"] for task in hub.calls] == ["plan"]


@pytest.mark.parametrize("drift_point", ["cp1", "blind_draft"])
async def test_cp1_reopens_when_a_runner_changes_before_dispatch(drift_point):
    settings = Settings()
    settings.research.enabled = True
    settings.research.evidence_checkpoint = drift_point == "blind_draft"
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def dispatch(task):
        assert task.meta["kind"] == "plan"
        plan = valid_plan()
        plan["step_execution_version"] = 1
        return result(task, structured=plan)

    class MovingRunnerHub(MiniHub):
        async def request_approval(self, **kwargs):
            receipt = await super().request_approval(**kwargs)
            if kwargs["kind"] == "research_plan":
                offered = kwargs["detail"]["단계 실행 위치 · runner"]
                if offered == "s1 · local · first-runner":
                    if drift_point == "cp1":
                        self.agents["worker"]["runner_id"] = "second-runner"
                    return {**receipt, "approval_id": "old-cp1"}
            return receipt

    dispatched = []

    class RecordingOrchestrator(Orchestrator):
        async def _blind_draft(self, rid, req, text, refs):
            if drift_point == "blind_draft":
                self.hub.agents["worker"]["runner_id"] = "second-runner"
            return True

        async def _run_dag(self, rid, text, steps, results, only, feedback):
            dispatched.extend(step["runner_id"] for step in steps)
            assert len(self.hub.approvals) == 2

        async def _research_after_steps(self, *args):
            pass

    hub = MovingRunnerHub(settings, dispatch, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["worker"]["runner_id"] = "first-runner"
    hub.requests["r"]["execution_location"] = {"mode": "local", "source": "PI selection", "approval_id": "route-choice"}
    await RecordingOrchestrator(hub).run_request("r")
    request = hub.requests["r"]
    assert request.get("outcome") == ("research_running" if settings.research.evidence_checkpoint else "plan_approved")
    first, second = [card["detail"] for card in hub.approvals if card["kind"] == "research_plan"]
    assert first["target_sha256"] != second["target_sha256"]
    assert second["단계 실행 위치 · runner"] == "s1 · local · second-runner"
    approval = request["research_contract"]["approval"]
    assert approval["target_sha256"] == second["target_sha256"] == contract.plan_sha256(request["plan"])
    assert approval["approval_id"] != "old-cp1"
    assert dispatched == (["second-runner"] if settings.research.evidence_checkpoint else [])


@pytest.mark.parametrize("drift_point", ["paper_skill", "budget_reservation"])
async def test_runner_change_during_step_preparation_reopens_cp1_before_any_send(monkeypatch, drift_point):
    settings = Settings()
    settings.research.enabled = settings.research.evidence_checkpoint = True
    settings.orchestrator.chief_of_staff_agent = settings.orchestrator.reviewer_agent = None
    sends = []
    prepared = []
    closed = []

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            plan = valid_plan(steps=2)
            plan["step_execution_version"] = 1
            return result(task, structured=plan)
        assert task.meta["kind"] == "step"
        sends.append((task.meta["step_id"], task.meta["session_runner"], len(hub.approvals)))
        return result(task, ok=False, error="end the mock step")

    class PreparingOrchestrator(Orchestrator):
        async def _start_paper_builds(self, rid, rows, execution_slots):
            prepared.append(rows)
            return object()

        async def _close_paper_builds(self, rid, batch):
            closed.append(batch)

        async def _resolve_step_paper_skills(self, task):
            assert self.paper_builds[task.request_id]
            await asyncio.sleep(0)
            self.hub.agents["worker"]["runner_id"] = "second-runner"
            return task.model_copy(update={"meta": {**task.meta, "paper_skills": []}})

        async def _reserve_dispatch(self, rid, task, *, optional):
            if drift_point == "budget_reservation" and task.meta["kind"] == "step":
                await asyncio.sleep(0)
                self.hub.agents["worker"]["runner_id"] = "second-runner"
            return await super()._reserve_dispatch(rid, task, optional=optional)

        async def _research_after_steps(self, *args):
            pass

    if drift_point == "paper_skill":
        monkeypatch.setattr(paper_skills, "attachments", lambda *args: [{"status": "deferred"}])
    hub = MiniHub(settings, dispatch, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["worker"]["runner_id"] = "first-runner"
    request = hub.requests["r"]
    request["execution_location"] = {"mode": "local", "source": "PI selection", "approval_id": "route-choice"}
    orchestrator = PreparingOrchestrator(hub)
    await orchestrator.run_request("r")

    first, second = [card["detail"] for card in hub.approvals if card["kind"] == "research_plan"]
    assert first["target_sha256"] != second["target_sha256"]
    assert request["research_contract"]["approval"]["target_sha256"] == second["target_sha256"]
    assert sorted(sends) == [("s1", "second-runner", 2), ("s2", "second-runner", 2)]
    assert orchestrator.attempts["r"]["s1"] == orchestrator.attempts["r"]["s2"] == 1
    assert not orchestrator.paper_builds and not orchestrator.first_step_dispatch
    assert len(prepared) == len(closed) == (2 if drift_point == "paper_skill" else 0)


@pytest.mark.parametrize("record", ["results", "reuse_tools", "pending_revisions"])
def test_runner_refresh_preserves_the_plan_for_existing_execution_records(record):
    plan = placed_plan()
    request = {"plan": plan, record: {"s1": {}}}
    choice = {"mode": "local"}
    agents = {"worker": {"runner_id": "replacement-runner"}}
    with pytest.raises(ValueError, match="step runner changed after execution began"):
        refresh_for_approval(plan, request, choice, agents)
    assert request["plan"]["steps"][0]["runner_id"] == "mock-runner"


@pytest.mark.parametrize("continued", [False, True])
async def test_late_runner_change_on_resume_requires_replan_and_preserves_results(continued):
    settings = Settings()
    settings.research.enabled = settings.research.evidence_checkpoint = True
    settings.orchestrator.chief_of_staff_agent = settings.orchestrator.reviewer_agent = None
    plan = placed_plan(count=2)
    receipt = approved(plan)
    prior = TaskResult(task_id="old-s1", agent_id="worker", ok=True, text="prior evidence")
    observed_stops = []

    async def dispatch(task):
        pytest.fail(f"step dispatched under the old approval: {task.meta['kind']}")

    class ResumeHub(MiniHub):
        def result_map(self, rid):
            return {"s1": prior}

    class MovingRunnerOrchestrator(Orchestrator):
        async def _verify_research_reuse(self, rid, plan):
            return True

        async def _reserve_dispatch(self, rid, task, *, optional):
            if task.meta["kind"] == "step":
                await asyncio.sleep(0)
                self.hub.agents["worker"]["runner_id"] = "replacement-runner"
            return await super()._reserve_dispatch(rid, task, optional=optional)

        async def _research_continue(self, rid, review, results, *, stops: list):
            assert results["s1"] == prior
            assert results["s2"].error_kind == "research_stop"
            observed_stops.extend(stops)
            return "fixture declined the replan"

    hub = ResumeHub(settings, dispatch, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["worker"]["runner_id"] = "mock-runner"
    request = hub.requests["r"]
    request.update(plan=plan, results={"s1": prior.model_dump(mode="json")},
                   execution_location={"mode": "local", "source": "PI selection", "approval_id": "route-choice"},
                   research_contract={**receipt, "execution_enabled": True})
    if continued:
        request["research_contract"]["continuation"] = {"plan_sha256": receipt["plan_sha256"]}
    await MovingRunnerOrchestrator(hub).run_request("r", resume=True)
    assert not hub.approvals
    assert [sid for sid, reason in observed_stops if "revise the plan and obtain a new CP1" in reason] == ["s2"]
    assert request["results"]["s1"]["task_id"] == prior.task_id
    assert request["research_contract"]["approval"]["target_sha256"] == receipt["plan_sha256"]
    assert contract.plan_sha256(request["plan"]) == receipt["plan_sha256"]


@pytest.mark.parametrize("auxiliary", ["result_correction", "slot_repair"])
async def test_auxiliary_turn_replans_on_runner_change_and_keeps_original_outputs(auxiliary):
    settings = Settings()
    settings.research.enabled = settings.research.evidence_checkpoint = True
    settings.orchestrator.chief_of_staff_agent = settings.orchestrator.reviewer_agent = None
    attempted = []
    observed_stops = []
    path = "outputs/result1.tsv"
    original_hash = "a" * 64

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            plan = valid_plan()
            plan["step_execution_version"] = 1
            return result(task, structured=plan)
        assert task.meta["kind"] == "step"
        ledger = _cp2_result(hub, task)
        if auxiliary == "slot_repair":
            ledger["evidence"][0]["slots"] = []
        else:
            del ledger["evidence"][0]["assessment_reason"]
        hub.agents["worker"]["runner_id"] = "replacement-runner"
        return result(task, structured=ledger, outputs=[path], output_sha256={path: original_hash},
                      session_id="original-session")

    class ResumableHub(MiniHub):
        def supports_resume(self, agent_id):
            return True

    class RecordingOrchestrator(Orchestrator):
        async def _reserve_dispatch(self, rid, task, *, optional):
            attempted.append(task.meta["kind"])
            return await super()._reserve_dispatch(rid, task, optional=optional)

        async def _research_continue(self, rid, review, results, *, stops: list):
            assert results["s1"].output_sha256 == {path: original_hash}
            assert results["s1"].session_id == "original-session"
            observed_stops.extend(stops)
            return "fixture declined the replan"

    hub = ResumableHub(settings, dispatch, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["worker"]["runner_id"] = "first-runner"
    request = hub.requests["r"]
    request["execution_location"] = {"mode": "local", "source": "PI selection", "approval_id": "route-choice"}
    await RecordingOrchestrator(hub).run_request("r")
    assert attempted == ["plan", "step", auxiliary]
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step"]
    assert request["results"]["s1"]["task_id"] == hub.calls[-1].id
    assert request["results"]["s1"]["error_kind"] == "research_stop"
    assert [sid for sid, reason in observed_stops if "revise the plan and obtain a new CP1" in reason] == ["s1"]
    [approval] = hub.approvals
    assert approval["detail"]["target_sha256"] == contract.plan_sha256(request["plan"])


@pytest.mark.parametrize("resume", [False, True])
async def test_cp1_binds_environment_clones_to_their_final_runners(resume):
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    plan = placed_plan(count=3)
    env, first, second = plan["steps"]
    env["outputs"] = [ENV_LOCK_OUTPUT]
    first["depends_on"] = second["depends_on"] = [env["id"]]
    second.update(agent_id="other-worker", runner_id="other-runner")

    async def dispatch(task):
        assert task.meta["kind"] == "plan"
        planner_draft = copy.deepcopy(plan)
        for step in planner_draft["steps"]:
            step.pop("runner_id")
        return result(task, structured=planner_draft)

    hub = MiniHub(settings, dispatch, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["worker"].update(engine="codex", sandbox="workspace-write", runner_id="mock-runner")
    hub.agents["other-worker"] = {**hub.agents["worker"], "id": "other-worker", "runner_id": "other-runner"}
    hub.agent_runner = {"worker": "mock-runner", "other-worker": "other-runner"}
    hub.runners = {runner: object() for runner in hub.agent_runner.values()}
    hub.runner_capabilities = {runner: {"runtime_env": "windows"} for runner in hub.runners}
    request = hub.requests["r"]
    request["execution_location"] = {"mode": "local", "source": "PI selection", "approval_id": "route-choice"}
    if resume:
        request["plan"] = plan
    await Orchestrator(hub).run_request("r", resume=resume)
    assert request.get("outcome") == "plan_approved", request.get("report")
    [card] = [card["detail"] for card in hub.approvals if card["kind"] == "research_plan"]
    frozen = json.loads(card["plan_canonical"])
    clones = [step for step in frozen["steps"] if step.get("environment_binding")]
    assert len(clones) == 2
    assert {step["agent_id"]: step["runner_id"] for step in clones} == hub.agent_runner
    assert all(step["runner_id"] == step["environment_binding"]["runner_id"] for step in clones)
    assert card["target_sha256"] == contract.plan_sha256(request["plan"])


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


async def test_pc_choice_offers_a_mixed_plan_and_cp1_binds_both_locations():
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def dispatch(task):
        assert task.meta['kind'] == 'plan'
        assert 'PC as the default' in task.prompt
        assert 'including in mixed plans' in task.output_schema['properties']['step_execution_version']['description']
        plan = valid_plan(steps=2)
        plan['step_execution_version'] = 1
        plan['steps'][0]['execution'] = 'hpc'
        plan['steps'][1]['depends_on'] = ['s1']
        return result(task, structured=plan)

    class ChoosingHub(MiniHub):
        async def request_approval(self, **kwargs):
            receipt = await super().request_approval(**kwargs)
            return {**receipt, 'note': '1: b'} if kwargs['kind'] == 'clarify' else receipt

    hub = ChoosingHub(settings, dispatch, mode='plan_only', work_kind='research', text='compare conditions')
    hub.agents['worker'].update(runner_id='mock-runner', whole_request_hpc=True, hpc_tools=True,
                               builtin_mcp=['hpc'])
    hub.runners = {'mock-runner': object()}
    hub.runner_capabilities = {'mock-runner': {'scheduler': 'mock'}}
    await Orchestrator(hub).run_request('r')
    row = hub.requests['r']
    assert row['outcome'] == 'plan_approved', row.get('report')
    assert row['execution_location']['mixed_hpc_runner_id'] == 'mock-runner'
    [card] = [card for card in hub.approvals if card['kind'] == 'research_plan']
    assert '단계별 HPC·PC' in card['summary']
    assert card['detail']['단계 실행 위치 · runner'] == 's1 · hpc · mock-runner\ns2 · local · mock-runner'
    assert card['detail']['target_sha256'] == contract.plan_sha256(row['plan'])
    assert [task.meta['kind'] for task in hub.calls] == ['plan']


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


@pytest.mark.parametrize(('change', 'reason'), [
    ('unversioned', 'mixed plans are refused'),
    ('unselected', 'mixed plans are refused'),
    ('pc_to_hpc', 'PC-to-HPC dependency'),
    ('different_runner', 'HPC-to-PC dependency on s1 requires the same runner'),
    ('hpc_not_eligible', 'requires whole-request HPC eligibility'),
    ('hpc_environment', 'HPC environment bindings'),
])
def test_mixed_placement_refuses_unapproved_or_unverifiable_routes(change, reason):
    plan = placed_plan(count=3)
    first, second, third = plan['steps']
    first['execution'] = 'hpc'
    second['depends_on'] = ['s1']
    third['depends_on'] = ['s2']
    choice = {'mode': 'local', 'approval_id': 'choice', 'mixed_hpc_runner_id': 'mock-runner'}
    agents = {'worker': {'runner_id': 'mock-runner', 'whole_request_hpc': True, 'hpc_tools': True,
                         'builtin_mcp': ['hpc']}, 'other': {'runner_id': 'other-runner'}}
    assert plan_errors(plan['steps'], choice, agents, version=1) == []
    version = 1
    if change == 'unversioned':
        version = None
    elif change == 'unselected':
        choice.pop('approval_id')
    elif change == 'pc_to_hpc':
        second['execution'], first['execution'] = 'hpc', 'local'
    elif change == 'different_runner':
        third.update(agent_id='other', runner_id='other-runner')
    elif change == 'hpc_not_eligible':
        agents['worker']['whole_request_hpc'] = False
    elif change == 'hpc_environment':
        first['outputs'] = [ENV_LOCK_OUTPUT]
    assert any(reason in problem for problem in plan_errors(plan['steps'], choice, agents, version=version))


@pytest.mark.parametrize('changed', ['none', 'pc_file', 'citation'])
async def test_mixed_continuation_checks_each_steps_input_scope(tmp_path, monkeypatch, changed):
    from labhq.research.input_proof import capture
    from tests.test_continuation_797 import archive

    source = tmp_path / 'reference'
    source.mkdir()
    table = source / 'table.tsv'
    table.write_text('original', encoding='utf-8')
    hub = FakeHub(None)
    hub.s.runner.reference_roots = [str(source)]
    hub.agents['worker'].update(runner_id='mock-runner', whole_request_hpc=True, hpc_tools=True, builtin_mcp=['hpc'])
    orchestrator = Orchestrator(hub)
    old = archive()
    plan = old['plan']
    plan['step_execution_version'] = 1
    for step in plan['steps']:
        step.update(runner_id='mock-runner', execution='hpc' if step['id'] == 's1' else 'local')
    sha = old['plan_sha256'] = contract.plan_sha256(plan)
    for row in old['results'].values():
        row['structured']['plan_sha256'] = sha
    old['reuse_tools'] = orchestrator._research_reuse_tools('r', plan)
    old['reuse_no_request_inputs'] = {step['id']: False for step in plan['steps']}
    req = hub.requests['r']
    req.update(plan=copy.deepcopy(plan), references=[{'kind': 'path', 'value': str(source)}],
               execution_location={'mode': 'local', 'approval_id': 'choice', 'mixed_hpc_runner_id': 'mock-runner'},
               research_contract={'round': 2, 'plan_sha256': sha, 'pack_snapshot': {}, 'rounds': [old],
                                  'continuation': {'plan_sha256': sha, 'round': 2}})
    pc_proof = capture(req, hub.s, allow_request_projects=True)
    hpc_proof = capture({}, hub.s, allow_request_projects=True)
    assert pc_proof and hpc_proof and pc_proof != hpc_proof
    old['reuse_input_proof'] = {'s1': hpc_proof, 's2': pc_proof, 's3': pc_proof}
    monkeypatch.setattr(continuation, 'verify_reuse', lambda *args: {})
    if changed == 'pc_file':
        table.write_text('changed PC-only input', encoding='utf-8')
    elif changed == 'citation':
        req['references'].append({'kind': 'pmid', 'value': '12345'})
    carry, _ = await orchestrator._continuation_candidates('r', req['plan'])
    expected = {'none': ['s1', 's2', 's3'], 'pc_file': ['s1'], 'citation': []}
    assert carry.reuse == expected[changed], carry.rerun
