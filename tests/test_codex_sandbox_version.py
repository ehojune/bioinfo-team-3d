"""Staff elevated sandbox setup vs. the Codex version in use (#328). Fakes only: no real Codex, no UAC."""
import json
import os
import sys
import time

import pytest

from labhq import doctor
from labhq.adapters import base, codex as codex_mod, get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner import codex_sandbox
from labhq.settings import Settings


def _home(tmp_path, *, state=True):
    home = tmp_path / "codex-staff"
    (home / ".sandbox").mkdir(parents=True)
    (home / ".sandbox" / "setup_marker.json").write_text("{}", encoding="utf-8")
    if state:
        (home / "auth.json").write_text("{}", encoding="utf-8")
    return home


def _runner_settings(tmp_path, home):
    exe = tmp_path / "codex.exe"
    exe.write_text("", encoding="utf-8")
    settings = Settings()
    settings.engines.codex.bin = str(exe)
    settings.engines.codex.env = {"CODEX_HOME": str(home)}
    return settings


def _ctx(tmp_path, settings, events):
    async def emit(kind, data):
        events.append((kind, data))

    agent = AgentSpec(id="engineer", name="Engineer", role="test", engine=Engine.codex, builtin_mcp=[])
    workdir = tmp_path / "workdir"
    workdir.mkdir(exist_ok=True)
    return RunContext(task=Task(agent_id=agent.id, prompt="x", request_id="req1"), agent=agent, workdir=workdir,
                      settings=settings, mcp_servers=[], env={}, emit=emit, prompt="x")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: True)
    monkeypatch.setattr(codex_sandbox, "_VERSIONS", {})


