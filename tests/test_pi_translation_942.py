"""PI-text translation (#942, trial 2026-10-24 attempt 9): every finished answer was rejected, two calls queued behind a
research step past the 30 s timeout, and the unknown cost of a haiku call named the staff member's Opus."""

import asyncio

import pytest

from labhq.models import Task, TaskResult
from labhq.pi_language import TRANSLATION_FORMAT, code_or_path
from labhq.pi_text import PI_TEXT_RULE, plain_text
from tests.test_pi_language import drain, event, hub, mock_model  # noqa: F401  (fixtures)
from tests.test_staff_control_review import _runner, _task, queued_control_io  # noqa: F401  (fixture)


def _accepted(text):
    """The validator _interpret applies to a finished answer."""
    import re
    shown = plain_text(text)
    return bool(re.search(r"[가-힣]", shown) and not code_or_path(shown) and len(shown) <= 500
                and len(re.split(r"(?<=[.!?。])\s+|\n+", shown)) <= 2)


def test_the_shared_card_rule_contradicts_the_two_sentence_check_so_the_translation_does_not_carry_it():
    headed = ("**무엇을 하려는지**\nKRAS G12C 표적치료제의 근거를 조사합니다.\n\n**왜 이렇게 하는지**\n시험 조건이 서로 달라서 "
              "수치를 그대로 비교하면 틀립니다.")  # the shape of attempt 9's pi_text_2 answer
    one_paragraph = "KRAS G12C 표적치료제의 근거를 조사하되 시험 조건이 달라 수치를 순위로 비교하지 않습니다. 본문을 읽지 못하면 초록만 봤다고 적습니다."
    assert not _accepted(headed) and _accepted(one_paragraph)
    assert "순서는 무엇을 하려는지" in PI_TEXT_RULE and "줄바꿈" in PI_TEXT_RULE  # what induced the headed answers
    assert "줄바꿈 없는 한 문단" in TRANSLATION_FORMAT and "두 문장 이내" in TRANSLATION_FORMAT


@pytest.mark.asyncio
async def test_the_translation_prompt_asks_for_one_paragraph_not_the_card_structure(hub, monkeypatch):  # noqa: F811
    calls = mock_model(monkeypatch, hub)
    await hub.publish(event())
    await drain(hub)
    [task] = calls
    assert TRANSLATION_FORMAT in task.prompt
    assert "순서는 무엇을 하려는지" not in task.prompt and "목록은 줄바꿈으로 나눕니다" not in task.prompt


@pytest.mark.asyncio
async def test_an_unknown_translation_names_the_model_it_used(hub, monkeypatch):  # noqa: F811
    hub.s.pi_language.timeout_s = 0.05

    async def never_returns(task):
        await asyncio.sleep(5)

    async def cancel_task(_task_id):
        return None

    monkeypatch.setattr(hub, "dispatch", never_returns)
    monkeypatch.setattr(hub, "cancel_task", cancel_task, raising=False)
    await hub.publish(event())
    await drain(hub)
    [item] = [v for k, v in hub.requests["r"]["cost_items"].items() if k.startswith("pi_text_")]
    assert item["status"] == "unknown" and item["reason"] == "outcome_unknown"
    assert item["model"] == hub.s.pi_language.model != "sonnet"  # the staff member's own model is sonnet here


@pytest.mark.asyncio
async def test_a_translation_does_not_wait_for_the_research_slot(tmp_path, monkeypatch, queued_control_io):  # noqa: F811
    release, step_started = asyncio.Event(), asyncio.Event()
    started = []

    async def run(ctx):
        started.append(ctx.task.id)
        if ctx.task.id == "step":
            step_started.set()
            await release.wait()
        return TaskResult(task_id=ctx.task.id, agent_id="worker", ok=True, text="done")

    runner = _runner(tmp_path, monkeypatch, run)
    runner.sem = asyncio.Semaphore(1)  # runner.max_parallel: 1, taken by the research step
    step = asyncio.create_task(runner.run_task(_task("step")))
    await asyncio.wait_for(step_started.wait(), 2)
    translation = Task(id="pi_text_9", request_id="r1", agent_id="worker", prompt="설명하세요",
                       meta={"kind": "pi_interpretation"})
    assert (await asyncio.wait_for(runner.run_task(translation), 2)).ok  # not queued behind the step
    assert started == ["step", "pi_text_9"] and not release.is_set()
    queued_step = asyncio.create_task(runner.run_task(_task("step2")))  # a research task still waits for its slot
    await asyncio.sleep(0.1)
    assert not queued_step.done() and started == ["step", "pi_text_9"]
    release.set()
    assert (await asyncio.wait_for(step, 2)).ok and (await asyncio.wait_for(queued_step, 2)).ok


def test_a_gateway_recorded_unknown_call_names_the_dispatched_override_model(hub):  # noqa: F811
    hub.store.put("task", "pi_text_7", {"request_id": "r", "payload": {"meta": {"agent_overrides": {
        "engine": "claude_code", "model": "haiku"}}}})
    hub._record_task_cost("r", "pi_text_7", TaskResult(task_id="pi_text_7", agent_id="chief_of_staff", ok=False),
                          outcome_unknown=True)
    item = hub.requests["r"]["cost_items"]["pi_text_7"]
    assert item["status"] == "unknown" and item["model"] == "haiku" and item["engine"] == "claude_code"
    hub.store.put("task", "plain", {"request_id": "r", "payload": {"meta": {}}})
    hub._record_task_cost("r", "plain", TaskResult(task_id="plain", agent_id="chief_of_staff", ok=False),
                          outcome_unknown=True)
    assert hub.requests["r"]["cost_items"]["plain"]["model"] == "sonnet"  # no override: the staff member's own
