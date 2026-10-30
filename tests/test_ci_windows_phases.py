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


def make_receipts(folder, context=None, *, extra_native=()):
    context = context or fake_context()
    folder.mkdir(exist_ok=True)
    p.write(folder/'identity.json', context)
    critical, parallel = fake_inventory()
    critical += list(extra_native)
    eligible = critical + parallel
    full = eligible + ['tests/test_pure.py::test_text']
    for phase, selected, count, start, end in (('critical', critical, 0, 10, 56), ('parallel', parallel, 4, 57, 400)):
        directory = folder/phase
        directory.mkdir(exist_ok=True)
        workers = ['master'] if count == 0 else ['gw' + str(i) for i in range(count)]
        coverage = dict(profile=context['profile'], context=context, phase=phase, selected_nodes=selected)
        if phase == 'critical':
            coverage.update(collection_scope='critical-targets-v2', collection_args=list(p.TARGETS),
                            collected_nodes=critical)
        else:
            coverage.update(collection_scope='global-v2', collection_args=['tests'],
                full_nodes=full, eligible_nodes=eligible, excluded_files={'tests/test_pure.py': 'pure fixture'})
        reports = [[node, stage, 'passed'] for node in selected for stage in ('setup', 'call', 'teardown')]
        for worker in set(workers) | {'master'}:
            own = reports if worker == 'master' else [r for r in reports if selected.index(r[0]) % 4 == int(worker[2:])]
            p.write(directory/('metrics-' + worker + '.json'), dict(profile=context['profile'], context=context,
                phase=phase, worker=worker, workers=count, started_at=start, finished_at=end, exitstatus=0,
                stage_reports=own, subtest_reports=[], host_resource_wait_s={}, longest_files=[]))
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


@pytest.mark.parametrize('case', ['subtests', 'failed', 'orphan', 'foreign', 'worker-lost', 'rerun'])
def test_subtest_results_are_not_a_second_node_execution_but_a_rerun_still_is(receipts, case):
    # #854: run 39681301430 stopped at "duplicate node execution" after pytest 9 reported unittest subTests
    # as extra call reports of one node. They are kept apart and counted; a real second call still fails.
    node, skipped = fake_inventory()[1][1], fake_inventory()[1][2]
    rows = [[node, 'passed'], [node, 'passed']]  # two subTests of one node: both kept, never deduplicated
    if case == 'failed':
        rows[1][1] = 'failed'
    elif case == 'orphan':
        rows = [[skipped, 'passed']]  # no passed call of that node to belong to
    elif case == 'foreign':
        rows = [['tests/test_other.py::test_unknown', 'passed']]
    for worker in ('master', 'gw1', 'gw2'):
        def mutate(value):
            if case == 'orphan' and worker in ('master', 'gw2'):
                value['stage_reports'] = [r for r in value['stage_reports'] if not (r[0] == skipped and r[1] == 'call')]
                next(r for r in value['stage_reports'] if r[0] == skipped and r[1] == 'setup')[2] = 'skipped'
            if case == 'rerun' and worker in ('master', 'gw1'):
                value['stage_reports'].append([node, 'call', 'passed'])
            elif worker != 'gw2' and not (case == 'worker-lost' and worker == 'gw1'):
                value['subtest_reports'] = copy.deepcopy(rows)
        change(receipts/f'parallel/metrics-{worker}.json', mutate)
    if case == 'subtests':
        proof = p.combine(receipts)
        assert proof['phases']['parallel']['passed'] == 4 and proof['phases']['parallel']['skipped'] == 0
        return
    with pytest.raises(ValueError, match='duplicate node execution' if case == 'rerun' else 'subtest|worker'):
        p.combine(receipts)