@pytest.mark.asyncio
async def test_version_recorded_once_after_successful_elevated_run(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    settings = _runner_settings(tmp_path, home)
    calls = []

    def probe(argv, env):
        calls.append(argv)
        return 0, "codex-cli 0.159.0-alpha.12.1"

    monkeypatch.setattr(codex_sandbox, "_probe", probe)
    events = []
    ctx = _ctx(tmp_path, settings, events)
    ctx.started_command, ctx.commands_ran = [settings.engines.codex.bin], True
    env = get_adapter(Engine.codex, settings).staff_env(ctx)
    watch = codex_sandbox.SandboxWatch()
    ok = TaskResult(task_id=ctx.task.id, agent_id="engineer", ok=True)

    await watch.after_run(settings, ctx, env, ok, ctx.emit)
    await watch.after_run(settings, ctx, env, ok, ctx.emit)

    record = json.loads((home / codex_sandbox.OK_FILE).read_text(encoding="utf-8"))
    assert record["codex_version"] == "0.159.0-alpha.12.1"
    assert record["bin"] == "codex.exe" and record["recorded_at"] > 0
    assert len(calls) == 1 and calls[0][1:] == ["--version"]  # read once per runner process
    assert not events


@pytest.mark.asyncio
async def test_no_record_off_windows_or_after_failure(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    settings = _runner_settings(tmp_path, home)
    monkeypatch.setattr(codex_sandbox, "_probe", lambda argv, env: (0, "codex-cli 1.0.0"))
    ctx = _ctx(tmp_path, settings, [])
    env = get_adapter(Engine.codex, settings).staff_env(ctx)
    failed = TaskResult(task_id=ctx.task.id, agent_id="engineer", ok=False, error="quota")
    await codex_sandbox.SandboxWatch().after_run(settings, ctx, env, failed, ctx.emit)
    assert not (home / codex_sandbox.OK_FILE).exists()
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: False)
    ok = TaskResult(task_id=ctx.task.id, agent_id="engineer", ok=True)
    await codex_sandbox.SandboxWatch().after_run(settings, ctx, env, ok, ctx.emit)
    assert not (home / codex_sandbox.OK_FILE).exists()


@pytest.mark.asyncio
async def test_repeated_setup_error_alerts_the_pi_once_with_the_command(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    settings = _runner_settings(tmp_path, home)
    events = []
    ctx = _ctx(tmp_path, settings, events)
    env = get_adapter(Engine.codex, settings).staff_env(ctx)
    watch = codex_sandbox.SandboxWatch()
    failed = TaskResult(task_id=ctx.task.id, agent_id="engineer", ok=False, error=codex_mod.ELEVATED_SETUP_ERROR,
                        error_kind="sandbox_setup_required")
    for _ in range(3):
        await watch.after_run(settings, ctx, env, failed, ctx.emit)

    alerts = [data for kind, data in events if kind == "agent.log" and data.get("level") == "alert"]
    notices = [data for kind, data in events if kind == "approval.requested"]
    assert len(alerts) == 1 and len(notices) == 1
    assert "Codex sandbox 다시 준비 필요" in alerts[0]["text"]
    assert notices[0]["kind"] == "codex_sandbox_setup" and notices[0]["request_id"] == "req1"
    command = notices[0]["detail"]["command"]
    assert 'windows.sandbox="elevated"' in command and "-s workspace-write" in command
    assert "$env:CODEX_HOME" in command and "$env:TEMP" in command and "Test-Path" in command
    assert not (home / codex_sandbox.OK_FILE).exists()


@pytest.mark.asyncio
async def test_sandbox_setup_required_message_is_the_setup_error(tmp_path):
    settings = Settings()
    events = []
    ctx = _ctx(tmp_path, settings, events)
    adapter = get_adapter(Engine.codex, settings)
    state = RunState()
    await adapter.handle_line(json.dumps({"type": "turn.failed", "error": {
        "message": "sandbox setup required: sandbox users missing or incompatible with marker version"}}), state, ctx)
    result = adapter.finalize(state, ctx, 1)
    assert result.error_kind == "sandbox_setup_required"
    assert codex_mod.ELEVATED_SETUP_ERROR in (result.error or "")


def _doctor_settings(tmp_path, monkeypatch, version):
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "worker.yaml").write_text("id: worker\nname: Worker\nrole: test\nengine: codex\n", encoding="utf-8")
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state"),
                                                   "workspace_root": str(tmp_path / "runs"),
                                                   "agents_dir": str(agents.parent)},
                                        "gateway": {"state_dir": str(tmp_path / "gateway")},
                                        "hpc": {"scheduler": "none"}})
    home = _home(tmp_path)
    settings.engines.codex.env = {"CODEX_HOME": str(home)}
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: "available")
    monkeypatch.setattr(doctor, "_resolve_command", lambda cmd, env, engine: cmd)
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, f"codex-cli {version}"))
    return settings, home


def _sandbox_row(settings):
    return next(r for r in doctor.collect(settings)["checks"] if r["name"] == "codex sandbox version")


def test_doctor_ok_when_recorded_version_matches(tmp_path, monkeypatch, windows):
    settings, home = _doctor_settings(tmp_path, monkeypatch, "0.159.0")
    codex_sandbox.record_ok(home, "0.159.0", "codex.exe")
    row = _sandbox_row(settings)
    assert row["status"] == "ok" and "0.159.0" in row["detail"]


def test_doctor_warns_with_command_when_version_changed(tmp_path, monkeypatch, windows):
    settings, home = _doctor_settings(tmp_path, monkeypatch, "0.159.0-alpha.12.1")
    codex_sandbox.record_ok(home, "0.158.2", "codex.exe")
    row = _sandbox_row(settings)
    assert row["status"] == "warn"
    assert row["detail"] == ("Codex가 0.158.2에서 0.159.0-alpha.12.1로 바뀜 — "
                             "무인 실행 전에 sandbox 준비를 다시 확인")
    assert 'exec --skip-git-repo-check -C $probe -s workspace-write -c \'windows.sandbox="elevated"\'' in row["hint"]
    assert "Test-Path" in row["hint"] and "$env:CODEX_HOME" in row["hint"]
    assert "fix:" in doctor.render({"checks": [row]})


