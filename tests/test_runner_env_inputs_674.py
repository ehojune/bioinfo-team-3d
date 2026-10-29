"""Failure boundaries for runner-local ancestor inputs (#674)."""
import pytest
import os
import hashlib
import json
import shutil

from labhq.models import TaskResult
from labhq.runner import step_inputs
from labhq.runner.step_inputs import stage_inputs, validate_runner_env
from tests.test_observed_outputs import _runner, _task, _write
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
    assert not result.ok and 'runner environment inputs unavailable' in result.error


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
                'forged_capability': 'workspace or request identity unavailable'}
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
    assert not result.ok and 'workspace or request identity unavailable' in result.error
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
@pytest.mark.parametrize('case', ['outside', 'writable', 'not_executable', 'loop', 'escape', 'writable_folder', 'mutable_hop'])
def test_other_interpreter_links_stay_refused(tmp_path, monkeypatch, case):
    root, folder, artifact, expected, system, real = linux_env(tmp_path, monkeypatch)
    link = folder / '.venv/bin/python'
    if case == 'outside':
        other = tmp_path / 'elsewhere'
        other.write_bytes(b'python')
        other.chmod(0o755)
        link.symlink_to(other)
    elif case == 'mutable_hop':
        # An untrusted link that itself points at an allowed system binary must not flatten into a pass.
        hop = tmp_path / 'mutable-link'
        hop.symlink_to(real)
        link.symlink_to(hop)
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


def daemon_runner(root, runner_id='lab pc 1', runtime='windows'):
    from types import SimpleNamespace
    from labhq.runner.daemon import Runner
    runner = Runner.__new__(Runner)
    runner.s = SimpleNamespace(runner=SimpleNamespace(id=runner_id, environment='wsl' if runtime == 'wsl' else 'native'))
    runner.ws_root = root
    return runner


def bound_task(folder, artifact, expected, runner_id='lab pc 1', request_id='req'):
    from labhq.models import Task
    return Task(id='consumer', agent_id='worker', request_id=request_id, prompt='run', meta={
        'kind': 'step', 'session_runner': runner_id, 'environment_bindings': [expected],
        'upstream_steps': {expected['step_id']: str(folder)},
        'upstream_artifacts': {expected['step_id']: {**artifact, 'task_id': 'env-task', 'workdir': str(folder)}}})


def write_receipt(folder, request_id='req', ok=True):
    (folder / 'manifest.json').write_text(json.dumps({
        'task': {'id': 'env-task', 'request_id': request_id, 'meta': {'step_id': 'env_win'}},
        'runs': {'env-task': {'ok': ok}}}), encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='windows runtime binding')
async def test_daemon_validates_a_genuine_environment_but_stays_closed_without_an_alias(tmp_path, monkeypatch):
    import labhq.runner.daemon as daemon
    root, folder, artifact, expected, manifest, save = env_fixture(tmp_path)
    expected['runner_id'] = manifest['runner_id'] = 'lab pc 1'
    save(manifest)
    write_receipt(folder)
    runner = daemon_runner(root)
    reason = runner._environment_refusal_reason(bound_task(folder, artifact, expected))
    assert 'protected interpreter alias is not implemented' in reason
    monkeypatch.setattr(daemon, 'ENVIRONMENT_ALIAS_AVAILABLE', True)
    assert runner._environment_refusal_reason(bound_task(folder, artifact, expected)) is None


@pytest.mark.skipif(os.name != 'nt', reason='windows runtime binding')
@pytest.mark.parametrize('failure', ['no_receipt', 'failed_run', 'other_request', 'tampered', 'no_interpreter', 'other_step_run'])
async def test_daemon_refuses_an_environment_that_does_not_check_out(tmp_path, failure):
    root, folder, artifact, expected, manifest, save = env_fixture(tmp_path)
    expected['runner_id'] = manifest['runner_id'] = 'lab pc 1'
    save(manifest)
    write_receipt(folder, request_id='other' if failure == 'other_request' else 'req', ok=failure != 'failed_run')
    if failure == 'other_step_run':
        # A successful run of some other step in the same workspace must not vouch for the environment step.
        record = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        record['runs']['env-task']['step_id'] = 'unrelated'
        (folder / 'manifest.json').write_text(json.dumps(record), encoding='utf-8')
    if failure == 'no_receipt':
        (folder / 'manifest.json').unlink()
    elif failure == 'tampered':
        artifact['output_sha256']['outputs/env/runner-manifest.json'] = '0' * 64
    elif failure == 'no_interpreter':
        (folder / '.venv/Scripts/python.exe').unlink()
    runner = daemon_runner(root)

    async def emit_all(_events):
        pass
    runner.emit_all = emit_all
    result = await runner._refuse_unprotected_environment(bound_task(folder, artifact, expected))
    assert result is not None and not result.ok and 'environment validation failed' in result.error


