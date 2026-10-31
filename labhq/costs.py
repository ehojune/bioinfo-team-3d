"""Task cost accounting: reported, estimated from a dated price table, or unaccounted (#270).

A task whose engine reports no dollar amount is never counted as $0. Its tokens are converted only with a
price row for that exact model id and only when every token count the row bills was reported; otherwise the
task stays unaccounted and is listed apart from the subtotal. Estimates are list prices, not a bill: Codex
staff usually run on a ChatGPT plan.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any

# USD per 1M tokens: OpenAI API Standard tier, short context (<=272K input tokens per call). Every model below has
# context_window 272000 in Codex's model list, so a call is short context unless the staff CODEX_HOME raises
# model_context_window; then this is a lower bound. Usage rule: input_tokens includes cached_input_tokens and
# cache_write_input_tokens (the prompt-caching guide's cost formula), and output_tokens includes reasoning tokens
# (the Responses API reports them as a breakdown of output_tokens).
PRICE_CATALOG: dict[str, Any] = {
    "version": "2026-10-02",
    "checked_on": "2026-10-02",
    "source": "https://developers.openai.com/api/docs/pricing",
    "usage_rule": "https://developers.openai.com/api/docs/guides/prompt-caching",
    "tier": "standard",
    "context": "short",
    "engines": {
        "codex": {
            "gpt-6-astra": {"input": 10.0, "cached_input": 1.0, "cache_write": 12.5, "output": 50.0},
            "gpt-6.1-sol": {"input": 2.0, "cached_input": 0.1, "cache_write": 2.5, "output": 10.0},
            "gpt-6-luna": {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5},
            "gpt-6-sol": {"input": 2.0, "cached_input": 0.2, "cache_write": 2.5, "output": 10.0},
            "gpt-5.6-sol": {"input": 4.0, "cached_input": 0.4, "cache_write": 5.0, "output": 20.0},
            "gpt-5.6-terra": {"input": 2.0, "cached_input": 0.2, "cache_write": 2.5, "output": 12.0},
            "gpt-5.6-luna": {"input": 0.2, "cached_input": 0.02, "cache_write": 0.25, "output": 1.2},
        },
    },
}
# Usage fields each billed rate needs; a missing one makes the estimate unknown rather than cheaper.
CODEX_FIELDS = {"input": "input_tokens", "cached_input": "cached_input_tokens",
                "cache_write": "cache_write_input_tokens", "output": "output_tokens"}
STALE_AFTER_DAYS = 90
ESTIMATE_NOTE = "추정은 판본 있는 API 가격표 환산이며 청구액이 아닙니다"
FREE_ENGINES = frozenset({"mock"})  # the mock adapter runs no model


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _price(engine: str | None, model: str | None, as_of: date) -> dict | None:
    row = (PRICE_CATALOG["engines"].get(engine or "") or {}).get(model or "")
    if row is None:
        return None
    checked = date.fromisoformat(PRICE_CATALOG["checked_on"])
    return {"model": model, "catalog_version": PRICE_CATALOG["version"], "checked_on": PRICE_CATALOG["checked_on"],
            "source": PRICE_CATALOG["source"], "tier": PRICE_CATALOG["tier"], "context": PRICE_CATALOG["context"],
            "usd_per_mtok": dict(row), "applied_on": as_of.isoformat(),
            "stale": (as_of - checked).days > STALE_AFTER_DAYS}


def _estimate(usage: dict, rates: dict) -> float | None:
    counts = {}
    for rate, field in CODEX_FIELDS.items():
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        counts[rate] = value
    ordinary = counts["input"] - counts["cached_input"] - counts["cache_write"]
    if ordinary < 0:
        return None
    return (ordinary * rates["input"] + counts["cached_input"] * rates["cached_input"]
            + counts["cache_write"] * rates["cache_write"] + counts["output"] * rates["output"]) / 1_000_000


def classify_cost(*, engine: str | None, model: str | None, usage: dict | None, usage_known: bool,
                  cost_usd: Any, cost_known: bool | None, task_id: str, as_of: date | None = None) -> dict:
    """One task's cost as `actual` (reported), `estimated` (price table) or `unknown` (with a reason)."""
    base = {"task_id": task_id, "engine": engine or "unknown", "model": model}
    reported = _amount(cost_usd)
    if reported is not None and cost_known is not False:
        return {**base, "status": "actual", "usd": reported}
    if engine in FREE_ENGINES:
        return {**base, "status": "actual", "usd": 0.0}
    price = _price(engine, model, as_of or _today())
    if price is None:
        return {**base, "status": "unknown", "usd": None, "reason": "price_missing"}
    usd = _estimate(usage or {}, price["usd_per_mtok"]) if usage_known else None
    if usd is None:
        return {**base, "status": "unknown", "usd": None, "reason": "usage_incomplete"}
    return {**base, "status": "estimated", "usd": usd, "price": price}


