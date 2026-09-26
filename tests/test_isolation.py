"""P1+ ④: staff CLIs must not inherit the PI's own CLI config (hooks, skills, plugins, global instructions)."""

import json

import pytest

from labhq.adapters import codex as codex_mod
from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings


async def _emit(kind, data):
    pass


def _command(engine, tmp_path, settings=None, env=None, claude_settings=None):
    settings = settings or Settings()
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine(engine), builtin_mcp=[])
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env=env or {}, emit=_emit, prompt="x",
                     claude_settings=claude_settings or {})
    return get_adapter(agent.engine, settings).build_command(ctx)


def _settings_arg(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])


def test_claude_isolation_flags_and_claude_md_exclude(tmp_path):
    cfg = tmp_path / "claude-home"
    cmd = _command("claude_code", tmp_path, env={"CLAUDE_CONFIG_DIR": str(cfg)})
    i = cmd.index("--setting-sources")
    assert cmd[i + 1] == "project,local" and "--disable-slash-commands" in cmd
    s = _settings_arg(cmd)
    assert s["autoMemoryEnabled"] is False
    assert (cfg / "CLAUDE.md").as_posix() in s["claudeMdExcludes"]


def test_claude_isolation_keeps_policy_deny_rules(tmp_path):
    deny = {"permissions": {"deny": ["Read(//data/cohort/**)"]}}
    s = _settings_arg(_command("claude_code", tmp_path, claude_settings=deny))
    assert s["permissions"] == deny["permissions"] and "claudeMdExcludes" in s


def test_claude_isolation_can_be_turned_off(tmp_path):
    settings = Settings()
    settings.engines.claude_code.isolate_user_config = False
    cmd = _command("claude_code", tmp_path, settings=settings)
    assert "--setting-sources" not in cmd and "--disable-slash-commands" not in cmd and "--settings" not in cmd


@pytest.mark.parametrize("windows", [True, False])
def test_codex_ignores_user_config_and_restores_windows_sandbox(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: windows)
    cmd = _command("codex", tmp_path)
    assert "--ignore-user-config" in cmd and "--ignore-rules" in cmd
    assert ('windows.sandbox="elevated"' in cmd) is windows
    assert cmd.index("--ignore-user-config") < len(cmd) - 1  # flags stay before the prompt


def test_codex_isolation_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: True)
    settings = Settings()
    settings.engines.codex.isolate_user_config = False
    cmd = _command("codex", tmp_path, settings=settings)
    assert "--ignore-user-config" not in cmd and 'windows.sandbox="elevated"' not in cmd


def test_engine_env_passes_through(tmp_path):
    settings = Settings()
    settings.engines.codex.env = {"CODEX_HOME": "/srv/labhq/codex-home"}
    adapter = get_adapter(Engine("codex"), settings)
    assert adapter.engine_env() == {"CODEX_HOME": "/srv/labhq/codex-home"}
    gem = get_adapter(Engine("gemini"), settings).engine_env()
    assert gem["GEMINI_CLI_TRUST_WORKSPACE"] == "true"
