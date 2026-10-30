import asyncio
import json
from pathlib import Path

import pytest

from labhq import cli, wsl_setup
from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from test_wsl_setup import settings, health


READY = dict(bioinfo_agent=True, bioinfo_skill=True, java=True, nextflow=True,
             claude_cli=True, claude_login=True)


@pytest.mark.parametrize("missing", list(READY))
def test_incomplete_probe_never_enables_and_routes_native(settings, monkeypatch, missing):
    facts = {**READY, missing: False}
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_shared_settings", lambda s: {})
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: {"ok": True, "url": "ws://host", **facts})
    monkeypatch.setattr(wsl_setup, "control", lambda *a: None)
    monkeypatch.setattr(cli, "_health", lambda s: health(s))
    before = Path(settings.config_path).read_bytes()
    wsl_setup.setup(settings)
    assert Path(settings.config_path).read_bytes() == before
    assert not settings.runner.wsl.enabled
    settings.runner.wsl.enabled = True  # an old enabled flag must not override current facts
    hub = Hub(settings)
    staff = [{"id": "bioinfo-agent"}]
    hub.register_runner("native", object(), staff, capabilities={"runtime_env": "windows"})
    hub.register_runner("wsl", object(), staff,
                        capabilities={"runtime_env": "wsl", "wsl_readiness": facts})
    assert hub.agent_runner["bioinfo-agent"] == "native"
    hub.store.close()


def test_check_and_activation_use_same_readiness(settings, monkeypatch, capsys):
    from labhq import wsl_readiness

    calls = []
    def refuse(facts):
        calls.append(facts)
        return False
    monkeypatch.setattr(wsl_readiness, "wsl_readiness", refuse)
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_shared_settings", lambda s: {})
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: {"ok": True, **READY})
    monkeypatch.setattr(cli, "_health", lambda s: health(s))
    assert any(r["name"] == "WSL 분석 준비" and r["status"] == "warn" for r in wsl_setup.check(settings))
    before = len(calls)
    with pytest.raises(SystemExit) as checked:
        cli.main(["-c", settings.config_path, "setup-wsl", "--check"])
    assert checked.value.code == 1 and len(calls) > before
    assert "WSL 분석 준비: 없음" in capsys.readouterr().out
    before = len(calls)
    with pytest.raises(SystemExit) as activated:
        cli.main(["-c", settings.config_path, "setup-wsl"])
    assert activated.value.code == 1
    assert len(calls) > before and not settings.runner.wsl.enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["target", "incarnation", "same"])
async def test_uncertain_delivery_stays_on_original_runner(settings, changed):
    # Ownership now stays with the disconnected runner; bound its reconnect wait in this test.
    settings.orchestrator.runner_reconnect_timeout_s = 0.01
    hub = Hub(settings)
    staff = [{"id": "bioinfo-agent"}]
    sent = []
    class Socket:
        def __init__(self, name, broken=False):
            self.name, self.broken = name, broken
        async def send_text(self, payload):
            message = json.loads(payload)
            if message["type"] != "task.dispatch":
                return
            sent.append(self.name)
            if self.broken:
                raise RuntimeError("frame delivered, connection lost")
            task = message["task"]
            hub.futures[task["id"]].set_result(TaskResult(task_id=task["id"], agent_id=task["agent_id"], ok=True))
    caps = {"runtime_env": "wsl", "wsl_readiness": READY}
    hub.register_runner("native", Socket("native"), staff, incarnation="native-1",
                        capabilities={"runtime_env": "windows"})
    hub.register_runner("wsl", Socket("wsl", broken=True), staff, incarnation="wsl-1", capabilities=caps)
    if changed != "target":
        async def reconnect(*args):
            hub.register_runner("wsl", Socket("reconnected"), staff,
                                incarnation="new" if changed == "incarnation" else "wsl-1", capabilities=caps)
            return True
        hub.wait_agent_online = reconnect
    task = Task(agent_id="bioinfo-agent", prompt="analyze")
    result = await asyncio.wait_for(hub.dispatch(task), 2)
    if changed == "same":
        assert result.ok and sent == ["wsl", "reconnected"]
    else:
        assert sent == ["wsl"]
        assert not result.ok and any(word in result.error for word in ("uncertain", "outcome unknown"))
        assert not result.cost_known
        if changed == "target":
            assert task.id not in hub.futures and task.id not in hub.task_runner
        assert hub.store.get("task", task.id)["runner_id"] == "wsl"
        if changed == "target":
            assert hub.agent_runner["bioinfo-agent"] == "wsl"
            assert not hub.store.get("task", task.id).get("completed")
    hub.store.close()


