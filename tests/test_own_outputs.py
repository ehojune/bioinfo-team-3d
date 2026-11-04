"""Read-only staff may save their own answer without changing the evidence under review."""
import json
import os
from pathlib import Path

import pytest

from labhq.models import Engine, Task
from labhq.settings import DataZone
from test_read_only_profile import _runner, _staff, _wire


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["consult", "followup", "review", "role", "plan_role", "tools_role"])
@pytest.mark.parametrize("engine", [Engine.claude_code, Engine.codex])
async def test_read_only_staff_can_save_own_answer(tmp_path, monkeypatch, kind, engine):
    runner, seen = _runner(tmp_path), []
    staff = _staff(tmp_path, engine)
    project = tmp_path / "project"
    project.mkdir()
    staff = staff.model_copy(update={"project_dirs": [str(project)]})
    zone = tmp_path / "controlled"
    zone.mkdir()
    runner.s.policy.data_zones = [DataZone(path=str(zone), level="restricted")]
    if kind == "role":
        staff = staff.model_copy(update={"sandbox": "read-only"})
    elif kind == "plan_role":
        staff = staff.model_copy(update={"permission_mode": "plan"})
    elif kind == "tools_role":
        staff = staff.model_copy(update={"builtin_tools": "Read,Glob,Grep"})

    def write(ctx):
        (ctx.workdir / "outputs" / "review.json").write_text('{"verdict":"accept"}', encoding="utf-8")
        (ctx.workdir / ".tmp").mkdir(exist_ok=True)
        (ctx.workdir / ".tmp" / "notes.txt").write_text("scratch", encoding="utf-8")

    _wire(runner, monkeypatch, staff, seen, write)
    result = await runner.run_task(Task(agent_id="worker", prompt="review", meta={
        "kind": kind, "outputs": ["outputs/review.json"]}))
    assert result.ok, result.error
    assert "outputs/review.json" in result.outputs
    ctx, cmd = seen[0]
    assert ctx.read_only
    if engine == Engine.claude_code:
        assert cmd[cmd.index("--permission-mode") + 1] == "default"
        assert set(cmd[cmd.index("--tools") + 1].split(",")) == {"Read", "Glob", "Grep", "Write", "Edit"}
        assert "--permission-prompt-tool" in cmd
        assert [s.name for s in ctx.mcp_servers] == ["labhq_approval"]
        assert "--allowedTools" not in cmd  # every write reaches the path gate
    else:
        assert ctx.agent.sandbox == "workspace-write"
        assert "--add-dir" not in cmd and "-s" not in cmd
        config = next(arg for arg in cmd if arg.startswith("permissions.labhq_staff="))
        assert 'extends = ":read-only"' in config  # no implicit cwd/global temp write
        for name in ("outputs", ".tmp"):
            assert json.dumps(str(ctx.workdir.resolve() / name)) + ' = "write"' in config
        assert config.count('= "write"') == 2
        assert json.dumps(str(zone)) + ' = "deny"' in config
        assert 'approval_policy="never"' in cmd


@pytest.mark.asyncio
async def test_claude_gate_allows_only_own_plain_outputs(tmp_path, monkeypatch):
    from labhq.tools import approval_mcp as gate

    work = tmp_path / "task"
    for folder in ("outputs", ".tmp", "inputs"):
        (work / folder).mkdir(parents=True)
    restricted = work / "outputs" / "restricted"
    restricted.mkdir()
    settings = _runner(tmp_path).s
    settings.policy.data_zones = [DataZone(path=str(restricted), level="restricted")]
    monkeypatch.setattr(gate, "S", settings)
    monkeypatch.setattr(gate, "WORKDIR", str(work))
    monkeypatch.setattr(gate, "WORKDIR_INPUT", str(work))
    monkeypatch.setattr(gate, "EXTRA_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(gate, "READ_ONLY", True, raising=False)

    class NoBroker:
        def __init__(self, **kwargs):
            pytest.fail("scoped file writes must never ask the PI")
    monkeypatch.setattr(gate.httpx, "AsyncClient", NoBroker)
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        for path in ("outputs/review.json", ".tmp/notes.txt"):
            result = json.loads(await gate.approval_prompt(tool, {key: str(work / path)}))
            assert result["behavior"] == "allow", result
        for path in (work / "inputs/table.tsv", tmp_path / "upstream/outputs/table.tsv",
                     tmp_path / "other/outputs/review.json", tmp_path / "project/source.py",
                     work / "TASK.md", work / "outputs/../inputs/table.tsv", restricted / "raw.txt"):
            result = json.loads(await gate.approval_prompt(tool, {key: str(path)}))
            assert result["behavior"] == "deny", (path, result)
    assert json.loads(await gate.approval_prompt("Bash", {"command": "echo x > outputs/x"}))["behavior"] == "deny"
    # Both link directions matter: outputs -> inputs and inputs -> outputs.
    for source, target in ((work / "outputs/alias", work / "inputs"), (work / "inputs/alias", work / "outputs")):
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(source), str(target)], check=True, capture_output=True)
        else:
            source.symlink_to(target, target_is_directory=True)
        result = json.loads(await gate.approval_prompt("Write", {"file_path": str(source / "evidence.txt")}))
        assert result["behavior"] == "deny"
    original = work / "inputs/original.txt"
    original.write_text("evidence")
    os.link(original, work / "outputs/hardlink.txt")
    result = json.loads(await gate.approval_prompt("Write", {"file_path": str(work / "outputs/hardlink.txt")}))
    assert result["behavior"] == "deny"
    assert original.read_text() == "evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["review", "consult", "followup"])
async def test_review_backstop_still_rejects_changed_evidence(tmp_path, monkeypatch, kind):
    runner = _runner(tmp_path)
    upstream = runner.ws_root / "previous"
    (upstream / "outputs").mkdir(parents=True)
    evidence = upstream / "outputs/evidence.txt"
    evidence.write_text("original")
    def write(ctx):
        (ctx.workdir / "outputs/review.json").write_text("{}")
        evidence.write_text("changed")
    _wire(runner, monkeypatch, _staff(tmp_path), [], write)
    result = await runner.run_task(Task(agent_id="worker", prompt="review", meta={
        "kind": kind, "upstream_dirs": [str(upstream)]}))
    assert not result.ok and "evidence.txt" in result.error
    assert "review.json" not in result.error


@pytest.mark.asyncio
async def test_mock_reviewer_json_is_collected_from_own_outputs(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    runner.s.runner.mock_delay_s = 0
    monkeypatch.setattr(runner.registry, "get", lambda _: _staff(tmp_path, Engine.mock))
    result = await runner.run_task(Task(agent_id="worker", prompt="review", meta={
        "kind": "review", "outputs": ["outputs/review.json"]}))
    assert result.ok, result.error
    assert result.outputs == ["outputs/review.json"]
    assert json.loads((Path(result.workdir) / "outputs/review.json").read_text(encoding="utf-8")) == result.structured
