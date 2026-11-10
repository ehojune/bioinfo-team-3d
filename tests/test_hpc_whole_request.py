"""#430: request-wide compute choice; no live scheduler, CLI or cluster."""
import json
import asyncio
import os
import stat
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator, validate_steps
from labhq.request_bundle import build_request_bundle
from labhq.runner.daemon import Runner
from labhq.evidence.audit import verify_request
from tests.test_cso import FakeHub, result
from tests.test_workspace_boundary import _settings
from tests.test_hpc_transfer_1308 import cluster  # noqa: F401 - fixture for the remote-scan test

pytestmark = pytest.mark.usefixtures("pre_v1_defaults")  # these tests hire or report; v1.0 turns both off (#1336)


def hub_for(dispatch, scheduler="mock", online=True, tools=True):
    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    hub.agents["worker"].update(runner_id="cluster", hpc_tools=tools, whole_request_hpc=tools,
                                builtin_mcp=["hpc"] if tools else [])
    hub.agent_runner = {"worker": "cluster"}
    hub.runner_capabilities = {"cluster": {"scheduler": scheduler}}
    hub.runners = {"cluster": object()} if online else {}
    return hub


@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("mode,recruit_state", [
    ("hpc", "eligible"), ("hpc", "no_whole_request"), ("hpc", "no_tools"),
    ("hpc", "no_builtin"), ("hpc", "other_runner"), ("hpc", "denied"),
    ("local", "no_tools"),
])
async def test_recruited_assignments_checked_before_cp1_and_processing(research, resume, mode, recruit_state):
    from labhq.orchestrator.execution import plan_errors
    from labhq.settings import Settings
    from tests.test_research_protocol import MiniHub, valid_plan

    settings = Settings()
    settings.research.enabled = research
    settings.research.evidence_checkpoint = False
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    plan = valid_plan()
    plan["steps"][0]["execution"] = mode
    plan["recruit"] = [{"paper": "PMID:12345678", "repo": "", "focus": "reproduce method",
                        "reason": "synthetic recruitment", "step_ids": ["s1"]}]
    if not research:
        plan = {key: plan[key] for key in ("steps", "recruit", "notes", "clarifying_questions")}
        plan["steps"][0]["outputs"] = []
    eligible = {"runner_id": "cluster", "hpc_tools": True, "whole_request_hpc": True,
                "builtin_mcp": ["hpc"]}
    recruit = {"id": "c_paper", "name": "Paper", "role": "method", "engine": "mock",
               "employment": "contract", "contract_status": "active", **eligible}
    if recruit_state == "no_whole_request":
        recruit["whole_request_hpc"] = False
    elif recruit_state == "no_tools":
        recruit["hpc_tools"] = False
    elif recruit_state == "no_builtin":
        recruit["builtin_mcp"] = []
    elif recruit_state == "other_runner":
        recruit["runner_id"] = "other"

    async def reply(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return result(task, structured=plan)
        if kind == "recruit_check":
            return result(task, structured={"status": "ready"})
        if kind == "plan_recruit":
            hub.agents["c_paper"] = recruit
            return result(task, structured={"status": "active", "agent_id": "c_paper"})
        return result(task, text="done")

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research" if research else "simple",
                  text="Reproduce the synthetic method")
    hub.agents["worker"].update(eligible)
    hub.agents["recruiter"] = {"id": "recruiter", "name": "Recruiter", "role": "recruiter", "engine": "mock"}
    hub.runners = {"cluster": object()}
    hub.runner_capabilities = {"cluster": {"scheduler": "mock"}}
    choice = {"mode": mode, "runner_id": "cluster", "workspace": "r", "source": "PI choice"}
    hub.requests["r"]["execution_location"] = choice
    assert plan_errors(plan["steps"], choice, hub.agents) == []  # the draft fallback is eligible
    if resume:
        hub.requests["r"]["plan"] = plan

    async def approve(**card):
        hub.approvals.append(card)
        return {"approved": recruit_state != "denied" or card["kind"] != "recruit_plan",
                "approval_id": "synthetic", "decided_at": 1.0}

    hub.request_approval = approve
    await Orchestrator(hub).run_request("r", resume=resume)
    req = hub.requests["r"]
    blocked = mode == "hpc" and recruit_state not in {"eligible", "denied"}
    if blocked:
        assert req["status"] == "failed"
        assert "after recruitment" in req["report"] and "whole-request HPC eligibility" in req["report"]
        assert all(card["kind"] != "research_plan" for card in hub.approvals)
        assert all(task.meta["kind"] not in {"step", "direct"} for task in hub.calls)
        assert not req.get("research_contract")
    else:
        assert req["status"] == "done", req.get("report")
        assigned = "worker" if recruit_state == "denied" else "c_paper"
        assert req["plan"]["steps"][0]["agent_id"] == assigned
        if research:
            cp1 = next(card for card in hub.approvals if card["kind"] == "research_plan")
            assert assigned in cp1["detail"]["plan_canonical"]
        else:
            assert any(task.meta["kind"] == "step" and task.agent_id == assigned for task in hub.calls)
    assert any(card["kind"] == "recruit_plan" for card in hub.approvals)
    assert any(task.meta["kind"] == "plan_recruit" for task in hub.calls) == (recruit_state != "denied")


@pytest.mark.parametrize('mode', ['hpc', 'local'])
@pytest.mark.parametrize('stored', [False, True])
async def test_precedent_receipt_reaches_planning_and_cp1_in_either_location(mode, stored):
    from labhq.settings import Settings
    from tests.test_research_protocol import MiniHub, valid_plan

    chosen = asyncio.Event()
    settings = Settings()
    settings.research.enabled = True
    settings.research.evidence_checkpoint = False
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    settings.orchestrator.precedent_agent = 'lit_scout'
    receipt = {'coverage_version': 2, 'paper_id': 'synthetic', 'source': 'mock',
               'access_level': 'full_text', 'extraction': 'complete',
               'text_file': '/stored/paper.md', 'text_sha256': 'a' * 64,
               'evidence': [], 'supplements_required': False, 'supplements': 'not applicable'}
    precedents = {'status': 'ok', 'papers': [{'title': 'synthetic', 'citations': ['synthetic'],
                   'fulltext_receipt': receipt}], 'required': [], 'recommended': [], 'limitations': []}

    async def dispatch(task):
        if task.meta['kind'] == 'precedent':
            await chosen.wait()  # the scout returns a new receipt after the PI has chosen
            return result(task, structured={key: value for key, value in precedents.items() if key != 'status'})
        if task.meta['kind'] == 'plan':
            plan = valid_plan()
            for step in plan['steps']:
                step['execution'] = mode
            return result(task, structured=plan)
        return result(task, text='done')

    hub = MiniHub(settings, dispatch, mode='orchestrate', work_kind='research', text='compare conditions')
    hub.agents['lit_scout'] = {'id': 'lit_scout', 'name': 'scout', 'engine': 'mock', 'role': 'test'}
    hub.agents['worker'].update(runner_id='cluster', hpc_tools=True, whole_request_hpc=True, builtin_mcp=['hpc'])
    hub.runners = {'cluster': object()}
    hub.runner_capabilities = {'cluster': {'scheduler': 'mock'}}
    if stored:
        hub.requests['r']['analysis_precedents'] = precedents

    async def answer(**card):
        hub.approvals.append(card)
        chosen.set()
        return {'approved': True, 'note': '1: a' if mode == 'hpc' else '1: b'}

    hub.request_approval = answer
    await Orchestrator(hub).run_request('r')
    req = hub.requests['r']
    assert req['analysis_precedents']['papers'][0]['fulltext_receipt'] == receipt
    assert req['execution_location']['mode'] == mode
    # Stored precedent texts no longer refuse HPC (#1336): only the HPC steps go without them.
    assert req['status'] == 'done', req.get('error')
    assert [card['kind'] for card in hub.approvals] == ['clarify', 'research_plan']
    assert any(task.meta['kind'] == 'plan' for task in hub.calls)
    if mode == 'hpc':
        assert all('precedent_texts' not in task.meta for task in hub.calls if task.meta['kind'] == 'step')


