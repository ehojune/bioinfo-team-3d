"""A verified runner environment is handed to a Claude consumer as a read-only grant (#674)."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq.models import Engine
from tests.test_runner_env_inputs_674 import bound_task, daemon_runner, env_fixture, write_receipt

NATIVE = "windows" if os.name == "nt" else "linux"


def fixture(tmp_path):
    """A genuine environment folder for this OS: a manifest that matches the plan and an interpreter file."""
    root, folder, artifact, expected, manifest, save = env_fixture(tmp_path)
    if NATIVE == "linux":
        for value in (expected, manifest):
            value.update(runtime="linux", python_path=".venv/bin/python")
        save(manifest)
        for old in (folder / ".venv/Scripts/python.exe",):
            old.unlink()
        (folder / ".venv/bin").mkdir(parents=True)
        (folder / ".venv/bin/python").write_bytes(b"python")
    expected["runner_id"] = manifest["runner_id"] = "lab pc 1"
    save(manifest)
    write_receipt(folder)
    return root, folder, artifact, expected


def runner_for(root, engine):
    runner = daemon_runner(root)
    runner._resolve_agent = lambda task: SimpleNamespace(engine=engine)
    return runner


def test_a_verified_environment_is_granted_to_a_claude_consumer(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None
    [grant] = runner._env_grants[task.id]
    assert Path(grant["venv"]) == folder / ".venv"
    assert Path(grant["python"]) == folder / expected["python_path"]


def test_other_engines_keep_the_refusal_because_they_get_no_read_grant(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.codex)
    task = bound_task(folder, artifact, expected)
    assert "protected interpreter alias is not implemented" in runner._environment_refusal_reason(task)
    assert task.id not in getattr(runner, "_env_grants", {})


def test_an_environment_that_does_not_check_out_is_refused_and_grants_nothing(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None  # an earlier good check leaves a grant behind
    artifact["output_sha256"]["outputs/env/runner-manifest.json"] = "0" * 64
    assert "environment validation failed" in runner._environment_refusal_reason(task)
    assert task.id not in runner._env_grants  # the stale grant is dropped with the refusal


def test_a_task_without_bindings_gets_no_grant(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    task.meta["environment_bindings"] = []
    assert runner._environment_refusal_reason(task) is None
    assert not getattr(runner, "_env_grants", {}).get(task.id)


def test_the_grant_becomes_a_read_dir_an_env_and_a_briefing_line(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None
    other = str(tmp_path / "reference")
    read_dirs, env, note = runner._apply_environment_grants(task, [other, other], read_only=False)
    venv = str(folder / ".venv")
    assert read_dirs == [other, venv]  # kept, deduplicated, venv added once
    python = str(folder / expected["python_path"])
    assert env["LABHQ_ENV_PYTHON"] == python and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert python in note and "read-only" in note.lower()


def test_a_read_only_task_gets_no_grant(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None
    assert runner._apply_environment_grants(task, ["x"], read_only=True) == (["x"], {}, "")
    runner._env_grants.pop(task.id)
    assert runner._apply_environment_grants(task, ["x"], read_only=False) == (["x"], {}, "")  # nothing to grant


def test_the_flag_still_gates_every_other_engine(tmp_path, monkeypatch):
    import labhq.runner.daemon as daemon
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.codex)
    task = bound_task(folder, artifact, expected)
    monkeypatch.setattr(daemon, "ENVIRONMENT_ALIAS_AVAILABLE", True)
    assert runner._environment_refusal_reason(task) is None  # a future alias would open it for any engine
