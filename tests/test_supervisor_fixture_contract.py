"""Mock-only proof of the CI fixture's launch boundary; no native/process calls."""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq import cli, windows_launch as win
from labhq.settings import Settings
from tests import test_supervisor_process as fixture


def test_only_exit_logging_contract_uses_ordinary_fixture():
    tree = ast.parse(Path(fixture.__file__).read_text(encoding="utf-8"))
    users = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)
             and any(arg.arg == "logging_run" for arg in node.args.args)]
    assert users == ["test_the_supervisor_logs_the_exit_code_of_a_role_that_fails"]


def test_logging_fixture_uses_one_ordinary_popen_and_existing_cleanup(tmp_path, monkeypatch):
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")},
                                        "runner": {"state_dir": str(tmp_path / "state")}})
    settings.config_path = str(tmp_path / "config.yaml")
    monkeypatch.setattr(fixture, "_settings", lambda *a: settings)
    monkeypatch.setattr(fixture, "_role_record", lambda *a: None)
    monkeypatch.setattr(win, "spawn", Mock(side_effect=AssertionError("logging contract requested detachment")))
    child = SimpleNamespace(pid=930_000_003, poll=Mock(return_value=1), wait=Mock(return_value=1),
                            close=Mock(), kill=Mock())
    popen = Mock(return_value=child)
    monkeypatch.setattr(fixture.subprocess, "Popen", popen)
    factory = fixture.run.__wrapped__(tmp_path)
    logging = fixture.logging_run.__wrapped__(next(factory), monkeypatch)
    try:
        process, actual = logging("import sys; sys.exit(7)")
        assert actual is settings and process.pid == child.pid
        popen.assert_called_once()
        command, = popen.call_args.args
        assert Path(command[1]).read_text(encoding="utf-8") == fixture.LOGGING_SUPERVISE
        assert command[-1] == str(fixture.ROOT)
        options = popen.call_args.kwargs
        assert options["stdout"].closed and options["stdin"] == fixture.subprocess.DEVNULL
        assert "creationflags" not in options and "env" not in options
        assert Path(options["stdout"].name).is_relative_to(cli._launch_state(settings))
        win.spawn.assert_not_called()
    finally:
        with pytest.raises(StopIteration):
            next(factory)
    child.wait.assert_called_once_with(timeout=30)
    child.close.assert_called_once_with()
    child.kill.assert_not_called()


def test_logging_fixture_never_removes_a_requested_entry_guard(tmp_path, monkeypatch):
    monkeypatch.setenv(win.CHECK_ENV, "1")
    popen = Mock()
    monkeypatch.setattr(fixture.subprocess, "Popen", popen)
    with pytest.raises(AssertionError):
        fixture._start_logging_script(object(), tmp_path / "logging.py", [])
    popen.assert_not_called()


@pytest.mark.parametrize("role,command_ok", [("gateway", True), ("runner", True), ("gateway", False)])
def test_logging_script_launches_only_its_exact_fake_gateway(monkeypatch, tmp_path, role, command_ok):
    monkeypatch.setattr(sys, "argv", ["logging.py", "config.yaml", "fake-code", str(fixture.ROOT)])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(win, "spawn", win.spawn)  # Restore the child-script injection when this mock test ends.
    monkeypatch.setattr(cli, "_child_launch", cli._child_launch)
    popen = Mock(return_value=object())
    monkeypatch.setattr(fixture.subprocess, "Popen", popen)
    tree = ast.parse(fixture.LOGGING_SUPERVISE, feature_version=(3, 10))
    # Only imports, definitions and boundary injection; never supervisor.main/process/thread/signal work.
    assert isinstance(tree.body[-1], ast.Raise)
    exec(compile(ast.Module(body=tree.body[:-1], type_ignores=[]), "<logging-fixture>", "exec"), {})
    command, options = cli._child_launch(object(), "gateway")
    if not command_ok:
        command = [sys.executable, "-m", "labhq.cli"]
    if role == "gateway" and command_ok:
        assert win.spawn(command, state=tmp_path, role=role, **options) is popen.return_value
        popen.assert_called_once_with(command, **options)
        assert "creationflags" not in options and "env" not in options
    else:
        with pytest.raises(AssertionError):
            win.spawn(command, state=tmp_path, role=role, **options)
        popen.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_fake_role_fixture_enters_verified_launch_with_file_output(tmp_path, monkeypatch, platform):
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")},
                                        "runner": {"state_dir": str(tmp_path / "state")}})
    settings.config_path = str(tmp_path / "config.yaml")
    monkeypatch.setattr(fixture, "_settings", lambda *a: settings)
    monkeypatch.setattr(fixture, "_role_record", lambda *a: None)
    monkeypatch.setattr(win.sys, "platform", platform)
    monkeypatch.setattr(win, "current_job", lambda: {"in_job": True})
    monkeypatch.setattr(win, "child_job_membership", lambda *a, **kw: False)
    child = SimpleNamespace(pid=930_000_001, poll=Mock(return_value=1), wait=Mock(return_value=1),
                            kill=Mock(), close=Mock())
    commands = []
    def popen(command, **options):
        commands.append((command, options))
        options["stdout"].write("fixture child output\n")
        return child
    spawn = Mock(wraps=lambda *a, **kw: win_spawn(*a, popen=popen, **kw))
    win_spawn = win.spawn
    monkeypatch.setattr(win, "spawn", spawn)
    monkeypatch.setattr(fixture.subprocess, "Popen", lambda *a, **kw: pytest.fail("raw Popen bypassed launch gate"))
    run = fixture.run.__wrapped__(tmp_path)
    try:
        process, actual = next(run)("import sys; sys.exit(7)")
        assert actual is settings
        spawn.assert_called_once()
        assert spawn.call_args.kwargs["role"] == "gateway-supervisor"
        assert spawn.call_args.kwargs["state"] == cli._launch_state(settings)
        command, options = commands[0]
        assert Path(options["stdout"].name).is_relative_to(cli._launch_state(settings))
        assert options["stdout"].closed
        assert options["cwd"] == str(fixture.ROOT)
        if platform == "win32":
            assert command[0] == sys._base_executable
            assert options["creationflags"] & win.BREAKAWAY
            assert options["env"][win.CHECK_ENV] == "1"
            assert options["env"]["__PYVENV_LAUNCHER__"] == sys.executable
        else:
            assert command[0] == sys.executable and options["start_new_session"]
        output, error = process.communicate(timeout=120)
        assert "fixture child output" in output and error is None and process.returncode == 1
    finally:
        with pytest.raises(StopIteration):
            next(run)
    child.wait.assert_any_call(timeout=30)
    child.close.assert_called_once_with()
    child.kill.assert_not_called()


