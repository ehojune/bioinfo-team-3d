"""Repair JSON transport failures before orchestration interprets employee output."""
from __future__ import annotations

import json

from ..adapters.base import RunContext
from ..models import TaskResult

PARSE_ERROR = "could not be parsed as JSON"
FORMAT_ERROR = "structured_output_format"
RETRY_PROMPT = (
    "StructuredOutput 형식 오류입니다. 내용(경로·명령·정규식 포함)은 한 글자도 바꾸지 말고, "
    "JSON 문자열 규칙에 맞게 역슬래시를 \\\\로, 따옴표를 \\\"로 escape해서 같은 내용을 다시 "
    "제출하세요. 한글 등 비ASCII 문자를 \\u escape로 바꾸지 말고 원래 글자로 쓰세요. "
    "작업을 다시 실행하거나 PI에게 묻지 마세요. "
    "도구 오류 원문만 근거로 쓰고 확인하지 않은 원인을 지어내지 않는다."
)
COMPACT_PLAN_RETRY_PROMPT = (
    "StructuredOutput 계획 출력이 잘린 것으로 보입니다. 계획을 줄여 다시 제출하세요. "
    "단계 설명은 압축하고 반복 문구는 제거하며 큰 표는 요약하세요. 그래도 길면 계획을 독립된 "
    "묶음(갈래)으로 나누고 각 갈래 안의 세부 단계를 합쳐 요약하세요. 한글 등 비ASCII 문자를 "
    "\\u escape로 바꾸지 말고 원래 "
    "글자로 쓰세요. JSON schema는 지키고 작업을 다시 실행하거나 PI에게 묻지 마세요."
)


def _retry_budget(ctx: RunContext) -> float | None:
    """Total allowance for this runner turn, bounded by the request's remaining allowance."""
    task_budget = ctx.task.budget_usd or ctx.agent.max_budget_usd or ctx.settings.policy.budget.per_task_usd
    limits = [float(task_budget)] if task_budget and task_budget > 0 else []
    if ctx.task.request_budget_remaining_usd is not None:
        limits.append(float(ctx.task.request_budget_remaining_usd))
    return min(limits) if limits else None


def _plan_chars(ctx: RunContext, result: TaskResult) -> int | None:
    if ctx.task.meta.get("kind") not in {"plan", "replan"} or result.structured is None:
        return None
    return len(json.dumps(result.structured, ensure_ascii=False, separators=(",", ":"), default=str))


async def _record_plan_size(ctx: RunContext, result: TaskResult) -> None:
    chars = _plan_chars(ctx, result)
    limit = ctx.settings.orchestrator.plan_max_chars
    if chars is None or chars <= limit:
        return
    if ctx.record_run:
        ctx.record_run(plan_chars=chars, plan_size_limit_chars=limit, plan_size_warning=True)
    await ctx.emit("agent.log", {
        "level": "warn", "code": "plan_size_limit",
        "text": f"연구 계획 크기가 설정 상한을 넘었습니다: {chars}자 > {limit}자",
        "actual_chars": chars, "limit_chars": limit,
    })


async def run_with_json_retries(adapter, ctx: RunContext) -> TaskResult:
    """Each retry resumes the same workspace/session and has its own accounting baseline."""
    emit, record = ctx.emit, ctx.record_run
    baseline: dict = {}
    parse_error: str | None = None
    truncation_suspected = False
    costs: list[float | None] = []
    usage: dict[str, int] = {}
    usage_known = True
    errors: list[str] = []
    retry_budget = _retry_budget(ctx)

    async def observe(kind: str, data: dict) -> None:
        nonlocal parse_error, truncation_suspected
        if kind == "agent.tool_error":
            text = str(data.get("text") or "")
            if data.get("structured_output_parse_error") or ("StructuredOutput" in text and PARSE_ERROR in text):
                parse_error = text
                truncation_suspected = (data.get("structured_output_closed") is False
                                        or bool(data.get("structured_output_near_limit")))
        await emit(kind, data)

    def account(**fields) -> None:
        baseline.update(fields)
        if record:
            record(**fields)

    ctx.emit, ctx.record_run = observe, account
    try:
        for attempt in range(ctx.settings.runner.structured_output_retries + 1):
            parse_error = None
            truncation_suspected = False
            result = await adapter.run(ctx)
            if attempt == 0 and not parse_error:
                await _record_plan_size(ctx, result)
                return result
            costs.append(result.cost_usd if result.cost_known is not False else None)
            usage_known = usage_known and result.usage_known
            for key, value in result.usage.items():
                usage[key] = usage.get(key, 0) + value
            errors.extend(result.tool_errors)
            if parse_error:
                errors.append(parse_error)
            if (result.structured is not None or not parse_error or result.error_kind == "halted"
                    or (ctx.halt and ctx.halt.is_set())):
                break
            remaining = None
            if retry_budget is not None:
                remaining = (retry_budget - sum(value for value in costs if value is not None)
                             if all(value is not None for value in costs) else 0.0)
            if (attempt == ctx.settings.runner.structured_output_retries or not result.session_id
                    or (remaining is not None and remaining <= 0)):
                result = result.model_copy(update={
                    "ok": False, "structured": None, "text": "", "blocking_decision": None,
                    "error_kind": FORMAT_ERROR,
                    "error": "StructuredOutput 형식 오류: " + parse_error,
                })
                break
            await emit("agent.log", {"level": "info", "text": (
                f"StructuredOutput 형식 오류 자동 재시도 {attempt + 1}/{ctx.settings.runner.structured_output_retries}")})
            compact = truncation_suspected and ctx.task.meta.get("kind") in {"plan", "replan"}
            retry_prompt = COMPACT_PLAN_RETRY_PROMPT if compact else RETRY_PROMPT
            ctx.task = ctx.task.model_copy(update={"resume_session_id": result.session_id,
                                                   "prompt": retry_prompt, "context": "",
                                                   **({"budget_usd": remaining} if remaining is not None else {})})
            ctx.prompt, ctx.prompt_pointer = retry_prompt, None
            ctx.resume_baseline = dict(baseline)
        known = all(value is not None for value in costs)
        final = result.model_copy(update={
            "cost_usd": sum(value for value in costs if value is not None) if known else None,
            "cost_known": known, "usage": usage, "usage_known": usage_known,
            "tool_errors": list(dict.fromkeys(errors)),
        })
        await _record_plan_size(ctx, final)
        return final
    finally:
        ctx.emit, ctx.record_run = emit, record
