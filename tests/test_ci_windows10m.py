"""Essential Windows policy contracts: pure data/fake Git, no native launch."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_selection as s, ci_suite as suite, ci_skip as cs
from tests.test_ci_cohort823 import A, H, event, fake_git, policy_file
from tests.test_ci_skip_policy import evidence, API, archive, reuse


def test_routine_job_is_ten_minutes_and_has_one_bounded_suite_with_diagnostic_tail():
    jobs = yaml.safe_load((s.ROOT / cs.WORKFLOW).read_text(encoding='utf-8'))['jobs']
    job = jobs['pytest-windows']
    bound = job['timeout-minutes']
    assert 'schedule' in bound and 'workflow_dispatch' in bound and '|| 10' in bound
    assert '360' in bound  # Preserve GitHub's previous default for full nightly/manual.
    tests = [x for x in job['steps'] if x.get('run', '').startswith('pytest ')]
    assert len(tests) == 1 and '|| 7' in tests[0]['timeout-minutes']
    assert 'continue-on-error' not in tests[0]
    assert not any('preview' in str(x).lower() for x in job['steps'])
    assert 'boundary_preview' not in jobs['changes']['outputs']
    tail = job['steps'][job['steps'].index(tests[0]) + 1:]
    assert any(x.get('if') == 'always()' and 'ci_suite report' in x.get('run', '') for x in tail)
    assert any(x.get('if') == 'always()' and x.get('uses', '').startswith('actions/upload-artifact') for x in tail)
    assert jobs['pytest']['strategy']['matrix']['python-version'] == ['3.10', '3.12']
    assert 'windows-full-v1' in job['env']['CI_COVERAGE_PROFILE']


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
    assert cs.VERSION == 7 and cs.COVERAGE['pytest-windows'] == s.WINDOWS_BOUNDARY
    assert s.WINDOWS_BOUNDARY == 'windows-boundary-v5'
    assert cs.COVERAGE['pytest-windows'] != 'windows-full-v1'
    assert s.policy()['version'] == 5


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


def test_retired_epoch_is_rejected_even_with_new_artifact_name_and_digest(evidence):
    import hashlib
    api = API(evidence)
    api.manifest['version'] = 6
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


def test_native_contract_preflight_and_missing_collected_case_fail(monkeypatch, tmp_path):
    data = s.validate_profile()
    assert set(data['mandatory_nodes']) == {
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
    config = SimpleNamespace(getoption=lambda key: s.WINDOWS_BOUNDARY)
    observation = suite.Observation(config)
    with pytest.raises(pytest.UsageError, match='mandatory native'):
        observation.pytest_collection_modifyitems(None, config, [item])
    error = json.loads((tmp_path/'selection-error-master.json').read_text(encoding='utf-8'))
    assert error['reusable'] is False


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
