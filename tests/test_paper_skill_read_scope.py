"""Composed daemon/policy contract, with fake adapters and synthetic owned files only."""
import hashlib
import os

import pytest

from labhq.adapters import codex as codex_mod
from labhq.adapters.held_dir import HeldDir
from labhq.models import Engine, Task, TaskResult
from tests.test_paper_skill_attach import runner, deterministic_paper_io, build_task  # noqa: F401
from tests.test_ci807_research_regression import mock_native_boundaries  # noqa: F401
from tests.test_step_inputs_665 import source, gate


async def run_paper(runner, monkeypatch, engine, ancestor=False, damage=None):
    r, _ = runner
    built = await r.run_task(build_task())
    assert built.structured['status'] == 'ready'
    r.registry.agents['worker'].engine = engine
    r.s.engines.codex.windows_sandbox = 'elevated'
    r.s.policy.private_paths = [str(r.s.path(r.s.runner.state_dir))]
    row = {**built.structured, 'request_id': 'r', 'agent_id': 'worker', 'step_id': 's1'}
    ancestors, steps = {}, {}
    if ancestor:
        sid = 'paper_skills' if ancestor == 'collision' else 'fetch'
        folder, artifact = source(r.ws_root, step=sid)
        ancestors[sid], steps[sid] = artifact, str(folder)
    if damage:
        from labhq.runner.workspace import TaskWorkspace
        install = TaskWorkspace.install_paper_skills
        def damaged(ws, *args):
            out = install(ws, *args)
            receipt = ws._paper_skill_receipts[0]
            target = ws.dir / receipt['input_ref']
            if damage == 'changed':
                data = target.read_bytes()
                target.write_bytes(b'X' + data[1:])  # equal size: content hash, not just stat, must refuse
            elif damage == 'missing':
                target.unlink()
            return out
        monkeypatch.setattr(TaskWorkspace, 'install_paper_skills', damaged)
        if damage == 'link_swap':
            opened = HeldDir.open_read_file
            def refuse_link(held, name):
                if name.startswith('paper-') and name.endswith('.md'):
                    raise OSError('mock reparse/link swap at owned read')
                return opened(held, name)
            monkeypatch.setattr(HeldDir, 'open_read_file', refuse_link)
    return r, row, ancestors, steps


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', [Engine.claude_code, Engine.codex])
@pytest.mark.parametrize('ancestor', [False, True])
async def test_verified_paper_read_composes_with_dag_protection_and_native_write_denial(runner, monkeypatch, engine, ancestor):
    r, row, ancestors, steps = await run_paper(runner, monkeypatch, engine, ancestor)
    seen = []
    class Adapter:
        async def run(self, ctx):
            seen.append(ctx)
            name = 'paper-' + hashlib.sha256(row['paper_id'].encode()).hexdigest()[:24]
            target = ctx.workdir / 'inputs/paper_skills' / (name + '.md')
            assert target.is_file()
            assert gate(ctx, 'Read', {'file_path': str(target)}).action == 'allow'
            assert str(target) in ctx.private_open_reads
            from labhq import private_paths
            assert str(target) in ctx.env[private_paths.OPEN_READS_ENV_VAR].split(os.pathsep)
            assert str(target.parent) not in ctx.private_open_reads
            assert str(target.parent.parent) not in ctx.private_open_reads
            assert gate(ctx, 'Write', {'file_path': str(target), 'content': 'x'}).action == 'deny'
            assert gate(ctx, 'Edit', {'file_path': str(target), 'old_string': 'x', 'new_string': 'y'}).action == 'deny'
            assert gate(ctx, 'Read', {'file_path': str(target.parent / 'unrelated.md')}).action == 'deny'
            private = r.s.path(r.s.runner.state_dir) / 'auth.json'
            assert gate(ctx, 'Read', {'file_path': str(private)}).action == 'deny'
            # Model a post-grant link/junction escape without requiring native link privileges.
            resolve = private_paths.resolved_read_path
            with monkeypatch.context() as scoped:
                scoped.setattr(private_paths, 'resolved_read_path',
                               lambda path: str(private) if path == str(target) else resolve(path))
                assert gate(ctx, 'Read', {'file_path': str(target)}).action == 'deny'
            assert gate(ctx, 'Bash', {'command': 'echo x > inputs/paper_skills/unrelated.md'}).action != 'allow'
            if ancestor:
                prior = ctx.workdir / 'inputs/fetch/table.tsv'
                assert gate(ctx, 'Read', {'file_path': str(prior)}).action == 'allow'
                assert gate(ctx, 'Write', {'file_path': str(prior), 'content': 'x'}).action == 'deny'
                assert str(prior.parent) in ctx.read_only_inputs
            assert str(target) in ctx.read_only_inputs
            if engine == Engine.codex:
                monkeypatch.setattr(codex_mod, '_is_windows', lambda: True)
                profile = next(v for v in codex_mod.CodexAdapter(ctx.settings).sandbox_flags(ctx)
                               if v.startswith('permissions.labhq_staff='))
                assert 'extends = ":read-only"' in profile
                assert f'{codex_mod._toml(str(target))} = "read"' in profile
                assert f'{codex_mod._toml(str(target.parent.parent))} = "deny"' in profile
                for folder in (target, target.parent, target.parent.parent, ctx.workdir):
                    assert f'{codex_mod._toml(str(folder))} = "write"' not in profile
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text='no attribution')
    monkeypatch.setattr('labhq.runner.daemon.get_adapter', lambda *args: Adapter())
    result = await r.run_task(Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'kind': 'step', 'step_id': 's1', 'paper_skills': [row], 'upstream_artifacts': ancestors,
        'upstream_steps': steps}))
    assert result.ok, result.error
    assert len(seen) == 1
    assert result.provenance['runs'][result.task_id]['paper_skills'][0]['called'] is False