def test_doctor_warns_when_nothing_recorded(tmp_path, monkeypatch, windows):
    settings, _ = _doctor_settings(tmp_path, monkeypatch, "0.159.0")
    row = _sandbox_row(settings)
    assert row["status"] == "warn" and "기록 없음" in row["detail"]
    assert 'windows.sandbox="elevated"' in row["hint"]


def test_hint_shows_app_path_generically(tmp_path):
    local = tmp_path / "Local"
    exe = local / "OpenAI" / "Codex" / "bin" / "abc123" / "codex.exe"
    hint = codex_sandbox.setup_hint(tmp_path / "home", str(exe), {"LOCALAPPDATA": str(local)})
    assert '& "$env:LOCALAPPDATA/OpenAI/Codex/bin/abc123/codex.exe" exec' in hint
    assert str(tmp_path) not in hint


def _app_folders(tmp_path, monkeypatch, versions):
    monkeypatch.setattr(base, "_is_windows", lambda: True)
    root = tmp_path / "Local" / "OpenAI" / "Codex" / "bin"
    for folder, (mtime, version) in versions.items():
        directory = root / folder
        directory.mkdir(parents=True)
        if version is not False:
            (directory / "codex.exe").touch()
        os.utime(directory, (mtime, mtime))
    monkeypatch.setattr(base, "_app_version", lambda exe: versions[exe.parent.name][1])
    return root, {"LOCALAPPDATA": str(tmp_path / "Local")}


def test_auto_bin_prefers_highest_version_and_skips_folders_without_codex(tmp_path, monkeypatch):
    root, env = _app_folders(tmp_path, monkeypatch, {
        "older-but-touched": (500, "codex-cli 0.159.0-alpha.12.1"),
        "newest": (100, "codex-cli 0.159.0"),
        "gone": (900, False),  # an update removed codex.exe from the old folder
    })
    assert base._resolve_command(["auto"], env, "codex")[0] == str(root / "newest" / "codex.exe")
    choice = base.codex_app_choice(env)
    assert choice["by"] == "version" and choice["candidates"] == 2 and choice["folder"] == "newest"


def test_auto_bin_falls_back_to_newest_folder_with_codex(tmp_path, monkeypatch):
    root, env = _app_folders(tmp_path, monkeypatch, {
        "a": (500, "codex-cli 0.160.0"),
        "b": (600, None),  # --version failed: versions are not comparable
        "gone": (900, False),
    })
    assert base._resolve_command(["auto"], env, "codex")[0] == str(root / "b" / "codex.exe")
    assert base.codex_app_choice(env)["by"] == "mtime"


def test_auto_bin_with_only_empty_folders_uses_path(tmp_path, monkeypatch):
    _, env = _app_folders(tmp_path, monkeypatch, {"gone": (900, False)})
    monkeypatch.setattr(base.shutil, "which", lambda name, **_: None)
    assert base._resolve_command(["auto"], env, "codex")[0] == "codex"


def test_version_order():
    key = base.version_key
    assert key("0.159.0-alpha.12.1") < key("0.159.0") < key("0.159.1") < key("0.160.0-alpha.1")
    assert key("0.159.0-alpha.2") < key("0.159.0-alpha.12") < key("0.159.0-beta")
    assert key("unreported") is None


def test_doctor_names_the_auto_folder(tmp_path, monkeypatch):
    root, env = _app_folders(tmp_path, monkeypatch, {"x1": (100, "codex-cli 0.158.0"), "y2": (50, "codex-cli 0.159.0")})
    monkeypatch.setenv("LOCALAPPDATA", env["LOCALAPPDATA"])
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, "codex-cli 0.159.0"))
    settings = Settings()
    settings.engines.codex.bin = "auto"
    row = next(r for r in doctor.collect(settings)["checks"] if r["group"] == "engine" and r["name"] == "codex")
    assert "auto: 앱 폴더 y2 선택" in row["detail"] and "판본이 가장 높은" in row["detail"]
    assert str(root) not in row["detail"]


