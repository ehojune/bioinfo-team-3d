"""Windows Codex work-folder protection, with mock adapters."""
import asyncio
from pathlib import Path
import pytest

from labhq.models import Engine, Task
from labhq.adapters import codex as codex_mod
from labhq.runner import daemon
from labhq.runner.daemon import _codex_sibling_denies
from labhq.runner.workspace import TaskWorkspace
from tests.support.time_proxy import RuntimeProxy
from tests.test_private_paths import _capture_runner, _runner_settings
from tests.test_step_inputs_665 import gate, run_step


def test_snapshot_excludes_own_and_active_codex_folders(tmp_path):
    day = tmp_path / '2026-11-13'
    own, active, sibling = [day / name for name in ('own', 'active', 'sibling')]
    older = tmp_path / '2026-11-12' / 'older'
    for folder in (own, active, sibling, older):
        folder.mkdir(parents=True)
    assert set(_codex_sibling_denies(tmp_path, own, [active])) == {str(sibling), str(older)}


def test_nested_workspaces_keep_inactive_siblings_denied(tmp_path):
    own = tmp_path / 'hpc' / 'r1' / 'steps' / 's1'
    active = tmp_path / 'hpc' / 'r1' / 'steps' / 's2'
    inactive = tmp_path / 'hpc' / 'r1' / 'steps' / 's3'
    other = tmp_path / 'hpc' / 'r2' / 'steps' / 's1'
    for folder in (own, active, inactive, other):
        folder.mkdir(parents=True)
    denies = [Path(p) for p in _codex_sibling_denies(tmp_path, own, [active])]
    assert all(not any(folder == p or folder.is_relative_to(p) for p in denies) for folder in (own, active))
    assert all(any(folder == p or folder.is_relative_to(p) for p in denies) for folder in (inactive, other))


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
async def test_preparing_folder_registered_before_creation_and_cleaned(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(daemon, 'sys', RuntimeProxy(daemon.sys, platform='win32'))
    settings = _runner_settings(tmp_path, None)
    settings.recruit.enabled = True
    runner, _ = _capture_runner(settings, monkeypatch, Engine.codex)
    entered, release = asyncio.Event(), asyncio.Event()
    original = daemon._settled_thread

    async def paused(fn, *args, **kwargs):
        if fn is TaskWorkspace:
            entered.set()
            await release.wait()
            if outcome == 'failure':
                raise RuntimeError('fixture failure')
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(daemon, '_settled_thread', paused)
    task = asyncio.create_task(runner.run_task(Task(id='one', agent_id='worker', request_id='r', prompt='q',
                                                   meta={'kind': 'step', 'upstream_artifacts': {}})))
    await asyncio.wait_for(entered.wait(), 10)
    preparing = list(runner._preparing_codex_workdirs.values())
    assert len(preparing) == 1 and not preparing[0].exists()
    preparing[0].mkdir(parents=True)
    sibling = preparing[0].parent / 'sibling'
    sibling.mkdir()
    assert str(preparing[0]) not in _codex_sibling_denies(runner.ws_root, sibling, preparing)
    if outcome == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif outcome == 'failure':
        release.set()
        with pytest.raises(RuntimeError, match='fixture failure'):
            await task
    else:
        release.set()
        result = await task
        assert result.ok, result.error
    assert not runner._preparing_codex_workdirs


@pytest.mark.asyncio
async def test_windows_codex_step_keeps_own_folder_and_denies_sibling(tmp_path, monkeypatch):
    runner, ctx, sibling = await run_step(tmp_path, monkeypatch, Engine.codex, platform='win32')
    assert str(runner.ws_root.resolve()) not in ctx.private_paths
    assert str(sibling) in ctx.private_paths
    assert str(ctx.workdir / 'inputs') in ctx.private_paths
    assert str(ctx.workdir / 'inputs') not in ctx.native_deny_read_paths
    assert str(sibling) in ctx.native_deny_read_paths
    target = ctx.workdir / 'inputs' / 'fetch' / 'table.tsv'
    assert gate(ctx, 'Read', {'file_path': str(target)}).action == 'allow'
    assert gate(ctx, 'Write', {'file_path': str(target), 'content': 'x'}).action == 'deny'
    assert gate(ctx, 'Edit', {'file_path': str(target), 'old_string': 'x', 'new_string': 'y'}).action == 'deny'
    monkeypatch.setattr(codex_mod, '_is_windows', lambda: True)
    ctx.settings.engines.codex.windows_sandbox = 'elevated'
    profile = next(v for v in codex_mod.CodexAdapter(ctx.settings).sandbox_flags(ctx)
                   if v.startswith('permissions.labhq_staff='))
    assert 'extends = ":read-only"' in profile
    assert f'{codex_mod._toml(str(ctx.workdir / "inputs"))} = "deny"' not in profile
    assert f'{codex_mod._toml(str(sibling))} = "deny"' in profile
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
    assert str(ctx.workdir / 'inputs') in ctx.private_paths
    assert str(ctx.workdir / 'inputs') not in ctx.native_deny_read_paths


@pytest.mark.asyncio
async def test_empty_environment_producer_keeps_workspace_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, 'sys', RuntimeProxy(daemon.sys, platform='win32'))
    monkeypatch.setattr(codex_mod, '_is_windows', lambda: True)
    settings = _runner_settings(tmp_path, None)
    settings.recruit.enabled = True
    settings.engines.codex.windows_sandbox = 'elevated'
    settings.engines.codex.isolate_user_config = True
    runner, seen = _capture_runner(settings, monkeypatch, Engine.codex)
    sibling = runner.ws_root / '2026-01-01' / 'sibling'
    sibling.mkdir(parents=True)
    task = Task(id='one', agent_id='worker', request_id='r', prompt='q',
                meta={'kind': 'step', 'upstream_artifacts': {}, 'environment_step': True})
    result = await runner.run_task(task)
    assert result.ok, result.error
    ctx = seen['ctx']
    assert not ctx.read_only_inputs
    profile = next(v for v in codex_mod.CodexAdapter(settings).sandbox_flags(ctx)
                   if v.startswith('permissions.labhq_staff='))
    assert 'extends = ":workspace"' in profile
    assert f'{codex_mod._toml(str(sibling))} = "deny"' in profile