def _engine_model(data: dict, agent: dict | None) -> tuple[str | None, str | None]:
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    runs = provenance.get("runs") if isinstance(provenance.get("runs"), dict) else {}
    run = runs.get(data.get("task_id")) if isinstance(runs.get(data.get("task_id")), dict) else {}
    agent = agent or {}
    # The task's own run first: a workdir's top-level engine/model is whoever made the folder.
    return (run.get("engine") or provenance.get("engine") or agent.get("engine"),
            run.get("model_id") or run.get("model") or provenance.get("model") or agent.get("model"))


def task_cost_item(result: Any, agent: dict | None = None, *, as_of: date | None = None,
                   model_tier: str | None = None) -> dict:
    """Classify a TaskResult (or its dump) with the engine and model the runner recorded for it."""
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
    engine, model = _engine_model(data, agent)
    item = classify_cost(engine=engine, model=model, usage=data.get("usage") or {},
                         usage_known=data.get("usage_known", True) is not False, cost_usd=data.get("cost_usd"),
                         cost_known=data.get("cost_known"), task_id=str(data.get("task_id") or ""), as_of=as_of)
    return _with_agent(item, data, agent, model_tier)


def outcome_unknown_item(result: Any, agent: dict | None = None) -> dict:
    """A task the gateway gave up on (runner generation changed, delivery uncertain): it may have spent anything."""
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
    engine, model = _engine_model(data, agent)
    return _with_agent({"task_id": str(data.get("task_id") or ""), "engine": engine or "unknown", "model": model,
                        "status": "unknown", "usd": None, "reason": "outcome_unknown"}, data, agent)


def _with_agent(item: dict, data: dict, agent: dict | None, model_tier: str | None = None) -> dict:
    """Name the staff member, so a request's cost can be read per agent (#57 ⑦ follow-up)."""
    agent_id = data.get("agent_id") or (agent or {}).get("id")
    item = {**item, "agent_id": str(agent_id)} if agent_id else item
    # A step run on its own tier (#625, step 2) is recorded at that tier, so a deliberate lowering is not a mismatch.
    tier = model_tier or (agent or {}).get("model_tier")
    return {**item, "model_tier": tier} if tier else item


def tier_mismatch_warnings(items: dict[str, dict], tiers: Any) -> list[str]:
    """One ``model_tier_mismatch:<agent>:<tier>:<model that ran>`` per agent whose declared tier names another model.

    Only when ``model_tiers`` says what the tier means on the engine that ran; an empty table, an unknown model, a task
    whose outcome the gateway never observed (``outcome_unknown``) or an
    item recorded before tiers were kept says nothing (#625)."""
    from .settings import models_match

    found: dict[tuple[str, str, str], None] = {}
    for item in items.values():
        if item.get("reason") == "outcome_unknown":  # nobody observed this task run: its model is only the dispatched one
            continue
        tier, model, agent = item.get("model_tier"), item.get("model"), item.get("agent_id")
        expected = tiers.model_for(tier, item.get("engine")) if tiers is not None else None
        if expected and model and agent and not models_match(expected, model):
            found[(str(agent), str(tier), str(model))] = None
    return [f"model_tier_mismatch:{agent}:{tier}:{model}" for agent, tier, model in found]


