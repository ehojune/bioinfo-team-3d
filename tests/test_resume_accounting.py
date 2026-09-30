"""Replay measured cumulative CLI counters without invoking vendor CLIs."""

import json
from pathlib import Path

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.gateway.server import create_app
from labhq.models import AgentSpec, Engine, Task
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.runner.workspace import TaskWorkspace
from labhq.settings import Settings


ROOT = Path(__file__).parent / "fixtures" / "real"


def captures(engine):
    name = "claude_resume_cost" if engine == "claude_code" else "codex_resume_usage"
    events = [json.loads(line) for line in (ROOT / engine / f"{name}.jsonl").read_text(
        encoding="utf-8").splitlines()]
    return [[event] for event in events] if engine == "claude_code" else [
        events[index:index + 2] for index in range(0, len(events), 2)]


def test_measured_counters_are_session_totals():
    claude = [group[0] for group in captures("claude_code")]
    assert [event["total_cost_usd"] for event in claude] == pytest.approx([
        0.0508328, 0.0599264, 0.0664079])
    assert [event["usage"]["input_tokens"] for event in claude] == [10, 10, 10]
    assert [sum(model["inputTokens"] for model in event["modelUsage"].values())
            for event in claude] == [10, 20, 30]
    assert len({event["session_id"] for event in claude}) == 1
    codex = captures("codex")
    assert len({group[0]["thread_id"] for group in codex}) == 1
    assert [group[1]["usage"]["input_tokens"] for group in codex] == [25624, 55788, 85978]
    assert [group[1]["usage"]["output_tokens"] for group in codex] == [5, 20, 25]


def setup_runner(tmp_path, monkeypatch, engine, groups=None, flow=None):
    settings = Settings()
    settings.runner.state_dir = settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.orchestrator.step_retry_backoff_s = 0
    settings.orchestrator.step_max_attempts = 3
    settings.hpc.scheduler = "none"
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine(engine), builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    groups = groups if groups is not None else captures(engine)
    baselines, calls = [], []

    class Replay:
        async def run(self, ctx):
            index = len(calls)
            calls.append(ctx.task)
            baselines.append(ctx.resume_baseline)
            adapter = get_adapter(agent.engine, settings)
            state = RunState(final_text="fixture result")
            for event in groups[index]:
                event = dict(event)
                if engine == "claude_code" and event["type"] == "result":
                    if (flow == "retry" and index < 2) or (flow == "wrap" and index == 0):
                        event.update(subtype="error_during_execution", is_error=True, result="rate limit")
                    elif flow == "wrap" and index == 1:
                        event.update(subtype="error_max_turns", is_error=True, result="turn limit")
                await adapter.handle_line(json.dumps(event), state, ctx)
            if flow == "wake" and index < 2:
                await runner._on_track({"job_id": f"j{index}", "task_id": ctx.task.id,
                                        "agent_id": agent.id})
            if ctx.task.meta.get("kind") == "wrap_up":
                (ctx.workdir / "outputs" / "PARTIAL_STATUS.md").write_text("fixture", encoding="utf-8")
            return adapter.finalize(state, ctx, 0)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Replay())
    return runner, agent, baselines, calls


