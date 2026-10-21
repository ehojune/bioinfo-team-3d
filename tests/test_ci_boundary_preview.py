"""#823 policy preview contracts: AST/data/fake Git only."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_selection as s, ci_suite as suite


@pytest.mark.parametrize('node', [
    *(f'test_verified_paper_read_composes_with_dag_protection_and_native_write_denial[{i}]' for i in range(4)),
    'test_paper_namespace_never_overwrites_or_regrants_the_ancestor_namespace',
    *(f'test_unproven_document_is_not_regranted_at_policy_composition[{case}]'
      for case in ('changed', 'missing', 'link_swap')),
])
def test_paper_skill_private_sandbox_link_and_hash_nodes_are_windows_boundaries(node):
    path = 'tests/test_paper_skill_read_scope.py'
    assert s.include_windows(path, (s.ROOT / path).read_text(encoding='utf-8'), s.policy(), node=node)[0]


from scripts import ci_skip as cs
from tests.test_ci_cohort823 import fake_git, policy_file
from tests.test_ci_manifest_identity import producer
from tests.test_ci_skip_policy import evidence, API, archive, reuse
import copy
import hashlib
import yaml

A, B, H = 'a' * 40, 'b' * 40, 'c' * 40


def trusted():
    repo = {'id': 123, 'full_name': cs.REPOSITORY}
    return {'pull_request': {'number': 823, 'base': {'sha': A, 'ref': 'main', 'repo': repo},
                            'head': {'sha': B, 'repo': dict(repo)}}}


def workflow_env():
    return {'GITHUB_REPOSITORY': cs.REPOSITORY, 'GITHUB_WORKFLOW_SHA': H,
            'GITHUB_WORKFLOW_REF': f'{cs.REPOSITORY}/{cs.WORKFLOW}@refs/pull/823/merge'}


def test_preview_retains_real_policy_and_changed_test_paths_but_normal_range_stays_full():
    paths = (s.PROFILE, 'tests/test_math.py', 'labhq/neutral.py')
    git, _ = fake_git(paths)
    assert s.change_range('pull_request', trusted(), H, git)['paths'] == sorted(paths)
    result = s.preview_range('pull_request', trusted(), H, git, workflow_env())
    assert result == {'version': 1, 'checkout': H, 'base': A, 'paths': sorted(paths),
                      'mode': 'preview', 'reusable': False}


@pytest.mark.parametrize('mutation', [
    lambda e: e['pull_request']['head']['repo'].update(id=999),
    lambda e: e['pull_request']['head']['repo'].update(full_name='fork/elsewhere'),
    lambda e: e['pull_request']['base'].update(ref='feature'),
    lambda e: e['pull_request']['base'].update(sha='e'*40),
    lambda e: e['pull_request']['head'].update(sha='e'*40),
])
def test_preview_rejects_forks_untrusted_base_and_snapshot_drift(mutation):
    event = trusted(); mutation(event); git, _ = fake_git((s.PROFILE,))
    with pytest.raises(ValueError):
        s.preview_range('pull_request', event, H, git, workflow_env())


@pytest.mark.parametrize('key', ['GITHUB_REPOSITORY', 'GITHUB_WORKFLOW_SHA', 'GITHUB_WORKFLOW_REF'])
def test_preview_never_uses_an_unbound_workflow(key):
    env = workflow_env(); env[key] = 'wrong'; git, _ = fake_git((s.PROFILE,))
    with pytest.raises(ValueError, match='workflow'):
        s.preview_range('pull_request', trusted(), H, git, env)


@pytest.mark.parametrize('paths', [
    ('labhq/neutral.py',), ('tests/test_new.py',), (s.PROFILE, 'tests/conftest.py'),
    (s.PROFILE, 'tests/helpers.py'), (s.PROFILE, 'tests/fixtures/a.json'),
    (s.PROFILE, 'pyproject.toml'), (s.PROFILE, 'labhq/test_guard.py'),
    (s.PROFILE, 'scripts/ci_isolation.py'), (s.PROFILE, '../escape'), (s.PROFILE, s.PROFILE),
])
def test_preview_is_narrow_policy_only_not_a_fixture_dependency_or_unknown_range_bypass(paths):
    git, _ = fake_git(paths)
    with pytest.raises(ValueError):
        s.preview_range('pull_request', trusted(), H, git, workflow_env())


@pytest.mark.parametrize('event_name', ['push', 'schedule', 'workflow_dispatch', 'unknown'])
def test_preview_does_not_duplicate_ordinary_main_nightly_or_manual_runs(event_name):
    git, _ = fake_git((s.PROFILE,))
    with pytest.raises(ValueError):
        s.preview_range(event_name, trusted(), H, git, workflow_env())


@pytest.mark.parametrize('parents', [[H, B, A], [H, A], [H, 'e'*40, B]])
def test_preview_keeps_exact_ordered_merge_parent_proof(parents):
    git, _ = fake_git((s.PROFILE,), parents=parents)
    with pytest.raises(ValueError, match='parents'):
        s.preview_range('pull_request', trusted(), H, git, workflow_env())


def test_preview_and_authoritative_selection_receipts_are_not_interchangeable(tmp_path, monkeypatch):
    source = 'def test_math(): assert 1+1==2'
    policy_file(tmp_path, source)
    path = tmp_path / 'tests/test_math.py'; path.parent.mkdir(exist_ok=True); path.write_text(source, encoding='utf-8')
    items = [SimpleNamespace(path=path, nodeid='tests/test_math.py::test_math')]
    git, _ = fake_git((s.PROFILE, 'tests/test_changed.py'))
    receipt = s.preview_range('pull_request', trusted(), H, git, workflow_env())
    directory = tmp_path / 'preview'; directory.mkdir()
    (directory / 'preview-changes.json').write_text(json.dumps(receipt), encoding='utf-8')
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(directory)); monkeypatch.setenv('GITHUB_SHA', H)
    assert suite.select(items, s.WINDOWS_PREVIEW, tmp_path)[1] == items
    with pytest.raises(pytest.UsageError):
        suite.select(items, s.WINDOWS_BOUNDARY, tmp_path)
    # Renaming cannot disguise a preview receipt as authoritative selection.
    (directory / 'changes.json').write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(pytest.UsageError):
        suite.select(items, s.WINDOWS_BOUNDARY, tmp_path)
    receipt['reusable'] = True
    (directory / 'preview-changes.json').write_text(json.dumps(receipt), encoding='utf-8')
    with pytest.raises(pytest.UsageError):
        suite.select(items, s.WINDOWS_PREVIEW, tmp_path)


def test_preview_keeps_changed_pure_test_and_unknown_nodes(tmp_path):
    source = 'def test_math(): assert 1'
    policy_file(tmp_path, source)
    path = tmp_path / 'tests/test_math.py'; path.parent.mkdir(exist_ok=True); path.write_text(source, encoding='utf-8')
    item = SimpleNamespace(path=path, nodeid='tests/test_math.py::test_math')
    assert suite.select([item], s.WINDOWS_PREVIEW, tmp_path, changed={'tests/test_math.py'})[0] == [item]


def test_preview_profile_cannot_supply_full_gate_receipts(producer):
    current, needs, event, _, _ = producer
    needs['pytest-windows']['outputs']['profile'] = s.WINDOWS_PREVIEW
    with pytest.raises(ValueError, match='coverage profile receipts'):
        cs.produce_manifest(current, needs, event)


@pytest.mark.parametrize('case', ['artifact-name', 'coverage-profile', 'extra-marker', 'old-epoch'])
def test_reuse_refuses_preview_artifacts_even_with_same_tree_and_valid_digest(evidence, case):
    api = API(evidence)
    if case == 'artifact-name':
        api.artifact['name'] = 'boundary-preview-42-1'
    else:
        if case == 'coverage-profile': api.manifest['coverage']['pytest-windows'] = s.WINDOWS_PREVIEW
        if case == 'extra-marker': api.manifest.update(reusable=False, mode='preview')
        if case == 'old-epoch': api.manifest['version'] = cs.VERSION - 1
        api.zip = archive(api.manifest)
        api.artifact.update(size_in_bytes=len(api.zip), digest='sha256:'+hashlib.sha256(api.zip).hexdigest())
    assert not reuse(api)


def test_workflow_preview_is_conditional_step_with_separate_namespace_and_unchanged_full_receipt():
    workflow = yaml.safe_load((s.ROOT/cs.WORKFLOW).read_text(encoding='utf-8'))
    jobs = workflow['jobs']; steps = jobs['pytest-windows']['steps']
    assert set(jobs) == {'changes', 'pytest', 'pytest-windows', 'clean-install', 'ro-crate-validator',
                         'pytest-slow', 'readme', 'patch-notes', 'validation-manifest'}
    assert 'boundary_preview' not in jobs['changes']['outputs']
    assert not any('preview' in str(step).lower() for step in steps)
    normal = [x for x in steps if x.get('run','').startswith('pytest ') and '--ci-profile $' in x['run']]
    assert len(normal) == 1 and 'continue-on-error' not in normal[0]
    assert jobs['pytest-windows']['outputs']['profile'] == '${{ steps.identity.outputs.profile }}'
    uploads = [x['with'] for x in steps if x.get('uses','').startswith('actions/upload-artifact')]
    assert len(uploads) == 1 and uploads[0]['name'].startswith('coverage-windows-')
    manifest = jobs['validation-manifest']['steps'][-1]['with']
    assert manifest['name'].startswith(f'ci-full-v{cs.VERSION}-') and manifest['path'] == 'ci-result.json'


@pytest.mark.parametrize('path,node', [
 ('tests/test_environment_install.py', 'test_literal_installation_instructions_are_data'),
 ('tests/test_environment_install.py', 'test_documented_bash_pythonpath_prefix_remains_allowed'),
 ('tests/test_environment_install.py', 'test_noninstall_nested_shell_body_remains_allowed'),
 ('tests/test_environment_install.py', 'test_a_launched_analysis_script_keeps_its_computed_arguments'),
 ('tests/test_environment_install.py', 'test_computed_words_after_the_script_or_wrapped_script_stay_allowed'),
 ('tests/test_policy.py', 'test_linear_scan_budget_uses_cpu_and_preserves_result_and_strict_limit'),
 ('tests/test_policy.py', 'test_hpc_thresholds'),
 ('tests/test_web_3d.py', 'test_3d_uses_shared_visual_state_for_pose_and_marker'),
])
def test_audited_mixed_pure_nodes_only_omit_with_exact_source_and_unchanged_test(path,node):
    data = s.policy(); source = (s.ROOT/path).read_text(encoding='utf-8')
    assert not s.include_windows(path, source, data, root=s.ROOT, node=node)[0]
    with pytest.raises(ValueError):
        s.include_windows(path, source+'\n# drift', data, root=s.ROOT, node=node)
    assert s.include_windows(path, source, data, root=s.ROOT, changed={path}, node=node)[0]


@pytest.mark.parametrize('path,node', [
 ('tests/test_environment_install.py', 'test_foreign_windows_executable_spellings_are_never_task_relative'),
 ('tests/test_environment_install.py', 'test_foreign_windows_targets_are_rejected_for_all_supported_managers'),
 ('tests/test_environment_install.py', 'test_posix_venv_python_binary_link_does_not_escape_the_local_environment'),
 ('tests/test_policy.py', 'test_touches_stays_linear_on_long_separator_runs'),
 ('tests/test_init_defaults.py', 'test_reference_list_is_small_explicit_and_has_no_personal_value'),
 ('tests/test_web_3d.py', 'test_office_in_node'),
 ('tests/test_web_3d.py', 'test_package_data_includes_office_assets'),
])
def test_mixed_refinement_does_not_drop_native_security_or_autouse_fixture_boundaries(path,node):
    assert s.include_windows(path,(s.ROOT/path).read_text(encoding='utf-8'),s.policy(),root=s.ROOT,node=node)[0]


@pytest.mark.parametrize('allowed', [True, False])
def test_plan_cli_emits_only_conditional_output_with_real_range_no_dummy_receipt(tmp_path, monkeypatch, capsys, allowed):
    import sys
    eventfile=tmp_path/'event.json'; eventfile.write_text(json.dumps(trusted()),encoding='utf-8')
    for key,value in {**workflow_env(), 'GITHUB_EVENT_PATH':str(eventfile),
                       'GITHUB_EVENT_NAME':'pull_request', 'GITHUB_SHA':H}.items():
        monkeypatch.setenv(key,value)
    git,_=fake_git((s.PROFILE,) if allowed else ('labhq/neutral.py',))
    monkeypatch.setattr(cs,'git',git); monkeypatch.setattr(sys,'argv',['ci_selection','preview-plan'])
    if allowed:
        s.main()
        assert capsys.readouterr().out == 'enabled=true\n'
    else:
        with pytest.raises(SystemExit):
            s.main()
        assert capsys.readouterr().out == ''
    assert not list(tmp_path.glob('*changes.json'))


def test_preview_report_is_explicitly_non_reusable_and_uses_distinct_summary(tmp_path,monkeypatch):
    import sys
    profile=s.WINDOWS_PREVIEW
    (tmp_path/'metrics-master.json').write_text(json.dumps({'profile':profile,'setup_s':1,'pytest_s':2}),encoding='utf-8')
    (tmp_path/'collection-master.json').write_text(json.dumps({'profile':profile,'reusable':False,
        'full_nodes':['a','b'],'selected_nodes':['a'],'excluded_files':{'b':'pure'}}),encoding='utf-8')
    summary=tmp_path/'step-summary.md'
    for key,value in {'CI_SUITE_ARTIFACT_DIR':str(tmp_path),'GITHUB_STEP_SUMMARY':str(summary),
                       'GITHUB_RUN_ID':'42','GITHUB_RUN_ATTEMPT':'1','CI_JOB_LABEL':'pytest-windows'}.items():
        monkeypatch.setenv(key,value)
    def api(path):
        assert path=='actions/runs/42/attempts/1/jobs?per_page=100'
        return {'total_count':1,'jobs':[{'name':'pytest-windows','created_at':'2026-10-21T00:00:00Z',
                                       'started_at':'2026-10-21T00:00:03Z'}]}
    monkeypatch.setattr(cs,'api',api); monkeypatch.setattr(sys,'argv',['ci_suite','report'])
    suite.main()
    result=json.loads((tmp_path/'preview-summary.json').read_text(encoding='utf-8'))
    assert result['reusable'] is False and result['profile']==profile
    assert (result['full_node_count'], result['selected_node_count'], result['job_queue_s'])==(2,1,3)
    assert 'warm preview' in result['timing_scope'] and 'host_resource_wait_s' in result
    assert 'reusable=false' in summary.read_text(encoding='utf-8')
    assert not (tmp_path/'summary.json').exists() and not (tmp_path/'ci-result.json').exists()
