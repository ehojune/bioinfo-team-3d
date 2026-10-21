"""Workflow context and host-resource classification; no native fixtures."""
import ast
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_selection as selection

ROOT = Path(__file__).resolve().parents[1]


def test_job_environment_uses_only_contexts_available_before_runner_assignment():
    # GitHub context availability table: job.env excludes runner/env/steps/job.
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8"))
    import re
    allowed = {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}
    for job in workflow["jobs"].values():
        for value in job.get("env", {}).values():
            for expression in re.findall(r"\$\{\{(.*?)\}\}", str(value)):
                roots = set(re.findall(r"\b([A-Za-z_][\w]*)\s*(?:\.|\[)", expression))
                assert roots <= allowed, (value, roots - allowed)


@pytest.mark.parametrize("job", ["pytest", "pytest-windows"])
def test_artifact_directory_is_initialized_portably_in_first_step(job):
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8"))
    step = workflow["jobs"][job]["steps"][0]
    assert step["env"]["CI_ARTIFACT_ROOT"].startswith("${{ runner.temp }}")
    command = step["run"]
    assert 'os.environ' in command and 'GITHUB_ENV' in command
    assert 'CI_SUITE_ARTIFACT_DIR=' in command and 'CI_SETUP_STARTED=' in command
    # Python receives paths through env, not shell-dependent path interpolation.
    assert '${{' not in command
    code = command.split('python -c "', 1)[1].rsplit('"', 1)[0]
    tree = ast.parse(code, feature_version=(3, 10))
    assert any(isinstance(n, ast.keyword) and n.arg == "encoding" for n in ast.walk(tree))
    # Execute only the parsed stdlib statement with fake imports and file I/O;
    # both POSIX and Windows paths retain spaces/quotes, exactly two env lines.
    for path in ("/runner temp/ci suite", "D:\\runner temp\\ci-'suite'"):
        output = StringIO()
        def open_env(name, mode, *, encoding):
            assert (name, mode, encoding) == ("fake-env-file", "a", "utf-8")
            return output
        modules = {"os": SimpleNamespace(environ={"GITHUB_ENV": "fake-env-file", "CI_ARTIFACT_ROOT": path}),
                   "time": SimpleNamespace(time=lambda: 123.5)}
        exec(compile(tree, "<workflow-init>", "exec"), {
            "__builtins__": {"__import__": lambda name, *args: modules[name], "print": print, "str": str},
            "open": open_env,
        })
        assert output.getvalue().splitlines() == ["CI_SETUP_STARTED=123.5", "CI_SUITE_ARTIFACT_DIR=" + path]


@pytest.mark.parametrize("source", [
    '"Hub Runner subprocess free_port socket are words in a report."',
    'from labhq.gateway.server import Hub\nhub = Hub(settings)',
    'from httpx import AsyncClient\nclient = AsyncClient(transport=ASGITransport(app=app))',
    'class Runner: pass\nrunner = Runner()',
    'import socket\nmonkeypatch.setattr(socket, "socket", fake_socket)',
    'from unittest.mock import Mock\nsubprocess = Mock()\nsubprocess.run([])',
])
def test_mock_types_imports_and_strings_do_not_claim_a_shared_host_resource(tmp_path, source):
    path = tmp_path / "test_pure.py"
    path.write_text(source, encoding="utf-8")
    assert not selection.host_resource(path, tmp_path)


@pytest.mark.parametrize("source", [
    'from socket import socket as sock\ns = sock(); s.bind(("127.0.0.1", 8787))',
    'import psutil as system\nlist(system.process_iter())',
    'import uvicorn\nserver=uvicorn.Server(uvicorn.Config(app,port=55921))\nawait server.serve()',
    'import subprocess as sp\nsp.run(["tasklist"])',
])
def test_actual_shared_host_capability_calls_and_aliases_require_serialization(tmp_path, source):
    path = tmp_path / "test_native.py"
    path.write_text(source, encoding="utf-8")
    assert selection.host_resource(path, tmp_path)


def test_resource_helpers_follow_absolute_and_relative_calls_and_fail_closed(tmp_path):
    folder = tmp_path / "tests"
    folder.mkdir()
    (folder / "ports.py").write_text('import socket\ndef reserve():\n    s=socket.socket(); s.bind(("127.0.0.1", 8787))', encoding="utf-8")
    for source in ('from tests.ports import reserve\nreserve()', 'from .ports import reserve\nreserve()', 'from ports import reserve\nreserve()'):
        path = folder / "test_client.py"
        path.write_text(source, encoding="utf-8")
        assert selection.host_resource(path, tmp_path)
    path.write_text('syntax is not valid !', encoding="utf-8")
    assert selection.host_resource(path, tmp_path)


def test_reviewed_cohort_is_explained_without_exempting_hard_boundaries():
    from tests.test_ci_parallel_policy import test_actual_profile_exclusions_have_reviewed_digest_and_keep_os_boundaries
    test_actual_profile_exclusions_have_reviewed_digest_and_keep_os_boundaries()


def test_cohort_contract_accepts_changed_reviewed_sources(monkeypatch):
    from tests.test_ci_parallel_policy import test_profile_contract_includes_changed_source_without_refreshing_approval
    test_profile_contract_includes_changed_source_without_refreshing_approval(monkeypatch)


@pytest.mark.parametrize("dependency", ["scripts/check.py", "config/schema.yaml", "labhq/catalog.yaml"])
def test_unrelated_product_files_do_not_globally_expire_every_test(tmp_path, dependency):
    from tests.test_ci_cohort823 import policy_file
    source = "def test_math(): assert 1 + 1 == 2"
    policy_file(tmp_path, source)
    before = selection.policy(tmp_path)
    path = tmp_path / dependency
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed unrelated dependency", encoding="utf-8")
    assert selection.policy(tmp_path) == before


def test_profile_is_bound_by_manifest_policy_and_version():
    from scripts import ci_skip
    assert selection.PROFILE in ci_skip.POLICY_FILES
    assert ci_skip.COVERAGE["pytest-windows"] == selection.WINDOWS_BOUNDARY
    assert ci_skip.VERSION == 7
