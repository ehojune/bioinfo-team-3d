"""#823 contracts: AST/data/fake Git only; no subprocess, socket or worker launch."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_selection as s, ci_suite as suite, ci_skip as cs

A, B, H = 'a' * 40, 'b' * 40, 'c' * 40


def fake_git(paths=('tests/test_math.py',), parents=None):
    calls = []
    def git(*args):
        calls.append(args)
        if args == ('rev-parse', 'HEAD'):
            return H
        if args[:1] == ('rev-list',):
            return ' '.join(parents or [H, A, B])
        if args[:1] == ('merge-base',):
            return A
        if args[:1] == ('diff',):
            assert args == ('diff', '--name-only', '-z', '--no-renames', A, H)
            return ''.join(p + '\0' for p in paths)
        raise AssertionError(args)
    return git, calls


def event():
    return {'pull_request': {'base': {'sha': A}, 'head': {'sha': B}}}


def receipt(tmp_path, data):
    (tmp_path / 'changes.json').write_text(json.dumps(data), encoding='utf-8')


def review(text):
    return {'sha256': s.source_digest(text), 'reason': 'synthetic arithmetic review', 'helpers': {}}


def policy_file(root, source):
    path = root / s.PROFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'version': 2, 'profile': s.WINDOWS_BOUNDARY, 'default': 'include',
            'boundary_cohorts': {'tests/test_windows*.py': 'native OS boundary'},
            'reviewed_pure': {'tests/test_math.py': review(source)}}
    path.write_text(json.dumps(data), encoding='utf-8')
    return data


def test_pr_range_uses_exact_ordered_checkout_base_head_and_all_changed_paths():
    git, calls = fake_git(('labhq/neutral.py', 'tests/test_math.py', 'tests/test_new.py'))
    data = s.change_range('pull_request', event(), H, git)
    assert data == {'version': 1, 'checkout': H, 'base': A,
                    'paths': ['labhq/neutral.py', 'tests/test_math.py', 'tests/test_new.py']}
    assert len(calls) == 3


def test_push_range_requires_after_checkout_and_before_ancestor():
    git, calls = fake_git()
    assert s.change_range('push', {'before': A, 'after': H}, H, git)['base'] == A
    assert ('merge-base', A, H) in calls
    with pytest.raises(ValueError):
        s.change_range('push', {'before': '0' * 40, 'after': H}, H, git)
    with pytest.raises(ValueError):
        s.change_range('push', {'before': A, 'after': B}, H, git)


@pytest.mark.parametrize('parents', [[H, B, A], [H, A], [H, A, B, A], [H, 'd' * 40, B]])
def test_unknown_or_drifted_merge_range_is_not_selection_evidence(parents):
    git, _ = fake_git(parents=parents)
    with pytest.raises(ValueError, match='parents'):
        s.change_range('pull_request', event(), H, git)


@pytest.mark.parametrize('paths', [('tests/../escape.py',), ('/absolute.py',), ('tests/test_a.py', 'tests/test_a.py'),
                                  ('tests/conftest.py',), ('tests/helpers.py',), ('scripts/ci_selection.py',),
                                  ('pyproject.toml',)])
def test_malformed_paths_or_shared_fixture_policy_changes_require_full(paths):
    git, _ = fake_git(paths)
    with pytest.raises(ValueError):
        s.change_range('pull_request', event(), H, git)


@pytest.mark.parametrize('kind', ['schedule', 'workflow_dispatch', 'unknown'])
def test_nightly_dispatch_and_unknown_events_do_not_claim_a_reduced_range(kind):
    git, _ = fake_git()
    with pytest.raises(ValueError):
        s.change_range(kind, {}, H, git)


@pytest.mark.parametrize('mutation', [lambda d: d.update(checkout=B), lambda d: d.update(version=True),
    lambda d: d.update(paths=['../test.py']), lambda d: d.update(paths='tests/test_math.py'),
    lambda d: d.update(paths=['z', 'a']), lambda d: d.update(extra=True), lambda d: d.update(full=True)])
def test_invalid_range_receipt_cannot_select_a_subset(tmp_path, monkeypatch, mutation):
    source = 'def test_math(): assert 1 + 1 == 2\n'
    p = tmp_path / 'tests/test_math.py'; p.parent.mkdir(); p.write_text(source, encoding='utf-8')
    policy_file(tmp_path, source)
    data = {'version': 1, 'checkout': H, 'base': A, 'paths': []}
    mutation(data); receipt(tmp_path, data)
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path)); monkeypatch.setenv('GITHUB_SHA', H)
    items = [SimpleNamespace(path=p, nodeid='tests/test_math.py::test_math')]
    assert suite.select(items, s.WINDOWS_BOUNDARY, tmp_path) == (items, [], {})


def test_changed_reviewed_tests_helpers_and_unknown_tests_are_always_included(tmp_path):
    source = 'def test_math(): assert 1 + 1 == 2\n'
    p = tmp_path / 'tests/test_math.py'; p.parent.mkdir(); p.write_text(source, encoding='utf-8')
    data = policy_file(tmp_path, source)
    items = [SimpleNamespace(path=p, nodeid='tests/test_math.py::test_math')]
    assert suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed=set())[1] == items
    assert suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed={'tests/test_math.py'})[0] == items
    helper = tmp_path / 'tests/helper.py'; helper.write_text('VALUE=1', encoding='utf-8')
    data['reviewed_pure']['tests/test_math.py']['helpers'] = {'tests/helper.py': s.source_digest('VALUE=1')}
    (tmp_path / s.PROFILE).write_text(json.dumps(data), encoding='utf-8')
    assert suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed={'tests/helper.py'})[0] == items
    helper.write_text('VALUE=2', encoding='utf-8')
    assert suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed=set())[0] == items
    assert s.include_windows('tests/test_unreviewed.py', source, {}, cohorts={})[0]


def test_unrelated_product_edit_does_not_expire_every_reviewed_file(tmp_path):
    source = 'def test_math(): assert 1 + 1 == 2\n'
    policy_file(tmp_path, source)
    (tmp_path / 'labhq').mkdir(); (tmp_path / 'labhq/unrelated.py').write_text('VALUE=2', encoding='utf-8')
    assert 'implementation_sha256' not in s.policy(tmp_path)
    assert not s.include_windows('tests/test_math.py', source, s.policy(tmp_path)['reviewed_pure'],
                                 root=tmp_path, cohorts={})[0]


@pytest.mark.parametrize('source', ['import sys\nassert sys.platform', 'from sqlite3 import connect\nconnect("x")',
    'from subprocess import Popen as child\nchild(["fake"])', 'import ctypes', 'import _winapi',
    'from labhq.runner import daemon', 'from labhq.cli import main', 'from mcp import ClientSession',
    'import os\nos.fsync(1)', 'import os\nos.symlink("a", "b")'])
def test_os_boundaries_cannot_be_approved_away_by_a_matching_review(source):
    assert s.include_windows('tests/test_math.py', source, {'tests/test_math.py': review(source)}, cohorts={})[0]


@pytest.mark.parametrize('source', [
    'import subprocess as sp\nsp.run(["owned-child"], cwd=tmp_path)',
    'from subprocess import Popen as child\nchild(["owned-child"], cwd=tmp_path)',
    'import asyncio as aio\nawait aio.create_subprocess_exec("owned-child", cwd=tmp_path)',
    'from socket import socket as sock\ns=sock(); s.bind(("127.0.0.1",0))',
    'from labhq.util import free_port as allocate\nport=allocate()',
    'import asyncio as aio\nawait aio.start_server(callback,"127.0.0.1",0)',
    'from labhq.util import free_port\nimport uvicorn\nport=free_port()\nuvicorn.Config(app, port=port)',
    'import psutil\npsutil.Process(owned_pid).wait()',
])
def test_isolated_children_ephemeral_ports_and_owned_pids_are_not_global_locks(tmp_path, source):
    p = tmp_path / 'test_owned.py'; p.write_text(source, encoding='utf-8')
    assert not s.host_resource(p, tmp_path)


@pytest.mark.parametrize('source', [
    'from psutil import process_iter as scan\nlist(scan())',
    'import psutil as ps\nscan=ps.process_iter\nscan()',
    'import psutil\npsutil.net_connections()',
    'import socket as net\ns=net.socket(); s.bind(("127.0.0.1",8787))',
    'import socket\ns=socket.socket(); s.bind(address)',
    'import asyncio as aio\nawait aio.start_server(callback,"127.0.0.1",unknown_port)',
    'import uvicorn\nserver=uvicorn.Server(uvicorn.Config(app, port=55921))\nawait server.serve()',
    'import subprocess as sp\nsp.run(["tasklist", "/FO", "CSV"])',
])
def test_fixed_ports_global_scans_aliases_and_unknown_bindings_stay_locked(tmp_path, source):
    p = tmp_path / 'test_shared.py'; p.write_text(source, encoding='utf-8')
    assert s.host_resource(p, tmp_path)


def test_unused_helper_import_does_not_lock_but_referenced_fixture_and_callback_do(tmp_path):
    folder = tmp_path / 'tests'; folder.mkdir()
    (folder / 'helper.py').write_text('import psutil\nVALUE=1\ndef shared(): return list(psutil.process_iter())\n', encoding='utf-8')
    p = folder / 'test_client.py'
    for source in ('from helper import shared', 'from helper import VALUE\nassert VALUE == 1',
                   'from helper import shared\ndef test_math(): assert 1'):
        p.write_text(source, encoding='utf-8'); assert not s.host_resource(p, tmp_path)
    for source in ('from helper import shared as scan\nscan()', 'import helper as h\ncallback(h.shared)',
                   'from helper import shared\ndef test_fixture(shared): pass'):
        p.write_text(source, encoding='utf-8'); assert s.host_resource(p, tmp_path)


def test_import_time_global_scan_is_protected_even_when_definition_is_unused(tmp_path):
    folder = tmp_path / 'tests'; folder.mkdir()
    (folder / 'helper.py').write_text('import psutil\nGLOBAL=list(psutil.process_iter())\ndef unused(): pass', encoding='utf-8')
    p = folder / 'test_import.py'; p.write_text('from helper import unused\nunused()', encoding='utf-8')
    assert s.host_resource(p, tmp_path)


def test_python310_policy_profile_and_manifest_epoch_are_bound():
    for path in ('scripts/ci_selection.py', 'scripts/ci_suite.py'):
        ast.parse((s.ROOT / path).read_text(encoding='utf-8-sig'), feature_version=(3, 10))
    assert cs.VERSION == 4 and cs.COVERAGE['pytest-windows'] == s.WINDOWS_BOUNDARY
    assert s.PROFILE in cs.POLICY_FILES
    assert s.WINDOWS_BOUNDARY != 'windows-boundary-v1'


def test_manifest_upload_name_matches_the_new_consumer_epoch():
    import yaml
    workflow = yaml.safe_load((s.ROOT / cs.WORKFLOW).read_text(encoding='utf-8'))
    steps = workflow['jobs']['validation-manifest']['steps']
    upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact'))
    assert upload['with']['name'] == f'ci-full-v{cs.VERSION}-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}'


from tests.test_ci_skip_policy import evidence


@pytest.mark.parametrize('case', ['legacy-epoch', 'legacy-profile', 'legacy-artifact'])
def test_legacy_profile_or_receipts_cannot_be_reused_as_new_coverage(evidence, case):
    import hashlib
    from tests.test_ci_skip_policy import API, archive, reuse
    api = API(evidence)
    if case == 'legacy-epoch':
        api.manifest['version'] = 3
    elif case == 'legacy-profile':
        api.manifest['coverage']['pytest-windows'] = 'windows-boundary-v1'
    else:
        api.artifact['name'] = 'ci-full-v3-42-1'
    api.zip = archive(api.manifest)
    api.artifact['digest'] = 'sha256:' + hashlib.sha256(api.zip).hexdigest()
    api.artifact['size_in_bytes'] = len(api.zip)
    assert not reuse(api)


@pytest.mark.parametrize('before', [None, '0' * 40, 'f' * 40])
def test_same_tree_with_another_changed_test_range_cannot_reuse(evidence, before):
    from tests.test_ci_skip_policy import API, reuse
    api = API(evidence)
    api.identity['selection_base'] = before
    assert not reuse(api)
    assert not any('artifacts' in path for path in api.calls)


def test_main_reuse_uses_actual_push_before_not_an_inherited_identity_key(monkeypatch):
    current = {'checkout_sha': H, 'selection_base': B}
    monkeypatch.setenv('GITHUB_SHA', H)
    seen = []
    monkeypatch.setattr(cs, 'reuse_main', lambda value: seen.append(value) or True)
    assert cs.decide('push', {'ref': 'refs/heads/main', 'before': A}, current) == 'reuse'
    assert seen == [{'checkout_sha': H, 'selection_base': A}]
    assert current['selection_base'] == B


def test_assignment_aliases_and_escaped_modules_keep_shared_calls_locked(tmp_path):
    p = tmp_path / 'test_alias.py'
    p.write_text('import psutil\nmodule=psutil\nlist(module.process_iter())', encoding='utf-8')
    assert s.host_resource(p, tmp_path)
    folder = tmp_path / 'tests'; folder.mkdir()
    (folder / 'helpers.py').write_text('import psutil\ndef scan(): return psutil.process_iter()', encoding='utf-8')
    p.write_text('import helpers\ncallback(helpers)', encoding='utf-8')
    assert s.host_resource(p, tmp_path)


def test_local_non_socket_bind_method_does_not_claim_a_host_port(tmp_path):
    p = tmp_path / 'test_clock.py'
    p.write_text('class Clock:\n    def bind(self, callback): return callback\nclock=Clock()\nclock.bind(callback)', encoding='utf-8')
    assert not s.host_resource(p, tmp_path)
    p.write_text('import socket\nclass Adapter:\n    def bind(self, callback):\n        s=socket.socket(); s.bind(("127.0.0.1", 8787))\na=Adapter()\na.bind(callback)', encoding='utf-8')
    assert s.host_resource(p, tmp_path)


def test_external_collection_and_missing_range_run_full(tmp_path, monkeypatch):
    source = 'def test_math(): assert 1 + 1 == 2'
    policy_file(tmp_path, source)
    outside = SimpleNamespace(path=tmp_path.parent / 'external-test.py', nodeid='external::test_case')
    assert suite.select([outside], s.WINDOWS_BOUNDARY, tmp_path, changed=set()) == ([outside], [], {})
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path)); monkeypatch.setenv('GITHUB_SHA', H)
    assert suite.select([outside], s.WINDOWS_BOUNDARY, tmp_path) == ([outside], [], {})


@pytest.mark.parametrize('source,locked', [
    ('import uvicorn\nconfig=uvicorn.Config(app,port=55921)', False),
    ('import uvicorn\nserver=uvicorn.Server(uvicorn.Config(app,port=55921))', False),
    ('import uvicorn\nserver=uvicorn.Server(uvicorn.Config(app,port=0))\nawait server.serve()', False),
    ('import uvicorn\nserver=uvicorn.Server(uvicorn.Config(app,port=8787))\nawait server.serve()', True),
    ('from labhq.runner.approvals import Broker as LocalBroker\nbroker=LocalBroker(55921, a,b,c)\nawait broker.serve()', True),
    ('from labhq.runner.approvals import Broker as LocalBroker\nbroker=LocalBroker(0, a,b,c)\nawait broker.serve()', False),
    ('from labhq.runner.daemon import Runner as NativeRunner\nr=NativeRunner(settings)\nawait r.run_forever()', True),
    ('class Runner:\n    async def run_forever(self): pass\nr=Runner()\nawait r.run_forever()', False),
])
def test_listener_start_is_distinct_from_configuration_and_unknown_broker_port(tmp_path, source, locked):
    p = tmp_path / 'test_server.py'; p.write_text(source, encoding='utf-8')
    assert s.host_resource(p, tmp_path) is locked


@pytest.mark.parametrize('text', ['new test', 'import sys\nsys.platform', 'import sqlite3', 'runner', 'MCP',
    'fsync', 'windows', 'doctor', 'wsl', 'hook', 'private_paths'])
def test_all_original_unknown_source_controls_stay_included(text):
    assert s.include_windows('tests/test_unknown.py', text, {}, cohorts={})[0]


@pytest.mark.parametrize('port,locked', [(0, False), (8787, True)])
def test_standard_library_listener_addresses_are_not_configuration_only(tmp_path, port, locked):
    p = tmp_path / 'test_listener.py'
    p.write_text(f'from http.server import HTTPServer as Server\ns=Server(("127.0.0.1",{port}),Handler)', encoding='utf-8')
    assert s.host_resource(p, tmp_path) is locked


def test_ephemeral_value_in_another_scope_cannot_certify_an_unknown_fixture_port(tmp_path):
    p = tmp_path / 'test_scopes.py'
    p.write_text('import socket\nfrom labhq.util import free_port\ndef other():\n    port=free_port()\ndef test_binding(port):\n    s=socket.socket(); s.bind(("127.0.0.1",port))', encoding='utf-8')
    assert s.host_resource(p, tmp_path)
    p.write_text('import socket\nfrom labhq.util import free_port\ndef test_binding():\n    port=free_port()\n    s=socket.socket(); s.bind(("127.0.0.1",port))', encoding='utf-8')
    assert not s.host_resource(p, tmp_path)


def test_unknown_listener_callback_and_conditional_port_are_not_certified_local(tmp_path):
    p = tmp_path / 'test_callback.py'
    p.write_text('import asyncio as aio\ncallback(aio.start_server)', encoding='utf-8')
    assert s.host_resource(p, tmp_path)
    p.write_text('import socket\nfrom labhq.util import free_port\ndef test_binding():\n    if condition:\n        port=free_port()\n    s=socket.socket(); s.bind(("127.0.0.1",port))', encoding='utf-8')
    assert s.host_resource(p, tmp_path)


@pytest.mark.parametrize('file', ['test_research_bulk_pack.py', 'test_research_notes_budget.py',
    'test_research_continue.py', 'test_semantics_public.py', 'test_gif_encode.py', 'test_notes_index.py',
    'test_edition_check_713.py', 'test_evidence_verify.py', 'test_semantics_objects.py'])
def test_hybrid_encoding_atomic_redaction_and_path_contracts_remain_windows_boundaries(file):
    path = 'tests/' + file
    data = s.policy()
    source = (s.ROOT / path).read_text(encoding='utf-8')
    assert path in data['boundary_cohorts']
    assert s.include_windows(path, source, {path: review(source)}, cohorts=data['boundary_cohorts'])[0]
