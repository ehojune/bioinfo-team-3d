"""Windows Codex work-folder protection, with mock adapters."""
import pytest

from labhq.models import Engine, Task
from labhq.runner import daemon
from labhq.runner.daemon import _codex_sibling_denies
from labhq.runner.workspace import TaskWorkspace
from tests.support.time_proxy import RuntimeProxy
from tests.test_private_paths import _capture_runner, _runner_settings
from tests.test_step_inputs_665 import run_step


def test_snapshot_excludes_own_and_active_codex_folders(tmp_path):
    day = tmp_path / '2026-11-13'
    own, active, sibling = [day / name for name in ('own', 'active', 'sibling')]
    older = tmp_path / '2026-11-12' / 'older'
    for folder in (own, active, sibling, older):
        folder.mkdir(parents=True)
    assert set(_codex_sibling_denies(tmp_path, own, [active])) == {str(sibling), str(older)}


@pytest.mark.asyncio
async def test_windows_codex_step_keeps_own_folder_and_denies_sibling(tmp_path, monkeypatch):
    runner, ctx, sibling = await run_step(tmp_path, monkeypatch, Engine.codex, platform='win32')
    assert str(runner.ws_root.resolve()) not in ctx.private_paths
    assert str(sibling) in ctx.private_paths
    assert str(ctx.workdir / 'inputs') not in ctx.private_paths
    assert ctx.read_only_inputs
    assert str(ctx.workdir) in ctx.private_open_reads


@pytest.mark.asyncio
@pytest.mark.parametrize('platform,engine', [('linux', Engine.codex), ('win32', Engine.claude_code)])
async def test_other_platform_or_engine_keeps_root_deny(tmp_path, monkeypatch, platform, engine):
    runner, ctx, _ = await run_step(tmp_path, monkeypatch, engine, platform=platform)
    assert str(runner.ws_root.resolve()) in ctx.private_paths
    assert str(ctx.workdir / "inputs") in ctx.private_paths


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['empty', 'existing', 'precedent', 'paper'])
async def test_empty_and_other_input_sources_use_scoped_profile(tmp_path, monkeypatch, case):
    monkeypatch.setattr(daemon, 'sys', RuntimeProxy(daemon.sys, platform='win32'))
    init = TaskWorkspace.__init__
    def prepare(self, *args, **kwargs):
        init(self, *args, **kwargs)
        if case != 'empty':
            (self.dir / 'inputs' / 'fixture').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(TaskWorkspace, '__init__', prepare)
    meta = {'kind': 'step', 'upstream_artifacts': {}}
    if case == 'precedent':
        meta['precedent_texts'] = [{'fixture': True}]
        monkeypatch.setattr(daemon, 'stage_precedent_texts',
                            lambda ws, *args: ([str(ws.dir / 'inputs' / 'fixture')], {}, []))
    if case == 'paper':
        meta['paper_skills'] = [{'fixture': True}]
        monkeypatch.setattr(TaskWorkspace, 'install_paper_skills', lambda *args: (None, []))
        monkeypatch.setattr(TaskWorkspace, 'paper_skill_read_files',
                            lambda ws: [str(ws.dir / 'inputs' / 'fixture')])
        monkeypatch.setattr(TaskWorkspace, 'record_paper_skill_usage', lambda *args: None)
        monkeypatch.setattr(TaskWorkspace, 'clear_paper_skills', lambda *args: None)
    settings = _runner_settings(tmp_path, None)
    settings.recruit.enabled = True
    runner, seen = _capture_runner(settings, monkeypatch, Engine.codex)
    result = await runner.run_task(Task(id='one', agent_id='worker', request_id='r', prompt='q', meta=meta))
    assert result.ok, result.error
    ctx = seen['ctx']
    assert ctx.read_only_inputs
    assert str(runner.ws_root.resolve()) not in ctx.private_paths
    assert str(ctx.workdir / 'inputs') not in ctx.private_paths