@pytest.mark.parametrize("mode", ["hpc", "local"])
async def test_choice_after_briefing_before_plan_and_recorded(mode):
    from labhq.orchestrator.execution import OPTIONS, QUESTION
    seen = []

    async def dispatch(task):
        seen.append(task.meta["kind"])
        if task.meta["kind"] == "plan":
            assert seen.index("briefing") < seen.index("choice") < seen.index("plan")
            return result(task, structured={"steps": [
                {"id": "s1", "agent_id": "worker", "instruction": "prepare", "depends_on": [], "execution": mode},
                {"id": "s2", "agent_id": "worker", "instruction": "analyze", "depends_on": ["s1"], "execution": mode},
            ]})
        if task.meta["kind"] == "step":
            assert task.meta["execution_location"]["mode"] == mode
            if mode == "hpc":
                assert task.meta["execution_location"]["workspace"] == "r"
                assert task.meta["upstream_steps"] == {}  # this synthetic plan produces no files
                assert "upstream_artifacts" in task.meta
                assert "hpc_submit" in task.prompt
        return result(task, text="done")

    hub = hub_for(dispatch)
    hub.s.orchestrator.chief_of_staff_agent = "cso"

    async def answer(**card):
        hub.approvals.append(card)
        assert card["summary"] == QUESTION
        assert card["detail"]["questions"][0]["options"] == OPTIONS
        seen.append("choice")
        return {"approved": True, "note": OPTIONS[0 if mode == "hpc" else 1]}

    hub.request_approval = answer
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("error")
    assert req["execution_location"]["mode"] == mode
    assert "실행 위치:" in req["execution_record"]
    assert len(hub.approvals) == 1
    assert all(s["execution"] == mode for s in req["plan"]["steps"])


@pytest.mark.parametrize("scheduler,online,tools", [("none", True, True), ("mock", False, True), ("mock", True, False)])
async def test_unavailable_hpc_defaults_local_without_card(scheduler, online, tools):
    hub = hub_for(lambda task: None, scheduler, online, tools)
    assert await Orchestrator(hub)._choose_execution("r")
    assert hub.requests["r"]["execution_location"]["mode"] == "local"
    assert not hub.approvals


async def test_direct_request_uses_only_target_agents_hpc_capability():
    hub = hub_for(lambda task: None)
    hub.requests["r"].update(mode="direct", agent_id="cso")

    assert await Orchestrator(hub)._choose_execution("r")
    assert hub.requests["r"]["execution_location"]["mode"] == "local"
    assert not hub.approvals


@pytest.mark.parametrize("note,approved", [("", True), ("HPC maybe", True), ("", False)])
async def test_no_explicit_choice_stops_before_dispatch(note, approved):
    hub = hub_for(lambda task: None)

    async def answer(**card):
        return {"approved": approved, "note": note}

    hub.request_approval = answer
    assert not await Orchestrator(hub)._choose_execution("r")
    assert not hub.calls
    assert "execution_location" not in hub.requests["r"]


@pytest.mark.parametrize("mode,bad", [("hpc", "local"), ("local", "hpc")])
async def test_mixed_plan_rejected_before_any_processing(mode, bad):
    steps = [{"id": "s1", "agent_id": "worker", "instruction": "first", "depends_on": [], "execution": mode},
             {"id": "s2", "agent_id": "worker", "instruction": "next", "depends_on": ["s1"], "execution": bad}]
    hub = hub_for(lambda task: None)
    hub.requests["r"]["execution_location"] = {"mode": mode, "runner_id": "cluster", "workspace": "r"}
    with pytest.raises(ValueError, match="mixed plans"):
        await Orchestrator(hub).run_dag("r", "test", steps, {})
    assert not hub.calls


async def test_saved_choice_is_reused_without_another_card():
    hub = hub_for(lambda task: None)
    saved = {"mode": "hpc", "runner_id": "cluster", "workspace": "r", "source": "PI 선택"}
    hub.requests["r"]["execution_location"] = saved.copy()
    assert await Orchestrator(hub)._choose_execution("r")
    assert hub.requests["r"]["execution_location"] == saved and not hub.approvals


def test_hpc_request_keeps_dag_instead_of_solo_shortcut():
    hub = hub_for(lambda task: None)
    req = hub.requests["r"]
    req.update(execution_location={"mode": "hpc"}, route="solo",
               route_decision={"mode": "solo", "agent_id": "worker"})
    decision, changed = Orchestrator(hub)._ensure_route_decision(req, list(hub.agents.values()), research=False)
    assert decision["mode"] == "team" and changed


@pytest.mark.parametrize("mode", ["hpc", "local"])
async def test_research_cp1_records_execution_choice(mode):
    from labhq.orchestrator.execution import OPTIONS
    from labhq.settings import Settings
    from tests.test_research_protocol import MiniHub, valid_plan

    settings = Settings()
    settings.research.enabled = True
    settings.research.evidence_checkpoint = False
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def reply(task):
        plan = valid_plan()
        for step in plan["steps"]:
            step["execution"] = mode
        return result(task, structured=plan)

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["worker"].update(runner_id="cluster", hpc_tools=True, whole_request_hpc=True, builtin_mcp=["hpc"])
    hub.runners = {"cluster": object()}
    hub.runner_capabilities = {"cluster": {"scheduler": "mock"}}

    async def answer(**card):
        hub.approvals.append(card)
        return {"approved": True, "note": OPTIONS[0 if mode == "hpc" else 1],
                "approval_id": "choice-id", "decided_at": 1.0}

    hub.request_approval = answer
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("error")
    assert [card["kind"] for card in hub.approvals] == ["clarify", "research_plan"]
    cp1 = hub.approvals[-1]
    assert cp1["detail"]["execution_location"] == req["execution_location"]
    assert req["execution_location"]["approval_id"] == "choice-id"
    assert "실행 위치:" in cp1["summary"] and "실행 위치:" in req["execution_record"]
    assert all(step["execution"] == mode for step in req["plan"]["steps"])


