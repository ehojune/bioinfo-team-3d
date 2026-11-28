"""Build a failure report solely from the already recorded step results."""

import re
from pathlib import PurePosixPath

LABELS = {
    "budget_denied": "예산 증액이 승인되지 않아 멈췄습니다.",
    "budget_cap": "예산 상한에 도달해 멈췄습니다.",
    "error_max_turns": "단계의 턴 상한에 도달해 멈췄습니다.",
}
TASK_LIMITS = {
    "budget_denied": "budget_denied",
    "budget_cap": "budget_cap",
    "error_max_budget_usd": "budget_cap",
    "error_max_turns": "error_max_turns",
}


class TurnLimit(ValueError):
    def __init__(self, kind, error):
        super().__init__(error or LABELS[kind])
        self.kind = kind


def stop_on_limit(run_step):
    async def run(task):
        result = await run_step(task)
        if not result.ok and (kind := TASK_LIMITS.get(result.error_kind)):
            raise TurnLimit(kind, result.error)
        return result

    return run


def limit_kind(results, denial=None, error_kind=None):
    if denial:
        return "budget_denied"
    if error_kind in TASK_LIMITS:
        return TASK_LIMITS[error_kind]
    limits = {TASK_LIMITS.get(row.get("error_kind")) for row in results.values() if not row.get("ok")}
    return next((kind for kind in LABELS if kind in limits), None)


def has_completed_outputs(results):
    return any(row.get("ok") and row.get("outputs") for row in results.values())


def saved_review_limit(review):
    status = review.get("status") if isinstance(review, dict) else None
    return status if isinstance(status, str) and status in LABELS else None


def _code(value):
    value = " ".join(str(value).split())
    fence = "`" * (1 + max(map(len, re.findall(r"`+", value)), default=0))
    return f"{fence} {value} {fence}"


def _outputs(step_id, result):
    paths = []
    owner = str(result.get("workdir_id") or step_id)
    if not re.fullmatch(r"[\w.-]+", owner) or owner in {".", ".."}:
        owner = step_id
    digests = result.get("output_sha256") or {}
    for raw in result.get("outputs") or []:
        relative = PurePosixPath(str(raw).replace("\\", "/"))
        if not relative.parts or relative.parts[0] != "outputs" or ".." in relative.parts:
            continue
        digest = digests.get(raw)
        source = _code(f"{owner}/{relative.as_posix()}")
        if digest and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            source += f" (SHA-256 {_code(digest)})"
        paths.append(f"  - {source}")
    if len(paths) > 5:
        return [*paths[:5], f"  - 그 외 {len(paths) - 5}개는 실행 기록에 있습니다."]
    return paths


def committed(request):
    return request.get("status") == "failed" and bool(request.get("partial_closeout"))


async def publish_once(request_id, current, inflight, publish):
    """Use the normal terminal transaction once, and allow a retry if it fails before commit."""
    if request_id in inflight or committed(current):
        return
    inflight.add(request_id)
    try:
        await publish()
    finally:
        inflight.discard(request_id)


def prepare(request, results, kind):
    """Mark the terminal candidate and compose its report before the normal commit."""
    request.update(status="failed", partial_closeout=kind)
    if request.get("research_contract"):
        request["outcome"] = "research_failed"
    completed = []
    missing = []
    steps = {step["id"]: step for step in (request.get("plan") or {}).get("steps") or []}
    for step_id in dict.fromkeys([*steps, *results]):
        row = results.get(step_id) or {}
        label = _code(step_id)
        if row.get("ok"):
            completed.extend([f"- {label}", *(_outputs(step_id, row) or ["  - 수집 산출물 없음"])])
        else:
            status = {"skipped": "건너뜀", "incomplete": "불완전", "failed": "실패"}.get(row.get("status"))
            missing.append(f"- {label} · {status or ('실패' if row else '실행 기록 없음')}")
        expected = steps.get(step_id, {}).get("outputs") or []
        absent = row.get("missing_outputs") or ([path for path in expected if path not in (row.get("outputs") or [])]
                                                if not row.get("ok") else [])
        if absent:
            missing.append(f"- {label} · 필수 산출물 누락: " + ", ".join(map(_code, absent)))
        if optional := row.get("missing_optional_outputs"):
            missing.append(f"- {label} · 선택 산출물 누락: " + ", ".join(map(_code, optional)))
    return "\n".join([
        "## 결론과 권고", LABELS[kind] + " 완료한 단계의 산출물을 부분 보고서에 남겼습니다.",
        "", "## 결과", *completed,
        "", "## 방법 요약", "저장된 단계 결과와 산출물 기록을 모았습니다. 추가 모델 호출 없이 작성했습니다.",
        "", "## 한계", "요청은 실패 상태입니다. 남은 단계와 최종 결론은 확정되지 않았습니다.",
        "종료 사유와 단계별 오류는 실행 기록에 있습니다.",
        *(missing or ["- 미완료 단계 기록 없음."]),
    ])
