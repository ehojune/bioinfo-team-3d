"""Runner regression for a result correction resumed after provider quota (#1524)."""

import json
from pathlib import Path

import pytest

from labhq.models import TaskResult
from labhq.runner.step_inputs import repair_outputs
from tests.test_observed_outputs import _runner, _task, _write


@pytest.mark.asyncio
@pytest.mark.parametrize("change_output", [False, True])
async def test_quota_retry_keeps_the_successful_correction_source(tmp_path, monkeypatch, change_output):
    path = "outputs/table.tsv"

    def write(workdir):
        _write(workdir, path, b"source\n")

    runner = _runner(tmp_path, monkeypatch, write)
    source = await runner.run_task(_task(tid="producer", outputs=[path]))
    assert source.ok, source.error
    assert source.outputs == [path]

    def quota_limited(ctx):
        return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, session_id="session",
                          error="You've hit your 5-hour limit")

    _runner(tmp_path, monkeypatch, lambda _workdir: None, reply=quota_limited)
    turn = dict(kind="result_correction", workdir=source.workdir, revision=0)
    paused = await runner.run_task(_task(tid="correction", parent_task=source.task_id, **turn))
    assert not paused.ok
    assert paused.error == "You've hit your 5-hour limit"
    assert paused.session_id == "session"

    def resume(workdir):
        if change_output:
            _write(workdir, path, b"changed\n")

    _runner(tmp_path, monkeypatch, resume)
    retry = _task(tid="resume", parent_task=paused.task_id, **turn)
    retry.resume_session_id = paused.session_id
    result = await runner.run_task(retry)
    if change_output:
        assert not result.ok
        assert result.error == "incomplete: result correction parent output is missing or changed"
    else:
        assert result.ok, result.error
        assert result.outputs == source.outputs
        assert result.output_sha256 == source.output_sha256
        manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["runs"]["correction"]["parent_task"] == source.task_id


@pytest.mark.parametrize(("field", "value"), [
    ("request_id", "another-request"), ("step_id", "another-step"), ("agent_id", "another-agent"),
    ("revision", 1), ("kind", "step"), ("ok", None), ("parent_task", "correction"),
    ("parent_task", "resume"), ("parent_task", None),
])
def test_correction_parent_chain_requires_matching_recorded_links(field, value):
    task = _task(tid="resume", kind="result_correction", parent_task="correction", revision=0)
    identity = dict(request_id=task.request_id, step_id="s1", agent_id=task.agent_id, revision=0)
    source = dict(identity, kind="step", ok=True, outputs=[], output_sha256={})
    failed = dict(identity, kind="result_correction", ok=False, parent_task="producer")
    failed[field] = value
    manifest = {"task": {"request_id": task.request_id}, "runs": {
        "producer": source, "correction": failed, "resume": source,
    }}
    with pytest.raises(ValueError, match="parent is not a successful turn of this step"):
        repair_outputs(manifest, task, [])