def test_hpc_plan_refuses_other_runner_but_isolates_step_output_names():
    from labhq.orchestrator.execution import plan_errors

    hub = hub_for(lambda task: None)
    choice = {"mode": "hpc", "runner_id": "cluster"}
    steps = [{"id": "s1", "agent_id": "worker", "execution": "hpc", "outputs": ["a.txt"]},
             {"id": "s2", "agent_id": "worker", "execution": "hpc", "outputs": ["outputs/a.txt"]}]
    assert plan_errors(steps, choice, hub.agents) == []
    steps[0]["outputs"] = ["outputs/a.txt", "outputs/a.txt/b.txt"]
    assert any("collision" in error for error in plan_errors(steps, choice, hub.agents))
    hub.agents["worker"]["runner_id"] = "other"
    assert any("selected runner" in error for error in plan_errors(steps[:1], choice, hub.agents))


async def test_isolated_workspace_mock_jobs_read_verified_request_data(tmp_path, monkeypatch):
    import labhq.tools.hpc_mcp as hpc
    from labhq.adapters.mock import MockAdapter

    settings = _settings(tmp_path)
    settings.runner.id, settings.hpc.scheduler = "cluster", "mock"
    settings.policy.approvals.hpc_core_hours_threshold = 100
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    monkeypatch.setattr(runner.registry, "get", lambda aid: agent)
    workdirs, submissions = [], []
    mock_submit = runner.scheduler.submit

    def submit(script, *args):
        job_id = mock_submit(script, *args)
        submissions.append(Path(script))
        output = Path(script).parent.parent / "outputs"
        if len(submissions) == 1:
            (output / "s1.txt").write_text("upstream", encoding="utf-8")
        else:
            assert not (output / "s1.txt").exists()
            (output / "s2.txt").write_text("upstream processed", encoding="utf-8")
        return job_id

    monkeypatch.setattr(runner.scheduler, "submit", submit)

    async def run(adapter, ctx):
        ctx.before_spawn()
        workdirs.append(ctx.workdir)
        monkeypatch.setattr(hpc, "S", settings)
        monkeypatch.setattr(hpc, "WORKDIR", ctx.workdir)
        monkeypatch.setattr(hpc, "TASK", ctx.task.id)
        monkeypatch.setattr(hpc, "AGENT", agent.id)
        monkeypatch.setenv("LABHQ_EXECUTION_MODE", ctx.env["LABHQ_EXECUTION_MODE"])

        async def broker(path, payload, timeout):
            assert path == "/jobs/submit"
            return await runner._on_submit(payload)

        monkeypatch.setattr(hpc, "_broker", broker)
        step = ctx.task.meta["step_id"]
        if step == "s2":
            data = Path(ctx.task.meta["hpc_data_inputs"]["s1"])
            assert (data / "s1.txt").read_text(encoding="utf-8") == "upstream"
            source = json.loads((data.parents[2] / ".labhq/hpc-inputs/s1" /
                                 f"{results[0].task_id}.json").read_text(encoding="utf-8"))[0]
            assert source["producer_step"] == "s1" and source["task_id"] == results[0].task_id
            assert source["sha256"] == results[0].output_sha256["outputs/s1.txt"]
            assert not (data / "manifest.json").exists()
        receipt = json.loads(await hpc.hpc_submit("echo first > s1.txt" if step == "s1" else
                                                "cat s1.txt > s2.txt", step))
        assert receipt["output_dir"] == str(ctx.workdir / "outputs")
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="submitted")

    monkeypatch.setattr(MockAdapter, "run", run)
    location = {"mode": "hpc", "runner_id": "cluster", "workspace": "r"}
    results = []
    for sid in ("s1", "s2"):
        upstream = {} if sid == "s1" else {"s1": {
            "task_id": results[0].task_id, "workdir": results[0].workdir,
            "outputs": results[0].outputs, "output_sha256": results[0].output_sha256}}
        results.append(await runner.run_task(Task(agent_id="worker", request_id="r", prompt="test",
            meta={"kind": "step", "step_id": sid, "execution_location": location,
                  "upstream_artifacts": upstream,
                  "upstream_steps": {key: row["workdir"] for key, row in upstream.items()},
                  "outputs": [f"outputs/{sid}.txt"]})))
    assert len(submissions) == 2 and len(set(workdirs)) == 2
    assert all(r.ok and r.pending_jobs for r in results)
    assert results[0].workdir != results[1].workdir
    workdir = Path(results[0].workdir)
    assert workdir == Path(settings.runner.workspace_root) / "hpc" / "r" / "steps" / "s1"
    assert (workdirs[1] / "inputs" / "s1" / "s1.txt").is_file()
    assert "outputs/s2.txt" in results[1].outputs

    request = {
        "id": "r", "text": "test", "mode": "team", "status": "done",
        "plan": {"steps": [
            {"id": "s1", "agent_id": "worker", "depends_on": [], "outputs": ["outputs/s1.txt"]},
            {"id": "s2", "agent_id": "worker", "depends_on": ["s1"], "outputs": ["outputs/s2.txt"]},
        ]},
        "results": {sid: result.model_dump(mode="json") for sid, result in zip(("s1", "s2"), results)},
    }
    manifest = (workdir / "manifest.json").read_bytes()
    job_records = sorted(path.relative_to(workdir) for path in (workdir / "jobs").rglob("*"))

    bundle = Path(build_request_bundle(request, settings)["path"])

    assert bundle == Path(settings.runner.workspace_root) / "requests" / "r"
    assert (workdir / "manifest.json").read_bytes() == manifest
    assert sorted(path.relative_to(workdir) for path in (workdir / "jobs").rglob("*")) == job_records
    assert (workdir / "outputs/s1.txt").read_text(encoding="utf-8") == "upstream"
    assert (Path(results[1].workdir) / "outputs/s2.txt").read_text(encoding="utf-8") == "upstream processed"
    assert verify_request(request, settings)["exit_code"] == 0


