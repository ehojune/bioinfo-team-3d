"""#707/#708: request-scope coverage and historical CP1 cost estimates."""

import pytest

from labhq.costs import historical_plan_cost_estimate
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, missing_request_analysis_terms
from tests.test_research_cp1 import _research_settings
from tests.test_research_protocol import MiniHub, valid_pack_values, valid_plan


def _past_request(cost: float = 12.0, steps: int = 2) -> dict:
    return {
        "id": "past",
        "status": "done",
        "work_kind": "research",
        "plan": valid_plan(steps=steps, pack_values=valid_pack_values()),
        "cost_summary": {
            "actual_usd": cost,
            "estimated_usd": 0.0,
            "subtotal_usd": cost,
            "unknown_count": 0,
        },
    }


def test_deg_named_in_request_but_missing_from_scope_and_steps_is_detected():
    plan = valid_plan(pack_values=valid_pack_values())

    missing = missing_request_analysis_terms("공개 RNA-seq에서 DEG 분석을 해 줘", plan)

    assert "DEG" in missing


@pytest.mark.parametrize(("request_text", "label"), [
    ("차등 메틸화 DMP를 구해 줘", "DMP/차등 메틸화"),
    ("경로 분석을 해 줘", "경로 분석"),
    ("공변량 회귀를 해 줘", "회귀"),
    ("메타분석을 해 줘", "메타분석"),
    ("생존분석을 해 줘", "생존분석"),
    ("GWAS를 해 줘", "GWAS"),
    ("시료를 분류해 줘", "분류"),
])
def test_prelisted_analysis_names_and_request_actions_are_checked(request_text, label):
    missing = missing_request_analysis_terms(request_text, valid_plan(pack_values=valid_pack_values()))

    assert label in missing


def test_historical_estimate_scales_completed_cost_by_planned_step_count():
    current = valid_plan(steps=3, pack_values=valid_pack_values())

    estimate = historical_plan_cost_estimate({"past": _past_request()}, current, {}, exclude_request_id="new")

    assert estimate["basis"] == "historical"
    assert estimate["similar_requests"] == 1
    assert estimate["historical_steps"] == 2
    assert estimate["average_per_step_usd"] == pytest.approx(6.0)
    assert estimate["estimate_usd"] == pytest.approx(18.0)


def test_historical_estimate_uses_employee_and_model_means_when_available():
    old = _past_request(cost=16.0, steps=2)
    old["plan"]["steps"][0]["agent_id"] = "a"
    old["plan"]["steps"][1]["agent_id"] = "b"
    old["cost_items"] = {
        "a": {"status": "actual", "usd": 2.0, "agent_id": "a", "engine": "claude_code", "model": "small"},
        "b": {"status": "actual", "usd": 10.0, "agent_id": "b", "engine": "codex", "model": "large"},
        "cso": {"status": "actual", "usd": 4.0, "agent_id": "cso", "engine": "claude_code", "model": "cso"},
    }
    current = valid_plan(steps=2, pack_values=valid_pack_values())
    for step in current["steps"]:
        step["agent_id"] = "b"

    estimate = historical_plan_cost_estimate(
        {"past": old}, current, {"b": {"engine": "codex", "model": "large"}})

    assert estimate["average_per_step_usd"] == pytest.approx(8.0)
    assert estimate["estimate_usd"] == pytest.approx(24.0)
    assert any(row["agent_id"] == "b" and row["model"] == "large" and row["average_usd"] == 10.0
               for row in estimate["worker_model_averages"])


async def _run_cp1(plan: dict, *, text: str, history: bool) -> MiniHub:
    settings = _research_settings()

    async def reply(task):
        assert task.meta["kind"] == "plan"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text=text)
    if history:
        hub.requests["past"] = _past_request()
    await Orchestrator(hub).run_request("r")
    return hub


@pytest.mark.asyncio
async def test_cp1_warns_when_requested_deg_is_missing_from_the_plan():
    hub = await _run_cp1(valid_plan(pack_values=valid_pack_values()),
                         text="공개 RNA-seq 자료에서 DEG 분석을 해 줘", history=False)

    card = hub.approvals[0]
    assert "요청 범위 누락" in card["summary"] and "DEG" in card["summary"]
    assert any("DEG" in warning for warning in card["detail"]["warnings"])


@pytest.mark.asyncio
async def test_cp1_uses_historical_per_step_estimate_and_shows_its_basis():
    plan = valid_plan(steps=3, pack_values=valid_pack_values())
    plan["budget_usd"] = 4.0
    hub = await _run_cp1(plan, text="공개 자료를 비교 분석해 줘", history=True)

    request = hub.requests["r"]
    card = hub.approvals[0]
    assert request["plan"]["budget_usd"] == pytest.approx(18.0)
    assert card["detail"]["cost_estimate"] == {
        "basis": "historical",
        "estimate_usd": 18.0,
        "similar_requests": 1,
        "historical_steps": 2,
        "average_per_step_usd": 6.0,
        "current_steps": 3,
        "worker_model_averages": [],
    }
    assert "요청 총비용 추정 $18.00" in card["summary"]
    assert "비슷한 완료 요청 1건 · 단계당 평균 $6.00" in card["summary"]


@pytest.mark.asyncio
async def test_cp1_keeps_the_plan_estimate_when_history_is_insufficient():
    plan = valid_plan(pack_values=valid_pack_values())
    plan["budget_usd"] = 4.0
    hub = await _run_cp1(plan, text="공개 자료를 비교 분석해 줘", history=False)

    request = hub.requests["r"]
    card = hub.approvals[0]
    assert request["plan"]["budget_usd"] == pytest.approx(4.0)
    assert card["detail"]["cost_estimate"]["basis"] == "fallback"
    assert card["detail"]["cost_estimate"]["estimate_usd"] == pytest.approx(4.0)
    assert "실측 기록 부족" in card["summary"]
