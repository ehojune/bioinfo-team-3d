"""#382: PI personal paths reach the Codex staff sandbox as deny-read, not only as an instruction. Command building
only; the live check (a canary under a denied path is unreadable) is in the PR report."""
import json
import sys

import pytest

from labhq.adapters import codex as codex_mod, get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings


def _cmd(tmp_path, *, private=("C:/Users/pi/.ssh", "C:/Users/pi/.claude"), sandbox="workspace-write",
         read_only=False, windows_sandbox="elevated", isolate=True):
    settings = Settings()
    settings.engines.codex.windows_sandbox = windows_sandbox
    settings.engines.codex.isolate_user_config = isolate
    agent = AgentSpec(id="w", name="W", role="test", engine=Engine.codex, builtin_mcp=[], sandbox=sandbox)
    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)

    async def emit(kind, data):
        pass

    ctx = RunContext(task=Task(agent_id="w", prompt="x"), agent=agent, workdir=workdir, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="x", read_only=read_only,
                     private_paths=list(private), private_enabled=True)
    return get_adapter(Engine.codex, settings).build_command(ctx)


def _profile(cmd):
    values = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-c"]
    default = [v for v in values if v.startswith("default_permissions=")]
    profile = [v for v in values if v.startswith("permissions.labhq_staff=")]
    return default, profile


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: True)


@pytest.mark.parametrize("sandbox, read_only, parent", [("workspace-write", False, ":workspace"),
                                                        ("read-only", True, ":read-only")])
def test_elevated_windows_run_denies_private_paths_through_a_profile(tmp_path, windows, sandbox, read_only, parent):
    cmd = _cmd(tmp_path, sandbox=sandbox, read_only=read_only)
    default, profile = _profile(cmd)
    assert "-s" not in cmd, "a permission profile and -s do not compose"
    assert default == ['default_permissions="labhq_staff"']
    value = profile[0].split("=", 1)[1]
    assert value.startswith('{extends = "' + parent + '", filesystem = {')
    assert '"C:/Users/pi/.ssh" = "deny"' in value and '"C:/Users/pi/.claude" = "deny"' in value
    if read_only:
        for name in ("outputs", ".tmp"):
            assert json.dumps(str((tmp_path / "wd" / name).resolve())) + ' = "write"' in value
        assert value.count('= "write"') == 2
    else:
        assert value.count('= "write"') == 0
    if sys.version_info >= (3, 11):
        import tomllib
        assert tomllib.loads("p = " + value)["p"]["filesystem"]["C:/Users/pi/.claude"] == "deny"


def test_windows_backslash_paths_stay_valid_toml(tmp_path, windows):
    cmd = _cmd(tmp_path, private=[r"C:\Users\pi\.ssh"])
    value = _profile(cmd)[1][0].split("=", 1)[1]
    assert r'"C:\\Users\\pi\\.ssh" = "deny"' in value
    if sys.version_info >= (3, 11):
        import tomllib
        assert list(tomllib.loads("p = " + value)["p"]["filesystem"]) == [r"C:\Users\pi\.ssh"]


@pytest.mark.parametrize("kwargs", [
    {"private": ()},                                  # nothing to deny
    {"windows_sandbox": "unelevated"},                # Codex refuses deny-read there and would not start
    {"isolate": False},                               # the PI's own config.toml decides the Windows sandbox
    {"sandbox": "danger-full-access"},                # no built-in profile to extend
])
def test_other_runs_keep_the_plain_sandbox_flag(tmp_path, windows, kwargs):
    cmd = _cmd(tmp_path, **kwargs)
    assert "-s" in cmd and _profile(cmd) == ([], [])


def test_off_windows_the_instruction_stays_the_only_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: False)
    cmd = _cmd(tmp_path)
    assert cmd[cmd.index("-s") + 1] == "workspace-write" and _profile(cmd) == ([], [])


def test_duplicate_spellings_are_listed_once(tmp_path, windows):
    cmd = _cmd(tmp_path, private=["C:/Users/pi/.ssh", "C:/Users/pi/.ssh"])
    assert _profile(cmd)[1][0].count(".ssh") == 1


def test_mcp_env_names_stay_bare_keys():
    assert codex_mod._toml({"LABHQ_BROKER_URL": "u"}) == '{LABHQ_BROKER_URL = "u"}'
    assert codex_mod._toml({"a.b": "x"}) == '{"a.b" = "x"}'


def test_new_default_private_entries():
    from labhq.private_paths import DEFAULT_HOME_ENTRIES
    assert {".gemini", ".env"} <= set(DEFAULT_HOME_ENTRIES)


# ---- #1055: a read-only reviewer's shell reached public sites through the elevated sandbox, uncounted ----
def _shell_policy(tmp_path, *, tools=(), sandbox="read-only", read_only=False):
    settings = Settings()
    agent = AgentSpec(id="w", name="W", role="test", engine=Engine.codex, builtin_mcp=[], sandbox=sandbox,
                      tools=list(tools))
    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)

    async def emit(kind, data):
        pass

    ctx = RunContext(task=Task(agent_id="w", prompt="x"), agent=agent, workdir=workdir, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="x", read_only=read_only)
    cmd = get_adapter(Engine.codex, settings).build_command(ctx)
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-c" and cmd[i + 1].startswith("shell_environment_policy.")]


def test_a_read_only_shell_gets_a_closed_proxy_so_default_http_clients_fail(tmp_path):
    [setting] = _shell_policy(tmp_path)
    value = setting.split("=", 1)[1]
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert f'{name} = "http://127.0.0.1:9"' in value
    assert 'NO_PROXY = ""' in value and 'no_proxy = ""' in value, "no host may be exempted from the closed proxy"
    if sys.version_info >= (3, 11):
        import tomllib
        assert tomllib.loads("p = " + value)["p"]["HTTPS_PROXY"] == "http://127.0.0.1:9"


def test_the_proxy_is_set_for_the_shell_only_not_for_the_codex_process(tmp_path):
    settings = Settings()
    agent = AgentSpec(id="w", name="W", role="test", engine=Engine.codex, builtin_mcp=[], sandbox="read-only")

    async def emit(kind, data):
        pass

    ctx = RunContext(task=Task(agent_id="w", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=emit, prompt="x", read_only=True)
    env = get_adapter(Engine.codex, settings).staff_env(ctx)
    assert not any("PROXY" in name.upper() for name in env), "the model connection must not go through the closed proxy"


@pytest.mark.parametrize("kwargs", [
    {"sandbox": "workspace-write"},                   # engineers install packages and pull references
    {"tools": ["WebSearch"]},                         # the agent was given the web on purpose
])
def test_agents_that_may_use_the_network_keep_it(tmp_path, kwargs):
    assert _shell_policy(tmp_path, **kwargs) == []
