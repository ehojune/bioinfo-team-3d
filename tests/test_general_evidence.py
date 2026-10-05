"""The lightweight evidence contract for ordinary (non-research) orchestration steps (#58 1, 4)."""

import hashlib
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import (Orchestrator, RESEARCH_STEP_PROMPT, attach_general_result,
                                    parse_general_result)
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from tests.test_cso import FakeHub


FULL = """Summary before the contract.

## Findings
Three samples passed.

## Evidence
- `outputs/table.tsv`

## Not established
The external cohort was not tested.

## Method changes
None.
"""


def test_general_result_blocks_parse_with_all_headings():
    assert parse_general_result(FULL) == {
        "findings": "Three samples passed.",
        "evidence": "- `outputs/table.tsv`",
        "not_established": "The external cohort was not tested.",
        "method_changes": "None.",
    }


def test_general_result_without_blocks_keeps_the_old_shape():
    result = attach_general_result(TaskResult(task_id="t", agent_id="worker", ok=True, text="ordinary answer"))
    dumped = result.model_dump(mode="json")
    assert result.general_sections == {} and result.evidence_path_warnings == []
    assert "general_sections" not in dumped and "evidence_path_warnings" not in dumped


def test_general_result_with_only_some_blocks_keeps_empty_values():
    result = parse_general_result("## Findings\nOne result.\n\n## Evidence\n")
    assert result == {"findings": "One result.", "evidence": "", "not_established": "", "method_changes": ""}


def test_uncollected_evidence_path_is_a_warning_not_a_rejection():
    result = TaskResult(task_id="t", agent_id="worker", ok=True, text=FULL.replace(
        "`outputs/table.tsv`", "`outputs/table.tsv`, `outputs/hash-only.tsv`, and `outputs/missing.tsv`"),
        outputs=["outputs/table.tsv"],
        output_sha256={"outputs/table.tsv": "a" * 64, "outputs/hash-only.tsv": "b" * 64})
    checked = attach_general_result(result)
    assert checked.ok is True
    assert checked.evidence_path_warnings == ["outputs/missing.tsv"]


@pytest.mark.asyncio
async def test_runner_collects_tool_error_first_lines_only_for_general_steps(tmp_path, monkeypatch):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class Adapter:
        async def run(self, ctx):
            await ctx.emit("agent.tool_error", {"text": "fixture lookup failed\n" + "PRIVATE " * 200})
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    general = await runner.run_task(Task(id="general", request_id="r", agent_id="worker", prompt="work",
                                           meta={"kind": "step", "general_result_contract": True}))
    research = await runner.run_task(Task(id="research", request_id="r", agent_id="worker", prompt="work",
                                            meta={"kind": "step"}))

    assert general.tool_errors == ["fixture lookup failed"]
    assert research.tool_errors == []
    assert "tool_errors" not in research.model_dump(mode="json")


@pytest.mark.asyncio
async def test_general_report_warns_without_copying_long_tool_output():
    hub = FakeHub(lambda task: None)
    hub.requests["r"]["plan"] = {"steps": [{"id": "A", "agent_id": "worker"}]}
    result = TaskResult(task_id="t", agent_id="worker", ok=True, text="done",
                        evidence_path_warnings=["outputs/missing.tsv"],
                        tool_errors=["fixture lookup failed\n" + "PRIVATE " * 200,
                                     "second failure should only affect the count"])
    Orchestrator(hub)._finish("r", "Narrative", {"A": {**result.model_dump(mode="json"), "status": "done"}}, ok=True)
    report = hub.requests["r"]["report"]
    appendix = hub.requests["r"]["report_appendix"]

    assert "실행 기록 참고" in report
    assert "## 보고서 경고" not in report
    assert "## 보고서 경고" in appendix
    assert "A: Evidence 경로 불일치 1건: outputs/missing.tsv" in appendix
    assert "A: 실패한 조회 — 증거도 부재 증명도 아님 2건" in appendix
    assert "fixture lookup failed" in appendix
    assert "PRIVATE" not in appendix and "second failure should only affect the count" not in appendix


def test_research_prompt_hash_and_empty_research_result_fields_stay_fixed():
    # #373 benches A/B add durable artifacts and one-block upstream path variables; #423 makes them inputs/ paths and asks for an outputs/env/ record.
    assert hashlib.sha256(RESEARCH_STEP_PROMPT.encode()).hexdigest() == (
        "cc998d77731b3bfa38de1c1d62a1ab15d1eb3ed177a78dc3d6883f54108b1be3")
    dumped = TaskResult(task_id="t", agent_id="worker", ok=True, structured={"version": 2}).model_dump(mode="json")
    assert "general_sections" not in dumped and "evidence_path_warnings" not in dumped and "tool_errors" not in dumped


def test_inline_code_that_is_not_an_output_path_is_not_a_warning():
    """PR #363 review: `pandas 2.2` or `GSE10072` in Evidence is not a missing output."""
    result = TaskResult(task_id="t", agent_id="worker", ok=True, text=FULL.replace(
        "`outputs/table.tsv`", "`outputs/table.tsv` (`pandas 2.2`, `GSE10072`, `python .tmp/run.py`)"),
        outputs=["outputs/table.tsv"], output_sha256={"outputs/table.tsv": "a" * 64})
    assert attach_general_result(result).evidence_path_warnings == []


@pytest.mark.asyncio
async def test_a_retry_that_succeeds_keeps_the_earlier_failed_lookups():
    """PR #363 review: a transient failure with a tool error, then a clean success, must keep the tool error."""
    async def dispatch(task):
        if len(hub.calls) == 1:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="HTTP 503 overloaded",
                              tool_errors=["fixture lookup failed"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

    hub = FakeHub(dispatch)
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="analyze",
                                                meta={"kind": "step", "step_id": "A"}))
    assert len(hub.calls) == 2 and res.ok
    assert res.tool_errors == ["fixture lookup failed"]
