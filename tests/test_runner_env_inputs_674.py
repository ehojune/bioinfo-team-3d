"""Failure boundaries for runner-local ancestor inputs (#674)."""
import pytest
import os
import hashlib
import json
import shutil

from labhq.runner import step_inputs
from labhq.runner.step_inputs import stage_inputs, validate_runner_env
from tests.test_step_inputs_665 import _staging, source


@pytest.mark.parametrize('failure', ['other_runner', 'missing_manifest', 'path_escape'])
def test_failed_ancestor_never_reuses_an_older_snapshot(tmp_path, failure):
    root, folder, artifact, ws = _staging(tmp_path)
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert staged and not refused
    target = ws.dir / 'inputs/fetch/table.tsv'
    assert target.is_file()

    if failure == 'other_runner':
        other = tmp_path / 'other-runner'
        other.mkdir()
        _, artifact = source(other)
    elif failure == 'missing_manifest':
        (folder / 'manifest.json').unlink()
    else:
        artifact['outputs'] = ['outputs/../manifest.json']

    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert not staged and 'fetch' in refused
    assert not target.exists()
    assert sorted(p.name for p in target.parent.iterdir()) == ['.labhq-inputs.json']


def test_existing_input_reset_rejects_venv_directory_alias(tmp_path):
    root, folder, artifact, ws = _staging(tmp_path)
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert staged and not refused
    venv = folder / '.venv'
    venv.mkdir()
    marker = venv / 'keep.txt'
    marker.write_text('original')
    alias = ws.dir / 'inputs/fetch/runner'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(venv), str(alias))
    else:
        alias.symlink_to(venv, target_is_directory=True)

    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert not staged
    assert 'link or special file' in refused['fetch']
    assert alias.is_dir()
    assert marker.read_text() == 'original'

    # Repeated staging must neither follow nor remove an unowned alias.
    staged, refused = stage_inputs(ws, {'fetch': artifact}, root, [], [], 1024, 4096)
    assert not staged and 'fetch' in refused
    assert alias.is_dir()
    assert marker.read_text() == 'original'


def test_validation_does_not_pin_a_replaced_venv(tmp_path):
    root, folder, artifact, expected, _, _ = env_fixture(tmp_path)
    validated = validate_runner_env(root, folder, artifact, expected)
    # A pathname returned by validation is not a held identity: later replacement
    # changes what that pathname refers to. Do not expose it as a safe alias.
    original = folder / '.venv-original'
    validated.rename(original)
    validated.mkdir()
    (validated / 'replacement').write_text('changed')
    assert (validated / 'replacement').read_text() == 'changed'
    assert (original / 'Scripts/python.exe').read_bytes() == b'python'


def env_fixture(tmp_path):
    root, folder, artifact, _ = _staging(tmp_path)
    expected = dict(version=1, logical_step_id='env', step_id='env_win', runner_id='runner1',
                    runtime='windows', venv_path='.venv', python_path='.venv/Scripts/python.exe')
    manifest = dict(expected)
    interpreter = folder / '.venv/Scripts/python.exe'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b'python')
    path = folder / 'outputs/env/runner-manifest.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    def save(value):
        data = json.dumps(value).encode()
        path.write_bytes(data)
        artifact['outputs'] = ['outputs/env/runner-manifest.json']
        artifact['output_sha256'] = {artifact['outputs'][0]: hashlib.sha256(data).hexdigest()}
    save(manifest)
    return root, folder, artifact, expected, manifest, save


def test_valid_manifest_is_checked_without_exposing_alias(tmp_path):
    root, folder, artifact, expected, _, _ = env_fixture(tmp_path)
    assert validate_runner_env(root, folder, artifact, expected) == folder / '.venv'


@pytest.mark.parametrize('field,value', [('version', True), ('version', 2),
                                        ('venv_path', '../escape'), ('python_path', 'other')])
