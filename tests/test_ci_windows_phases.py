"""Fake JSON/report lifecycle contracts only: no workers, browser or processes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_skip as cs, ci_suite as suite, ci_windows_phases as p

WINDOWS_PYTHON = r'C:\fake\python.exe'


class RuntimeProxy:
    """Override a consumer's runtime view without mutating host stdlib objects."""
    def __init__(self, original, **overrides):
        self.__dict__.update(overrides)
        self._original = original

    def __getattr__(self, name):
        return getattr(self._original, name)


def fake_windows_runtime(monkeypatch):
    """Bind receipts and both consumers to Windows 3.12, never the test host."""
    import ntpath
    import os
    import sys
    windows = SimpleNamespace(python_version=lambda: '3.12.10',
                              platform=lambda: 'Windows-fake', system=lambda: 'Windows')
    for consumer in (suite, cs):
        monkeypatch.setattr(consumer, 'sys', RuntimeProxy(sys, executable=WINDOWS_PYTHON))
        monkeypatch.setattr(consumer, 'os', RuntimeProxy(os, path=ntpath))
    monkeypatch.setattr(suite, 'platform', windows)
    # ci_skip.main imports platform inside the function. Replace that import
    # slot for this test only; do not mutate the host platform module itself.
    monkeypatch.setitem(sys.modules, 'platform', windows)
    return dict(python_version=windows.python_version(),
                python_executable=ntpath.normcase(ntpath.abspath(WINDOWS_PYTHON)))


def fake_context(current=None, *, checkout='e' * 40, head='d' * 40):
    current = current or dict(tree='b' * 40, policy={path: f'{i+1:x}' * 40 for i, path in enumerate(cs.POLICY_FILES)})
    return dict(execution=p.EXECUTION, repository=cs.REPOSITORY, run_id=42, attempt=1,
        checkout_sha=checkout, head_sha=head, tree=current['tree'], policy=current['policy'],
        workflow_sha=checkout, workflow_ref=f'{cs.REPOSITORY}/{cs.WORKFLOW}@refs/pull/788/merge',
        profile=p.PROFILE, python_version='3.12.10', python_executable='C:/fake/python.exe',
        invocation='0' * 32, setup_started=1)


def fake_inventory():
    job = [p.MANDATORY[0], *(p.MANDATORY[1] + '[' + kind + ']' for kind in ('unknown_job', 'receipt_timeout')),
           *(p.FILES[0] + '::test_owned_' + str(i) for i in range(2))]
    shutdown = list(p.MANDATORY[2:]) + [p.FILES[1] + '::test_owned_' + str(i) for i in range(17)]
    critical = job + shutdown + [p.BROWSER]
    parallel = ['tests/test_fake.py::test_owned_' + str(i) for i in range(4)]
    return critical, parallel


def make_receipts(folder, context=None):
    context = context or fake_context()
    folder.mkdir(exist_ok=True)
    p.write(folder/'identity.json', context)
    critical, parallel = fake_inventory()
    eligible = critical + parallel
    full = eligible + ['tests/test_pure.py::test_text']
    for phase, selected, count, start, end in (('critical', critical, 0, 10, 56), ('parallel', parallel, 4, 57, 400)):
        directory = folder/phase
        directory.mkdir(exist_ok=True)
        workers = ['master'] if count == 0 else ['gw' + str(i) for i in range(count)]
        coverage = dict(profile=context['profile'], context=context, phase=phase,
                        full_nodes=full, eligible_nodes=eligible, selected_nodes=selected,
                        excluded_files={'tests/test_pure.py': 'pure fixture'})
        reports = [[node, stage, 'passed'] for node in selected for stage in ('setup', 'call', 'teardown')]
        for worker in set(workers) | {'master'}:
            own = reports if worker == 'master' else [r for r in reports if selected.index(r[0]) % 4 == int(worker[2:])]
            p.write(directory/('metrics-' + worker + '.json'), dict(profile=context['profile'], context=context,
                phase=phase, worker=worker, workers=count, started_at=start, finished_at=end, exitstatus=0,
                stage_reports=own, host_resource_wait_s={}, longest_files=[]))
        for worker in workers:
            p.write(directory/('collection-' + worker + '.json'), coverage)
    p.ready(folder)
    proof = p.combine(folder)
    p.write(folder/'windows-proof.json', proof)
    return proof


