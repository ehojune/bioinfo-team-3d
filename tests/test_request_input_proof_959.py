import copy

import pytest

from labhq.research import continuation as c
from labhq.research.input_proof import capture
from tests.test_continuation_797 import archive, AGENTS
from tests.test_continuation_review_807 import partial_hub, approve
from tests.test_research_cp2 import _settings
from labhq.models import Task, TaskResult


def fixture(tmp_path):
    settings = _settings()
    settings.runner.reference_roots = [str(tmp_path)]
    folder = tmp_path / 'inputs'
    folder.mkdir()
    (folder / 'a.txt').write_text('first')
    request = {'references': [{'kind': 'path', 'value': str(folder)}]}
    return settings, request, folder


@pytest.mark.parametrize('directory', [True, False])
def test_recorded_content_allows_reuse_and_changes_decline(tmp_path, directory):
    settings, request, folder = fixture(tmp_path)
    if not directory:
        request['references'][0]['value'] = str(folder / 'a.txt')
    proof = capture(request, settings)
    assert proof is not None
    old = archive()
    old.update(request)
    old['reuse_input_proof'] = {sid: proof for sid in old['results']}
    current = copy.deepcopy(request)
    current['_current_input_proof'] = capture(current, settings)
    carry = c.carry_over(old, old['plan'], {}, [])
    tools = c.tool_signatures(old['plan'], AGENTS)
    assert not c.proof_problems(old, old['plan'], carry, tools, current, current['_current_input_proof'])
    (folder / 'a.txt').write_text('changed')
    current['_current_input_proof'] = capture(current, settings)
    assert set(c.proof_problems(old, old['plan'], carry, tools, current, current['_current_input_proof'])) == set(carry.reuse)


def test_manifest_tracks_empty_directories_and_refuses_unreadable_or_links(tmp_path, monkeypatch):
    settings, request, folder = fixture(tmp_path)
    first = capture(request, settings)
    (folder / 'empty').mkdir()
    assert capture(request, settings) != first
    from pathlib import Path
    monkeypatch.setattr(Path, 'open', lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    assert capture(request, settings) is None


def test_outside_root_and_remote_reference_decline(tmp_path):
    settings, request, folder = fixture(tmp_path)
    settings.runner.reference_roots = [str(folder / 'other')]
    assert capture(request, settings) is None


def test_directory_file_addition_deletion_and_limits(tmp_path):
    settings, request, folder = fixture(tmp_path)
    first = capture(request, settings)
    extra = folder / 'b.txt'
    extra.write_text('new')
    assert capture(request, settings) != first
    extra.unlink()
    assert capture(request, settings) == first
    settings.runner.reference_scan_max_entries = 1
    assert capture(request, settings) is None


def test_file_reference_proves_exposed_sibling_files(tmp_path):
    settings, request, folder = fixture(tmp_path)
    request['references'][0]['value'] = str(folder / 'a.txt')
    before = capture(request, settings)
    assert before is not None
    sibling = folder / 'sibling.txt'
    sibling.write_text('first')
    added = capture(request, settings)
    assert added != before
    sibling.write_text('changed')
    assert capture(request, settings) != added


def test_windows_wsl_manifest_uses_one_logical_identity():
    from labhq.research.input_proof import _logical_path
    from labhq.wsl_paths import message_paths
    windows = {'references': [{'kind': 'path', 'value': 'C:/inputs/table.tsv'}],
               'project_dirs': ['D:/project']}
    wsl = message_paths(windows, 'wsl')
    assert message_paths(wsl, 'windows') == windows
    assert _logical_path('C:/inputs') == _logical_path('/mnt/c/inputs')


def test_hardlink_input_refused(tmp_path):
    settings, request, folder = fixture(tmp_path)
    import os
    os.link(folder / 'a.txt', folder / 'alias.txt')
    assert capture(request, settings) is None


def test_restricted_and_private_inputs_not_opened(tmp_path, monkeypatch):
    settings, request, folder = fixture(tmp_path)
    from labhq.settings import DataZone
    from pathlib import Path
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('guarded input opened'))
    settings.policy.data_zones = [DataZone(path=str(folder))]
    assert capture(request, settings) is None
    settings.policy.data_zones = []
    settings.policy.private_paths = [str(folder)]
    assert capture(request, settings) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mutate', [False, True])
async def test_runner_proves_its_own_input_content(tmp_path, monkeypatch, mutate):
    from tests.test_input_hashes import _project_runner
    settings, request, folder = fixture(tmp_path)
    class Adapter:
        async def run(self, ctx):
            if mutate:
                (folder / 'a.txt').write_text('runner changed input')
            return TaskResult(task_id=ctx.task.id, agent_id='worker', ok=True, text='done')
    runner, agent = _project_runner(tmp_path, monkeypatch, Adapter())
    runner.s.runner.reference_roots = settings.runner.reference_roots
    expected = capture(request, runner.s)
    result = await runner.run_task(Task(agent_id=agent.id, request_id='r', prompt='fixture', meta={
        'kind': 'step', 'step_id': 's1', 'reuse_input_request': request, 'reuse_input_meta': {}}))
    assert result.request_input_sha256 == (None if mutate else expected['sha256'])


@pytest.mark.asyncio
@pytest.mark.parametrize('mutate', [False, True])
@pytest.mark.parametrize('receipt', ['matching', 'missing', 'different'])
async def test_execution_records_and_archives_input_proof(tmp_path, monkeypatch, mutate, receipt):
    hub, orch = partial_hub(monkeypatch)
    settings, request, folder = fixture(tmp_path)
    hub.s.runner.reference_roots = settings.runner.reference_roots
    req = hub.requests['r']
    req.update(request)
    req['research_contract'].pop('continuation')
    hub.s.orchestrator.step_max_attempts = 1
    async def reply(task):
        before = capture(request, hub.s)
        if mutate:
            (folder / 'a.txt').write_text('changed during dispatch')
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          request_input_sha256=(before['sha256'] if receipt == 'matching'
                                                else None if receipt == 'missing' else '0' * 64))
    hub.reply = reply
    await orch.run_step(Task(agent_id='worker', request_id='r', prompt='fixture',
                             meta={'kind': 'step', 'step_id': 's1'}))
    proof = req['reuse_input_proof']['s1']
    assert (proof is None) == (mutate or receipt != 'matching')
    assert c.archive_round(req, {})['reuse_input_proof']['s1'] == proof
    request['references'] = [{'kind': 'url', 'value': 'https://example.org'}]
    assert capture(request, settings) is None


@pytest.mark.asyncio
async def test_approved_reference_reuse_stops_when_content_changes(tmp_path, monkeypatch):
    hub, orch = partial_hub(monkeypatch)
    settings, request, folder = fixture(tmp_path)
    hub.s.runner.reference_roots = settings.runner.reference_roots
    req = hub.requests['r']
    req.update(request)
    old = req['research_contract']['rounds'][-1]
    old.update(request)
    old['reuse_input_proof'] = {sid: capture(request, hub.s) for sid in old['results']}
    await orch._prepare_research_reuse('r', req['plan'])
    assert req['research_contract']['continuation']['reuse'] == ['s1']
    approve(req)
    assert await orch._verify_research_reuse('r', req['plan'])
    (folder / 'a.txt').write_text('changed')
    assert not await orch._verify_research_reuse('r', req['plan'])
    assert not hub.calls
