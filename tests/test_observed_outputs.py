from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def _runner(tmp_path: Path, monkeypatch, write, *, hash_max_bytes=512 * 1024 * 1024,
            engine=Engine.claude_code, seen=None) -> Runner:
    settings = Settings()
    settings.runner.output_hash_max_bytes = hash_max_bytes
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            if seen is not None:
                seen["claude_settings"] = ctx.claude_settings
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
async def test_bytecode_caches_are_observed_but_not_unreported(tmp_path, monkeypatch):
    """v0.5 trial (#485): outputs/lib/__pycache__ came up as files the employee did not report."""
    def write(workdir):
        _write(workdir, "outputs/lib/pairde.py", b"x = 1\n")
        _write(workdir, "outputs/lib/__pycache__/pairde.cpython-312.pyc", b"\x00")
        _write(workdir, "outputs/__pycache__x/kept.txt", b"kept\n")

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_task(outputs=["lib/pairde.py"]))

    assert result.unreported_outputs == ["outputs/__pycache__x/kept.txt"]
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    observed = manifest["runs"][result.task_id]["observed_outputs"]
    assert "outputs/lib/__pycache__/pairde.cpython-312.pyc" in {row["path"] for row in observed}


@pytest.mark.asyncio
async def test_claude_post_tool_hook_ids_the_nearest_observed_output_write(tmp_path, monkeypatch):
    from labhq.hooks.tool_use import record_post_tool_use

    def write(workdir):
        env = {"LABHQ_WORKDIR": str(workdir), "LABHQ_TASK_ID": "task-o"}
        assert record_post_tool_use({"tool_use_id": "toolu_first", "tool_name": "Write"}, env, now_ns=1_000_000_000)
        assert record_post_tool_use({"tool_use_id": "toolu_last", "tool_name": "Edit"}, env, now_ns=2_000_000_000)
        _write(workdir, "outputs/table.tsv", b"last\n")
        os.utime(workdir / "outputs" / "table.tsv", ns=(2_100_000_000, 2_100_000_000))

    seen = {}
    result = await _runner(tmp_path, monkeypatch, write, seen=seen).run_task(_task())

    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    [row] = manifest["runs"][result.task_id]["observed_outputs"]
    assert row["tool_use_id"] == "toolu_last"
    assert result.output_tool_use_ids == {"outputs/table.tsv": "toolu_last"}
    [hook] = seen["claude_settings"]["hooks"]["PostToolUse"]
    assert hook["matcher"] == "Write|Edit|Bash|PowerShell" and "labhq.hooks.tool_use" in hook["hooks"][0]["command"]


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [[], [("toolu_a", 2_000_000_000), ("toolu_b", 2_000_000_000)]])
async def test_observed_output_leaves_tool_use_id_empty_without_a_unique_write(tmp_path, monkeypatch, calls):
    from labhq.hooks.tool_use import record_post_tool_use

    def write(workdir):
        env = {"LABHQ_WORKDIR": str(workdir), "LABHQ_TASK_ID": "task-o"}
        for tool_use_id, time_ns in calls:
            assert record_post_tool_use({"tool_use_id": tool_use_id, "tool_name": "Bash"}, env, now_ns=time_ns)
        _write(workdir, "outputs/table.tsv", b"ambiguous\n")
        os.utime(workdir / "outputs" / "table.tsv", ns=(2_100_000_000, 2_100_000_000))

    result = await _runner(tmp_path, monkeypatch, write).run_task(_task())

    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    [row] = manifest["runs"][result.task_id]["observed_outputs"]
    assert row["tool_use_id"] is None
    assert result.output_tool_use_ids == {}


@pytest.mark.asyncio
async def test_codex_observed_output_has_no_tool_use_id(tmp_path, monkeypatch):
    result = await _runner(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", b"codex\n"),
                           engine=Engine.codex).run_task(_task())
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    [row] = manifest["runs"][result.task_id]["observed_outputs"]
    assert row["tool_use_id"] is None and result.output_tool_use_ids == {}


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


@pytest.mark.asyncio
async def test_output_replaced_with_same_size_and_mtime_is_observed(tmp_path, monkeypatch):
    """`cp -p` or an atomic replace keeps size and mtime; the new inode still marks it changed (PR #334 review)."""
    workdir = tmp_path / "fixed"
    _write(workdir, "outputs/table.tsv", b"aaaa")
    kept = (workdir / "outputs" / "table.tsv").stat()

    def write(wd):
        _write(wd, "outputs/.table.tmp", b"bbbb")
        os.replace(wd / "outputs" / ".table.tmp", wd / "outputs" / "table.tsv")
        os.utime(wd / "outputs" / "table.tsv", ns=(kept.st_atime_ns, kept.st_mtime_ns))

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_task(outputs=["table.tsv"], workdir=workdir))

    manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
    observed = manifest["runs"][result.task_id]["observed_outputs"]
    assert [row["path"] for row in observed] == ["outputs/table.tsv"]
    assert observed[0]["sha256"] == _sha(b"bbbb") and result.output_sha256["outputs/table.tsv"] == _sha(b"bbbb")
    assert "ino" not in observed[0] and "ctime_ns" not in observed[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("cap, reason", [(5, "상한"), (1024, "바뀜")])
async def test_output_growing_while_hashed_gets_no_hash(tmp_path, monkeypatch, cap, reason):
    """A writer still appending after the CLI ended: the read stops at the cap, and a size that moved while the
    file was hashed leaves no hash (PR #334 review). The listing saw 4 bytes; 10 are there by the time it reads."""
    real_fstat = os.fstat

    def fstat(fd):
        # Every look before hashing starts sees 4 bytes (POSIX also checks the fd when it opens the file); the
        # check after hashing, made where `digest` exists, sees the 10 bytes the writer left.
        info = real_fstat(fd)
        if info.st_size == 10 and "digest" not in sys._getframe(1).f_locals:
            return type("Stat", (), {name: getattr(info, name) for name in
                                     ("st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns", "st_mode")} | {"st_size": 4})()
        return info

    monkeypatch.setattr("labhq.runner.workspace.os.fstat", fstat)
    runner = _runner(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/grow.log", b"0123456789"),
                     hash_max_bytes=cap)
    result = await runner.run_task(_task(outputs=["grow.log"]))

    assert result.output_sha256 == {}
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    row = manifest["runs"][result.task_id]["observed_outputs"][0]
    assert row["sha256"] is None and reason in row["reason"]