@pytest.mark.asyncio
async def test_runner_records_and_alerts_through_its_task_results(tmp_path, monkeypatch, windows):
    from labhq.runner.daemon import Runner

    home = _home(tmp_path)
    settings = _runner_settings(tmp_path, home)
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    monkeypatch.setattr(codex_sandbox, "_probe", lambda argv, env: (0, "codex-cli 0.159.2"))
    runner = Runner(settings)
    agent = AgentSpec(id="engineer", name="Engineer", role="test", engine=Engine.codex, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    sent = []
    monkeypatch.setattr(runner, "send", lambda message: sent.append(message))
    outcomes = [TaskResult(task_id="", agent_id="engineer", ok=False, error=codex_mod.ELEVATED_SETUP_ERROR,
                           error_kind="sandbox_setup_required")] * 2 + [
               TaskResult(task_id="", agent_id="engineer", ok=True, text="done")]
    real = get_adapter(Engine.codex, settings)

    class Adapter:
        async def run(self, ctx):
            result = outcomes.pop(0)
            if result.ok:  # what run() reports when the sandbox started a command
                ctx.started_command, ctx.commands_ran = [settings.engines.codex.bin], True
            return result.model_copy(update={"task_id": ctx.task.id})

        def staff_env(self, ctx):
            return real.staff_env(ctx)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    try:
        for _ in range(3):
            await runner.run_task(Task(agent_id="engineer", prompt="x"))
    finally:
        runner.store.close()
    assert sum(m.get("type") == "approval.requested" for m in sent) == 1
    assert sum(m.get("type") == "agent.log" and (m.get("data") or {}).get("level") == "alert" for m in sent) == 1
    assert codex_sandbox.read_ok(home)["codex_version"] == "0.159.2"


def _exe(directory, name="codex.exe"):
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.mark.asyncio
async def test_record_names_the_build_that_ran_not_a_fresh_resolution(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    settings = _runner_settings(tmp_path, home)
    old, new = _exe(tmp_path / "old"), _exe(tmp_path / "new")
    settings.engines.codex.bin = str(new)  # what `auto` resolves to after an app update during the run
    builds = {str(old): "codex-cli 0.158.2", str(new): "codex-cli 0.159.0"}
    monkeypatch.setattr(codex_sandbox, "_probe", lambda argv, env: (0, builds[argv[0]]))
    ctx = _ctx(tmp_path, settings, [])
    ctx.started_command, ctx.commands_ran = [str(old)], True
    env = get_adapter(Engine.codex, settings).staff_env(ctx)
    ok = TaskResult(task_id=ctx.task.id, agent_id="engineer", ok=True)
    await codex_sandbox.SandboxWatch().after_run(settings, ctx, env, ok, ctx.emit)
    assert codex_sandbox.read_ok(home)["codex_version"] == "0.158.2"


@pytest.mark.asyncio
async def test_ok_run_without_a_shell_command_records_nothing(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    settings = _runner_settings(tmp_path, home)
    monkeypatch.setattr(codex_sandbox, "_probe", lambda argv, env: (0, "codex-cli 0.159.0"))
    ctx = _ctx(tmp_path, settings, [])
    ctx.started_command, ctx.commands_ran = [settings.engines.codex.bin], False  # text-only / MCP-only turn
    env = get_adapter(Engine.codex, settings).staff_env(ctx)
    ok = TaskResult(task_id=ctx.task.id, agent_id="engineer", ok=True)
    await codex_sandbox.SandboxWatch().after_run(settings, ctx, env, ok, ctx.emit)
    assert not (home / codex_sandbox.OK_FILE).exists()


def _fake_codex(tmp_path, name, events):
    stream = tmp_path / f"{name}.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    script = tmp_path / f"{name}.py"
    script.write_text(f"import sys\nsys.stdout.write(open({str(stream)!r}, encoding='utf-8').read())\n",
                      encoding="utf-8")
    return script


@pytest.mark.asyncio
@pytest.mark.parametrize("command_ran", [True, False])
async def test_adapter_run_reports_its_launcher_and_whether_a_command_ran(tmp_path, command_ran):
    events = [{"type": "thread.started", "thread_id": "t1"}]
    if command_ran:
        events += [{"type": "item.started", "item": {"type": "command_execution", "command": "ls"}},
                   {"type": "item.completed", "item": {"type": "command_execution", "command": "ls",
                                                       "exit_code": 0, "aggregated_output": ""}}]
    events += [{"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
               {"type": "turn.completed", "usage": {}}]
    script = _fake_codex(tmp_path, "codex", events)
    settings = Settings()
    settings.engines.codex.bin = sys.executable
    settings.engines.codex.prefix_args = [str(script)]
    (tmp_path / "codex-home").mkdir()
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    ctx = _ctx(tmp_path, settings, [])
    result = await get_adapter(Engine.codex, settings).run(ctx)
    assert result.ok
    assert ctx.started_command == [sys.executable, str(script)]
    assert ctx.commands_ran is command_ran


async def _command_failure(tmp_path, output):
    settings = Settings()
    ctx = _ctx(tmp_path, settings, [])
    adapter = get_adapter(Engine.codex, settings)
    state = RunState()
    for event in ({"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1,
                                                      "aggregated_output": output}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "tests fail"}},
                  {"type": "turn.completed", "usage": {}}):
        await adapter.handle_line(json.dumps(event), state, ctx)
    return adapter.finalize(state, ctx, 0)


@pytest.mark.asyncio
async def test_command_output_with_the_setup_phrase_is_an_ordinary_failure(tmp_path):
    result = await _command_failure(tmp_path, "FAILED tests/test_x.py - assert 'sandbox setup required' in msg")
    assert result.ok and result.error_kind is None


@pytest.mark.asyncio
async def test_codex_spawn_error_with_the_setup_phrase_is_the_setup_error(tmp_path):
    result = await _command_failure(tmp_path, "Failed to create unified exec process: sandbox setup required: "
                                              "sandbox users missing or incompatible with marker version")
    assert not result.ok and result.error_kind == "sandbox_setup_required"


def test_hint_uses_a_placeholder_for_a_bin_outside_the_app_folder(tmp_path):
    name = "codex.exe" if os.name == "nt" else "codex"
    configured, on_path = _exe(tmp_path / "custom", name), _exe(tmp_path / "pathbin", name)
    env = {"PATH": str(on_path.parent), "LOCALAPPDATA": str(tmp_path / "Local")}
    for command in ([str(configured)], [sys.executable, str(tmp_path / "codex.js")]):
        hint = codex_sandbox.setup_hint(tmp_path / "home", command, env)
        assert "& '<engines.codex.bin>' exec" in hint and str(tmp_path) not in hint
    assert codex_sandbox.powershell_executable([str(on_path)], env) == "codex"  # PATH finds that same build
    assert codex_sandbox.powershell_executable(["codex"], env) == "codex"


def test_hint_clears_an_earlier_probe_result_first(tmp_path):
    hint = codex_sandbox.setup_hint(tmp_path / "home", None, {})
    assert hint.index("Remove-Item") < hint.index(" exec ") < hint.index("Test-Path")


# --- #382: one elevated CODEX_HOME per PC -----------------------------------------------------------------------------

def _preflight(tmp_path, home):
    settings = _runner_settings(tmp_path, home)
    adapter = get_adapter(Engine.codex, settings)
    ctx = _ctx(tmp_path, settings, [])
    return adapter.preflight_error(ctx, adapter.staff_env(ctx))


def _secrets(home, mtime):
    secrets = home / ".sandbox-secrets" / "sandbox_users.json"
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text("{}", encoding="utf-8")  # contents are never read; only the time counts
    os.utime(secrets, (mtime, mtime))
    return secrets


def test_preflight_refuses_an_empty_setup_marker(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    (home / ".sandbox" / "setup_marker.json").write_bytes(b"")
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: None)
    error = _preflight(tmp_path, home)
    assert error and "elevated sandbox setup" in error and "empty or unreadable" in error


def _accounts(changed):
    return {name: changed for name in codex_mod.SANDBOX_ACCOUNTS}


def test_preflight_refuses_a_home_whose_passwords_another_home_reset(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    _secrets(home, 1_000_000)
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: _accounts(1_000_000 + 1800.0))
    error = _preflight(tmp_path, home)
    assert error and "another Codex home" in error and "#382" in error
    assert "did not start Codex" in error


@pytest.mark.parametrize("accounts", [None, _accounts(1_000_000 - 3600.0), _accounts(1_000_000 + 2.0)])
def test_preflight_accepts_a_home_that_set_the_current_passwords(tmp_path, monkeypatch, windows, accounts):
    home = _home(tmp_path)
    _secrets(home, 1_000_000)
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: accounts)
    assert _preflight(tmp_path, home) is None


def test_preflight_refuses_when_a_sandbox_account_was_deleted(tmp_path, monkeypatch, windows):
    """A Codex reinstall removes the accounts; the next command would recreate them through setup (PR #437 review)."""
    home = _home(tmp_path)
    _secrets(home, 1_000_000)
    monkeypatch.setattr(codex_mod, "sandbox_accounts",
                        lambda: {"CodexSandboxOffline": 1_000_000 - 10.0, "CodexSandboxOnline": None})
    assert "CodexSandboxOnline no longer exists" in (_preflight(tmp_path, home) or "")
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: _accounts(None))
    assert "no longer exists" in (_preflight(tmp_path, home) or "")


