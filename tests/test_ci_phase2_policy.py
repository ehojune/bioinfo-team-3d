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
    'from labhq.util import free_port\nport = free_port()',
    'from socket import socket as sock\ns = sock(); s.bind(("127.0.0.1", 0))',
    'import socket as net\ns = net.socket()',
    'from uvicorn import Server as NativeServer\nserver = NativeServer(config)',
    'import subprocess as sp\nsp.Popen(["synthetic"])',
    'from subprocess import run as execute\nexecute(["synthetic"])',
    'import asyncio as aio\nawait aio.create_subprocess_exec("synthetic")',
    'import os\nos.kill(123, 0)',
])
def test_actual_host_capability_calls_and_aliases_require_serialization(tmp_path, source):
    path = tmp_path / "test_native.py"
    path.write_text(source, encoding="utf-8")
    assert selection.host_resource(path, tmp_path)


def test_resource_helpers_follow_absolute_and_relative_imports_and_fail_closed(tmp_path):
    folder = tmp_path / "tests"
    folder.mkdir()
    (folder / "ports.py").write_text('from socket import socket\ndef reserve(): return socket()', encoding="utf-8")
    for source in ('from tests.ports import reserve', 'from .ports import reserve', 'from ports import reserve'):
        path = folder / "test_client.py"
        path.write_text(source, encoding="utf-8")
        assert selection.host_resource(path, tmp_path)
    path.write_text('syntax is not valid !', encoding="utf-8")
    assert selection.host_resource(path, tmp_path)


def test_reviewed_cohort_is_bounded_and_explained_without_exempting_hard_boundaries():
    data = json.loads((ROOT / selection.PROFILE).read_text(encoding="utf-8"))
    reviewed = data["reviewed_pure"]
    assert 10 <= len(reviewed) <= 25
    for path, row in reviewed.items():
        assert row["reason"].strip()
        assert selection.source_digest((ROOT / path).read_text(encoding="utf-8")) == row["sha256"]
        assert not selection.include_windows(path, (ROOT / path).read_text(encoding="utf-8"), reviewed)[0]
    for path in ("tests/test_windows_job_ci.py", "tests/test_state_isolation.py", "tests/test_supervisor_process.py",
                 "tests/test_cli.py", "tests/test_reproduction_receipt.py", "tests/test_durable_writer.py"):
        assert selection.include_windows(path, "", reviewed)[0]


@pytest.mark.parametrize("dependency", ["scripts/check.py", "config/schema.yaml", "labhq/catalog.yaml",
                                      "tests/conftest.py", "tests/test_research_protocol.py",
                                      "tests/test_research_cp1.py", "pyproject.toml"])
def test_non_python_runtime_and_reviewed_helper_changes_expire_the_review(tmp_path, dependency):
    before = selection.implementation_digest(tmp_path)
    path = tmp_path / dependency
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed review dependency", encoding="utf-8")
    assert selection.implementation_digest(tmp_path) != before


def test_profile_does_not_fingerprint_itself_but_is_bound_by_manifest_policy(tmp_path):
    from scripts import ci_skip
    before = selection.implementation_digest(tmp_path)
    path = tmp_path / selection.PROFILE
    path.parent.mkdir(parents=True)
    path.write_text('{"implementation_sha256":"replacement"}', encoding="utf-8")
    assert selection.implementation_digest(tmp_path) == before
    assert selection.PROFILE in ci_skip.POLICY_FILES
