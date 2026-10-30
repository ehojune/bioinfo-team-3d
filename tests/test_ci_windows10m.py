"""Essential Windows policy contracts: pure data/fake Git, no native launch."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_selection as s, ci_suite as suite, ci_skip as cs
from tests.test_ci_cohort823 import A, H, event, fake_git, policy_file
from tests.test_ci_skip_policy import evidence, API, archive, reuse


def test_clean_install_diagnostics_is_reviewed_inventory_without_new_native_cohort():
    from fnmatch import fnmatchcase
    data = s.validate_profile()  # Source/AST reads only; never import or run diagnostics tests.
    path = 'tests/test_clean_install_diagnostics.py'
    assert path in data['known_tests']
    assert path not in data['mixed_nodes']
    assert not any(fnmatchcase(path, pattern) for pattern in data['boundary_cohorts'])


def test_routine_job_is_bounded_and_has_one_bounded_suite_with_diagnostic_tail():
    jobs = yaml.safe_load((s.ROOT / cs.WORKFLOW).read_text(encoding='utf-8'))['jobs']
    job = jobs['pytest-windows']
    bound = job['timeout-minutes']
    assert 'schedule' in bound and 'workflow_dispatch' in bound and '|| 14' in bound
    assert '360' in bound  # Preserve GitHub's previous default for full nightly/manual.
    tests = [x for x in job['steps'] if x.get('run', '').startswith('pytest ')]
    # Two mandatory cold-browser viewports each have a 60 s hard cap; leave native tests and collection room.
    assert len(tests) == 2 and tests[0]['timeout-minutes'] == 3
    assert '|| 9' in tests[1]['timeout-minutes']  # 1.5x the measured worst successful pool step (#931)
    assert '--ci-phase critical' in tests[0]['run'] and '--ci-phase parallel' in tests[1]['run']
    assert all('continue-on-error' not in step for step in tests)
    assert not any('preview' in str(x).lower() for x in job['steps'])
    assert 'boundary_preview' not in jobs['changes']['outputs']
    tail = job['steps'][job['steps'].index(tests[0]) + 1:]
    assert any(x.get('if') == 'success()' and 'ci_suite report' in x.get('run', '') for x in tail)
    assert any(x.get('if') == 'always()' and x.get('uses', '').startswith('actions/upload-artifact') for x in tail)
    assert jobs['pytest']['strategy']['matrix']['python-version'] == ['3.10', '3.12']
    assert 'windows-full-v1' in job['env']['CI_COVERAGE_PROFILE']


@pytest.mark.parametrize('selection,pytest_outcome,annotated,reported', [
    ('failure', 'skipped', True, False),
    ('success', 'failure', False, False),
    ('success', 'success', False, True),
    ('skipped', 'success', False, True),  # Full Windows has no changed-test selection step.
    ('success', 'cancelled', False, False),
])
def test_windows_failure_routing_preserves_primary_failure_and_success_receipts(
        selection, pytest_outcome, annotated, reported):
    job = yaml.safe_load((s.ROOT / cs.WORKFLOW).read_text(encoding='utf-8'))['jobs']['pytest-windows']
    steps = job['steps']
    select = next(x for x in steps if x.get('id') == 'selection')
    annotation = next(x for x in steps if '::error title=Windows selection rejected::' in x.get('run', ''))
    report = next(x for x in steps if x.get('id') == 'coverage')
    upload = next(x for x in steps if x.get('uses', '').startswith('actions/upload-artifact'))
    assert select['run'] == 'python -m scripts.ci_selection'
    assert select['if'] == "env.CI_COVERAGE_PROFILE != 'windows-full-v1'"
    assert steps.index(select) < steps.index(annotation) < steps.index(report) < steps.index(upload)
    assert not job.get('continue-on-error')
    assert all(not x.get('continue-on-error') for x in steps)
    assert report['run'] == 'python -m scripts.ci_suite report'
    assert job['outputs']['receipt'] == '${{ steps.coverage.outputs.receipt }}'
    assert job['outputs']['execution'] == '${{ steps.coverage.outputs.execution }}'
    # Interpret only these status predicates; fail on a workflow expression we have not reviewed.
    # A later successful annotation cannot erase the failed selection or pytest step.
    failure = 'failure' in (selection, pytest_outcome)
    success = not failure and 'cancelled' not in (selection, pytest_outcome)
    conditions = {
        "failure() && steps.selection.outcome == 'failure'": failure and selection == 'failure',
        'success()': success,
        'always()': True,
    }
    assert conditions[annotation['if']] is annotated
    assert conditions[report['if']] is reported
    assert conditions[upload['if']] is True  # Preserve diagnostic artifacts even after failure.


@pytest.mark.parametrize('paths', [(s.PROFILE,), (cs.WORKFLOW,), ('scripts/ci_suite.py',),
    ('tests/conftest.py',), ('pyproject.toml',), ('tests/helpers.py',)])
def test_valid_policy_or_shared_change_never_turns_the_routine_range_into_full(paths):
    git, _ = fake_git(paths)
    result = s.change_range('pull_request', event(), H, git)
    assert result == {'version': 1, 'checkout': H, 'base': A, 'paths': list(paths)}


def test_missing_or_invalid_evidence_is_a_failure_not_a_full_run(tmp_path, monkeypatch):
    source = 'def test_math(): assert 1 + 1 == 2'
    policy_file(tmp_path, source)
    path = tmp_path / 'tests/test_math.py'
    path.parent.mkdir(exist_ok=True)
    path.write_text(source, encoding='utf-8')
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path))
    monkeypatch.setenv('GITHUB_SHA', H)
    with pytest.raises(pytest.UsageError, match='selection'):
        suite.select([SimpleNamespace(path=path, nodeid='tests/test_math.py::test_math')],
                     s.WINDOWS_BOUNDARY, tmp_path)


def test_changed_pure_ci_test_does_not_expand_to_unrelated_runtime_tests(tmp_path):
    source = 'def test_math(): assert 1 + 1 == 2'
    data = policy_file(tmp_path, source)
    data['mixed_nodes'] = {}
    assert not s.include_windows('tests/test_math.py', source, data, root=tmp_path,
                                 changed={'tests/test_math.py', cs.WORKFLOW}, node='test_math')[0]
    native = 'import sys\ndef test_math(): assert sys.platform'
    assert s.include_windows('tests/test_math.py', native, data, root=tmp_path,
                             changed={'tests/test_math.py'}, node='test_math')[0]


def test_unrelated_transitive_helper_is_not_a_windows_selection_capability(tmp_path):
    source = 'from tests.helper import calculate\ndef test_math(): assert calculate() == 2'
    path = tmp_path / 'tests/helper.py'
    path.parent.mkdir()
    path.write_text('import sqlite3\ndef calculate(): return 2', encoding='utf-8')
    data = policy_file(tmp_path, source)
    data['mixed_nodes'] = {}
    assert not s.include_windows('tests/test_math.py', source, data, root=tmp_path, node='test_math')[0]


def test_native_failures_and_each_required_boundary_remain_selected():
    data = s.policy()
    paths = ('tests/test_web_shutdown_process.py', 'tests/test_windows_broker.py',
             'tests/test_windows_job_ci.py', 'tests/test_proc_tree.py', 'tests/test_no_tree_kill.py',
             'tests/test_runner_path_boundary.py', 'tests/test_private_paths.py',
             'tests/test_shell_write_quotes.py', 'tests/test_state_isolation.py',
             'tests/test_durable_writer.py', 'tests/test_doctor.py', 'tests/test_wsl_readiness.py',
             'tests/test_cli_launch.py', 'tests/test_mcp_servers.py', 'tests/test_reproduction_receipt.py',
             'tests/test_read_only_profile.py', 'tests/test_claude_write_paths.py')
    for path in paths:
        assert s.include_windows(path, (s.ROOT / path).read_text(encoding='utf-8'), data)[0], path
    # Cross-platform parsing/schema suites still run on full Linux/nightly Windows.
    path = 'tests/test_research_bulk_pack.py'
    assert not s.include_windows(path, (s.ROOT/path).read_text(encoding='utf-8'), data,
                                node='test_bulk_model_choices_are_one_closed_decision_table')[0]


def test_new_policy_profiles_cannot_exchange_full_or_historical_windows_receipts():
    assert cs.VERSION == 8 and cs.COVERAGE['pytest-windows'] == s.WINDOWS_BOUNDARY
    assert s.WINDOWS_BOUNDARY == 'windows-boundary-v6'
    assert cs.COVERAGE['pytest-windows'] != 'windows-full-v1'
    assert s.policy()['version'] == 6


def test_selection_failure_cli_saves_diagnostic_then_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path))
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(tmp_path / 'missing-event.json'))
    monkeypatch.setattr(s.sys, 'argv', ['ci_selection'])
    with pytest.raises(SystemExit) as result:
        s.main()
    assert result.value.code != 0
    data = json.loads((tmp_path/'selection-error.json').read_text(encoding='utf-8'))
    assert data['reusable'] is False and data['status'] == 'invalid-selection'
    assert not (tmp_path/'changes.json').exists()


@pytest.mark.parametrize('profile', ['windows-full-v1', 'windows-boundary-v4', s.WINDOWS_PREVIEW])
def test_same_tree_different_windows_coverage_cannot_be_reused(evidence, profile):
    import hashlib
    api = API(evidence)
    api.manifest['coverage']['pytest-windows'] = profile
    api.zip = archive(api.manifest)
    api.artifact.update(size_in_bytes=len(api.zip), digest='sha256:' + hashlib.sha256(api.zip).hexdigest())
    assert not reuse(api)


@pytest.mark.parametrize('version', [6, 7])
def test_retired_epoch_is_rejected_even_with_new_artifact_name_and_digest(evidence, version):
    import hashlib
    api = API(evidence)
    api.manifest['version'] = version
    api.zip = archive(api.manifest)
    api.artifact.update(size_in_bytes=len(api.zip), digest='sha256:' + hashlib.sha256(api.zip).hexdigest())
    assert not reuse(api)


def test_doc_only_prose_still_skips_but_command_change_does_not():
    from markdown_it import MarkdownIt
    assert MarkdownIt().parse('Ordinary prose.')  # dependency present, not error fallback
    assert cs.document_change('README.md', 'The description is clear.', 'The description is concise.')
    assert not cs.document_change('README.md', 'Build with unfamiliar assemble.', 'Build with unfamiliar deploy.')


def test_report_api_error_keeps_local_counts_and_durations(monkeypatch, tmp_path):
    import sys
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path))
    monkeypatch.setenv('GITHUB_RUN_ID', '42')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setattr(sys, 'argv', ['ci_suite', 'report'])
    result = dict(profile=s.WINDOWS_BOUNDARY, setup_s=60, pytest_s=300,
                  full_node_count=10000, selected_node_count=3000, host_resource_wait_s={'a.py': 2})
    monkeypatch.setattr(suite, 'summarize', lambda directory: result)
    def unavailable(*args):
        raise RuntimeError('synthetic timing API failure')
    monkeypatch.setattr(cs, 'api', unavailable)
    with pytest.raises(RuntimeError):
        suite.main()
    saved = json.loads((tmp_path/'summary.json').read_text(encoding='utf-8'))
    assert saved['selected_node_count'] == 3000 and saved['host_resource_wait_s'] == {'a.py': 2}
    assert saved['over_ten_minutes'] is False


@pytest.mark.parametrize("worker", [None, "gw0", "gw1"])
def test_native_contract_preflight_and_missing_collected_case_fail(monkeypatch, tmp_path, worker):
    if worker is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    data = s.validate_profile()
    assert set(data['mandatory_nodes']) == {
        'tests/test_local_jobs_675.py::test_offline_request_cancel_replayed_to_restarted_runner_ends_only_its_job',
        'tests/test_windows_job_ci.py::test_wmi_escapes_owned_outer_nonbreakaway_inner_silent_job',
        'tests/test_windows_job_ci.py::test_real_wmi_bootstrap_never_enters_fake_role_without_verified_ack',
        'tests/test_web_shutdown_process.py::test_web_shutdown_ends_a_real_gateway_through_its_own_shutdown',
        'tests/test_web_shutdown_process.py::test_forced_web_shutdown_with_running_work_also_ends_gracefully_and_keeps_the_record',
        'tests/test_web_shutdown_process.py::test_the_watcher_stops_a_gateway_that_never_finishes_its_shutdown',
    }
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path))
    monkeypatch.setattr(suite, 'ROOT', tmp_path)
    item = SimpleNamespace(path=tmp_path/'tests/test_native.py', nodeid='tests/test_native.py::test_other')
    monkeypatch.setattr(suite, 'select', lambda *args: ([item], [], {}))
    monkeypatch.setattr(suite, 'policy', lambda: dict(boundary_cohorts={'tests/test_native.py':'native'},
        mandatory_nodes=['tests/test_native.py::test_must_exist']))
    config = SimpleNamespace(getoption=lambda key: s.WINDOWS_BOUNDARY if key == "ci_profile" else None)
    observation = suite.Observation(config)
    with pytest.raises(pytest.UsageError, match='mandatory native'):
        observation.pytest_collection_modifyitems(None, config, [item])
    expected_worker = worker or 'master'
    assert observation.worker == expected_worker
    error_path = tmp_path / f'selection-error-{expected_worker}.json'
    assert list(tmp_path.glob('selection-error-*.json')) == [error_path]
    error = json.loads(error_path.read_text(encoding='utf-8'))
    assert error['reusable'] is False
    assert error == dict(profile=s.WINDOWS_BOUNDARY, status='invalid-selection', reusable=False)


def node_wrapper(run, *, required_browser=False, clock=lambda: 1000.0, monotonic=lambda: 50.0):
    """Execute only the wrapper definition, with a module-local fake transport and clocks."""
    import subprocess
    tree = ast.parse((s.ROOT/'tests/test_web_3d.py').read_text(encoding='utf-8'), feature_version=(3, 10))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'test_office_in_node')
    function.decorator_list = []
    namespace = dict(Path=Path, __file__=str(s.ROOT/'tests/test_web_3d.py'), pytest=pytest,
                     re=__import__('re'), os=SimpleNamespace(environ={'LABHQ_REQUIRE_RESIZABLE_BROWSER': '1'} if required_browser else {}),
                     shutil=SimpleNamespace(which=lambda name: 'fake-node'), sys=SimpleNamespace(executable='fake-python'),
                     node_binary=lambda: 'fake-node',
                     time=SimpleNamespace(time=clock, monotonic=monotonic), json=json,
                     subprocess=SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<fake-node-wrapper>', 'exec'), namespace)
    return namespace['test_office_in_node']


# #833: the resizable script runs once per viewport; each invocation's third test is that viewport's browser test.
RESIZABLE_TITLES = {'desktop': 'desktop splitters drag, respond to arrows, persist and reset',
                    'phone': 'phone layout hides the splitters and keeps the office full width'}


def resizable_tap(viewport, *, passed=3, skipped=0, title=None):
    """TAP of one viewport invocation: the two static tests, then the selected browser test."""
    return (f'TAP version 13\nok 1 - static values\nok 2 - static shell\nok 3 - {title or RESIZABLE_TITLES[viewport]}\n'
            f'# pass {passed}\n# fail 0\n# skipped {skipped}\n')


@pytest.mark.parametrize('binary', [False, True])
def test_node_timeout_retains_partial_output_and_original_failure(binary):
    import subprocess
    calls = []
    def run(command, **options):
        assert command[0] == 'fake-node'
        calls.append(options['env'].get('LABHQ_RESIZABLE_VIEWPORT'))
        # The exact interpreter reaches the Windows Job wrapper of the demo driver (#842); no PATH python.
        # #833: startup/UI have separate budgets inside this one bounded, cleanup-inclusive invocation.
        assert options == dict(capture_output=True, text=True, encoding='utf-8', timeout=60,
                               env={'LABHQ_TEST_PYTHON': 'fake-python', 'LABHQ_RESIZABLE_VIEWPORT': 'desktop',
                                    'LABHQ_TEST_OUTER_DEADLINE_MS': '1060000'})
        stdout, stderr = 'TAP version 13\n', 'resizable browser: desktop load\n'
        if binary:
            stdout, stderr = stdout.encode(), stderr.encode()
        raise subprocess.TimeoutExpired(command, 60, output=stdout, stderr=stderr)
    with pytest.raises(AssertionError) as result:
        node_wrapper(run)('web_resizable_panels.cjs', 'fail 0')
    assert isinstance(result.value.__cause__, subprocess.TimeoutExpired)
    assert 'web_resizable_panels.cjs (desktop) exceeded 60s' in str(result.value)
    assert 'TAP version 13' in str(result.value)
    assert 'resizable browser: desktop load' in str(result.value)
    assert calls == ['desktop']  # a failed first viewport ends the node; the second never reports success alone


def test_each_resizable_viewport_runs_in_its_own_invocation_with_a_fresh_deadline():
    seen, times = [], iter([1000.0, 1012.5])
    def run(command, **options):
        seen.append((Path(command[1]).name, options))
        viewport = options['env'].get('LABHQ_RESIZABLE_VIEWPORT')
        return SimpleNamespace(returncode=0, stdout=resizable_tap(viewport) if viewport else 'fail 0', stderr='')
    node_wrapper(run, required_browser=True, clock=lambda: next(times))('web_resizable_panels.cjs', 'fail 0')
    assert [name for name, _ in seen] == ['web_resizable_panels.cjs'] * 2
    inherited = {'LABHQ_REQUIRE_RESIZABLE_BROWSER': '1', 'LABHQ_TEST_PYTHON': 'fake-python'}
    assert [options['env'] for _, options in seen] == [
        {**inherited, 'LABHQ_RESIZABLE_VIEWPORT': 'desktop', 'LABHQ_TEST_OUTER_DEADLINE_MS': '1060000'},
        {**inherited, 'LABHQ_RESIZABLE_VIEWPORT': 'phone', 'LABHQ_TEST_OUTER_DEADLINE_MS': '1072500'}]
    assert [options['timeout'] for _, options in seen] == [60, 60]
    # Other scripts: one invocation, no viewport or deadline variable, their own timeout.
    seen.clear()
    wrapper = node_wrapper(run)
    for name in ('web_state.cjs', 'web_office_browser.cjs'):
        wrapper(name, 'fail 0')
    assert [(name, options['env'], options['timeout']) for name, options in seen] == [
        ('web_state.cjs', {'LABHQ_TEST_PYTHON': 'fake-python'}, 30),
        ('web_office_browser.cjs', {'LABHQ_TEST_PYTHON': 'fake-python'}, 170)]


@pytest.mark.parametrize('failing', ['desktop', 'phone'])
@pytest.mark.parametrize('problem', ['returncode', 'skipped', 'pass2', 'other-viewport', 'no-marker', 'timeout'])
def test_a_failed_or_skipped_viewport_fails_the_whole_resizable_node(failing, problem):
    import subprocess
    calls = []
    def run(command, **options):
        viewport = options['env']['LABHQ_RESIZABLE_VIEWPORT']
        calls.append(viewport)
        if viewport != failing:
            return SimpleNamespace(returncode=0, stdout=resizable_tap(viewport), stderr='')
        if problem == 'timeout':
            raise subprocess.TimeoutExpired(command, 60, output='TAP version 13\n', stderr='')
        other = 'phone' if viewport == 'desktop' else 'desktop'
        stdout = {'returncode': resizable_tap(viewport), 'skipped': resizable_tap(viewport, passed=2, skipped=1),
                  'pass2': resizable_tap(viewport, passed=2),
                  'other-viewport': resizable_tap(viewport, title=RESIZABLE_TITLES[other]),
                  'no-marker': resizable_tap(viewport).replace('# fail 0', '# fail 1')}[problem]
        return SimpleNamespace(returncode=1 if problem == 'returncode' else 0, stdout=stdout, stderr='')
    with pytest.raises(AssertionError, match=rf'web_resizable_panels\.cjs \({failing}\)'):
        node_wrapper(run, required_browser=True)('web_resizable_panels.cjs', 'fail 0')
    assert calls == (['desktop'] if failing == 'desktop' else ['desktop', 'phone'])


@pytest.mark.parametrize('ending', ['returncode', 'timeout'])
def test_a_failed_viewport_reports_its_launch_epoch_deadline_and_monotonic_duration(ending):
    """#833: one wall-clock reading sets the deadline and is reported; the duration comes from the monotonic clock."""
    import subprocess
    epochs, monotonics = iter([1000.0]), iter([50.0, 72.21])
    seen = []
    def run(command, **options):
        seen.append(options['env']['LABHQ_TEST_OUTER_DEADLINE_MS'])
        if ending == 'timeout':
            raise subprocess.TimeoutExpired(command, 60, output='TAP version 13\n', stderr='')
        return SimpleNamespace(returncode=1, stdout=resizable_tap('desktop'), stderr='')
    with pytest.raises(AssertionError) as failure:
        node_wrapper(run, required_browser=True, clock=lambda: next(epochs),
                     monotonic=lambda: next(monotonics))('web_resizable_panels.cjs', 'fail 0')
    assert seen == ['1060000']  # the deadline is the same single reading, not a second one
    line = next(line for line in str(failure.value).splitlines() if line.startswith('resizable timing (python): '))
    assert json.loads(line.split(': ', 1)[1]) == {'viewport': 'desktop', 'launch_epoch_ms': 1000000,
                                                  'deadline_epoch_ms': 1060000, 'returned_after_mono_ms': 22210}
    # Other scripts report no timing line.
    def other(command, **options):
        return SimpleNamespace(returncode=1, stdout='fail 0', stderr='')
    with pytest.raises(AssertionError) as plain:
        node_wrapper(other)('web_state.cjs', 'fail 0')
    assert 'resizable timing' not in str(plain.value)


