"""Disposable runner fixture for the runner-specific environment contract."""

import hashlib
import json

import pytest

from labhq.models import AgentSpec, Task
from labhq.runner.step_inputs import stage_inputs
from labhq.runner.workspace import TaskWorkspace


@pytest.fixture
def runners(tmp_path):
    """Two isolated workspace roots; labels model runners, not host operating systems."""
    roots = {name: tmp_path / name for name in ('windows', 'wsl')}
    for root in roots.values():
        root.mkdir()
    return roots


def artifact(root, runner):
    source = root / 'env-source'
    (source / 'outputs').mkdir(parents=True)
    payload = f'fixture for {runner}\n'.encode()
    (source / 'outputs/requirements.lock.txt').write_bytes(payload)
    (source / 'manifest.json').write_text(json.dumps({
        'task': {'id': 'env-task', 'request_id': 'request', 'meta': {'step_id': 'env'}},
        'runs': {'env-task': {'ok': True}},
    }), encoding='utf-8')
    return {'task_id': 'env-task', 'workdir': str(source),
            'outputs': ['outputs/requirements.lock.txt'],
            'output_sha256': {'outputs/requirements.lock.txt': hashlib.sha256(payload).hexdigest()}}


@pytest.mark.parametrize('runner', ['windows', 'wsl'])
def test_disposable_runner_artifact_boundary(runners, runner):
    root = runners[runner]
    own = artifact(root, runner)
    other = 'wsl' if runner == 'windows' else 'windows'
    foreign = artifact(runners[other], other)
    task = Task(id='consumer', agent_id='worker', request_id='request', prompt='fixture')
    ws = TaskWorkspace(root, task, AgentSpec(id='worker', name='Worker', role='test'))

    staged, refused = stage_inputs(ws, {'env': own}, root, [], [], 1024, 4096)
    assert staged and not refused
    assert (ws.dir / 'inputs/env/requirements.lock.txt').read_bytes() == f'fixture for {runner}\n'.encode()

    staged, refused = stage_inputs(ws, {'env': foreign}, root, [], [], 1024, 4096)
    assert not staged and 'env' in refused
    assert not (ws.dir / 'inputs/env/requirements.lock.txt').exists()