def test_the_observer_keeps_a_subtest_report_out_of_the_node_lifecycle():
    subtests = pytest.importorskip('_pytest.subtests')
    observation = SimpleNamespace(diagnostics=None, checkpoints=None, stage_reports=[], subtest_reports=[],
                                  context='', files=suite.defaultdict(float), results=suite.defaultdict(set))
    for outcome in ('passed', 'failed'):
        report = subtests.SubtestReport('t::x', ('t', 0, 'x'), {}, outcome, None, 'call')
        suite.Observation.pytest_runtest_logreport(observation, report)
    suite.Observation.pytest_runtest_logreport(observation, fake_report('t::x'))
    assert observation.stage_reports == [['t::x', 'call', 'passed']]
    assert observation.subtest_reports == [['t::x', 'passed'], ['t::x', 'failed']]
    assert observation.results['failed'] == {'t::x'}  # a failed subtest is never hidden


@pytest.mark.parametrize('phase', ['critical', 'parallel'])
def test_real_collection_hook_keeps_actual_phase_selected_nodes_only(monkeypatch, receipts, phase):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(suite, 'time', RuntimeProxy(suite.time, monotonic=lambda: clock.now))
    def fake_version(name):
        assert name == 'pytest-xdist'
        return 'fake-xdist'  # Fake worker metrics do not require installed worker machinery.
    monkeypatch.setattr(suite, 'importlib', RuntimeProxy(suite.importlib,
        metadata=RuntimeProxy(suite.importlib.metadata, version=fake_version)))
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
    initial = critical if phase == 'critical' else full
    items = [SimpleNamespace(path=receipts/n.split('::', 1)[0], nodeid=n) for n in initial]
    def select(*args):
        assert phase == 'parallel', 'critical must not read or forge a global selection'
        return items[:-1], items[-1:], {'tests/test_pure.py': 'pure fixture'}
    monkeypatch.setattr(suite, 'select', select)
    monkeypatch.setattr(suite, 'policy', lambda: dict(boundary_cohorts={f: 'native' for f in p.FILES},
                                                   mandatory_nodes=list(p.MANDATORY)))
    deselected = []
    config = fake_collection_config(phase)
    config.getoption = lambda key: dict(ci_profile=p.PROFILE, ci_phase=phase).get(key)
    config.hook = SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    observer = suite.Observation(config)
    manager = pytest.PytestPluginManager()
    manager.register(observer)
    manager.check_pending()  # Validate real hook signatures, without running pytest/xdist children.
    observer.pytest_sessionstart()
    collection = observer.pytest_collection(None)
    next(collection)
    clock.now += 85
    if phase == 'critical':
        with pytest.raises(pytest.UsageError):
            observer.pytest_xdist_setupnodes(config, [None] * 4)
    else:
        with pytest.raises(pytest.UsageError):
            observer.pytest_xdist_setupnodes(config, [None] * 3)
        observer.pytest_xdist_setupnodes(config, [None] * 4)
    observer.pytest_collection_modifyitems(None, config, items)
    clock.now += 1
    with pytest.raises(StopIteration):
        next(collection)
    expected = critical if phase == 'critical' else parallel
    assert [i.nodeid for i in items] == expected
    saved = p.read(receipts/phase/'collection-master.json')
    assert saved['selected_nodes'] == expected
    if phase == 'critical':
        assert saved['collected_nodes'] == critical and saved['collection_args'] == list(p.TARGETS)
        assert not {'full_nodes', 'eligible_nodes', 'excluded_files'} & saved.keys()
    else:
        assert saved['eligible_nodes'] == critical + parallel and saved['full_nodes'] == full
    assert set(i.nodeid for i in deselected) == set(initial) - set(expected)
    # Actual hook events are retained per stage, not a synthetic expanded PASS.
    for node in expected:
        observer.pytest_runtest_logstart(node, None)
        for stage in ('setup', 'call', 'teardown'):
            getattr(observer, 'pytest_runtest_' + stage)(SimpleNamespace(nodeid=node))
            clock.now += .01
            observer.pytest_runtest_logreport(SimpleNamespace(nodeid=node, when=stage,
                outcome='passed', duration=.001, failed=False, skipped=False, passed=True))
    assert observer.stage_reports == [[n, stage, 'passed'] for n in expected for stage in ('setup', 'call', 'teardown')]
    observer.pytest_sessionfinish(None, 0)
    timing = p.read(receipts/phase/'checkpoints-master.json')
    assert timing['checkpoints_s']['selection_finished'] == 85
    assert timing['checkpoints_s']['collection_finished'] == timing['checkpoints_s']['first_test_started'] == 86
    assert timing['checkpoints_s']['session_finished'] > 86
    assert timing['completed_reports'] == dict.fromkeys(('setup', 'call', 'teardown'), len(expected))
    assert timing['current']['stage'] == 'teardown' and timing['current']['state'] == 'completed'
    assert timing['diagnostic_only'] and timing['reusable'] is False


