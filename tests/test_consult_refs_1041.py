"""One wrong consultation ref leaves only that ref out; unsafe refs still refuse the request (#1043)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def make_runner(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    source = Path(settings.runner.workspace_root) / "source"
    (source / "folder").mkdir(parents=True)
    (source / "good.txt").write_text("fine", encoding="utf-8")
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    return runner, source, calls


async def consult(runner, source, refs):
    return await runner.run_task(Task(agent_id="cso", prompt="Read the refs", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": refs}))


@pytest.mark.parametrize("bad", ["outputs/not-yet-written.tsv", "표 (아직 없는 파일, 설명 문구)",
                                 "missing-dir/missing.txt"])
async def test_a_missing_ref_is_left_out_with_its_reason(tmp_path, monkeypatch, bad):
    runner, source, calls = make_runner(tmp_path, monkeypatch)
    try:
        result = await consult(runner, source, ["good.txt", bad])
        assert result.ok and len(calls) == 1
        prompt = calls[0].task.prompt
        assert "good.txt" in prompt and "참고 파일 제외: " + json.dumps(bad, ensure_ascii=False) in prompt
        assert any(p.read_text(encoding="utf-8") == "fine"
                   for p in (calls[0].workdir / "refs" / f"consult-{calls[0].task.id}").iterdir())
    finally:
        runner.store.close()


async def test_every_ref_left_out_still_runs_the_consultation(tmp_path, monkeypatch):
    runner, source, calls = make_runner(tmp_path, monkeypatch)
    try:
        result = await consult(runner, source, ["nope.txt"])
        assert result.ok and "참고 파일 제외" in calls[0].task.prompt
    finally:
        runner.store.close()


@pytest.mark.parametrize("unsafe", ["folder", "missing/D:/secret.txt", "../outside.txt", "/etc/passwd", "C:/x.txt", "a/../../b.txt"])
async def test_an_unsafe_ref_still_refuses_even_beside_a_good_one(tmp_path, monkeypatch, unsafe):
    runner, source, calls = make_runner(tmp_path, monkeypatch)
    try:
        result = await consult(runner, source, ["good.txt", unsafe])
        assert not result.ok and "consult refs" in (result.error or "") and calls == []
    finally:
        runner.store.close()


@pytest.mark.skipif(os.name == "nt", reason="file links need POSIX permissions")
async def test_a_linked_ref_still_refuses(tmp_path, monkeypatch):
    runner, source, calls = make_runner(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "link.txt").symlink_to(outside)
    try:
        result = await consult(runner, source, ["good.txt", "link.txt"])
        assert not result.ok and calls == []
    finally:
        runner.store.close()