@posix_only
async def test_daemon_accepts_a_standard_venv_link_and_refuses_an_untrusted_one(tmp_path, monkeypatch):
    import labhq.runner.daemon as daemon
    root, folder, artifact, expected, system, real = linux_env(tmp_path, monkeypatch)
    write_receipt(folder)
    (system / 'python3').symlink_to('python3.10')
    link = folder / '.venv/bin/python'
    link.symlink_to(system / 'python3')
    runner = daemon_runner(root, 'runner1', 'linux')
    task = bound_task(folder, artifact, expected, 'runner1')
    monkeypatch.setattr(daemon, 'ENVIRONMENT_ALIAS_AVAILABLE', True)
    assert runner._environment_refusal_reason(task) is None
    link.unlink()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.write_bytes(b'python')
    elsewhere.chmod(0o755)
    link.symlink_to(elsewhere)
    assert 'environment validation failed' in runner._environment_refusal_reason(task)


# An environment step succeeds only when its collected manifest equals the pinned binding (#674).

PRODUCER_MANIFEST = "outputs/env/runner-manifest.json"
PRODUCER_LOCK = "outputs/env/requirements.lock.txt"
PRODUCER_WINDOWS = os.name == "nt"
PRODUCER_PYTHON = ".venv/Scripts/python.exe" if PRODUCER_WINDOWS else ".venv/bin/python"
PRODUCER_BINDING = dict(version=1, logical_step_id="env", step_id="env", runner_id="lab pc 1",
               runtime="windows" if PRODUCER_WINDOWS else "linux", venv_path=".venv", python_path=PRODUCER_PYTHON)


async def run_env_producer(tmp_path, monkeypatch, manifest, *, interpreter=True, binding=None):
    def write(workdir):
        _write(workdir, PRODUCER_LOCK, b"pandas==2.2\n")
        _write(workdir, PRODUCER_MANIFEST, json.dumps(manifest).encode())
        if interpreter:
            _write(workdir, PRODUCER_PYTHON, b"python")

    runner = _runner(tmp_path, monkeypatch, write)
    runner.s.runner.id = PRODUCER_BINDING['runner_id']
    task = _task(outputs=[PRODUCER_LOCK, PRODUCER_MANIFEST], step_id="env", environment_binding=PRODUCER_BINDING if binding is None else binding)
    return await runner.run_task(task)


@pytest.mark.asyncio
async def test_matching_manifest_and_interpreter_keep_the_environment_step_successful(tmp_path, monkeypatch):
    result = await run_env_producer(tmp_path, monkeypatch, PRODUCER_BINDING)
    assert result.ok, result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest", [
    {}, {**PRODUCER_BINDING, "runner_id": "other"}, {**PRODUCER_BINDING, "step_id": "env_other"},
    {**PRODUCER_BINDING, "runtime": "wsl"}, {**PRODUCER_BINDING, "version": 2}], ids=["empty", "stale_runner", "stale_step",
                                                                    "other_runtime", "version"])
async def test_a_manifest_that_differs_from_the_binding_fails_the_step(tmp_path, monkeypatch, manifest):
    result = await run_env_producer(tmp_path, monkeypatch, manifest)
    assert not result.ok
    assert "does not match the pinned binding" in result.error


@pytest.mark.asyncio
async def test_a_missing_interpreter_fails_the_step(tmp_path, monkeypatch):
    result = await run_env_producer(tmp_path, monkeypatch, PRODUCER_BINDING, interpreter=False)
    assert not result.ok and "pinned binding" in result.error


@pytest.mark.asyncio
async def test_steps_without_a_binding_are_not_checked(tmp_path, monkeypatch):
    def write(workdir):
        _write(workdir, "outputs/table.tsv", b"x\n")

    result = await _runner(tmp_path, monkeypatch, write).run_task(_task(outputs=["outputs/table.tsv"]))
    assert result.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", ["job", "blocking_decision"])
