"""Stored precedent texts no longer refuse a whole-request HPC request; HPC steps just go without them (#1336)."""
import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator import execution as execution_policy
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from tests.test_cso import result
from tests.test_hpc_whole_request import hub_for
from tests.test_workspace_boundary import _settings

PRECEDENT = [{"paper_id": "synthetic", "text_file": "/stored/paper.md", "text_sha256": "a" * 64}]
HPC = {"mode": "hpc", "runner_id": "cluster"}


def test_precedent_texts_do_not_refuse_hpc_but_request_inputs_still_do():
    assert execution_policy.input_errors({"precedent_texts": PRECEDENT}, HPC) == []
    assert execution_policy.input_errors({"project_dirs": ["/data"]}, HPC)
    assert execution_policy.input_errors({"reference_dirs": ["/refs"]}, HPC)
    assert "precedent full texts are likewise unsupported" not in execution_policy.plan_rule(HPC)


@pytest.mark.parametrize("mode", ["hpc", "local"])
async def test_hpc_step_is_dispatched_without_precedent_texts_local_keeps_them(mode):
    seen = []

    async def dispatch(task):
        seen.append(dict(task.meta))
        return result(task, text="done")

    hub = hub_for(dispatch)
    hub.requests["r"]["execution_location"] = {**HPC, "mode": mode}
    task = Task(agent_id="worker", request_id="r", prompt="read stored paper",
                meta={"kind": "step", "step_id": "s1", "precedent_texts": PRECEDENT})
    assert (await Orchestrator(hub).run_step(task)).ok
    assert len(seen) == 1
    if mode == "hpc":
        assert "precedent_texts" not in seen[0]
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