@pytest.fixture
def receipts(tmp_path):
    make_receipts(tmp_path)
    return tmp_path


def change(path, transform):
    value = p.read(path)
    transform(value)
    p.write(path, value)


def test_exact_union_disjoint_actual_phase_receipts_and_elapsed_durations(receipts):
    proof = p.combine(receipts)
    assert proof['selected_node_count'] == 30 and proof['full_node_count'] == 31
    assert proof['phases']['critical'] == dict(selected=26, passed=26, skipped=0, failed=0,
        workers=0, started_at=10, finished_at=56, pytest_s=46)
    assert proof['phases']['parallel']['selected'] == 4 and proof['phases']['parallel']['workers'] == 4
    assert proof['setup_s'] == 9 and proof['pytest_s'] == 390  # Includes barrier gap, not a forged sum.
    assert p.read(receipts/'critical/collection-master.json')['selected_nodes'] == fake_inventory()[0]
    assert p.read(receipts/'parallel/collection-gw0.json')['selected_nodes'] == fake_inventory()[1]


@pytest.mark.parametrize('file', ['identity.json', 'critical-success.json', 'critical/metrics-master.json',
    'critical/collection-master.json', 'parallel/metrics-master.json', 'parallel/metrics-gw3.json',
    'parallel/collection-gw2.json'])
def test_missing_crash_or_partial_receipt_is_never_reusable(receipts, file):
    (receipts/file).unlink()
    with pytest.raises((OSError, ValueError)):
        p.combine(receipts)


@pytest.mark.parametrize('case', ['skip', 'failed', 'timeout', 'missing-call', 'missing-teardown',
    'duplicate', 'foreign', 'stale-run', 'stale-attempt', 'stale-tree', 'stale-policy', 'interpreter'])
def test_critical_bad_lifecycle_or_stale_identity_blocks_pool(receipts, case):
    path = receipts/'critical/metrics-master.json'
    def mutate(value):
        if case in ('skip', 'failed'):
            value['stage_reports'][1][2] = 'skipped' if case == 'skip' else 'failed'
        elif case == 'timeout':
            value['exitstatus'] = 1
        elif case.startswith('missing-'):
            value['stage_reports'] = [r for r in value['stage_reports'] if
                not (r[0] == p.BROWSER and r[1] == case.removeprefix('missing-'))]
        elif case == 'duplicate':
            value['stage_reports'].append(value['stage_reports'][0])
        elif case == 'foreign':
            value['stage_reports'][0][0] = 'tests/test_other.py::test_unknown'
        else:
            key = dict(**{'stale-run': 'run_id', 'stale-attempt': 'attempt', 'stale-tree': 'tree',
                         'stale-policy': 'policy', 'interpreter': 'python_executable'})[case]
            value['context'][key] = 99 if key in ('run_id', 'attempt') else 'changed'
    change(path, mutate)
    with pytest.raises(ValueError):
        p.ready(receipts)
    with pytest.raises(ValueError):
        p.combine(receipts)


@pytest.mark.parametrize('case', ['overlap', 'lost', 'worker-lost', 'duplicate-parameter', 'full-drift',
    'eligible-drift', 'early-controller', 'early-worker', 'stale-barrier', 'error-file'])
def test_parallel_cannot_forge_expanded_collection_or_overlap_critical(receipts, case):
    path = receipts/'parallel/collection-gw0.json'
    if case in ('overlap', 'lost', 'duplicate-parameter', 'full-drift', 'eligible-drift'):
        def mutate(v):
            if case == 'overlap': v['selected_nodes'].append(p.BROWSER)
            elif case == 'lost': v['selected_nodes'].pop()
            elif case == 'duplicate-parameter': v['full_nodes'].append(v['full_nodes'][0])
            elif case == 'full-drift': v['full_nodes'].pop()
            else: v['eligible_nodes'].pop()
        change(path, mutate)
    elif case == 'worker-lost':
        change(receipts/'parallel/metrics-gw0.json', lambda v: v['stage_reports'].pop())
    elif case.startswith('early-'):
        worker = 'master' if case.endswith('controller') else 'gw0'
        change(receipts/f'parallel/metrics-{worker}.json', lambda v: v.update(started_at=55))
    elif case == 'stale-barrier':
        change(receipts/'critical-success.json', lambda v: v.update(critical_digest='0' * 64))
    else:
        p.write(receipts/'parallel/selection-error-master.json', {'reusable': False})
    with pytest.raises(ValueError):
        p.combine(receipts)