def run_record(runner, task):
    return json.loads((runner.workspaces[task.id].dir / "manifest.json").read_text(
        encoding="utf-8"))["runs"][task.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude_code", "codex"])
async def test_runner_two_resumes_emit_and_record_only_deltas(tmp_path, monkeypatch, engine):
    runner, agent, baselines, calls = setup_runner(tmp_path, monkeypatch, engine)
    results, session = [], None
    for index in range(3):
        task = Task(id=f"t{index}", agent_id=agent.id, prompt="fixture", resume_session_id=session)
        result = await runner.run_task(task)
        session = result.session_id
        results.append(result)
        record = run_record(runner, task)
        assert record["cost_usd"] == result.cost_usd
        assert record["usage"] == result.usage
        assert record["usage_known"] is True
        assert record["session_usage_total"] == captures(engine)[index][-1][
            "modelUsage" if engine == "claude_code" else "usage"]
        if engine == "claude_code":
            assert record["session_cost_total"] == captures(engine)[index][0]["total_cost_usd"]
    assert baselines[0] is None
    assert [baseline["session_id"] for baseline in baselines[1:]] == [session, session]
    events = [event["data"] for event in runner.store.pending() if event["type"] == "agent.usage"]
    assert [event["tokens"] for event in events] == [result.usage for result in results]
    assert [event["cost_usd"] for event in events] == [result.cost_usd for result in results]
    if engine == "claude_code":
        assert [result.cost_usd for result in results] == pytest.approx([0.0508328, 0.0090936, 0.0064815])
        assert sum(result.cost_usd for result in results) == pytest.approx(0.0664079)
        assert [result.usage["input_tokens"] for result in results] == [10, 10, 10]
    else:
        assert all(result.cost_usd is None and not result.cost_known for result in results)
        assert [result.usage["input_tokens"] for result in results] == [25624, 30164, 30190]
        assert sum(result.usage["output_tokens"] for result in results) == 25
        assert sum(result.usage["input_tokens"] for result in results) == 85978


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["wake", "retry", "wrap"])
async def test_request_wake_retry_and_wrap_sum_three_fixture_calls(tmp_path, monkeypatch, flow):
    runner, agent, baselines, calls = setup_runner(tmp_path, monkeypatch, "claude_code", flow=flow)
    hub = create_app(runner.s).state.hub
    hub.agents[agent.id] = agent.model_dump(mode="json")
    hub.requests["r"] = {"id": "r", "text": "fixture", "status": "running", "cost_usd": 0,
                          "cost_known": True, "usage": {}, "budget_usd": 1}
    hub.save_request("r")

    async def dispatch(task):
        result = await runner.run_task(task)
        message = {"type": "task.result", "task_id": task.id, "request_id": "r",
                   "data": result.model_dump(mode="json")}
        await hub.on_runner_message("runner", message)
        await hub.on_runner_message("runner", message)  # transport replay must not bill twice
        return result

    async def wait_jobs(tid):
        jobs = []
        for job in runner.jobs.values():
            if job["task_id"] == tid:
                job["terminal"] = True
                jobs.append({**job, "state": "done", "exit_status": 0})
        return {"jobs": jobs}

    monkeypatch.setattr(hub, "dispatch", dispatch)
    monkeypatch.setattr(hub, "wait_jobs", wait_jobs)
    orch = Orchestrator(hub)
    result = await orch.run_step(Task(agent_id=agent.id, request_id="r", prompt="fixture",
                                      meta={"step_id": "A", "kind": "step"}))
    assert len(calls) == 3
    assert calls[0].resume_session_id is None
    assert all(task.resume_session_id == result.session_id for task in calls[1:])
    assert orch.cost["r"] == pytest.approx(0.0664079)
    assert hub.requests["r"]["cost_usd"] == pytest.approx(0.0664079)
    assert hub.requests["r"]["usage"]["input_tokens"] == 30
    assert hub.requests["r"]["usage"]["output_tokens"] == 120
    orch._finish("r", "fixture report", {}, result.ok)
    assert hub.requests["r"]["cost_usd"] == pytest.approx(0.0664079)
    if flow == "wrap":
        assert not result.ok and result.partial_results
        assert calls[-1].meta["kind"] == "wrap_up"
    else:
        assert result.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude_code", "codex"])
async def test_lost_baseline_does_not_bill_total_and_next_resume_recovers(tmp_path, monkeypatch, engine):
    groups = captures(engine)
    runner, agent, baselines, calls = setup_runner(tmp_path, monkeypatch, engine, groups=groups[1:])
    session = groups[0][0].get("session_id") or groups[0][0]["thread_id"]
    hub = create_app(runner.s).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "cost_usd": 0.0508328,
                          "cost_known": True, "usage": {}}
    for index in range(2):
        task = Task(agent_id=agent.id, prompt="fixture", resume_session_id=session)
        result = await runner.run_task(task)
        record = run_record(runner, task)
        assert record["session_usage_total"] == groups[index + 1][-1][
            "modelUsage" if engine == "claude_code" else "usage"]
        await hub.on_runner_message("runner", {"type": "task.result", "task_id": task.id,
            "request_id": "r", "data": result.model_dump(mode="json")})
        if index == 0:
            assert result.cost_usd is None and result.cost_known is False
            assert hub.requests["r"]["cost_usd"] == 0.0508328
            if engine == "codex":
                assert result.usage == {} and result.usage_known is False
            else:
                assert result.usage["input_tokens"] == 10
    assert baselines[0] is None and baselines[1]["session_id"] == session
    assert hub.requests["r"]["cost_known"] is False
    if engine == "claude_code":
        assert result.cost_usd == pytest.approx(0.0064815)
        assert hub.requests["r"]["cost_usd"] == pytest.approx(0.0573143)
    else:
        assert hub.requests["r"]["usage_known"] is False
        assert hub.request_summary(hub.requests["r"])["usage_known"] is False
        assert hub.requests["r"]["usage"]["input_tokens"] == 30190