@pytest.mark.parametrize("script", ["PARENT", "LAUNCHER"])
def test_parent_pin_scripts_preserve_chain_through_verified_launch(monkeypatch, tmp_path, script, capsys):
    calls = []
    child = SimpleNamespace(pid=930_000_002, wait=Mock(return_value=0), close=Mock())
    def launch(settings, entry, args):
        calls.append((settings, entry, args))
        return child
    monkeypatch.setattr(fixture, "_start_script", launch, raising=False)
    monkeypatch.setattr(win, "require_detached_job", Mock())
    settings = object()
    monkeypatch.setattr(fixture.Settings, "load", lambda config: settings)
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: '"parents_pinned"')
    monkeypatch.setattr(fixture.subprocess, "Popen", lambda *a, **kw: pytest.fail("raw ancestry launch"))
    args = ["parent.py", "supervise.py", "config.yaml", str(tmp_path), str(fixture.ROOT)]
    monkeypatch.setattr(sys, "argv", ["launcher.py", *args] if script == "LAUNCHER" else args)
    exec(compile(getattr(fixture, script), f"<{script}>", "exec"), {})
    win.require_detached_job.assert_called_once_with()
    assert len(calls) == 1 and calls[0][0] is settings
    assert calls[0][1] == ("parent.py" if script == "LAUNCHER" else "supervise.py")
    if script == "LAUNCHER":
        assert calls[0][2] == args[1:]
        child.wait.assert_called_once_with(timeout=120)
        assert str(child.pid) in capsys.readouterr().out
    else:
        assert calls[0][2] == ["config.yaml", fixture.SLEEP]
    child.close.assert_called_once_with()


def test_ci_failure_diagnostic_includes_safe_launch_facts(tmp_path):
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path)},
                                        "runner": {"state_dir": str(tmp_path)}})
    win.outcome(cli._launch_state(settings), "gateway", "unverified", child_in_job=True, child_job_query={})
    diagnostic = fixture._failure_evidence(settings)
    assert '"child_in_job": true' in diagnostic and '"status": "unverified"' in diagnostic


@pytest.mark.parametrize("reason", ["denied", "unknown membership", "receipt timeout"])
def test_fixture_propagates_launch_failure_without_raw_retry(tmp_path, monkeypatch, reason):
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path)},
                                        "runner": {"state_dir": str(tmp_path)}})
    streams = []
    failure = win.LaunchError(reason)
    def fail(*args, **options):
        streams.append(options["stdout"])
        raise failure
    spawn = Mock(side_effect=fail)
    monkeypatch.setattr(win, "spawn", spawn)
    monkeypatch.setattr(fixture.subprocess, "Popen", lambda *a, **kw: pytest.fail("unsafe retry"))
    kill = Mock(side_effect=AssertionError("unowned PID termination"))
    monkeypatch.setattr(fixture.os, "kill", kill)
    monkeypatch.setattr(fixture.proc_tree, "kill_tree", kill)
    with pytest.raises(AssertionError, match="detached fixture launch failed before supervisor startup") as caught:
        fixture._start_script(settings, tmp_path / "supervise.py", [])
    assert caught.value.__cause__ is failure and streams[0].closed
    spawn.assert_called_once()
    kill.assert_not_called()
    assert not cli._pid_file(settings, "gateway-supervisor").exists()
    assert not list(tmp_path.rglob("ack.json"))


def test_embedded_scripts_and_fixture_keep_python310_syntax():
    ast.parse(Path(fixture.__file__).read_text(encoding="utf-8"), feature_version=(3, 10))
    ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3, 10))
    for script in (fixture.SUPERVISE, fixture.PARENT, fixture.LAUNCHER):
        ast.parse(script, feature_version=(3, 10))
