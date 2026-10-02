"""#276: a Claude staff session refuses inbound cross-session messages from the PI's other sessions."""

import json

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings


async def _emit(_kind, _data):
    pass


@pytest.mark.parametrize("profile", ["isolated", "not_isolated", "read_only"])
@pytest.mark.parametrize("given", [None, "accept", "hold"])
def test_claude_staff_settings_refuse_cross_session_inbound(tmp_path, profile, given):
    settings = Settings()
    settings.engines.claude_code.isolate_user_config = profile != "not_isolated"
    agent = AgentSpec(id="probe", name="Probe", role="test", engine=Engine.claude_code, builtin_mcp=[])
    claude_settings = {} if given is None else {"crossSessionInbound": given}
    ctx = RunContext(task=Task(agent_id="probe", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=_emit, prompt="x", claude_settings=claude_settings,
                     read_only=profile == "read_only")

    command = get_adapter(agent.engine, settings).build_command(ctx)
    generated = json.loads(command[command.index("--settings") + 1])

    # --settings is Claude's flagSettings source: it outranks user settings, and project/local files can only make
    # the value stricter (Claude Code 2.1.282). refuse is the strictest value; scripts/probe_inbound_mcp.py runs the
    # real CLI against it.
    assert generated["crossSessionInbound"] == "refuse"
    assert generated["permissions"]["deny"][-2:] == ["SendMessage", "ListAgents"]