def aggregate_costs(items: dict[str, dict], tiers: Any = None) -> dict:
    """Request totals: reported and estimated dollars apart, unknown tasks counted, never added as $0."""
    actual = estimated = 0.0
    unknown: list[str] = []
    by_engine: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}
    warnings: list[str] = []
    prices: dict[tuple, dict] = {}
    for tid, item in items.items():
        engine = by_engine.setdefault(item.get("engine") or "unknown",
                                      {"actual_usd": 0.0, "estimated_usd": 0.0, "unknown_count": 0})
        staff = by_agent.setdefault(item.get("agent_id") or "unknown",
                                    {"actual_usd": 0.0, "estimated_usd": 0.0, "unknown_count": 0})
        status = item.get("status")
        if status == "actual":
            actual += float(item["usd"])
            engine["actual_usd"] += float(item["usd"])
            staff["actual_usd"] += float(item["usd"])
        elif status == "estimated":
            estimated += float(item["usd"])
            engine["estimated_usd"] += float(item["usd"])
            staff["estimated_usd"] += float(item["usd"])
            price = item.get("price") or {}
            key = (item.get("engine"), price.get("model"))
            prices.setdefault(key, {"engine": item.get("engine"), **{k: price.get(k) for k in (
                "model", "catalog_version", "checked_on", "source", "tier", "context", "stale")}})
            if price.get("stale"):
                prices[key]["stale"] = True
        else:
            unknown.append(tid)
            engine["unknown_count"] += 1
            staff["unknown_count"] += 1
    for (engine_name, model), price in sorted(prices.items(), key=lambda pair: tuple(map(str, pair[0]))):
        if price.get("stale"):
            warnings.append(f"price_stale:{engine_name}:{model}")
    warnings.extend(tier_mismatch_warnings(items, tiers))
    for row in (*by_engine.values(), *by_agent.values()):
        row["actual_usd"] = round(row["actual_usd"], 6)
        row["estimated_usd"] = round(row["estimated_usd"], 6)
    summary = {"actual_usd": round(actual, 6), "estimated_usd": round(estimated, 6),
               "subtotal_usd": round(actual + estimated, 6), "unknown_count": len(unknown),
               "unknown_tasks": unknown, "by_engine": dict(sorted(by_engine.items())),
               "prices": list(prices.values()), "warnings": warnings}
    # Items recorded before agent ids were kept leave the summary as it was.
    if any(item.get("agent_id") for item in items.values()):
        summary["by_agent"] = dict(sorted(by_agent.items()))
    return summary


def request_cost_summary(req: dict, tiers: Any = None) -> dict:
    """The request's summary, reading legacy numeric rows and new unknown ``None`` rows together."""
    items = dict(req.get("cost_items") or {})
    for tid, value in (req.get("cost_by_task") or {}).items():
        if tid in items:
            continue
        amount = _amount(value)
        items[tid] = ({"task_id": tid, "engine": "unknown", "model": None,
                       "status": "actual", "usd": amount}
                      if amount is not None else
                      {"task_id": tid, "engine": "unknown", "model": None,
                       "status": "unknown", "usd": None, "reason": "unreported"})
    return aggregate_costs(items, tiers)


