"""JSON transport failures are repaired before a PI decision or result-contract correction."""
import json
from pathlib import Path

import pytest

from labhq.adapters.base import ROLE_FOOTER, RunState
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from tests.test_cso import FakeHub, penguins_hub, replan_hub, result as task_result

ERROR = "InputValidationError: StructuredOutput was called with input that could not be parsed as JSON"


def make_runner(tmp_path, monkeypatch, failures, *, limit=2, error=ERROR, session="same-session",
                valid_on_parse_error=False):
    cfg = Settings()
    cfg.gateway.state_dir = cfg.runner.state_dir = str(tmp_path / "state")
    cfg.runner.workspace_root = str(tmp_path / "runs")
    cfg.runner.agents_dir = str(tmp_path / "agents")
    cfg.runner.talent_dir = str(tmp_path / "talent")
    if limit != 2:
        cfg.runner.structured_output_retries = limit
    runner = Runner(cfg)
    runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.mock)
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append((ctx.task.model_copy(deep=True), ctx.prompt, ctx.resume_baseline, ctx.workdir))
            # Exercise the real Claude event conversion; no CLI, model, or GitHub calls.
            if len(calls) <= failures:
                await ClaudeCodeAdapter(cfg).handle_line(json.dumps({
                    "type": "user", "message": {"content": [
                        {"type": "tool_result", "is_error": True, "content": error}]}}), RunState(), ctx)
            ctx.record_run(session_id=session, session_cost_total=len(calls) * 0.1)
            failed = len(calls) <= failures
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, session_id=session,
                              cost_usd=0.1, usage={"input_tokens": 10},
                              text="안전 검사 때문에 멈췄습니다" if failed else "완료",
                              structured=None if failed and not valid_on_parse_error else {"answer": "완료"})

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_: Adapter())
    return runner, calls


@pytest.mark.asyncio
async def test_valid_final_structured_output_suppresses_resume_retry(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 1, valid_on_parse_error=True)

    result = await runner.run_task(Task(agent_id="worker", prompt="original", output_schema={"type": "object"}))

    assert len(calls) == 1
    assert result.ok and result.structured == {"answer": "완료"}
    assert result.cost_usd == pytest.approx(0.1) and result.usage["input_tokens"] == 10
    assert ERROR in result.tool_errors


@pytest.mark.asyncio
@pytest.mark.parametrize("task_budget,request_limit,spent", [(0.1, 0.6, 0.1), (0.5, 0.2, 0.1)])
async def test_retry_stops_when_smaller_budget_is_spent(tmp_path, monkeypatch, task_budget, request_limit, spent):
    runner, calls = make_runner(tmp_path, monkeypatch, 10)
    hub = FakeHub(runner.run_task)
    hub.requests["r"]["budget_usd"] = request_limit
    orch = Orchestrator(hub)
    orch.cost["r"] = spent

    result = await orch.run_step(Task(
        agent_id="worker", request_id="r", prompt="original", output_schema={"type": "object"},
        budget_usd=task_budget,
    ))

    assert len(calls) == 1
    assert hub.calls[0].request_budget_remaining_usd == pytest.approx(request_limit - spent)
    assert not result.ok and result.error_kind == "structured_output_format"
    assert result.cost_usd == pytest.approx(0.1) and result.usage["input_tokens"] == 10
    assert ERROR in result.tool_errors


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["plan", "step", "direct"])
async def test_parse_error_then_resume_succeeds_without_pi_card(tmp_path, monkeypatch, kind):
    runner, calls = make_runner(tmp_path, monkeypatch, 1)
    hub = FakeHub(runner.run_task)
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="original",
                                                  output_schema={"type": "object"}, meta={"kind": kind}))
    assert len(calls) == 2
    assert calls[1][0].resume_session_id == "same-session"
    assert calls[0][3] == calls[1][3]
    assert calls[1][2]["session_cost_total"] == pytest.approx(0.1)
    assert "같은 내용" in calls[1][1] and "경로는 /" in calls[1][1]
    assert result.ok and result.structured == {"answer": "완료"}
    assert result.cost_usd == pytest.approx(0.2) and result.usage["input_tokens"] == 20
    assert ERROR in result.tool_errors
    assert hub.approvals == []


@pytest.mark.asyncio
async def test_retry_limit_yields_only_format_error_card(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 10)
    hub = FakeHub(runner.run_task)
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="original",
                                                  output_schema={"type": "object"}, meta={"kind": "plan"}))
    assert len(calls) == 3 and len(hub.calls) == 1
    assert not result.ok and result.error_kind == "structured_output_format"
    assert result.structured is None and "안전 검사" not in result.text
    assert len(hub.approvals) == 1
    assert "형식 오류" in hub.approvals[0]["summary"]
    assert ERROR in hub.approvals[0]["detail"]["tool_errors"]


@pytest.mark.asyncio
async def test_exhausted_worker_does_not_reset_limit_by_replanning():
    def replan(_task):
        pytest.fail("format retry limit must not be reset by a new plan")

    hub = replan_hub([{"id": "analysis", "agent_id": "worker", "instruction": "analyze", "depends_on": []}],
                     lambda task: task_result(task, ok=False, error_kind="structured_output_format",
                                              error="StructuredOutput 형식 오류: " + ERROR, tool_errors=[ERROR]),
                     replan)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "failed"
    assert not any(task.meta["kind"] == "replan" for task in hub.calls)
    assert len([card for card in hub.approvals if "형식 오류" in card["summary"]]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,session,expected", [(0, "same-session", 1), (1, "same-session", 2), (2, None, 1)])
async def test_bound_and_missing_session_never_restart_work(tmp_path, monkeypatch, limit, session, expected):
    runner, calls = make_runner(tmp_path, monkeypatch, 10, limit=limit, session=session)
    result = await runner.run_task(Task(agent_id="worker", prompt="original", output_schema={"type": "object"}))
    assert len(calls) == expected
    assert not result.ok and result.error_kind == "structured_output_format"


@pytest.mark.asyncio
async def test_unrelated_tool_error_is_not_a_json_retry(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 1, error="File could not be parsed as JSON")
    result = await runner.run_task(Task(agent_id="worker", prompt="original"))
    assert len(calls) == 1 and result.ok


@pytest.mark.asyncio
async def test_detection_uses_original_error_before_preview_truncation(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 1, error="prefix " * 80 + ERROR)
    result = await runner.run_task(Task(agent_id="worker", prompt="original", output_schema={"type": "object"}))
    assert len(calls) == 2 and result.ok


def test_staff_and_chief_prompts_forbid_invented_causes():
    rule = "확인하지 않은 원인을 지어내지 않는다"
    assert rule in ROLE_FOOTER
    assert rule in Path("agents/core/chief_of_staff.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_plan_prompt_preserves_user_command_and_regex_verbatim():
    hub = penguins_hub(["fixture"])
    command = r'"C:\tool\run.exe --pattern \d+\t"'
    hub.requests["r"]["text"] += f" Run this command exactly: {command}"
    await Orchestrator(hub).run_request("r")
    plan = next(task for task in hub.calls if task.meta["kind"] == "plan")
    assert command in plan.prompt
    assert command in hub.requests["r"]["text"]
