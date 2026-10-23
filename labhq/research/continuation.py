"""Continue a review/CP2 revision through a new CP1 (#90, #797).

Reuse requires the normalized node specification, exact scientific brief/hypotheses/protocol/pack contract,
its own/ancestor branches, declared tool configuration, completed bound ledger and proved dependency artifacts.
No fuzzy wording equivalence is inferred. Unproved external inputs and directory outputs are declined before CP1.

``carry_over`` is provisional structural selection. The orchestrator checks input proof and ``verify_reuse``
before CP1, seals the executable candidates to that plan hash, and rechecks the approved seal on resume and before
dispatch. Drift stops for renewed approval; it never silently replaces reuse with a rerun. Files can still change
between checks: this is verification at boundaries, not a filesystem lock.

Reused results bind to the new plan; ``reused_from`` keeps their original round/task. If a continuation stops before
any dispatch, ``restore_round`` restores the archived plan/receipts/results/budget and keeps the declined draft.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .contract import ResearchPlan, plan_sha256, validate_research_result

CONTINUE_GATE = "research_continue"


def _refusal_scope_sha256(carried: dict) -> str:
    scope = {key: carried.get(key) for key in
             ("round", "from_plan_sha256", "p1_issues", "approval_id", "trigger", "pi_request", "note")}
    return hashlib.sha256(_canonical(scope).encode()).hexdigest()


def refusal_context(req: dict) -> dict | None:
    """Bind the refusal of an unstarted continuation to the restored round, not a new research request."""
    contract = req.get("research_contract") or {}
    if not isinstance(contract, dict):
        return None
    carried = contract.get("continuation") or {}
    if not isinstance(carried, dict):
        return None
    ended = carried.get("ended_before_dispatch") or {}
    rounds = contract.get("rounds") or []
    if (req.get("status") != "failed" or not isinstance(ended, dict)
            or ended.get("outcome") != "model_refused" or not isinstance(rounds, list) or not rounds
            or not isinstance(rounds[-1], dict)
            or carried.get("verified") or carried.get("dispatch_started")):
        return None
    previous = rounds[-1]
    number, target = previous.get("round"), carried.get("round")
    task_results = ended.get("task_results") or {}
    if not isinstance(task_results, dict) or not isinstance(task_results.get("plan"), dict):
        return None
    result = task_results["plan"]
    task_id = result.get("task_id")
    created = req.get("created_at")
    if (type(number) is not int or type(target) is not int or target != number + 1
            or contract.get("round") != number or not isinstance(task_id, str) or not task_id
            or not isinstance(carried.get("approval_id"), str) or not carried["approval_id"]
            or result.get("error_kind") != "model_refused"
            or type(created) not in (int, float) or not math.isfinite(created) or created <= 0
            or not previous.get("plan_sha256")
            or contract.get("plan_sha256") != previous["plan_sha256"]
            or carried.get("from_plan_sha256") != previous["plan_sha256"]
            or _canonical(req.get("plan")) != _canonical(previous.get("plan"))):
        return None
    return {"created_at": created, "round": target, "cso_model": req.get("cso_model"),
            "from_plan_sha256": previous["plan_sha256"], "refusal_task_id": task_id,
            "continuation_sha256": _refusal_scope_sha256(carried),
            "snapshot_sha256": hashlib.sha256(_canonical(previous).encode()).hexdigest()}


def refusal_retry_pending(req: dict) -> bool:
    contract = req.get("research_contract") or {}
    if not isinstance(contract, dict):
        raise ValueError("이어 가기 복구 기록을 확인할 수 없습니다.")
    carried = contract.get("continuation") or {}
    if not isinstance(carried, dict):
        raise ValueError("이어 가기 복구 기록을 확인할 수 없습니다.")
    retry = carried.get("refusal_retry") or {}
    if not isinstance(retry, dict):
        raise ValueError("이어 가기 복구 기록을 확인할 수 없습니다.")
    if retry.get("state") not in {"approved", "planning"}:
        return False
    identity = retry.get("identity") or {}
    rounds = contract.get("rounds") or []
    decision = retry.get("decision") or {}
    if (not isinstance(identity, dict) or not isinstance(rounds, list) or not rounds
            or not isinstance(rounds[-1], dict) or not isinstance(decision, dict)
            or not identity or req.get("created_at") != identity.get("created_at")
            or contract.get("round") != identity.get("round") or carried.get("round") != identity.get("round")
            or carried.get("from_plan_sha256") != identity.get("from_plan_sha256")
            or _refusal_scope_sha256(carried) != identity.get("continuation_sha256")
            or contract.get("plan_sha256") != identity.get("from_plan_sha256")
            or _canonical(req.get("plan")) != _canonical(rounds[-1].get("plan"))
            or hashlib.sha256(_canonical(rounds[-1]).encode()).hexdigest() != identity.get("snapshot_sha256")
            or req.get("cso_model") != decision.get("cso_model")):
        raise ValueError("이어 가기 복구 이후 요청이나 계획이 바뀌었습니다.")
    return True


def prepare_refusal_retry(req: dict, identity: dict, decision: dict, approval_id: str) -> None:
    """Retry the same already-approved continuation round; preserve its archive, results, spending and budget."""
    if (not approval_id or refusal_context(req) != identity or not isinstance(decision, dict)
            or set(decision) != {"action", "cso_model"} or decision.get("action") != "model"
            or not isinstance(decision.get("cso_model"), str) or not decision["cso_model"]):
        raise ValueError("이어 가기 거절 이후 요청이나 계획이 바뀌었습니다.")
    contract = req["research_contract"]
    carried = contract["continuation"]
    carried.setdefault("refusal_history", []).append(copy.deepcopy(carried["ended_before_dispatch"]))
    carried["refusal_retry"] = {"state": "approved", "approval_id": approval_id,
                                "identity": copy.deepcopy(identity), "decision": copy.deepcopy(decision)}
    # A declined draft remains audit evidence, but cannot act as a new CP1 or reuse seal on the next planning turn.
    for key in ("ended_before_dispatch", "plan_sha256", "verified", "dispatch_started", "reuse", "rerun", "reused_from"):
        carried.pop(key, None)
    contract["round"] = identity["round"]
    req["cso_model"] = decision["cso_model"]
    for key in ("cso_session_id", "cso_workdir", "model_refusal_approval_id", "finished_at", "error", "outcome"):
        req.pop(key, None)
    req["status"] = "waiting_for_runner"


class ReuseApprovalRequired(RuntimeError):
    """An approved reuse candidate changed; stop instead of dispatching a replacement."""

# The research_contract fields one frozen plan owns. A round archive keeps them, and a continuation that ends before
# dispatch puts them back; the CP2 receipt lives under ``checkpoints.cp2`` and is archived as ``cp2``.
ROUND_CONTRACT_FIELDS = ("plan_sha256", "approval", "pack_snapshot", "pack_applicability", "review")
# The request fields one frozen plan owns.
ROUND_REQUEST_FIELDS = ("plan", "output_types_stats", "reuse_tools", "reuse_no_request_inputs")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def frozen_context(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen question, hypotheses and protocol every research step is dispatched with (the step prompt's digest).

    The hypotheses are the CP1-approved definitions a step judges against. Without them a step that had to rule on
    H0 and the alternatives wrote its own (web trial 2026-10-08: s9 redefined A1 and A2, a review P1; in round 2 it
    stopped to ask the PI for the sentences)."""
    brief = plan.get("brief") or {}
    return {"question": brief.get("question"), "scope": brief.get("scope"),
            "branches": plan.get("branches") or [],
            "hypotheses": {"primary": brief.get("primary_hypothesis"),
                           "null_or_alternatives": brief.get("null_or_alternatives") or [],
                           "distinguishing_observations": brief.get("distinguishing_observations") or []},
            "protocol": {k: v for k, v in (plan.get("protocol") or {}).items() if k != "packs"},
            "pack_values": plan.get("pack_values") or {}}


