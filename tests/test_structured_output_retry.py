"""JSON transport failures are repaired before a PI decision or result-contract correction."""
import json
import time
from pathlib import Path

import pytest

from labhq.adapters.base import ROLE_FOOTER, RunContext, RunState
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from tests.test_cso import FakeHub, penguins_hub, replan_hub, result as task_result

ERROR = "InputValidationError: StructuredOutput was called with input that could not be parsed as JSON"


@pytest.mark.parametrize("change", ["tools", "unknown_tools", "child_unknown", "job_unknown", "time",
                                     "deadline", "halt", "outputs", "scan", "retry", "deleted"])
def test_early_exit_requires_every_piece_of_evidence(change):
    from labhq.runner.structured_output import early_exit_retry_evidence
    proof = {"end_observed": "process_exit", "job": "active", "wait": "completed", "job_active_processes": 1,
             "wrapper_created": 123, "cli_pid": 10, "cli_created": 124, "tool_events": "none",
             "halt_requested": False, "elapsed_s": 2.8, "deadline_monotonic": time.monotonic() + 60,
             "clock_id": "runner"}
    result = TaskResult(task_id="t", agent_id="a", ok=False, exit_code=0xC000013A, cleanup_evidence=proof)
    before, after = {"outputs/a": (1,)}, {"outputs/a": (1,)}
    incomplete = tool_calls = already = False
    if change == "tools": tool_calls = True
    if change == "unknown_tools": proof["tool_events"] = "UNKNOWN"
    if change == "child_unknown": proof["cli_created"] = None
    if change == "job_unknown": proof["job_active_processes"] = None
    if change == "time": proof["elapsed_s"] = 30.1
    if change == "deadline": proof["deadline_monotonic"] = time.monotonic() - 1
    if change == "halt": proof["halt_requested"] = True
    if change == "outputs": after = {"outputs/a": (2,)}
    if change == "deleted": after = {}
    if change == "scan": incomplete = True
    if change == "retry": already = True
    result.cleanup_evidence = proof
    assert early_exit_retry_evidence(result, before=before, after=after, incomplete=incomplete,
                                     tool_calls=tool_calls, already_retried=already) is None


def test_early_exit_offers_one_retry_only_with_complete_evidence():
    from labhq.runner.structured_output import early_exit_retry_evidence
    deadline = time.monotonic() + 60
    result = TaskResult(task_id="t", agent_id="a", ok=False, exit_code=0xC000013A, cleanup_evidence={
        "end_observed": "process_exit", "job": "active", "wait": "completed", "job_active_processes": 1,
        "wrapper_created": 123, "cli_pid": 10, "cli_created": 124, "tool_events": "none",
        "halt_requested": False, "elapsed_s": 2.8, "deadline_monotonic": deadline, "clock_id": "runner"})
    assert early_exit_retry_evidence(result, before={}, after={}, incomplete=False,
                                     tool_calls=False, already_retried=False) == {"deadline": deadline, "clock_id": "runner"}


def test_retry_output_shape_rejects_omitted_entries_and_scan_limits(tmp_path):
    from labhq.runner.daemon import _retry_output_shape
    root = tmp_path / "outputs"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "existing.txt").write_text("unchanged", encoding="utf-8")
    shape = _retry_output_shape(tmp_path, [], 10, 2)
    assert set(shape[0]) == {"outputs/nested/existing.txt"} and shape[1] == {"outputs/nested"}
    assert _retry_output_shape(tmp_path, [], 1, 2) is None
    assert _retry_output_shape(tmp_path, [], 10, 0) is None
    assert _retry_output_shape(tmp_path, [root / "nested"], 10, 2) is None


def test_retry_shape_includes_and_compares_excluded_result_files(tmp_path):
    from labhq.runner.daemon import _retry_output_shape, _retry_scan_complete
    root = tmp_path / "outputs"
    root.mkdir()
    result = root / "RESULT_previous.md"
    result.write_text("old", encoding="utf-8")
    before = _retry_output_shape(tmp_path, [], 10, 2)
    assert _retry_scan_complete(before, {})  # the normal scanner omits runner RESULT files
    result.write_text("changed", encoding="utf-8")
    assert _retry_output_shape(tmp_path, [], 10, 2) != before
    (root / "unscanned.txt").write_text("not a RESULT file", encoding="utf-8")
    assert not _retry_scan_complete(_retry_output_shape(tmp_path, [], 10, 2), {})


