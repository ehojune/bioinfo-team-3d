"""labhq_job is for Claude staff only: the prompt, the mounted tools and the refusal agree for every engine (#1003).

A job the runner starts runs outside the OS sandbox a Codex run keeps its commands in, and only the Claude approval gate
refuses detaching commands, so Codex staff must not be told to use labhq_job nor be offered it."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from labhq.adapters import base
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings

from tests.test_local_jobs_675 import _runner

ROOT = Path(__file__).resolve().parent.parent


def _footer(engine):
    agent = AgentSpec(id="worker", name="w", role="test", engine=engine, system_prompt="ROLE", builtin_mcp=[])
    ctx = SimpleNamespace(agent=agent, task=Task(agent_id="worker", prompt="go"), read_only=False,
                          private_labels=[], private_open_reads=[])
    return base.role_footer(ctx)


def test_claude_staff_keep_the_job_tool_rule_unchanged():
    text = _footer(Engine.claude_code)
    assert text.endswith(base.WORKSPACE_WRITE_RULES)
    assert "job_submit" in text and "which is refused" in text


@pytest.mark.parametrize("engine", [Engine.codex, Engine.gemini, Engine.antigravity, Engine.cli])
def test_other_engines_are_not_told_to_use_a_tool_they_cannot_use(engine):
    text = _footer(engine)
    assert text.endswith(base.WORKSPACE_WRITE_RULES_NO_JOB)
    assert "job_submit" not in text and "which is refused" not in text
    assert "labhq_job is not available to you" in text
    assert "labhq_hpc" in text and "ask the PI" in text  # the alternatives that exist for this engine


def test_the_two_rule_sets_differ_only_in_the_long_computation_bullet():
    claude, other = base.WORKSPACE_WRITE_RULES.splitlines(), base.WORKSPACE_WRITE_RULES_NO_JOB.splitlines()
    differing = [i for i, (a, b) in enumerate(zip(claude, other)) if a != b]
    assert len(claude) == len(other) and differing
    assert all("10 minutes" in claude[i] or "labhq_job" in claude[i] or "background" in claude[i] for i in differing)


def test_the_job_tool_is_mounted_only_for_claude_staff(tmp_path):
    from labhq.gateway.server import Hub

    _, runner = _runner(tmp_path)
    try:
        for engine, expected in ((Engine.claude_code, True), (Engine.codex, False), (Engine.gemini, False)):
            agent = AgentSpec(id="a", name="a", role="test", engine=engine, builtin_mcp=["job"])
            assert ("labhq_job" in [s.name for s in runner._mcp_servers(agent, {})]) is expected, engine
            assert ("labhq_job" in Hub._agent_mcp({"builtin_mcp": ["job"], "engine": engine.value})) is expected, engine
    finally:
        runner.store.close()


def test_an_agent_record_without_an_engine_key_is_a_claude_agent_for_the_job_tool():
    """AgentSpec's engine defaults to claude_code, so a stored agent dict that omits the key gets labhq_job too."""
    from labhq.gateway.server import Hub

    assert "labhq_job" in Hub._agent_mcp({"builtin_mcp": ["job"]})
    assert "labhq_job" not in Hub._agent_mcp({"builtin_mcp": ["job"], "engine": "codex"})
    assert "labhq_job" not in Hub._agent_mcp({"builtin_mcp": []})
    assert AgentSpec(id="a", name="a", role="test").engine == Engine.claude_code  # the default this mirrors


def test_no_core_codex_agent_lists_the_job_tool():
    for path in sorted((ROOT / "agents").rglob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if spec.get("engine") == "codex":
            assert "job" not in (spec.get("builtin_mcp") or []), path.name


@pytest.mark.asyncio
async def test_a_codex_submit_is_refused_with_the_alternatives(tmp_path):
    from tests.test_local_jobs_675 import QUICK

    _, runner = _runner(tmp_path)
    try:
        runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.codex,
                                                     builtin_mcp=[])
        ctx = SimpleNamespace(agent=runner.registry.agents["worker"])
        runner.tool_contexts["t1"] = ctx
        runner.workspaces["t1"] = SimpleNamespace(dir=tmp_path)
        runner.active_roots["t1"] = (False, None)
        out = await runner._submit_local_job({"task_id": "t1", "agent_id": "worker", "command": QUICK})
        assert "sandbox" in out["error"] and "labhq_hpc" in out["error"] and "ask the PI" in out["error"]
        assert not [j for j in runner.jobs if j.startswith("local-")]
    finally:
        runner.store.close()
