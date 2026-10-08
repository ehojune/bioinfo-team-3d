"""Continue a research request after its scientific review ended with "revise" (#90, #58) or the PI chose revise at
CP2 (PI 점검 R10).

After a review "revise" the PI approves the continuation on a card (gate ``research_continue``) and the CSO writes a
complete new research PLAN that carries the reviewer's P1 issues. After a CP2 revise the PI's CP2 note is the revision
request (``continuation.pi_request``) and no extra card is asked. Either way the PI approves the new plan at a new CP1
(a new plan_sha256), and both count against ``research.revise_continuations``. A completed step of the previous plan
is reused instead of run again only when all of these hold; anything else re-runs:

- the new plan keeps the step byte-identical (canonical JSON of every field) under the same frozen question, scope,
  protocol and pack values, which together are exactly what the step was dispatched with apart from its upstream
  results, the PI's CP1 note and the spending cap (``frozen_context``; neither changes the plan, so a new round's
  note or cap does not re-run a reused step), and under the same domain pack snapshot (``protocol.packs`` with each pack's sha256,
  and the contract's ``pack_snapshot``): a pack whose definition changed under the same ``id@version`` re-runs every
  step, since a step does not declare which pack rule its result rests on;
- no P1 issue of the review names the step;
- the previous round left the step a completed result;
- every step it depends on, directly or not, is reused too;
- the previous result, rebound to the new plan (``structured.plan_sha256``), validates under the new plan;
- just before dispatch, after the new CP1 approval, every output file the step recorded still has the sha256 labhq
  recorded for it (``refused_reuse``). A step that cannot be checked on this PC re-runs.

``carry_over`` alone decides this, from the archived round, on every path: adopting the new plan, the hash check
after its CP1, and a restart between them. A reused result is a copy bound to the current plan, so CP2, the report,
``labhq verify`` and the semantics records read one plan with its own results; the round and plan it ran under stay
in ``continuation.reused_from`` and in the round archive. A continuation that ends before any step ran puts the
archived round back whole (``restore_round``) and keeps its declined draft in ``continuation.declined_plan``.
The archive also keeps the request's spending cap (``budget_usd``), which a new CP1 may have lowered, so that rollback
puts it back too (PR #499 review).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .contract import ResearchPlan, plan_sha256, validate_research_result

CONTINUE_GATE = "research_continue"

# The research_contract fields one frozen plan owns. A round archive keeps them, and a continuation that ends before
# dispatch puts them back; the CP2 receipt lives under ``checkpoints.cp2`` and is archived as ``cp2``.
ROUND_CONTRACT_FIELDS = ("plan_sha256", "approval", "pack_snapshot", "pack_applicability", "review")
# The request fields one frozen plan owns.
ROUND_REQUEST_FIELDS = ("plan", "output_types_stats")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def frozen_context(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen question and protocol every research step is dispatched with (the step prompt's digest)."""
    brief = plan.get("brief") or {}
    return {"question": brief.get("question"), "scope": brief.get("scope"),
            "protocol": {k: v for k, v in (plan.get("protocol") or {}).items() if k != "packs"},
            "pack_values": plan.get("pack_values") or {}}