@pytest.mark.asyncio
async def test_paper_namespace_never_overwrites_or_regrants_the_ancestor_namespace(runner, monkeypatch):
    r, row, ancestors, steps = await run_paper(runner, monkeypatch, Engine.codex, ancestor='collision')
    class Adapter:
        async def run(self, ctx):
            prior = ctx.workdir / 'inputs/paper_skills/table.tsv'
            assert prior.read_bytes() == b'gene\nfixture\n'
            assert gate(ctx, 'Read', {'file_path': str(prior)}).action == 'allow'
            assert gate(ctx, 'Write', {'file_path': str(prior), 'content': 'x'}).action == 'deny'
            assert not list(prior.parent.glob('paper-*.md'))
            receipt = r.workspaces[ctx.task.id]._paper_skill_receipts[0]
            assert receipt['reason'] == 'input_namespace_conflict' and receipt['called'] is False
            assert str(prior.parent) in ctx.read_only_inputs
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text='no attribution')
    monkeypatch.setattr('labhq.runner.daemon.get_adapter', lambda *args: Adapter())
    result = await r.run_task(Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'kind': 'step', 'step_id': 's1', 'paper_skills': [row], 'upstream_artifacts': ancestors,
        'upstream_steps': steps}))
    assert result.ok, result.error
    assert result.provenance['runs'][result.task_id]['paper_skills'][0]['called'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['changed', 'missing', 'link_swap'])
async def test_unproven_document_is_not_regranted_at_policy_composition(runner, monkeypatch, damage):
    r, row, _, _ = await run_paper(runner, monkeypatch, Engine.claude_code, damage=damage)
    class Adapter:
        async def run(self, ctx):
            name = 'paper-' + hashlib.sha256(row['paper_id'].encode()).hexdigest()[:24]
            target = ctx.workdir / 'inputs/paper_skills' / (name + '.md')
            assert str(target) not in ctx.private_open_reads
            assert gate(ctx, 'Read', {'file_path': str(target)}).action == 'deny'
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text='no attribution')
    monkeypatch.setattr('labhq.runner.daemon.get_adapter', lambda *args: Adapter())
    result = await r.run_task(Task(agent_id='worker', request_id='r', prompt='fixture', meta={
        'kind': 'step', 'step_id': 's1', 'paper_skills': [row], 'upstream_artifacts': {}}))
    assert result.ok, result.error
