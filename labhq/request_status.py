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


def request_display_label(request: dict) -> str:
    if request.get("status") == "cancelled" and request.get("cancel_reason") == "request_deadline":
        return "시간 상한으로 취소됨"  # the request's wall-clock limit (#877), not a PI decision
    if request.get("pi_review_pending"):
        return "보고서 검토 대기" if request.get("status") in {"done", "review_pending"} else "결과 확인 대기"
    return request_status_label(request.get("status"))


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