def historical_plan_cost_estimate(requests: Any, plan: dict, agents: dict[str, dict], *,
                                  exclude_request_id: str | None = None) -> dict:
    """Estimate one planned request from completed requests with recorded ``cost_summary``.

    The stable fallback is the observed total per planned step. When task rows name employees and models, their
    per-step means plus shared orchestration overhead refine that baseline for the current assignments. One completed
    request is enough; without one the caller keeps the planner's existing estimate.
    """
    current_steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    current_topics = set(plan.get("topics") or [])
    current_kind = ((plan.get("intake") or {}).get("work_kind") if isinstance(plan.get("intake"), dict) else None)
    rows = requests.values() if isinstance(requests, dict) else requests
    comparable: list[tuple[dict, list[dict], float]] = []
    for request in rows or []:
        if not isinstance(request, dict) or request.get("id") == exclude_request_id or request.get("status") != "done":
            continue
        old_plan = request.get("plan") if isinstance(request.get("plan"), dict) else {}
        old_steps = [step for step in old_plan.get("steps") or [] if isinstance(step, dict)]
        summary = request.get("cost_summary") if isinstance(request.get("cost_summary"), dict) else {}
        subtotal = _amount(summary.get("subtotal_usd"))
        old_kind = ((old_plan.get("intake") or {}).get("work_kind")
                    if isinstance(old_plan.get("intake"), dict) else request.get("work_kind"))
        old_topics = set(old_plan.get("topics") or [])
        if (not old_steps or subtotal is None or subtotal <= 0 or summary.get("unknown_count") != 0 or
                (current_kind and old_kind and current_kind != old_kind) or
                (current_topics and current_topics.isdisjoint(old_topics))):
            continue
        comparable.append((request, old_steps, subtotal))

    if not comparable:
        return {"basis": "insufficient", "similar_requests": 0, "historical_steps": 0,
                "average_per_step_usd": None, "current_steps": len(current_steps),
                "worker_model_averages": []}

    total_cost = sum(row[2] for row in comparable)
    historical_steps = sum(len(row[1]) for row in comparable)
    overall_mean = total_cost / historical_steps
    signature_cost: dict[tuple[str, str, str], float] = {}
    signature_steps: dict[tuple[str, str, str], int] = {}
    agent_cost: dict[str, float] = {}
    agent_steps: dict[str, int] = {}
    worker_cost = overhead_cost = 0.0

    for request, old_steps, _subtotal in comparable:
        steps_by_id = {str(step.get("id")): step for step in old_steps if step.get("id")}
        steps_by_agent: dict[str, int] = {}
        for step in old_steps:
            agent_id = str(step.get("agent_id") or "")
            if agent_id:
                steps_by_agent[agent_id] = steps_by_agent.get(agent_id, 0) + 1
        step_costs: dict[tuple[str, tuple[str, str, str]], float] = {}
        for item in (request.get("cost_items") or {}).values():
            if not isinstance(item, dict) or item.get("status") not in {"actual", "estimated"}:
                continue
            usd = _amount(item.get("usd"))
            if usd is None:
                continue
            agent_id = str(item.get("agent_id") or "")
            step_id = str(item.get("step_id") or "")
            step = steps_by_id.get(step_id)
            if step is None or agent_id != str(step.get("agent_id") or ""):
                overhead_cost += usd
                continue
            signature = (agent_id, str(item.get("engine") or "unknown"), str(item.get("model") or "unknown"))
            key = (step_id, signature)
            step_costs[key] = step_costs.get(key, 0.0) + usd
        for (_step_id, signature), usd in step_costs.items():
            signature_cost[signature] = signature_cost.get(signature, 0.0) + usd
            agent_id = signature[0]
            agent_cost[agent_id] = agent_cost.get(agent_id, 0.0) + usd
            worker_cost += usd
            signature_steps[signature] = signature_steps.get(signature, 0) + 1
        for agent_id, count in steps_by_agent.items():
            agent_steps[agent_id] = agent_steps.get(agent_id, 0) + count

    averages = [{"agent_id": key[0], "engine": key[1], "model": key[2],
                 "average_usd": round(value / signature_steps[key], 6),
                 "historical_steps": signature_steps[key]}
                for key, value in sorted(signature_cost.items()) if signature_steps.get(key)]
    estimate = overall_mean * len(current_steps)
    if worker_cost and current_steps:
        shared_per_step = overhead_cost / historical_steps
        worker_per_step = worker_cost / historical_steps
        tailored = shared_per_step * len(current_steps)
        for step in current_steps:
            agent_id = str(step.get("agent_id") or "")
            agent = agents.get(agent_id) or {}
            signature = (agent_id, str(agent.get("engine") or "unknown"), str(agent.get("model") or "unknown"))
            if signature_steps.get(signature):
                tailored += signature_cost[signature] / signature_steps[signature]
            elif agent_steps.get(agent_id):
                tailored += agent_cost.get(agent_id, 0.0) / agent_steps[agent_id]
            else:
                tailored += worker_per_step
        estimate = tailored

    return {"basis": "historical", "estimate_usd": round(estimate, 2),
            "similar_requests": len(comparable), "historical_steps": historical_steps,
            "average_per_step_usd": round(overall_mean, 2), "current_steps": len(current_steps),
            "worker_model_averages": averages}


def request_cost_totals(requests: Iterable[dict]) -> dict:
    """Display totals across requests, including legacy records without task classifications."""
    return _request_cost_totals(requests)