def test_parallel_plugin_refuses_to_start_without_verified_critical_barrier(monkeypatch, receipts):
    context = p.read(receipts/'identity.json')
    context.update(fake_windows_runtime(monkeypatch))
    make_receipts(receipts, context)
    for key, value in dict(CI_SUITE_ARTIFACT_DIR=str(receipts), GITHUB_SHA=context['checkout_sha'],
        GITHUB_WORKFLOW_SHA=context['workflow_sha'], GITHUB_WORKFLOW_REF=context['workflow_ref'],
        GITHUB_REPOSITORY=context['repository'], GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1').items():
        monkeypatch.setenv(key, value)
    (receipts/'critical-success.json').unlink()
    config = fake_collection_config('parallel')
    config.getoption = lambda key: dict(ci_profile=p.PROFILE, ci_phase='parallel').get(key)
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
    import shlex
    command = shlex.split(steps[critical]['run'])
    assert command[command.index('critical') + 1:] == list(p.TARGETS)
    assert steps[critical]['timeout-minutes'] == 3 and '|| 13' in steps[parallel]['timeout-minutes']
    assert shlex.split(steps[parallel]['run'])[-1] == 'tests'
    assert '-n 4 --dist loadfile' in steps[parallel]['run']
    assert not any(x.get('continue-on-error') or x.get('if') for x in steps[critical:parallel+1])
    assert '|| 18' in job['timeout-minutes'] and 'windows-full-v1' in job['env']['CI_COVERAGE_PROFILE']
    assert 'receipt' in job['outputs'] and 'execution' in job['outputs']
    manifest = jobs['validation-manifest']['steps']
    assert any(x.get('uses') == 'actions/download-artifact@v4' and
               x['with']['artifact-ids'] == '${{ steps.windows-artifact.outputs.artifact-id }}'
               and x['with']['merge-multiple'] is True for x in manifest)
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


@pytest.mark.parametrize('phase,worker', [('critical', 'master'), ('parallel', 'gw2')])
def test_monotonic_checkpoints_separate_collection_calls_and_cleanup(tmp_path, phase, worker):
    clock = SimpleNamespace(now=1000.0)
    path = tmp_path / f'checkpoints-{worker}.json'
    checkpoints = suite.PhaseCheckpoints(path, fake_context(), phase, worker, clock=lambda: clock.now)
    clock.now += 2
    checkpoints.mark('session_started')
    clock.now += 1
    checkpoints.mark('collection_started')
    clock.now += 85
    checkpoints.mark('selection_finished')
    clock.now += 1
    checkpoints.mark('collection_finished')
    checkpoints.mark('first_test_started')
    item = 'tests/test_fake.py::test_owned'
    for stage in ('setup', 'call'):
        checkpoints.stage(item, stage, 'started')
        clock.now += 2
        checkpoints.stage(item, stage, 'completed')
    checkpoints.stage(item, 'teardown', 'started')
    # No teardown report or sessionfinish: an interrupted cleanup is not PASS.
    checkpoints.flush()
    saved = p.read(path)
    assert saved['diagnostic_only'] is True and saved['reusable'] is False
    assert saved['phase'] == phase and saved['worker'] == worker
    assert saved['context'] == fake_context()
    assert saved['clock'] == 'process-local monotonic elapsed seconds'
    assert saved['checkpoints_s'] == dict(configured=0, session_started=2,
        collection_started=3, selection_finished=88, collection_finished=89, first_test_started=89)
    assert saved['completed_reports'] == dict(setup=1, call=1, teardown=0)
    assert saved['current'] == dict(nodeid=item, nodeid_truncated=False, stage='teardown',
                                    state='started', elapsed_s=93)
    assert 'session_finished' not in saved['checkpoints_s']
    assert 'exitstatus' not in saved and 'results' not in saved


def test_interrupted_collection_has_no_invented_finish_or_test_time(tmp_path):
    path = tmp_path / 'checkpoints-master.json'
    clock = SimpleNamespace(now=12.0)
    checkpoints = suite.PhaseCheckpoints(path, fake_context(), 'critical', 'master', clock=lambda: clock.now)
    clock.now += 4
    checkpoints.mark('collection_started')
    saved = p.read(path)
    assert saved['checkpoints_s'] == dict(configured=0, collection_started=4)
    assert saved['current'] is None and saved['completed_reports'] == dict(setup=0, call=0, teardown=0)
    assert 'collection_finished' not in saved['checkpoints_s']
    assert 'first_test_started' not in saved['checkpoints_s']


def test_parallel_diagnostics_are_bounded_and_throttled_but_keep_observed_counts(tmp_path):
    clock = SimpleNamespace(now=100.0)
    path = tmp_path / 'checkpoints-gw0.json'
    checkpoints = suite.PhaseCheckpoints(path, fake_context(), 'parallel', 'gw0', clock=lambda: clock.now)
    for i in range(1000):
        checkpoints.stage('n' * 2000 + str(i), 'call', 'started')
        checkpoints.stage('n' * 2000 + str(i), 'call', 'completed')
    assert p.read(path)['completed_reports']['call'] < 1000  # No per-report disk write in the pool.
    clock.now += 5
    checkpoints.stage('last', 'teardown', 'started')
    saved = p.read(path)
    assert saved['completed_reports']['call'] == 1000
    assert saved['current']['stage'] == 'teardown' and saved['current']['state'] == 'started'
    checkpoints.stage('n' * 2000, 'teardown', 'completed')
    checkpoints.mark('session_finished')
    saved = p.read(path)
    assert len(saved['current']['nodeid']) == 512 and saved['current']['nodeid_truncated'] is True
    assert saved['checkpoints_s']['session_finished'] == 5
    assert len(path.read_bytes()) < 10000


def test_diagnostic_json_cannot_replace_or_modify_phase_receipt_proof(receipts):
    expected = p.combine(receipts)
    for phase in ('critical', 'parallel'):
        # Untrusted diagnostic content is not read as a receipt, even if malformed.
        (receipts / phase / 'checkpoints-master.json').write_text('{not a receipt', encoding='utf-8')
    assert p.combine(receipts) == expected
    (receipts / 'critical' / 'metrics-master.json').unlink()
    with pytest.raises((OSError, ValueError)):
        p.combine(receipts)


def test_diagnostic_write_failure_does_not_mask_an_interrupted_stage(tmp_path, capsys):
    checkpoints = suite.PhaseCheckpoints(tmp_path/'missing'/'checkpoints-master.json',
                                        fake_context(), 'critical', 'master', clock=lambda: 42)
    checkpoints.stage('test_owned', 'teardown', 'started')
    assert checkpoints.data['current']['state'] == 'started'
    assert checkpoints.data['completed_reports']['teardown'] == 0
    assert capsys.readouterr().err.count('checkpoint write unavailable') == 1


def fake_report(nodeid, when='call', outcome='passed', duration=0.01, longrepr=None):
    return SimpleNamespace(nodeid=nodeid, when=when, outcome=outcome, duration=duration, longrepr=longrepr,
                           passed=outcome == 'passed', failed=outcome == 'failed', skipped=outcome == 'skipped')


def crash(message):
    return SimpleNamespace(reprcrash=SimpleNamespace(message=message))


def test_the_diagnostic_write_survives_a_test_that_patches_path_open_to_fail(tmp_path, monkeypatch):
    # #998: a test guarding Path.open with pytest.fail made the call-phase report flush raise Failed (not an OSError)
    # out of pytest_runtest_logreport, which xdist reports as INTERNALERROR. The flush must not use Path.open.
    path = tmp_path / 'diagnostics-gw0.json'
    diagnostics = suite.StageDiagnostics(path, 'parallel', 'gw0', clock=lambda: 0, interval=0)
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('guarded input opened'))
    monkeypatch.setattr(Path, 'write_text', lambda *a, **k: pytest.fail('Path.write_text used'))
    diagnostics.record(fake_report('tests/test_fake.py::test_ok', duration=0.5))
    monkeypatch.undo()
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['reports'] == 1 and diagnostics.warned is False