def test_a_home_never_set_up_is_not_judged_by_the_accounts(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)  # marker but no .sandbox-secrets: CI runners have no sandbox accounts at all
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: _accounts(None))
    assert _preflight(tmp_path, home) is None


def test_preflight_refuses_a_secrets_folder_without_its_users_file(tmp_path, monkeypatch, windows):
    home = _home(tmp_path)
    (home / ".sandbox-secrets").mkdir()
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: None)
    assert "no sandbox_users.json" in (_preflight(tmp_path, home) or "")


def test_accounts_are_unknown_off_windows(monkeypatch):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: False)
    assert codex_mod.sandbox_accounts() is None


@pytest.mark.asyncio
async def test_first_setup_error_ends_the_cli_instead_of_waiting(tmp_path):
    """A hung UAC fallback keeps Codex alive with nothing more to say; labhq must not wait for task_timeout_s."""
    events = [{"type": "thread.started", "thread_id": "t1"},
              {"type": "item.completed", "item": {"type": "command_execution", "command": "ls", "exit_code": 1,
                                                  "aggregated_output": "Failed to create unified exec process: "
                                                  "sandbox setup required: sandbox users missing or incompatible "
                                                  "with marker version"}}]
    stream = tmp_path / "hang.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    script = tmp_path / "hang.py"
    script.write_text("import sys, time\n"
                      f"sys.stdout.write(open({str(stream)!r}, encoding='utf-8').read()); sys.stdout.flush()\n"
                      "time.sleep(120)\n", encoding="utf-8")
    settings = Settings()
    settings.engines.codex.bin = sys.executable
    settings.engines.codex.prefix_args = [str(script)]
    settings.runner.task_timeout_s = 300
    (tmp_path / "codex-home").mkdir()
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    log = []
    ctx = _ctx(tmp_path, settings, log)
    started = time.monotonic()
    result = await get_adapter(Engine.codex, settings).run(ctx)
    assert time.monotonic() - started < 60
    assert not result.ok and result.error_kind == "sandbox_setup_required"
    assert codex_mod.ELEVATED_SETUP_ERROR in (result.error or "")
    warns = [d["text"] for k, d in log if k == "agent.log" and d.get("level") == "warn"]
    assert sum("#382" in text for text in warns) == 1