@pytest.mark.parametrize("latest_total", [0.2, None])
def test_baseline_selects_latest_run_across_workspaces_after_restart(tmp_path, monkeypatch, latest_total):
    runner, agent, _, _ = setup_runner(tmp_path, monkeypatch, "claude_code")
    for tid, timestamp, session, engine, total in [
        ("old", 1, "s", "claude_code", 0.1), ("new", 2, "s", "claude_code", latest_total),
        ("other-session", 3, "other", "claude_code", 9), ("other-engine", 4, "s", "codex", 8)]:
        ws = TaskWorkspace(runner.ws_root, Task(id=tid, agent_id=agent.id, prompt="fixture"), agent)
        ws.update_run(tid, ended_at=timestamp, session_id=session, engine=engine, session_cost_total=total)
    other = Task(id="other-runner", agent_id=agent.id, prompt="fixture")
    TaskWorkspace(runner.ws_root, other, agent).update_run(
        other.id, ended_at=5, session_id="s", engine="claude_code", runner_id="different-runner",
        session_cost_total=7)
    restarted = Runner(runner.s)
    task = Task(agent_id=agent.id, prompt="fixture", resume_session_id="s")
    ws = TaskWorkspace(runner.ws_root, task, agent)
    assert restarted._resume_baseline(task, agent, ws)["session_cost_total"] == latest_total


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude_code", "codex"])
@pytest.mark.parametrize("bad", ["absent", "legacy", "wrong_session", "reset", "invalid"])
async def test_unusable_baseline_marks_delta_unknown(tmp_path, engine, bad):
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine(engine), builtin_mcp=[])
    group = captures(engine)[1]
    session = group[0].get("session_id") or group[0]["thread_id"]
    baseline = {"session_id": session, "session_cost_total": 0.0508328,
                "session_usage_total": captures("codex")[0][-1]["usage"]}
    if bad == "absent":
        baseline = None
    elif bad == "legacy":
        baseline = {"session_id": session, "cost_usd": 0.0508328, "usage": {"input_tokens": 25624}}
    elif bad == "wrong_session":
        baseline["session_id"] = "another-session"
    elif bad == "reset":
        baseline.update(session_cost_total=1, session_usage_total={"input_tokens": 100000})
    else:
        baseline.update(session_cost_total="invalid", session_usage_total=[])
    events, recorded = [], {}

    async def emit(kind, data):
        events.append((kind, data))

    ctx = RunContext(task=Task(agent_id="a", prompt="fixture", resume_session_id=session),
        agent=agent, settings=settings, workdir=tmp_path, mcp_servers=[], env={}, emit=emit,
        prompt="fixture", resume_baseline=baseline, record_run=lambda **fields: recorded.update(fields))
    state = RunState(final_text="fixture result")
    adapter = get_adapter(agent.engine, settings)
    for event in group:
        await adapter.handle_line(json.dumps(event), state, ctx)
    result = adapter.finalize(state, ctx, 0)
    assert result.cost_usd is None and result.cost_known is False
    usage_event = next(data for kind, data in events if kind == "agent.usage")
    assert usage_event["cost_usd"] is None and usage_event["cost_known"] is False
    if engine == "claude_code":
        assert result.usage["input_tokens"] == usage_event["tokens"]["input_tokens"] == 10
        assert recorded["session_cost_total"] == 0.0599264
    else:
        assert result.usage == usage_event["tokens"] == {}
        assert result.usage_known is usage_event["usage_known"] is False
        assert recorded["session_usage_total"] == group[-1]["usage"]


@pytest.mark.asyncio
async def test_codex_multiple_snapshots_in_one_invocation_are_not_added_as_totals(tmp_path):
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine.codex, builtin_mcp=[])
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    ctx = RunContext(task=Task(agent_id="a", prompt="fixture"), agent=agent, settings=settings,
                     workdir=tmp_path, mcp_servers=[], env={}, emit=emit, prompt="fixture")
    state = RunState(final_text="done")
    adapter = get_adapter(agent.engine, settings)
    for group in captures("codex"):
        for event in group:
            await adapter.handle_line(json.dumps(event), state, ctx)
    result = adapter.finalize(state, ctx, 0)
    assert result.usage["input_tokens"] == 85978
    assert sum(data["tokens"]["input_tokens"] for kind, data in events if kind == "agent.usage") == 85978
