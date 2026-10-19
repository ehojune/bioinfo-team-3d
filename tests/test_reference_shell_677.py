"""Classify literal reference/workspace commands; never execute submitted commands."""
import os

import pytest

from labhq.models import Engine, Task
from labhq.policy import evaluate_tool
from labhq.workspace_commands import workspace_command
from tests.test_tool_card_explain import layout, gate
from tests.test_private_paths import _capture_runner, _runner_settings


@pytest.mark.parametrize('command', [
    'cd "{refs}" && cat guide.md',
    'cd "{refs}" && grep -c line guide.md && wc -l guide.md',
    'cd "{refs}" && sha256sum guide.md',
    'cd "{refs}" && sha256sum *.zip',
    'cd "{refs}" && od -An -tx1 -N 40 guide.md',
])
def test_literal_reference_reads(layout, command):
    refs = layout[3]
    (refs / 'guide.md').write_text('line\n', encoding='utf-8')
    (refs / 'data.zip').write_bytes(b'fixture bytes; not an archive run')
    assert gate(layout, 'Bash', {'command': command.format(refs=refs.as_posix())}).action == 'allow'


@pytest.mark.parametrize('absolute', [False, True])
def test_powershell_directory_and_version(layout, monkeypatch, absolute):
    # Trust is separately tested below; never inspect or execute a host Python here.
    monkeypatch.setattr('labhq.workspace_commands._resolve_executable',
                        lambda *args: (True, 'fixture trusted executable'))
    target = str(layout[2] / 'outputs') if absolute else 'outputs'
    command = f'New-Item -ItemType Directory -Force "{target}" | Out-Null; python --version'
    assert gate(layout, 'PowerShell', {'command': command}).action == 'allow'


@pytest.mark.parametrize('command', [
    'cd "{refs}" && touch new.txt',
    'cd "{refs}" && cat guide.md > "{work}/out.txt"',
    'cd "{refs}" && sha256sum -c SHA256SUMS',
    'cd "{refs}" && wc --files0-from=guide.md',
    'cd "{refs}" && od --output-duplicates --bad guide.md',
    'cd "{refs}" && cat auth.json',
    'cd "{refs}" && cat ../private/diary.md',
    'cd "{refs}" && cat guide.md; cat guide.md',
    'python -c "import zipfile; zipfile.ZipFile(\'guide.zip\')"',
    "python - <<'EOF'\nprint('fixture')\nEOF",
])
def test_indirect_or_unsafe_reads_still_ask(layout, command):
    assert gate(layout, 'Bash', {'command': command.format(
        refs=layout[3].as_posix(), work=layout[2].as_posix())}).action != 'allow'


@pytest.mark.parametrize('suffix', ['-Value x', '-ItemType SymbolicLink', '-Force -Force'])
def test_new_item_unknown_arguments_stay_closed(layout, suffix):
    command = f'New-Item -ItemType Directory outputs {suffix} | Out-Null'
    assert gate(layout, 'PowerShell', {'command': command}).action != 'allow'


def test_new_commands_keep_executable_and_zone_checks(layout, monkeypatch):
    work, refs = layout[2:]
    monkeypatch.setattr('labhq.workspace_commands._resolve_executable',
                        lambda *args: (False, 'fixture writable executable'))
    assert not workspace_command('python --version', str(work), powershell=True)
    monkeypatch.setattr('labhq.workspace_commands._resolve_executable',
                        lambda *args: (True, 'fixture trusted executable'))
    assert not workspace_command(f'wc -l "{refs}/guide.md"', str(work),
                                 read_roots=[str(refs)], forbidden=[str(refs)])


@pytest.mark.parametrize('command', ['wc -l auth.json', 'sha256sum .env', 'od -N 40 token.json'])
def test_new_reads_never_open_credentials(layout, command):
    assert gate(layout, 'Bash', {'command': command}).action != 'allow'


def test_new_item_external_and_mock_link_targets_stay_closed(layout, monkeypatch):
    work, refs = layout[2:]
    assert gate(layout, 'PowerShell', {'command':
        f'New-Item -ItemType Directory "{refs}/outputs" | Out-Null'}).action != 'allow'
    from labhq import workspace_commands as commands
    real = commands.resolved_read_path
    monkeypatch.setattr(commands, 'resolved_read_path', lambda path:
        str(refs / 'escaped') if str(path).endswith('alias') else real(path))
    assert gate(layout, 'PowerShell', {'command':
        'New-Item -ItemType Directory alias | Out-Null'}).action != 'allow'


@pytest.mark.parametrize('operand', ['outputs,../other', '@args', '@paths'])
def test_powershell_new_item_never_expands_array_or_splat(layout, operand):
    assert gate(layout, 'PowerShell', {'command':
        f'New-Item -ItemType Directory {operand} | Out-Null'}).action != 'allow'


@pytest.mark.parametrize('tool,command', [
    ('Bash', 'wc -l guide.md'),
    ('PowerShell', 'New-Item -ItemType Directory outputs | Out-Null'),
])
def test_read_only_task_still_denies_shell(layout, tool, command):
    cfg, _, work, _ = layout
    assert evaluate_tool(tool, {'command': command}, cfg.policy, workdir=str(work),
        allowed_roots=[str(work)], private_open_reads=[str(work)],
        private_enabled=True, read_only=True).action == 'deny'


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', [Engine.claude_code, Engine.codex])
async def test_runner_passes_only_validated_reference_reads(tmp_path, monkeypatch, engine):
    settings = _runner_settings(tmp_path, [str(tmp_path / 'private')])
    refs, outside = tmp_path / 'refs', tmp_path / 'outside'
    refused, unused = tmp_path / 'refused', tmp_path / 'unused'
    for path in (refs, outside, refused, unused):
        path.mkdir(exist_ok=True)
    settings.runner.reference_roots = [str(refs), str(refused), str(unused)]
    # Mock the scanner's refusal without creating a native link/mount or reading operational files.
    monkeypatch.setattr('labhq.runner.daemon.scan_reference_dir', lambda path, *args:
                        'fixture scan refusal' if path == refused else None)
    runner, seen = _capture_runner(settings, monkeypatch, engine)
    result = await runner.run_task(Task(id='t', agent_id='worker', request_id='r', prompt='fixture',
        meta={'reference_dirs': [str(refs), str(outside), str(refused)]}))
    assert result.ok, result.error
    ctx = seen['ctx']
    assert str(refs) in ctx.private_open_reads
    assert str(outside) not in ctx.private_open_reads
    assert str(refused) not in ctx.private_open_reads
    assert str(unused) not in ctx.private_open_reads
    assert ctx.env['LABHQ_PRIVATE_OPEN_READS'].split(os.pathsep) == ctx.private_open_reads
    # The read grant is never a writable extra root.
    assert str(refs) not in ctx.env['LABHQ_EXTRA_ROOTS'].split(os.pathsep)
    monkeypatch.setattr('labhq.workspace_commands._resolve_executable',
                        lambda *args: (True, 'fixture trusted executable'))
    def decision(command):
        return evaluate_tool('Bash', {'command': command}, settings.policy,
            allowed_roots=[str(ctx.workdir)], workdir=str(ctx.workdir),
            private_paths=ctx.private_paths, private_open_reads=ctx.private_open_reads,
            private_enabled=ctx.private_enabled)
    assert decision(f'cd "{refs}" && cat guide.md').action == 'allow'
    assert decision(f'touch "{refs}/new.txt"').action != 'allow'
    assert decision(f'cat "{outside}/guide.md"').action != 'allow'
    assert decision(f'cat "{refused}/guide.md"').action != 'allow'
