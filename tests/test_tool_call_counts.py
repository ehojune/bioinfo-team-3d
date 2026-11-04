import json
from pathlib import Path

import pytest

from labhq.adapters import get_adapter as real_get_adapter
from labhq.adapters.base import RunState
from labhq.models import AgentSpec, Engine, Task, TaskResult, ToolCallCount
from labhq.portfolio import build_portfolio_files
from labhq.request_bundle import build_request_bundle
from labhq.orchestrator.cso import Orchestrator, merged_turn
from labhq.runner.daemon import Runner, ToolCallCounter
from labhq.settings import Settings
from tests.test_contract_slot_repair import slot_hub
from tests.test_cso import FakeHub, replan_hub, result
from tests.test_research_report import _hub as research_hub


def _count(calls: int, failures: int = 0) -> ToolCallCount:
    return ToolCallCount(calls=calls, failures=failures)


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


@pytest.mark.asyncio
async def test_success_result_counts_its_turn_once_in_the_final_step():
    hub = research_hub()
    original = hub.reply

    async def reply(task):
        outcome = await original(task)
        if task.meta["kind"] == "step":
            outcome.tool_calls = {"Read": _count(2)}
        return outcome

    hub.reply = reply
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["results"]["s1"]["tool_calls"] == {
        "Read": {"calls": 2, "failures": 0},
    }


@pytest.mark.asyncio
async def test_failed_result_correction_keeps_both_turns_once():
    hub = research_hub(contract_violation=True)
    hub.s.research.result_corrections = 1
    original = hub.reply

    async def reply(task):
        outcome = await original(task)
        if task.meta["kind"] == "step":
            outcome.tool_calls = {"Read": _count(1)}
        elif task.meta["kind"] == "result_correction":
            outcome.tool_calls = {"shell": _count(2, 1)}
        return outcome

    hub.reply = reply
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["results"]["s1"]["tool_calls"] == {
        "Read": {"calls": 1, "failures": 0},
        "shell": {"calls": 2, "failures": 1},
    }


@pytest.mark.asyncio
async def test_rejected_slot_repair_keeps_the_rejected_turn_count():
    hub = slot_hub({"e1": {"status": "attempted_none", "reason": "not found"}})
    original = hub.reply

    async def reply(task):
        outcome = await original(task)
        if task.meta["kind"] == "step":
            outcome.tool_calls = {"Read": _count(1)}
        elif task.meta["kind"] == "slot_repair":
            outcome = outcome.model_copy(update={
                "tool_calls": {"shell": _count(1)},
                "unreported_outputs": ["outputs/rejected.txt"],
            })
        return outcome

    hub.reply = reply
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["results"]["s1"]["tool_calls"] == {
        "Read": {"calls": 1, "failures": 0},
        "shell": {"calls": 1, "failures": 0},
    }


@pytest.mark.asyncio
async def test_halted_step_keeps_pre_halt_and_resumed_turn_counts():
    async def dispatch(task):
        if len(hub.calls) == 1:
            return result(task, ok=False, error="halted", error_kind="halted", session_id="session-1",
                          workdir="runs/A", tool_calls={"Read": {"calls": 1, "failures": 0}})
        return result(task, text="done", workdir="runs/A",
                      tool_calls={"shell": {"calls": 2, "failures": 0}})

    hub = FakeHub(dispatch)
    hub.supports_resume = lambda _agent: True

    async def wait_halted(*_args):
        return True

    hub.wait_halted = wait_halted
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="work", meta={"kind": "step", "step_id": "A"}))

    assert outcome.model_dump(mode="json")["tool_calls"] == {
        "Read": {"calls": 1, "failures": 0},
        "shell": {"calls": 2, "failures": 0},
    }


@pytest.mark.asyncio
async def test_failed_revision_does_not_recount_the_previous_turn():
    steps = [{"id": "analysis", "agent_id": "worker", "instruction": "analyze", "depends_on": []}]

    def on_step(task):
        if task.meta["revision"]:
            return result(task, ok=False, error="revision broke", workdir="runs/analysis",
                          tool_calls={"shell": {"calls": 1, "failures": 1}})
        return result(task, text="analysis done", workdir="runs/analysis",
                      tool_calls={"Read": {"calls": 2, "failures": 0}})

    def on_review(task):
        revise = task.meta["revision"] == 0
        return {"verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 4, "thoroughness": 4},
                "issues": ([{"step_id": "analysis", "priority": "P1", "problem": "method",
                              "request": "revise in place"}] if revise else [])}

    hub = replan_hub(steps, on_step, lambda task: pytest.fail("unexpected replan"),
                     max_replans=0, max_failure_replans=1, on_review=on_review)
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["results"]["analysis"]["tool_calls"] == {
        "Read": {"calls": 2, "failures": 0},
        "shell": {"calls": 1, "failures": 1},
    }


def test_error_without_tool_use_id_is_counted_as_unknown_not_the_latest_tool():
    counter = ToolCallCounter()
    counter.observe("agent.tool", {"id": "read-1", "name": "Read"})
    counter.observe("agent.tool", {"id": "shell-1", "name": "shell"})
    counter.observe("agent.tool_error", {"text": "earlier overlapping call failed"})

    assert {name: count.model_dump(mode="json") for name, count in counter.summary().items()} == {
        "Read": {"calls": 1, "failures": 0},
        "shell": {"calls": 1, "failures": 0},
        "도구 미상 오류": {"calls": 1, "failures": 1},
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
