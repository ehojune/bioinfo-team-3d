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


@pytest.mark.asyncio
@pytest.mark.parametrize("files,allow,refused", [
    (["AGENTS.md"], False, True),
    (["AGENTS.override.md"], False, True),
    (["AGENTS.md"], True, False),
    ([], False, False),
])
async def test_codex_refuses_when_global_agents_md_would_load(tmp_path, files, allow, refused):
    home = tmp_path / "codex-home"
    home.mkdir()
    for name in files:
        (home / name).write_text("PI global instructions")
    settings = Settings()
    settings.engines.codex.bin = str(tmp_path / "no-such-codex")
    settings.engines.codex.env = {"CODEX_HOME": str(home)}
    settings.engines.codex.allow_global_agents_md = allow
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine("codex"), builtin_mcp=[])
    wd = tmp_path / "wd"
    wd.mkdir()
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=wd, settings=settings,
                     mcp_servers=[], env={}, emit=_emit, prompt="x")
    res = await get_adapter(agent.engine, settings).run(ctx)
    assert not res.ok
    # Refusal happens before spawning; otherwise the missing binary is what fails.
    assert ("refused" in res.error) is refused and ("executable not found" in res.error) is (not refused)
    if refused:
        assert str(home) not in res.error and not any(wd.iterdir())


@pytest.mark.asyncio
async def test_runtime_codex_home_override_is_what_preflight_checks(tmp_path):
    """ctx.env wins over engines.codex.env for the subprocess, so preflight must check that one."""
    clean, polluted = tmp_path / "clean", tmp_path / "polluted"
    clean.mkdir()
    polluted.mkdir()
    (polluted / "AGENTS.md").write_text("PI global instructions")
    settings = Settings()
    settings.engines.codex.bin = str(tmp_path / "no-such-codex")
    settings.engines.codex.env = {"CODEX_HOME": str(clean)}
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine("codex"), builtin_mcp=[])
    wd = tmp_path / "wd"
    wd.mkdir()
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=wd, settings=settings,
                     mcp_servers=[], env={"CODEX_HOME": str(polluted)}, emit=_emit, prompt="x")
    res = await get_adapter(agent.engine, settings).run(ctx)
    assert not res.ok and "refused" in res.error


@pytest.mark.asyncio
async def test_env_added_in_prepare_reaches_subprocess(tmp_path, monkeypatch):
    """engine: cli adds CliSpec.env to ctx.env in prepare(); the subprocess env must be built after it."""
    import asyncio

    from labhq.models import CliSpec

    seen = {}

    async def fake_exec(*cmd, env=None, **kw):
        seen.update(env or {})
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine("cli"), builtin_mcp=[],
                      cli=CliSpec(command=["agent"], output="jsonl", env={"BIOINFO_AGENT_KEY": "from-cli-spec"}))
    wd = tmp_path / "wd"
    wd.mkdir()
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=wd, settings=settings,
                     mcp_servers=[], env={}, emit=_emit, prompt="x")
    await get_adapter(agent.engine, settings).run(ctx)
    assert seen.get("BIOINFO_AGENT_KEY") == "from-cli-spec"


def test_isolation_option_exists_only_where_implemented():
    """gemini/antigravity cannot drop user settings; the option must not appear to work there."""
    from pydantic import ValidationError

    from labhq.settings import EnginesSettings

    s = Settings()
    assert s.engines.claude_code.isolate_user_config and s.engines.codex.isolate_user_config
    assert not hasattr(s.engines.antigravity, "isolate_user_config")
    assert not hasattr(s.engines.gemini, "isolate_user_config")
    with pytest.raises(ValidationError):
        EnginesSettings.model_validate({"antigravity": {"bin": "agy", "isolate_user_config": True}})


def test_probe_script_applies_the_same_preflight(tmp_path, monkeypatch):
    import scripts.probe_engines as probe

    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "AGENTS.md").write_text("PI global instructions")
    monkeypatch.setenv("CODEX_HOME", str(home))

    def no_spawn(*a, **k):
        raise AssertionError("probe spawned the CLI despite the preflight refusal")

    monkeypatch.setattr(probe.subprocess, "run", no_spawn)
    monkeypatch.setattr("sys.argv", ["probe_engines.py", "codex", "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc:
        probe.main()
    assert exc.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("polluted_var,refused", [
    ("HOME", True),          # HOME-only override; USERPROFILE still points elsewhere (Windows case)
    ("USERPROFILE", True),   # the reverse
    (None, False),           # both candidates clean
    ("relative_codex_home", True),
])
async def test_codex_preflight_checks_every_home_the_child_might_use(tmp_path, polluted_var, refused):
    """CLIs resolve ~ from HOME or USERPROFILE depending on CLI and OS; preflight fails closed on any candidate."""
    wd = tmp_path / "wd"
    wd.mkdir()
    clean, polluted = tmp_path / "clean-home", tmp_path / "polluted-home"
    for h in (clean, polluted):
        (h / ".codex").mkdir(parents=True)
    (polluted / ".codex" / "AGENTS.md").write_text("instructions")
    env = {"HOME": str(clean), "USERPROFILE": str(clean), "CODEX_HOME": ""}
    if polluted_var in ("HOME", "USERPROFILE"):
        env[polluted_var] = str(polluted)
    elif polluted_var == "relative_codex_home":
        (wd / "chome").mkdir()
        (wd / "chome" / "AGENTS.md").write_text("instructions")
        env["CODEX_HOME"] = "chome"
    settings = Settings()
    settings.engines.codex.bin = str(tmp_path / "no-such-codex")
    settings.engines.codex.env = env
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine("codex"), builtin_mcp=[])
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=wd, settings=settings,
                     mcp_servers=[], env={}, emit=_emit, prompt="x")
    res = await get_adapter(agent.engine, settings).run(ctx)
    assert not res.ok
    assert ("refused" in res.error) is refused and ("executable not found" in res.error) is (not refused)


