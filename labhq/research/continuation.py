"""Continue a research request after its scientific review ended with "revise" (#90, #58).

The PI approves the continuation on a card (gate ``research_continue``). The CSO then writes a complete new research
PLAN that carries the reviewer's P1 issues, and the PI approves it at a new CP1 (a new plan_sha256). A completed step
of the previous plan is reused instead of run again only when all of these hold; anything else re-runs:

- the new plan keeps the step byte-identical (canonical JSON of every field) under the same frozen question, scope,
  protocol and pack values, which together are exactly what the step was dispatched with apart from its upstream
  results (``frozen_context``);
- no P1 issue of the review names the step;
- the previous round left the step a completed result;
- every step it depends on, directly or not, is reused too;
- just before dispatch, after the new CP1 approval, every output file the step recorded still has the sha256 labhq
  recorded for it (``refused_reuse``). A step that cannot be checked on this PC re-runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

CONTINUE_GATE = "research_continue"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def frozen_context(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen question and protocol every research step is dispatched with (the step prompt's digest)."""
    brief = plan.get("brief") or {}
    return {"question": brief.get("question"), "scope": brief.get("scope"),
            "protocol": {k: v for k, v in (plan.get("protocol") or {}).items() if k != "packs"},
            "pack_values": plan.get("pack_values") or {}}


def p1_issues(review: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The review's P1 issues; a stored issue without a priority counts as P1, as in the verdict rule."""
    return [issue for issue in (review or {}).get("issues") or []
            if isinstance(issue, dict) and issue.get("priority", "P1") == "P1"]


def _ancestors(steps: list[Mapping[str, Any]]) -> dict[str, set[str]]:
    by_id = {step["id"]: step for step in steps}
    found: dict[str, set[str]] = {}
    for sid in by_id:
        pending, seen = list(by_id[sid].get("depends_on") or []), set()
        while pending:
            ancestor = pending.pop()
            if ancestor in seen or ancestor not in by_id:
                continue
            seen.add(ancestor)
            pending.extend(by_id[ancestor].get("depends_on") or [])
        found[sid] = seen
    return found


def split_reuse(previous_plan: Mapping[str, Any], plan: Mapping[str, Any],
                previous_results: Mapping[str, Mapping[str, Any]],
                issues: Iterable[Mapping[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """(steps reused from the previous round, {step that runs: why}), in the new plan's step order."""
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    old = {step["id"]: step for step in previous_plan.get("steps") or [] if isinstance(step, Mapping)}
    named = {issue.get("step_id") for issue in issues if issue.get("step_id")}
    same_context = _canonical(frozen_context(previous_plan)) == _canonical(frozen_context(plan))
    own: dict[str, str | None] = {}
    for step in steps:
        sid = step["id"]
        result = previous_results.get(sid) or {}
        own[sid] = ("new step" if sid not in old else
                    "spec changed" if _canonical(old[sid]) != _canonical(step) else
                    "question, scope, protocol or pack values changed" if not same_context else
                    "named by a P1 review issue" if sid in named else
                    "no completed result in the previous round" if not result.get("ok") else None)
    ancestors = _ancestors(steps)
    reused: list[str] = []
    rerun: dict[str, str] = {}
    for step in steps:
        sid = step["id"]
        upstream = sorted(a for a in ancestors[sid] if own.get(a))
        if own[sid]:
            rerun[sid] = own[sid]
        elif upstream:
            rerun[sid] = "upstream re-runs: " + ", ".join(upstream)
        else:
            reused.append(sid)
    return reused, rerun


def with_dependents(plan: Mapping[str, Any], refused: Iterable[str]) -> set[str]:
    """The refused steps and every step that depends on one of them."""
    refused = set(refused)
    ancestors = _ancestors([step for step in plan.get("steps") or [] if isinstance(step, Mapping)])
    return refused | {sid for sid, found in ancestors.items() if found & refused}


def verify_reuse(results: Mapping[str, Mapping[str, Any]], settings: Any) -> dict[str, str]:
    """{step: why its reuse is refused} after hashing every recorded output of the reused steps again.

    Uses the `labhq verify` walker. Anything but a matching hash refuses reuse: a changed, missing or unreadable file,
    an output without a recorded hash, or a work folder this PC cannot see."""
    from ..evidence.audit import OK, verify_request

    report = verify_request({"results": dict(results)}, settings)
    refused: dict[str, str] = {}
    for reason in report["reasons"]:
        sid, _, detail = str(reason).partition(": ")
        refused.setdefault(sid, detail or str(reason))
    for row in report["files"]:
        if row["status"] != OK:
            refused.setdefault(str(row["step_id"]), f"{row['path']} {row['status']}"
                               + (f" ({row['detail']})" if row.get("detail") else ""))
    return refused


def plan_prompt(previous_plan: Mapping[str, Any], previous_sha256: str, issues: list[Mapping[str, Any]],
                round_no: int) -> str:
    """Appended to the research PLAN prompt of a continuation round."""
    lines = [f"- {issue.get('step_id') or '-'}"
             f"{'/' + str(issue['claim_id']) if issue.get('claim_id') else ''} [{issue.get('category') or 'other'}] "
             f"{issue.get('problem')} Request: {issue.get('request')}" for issue in issues]
    return (
        f"\n\nContinuation (research round {round_no}). The scientific review of the previous frozen plan ended "
        "with verdict \"revise\". Return a complete new research PLAN that fixes every P1 issue below; the PI "
        "approves it at a new CP1.\n"
        "labhq reuses a previous step's result without running it again only when your plan keeps that step "
        "byte-identical (same id and every field), keeps brief.question, brief.scope, protocol and pack_values "
        "unchanged, no P1 issue names the step, and every step it depends on is reused too. Any change to the "
        "question, scope, protocol or pack values re-runs every step, so change them only when an issue needs it. "
        "Change or add only the steps the issues need, give a changed step instructions that address its issue, "
        "and use a new id for a step that does different work.\n"
        "P1 issues:\n" + ("\n".join(lines) or "- (none)") +
        f"\n\nPrevious frozen plan (plan_sha256 {previous_sha256}):\n{_canonical(previous_plan)}")


def review_prompt(issues: list[Mapping[str, Any]]) -> str:
    """Appended to the research review prompt of a continuation round: the issues the new plan was meant to fix."""
    if not issues:
        return ""
    return ("\n\nThis plan is a continuation approved at a new CP1 after a previous review asked for these P1 "
            "fixes. Check whether each is fixed and file an issue for one that is not:\n" +
            "\n".join(f"- {issue.get('step_id') or '-'}: {issue.get('problem')} Request: {issue.get('request')}"
                      for issue in issues))
