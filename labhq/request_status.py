"""Shared request lifecycle classification."""

ACTIVE_REQUEST_STATES = frozenset({"running", "waiting_for_runner", "waiting_quota", "waiting_login",
                                   "waiting_facilities_fix", "waiting_pi"})
TERMINAL_REQUEST_STATES = frozenset({"done", "failed", "cancelled", "rejected", "review_pending"})
TERMINAL_FOLLOWUP_STATES = frozenset({"done", "failed", "interrupted"})
# What `labhq status` and `labhq watch` print for each request status.
REQUEST_STATUS_KO = {"running": "진행 중", "waiting_for_runner": "러너 기다림", "waiting_quota": "한도 대기",
                     "waiting_login": "로그인 대기", "waiting_facilities_fix": "환경 수정 승인 대기",
                     "waiting_pi": "PI 결정 대기", "interrupted": "중단됨", "done": "완료", "failed": "실패",
                     "cancelled": "취소됨", "rejected": "거부됨", "review_pending": "보고서 검토 대기"}


def request_status_label(status: object) -> str:
    return REQUEST_STATUS_KO.get(str(status), str(status or "상태 모름"))


def failed_step_ids(request: dict) -> list[str]:
    """The steps of a request that finished `done` but left a failed or skipped result (#1220).

    Derived from the results the request already holds and never stored: a summary that lists them (`failed_steps`) is
    used as it is. Only a step that recorded a result can be counted. The plan minus the results is not read as "never
    ran": plan-only, a solo route that keeps the team plan and a plan approved without the evidence checkpoint all end
    `done` with plan steps that were never meant to run. Any other status has none, since `failed` and `cancelled` already
    say that something did not complete."""
    if request.get("status") not in {"done", "review_pending"}:
        return []
    listed = request.get("failed_steps")
    if isinstance(listed, list):
        return [str(step) for step in listed]
    results = request.get("results") if isinstance(request.get("results"), dict) else {}
    planned = [str(step["id"]) for step in (request.get("plan") or {}).get("steps") or []
               if isinstance(step, dict) and step.get("id")]
    ordered = [step for step in planned if step in results] + [str(step) for step in results if str(step) not in planned]
    return [step for step in ordered
            if not isinstance(results.get(step), dict) or results[step].get("ok") is not True
            or results[step].get("status") == "skipped"]


def request_display_label(request: dict) -> str:
    if request.get("status") == "cancelled" and request.get("cancel_reason") == "request_deadline":
        return "시간 상한으로 취소됨"  # the request's wall-clock limit (#877), not a PI decision
    failed = failed_step_ids(request)
    suffix = f" · 일부 실패 {len(failed)}" if failed else ""  # done, but not everything finished (#1220)
    if request.get("pi_review_pending"):
        return ("보고서 검토 대기" if request.get("status") in {"done", "review_pending"} else "결과 확인 대기") + suffix
    if failed:
        return f"{request_status_label('done')}{suffix}"
    return request_status_label(request.get("status"))


def _span(seconds: float) -> str:
    return f"{int(seconds)}초" if seconds < 120 else f"{round(seconds / 60)}분"


def request_deadline_text(request: dict, now: float) -> str:
    """What `labhq status` says about a request's wall-clock limit (#877): off, or on with the time left and the PI
    waiting time that is not counted. A request accepted before the limit existed has none, so it reads "꺼짐"."""
    clock = request.get("wall_clock")
    if not isinstance(clock, dict) or not clock.get("limit_s"):
        return "꺼짐"
    waiting = request.get("status") == "waiting_pi"
    since = clock.get("pi_since")
    open_wait = max(0.0, now - float(since)) if waiting and since is not None else 0.0
    held = float(clock.get("held_s") or 0.0) + open_wait
    left = float(request.get("created_at") or now) + float(clock["limit_s"]) + held - now
    text = f"켜짐 {_span(float(clock['limit_s']))} · " + (f"남은 {_span(left)}" if left > 0 else "기한이 지났습니다(곧 취소)")
    if held > 0:
        text += f" · PI 대기 제외 {_span(held)}"
    if waiting:
        text += " · PI 결정 대기 중이라 시간이 가지 않습니다"
    return text


def is_active_request(status: object) -> bool:
    return status in ACTIVE_REQUEST_STATES


def is_terminal_request(status: object) -> bool:
    return status in TERMINAL_REQUEST_STATES


def is_active_followup(followup: dict) -> bool:
    """Unknown and future non-terminal states still mean that the follow-up occupies the request."""
    return followup.get("status") not in TERMINAL_FOLLOWUP_STATES


def has_active_followup(request: dict) -> bool:
    return any(is_active_followup(followup) for followup in request.get("followups") or [])


def is_past_request(request: dict) -> bool:
    """A terminal request leaves the office only after the PI acknowledged it."""
    return (is_terminal_request(request.get("status")) and bool(request.get("pi_reviewed_at"))
            and not has_active_followup(request))


def is_office_request(request: dict) -> bool:
    return not is_past_request(request)
