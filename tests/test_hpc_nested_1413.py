from types import SimpleNamespace
import asyncio

import pytest

from labhq.tools.hpc_mcp import nested_output_write
from labhq.models import Task
from labhq.orchestrator.cso import Orchestrator, failure_kind
from tests.test_cso import FakeHub, result
from tests.test_hpc_remote_data import inventory


def hpc_hub(hub):
    hub.requests["r"]["execution_location"] = {"mode": "hpc", "runner_id": "runner"}
    hub.agents["worker"].update(runner_id="runner", hpc_tools=True, whole_request_hpc=True, builtin_mcp=["hpc"])


def test_nested_error_never_enters_general_transient_retry():
    from labhq.models import TaskResult
    outcome = TaskResult(task_id="t", agent_id="worker", ok=False, error_kind="nested_hpc_outputs",
                         error="incomplete: missing outputs: outputs/timeout.tsv")
    assert failure_kind(outcome) == "terminal"


@pytest.mark.asyncio
async def test_submit_refuses_before_job_files_or_broker(tmp_path, monkeypatch):
    from labhq.tools import hpc_mcp
    monkeypatch.setenv("LABHQ_EXECUTION_MODE", "hpc")
    monkeypatch.setattr(hpc_mcp, "WORKDIR", tmp_path)

    async def never(*args, **kwargs):
        pytest.fail("preflight must stop before the broker")

    monkeypatch.setattr(hpc_mcp, "_broker", never)
    with pytest.raises(Exception, match="already starts in outputs"):
        await hpc_mcp.hpc_submit("echo x > outputs/result", "fixture")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("script", [
    "echo hello > outputs/result.tsv", "echo hi >> ./outputs/a",
    "cp input outputs/result", "mv input outputs/result", "mkdir -p outputs/data",
    "touch outputs/result", "printf ok | tee outputs/result", "cd outputs",
])
def test_literal_nested_write_is_rejected(script):
    assert nested_output_write(script)


@pytest.mark.parametrize("script", [
    "cat outputs/input", "cp outputs/input result", "echo outputs/result",
    "echo hi > result", "cd elsewhere", "# echo hi > outputs/result",
    "python scripts/fetch.py", 'echo hi > "$dest"',
    "cd elsewhere; echo hi > outputs/result", "echo hi > outputs/../result",
    "cat <<EOF\necho hi > outputs/result\nEOF",
    "cp -t /tmp outputs/input", "touch -r outputs/reference result",
    "cp --target-directory=/tmp outputs/input",
])
def test_reads_normal_paths_and_dynamic_paths_are_not_classified(script):
    assert not nested_output_write(script)


@pytest.mark.asyncio
@pytest.mark.parametrize("rerun_ok", [True, False])
async def test_nested_repair_is_same_worker_and_only_once_even_after_restart(rerun_ok):
    async def dispatch(task):
        if task.meta.get("nested_hpc_rerun"):
            return result(task, ok=rerun_ok, text="done" if rerun_ok else "", workdir="C:/fake/work",
                          error=None if rerun_ok else "still missing", session_id="session")
        return result(task, ok=False, error_kind="nested_hpc_outputs", error="verified nested output",
                      missing_outputs=["outputs/result"], workdir="C:/fake/work", session_id="session")

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda agent: True
    hpc_hub(hub)
    task = Task(agent_id="worker", request_id="r", prompt="produce result",
                meta={"kind": "step", "step_id": "s1"})
    outcome = await Orchestrator(hub).run_step(task)
    assert outcome.ok is rerun_ok
    assert len(hub.calls) == 2
    assert hub.calls[1].agent_id == "worker" and hub.calls[1].resume_session_id == "session"
    assert "verified nested output" in hub.calls[1].prompt
    assert hub.requests["r"]["nested_hpc_retries"] == {"s1": True}
    # A new orchestrator with the restored request cannot spend another repair.
    outcome = await Orchestrator(hub).run_step(task)
    assert not outcome.ok and len(hub.calls) == 3


@pytest.mark.asyncio
async def test_other_failure_has_no_special_retry():
    async def dispatch(task):
        return result(task, ok=False, error="missing outputs", missing_outputs=["outputs/result"])
    hub = FakeHub(dispatch)
    hpc_hub(hub)
    task = Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"})
    assert not (await Orchestrator(hub).run_step(task)).ok
    assert len(hub.calls) == 1


@pytest.mark.asyncio
async def test_nested_failure_after_job_wake_is_repaired_and_retry_job_is_awaited():
    async def dispatch(task):
        count = len(hub.calls)
        if count in (1, 3):
            return result(task, text="submitted", pending_jobs=[f"job{count}"],
                          workdir="C:/fake/work", session_id="session")
        if count == 2:
            return result(task, ok=False, error_kind="nested_hpc_outputs", error="verified nested output",
                          missing_outputs=["outputs/result"], workdir="C:/fake/work", session_id="session")
        return result(task, text="done", workdir="C:/fake/work", session_id="session")

    hub = FakeHub(dispatch)
    hpc_hub(hub)
    hub.supports_resume = lambda agent: True
    hub.wait_jobs = lambda tid: asyncio.sleep(0, result={"jobs": [{"job_id": "job", "state": "completed", "exit_status": 0}]})
    task = Task(agent_id="worker", request_id="r", prompt="produce result", meta={"kind": "step", "step_id": "s1"})
    outcome = await Orchestrator(hub).run_step(task)
    assert outcome.ok and len(hub.calls) == 4
    assert [bool(t.meta.get("nested_hpc_rerun")) for t in hub.calls] == [False, False, True, True]
    assert all(t.agent_id == "worker" for t in hub.calls)


