"""#1445: the approval gate judges each Claude staff command from the task folder, so Bash must return there."""

from labhq.adapters.base import RunContext
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings


def _ctx(tmp_path):
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, system_prompt="ROLE")
    return RunContext(task=Task(agent_id="worker", prompt="go"), agent=agent, workdir=tmp_path,
                      settings=Settings(), mcp_servers=[], env={}, emit=None, prompt="go", read_only=False)


def test_claude_staff_bash_returns_to_the_task_folder_after_each_command(tmp_path):
    env = ClaudeCodeAdapter(Settings()).staff_env(_ctx(tmp_path))
    assert env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1"


def test_a_pi_engine_value_cannot_turn_the_task_folder_reset_off(tmp_path):
    settings = Settings.model_validate({"engines": {"claude_code": {
        "bin": "claude", "env": {"CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "0"}}}})
    env = ClaudeCodeAdapter(settings).staff_env(_ctx(tmp_path))
    assert env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1"
