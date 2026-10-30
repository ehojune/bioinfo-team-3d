"""Read-only request preview: known pre-plan calls and operator-configured cost ranges."""
from __future__ import annotations

from typing import Any

from .models import AgentSpec
from .settings import Settings


def estimate_call(settings: Settings, agent: AgentSpec, *, stage: str, model: str | None = None) -> dict[str, Any]:
    """Estimate one call without consulting an engine or inferring a missing price."""
    tier = agent.model_tier
    rate = getattr(settings.model_tier_prices, tier, None) if tier else None
    known = rate is not None and rate.min_usd is not None and rate.max_usd is not None
    # The model this call would run: a request's own, the agent's, else what its tier means on its engine (#625).
    run_model = model or agent.model or settings.model_tiers.model_for(tier, agent.engine.value)
    return {
        "stage": stage,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model": run_model,
        "model_tier": tier,
        "calls": 1,
        "cost_status": "known" if known else "unknown",
        "cost_range_usd": [rate.min_usd, rate.max_usd] if known else None,
    }


def build_preview(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    """Build the preview from local config and roster YAML only."""
    # doctor._roster is deliberately the read-only loader: unlike Registry.load it never archives expired contracts.
    from .doctor import _roster

    roster = {agent.id: agent for agent in _roster(settings)}
    calls: list[dict[str, Any]] = []
    if body.get("mode") == "direct":
        agent_id = str(body.get("agent_id") or "")
        if agent_id not in roster:
            raise ValueError(f"직원 설정을 찾지 못했습니다: {agent_id}")
        calls.append(estimate_call(settings, roster[agent_id], stage="직접 요청"))
        future = "직접 호출 뒤 추가 단계 없음"
        future_label = "직접 호출 이후"
        future_unknown = False
    else:
        requested_model = body.get("cso_model")
        if requested_model and requested_model not in settings.orchestrator.cso_models:
            raise ValueError(f"허용되지 않은 CSO 모델입니다: {requested_model}")
        for agent_id, stage in (
            (settings.orchestrator.chief_of_staff_agent, "CSO 입력 브리핑"),
            (settings.orchestrator.precedent_agent, "선행 연구 기준 조사"),
        ):
            if agent_id and agent_id in roster:
                calls.append(estimate_call(settings, roster[agent_id], stage=stage))
        cso_id = settings.orchestrator.cso_agent
        if cso_id not in roster:
            raise ValueError(f"CSO 직원 설정을 찾지 못했습니다: {cso_id}")
        calls.append(estimate_call(settings, roster[cso_id], stage="CSO 계획", model=requested_model))
        future = ("계획 결과에 따른 확인·교정 호출 수 미정; 직원 실행은 하지 않음"
                  if body.get("mode") == "plan_only" else
                  "계획 결과에 따른 직원 단계·확인·교정·검토·보고 호출 수 미정")
        future_label = "계획 이후"
        future_unknown = True

    low = sum(call["cost_range_usd"][0] for call in calls if call["cost_range_usd"] is not None)
    high = sum(call["cost_range_usd"][1] for call in calls if call["cost_range_usd"] is not None)
    unknown = sum(call["cost_status"] == "unknown" for call in calls)
    return {"calls": calls, "known_cost_range_usd": [round(low, 6), round(high, 6)],
            "unknown_cost_calls": unknown, "future": future, "future_label": future_label,
            "future_unknown": future_unknown}


def _range_text(low: float, high: float) -> str:
    return f"${low:.2f}" if low == high else f"${low:.2f}-${high:.2f}"


def render_preview(preview: dict[str, Any]) -> str:
    """Human-readable preview that never makes an unknown total look like zero."""
    lines = ["dry-run — 모델·도구·네트워크 호출 없음", "", "예상 단계"]
    for index, call in enumerate(preview["calls"], 1):
        price = (_range_text(*call["cost_range_usd"]) if call["cost_range_usd"] is not None else "비용 모름")
        tier = call.get("model_tier") or "등급 미지정"
        model = call.get("model") or "모델 미정"
        lines.append(f"{index}. {call['stage']} — {call['agent_id']} ({tier}·{model}) — 1회 — {price}")
    lines += [f"{len(preview['calls']) + 1}. {preview['future_label']} — {preview['future']}",
              "", "직원별 예상 호출"]
    grouped: dict[str, int] = {}
    for call in preview["calls"]:
        grouped[call["agent_id"]] = grouped.get(call["agent_id"], 0) + 1
    lines.extend(f"- {agent_id}: {count}회" for agent_id, count in grouped.items())
    low, high = preview["known_cost_range_usd"]
    total = f"알려진 비용 {_range_text(low, high)}"
    if preview["unknown_cost_calls"]:
        total += f" + 비용 모름 {preview['unknown_cost_calls']}건"
    if preview["future_unknown"]:
        lines.append("- 계획 이후 직원: 미정")
    lines += ["", f"합계: {total}"]
    if preview["future_unknown"]:
        lines.append("계획 이후 비용: 모름")
    return "\n".join(lines)