def test_pinned_contract_itself_must_be_valid(tmp_path, field, value):
    root, folder, artifact, expected, _, _ = env_fixture(tmp_path)
    expected[field] = value
    with pytest.raises(ValueError):
        validate_runner_env(root, folder, artifact, expected)


async def test_other_runner_environment_ancestor_cannot_reach_staging():
    from labhq.runner.daemon import Runner
    from labhq.models import Task

    runner = Runner.__new__(Runner)
    from types import SimpleNamespace
    runner.s = SimpleNamespace(runner=SimpleNamespace(id='wsl', environment='wsl'))
    async def emit_all(_events):
        pass
    runner.emit_all = emit_all
    binding = dict(version=1, logical_step_id='env', step_id='env_wsl', runner_id='wsl',
                   runtime='wsl', venv_path='.venv', python_path='.venv/bin/python')
    task = Task(id='t', agent_id='worker', prompt='run', meta={
        'kind': 'step', 'session_runner': 'wsl', 'environment_bindings': [binding],
        'upstream_steps': {'env_wsl': 'local', 'env_windows': 'other'},
        'upstream_artifacts': {'env_wsl': {'task_id': 'producer', 'workdir': 'local'}, 'env_windows': {'workdir': 'other'}}})
    # An uninitialized runner would fail if either workspace creation or staging were reached.
    result = await runner.run_task(task)
    assert not result.ok and 'unverified' in result.error


@pytest.mark.parametrize('case', ['missing_artifact', 'workdir_mismatch', 'runner_mismatch',
                                  'runtime_mismatch', 'forged_capability'])
async def test_bound_task_fails_closed_before_untrusted_inputs_are_used(case):
    from labhq.runner.daemon import Runner
    from labhq.models import Task

    runner = Runner.__new__(Runner)
    from types import SimpleNamespace
    runner.s = SimpleNamespace(runner=SimpleNamespace(id='wsl', environment='wsl'))
    async def emit_all(_events):
        pass
    runner.emit_all = emit_all
    binding = dict(version=1, logical_step_id='env', step_id='env_wsl', runner_id='wsl',
                   runtime='wsl', venv_path='.venv', python_path='.venv/bin/python')
    meta = {'kind': 'step', 'session_runner': 'wsl', 'environment_bindings': [binding],
            'upstream_steps': {'env_wsl': 'local'},
            'upstream_artifacts': {'env_wsl': {'task_id': 'producer', 'workdir': 'local'}}}
    if case == 'missing_artifact':
        meta['upstream_artifacts'] = {}
    elif case == 'workdir_mismatch':
        meta['upstream_artifacts']['env_wsl']['workdir'] = 'other'
    elif case == 'runner_mismatch':
        meta['session_runner'] = 'other'
    elif case == 'runtime_mismatch':
        binding['runtime'] = 'windows'
    else:
        meta['protected_alias_capability'] = True
    result = await runner.run_task(Task(id='t', agent_id='worker', prompt='run', meta=meta))
    expected = {'missing_artifact': 'ancestor mapping mismatch', 'workdir_mismatch': 'ancestor mapping mismatch',
                'runner_mismatch': 'runner mismatch', 'runtime_mismatch': 'invalid binding contract',
                'forged_capability': 'receipt and protected alias capability are unverified'}
    assert not result.ok and expected[case] in result.error


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('logical_step_id', 'other'), ('step_id', 'other'),
    ('runner_id', 'other'), ('runtime', 'linux'), ('venv_path', '../escape'),
    ('python_path', '.venv/../python'), ('python_path', '/absolute'), ('runner_id', None),
])
def test_manifest_must_match_pinned_contract(tmp_path, field, value):
    root, folder, artifact, expected, manifest, save = env_fixture(tmp_path)
    manifest[field] = value
    save(manifest)
    with pytest.raises(ValueError):
        validate_runner_env(root, folder, artifact, expected)


