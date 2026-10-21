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
    return {'version':6, 'profile':s.WINDOWS_BOUNDARY, 'default':'boundary',
            'known_tests':['tests/test_math.py'], 'boundary_cohorts':{}, 'mandatory_nodes':[],
            'mixed_nodes':{'tests/test_math.py':{'sha256':s.source_digest(text),
                'reason':'synthetic arithmetic review', 'nodes':{'test_native':'native boundary'}}}}


def policy_file(root, source):
    path = root / s.PROFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data=review(source)
    data['boundary_cohorts']={'tests/test_windows*.py':'native OS boundary'}
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
                                  ('tests/conftest.py',), ('tests/helpers.py',), ('tests/fixtures/schema.yaml',),
                                  ('tests/fixtures/example.txt',), ('scripts/ci_selection.py',),
                                  ('pyproject.toml',)])
def test_malformed_paths_or_shared_fixture_policy_changes_require_full(paths):
    git, _ = fake_git(paths)
    if any('..' in path.split('/') or path.startswith('/') for path in paths) or len(set(paths)) != len(paths):
        with pytest.raises(ValueError):
            s.change_range('pull_request', event(), H, git)
    else:
        assert s.change_range('pull_request', event(), H, git)['paths'] == sorted(paths)


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
    with pytest.raises(pytest.UsageError):
        suite.select(items, s.WINDOWS_BOUNDARY, tmp_path)


def test_changed_reviewed_tests_helpers_and_unknown_tests_are_always_included(tmp_path):
    source='def test_math(): assert 1+1==2'
    p=tmp_path/'tests/test_math.py'; p.parent.mkdir(); p.write_text(source,encoding='utf-8')
    data=policy_file(tmp_path,source)
    items=[SimpleNamespace(path=p,nodeid='tests/test_math.py::test_math')]
    assert suite.select(items,s.WINDOWS_BOUNDARY,tmp_path,changed=set())[1]==items
    assert suite.select(items,s.WINDOWS_BOUNDARY,tmp_path,changed={'tests/test_math.py'})[0]==items
    # An unrelated shared helper edit no longer expands every file.
    assert suite.select(items,s.WINDOWS_BOUNDARY,tmp_path,changed={'tests/helper.py'})[1]==items
    p.write_text(source+'\ndef test_new(): assert 2',encoding='utf-8')
    with pytest.raises(pytest.UsageError):
        suite.select(items,s.WINDOWS_BOUNDARY,tmp_path,changed=set())
    with pytest.raises(ValueError):
        s.include_windows('tests/test_unreviewed.py',source,data,node='test_math')


def test_unrelated_product_edit_does_not_expire_every_reviewed_file(tmp_path):
    source='def test_math(): assert 1+1==2'
    data=policy_file(tmp_path,source)
    (tmp_path/'labhq').mkdir(); (tmp_path/'labhq/unrelated.py').write_text('VALUE=2',encoding='utf-8')
    assert 'implementation_sha256' not in s.policy(tmp_path)
    assert not s.include_windows('tests/test_math.py',source,data,root=tmp_path,node='test_math')[0]


@pytest.mark.parametrize('source', ['import sys\nassert sys.platform', 'from sqlite3 import connect\nconnect("x")',
    'from subprocess import Popen as child\nchild(["fake"])', 'import ctypes', 'import _winapi',
    'from labhq.runner import daemon', 'from labhq.cli import main', 'from mcp import ClientSession',
    'import os\nos.fsync(1)', 'import os\nos.symlink("a", "b")'])
def test_os_boundaries_cannot_be_approved_away_by_a_matching_review(source):
    with pytest.raises(ValueError):
        s.include_windows('tests/test_math.py',source,review(source),node='test_math')


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
    assert cs.VERSION == 8 and cs.COVERAGE['pytest-windows'] == s.WINDOWS_BOUNDARY
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
    with pytest.raises(pytest.UsageError):
        suite.select([outside], s.WINDOWS_BOUNDARY, tmp_path, changed=set())
    monkeypatch.setenv('CI_SUITE_ARTIFACT_DIR', str(tmp_path)); monkeypatch.setenv('GITHUB_SHA', H)
    with pytest.raises(pytest.UsageError):
        suite.select([outside], s.WINDOWS_BOUNDARY, tmp_path)


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
    with pytest.raises(ValueError):
        s.include_windows('tests/test_unknown.py',text,review(''),node='test_math')


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


