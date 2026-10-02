"""Task cost accounting: reported, estimated from a dated price table, or unaccounted (#270).

A task whose engine reports no dollar amount is never counted as $0. Its tokens are converted only with a
price row for that exact model id and only when every token count the row bills was reported; otherwise the
task stays unaccounted and is listed apart from the subtotal. Estimates are list prices, not a bill: Codex
staff usually run on a ChatGPT plan.
"""
from __future__ import annotations

import math
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


def task_cost_item(result: Any, agent: dict | None = None, *, as_of: date | None = None) -> dict:
    """Classify a TaskResult (or its dump) with the engine and model the runner recorded for it."""
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
    engine, model = _engine_model(data, agent)
    return classify_cost(engine=engine, model=model, usage=data.get("usage") or {},
                         usage_known=data.get("usage_known", True) is not False, cost_usd=data.get("cost_usd"),
                         cost_known=data.get("cost_known"), task_id=str(data.get("task_id") or ""), as_of=as_of)


def outcome_unknown_item(result: Any, agent: dict | None = None) -> dict:
    """A task the gateway gave up on (runner generation changed, delivery uncertain): it may have spent anything."""
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
    engine, model = _engine_model(data, agent)
    return {"task_id": str(data.get("task_id") or ""), "engine": engine or "unknown", "model": model,
            "status": "unknown", "usd": None, "reason": "outcome_unknown"}


def aggregate_costs(items: dict[str, dict]) -> dict:
    """Request totals: reported and estimated dollars apart, unknown tasks counted, never added as $0."""
    actual = estimated = 0.0
    unknown: list[str] = []
    by_engine: dict[str, dict] = {}
    warnings: list[str] = []
    prices: dict[tuple, dict] = {}
    for tid, item in items.items():
        engine = by_engine.setdefault(item.get("engine") or "unknown",
                                      {"actual_usd": 0.0, "estimated_usd": 0.0, "unknown_count": 0})
        status = item.get("status")
        if status == "actual":
            actual += float(item["usd"])
            engine["actual_usd"] += float(item["usd"])
        elif status == "estimated":
            estimated += float(item["usd"])
            engine["estimated_usd"] += float(item["usd"])
            price = item.get("price") or {}
            key = (item.get("engine"), price.get("model"))
            prices.setdefault(key, {"engine": item.get("engine"), **{k: price.get(k) for k in (
                "model", "catalog_version", "checked_on", "source", "tier", "context", "stale")}})
            if price.get("stale"):
                prices[key]["stale"] = True
        else:
            unknown.append(tid)
            engine["unknown_count"] += 1
    for (engine_name, model), price in sorted(prices.items(), key=lambda pair: tuple(map(str, pair[0]))):
        if price.get("stale"):
            warnings.append(f"price_stale:{engine_name}:{model}")
    for engine in by_engine.values():
        engine["actual_usd"] = round(engine["actual_usd"], 6)
        engine["estimated_usd"] = round(engine["estimated_usd"], 6)
    return {"actual_usd": round(actual, 6), "estimated_usd": round(estimated, 6),
            "subtotal_usd": round(actual + estimated, 6), "unknown_count": len(unknown),
            "unknown_tasks": unknown, "by_engine": dict(sorted(by_engine.items())),
            "prices": list(prices.values()), "warnings": warnings}


def request_cost_summary(req: dict) -> dict | None:
    """The request's summary, or None while an older record has tasks counted without a classification."""
    items = req.get("cost_items") or {}
    if set(req.get("cost_by_task") or {}) - set(items):
        return None
    return aggregate_costs(items)


def _usd(value: Any, digits: int = 2) -> str:
    return f"${float(value or 0):.{digits}f}"


def _parts(actual: float, estimated: float, unknown: int, digits: int = 2) -> str:
    parts = []
    if actual > 0 or not (estimated > 0 or unknown):
        parts.append(f"확인 {_usd(actual, digits)}")
    if estimated > 0:
        parts.append(f"추정 {_usd(estimated, digits)}")
    if unknown:
        parts.append(f"미집계 {unknown}건")
    return " + ".join(parts)


def format_cost(summary: dict, digits: int = 2) -> str:
    """`확인 $0.30 + 추정 $0.20 + 미집계 1건`; zero parts are left out."""
    return _parts(float(summary.get("actual_usd") or 0), float(summary.get("estimated_usd") or 0),
                  int(summary.get("unknown_count") or 0), digits)


def format_engines(summary: dict, digits: int = 2) -> str:
    """`claude_code 확인 $0.30 · codex 추정 $0.20 + 미집계 1건`."""
    return " · ".join(f"{name} {format_cost(engine, digits)}"
                      for name, engine in (summary.get("by_engine") or {}).items())


def format_warnings(summary: dict) -> str:
    """`가격표 오래됨: codex gpt-6.1-sol (2026-10-02 확인)`, or empty."""
    stale = [p for p in summary.get("prices") or [] if p.get("stale")]
    if not stale:
        return ""
    return "가격표 오래됨: " + ", ".join(f"{p.get('engine')} {p.get('model')} ({p.get('checked_on')} 확인)"
                                     for p in stale)


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
        return f"{_usd(known) + ' + ' if known else ''}비용 미집계"
    return _usd(known)
