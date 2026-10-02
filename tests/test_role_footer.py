"""The lab rules every staff instruction file ends with (adapters.base.role_footer)."""

import pytest

from labhq.adapters.base import ROLE_FOOTER, WORKSPACE_WRITE_RULES, RunContext, role_footer
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings

TEMP_RULE = "임시 파일·스크립트는 작업 폴더 안 .tmp/ 아래에 둔다"
PATH_RULE = "셸 명령의 경로는 셸 변수로 만들지 말고 작업 폴더 기준 상대 경로로 쓴다"


def _ctx(tmp_path, engine, read_only):
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, system_prompt="ROLE")
    workdir = tmp_path / ("ro" if read_only else "rw")
    workdir.mkdir(parents=True, exist_ok=True)
    return RunContext(task=Task(agent_id="worker", prompt="go"), agent=agent, workdir=workdir, settings=Settings(),
                      mcp_servers=[], env={}, emit=None, prompt="go", read_only=read_only)


def test_writing_staff_get_the_temp_folder_and_relative_path_rules(tmp_path):
    """2nd mock trial: /tmp writes and $VAR paths each raised a PI approval card the gate could not settle."""
    footer = role_footer(_ctx(tmp_path, Engine.codex, read_only=False))
    assert TEMP_RULE in footer and "/tmp·%TEMP%" in footer and "PI 승인" in footer
    assert PATH_RULE in footer and "승인 게이트" in footer
    assert footer == ROLE_FOOTER + WORKSPACE_WRITE_RULES  # nothing else configured: only the two lines are added


def test_read_only_staff_do_not_get_the_write_rules(tmp_path):
    footer = role_footer(_ctx(tmp_path, Engine.codex, read_only=True))
    assert footer == ROLE_FOOTER
    assert TEMP_RULE not in footer and PATH_RULE not in footer


@pytest.mark.parametrize("engine,adapter,read", [
    (Engine.codex, CodexAdapter, lambda wd: (wd / "AGENTS.md").read_text(encoding="utf-8")),
    (Engine.claude_code, ClaudeCodeAdapter, lambda wd: (wd / ".labhq" / "system_prompt.md").read_text(encoding="utf-8")),
])
def test_staff_instruction_files_carry_the_write_rules(tmp_path, engine, adapter, read):
    ctx = _ctx(tmp_path, engine, read_only=False)
    adapter(Settings()).prepare(ctx)
    text = read(ctx.workdir)
    assert TEMP_RULE in text and PATH_RULE in text
