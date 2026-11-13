"""Windows Codex ancestor-input refusal; only mock adapters are used."""
import pytest

from labhq.models import Engine, Task
from labhq.runner import daemon
from tests.support.time_proxy import RuntimeProxy
from tests.test_private_paths import _capture_runner, _runner_settings
from tests.test_step_inputs_665 import run_step


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["step", "result_correction", "slot_repair"])
async def test_windows_codex_ancestor_is_refused_before_adapter_and_workspace(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(daemon, "sys", RuntimeProxy(daemon.sys, platform="win32"))
    runner, seen = _capture_runner(_runner_settings(tmp_path, None), monkeypatch, Engine.codex)
    task = Task(id="down", agent_id="worker", request_id="r", prompt="q",
                meta={"kind": kind, "upstream_artifacts": {"fetch": {}}})
    result = await runner.run_task(task)
    assert not result.ok and "Windows Codex" in result.error and "sandbox" in result.error
    assert "ctx" not in seen and not runner.ws_root.exists()
    assert any(e["type"] == "task.result" and e["data"]["error"] == result.error for e in runner.store.pending())


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,engine", [("linux", Engine.codex), ("win32", Engine.claude_code)])
async def test_other_platform_or_engine_keeps_ancestor_protection(tmp_path, monkeypatch, platform, engine):
    runner, ctx, _ = await run_step(tmp_path, monkeypatch, engine, platform=platform)
    assert daemon.sys.platform == platform
    assert str(runner.ws_root.resolve()) in ctx.private_paths
    assert str(ctx.workdir / "inputs") in ctx.private_paths
    assert str(ctx.workdir) in ctx.private_open_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{"kind": "step", "upstream_artifacts": {}}, {"kind": "direct"}])
async def test_windows_codex_without_ancestor_continues(tmp_path, monkeypatch, meta):
    monkeypatch.setattr(daemon, "sys", RuntimeProxy(daemon.sys, platform="win32"))
    runner, seen = _capture_runner(_runner_settings(tmp_path, None), monkeypatch, Engine.codex)
    result = await runner.run_task(Task(id="one", agent_id="worker", request_id="r", prompt="q", meta=meta))
    assert result.ok and "ctx" in seen