async def test_hpc_step_without_job_is_not_success(tmp_path, monkeypatch):
    from labhq.adapters.mock import MockAdapter

    async def no_job(adapter, ctx):
        return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="local result")

    monkeypatch.setattr(MockAdapter, "run", no_job)
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    settings.runner.mock_delay_s = 0
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    monkeypatch.setattr(runner.registry, "get", lambda aid: agent)
    result = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="no job",
        meta={"kind": "step", "step_id": "s1", "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}}))
    assert not result.ok and "잡 기록" in result.error


async def test_local_submission_refused_at_tool_and_broker(tmp_path, monkeypatch):
    import labhq.tools.hpc_mcp as hpc
    from types import SimpleNamespace
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)
    task = Task(agent_id="worker", prompt="test", meta={"kind": "step", "execution_location": {"mode": "local"}})
    runner.workspaces[task.id] = SimpleNamespace(task=task, dir=tmp_path)
    runner.active_roots[task.id] = (False, [tmp_path])
    out = await runner._on_submit({"task_id": task.id, "agent_id": task.agent_id})
    assert "local execution" in out["error"] and not runner.jobs
    monkeypatch.setenv("LABHQ_EXECUTION_MODE", "local")
    with pytest.raises(Exception, match="local execution"):
        await hpc.hpc_submit("echo no", "denied")
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    assert "labhq_hpc" not in [s.name for s in runner._mcp_servers(agent, {"LABHQ_EXECUTION_MODE": "local"})]


@pytest.mark.skipif(os.name == "nt", reason="POSIX shared-account permission modes; Windows covers mock IO above")
def test_shared_account_preserves_previous_job_outputs_and_private_control_files(tmp_path):
    import grp
    import pwd
    from labhq.tools.hpc_mcp import _prepare_job_files
    from labhq.tools.scheduler import build_script
    group, user = grp.getgrgid(os.getgid()).gr_name, pwd.getpwuid(os.getuid()).pw_name
    work = tmp_path / "request"
    work.mkdir(mode=0o700)
    (work / "TASK.md").write_text("private control", encoding="utf-8")
    for sid in ("s1", "s2"):
        _prepare_job_files(work, work / "jobs" / f"{sid}.sh", work / "jobs" / "logs",
                           build_script("echo test", str(work / "outputs"), umask="007"),
                           group, tmp_path, user, output_name="outputs")
        if sid == "s1":
            (work / "outputs" / "first").write_text("first", encoding="utf-8")
            (work / "outputs" / "first").chmod(0o660)
    assert stat.S_IMODE((work / "outputs" / "first").stat().st_mode) == 0o660
    assert stat.S_IMODE((work / "outputs").stat().st_mode) == 0o2770
    assert stat.S_IMODE((work / "TASK.md").stat().st_mode) == 0o600
    assert (work / "outputs" / "first").read_text(encoding="utf-8") == "first"


@pytest.mark.parametrize("note,mode", [("1: a", "hpc"), ("1: b", "local"), ("a", "hpc"), ("2", "local"),
                                      ("1: a, 1: b", None), ("2: a", None), ("HPC maybe", None)])
def test_cli_compute_selection(note, mode):
    from labhq.orchestrator.execution import selection
    assert selection(note) == mode


@pytest.mark.parametrize("orchestrator", ["cso", "sci_reviewer", "editor", "facilities", "custom-chief"])
def test_team_choice_excludes_orchestration_only_runners(orchestrator):
    from labhq.orchestrator.execution import available_runner
    hub = hub_for(lambda task: None, tools=False)
    hub.s.orchestrator.chief_of_staff_agent = "custom-chief"
    hub.agents[orchestrator] = dict(runner_id="cluster", hpc_tools=True, whole_request_hpc=True, builtin_mcp=["hpc"])
    assert available_runner(hub) is None
    assert available_runner(hub, orchestrator) == "cluster"  # explicit direct target remains valid


@pytest.mark.parametrize("configured", [False, True])
def test_runner_reports_whole_request_eligibility_without_exposing_configured_paths(tmp_path, configured):
    from labhq.orchestrator.execution import available_runner, plan_errors
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"],
                     project_dirs=[str(tmp_path / "private-input")] if configured else [])
    runner.registry.agents = {agent.id: agent}
    row = runner.roster()[0]
    assert row["hpc_tools"] is True and row["whole_request_hpc"] is (not configured)
    assert "private-input" not in json.dumps(row) and "project_dirs" not in row
    hub = hub_for(lambda task: None)
    hub.agents["worker"] = {**row, "runner_id": "cluster"}
    assert available_runner(hub, "worker") == (None if configured else "cluster")
    assert available_runner(hub) == (None if configured else "cluster")
    errors = plan_errors([{"id": "s1", "agent_id": "worker", "execution": "hpc"}],
                         {"mode": "hpc", "runner_id": "cluster"}, hub.agents)
    assert bool(errors) is configured


def test_missing_whole_request_capability_is_not_assumed_from_generic_hpc_tools():
    from labhq.orchestrator.execution import available_runner, plan_errors
    hub = hub_for(lambda task: None)
    hub.agents["worker"].pop("whole_request_hpc")
    assert available_runner(hub) is None
    assert plan_errors([{"id": "s1", "agent_id": "worker", "execution": "hpc"}],
                       {"mode": "hpc", "runner_id": "cluster"}, hub.agents)


@pytest.mark.parametrize("paths", [
    ["outputs/x", "outputs/x"], ["outputs/x", "outputs/x/y"], ["outputs/x/y", "outputs/x"],
    ["outputs/A", "outputs/a"],
])
def test_same_step_file_collisions_include_ancestors_and_optional_outputs(paths):
    from labhq.orchestrator.execution import plan_errors
    hub = hub_for(lambda task: None)
    choice = {"mode": "hpc", "runner_id": "cluster"}
    step = {"id": "s1", "agent_id": "worker", "execution": "hpc",
            "outputs": paths[:1], "optional_outputs": paths[1:]}
    assert any("collision" in err for err in plan_errors([step], choice, hub.agents))
    other = {**step, "id": "s2", "outputs": paths[1:], "optional_outputs": []}
    assert plan_errors([{**step, "optional_outputs": []}, other], choice, hub.agents) == []


@pytest.mark.parametrize("environment", [
    {"environment_binding": {"version": 1}}, {"environment_bindings": [{"version": 1}]},
    {"outputs": ["outputs/env/requirements.lock.txt"]},
])
async def test_hpc_environment_contract_refused_before_any_step_dispatch(environment):
    hub = hub_for(lambda task: None)
    hub.requests["r"]["execution_location"] = {"mode": "hpc", "runner_id": "cluster"}
    steps = [{"id": "s1", "agent_id": "worker", "instruction": "prepare", "execution": "hpc",
              "depends_on": []},
             {"id": "s2", "agent_id": "worker", "instruction": "environment", "execution": "hpc",
              "depends_on": ["s1"], **environment}]
    with pytest.raises(ValueError, match="scheduler-readable environment snapshot"):
        await Orchestrator(hub).run_dag("r", "test", steps, {})
    assert hub.calls == []  # not even the preceding non-environment step starts