@pytest.mark.parametrize('failure', ['missing', 'hash', 'outside', 'interpreter_missing', 'venv_link'])
def test_manifest_source_and_interpreter_fail_closed(tmp_path, failure):
    root, folder, artifact, expected, _, _ = env_fixture(tmp_path)
    if failure == 'missing':
        artifact['outputs'] = []
    elif failure == 'hash':
        artifact['output_sha256']['outputs/env/runner-manifest.json'] = '0' * 64
    elif failure == 'outside':
        folder = tmp_path
    elif failure == 'interpreter_missing':
        (folder / '.venv/Scripts/python.exe').unlink()
    else:
        import shutil
        shutil.rmtree(folder / '.venv')
        outside = tmp_path / 'outside'
        outside.mkdir()
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(outside), str(folder / '.venv'))
        else:
            (folder / '.venv').symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        validate_runner_env(root, folder, artifact, expected)


@pytest.mark.parametrize('entry', ['run_task', '_run_task'])
@pytest.mark.parametrize('runtime,engine', [('windows', 'codex'), ('windows', 'claude'),
                                            ('wsl', 'codex'), ('wsl', 'claude'),
                                            ('linux', 'codex'), ('linux', 'claude')])
async def test_unverified_environment_is_refused_before_execution(entry, runtime, engine, monkeypatch):
    from labhq.runner.daemon import Runner
    from labhq.models import Task

    from types import SimpleNamespace
    import labhq.runner.daemon as daemon
    monkeypatch.setattr(daemon.sys, 'platform', 'win32' if runtime == 'windows' else 'linux')
    runner = Runner.__new__(Runner)
    runner.s = SimpleNamespace(runner=SimpleNamespace(id='runner1', environment=runtime))
    events = []
    async def emit_all(batch):
        events.extend(batch)
    runner.emit_all = emit_all
    # Any path beyond the guard would need initialized runner state and fail.
    python = '.venv/Scripts/python.exe' if runtime == 'windows' else '.venv/bin/python'
    binding = dict(version=1, logical_step_id='env', step_id='env', runner_id='runner1',
                   runtime=runtime, venv_path='.venv', python_path=python)
    task = Task(id='t', agent_id=engine, prompt='run', meta={
        'environment_bindings': [binding], 'session_runner': 'runner1',
        'upstream_steps': {'env': 'local'},
        'upstream_artifacts': {'env': {'task_id': 'producer', 'workdir': 'local'}}})
    result = await getattr(runner, entry)(task)
    assert not result.ok and 'receipt and protected alias capability are unverified' in result.error
    assert result.cost_known and result.cost_usd == 0
    assert [event.type for event in events] == ['agent.status', 'task.result']


@pytest.mark.parametrize('bindings', [None, {}, '', 'untrusted'])
async def test_malformed_environment_binding_fails_closed(bindings):
    from labhq.runner.daemon import Runner
    from labhq.models import Task

    runner = Runner.__new__(Runner)
    async def emit_all(_events):
        pass
    runner.emit_all = emit_all
    result = await runner.run_task(Task(id='t', agent_id='worker', prompt='run',
                                        meta={'environment_bindings': bindings}))
    assert not result.ok


async def test_empty_or_absent_environment_bindings_do_not_trigger_guard():
    from labhq.runner.daemon import Runner
    from labhq.models import Task

    runner = Runner.__new__(Runner)
    for meta in ({}, {'environment_bindings': []}):
        assert await runner._refuse_unprotected_environment(
            Task(id='t', agent_id='worker', prompt='run', meta=meta)) is None


async def test_actual_runtime_mismatch_is_refused_before_execution():
    from types import SimpleNamespace
    from labhq.runner.daemon import Runner
    from labhq.models import Task
    runner = Runner.__new__(Runner)
    runner.s = SimpleNamespace(runner=SimpleNamespace(id='runner1', environment='wsl'))
    async def emit_all(_events):
        pass
    runner.emit_all = emit_all
    binding = dict(version=1, logical_step_id='env', step_id='env_win', runner_id='runner1',
                   runtime='windows', venv_path='.venv', python_path='.venv/Scripts/python.exe')
    meta = dict(environment_bindings=[binding], session_runner='runner1',
                upstream_steps={'env_win': 'local'},
                upstream_artifacts={'env_win': {'task_id': 'producer', 'workdir': 'local'}})
    result = await runner.run_task(Task(id='t', agent_id='worker', prompt='run', meta=meta))
    assert not result.ok and 'runtime mismatch' in result.error