@pytest.mark.parametrize("text, value", [
    ('[windows]\nsandbox = "elevated"\n', "elevated"),
    ("[windows]\nsandbox = 'elevated'  # app default\n", "elevated"),
    ('model = "x"\nwindows.sandbox = "elevated"\n', "elevated"),
    ('[windows]\nsandbox = "unelevated"\n', "unelevated"),
    ('[projects.a]\nsandbox = "elevated"\n[windows]\nsandbox = "unelevated"\n', "unelevated"),
    ('[windows]\n# sandbox = "elevated"\n', codex_sandbox.UNSET),
    ('[[mcp]]\nsandbox = "elevated"\n', codex_sandbox.UNSET),
    ('model = "x"\n', codex_sandbox.UNSET),
])
def test_config_windows_sandbox_reads_only_that_key(tmp_path, text, value):
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    assert codex_sandbox.config_windows_sandbox(config) == value
    assert codex_sandbox.config_windows_sandbox(tmp_path / "missing.toml") is None


def test_doctor_names_the_other_elevated_home_when_the_staff_home_is_stale(tmp_path, monkeypatch, windows):
    """The stale staff home is what the other elevated home causes, so the warning must not hide behind the fail."""
    settings, home = _doctor_settings(tmp_path, monkeypatch, "0.159.0")
    _secrets(home, 1_000_000)
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: _accounts(1_000_000 + 1800.0))
    own = tmp_path / "own-codex"
    own.mkdir()
    (own / "config.toml").write_text('[windows]\nsandbox = "elevated"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(own))
    checks = doctor.collect(settings)["checks"]
    staff = next(r for r in checks if r["group"] == "staff" and r["name"] == "worker")
    assert staff["status"] == "fail" and "another Codex home" in staff["detail"] and "#382" in staff["hint"]
    assert [r["status"] for r in checks if r["name"] == "codex sandbox homes"] == ["warn"]


def test_doctor_warns_when_the_runner_accounts_own_codex_home_is_also_elevated(tmp_path, monkeypatch, windows):
    settings, home = _doctor_settings(tmp_path, monkeypatch, "0.159.0")
    own = tmp_path / "own-codex"
    own.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(own))
    monkeypatch.setattr(codex_mod, "sandbox_accounts", lambda: None)
    (own / "config.toml").write_text('[windows]\nsandbox = "elevated"\n', encoding="utf-8")
    rows = [r for r in doctor.collect(settings)["checks"] if r["name"] == "codex sandbox homes"]
    assert len(rows) == 1 and rows[0]["status"] == "warn" and "#382" in rows[0]["detail"]
    assert '"unelevated"' in rows[0]["hint"] and str(tmp_path) not in json.dumps(rows[0])

    (own / "config.toml").write_text('model = "x"\n', encoding="utf-8")  # no key: the app sets elevated again
    rows = [r for r in doctor.collect(settings)["checks"] if r["name"] == "codex sandbox homes"]
    assert len(rows) == 1 and "키가 없음" in rows[0]["detail"]
    (own / "config.toml").write_text('[windows]\nsandbox = "unelevated"\n', encoding="utf-8")
    assert not [r for r in doctor.collect(settings)["checks"] if r["name"] == "codex sandbox homes"]
    (own / "config.toml").unlink()  # no Codex config at all: nothing to warn about
    assert not [r for r in doctor.collect(settings)["checks"] if r["name"] == "codex sandbox homes"]
    monkeypatch.setenv("CODEX_HOME", str(home))  # the staff home itself is not "another" home
    (home / "config.toml").write_text('[windows]\nsandbox = "elevated"\n', encoding="utf-8")
    assert not [r for r in doctor.collect(settings)["checks"] if r["name"] == "codex sandbox homes"]


def test_setup_hint_warns_that_only_one_home_may_be_elevated(tmp_path):
    hint = codex_sandbox.setup_hint(tmp_path / "home", None, {})
    assert hint.startswith(codex_sandbox.ONE_HOME_NOTE) and "#382" in hint
    assert hint.index("#382") < hint.index(" exec ")