async def test_runner_refuses_hpc_environment_binding_without_spawning(tmp_path, monkeypatch):
    from labhq.adapters.mock import MockAdapter
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)

    async def never(adapter, ctx):
        raise AssertionError("must refuse before adapter spawn")

    monkeypatch.setattr(MockAdapter, "run", never)
    result = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="create .venv",
        meta={"kind": "step", "step_id": "env", "environment_binding": {"version": 1},
              "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}}))
    assert not result.ok and "scheduler-readable environment snapshot" in result.error
    assert result.cost_usd == 0 and result.cost_known
    assert not runner.workspaces and not runner.jobs


async def test_hpc_choice_and_dispatch_follow_request_pinned_runner_view():
    async def dispatch(task):
        return result(task, text="done")

    hub = hub_for(dispatch, tools=False)
    native = dict(hub.agents["worker"])
    pinned = {**native, "runner_id": "wsl", "hpc_tools": True, "whole_request_hpc": True, "builtin_mcp": ["hpc"]}
    hub.runners["wsl"] = object()
    hub.runner_capabilities["wsl"] = {"scheduler": "mock"}
    class PinnedHub(FakeHub):
        def request_agents(self, rid):
            return {**self.agents, "worker": pinned if rid == "r" else native}

        def request_runner(self, aid, rid):
            return "wsl" if rid == "r" and aid == "worker" else "cluster"

    hub.__class__ = PinnedHub

    async def answer(**card):
        return {"approved": True, "note": "1: a"}

    hub.request_approval = answer
    orch = Orchestrator(hub)
    assert await orch._choose_execution("r")
    assert hub.requests["r"]["execution_location"]["runner_id"] == "wsl"
    finished = {}
    await orch.run_dag("r", "test", [{"id": "s1", "agent_id": "worker", "instruction": "run",
                                     "depends_on": [], "execution": "hpc"}], finished)
    assert finished["s1"].ok
    assert hub.agents["worker"] == native and hub.agent_runner["worker"] == "cluster"


@pytest.mark.parametrize("field", ["project_dirs", "references"])
@pytest.mark.parametrize("mode", ["hpc", "local"])
async def test_existing_request_paths_refuse_hpc_before_processing_but_preserve_local(field, mode):
    hub = hub_for(lambda task: None)
    hub.requests["r"][field] = ["/existing/input"] if field == "project_dirs" else [
        {"kind": "path", "value": "/existing/input"}]

    async def answer(**card):
        return {"approved": True, "note": "1: a" if mode == "hpc" else "1: b"}

    hub.request_approval = answer
    accepted = await Orchestrator(hub)._choose_execution("r")
    assert accepted is (mode == "local")
    assert hub.calls == []
    if mode == "hpc":
        assert hub.requests["r"]["status"] == "failed"
        assert "request-input snapshot" in hub.requests["r"]["report"]
        assert hub.requests["r"]["execution_location"]["mode"] == "hpc"  # no silent local downgrade


async def test_saved_hpc_choice_with_unstaged_request_input_never_starts_dag():
    hub = hub_for(lambda task: None)
    hub.requests["r"].update(project_dirs=["/existing/input"],
        execution_location={"mode": "hpc", "runner_id": "cluster", "source": "PI 선택"})
    with pytest.raises(ValueError, match="request-input snapshot"):
        await Orchestrator(hub).run_dag("r", "test", [{"id": "s1", "agent_id": "worker",
            "execution": "hpc", "instruction": "process inputs", "depends_on": []}], {})
    assert not await Orchestrator(hub)._choose_execution("r")
    assert not hub.calls and not hub.approvals


@pytest.mark.parametrize("field", ["project_dirs", "references"])
@pytest.mark.parametrize("resume", [False, True])
async def test_saved_hpc_inputs_refuse_before_briefing_or_precedent(field, resume):
    async def never(task):
        raise AssertionError("saved HPC inputs must refuse before any local model turn")

    hub = hub_for(never)
    hub.s.orchestrator.chief_of_staff_agent = "cso"
    req = hub.requests["r"]
    req[field] = ["/existing/input"] if field == "project_dirs" else [{"kind": "path", "value": "/existing/input"}]
    req["execution_location"] = {"mode": "hpc", "runner_id": "cluster", "source": "PI 선택"}
    orch = Orchestrator(hub)
    await orch.run_request("r", resume=resume)
    assert req["status"] == "failed" and "request-input snapshot" in req["report"]
    assert not hub.calls and not hub.approvals
    with pytest.raises(ValueError, match="request-input snapshot"):
        await orch.run_step(Task(agent_id="cso", request_id="r", prompt="brief",
                                 meta={"kind": "briefing"}))
    assert not hub.calls


@pytest.mark.parametrize("source", ["staff", "project_dirs", "reference_dirs"])
async def test_runner_refuses_unstaged_hpc_inputs_before_workspace_or_job(tmp_path, monkeypatch, source):
    from labhq.adapters.mock import MockAdapter
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"],
                     project_dirs=[str(tmp_path / "input")] if source == "staff" else [])
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, "get", lambda _: agent)

    async def never(adapter, ctx):
        raise AssertionError("unstaged inputs must not reach the adapter")

    monkeypatch.setattr(MockAdapter, "run", never)
    result = await runner.run_task(Task(agent_id=agent.id, request_id="r", prompt="process input",
        meta={"kind": "step", "step_id": "s1", **({source: [str(tmp_path / "input")]} if source != "staff" else {}),
              "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}}))
    assert not result.ok and "request-input snapshot" in result.error
    assert result.cost_known and result.cost_usd == 0
    assert not runner.workspaces and not runner.jobs


PRECEDENT = [{"paper_id": "synthetic", "text_file": "/stored/paper.md", "text_sha256": "a" * 64}]


def test_precedent_texts_do_not_refuse_hpc_but_request_inputs_still_do():
    from labhq.orchestrator import execution as execution_policy
    hpc = {"mode": "hpc", "runner_id": "cluster"}
    assert execution_policy.input_errors({"precedent_texts": PRECEDENT}, hpc) == []  # #1336
    assert execution_policy.input_errors({"project_dirs": ["/data"]}, hpc)
    assert execution_policy.input_errors({"reference_dirs": ["/refs"]}, hpc)
    assert "precedent full texts are likewise unsupported" not in execution_policy.plan_rule(hpc)


@pytest.mark.parametrize("mode", ["hpc", "local"])
async def test_hpc_step_is_dispatched_without_precedent_texts_local_keeps_them(mode):
    seen = []

    async def dispatch(task):
        seen.append(dict(task.meta))
        return result(task, text="stored paper processed")

    hub = hub_for(dispatch)
    hub.requests["r"]["execution_location"] = {"mode": mode, "runner_id": "cluster"}
    task = Task(agent_id="worker", request_id="r", prompt="read stored paper",
                meta={"kind": "step", "step_id": "s1", "precedent_texts": PRECEDENT})
    assert (await Orchestrator(hub).run_step(task)).ok
    assert len(seen) == 1 and hub.requests["r"]["execution_location"]["mode"] == mode
    if mode == "hpc":
        assert "precedent_texts" not in seen[0]  # scheduler jobs never read them (#1336)
    else:
        assert seen[0]["precedent_texts"] == PRECEDENT


async def test_runner_drops_precedent_texts_from_a_whole_hpc_step_instead_of_refusing(tmp_path, monkeypatch):
    from labhq.adapters.mock import MockAdapter
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, "get", lambda _: agent)
    seen = []

    async def run(adapter, ctx):
        seen.append(dict(ctx.task.meta))
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=False, error="stop after the adapter saw it")

    monkeypatch.setattr(MockAdapter, "run", run)
    res = await runner.run_task(Task(agent_id=agent.id, request_id="r", prompt="process",
        meta={"kind": "step", "step_id": "s1", "precedent_texts": PRECEDENT,
              "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}}))
    assert "precedent snapshot" not in (res.error or "")
    assert seen and "precedent_texts" not in seen[0]


async def test_waiting_job_outputs_survive_wake_and_runner_restart(tmp_path, monkeypatch):
    from labhq.adapters.mock import MockAdapter
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    location = {"mode": "hpc", "runner_id": settings.runner.id}
    calls = []

    async def turn(adapter, ctx):
        ctx.before_spawn()
        calls.append(ctx)
        if len(calls) == 1:
            # Synthetic submission receipt; actual broker/mock scheduler submission is covered above.
            ctx.record_run(hpc_submitted=True, hpc_step_id="s1", hpc_revision=0)
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, session_id="session-s1")

    monkeypatch.setattr(MockAdapter, "run", turn)
    first_runner = Runner(settings)
    monkeypatch.setattr(first_runner.registry, "get", lambda _: agent)
    first = await first_runner.run_task(Task(agent_id=agent.id, request_id="r", prompt="submit",
        meta={"kind": "step", "step_id": "s1", "execution_location": location}))
    workdir = Path(first.workdir)
    (workdir / "outputs" / "table.tsv").write_text("v\n7\n", encoding="utf-8")
    (workdir / "outputs" / "ancillary.tsv").write_text("gene\nTP53\n", encoding="utf-8")

    restarted = Runner(settings)
    monkeypatch.setattr(restarted.registry, "get", lambda _: agent)
    wake_task = Task(agent_id=agent.id, request_id="r", resume_session_id="session-s1", prompt="wake",
        meta={"kind": "step", "step_id": "s1", "execution_location": location,
              "outputs": ["outputs/table.tsv"]})
    wake = await restarted.run_task(wake_task)
    assert wake.ok, wake.error
    assert wake.workdir == first.workdir
    assert "outputs/ancillary.tsv" in wake.unreported_outputs
    manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
    assert "outputs/ancillary.tsv" in {row["path"] for row in manifest["runs"][wake_task.id]["observed_outputs"]}
    baseline = restarted._resume_baseline(wake_task, agent, restarted.workspaces[wake_task.id])
    assert baseline["request_id"] == "r" and baseline["step_id"] == "s1"
    assert restarted._resume_baseline(wake_task.model_copy(update={"request_id": "other"}),
                                      agent, restarted.workspaces[wake_task.id]) is None
    revised = wake_task.model_copy(update={"meta": {**wake_task.meta, "revision": 1}})
    assert restarted.workspaces[wake_task.id].hpc_output_baseline(revised) is None
    req = {"id": "r", "status": "done", "mode": "team", "plan": {"steps": [{"id": "s1"}]},
           "results": {"s1": wake.model_dump(mode="json")}}
    bundle = Path(build_request_bundle(req, settings)["path"])
    assert (bundle / "steps/s1/outputs/ancillary.tsv").read_text(encoding="utf-8") == "gene\nTP53\n"
    assert verify_request(req, settings)["exit_code"] == 0


async def test_legacy_shared_folder_is_preserved_without_adoption(tmp_path, monkeypatch):
    from labhq.adapters.mock import MockAdapter
    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    legacy = Path(settings.runner.workspace_root) / "hpc/r"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text('{"legacy": true}', encoding="utf-8")
    (legacy / "outputs").mkdir()
    (legacy / "outputs/old.txt").write_text("preserve", encoding="utf-8")
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    monkeypatch.setattr(runner.registry, "get", lambda _: agent)

    async def no_job(adapter, ctx):
        ctx.before_spawn()
        assert not (ctx.workdir / "outputs/old.txt").exists()
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True)

    monkeypatch.setattr(MockAdapter, "run", no_job)
    result = await runner.run_task(Task(agent_id=agent.id, request_id="r", prompt="new step",
        meta={"kind": "step", "step_id": "s1",
              "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}}))
    assert not result.ok and "잡 기록" in result.error
    assert Path(result.workdir) == legacy / "steps/s1"
    assert (legacy / "manifest.json").read_text(encoding="utf-8") == '{"legacy": true}'
    assert (legacy / "outputs/old.txt").read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(os.name == "nt", reason="POSIX group grants; covered by Linux CI")
@pytest.mark.parametrize("problem", [None, "changed", "symlink", "hardlink"])
def test_shared_job_data_grants_only_inventoried_verified_files(tmp_path, monkeypatch, problem):
    import hashlib
    import labhq.tools.hpc_mcp as hpc
    settings = _settings(tmp_path)
    monkeypatch.setattr(hpc, "S", settings)
    request = tmp_path / "hpc/r"
    workdir = request / "steps/s2"
    workdir.mkdir(parents=True)
    data = request / "data/s1/t1"
    data.mkdir(parents=True)
    collected = data / "input.tsv"
    collected.write_text("checked", encoding="utf-8")
    unknown = data / "private.txt"
    unknown.write_text("not shared", encoding="utf-8")
    unknown.chmod(0o600)
    records = [{"producer_step": "s1", "task_id": "t1", "path": "input.tsv",
                "sha256": hashlib.sha256(b"checked").hexdigest()}]
    meta = request / ".labhq/hpc-inputs/s1/t1.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps(records), encoding="utf-8")
    if problem == "changed":
        collected.write_text("changed", encoding="utf-8")
    elif problem == "symlink":
        collected.unlink()
        collected.symlink_to(unknown)
    elif problem == "hardlink":
        collected.unlink()
        os.link(unknown, collected)
    if problem:
        with pytest.raises((OSError, RuntimeError)):
            hpc._share_data_inputs(workdir, {"s1": str(data)}, os.getgid())
    else:
        hpc._share_data_inputs(workdir, {"s1": str(data)}, os.getgid())
        assert stat.S_IMODE(collected.stat().st_mode) == 0o440
    assert stat.S_IMODE(unknown.stat().st_mode) == 0o600


def _job_turns(ids):
    """A step whose turns submit these job ids in order (None: finish), with wait_jobs answering at once."""
    dispatched = []

    async def dispatch(task):
        dispatched.append(task)
        index = len(dispatched) - 1
        job = ids[index] if index < len(ids) else None
        return result(task, text="done" if job is None else "waiting", workdir="/w",
                      pending_jobs=[] if job is None else [job])

    hub = FakeHub(dispatch)

    async def wait_jobs(task_id):
        await asyncio.sleep(0)
        return {"jobs": [{"job_id": "j", "state": "failed", "exit_status": 1}]}

    hub.wait_jobs = wait_jobs
    return hub, dispatched


async def test_a_wake_that_resubmits_hpc_work_does_not_count_toward_the_wake_limit():
    """5th airway trial: build_index fixed and resubmitted its job five times, then died at the limit (#1357)."""
    hub, dispatched = _job_turns([f"j{i}" for i in range(7)])  # 7 submissions, each wake a new job
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="index"))
    assert res.ok and res.text == "done" and len(dispatched) == 8


async def test_a_wake_that_submits_nothing_new_still_counts():
    hub, dispatched = _job_turns(["j0"] * 20)  # the same job keeps being reported: no progress
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="index"))
    assert res.error_kind == "wake_limit" and len(dispatched) == 1 + hub.s.orchestrator.max_wake_cycles


async def test_endless_resubmission_stops_at_the_hard_cap_and_releases_the_live_jobs():
    from labhq.orchestrator import cso
    hub, dispatched = _job_turns([f"j{i}" for i in range(1000)])
    sent, stored = [], {}
    hub.task_runner = {}

    async def send_runner(runner_id, msg):
        sent.append((runner_id, msg))

    class Store:
        def get(self, kind, key):
            return {"runner_id": "cluster"} if kind == "task" else None

        def put(self, kind, key, body):
            stored[(kind, key)] = body

    hub.send_runner, hub.store = send_runner, Store()
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="index"))
    cap = hub.s.orchestrator.max_wake_cycles * cso.WAKE_HARD_CAP_FACTOR
    assert res.error_kind == "wake_limit" and len(dispatched) == 1 + cap and res.pending_jobs == []
    last = dispatched[-1].id
    assert sent == [("cluster", {"type": "task.jobs_cancel", "task_id": last, "release": True})]
    assert stored[("jobs_cancel", last)]["runner_id"] == "cluster"  # resent on reconnect until confirmed