async def test_a_paused_environment_step_is_not_held_to_its_binding_yet(tmp_path, monkeypatch, pause):
    holder = {}

    def reply(ctx):
        if pause == "job":
            holder["runner"].jobs["job-1"] = {"task_id": ctx.task.id}
        return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="waiting",
                          structured={"blocking_decision": "Which index?"} if pause == "blocking_decision" else None)

    runner = holder["runner"] = _runner(tmp_path, monkeypatch, lambda _workdir: None, reply=reply)
    runner.s.runner.id = PRODUCER_BINDING['runner_id']
    result = await runner.run_task(_task(outputs=[PRODUCER_LOCK, PRODUCER_MANIFEST],
                                         environment_binding=PRODUCER_BINDING, step_id="env"))
    assert result.ok, result.error


@pytest.mark.asyncio
async def test_a_deeply_nested_manifest_fails_the_step_instead_of_aborting_the_runner(tmp_path, monkeypatch):
    def write(workdir):
        _write(workdir, PRODUCER_LOCK, b"pandas==2.2")
        _write(workdir, PRODUCER_MANIFEST, b"[" * 200_000)
        _write(workdir, PRODUCER_PYTHON, b"python")

    runner = _runner(tmp_path, monkeypatch, write)
    runner.s.runner.id = PRODUCER_BINDING['runner_id']
    result = await runner.run_task(_task(outputs=[PRODUCER_LOCK, PRODUCER_MANIFEST],
                                         environment_binding=PRODUCER_BINDING, step_id="env"))
    assert not result.ok and "pinned binding" in result.error


@pytest.mark.parametrize("change,reason", [
    ({}, None), ({"runner_id": "other"}, "runner mismatch"), ({"runtime": "wsl" if PRODUCER_WINDOWS else "windows",
                                                              "python_path": ".venv/bin/python" if PRODUCER_WINDOWS
                                                              else ".venv/Scripts/python.exe"}, "runtime mismatch"),
    ({"venv_path": "../x"}, "invalid binding contract"), ({"step_id": "bad/step"}, "invalid binding contract"),
    ({"step_id": "other"}, "step mismatch"), ({"__drop_step__": True}, "step mismatch"),
    ({"__session__": ["lab pc 1"]}, "runner mismatch"), ({"__session__": {"a": 1}}, "runner mismatch")])
def test_producer_binding_is_checked_before_the_step_runs(change, reason):
    from types import SimpleNamespace
    from labhq.models import Task
    from labhq.runner.daemon import Runner

    runner = Runner.__new__(Runner)
    runner.s = SimpleNamespace(runner=SimpleNamespace(id="lab pc 1", environment="native"))
    drop = change.pop("__drop_step__", False)
    session = change.pop("__session__", "lab pc 1")
    binding = {**PRODUCER_BINDING, **change}
    meta = {"kind": "step", "step_id": "env", "session_runner": session, "environment_binding": binding}
    if drop:
        del meta["step_id"]
    task = Task(id="producer", agent_id="worker", prompt="run", meta=meta)
    got = runner._environment_refusal_reason(task)
    assert (got is None) if reason is None else (got is not None and reason in got)


def test_a_producer_binding_that_is_not_an_object_is_refused():
    from types import SimpleNamespace
    from labhq.models import Task
    from labhq.runner.daemon import Runner

    runner = Runner.__new__(Runner)
    runner.s = SimpleNamespace(runner=SimpleNamespace(id="lab pc 1", environment="native"))
    got = runner._environment_refusal_reason(
        Task(id="p", agent_id="worker", prompt="run", meta={"environment_binding": "env"}))
    assert got is not None and "invalid binding" in got


@pytest.mark.asyncio
async def test_a_producer_bound_to_another_runner_is_refused_before_the_engine_starts(tmp_path, monkeypatch):
    started = []
    runner = _runner(tmp_path, monkeypatch, lambda workdir: started.append(workdir))
    runner.s.runner.id = "someone else"
    result = await runner.run_task(_task(outputs=[PRODUCER_LOCK, PRODUCER_MANIFEST], step_id="env",
                                         environment_binding=PRODUCER_BINDING))
    assert not result.ok and "runner mismatch" in result.error
    assert not started