def pack_context(plan: Mapping[str, Any], snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """The domain packs a step's result was judged under: the plan's pack refs (id, version, sha256) and the
    contract's pack snapshot. Not in the step prompt, so not in ``frozen_context``."""
    return {"packs": (plan.get("protocol") or {}).get("packs") or [],
            "pack_snapshot": dict(snapshot) if isinstance(snapshot, Mapping) else None}


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


@dataclass
class Carry:
    """What a continuation round takes from the archived round, in the new plan's step order."""

    reuse: list[str] = field(default_factory=list)
    rerun: dict[str, str] = field(default_factory=dict)  # step that runs -> why
    results: dict[str, dict[str, Any]] = field(default_factory=dict)  # reused step -> result bound to the new plan
    reused_from: dict[str, dict[str, Any]] = field(default_factory=dict)  # reused step -> round, plan, task it ran in
    salvage: dict[str, Any] = field(default_factory=dict)  # reused step -> its CP2 salvage rows


def _rebind(result: Mapping[str, Any], plan: ResearchPlan, target: str) -> tuple[dict[str, Any] | None, str | None]:
    """A copy of ``result`` whose ledger names the new plan, or why it cannot: the copy must validate under it."""
    ledger = result.get("structured")
    if not isinstance(ledger, Mapping):
        return None, "no result ledger to rebind"
    bound = {**copy.deepcopy(dict(result)), "structured": {**copy.deepcopy(dict(ledger)), "plan_sha256": target}}
    try:
        validate_research_result(bound["structured"], plan=plan)
    except (TypeError, ValueError) as error:  # pydantic's ValidationError is a ValueError
        return None, f"result does not bind to the new plan: {str(error).splitlines()[0]}"
    return bound, None


def carry_over(previous: Mapping[str, Any], plan: Mapping[str, Any], pack_snapshot: Mapping[str, Any] | None,
               issues: Iterable[Mapping[str, Any]], refused: Mapping[str, str] | None = None) -> Carry:
    """Which steps of ``plan`` reuse the archived round ``previous`` (a ``rounds`` entry) and the results they take.

    The one reuse rule (module docstring) for every path. ``refused`` names steps the caller already refused with
    the reason, e.g. the output hash check after CP1; a refusal also re-runs every step below it."""
    refused = dict(refused or {})
    parsed = ResearchPlan.model_validate(plan)
    target = plan_sha256(parsed)
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    previous_plan = previous.get("plan") or {}
    old = {step["id"]: step for step in previous_plan.get("steps") or [] if isinstance(step, Mapping)}
    previous_results = previous.get("results") or {}
    named = {issue.get("step_id") for issue in issues if issue.get("step_id")}
    context = ("question, scope, protocol or pack values changed"
               if _canonical(frozen_context(previous_plan)) != _canonical(frozen_context(plan)) else
               "domain pack snapshot changed"
               if _canonical(pack_context(previous_plan, previous.get("pack_snapshot"))) !=
               _canonical(pack_context(plan, pack_snapshot)) else None)
    earlier = (previous.get("continuation") or {}).get("reused_from") or {}
    carry = Carry()
    own: dict[str, str | None] = {}
    bound: dict[str, dict[str, Any]] = {}
    for step in steps:
        sid = step["id"]
        result = previous_results.get(sid) or {}
        reason = ("new step" if sid not in old else
                  "spec changed" if _canonical(old[sid]) != _canonical(step) else
                  context or ("named by a P1 review issue" if sid in named else
                              "no completed result in the previous round" if not result.get("ok") else
                              refused.get(sid)))
        if reason is None:
            copied, reason = _rebind(result, parsed, target)
            if copied is not None:
                bound[sid] = copied
        own[sid] = reason
    ancestors = _ancestors(steps)
    salvage = (previous.get("cp2") or {}).get("result_salvage") or {}
    for step in steps:
        sid = step["id"]
        upstream = sorted(a for a in ancestors[sid] if own.get(a))
        if own[sid]:
            carry.rerun[sid] = own[sid]
        elif upstream:
            carry.rerun[sid] = "upstream re-runs: " + ", ".join(upstream)
        else:
            carry.reuse.append(sid)
            carry.results[sid] = bound[sid]
            # A step reused twice still names the round that ran it.
            carry.reused_from[sid] = copy.deepcopy(earlier.get(sid)) or {
                "round": previous.get("round"), "plan_sha256": previous.get("plan_sha256"),
                "task_id": bound[sid].get("task_id")}
            if salvage.get(sid):
                carry.salvage[sid] = copy.deepcopy(salvage[sid])
    return carry


def split_reuse(previous_plan: Mapping[str, Any], plan: Mapping[str, Any],
                previous_results: Mapping[str, Mapping[str, Any]],
                issues: Iterable[Mapping[str, Any]], pack_snapshot: Mapping[str, Any] | None = None,
                previous_pack_snapshot: Mapping[str, Any] | None = None) -> tuple[list[str], dict[str, str]]:
    """(steps reused from the previous round, {step that runs: why}): ``carry_over`` on a bare plan and results."""
    carry = carry_over({"plan": previous_plan, "results": previous_results, "pack_snapshot": previous_pack_snapshot},
                       plan, pack_snapshot, issues)
    return carry.reuse, carry.rerun


def with_dependents(plan: Mapping[str, Any], refused: Iterable[str]) -> set[str]:
    """The refused steps and every step that depends on one of them."""
    refused = set(refused)
    ancestors = _ancestors([step for step in plan.get("steps") or [] if isinstance(step, Mapping)])
    return refused | {sid for sid, found in ancestors.items() if found & refused}


def archive_round(req: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The finished round as ``research_contract.rounds`` keeps it: everything its frozen plan owns."""
    contract = req.get("research_contract") or {}
    archived: dict[str, Any] = {"round": int(contract.get("round") or 1)}
    for key in ROUND_CONTRACT_FIELDS:
        archived[key] = copy.deepcopy(contract.get(key))
    for key in ROUND_REQUEST_FIELDS:
        archived[key] = copy.deepcopy(req.get(key))
    archived["cp2"] = copy.deepcopy((contract.get("checkpoints") or {}).get("cp2"))
    archived["continuation"] = copy.deepcopy(contract.get("continuation"))
    archived["results"] = copy.deepcopy(dict(results))
    archived["budget_usd"] = req.get("budget_usd")  # the cap this round ran under; a new CP1 may lower it
    return archived


def restore_round(req: dict[str, Any], archived: Mapping[str, Any]) -> dict[str, Any] | None:
    """Put the archived round back as the request's frozen plan, approval, pack snapshot, CP2 receipt and review.

    For a continuation that ends before any step ran: its record then pairs the results that ran with the plan they
    ran under. Returns the replaced draft (plan, hash, CP1 receipt) when one had been adopted, else None."""
    contract = req["research_contract"]
    carried = contract.get("continuation") or {}
    # Adoption set carried.plan_sha256 in the save that stored the draft as req["plan"] and dropped the previous
    # round's approval, so any approval here is the draft's CP1 receipt.
    draft = ({"plan_sha256": carried["plan_sha256"], "plan": copy.deepcopy(req.get("plan")),
              "approval": copy.deepcopy(contract.get("approval"))} if carried.get("plan_sha256") else None)
    for key in ROUND_CONTRACT_FIELDS:
        if archived.get(key) is None:
            contract.pop(key, None)
        else:
            contract[key] = copy.deepcopy(archived[key])
    for key in ROUND_REQUEST_FIELDS:
        if archived.get(key) is None:
            req.pop(key, None)
        else:
            req[key] = copy.deepcopy(archived[key])
    checkpoints = contract.setdefault("checkpoints", {})
    if archived.get("cp2") is None:
        checkpoints.pop("cp2", None)
    else:
        checkpoints["cp2"] = copy.deepcopy(archived["cp2"])
    contract.pop("result_salvage", None)  # the draft's carried salvage; the archived receipt holds the round's
    contract["round"] = int(archived.get("round") or 1)
    if "budget_usd" in archived:  # an archive written before the cap was kept leaves today's cap as it is
        req["budget_usd"] = archived["budget_usd"]
    return draft


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
                round_no: int, pi_request: str = "") -> str:
    """Appended to the research PLAN prompt of a continuation round. ``pi_request`` is the PI's CP2 revise note: the
    round continues a CP2 revise, not a review "revise" (R10)."""
    if pi_request:
        why = ("At CP2 the PI did not approve the evidence of the previous frozen plan and asked for a revision. "
               "Return a complete new research PLAN that does what the PI's revision request below asks; the PI "
               "approves it at a new CP1.\n")
        named = ""
        scope = ("Change or add only the steps the request needs. A step the request asks to run again must change "
                 "(give it instructions that address the request), since a byte-identical step keeps its previous "
                 "result. Use a new id for a step that does different work.\n")
        listed = "PI revision request (CP2):\n" + pi_request
    else:
        why = ("The scientific review of the previous frozen plan ended with verdict \"revise\". Return a complete "
               "new research PLAN that fixes every P1 issue below; the PI approves it at a new CP1.\n")
        named = "no P1 issue names the step, "
        scope = ("Change or add only the steps the issues need, give a changed step instructions that address its "
                 "issue, and use a new id for a step that does different work.\n")
        lines = [f"- {issue.get('step_id') or '-'}"
                 f"{'/' + str(issue['claim_id']) if issue.get('claim_id') else ''} "
                 f"[{issue.get('category') or 'other'}] {issue.get('problem')} Request: {issue.get('request')}"
                 for issue in issues]
        listed = "P1 issues:\n" + ("\n".join(lines) or "- (none)")
    return (
        f"\n\nContinuation (research round {round_no}). " + why +
        "labhq reuses a previous step's result without running it again only when your plan keeps that step "
        "byte-identical (same id and every field), keeps brief.question, brief.scope, protocol and pack_values "
        f"unchanged, {named}and every step it depends on is reused too. Any change to the question, scope, protocol "
        "or pack values re-runs every step, so change them only when the request or an issue needs it. " + scope +
        listed + f"\n\nPrevious frozen plan (plan_sha256 {previous_sha256}):\n{_canonical(previous_plan)}")


def review_prompt(issues: list[Mapping[str, Any]], pi_request: str = "") -> str:
    """Appended to the research review prompt of a continuation round: the issues the new plan was meant to fix, or
    the PI's CP2 revision request it was written from (R10)."""
    if pi_request:
        return ("\n\nThis plan is a continuation approved at a new CP1 after the PI asked at CP2 for this revision "
                "instead of approving the earlier evidence. Check whether the results address it and file an issue "
                "where they do not:\n" + pi_request)
    if not issues:
        return ""
    return ("\n\nThis plan is a continuation approved at a new CP1 after a previous review asked for these P1 "
            "fixes. Check whether each is fixed and file an issue for one that is not:\n" +
            "\n".join(f"- {issue.get('step_id') or '-'}: {issue.get('problem')} Request: {issue.get('request')}"
                      for issue in issues))