def _staged_job(runner, tmp_path, *, step_file=None):
    """A finished job whose fetch landed in jobs/fetch/<job>/ with one result, outputs/ holding the step's own file."""
    workdir = tmp_path / "step"
    staged = workdir / "jobs" / "fetch" / "7682" / "sub"
    staged.mkdir(parents=True)
    (staged / "index.tsv").write_text("job result", encoding="utf-8")
    (workdir / "outputs" / "sub").mkdir(parents=True)
    if step_file is not None:
        (workdir / "outputs" / "sub" / "index.tsv").write_text(step_file, encoding="utf-8")
    (workdir / "outputs" / "kept.tsv").write_text("the step's own", encoding="utf-8")
    job = {"task_id": "t1", "job_id": "7682", "agent_id": "a", "workdir": str(workdir), "terminal": True,
           "fetched": True, "fetched_paths": ["sub/index.tsv"]}
    runner.jobs = {"7682": job}
    return workdir, job


def _late_runner(tmp_path):
    runner = Runner(_settings(tmp_path))
    events = []

    async def emit(event):
        events.append(event)

    runner.emit = emit
    return runner, events


async def test_job_files_are_staged_and_committed_only_while_the_step_still_waits(tmp_path, monkeypatch):
    from labhq import hpc_transfer
    runner, _ = _late_runner(tmp_path)
    seen = []
    monkeypatch.setattr(hpc_transfer, "fetch_results",
                        lambda transfer, spec, logs, fetch_to, base=None: seen.append(fetch_to) or {"fetched": [], "skipped": []})
    monkeypatch.setattr(runner.scheduler, "transfer", lambda: object(), raising=False)
    workdir, job = _staged_job(runner, tmp_path, step_file="the step's earlier table")
    runner._fetch_job_files(dict(job), {"fetch_to": str(workdir / "outputs")})
    assert seen == [workdir / "jobs" / "fetch" / "7682"]  # never straight into outputs/ (#1357)
    runner._commit_fetch(job, workdir / "outputs")
    assert (workdir / "outputs" / "sub" / "index.tsv").read_text(encoding="utf-8") == "job result"
    assert (workdir / "jobs" / "replaced" / "7682" / "sub" / "index.tsv").read_text(encoding="utf-8") ==         "the step's earlier table"