def test_resizable_script_reports_its_own_clocks_before_any_require_and_at_each_open():
    source = (s.ROOT/'tests/web_resizable_panels.cjs').read_text(encoding='utf-8')
    code = [line for line in source.splitlines() if line.strip() and not line.lstrip().startswith('//')]
    assert code[0] == 'const MODULE_ENTRY_MONO_MS = performance.now();'  # the first statement, before any require
    assert code[1].startswith("const {test} = require(")
    assert source.count("console.error('resizable timing: ' + JSON.stringify({stage, ...extra,") == 1
    for key in ('time_origin_epoch_ms: Math.round(performance.timeOrigin)', 'mono_ms: Math.round(performance.now())',
                'epoch_ms: epoch', 'outer_remaining_ms: workDeadline === undefined ? null : outerDeadline - epoch',
                'work_remaining_ms: workDeadline === undefined ? null : workDeadline - epoch'):
        assert key in source
    assert "timing('module-ready', {module_entry_mono_ms: Math.round(MODULE_ENTRY_MONO_MS)});" in source
    # Top-level code: it runs while the module loads, before node:test starts the (already registered) static tests.
    assert "\ntiming('module-ready'" in source
    for viewport in ('desktop', 'phone'):
        opened = source.index(f"console.error('resizable browser: {viewport} open');")
        row = source.index(f"timing('open', {{viewport: '{viewport}'}});")
        assert opened < row < source.index('await openDemo(', row)  # right before this viewport's driver trace starts
    # Observation only: the deadline variables are still assigned exactly once.
    assert source.count('const workDeadline =') == 1 and source.count('const outerDeadline =') == 1