def pack_context(plan: Mapping[str, Any], snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """The domain packs a step's result was judged under: the plan's pack refs (id, version, sha256) and the
    contract's pack snapshot. Not in the step prompt, so not in ``frozen_context``."""
    return {"packs": (plan.get("protocol") or {}).get("packs") or [],
            "pack_snapshot": dict(snapshot) if isinstance(snapshot, Mapping) else None}


def node_context(plan: Mapping[str, Any], step: Mapping[str, Any]) -> str:
    """Exact scientific context; only explicitly independent branches are scoped per node.

    No wording is treated as semantically equivalent. Synthesis sees all branches.
    """
    context = frozen_context(plan)
    context['brief'] = plan.get('brief') or {}
    context['topics'] = sorted(plan.get('topics') or [])
    context['intake'] = plan.get('intake') or {}
    # Free-form assumptions can affect every node; do not infer that a new one is irrelevant.
    context['assumptions'] = plan.get('assumptions') or []
    # Paper selections and conversion receipts are method inputs, delivered via task metadata.
    # Keep complete rows and their order; scope exactly to this node's selection/attachment targets.
    # Only a digest enters the prompt context, never local skill paths or receipt values.
    context['paper_method_sha256'] = _digest({
        'selections': [row for row in plan.get('paper_skills') or []
                       if step.get('id') in (row.get('step_ids') or [])],
        'results': [row for row in plan.get('paper_skill_results') or []
                    if step.get('id') in (row.get('step_ids') or [])
                    and step.get('agent_id') in (row.get('agent_ids') or [])],
    })
    relevant = {step.get("id"), *_ancestors(plan.get("steps") or []).get(step.get("id"), set())}
    branches = {s.get("branch") for s in plan.get("steps") or [] if s.get("id") in relevant}
    if "synthesis" not in branches:
        context["branches"] = [b for b in context["branches"] if b.get("id") in branches]
    return _canonical(context)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def tool_signatures(plan: Mapping[str, Any], agents: Mapping[str, Any], engines: Any = None) -> dict[str, str]:
    """Declared agent/tool/purpose configuration, per node; no other agent invalidates it.

    Hash the complete configured record rather than guessing which tool/skill fields matter.
    This is not proof of an external tool installation/version.
    """
    signatures = {}
    for step in plan.get("steps") or []:
        agent = agents.get(step.get("agent_id"))
        if isinstance(agent, Mapping) and agent.get("engine"):
            try:
                payload = dict(agent)
                config = getattr(engines, str(agent['engine']), None) if engines is not None else None
                if engines is not None and config is None and agent['engine'] != 'mock':
                    continue
                if config is not None:
                    payload = {'agent': payload, 'engine': config.model_dump(mode='json')}
                # Version the executed prompt contract too: historical signatures without the scoped
                # branch/paper-method context cannot prove the current execution-input contract.
                signatures[step["id"]] = _digest({'node_context': 2, 'configuration': payload})
            except (TypeError, ValueError):
                pass  # an unrecordable declaration supplies no reuse proof
    return signatures


def _artifacts(step: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, str]:
    """Recorded regular-file proof only. Directory outputs deliberately have no reuse contract."""
    from ..util import output_relpath

    outputs = result.get("outputs") or []
    hashes = result.get("output_sha256") or {}
    required = [output_relpath(path) for path in step.get("outputs") or []]
    if (not required or None in required or not outputs or len(set(outputs)) != len(outputs)
            or not set(required) <= set(outputs) or not isinstance(hashes, Mapping)):
        raise ValueError("missing or ambiguous output proof")
    for path in outputs:
        if (not isinstance(path, str) or output_relpath(path) != path
                or not isinstance(hashes.get(path), str) or not re.fullmatch(r"[0-9a-f]{64}", hashes[path])):
            raise ValueError("missing file hash (directory outputs need a verified manifest; reuse declined)")
    return {path: hashes[path] for path in sorted(outputs)}


def has_request_inputs(request: Mapping[str, Any]) -> bool:
    """These inputs reach every step; no content-hash proof is currently available for them."""
    return any(request.get(key) for key in ('references', 'reference_dirs', 'project_dirs'))


def proof_problems(previous: Mapping[str, Any], plan: Mapping[str, Any], carry: Carry,
                   tools: Mapping[str, str], current_request: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Fail closed on unproved external inputs, dependencies or historical tool configuration."""
    old_tools = previous.get("reuse_tools") or {}
    steps = {s["id"]: s for s in plan.get("steps") or []}
    ancestors = _ancestors(list(steps.values()))
    problems = {}
    for sid in carry.reuse:
        step = steps[sid]
        try:
            # A lazy selection has no CP1-fixed content hash. Do not mistake the unchanged
            # descriptor (or an ancestor's descriptor) for proof of the method actually delivered.
            if any(row.get('status') == 'deferred' and
                   set(row.get('step_ids') or []) & {sid, *ancestors[sid]}
                   for row in plan.get('paper_skill_results') or []):
                raise ValueError('deferred paper skill has no pre-approval content proof')
            absence = previous.get('reuse_no_request_inputs')
            if (has_request_inputs(previous) or has_request_inputs(current_request or {})
                    or not isinstance(absence, Mapping) or absence.get(sid) is not True):
                raise ValueError('request-level input content proof is unavailable (including legacy execution)')
            if previous.get('plan_sha256') != plan_sha256(previous.get('plan') or {}):
                raise ValueError('archived plan identity is missing or changed')
            original = (previous.get('results') or {}).get(sid) or {}
            ledger = original.get('structured') or {}
            if (ledger.get('plan_sha256') != previous['plan_sha256'] or ledger.get('step_id') != sid
                    or original.get('agent_id') != step.get('agent_id') or not original.get('task_id')):
                raise ValueError('result identity does not match the archived node')
            if not tools.get(sid) or tools[sid] != old_tools.get(sid):
                raise ValueError("missing or changed declared tool configuration")
            _artifacts(step, carry.results[sid])
            for dep in ancestors[sid]:
                if dep not in carry.results:
                    raise ValueError("missing dependency result")
                _artifacts(steps[dep], carry.results[dep])
            for ref in step.get("input_refs") or []:
                if not isinstance(ref, str) or not ref.startswith("step:"):
                    raise ValueError("external input has no verified content proof")
                source, sep, path = ref[5:].partition("/")
                from ..util import output_relpath
                normalized = output_relpath(path) if sep else None
                if (source not in ancestors[sid] or source not in carry.results or normalized is None
                        or normalized not in _artifacts(steps[source], carry.results[source])):
                    raise ValueError("missing or ambiguous declared dependency input")
        except (ValueError, TypeError, KeyError) as exc:
            problems[sid] = str(exc)
    return problems


def reuse_snapshot(plan: Mapping[str, Any], pack_snapshot: Mapping[str, Any], carry: Carry,
                   tools: Mapping[str, str]) -> dict[str, Any]:
    """A deterministic executable candidate set, sealed to the exact CP1 plan and result records."""
    normalized = ResearchPlan.model_validate(plan)
    ancestors = _ancestors(plan.get("steps") or [])
    steps = {s["id"]: s for s in plan["steps"]}
    nodes = {}
    for step in normalized.steps:
        sid = step.id
        if sid not in carry.reuse:
            continue
        inputs = {dep: _artifacts(steps[dep], carry.results[dep]) for dep in sorted(ancestors[sid])}
        nodes[sid] = {"identity_sha256": _digest({"step": step.model_dump(mode="json"),
                       "context": node_context(plan, steps[sid]), "packs": pack_context(plan, pack_snapshot),
                       "tool": tools[sid], "upstream_artifacts": inputs}),
                      "result_sha256": _digest(carry.results[sid]),
                      "outputs": _artifacts(steps[sid], carry.results[sid])}
    snapshot = {"version": 1, "plan_sha256": plan_sha256(normalized), "nodes": nodes,
                "reuse": list(carry.reuse), "rerun": dict(carry.rerun)}
    return {**snapshot, "sha256": _digest(snapshot)}


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
    the reason, e.g. the output hash check before CP1; a refusal also excludes every step below it."""
    refused = dict(refused or {})
    parsed = ResearchPlan.model_validate(plan)
    target = plan_sha256(parsed)
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    previous_plan = previous.get("plan") or {}
    old = {step["id"]: step for step in previous_plan.get("steps") or [] if isinstance(step, Mapping)}
    previous_results = previous.get("results") or {}
    named = {issue.get("step_id") for issue in issues if issue.get("step_id")}
    context = ("domain pack snapshot changed"
               if _canonical(pack_context(previous_plan, previous.get("pack_snapshot"))) !=
               _canonical(pack_context(plan, pack_snapshot)) else None)
    old_parsed = ResearchPlan.model_validate(previous_plan)
    normalized_old = {step.id: step.model_dump(mode="json") for step in old_parsed.steps}
    normalized_new = {step.id: step.model_dump(mode="json") for step in parsed.steps}
    earlier = (previous.get("continuation") or {}).get("reused_from") or {}
    carry = Carry()
    own: dict[str, str | None] = {}
    bound: dict[str, dict[str, Any]] = {}
    for step in steps:
        sid = step["id"]
        result = previous_results.get(sid) or {}
        reason = ("new step" if sid not in old else
                  "spec changed" if _canonical(normalized_old[sid]) != _canonical(normalized_new[sid]) else
                  "question, scope, hypotheses, protocol or pack values changed"
                  if node_context(previous_plan, old[sid]) != node_context(plan, step) else
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

    empty = {sid: "missing recorded output proof" for sid, result in results.items()
             if not result.get("outputs") or not result.get("output_sha256")}
    report = verify_request({"results": dict(results)}, settings)
    refused: dict[str, str] = dict(empty)
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
                 "(give it instructions that address the request), since an unchanged step with verified inputs may keep its previous "
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
        "labhq can reuse a result only for the same normalized node specification and declared inputs, "
        "under the exact scientific brief, brief.primary_hypothesis, brief.null_or_alternatives, "
        "brief.distinguishing_observations, protocol and pack_values, with the same tool configuration. "
        f"It also requires verified artifact hashes, {named}and every dependency reused too. "
        "Independent unrelated node/branch changes do not invalidate a candidate; no fuzzy wording equivalence "
        "is inferred. Unproved inputs or directory outputs are declined before CP1. CP1 shows only verified "
        "candidates; changes after approval require renewed approval, never a silent rerun. " + scope +
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