def test_a_failure_is_on_disk_with_stage_duration_and_first_line_before_the_session_ends(tmp_path):
    clock = SimpleNamespace(now=50.0)
    path = tmp_path / 'diagnostics-gw3.json'
    diagnostics = suite.StageDiagnostics(path, 'parallel', 'gw3', clock=lambda: clock.now)
    clock.now += 1
    diagnostics.record(fake_report('tests/test_fake.py::test_ok', duration=0.5))
    clock.now += 2
    message = ('asyncio.exceptions.TimeoutError: waited\x1b[31m ghp_' + 'a' * 30 + ' Bearer abc.def '
               + 'x' * 400 + '\nsecond line with a payload')
    diagnostics.record(fake_report('tests/test_fake.py::test_slow_fallback', outcome='failed', duration=2.25,
                                   longrepr=crash(message)))
    # No interval passed and no sessionfinish: the failure was written at once (a cancelled pool keeps it).
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['diagnostic_only'] is True and saved['reusable'] is False and saved['session_finished'] is False
    assert saved['schema'] == 'ci-stage-diagnostics-v1' and saved['phase'] == 'parallel' and saved['worker'] == 'gw3'
    failure, = saved['failures']
    assert failure['nodeid'] == 'tests/test_fake.py::test_slow_fallback' and failure['stage'] == 'call'
    assert failure['outcome'] == 'failed' and failure['duration_s'] == 2.25 and failure['ended_s'] == 3
    assert failure['kind'] == 'asyncio.exceptions.TimeoutError'
    assert len(failure['message']) == 300 and failure['message_truncated'] is True
    assert 'ghp_' not in failure['message'] and 'abc.def' not in failure['message'] and '\x1b' not in failure['message']
    assert 'second line' not in json.dumps(saved)  # only the first line, never the traceback or captured output
    assert [row['nodeid'] for row in saved['slowest']] == ['tests/test_fake.py::test_slow_fallback',
                                                          'tests/test_fake.py::test_ok']
    diagnostics.finish(2)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['session_finished'] is True and saved['exitstatus'] == 2
    assert not list(tmp_path.glob('.*.tmp'))  # the whole file was replaced, no partial write left behind


