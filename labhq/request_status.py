"""Shared request lifecycle classification."""

ACTIVE_REQUEST_STATES = frozenset({"running", "waiting_for_runner", "waiting_quota", "waiting_login"})
TERMINAL_REQUEST_STATES = frozenset({"done", "failed", "cancelled", "rejected"})


def is_active_request(status: object) -> bool:
    return status in ACTIVE_REQUEST_STATES


def is_terminal_request(status: object) -> bool:
    return status in TERMINAL_REQUEST_STATES