@pytest.mark.parametrize("missing", list(READY))
def test_ready_runner_loses_precedence_when_facts_change(settings, missing):
    hub = Hub(settings)
    staff = [{"id": "bioinfo-agent"}]
    hub.register_runner("native", object(), staff, capabilities={"runtime_env": "windows"})
    hub.register_runner("wsl", object(), staff, capabilities={"runtime_env": "wsl", "wsl_readiness": READY})
    assert hub.agent_runner["bioinfo-agent"] == "wsl"
    hub.set_roster("wsl", staff, {"runtime_env": "wsl", "wsl_readiness": {**READY, missing: False}})
    assert hub.agent_runner["bioinfo-agent"] == "native"
    hub.store.close()


@pytest.mark.parametrize("missing", [None, "bioinfo_agent", "bioinfo_skill", "claude_cli", "claude_login", "java", "nextflow"])
def test_linux_probe_checks_plugin_tools_and_staff_login(tmp_path, monkeypatch, missing):
    import subprocess
    from labhq import wsl_readiness

    plugin = tmp_path / "plugin"
    manifest = plugin / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    if missing != "bioinfo_agent":
        manifest.write_text('{"name": "bioinfo"}', encoding="utf-8")
    skill = plugin / "skills/bioinfo-analyze/SKILL.md"
    skill.parent.mkdir(parents=True)
    if missing != "bioinfo_skill":
        skill.write_text("analyze", encoding="utf-8")
    monkeypatch.setattr(wsl_readiness.shutil, "which", lambda *a, **kw: None if missing == "claude_cli" else "/opt/bin/claude")
    env = {"PATH": "/opt/bin", "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-staff")}
    calls = []
    def run(args, **kwargs):
        assert kwargs["env"] == env and kwargs["stdin"] == subprocess.DEVNULL
        calls.append(args)
        output = json.dumps({"loggedIn": missing != "claude_login"}).encode()
        return subprocess.CompletedProcess(args, int(args[0] == missing), output)
    monkeypatch.setattr(wsl_readiness.subprocess, "run", run)
    facts = wsl_readiness.probe([str(plugin)], env)
    assert wsl_readiness.wsl_readiness(facts) == (missing is None)
    if missing:
        assert facts[missing] is False
    if missing == "claude_cli":
        assert not any("auth" in args for args in calls)


def test_missing_cli_prints_install_hint(settings, monkeypatch):
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: {**READY, "claude_cli": False})
    monkeypatch.setattr(cli, "_health", lambda s: None)
    text = wsl_setup.render(wsl_setup.check(settings))
    assert "WSL Claude CLI: 없음" in text and "Linux용 Claude Code CLI를 설치" in text


def test_runner_publishes_readiness_for_its_actual_staff_config(settings, monkeypatch):
    from labhq import wsl_readiness
    from labhq.models import AgentSpec
    from labhq.runner.daemon import Runner

    settings.runner.environment = "wsl"
    settings.runner.workspace_root = "/mnt/c/Shared/Runs"
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": "staff-test-home"}
    runner = Runner(settings)
    runner.registry.agents["bioinfo-agent"] = AgentSpec(id="bioinfo-agent", name="Bio", role="analysis",
                                                       engine="claude_code", plugin_dirs=["plugin"])
    def probe(dirs, env, cli):
        assert dirs == [str(runner.ws_root / "plugin")]
        assert env["CLAUDE_CONFIG_DIR"] == "staff-test-home" and cli == "claude"
        return READY
    monkeypatch.setattr(wsl_readiness, "probe", probe)
    runner._refresh_wsl_readiness()
    assert runner.capabilities()["wsl_readiness"] == READY
    runner.store.close()


