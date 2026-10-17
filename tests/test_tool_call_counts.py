import json
from pathlib import Path

import pytest

from labhq.adapters import get_adapter as real_get_adapter
from labhq.adapters.base import RunState
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.portfolio import build_portfolio_files
from labhq.request_bundle import build_request_bundle
from labhq.orchestrator.cso import merged_turn
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    return settings


def _stream(engine: Engine) -> list[dict]:
    if engine is Engine.claude_code:
        return [
            {"type": "system", "subtype": "init", "session_id": "claude-session", "model": "fixture"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c1",
                "name": "mcp__pubmed__search_articles", "input": {"query": "PRIVATE QUERY"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c1",
                "is_error": True, "content": "PRIVATE FAILURE"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c2",
                "name": "Read", "input": {"file_path": "PRIVATE PATH"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c2",
                "content": "ok"}]}},
            {"type": "result", "subtype": "success", "result": "done", "session_id": "claude-session"},
        ]
    return [
        {"type": "thread.started", "thread_id": "codex-session"},
        {"type": "item.started", "item": {"id": "x1", "type": "mcp_tool_call", "server": "pubmed",
            "tool": "search_articles", "arguments": {"query": "PRIVATE QUERY"}}},
        {"type": "item.completed", "item": {"id": "x1", "type": "mcp_tool_call", "server": "pubmed",
            "tool": "search_articles", "status": "failed", "error": {"message": "PRIVATE FAILURE"}}},
        {"type": "item.started", "item": {"id": "x2", "type": "command_execution",
            "command": "type PRIVATE PATH"}},
        {"type": "item.completed", "item": {"id": "x2", "type": "command_execution", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]


def test_followup_turns_add_tool_counts_without_retaining_inputs():
    first = TaskResult(task_id="first", agent_id="worker", ok=True, tool_calls={
        "mcp:pubmed.search_articles": {"calls": 2, "failures": 1},
    })
    later = TaskResult(task_id="later", agent_id="worker", ok=True, tool_calls={
        "mcp:pubmed.search_articles": {"calls": 1, "failures": 0},
        "Read": {"calls": 3, "failures": 0},
    })

    assert merged_turn(first, later).model_dump(mode="json")["tool_calls"] == {
        "Read": {"calls": 3, "failures": 0},
        "mcp:pubmed.search_articles": {"calls": 3, "failures": 1},
    }


async def _run(settings: Settings, monkeypatch, engine: Engine, task_id: str) -> TaskResult:
    runner = Runner(settings)
    agent = AgentSpec(id=engine.value, name=engine.value, role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class StreamAdapter:
        async def run(self, ctx):
            adapter = real_get_adapter(engine, settings)
            state = RunState()
            for event in _stream(engine):
                await adapter.handle_line(json.dumps(event), state, ctx)
            return adapter.finalize(state, ctx, 0)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: StreamAdapter())
    return await runner.run_task(Task(id=task_id, request_id="request-tools", agent_id=agent.id,
                                      prompt="count tools", meta={"kind": "step"}))


@pytest.mark.asyncio
async def test_runner_counts_claude_and_codex_tools_and_bundle_records_staff_table(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    claude = await _run(settings, monkeypatch, Engine.claude_code, "task-claude")
    codex = await _run(settings, monkeypatch, Engine.codex, "task-codex")

    assert claude.model_dump(mode="json")["tool_calls"] == {
        "Read": {"calls": 1, "failures": 0},
        "mcp:pubmed.search_articles": {"calls": 1, "failures": 1},
    }
    assert codex.model_dump(mode="json")["tool_calls"] == {
        "mcp:pubmed.search_articles": {"calls": 1, "failures": 1},
        "shell": {"calls": 1, "failures": 0},
    }
    wire = TaskResult.model_validate_json(codex.model_dump_json()).model_dump(mode="json")
    assert wire["tool_calls"] == codex.model_dump(mode="json")["tool_calls"]
    assert "PRIVATE QUERY" not in json.dumps(wire) and "PRIVATE PATH" not in json.dumps(wire)

    request = {
        "id": "request-tools", "report": "done", "report_appendix": "", "execution_record": "# 실행 기록",
        "plan": {"steps": [
            {"id": "claude-step", "depends_on": []},
            {"id": "codex-step", "depends_on": ["claude-step"]},
        ]},
        "results": {
            "claude-step": claude.model_dump(mode="json"),
            "codex-step": codex.model_dump(mode="json"),
        },
    }
    bundle = Path(build_request_bundle(request, settings)["path"])
    record = (bundle / "execution_record.md").read_text(encoding="utf-8")

    assert "## 직원별 도구 호출 수" in record
    assert "| claude-step | claude_code | mcp:pubmed.search_articles | 1 | 1 |" in record
    assert "| codex-step | codex | shell | 1 | 0 |" in record
    assert "PRIVATE QUERY" not in record and "PRIVATE PATH" not in record
    files = build_portfolio_files(request, settings, {}, {}, bundle=bundle)
    published_record = files["run/execution_record.md"]
    assert "| claude-step | claude_code | mcp:pubmed.search_articles | 1 | 1 |" in published_record
    assert "PRIVATE QUERY" not in published_record and "PRIVATE PATH" not in published_record
