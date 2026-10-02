from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def _runner(tmp_path: Path, monkeypatch, write, *, hash_max_bytes=512 * 1024 * 1024) -> Runner:
    settings = Settings()
    settings.runner.output_hash_max_bytes = hash_max_bytes
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            refused = ctx.before_spawn() if ctx.before_spawn else None
            if refused:
                return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=False, error=refused)
            write(Path(ctx.workdir))
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    return runner


def _task(tid="task-o", *, outputs=None, workdir=None) -> Task:
    return Task(id=tid, request_id="r1", agent_id="worker", prompt="write outputs",
                meta={"kind": "step", "step_id": "s1", "outputs": outputs or [],
                      **({"workdir": str(workdir)} if workdir else {})})


def _write(root: Path, relative: str, body: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_manifest_hashes_declared_and_unreported_fake_cli_outputs(tmp_path, monkeypatch):
    declared, extra = b"declared\n", b"extra\n"

    def write(workdir):
        _write(workdir, "outputs/declared.tsv", declared)
        _write(workdir, "outputs/extra.tsv", extra)

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_task(outputs=["declared.tsv"]))

    assert result.output_sha256 == {"outputs/declared.tsv": _sha(declared)}
    assert result.unreported_outputs == ["outputs/extra.tsv"]
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    observed = {row["path"]: row for row in manifest["runs"][result.task_id]["observed_outputs"]}
    assert observed["outputs/declared.tsv"]["sha256"] == _sha(declared)
    assert observed["outputs/extra.tsv"]["sha256"] == _sha(extra)
    assert all(row["task_id"] == result.task_id and row["agent_id"] == "worker" for row in observed.values())
    warnings = [event["data"].get("text", "") for event in runner.store.pending()
                if event["type"] == "agent.log" and event["data"].get("level") == "warn"]
    assert any("outputs/extra.tsv" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_unchanged_preexisting_output_is_hashed_but_not_observed(tmp_path, monkeypatch):
    workdir = tmp_path / "fixed"
    old = b"already here"
    _write(workdir, "outputs/old.tsv", old)
    runner = _runner(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/new.tsv", b"new"))

    result = await runner.run_task(_task(outputs=["old.tsv", "new.tsv"], workdir=workdir))

    assert result.output_sha256["outputs/old.tsv"] == _sha(old)
    manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
    observed = manifest["runs"][result.task_id]["observed_outputs"]
    assert [row["path"] for row in observed] == ["outputs/new.tsv"]


@pytest.mark.asyncio
async def test_output_over_hash_limit_has_null_hash_and_reason(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/large.bin", b"1234"),
                     hash_max_bytes=3)
    result = await runner.run_task(_task(outputs=["large.bin"]))

    assert result.output_sha256 == {}
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    row = manifest["runs"][result.task_id]["observed_outputs"][0]
    assert row["path"] == "outputs/large.bin" and row["sha256"] is None
    assert "3" in row["reason"]


@pytest.mark.asyncio
async def test_output_link_is_recorded_without_following_target(tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    def write(workdir):
        try:
            os.symlink(outside, workdir / "outputs" / "alias.txt")
        except (OSError, NotImplementedError):
            pytest.skip("this account cannot create a file symlink")

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_task())
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    row = manifest["runs"][result.task_id]["observed_outputs"][0]
    assert row["path"] == "outputs/alias.txt" and row["link"] is True and row["sha256"] is None
    assert "secret" not in json.dumps(row)