def test_optional_browser_still_runs_both_viewports_and_requires_each_to_succeed():
    calls = []
    def run(command, **options):
        calls.append(options['env']['LABHQ_RESIZABLE_VIEWPORT'])
        return SimpleNamespace(returncode=0, stdout=resizable_tap(calls[-1], passed=2, skipped=1), stderr='')
    node_wrapper(run)('web_resizable_panels.cjs', 'fail 0')  # no Chrome outside the required phase: a skip is allowed
    assert calls == ['desktop', 'phone']


@pytest.mark.parametrize('returncode,stdout', [(0, 'fail 0'), (1, 'fail 0'), (0, 'fail 1')])
def test_node_wrapper_still_requires_success_and_marker(returncode, stdout):
    wrapper = node_wrapper(lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=''))
    if returncode == 0 and stdout == 'fail 0':
        wrapper('web_resizable_panels.cjs', 'fail 0')
    else:
        with pytest.raises(AssertionError):
            wrapper('web_resizable_panels.cjs', 'fail 0')


@pytest.mark.parametrize('passed,skipped,accepted', [(3, 0, True), (2, 1, False), (3, 1, False), (2, 0, False)])
def test_required_browser_needs_actual_inner_node_passes_and_zero_skips(passed, skipped, accepted):
    wrapper = node_wrapper(lambda command, **options: SimpleNamespace(returncode=0, stderr='', stdout=resizable_tap(
        options['env']['LABHQ_RESIZABLE_VIEWPORT'], passed=passed, skipped=skipped)), required_browser=True)
    if accepted:
        wrapper('web_resizable_panels.cjs', 'fail 0')
    else:
        with pytest.raises(AssertionError):
            wrapper('web_resizable_panels.cjs', 'fail 0')


