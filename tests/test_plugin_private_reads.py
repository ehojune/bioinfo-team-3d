"""PR #553: loading a skill must not expose its configuration home's siblings."""
import os

import pytest

from labhq import doctor, private_paths
from labhq.adapters import codex as codex_mod
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.models import Engine, Task
from labhq.policy import claude_rule_path, evaluate_tool
from labhq.recruit.paper2agent import skill_path
from tests.test_private_paths import (
    REAL_LABHQ_ENTRIES, _capture_runner, _home, _no_short_names, _runner_settings,
)  # noqa: F401


async def _run(tmp_path, monkeypatch, engine, plugin=False):
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    settings = _runner_settings(tmp_path, None)
    staff = home / "staff"
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(staff / "claude")}
    settings.engines.codex.env = {"CODEX_HOME": str(staff / "codex")}
    root = staff / ("claude" if engine == Engine.claude_code else "codex")
    skill = root / "plugins" / "example" if plugin else skill_path(engine.value, settings)
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("fixture skill", encoding="utf-8")
    secrets = [root / p for p in (
        ".credentials.json", "auth.json", "history.jsonl", "projects/other/session.jsonl",
        "sessions/other/session.jsonl", "settings.json", "settings.local.json", "todos/task.json",
        "plugins/other/private.txt",
    )]
    for path in secrets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture private", encoding="utf-8")
    runner, seen = _capture_runner(settings, monkeypatch, engine=engine)
    if plugin:
        runner.registry.get("worker").plugin_dirs = [str(skill)]
    task = Task(id="t", agent_id="worker", request_id="r", prompt="q",
                meta={} if plugin else {"kind": "recruit"})
    result = await runner.run_task(task)
    assert result.ok, result.error
    return seen["ctx"], home, skill, secrets


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [Engine.claude_code, Engine.codex])
@pytest.mark.parametrize("plugin", [False, True], ids=["recruiter", "plugin"])
@pytest.mark.parametrize("tool", ["Read", "Bash"])
async def test_skill_keeps_sibling_reads_behind_the_private_gate(tmp_path, monkeypatch, engine, plugin, tool):
    ctx, home, skill, secrets = await _run(tmp_path, monkeypatch, engine, plugin)

    def gate(name, path):
        data = {"file_path": str(path)} if name == "Read" else {"command": f'cat "{path}"'}
        return evaluate_tool(name, data, ctx.settings.policy, allowed_roots=[str(ctx.workdir)],
                             workdir=str(ctx.workdir), home=str(home), environ={},
                             private_paths=ctx.private_paths, private_open_reads=ctx.private_open_reads,
                             private_enabled=ctx.private_enabled)

    for path in secrets:
        # Shell access requires approval; it must never be automatically allowed.
        assert gate(tool, path).action == ("deny" if tool == "Read" else "ask"), path
    assert gate("Read", skill / "SKILL.md").action == "allow"
    write = evaluate_tool("Write", {"file_path": str(skill / "new.txt"), "content": "x"}, ctx.settings.policy,
                          allowed_roots=[str(ctx.workdir)], workdir=str(ctx.workdir), home=str(home), environ={},
                          private_paths=ctx.private_paths, private_open_reads=ctx.private_open_reads)
    assert write.action == "deny"
    assert ctx.env[private_paths.OPEN_READS_ENV_VAR].split(os.pathsep) == ctx.private_open_reads
    if engine == Engine.claude_code:
        ctx.agent.tools = ["Read", "Bash"]
        command = ClaudeCodeAdapter(ctx.settings).build_command(ctx)
        assert f"Read(/{claude_rule_path(str(skill))}/**)" in command
        assert gate("Read", secrets[0].parent / "projects" / private_paths.claude_project_slug(
            os.path.abspath(str(ctx.workdir))) / "output.txt").action == "allow"


@pytest.mark.asyncio
async def test_codex_profile_denies_home_and_reopens_only_skill(tmp_path, monkeypatch):
    ctx, _home_, skill, secrets = await _run(tmp_path, monkeypatch, Engine.codex)
    monkeypatch.setattr(codex_mod, "_is_windows", lambda: True)
    ctx.settings.engines.codex.windows_sandbox = "elevated"
    flags = codex_mod.CodexAdapter(ctx.settings).sandbox_flags(ctx)
    profile = next(v for v in flags if v.startswith("permissions.labhq_staff="))
    assert f'{codex_mod._toml(str(secrets[0].parent))} = "deny"' in profile
    assert f'{codex_mod._toml(str(skill))} = "read"' in profile
    assert f'{codex_mod._toml(str(skill.parent))} = "read"' not in profile


@pytest.mark.asyncio
async def test_doctor_retains_private_home_with_a_nested_plugin(tmp_path, monkeypatch):
    ctx, _home_, skill, secrets = await _run(tmp_path, monkeypatch, Engine.claude_code, plugin=True)
    found = doctor._doctor_private(ctx.settings, [ctx.agent], None)
    assert str(secrets[0].parent) in found.paths
    assert str(skill) in found.open_reads
