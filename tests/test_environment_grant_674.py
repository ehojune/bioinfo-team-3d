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


def runner_for(root, engine, zones=(), entries=2000, plan=None):
    runner = daemon_runner(root)
    runner.s.policy = SimpleNamespace(data_zones=[])
    runner._resolve_agent = lambda task: SimpleNamespace(engine=engine, sandbox=None, permission_mode=plan, builtin_tools=None)
    runner._zones = lambda: list(zones)
    runner.s.runner.reference_scan_max_entries = entries
    runner.s.runner.reference_scan_max_depth = 16
    return runner


def test_a_verified_environment_is_granted_to_a_claude_consumer(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None
    [grant] = runner._env_grants[task.id]
    assert Path(grant["venv"]) == folder / ".venv"
    assert Path(grant["python"]) == folder / expected["python_path"]


def test_engines_without_any_environment_path_keep_the_refusal(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.cli)
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
    runner = runner_for(root, Engine.cli)
    task = bound_task(folder, artifact, expected)
    monkeypatch.setattr(daemon, "ENVIRONMENT_ALIAS_AVAILABLE", True)
    assert runner._environment_refusal_reason(task) is None  # a future alias would open it for any engine


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs no privilege on POSIX")
def test_a_link_inside_the_venv_that_leads_into_a_zone_is_not_shared(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    zone = tmp_path / "dua"
    zone.mkdir()
    (zone / "raw.tsv").write_text("controlled")
    (folder / ".venv" / "lib").mkdir(exist_ok=True)
    (folder / ".venv" / "lib" / "data").symlink_to(zone, target_is_directory=True)
    runner = runner_for(root, Engine.claude_code, zones=[zone])
    task = bound_task(folder, artifact, expected)
    reason = runner._environment_refusal_reason(task)
    assert "cannot be shared read-only" in reason and "controlled data zone" in reason
    assert task.id not in runner._env_grants


def test_a_listing_that_cannot_finish_is_refused_not_trusted(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    for index in range(30):
        (folder / ".venv" / f"file{index}").write_text("x")
    runner = runner_for(root, Engine.claude_code, entries=2)  # the scan allows 2 x 10 entries
    reason = runner._environment_refusal_reason(bound_task(folder, artifact, expected))
    assert "listing was incomplete" in reason


def test_a_plain_verified_venv_has_no_grant_problem(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    assert runner._environment_grant_problem(folder / ".venv") is None


def test_a_grant_does_not_outlive_its_task_or_pass_to_a_reused_id(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None and task.id in runner._env_grants
    plain = task.model_copy(update={"meta": {"kind": "step", "environment_bindings": []}})
    assert runner._environment_refusal_reason(plain) is None  # the same id again, now without a binding
    assert task.id not in runner._env_grants  # the early return cleared the old grant too
    assert runner._apply_environment_grants(plain, ["x"], read_only=False) == (["x"], {}, "")


def test_a_read_only_consumer_is_refused_rather_than_run_on_the_system_python(tmp_path):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code, plan="plan")  # permission_mode plan makes the run read-only
    task = bound_task(folder, artifact, expected)
    assert "read-only step cannot be given the planned environment" in runner._environment_refusal_reason(task)
    assert task.id not in runner._env_grants
    task.meta["kind"] = "review"  # a review step is read-only too
    plain = runner_for(root, Engine.claude_code)
    assert "read-only step" in plain._environment_refusal_reason(task)


def test_an_environment_without_a_write_deny_rule_is_not_shared(tmp_path, monkeypatch):
    import labhq.runner.daemon as daemon
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, Engine.claude_code)
    task = bound_task(folder, artifact, expected)
    monkeypatch.setattr(daemon, "claude_rule_ready", lambda path: False)  # a UNC workspace
    reason = runner._environment_refusal_reason(task)
    assert "cannot be shared read-only" in reason and "network path" in reason
    assert task.id not in runner._env_grants


@pytest.mark.parametrize("engine", [Engine.codex, Engine.gemini])
def test_codex_and_gemini_get_the_interpreter_path_without_a_read_root(tmp_path, engine):
    root, folder, artifact, expected = fixture(tmp_path)
    runner = runner_for(root, engine)
    task = bound_task(folder, artifact, expected)
    assert runner._environment_refusal_reason(task) is None
    other = str(tmp_path / "reference")
    read_dirs, env, note = runner._apply_environment_grants(task, [other], read_only=False)
    assert read_dirs == [other]  # Codex's sandbox already reads outside the work folder; Gemini has no read-only grant
    python = str(folder / expected["python_path"])
    assert env == {"LABHQ_ENV_PYTHON": python, "PYTHONDONTWRITEBYTECODE": "1"} and python in note


@pytest.mark.parametrize("engine", [Engine.codex, Engine.gemini])
def test_the_fail_closed_checks_apply_to_every_engine(tmp_path, monkeypatch, engine):
    import labhq.runner.daemon as daemon
    root, folder, artifact, expected = fixture(tmp_path)
    task = bound_task(folder, artifact, expected)
    monkeypatch.setattr(daemon, "claude_rule_ready", lambda path: False)  # a UNC workspace
    runner = runner_for(root, engine)
    assert "cannot be shared read-only" in runner._environment_refusal_reason(task)  # same refusal as for Claude
    monkeypatch.undo()
    plan = runner_for(root, engine, plan="plan")
    assert "read-only step" in plan._environment_refusal_reason(task)
    tampered = runner_for(root, engine)
    artifact["output_sha256"]["outputs/env/runner-manifest.json"] = "0" * 64
    assert "environment validation failed" in tampered._environment_refusal_reason(task)