async def test_a_released_steps_staged_files_go_to_jobs_late_and_outputs_stay(tmp_path):
    runner, _ = _late_runner(tmp_path)
    workdir, job = _staged_job(runner, tmp_path, step_file="the step's own table")
    assert await runner._cancel_local_jobs("t1", relocate=True)  # the release arrived during the transfer
    runner._commit_fetch(job, workdir / "outputs")
    assert (workdir / "jobs" / "late" / "7682" / "sub" / "index.tsv").read_text(encoding="utf-8") == "job result"
    assert (workdir / "outputs" / "sub" / "index.tsv").read_text(encoding="utf-8") == "the step's own table"
    assert job["late"] is True
    runner.__dict__["_jobs_cancelled"] = set()  # a restarted runner still knows from its store
    assert runner._jobs_released("t1")


async def test_committed_results_move_out_and_replaced_files_come_back_on_release(tmp_path):
    """The fetch finished before the step's last wake reported (#1363 review)."""
    runner, events = _late_runner(tmp_path)
    workdir, job = _staged_job(runner, tmp_path, step_file="the step's own table")
    runner._commit_fetch(job, workdir / "outputs")
    assert await runner._cancel_local_jobs("t1")  # a request cancel: consumed results stay
    assert (workdir / "outputs" / "sub" / "index.tsv").read_text(encoding="utf-8") == "job result"
    assert await runner._cancel_local_jobs("t1", relocate=True)  # a wake-limit release
    assert (workdir / "jobs" / "late" / "7682" / "sub" / "index.tsv").read_text(encoding="utf-8") == "job result"
    assert (workdir / "outputs" / "sub" / "index.tsv").read_text(encoding="utf-8") == "the step's own table"
    assert (workdir / "outputs" / "kept.tsv").exists() and job["late"] is True
    assert any((event.data or {}).get("job_late") == {"job_id": "7682"} for event in events)


