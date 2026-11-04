"""Anonymized #983 pattern: missing facilities, CSO replies, worker asks again.

Only fake consults and temporary stores; no operator DB, model or repair execution.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest

from labhq.gateway.server import Hub
from labhq.models import AskRequest, TaskResult
from labhq.settings import Settings


@pytest.fixture
def support(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.orchestrator.step_max_attempts = 1
    settings.orchestrator.step_retry_backoff_s = 0
    hub = Hub(settings)
    hub.agents = {"cso": {"engine": "mock"}}
    hub.agent_runner = {"cso": "fake"}
    hub.requests["r"] = {"id": "r", "text": "public example", "status": "running"}
    calls = []

    async def dispatch(task):
        calls.append(task)
        # Preserve the observed bad recommendation without copying private run evidence.
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          text="도구 경로는 시설팀에 다시 문의하세요.")

    hub.dispatch = dispatch
    yield hub, calls
    hub.store.close()


def question(**updates):
    return AskRequest(task_id="blocked-step", agent_id="literature", request_id="r", to="facilities",
                      question="도구 경로를 확인해 주세요", why_blocked="도구를 찾지 못했습니다").model_copy(update=updates)


def assert_fallback(answer):
    assert answer["requested_to"] == "facilities"
    assert answer["routed_to"] == "cso"
    assert answer["fallback_reason"] == "facilities_unavailable"
    text = answer.get("answer") or answer["reason"]
    assert "CSO가 대체 담당" in text
    assert "시설팀에 다시 문의하지 마세요" in text
    assert "진단·수정·복구 성공을 뜻하지 않습니다" in text


async def test_absent_facilities_prompt_and_reply_identify_actual_responder(support):
    hub, calls = support
    ask = question()
    await hub.orchestrator.answer_ask(ask, "fake")
    assert [task.agent_id for task in calls] == ["cso"]
    assert "Do not ask or tell the employee to contact facilities again" in calls[0].prompt
    assert "stop the blocked step" in calls[0].prompt
    assert_fallback(hub.store.get("ask", ask.id)["answer"])


async def test_reworded_repeat_in_same_step_does_not_dispatch_again(support):
    hub, calls = support
    await hub.orchestrator.answer_ask(question(), "fake")
    repeat = question(question="CSO 안내를 받고 시설팀에 다시 문의합니다")
    await hub.orchestrator.answer_ask(repeat, "fake")
    answer = hub.store.get("ask", repeat.id)["answer"]
    assert len(calls) == 1
    assert answer["status"] == "rejected"
    assert "이미 CSO 대체 답변" in answer["reason"]
    assert_fallback(answer)


async def test_available_facilities_still_receives_question_after_prior_fallback(support):
    hub, calls = support
    await hub.orchestrator.answer_ask(question(), "fake")
    hub.agents["facilities"] = {"engine": "mock"}
    hub.agent_runner["facilities"] = "fake"
    ask = question()  # Same signature: a restored facilities roster must bypass the old CSO cache.
    await hub.orchestrator.answer_ask(ask, "fake")
    answer = hub.store.get("ask", ask.id)["answer"]
    assert [task.agent_id for task in calls] == ["cso", "facilities"]
    assert answer["from"] == answer["routed_to"] == "facilities"
    assert "fallback_reason" not in answer
    assert "Facilities diagnosis only" in calls[-1].prompt


async def test_legacy_cached_fallback_gets_notice_without_new_consult(support):
    hub, calls = support
    first = question()
    await hub.orchestrator.answer_ask(first, "fake")
    stored = hub.store.get("ask", first.id)
    stored["answer"] = {"status": "answered", "from": "cso", "routed_to": "cso",
                        "answer": "시설팀에 다시 문의하세요."}
    hub.store.put("ask", first.id, stored)
    repeat = question()
    await hub.orchestrator.answer_ask(repeat, "fake")
    answer = hub.store.get("ask", repeat.id)["answer"]
    assert len(calls) == 1 and answer["cached"]
    assert_fallback(answer)


@pytest.mark.parametrize("failure", ["empty", "budget", "offline"])
async def test_failed_cso_fallback_stays_rejected_with_availability_notice(support, failure):
    hub, calls = support

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="cso", ok=failure == "empty",
                          error=None if failure == "empty" else failure)

    hub.dispatch = dispatch
    ask = question()
    await hub.orchestrator.answer_ask(ask, "fake")
    answer = hub.store.get("ask", ask.id)["answer"]
    assert answer["status"] == "rejected" and "answer" not in answer
    assert_fallback(answer)


async def test_other_step_can_get_its_own_cso_fallback(support):
    hub, calls = support
    await hub.orchestrator.answer_ask(question(), "fake")
    ask = question(task_id="another-step")
    await hub.orchestrator.answer_ask(ask, "fake")
    assert len(calls) == 2
    assert_fallback(hub.store.get("ask", ask.id)["answer"])


async def test_continuation_task_keeps_same_step_reask_guard(support):
    hub, calls = support
    for task_id in ("blocked-step", "continued-task"):
        hub.store.put("task", task_id, {"kind": "step", "step_id": "S1", "request_id": "r"})
    await hub.orchestrator.answer_ask(question(), "fake")
    ask = question(task_id="continued-task", question="같은 단계에서 시설팀에 다시 문의합니다")
    await hub.orchestrator.answer_ask(ask, "fake")
    assert len(calls) == 1
    assert hub.store.get("ask", ask.id)["answer"]["status"] == "rejected"


async def test_overlapping_same_step_reasks_dispatch_only_one_fallback(support, monkeypatch):
    hub, calls = support
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_lock = hub.orchestrator._consult_lock

    @asynccontextmanager
    async def tracked_lock(*args):
        if entered.is_set():
            queued.set()
        async with original_lock(*args):
            yield

    monkeypatch.setattr(hub.orchestrator, "_consult_lock", tracked_lock)

    async def dispatch(task):
        calls.append(task)
        entered.set()
        await release.wait()
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="시설팀에 재문의하세요")

    hub.dispatch = dispatch
    first = asyncio.create_task(hub.orchestrator.answer_ask(question(), "fake"))
    await asyncio.wait_for(entered.wait(), 5)
    repeat = question(question="기다리는 동안 시설팀에 다시 문의합니다")
    second = asyncio.create_task(hub.orchestrator.answer_ask(repeat, "fake"))
    try:
        await asyncio.wait_for(queued.wait(), 5)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 5)
        assert len(calls) == 1
        assert hub.store.get("ask", repeat.id)["answer"]["status"] == "rejected"
    finally:
        release.set()
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_installation_hard_stop_still_goes_to_pi_without_cso(support):
    hub, calls = support
    approvals = []

    async def approve(**kwargs):
        approvals.append(kwargs)
        return {"approved": False, "note": "승인하지 않습니다"}

    hub.request_approval = approve
    ask = question(question="새 package를 설치해도 되나요?")
    await hub.orchestrator.answer_ask(ask, "fake")
    answer = hub.store.get("ask", ask.id)["answer"]
    assert not calls and len(approvals) == 1
    assert answer["routed_to"] == "pi" and answer["status"] == "rejected"
    assert "fallback_reason" not in answer


async def test_no_cso_roster_refusal_still_explains_missing_requested_target(support):
    hub, calls = support
    hub.agents.clear()
    ask = question()
    await hub.orchestrator.answer_ask(ask, "fake")
    answer = hub.store.get("ask", ask.id)["answer"]
    assert not calls and answer["status"] == "rejected"
    assert "'cso'이 roster에 없습니다" in answer["reason"]
    assert_fallback(answer)
