"""Follow-ups of PR #122 (#135 #145 #146 #147 #148): what reaches a staff CLI besides its argv. The process env
(the runner's parent session, `engines.<engine>.env`) and files in a reused workspace that the CLI reads as
instructions or configuration. Nothing here runs a real CLI; the spawn is replaced and its argv and env are read."""

import asyncio
import json
from pathlib import Path

import pytest

from labhq.adapters.base import RunContext
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, Task
from labhq.runner.daemon import Runner
from labhq.settings import Settings


async def _emit(*_args):
    pass


class _Proc:
    """Enough of an asyncio subprocess for base.run: one result line and exit 0."""

    returncode = 0
    stdin = None

    def __init__(self, engine):
        lines = ([b'{"type":"thread.started","thread_id":"t1"}\n',
                  b'{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}\n',
                  b'{"type":"turn.completed","usage":{}}\n'] if engine == Engine.codex else
                 [b'{"type":"result","subtype":"success","result":"answer","session_id":"s1"}\n'])
        self.stdout, self.stderr = self._lines(lines), self._lines([])

    @staticmethod
    async def _lines(lines):
        for line in lines:
            yield line

    async def wait(self):
        return 0


@pytest.fixture
def spawned(monkeypatch):
    """Every spawn as (argv, env). The adapter runs for real up to create_subprocess_exec."""
    seen = []

    def install(engine):
        async def fake_exec(*cmd, env=None, **kwargs):
            seen.append((list(cmd), dict(env or {})))
            return _Proc(engine)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda cmd, env, engine: cmd)
        return seen

    return install


def _settings(tmp_path):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    # Codex preflight refuses when a global AGENTS.md would load; keep this machine's own one out of the test.
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    return settings


def _staff(engine=Engine.codex, **fields):
    return AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[],
                     system_prompt="You are the worker.", **fields)


def _ctx(tmp_path, agent, settings, read_only=False, workdir=None):
    from labhq.adapters import read_only_profile

    wd = workdir or tmp_path / "wd"
    wd.mkdir(parents=True, exist_ok=True)
    return RunContext(task=Task(agent_id=agent.id, prompt="q"), agent=read_only_profile(agent) if read_only else agent,
                      workdir=wd, settings=settings, mcp_servers=[], env={"LABHQ_TASK_ID": "t"}, emit=_emit,
                      prompt="q", read_only=read_only)


# ---------------- #146: a runner started from a Codex terminal ----------------

# What a Codex desktop terminal can hand the runner. CODEX_EXEC_SERVER_URL is named in #146: codex-cli 0.159.2 reads
# it and it looks like it moves where commands run. The others loosen or redirect a staff session the same way.
CODEX_SESSION = {"CODEX_EXEC_SERVER_URL": "ws://127.0.0.1:9/exec", "CODEX_SANDBOX": "seatbelt",
                 "CODEX_SANDBOX_NETWORK_DISABLED": "0", "CODEX_PERMISSION_PROFILE": "danger",
                 "CODEX_THREAD_ID": "host-thread", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "codex_desktop"}


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_parent_codex_session_variables_do_not_reach_staff_codex(tmp_path, monkeypatch, spawned, read_only):
    seen = spawned(Engine.codex)
    for key, value in CODEX_SESSION.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CODEX_API_KEY", "staff-key")
    monkeypatch.setenv("CODEX_CA_CERTIFICATE", "/etc/ssl/lab-ca.pem")
    settings = _settings(tmp_path)
    result = await CodexAdapter(settings).run(_ctx(tmp_path, _staff(), settings, read_only))
    assert result.ok, result.error
    env = seen[0][1]
    assert not CODEX_SESSION.keys() & env.keys(), "the parent session's CODEX_* stay with the parent"
    assert env["CODEX_HOME"] == str(tmp_path / "codex-home")
    assert env["CODEX_API_KEY"] == "staff-key" and env["CODEX_CA_CERTIFICATE"] == "/etc/ssl/lab-ca.pem"


@pytest.mark.asyncio
async def test_a_codex_variable_the_pi_configures_still_reaches_a_step(tmp_path, monkeypatch, spawned):
    seen = spawned(Engine.codex)
    monkeypatch.setenv("CODEX_SQLITE_HOME", "host-sqlite")
    settings = _settings(tmp_path)
    settings.engines.codex.env["CODEX_SQLITE_HOME"] = "staff-sqlite"
    await CodexAdapter(settings).run(_ctx(tmp_path, _staff(), settings))
    assert seen[0][1]["CODEX_SQLITE_HOME"] == "staff-sqlite", "engines.codex.env is applied after the strip"


def test_bench_baseline_drops_the_parent_codex_session_too(tmp_path, monkeypatch):
    from labhq import bench

    for key, value in CODEX_SESSION.items():
        monkeypatch.setenv(key, value)
    settings = _settings(tmp_path)
    child_env = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def spawn(*args, **kwargs):
        child_env.update(kwargs["env"])
        return Process()

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *args: command)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(bench._run_baseline(bench.load_case("public-protein-qc"), "astra-ultra", tmp_path,
                                   "real", ["fake-cli"], settings))
    assert child_env and not CODEX_SESSION.keys() & child_env.keys()
    assert child_env["CODEX_HOME"] == str(tmp_path / "codex-home")