@pytest.mark.parametrize('file', ['test_research_bulk_pack.py','test_research_notes_budget.py',
    'test_research_continue.py','test_semantics_public.py','test_gif_encode.py','test_notes_index.py',
    'test_edition_check_713.py','test_evidence_verify.py','test_semantics_objects.py'])
def test_hybrid_encoding_atomic_redaction_and_path_contracts_remain_windows_boundaries(file):
    path='tests/'+file; data=s.policy(); source=(s.ROOT/path).read_text(encoding='utf-8')
    if path in data['mixed_nodes']:
        for node in data['mixed_nodes'][path]['nodes']:
            assert s.include_windows(path,source,data,node=node)[0]
    else:
        # Nonessential hybrid suites remain intact in both full profiles. Their
        # native primitives are represented by durable/private/receipt contracts.
        assert path not in data['boundary_cohorts']
        item=SimpleNamespace(path=s.ROOT/path,nodeid=path+'::test_contract')
        for profile in ('linux-full-v1','windows-full-v1'):
            assert suite.select([item],profile)==([item],[],{})


@pytest.mark.parametrize('source,locked', [
    ('from labhq import proc_tree as tree\ntree.child_pids(owned_pid)', True),
    ('from labhq.proc_tree import _table as snapshot\nsnapshot()', True),
    ('from labhq.proc_tree import kill_tree as end_owned\nend_owned(owned_pid, created=ticks)', True),
    ('from labhq import proc_tree\nproc_tree.pin_ancestors()', True),
    ('from labhq.adapters import base as adapter\nadapter._windows_process_snapshot()', True),
    ('from labhq.adapters.base import _idle_process_tree as inspect\ninspect(owned_pid, [])', True),
    ('from labhq import proc_tree', False),
    ('from labhq.proc_tree import Proc\nrow=Proc(1,2,3)', False),
    ('from labhq import proc_tree\nproc_tree.parse_created("20260101")', False),
])
def test_reviewed_project_global_table_apis_are_capabilities_not_module_names(tmp_path, source, locked):
    p = tmp_path / 'test_project_scan.py'; p.write_text(source, encoding='utf-8')
    assert s.host_resource(p, tmp_path) is locked


def test_gateway_watcher_alias_and_used_helper_preserve_snapshot_lock(tmp_path):
    folder = tmp_path / 'tests'; folder.mkdir()
    (folder / 'helpers.py').write_text(
        'from labhq.gateway.shutdown import watch_gateway_exit as watch\n'
        'def finish(): return watch(settings, pid, created)\n', encoding='utf-8')
    p = folder / 'test_watch.py'
    p.write_text('from helpers import finish as end\ndef test_watch(): end()\n'
                 'def test_pure(): assert 2 + 2 == 4\n', encoding='utf-8')
    assert s.host_resource(p, tmp_path, symbols=['test_watch'])
    assert not s.host_resource(p, tmp_path, symbols=['test_pure'])


@pytest.mark.parametrize('port,locked', [('0', False), ('8787', True), ('unknown', True)])
def test_runner_broker_owned_zero_and_node_scope(tmp_path, port, locked):
    p = tmp_path / 'test_runner.py'
    p.write_text('from labhq.settings import Settings\nfrom labhq.runner.daemon import Runner\n'
        'async def test_serve():\n    s=Settings()\n    s.runner.broker_port=' + port + '\n'
        '    runner=Runner(s)\n    await runner.run_forever()\n'
        'def test_pure(): assert 1\n', encoding='utf-8')
    assert s.host_resource(p, tmp_path, symbols=['test_serve']) is locked
    assert not s.host_resource(p, tmp_path, symbols=['test_pure'])


def test_runner_port_proof_rejects_escaping_or_conditional_settings(tmp_path):
    p = tmp_path / 'test_escape.py'
    for statement in ('mutate(s)', 'alias=s', 's.runner.broker_port=8787', 's.runner=unknown',
                      'if condition:\n        s.runner.broker_port=0'):
        p.write_text('from labhq.settings import Settings\nfrom labhq.runner.daemon import Runner\n'
            'async def test_serve():\n    s=Settings()\n    s.runner.broker_port=0\n    ' + statement + '\n'
            '    runner=Runner(s)\n    await runner.run_forever()\n', encoding='utf-8')
        assert s.host_resource(p, tmp_path, symbols=['test_serve'])


