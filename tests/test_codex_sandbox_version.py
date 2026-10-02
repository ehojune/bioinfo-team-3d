"""Staff elevated sandbox setup vs. the Codex version in use (#328). Fakes only: no real Codex, no UAC."""
import json
import os

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
            return outcomes.pop(0).model_copy(update={"task_id": ctx.task.id})

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
