"""#430: request-wide compute choice; no live scheduler, CLI or cluster."""
import json
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


def hub_for(dispatch, scheduler="mock", online=True, tools=True):
    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    hub.agents["worker"].update(runner_id="cluster", hpc_tools=tools, builtin_mcp=["hpc"] if tools else [])
    hub.agent_runner = {"worker": "cluster"}
    hub.runner_capabilities = {"cluster": {"scheduler": scheduler}}
    hub.runners = {"cluster": object()} if online else {}
    return hub


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
                assert task.meta["upstream_steps"] == {}
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
    assert "실행 위치:" in req["report_appendix"]
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
    hub.agents["worker"].update(runner_id="cluster", hpc_tools=True, builtin_mcp=["hpc"])
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
    assert "실행 위치:" in cp1["summary"] and "실행 위치:" in req["report_appendix"]
    assert all(step["execution"] == mode for step in req["plan"]["steps"])


def test_hpc_plan_refuses_other_runner_and_overlapping_output_names():
    from labhq.orchestrator.execution import plan_errors

    hub = hub_for(lambda task: None)
    choice = {"mode": "hpc", "runner_id": "cluster"}
    steps = [{"id": "s1", "agent_id": "worker", "execution": "hpc", "outputs": ["a.txt"]},
             {"id": "s2", "agent_id": "worker", "execution": "hpc", "outputs": ["outputs/a.txt"]}]
    assert any("collision" in error for error in plan_errors(steps, choice, hub.agents))
    hub.agents["worker"]["runner_id"] = "other"
    assert any("selected runner" in error for error in plan_errors(steps[:1], choice, hub.agents))


async def test_shared_workspace_mock_jobs_read_previous_output(tmp_path, monkeypatch):
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
            assert (output / "s1.txt").read_text(encoding="utf-8") == "upstream"
            (output / "s2.txt").write_text("upstream processed", encoding="utf-8")
        return job_id

    monkeypatch.setattr(runner.scheduler, "submit", submit)

    async def run(adapter, ctx):
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
        receipt = json.loads(await hpc.hpc_submit("echo first > s1.txt" if step == "s1" else
                                                "cat s1.txt > s2.txt", step))
        assert receipt["output_dir"] == str(ctx.workdir / "outputs")
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="submitted")

    monkeypatch.setattr(MockAdapter, "run", run)
    location = {"mode": "hpc", "runner_id": "cluster", "workspace": "r"}
    results = []
    for sid in ("s1", "s2"):
        results.append(await runner.run_task(Task(agent_id="worker", request_id="r", prompt="test",
            meta={"kind": "step", "step_id": sid, "execution_location": location, "outputs": [f"outputs/{sid}.txt"]})))
    assert len(submissions) == 2 and len(set(workdirs)) == 1
    assert all(r.ok and r.pending_jobs for r in results)
    assert results[0].workdir == results[1].workdir
    workdir = Path(results[0].workdir)
    assert workdir == Path(settings.runner.workspace_root) / "hpc" / "r"
    assert not (workdirs[0] / "inputs").exists()
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
    assert (workdir / "outputs/s2.txt").read_text(encoding="utf-8") == "upstream processed"
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
