import json

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.models import AgentSpec, CliSpec, Engine, Task
from labhq.settings import Settings
from labhq import util
from labhq.util import short


@pytest.mark.parametrize("field", ["input", "text"])
@pytest.mark.parametrize("value", ["a" * 401, {"command": "가" * 401}, "line\n" * 200])
def test_event_preview_keeps_payload_bound_and_marks_only_actual_clipping(field, value):
    preview = util.event_preview(field, value, 400)
    assert preview[field] == short(value, 400)
    assert len(preview[field]) == 400
    assert preview[f"{field}_truncated"] is True
    assert preview[f"{field}_limit"] == 400
    assert preview[f"{field}_chars"] == len(short(value, 10000))
    assert util.event_preview(field, "short\nline", 400) == {field: "short line"}
    assert util.event_preview(field, "x" * 400, 400) == {field: "x" * 400}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine,limit", [("codex", 300), ("claude_code", 400), ("gemini", 400),
                                        ("antigravity", 400), ("cli", 400)])
async def test_engine_command_events_report_server_clipping(tmp_path, engine, limit):
    raw = "powershell -Command " + "가" * 900
    payloads = {
        "codex": {"type": "item.started", "item": {"type": "command_execution", "command": raw}},
        "claude_code": {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": raw}]}},
        "gemini": {"type": "tool_use", "tool_name": "shell", "parameters": raw},
        "antigravity": {"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE",
                          "tool_name": "shell", "tool_info": {"parameters": raw}}},
        "cli": {"type": "tool", "name": "shell", "input": raw},
    }
    settings = Settings()
    agent = AgentSpec(id="fixture", name="Fixture", role="test", engine=Engine(engine), builtin_mcp=[],
                      cli=CliSpec(command=["example"], output="jsonl") if engine == "cli" else None)
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    context = RunContext(task=Task(agent_id=agent.id, prompt="test"), agent=agent, workdir=tmp_path,
                         settings=settings, mcp_servers=[], env={}, emit=emit, prompt="test")
    await get_adapter(agent.engine, settings).handle_line(json.dumps(payloads[engine]), RunState(), context)
    assert len(events) == 1 and events[0][0] == "agent.tool"
    assert events[0][1]["input"] == short(raw, limit)
    assert events[0][1]["input_truncated"] is True
    assert events[0][1]["input_limit"] == limit
    assert events[0][1]["input_chars"] == len(raw)
