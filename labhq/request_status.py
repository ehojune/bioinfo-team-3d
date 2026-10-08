"""Shared request lifecycle classification."""

ACTIVE_REQUEST_STATES = frozenset({"running", "waiting_for_runner", "waiting_quota", "waiting_login",
                                   "waiting_facilities_fix", "waiting_pi"})
TERMINAL_REQUEST_STATES = frozenset({"done", "failed", "cancelled", "rejected"})
# What `labhq status` and `labhq watch` print for each request status.
REQUEST_STATUS_KO = {"running": "진행 중", "waiting_for_runner": "러너 기다림", "waiting_quota": "한도 대기",
                     "waiting_login": "로그인 대기", "waiting_facilities_fix": "환경 수정 승인 대기",
                     "waiting_pi": "PI 결정 대기", "interrupted": "중단됨", "done": "완료", "failed": "실패",
                     "cancelled": "취소됨", "rejected": "거부됨"}


def request_status_label(status: object) -> str:
    return REQUEST_STATUS_KO.get(str(status), str(status or "상태 모름"))


def is_active_request(status: object) -> bool:
    return status in ACTIVE_REQUEST_STATES


def is_terminal_request(status: object) -> bool:
    return status in TERMINAL_REQUEST_STATES