def make_runner(tmp_path, monkeypatch, failures, *, limit=2, error=ERROR, session="same-session",
                valid_on_parse_error=False, final_structured=None, raw_input=None,
                plan_max_chars=None):
    cfg = Settings()
    cfg.gateway.state_dir = cfg.runner.state_dir = str(tmp_path / "state")
    cfg.runner.workspace_root = str(tmp_path / "runs")
    cfg.runner.agents_dir = str(tmp_path / "agents")
    cfg.runner.talent_dir = str(tmp_path / "talent")
    if limit != 2:
        cfg.runner.structured_output_retries = limit
    if plan_max_chars is not None:
        cfg.orchestrator.plan_max_chars = plan_max_chars
    runner = Runner(cfg)
    runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.mock)
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append((ctx.task.model_copy(deep=True), ctx.prompt, ctx.resume_baseline, ctx.workdir))
            # Exercise the real Claude event conversion; no CLI, model, or GitHub calls.
            if len(calls) <= failures:
                adapter = ClaudeCodeAdapter(cfg)
                if raw_input is not None:
                    state = RunState()
                    await adapter.handle_line(json.dumps({
                        "type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                            "content_block": {"type": "tool_use", "id": "toolu_bad",
                                              "name": "StructuredOutput", "input": {}}}}), state, ctx)
                    for start in range(0, len(raw_input), 997):
                        await adapter.handle_line(json.dumps({
                            "type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                "delta": {"type": "input_json_delta",
                                          "partial_json": raw_input[start:start + 997]}}}), state, ctx)
                    await adapter.handle_line(json.dumps({
                        "type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}), state, ctx)
                    await adapter.handle_line(json.dumps({
                        "type": "assistant", "message": {"content": [{"type": "tool_use",
                            "id": "toolu_bad", "name": "StructuredOutput",
                            "input": {"__unparsedToolInput": {"raw": raw_input[:2048]}}}]}}), state, ctx)
                else:
                    state = RunState()
                await adapter.handle_line(json.dumps({
                    "type": "user", "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "toolu_bad", "is_error": True,
                         "content": error}]}}), state, ctx)
            ctx.record_run(session_id=session, session_cost_total=len(calls) * 0.1)
            failed = len(calls) <= failures
            structured = final_structured if final_structured is not None else {"answer": "완료"}
            return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, session_id=session,
                              cost_usd=0.1, usage={"input_tokens": 10},
                              text="안전 검사 때문에 멈췄습니다" if failed else "완료",
                              structured=None if failed and not valid_on_parse_error else structured)

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
    retry_prompt = calls[1][1]
    assert "경로는 /" not in retry_prompt
    assert "한 글자도 바꾸지 말고" in retry_prompt
    assert r"역슬래시를 \\로" in retry_prompt
    assert r'따옴표를 \"로' in retry_prompt
    assert "비ASCII" in retry_prompt and r"\u" in retry_prompt
    assert result.ok and result.structured == {"answer": "완료"}
    assert result.cost_usd == pytest.approx(0.2) and result.usage["input_tokens"] == 20
    assert ERROR in result.tool_errors
    assert hub.approvals == []


@pytest.mark.asyncio
async def test_format_error_saves_complete_raw_input(tmp_path, monkeypatch):
    raw = '{"plan":"' + ("한글 계획 " * 700) + '"'
    runner, calls = make_runner(tmp_path, monkeypatch, 1, raw_input=raw)

    await runner.run_task(Task(agent_id="worker", prompt="original", output_schema={"type": "object"},
                               meta={"kind": "plan"}))

    saved = list((calls[0][3] / ".tmp" / "structured_output_failures").glob("*.json"))
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8") == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    '{"plan":"' + ("긴 계획 " * 100),
    '{"plan":' + ("x" * 100) + '}',
])
async def test_suspected_truncated_plan_uses_compact_retry(tmp_path, monkeypatch, raw):
    runner, calls = make_runner(tmp_path, monkeypatch, 1, raw_input=raw, plan_max_chars=100)

    result = await runner.run_task(Task(agent_id="worker", prompt="original", output_schema={"type": "object"},
                                        meta={"kind": "plan"}))

    assert result.ok and len(calls) == 2
    retry_prompt = calls[1][1]
    assert "계획을 줄여 다시" in retry_prompt
    assert "단계 설명" in retry_prompt and "반복 문구" in retry_prompt and "큰 표" in retry_prompt
    assert "갈래" in retry_prompt


def test_claude_requests_partial_messages_for_full_invalid_json(tmp_path):
    cfg = Settings()
    agent = AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code)
    ctx = RunContext(task=Task(agent_id="worker", prompt="original"), agent=agent, workdir=tmp_path,
                     settings=cfg, mcp_servers=[], env={}, emit=None, prompt="original")

    command = ClaudeCodeAdapter(cfg).build_command(ctx)

    assert "--include-partial-messages" in command


@pytest.mark.asyncio
async def test_large_plan_records_configured_size_warning(tmp_path, monkeypatch):
    plan = {"answer": "가" * 80}
    runner, _calls = make_runner(tmp_path, monkeypatch, 0, final_structured=plan, plan_max_chars=40)

    result = await runner.run_task(Task(agent_id="worker", prompt="original", output_schema={"type": "object"},
                                        meta={"kind": "plan"}))

    assert result.ok
    warnings = [event["data"] for event in runner.store.pending()
                if event["type"] == "agent.log" and event["data"].get("code") == "plan_size_limit"]
    assert len(warnings) == 1
    assert warnings[0]["limit_chars"] == 40 and warnings[0]["actual_chars"] > 40


@pytest.mark.asyncio
async def test_retry_preserves_windows_path_value(tmp_path, monkeypatch):
    command = r'C:\tool\run.exe --pattern "\d+\t"'
    runner, calls = make_runner(tmp_path, monkeypatch, 1, final_structured={"command": command})

    result = await runner.run_task(Task(
        agent_id="worker", prompt=f"Run this command exactly: {command}",
        output_schema={"type": "object"},
    ))

    assert len(calls) == 2
    assert result.structured == {"command": command}
    assert result.structured["command"] == command


@pytest.mark.asyncio
async def test_retry_limit_yields_only_format_error_card(tmp_path, monkeypatch):
    runner, calls = make_runner(tmp_path, monkeypatch, 10)
    hub = FakeHub(runner.run_task)
    result = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="original",
                                                  output_schema={"type": "object"}, meta={"kind": "plan"}))
    # #673: each repair is its own dispatch (so it passes the request budget gate), not a loop inside one turn.
    assert len(calls) == 3 and len(hub.calls) == 3
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