def test_runner_id_may_be_any_configured_value(tmp_path):
    root, folder, artifact, expected, manifest, save = env_fixture(tmp_path)
    expected['runner_id'] = manifest['runner_id'] = 'lab pc 1'
    save(manifest)
    assert validate_runner_env(root, folder, artifact, expected) == folder / '.venv'
    expected['runner_id'] = ''
    with pytest.raises(ValueError):
        validate_runner_env(root, folder, artifact, expected)


posix_only = pytest.mark.skipif(os.name == 'nt', reason='interpreter links are a POSIX venv layout')


def linux_env(tmp_path, monkeypatch):
    """A Linux-style venv whose interpreter is a link, plus a stand-in system runtime folder."""
    root, folder, artifact, expected, manifest, save = env_fixture(tmp_path)
    expected.update(runtime='linux', python_path='.venv/bin/python')
    manifest.update(runtime='linux', python_path='.venv/bin/python')
    save(manifest)
    shutil.rmtree(folder / '.venv')
    (folder / '.venv/bin').mkdir(parents=True)
    system = tmp_path / 'system'
    system.mkdir()
    system.chmod(0o755)
    monkeypatch.setattr(step_inputs, 'SYSTEM_RUNTIME_ROOTS', (str(system),))
    monkeypatch.setattr(step_inputs, '_TRUSTED_OWNER', os.getuid())
    real = system / 'python3.10'
    real.write_bytes(b'python')
    real.chmod(0o755)
    return root, folder, artifact, expected, system, real


@posix_only
def test_standard_venv_link_chain_to_a_system_runtime_is_accepted(tmp_path, monkeypatch):
    root, folder, artifact, expected, system, real = linux_env(tmp_path, monkeypatch)
    (system / 'python3').symlink_to('python3.10')
    (folder / '.venv/bin/python3').symlink_to(system / 'python3')
    (folder / '.venv/bin/python').symlink_to('python3')
    assert validate_runner_env(root, folder, artifact, expected) == folder / '.venv'


@posix_only
def test_link_to_an_executable_inside_the_venv_is_accepted(tmp_path, monkeypatch):
    root, folder, artifact, expected, _, _ = linux_env(tmp_path, monkeypatch)
    inner = folder / '.venv/bin/python3.10'
    inner.write_bytes(b'python')
    inner.chmod(0o755)
    (folder / '.venv/bin/python').symlink_to('python3.10')
    assert validate_runner_env(root, folder, artifact, expected) == folder / '.venv'


@posix_only
@pytest.mark.parametrize('case', ['outside', 'writable', 'not_executable', 'loop', 'escape', 'writable_folder'])
def test_other_interpreter_links_stay_refused(tmp_path, monkeypatch, case):
    root, folder, artifact, expected, system, real = linux_env(tmp_path, monkeypatch)
    link = folder / '.venv/bin/python'
    if case == 'outside':
        other = tmp_path / 'elsewhere'
        other.write_bytes(b'python')
        other.chmod(0o755)
        link.symlink_to(other)
    elif case == 'writable':
        real.chmod(0o775)
        link.symlink_to(real)
    elif case == 'not_executable':
        real.chmod(0o644)
        link.symlink_to(real)
    elif case == 'loop':
        link.symlink_to('python')
    elif case == 'escape':
        link.symlink_to('../../../../system/python3.10')
        (tmp_path / 'system').joinpath('python3.10').chmod(0o755)
        monkeypatch.setattr(step_inputs, 'SYSTEM_RUNTIME_ROOTS', (str(tmp_path / 'nowhere'),))
    else:
        system.chmod(0o777)
        link.symlink_to(real)
    with pytest.raises(ValueError):
        validate_runner_env(root, folder, artifact, expected)