@pytest.mark.parametrize('longrepr,kind,message', [
    ('E   assert 1 == 2\nE   +  where 1\n\ntests/test_fake.py:9: AssertionError', None,
     'tests/test_fake.py:9: AssertionError'),  # a location is not an exception kind
    (crash('assert [1] == [2]'), None, 'assert [1] == [2]'),
    (None, None, ''),
])
def test_a_failure_without_a_crash_line_keeps_only_a_bounded_last_line(tmp_path, longrepr, kind, message):
    diagnostics = suite.StageDiagnostics(tmp_path / 'diagnostics-master.json', None, 'master', clock=lambda: 0)
    diagnostics.record(fake_report('t::x', when='setup', outcome='failed', longrepr=longrepr))
    failure, = diagnostics.data['failures']
    assert (failure['kind'], failure['message'], failure['stage']) == (kind, message, 'setup')


def test_stage_diagnostics_are_bounded_deduplicated_and_throttled(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    path = tmp_path / 'diagnostics-gw0.json'
    monkeypatch.setattr(suite.StageDiagnostics, 'MAX_FAILURES', 3)
    diagnostics = suite.StageDiagnostics(path, 'parallel', 'gw0', clock=lambda: clock.now)
    for i in range(200):
        diagnostics.record(fake_report(str(i) + 'n' * 2000, duration=float(i)))
    assert json.loads(path.read_text(encoding='utf-8'))['reports'] < 200  # passed stages do not write each time
    for bad in (float('nan'), float('inf'), -1.0, True, '3', None, 1e9):
        diagnostics.record(fake_report(f'bad::{bad!r}', duration=bad))
    for i in range(5):
        diagnostics.record(fake_report(f'f::{i}', outcome='failed', duration=0.0, longrepr=crash('E')))
    diagnostics.record(fake_report('f::0', outcome='failed', duration=0.0, longrepr=crash('E')))
    diagnostics.finish(1)  # dropped failures beyond the cap wait for the normal throttle or the session end
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert len(saved['failures']) == 3 and saved['failures_dropped'] == 2 and saved['duplicate_reports'] == 1
    assert len(saved['slowest']) == 30 and saved['slowest'][0]['duration_s'] == 199.0
    assert [row['duration_s'] for row in saved['slowest']] == sorted((row['duration_s'] for row in saved['slowest']),
                                                                    reverse=True)
    assert all(len(row['nodeid']) == 512 and row['nodeid_truncated'] for row in saved['slowest'])
    assert not any(row['nodeid'].startswith('bad::') for row in saved['slowest'])
    assert saved['reports'] == 200 + 7 + 5 and len(path.read_bytes()) < 100_000


def test_long_ids_sharing_their_first_512_characters_stay_distinct_failures(tmp_path):
    diagnostics = suite.StageDiagnostics(tmp_path / 'diagnostics-gw1.json', 'parallel', 'gw1', clock=lambda: 0)
    prefix = 'tests/test_fake.py::test_param[' + 'p' * 600
    for suffix in ('a]', 'b]', 'a]'):  # two parameters, then the first one's report again
        diagnostics.record(fake_report(prefix + suffix, outcome='failed', longrepr=crash('AssertionError')))
    failures = diagnostics.data['failures']
    assert len(failures) == 2 and diagnostics.data['duplicate_reports'] == 1
    assert all(len(row['nodeid']) == 512 and row['nodeid_truncated'] for row in failures)
    assert failures[0]['nodeid'] == failures[1]['nodeid']  # the written ids are cut alike ...
    assert failures[0]['nodeid_sha256'] != failures[1]['nodeid_sha256']  # ... their identities are not


# Built at run time so that no credential-shaped literal is in this file (scripts/check_public.sh reads it).
SECRET_SHAPES = {
    'github': 'gh' + 'p_' + 'A1' * 12,
    'github_pat': 'github' + '_pat_' + 'B2' * 12,
    'openai': 'sk' + '-' + 'C3d4' * 6,
    'aws': 'AK' + 'IA' + 'Q' * 16,
    'google': 'AI' + 'za' + 'E5' * 18,
    'slack': 'xo' + 'xb-' + '1234567890-abc',
    'private_key': '-----' + 'BEGIN ' + 'RSA PRIVATE' + ' KEY----- MIIEow',
    'bearer': 'Bear' + 'er ' + 'eyJ.token.value',
}


@pytest.mark.parametrize('name', sorted(SECRET_SHAPES))
def test_a_crash_line_hides_the_credential_shapes_the_public_check_refuses(tmp_path, name):
    secret = SECRET_SHAPES[name]
    diagnostics = suite.StageDiagnostics(tmp_path / 'diagnostics-master.json', None, 'master', clock=lambda: 0)
    diagnostics.record(fake_report('t::x', outcome='failed',
                                   longrepr=crash(f'RuntimeError: request failed with {secret} after 3 tries')))
    failure, = diagnostics.data['failures']
    assert failure['kind'] == 'RuntimeError' and '[redacted]' in failure['message']
    assert secret not in failure['message'] and secret.split()[-1] not in failure['message']
    assert secret not in (tmp_path / 'diagnostics-master.json').read_text(encoding='utf-8')


def test_redaction_reads_a_bounded_line_and_keeps_the_300_character_output(tmp_path):
    diagnostics = suite.StageDiagnostics(tmp_path / 'diagnostics-master.json', None, 'master', clock=lambda: 0)
    line = 'AssertionError: ' + 'sk' + '-' + 'z' * 200_000  # one long credential-shaped run, no backtracking
    diagnostics.record(fake_report('t::x', outcome='failed', longrepr=crash(line)))
    failure, = diagnostics.data['failures']
    assert failure['message'] == 'AssertionError: [redacted]' and failure['message_truncated'] is True


def test_a_stage_diagnostics_write_failure_never_changes_the_run(tmp_path, capsys):
    diagnostics = suite.StageDiagnostics(tmp_path / 'missing' / 'diagnostics-master.json', None, 'master',
                                         clock=lambda: 0)
    diagnostics.record(fake_report('t::x', outcome='failed', longrepr=crash('AssertionError')))
    diagnostics.finish(1)
    assert diagnostics.data['failures'][0]['kind'] == 'AssertionError'
    assert capsys.readouterr().err.count('stage diagnostics write unavailable') == 1

    class Broken:
        def record(self, report):
            raise RuntimeError('diagnostic bug')

        def finish(self, exitstatus):
            raise RuntimeError('diagnostic bug')

    observation = SimpleNamespace(diagnostics=Broken(), checkpoints=None, stage_reports=[], context='',
                                  files=suite.defaultdict(float), results=suite.defaultdict(set))
    report = fake_report('t::x', outcome='failed', duration=1.0)
    suite.Observation.pytest_runtest_logreport(observation, report)
    assert observation.stage_reports == [['t::x', 'call', 'failed']] and observation.results['failed'] == {'t::x'}


def test_stage_diagnostics_cannot_replace_or_modify_phase_receipt_proof(receipts):
    expected = p.combine(receipts)
    for phase in ('critical', 'parallel'):
        for worker in ('master', 'gw0'):
            (receipts / phase / f'diagnostics-{worker}.json').write_text('{not a receipt', encoding='utf-8')
        suite.StageDiagnostics(receipts / phase / 'diagnostics-gw1.json', phase, 'gw1', clock=lambda: 0).record(
            fake_report('t::x', outcome='failed', longrepr=crash('AssertionError')))
    assert p.combine(receipts) == expected


def fake_collection_config(phase, **options):
    return SimpleNamespace(rootpath=suite.ROOT, args=list(p.TARGETS) if phase == 'critical' else ['tests'],
        option=SimpleNamespace(**options), getini=lambda key: {
            'python_files': ['test_*.py', '*_test.py'], 'python_classes': ['Test'],
            'python_functions': ['test']}.get(key))


@pytest.mark.parametrize('phase', ['critical', 'parallel'])
def test_phase_invocation_binds_complete_explicit_collection_without_filters(monkeypatch, phase):
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    config = fake_collection_config(phase)
    assert suite.collection_arguments(config, phase) == config.args
    config.args = config.args[:-1]
    with pytest.raises(ValueError, match='collection arguments'):
        suite.collection_arguments(config, phase)


@pytest.mark.parametrize('option,value', [('keyword', 'not native'), ('markexpr', 'not windows'),
    ('deselect', ['tests/test_windows_job_ci.py::test_owned']), ('ignore', [p.FILES[0]]),
    ('ignore_glob', ['*job*']), ('lf', True), ('collectonly', True), ('pyargs', True),
    ('override_ini', ['python_functions=test_subset']), ('stepwise', True)])
def test_critical_invocation_rejects_collection_reduction(monkeypatch, option, value):
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    with pytest.raises(ValueError, match='collection filter'):
        suite.collection_arguments(fake_collection_config('critical', **{option: value}), 'critical')


def test_critical_automatically_includes_new_native_cases_without_accepting_old_execution():
    critical, _ = fake_inventory()
    extra = p.FILES[0] + '::test_new_native_parameter[new]'
    assert p.critical(critical + [extra], critical + [extra]) == critical + [extra]
    identity = fake_context()
    identity['execution'] = 'windows-critical-serial-v1'
    with pytest.raises(ValueError, match='execution policy'):
        p.validate_identity(identity)


def test_narrow_critical_uses_global_pool_inventory_without_requiring_equal_collection_order(receipts):
    for path in (receipts/'parallel').glob('collection-*.json'):
        change(path, lambda value: value.update(full_nodes=list(reversed(value['full_nodes'])),
                                                eligible_nodes=list(reversed(fake_inventory()[0])) + fake_inventory()[1]))
    proof = p.combine(receipts)
    global_coverage = p.read(receipts/'parallel/collection-gw0.json')
    assert proof['full_digest'] == p.digest(global_coverage['full_nodes'])
    assert proof['selected_digest'] == p.digest(global_coverage['eligible_nodes'])
    critical_coverage = p.read(receipts/'critical/collection-master.json')
    assert set(critical_coverage['collected_nodes']) == set(fake_inventory()[0])
    assert 'full_nodes' not in critical_coverage and proof['selected_node_count'] == 30


def test_added_native_cases_are_required_in_both_actual_collections_and_pass_barrier(tmp_path):
    extra = p.FILES[1] + '::test_new_native_case[extra]'
    proof = make_receipts(tmp_path, extra_native=[extra])
    assert proof['phases']['critical']['selected'] == proof['phases']['critical']['passed'] == 27
    assert proof['selected_node_count'] == 31
    p.check_ready(tmp_path, p.read(tmp_path/'identity.json'))
    # Independent global discovery of another case must not certify an earlier
    # smaller critical run, even if its valid barrier and every pool result exist.
    for path in (tmp_path/'parallel').glob('collection-*.json'):
        def add(value):
            for key in ('full_nodes', 'eligible_nodes'):
                value[key].append(p.FILES[0] + '::test_lost_new_native')
        change(path, add)
    with pytest.raises(ValueError, match='critical/global collection drift'):
        p.combine(tmp_path)


@pytest.mark.parametrize('case', ['extra-global-fields', 'wrong-targets', 'foreign', 'missing', 'substituted'])
def test_narrow_phase_scope_or_changed_contract_never_certifies_global_coverage(receipts, case):
    coverage = receipts/'critical/collection-master.json'
    def alter(value):
        if case == 'extra-global-fields':
            value.update(full_nodes=value['collected_nodes'], eligible_nodes=value['selected_nodes'], excluded_files={})
        elif case == 'wrong-targets':
            value['collection_args'][0] += '::test_filtered'
        else:
            if case == 'missing':
                value['collected_nodes'].pop(3)
                value['selected_nodes'].pop(3)
            else:
                replacement = 'tests/test_foreign.py::test_other' if case == 'foreign' else p.FILES[0] + '::test_other_parameter[x]'
                for key in ('collected_nodes', 'selected_nodes'):
                    value[key][3] = replacement
    change(coverage, alter)
    if case == 'substituted':
        # All lifecycle reports match the substituted local collection: only
        # the independent global inventory can detect this equal-count drift.
        node = p.read(coverage)['selected_nodes'][3]
        def reports(value):
            for row in value['stage_reports']:
                if row[0] == fake_inventory()[0][3]:
                    row[0] = node
        change(receipts/'critical/metrics-master.json', reports)
        p.ready(receipts)
    with pytest.raises(ValueError):
        p.combine(receipts)


@pytest.mark.parametrize('key', ['checkout_sha', 'head_sha', 'workflow_sha', 'workflow_ref', 'invocation',
                               'tree', 'policy', 'run_id', 'attempt', 'python_executable'])
def test_narrow_receipt_remains_bound_to_every_phase_identity(receipts, key):
    def alter(value):
        value['context'][key] = 99 if key in ('run_id', 'attempt') else 'changed'
    change(receipts/'critical/collection-master.json', alter)
    with pytest.raises(ValueError):
        p.ready(receipts)
    with pytest.raises(ValueError):
        p.combine(receipts)


def test_collection_options_cannot_hide_requirements_in_environment_or_ini(monkeypatch):
    config = fake_collection_config('critical')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k not_native')
    with pytest.raises(ValueError, match='PYTEST_ADDOPTS'):
        suite.collection_arguments(config, 'critical')
    monkeypatch.delenv('PYTEST_ADDOPTS')
    config.getini = lambda key: ['test_subset']
    with pytest.raises(ValueError, match='python_files'):
        suite.collection_arguments(config, 'critical')


def test_critical_invocation_cannot_collect_another_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    config = fake_collection_config('critical')
    config.rootpath = tmp_path
    with pytest.raises(ValueError, match='collection root'):
        suite.collection_arguments(config, 'critical')