def test_claude_md_excludes_cover_every_home_candidate(tmp_path):
    a, b = tmp_path / "home-a", tmp_path / "home-b"
    s = _settings_arg(_command("claude_code", tmp_path, env={"HOME": str(a), "USERPROFILE": str(b)}))
    for h in (a, b):
        assert (h / ".claude" / "CLAUDE.md").as_posix() in s["claudeMdExcludes"]


def test_probe_script_reads_labhq_config(tmp_path, monkeypatch):
    """engines.codex.env from LABHQ_CONFIG must reach the probe's preflight, as it does for the runner."""
    import scripts.probe_engines as probe

    clean, polluted = tmp_path / "clean", tmp_path / "polluted"
    clean.mkdir()
    polluted.mkdir()
    (polluted / "AGENTS.md").write_text("PI global instructions")
    cfg = tmp_path / "labhq.yaml"
    cfg.write_text(f"engines:\n  codex:\n    bin: codex\n    env: {{CODEX_HOME: '{polluted.as_posix()}'}}\n",
                   encoding="utf-8")
    monkeypatch.setenv("LABHQ_CONFIG", str(cfg))
    monkeypatch.setenv("CODEX_HOME", str(clean))  # without the config the probe would pass and spawn

    def no_spawn(*a, **k):
        raise AssertionError("probe ignored LABHQ_CONFIG")

    monkeypatch.setattr(probe.subprocess, "run", no_spawn)
    monkeypatch.setattr("sys.argv", ["probe_engines.py", "codex", "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc:
        probe.main()
    assert exc.value.code == 2


def test_claude_md_excludes_cover_workspace_ancestors(tmp_path):
    """Claude walks up from its cwd loading CLAUDE.md; a canary in an ancestor reached an isolated 2.1.282 session."""
    wd = tmp_path / "anc" / "runs" / "task"
    wd.mkdir(parents=True)
    ex = _settings_arg(_command("claude_code", wd))["claudeMdExcludes"]
    for anc in wd.resolve().parents:
        assert (anc / "CLAUDE.md").as_posix() in ex and (anc / ".claude" / "CLAUDE.md").as_posix() in ex
    assert (wd.resolve() / "CLAUDE.md").as_posix() not in ex  # the workspace itself is not personal config


@pytest.mark.parametrize("name", ["../escape", "sub/dir", "..", "C:/Users/x/probe"])
def test_probe_name_cannot_leave_the_output_dir(tmp_path, monkeypatch, name):
    """An unredacted raw stream must never land outside --output-dir (e.g. inside the public repository)."""
    import scripts.probe_engines as probe

    def no_spawn(*a, **k):
        raise AssertionError("probe spawned the CLI with an unsafe --name")

    monkeypatch.setattr(probe.subprocess, "run", no_spawn)
    monkeypatch.setattr("sys.argv", ["probe_engines.py", "codex", "--output-dir", str(tmp_path / "out"), "--name", name])
    with pytest.raises(SystemExit) as exc:
        probe.main()
    assert exc.value.code == 2


def test_codex_turns_on_web_search_only_for_web_staff(tmp_path):
    assert 'web_search="live"' not in _command("codex", tmp_path)
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine.codex, builtin_mcp=[], tools=["WebSearch"])
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=tmp_path, settings=Settings(),
                     mcp_servers=[], env={}, emit=_emit, prompt="x", claude_settings={})
    cmd = get_adapter(agent.engine, Settings()).build_command(ctx)
    assert cmd[cmd.index('web_search="live"') - 1] == "-c" and cmd.index('web_search="live"') < len(cmd) - 1


def test_literature_scout_runs_on_codex_with_web_search(tmp_path):
    from pathlib import Path
    from labhq.registry import Registry
    reg = Registry(Path(__file__).resolve().parents[1] / "agents", tmp_path)
    reg.load()
    scout = reg.agents["lit_scout"]
    assert scout.engine == Engine.codex and scout.model == "gpt-6-luna" and "WebSearch" in scout.tools
