"""PR #638: a private parent never grants access to sibling tasks or staff."""
import fnmatch
import json
import os

import pytest

from labhq import private_paths as pp
from labhq.adapters import codex as codex_mod
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.models import Engine, Task
from labhq.policy import claude_rule_path, evaluate_tool
from tests.test_private_paths import _capture_runner, _runner_settings, _link_dir


@pytest.mark.asyncio
@pytest.mark.parametrize('surface', ['claude', 'codex', 'shell'])
async def test_private_parent_keeps_other_task_output_closed(tmp_path, monkeypatch, surface):
    settings = _runner_settings(tmp_path, [str(tmp_path)])
    engine = Engine.codex if surface == 'codex' else Engine.claude_code
    runner, seen = _capture_runner(settings, monkeypatch, engine=engine)
    assert (await runner.run_task(Task(id='current', agent_id='worker', prompt='q'))).ok
    ctx = seen['ctx']
    own = ctx.workdir / 'outputs' / 'RESULT.md'
    other = ctx.workdir.parent / 'other-task' / 'outputs' / 'RESULT.md'
    for path in (own, other):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    if surface == 'claude':
        ctx.agent.tools = ['Read']
        cmd = ClaudeCodeAdapter(settings).build_command(ctx)
        rules = [r[5:-1] for r in cmd[cmd.index('--allowedTools') + 1:] if r.startswith('Read(')]
        allowed = lambda p: any(fnmatch.fnmatch('/' + claude_rule_path(str(p)), r) for r in rules)
    elif surface == 'codex':
        monkeypatch.setattr(codex_mod, '_is_windows', lambda: True)
        settings.engines.codex.windows_sandbox = 'elevated'
        flags = codex_mod.CodexAdapter(settings).sandbox_flags(ctx)
        profile = next(v for v in flags if v.startswith('permissions.labhq_staff='))
        # Decode TOML with the same inline-table syntax on Python 3.10 too.
        import re
        entries = re.findall(r'("(?:[^"\\]|\\.)*") = "(read|write|deny)"', profile)
        permissions = [(json.loads(p), mode) for p, mode in entries]
        def allowed(p):
            matches = [(len(root), mode) for root, mode in permissions if pp.inside_any(str(p), [root])]
            return bool(matches) and max(matches)[1] != 'deny'
    else:
        allowed = lambda p: pp.managed_shell_read(f'cat "{p}"', ctx.private_paths,
                                                 ctx.private_open_reads, str(ctx.workdir)) is True
    assert allowed(own)
    assert not allowed(other)
    assert evaluate_tool('Read', {'file_path': str(other)}, settings.policy,
                         workdir=str(ctx.workdir), private_paths=ctx.private_paths,
                         private_open_reads=ctx.private_open_reads).action == 'deny'


@pytest.mark.parametrize('work', ['runs/2026-10-15/current', 'talent/paper/build/current'])
@pytest.mark.parametrize('candidate', ['', '.', 'runs', 'runs/2026-10-15', 'talent',
                                       'talent/paper/build', 'staff', 'staff/claude',
                                       'staff/claude/projects', 'alias'])
def test_every_open_read_excludes_sibling_workspaces_and_staff(tmp_path, work, candidate):
    settings = _runner_settings(tmp_path, [str(tmp_path)])
    settings.runner.workspace_root = str(tmp_path / 'runs')
    settings.runner.talent_dir = str(tmp_path / 'talent')
    settings.engines.claude_code.env = {'CLAUDE_CONFIG_DIR': str(tmp_path / 'staff/claude')}
    settings.engines.codex.env = {'CODEX_HOME': str(tmp_path / 'staff/codex')}
    cwd = tmp_path / work
    sibling = cwd.parent / 'other-task'
    staff = tmp_path / 'staff/codex'
    for p in (cwd, sibling, staff, tmp_path / 'staff/claude/projects/other-task'):
        p.mkdir(parents=True, exist_ok=True)
    if candidate == 'alias':
        _link_dir(tmp_path / candidate, cwd.parent)
    candidates = [str(tmp_path / candidate)] if candidate else []
    found = pp.resolve_private_paths(settings, keep=[cwd, tmp_path / 'runs'], cwd=cwd,
                                     open_reads=candidates)
    assert str(tmp_path) in found.paths
    assert pp.open_read_allowed(str(cwd / 'guide.md'), found.open_reads)
    for root in found.open_reads:
        for other in (sibling, staff, tmp_path / 'staff/claude/projects/other-task'):
            assert not pp.inside_any(str(other), [root]), (root, other)
            assert not pp.inside_any(os.path.realpath(other), [os.path.realpath(root)])
