"""The lab rules every staff instruction file ends with (adapters.base.role_footer)."""

import pytest

from labhq.adapters.base import ROLE_FOOTER, WORKSPACE_WRITE_RULES, RunContext, role_footer
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings

TEMP_RULE = "Put temporary files and scripts under ./.tmp/ in your workspace"
PATH_RULE = "write paths relative to your workspace, not built from shell variables"
WAIT_RULE = "Wait for every command to finish and verify its result before ending your turn"


def _ctx(tmp_path, engine, read_only):
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, system_prompt="ROLE")
    workdir = tmp_path / ("ro" if read_only else "rw")
    workdir.mkdir(parents=True, exist_ok=True)
    return RunContext(task=Task(agent_id="worker", prompt="go"), agent=agent, workdir=workdir, settings=Settings(),
                      mcp_servers=[], env={}, emit=None, prompt="go", read_only=read_only)


def test_writing_staff_get_the_temp_folder_and_relative_path_rules(tmp_path):
    """2nd mock trial: /tmp writes and $VAR paths each raised a PI approval card the gate could not settle."""
    footer = role_footer(_ctx(tmp_path, Engine.codex, read_only=False))
    assert TEMP_RULE in footer and "/tmp or %TEMP%" in footer and "needs PI approval" in footer
    assert PATH_RULE in footer and "approval gate" in footer
    assert WAIT_RULE in footer and "ending the turn stops background work" in footer
    assert "labhq_hpc" in footer and "ask the PI" in footer
    assert footer == ROLE_FOOTER + WORKSPACE_WRITE_RULES  # nothing else is configured


def test_read_only_staff_do_not_get_the_write_rules(tmp_path):
    footer = role_footer(_ctx(tmp_path, Engine.codex, read_only=True))
    assert footer == ROLE_FOOTER
    assert TEMP_RULE not in footer and PATH_RULE not in footer and WAIT_RULE not in footer


@pytest.mark.parametrize("engine,adapter,read", [
    (Engine.codex, CodexAdapter, lambda wd: (wd / "AGENTS.md").read_text(encoding="utf-8")),
    (Engine.claude_code, ClaudeCodeAdapter, lambda wd: (wd / ".labhq" / "system_prompt.md").read_text(encoding="utf-8")),
])
def test_staff_instruction_files_carry_the_write_rules(tmp_path, engine, adapter, read):
    ctx = _ctx(tmp_path, engine, read_only=False)
    adapter(Settings()).prepare(ctx)
    text = read(ctx.workdir)
    assert TEMP_RULE in text and PATH_RULE in text


@pytest.mark.parametrize("read_only", [False, True])
def test_every_staff_footer_covers_failed_and_empty_lookups_and_weaker_methods(tmp_path, read_only):
    """#84: a failed lookup is reported as a failure, an empty search with what was searched, and a fallback to a
    weaker method is stated in the report. Read-only staff (the reviewer) get these rules too."""
    footer = " ".join(role_footer(_ctx(tmp_path, Engine.codex, read_only=read_only)).split())
    assert "A lookup that failed (error, timeout, refused access) is a failure, never a negative result" in footer
    assert "A search that found nothing goes in the report with what was searched: source, query, scope and " \
           "filters" in footer
    assert "If you fall back to a weaker method" in footer and "never switch silently" in footer
    assert "부재 증명" not in ROLE_FOOTER  # the built-in tool failure line (#339) stays on the tool error only


def test_writing_staff_get_the_environment_rule():
    from labhq.adapters.base import WORKSPACE_WRITE_RULES

    assert "If the plan has an environment step, run packages from its interpreter" in WORKSPACE_WRITE_RULES
    assert "--target ./.pylib" in WORKSPACE_WRITE_RULES and "never write into another step's workspace" in WORKSPACE_WRITE_RULES


def test_claude_staff_disables_background_tasks_by_default_and_respects_pi_value(tmp_path):
    default = ClaudeCodeAdapter(Settings())
    assert default.staff_env(_ctx(tmp_path, Engine.claude_code, read_only=False))[
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"

    configured = Settings.model_validate({"engines": {"claude_code": {
        "bin": "claude", "env": {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "0"}}}})
    overridden = ClaudeCodeAdapter(configured)
    assert overridden.staff_env(_ctx(tmp_path, Engine.claude_code, read_only=False))[
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "0"