def _request_cost_totals(requests: Iterable[dict], since: float | None = None) -> dict:
    cost = estimated = 0.0
    unknown = 0
    legacy_unknown = False
    for request in requests:
        if since is not None and float(request.get("created_at") or 0) < since:
            items = {
                tid: item for tid, item in (request.get("cost_items") or {}).items()
                if isinstance(item.get("recorded_at"), (int, float))
                and not isinstance(item.get("recorded_at"), bool)
                and float(item["recorded_at"]) >= since
            }
            summary = aggregate_costs(items)
            cost += float(summary.get("subtotal_usd") or 0)
            estimated += float(summary.get("estimated_usd") or 0)
            unknown += int(summary.get("unknown_count") or 0)
            continue
        cost += float(request.get("cost_usd") or 0)
        summary = request.get("cost_summary") or {}
        estimated += float(summary.get("estimated_usd") or 0)
        unknown += int(summary.get("unknown_count") or 0)
        legacy_unknown = legacy_unknown or (request.get("cost_known") is False and not summary)
    return {"cost_usd": round(cost, 6), "estimated_usd": round(estimated, 6),
            "unknown_count": unknown, "legacy_unknown": legacy_unknown}


def request_session_cost_totals(requests: Iterable[dict], since: float) -> dict:
    """Costs recorded in this gateway session; old legacy rows without timestamps stay outside it."""
    return _request_cost_totals(requests, since=since)


def _usd(value: Any, digits: int = 2) -> str:
    return f"${float(value or 0):.{digits}f}"


def _parts(actual: float, estimated: float, unknown: int, digits: int = 2) -> str:
    parts = []
    if actual > 0 or not (estimated > 0 or unknown):
        parts.append(f"확인 {_usd(actual, digits)}")
    if estimated > 0:
        parts.append(f"추정 {_usd(estimated, digits)}")
    if unknown:
        parts.append(f"비용 모름 {unknown}건")
    return " + ".join(parts)


def format_cost(summary: dict, digits: int = 2) -> str:
    """`확인 $0.30 + 추정 $0.20 + 비용 모름 1건`; zero parts are left out."""
    return _parts(float(summary.get("actual_usd") or 0), float(summary.get("estimated_usd") or 0),
                  int(summary.get("unknown_count") or 0), digits)


def format_engines(summary: dict, digits: int = 2) -> str:
    """`claude_code 확인 $0.30 · codex 추정 $0.20 + 비용 모름 1건`."""
    return " · ".join(f"{name} {format_cost(engine, digits)}"
                      for name, engine in (summary.get("by_engine") or {}).items())


def format_warnings(summary: dict) -> str:
    """`가격표 오래됨: codex gpt-6.1-sol (2026-10-02 확인)`, or empty."""
    parts = []
    stale = [p for p in summary.get("prices") or [] if p.get("stale")]
    if stale:
        parts.append("가격표 오래됨: " + ", ".join(f"{p.get('engine')} {p.get('model')} ({p.get('checked_on')} 확인)"
                                              for p in stale))
    mismatched = [w.split(":", 3)[1:] for w in summary.get("warnings") or [] if w.startswith("model_tier_mismatch:")]
    if mismatched:
        parts.append("선언 등급과 다른 모델: " + ", ".join(f"{agent}({tier}) → {model}"
                                                      for agent, tier, model in mismatched))
    return "; ".join(parts)


def cost_detail(summary: dict, digits: int = 2) -> str:
    """Engine subtotals, the estimate note and stale price warnings: `(codex 추정 $0.20); 추정은 ...`."""
    detail = f"({format_engines(summary, digits)})" if summary.get("by_engine") else ""
    if summary.get("estimated_usd"):
        detail += f"; {ESTIMATE_NOTE}"
    stale = format_warnings(summary)
    return detail + (f"; {stale}" if stale else "")


def cost_text(cost_usd: Any, cost_known: Any, summary: dict | None = None) -> str:
    """The display line: the summary when there is one, else the stored total with the unaccounted mark."""
    if summary:
        return format_cost(summary)
    known = float(cost_usd or 0)
    if cost_known is False:
        return f"{_usd(known) + ' + ' if known else ''}비용 모름"
    return _usd(known)