def test_resizable_browser_diagnostics_keep_deadline_cleanup_and_selection():
    source = (s.ROOT/'tests/web_resizable_panels.cjs').read_text(encoding='utf-8')
    wrapper_source = (s.ROOT/'tests/test_web_3d.py').read_text(encoding='utf-8')
    assert 'skip: skipReason() || false, timeout: 60000' in source
    for viewport in ('desktop', 'phone'):
        for phase in ('open', 'load', 'assertions', 'close', 'closed'):
            assert f"console.error('resizable browser: {viewport} {phase}')" in source
        assert f'await {viewport}.close();' in source
        # #833: one browser test per viewport, registered only when selected; the wrapper checks its exact title.
        assert f"if (runs('{viewport}')) test('{RESIZABLE_TITLES[viewport]}'," in source
        assert f"'{viewport}': '{RESIZABLE_TITLES[viewport]}'" in wrapper_source
    assert source.count('finally {') == 2
    assert source.count("\ntest('") == 2  # the two static tests always run
    assert "const VIEWPORTS = ['desktop', 'phone'];" in source
    assert 'if (selected !== undefined && !VIEWPORTS.includes(selected)) {' in source
    # One work deadline per invocation, from that watchdog's end minus the driver's close reserve; refused without a
    # selected viewport, so two viewports never share one budget again (job 120174823730).
    assert source.count('process.env.LABHQ_TEST_OUTER_DEADLINE_MS') == 2
    assert 'if (process.env.LABHQ_TEST_OUTER_DEADLINE_MS !== undefined && selected === undefined) {' in source
    assert 'outerDeadline - CLOSE_RESERVE_MS' in source
    assert source.count('const workDeadline =') == 1
    assert source.count('reducedMotion: true, workDeadline,') == 2
    assert 'const STARTUP_BUDGET_MS = 30000, UI_BUDGET_MS = 20000;' in source
    assert source.count('...phaseBudgets') == 2
    assert "if (process.env.LABHQ_REQUIRE_RESIZABLE_BROWSER === '1' && workDeadline === undefined) {" in source
    assert "'web_resizable_panels.cjs': 60" in wrapper_source
    data = s.validate_profile()
    path = 'tests/test_web_3d.py'
    assert s.include_windows(path, (s.ROOT/path).read_text(encoding='utf-8'), data,
                             node='test_office_in_node[web_resizable_panels.cjs-fail 0]')[0]
    item = SimpleNamespace(nodeid='resizable-browser')
    for profile in ('linux-full-v1', 'windows-full-v1'):
        assert suite.select([item], profile) == ([item], [], {})


