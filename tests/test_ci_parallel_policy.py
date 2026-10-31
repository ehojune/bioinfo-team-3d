"""Synthetic profiles, workers and receipts only; no worker/process/thread launch."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_isolation as isolation, ci_selection as selection, ci_suite as suite
from scripts import ci_skip as cs

ROOT = Path(__file__).resolve().parents[1]


def test_unknown_and_os_boundary_sources_are_included_even_with_a_matching_review():
    from tests.test_ci_cohort823 import review
    for text in ('import sys\nassert sys.platform','import sqlite3\nsqlite3.connect("x")','import os\nos.fsync(1)'):
        with pytest.raises(ValueError):
            selection.include_windows('tests/test_new.py',text,review(text),node='test_math')
        with pytest.raises(ValueError):
            selection.include_windows('tests/test_math.py',text,review(text),node='test_math')


def test_only_unchanged_reviewed_text_can_be_excluded():
    from tests.test_ci_cohort823 import review
    source='def test_math():\n    assert 1+1==2\n'; data=review(source)
    assert not selection.include_windows('tests/test_math.py',source,data,node='test_math')[0]
    assert not selection.include_windows('tests/test_math.py',source.replace('\n','\r\n'),data,node='test_math')[0]
    with pytest.raises(ValueError):
        selection.include_windows('tests/test_math.py',source+'def test_new(): pass\n',data,node='test_math')
    with pytest.raises(ValueError):
        selection.include_windows('tests/test_renamed.py',source,data,node='test_math')


def test_unchanged_pure_review_is_local_to_source_and_helpers_not_all_runtime(tmp_path):
    from tests.test_ci_cohort823 import policy_file
    source = "def test_math(): assert 1 + 1 == 2"
    test = tmp_path / "tests/test_math.py"
    test.parent.mkdir()
    test.write_text(source, encoding="utf-8")
    policy_file(tmp_path, source)
    nodes = [SimpleNamespace(path=test, nodeid="test_math.py::test_math")]
    assert suite.select(nodes, selection.WINDOWS_BOUNDARY, tmp_path, changed=set())[0] == []
    runtime = tmp_path / "labhq/module.py"
    runtime.parent.mkdir()
    runtime.write_text("def calculate(): return 2", encoding="utf-8")
    assert suite.select(nodes, selection.WINDOWS_BOUNDARY, tmp_path, changed=set())[0] == []
    test.write_text(source + "\ndef test_new(): assert True", encoding="utf-8")
    with pytest.raises(pytest.UsageError):
        suite.select(nodes, selection.WINDOWS_BOUNDARY, tmp_path, changed=set())


def test_selection_keeps_every_node_of_unknown_files_and_both_full_profiles(monkeypatch,tmp_path):
    from tests.test_ci_cohort823 import policy_file
    pure,unknown=tmp_path/'tests/test_math.py',tmp_path/'tests/test_new.py'
    source='def test_math(): assert 1'
    policy_file(tmp_path,source); pure.parent.mkdir(exist_ok=True)
    pure.write_text(source,encoding='utf-8'); unknown.write_text('assert 2',encoding='utf-8')
    nodes=[SimpleNamespace(path=pure,nodeid='tests/test_math.py::test_math'),
           SimpleNamespace(path=unknown,nodeid='tests/test_new.py::test_c')]
    with pytest.raises(pytest.UsageError):
        suite.select(nodes,selection.WINDOWS_BOUNDARY,tmp_path,changed=set())
    for profile in ('linux-full-v1','windows-full-v1'):
        assert suite.select(nodes,profile,tmp_path)==(nodes,[],{})


def test_actual_profile_exclusions_have_reviewed_digest_and_keep_os_boundaries():
    data=selection.policy()
    assert len(data['known_tests'])>300
    for path,row in data['mixed_nodes'].items():
        assert row['reason'].strip() and row['nodes']
        source=(ROOT/path).read_text(encoding='utf-8')
        assert selection.source_digest(source)==row['sha256']
        for node in row['nodes']:
            assert selection.include_windows(path,source,data,node=node)[0]
    for path in ('tests/test_windows_job_ci.py','tests/test_supervisor_process.py','tests/test_state_isolation.py',
                 'tests/test_private_paths.py','tests/test_reproduction_receipt.py','tests/test_durable_writer.py',
                 'tests/test_continuation_review_807.py','tests/test_resume_receipt_clock.py',
                 'tests/test_stall_watch.py','tests/test_step_memory_limit.py'):
        assert selection.include_windows(path,(ROOT/path).read_text(encoding='utf-8'),data)[0]


def test_profile_contract_includes_changed_source_without_refreshing_approval(monkeypatch):
    data=selection.policy()
    monkeypatch.setattr(selection,'source_digest',lambda text:'0'*64)
    for path in data['mixed_nodes']:
        with pytest.raises(ValueError):
            selection.include_windows(path,(ROOT/path).read_text(encoding='utf-8'),data,node='test_math')


def test_policy_file_is_versioned_and_not_a_runtime_fingerprint():
    data=selection.policy()
    assert data['version']==6 and data['profile']==selection.WINDOWS_BOUNDARY
    assert 'implementation_sha256' not in data and data['default']=='boundary'


@pytest.mark.parametrize("workerinput", [{}, {"labhq_operator_home": "/different"}])
def test_workers_cannot_replace_the_original_operator_home(workerinput, tmp_path):
    with pytest.raises(ValueError, match="original operator home"):
        isolation.operator_home(str(tmp_path), workerinput)


def test_controller_and_fake_workers_keep_operator_guard_identity_and_distinct_temp_roots(tmp_path):
    home = str(tmp_path / "operator")
    assert isolation.operator_home(home) == home
    assert isolation.operator_home(home, {"labhq_operator_home": home}) == home
    paths = {tmp_path / w / "collection-home" for w in ("master", "gw0", "gw1")}
    assert len(paths) == 3 and all(p != Path(home) for p in paths)
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'node.workerinput["labhq_operator_home"] = _OPERATOR_HOME' in conftest
    assert 'operator_home(_OPERATOR_HOME, getattr(session.config, "workerinput", None))' in conftest
    assert 'session.config._tmp_path_factory.getbasetemp() / "collection-home"' in conftest
    assert 'patch.setenv("LABHQ_TEST_REAL_HOME", _OPERATOR_HOME)' in conftest


def test_actual_session_hooks_forward_operator_identity_before_worker_collection(monkeypatch, tmp_path):
    import conftest
    node = SimpleNamespace(workerinput={})
    conftest.pytest_configure_node(node)
    assert node.workerinput["labhq_operator_home"] == conftest._OPERATOR_HOME
    homes = []
    for worker in ("master", "gw0", "gw1"):
        env = {"LABHQ_STATE_DIR": "inherited", "LABHQ_CONFIG": "operator", "CODEX_HOME": "operator"}
        patch = SimpleNamespace(setenv=lambda k, v: env.__setitem__(k, v),
                                delenv=lambda k, raising=False: env.pop(k, None))
        monkeypatch.setattr(conftest, "_SESSION_ENV", patch)
        config = SimpleNamespace(_tmp_path_factory=SimpleNamespace(getbasetemp=lambda: tmp_path / worker))
        if worker != "master":
            config.workerinput = node.workerinput
        conftest.pytest_sessionstart(SimpleNamespace(config=config))
        assert env["LABHQ_TEST_REAL_HOME"] == conftest._OPERATOR_HOME
        assert env["HOME"] == env["USERPROFILE"] == str(tmp_path / worker / "collection-home")
        assert "LABHQ_STATE_DIR" not in env and "LABHQ_CONFIG" not in env and "CODEX_HOME" not in env
        assert env["LABHQ_HOME"].startswith(env["HOME"])
        homes.append(env["HOME"])
    assert len(set(homes)) == 3


def test_ci_resource_lock_covers_protocol_setup_body_and_teardown_without_native_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("CI_SUITE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(suite, "ROOT", tmp_path)
    observation = suite.Observation(SimpleNamespace(getoption=lambda name: "linux-full-v1" if name == "ci_profile" else None))
    observation.worker_count = 2
    events = []
    class Lock:
        def __init__(self, path, timeout):
            assert path == str(tmp_path / "host-resources.lock") and timeout == 1500
        def __enter__(self):
            events.append("acquire")
        def __exit__(self, *args):
            events.append("release")
    monkeypatch.setitem(sys.modules, "filelock", SimpleNamespace(FileLock=Lock))
    monkeypatch.setattr(suite, "host_resource", lambda path, **kwargs: True)
    protocol = observation.pytest_runtest_protocol(SimpleNamespace(path=tmp_path / "fixture.py", nodeid="fixture.py::test_fake"), None)
    next(protocol)
    assert events == ["acquire"]
    events += ["fixture setup", "test", "fixture teardown"]
    with pytest.raises(StopIteration):
        protocol.send(None)
    assert events == ["acquire", "fixture setup", "test", "fixture teardown", "release"]


@pytest.mark.parametrize("value", ["gw../1", "../master", "", "gw1/other"])
def test_worker_artifacts_cannot_escape_their_job_directory(value):
    with pytest.raises(ValueError):
        suite.safe_worker(value)


def test_referenced_local_test_helpers_with_fixed_ports_are_serialized(tmp_path):
    folder = tmp_path / "tests"
    folder.mkdir()
    (folder / "helper.py").write_text("import socket\ndef start():\n    s=socket.socket(); s.bind(('127.0.0.1',8787))", encoding="utf-8")
    test = folder / "test_a.py"
    test.write_text("from helper import start\nstart()", encoding="utf-8")
    assert selection.host_resource(test, tmp_path)
    test.write_text("from helper import start\nassert 1 + 1 == 2", encoding="utf-8")
    assert not selection.host_resource(test, tmp_path)


def test_summary_binds_full_and_selected_node_lists_and_reports_real_counts(tmp_path):
    (tmp_path / "metrics-master.json").write_text(json.dumps({"workers": 2}), encoding="utf-8")
    coverage = {"full_nodes": ["a", "b"], "selected_nodes": ["b"], "excluded_files": {"a": "reviewed"}}
    for worker in ("gw0", "gw1"):
        (tmp_path / f"collection-{worker}.json").write_text(json.dumps(coverage), encoding="utf-8")
    assert suite.summarize(tmp_path)["full_node_count"] == 2
    assert suite.summarize(tmp_path)["selected_node_count"] == 1
    coverage["selected_nodes"] = ["a"]
    (tmp_path / "collection-gw1.json").write_text(json.dumps(coverage), encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistent"):
        suite.summarize(tmp_path)


def test_report_records_job_queue_separately_from_setup_and_pytest(monkeypatch, tmp_path):
    monkeypatch.setenv("CI_SUITE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("CI_JOB_LABEL", "pytest-windows")
    monkeypatch.setattr(sys, "argv", ["ci_suite", "report"])
    metrics = {"profile": "windows-boundary-v6", "workers": 3, "cpu_logical": 8,
               "setup_s": 80, "pytest_s": 250, "results": {"passed": 1}, "longest_files": [["a.py", 250]]}
    monkeypatch.setattr(suite, "summarize", lambda path: metrics)
    def api(path):
        assert path == "actions/runs/42/attempts/2/jobs?per_page=100"
        return {"total_count": 1, "jobs": [{"name": "pytest-windows", "created_at": "2026-10-21T00:00:00Z",
                                            "started_at": "2026-10-21T00:00:07Z"}]}
    monkeypatch.setattr(cs, "api", api)
    suite.main()
    result = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert result["job_queue_s"] == 7 and result["setup_s"] == 80 and result["pytest_s"] == 250
    assert result["workers"] == 3 and result["cpu_logical"] == 8 and result["over_five_minutes"] is True
    assert result["longest_files"] == [["a.py", 250]]


def test_unrecognized_selection_policy_cannot_reduce_coverage(tmp_path):
    path = tmp_path / selection.PROFILE
    path.parent.mkdir(parents=True)
    path.write_text('{"version":1,"profile":"windows-boundary-v6","default":"exclude","reviewed_pure":{}}',
                    encoding="utf-8")
    with pytest.raises(ValueError):
        selection.policy(tmp_path)
    nodes = [SimpleNamespace(path=tmp_path / "new.py", nodeid="new.py::test_new")]
    with pytest.raises(pytest.UsageError):
        suite.select(nodes, "windows-boundary-v6", tmp_path)
    path.write_text('{"version":1,"version":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        selection.policy(tmp_path)


def test_workflow_parallelizes_only_explicit_outer_suites_and_keeps_full_windows_reachable():
    workflow = yaml.safe_load((ROOT / cs.WORKFLOW).read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    for name in ("pytest", "pytest-windows"):
        commands = "\n".join(s.get("run", "") for s in jobs[name]["steps"])
        assert "scripts.ci_suite" in commands
        if name == "pytest":
            assert '-n auto --dist loadfile' in commands and '"-n", "auto", "--dist", "loadfile"' in commands
            assert jobs[name]["strategy"]["matrix"]["python-version"] == ["3.10", "3.12"]
            diagnostics = next(s for s in jobs[name]["steps"] if s.get("timeout-minutes"))
            assert diagnostics["timeout-minutes"] == 25
            assert "StallDiagnostics" in diagnostics["run"] and "faulthandler_timeout=300" in diagnostics["run"]
        assert "--ci-profile" in commands and jobs[name]["env"]["PYTHONHASHSEED"] == "0"
        assert any(s.get("if") == "always()" and s.get("uses", "").startswith("actions/upload-artifact")
                   for s in jobs[name]["steps"])
    condition = jobs["pytest-windows"]["env"]["CI_COVERAGE_PROFILE"]
    assert all(word in condition for word in ("schedule", "workflow_dispatch", "windows-full-v1", "windows-boundary-v6"))
    assert "schedule:" in (ROOT / cs.WORKFLOW).read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pytest-xdist==3.8.0"' in project and "addopts" not in project
    assert cs.COVERAGE["pytest-windows"] == "windows-boundary-v6" and cs.VERSION == 8
    assert all(p in cs.POLICY_FILES for p in (selection.PROFILE, "scripts/ci_suite.py", "tests/conftest.py", "pyproject.toml"))


@pytest.mark.parametrize("summary,expected", [("Plain progress.", True), ("", False), ("first\nsecond", False),
                                            (123, False)])
def test_current_plain_language_patch_summary_is_metadata_only(summary, expected):
    value = {"pr": 793, "rows": [{"sha": "abcdef0", "at": "2026-10-21 00:40", "text": "Technical detail.",
                                   "summary": summary}]}
    assert cs.record(yaml.safe_dump(value)) is expected


# ---- #1242: a changed fixture reaches the tests that name it, and when none does, every routine Windows test ----

def _fixture_root(tmp_path, sources):
    from tests.test_ci_cohort823 import review

    (tmp_path / 'tests').mkdir(exist_ok=True)
    for name, text in sources.items():
        (tmp_path / 'tests' / name).write_text(text, encoding='utf-8')
    return review(sources['test_math.py'])


FIXTURE = 'tests/fixtures/fake_deadline_cli.py'


def test_a_changed_fixture_selects_the_test_that_runs_it_by_path_name_or_module(tmp_path):
    source = 'FAKE = ROOT / "tests" / "fixtures" / "fake_deadline_cli.py"\ndef test_logic(): assert 1\n'
    data = _fixture_root(tmp_path, {'test_math.py': source})
    args = dict(root=tmp_path, node='test_logic')
    assert not selection.include_windows('tests/test_math.py', source, data, changed=set(), **args)[0]
    included, reason = selection.include_windows('tests/test_math.py', source, data, changed={FIXTURE}, **args)
    assert included and 'refers to' in reason
    by_module = 'from tests.fixtures import fake_deadline_cli\ndef test_logic(): assert 1\n'
    data = _fixture_root(tmp_path, {'test_math.py': by_module})
    assert selection.include_windows('tests/test_math.py', by_module, data, changed={FIXTURE}, **args)[0]


def test_a_test_that_does_not_name_the_changed_fixture_stays_out_while_another_does(tmp_path):
    source = 'def test_logic(): assert 1\n'
    data = _fixture_root(tmp_path, {'test_math.py': source, 'test_other.py': 'RUN = "fake_deadline_cli.py"\n'})
    included, reason = selection.include_windows('tests/test_math.py', source, data, changed={FIXTURE},
                                                 root=tmp_path, node='test_logic')
    assert not included, "another test names the fixture, so no guess is needed"


def test_a_changed_fixture_that_no_test_names_runs_every_routine_windows_test(tmp_path):
    source = 'def test_logic(): assert 1\n'
    data = _fixture_root(tmp_path, {'test_math.py': source})
    included, reason = selection.include_windows('tests/test_math.py', source, data,
                                                 changed={'tests/fixtures/orphan_table.json'}, root=tmp_path,
                                                 node='test_logic')
    assert included and 'no collected test names' in reason


def test_a_changed_file_outside_the_fixtures_is_not_read_as_one(tmp_path):
    source = 'def test_logic(): assert 1\n'
    data = _fixture_root(tmp_path, {'test_math.py': source})
    assert not selection.include_windows('tests/test_math.py', source, data, changed={'tests/support/helper.txt'},
                                         root=tmp_path, node='test_logic')[0]


def test_the_real_fixture_only_change_of_pr_1235_now_reaches_its_windows_test():
    """The hole: only tests/fixtures/fake_deadline_cli.py changed, and the test that runs it as a subprocess was not
    chosen because nothing imports it."""
    path = 'tests/test_request_deadline_descendants_1232.py'
    text = (selection.ROOT / path).read_text(encoding='utf-8')
    assert selection.references_fixture(text, FIXTURE)
    included, reason = selection.include_windows(path, text, selection.policy(), root=selection.ROOT,
                                                 changed={FIXTURE})
    assert included, reason