@pytest.mark.asyncio
async def test_retry_limit_survives_gateway_store_reopen(tmp_path):
    from labhq.gateway.server import Hub
    from tests.test_consult_restart import settings

    s = settings(tmp_path)
    s.policy.budget.reserve_before_dispatch = False
    task = Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"})
    agents = {"worker": {"id": "worker", "engine": "mock", "runner_id": "runner", "hpc_tools": True,
                         "whole_request_hpc": True, "builtin_mcp": ["hpc"]}}
    calls = []

    async def dispatch(current):
        calls.append(current)
        return result(current, ok=False, error_kind="nested_hpc_outputs", error="verified nested output",
                      missing_outputs=["outputs/result"], cost_usd=0, cost_known=True)

    first = Hub(s)
    first.agents = agents
    first.requests["r"] = {"id": "r", "text": "x", "status": "running", "budget_usd": 10,
                           "execution_location": {"mode": "hpc", "runner_id": "runner"}}
    first.save_request("r")
    first.dispatch = dispatch
    try:
        await first.orchestrator.run_step(task)
        assert len(calls) == 2 and first.store.get("request", "r")["nested_hpc_retries"] == {"s1": True}
    finally:
        first.store.close()

    reopened = Hub(s)
    reopened.agents = agents
    reopened.dispatch = dispatch
    try:
        assert reopened.requests["r"]["nested_hpc_retries"] == {"s1": True}
        await reopened.orchestrator.run_step(task)
        assert len(calls) == 3
    finally:
        reopened.store.close()


@pytest.mark.parametrize("case", ["valid", "changed", "skipped", "unchanged", "wrong"])
def test_nested_diagnosis_requires_verified_candidate_and_never_collects_it(tmp_path, monkeypatch, case):
    from labhq import hpc_transfer as ht
    from labhq.runner.daemon import Runner

    manifest = inventory(path="outputs/result.tsv" if case != "wrong" else "outputs/other.tsv")
    if case == "skipped":
        manifest["skipped"] = [{"path": "outputs/result.tsv", "reason": "link"}]
    if case == "unchanged":
        manifest["unchanged_inputs"] = ["outputs/result.tsv"]
    checked = []

    def verify(_transfer, candidate, _request_id):
        checked.append([row["path"] for row in candidate["files"]])
        if case == "changed" and candidate["files"]:
            raise ht.TransferError("changed or unsafe path")

    monkeypatch.setattr(ht, "verify_remote_data", verify)
    task = Task(agent_id="worker", request_id="r", prompt="x",
                meta={"kind": "step", "step_id": "s1", "outputs": ["outputs/result.tsv"]})
    ws = SimpleNamespace(dir=tmp_path, task=task)
    runner = Runner.__new__(Runner)
    runner.scheduler = SimpleNamespace(transfer=lambda: object())
    runner.jobs = {"1": {"workdir": str(tmp_path), "transfer": {"request_id": "r", "step_id": "s1"},
                         "submitted_by_runner": True, "terminal": True, "state": "completed", "fetched": True,
                         "remote_manifest": manifest}}
    found, = runner._remote_step_outputs(ws)
    assert found["files"] == []
    assert checked == [[]]  # no candidate is read unless its declared output is missing
    candidates = found.pop("_nested_candidates")
    assert runner._verify_nested_candidates("r", [], candidates) == []
    assert checked == [[]]
    verified = runner._verify_nested_candidates("r", ["outputs/result.tsv"], candidates)
    assert verified == (["outputs/result.tsv"] if case == "valid" else [])


@pytest.mark.asyncio
async def test_runner_reports_verified_nested_output_as_missing_without_collecting(tmp_path, monkeypatch):
    from labhq import hpc_transfer as ht
    from labhq.adapters.mock import MockAdapter
    from labhq.models import AgentSpec, Engine, TaskResult
    from labhq.runner.daemon import Runner
    from tests.test_workspace_boundary import _settings

    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, "get", lambda _: agent)
    monkeypatch.setattr(runner.scheduler, "transfer", lambda: SimpleNamespace(path=lambda p: p))
    monkeypatch.setattr(ht, "verify_remote_data", lambda *_: None)

    async def run(_adapter, ctx):
        ctx.before_spawn()
        ctx.record_run(hpc_submitted=True, hpc_step_id="s1", hpc_revision=0)
        runner.jobs["job"] = {"task_id": "previous", "workdir": str(ctx.workdir),
                              "transfer": {"request_id": "r", "step_id": "s1"},
                              "submitted_by_runner": True, "terminal": True, "state": "completed", "fetched": True,
                              "remote_manifest": inventory(path="outputs/result.tsv")}
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done")

    monkeypatch.setattr(MockAdapter, "run", run)
    task = Task(agent_id="worker", request_id="r", prompt="x", meta={
        "kind": "step", "step_id": "s1", "outputs": ["outputs/result.tsv"],
        "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}})
    outcome = await runner.run_task(task)
    assert not outcome.ok and outcome.error_kind == "nested_hpc_outputs"
    assert outcome.missing_outputs == ["outputs/result.tsv"] and "extra outputs/" in outcome.error
    assert "outputs/outputs/result.tsv" not in outcome.outputs