def test_demo_driver_cleanup_waits_for_actual_close_and_preserves_startup_errors():
    source = (s.ROOT/'tests/support/demo_driver.cjs').read_text(encoding='utf-8')
    assert "chromeClosed = new Promise(resolve => chrome.once('close', resolve));" in source
    assert source.index("chrome.once('close', resolve)") < source.index("chrome.stderr.on('data'")
    assert source.index('ws.onclose = resolve') < source.index('ws.onopen = resolve')
    assert 'const close = () => closing ||= closeDemoResources(' in source
    assert "if (chrome.exitCode === null && chrome.signalCode === null) chrome.kill();" in source
    assert 'server.close(error => error ? reject(error) : resolve());' in source
    assert source.index('await Promise.race(') < source.index('await removeProfile(profile);')
    assert '} finally { if (phase !== undefined) clearTimer(phase); clearTimer(timer); }' in source
    assert "new AggregateError([e, cleanup], 'demo startup and cleanup failed')" in source
    # #842: one 5 s deadline; a stalled graceful close is forced at 3 s inside it, once, and the deadline still rejects.
    assert 'const CLEANUP_MS = 5000, GRACEFUL_CLEANUP_MS = 3000;' in source
    assert 'if (chrome) phase = setTimer(force, GRACEFUL_CLEANUP_MS);' in source and '}, CLEANUP_MS);' in source
    assert 'if (!graceful || forced) return;' in source
    deadline = source[source.index("report('timeout');"):source.index("'demo cleanup incomplete'));")]
    assert deadline.index('observeLate();') < deadline.index('force();') < deadline.index('reject(')
    assert "{after_deadline_ms: Math.round(clock() - deadline)}" in source
    assert "if (opts.testWithholdBrowserClose === true) return true;" in source  # test-only forced path
    # #842/#843: on Windows Chrome runs under the product's KILL_ON_CLOSE Job wrapper; ending the owned wrapper
    # closes the Job. Fail closed before any resource exists; Linux spawns Chrome directly as before.
    assert "const WINDOWS_JOB_WRAPPER = path.resolve(__dirname, 'windows_job_browser.py');" in source
    assert "return [python, ['-I', WINDOWS_JOB_WRAPPER, '--windows-job', String(WINDOWS_JOB_LIMIT_BYTES), '--', chromePath, ...args]];" in source
    assert "if (platform !== 'win32') return null;" in source and "if (!python) return [chromePath, args];" in source
    assert source.index('const jobPython = windowsJobPython();') < source.index('const profile = fs.mkdtempSync(')
    assert 'chrome = spawn(browser, browserArgs,' in source