def test_reviewed_watcher_chain_is_still_the_actual_product_source():
    # Parse only; never import/call the native watcher or process APIs.
    def function(path, name):
        tree = ast.parse((s.ROOT / path).read_text(encoding='utf-8'), feature_version=(3, 10))
        aliases = s.aliases(tree)
        package = Path(path).parent.parts
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.level:
                prefix = '.'.join(package[:len(package) - n.level + 1])
                for item in n.names:
                    aliases[item.asname or item.name] = '.'.join(filter(None, (prefix, n.module, item.name)))
        return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), aliases
    watcher, aliases = function('labhq/gateway/shutdown.py', 'watch_gateway_exit')
    assert any(isinstance(n, ast.Call) and s.qualified(n.func, aliases) == 'labhq.cli._terminate_tree'
               for n in ast.walk(watcher))
    stop, aliases = function('labhq/cli.py', '_terminate_tree')
    assert any(isinstance(n, ast.Call) and s.qualified(n.func, aliases) == 'labhq.proc_tree.kill_tree'
               for n in ast.walk(stop))


def test_same_module_fixtures_and_autouse_teardown_are_in_node_lock(tmp_path):
    p = tmp_path / 'test_fixtures.py'
    source = ('import pytest\nimport psutil\n@pytest.fixture\n'
              'def shared():\n    yield\n    list(psutil.process_iter())\n'
              'def test_with(shared): pass\ndef test_pure(): assert 1\n')
    p.write_text(source, encoding='utf-8')
    assert s.host_resource(p, tmp_path, symbols=['test_with'])
    assert not s.host_resource(p, tmp_path, symbols=['test_pure'])
    p.write_text(source.replace('@pytest.fixture', '@pytest.fixture(autouse=True)'), encoding='utf-8')
    assert s.host_resource(p, tmp_path, symbols=['test_pure'])
    p.write_text(source + '\n@pytest.mark.usefixtures("shared")\ndef test_marked(): pass\n', encoding='utf-8')
    assert s.host_resource(p, tmp_path, symbols=['test_marked'])
    p.write_text(source.replace('@pytest.fixture', '@pytest.fixture(name="renamed")')
                 .replace('test_with(shared)', 'test_with(renamed)'), encoding='utf-8')
    assert s.host_resource(p, tmp_path, symbols=['test_with'])


def test_actual_durable_runner_zero_and_watcher_nodes_without_native_execution():
    assert not s.host_resource(s.ROOT / 'tests/test_durable_writer.py',
                               symbols=['test_runner_shutdown_waits_for_queued_writes'])
    assert s.host_resource(s.ROOT / 'tests/test_web_shutdown_process.py',
                           symbols=['test_the_watcher_stops_a_gateway_that_never_finishes_its_shutdown'])


def test_include_only_union_preserves_ordinary_logic_without_per_file_pure_proof(tmp_path):
    data = review('def test_math(): assert 2')
    data['mixed_nodes'] = {}
    source = 'def test_math(): assert schema.validate({"field": 2})'
    assert not s.include_windows('tests/test_math.py', source, data)[0]
    assert not s.include_windows('tests/test_math.py', source + '\n# unrelated prose', data)[0]
    assert not s.include_windows('tests/test_math.py', source, data, changed={'tests/test_math.py'})[0]
    with pytest.raises(ValueError):
        s.include_windows('tests/test_new.py', source, data)


@pytest.mark.parametrize('source', ['import os\ndef test_math(): os.fsync(fd)',
    'from subprocess import run as child\ndef test_math(): child(["owned"])',
    'import sys\ndef test_math(): assert sys.platform',
    'import sqlite3\ndef test_math(): sqlite3.connect("owned")',
    'import ctypes as native\ndef test_math(): native.windll.kernel32'])
def test_include_only_backstop_protects_qualified_native_api_aliases(source):
    data = review(source); data['mixed_nodes'] = {}
    assert s.include_windows('tests/test_math.py', source, data, changed={'tests/test_math.py'})[0]