def test_bootstrap_uses_same_probe_and_private_staff_home(tmp_path, monkeypatch):
    from labhq import wsl_bootstrap, wsl_readiness
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PATH", "test-path")
    monkeypatch.setenv("PYTHONUTF8", "1")
    root = tmp_path / "wsl"
    def probe(dirs, env):
        assert dirs == ["/opt/bioinfo-agent"]
        assert env["CLAUDE_CONFIG_DIR"] == str(root / "claude-staff")
        return READY
    monkeypatch.setattr(wsl_readiness, "probe", probe)
    import subprocess
    monkeypatch.setattr(wsl_bootstrap, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, b""))
    import os
    previous = os.umask(0o077)
    try:
        facts = wsl_bootstrap.main({"action": "probe", "root": str(root), "id": "wsl",
                                    "bioinfo_agent": "/opt/bioinfo-agent"})
    finally:
        os.umask(previous)
    assert wsl_readiness.wsl_readiness(facts)


def test_setup_hands_the_model_tier_table_to_the_wsl_runner(settings, monkeypatch):
    """#625: a model-less agent (bioinfo-agent) must run the tier's model on the WSL runner as on the Windows one."""
    monkeypatch.setattr(wsl_setup, "_shared_settings", lambda s: {"workspace_root": "/mnt/c/Shared/Runs",
                                                               "policy": s.policy.model_dump()})
    monkeypatch.setattr(wsl_setup, "distributions", lambda: ["Ubuntu-22.04"])
    monkeypatch.setattr(cli, "_health", lambda s: health(s))
    settings.model_tiers.fast["claude_code"] = "haiku"
    calls = []

    def remote(s, action, **kwargs):
        calls.append((action, kwargs))
        return {"ok": True, **READY, "url": "ws://192.0.2.1:43123"}

    monkeypatch.setattr(wsl_setup, "_remote", remote)
    wsl_setup.setup(settings)
    configure = [kwargs for action, kwargs in calls if action == "configure"]
    assert len(configure) == 1 and configure[0]["model_tiers"]["fast"] == {"claude_code": "haiku"}


def test_bootstrap_writes_the_model_tier_table_into_the_wsl_config(settings, tmp_path, monkeypatch):
    from labhq import wsl_bootstrap
    from labhq.settings import Settings

    root, source = tmp_path / "wsl", tmp_path / "checkout"
    root.mkdir()
    (source / "agents/core").mkdir(parents=True)
    (source / "agents/core/bioinfo-agent.yaml").write_text(
        "id: bioinfo-agent" + chr(10) + "name: Bioinfo" + chr(10) + "role: pipeline" + chr(10) + "engine: mock" + chr(10))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(wsl_bootstrap.sys, "prefix", str(root / "venv"))
    data = {"action": "configure", "root": str(root), "source": str(source), "id": "lab-wsl",
            "url": "ws://192.0.2.1:43123", "token": settings.gateway.runner_token,
            "bioinfo_agent": "/opt/bioinfo-agent", "supervisor": {"restart": True},
            "workspace_root": "/mnt/c/Shared/Runs", "policy": settings.policy.model_dump(),
            "model_tiers": {"fast": {"claude_code": "haiku"}, "balanced": {}, "reasoning": {}}}
    assert wsl_bootstrap.main(data)["ok"]
    assert Settings.load(str(root / "labhq.yaml")).model_tiers.model_for("fast", "claude_code") == "haiku"
    older = {key: value for key, value in data.items() if key != "model_tiers"}  # a Windows side that sends none
    assert wsl_bootstrap.main(older)["ok"]
    assert Settings.load(str(root / "labhq.yaml")).model_tiers.model_for("fast", "claude_code") is None