def test_incomplete_timed_out_report_is_diagnostic_not_success(monkeypatch, tmp_path):
    import sys
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['ci_suite', 'report'])
    with pytest.raises(OSError):
        suite.main()
    data = json.loads((tmp_path/'report-error.json').read_text(encoding='utf-8'))
    assert data['status'] == 'incomplete' and data['reusable'] is False


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'timed_out'])
def test_failed_or_timed_out_windows_job_cannot_authorize_reuse(evidence, conclusion):
    api = API(evidence)
    next(job for job in api.jobs if job['name'] == 'pytest-windows')['conclusion'] = conclusion
    assert not reuse(api)


def test_changed_native_test_helper_adds_its_direct_consumer_only(tmp_path):
    source = 'from tests.helper import read\ndef test_math(): assert read() == 2'
    data = policy_file(tmp_path, source)
    data['mixed_nodes'] = {}
    helper = tmp_path/'tests/helper.py'
    helper.parent.mkdir(exist_ok=True)
    helper.write_text('import sqlite3\ndef read(): return sqlite3.connect("owned")', encoding='utf-8')
    assert s.include_windows('tests/test_math.py', source, data, root=tmp_path,
                             changed={'tests/helper.py'}, node='test_math')[0]
    helper.write_text('def read(): return 2', encoding='utf-8')
    assert not s.include_windows('tests/test_math.py', source, data, root=tmp_path,
                                 changed={'tests/helper.py'}, node='test_math')[0]