def test_critical_requires_every_native_parameter_and_known_browser():
    critical, parallel = fake_inventory()
    full = critical + parallel
    for remove in (p.BROWSER, critical[1], critical[-2]):
        with pytest.raises(ValueError):
            p.critical([n for n in full if n != remove], full)
        with pytest.raises(ValueError):
            p.critical(full, [n for n in full if n != remove])


def test_existing_parallel_skips_are_counted_but_never_allowed_in_critical(receipts):
    selected = fake_inventory()[1][0]
    for worker in ('master', 'gw0'):
        def skip(value):
            value['stage_reports'] = [r for r in value['stage_reports'] if not (r[0] == selected and r[1] == 'call')]
            next(r for r in value['stage_reports'] if r[0] == selected and r[1] == 'setup')[2] = 'skipped'
        change(receipts/f'parallel/metrics-{worker}.json', skip)
    proof = p.combine(receipts)
    assert proof['phases']['parallel']['skipped'] == 1
    assert proof['phases']['critical']['skipped'] == 0


@pytest.mark.parametrize('phase', ['critical', 'parallel'])
def test_real_collection_hook_keeps_actual_phase_selected_nodes_only(monkeypatch, receipts, phase):
    context = p.read(receipts/'identity.json')
    context.update(fake_windows_runtime(monkeypatch))
    make_receipts(receipts, context)
    for key, value in dict(CI_SUITE_ARTIFACT_DIR=str(receipts), GITHUB_SHA=context['checkout_sha'],
        GITHUB_WORKFLOW_SHA=context['workflow_sha'], GITHUB_WORKFLOW_REF=context['workflow_ref'],
        GITHUB_REPOSITORY=context['repository'], GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1',
        LABHQ_REQUIRE_RESIZABLE_BROWSER='1', PYTEST_XDIST_WORKER='master', PYTEST_XDIST_WORKER_COUNT='0').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(suite, 'ROOT', receipts)
    critical, parallel = fake_inventory()
    full = critical + parallel + ['tests/test_pure.py::test_text']
    items = [SimpleNamespace(path=receipts/n.split('::', 1)[0], nodeid=n) for n in full]
    monkeypatch.setattr(suite, 'select', lambda *args: (items[:-1], items[-1:], {'tests/test_pure.py': 'pure fixture'}))
    monkeypatch.setattr(suite, 'policy', lambda: dict(boundary_cohorts={f: 'native' for f in p.FILES},
                                                   mandatory_nodes=list(p.MANDATORY)))
    deselected = []
    config = SimpleNamespace(getoption=lambda key: dict(ci_profile=p.PROFILE, ci_phase=phase).get(key),
                             hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    observer = suite.Observation(config)
    if phase == 'critical':
        with pytest.raises(pytest.UsageError):
            observer.pytest_xdist_setupnodes(config, [None] * 4)
    else:
        with pytest.raises(pytest.UsageError):
            observer.pytest_xdist_setupnodes(config, [None] * 3)
        observer.pytest_xdist_setupnodes(config, [None] * 4)
    observer.pytest_collection_modifyitems(None, config, items)
    expected = critical if phase == 'critical' else parallel
    assert [i.nodeid for i in items] == expected
    saved = p.read(receipts/phase/'collection-master.json')
    assert saved['selected_nodes'] == expected and saved['eligible_nodes'] == critical + parallel
    assert saved['full_nodes'] == full
    assert set(i.nodeid for i in deselected) == set(full) - set(expected)
    # Actual hook events are retained per stage, not a synthetic expanded PASS.
    for node in expected:
        for stage in ('setup', 'call', 'teardown'):
            observer.pytest_runtest_logreport(SimpleNamespace(nodeid=node, when=stage,
                outcome='passed', duration=.001, failed=False, skipped=False, passed=True))
    assert observer.stage_reports == [[n, stage, 'passed'] for n in expected for stage in ('setup', 'call', 'teardown')]


def test_parallel_plugin_refuses_to_start_without_verified_critical_barrier(monkeypatch, receipts):
    context = p.read(receipts/'identity.json')
    context.update(fake_windows_runtime(monkeypatch))
    make_receipts(receipts, context)
    for key, value in dict(CI_SUITE_ARTIFACT_DIR=str(receipts), GITHUB_SHA=context['checkout_sha'],
        GITHUB_WORKFLOW_SHA=context['workflow_sha'], GITHUB_WORKFLOW_REF=context['workflow_ref'],
        GITHUB_REPOSITORY=context['repository'], GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1').items():
        monkeypatch.setenv(key, value)
    (receipts/'critical-success.json').unlink()
    config = SimpleNamespace(getoption=lambda key: dict(ci_profile=p.PROFILE, ci_phase='parallel').get(key))
    with pytest.raises(FileNotFoundError, match='critical-success.json'):
        suite.Observation(config)


def test_manifest_producer_revalidates_all_receipts_and_output_digest(monkeypatch, receipts):
    proof = p.combine(receipts)
    context = proof['context']
    current = dict(checkout_sha=context['checkout_sha'], tree=context['tree'], policy=context['policy'])
    needs = {'pytest-windows': {'outputs': dict(execution=p.EXECUTION, receipt=p.digest(proof))}}
    monkeypatch.setenv('CI_WINDOWS_RECEIPT_DIR', str(receipts))
    monkeypatch.setenv('GITHUB_WORKFLOW_REF', context['workflow_ref'])
    assert cs.windows_receipt(current, needs, {'id': 42, 'run_attempt': 1}, context['head_sha']) == proof
    needs['pytest-windows']['outputs']['receipt'] = '0' * 64
    with pytest.raises(ValueError):
        cs.windows_receipt(current, needs, {'id': 42, 'run_attempt': 1}, context['head_sha'])


def test_identity_cli_creates_fresh_directory_before_pytest_with_fake_git(monkeypatch, tmp_path):
    import sys
    import uuid
    context = fake_context()
    context.update(fake_windows_runtime(monkeypatch))
    current = {k: context[k] for k in ('checkout_sha', 'tree', 'policy')}
    event = tmp_path/'event.json'
    p.write(event, {'pull_request': {'head': {'sha': context['head_sha']}}})
    directory = tmp_path/'not-created-yet'
    for key, value in dict(CI_SUITE_ARTIFACT_DIR=str(directory), GITHUB_EVENT_PATH=str(event),
        GITHUB_SHA=context['checkout_sha'], GITHUB_WORKFLOW_SHA=context['workflow_sha'],
        GITHUB_WORKFLOW_REF=context['workflow_ref'], GITHUB_REPOSITORY=cs.REPOSITORY,
        GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1', CI_COVERAGE_PROFILE=p.PROFILE, CI_SETUP_STARTED='1').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cs, 'identity', lambda: current)
    monkeypatch.setattr(sys, 'argv', ['ci_skip', 'windows-identity'])
    monkeypatch.setattr(uuid, 'uuid4', lambda: SimpleNamespace(hex='1' * 32))
    assert not directory.exists()
    cs.main()
    saved = p.validate_identity(p.read(directory/'identity.json'))
    assert saved['invocation'] == '1' * 32 and saved['head_sha'] == context['head_sha']
    assert saved['python_version'] == context['python_version'] == '3.12.10'
    assert saved['python_executable'] == context['python_executable']


def test_combined_summary_preserves_exclusions_and_never_double_counts_controller_wait(receipts):
    change(receipts/'parallel/metrics-master.json', lambda v: v.update(host_resource_wait_s={'fake.py': 999}))
    change(receipts/'parallel/metrics-gw0.json', lambda v: v.update(host_resource_wait_s={'fake.py': 1.25}))
    summary = suite.summarize(receipts)
    assert summary['host_resource_wait_s'] == {'fake.py': 1.25}
    assert summary['excluded_files'] == {'tests/test_pure.py': 'pure fixture'}
    assert summary['results'] == dict(passed=30, skipped=0, failed=0)
    assert p.read(receipts/'windows-proof.json') == p.combine(receipts)


def test_incomplete_report_is_retained_nonreusable_without_metadata_query(monkeypatch, receipts):
    import sys
    (receipts/'critical/metrics-master.json').unlink()
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(receipts))
    monkeypatch.setattr(sys, 'argv', ['ci_suite', 'report'])
    def forbidden(*args, **kwargs):
        pytest.fail('metadata query after incomplete receipt')
    monkeypatch.setattr(cs, 'api', forbidden)
    with pytest.raises(OSError):
        suite.main()
    saved = p.read(receipts/'report-error.json')
    assert saved['status'] == 'incomplete' and saved['reusable'] is False


@pytest.mark.parametrize('key', ['checkout_sha', 'head_sha', 'tree', 'policy', 'run_id', 'attempt',
                               'workflow_sha', 'workflow_ref', 'profile'])
def test_consumer_rejects_each_phase_identity_mismatch(receipts, key):
    proof = p.combine(receipts)
    context = copy.deepcopy(proof['context'])
    current = dict(tree=context['tree'], policy=context['policy'])
    proof['context'][key] = 99 if key in ('run_id', 'attempt') else 'changed'
    with pytest.raises(ValueError):
        cs.validate_windows_execution(proof, current, {'id': 42, 'run_attempt': 1},
            context['head_sha'], context['checkout_sha'], context['workflow_ref'])


def test_workflow_barrier_download_binding_no_skip_and_ten_minute_total():
    jobs = yaml.safe_load((suite.ROOT/cs.WORKFLOW).read_text(encoding='utf-8'))['jobs']
    job = jobs['pytest-windows']
    steps = job['steps']
    critical = next(i for i, x in enumerate(steps) if '--ci-phase critical' in x.get('run', ''))
    parallel = next(i for i, x in enumerate(steps) if '--ci-phase parallel' in x.get('run', ''))
    assert critical < parallel and steps[critical + 1]['run'] == 'python -m scripts.ci_suite critical-ready'
    assert '-n 0' in steps[critical]['run'] and steps[critical]['env']['LABHQ_REQUIRE_RESIZABLE_BROWSER'] == '1'
    assert '-n 4 --dist loadfile' in steps[parallel]['run']
    assert not any(x.get('continue-on-error') or x.get('if') for x in steps[critical:parallel+1])
    assert '|| 10' in job['timeout-minutes'] and 'windows-full-v1' in job['env']['CI_COVERAGE_PROFILE']
    assert 'receipt' in job['outputs'] and 'execution' in job['outputs']
    manifest = jobs['validation-manifest']['steps']
    assert any(x.get('uses') == 'actions/download-artifact@v4' and
               x['with']['name'] == 'coverage-windows-${{ github.run_id }}-${{ github.run_attempt }}' for x in manifest)
    browser = (suite.ROOT/'tests/web_resizable_panels.cjs').read_text(encoding='utf-8')
    assert "process.env.LABHQ_REQUIRE_RESIZABLE_BROWSER === '1' && skipReason()" in browser
    assert 'throw new Error(`required resizable browser unavailable:' in browser
    assert 'timeout: 60000' in browser


@pytest.mark.parametrize('host_version', ['3.10.22', '3.12.10'])
@pytest.mark.parametrize('case', ['critical', 'parallel', 'barrier', 'cli'])
def test_windows_runtime_fakes_are_independent_of_host(monkeypatch, receipts, host_version, case):
    import os
    import platform
    import posixpath
    import sys
    original_executable, original_path = sys.executable, os.path
    host = RuntimeProxy(platform, python_version=lambda: host_version)
    monkeypatch.setitem(sys.modules, 'platform', host)
    monkeypatch.setattr(suite, 'platform', host)
    for consumer in (suite, cs):
        monkeypatch.setattr(consumer, 'sys', RuntimeProxy(sys, executable=f'/host/{host_version}/python'))
        monkeypatch.setattr(consumer, 'os', RuntimeProxy(os, path=posixpath))
    if case in ('critical', 'parallel'):
        test_real_collection_hook_keeps_actual_phase_selected_nodes_only(monkeypatch, receipts, case)
    elif case == 'barrier':
        test_parallel_plugin_refuses_to_start_without_verified_critical_barrier(monkeypatch, receipts)
    else:
        test_identity_cli_creates_fresh_directory_before_pytest_with_fake_git(monkeypatch, receipts)
    assert suite.platform.python_version() == '3.12.10'
    assert suite.sys.executable == cs.sys.executable == WINDOWS_PYTHON
    assert host.python_version() == host_version
    assert sys.executable == original_executable and os.path is original_path


@pytest.mark.parametrize('version,accepted', [('3.10.22', False), ('3.12.10', True)])
def test_actual_windows_identity_keeps_python312_requirement(version, accepted):
    context = fake_context()
    context['python_version'] = version
    if accepted:
        assert p.validate_identity(context) == context
    else:
        with pytest.raises(ValueError, match='identity interpreter'):
            p.validate_identity(context)
