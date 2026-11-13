"""Windows Codex work-folder protection, with mock adapters."""
import asyncio
import os
import subprocess
from pathlib import Path
import pytest

from labhq.models import Engine, Task
from labhq.adapters import codex as codex_mod
from labhq.adapters import base as base_mod
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
    assert set(_codex_sibling_denies(tmp_path, own, [active])) == {str(sibling), str(older.parent)}


def test_other_day_with_active_workspace_keeps_its_siblings_separate(tmp_path):
    own = tmp_path / '2026-11-13' / 'own'
    active = tmp_path / '2026-11-12' / 'active'
    sibling = active.parent / 'sibling'
    old = tmp_path / '2026-11-11' / 'old'
    for path in (own, active, sibling, old):
        path.mkdir(parents=True)
    assert set(_codex_sibling_denies(tmp_path, own, [active])) == {str(sibling), str(old.parent)}


@pytest.mark.parametrize('count', [151, 300])
def test_other_days_compress_without_losing_previous_folder_coverage(tmp_path, count):
    own = tmp_path / '2026-11-13' / 'own'
    own.mkdir(parents=True)
    siblings = [own.parent / f'task_{i:04d}' for i in range(count)]
    older = [tmp_path / day / f'task_{i:04d}' for day in ('2026-11-11', '2026-11-12')
             for i in range(300)]
    for path in (*siblings, *older):
        path.mkdir(parents=True, exist_ok=True)
    denies = {Path(path) for path in _codex_sibling_denies(tmp_path, own, [])}
    assert all(path in denies for path in siblings)
    assert all(any(path == deny or path.is_relative_to(deny) for deny in denies) for path in older)
    assert len(denies) == count + 2
    assert not any(own == deny or own.is_relative_to(deny) for deny in denies)


def test_windows_command_limit_counts_utf16_and_does_not_truncate(monkeypatch):
    monkeypatch.setattr(base_mod, 'command_line_limit', lambda: 32_000)
    monkeypatch.setattr(base_mod, 'argument_byte_limit', lambda: None)
    assert base_mod._command_too_long(['x' * 32_000]) is None
    assert base_mod._command_too_long(['x' * 31_999 + '\U0001f600']) == (
        32_001, 32_000, 'Windows command-line UTF-16 units')


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [151, 300])
async def test_serialized_codex_command_keeps_every_deny_once(tmp_path, monkeypatch, count):
    _, ctx, _ = await run_step(tmp_path, monkeypatch, Engine.codex, platform='win32')
    monkeypatch.setattr(codex_mod, '_is_windows', lambda: True)
    ctx.settings.engines.codex.windows_sandbox = 'elevated'
    ctx.settings.engines.codex.isolate_user_config = True
    paths = [f'D:\\runs\\2026-11-13\\task_{i:010d}_lit_scout' for i in range(count)]
    ctx.native_deny_read_paths = paths
    command = codex_mod.CodexAdapter(ctx.settings).build_command(ctx)
    profile = next(arg for arg in command if arg.startswith('permissions.labhq_staff='))
    assert all(profile.count(codex_mod._toml(path)) == 1 for path in paths)
    assert sum(arg.startswith('permissions.labhq_staff=') for arg in command) == 1
    assert len(subprocess.list2cmdline(command).encode('utf-16-le')) // 2 < 32_000


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


def test_snapshot_has_one_entry_per_inactive_sibling_and_keeps_literal_brackets(tmp_path):
    own = tmp_path / 'own'
    own.mkdir()
    siblings = [tmp_path / f'case-{i:03d}' for i in range(151)] + [tmp_path / 'literal[1]']
    for folder in siblings:
        folder.mkdir()
    assert set(_codex_sibling_denies(tmp_path, own, [])) == {str(p) for p in siblings}


def test_snapshot_cannot_follow_later_creation_or_preparing_transition(tmp_path):
    own, inactive = tmp_path / 'own', tmp_path / 'inactive'
    own.mkdir()
    inactive.mkdir()
    snapshot = set(_codex_sibling_denies(tmp_path, own, []))
    later = tmp_path / 'later'
    later.mkdir()
    # Captured paths cannot see a newly created sibling, or un-deny an old sibling
    # which becomes a preparing workspace after this snapshot.
    assert str(later) not in snapshot
    assert str(inactive) in snapshot
    refreshed = set(_codex_sibling_denies(tmp_path, own, [inactive]))
    assert str(later) in refreshed
    assert str(inactive) not in refreshed


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction fixture')
def test_snapshot_skips_root_junction_but_keeps_parent_with_nested_junction(tmp_path):
    own, active, inactive = (tmp_path / name for name in ('own', 'active', 'inactive'))
    for folder in (own, active, inactive):
        folder.mkdir()
    for link, target in ((tmp_path / 'root-link', own), (inactive / 'nested-link', active)):
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                       check=True, capture_output=True)
    assert (tmp_path / 'root-link').resolve() == own.resolve()
    assert (inactive / 'nested-link').resolve() == active.resolve()
    assert set(_codex_sibling_denies(tmp_path, own, [active])) == {str(inactive)}


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
async def test_other_day_ancestor_is_staged_outside_native_deny(tmp_path, monkeypatch):
    from tests import test_step_inputs_665 as input_tests

    original = input_tests.source
    def older_source(root):
        day = root / '2001-01-01'
        day.mkdir()
        return original(day)
    monkeypatch.setattr(input_tests, 'source', older_source)
    runner, ctx, upstream = await run_step(tmp_path, monkeypatch, Engine.codex, platform='win32')
    target = ctx.workdir / 'inputs' / 'fetch' / 'table.tsv'
    denies = [Path(path).resolve() for path in ctx.native_deny_read_paths]
    assert upstream.parent.resolve() in denies
    assert target.read_bytes() == b'gene\nfixture\n'
    assert not any(target.resolve() == path or target.resolve().is_relative_to(path) for path in denies)
    assert gate(ctx, 'Read', {'file_path': str(target)}).action == 'allow'
    assert gate(ctx, 'Write', {'file_path': str(target), 'content': 'x'}).action == 'deny'


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
    assert f'{codex_mod._toml(str(sibling.parent))} = "deny"' in profile