def test_mixed_modules_keep_native_nodes_and_restore_full_on_drift_or_unknown_node():
    data = s.policy()
    for path, row in data['mixed_nodes'].items():
        source = (s.ROOT / path).read_text(encoding='utf-8')
        declared = {n.name for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name.startswith('test_')}
        assert set(row['nodes']) <= declared
        for node in row['nodes']:
            assert s.include_windows(path, source, data, node=node)[0]
        assert any(not s.include_windows(path, source, data, node=node)[0] for node in declared - set(row['nodes']))
        with pytest.raises(ValueError):
            s.include_windows(path, source, data, node='unrecognized_collector_node')
        with pytest.raises(ValueError):
            s.include_windows(path, source + '\n# drift', data, node=next(iter(declared)))


@pytest.mark.parametrize('case', ['v4-manifest', 'v2-profile', 'v4-artifact'])
def test_previous_boundary_policy_cannot_certify_the_include_only_contract(evidence, case):
    import hashlib
    from tests.test_ci_skip_policy import API, archive, reuse
    api = API(evidence)
    if case == 'v4-manifest':
        api.manifest['version'] = 4
    elif case == 'v2-profile':
        api.manifest['coverage']['pytest-windows'] = 'windows-boundary-v2'
    else:
        api.artifact['name'] = 'ci-full-v4-42-1'
    api.zip = archive(api.manifest)
    api.artifact['digest'] = 'sha256:' + hashlib.sha256(api.zip).hexdigest()
    api.artifact['size_in_bytes'] = len(api.zip)
    assert not reuse(api)


def test_boundary_backstop_is_per_node_and_follows_used_native_helpers(tmp_path):
    folder = tmp_path / 'tests'; folder.mkdir()
    (folder / 'helpers.py').write_text('import subprocess as sp\n'
        'def native(): sp.run(["owned-child"])\n'
        'def pure(): return {"field": 2}\n', encoding='utf-8')
    source = ('from helpers import native as launch, pure as data\n'
              'def test_native(): launch()\n'
              'def test_pure(): assert data()=={"field":2}\n')
    policy = review(source); policy['mixed_nodes'] = {}
    assert not s.include_windows('tests/test_math.py', source, policy, root=tmp_path, node='test_native')[0]
    assert s.include_windows('tests/test_math.py', source, policy, root=tmp_path, changed={'tests/helpers.py'}, node='test_native')[0]
    assert not s.include_windows('tests/test_math.py', source, policy, root=tmp_path, node='test_pure')[0]


def test_import_time_native_call_and_autouse_fixture_remain_boundaries(tmp_path):
    policy = review(''); policy['mixed_nodes'] = {}
    for source in ('import socket\ns=socket.socket()\ns.bind(("localhost",0))\ndef test_pure(): assert 1',
        'import pytest\nimport subprocess\n@pytest.fixture(autouse=True)\n'
        'def start(): subprocess.run(["owned-child"])\ndef test_pure(): assert 1',
        'import subprocess\ndef start(): subprocess.run(["owned-child"])\n'
        'GLOBAL=start()\ndef test_pure(): assert 1'):
        assert s.include_windows('tests/test_math.py', source, policy, root=tmp_path, changed={'tests/test_math.py'}, node='test_pure')[0]


def test_unused_native_sibling_is_not_a_boundary_for_a_pure_test():
    source = ('import subprocess\ndef unused(): subprocess.run(["owned-child"])\n'
              'def test_pure(): assert 1+1==2\ndef test_native(): unused()')
    policy = review(source); policy['mixed_nodes'] = {}
    assert not s.include_windows('tests/test_math.py', source, policy, node='test_pure')[0]
    assert s.include_windows('tests/test_math.py', source, policy, changed={'tests/test_math.py'}, node='test_native')[0]


def test_suite_node_cache_cannot_apply_first_pure_decision_to_native_sibling(tmp_path):
    source = ('import subprocess\ndef test_pure(): assert 1\n'
              'def test_native(): subprocess.run(["owned-child"])')
    data = policy_file(tmp_path, source); data['mixed_nodes'] = {}
    (tmp_path / s.PROFILE).write_text(json.dumps(data), encoding='utf-8')
    path = tmp_path / 'tests/test_math.py'; path.parent.mkdir(exist_ok=True)
    path.write_text(source, encoding='utf-8')
    pure = SimpleNamespace(path=path, nodeid='tests/test_math.py::test_pure')
    native = SimpleNamespace(path=path, nodeid='tests/test_math.py::test_native')
    for items in ([pure, native], [native, pure]):
        chosen, omitted, _ = suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed={'tests/test_math.py'})
        assert chosen == [native] and omitted == [pure]