async def test_a_wake_limit_release_is_replayed_with_its_release_mark_after_a_reconnect(tmp_path):
    """The runner was away when the step was released: the replay must still say `release` (#1364)."""
    import time
    from labhq.gateway.server import create_app
    from labhq.settings import Settings

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.store.put("jobs_cancel", "released", {"runner_id": "r1", "requested_at": time.time(), "release": True})
    hub.store.put("jobs_cancel", "cancelled", {"runner_id": "r1", "requested_at": time.time()})
    sent = []

    async def send(runner_id, message):
        sent.append((runner_id, message))

    hub.send_runner = send
    await hub.flush_task_cancels("r1")  # it reconnects
    assert ("r1", {"type": "task.jobs_cancel", "task_id": "released", "release": True}) in sent
    assert ("r1", {"type": "task.jobs_cancel", "task_id": "cancelled"}) in sent  # a request cancel stays plain


def _attempts(tmp_path, states, names=None):
    """A step's remote jobs, submitted in this order with these terminal states (None: still running)."""
    from types import SimpleNamespace
    from tests.test_hpc_remote_data import inventory

    ws = SimpleNamespace(dir=tmp_path, task=Task(agent_id="worker", request_id="r", prompt="fetch",
                                                 meta={"step_id": "s1", "outputs": ["outputs/tx2gene.tsv"]}))
    runner = Runner.__new__(Runner)
    runner.scheduler = SimpleNamespace(transfer=lambda: object())
    runner.jobs = {}
    for index, state in enumerate(states):
        runner.jobs[str(7677 + index)] = {
            "workdir": str(tmp_path), "transfer": {"request_id": "r", "step_id": "s1"}, "submitted_by_runner": True,
            "name": (names or ["fetch_fastq"] * len(states))[index],
            "submitted_at": 1000.0 + index, "terminal": state is not None, "state": state or "running",
            "fetched": state == "completed", **({"remote_manifest": inventory()} if state == "completed" else {})}
    return runner, ws


@pytest.mark.parametrize("states,ok", [
    (["failed", "completed"], True),             # walltime-killed 7677, then 7687 exit 0 (#1366)
    (["cancelled", "failed", "completed"], True),
    (["failed"], False),                          # every attempt failed: refused as before
    (["completed", "failed"], False),             # a failure after the success still refuses
    ([None, "completed"], False),                 # an earlier attempt still running still refuses
])
def test_a_failed_attempt_replaced_by_a_later_success_does_not_refuse_the_outputs(tmp_path, monkeypatch, states, ok):
    from labhq import hpc_transfer as ht
    monkeypatch.setattr(ht, "verify_remote_data", lambda *args: None)
    runner, ws = _attempts(tmp_path, states)
    if ok:
        found, = runner._remote_step_outputs(ws)
        assert [row["path"] for row in found["files"]] == ["tx2gene.tsv"]
    else:
        with pytest.raises(ht.TransferError, match="no verified successful output manifest"):
            runner._remote_step_outputs(ws)


def test_a_failed_shard_is_not_replaced_by_another_jobs_success(tmp_path, monkeypatch):
    """Only a later job of the same name is a resubmission (#1367 review): a failed shard keeps refusing."""
    from labhq import hpc_transfer as ht
    monkeypatch.setattr(ht, "verify_remote_data", lambda *args: None)
    runner, ws = _attempts(tmp_path, ["failed", "completed"], names=["shard_1", "shard_2"])
    with pytest.raises(ht.TransferError, match="no verified successful output manifest"):
        runner._remote_step_outputs(ws)
    runner, ws = _attempts(tmp_path, ["failed", "completed"], names=["", ""])  # unnamed jobs are never paired
    with pytest.raises(ht.TransferError):
        runner._remote_step_outputs(ws)


def test_an_unreadable_manifest_replaced_by_a_same_name_success_does_not_refuse(tmp_path, monkeypatch):
    """6th airway trial: 8960 exited 0 but its manifest was too large to read; a same-name success replaces it (#1368)."""
    from labhq import hpc_transfer as ht
    monkeypatch.setattr(ht, "verify_remote_data", lambda *args: None)
    runner, ws = _attempts(tmp_path, ["completed", "completed"])
    first = runner.jobs["7677"]
    first["fetched"] = False
    first.pop("remote_manifest", None)
    found, = runner._remote_step_outputs(ws)
    assert [row["path"] for row in found["files"]] == ["tx2gene.tsv"]
    runner, ws = _attempts(tmp_path, ["completed"])  # alone, the unreadable manifest still refuses
    runner.jobs["7677"]["fetched"] = False
    with pytest.raises(ht.TransferError):
        runner._remote_step_outputs(ws)


@pytest.mark.parametrize("declared,ok", [(["outputs"], True), (["outputs/env/result.tsv"], False)])
def test_an_environment_folder_left_out_of_the_manifest_does_not_make_outputs_incomplete(
        tmp_path, monkeypatch, declared, ok):
    from labhq import hpc_transfer as ht
    monkeypatch.setattr(ht, "verify_remote_data", lambda *args: None)
    runner, ws = _attempts(tmp_path, ["completed"])
    ws.task.meta["outputs"] = declared
    runner.jobs["7677"]["remote_manifest"]["skipped"] = [{"path": "env", "reason": "environment folder"}]
    if ok:
        assert runner._remote_step_outputs(ws)
    else:  # a declared output inside the environment folder is still missing
        with pytest.raises(ht.TransferError, match="incomplete"):
            runner._remote_step_outputs(ws)



def test_the_remote_scan_leaves_out_a_conda_environment_and_still_caps_real_outputs(cluster, tmp_path):
    """Runs the POSIX helper (Linux CI): a job-folder conda env made the 6th trial's manifest exceed 4 MB (#1368)."""
    import os
    if os.name == "nt":
        pytest.skip("remote POSIX descriptor helper is exercised by Linux CI")
    from labhq import hpc_transfer as ht
    from tests.test_hpc_remote_data import helper_transfer, prepare
    transfer = helper_transfer(cluster)
    env = {f"env/conda-meta/pkg{i}.json": b"{}" for i in range(3)}
    env.update({f"env/lib/f{i:05d}.py": b"" for i in range(25000)})  # more rows than the scan allows
    spec = prepare(cluster, transfer, tmp_path, {"de_results.csv": b"gene,lfc", **env,
                                                 "venv/pyvenv.cfg": b"home=/x", "venv/lib/a.py": b""})
    remote = ht.fetch_results(transfer, spec, tmp_path / "logs", tmp_path / "outputs")["remote"]
    assert [row["path"] for row in remote["files"]] == ["de_results.csv"]
    assert {(row["path"], row["reason"]) for row in remote["skipped"]} >= {
        ("env", "environment folder"), ("venv", "environment folder")}
    many = {f"out/r{i:05d}.tsv": b"" for i in range(20001)}  # real outputs over the cap still refuse
    spec = prepare(cluster, transfer, tmp_path, many)
    with pytest.raises(Exception, match="checking remote data failed|too many remote outputs"):
        ht.fetch_results(transfer, spec, tmp_path / "logs2", tmp_path / "outputs2")
