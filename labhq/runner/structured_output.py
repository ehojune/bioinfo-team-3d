"""Repair JSON transport failures before orchestration interprets employee output."""
from __future__ import annotations

from ..adapters.base import RunContext
from ..models import TaskResult

PARSE_ERROR = "could not be parsed as JSON"
FORMAT_ERROR = "structured_output_format"
RETRY_PROMPT = (
    "StructuredOutput 형식 오류입니다. 경로는 /로 쓰고 문자열 안 역슬래시·따옴표를 JSON 규칙에 맞게 "
    "이스케이프해 같은 내용을 다시 제출하세요. 작업을 다시 실행하거나 PI에게 묻지 마세요. "
    "도구 오류 원문만 근거로 쓰고 확인하지 않은 원인을 지어내지 않는다."
)


async def run_with_json_retries(adapter, ctx: RunContext) -> TaskResult:
    """Each retry resumes the same workspace/session and has its own accounting baseline."""
    emit, record = ctx.emit, ctx.record_run
    baseline: dict = {}
    parse_error: str | None = None
    costs: list[float | None] = []
    usage: dict[str, int] = {}
    usage_known = True
    errors: list[str] = []

    async def observe(kind: str, data: dict) -> None:
        nonlocal parse_error
        if kind == "agent.tool_error":
            text = str(data.get("text") or "")
            if data.get("structured_output_parse_error") or ("StructuredOutput" in text and PARSE_ERROR in text):
                parse_error = text
        await emit(kind, data)

    def account(**fields) -> None:
        baseline.update(fields)
        if record:
            record(**fields)

    ctx.emit, ctx.record_run = observe, account
    try:
        for attempt in range(ctx.settings.runner.structured_output_retries + 1):
            parse_error = None
            result = await adapter.run(ctx)
            if attempt == 0 and not parse_error:
                return result
            costs.append(result.cost_usd if result.cost_known is not False else None)
            usage_known = usage_known and result.usage_known
            for key, value in result.usage.items():
                usage[key] = usage.get(key, 0) + value
            errors.extend(result.tool_errors)
            if not parse_error or result.error_kind == "halted" or (ctx.halt and ctx.halt.is_set()):
                break
            errors.append(parse_error)
            if attempt == ctx.settings.runner.structured_output_retries or not result.session_id:
                result = result.model_copy(update={
                    "ok": False, "structured": None, "text": "", "blocking_decision": None,
                    "error_kind": FORMAT_ERROR,
                    "error": "StructuredOutput 형식 오류: " + parse_error,
                })
                break
            await emit("agent.log", {"level": "info", "text": (
                f"StructuredOutput 형식 오류 자동 재시도 {attempt + 1}/{ctx.settings.runner.structured_output_retries}")})
            ctx.task = ctx.task.model_copy(update={"resume_session_id": result.session_id,
                                                   "prompt": RETRY_PROMPT, "context": ""})
            ctx.prompt, ctx.prompt_pointer = RETRY_PROMPT, None
            ctx.resume_baseline = dict(baseline)
        known = all(value is not None for value in costs)
        return result.model_copy(update={
            "cost_usd": sum(value for value in costs if value is not None) if known else None,
            "cost_known": known, "usage": usage, "usage_known": usage_known,
            "tool_errors": list(dict.fromkeys(errors)),
        })
    finally:
        ctx.emit, ctx.record_run = emit, record
