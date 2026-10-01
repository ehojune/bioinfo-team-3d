"""Action layer of the object view, shadow stage A1 (#149 결정 13): which existing actions were possible, never
taking one.

``semantics_objects.py`` names the lab's things. This module names the existing actions on them (decide an
approval, answer a question, follow up, cancel a task, recruit, change a contract, submit to HPC) with their
preconditions, and evaluates them at fixed observation points: when a request ends (the #150 B1 hook) and when a
follow-up is asked, refused or ends. What leaves the module is fixed names, booleans and counts on the B1 line
boundary; texts handed in go to the boundary check only.

Nothing here executes an action. The built-in execution allowlist is empty and no setting widens it, an HPC
action is always ``refused_p3`` until the P3 decision, and the module imports no gateway, orchestrator, runner,
store, network or process code (tests check the imports). Every evaluation reads a frozen copy. A precondition
the records cannot show for that moment stays unknown: it is never filled from the current roster or a later row.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Any

V = 1
ACTIONS = ("approval.decide", "ask.answer", "request.followup", "task.cancel", "recruit.start",
           "contract.update", "hpc.submit")
EXECUTABLE: frozenset[str] = frozenset()  # A1 runs nothing (sol·astra review 2026-10-02); A2 is its own PR
CONDITIONS: dict[str, tuple[str, ...]] = {
    "approval.decide": ("pending", "not_expired", "request_open", "plan_hash_matches"),
    "ask.answer": ("pending", "not_expired", "request_open", "ask_open"),
    "request.followup": ("request_terminal", "no_running_followup", "responder_on_roster", "responder_read_only"),
    "task.cancel": ("request_running", "task_running", "no_pending_jobs"),
    "recruit.start": ("recruiter_on_roster", "paper_or_repo"),
    "contract.update": ("contract_staff", "runner_on_roster"),
    "hpc.submit": (),  # never evaluated: refused_p3
}
EVALUATED = tuple(a for a in ACTIONS if CONDITIONS[a])
WINDOWED = ("approval.decide", "ask.answer", "request.followup", "task.cancel", "recruit.start", "hpc.submit")
CHECKED = ("approval.decide", "ask.answer", "task.cancel")  # taken_while_blocked from recorded times
BY_KIND = ("approval.decide", "ask.answer", "hpc.submit")  # windows also counted by approval kind
APPROVAL_KINDS = ("budget", "clarify", "download", "hpc_submit", "question", "recruit", "research_plan", "resume",
                  "tool_permission", "other")
UNTIMED_KINDS = frozenset({"resume"})  # a resume approval waits for the PI without a timer
TERMINAL = frozenset({"done", "failed", "cancelled", "rejected"})
PHASES = ("asked", "refused", "ended")
OUTCOMES = ("done", "failed", "refused_read_only")
MISMATCHES = ("taken_while_blocked", "refused_while_open")
EXEC_STATES = ("shadow_only", "refused_p3")
WORDS = frozenset({"followup", "refused", "refused_read_only", *EXEC_STATES})  # values the B1 check takes as code's
TALLY_KEYS = ("n", "open", "blocked", "unknown", "blocked_by", "unknown_by")
WINDOW_KEYS = ("windows", "closed", "unbounded", "taken", "taken_unknown", "lengths_s", "lengths_truncated",
               "by_kind")
MISMATCH_KEYS = ("checked", "taken_while_blocked", "unknown")
SECTION_KEYS = ("v", "status", "ms", "error_kind", "exec", "now", "past", "mismatch")
FOLLOWUP_KEYS = ("v", "type", "ts", "epoch", "request_id", "phase", "key", "status", "error_kind", "ms", "outcome",
                 "conditions", "verdict_open", "mismatch", "has_task", "wait_s", "busy_skipped")
STATUSES = ("ok", "timeout", "error")
MAX_LENGTHS = 100
_HEX16 = frozenset("0123456789abcdef")
# ids and digests a line may carry anyway (a request id, a hash): never boundary texts, or an approval detail that
# names its own request would turn the shadow off
_ID_LIKE = re.compile(r"(?:req|task|appr|ask|fu|job)_[A-Za-z0-9]{1,64}|[0-9a-f]{16,64}")


# ---------------------------------------------------------------- small pieces

def execution_refusal(name: str) -> str:
    """Why this stage does not run ``name``. Always a refusal: HPC first, so it stays refused_p3 whatever else
    changes, and everything else is shadow-only because EXECUTABLE is empty and no setting adds to it."""
    if not isinstance(name, str) or name.startswith("hpc."):
        return "refused_p3"
    return "shadow_only"


def freeze(value: Any) -> Any:
    """A read-only copy: mappings become MappingProxyType, lists tuples. A write raises TypeError."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _all(values: Iterable[bool | None]) -> bool | None:
    """Three-valued AND: False wins, then unknown."""
    values = list(values)
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


