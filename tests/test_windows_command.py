"""Windows npm shims must never receive the prompt through cmd.exe."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, _resolve_command
from labhq.models import AgentSpec, CliSpec, Engine, Task
from labhq.settings import Settings


@pytest.mark.parametrize("path_node", [False, True])
@pytest.mark.parametrize("launcher", ['"%_prog%"', '"%~dp0\\node.exe"'])
def test_npm_shim_prefers_its_own_node(tmp_path, monkeypatch, path_node, launcher):
    shim = tmp_path / "agent.cmd"
    shim.write_text('SET "_prog=%~dp0\\node.exe"\n'
                    f'{launcher} "%~dp0\\agent.js" %*\n')
    script = tmp_path / "agent.js"
    script.touch()
    node = tmp_path / "node.exe"
    node.touch()
    other = tmp_path / "path-node.exe"
    monkeypatch.setattr("labhq.adapters.base.shutil.which",
                        lambda value, **_: str(other) if path_node and value == "node.exe" else None)
    assert _resolve_command([str(shim), "arg"], {}, "cli") == [str(node), str(script), "arg"]


@pytest.mark.parametrize("launcher", ['"%~dp0\\node.exe"', 'node', '"%_prog%"'])
def test_npm_shim_uses_path_node_when_launcher_requires_it(tmp_path, monkeypatch, launcher):
    shim = tmp_path / "agent.cmd"
    shim.write_text('SET "_prog=node"\n' + f'{launcher} "%~dp0\\agent.js" %*\n')
    script = tmp_path / "agent.js"
    script.touch()
    if launcher != '"%~dp0\\node.exe"':
        (tmp_path / "node.exe").touch()
    node = tmp_path / "path-node.exe"
    monkeypatch.setattr("labhq.adapters.base.shutil.which",
                        lambda value, **_: str(node) if value == "node.exe" else None)
    assert _resolve_command([str(shim)], {}, "cli") == [str(node), str(script)]


@pytest.mark.parametrize("name, script", [
    ("codex", "node_modules/@openai/codex/bin/codex.js"),
    ("gemini", "node_modules/@google/gemini-cli/bundle/gemini.js"),
])
def test_npm_shim_unwraps_without_changing_multiline_argv(tmp_path, monkeypatch, name, script):
    shim = tmp_path / f"{name}.cmd"
    shim.write_text((Path(__file__).parent / "fixtures" / "npm" / shim.name).read_text())
    target = tmp_path / script
    target.parent.mkdir(parents=True)
    target.write_text("// fixture")
    node = tmp_path / "node.exe"
    node.touch()
    monkeypatch.setattr("labhq.adapters.base.shutil.which",
                        lambda value, **_: str(shim) if value == name else str(node) if value == "node.exe" else None)
    prompt = "first line\nsecond line"
    assert _resolve_command([name, "-p", prompt], {}, name) == [str(node), str(target), "-p", prompt]


def test_native_resolution_and_unrecognized_batch_refusal(tmp_path, monkeypatch):
    native = tmp_path / "codex.exe"
    batch = tmp_path / "custom.bat"
    batch.write_text("@echo off\nexit /b 0\n")
    monkeypatch.setattr("labhq.adapters.base.shutil.which",
                        lambda value, **_: str(native) if value == "codex" else str(batch) if value == "custom" else None)
    assert _resolve_command(["codex", "exec"], {}, "codex") == [str(native), "exec"]
    with pytest.raises(ValueError, match="custom.*native executable.*prefix_args"):
        _resolve_command(["custom", "-p", "first\nsecond"], {}, "custom")


async def test_prefix_args_expand_and_precede_agent_args(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text("import json, sys\n"
                      "print(json.dumps({'type':'message','role':'assistant','content':json.dumps(sys.argv[1:])}))\n"
                      "print(json.dumps({'type':'result','status':'success'}))\n")
    monkeypatch.setenv("FAKE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_PYTHON", sys.executable)
    s = Settings()
    s.engines.gemini.bin = "%FAKE_PYTHON%" if sys.platform == "win32" else "${FAKE_PYTHON}"
    s.engines.gemini.prefix_args = ["%FAKE_SCRIPT%" if sys.platform == "win32" else "${FAKE_SCRIPT}"]
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine.gemini)
    task = Task(agent_id="a", prompt="first\nsecond")
    workdir = tmp_path / "work"
    workdir.mkdir()
    ctx = RunContext(task=task, agent=agent, workdir=workdir, settings=s, mcp_servers=[],
                     env={}, emit=AsyncMock(), prompt=task.prompt)
    result = await get_adapter(Engine.gemini, s).run(ctx)
    assert result.ok
    argv = json.loads(result.text)
    assert argv[0:2] == ["-p", "first\nsecond"]
    command = (workdir / ".labhq" / "command.txt").read_text()
    assert command.index(str(script)) < command.index("-p")


async def test_batch_refused_before_spawn(tmp_path, monkeypatch):
    batch = tmp_path / "agent.cmd"
    batch.write_text("@echo off\n")
    s = Settings()
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine.cli,
                      cli=CliSpec(command=[str(batch), "first\nsecond"]))
    task = Task(agent_id="a", prompt="first\nsecond")
    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=s, mcp_servers=[],
                     env={}, emit=AsyncMock(), prompt=task.prompt)
    spawn = AsyncMock()
    monkeypatch.setattr("labhq.adapters.base.asyncio.create_subprocess_exec", spawn)
    result = await get_adapter(Engine.cli, s).run(ctx)
    assert not result.ok and "prefix_args" in result.error and "cli" in result.error
    spawn.assert_not_awaited()