def test_windows_pool_step_records_worker_stacks_before_its_time_limit():
    # #1076: a pool step cut at its 9 minute cap left no trace of the test it was in.
    jobs = yaml.safe_load((s.ROOT / cs.WORKFLOW).read_text(encoding='utf-8'))['jobs']
    job = jobs['pytest-windows']
    pool = next(x for x in job['steps'] if '--ci-phase parallel' in x.get('run', ''))
    assert '--ci-stall' in pool['run']
    assert '--ci-stall' not in next(x for x in job['steps'] if '--ci-phase critical' in x.get('run', ''))['run']
    interval = float(job['env']['CI_STALL_INTERVAL_S'])
    assert 1 <= interval and interval * 3 < 9 * 60  # three snapshots fit before the step is cut
    assert any(x.get('if') == 'always()' and x.get('uses', '').startswith('actions/upload-artifact')
               for x in job['steps'])  # the stack logs survive a cut step


@pytest.mark.parametrize('value, expected', [(None, 300), ('150', 150.0), ('0.5', 300), ('x', 300), ('-5', 300)])
def test_stall_interval_reads_the_environment_and_falls_back_when_unusable(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('CI_STALL_INTERVAL_S', raising=False)
    else:
        monkeypatch.setenv('CI_STALL_INTERVAL_S', value)
    assert suite.stall_interval() == expected


def test_a_stall_snapshot_names_the_running_test_and_lands_in_a_log_the_phase_checks_ignore(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv('CI_STALL_INTERVAL_S', '1')
    log = tmp_path / 'stacks-gw0.log'
    stall = suite.WorkerStall(log, lambda: 'tests/test_x.py::test_y [call]')
    stall.start()
    for _ in range(40):
        if log.exists() and 'CI snapshot: tests/test_x.py::test_y [call]' in log.read_text(encoding='utf-8'):
            break
        time.sleep(0.1)
    stall.close()
    assert 'CI snapshot: tests/test_x.py::test_y [call]' in log.read_text(encoding='utf-8')
    # windows.combine globs collection-*.json, metrics-*.json and *error*.json only; a .log is not one of them.
    assert not any(log.match(pattern) for pattern in ('collection-*.json', 'metrics-*.json', '*error*.json'))