def _texts(value: Any) -> Iterable[str]:
    """The free texts in a value, for the boundary check only."""
    if isinstance(value, str):
        if not _ID_LIKE.fullmatch(value.strip()):
            yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _texts(v)


def followup_key(rid: Any, fid: Any) -> str:
    """One follow-up across its asked and ended lines, without its id or text."""
    return hashlib.sha256(f"followup\x1f{rid}\x1f{fid}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- inputs (plain copies, taken by the caller)

def _responder(agent: Any, agents: Mapping[str, Any], read_only: Callable[[Any], bool]) -> dict:
    entry = agents.get(agent) if isinstance(agent, str) else None
    if not isinstance(entry, Mapping):
        return {"on_roster": False, "read_only": None}
    try:
        ro: bool | None = bool(read_only(entry.get("engine")))
    except Exception:  # noqa: BLE001 - unknown, never a guess
        ro = None
    return {"on_roster": True, "read_only": ro}


def approval_row(approval: Mapping[str, Any], decision: Mapping[str, Any] | None,
                 plan_sha256: Any = None) -> dict:
    """Kind, times and state of one approval. The target hash is compared only for a pending one: a decided
    approval's plan may have changed since, and the current hash is not the one it was decided on."""
    kind = approval.get("kind") if approval.get("kind") in APPROVAL_KINDS else "other"
    row: dict[str, Any] = {"kind": kind, "created_at": approval.get("created_at"),
                           "timeout_s": approval.get("timeout_s")}
    if decision is None:
        row["state"] = "pending"
        if kind == "research_plan":
            detail = approval.get("detail") if isinstance(approval.get("detail"), Mapping) else {}
            target = detail.get("target_sha256")
            row["plan_hash_matches"] = (target == plan_sha256 if isinstance(target, str)
                                        and isinstance(plan_sha256, str) else None)
    else:
        state = decision.get("state")
        row["state"] = state if state in ("timed_out", "expired") else "decided" if "approved" in decision else None
        row["decided_at"] = decision.get("decided_at")
    return row


def request_inputs(req: Mapping[str, Any], approvals: Mapping[str, Any], agents: Mapping[str, Any],
                   hosts: Mapping[str, Any], *, cso_agent: str, recruiter: str,
                   read_only: Callable[[Any], bool], now: float) -> dict:
    """What the terminal observation needs from memory: states, times and booleans. ``sensitive`` carries the
    texts only for the boundary check; nothing reads it into a line."""
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    contract = req.get("research_contract") if isinstance(req.get("research_contract"), Mapping) else {}
    followups = [f for f in req.get("followups") or [] if isinstance(f, Mapping)]
    recruit = [r for r in plan.get("recruit") or [] if isinstance(r, Mapping)]
    responder = req.get("agent_id") if req.get("mode") == "direct" else cso_agent
    pending, sensitive = [], []
    for _, entry in sorted(approvals.items()):
        approval = entry.get("approval") if isinstance(entry, Mapping) else None
        if isinstance(approval, Mapping) and approval.get("request_id") == req.get("id"):
            pending.append(approval_row(approval, None, contract.get("plan_sha256")))
            sensitive += _texts([approval.get("summary"), approval.get("detail")])
    for f in followups:
        sensitive += _texts([f.get("text"), f.get("answer"), f.get("error")])
    sensitive += _texts(recruit)
    return {
        "observed_at": now,
        "request": {"status": req.get("status"), "created_at": req.get("created_at"),
                    "finished_at": req.get("finished_at")},
        "followups": [{"status": f.get("status"), "asked_at": f.get("asked_at"), "answered_at": f.get("answered_at"),
                       "has_task": bool(f.get("task_id"))} for f in followups],
        "responder": _responder(responder, agents, read_only),
        "recruit": [{"paper_or_repo": bool(r.get("paper") or r.get("repo"))} for r in recruit],
        "recruiter_on_roster": recruiter in agents,
        "contract_staff": [{"runner_on_roster": aid in hosts or recruiter in hosts}
                           for aid, a in sorted(agents.items())
                           if isinstance(a, Mapping) and a.get("employment") == "contract"],
        "pending": pending,
        "sensitive": sensitive,
    }


def row_inputs(tasks: Mapping[str, Any], decisions: Mapping[str, Any]) -> dict:
    """This request's task and decision rows, read by the shadow worker: states and times only."""
    rows, sensitive = [], []
    for _, task in sorted(tasks.items()):
        if not isinstance(task, Mapping):
            continue
        result = task.get("result") if isinstance(task.get("result"), Mapping) else None
        completed = bool(task.get("completed")) and result is not None
        jobs = result.get("pending_jobs") if result else None
        rows.append({"accepted": bool(task.get("accepted")), "completed": completed,
                     "dispatched_at": task.get("dispatched_at"),
                     "cancelled": (result.get("error") == "cancelled") if completed else None,
                     "pending_jobs": len(jobs) if isinstance(jobs, list) else None})
    decided = []
    for _, decision in sorted(decisions.items()):
        if isinstance(decision, Mapping) and isinstance(decision.get("approval"), Mapping):
            decided.append(approval_row(decision["approval"], decision))
            sensitive += _texts([decision["approval"].get("summary"), decision["approval"].get("detail"),
                                 decision.get("note")])
    return {"tasks": rows, "decided": decided, "sensitive": sensitive}


def followup_inputs(req: Mapping[str, Any], fid: Any, phase: str, outcome: Any, agents: Mapping[str, Any], *,
                    cso_agent: str, read_only: Callable[[Any], bool], now: float) -> dict:
    """One follow-up observation. ``asked``: the server just added the entry, so the entry is not a precondition
    of itself; ``refused``: the server refused and added nothing; ``ended``: the follow-up has its outcome."""
    followups = [f for f in req.get("followups") or [] if isinstance(f, Mapping)]
    entry = next((f for f in followups if fid is not None and f.get("id") == fid), None)
    others = [f for f in followups if f is not entry]
    if entry is not None and entry.get("agent_id"):
        responder = entry.get("agent_id")
    else:
        responder = req.get("agent_id") if req.get("mode") == "direct" else cso_agent
    sensitive = list(_texts(req.get("text")))
    for f in followups:
        sensitive += _texts([f.get("text"), f.get("answer"), f.get("error")])
    return {
        "observed_at": now, "phase": phase if phase in PHASES else None,
        "outcome": outcome if outcome in OUTCOMES else None,
        "key": followup_key(req.get("id"), fid) if fid is not None else None,
        "request": {"status": req.get("status"), "finished_at": req.get("finished_at")},
        "others_running": any(f.get("status") == "running" for f in others),
        "responder": _responder(responder, agents, read_only),
        "entry": None if entry is None else {"asked_at": entry.get("asked_at"), "answered_at": entry.get("answered_at"),
                                             "has_task": bool(entry.get("task_id"))},
        "sensitive": sensitive,
    }


# ---------------------------------------------------------------- evaluation

def _tally(action: str) -> dict:
    return {"n": 0, "open": 0, "blocked": 0, "unknown": 0, "blocked_by": {c: 0 for c in CONDITIONS[action]},
            "unknown_by": {c: 0 for c in CONDITIONS[action]}}


def _windows(action: str) -> dict:
    out: dict[str, Any] = {"windows": 0, "closed": 0, "unbounded": 0, "taken": 0, "taken_unknown": 0,
                           "lengths_s": [], "lengths_truncated": False}
    if action in BY_KIND:
        out["by_kind"] = {k: 0 for k in APPROVAL_KINDS}
    return out


def _count(tally: dict, conds: Mapping[str, bool | None]) -> None:
    verdict = _all(conds.values())
    tally["n"] += 1
    tally["open" if verdict else "blocked" if verdict is False else "unknown"] += 1
    for name, value in conds.items():
        if value is False:
            tally["blocked_by"][name] += 1
        elif value is None:
            tally["unknown_by"][name] += 1


def _window(win: dict, start: Any, end: Any, taken: bool | None, kind: str | None = None) -> None:
    win["windows"] += 1
    if kind is not None:
        win["by_kind"][kind] += 1
    s, e = _num(start), _num(end)
    if s is None or e is None or e < s:
        win["unbounded"] += 1
    else:
        win["closed"] += 1
        if len(win["lengths_s"]) < MAX_LENGTHS:
            win["lengths_s"].append(round(e - s, 1))
        else:
            win["lengths_truncated"] = True
    if taken is None:
        win["taken_unknown"] += 1
    elif taken:
        win["taken"] += 1


def _check(mismatch: dict, verdict: bool | None) -> None:
    mismatch["checked"] += 1
    if verdict is False:
        mismatch["taken_while_blocked"] += 1
    elif verdict is None:
        mismatch["unknown"] += 1


def followup_conditions(status: Any, others_running: Any, responder: Mapping[str, Any]) -> dict:
    """The server's own checks in ``start_followup``, in its order, as three-valued facts."""
    return {"request_terminal": status in TERMINAL if isinstance(status, str) else None,
            "no_running_followup": (not others_running) if isinstance(others_running, bool) else None,
            "responder_on_roster": responder.get("on_roster") if isinstance(responder.get("on_roster"), bool) else None,
            "responder_read_only": responder.get("read_only") if isinstance(responder.get("read_only"), bool) else None}


def _approval_at(row: Mapping[str, Any], when: float | None, finished: float | None) -> dict:
    """Preconditions of deciding ``row`` at ``when``, from recorded times only."""
    created, timeout = _num(row.get("created_at")), _num(row.get("timeout_s"))
    if row.get("kind") in UNTIMED_KINDS:
        not_expired: bool | None = True
    else:
        not_expired = when <= created + timeout if None not in (when, created, timeout) else None
    request_open = when < finished if when is not None and finished is not None else None
    return {"not_expired": not_expired, "request_open": request_open}


def evaluate(inputs: Mapping[str, Any], check: Callable[[], None]) -> dict:
    """The terminal observation: preconditions now, recorded windows, and decisions taken while blocked."""
    a = freeze(inputs)
    now_t = _num(a.get("observed_at"))
    req = a.get("request") or {}
    status = req.get("status")
    terminal = status in TERMINAL if isinstance(status, str) else None
    finished = _num(req.get("finished_at"))
    now = {name: _tally(name) for name in EVALUATED}
    past = {name: _windows(name) for name in WINDOWED}
    mismatch = {name: {k: 0 for k in MISMATCH_KEYS} for name in CHECKED}

    for row in a.get("pending") or ():
        name = "ask.answer" if row.get("kind") == "question" else "approval.decide"
        conds = {"pending": True, **_approval_at(row, now_t, None)}
        conds["request_open"] = None if terminal is None else not terminal
        if name == "approval.decide":
            conds["plan_hash_matches"] = row.get("plan_hash_matches") if row.get("kind") == "research_plan" else True
        else:
            conds["ask_open"] = None  # the ask ledger is not in the snapshot
        _count(now[name], conds)
    check()
    _count(now["request.followup"], followup_conditions(
        status, any(f.get("status") == "running" for f in a.get("followups") or ()), a.get("responder") or {}))
    tasks = a.get("tasks") or ()
    for task in tasks:
        if task.get("accepted") and not task.get("completed"):
            _count(now["task.cancel"], {"request_running": status == "running" if isinstance(status, str) else None,
                                        "task_running": True, "no_pending_jobs": None})
    for proposal in a.get("recruit") or ():
        _count(now["recruit.start"], {"recruiter_on_roster": bool(a.get("recruiter_on_roster")),
                                      "paper_or_repo": bool(proposal.get("paper_or_repo"))})
    for staff in a.get("contract_staff") or ():
        _count(now["contract.update"], {"contract_staff": True, "runner_on_roster": bool(staff.get("runner_on_roster"))})
    check()

    for row in (*(a.get("decided") or ()), *(a.get("pending") or ())):
        name = "ask.answer" if row.get("kind") == "question" else "approval.decide"
        state = row.get("state")
        taken = True if state == "decided" else False if state in ("timed_out", "expired", "pending") else None
        _window(past[name], row.get("created_at"), row.get("decided_at"), taken, row.get("kind"))
        if row.get("kind") == "hpc_submit":  # the approval window around an agent's submit; the submit is not ours
            _window(past["hpc.submit"], row.get("created_at"), row.get("decided_at"), None, row.get("kind"))
        if taken:
            conds = _approval_at(row, _num(row.get("decided_at")), finished)
            if name == "ask.answer":
                conds["ask_open"] = None
            elif row.get("kind") == "research_plan":
                conds["plan_hash_matches"] = None  # the hash it was decided on is not recorded
            _check(mismatch[name], _all(conds.values()))
    _window(past["request.followup"], req.get("finished_at"), None, False)  # opens at the end, never closes
    for task in tasks:
        if task.get("accepted"):
            cancelled = task.get("cancelled")
            _window(past["task.cancel"], task.get("dispatched_at"), None, cancelled if isinstance(cancelled, bool)
                    else None)  # no recorded end: a dispatch time is not an accept time
            if cancelled is True:
                _check(mismatch["task.cancel"], None)  # pending jobs at the cancel are not recorded
    for _ in a.get("recruit") or ():
        _window(past["recruit.start"], None, None, None)  # a recruit start is not linked to its request
    check()
    return {"v": V, "exec": {name: execution_refusal(name) for name in ACTIONS}, "now": now, "past": past,
            "mismatch": mismatch}


def followup_line(inputs: Mapping[str, Any], *, rid: Any, epoch: int, ts: float, busy_skipped: int = 0) -> dict:
    """One follow-up observation line: the server's preconditions at that moment and the model's verdict."""
    a = freeze(inputs)
    phase, outcome = a.get("phase"), a.get("outcome")
    conds = followup_conditions((a.get("request") or {}).get("status"), a.get("others_running"),
                                a.get("responder") or {})
    verdict = _all(conds.values())
    line: dict[str, Any] = {"v": V, "type": "followup", "ts": ts, "epoch": epoch, "request_id": rid,
                            "phase": phase, "key": a.get("key"), "status": "ok", "conditions": conds,
                            "verdict_open": verdict, "busy_skipped": int(busy_skipped or 0)}
    if phase == "asked":  # the server took it: was the model's verdict blocked?
        line["mismatch"] = {"taken_while_blocked": None if verdict is None else verdict is False}
    elif phase == "refused":  # the server refused at the door: was the model's verdict open?
        line["mismatch"] = {"refused_while_open": None if verdict is None else verdict is True}
    elif phase == "ended":
        entry = a.get("entry") or {}
        asked, answered = _num(entry.get("asked_at")), _num(entry.get("answered_at"))
        line.update(outcome=outcome, has_task=entry.get("has_task") if isinstance(entry.get("has_task"), bool) else None,
                    wait_s=round(answered - asked, 2) if asked is not None and answered is not None
                    and answered >= asked else None)
        if outcome == "refused_read_only":  # refused after it was taken, by the stated read-only reason only
            line["mismatch"] = {"refused_while_open": None if verdict is None else verdict is True}
    return line


# ---------------------------------------------------------------- shape: names, booleans and counts only

def _count_ok(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _tri(value: Any) -> bool:
    return value is None or isinstance(value, bool)


def _token(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 96 and value.replace("_", "").isalnum()


def _counts(value: Any, keys: Iterable[str]) -> bool:
    keys = tuple(keys)
    return isinstance(value, Mapping) and set(value) <= set(keys) and all(_count_ok(v) for v in value.values())


def _section_problems(sec: Any) -> list[str]:
    if not isinstance(sec, Mapping) or not set(sec) <= set(SECTION_KEYS):
        return ["actions_shape"]
    if sec.get("status") not in STATUSES or not (sec.get("ms") is None or _num(sec.get("ms")) is not None):
        return ["actions_shape"]
    if "error_kind" in sec and not _token(sec["error_kind"]):
        return ["actions_shape"]
    if sec.get("status") != "ok":
        return [] if set(sec) <= {"status", "ms", "error_kind"} else ["actions_shape"]
    exec_ = sec.get("exec")
    if (sec.get("v") != V or not isinstance(exec_, Mapping) or set(exec_) != set(ACTIONS)
            or any(exec_[a] != execution_refusal(a) for a in ACTIONS)):
        return ["actions_shape"]
    now, past, mismatch = sec.get("now"), sec.get("past"), sec.get("mismatch")
    if not (isinstance(now, Mapping) and set(now) <= set(EVALUATED) and isinstance(past, Mapping)
            and set(past) <= set(WINDOWED) and isinstance(mismatch, Mapping) and set(mismatch) <= set(CHECKED)):
        return ["actions_shape"]
    for action, tally in now.items():
        if not (isinstance(tally, Mapping) and set(tally) == set(TALLY_KEYS)
                and all(_count_ok(tally[k]) for k in ("n", "open", "blocked", "unknown"))
                and _counts(tally["blocked_by"], CONDITIONS[action]) and _counts(tally["unknown_by"], CONDITIONS[action])):
            return ["actions_shape"]
    for action, win in past.items():
        if not (isinstance(win, Mapping) and set(win) <= set(WINDOW_KEYS) and ("by_kind" in win) == (action in BY_KIND)
                and all(_count_ok(win.get(k)) for k in ("windows", "closed", "unbounded", "taken", "taken_unknown"))
                and isinstance(win.get("lengths_truncated"), bool) and isinstance(win.get("lengths_s"), (list, tuple))
                and len(win["lengths_s"]) <= MAX_LENGTHS and all(_num(x) is not None for x in win["lengths_s"])
                and ("by_kind" not in win or _counts(win["by_kind"], APPROVAL_KINDS))):
            return ["actions_shape"]
    if not all(_counts(m, MISMATCH_KEYS) and set(m) == set(MISMATCH_KEYS) for m in mismatch.values()):
        return ["actions_shape"]
    return []


def _followup_problems(line: Mapping[str, Any]) -> list[str]:
    if not set(line) <= set(FOLLOWUP_KEYS) or line.get("status") not in STATUSES:
        return ["followup_shape"]
    key = line.get("key")
    if not (key is None or (isinstance(key, str) and len(key) == 16 and set(key) <= _HEX16)):
        return ["followup_shape"]
    if line.get("phase") not in PHASES or line.get("outcome") not in (None, *OUTCOMES):
        return ["followup_shape"]
    if "error_kind" in line and not _token(line["error_kind"]):
        return ["followup_shape"]
    conds, mismatch = line.get("conditions"), line.get("mismatch")
    if conds is not None and not (isinstance(conds, Mapping) and set(conds) == set(CONDITIONS["request.followup"])
                                  and all(_tri(v) for v in conds.values())):
        return ["followup_shape"]
    if mismatch is not None and not (isinstance(mismatch, Mapping) and set(mismatch) <= set(MISMATCHES)
                                     and all(_tri(v) for v in mismatch.values())):
        return ["followup_shape"]
    if not all(_tri(line.get(k)) for k in ("verdict_open", "has_task")):
        return ["followup_shape"]
    if not all(line.get(k) is None or _num(line.get(k)) is not None for k in ("ts", "ms", "wait_s")):
        return ["followup_shape"]
    return [] if _count_ok(line.get("busy_skipped", 0)) else ["followup_shape"]


def shape_problems(line: Mapping[str, Any]) -> list[str]:
    """Why an actions section or a follow-up line may not be written: any field, name or value outside the fixed
    sets. The B1 boundary check still runs on the whole line; this one also covers keys and enums."""
    problems: list[str] = []
    if "actions" in line:
        problems += _section_problems(line["actions"])
    if line.get("type") == "followup":
        problems += _followup_problems(line)
    return problems


# ---------------------------------------------------------------- report (local, no network)

def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def report(lines: Iterable[Mapping[str, Any]], *, setting: str, on: bool) -> dict:
    """The A1 section of ``labhq semantics report``. One window per request (its last line) and one follow-up per
    key, so a line written twice counts once. Thresholds are unmeasured proposals and not A2 safety evidence."""
    by_request: dict[str, Mapping[str, Any]] = {}
    unobserved = broken = truncated = 0
    failed = {"timeout": 0, "error": 0}
    asked: dict[str, Mapping[str, Any]] = {}
    ended: dict[str, Mapping[str, Any]] = {}
    refused: list[Mapping[str, Any]] = []
    followup_failed = busy = boundary = auto_off = 0
    for line in lines:
        kind = line.get("type")
        if kind == "auto_off":
            auto_off += 1
            boundary += line.get("reason") == "info_boundary"
        elif kind == "request":
            sec = line.get("actions")
            if sec is None:
                unobserved += 1
            elif _section_problems(sec):
                broken += 1
            elif sec.get("status") != "ok":
                failed[sec["status"]] += 1
            elif isinstance(line.get("request_id"), str):
                by_request[line["request_id"]] = line
                truncated += bool((line.get("rows") or {}).get("tasks_truncated"))
        elif kind == "followup":
            if _followup_problems(line):
                broken += 1
                continue
            busy += int(line.get("busy_skipped") or 0)
            if line.get("status") != "ok":
                followup_failed += 1
            elif line.get("phase") == "refused":
                refused.append(line)
            elif isinstance(line.get("key"), str):
                (asked if line.get("phase") == "asked" else ended).setdefault(line["key"], line)

    now = {name: _tally(name) for name in EVALUATED}
    past = {name: _windows(name) for name in WINDOWED}
    mismatch = {name: {k: 0 for k in MISMATCH_KEYS} for name in CHECKED}
    lengths: dict[str, list[float]] = {name: [] for name in WINDOWED}
    for line in by_request.values():
        sec = line["actions"]
        for name, tally in sec["now"].items():
            for k in ("n", "open", "blocked", "unknown"):
                now[name][k] += tally[k]
            for k in ("blocked_by", "unknown_by"):
                for cond, n in tally[k].items():
                    now[name][k][cond] += n
        for name, win in sec["past"].items():
            for k in ("windows", "closed", "unbounded", "taken", "taken_unknown"):
                past[name][k] += win[k]
            lengths[name] += [float(x) for x in win["lengths_s"]]
            past[name]["lengths_truncated"] = past[name]["lengths_truncated"] or win["lengths_truncated"]
            for kind_name, n in (win.get("by_kind") or {}).items():
                past[name]["by_kind"][kind_name] += n
        for name, m in sec["mismatch"].items():
            for k in MISMATCH_KEYS:
                mismatch[name][k] += m[k]
    for name in WINDOWED:
        past[name].pop("lengths_s")
        past[name]["length_median_s"] = round(statistics.median(lengths[name]), 1) if lengths[name] else None

    fu_verdicts = [now["request.followup"][k] for k in ("open", "blocked", "unknown")]
    attempts = [*asked.values(), *refused]
    unknown_attempts = sum(1 for l in attempts if l.get("verdict_open") is None)
    verdicts = sum(fu_verdicts) + len(attempts)
    unknown_share = _ratio(now["request.followup"]["unknown"] + unknown_attempts, verdicts)
    taken_blocked = sum(1 for l in asked.values() if (l.get("mismatch") or {}).get("taken_while_blocked") is True)
    refused_open = sum(1 for l in [*refused, *ended.values()]
                       if (l.get("mismatch") or {}).get("refused_while_open") is True)
    mismatch_unknown = sum(1 for l in [*asked.values(), *refused, *ended.values()]
                           for v in (l.get("mismatch") or {}).values() if v is None)
    outcomes = {o: sum(1 for l in ended.values() if l.get("outcome") == o) for o in OUTCOMES}
    followup = {"windows": len(by_request), "asked": len(asked), "refused": len(refused),
                "ended": outcomes, "unterminated": len(set(asked) - set(ended)),
                "ended_without_asked": len(set(ended) - set(asked)),
                "taken_while_blocked": taken_blocked, "refused_while_open": refused_open,
                "mismatch_unknown": mismatch_unknown, "unknown_share": unknown_share,
                "line_failures": followup_failed, "busy_skipped": busy}
    mismatches = taken_blocked + refused_open + sum(m["taken_while_blocked"] for m in mismatch.values())
    gate = {"followup_windows": len(by_request), "unknown_share": unknown_share, "mismatch": mismatches,
            "boundary_off": boundary, "auto_off": auto_off}
    proposals = []
    n = len(by_request)
    if n >= 15 and unknown_share is not None and unknown_share > 0.5:
        proposals.append("기록 공백: request.followup 판정 unknown > 50% — 기록 보강 issue 또는 A1 제거")
    windows = sum(past[name]["windows"] for name in WINDOWED)
    if n >= 15 and windows and mismatches / windows >= 0.1:
        proposals.append(f"불일치: mismatch {mismatches}건이 창 {windows}개의 10% 이상")
    return {"setting": setting, "on": on, "executable": sorted(EXECUTABLE),
            "observed": {"requests": n, "unobserved": unobserved, "failed": failed, "broken": broken,
                         "tasks_truncated": truncated},
            "now": now, "past": past, "mismatch": mismatch, "followup": followup, "gate": gate,
            "propose": proposals}


def render(rep: Mapping[str, Any]) -> list[str]:
    a = rep.get("actions")
    if not isinstance(a, Mapping):
        return []

    def v(x: Any) -> str:
        return "-" if x is None else str(x)

    obs, fu, gate = a["observed"], a["followup"], a["gate"]
    out = ["", "액션 층 그림자 A1 (#149 결정 13, 실행 없음)",
           f"설정 actions {a['setting']} · {'on' if a['on'] else 'off'} · 실행 허용 목록: "
           f"{', '.join(a['executable']) or '없음'} · hpc.* 항상 refused_p3",
           f"관측 요청 {obs['requests']} · 미관측 {obs['unobserved']} · 실패 timeout {obs['failed']['timeout']}/"
           f"error {obs['failed']['error']} · 깨진 칸 {obs['broken']} · task 행 잘림 {obs['tasks_truncated']}",
           "", "| 액션 | 창 | 닫힘 | 미종결 | taken | taken 미상 | 길이 중앙값 s | 지금 open/blocked/unknown |",
           "|---|---|---|---|---|---|---|---|"]
    for name in ACTIONS:
        win = a["past"].get(name)
        now = a["now"].get(name)
        cells = (f"{win['windows']} | {win['closed']} | {win['unbounded']} | {win['taken']} | {win['taken_unknown']} | "
                 f"{v(win['length_median_s'])}") if win else "- | - | - | - | - | -"
        state = f"{now['open']}/{now['blocked']}/{now['unknown']}" if now else "refused_p3"
        out.append(f"| {name} | {cells} | {state} |")
    out += ["", f"이어 묻기: 창 {fu['windows']} · 물음 {fu['asked']} · 거부 {fu['refused']} · 끝 "
                + ", ".join(f"{k} {n}" for k, n in fu["ended"].items())
                + f" · 미종결 {fu['unterminated']} · busy 건너뜀 {fu['busy_skipped']}",
            f"불일치: taken_while_blocked {fu['taken_while_blocked']} · refused_while_open {fu['refused_while_open']} · "
            + " · ".join(f"{k} {m['taken_while_blocked']}/{m['checked']}" for k, m in a["mismatch"].items())
            + f" · 판정 불가 {fu['mismatch_unknown']}",
            f"A2 검토 자료(안전 증거 아님, 기준은 미측정 제안치): 이어 묻기 창 {gate['followup_windows']} (≥20) · "
            f"unknown {v(gate['unknown_share'])} (≤0.1) · mismatch {gate['mismatch']} (0) · "
            f"경계 off {gate['boundary_off']} (0) · 자동 off {gate['auto_off']} (0) · PI 확인 필요"]
    out += ["PROPOSE_REMOVAL — A1(결정은 PI): " + p for p in a["propose"]]
    return out
