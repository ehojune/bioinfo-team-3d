"""Virtual-Biotech-style orchestration, adapted to a one-PI bioinformatics lab.

briefing (chief of staff) → CSO plan (JSON DAG + optional contract hires) → steps run in parallel
where dependencies allow (hibernate on HPC jobs, resume when they finish) → scientific reviewer
(3 criteria) → targeted revisions → CSO synthesis report.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

from ..adapters import READ_ONLY_OVERRIDES, is_read_only_task, read_only_refusal
from ..ask_results import ask_result, read_ask_results, rejected_step
from ..costs import cost_detail, format_cost, task_cost_item
from ..evidence.report_check import (FAILED_LOOKUP_TITLE, anchor, check_report, claim_rows, failed_lookup_lines,
                                     failed_lookups)
from ..intake import (CLARIFYING_QUESTION_SCHEMA, QUESTION_RULE, has_structure, normalize_questions,
                      question_detail_lines, questions_summary, reference_dirs, render_references)
from ..models import AskRequest, RunnerUnavailable, Task, TaskResult, hard_stop_kind, new_id, waiting
from ..quota import is_quota_error, received_quota_wait
from ..research.contract import (EVIDENCE_CHOICES, RESEARCH_STEP_SCHEMA, bind_result_artifacts,
                                 canonical_plan_json, classify_intake, freeze_plan, read_evidence_decision,
                                 refresh_plan_approval, research_plan_errors, research_plan_schema,
                                 validate_research_plan, validate_research_result, with_pack_refs)
from ..research.packs import configured_packs, pack_refs, pack_snapshot, render_pack_catalog, render_pack_review
from ..util import clip, extract_json, output_relpath, short
from .. import vocab as output_vocab
from ..vocab import declare as output_types

if TYPE_CHECKING:
    from ..gateway.server import Hub

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "clarifying_questions": {"type": "array", "maxItems": 4, "items": CLARIFYING_QUESTION_SCHEMA},
        "steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "agent_id": {"type": "string"},
                           "instruction": {"type": "string"},
                           "outputs": {"type": "array", "items": {"type": "string"}},
                           "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "agent_id", "instruction", "depends_on"]}},
        "recruit": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"paper": {"type": "string"}, "repo": {"type": "string"},
                           "focus": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["paper", "repo", "focus", "reason"]}},
        "notes": {"type": "string"},
    },
    "required": ["clarifying_questions", "steps", "recruit", "notes"],
}


def plan_schema(declare: bool) -> dict[str, Any]:
    """PLAN_SCHEMA itself when output type declarations are off; with them, steps take optional output_types."""
    if not declare:
        return PLAN_SCHEMA
    schema = json.loads(json.dumps(PLAN_SCHEMA))
    schema["properties"]["steps"]["items"] = output_types.with_output_types(PLAN_SCHEMA["properties"]["steps"]["items"])
    return schema


def replan_schema(declare: bool) -> dict[str, Any]:
    """A PLAN of new steps plus ``drop``: completed reviewer-flagged steps to retire (#271)."""
    schema = json.loads(json.dumps(plan_schema(declare)))
    schema["properties"]["drop"] = {"type": "array", "items": {"type": "string"}}
    schema["required"] = [*schema["required"], "drop"]
    return schema

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "scores": {"type": "object", "additionalProperties": False,
                   "properties": {"addresses_question": {"type": "integer", "minimum": 1, "maximum": 5},
                                  "evidence": {"type": "integer", "minimum": 1, "maximum": 5},
                                  "thoroughness": {"type": "integer", "minimum": 1, "maximum": 5}},
                   "required": ["addresses_question", "evidence", "thoroughness"]},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"step_id": {"type": "string"}, "problem": {"type": "string"}, "request": {"type": "string"}},
            "required": ["step_id", "problem", "request"]}},
    },
    "required": ["verdict", "scores", "issues"],
}

# The research lane's one review after CP2 approval (#58 ⑤). REVIEW_SCHEMA above stays the generic review, and
# labhq/research/review.py keeps its per-step REVIEW v2 schema (RESEARCH_REVIEW_SCHEMA), not on this path yet.
RESEARCH_REVIEW_CATEGORIES = ("overgeneralization", "cherry_picking", "no_comparator", "undefined_scale",
                              "speculation_as_fact", "unsupported_by_artifact", "method_change_unstated", "other")
RESEARCH_LANE_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"step_id": {"type": "string"}, "claim_id": {"type": "string"},
                           "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                           "category": {"type": "string", "enum": list(RESEARCH_REVIEW_CATEGORIES)},
                           "evidence_quote": {"type": "string"}, "problem": {"type": "string"},
                           "request": {"type": "string"}},
            "required": ["step_id", "claim_id", "priority", "category", "evidence_quote", "problem", "request"]}},
    },
    "required": ["verdict", "issues"],
}

BRIEFING_PROMPT = """Prepare a briefing (≤300 words) for the CSO on this research request:
field context, recent developments (search the web if available), which datasets exist and how they can be
accessed (public vs controlled access, DUA constraints), and feasibility risks.

Request: {request}"""

# Plan and re-plan questions of the general lane go to the PI's phone card. Nothing validates their length, so
# both prompts carry the same rule.
PI_CARD_QUESTION_RULE = "Each question must fit the PI's phone card: at most 700 characters, the question itself first."

PLAN_PROMPT = """Decompose the PI's request into steps for your team. You do not analyze anything yourself.

Team roster (use these agent ids exactly):
{roster}

Runner compute capabilities:
{capabilities}

Chief of staff briefing:
{briefing}

Rules:
- At most {max_steps} steps. Express order with depends_on; independent steps run in parallel.
- Declare each step's expected output files in outputs so dependencies can be checked. Each one is a
  path inside that step's own workspace outputs/ folder, written as outputs/<name> (for example
  outputs/answer.md), and the instruction saves it at that same path. Never declare an absolute path, `..`,
  or a file at the workspace root; a file the request asks to save in the work folder also goes under outputs/.{output_types_rule}
- Use HPC jobs only when the assigned agent has labhq_hpc tools and a scheduler is available.
  Local CLI is available for light work. If a step needs unavailable compute, ask the PI in
  clarifying_questions before planning execution. Put a QC step after any data generation.
- If no roster member covers a required method, add a contract hire to `recruit` (paper + code repo +
  focus) and plan the step for whoever is closest; the PI decides whether to hire.
- {question_rule} """ + PI_CARD_QUESTION_RULE + """

PI's request: {request}"""

RESEARCH_PLAN_PROMPT = """Create a frozen research contract for the PI's request. Do not execute or analyze.

Team roster (use these agent ids exactly, never an orchestration role):
{roster}

Runner compute capabilities:
{capabilities}

Chief of staff briefing:
{briefing}

Authoritative intake decision (copy it into `intake`):
{intake}

Configured domain packs (labhq writes `protocol.packs` from this snapshot; leave it empty):
{packs}

Contract rules:
- Return schema_version 2 and at most {max_steps} steps. Never silently drop a step to fit the limit.
- The brief states question, purpose, subject, scope, deliverables, and observable completion conditions.
- Explanatory/comparative work states a primary hypothesis, alternatives, and distinguishing observations.
  Exploratory/technical work uses its purpose and does not invent H0/H1.
- Freeze analysis unit, selection/exclusion, comparators, metrics, validation, resources, stop/approval
  conditions, data boundaries, and statistics applicability before execution. Every not_applicable item needs a reason.
  Applicable statistics needs estimand, analysis_unit, and primary_outcomes.
- Each step declares phase, claim_ids, input_refs, outputs, checks, evidence_slots, and depends_on. Every output is
  inside that step's own workspace outputs/ folder, written as outputs/<name>, and the instruction uses that exact
  path. Never declare an absolute path, home path, `..`, or a file at the workspace root.{output_types_rule}
- Put QC after data generation. {question_rule}
- For every configured pack, fill top-level `pack_values[key]` with exactly the keys in its `pack_values_keys`:
  a value for each field, a non-empty explanation for each validator id, and a non-empty outcome for each
  acceptance id. Acceptance ids are the pack's rule ids; reviewer questions are not acceptance ids.
  Pack `rules` are machine checks on those values: when every `when` predicate holds (a list means all),
  the `require` predicate must hold and the `forbid` predicate must not. Free-text explanations never pass a rule.
  Domain packs may extend this contract but cannot weaken it. A missing/invalid value or conflict makes planning fail.
- PR 1 pilot stops after CP1 approval. Research steps will not run in this PR.

PI's request: {request}"""
# Off keeps the prompt of main byte for byte. On swaps its CP1-only line for the contract the steps run under
# (#90 CP2).
RESEARCH_CP1_ONLY_RULE = "PR 1 pilot stops after CP1 approval. Research steps will not run in this PR."
RESEARCH_CP2_RULE = (
    "After CP1 approval labhq runs these steps exactly as frozen; nothing re-plans them. Each step returns a "
    "result v2 ledger bound to this plan's sha256, its step id, its claim_ids and its evidence_slots, with claims "
    "and evidence kept apart. An artifact_refs path must be one of that step's declared outputs or an upstream "
    "step's collected artifact; evidence citing any other path is refused. The request then stops at CP2, where "
    "the PI approves, requests revision of, or denies the evidence. After approval one reviewer checks the claims "
    "against their ledgers and files, and the final report ties every conclusion to a ledger claim as "
    "[[claim:<step_id>/<claim_id>]], which labhq checks. A failed step, a requested revision or a reviewer's revise "
    "ends the request, and a changed plan needs a new CP1 approval.")
RESEARCH_CP2_PLAN_PROMPT = RESEARCH_PLAN_PROMPT.replace(RESEARCH_CP1_ONLY_RULE, RESEARCH_CP2_RULE)
# CP2 cards an answer without a readable choice gets before the request ends as not approved.
CP2_MAX_ASKS = 3

STEP_PROMPT = """Overall request (context only): {request}

Your step ({step_id}): {instruction}

Teammates' upstream results are in the context section. Deliver: what you did, key results with file
paths, caveats and open questions. Do not quietly switch to a weaker method when one fails: keep debugging,
and if you give it up, say what you tried and why you stopped. If you cannot proceed without a PI decision,
return JSON with "blocking_decision": "the specific question and choices", written for the PI's phone card:
at most 700 characters, the question itself in the first sentence, then each choice on its own line starting
with "- ". Inside the JSON string write each line break as \\n. Do not proceed with the blocked work."""


STEP_OUTPUTS_RULE = ("\n\nDeclared outputs: save each at exactly this path in your workspace; "
                     "labhq collects only these: {paths}")

REVIEW_PROMPT = """You are the scientific reviewer. Evaluate the team's work on the request below with three
criteria scored 1–5: addresses_question, evidence (how well conclusions are supported), thoroughness.
List concrete issues per step_id with a specific revision request. Use verdict "revise" only if fixing an
issue would materially change the conclusions.

Request: {request}

Team results:
{results}"""

REPLAN_PROMPT = """Re-plan only the unfinished part of the PI's request. You do not analyze anything yourself.

Team roster (use these agent ids exactly):
{roster}

Runner compute capabilities:
{capabilities}

Why labhq asks for a re-plan:
{trigger}

Rules:
- Completed steps keep their results and outputs and are never rerun. New steps may depend on them by id.
- Retired steps (no longer in the plan): {retired}. Return the steps that replace their work.
- {drop_rule}
- Give every new step a new id that this request has never used. Used ids: {used}.
- Kept and new steps together are at most {max_steps}. Express order with depends_on.
- Declare each output as outputs/<name> inside that step's own workspace and save it at that path.{output_types_rule}
- Stay within the request, permissions, data boundaries and PI approvals. If scope, cost, compute, data access or an
  approval must change, ask in clarifying_questions and do not plan the blocked work. """ + PI_CARD_QUESTION_RULE + """
- {empty_rule}

PI's request: {request}

Current plan:
{plan}

Team results so far:
{results}"""

SYNTH_PROMPT = """Write the final report for the PI.
Structure: 1) answer / recommendation, 2) evidence by step (with file paths), 3) reviewer concerns and how
they were addressed, 4) what would change the conclusion, 5) next steps (including any proposed contract hires).

Request: {request}

Team results:
{results}

Reviewer: {review}"""

# Appended to SYNTH_PROMPT only when the generic review loop ended with the verdict still "revise" (2nd mock
# trial F5); the request still fails, but the PI gets the CSO's conclusion instead of a step dump.
UNRESOLVED_REVIEW_NOTE = (
    "\n\nThe reviewer still asked for revisions after the last revision labhq could run. List each of the "
    "reviewer's remaining issues, one by one, in a section titled \"해결되지 않은 리뷰 지적\", and do not state any "
    "conclusion those issues bear on as if it were settled.")

# The research lane after CP2 approval (#58 ③⑤). SYNTH_PROMPT and REVIEW_PROMPT above stay the generic ones.
RESEARCH_REVIEW_PROMPT = """You are the scientific reviewer of a research request that ran under a frozen,
PI-approved plan; the PI approved its evidence at CP2. Check every claim against its evidence ledger. Where the
readable files below can be opened, read them and compare what they contain with what each claim says.

Return one issue per problem with:
- step_id, and claim_id ("" when the issue is not about one claim)
- priority: P1 if fixing it would change a conclusion, P2 if it weakens a conclusion, P3 if it is wording only
- category: overgeneralization, cherry_picking, no_comparator, undefined_scale, speculation_as_fact,
  unsupported_by_artifact, method_change_unstated, or other
- evidence_quote: the exact text, from the ledger or from a file you read, that shows the problem
- problem, and request: the specific fix
Answer the domain pack reviewer questions below as issues where they find a problem.
Use verdict "revise" only when there is at least one P1 issue; otherwise "accept". labhq does not re-run the frozen
plan: a revise ends the request, and a fixed plan needs a new CP1 approval.

Request: {request}

Frozen plan (plan_sha256 {plan_sha256}):
{plan}

Domain packs (reviewer questions and rules):
{packs}

Evidence ledgers per step: claims, evidence, links, artifact_refs with the artifact_sha256 labhq recorded, evidence
refused and claims left unsupported at CP2, not_established, failures and method_changes.
{ledgers}"""

RESEARCH_REVIEW_RETRY = ('\n\nReturn ONLY a JSON object with verdict exactly "accept" or "revise" and issues, each '
                         'issue with every field. Do not add prose.')

def with_p1_verdict(review: dict) -> dict:
    """The research review's verdict follows its priorities: revise exactly when an issue is P1 (a fix would change
    the conclusion). A reviewer verdict that disagrees is kept as ``reviewer_verdict`` (PR #336 review)."""
    verdict = "revise" if any(issue.get("priority") == "P1" for issue in review.get("issues") or []) else "accept"
    if review.get("verdict") == verdict:
        return review
    return {**review, "verdict": verdict, "reviewer_verdict": review.get("verdict")}


RESEARCH_SYNTH_PROMPT = """Write the final research report for the PI from the frozen plan, the evidence the PI
approved at CP2 and the research review below.

Claim anchors (labhq checks them by machine):
- End every sentence that states a conclusion or a number with the anchor of the claim it rests on, written
  exactly [[claim:<step_id>/<claim_id>]].
- Anchor only the citable claims listed below, each with the anchor shown there. A claim that is not listed as
  citable is not established: do not state it as a conclusion.
- Put the not-established items and failed lookups below in their own section titled "확립되지 않은 것", without
  anchors. A failed or empty lookup is neither evidence nor proof of absence.
- Report the reviewer's P1 and P2 issues as limitations.
Structure: 1) answer, 2) evidence by claim (with anchors and file paths), 3) 확립되지 않은 것, 4) limitations,
5) what would change the conclusion, and next steps.

Request: {request}

Frozen plan (plan_sha256 {plan_sha256}):
{plan}

Citable claims:
{citable}

Claims that cannot be cited:
{uncitable}

Not established, failed lookups and method changes:
{gaps}

Research review (verdict {verdict}):
{issues}

Team results:
{results}"""

WAKE_PROMPT = """Your HPC jobs finished:
{jobs}
Job scripts and logs are under jobs/ in your workspace ({workdir}). Check exit status and outputs, then
continue your step and report as instructed."""

WRAP_PROMPT = "Your turn limit was reached. Save any partial results under outputs/ and write outputs/PARTIAL_STATUS.md with what is done and what remains unfinished."

ASK_WAKE_PROMPT = """A blocking question from your previous turn has been answered:
Your earlier blocking question and the PI's answer:
{answers}

Continue the same step in this session. Treat another employee's answer as advice, not as PI approval.
Do not repeat the same question."""

CONSULT_PROMPT = """Answer one blocked employee's question using the request, plan and policy context below.
This is a read-only, one-answer consult. Do not call labhq_ask and do not approve installations,
destructive work, restricted-data access, budget overruns or work outside the request.
When bioinfo-agent asks whether to build a missing reusable pipeline, answer that it should build it.

From: {sender}
Question: {question}
Why blocked: {why_blocked}
Tried: {tried}
Options: {options}
Refs in the blocked task workspace: {refs}
{reference_note}

Request: {request}
Plan: {plan}"""


FOLLOWUP_PROMPT = """The PI asks a follow-up question about this finished request. Answer from the work already done:
this request's report, its output files and your earlier session. This is read-only: do not start new
analyses, HPC jobs, installations or hires. If answering needs new work, say which request the PI should send.

Original request: {request}

Final report (excerpt):
{report}
{history}
PI follow-up question: {question}"""


def continuation_prompt(task: Task, updates: str, *, resumable: bool,
                        previous_result: TaskResult | None, context_chars: int) -> str:
    """One policy for continuing a task, including engines without session resume."""
    if resumable:
        return updates
    previous = ""
    if previous_result:
        previous = previous_result.text
        if previous_result.structured is not None:
            previous += "\n" + json.dumps(previous_result.structured, ensure_ascii=False)
    return (f"Original instruction:\n{task.prompt}\n\nOriginal context:\n{task.context or '(none)'}"
            f"\n\nPrevious turn:\n{clip(previous, context_chars) or '(none)'}"
            f"\n\nContinuation updates:\n{updates}")


def reference_meta(request: dict | None) -> dict[str, list[str]]:
    """Path references travel as read-only directories; the runner re-checks them against its roots."""
    dirs = reference_dirs((request or {}).get("references"))
    return {"reference_dirs": dirs} if dirs else {}


def format_roster(agents: list[dict]) -> str:
    lines = []
    for a in agents:
        tag = " [파견직]" if a.get("employment") == "contract" else ""
        tools = f" · tools: {', '.join(a['mcp'])}" if a.get("mcp") else ""
        allowed = a.get("tools") or []
        builtin = a.get("builtin_tools")
        if a.get("engine") == "codex":
            access = "read-only" if a.get("sandbox") == "read-only" else "write-capable"
        else:
            access = "read-only" if a.get("permission_mode") == "plan" or (
                builtin and not any(x in builtin for x in ("Write", "Edit", "Bash"))) or (
                allowed and not any(x.startswith(("Write", "Edit", "Bash")) for x in allowed)) else "write-capable"
        lines.append(f"- {a['id']}{tag}: {a['name']} – {a['role']} "
                     f"({a['engine']}/{a.get('model') or 'default'}); {access}; "
                     f"tools={allowed or builtin or 'default'}; denied={a.get('disallowed_tools') or []}; "
                     f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}; "
                     f"max_turns={a.get('max_turns') or 'unlimited'}{tools}")
    return "\n".join(lines)


def _reaches(steps: list[dict], start: str, target: str) -> bool:
    """True when `start` already depends on `target`, directly or through other steps."""
    deps = {s["id"]: s["depends_on"] for s in steps}
    seen, stack = set(), [start]
    while stack:
        sid = stack.pop()
        if sid == target:
            return True
        if sid not in seen:
            seen.add(sid)
            stack.extend(deps.get(sid, []))
    return False


ORCHESTRATION_ROLES = frozenset({"cso", "chief_of_staff", "sci_reviewer"})


def qa_text(entry: Any) -> str:
    """A PI answer together with what it answers (older records stored the answer alone)."""
    if isinstance(entry, dict):
        qs = entry.get("questions") or ([entry["question"]] if entry.get("question") else [])
        details = entry.get("question_details") or []
        asked = "\n".join("\n".join([f"Q{i}. {q}", *(question_detail_lines(details[i - 1])
                                                    if i <= len(details) and isinstance(details[i - 1], dict)
                                                    else [])])
                          for i, q in enumerate(qs, 1))
        return f"{asked}\nPI answer: {entry.get('answer', '')}" if asked else f"PI answer: {entry.get('answer', '')}"
    return f"PI answer: {entry}"


class PlanOutputsError(ValueError):
    """A declared step output that no normalization can bring under the step's outputs/ folder (#220)."""


def _append_report_metadata(report: str, sections: list[str]) -> str:
    """Add LabHQ audit text without moving a sole trailing benchmark result block from last place (#229)."""
    if not sections:
        return report
    metadata = "\n\n".join(section.strip() for section in sections if section.strip())
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(report))
    if len(blocks) == 1 and not report[blocks[0].end():].strip():
        before = report[:blocks[0].start()].rstrip()
        return ((before + "\n\n") if before else "") + metadata + "\n\n" + blocks[0].group(0)
    return report.rstrip() + "\n\n" + metadata


def _research_plan_digest(plan: dict) -> str:
    """The frozen plan as the research reviewer and report writer read it: question, protocol, pack values, steps."""
    steps = [{key: step.get(key) for key in ("id", "agent_id", "phase", "instruction", "claim_ids", "outputs",
                                             "evidence_slots", "depends_on")} for step in plan.get("steps") or []]
    digest = {"brief": plan.get("brief"),
              "protocol": {k: v for k, v in (plan.get("protocol") or {}).items() if k != "packs"},
              "pack_values": plan.get("pack_values") or {}, "steps": steps}
    return clip(json.dumps(digest, ensure_ascii=False), 8000)


def _research_issue_lines(issues: list[dict]) -> list[str]:
    """Research review issues, P1 first (the sort is stable within a priority)."""
    return [f"- [{issue['priority']}] {issue['step_id']}{'/' + issue['claim_id'] if issue['claim_id'] else ''} "
            f"{issue['category']}: {issue['problem']} Request: {issue['request']} "
            f"Quote: \"{short(issue['evidence_quote'], 300)}\""
            for issue in sorted(issues, key=lambda issue: issue["priority"])]


def _without_empty(value: Any) -> Any:
    """A ledger row without its null or empty optional fields, so the reviewer prompt carries what was stated."""
    if isinstance(value, dict):
        return {key: _without_empty(item) for key, item in value.items() if item not in (None, [], {}, "")}
    if isinstance(value, list):
        return [_without_empty(item) for item in value]
    return value


def _citable_claim_line(row: dict) -> str:
    claim = row["claim"]
    line = (f"- {anchor(row['step_id'], claim['id'])} {claim.get('status')} ({claim.get('importance')}; "
            f"scope: {claim.get('scope')}): {claim.get('statement')}")
    return line + (f" Limitations: {'; '.join(claim['limitations'])}" if claim.get("limitations") else "")


def _research_gap_lines(ledgers: dict[str, Any], lookups: list[dict[str, str]]) -> list[str]:
    """What the research report must list apart from its conclusions: not established, failures, failed lookups,
    and method changes."""
    lines = [f"- {sid} not established: {item}" for sid, ledger in ledgers.items()
             for item in (ledger or {}).get("not_established") or []]
    lines += [f"- {sid} failure: {item}" for sid, ledger in ledgers.items()
              for item in (ledger or {}).get("failures") or []]
    lines += failed_lookup_lines(lookups)
    lines += [f"- {sid} method change{' (affects the conclusion)' if change.get('affects_conclusion') else ''}: "
              f"{change.get('field')}: {change.get('planned')} -> {change.get('actual')}; {change.get('reason')}"
              for sid, ledger in ledgers.items() for change in (ledger or {}).get("method_changes") or []]
    return lines


def replan_history_lines(history: list[dict]) -> list[str]:
    """One line per re-plan attempt (#271). A retired step that failed keeps its cause, so the PI and the
    reviewer read why the original method was replaced, not only that it was."""
    lines = []
    for entry in history:
        line = f"- #{entry.get('attempt') or '-'} {entry['trigger']}: {entry['status']}"
        prior = entry.get("prior_results") or {}
        if entry.get("retired"):
            line += "; retired: " + ", ".join(
                f"{sid} ({short(prior[sid].get('error') or 'failed', 200)})"
                if sid in prior and not prior[sid].get("ok")
                and not str(prior[sid].get("error") or "").startswith("skipped:") else sid
                for sid in entry["retired"])
        if entry.get("added"):
            line += f"; added: {', '.join(entry['added'])}"
        if entry.get("reason"):
            line += f"; reason: {short(entry['reason'], 500)}"
        lines.append(line)
    return lines


def replan_history_note(req: dict) -> str:
    """Re-plan history for the reviewer and the final report's author. Empty, so their prompts stay as before,
    unless orchestrator.max_replans recorded an attempt (#271)."""
    if not req.get("replan_history"):
        return ""
    return ("\n\nPlan changes during this request (labhq re-plan history):\n" +
            "\n".join(replan_history_lines(req["replan_history"])))


def with_downstream_revisions(steps: list[dict], feedback: dict[str, str]) -> dict[str, str]:
    """Reviewer feedback plus every step downstream of a flagged one (PR #337 review).

    A revised step changes what its dependents read, so a dependent the reviewer did not flag re-runs too, with a
    note naming the revised upstream steps; otherwise a later revision or the report reads a bridge step (s5 → s8
    → s9) built on the old result. Flagged steps keep their own notes and get no extra one.
    """
    children: dict[str, list[str]] = {s["id"]: [] for s in steps}
    for step in steps:
        for dep in step["depends_on"]:
            children.setdefault(dep, []).append(step["id"])
    revised_above: dict[str, set[str]] = {}
    pending = list(feedback)
    while pending:
        sid = pending.pop()
        for child in children.get(sid, []):
            if child in feedback:
                continue
            roots = {sid} if sid in feedback else revised_above.get(sid, set())
            if not roots <= revised_above.get(child, set()):
                revised_above.setdefault(child, set()).update(roots)
                pending.append(child)
    extended = dict(feedback)
    for sid in (s["id"] for s in steps):
        if sid in revised_above:
            extended[sid] = (f"- Upstream step(s) {', '.join(sorted(revised_above[sid]))} were revised after the "
                             "scientific review. Redo your step on their new results and update your outputs.\n")
    return extended


def _output_reference(inner: str) -> re.Pattern[str]:
    """A root, absolute, home, or bare instruction reference to one declared output (#229)."""
    body = r"[/\\]".join(re.escape(part) for part in inner.split("/"))
    prefix = (r"(?:\.[/\\]|~[/\\](?:[^\s\"'`/\\]+[/\\])*|[A-Za-z]:[/\\](?:[^\s\"'`/\\]+[/\\])*|"
              r"[/\\](?:[^\s\"'`/\\]+[/\\])*)?")
    return re.compile(r"(?<![A-Za-z0-9_.\-/\\])" + prefix + body +
                      r"(?![A-Za-z0-9_\-/\\]|\.[A-Za-z0-9_])", re.IGNORECASE)


_OUTPUT_ACTION = re.compile(
    r"\b(?:write|writes|writing|written|save|saves|saving|saved|create|creates|creating|created|"
    r"produce|produces|producing|produced|export|exports|exporting|exported|store|stores|storing|stored|"
    r"deliver|delivers|delivering|delivered|emit|emits|emitting|emitted|generate|generates|generating|generated|"
    r"make|makes|making|made)\b|"
    r"작성|저장|생성|내보내|산출|만들",
    re.IGNORECASE,
)
_INPUT_ACTION = re.compile(
    r"\b(?:read|reads|reading|load|loads|loading|loaded|use|uses|using|used|consume|consumes|consuming|"
    r"consumed|open|opens|opening|opened|inspect|inspects|inspecting|inspected|input|from)\b|"
    r"읽|불러|사용|입력|열어|검사",
    re.IGNORECASE,
)


def _instruction_path_action(instruction: str, start: int, end: int) -> str | None:
    """Nearest input/output action around a path; ties stay ambiguous instead of changing an input."""
    left, right = max(0, start - 200), min(len(instruction), end + 100)
    actions = []
    for kind, pattern in (("output", _OUTPUT_ACTION), ("input", _INPUT_ACTION)):
        for action in pattern.finditer(instruction, left, right):
            if action.start() < end and action.end() > start:
                continue
            if action.end() <= start:
                distance = start - action.end()
            elif action.start() >= end:
                distance = action.start() - end
            else:
                distance = 0
            actions.append((distance, kind))
    if not actions:
        return None
    nearest = min(distance for distance, _ in actions)
    kinds = {kind for distance, kind in actions if distance == nearest}
    return kinds.pop() if len(kinds) == 1 else None


def _external_output_reference(instruction: str, match: re.Match[str]) -> bool:
    """True only when an external-form path is directly governed by an output action."""
    if _instruction_path_action(instruction, match.start(), match.end()) != "output":
        return False
    left = max(0, match.start() - 200)
    actions = [action for action in _OUTPUT_ACTION.finditer(instruction, left, match.start())]
    if not actions:
        return False
    bridge = instruction[actions[-1].end():match.start()]
    return re.fullmatch(r"\s*(?:(?:to|at|as|into)\s+)?", bridge, re.IGNORECASE) is not None


def _external_reference(match: re.Match[str]) -> bool:
    value = match.group(0).replace("\\", "/")
    return value.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:/", value) is not None


def _instruction_references_artifact(instruction: str, inner: str) -> bool:
    """Match only workspace artifact forms; absolute/home paths are external inputs or destinations."""
    patterns = (_output_reference(inner), _output_reference(f"outputs/{inner}"))
    return any(not _external_reference(match) for pattern in patterns for match in pattern.finditer(instruction))


def _rewrite_output_references(instruction: str, inner: str, rel: str) -> tuple[str, int, list[str]]:
    pattern = _output_reference(inner)
    matches = list(pattern.finditer(instruction))
    # More than one same-basename reference that includes an absolute/home path can mix an input and output.
    # This shape is ambiguous regardless of wording, so do not rely on an open-ended language list.
    if len(matches) > 1 and any(_external_reference(match) for match in matches):
        return instruction, 0, [match.group(0) for match in matches]
    rewritten = 0
    ambiguous = []

    def replace(match: re.Match[str]) -> str:
        nonlocal rewritten
        action = _instruction_path_action(instruction, match.start(), match.end())
        if action == "output" and (not _external_reference(match) or
                                   _external_output_reference(instruction, match)):
            rewritten += 1
            return f"./{rel}"
        ambiguous.append(match.group(0))
        return match.group(0)

    return pattern.sub(replace, instruction), rewritten, ambiguous


def _contain_outputs(step: dict) -> tuple[list[str], str | None]:
    """Keep every declared output under outputs/ before dispatch (#220).

    The runner collects a declared output only from the workspace outputs/ folder (`output_relpath`). A plan
    that declares `answer.md` but tells the agent to write `./answer.md` gets an INCOMPLETE step even though
    the file exists. A root reference to the step's own output is rewritten to `./outputs/...` and the
    declaration made explicit. An output that leaves outputs/ (absolute, drive, `..`) cannot be fixed here;
    it comes back as a problem for the caller to reject.
    """
    warnings, bad, ambiguous, outputs, seen = [], [], [], [], set()
    for name in step["outputs"]:
        rel = output_relpath(name)
        if rel is None:
            bad.append(name)
            continue
        if rel in seen:
            warnings.append(f"step {step['id']}: duplicate output {name!r} removed as {rel}")
            continue
        seen.add(rel)
        inner = rel[len("outputs/"):] if rel.startswith("outputs/") else rel
        instruction, refs, unclear = _rewrite_output_references(step["instruction"], inner, rel)
        ambiguous.extend(unclear)
        if refs or name.strip().replace("\\", "/") != rel:
            step["instruction"] = instruction
            warnings.append(f"step {step['id']}: output {name!r} moved under outputs/ as {rel}")
        outputs.append(rel)
    step["outputs"] = outputs + bad
    problems = []
    if bad:
        problems.append(f"step {step['id']}: outputs {bad} are outside its outputs/ folder")
    if ambiguous:
        problems.append(f"step {step['id']}: ambiguous instruction paths {ambiguous} match declared outputs; "
                        "use a distinct input name and an explicit output action with outputs/<name>")
    return warnings, "; ".join(problems) if problems else None


def _normalize_plan_outputs(plan: Any) -> tuple[Any, list[str]]:
    """Copy and contain structurally valid-looking step outputs before any lane consumes the plan."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return plan, []
    normalized = {**plan, "steps": [dict(step) if isinstance(step, dict) else step for step in plan["steps"]]}
    warnings, problems = [], []
    for step in normalized["steps"]:
        if not (isinstance(step, dict) and isinstance(step.get("id"), str) and
                isinstance(step.get("instruction"), str) and isinstance(step.get("outputs"), list) and
                all(isinstance(name, str) for name in step["outputs"])):
            continue
        contained, problem = _contain_outputs(step)
        warnings.extend(contained)
        if problem:
            problems.append(problem)
    if problems:
        raise PlanOutputsError("; ".join(problems) + ". Declare each output as outputs/<name> inside the "
                               "step's own workspace and save it at that path.")
    return normalized, warnings


def validate_steps(raw: list[dict], known: set[str], max_steps: int,
                   excluded: frozenset[str] | set[str] = ORCHESTRATION_ROLES, *,
                   vocab: output_vocab.Vocab | None = None, stats: dict | None = None,
                   reject_excess: bool = False) -> tuple[list[dict], list[str]]:
    """Steps ready to dispatch, plus warnings. With ``vocab`` (plan.declare_output_types on), each step's
    ``output_types`` is normalized against its final outputs and per-request counts go into ``stats``; without it,
    any ``output_types`` the CSO sent is dropped and the steps are exactly what they were before #221.

    A fresh CSO plan is cut to ``max_steps``. A stored plan passes ``reject_excess``: cutting it would drop steps
    the request already promised, so a plan over the limit raises instead (#282)."""
    if reject_excess and len(raw) > max_steps:
        raise ValueError(f"stored plan has {len(raw)} steps; maximum is {max_steps}; "
                         "raise orchestrator.max_steps to resume it")
    warnings, steps, seen = [], [], set()
    raw_types: dict[str, Any] = {}
    for i, s in enumerate(raw[:max_steps]):
        if s.get("agent_id") in excluded:
            warnings.append(f"step {s.get('id') or i + 1}: orchestration role removed")
            continue
        sid = str(s.get("id") or f"s{i + 1}")
        if sid in seen:
            sid = f"{sid}_{i}"
        seen.add(sid)
        steps.append({"id": sid, "agent_id": s.get("agent_id", ""), "instruction": s.get("instruction", ""),
                      "depends_on": [str(d) for d in s.get("depends_on") or []],
                      "outputs": [str(o) for o in s.get("outputs") or []]})
        raw_types[sid] = s.get("output_types")
    problems = []
    for s in steps:
        contained, problem = _contain_outputs(s)
        warnings.extend(contained)
        problems += [problem] if problem else []
    if problems:
        raise PlanOutputsError("; ".join(problems) + ". Declare each output as outputs/<name> inside the "
                               "step's own workspace and save it at that path.")
    ids = {s["id"] for s in steps}
    producers: dict[str, list[str]] = {}
    for s in steps:
        for o in s["outputs"]:
            producers.setdefault(o, []).append(s["id"])
    for s in steps:
        invalid = [d for d in s["depends_on"] if d not in ids or d == s["id"]]
        if invalid:
            raise ValueError(f"step {s['id']}: invalid dependencies {invalid}")
    # Infer a dependency only from an unambiguous reference: another step's id, or an output name that exactly
    # one other step produces and this step does not produce itself. Never infer one that would close a cycle.
    for s in steps:
        for other in steps:
            if other["id"] == s["id"] or other["id"] in s["depends_on"]:
                continue
            own = set(s["outputs"])
            output_names = [o for o in other["outputs"] if producers.get(o) == [other["id"]] and o not in own]
            id_referenced = bool(other["id"] and re.search(
                r"(?<![\w])" + re.escape(other["id"]) + r"(?![\w])", s["instruction"]))
            output_referenced = any(_instruction_references_artifact(
                s["instruction"], o[len("outputs/"):] if o.startswith("outputs/") else o)
                                    for o in output_names)
            if not id_referenced and not output_referenced:
                continue
            if _reaches(steps, other["id"], s["id"]):
                warnings.append(f"step {s['id']}: reference to {other['id']} not added (would create a cycle)")
                continue
            s["depends_on"].append(other["id"])
            warnings.append(f"step {s['id']}: added dependency on {other['id']} referenced in instruction")
    for s in steps:
        if s["agent_id"] not in known:
            warnings.append(f"step {s['id']}: unknown agent {s['agent_id']!r}")
    if vocab is not None:  # after _contain_outputs, so names pair with the outputs the runner will collect
        for s in steps:
            entries, issues = output_types.normalize_entries(s["outputs"], raw_types.get(s["id"]), vocab)
            if entries:
                s["output_types"] = entries
            warning = output_types.issue_warning(s["id"], issues)
            if warning:
                warnings.append(warning)
            if stats is not None:
                stats.update(output_types.add_stats(stats, output_types.stats(s["outputs"], entries, issues)))
    # Reject cycles before dispatch.
    indeg = {s["id"]: len(s["depends_on"]) for s in steps}
    children: dict[str, list[str]] = {s["id"]: [] for s in steps}
    for s in steps:
        for d in s["depends_on"]:
            children[d].append(s["id"])
    queue, visited = [k for k, v in indeg.items() if v == 0], 0
    while queue:
        k = queue.pop()
        visited += 1
        for c in children[k]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if visited != len(steps):
        raise ValueError("plan has a dependency cycle")
    return steps, warnings


def prepare_research_declarations(plan: Any, vocab: output_vocab.Vocab | None, stats: dict) -> Any:
    """A copy of a fresh research PLAN whose steps carry normalized ``output_types`` (or none when off).

    Only plans the CSO just returned go through here; a stored plan keeps the declarations it was approved with."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return plan
    plan = {**plan, "steps": [dict(step) if isinstance(step, dict) else step for step in plan["steps"]]}
    for step in plan["steps"]:
        if not isinstance(step, dict):
            continue
        raw = step.pop("output_types", None)
        if vocab is None:
            continue
        outputs = [str(o) for o in step.get("outputs") or []] if isinstance(step.get("outputs"), list) else []
        entries, issues = output_types.normalize_entries(outputs, raw, vocab)
        if entries:
            step["output_types"] = entries
        stats.update(output_types.add_stats(stats, output_types.stats(outputs, entries, issues)))
    return plan


MAX_PLAN_PROBLEMS = 30


def _problem_lines(problems: list[str], bullet: str) -> list[str]:
    lines = [(f"{index}. " if bullet == "1." else bullet) + short(problem, 1500)
             for index, problem in enumerate(problems[:MAX_PLAN_PROBLEMS], 1)]
    if len(problems) > MAX_PLAN_PROBLEMS:
        lines.append(f"... +{len(problems) - MAX_PLAN_PROBLEMS}")
    return lines


def plan_correction(problems: list[str]) -> str:
    """Every problem of the rejected PLAN, so one correction can fix them all (#222)."""
    return "\n".join(["The previous research PLAN failed validation. Fix every problem below and return one complete "
                      "corrected PLAN. Do not remove steps by truncation. labhq writes protocol.packs.",
                      *_problem_lines(problems, "1.")])


def plan_invalid_report(problems: list[str], packs: dict[str, Any]) -> str:
    """What the PI reads when the corrected PLAN still fails: the problems, and the packs it was held to."""
    lines = [f"연구 계획 검증 실패: CSO가 한 번 고친 계획도 계약 검사를 통과하지 못해 CP1 승인 카드를 만들지 "
             f"않았습니다(남은 문제 {len(problems)}건).", "", "남은 문제:", *_problem_lines(problems, "- ")]
    if packs:
        lines += ["", "설정된 domain pack: " + "; ".join(f"{key} ({loaded.pack.applies_when})"
                                                      for key, loaded in sorted(packs.items())),
                  "요청 대상이 이 pack과 다르면 pack 값을 채울 수 없습니다. 그때는 `research.active_packs`를 확인하세요."]
    return "\n".join(lines)


class BudgetExceeded(RuntimeError):
    pass


def valid_review(value: Any, schema: dict[str, Any] = REVIEW_SCHEMA) -> bool:
    """Check every required field of ``schema`` (REVIEW_SCHEMA unless given) without a new JSON Schema dependency."""
    def matches(item: Any, schema: dict) -> bool:
        typ = schema.get("type")
        if typ == "object":
            if not isinstance(item, dict):
                return False
            props = schema["properties"]
            if not set(schema.get("required", [])).issubset(item):
                return False
            if schema.get("additionalProperties") is False and set(item) - set(props):
                return False
            return all(matches(v, props[k]) for k, v in item.items())
        if typ == "array":
            return isinstance(item, list) and all(matches(v, schema["items"]) for v in item)
        if typ == "integer":
            return (type(item) is int and schema.get("minimum", item) <= item <= schema.get("maximum", item))
        if typ == "string":
            return isinstance(item, str) and item in schema.get("enum", [item])
        return False

    return matches(value, schema)


def blocking_question(result: TaskResult) -> str | None:
    """The PI decision a step stopped for (STEP_PROMPT), from the runner field or its JSON, else None.

    STEP_PROMPT puts each choice on its own line, so a step may write a real newline inside the JSON string;
    that still reads as the question instead of dropping it (#342 review).
    """
    structured = (result.structured if isinstance(result.structured, dict)
                  else extract_json(result.text, strict=False))
    question = result.blocking_decision or (structured.get("blocking_decision") if isinstance(structured, dict)
                                            else None)
    return question.strip() if isinstance(question, str) and question.strip() else None


def failure_kind(outcome: TaskResult | BaseException) -> str | None:
    """Classify dispatch outcomes with this retry table.

    Outcome                                      Kind
    Successful result                            None
    Offline, timeout, empty result/CLI stream,   transient
      explicit rate-limit/overload/5xx/network signal
    Policy/approval/budget/cancel/invalid input, terminal
      ordinary nonzero exit, other error

    An exit code alone is never evidence that another run is safe.
    """
    if isinstance(outcome, asyncio.CancelledError):
        return "terminal"
    if isinstance(outcome, BudgetExceeded):
        return "terminal"
    if isinstance(outcome, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return "transient"
    if isinstance(outcome, BaseException):
        return "terminal"
    if outcome.error_kind == "ask_rejected":
        return "terminal"
    if outcome.ok and (outcome.text.strip() or outcome.structured is not None or waiting(outcome)):
        return None
    if outcome.pending_jobs:
        # A failed CLI turn can still have submitted live jobs. Replaying the step
        # risks another qsub even when the CLI supports session resume.
        return "terminal"
    error = (outcome.error or "").lower()
    if outcome.quota_reset_at is not None or is_quota_error("", error):
        return "quota"
    if any(word in error for word in ("policy", "permission", "denied", "approval", "auth",
                                      "budget", "cancel", "ineligibletier", "401")):
        return "terminal"
    if (("invalid model selection" in error and "is not recognized" in error)
            or any(word in error for word in ("model catalog", "model catalogue", "failed to fetch models"))):
        return "transient"
    if any(word in error for word in ("validat", "invalid", "not found")):
        return "terminal"
    if outcome.ok or (not outcome.text.strip() and
                      (not error or "empty cli stream" in error or "no result event" in error)):
        return "transient"
    if any(word in error for word in ("timeout", "timed out", "rate limit", "rate-limit", "429",
                                      "overload", "capacity", "temporar", "resource exhausted",
                                      "too many requests", "connection reset", "connection refused",
                                      "connection aborted", "broken pipe", "network unreachable",
                                      "runner restarted", "try again")):
        return "transient"
    if re.search(r"\b5\d{2}\b|\b5xx\b", error):
        return "transient"
    return "terminal"


def holds_session(entry: dict, agent_id: str, session_id: str | None, workdir: str | None) -> bool:
    """Whether a task-ledger entry may still use this agent's session or workdir (#93, #112, #144).

    A task holds them until its result is recorded. That includes dispatched-but-not-yet-accepted
    tasks (delivery can be in flight) and tasks a previous gateway started, because the runner keeps
    running what it accepted. An abandoned task has no reported outcome, so it still holds them.
    """
    payload = entry.get("payload") or {}
    if payload.get("agent_id") != agent_id or (entry.get("completed") and not entry.get("abandoned")):
        return False
    held = (payload.get("meta") or {}).get("workdir")
    return bool((session_id and payload.get("resume_session_id") == session_id) or
                (workdir and held and Path(workdir).resolve() == Path(held).resolve()))


def _cost_summary_field(req: dict) -> dict:
    """The follow-up event carries the request's cost summary only once there is one (#270)."""
    return {"cost_summary": req["cost_summary"]} if req.get("cost_summary") else {}


class Orchestrator:
    def __init__(self, hub: "Hub"):
        self.hub = hub
        self.cfg = hub.s.orchestrator
        self.cost: dict[str, float] = {}
        # Task IDs whose cost is already in self.cost; durable totals can run ahead of run_step.
        self.cost_tasks: dict[str, set[str]] = {}
        # Counted tasks whose cost is unaccounted: each is held at per_task_usd when the cap is judged (#270).
        self.cost_unknown: dict[str, set[str]] = {}
        self.attempts: dict[str, dict[str, int]] = {}
        self.budget_locks: dict[str, asyncio.Lock] = {}
        self.budget_denials: dict[str, str] = {}
        self.budget_outcomes: dict[str, list[dict]] = {}
        self.consult_locks: dict[tuple[str | None, str], tuple[asyncio.Lock, int]] = {}

    async def _emit(self, rid: str, typ: str, data: dict) -> None:
        await self.hub.publish({"type": typ, "ts": time.time(), "request_id": rid, "data": data})

    def _output_vocab(self) -> output_vocab.Vocab | None:
        """The vocabulary when plan.declare_output_types is on and it loads; None means today's plan, unchanged."""
        plan = getattr(self.hub.s, "plan", None)
        return output_vocab.current() if getattr(plan, "declare_output_types", False) else None

    def _request_identity(self, rid: str | None, agent_id: str | None) -> dict[str, str] | None:
        """The engine and model a request-local ``cso_model`` gives `agent_id` in request `rid` (#272), or None.

        Every CSO task of the request takes it: plan, replan, consult, follow-up, synthesis and a resumed run, so
        the saved ``cso_session_id`` is always resumed by the engine that made it. A GPT CSO's science reviewer
        gets a different model. Raises when the choice can no longer be honored, never falls back to the registry.
        """
        req = (getattr(self.hub, "requests", None) or {}).get(rid or "") or {}
        model = req.get("cso_model")
        if not model or not agent_id or agent_id not in {self.cfg.cso_agent, self.cfg.reviewer_agent}:
            return None
        if model not in self.cfg.cso_models:  # the config changed after the request was accepted
            raise ValueError(f"cso_model {model!r} is no longer in orchestrator.cso_models; "
                             "the request cannot keep its CSO")

        def engine_for(name: str) -> str:
            return "codex" if name.casefold().startswith("gpt-") else "claude_code"

        if agent_id == self.cfg.cso_agent:
            return {"engine": engine_for(model), "model": model}
        if not model.casefold().startswith("gpt-"):
            return None

        # A GPT CSO must not review its own work with the same model. Prefer the configured CSO's
        # ordinary Claude model; if a custom roster lacks one, use another allowed model.
        ordinary = (getattr(self.hub, "agents", None) or {}).get(self.cfg.cso_agent, {})
        reviewer_model = ordinary.get("model")
        reviewer_engine = ordinary.get("engine")
        if not reviewer_model or reviewer_model == model:
            reviewer_model = next((candidate for candidate in self.cfg.cso_models if candidate != model), None)
            reviewer_engine = None
        if not reviewer_model or reviewer_model == model:
            raise ValueError("a GPT CSO requires a different configured science reviewer model")
        return {"engine": reviewer_engine or engine_for(reviewer_model), "model": reviewer_model}

    def _with_request_identity(self, task: Task) -> Task:
        """`task` carrying its request's CSO identity; a task that already names another identity is refused."""
        identity = self._request_identity(task.request_id, task.agent_id)
        if identity is None:
            return task
        sent = task.meta.get("agent_identity")
        if sent is not None and sent != identity:
            raise ValueError(f"task names agent identity {sent!r}, but its request uses {identity!r}")
        return task.model_copy(update={"meta": {**task.meta, "agent_identity": identity}})

    def _engine(self, rid: str | None, agent_id: str) -> str | None:
        """The engine `agent_id` runs on for request `rid`: the request's CSO identity, else the roster's."""
        identity = self._request_identity(rid, agent_id)
        return identity["engine"] if identity else (self.hub.agents.get(agent_id) or {}).get("engine")

    def _type_meta(self, step: dict) -> dict[str, Any]:
        """Dispatch meta for a step's declarations. Off: nothing (stored declarations stay in the plan, unused).
        Declarations keep the vocabulary version they were made under, never today's."""
        vocab = self._output_vocab()
        if vocab is None:
            return {}
        entries = [e for e in step.get("output_types") or [] if isinstance(e, dict)]
        version = entries[0].get("vocab") if entries else vocab.sha256
        kept = [e for e in entries if e.get("vocab") == version]
        meta: dict[str, Any] = {"output_types_vocab": version}
        if kept:
            meta["output_types"] = output_types.meta_declarations(kept)
        return meta

    def _last_agent_session(self, request_id: str | None, agent_id: str) -> tuple[str | None, str | None]:
        candidates = []
        for entry in self.hub.store.all("task").values():
            payload = entry.get("payload") or {}
            result = entry.get("result") or {}
            if (entry.get("request_id") == request_id and payload.get("agent_id") == agent_id and
                    result.get("session_id")):
                candidates.append((entry.get("dispatched_at", 0), result["session_id"], result.get("workdir")))
        if not candidates:
            return None, None
        _, session_id, workdir = max(candidates)
        return session_id, workdir

    def _ask_scope(self, body: dict) -> tuple:
        """Recover a stable step identity from the durable task ledger, including old asks."""
        rid, tid = body.get("request_id"), body.get("task_id")
        task = self.hub.store.get("task", tid) if tid else None
        task = task or {}
        meta = (task.get("payload") or {}).get("meta") or {}
        kind = task.get("kind") or meta.get("kind")
        if kind == "direct" or (kind in {None, "wrap_up"} and
                                self.hub.requests.get(rid, {}).get("mode") == "direct"):
            return (rid, "direct")
        sid = task.get("step_id") or meta.get("step_id")
        return (rid, "step", sid) if sid else (rid, "task", tid)

    @asynccontextmanager
    async def _consult_lock(self, request_id: str | None, agent_id: str):
        # Lock the target for the request, including selection of its session/workdir.
        # A session can rotate after each consult; locking its old ID would let a
        # waiter overlap a new consult using the updated ID and the same workdir.
        key = (request_id, agent_id)
        lock, users = self.consult_locks.get(key, (asyncio.Lock(), 0))
        self.consult_locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self.consult_locks[key]
            if users == 1:
                del self.consult_locks[key]
            else:
                self.consult_locks[key] = (lock, users - 1)

    def _consult_resource_busy(self, agent_id: str, session_id: str | None, workdir: str | None) -> bool:
        # The in-memory consult lock does not survive a gateway restart; the durable ledger does (#93).
        store = getattr(self.hub, "store", None)
        return store is not None and any(holds_session(entry, agent_id, session_id, workdir)
                                         for entry in store.all("task").values())

    async def _free_session(self, agent_id: str, session_id: str | None, workdir: str | None, *,
                            rid: str, step: str) -> tuple[str | None, str | None]:
        """The session and workdir any resumed dispatch may use (#112, #144, #208).

        A consult isolates at once because its asker is blocked. These wait for an earlier task
        that still holds them, and start a new session and workdir when its outcome is unknown.
        """
        if not (session_id or workdir):
            return session_id, workdir
        wait = getattr(self.hub, "wait_session_free", None)
        if wait is not None:
            return await wait(agent_id, session_id, workdir, request_id=rid, step_id=step)
        return (None, None) if self._consult_resource_busy(agent_id, session_id, workdir) else (session_id, workdir)

    async def answer_ask(self, ask: AskRequest, runner_id: str) -> None:
        """Route one bounded question. Hard stops are classified before any model runs."""
        normalized_refs = [str(PurePosixPath(ref.replace("\\", "/"))) for ref in ask.refs]
        signature = hashlib.sha256(
            (f"{ask.task_id}\0{ask.to}\0{ask.question.strip().casefold()}\0"
             + json.dumps(normalized_refs, ensure_ascii=False, separators=(",", ":"))).encode("utf-8")
        ).hexdigest()
        entries = self.hub.store.all("ask")
        current = entries.get(ask.id) or {}
        scope = self._ask_scope(ask.model_dump())
        self.hub.store.put("ask", ask.id, {"ask": ask.model_dump(mode="json"), "origin": runner_id,
                                           **current, "signature": signature, "scope": scope})
        for other_id, entry in entries.items():
            if other_id != ask.id and entry.get("signature") == signature and entry.get("state") == "resolved":
                cached = ask_result(**{**entry["answer"], "cached": True})
                await self.hub.resolve_ask(ask, runner_id, cached)
                return

        accepted = [entry for other_id, entry in entries.items() if other_id != ask.id and
                    entry.get("state") != "rejected" and
                    (entry.get("answer") or {}).get("status") != "rejected"]
        same_step = [entry for entry in accepted if
                     tuple(entry.get("scope") or self._ask_scope(entry.get("ask") or {})) == scope]
        task_count = len(same_step)
        target_count = sum((entry.get("ask") or {}).get("to") == ask.to for entry in same_step)
        request_count = sum((entry.get("ask") or {}).get("request_id") == ask.request_id for entry in accepted)
        limit_reason = None
        if task_count >= 3:
            limit_reason = "이 step/task의 질의 상한(3회)에 도달했습니다"
        elif target_count >= 2:
            limit_reason = "이 step/task에서 같은 대상에게 묻는 상한(2회)에 도달했습니다"
        elif request_count >= 12:
            limit_reason = "이 요청의 질의 상한(12회)에 도달했습니다"
        if limit_reason:
            self.hub.store.put("ask", ask.id, {**(self.hub.store.get("ask", ask.id) or {}),
                                                "state": "rejected"})
            await self.hub.resolve_ask(ask, runner_id, ask_result(reason=limit_reason, **{
                "from": "labhq", "routed_to": "cso", "remaining_asks": 0}))
            return

        hard_stops = (self.hub.s.policy.bioinfo_agent.hard_stops
                      if ask.agent_id == "bioinfo-agent" else None)
        stop = hard_stop_kind(ask, hard_stops)
        requested = ask.to
        # Who this ask went to before a gateway restart. The roster lacks that employee until its
        # runner reconnects, and the runner may still be answering it (#113).
        previous = current.get("routed_to")
        status = self.hub.requests.get(ask.request_id or "", {}).get("status")
        seen_agent = getattr(self.hub, "has_seen_agent", lambda _agent_id: False)
        if stop:
            routed = "pi"
        elif requested == "pi":
            routed = "cso"
        elif requested == "facilities":
            routed = (previous if previous in {"facilities", "cso"} else
                      "facilities" if ("facilities" in self.hub.agents or
                                       (status == "waiting_for_runner" and seen_agent("facilities"))) else "cso")
        else:
            routed = requested.removeprefix("colleague:") if requested.startswith("colleague:") else requested

        entry = self.hub.store.get("ask", ask.id) or {}
        self.hub.store.put("ask", ask.id, {**entry, "hard_stop": stop, "routed_to": routed})
        if routed == "pi":
            decision = await self.hub.request_approval(
                kind="question", request_id=ask.request_id,
                summary=f"Hard stop ({stop}): {ask.question}",
                detail={"ask_id": ask.id, "from": ask.agent_id, "why_blocked": ask.why_blocked,
                        "options": ask.options},
            )
            note = str(decision.get("note") or "").strip()
            answer = note or ("PI가 진행을 허용하지 않았습니다" if not decision.get("approved") else
                              "PI가 승인했지만 답변을 남기지 않았습니다")
            await self.hub.resolve_ask(ask, runner_id, ask_result(answer=answer, decision=decision,
                **{"from": "pi", "routed_to": "pi", "hard_stop": stop}))
            return

        wait_online = getattr(self.hub, "wait_agent_online", None)
        if routed not in self.hub.agents and wait_online and (status == "waiting_for_runner" or routed == previous):
            # Resume approval re-routes asks before runners reconnect, and the roster is empty
            # until they do. The target's runner may also still hold this ask's consult (#93, #113).
            await wait_online(routed, self.hub.s.gateway.resume_wait_s)
        if requested == "facilities" and routed == "facilities" and routed not in self.hub.agents:
            routed = "cso"  # its runner did not come back; the CSO answers in its own session
            self.hub.store.put("ask", ask.id, {**(self.hub.store.get("ask", ask.id) or {}), "routed_to": routed})
            if routed not in self.hub.agents and wait_online and status == "waiting_for_runner":
                await wait_online(routed, self.hub.s.gateway.resume_wait_s)
        if routed not in self.hub.agents:
            await self.hub.resolve_ask(ask, runner_id, ask_result(
                reason=f"대상 직원 {routed!r}이 roster에 없습니다",
                **{"from": "labhq", "routed_to": routed}))
            return
        try:
            refusal = read_only_refusal(routed, self._engine(ask.request_id, routed))
        except ValueError as error:  # its request's CSO model cannot be honored: never answer as another model
            refusal = str(error)
        if refusal:  # a consult runs as the runner's read-only profile; an engine that ignores it could write
            await self.hub.resolve_ask(ask, runner_id, ask_result(
                reason=refusal, **{"from": "labhq", "routed_to": routed}))
            return

        # A task workdir belongs to its runner's filesystem. The gateway only forwards it when the
        # consult will run on that same runner; the runner validates the path and refs before launch.
        same_runner = self.hub.agent_runner.get(routed) == runner_id
        refs = normalized_refs if same_runner else []
        source_workdir = ask.source_workdir if refs and ask.source_workdir else None
        reference_note = ("참고 파일은 다른 runner에 있어 읽을 수 없다"
                          if ask.refs and not same_runner else "")
        request = self.hub.requests.get(ask.request_id or "", {})
        prompt = CONSULT_PROMPT.format(
            sender=ask.agent_id, question=ask.question, why_blocked=ask.why_blocked,
            tried=json.dumps(ask.tried, ensure_ascii=False), options=json.dumps(ask.options, ensure_ascii=False),
            refs=json.dumps(refs, ensure_ascii=False), reference_note=reference_note,
            request=clip(request.get("text") or "", 4000), plan=clip(json.dumps(request.get("plan") or {},
                                                                                 ensure_ascii=False), 6000),
        )
        overrides = dict(READ_ONLY_OVERRIDES)
        async with self._consult_lock(ask.request_id, routed):
            # After a gateway restart the runner may still be running this ask's consult.
            # Adopt its result; when its outcome is unknown, isolate the new consult.
            adopt = getattr(self.hub, "adopt_consult", None)
            prior, result = await adopt(ask.id, routed) if adopt else (0, None)
            first_attempt = 1
            if result is not None:
                self._count_adopted_cost(ask.request_id, result.task_id)
                if failure_kind(result) == "transient" and prior < self.cfg.step_max_attempts:
                    result, first_attempt = None, prior + 1  # only the retries that are left
            elif prior:
                first_attempt = min(prior, self.cfg.step_max_attempts)  # its outcome was never observed
            if result is None:
                session_id, workdir = self._last_agent_session(ask.request_id, routed)
                if requested.startswith("colleague:"):
                    session_id, workdir = None, None
                if routed == self.cfg.cso_agent:
                    session_id = request.get("cso_session_id") or session_id
                    workdir = request.get("cso_workdir") or workdir
                if prior or self._consult_resource_busy(routed, session_id, workdir):
                    session_id, workdir = None, None
                consult = Task(
                    agent_id=routed, request_id=ask.request_id, prompt=prompt,
                    resume_session_id=session_id if self.hub.supports_resume(routed) else None,
                    meta={"kind": "consult", "ask_id": ask.id, "title": f"{ask.agent_id} 질의 답변",
                          "agent_overrides": overrides,
                          **({**({"source_workdir": source_workdir} if source_workdir else {}),
                              "consult_refs": refs} if refs else {}),
                          **({"workdir": workdir} if workdir else {})},
                )
                result = await (self.run_step(consult, first_attempt=first_attempt) if first_attempt > 1
                                else self.run_step(consult))
            if routed == self.cfg.cso_agent and result.session_id:
                request["cso_session_id"], request["cso_workdir"] = result.session_id, result.workdir
                if ask.request_id in self.hub.requests:
                    self.hub.save_request(ask.request_id)
        answered = result.ok and bool(result.text.strip())
        answer = result.text.strip() if answered else f"상담 실패: {result.error or 'empty answer'}"
        await self.hub.resolve_ask(ask, runner_id, ask_result(
            answer=answer if answered else None, reason=None if answered else answer,
            **{"from": routed, "routed_to": routed, "remaining_asks": max(0, 2 - task_count)}))

    # ---------- follow-up on a finished request (#36) ----------
    async def run_followup(self, rid: str, fid: str) -> None:
        """Resume the request's CSO (or direct agent) session in its workspace; the request stays finished."""
        req = self.hub.requests[rid]
        entry = next(f for f in req.get("followups") or [] if f.get("id") == fid)
        agent, direct = entry["agent_id"], req.get("mode") == "direct"
        try:
            refusal = read_only_refusal(agent, self._engine(rid, agent))
        except ValueError as error:  # its request's CSO model cannot be honored: never resume as another model
            refusal = str(error)
        if refusal:  # the same workspace and session, with an engine that would not keep it read-only
            entry.update(status="failed", answer="", error=refusal, answered_at=time.time())
            self.hub.save_request(rid)
            if getattr(self.hub, "semantics_shadow", None) is not None:  # semantics-hook: actions
                self.hub.semantics_shadow.after_followup(rid, fid, "ended", "refused_read_only")  # semantics-hook: actions
            await self._emit(rid, "request.followup_done", {
                "id": fid, "ok": False, "answer": "", "error": refusal,
                "cost_usd": float(req.get("cost_usd") or 0), "cost_known": req.get("cost_known", True),
                **_cost_summary_field(req)})
            return
        if direct:
            session_id, workdir = self._last_agent_session(rid, agent)
        else:
            session_id, workdir = req.get("cso_session_id"), req.get("cso_workdir")
        await self._emit(rid, "request.followup", {"id": fid, "text": entry["text"], "agent_id": agent,
                                                   "status": "running"})
        # A restart marks the running follow-up interrupted, but its runner may still answer it in this
        # session and workdir (#144).
        session_id, workdir = await self._free_session(agent, session_id, workdir, rid=rid, step="followup")
        resumable = bool(session_id and self.hub.supports_resume(agent))
        earlier = [f for f in req.get("followups") or [] if f.get("id") != fid and f.get("status") == "done"][-3:]
        history = "".join(f"\nEarlier follow-up: {f.get('text')}\nYour answer: {clip(f.get('answer') or '', 1500)}\n"
                          for f in earlier)
        outputs = [r["workdir"] for r in (req.get("results") or {}).values()
                   if isinstance(r, dict) and r.get("workdir") and r.get("outputs")]
        task = Task(agent_id=agent, request_id=rid, resume_session_id=session_id if resumable else None,
                    prompt=FOLLOWUP_PROMPT.format(
                        request=clip((req.get("text") or "") + render_references(req.get("references")), 4000),
                        report=clip(req.get("report") or "(no report)", 6000), history=history,
                        question=entry["text"]),
                    meta={**reference_meta(req), "kind": "followup", "followup_id": fid,
                          "title": f"이어 묻기: {entry['text'][:80]}", "request": req.get("text") or "",
                          "agent_overrides": dict(READ_ONLY_OVERRIDES), "upstream_dirs": list(dict.fromkeys(outputs)),
                          **({"workdir": workdir} if workdir else {})})
        self.cost[rid] = max(self.cost.get(rid, 0.0), float(req.get("cost_usd") or 0))
        self._seed_cost(rid, req)
        try:
            result = await self.run_step(task)
        except BudgetExceeded as error:
            result = TaskResult(task_id=task.id, agent_id=agent, ok=False, error=str(error))
        except Exception as error:  # the follow-up must end in a recorded state, never stay "running"
            result = TaskResult(task_id=task.id, agent_id=agent, ok=False, error=f"{type(error).__name__}: {error}")
        answered = result.ok and bool(result.text.strip())
        entry.update(status="done" if answered else "failed", answer=clip(result.text.strip(), 20000) if answered else "",
                     error=None if answered else (result.error or "empty answer"), task_id=result.task_id,
                     resumed_session=task.resume_session_id, answered_at=time.time())
        if not direct and result.session_id and self.hub.supports_resume(agent):
            req["cso_session_id"], req["cso_workdir"] = result.session_id, result.workdir or workdir
        self.hub.save_request(rid)
        if getattr(self.hub, "semantics_shadow", None) is not None:  # semantics-hook: actions
            self.hub.semantics_shadow.after_followup(rid, fid, "ended", "done" if answered else "failed")  # semantics-hook: actions
        await self._emit(rid, "request.followup_done", {
            "id": fid, "ok": answered, "answer": clip(entry["answer"], 20000), "error": entry["error"],
            "cost_usd": float(req.get("cost_usd") or 0), "cost_known": req.get("cost_known", True),
            **_cost_summary_field(req)})

    def _count_adopted_cost(self, rid: str | None, task_id: str) -> None:
        """Add an adopted task's recorded cost once (#93).

        The durable request total can already hold parallel tasks whose run_step has not
        added them yet, so copying that total would count those tasks twice.
        """
        if rid in self.hub.requests:
            self._count_cost(rid, task_id)

    def _count_cost(self, rid: str | None, task_id: str, result: TaskResult | None = None) -> None:
        """Count a task once: the gateway's classification when it has one, else this result's (#270)."""
        if not rid:
            return
        counted = self.cost_tasks.setdefault(rid, set())
        if task_id in counted:
            return
        counted.add(task_id)
        req = self.hub.requests.get(rid) or {}
        item = (req.get("cost_items") or {}).get(task_id)
        if item is None and result is not None:
            item = task_cost_item(result, (getattr(self.hub, "agents", None) or {}).get(result.agent_id))
        if item is None:  # recorded before costs were classified
            amount = float((req.get("cost_by_task") or {}).get(task_id) or 0)
        elif item.get("status") == "unknown":
            self.cost_unknown.setdefault(rid, set()).add(task_id)
            self.cost.setdefault(rid, 0.0)
            return
        else:
            amount = float(item.get("usd") or 0)
        self.cost[rid] = self.cost.get(rid, 0.0) + amount

    def _seed_cost(self, rid: str, req: dict) -> None:
        """Restore unaccounted tasks from the durable record; a restart must not forget them."""
        unknown = self.cost_unknown.setdefault(rid, set())
        items = req.get("cost_items") or {}
        unknown.update(tid for tid, item in items.items() if item.get("status") == "unknown")
        if req.get("cost_known") is False and not unknown:
            unknown.add("recorded-before-classification")

    # ---------- one agent step, including HPC hibernate/wake cycles ----------
    async def run_step(self, task: Task, first_attempt: int = 1) -> TaskResult:
        task = self._with_request_identity(task)
        rid = task.request_id or ""
        initial_attempt = first_attempt
        engine = str((self.hub.agents.get(task.agent_id) or {}).get("engine") or "")

        def quota_deadline() -> float:
            req = self.hub.requests[rid]
            windows = req.setdefault("quota_windows", {})
            if engine not in windows:
                started = time.time()
                windows[engine] = {"started_at": started,
                                   "deadline_at": started + self.cfg.quota_max_wait_s}
                self.hub.save_request(rid)
            return float(windows[engine]["deadline_at"])

        def clear_quota_window() -> None:
            req = self.hub.requests.get(rid) or {}
            if (req.get("quota_windows") or {}).pop(engine, None) is not None:
                if not req.get("quota_windows"):
                    req.pop("quota_windows", None)
                self.hub.save_request(rid)

        def quota_failure(current: Task, reason: str) -> TaskResult:
            return TaskResult(task_id=current.id, agent_id=current.agent_id, ok=False,
                              error_kind="quota_wait_limit", error=reason)

        async def dispatch_with_retry(current: Task, max_attempts: int | None = None,
                                      start: int = 1) -> TaskResult:
            key = str(current.meta.get("step_id") or current.meta.get("kind") or current.id)
            limit = max_attempts or self.cfg.step_max_attempts
            first_attempt = min(max(getattr(self.hub, "recovery_attempt", lambda _task: 1)(current), start),
                                limit)
            previous_workdir = current.meta.get("workdir")
            previous_session = current.resume_session_id
            previous_result = None
            retry_answers = []
            for attempt in range(first_attempt, limit + 1):
                await self._check_budget(rid)
                hold = getattr(self.hub, "quota_hold", lambda _engine: None)(engine)
                if hold:
                    deadline = quota_deadline()
                    if not await self.hub.wait_quota(rid, key, engine,
                                                     resume_at=float(hold["resume_at"]),
                                                     deadline_at=deadline,
                                                     reason="same engine account is waiting for quota"):
                        return quota_failure(current, "subscription quota wait exceeded the configured maximum")
                    await self._check_budget(rid)  # a parallel step may have spent or been denied it meanwhile
                self.attempts.setdefault(rid, {})[key] = self.attempts.get(rid, {}).get(key, 0) + 1
                attempt_task = current.model_copy(update={"id": current.id if attempt == 1 else new_id("task"),
                                                  "resume_session_id": previous_session,
                                                  "meta": {**current.meta, "attempt": attempt,
                                                           **({"workdir": previous_workdir} if previous_workdir else {})}})
                if attempt > 1:
                    can_resume = bool(previous_session and self.hub.supports_resume(current.agent_id))
                    attempt_task = attempt_task.model_copy(update={
                        "prompt": continuation_prompt(
                            current, "\n\n".join(["Retry the same task after the transient failure.", *retry_answers]),
                            resumable=can_resume,
                            previous_result=previous_result, context_chars=self.cfg.context_chars_per_step),
                        "context": "", "resume_session_id": previous_session if can_resume else None})
                await self._emit(rid, "request.step_attempt", {"step_id": key, "attempt": attempt})
                offline = False
                try:
                    res = await self.hub.dispatch(attempt_task)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    res = TaskResult(task_id=attempt_task.id, agent_id=current.agent_id, ok=False, error=str(exc))
                    kind = failure_kind(exc)
                    offline = isinstance(exc, RunnerUnavailable)
                else:
                    self._count_cost(rid, res.task_id, res)
                    kind = failure_kind(res)
                    previous_workdir = res.workdir or previous_workdir
                    if res.session_id and self.hub.supports_resume(current.agent_id):
                        previous_session = res.session_id
                # Audit even failed/exceptional attempts. Keep answered asks across
                # every retry, including engines without session resume.
                answers = getattr(self.hub, "ask_results_for_task", lambda _tid: [])(res.task_id)
                outcome = read_ask_results(answers)
                if outcome["status"] == "rejected":
                    res = rejected_step(res, outcome["reason"])
                    kind = "terminal"
                elif outcome["answer"]:
                    update = ASK_WAKE_PROMPT.format(answers=outcome["answer"])
                    if update not in retry_answers:
                        retry_answers.append(update)
                await self._check_budget(rid, block=False)
                previous_result = res
                if kind is None:
                    return res
                if rid in self.budget_denials:
                    if res.ok:
                        res = res.model_copy(update={"ok": False, "error": "empty result"})
                    return res  # this attempt ran; only its not-yet-started retry is blocked
                if kind != "transient" or attempt == limit:
                    if res.ok:
                        res = res.model_copy(update={"ok": False, "error": "empty result"})
                    return res
                if offline:
                    await self._emit(rid, "request.step_wait", {"step_id": key, "agent_id": current.agent_id,
                                                                 "reason": "runner offline"})
                    if not await self.hub.wait_agent_online(current.agent_id, self.cfg.runner_reconnect_timeout_s):
                        return res.model_copy(update={"error": "runner reconnect timeout"})
                else:
                    await asyncio.sleep(self.cfg.step_retry_backoff_s * 2 ** (attempt - 1))
                await self._emit(rid, "request.step_retry", {"step_id": key, "attempt": attempt,
                                                               "next_attempt": attempt + 1,
                                                               "reason": res.error or "empty result"})
            raise AssertionError("unreachable")

        async def dispatch_turn(turn: Task, max_attempts: int | None = None, start: int = 1) -> TaskResult:
            """Every turn of the step, wake and wrap-up included, parks on a subscription quota and resumes."""
            current = turn
            res = await dispatch_with_retry(current, max_attempts, start)
            while True:
                quota = None if res.ok else received_quota_wait(engine, res.error, res.quota_reset_at,
                                                                 default_wait_s=self.cfg.quota_default_wait_s)
                if quota is None:
                    clear_quota_window()
                    return res
                deadline = quota_deadline()
                window = self.hub.requests[rid]["quota_windows"][engine]
                if window.get("task_id") == res.task_id:
                    resume_at = float(window["resume_at"])
                else:
                    resume_at = quota.resume_at
                    window.update(task_id=res.task_id, resume_at=resume_at, reset_time_parsed=quota.parsed)
                    self.hub.save_request(rid)
                if resume_at > deadline:
                    return quota_failure(current, f"subscription quota reset exceeds the configured maximum; "
                                                  f"reset={resume_at:.0f}, deadline={deadline:.0f}")
                key = str(current.meta.get("step_id") or current.meta.get("kind") or current.id)
                if not await self.hub.wait_quota(rid, key, engine, resume_at=resume_at,
                                                 deadline_at=deadline, reason=res.error or "subscription quota"):
                    return quota_failure(current, "subscription quota wait exceeded the configured maximum")
                can_resume = bool(res.session_id and self.hub.supports_resume(current.agent_id))
                # The turn that hit the quota is the base, so a wake turn keeps its job results and ask answers.
                current = Task(agent_id=current.agent_id, request_id=current.request_id,
                               output_schema=current.output_schema,
                               prompt=continuation_prompt(turn, "The subscription quota has reset. "
                                                                "Continue the same task.",
                                                          resumable=can_resume, previous_result=res,
                                                          context_chars=self.cfg.context_chars_per_step),
                               meta={**current.meta, "kind": current.meta.get("kind", "step"),
                                     "parent_task": res.task_id,
                                     **({"workdir": res.workdir} if res.workdir else {})},
                               resume_session_id=res.session_id if can_resume else None)
                res = await dispatch_with_retry(current, max_attempts)  # its first gate rechecks the budget

        res = await dispatch_turn(task, start=initial_attempt)
        overrides = task.meta.get("agent_overrides") or {}
        # A read-only task (consult, follow-up) has nothing to save, and a wrap-up must never lift its limits.
        read_only = is_read_only_task(task.meta)
        if (not res.ok and res.error_kind == "error_max_turns" and res.session_id and not read_only
                and self.hub.supports_resume(task.agent_id)):
            wrap = Task(agent_id=task.agent_id, request_id=rid,
                        prompt=continuation_prompt(task, WRAP_PROMPT, resumable=True,
                                                   previous_result=res, context_chars=self.cfg.context_chars_per_step),
                        resume_session_id=res.session_id,
                        meta={**task.meta, "kind": "wrap_up", "parent_task": res.task_id,
                              "workdir": res.workdir, "agent_overrides": {**overrides, "max_turns": 2},
                              "outputs": ["PARTIAL_STATUS.md"],
                              "collect_direct_outputs": task.meta.get("kind") == "direct"})
            try:
                partial = await dispatch_turn(wrap, max_attempts=1)
                note = ("partial results saved" if partial.outputs else
                        "status note missing" if partial.ok else partial.error)
                # A file the wrap-up rewrote no longer has the first run's hash: it is dropped rather than kept
                # stale, so `labhq verify` reports it unrecorded instead of a mismatch (#58, PR #339 review).
                rewritten = set(partial.unreported_outputs) | set(partial.output_sha256)
                res = res.model_copy(update={"partial_results": bool(partial.outputs),
                                             "outputs": list(dict.fromkeys([*res.outputs, *partial.outputs])),
                                             "output_sha256": {**{path: digest for path, digest in
                                                                  res.output_sha256.items() if path not in rewritten},
                                                               **partial.output_sha256},
                                             "error": f"{res.error or 'error_max_turns'}; wrap-up: {note}"})
            except BudgetExceeded:
                pass
        cycles = 0
        while res.ok and waiting(res) and cycles < self.cfg.max_wake_cycles:
            cycles += 1
            prompts = []
            if res.pending_asks:
                answers = await self.hub.wait_asks(res.pending_asks)
                outcome = read_ask_results(answers)
                if outcome["status"] == "rejected":
                    return rejected_step(res, outcome["reason"])
                prompts.append(ASK_WAKE_PROMPT.format(answers=outcome["answer"]))
            if res.pending_jobs:
                info = await self.hub.wait_jobs(res.task_id)
                jobs = "\n".join(f"- {j['job_id']} ({j.get('name') or ''}): {j['state']} exit={j.get('exit_status')}"
                                 for j in info.get("jobs", []))
                prompts.append(WAKE_PROMPT.format(jobs=jobs, workdir=res.workdir))
            title = ("HPC 결과와 질의 답변을 받고 이어서 작업" if res.pending_jobs and res.pending_asks else
                     "HPC 결과 확인 후 이어서 작업" if res.pending_jobs else "질의 답변을 받고 이어서 작업")
            meta = {**task.meta, "kind": task.meta.get("kind", "step"),
                    "parent_task": res.task_id, "workdir": res.workdir,
                    "title": title}
            can_resume = bool(res.session_id and self.hub.supports_resume(task.agent_id))
            wake = Task(
                agent_id=task.agent_id, request_id=task.request_id, output_schema=task.output_schema,
                prompt=continuation_prompt(task, "\n\n".join(prompts), resumable=can_resume,
                                           previous_result=res, context_chars=self.cfg.context_chars_per_step),
                meta=meta, resume_session_id=res.session_id if can_resume else None,
            )
            res = await dispatch_turn(wake)
        if res.ok and waiting(res):
            res = res.model_copy(update={
                "ok": False, "error_kind": "wake_limit",
                "error": f"wake cycle limit ({self.cfg.max_wake_cycles}) reached; "
                         f"pending asks: {res.pending_asks}; pending jobs: {res.pending_jobs}",
                "pending_asks": [], "pending_jobs": [], "blocking_decision": None,
            })
        return res

    async def _check_budget(self, rid: str, *, block: bool = True) -> None:
        # A completed attempt keeps its result; a denied budget only blocks the next attempt.
        async with self.budget_locks.setdefault(rid, asyncio.Lock()):
            if rid in self.budget_denials:
                if block:
                    raise BudgetExceeded(self.budget_denials[rid])
                return
            req = self.hub.requests.get(rid, {})
            limit = req.get("budget_usd") or self.hub.s.policy.budget.per_request_usd
            if not limit:
                return
            spent, unknown, bound = self._budget_bound(rid, limit)
            if bound <= limit:
                return
            over = spent > limit
            pending = f" + 미집계 {unknown}건" if unknown else ""
            reserve = self._unknown_reserve(limit)
            summary = (f"예산 초과: ${spent:.2f}{pending} / ${limit:.2f} — 계속 진행할까요?" if over else
                       f"예산 판정 불가: 집계 ${spent:.2f}{pending}(건당 ${reserve:.2f}로 보면 ${bound:.2f}) / "
                       f"상한 ${limit:.2f} — 계속 진행할까요?")
            try:
                detail = {"spent_usd": spent, "limit_usd": limit, "requested_budget_usd": max(limit * 2, bound),
                          **({"unknown_count": unknown, "unknown_reserve_usd": reserve} if unknown else {})}
                dec = await self.hub.request_approval(kind="budget", request_id=rid, summary=summary, detail=detail)
            except Exception as exc:
                dec = {"approved": False, "note": str(exc)}
            # include concurrently completed attempts in this decision
            spent, unknown, bound = self._budget_bound(rid, limit)
            approved = bool(dec.get("approved"))
            outcome = {"spent_usd": round(spent, 4), "limit_usd": limit, "approved": approved,
                       **({"unknown_count": unknown} if unknown else {})}
            self.budget_outcomes.setdefault(rid, []).append(outcome)
            await self._emit(rid, "request.budget_exceeded", outcome)
            if approved:
                self.hub.requests[rid]["budget_usd"] = max(limit * 2, bound)
            else:
                pending = f" + {unknown} unaccounted task(s)" if unknown else ""
                reason = (f"budget exceeded (${spent:.2f}{pending} > ${limit:.2f}); approval denied" if over else
                          f"budget unaccounted (${spent:.2f}{pending} may pass ${limit:.2f}); approval denied")
                self.budget_denials[rid] = reason
                if block:
                    raise BudgetExceeded(reason)

    def _unknown_reserve(self, limit: float) -> float:
        """What an unaccounted task is assumed to have spent for the cap (#270): the per-task budget, else the cap."""
        per_task = self.hub.s.policy.budget.per_task_usd
        return float(per_task) if per_task and per_task > 0 else float(limit)

    def _budget_bound(self, rid: str, limit: float) -> tuple[float, int, float]:
        """Counted dollars, unaccounted tasks, and the total the cap is judged on: never $0 for an unknown task."""
        spent = self.cost.get(rid, 0.0)
        unknown = len(self.cost_unknown.get(rid) or ())
        return spent, unknown, spent + unknown * self._unknown_reserve(limit)

    # ---------- DAG ----------
    async def run_dag(self, rid: str, request: str, steps: list[dict], results: dict[str, TaskResult],
                      only: set[str] | None = None, feedback: dict[str, str] | None = None) -> None:
        by_id = {s["id"]: s for s in steps}
        todo = {s["id"] for s in steps if only is None or s["id"] in only}
        running: dict[str, asyncio.Task] = {}
        ancestors: dict[str, set[str]] = {}
        for sid in by_id:
            pending = list(by_id[sid]["depends_on"])
            found: set[str] = set()
            while pending:
                ancestor = pending.pop()
                if ancestor in found:
                    continue
                found.add(ancestor)
                pending.extend(by_id[ancestor]["depends_on"])
            ancestors[sid] = found
        req_state = self.hub.requests.get(rid)
        research_plan = ((req_state or {}).get("plan") if
                         ((req_state or {}).get("research_contract") or {}).get("execution_enabled") else None)
        # PI answers to blocking questions survive a gateway restart (the re-run step still needs them).
        decisions: dict[str, Any] = dict((req_state or {}).get("step_decisions") or {})
        sem = asyncio.Semaphore(self.cfg.max_parallel_steps)

        async def skip(sid: str, reason: str, upstream_ids: list[str] | None = None) -> None:
            previous = results.get(sid)
            if feedback and sid in feedback and previous and previous.ok:
                results[sid] = previous.model_copy(update={"revision_failed": f"skipped: {reason}"})
            else:
                results[sid] = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False,
                                          error=f"skipped: {reason}")
            await self._emit(rid, "request.step_skipped", {"step_id": sid, "status": "skipped",
                                                            "upstream": upstream_ids or [], "reason": reason})

        def upstream(step: dict) -> str:
            parts = []
            for d in step["depends_on"]:
                r = results.get(d)
                if r:
                    head = f"## {d} · {by_id[d]['agent_id']}" + ("" if r.ok else f" (FAILED: {short(r.error, 200)})")
                    artifacts = [{"workdir_id": r.workdir_id, "path": path}
                                 for path in r.outputs]
                    paths = "\n".join(str(Path(r.workdir) / path) for path in r.outputs) if r.workdir else ""
                    parts.append(f"{head}\nDeclared output artifacts: {json.dumps(artifacts)}\n"
                                 f"Readable files:\n{paths}\n{clip(r.text, self.cfg.context_chars_per_step)}")
            return "\n\n".join(parts)

        async def run_one(step: dict) -> TaskResult:
            prompt = STEP_PROMPT.format(request=request, step_id=step["id"], instruction=step["instruction"])
            if research_plan:
                contract = ((req_state or {}).get("research_contract") or {})
                prompt += ("\n\nReturn the structured research result contract required by the output schema. "
                           f"Copy plan_sha256={contract.get('plan_sha256')}, step_id={step['id']}, "
                           f"claim_ids={json.dumps(step.get('claim_ids') or [])}, and fill evidence_slots="
                           f"{json.dumps(step.get('evidence_slots') or [])}. Keep claims and evidence separate. "
                           "Each artifact_refs path is one of your declared outputs (outputs/<name>) or an upstream "
                           "artifact written as <workdir_id>/<path>; evidence citing any other path is refused at CP2. "
                           "If you cannot proceed without a PI decision, return the same schema with every list "
                           "empty and the question with its choices in blocking_decision; you re-run with the answer.")
            declared = [rel for rel in map(output_relpath, step.get("outputs") or []) if rel]
            if declared:
                prompt += STEP_OUTPUTS_RULE.format(paths=", ".join(f"./{rel}" for rel in declared))
            decision = decisions.get(step["id"])
            ctx = upstream(step)
            updates = []
            if decision:
                outcome = read_ask_results([decision])
                if outcome["status"] == "rejected":
                    previous = (TaskResult.model_validate(decision["previous_result"])
                                if decision.get("previous_result") else
                                TaskResult(task_id="", agent_id=step["agent_id"], ok=True))
                    return rejected_step(previous, outcome["reason"])
                updates.append(ASK_WAKE_PROMPT.format(answers=qa_text(decision)))
            revising = bool(feedback and step["id"] in feedback)
            if revising:
                updates.append(f"[Scientific reviewer feedback — revise your step]\n{feedback[step['id']]}")
            previous = results.get(step["id"])
            if previous is None and decision and decision.get("previous_result"):
                previous = TaskResult.model_validate(decision["previous_result"])
            session_id = (previous.session_id if previous and revising else
                          decision.get("session_id") if decision else None)
            workdir = (decision.get("workdir") if decision and decision.get("workdir") else
                       previous.workdir if previous and previous.workdir and revising else None)
            if session_id or workdir:
                session_id, workdir = await self._free_session(
                    step["agent_id"], session_id, workdir, rid=rid, step=step["id"])
            can_resume = bool(session_id and self.hub.supports_resume(step["agent_id"]))
            upstream_dirs = [results[d].workdir for d in step["depends_on"]
                             if d in results and results[d].workdir and results[d].outputs]
            task = Task(agent_id=step["agent_id"], request_id=rid, prompt=prompt, context=ctx,
                        output_schema=RESEARCH_STEP_SCHEMA if research_plan else None,
                        resume_session_id=session_id if can_resume else None,
                        meta={**reference_meta(self.hub.requests.get(rid)),
                              "kind": "step", "step_id": step["id"], "request": request,
                              "instruction": step["instruction"],
                              "revision": self.hub.requests.get(rid, {}).get("pending_revisions", {})
                              .get(step["id"], {}).get("revision", 0),
                              "title": f"{step['id']}: {step['instruction'][:100]}" + (" (리뷰 반영 수정)" if feedback else ""),
                               "project_dirs": self.hub.requests.get(rid, {}).get("project_dirs", []),
                               "upstream_dirs": upstream_dirs, "outputs": step.get("outputs", []),
                               **self._type_meta(step),
                               **({"workdir": workdir} if workdir else {})})
            if updates:
                task = task.model_copy(update={
                    "prompt": continuation_prompt(task, "\n\n".join(updates), resumable=can_resume,
                                                   previous_result=previous,
                                                   context_chars=self.cfg.context_chars_per_step),
                    "context": ""})
            async with sem:
                return await self.run_step(task)

        while todo or running:
            # An unchanged bridge outside `only` still connects a revision to an earlier revised ancestor. Waiting
            # on every ancestor in this run prevents a downstream revision from reading the bridge's stale result.
            ready = [sid for sid in todo if not any(d in todo or d in running for d in ancestors[sid])]
            for sid in ready:
                todo.discard(sid)
                if rid in self.budget_denials:
                    await skip(sid, self.budget_denials[rid])
                    continue
                blocked = [d for d in by_id[sid]["depends_on"] if d not in results or not results[d].ok]
                if blocked:
                    reason = ", ".join(f"{d}: {results[d].error if d in results else 'not run'}" for d in blocked)
                    await skip(sid, f"upstream {reason}", blocked)
                    continue
                running[sid] = asyncio.create_task(run_one(by_id[sid]))
            if not running:
                if ready:
                    continue
                for sid in todo:
                    results[sid] = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error="unmet dependencies")
                break
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for sid, t in list(running.items()):
                if t in done:
                    running.pop(sid)
                    try:
                        outcome = t.result()
                    except BudgetExceeded as e:
                        await skip(sid, str(e))
                        continue
                    except asyncio.CancelledError:
                        outcome = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error="cancelled")
                    except Exception as e:
                        outcome = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error=str(e))
                    # A step that stops for a PI decision did not do the blocked work: its question goes to the
                    # PI below and the step re-runs, so outputs and the research ledger are checked on that run.
                    asked = blocking_question(outcome) if outcome.ok else None
                    if outcome.ok and not asked and by_id[sid].get("outputs"):
                        missing = [name for name in by_id[sid]["outputs"]
                                   if output_relpath(name) not in outcome.outputs]
                        if missing:
                            outcome = outcome.model_copy(update={"ok": False, "missing_outputs": missing,
                                                                 "error": f"incomplete: missing outputs: {', '.join(missing)}"})
                    if outcome.ok and not asked and research_plan:
                        structured = (outcome.structured if isinstance(outcome.structured, dict)
                                      else extract_json(outcome.text))
                        if isinstance(structured, dict):  # the step schema's empty question field is not ledger
                            structured = {k: v for k, v in structured.items() if k != "blocking_decision"}
                        try:
                            validated_result = validate_research_result(structured, plan=research_plan)
                            if validated_result.step_id != sid:
                                raise ValueError(f"research result step_id {validated_result.step_id} does not match {sid}")
                            outcome = outcome.model_copy(update={"structured": validated_result.model_dump(mode="json")})
                        except (TypeError, ValueError) as error:
                            outcome = outcome.model_copy(update={"ok": False,
                                "error": f"invalid research result contract: {error}"})
                    previous = results.get(sid)
                    if (feedback and sid in feedback and previous and previous.ok and not outcome.ok
                            and outcome.error_kind not in {"ask_rejected", "wake_limit"}):
                        outcome = previous.model_copy(update={"revision_failed":
                            f"{outcome.error_kind or failure_kind(outcome) or 'terminal'}: {outcome.error or 'unknown error'}"})
                    results[sid] = outcome
                    await self._emit(rid, "request.step_done", {"step_id": sid, "ok": results[sid].ok,
                                                                "agent_id": by_id[sid]["agent_id"],
                                                                "attempts": self.attempts.get(rid, {}).get(sid, 0),
                                                                "reason": results[sid].error})
                    res = results[sid]
                    question = blocking_question(res)
                    if res.ok and question:
                        if sid in decisions:
                            if req_state is not None:
                                req_state["pending_questions"] = [question.strip()]
                                self.hub.save_request(rid)
                            raise RuntimeError(f"step {sid} still requires a PI decision after its answer")
                        # Forget the question-only result before waiting (durably): a restart during the wait must
                        # re-run the step, not treat it as done and feed dependents a question.
                        results.pop(sid, None)
                        if req_state is not None:
                            req_state["pending_questions"] = [question.strip()]
                            self.hub.save_request(rid)
                        if not hasattr(self.hub, "store"):  # lightweight unit-test hubs
                            decision = await self.hub.request_approval(
                                kind="clarify", request_id=rid,
                                summary=f"Step {sid} needs a PI decision:\n{question.strip()}")
                            answer = ask_result(decision=decision, **{"from": "pi"})
                            answers = [answer]
                        else:
                            ask = AskRequest(task_id=res.task_id, agent_id=res.agent_id, request_id=rid,
                                             to="cso", question=question.strip(),
                                             why_blocked=f"step {sid}의 묻고 멈추는 게이트", wait="hibernate")
                            origin = self.hub.agent_runner.get(res.agent_id, "")
                            self.hub.store.put("ask", ask.id, {"state": "working", "origin": origin,
                                                               "ask": ask.model_dump(mode="json")})
                            await self.hub.publish({"type": "agent.ask", "ts": time.time(),
                                                    "task_id": res.task_id, "agent_id": res.agent_id,
                                                    "request_id": rid, "data": ask.model_dump(mode="json")})
                            await self.answer_ask(ask, origin)
                            answers = await self.hub.wait_asks([ask.id])
                            answer = answers[0]
                        outcome = read_ask_results(answers)
                        if outcome["status"] == "rejected":
                            results[sid] = rejected_step(res, f"{question.strip()}: {outcome['reason']}")
                            if req_state is not None:
                                req_state["pending_questions"] = []
                                self.hub.save_request(rid)
                            await self._emit(rid, "request.step_done", {"step_id": sid, "ok": False,
                                "agent_id": res.agent_id, "reason": results[sid].error})
                            continue
                        # Keep the question with the answer: "b" or "the second option" means nothing alone.
                        decisions[sid] = {**ask_result(**answer), "question": question.strip(),
                                          "from": answer.get("from"), "session_id": res.session_id,
                                          "workdir": res.workdir, "previous_result": res.model_dump(mode="json")}
                        if req_state is not None:
                            req_state["pending_questions"] = []
                            req_state.setdefault("step_decisions", {})[sid] = decisions[sid]
                            self.hub.save_request(rid)
                        todo.add(sid)

    async def _research_after_steps(self, rid: str, text: str, steps: list[dict], results: dict[str, TaskResult],
                                    n: int, serialized: Callable[[], dict[str, dict]], packs: dict) -> None:
        """The research lane after its steps ran: a failure ends the request, success stops at CP2 (#90).

        The generic re-plan, review and synthesis would change or judge the frozen PLAN without a new CP1 approval,
        so the research lane never reaches them. Evidence whose artifact labhq did not collect is refused and shown
        on the card. CP2 reads only the structured approve/revise/deny choice; an unreadable answer is not approved
        and is asked again. Only an approval goes on to the research review and report (``_research_report``).
        """
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        failed = [s["id"] for s in steps if s["id"] not in results or not results[s["id"]].ok]
        if rid in self.budget_denials or failed:
            contract["failure"] = {"steps": failed, "plan_sha256": contract["plan_sha256"],
                                   **({"budget": self.budget_denials[rid]} if rid in self.budget_denials else {})}
            req["outcome"] = "research_failed"
            self.hub.save_request(rid)
            self._finish(rid, self.report_results(steps, results, n) +
                         "\n\nResearch stopped before CP2. labhq does not re-plan a frozen research PLAN; a changed "
                         "plan needs a new CP1 approval of its hash.", serialized(), ok=False)
            return
        ledgers = {s["id"]: results[s["id"]].structured for s in steps}
        refused: list[dict[str, str]] = []
        unsupported: list[dict[str, str]] = []
        artifact_sha256: dict[str, str | None] = {}
        unreported_outputs = {s["id"]: list(results[s["id"]].unreported_outputs) for s in steps}
        for step in steps:
            result = results[step["id"]]
            upstream = [(results[d].workdir_id, results[d].workdir, list(results[d].outputs),
                         dict(results[d].output_sha256))
                        for d in step["depends_on"] if d in results and results[d].ok]
            bound = bind_result_artifacts(result.structured if isinstance(result.structured, dict) else {},
                                           outputs=list(result.outputs), upstream=upstream,
                                           output_sha256=dict(result.output_sha256))
            refused += [{"step_id": step["id"], **row} for row in bound["refused_evidence"]]
            unsupported += [{"step_id": step["id"], **row} for row in bound["unsupported_claims"]]
            artifact_sha256.update({f"{step['id']}/{artifact_id}": value
                                    for artifact_id, value in bound["artifact_sha256"].items()})
        claims = sum(len((ledger or {}).get("claims") or []) for ledger in ledgers.values())
        rows = sum(len((ledger or {}).get("evidence") or []) for ledger in ledgers.values())
        summary = (f"CP2 evidence review: {len(ledgers)} step(s), {claims} claim(s), {rows} evidence row(s)" +
                   (f", {len(refused)} refused" if refused else "") + ". Choose approve, revise or deny.")
        detail = {"gate": "research_evidence", "plan_sha256": contract["plan_sha256"],
                  "choices": list(EVIDENCE_CHOICES),
                  **({"refused_evidence": refused} if refused else {}),
                  **({"unsupported_claims": unsupported} if unsupported else {}),
                  "artifact_sha256": artifact_sha256, "unreported_outputs": unreported_outputs,
                  "results": ledgers}
        recorded = (contract.get("checkpoints") or {}).get("cp2") or {}
        if recorded.get("decision") and recorded.get("plan_sha256") == contract["plan_sha256"]:
            # A restart after the receipt was saved: the PI already decided this plan's CP2, so it is not asked again.
            decided, note, asks = recorded["decision"], str(recorded.get("note") or ""), recorded.get("asks")
            refused = recorded.get("refused_evidence") or []
            unsupported = recorded.get("unsupported_claims") or []
        else:
            cp2: str | None = None
            decision: dict[str, Any] = {}
            asks = 0
            while cp2 is None and asks < CP2_MAX_ASKS:
                asks += 1
                decision = await self.hub.request_approval(
                    kind="research_evidence", request_id=rid, detail=detail,
                    summary=summary if asks == 1 else
                    "The previous CP2 answer had no readable approve/revise/deny choice and was not approved. " + summary)
                cp2 = read_evidence_decision(decision)
            decided = cp2 or "unreadable"
            note = str(decision.get("note") or "").strip()
            contract.setdefault("checkpoints", {})["cp2"] = {
                "gate": "research_evidence", "decision": decided, "choice": decision.get("choice"), "note": note,
                "approval_id": decision.get("approval_id"), "decided_at": decision.get("decided_at"),
                "plan_sha256": contract["plan_sha256"], "asks": asks,
                "refused_evidence": refused, "unsupported_claims": unsupported,
                "artifact_sha256": artifact_sha256, "unreported_outputs": unreported_outputs}
        req["outcome"] = f"evidence_{decided}"
        self.hub.save_request(rid)
        audit = f"CP2 evidence review: {decided}."
        if refused:
            audit += "\nRefused evidence (not approved at CP2):\n" + "\n".join(
                f"- {row['step_id']}/{row['evidence_id']}: {row['reason']}" for row in refused)
        if unsupported:
            audit += "\nUnsupported claims:\n" + "\n".join(
                f"- {row['step_id']}/{row['claim_id']}: {row['reason']}" for row in unsupported)
        if decided == "revision_requested":
            audit += "\nResearch steps are not re-run yet; a changed plan needs a new CP1 approval."
        elif decided == "unreadable":
            audit += f"\nNo readable approve/revise/deny choice after {asks} card(s); the evidence is not approved."
        if note:
            audit += f"\nPI note: {note}"
        report = self.format_results(steps, results, n) + "\n\n" + audit
        reviewer = self.cfg.reviewer_agent
        if decided == "approved" and reviewer and reviewer in self.hub.agents:
            receipt = contract["checkpoints"]["cp2"]
            await self._research_report(rid, text, steps, results, n, serialized, packs, report, audit, ledgers,
                                        refused=refused, unsupported=unsupported,
                                        artifact_sha256=receipt.get("artifact_sha256") or artifact_sha256)
            return
        if decided == "approved":
            report += ("\nNo reviewer agent is configured (orchestrator.reviewer_agent), so the research review and "
                       "report did not run.")
        self._finish(rid, report, serialized(), ok=decided == "approved")

    async def _research_report(self, rid: str, text: str, steps: list[dict], results: dict[str, TaskResult], n: int,
                               serialized: Callable[[], dict[str, dict]], packs: dict, cp2_report: str,
                               cp2_audit: str, ledgers: dict[str, Any], *, refused: list[dict],
                               unsupported: list[dict],
                               artifact_sha256: dict[str, str | None]) -> None:
        """After CP2 approval: one research review, then the CSO's report and its claim-anchor check (#58 ③⑤).

        The plan stays frozen, so a review that asks for revision ends the request; a fixed plan needs a new CP1
        approval. The review is saved in the contract and reused after a restart; the report is written again.
        Failed lookups are attached to every report here, whether or not the CSO listed them (#58 ④).
        """
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        plan_hash = contract["plan_sha256"]
        refs = reference_meta(req)
        reviewer = self.cfg.reviewer_agent
        lookups = failed_lookups(ledgers)
        lookup_section = [f"{FAILED_LOOKUP_TITLE}:\n" + "\n".join(failed_lookup_lines(lookups))] if lookups else []

        def end(outcome: str, report: str, ok: bool, review: dict | None, failure: dict | None = None) -> None:
            if failure is not None:
                contract["failure"] = {"steps": [], "plan_sha256": plan_hash, **failure}
            req["outcome"] = outcome
            self.hub.save_request(rid)
            self._finish(rid, _append_report_metadata(report, lookup_section), serialized(), ok=ok, review=review)

        def budget_denied(stage: str, review: dict | None) -> None:
            end("research_failed", cp2_report + f"\n\nResearch {stage} stopped: the budget was not approved.",
                False, review, {"stage": stage, "budget": self.budget_denials[rid]})

        stored = contract.get("review") or {}
        review: dict = {}
        if stored.get("plan_sha256") == plan_hash:
            saved = {"verdict": stored.get("verdict"), "issues": stored.get("issues")}
            review = saved if valid_review(saved, RESEARCH_LANE_REVIEW_SCHEMA) else {}
        if not review:
            prompt = RESEARCH_REVIEW_PROMPT.format(
                request=text, plan_sha256=plan_hash, plan=_research_plan_digest(req["plan"]),
                packs=render_pack_review(packs),
                ledgers=self._research_ledgers(steps, results, ledgers, refused, unsupported, artifact_sha256, n))
            reply: TaskResult | None = None
            for parse_attempt in (1, 2):
                reply = await self.run_step(Task(
                    agent_id=reviewer, request_id=rid, output_schema=RESEARCH_LANE_REVIEW_SCHEMA,
                    prompt=prompt if parse_attempt == 1 else prompt + RESEARCH_REVIEW_RETRY,
                    meta={**refs, "kind": "review", "parse_attempt": parse_attempt, "request": text,
                          "title": "연구 리뷰"}))
                if rid in self.budget_denials:
                    budget_denied("review", None)
                    return
                parsed = (reply.structured if valid_review(reply.structured, RESEARCH_LANE_REVIEW_SCHEMA)
                          else extract_json(reply.text))
                if reply.ok and valid_review(parsed, RESEARCH_LANE_REVIEW_SCHEMA):
                    review = with_p1_verdict(parsed)
                    break
            if not review:
                reason = (reply.error if reply else None) or "missing or invalid research review"
                end("research_review_unparsed", cp2_report + f"\n\nResearch review: unparsed ({reason}).", False,
                    {"status": "review_unparsed", "reason": reason})
                return
            contract["review"] = {"plan_sha256": plan_hash, "reviewer": reviewer, **review}
            self.hub.save_request(rid)
        issues = _research_issue_lines(review["issues"])
        if review["verdict"] == "revise":
            end("research_review_revise", "\n".join([
                cp2_report, "", f"Research review ({reviewer}): revise.", *issues,
                "labhq does not re-run a frozen research PLAN; a fixed plan needs a new CP1 approval of its hash."]),
                False, review)
            return

        citable, other = claim_rows(ledgers, unsupported)
        resumable = self.hub.supports_resume(self.cfg.cso_agent)
        session_id, workdir = await self._free_session(
            self.cfg.cso_agent, req.get("cso_session_id") if resumable else None,
            req.get("cso_workdir") if resumable else None, rid=rid, step="synthesis")
        final = await self.run_step(Task(
            agent_id=self.cfg.cso_agent, request_id=rid, resume_session_id=session_id,
            prompt=RESEARCH_SYNTH_PROMPT.format(
                request=text, plan_sha256=plan_hash, plan=_research_plan_digest(req["plan"]),
                citable="\n".join(map(_citable_claim_line, citable)) or "(none)",
                uncitable="\n".join(f"- {row['step_id']}/{row['claim'].get('id')} ({row['reason']}): "
                                    f"{row['claim'].get('statement')}" for row in other) or "(none)",
                gaps="\n".join(_research_gap_lines(ledgers, lookups)) or "(none)", verdict=review["verdict"],
                issues="\n".join(issues) or "(none)", results=self.format_results(steps, results, n)),
            meta={**refs, "kind": "synthesis", "request": text, "title": "연구 보고서 작성",
                  **({"workdir": workdir} if workdir else {})}))
        if not final.ok:
            if rid in self.budget_denials:
                budget_denied("synthesis", review)
            else:
                end("research_failed", cp2_report + f"\n\nSynthesis failed: {final.error}", False, review,
                    {"stage": "synthesis", "error": final.error or "unknown error"})
            return
        # A report that finished keeps its text and check even if the budget card after it was denied; the denial
        # only fails the request, as in the generic synthesis (run_step: a completed attempt keeps its result).
        check = check_report(final.text, ledgers, unsupported=unsupported, refused=refused,
                             artifact_sha256=artifact_sha256)
        contract["report_check"] = check
        claim_check = (["Claim check: the report is incomplete.\n" +
                        "\n".join(_problem_lines(check["problems"], "- "))] if check["problems"] else [])
        report = _append_report_metadata(final.text, [*claim_check, cp2_audit])
        end("report_incomplete" if check["problems"] else "research_reported", report,
            not check["problems"] and rid not in self.budget_denials, review)

    def _research_ledgers(self, steps: list[dict], results: dict[str, TaskResult], ledgers: dict[str, Any],
                          refused: list[dict], unsupported: list[dict], artifact_sha256: dict[str, str | None],
                          n: int) -> str:
        """Each step's CP2-approved ledger for the research reviewer, with recorded hashes and readable files."""
        parts = []
        for step in steps:
            sid, result = step["id"], results[step["id"]]
            ledger = dict(ledgers.get(sid) or {})
            ledger["artifact_refs"] = [
                {**ref, "artifact_sha256": artifact_sha256.get(f"{sid}/{ref.get('artifact_id')}")}
                for ref in ledger.get("artifact_refs") or []]
            ledger["refused_at_cp2"] = [row for row in refused if row.get("step_id") == sid]
            ledger["unsupported_at_cp2"] = [row for row in unsupported if row.get("step_id") == sid]
            # Claim, evidence and link rows lose their empty optional fields. Everything else stays as is, so an
            # artifact without a recorded hash still reads "artifact_sha256": null.
            ledger = {key: [_without_empty(row) for row in value] if key in {"claims", "evidence", "links"}
                      and isinstance(value, list) else value for key, value in ledger.items()}
            files = "\n".join(str(Path(result.workdir) / path) for path in result.outputs) if result.workdir else ""
            parts.append(f"### {sid} · {step['agent_id']}\nReadable files:\n{files or '(none)'}\n"
                         f"Ledger:\n{clip(json.dumps(ledger, ensure_ascii=False), n)}")
        return "\n\n".join(parts)

    @staticmethod
    def format_results(steps: list[dict], results: dict[str, TaskResult], n: int) -> str:
        out = []
        by_id = {s["id"]: s for s in steps}

        def causes(sid: str, seen: set[str] | None = None) -> list[str]:
            seen = seen or set()
            if sid in seen:
                return []
            seen.add(sid)
            r = results.get(sid)
            if not r:
                return [f"{sid}: not run"]
            chain = [f"{sid} [{r.error_kind or failure_kind(r) or 'terminal'}]: {r.error or 'unknown error'}"]
            for dep in by_id[sid].get("depends_on", []):
                if dep not in results or not results[dep].ok:
                    chain.extend(causes(dep, seen))
            return chain

        for s in steps:
            r = results.get(s["id"])
            status = ("ok" if r and r.ok else "INCOMPLETE" if r and r.missing_outputs else
                      "SKIPPED" if r and (r.error or "").startswith("skipped:") else "FAILED")
            if r and not r.ok:
                status += f": {short(r.error, 200)}"
            artifacts = "\n".join(f"- {r.workdir_id or 'unknown-workdir'}/{p}" for p in r.outputs) if r else ""
            detail = f"\nOutputs:\n{artifacts or '- none'}"
            if r and r.missing_outputs:
                detail += f"\nMissing: {', '.join(r.missing_outputs)}"
            if r and r.revision_failed:
                detail += f"\nRevision failed; retained last successful result: {r.revision_failed}"
            if r and r.partial_results:
                detail += "\nFailed with partial results."
            if not r or not r.ok:
                detail += "\nCause chain: " + " <- ".join(causes(s["id"]))
            out.append(f"### {s['id']} · {s['agent_id']} ({status})\nInstruction: {s['instruction']}\n"
                       f"{clip(r.text if r else '', n)}{detail}")
        return "\n\n".join(out)

    @staticmethod
    def report_results(steps: list[dict], results: dict[str, TaskResult], n: int) -> str:
        """The PI's report of a failed request: one cause and next action per failed step (#331).

        format_results feeds reviewers and synthesis; the report leaves full instructions, outputs and errors
        to the round records and work folders, so a 12-step failure stays readable.
        """
        by_id = {s["id"]: s for s in steps}
        result_chars, max_paths = min(n, 1200), 10

        def skipped(r: TaskResult | None) -> bool:
            return bool(r) and (r.error or "").startswith("skipped:")

        def roots(sid: str, seen: set[str]) -> list[str]:
            """The failed ancestors a skipped step waits on, not every link of the chain."""
            found = []
            for dep in by_id.get(sid, {}).get("depends_on", []):
                r = results.get(dep)
                if dep in seen or (r and r.ok):
                    continue
                seen.add(dep)
                found.extend(roots(dep, seen) if not r or skipped(r) else
                             [f"{dep} [{r.error_kind or failure_kind(r) or 'terminal'}]: {short(r.error, 160)}"])
            return found

        def next_action(sid: str, r: TaskResult | None) -> str:
            if not r:
                return "not run; re-send the request once the steps above are fixed."
            if skipped(r):
                return "fix the failed upstream step(s) above, then re-send the request."
            if r.missing_outputs:
                return f"produce the missing outputs in {r.workdir_id or 'the work folder'}, then re-run this step."
            if (r.error_kind or failure_kind(r)) == "transient":
                return "a transient error: re-send the request."
            return f"read the round record and {r.workdir_id or 'work folder'} log for {sid}, fix the cause, re-send."

        out = []
        for s in steps:
            r = results.get(s["id"])
            ok = bool(r and r.ok)
            status = ("ok" if ok else "INCOMPLETE" if r and r.missing_outputs else
                      "SKIPPED" if skipped(r) else "FAILED")
            lines = [f"### {s['id']} · {s['agent_id']} ({status})", f"Task: {short(s['instruction'], 160)}"]
            if ok:
                lines.append(clip(r.text, result_chars))
            else:
                lines.append(f"Cause: {short(r.error if r else 'not run', 300)}")
                upstream = roots(s["id"], {s["id"]})
                if upstream:
                    lines.append("Root cause: " + "; ".join(upstream[:3]) +
                                 (f" (+{len(upstream) - 3} more)" if len(upstream) > 3 else ""))
                lines.append(f"Next: {next_action(s['id'], r)}")
            if r and r.outputs:
                paths = [f"- {r.workdir_id or 'unknown-workdir'}/{p}" for p in r.outputs[:max_paths]]
                if len(r.outputs) > max_paths:
                    paths.append(f"- … {len(r.outputs) - max_paths} more")
                lines.append("Outputs:\n" + "\n".join(paths))
            if r and r.missing_outputs:
                lines.append(f"Missing: {short(', '.join(r.missing_outputs), 300)}")
            if r and r.revision_failed:
                lines.append(f"Revision failed; retained last successful result: {short(r.revision_failed, 200)}")
            if r and r.partial_results:
                lines.append("Failed with partial results.")
            out.append("\n".join(lines))
        out.append("Full instructions, outputs and errors per step: the request's round records and work folders.")
        return "\n\n".join(out)

    # ---------- request entry point ----------
    async def run_request(self, rid: str, resume: bool = False) -> None:
        req = self.hub.requests[rid]
        refs = reference_meta(req)
        # Pointers ride with the request text, so briefing, plan, steps, review and report all see them (#36).
        text = req["text"] + render_references(req.get("references")) + "".join(
            "\n\nPI clarification (questions and answer):\n" + qa_text(c) for c in req.get("clarifications") or [])
        self.cost[rid] = float(req.get("cost_usd") or 0)
        self.cost_tasks[rid] = set(req.get("cost_by_task") or {})
        self._seed_cost(rid, req)
        try:
            research_pilot = bool(self.hub.s.research.enabled)
            intake = (classify_intake(req["text"], req.get("work_kind", "auto"),
                                      scope_status=req.get("scope_status", "in_scope"))
                      if research_pilot else None)
            # A request that already carries a research contract stays research whatever the config now says,
            # so it never reaches the generic re-plan, review or synthesis paths (#90 CP2).
            research_lane = bool(intake and intake.work_kind == "research") or bool(req.get("research_contract"))
            research_execution = bool(research_lane and self.hub.s.research.evidence_checkpoint
                                      and req["mode"] != "plan_only")
            if req.get("research_contract") and not research_pilot:
                req["outcome"] = "research_disabled"
                self._finish(rid, "This request carries a frozen research contract, but research.enabled is now "
                             "off. No step was dispatched and nothing was re-planned; turn the research pilot "
                             "back on to resume it.", {}, ok=False)
                return
            if intake:
                req["intake"] = intake.model_dump(mode="json")
                self.hub.save_request(rid)
            if req["mode"] == "direct":
                if research_lane:
                    req["outcome"] = "needs_research"
                    self._finish(rid, "Research work cannot use direct mode in the PR 1 pilot. "
                                 "Submit it as orchestrate or plan_only for a frozen, PI-approved plan.", {}, ok=False)
                    return
                res = await self.run_step(Task(agent_id=req["agent_id"], request_id=rid, prompt=text,
                                               budget_usd=req.get("budget_usd"),
                                               meta={**refs, "kind": "direct", "title": text[:100],
                                                     "project_dirs": req.get("project_dirs", [])}))
                self._finish(rid, res.text, {"direct": res.model_dump(mode="json")},
                             ok=res.ok and rid not in self.budget_denials)
                return

            all_agents = list(self.hub.agents.values())
            known = {a["id"] for a in all_agents}
            # The configured orchestration agents, not only the default ids, stay out of the worker roster.
            orchestration = set(ORCHESTRATION_ROLES) | {x for x in (self.cfg.cso_agent, self.cfg.chief_of_staff_agent,
                                                                   self.cfg.reviewer_agent) if x}
            roster = [a for a in all_agents if a["id"] not in orchestration]
            n = self.cfg.context_chars_per_step
            packs = configured_packs(self.hub.s) if research_lane else {}
            active_pack_hashes = pack_snapshot(packs)
            capabilities = "\n".join(  # the first plan and a re-plan after resume (#271) both need it
                f"- {a['id']}: scheduler={a.get('scheduler', 'none')}, "
                f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}, "
                f"other compute={', '.join(a.get('compute_backends') or ['local CLI'])}"
                for a in roster)

            async def finish_research_plan(plan: dict[str, Any]) -> bool:
                stored = req.get("research_contract") or {}
                previous = stored.get("approval")
                approval = refresh_plan_approval(plan, previous)
                # Fixed with the request: a resume keeps its own execution flag, checkpoint receipts and failure
                # record. The current config applies only to a request that has no contract yet (#90 CP2).
                execution_enabled = (bool(stored["execution_enabled"]) if "execution_enabled" in stored
                                     else research_execution)
                req["research_contract"] = {
                    **stored,
                    "schema_version": 1,
                    "work_kind": "research",
                    "execution_enabled": execution_enabled,
                    "plan_sha256": approval.get("current_sha256") or approval.get("target_sha256"),
                    "pack_snapshot": active_pack_hashes,
                    "approval": approval,
                }
                self.hub.save_request(rid)
                if approval.get("status") != "approved":
                    plan_hash = req["research_contract"]["plan_sha256"]
                    summary = ("CP1 research plan approval: approve the frozen question, methods, completion/stop "
                               f"conditions, data boundary, and selected packs. plan_sha256={plan_hash}")
                    decision = await self.hub.request_approval(
                        kind="research_plan", request_id=rid, summary=summary[:700],
                        detail={"gate": "research_plan", "target_sha256": plan_hash,
                                "plan_canonical": canonical_plan_json(plan),
                                "protocol_revision": plan["protocol"]["revision"],
                                "packs": plan["protocol"]["packs"],
                                "scope_status": plan["intake"]["scope_status"]})
                    approval = freeze_plan(plan, decision)
                    approval.update(request_id=rid, protocol_revision=plan["protocol"]["revision"])
                    req["research_contract"]["approval"] = approval
                    self.hub.save_request(rid)
                approved = bool(approval.get("approved"))
                if not approved:
                    req["outcome"] = "plan_rejected"
                    self._finish(rid, "Research plan was not approved; no employee research step was dispatched.",
                                 {}, ok=False)
                    return False
                if not execution_enabled:
                    req["outcome"] = "plan_approved"
                    self._finish(rid, "Research plan frozen and approved. Evidence checkpoint is off, so the "
                                 "request stops before employee dispatch.", {}, ok=True)
                    return False
                req["outcome"] = "research_running"
                self.hub.save_request(rid)
                return True

            if resume and req.get("plan", {}).get("steps"):
                if research_lane:
                    req["plan"], _ = _normalize_plan_outputs(req["plan"])
                    validated = validate_research_plan(req["plan"], max_steps=self.cfg.max_steps,
                                                       active_packs=active_pack_hashes,
                                                       expected_intake=intake, pack_definitions=packs)
                    req["plan"] = validated.model_dump(mode="json")
                    if not await finish_research_plan(req["plan"]):
                        return
                    steps = req["plan"]["steps"]
                    warnings = req["plan"].get("warnings") or []
                elif req["mode"] == "plan_only":  # restarted after the plan was saved: still no step runs
                    req["outcome"] = "plan_only"
                    self._finish(rid, "Plan completed.", {}, ok=True)
                    return
                else:
                    type_stats: dict = {}
                    vocab = self._output_vocab()
                    steps, warnings = validate_steps(req["plan"]["steps"], known, self.cfg.max_steps,
                                                     orchestration, vocab=vocab, stats=type_stats,
                                                     reject_excess=True)
                    req["plan"] = {**req["plan"], "steps": steps, "warnings": warnings}
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                self.hub.save_request(rid)
                results: dict[str, TaskResult] = self.hub.result_map(rid)
                remaining = {s["id"] for s in steps} - set(req.get("results") or {})
                pending_revisions = req.get("pending_revisions") or {}
                for sid, entry in pending_revisions.items():
                    if sid in remaining and entry.get("previous_result"):
                        dict.__setitem__(results, sid, TaskResult.model_validate(entry["previous_result"]))
                resume_feedback = {sid: entry["feedback"] for sid, entry in pending_revisions.items()
                                   if sid in remaining and entry.get("feedback")}
            else:
                briefing = ""
                cos = self.cfg.chief_of_staff_agent
                if cos and cos in known:
                    b = await self.run_step(Task(agent_id=cos, request_id=rid, prompt=BRIEFING_PROMPT.format(request=text),
                                                 meta={**refs, "kind": "briefing", "request": text,
                                                       "title": "CSO용 브리핑 준비"}))
                    if not b.ok:
                        self._finish(rid, f"브리핑 실패: {b.error}", {"briefing": b.model_dump(mode="json")}, ok=False)
                        return
                    if rid in self.budget_denials:
                        self._finish(rid, "브리핑 뒤 예산 승인 거부", {"briefing": b.model_dump(mode="json")}, ok=False)
                        return
                    briefing = b.text

                reuse_advisory = ""  # semantics-hook

                async def make_plan(plan_request: str) -> TaskResult:
                    continuation = self.hub.supports_resume(self.cfg.cso_agent)
                    session_id, workdir = await self._free_session(
                        self.cfg.cso_agent, req.get("cso_session_id") if continuation else None,
                        req.get("cso_workdir") if continuation else None, rid=rid, step="plan")
                    if research_lane:
                        vocab = self._output_vocab()
                        template = RESEARCH_CP2_PLAN_PROMPT if research_execution else RESEARCH_PLAN_PROMPT
                        prompt = template.format(
                            request=plan_request, roster=format_roster(roster),
                            capabilities=capabilities or "No workers available",
                            briefing=clip(briefing, 4000) or "(none)", max_steps=self.cfg.max_steps,
                            intake=json.dumps(intake.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                            packs=render_pack_catalog(packs), question_rule=QUESTION_RULE,
                            output_types_rule=output_types.prompt_rule(vocab) if vocab else "")
                        prompt += reuse_advisory  # semantics-hook
                        schema = research_plan_schema(vocab is not None, output_types.ENTRY_SCHEMA)
                    else:
                        vocab = self._output_vocab()
                        prompt = PLAN_PROMPT.format(request=plan_request, roster=format_roster(roster),
                                                    capabilities=capabilities or "No workers available",
                                                    briefing=clip(briefing, 4000) or "(none)",
                                                    max_steps=self.cfg.max_steps, question_rule=QUESTION_RULE,
                                                    output_types_rule=output_types.prompt_rule(vocab) if vocab else "")
                        schema = plan_schema(vocab is not None)
                    planned = await self.run_step(Task(
                        agent_id=self.cfg.cso_agent, request_id=rid, output_schema=schema,
                        resume_session_id=session_id, prompt=prompt,
                        meta={**refs, "kind": "plan", "roster": roster, "request": plan_request,
                              "title": "업무 분해·배정 계획 수립", **({"workdir": workdir} if workdir else {})}))
                    if planned.session_id:
                        req["cso_session_id"] = planned.session_id
                        req["cso_workdir"] = planned.workdir
                        self.hub.save_request(rid)
                    return planned

                plan_res = await make_plan(text)
                if not plan_res.ok:
                    self._finish(rid, f"계획 실패: {plan_res.error}", {"plan": plan_res.model_dump(mode="json")}, ok=False)
                    return
                if rid in self.budget_denials:
                    self._finish(rid, "계획 뒤 예산 승인 거부", {"plan": plan_res.model_dump(mode="json")}, ok=False)
                    return
                plan = plan_res.structured if isinstance(plan_res.structured, dict) else extract_json(plan_res.text) or {}
                # semantics-shadow: begin (#149 decision 15 advisory A/B)
                if research_lane:
                    service = getattr(self.hub, "semantics_" + "shadow", None)
                    if service is not None:
                        try:
                            draft, _ = _normalize_plan_outputs(plan)
                            draft = prepare_research_declarations(draft, self._output_vocab(), {})
                            snap = service.advisory_snapshot(rid, draft)  # live state, copied on the loop
                            offer = await asyncio.to_thread(service.advisory_offer, rid, snap)
                        except (ValueError, TypeError, PlanOutputsError):
                            offer = None
                        if offer is not None:  # ab: ids offered (shadow arm: would be), frozen for the end record
                            req["semantics_ab"] = {"arm": offer["arm"], "offered": list(offer["offered"])}
                            self.hub.save_request(rid)
                        if offer is not None and offer["offered"]:
                            # Both arms re-plan once, so they pay for the same CSO calls. Only the advisory arm's
                            # prompt gains the list; the shadow arm sends its first prompt again.
                            candidates = offer["candidates"]
                            if candidates:
                                lines = ["\n\nOptional reusable artifacts (advisory only; ignore any or all of them).",
                                         "If you use one, copy its artifact_id exactly into the relevant step's "
                                         "input_refs:"]
                                lines += [f"- artifact_id={c['artifact_id']} data_type={c['data_type']} "
                                          f"created_request_id={c['request_id']}" for c in candidates[:5]]
                                reuse_advisory = "\n".join(lines)
                            plan_res = await make_plan(text)
                            if not plan_res.ok:
                                self._finish(rid, f"A/B re-plan failed: {plan_res.error}", {}, ok=False)
                                return
                            if rid in self.budget_denials:
                                self._finish(rid, "A/B 재계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False)
                                return
                            plan = (plan_res.structured if isinstance(plan_res.structured, dict)
                                    else extract_json(plan_res.text) or {})
                # semantics-shadow: end
                details = normalize_questions(plan.get("clarifying_questions"))
                questions = [q["question"] for q in details]
                if questions:
                    req["pending_questions"] = questions
                    if has_structure(details):
                        req["pending_question_details"] = details
                    self.hub.save_request(rid)
                    await self._emit(rid, "request.questions", {"questions": questions, "details": details})
                    if self.cfg.wait_for_clarification:
                        # The card shows options as buttons and returns the composed answer as the note (#34).
                        dec = await self.hub.request_approval(kind="clarify", request_id=rid,
                                                              summary=questions_summary(details),
                                                              detail={"questions": details})
                        if not dec.get("approved") or not str(dec.get("note") or "").strip():
                            self._finish(rid, "PI clarification denied or unanswered.", {}, ok=False)
                            return
                        entry = {"questions": questions, "answer": str(dec["note"]).strip()}
                        if has_structure(details):
                            entry["question_details"] = details
                        req.setdefault("clarifications", []).append(entry)
                        req["pending_questions"] = []
                        req.pop("pending_question_details", None)
                        self.hub.save_request(rid)  # a restart must not lose the PI's answer
                        text += "\n\nPI clarification (questions and answer):\n" + qa_text(entry)
                        plan_res = await make_plan(text)
                        if not plan_res.ok:
                            self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False)
                            return
                        plan = plan_res.structured if isinstance(plan_res.structured, dict) else extract_json(plan_res.text) or {}
                        still = normalize_questions(plan.get("clarifying_questions"))
                        if still:
                            req["pending_questions"] = [q["question"] for q in still]
                            self._finish(rid, "Re-plan still requires PI clarification.", {}, ok=False)
                            return
                if research_lane:
                    vocab = self._output_vocab()
                    workers = sorted(known - orchestration)

                    def plan_problems(candidate: Any) -> list[str]:
                        try:
                            problems = research_plan_errors(candidate, max_steps=self.cfg.max_steps,
                                                            active_packs=active_pack_hashes,
                                                            expected_intake=intake, pack_definitions=packs)
                        except (ValueError, TypeError) as error:
                            problems = [str(error)]
                        drafted = candidate.get("steps") if isinstance(candidate, dict) else None
                        # A non-string agent_id is already a schema problem; only ids are compared with the roster.
                        drafted_ids = [step.get("agent_id") for step in drafted if isinstance(step, dict)
                                       and isinstance(step.get("agent_id"), str)] if isinstance(drafted, list) else []
                        bad_agents = [agent for agent in drafted_ids if agent not in known or agent in orchestration]
                        if bad_agents:
                            problems.append(f"research plan uses unavailable or orchestration agents: {bad_agents}; "
                                            f"use roster ids {workers}")
                        return problems

                    for attempt in (1, 2):
                        type_stats = {}
                        # The pack snapshot is configuration, so labhq writes protocol.packs, not the CSO (#222).
                        plan = with_pack_refs(plan, pack_refs(packs))
                        output_problems = []
                        try:
                            plan, _ = _normalize_plan_outputs(plan)
                        except PlanOutputsError as error:
                            output_problems = [str(error)]
                        # Declarations are normalized (or, when off, removed) after output paths, so names pair
                        # with the exact artifacts the runner will collect.
                        plan = prepare_research_declarations(plan, vocab, type_stats)
                        problems = output_problems + plan_problems(plan)
                        if not problems:
                            validated = validate_research_plan(plan, max_steps=self.cfg.max_steps,
                                                               active_packs=active_pack_hashes,
                                                               expected_intake=intake, pack_definitions=packs)
                            break
                        if attempt == 2:
                            req["outcome"] = "plan_invalid"
                            req["plan_validation"] = {"attempts": attempt, "errors": problems}
                            report = plan_invalid_report(problems, packs)
                            self._finish(rid, report, {}, ok=False, error=report.split("\n", 1)[0])
                            return
                        plan_res = await make_plan(text + "\n\n" + plan_correction(problems))
                        if not plan_res.ok:
                            self._finish(rid, f"Research re-plan failed: {plan_res.error}", {}, ok=False)
                            return
                        if rid in self.budget_denials:
                            self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                         {"plan": plan_res.model_dump(mode="json")}, ok=False)
                            return
                        plan = (plan_res.structured if isinstance(plan_res.structured, dict)
                                else extract_json(plan_res.text) or {})
                    req["plan"] = validated.model_dump(mode="json")
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                    warnings: list[str] = []
                    steps = req["plan"]["steps"]
                else:
                    vocab = self._output_vocab()
                    for attempt in (1, 2):
                        type_stats: dict = {}
                        try:
                            steps, warnings = validate_steps(plan.get("steps") or [], known, self.cfg.max_steps,
                                                             orchestration, vocab=vocab, stats=type_stats)
                            break
                        except PlanOutputsError as error:
                            if attempt == 2:
                                raise ValueError(f"plan invalid after correction: {error}") from error
                            # Before any step runs: one corrected plan, as the research lane does (#220).
                            plan_res = await make_plan(text + "\n\nThe previous PLAN failed validation: " +
                                                       str(error) + "\nReturn a complete corrected PLAN.")
                            if not plan_res.ok:
                                self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False)
                                return
                            if rid in self.budget_denials:
                                self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False)
                                return
                            plan = (plan_res.structured if isinstance(plan_res.structured, dict)
                                    else extract_json(plan_res.text) or {})
                            still = normalize_questions(plan.get("clarifying_questions"))
                            if still:
                                req["pending_questions"] = [q["question"] for q in still]
                                if has_structure(still):
                                    req["pending_question_details"] = still
                                self.hub.save_request(rid)
                                await self._emit(rid, "request.questions",
                                                 {"questions": req["pending_questions"], "details": still})
                                if self.cfg.wait_for_clarification:
                                    self._finish(rid, "Corrected plan still requires PI clarification.", {}, ok=False)
                                    return
                    req["plan"] = {**plan, "steps": steps, "warnings": warnings}
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                await self._emit(rid, "request.plan", req["plan"])
                for rec in plan.get("recruit") or []:
                    if rec.get("repo") or rec.get("paper"):
                        await self._emit(rid, "recruit.suggested", rec)  # UI shows a 채용 제안 card → POST /api/recruit
                if not steps:
                    self._finish(rid, plan_res.text or "CSO returned no steps.", {}, ok=False)
                    return
                if research_lane:
                    if not await finish_research_plan(req["plan"]):
                        return
                elif req["mode"] == "plan_only":
                    req["outcome"] = "plan_only"
                    self._finish(rid, plan_res.text or "Plan completed.", {}, ok=True)
                    return
                results = self.hub.result_map(rid)
                remaining = {s["id"] for s in steps} - set(results)
                resume_feedback = {}

            async def attempt_replan(review: dict | None = None, review_progress: dict | None = None) -> str:
                """Opt-in CSO re-plan of the unfinished DAG (#271): "disabled", "applied", "declined" or "failed".

                Completed steps stay and never rerun. A failure re-plan retires the failed, skipped and unrun steps; a
                review re-plan may also retire reviewer-flagged steps with their dependents. New steps take new ids,
                so ledger recovery, PI step decisions and pending revisions of an old id never reach them. Nothing
                changes until the merged DAG passes validate_steps. A review re-plan marks ``review_progress`` phase
                "replan" before the CSO call and "replanned" in the same save as the new plan, so a restart either
                repeats the review (recovered from the task ledger) or reviews the new steps.
                """
                nonlocal steps, text
                limit = self.cfg.max_replans
                # An approved research plan changes only through a new CP1 approval of its hash, never here.
                if limit <= 0 or research_lane:
                    return "disabled"
                trigger = "step_failure" if review is None else "review_revise"
                progress = req.setdefault("replan_progress", {"attempts": 0, "max": limit, "in_flight": False})
                history = req.setdefault("replan_history", [])

                def record(status: str, attempt: int | None = None, **entry: Any) -> str:
                    history.append({"attempt": attempt, "trigger": trigger, "status": status, **entry})
                    progress["in_flight"] = False
                    self.hub.save_request(rid)
                    return status if status in {"applied", "declined"} else "failed"

                by_id = {s["id"]: s for s in steps}
                completed = [sid for sid in by_id if sid in results and results[sid].ok]
                if review is None:
                    def block_reason(outcome: TaskResult) -> str | None:
                        error = outcome.error or ""
                        if error.startswith("skipped:"):  # its upstream carries the reason
                            return None
                        if outcome.error_kind == "ask_rejected":
                            return "a PI decision rejected this step; re-planning would route around it"
                        if outcome.pending_jobs:
                            return (f"failed with live HPC jobs {outcome.pending_jobs}; a new step could submit "
                                    "them again")
                        if outcome.error_kind == "wake_limit":  # run_step cleared its jobs and asks; error keeps them
                            return (f"{error}; its HPC jobs or PI questions may still be live, so a new step "
                                    "could submit or ask them again")
                        if "cancel" in error.lower():  # failure_kind's terminal cancel: the PI's task cancel
                            return f"{error}; a cancelled step is a decision, not a failure to plan around"
                        return None

                    blocked = [f"{sid}: {reason}" for sid in by_id if sid in results and not results[sid].ok
                               and (reason := block_reason(results[sid]))]
                    if blocked:
                        return record("blocked", reason="; ".join(blocked))
                used_attempts = int(progress.get("attempts") or 0)
                # A restart re-asks the attempt it interrupted; a cap lowered meanwhile still applies to it.
                attempt = max(used_attempts, 1) if progress.get("in_flight") else used_attempts + 1
                if attempt > limit:
                    return record("limit", reason=f"re-plan limit reached ({used_attempts}/{limit})")
                progress.update(attempts=attempt, max=limit, in_flight=True)
                if review_progress is not None:
                    req["review_progress"] = {**review_progress, "phase": "replan"}
                self.hub.save_request(rid)  # counted before the CSO call, so a restart cannot reset the cap

                unfinished = [sid for sid in by_id if sid not in completed]
                flagged = sorted({issue.get("step_id") for issue in (review or {}).get("issues") or []
                                  if isinstance(issue, dict) and issue.get("step_id") in completed})
                used = set(by_id) | {sid for entry in history for sid in entry.get("retired") or []}
                vocab = self._output_vocab()
                if review is None:
                    why = (f"Steps {', '.join(unfinished)} failed or could not run; the causes are in the team "
                           "results below.")
                    drop_rule = "Leave drop empty: a failure re-plan keeps every completed step."
                    empty_rule = "If the request cannot be completed safely, return no steps and explain why in notes."
                else:
                    why = "The scientific reviewer asked for revisions:\n" + json.dumps(review, ensure_ascii=False,
                                                                                       sort_keys=True)
                    drop_rule = (f"To redo a reviewer-flagged step ({', '.join(flagged) or 'none'}), list it in drop "
                                 "and return its replacement. Dropping a step also retires the completed steps that "
                                 "depend on it; return replacements for them too. If you return steps, a flagged "
                                 "step you do not drop keeps its result and is not revised in this round.")
                    empty_rule = ("If revising the flagged steps in place is enough, return no steps and an empty "
                                  "drop; labhq then sends the review to those steps.")

                async def ask_cso(parse_attempt: int) -> dict:
                    resumable = self.hub.supports_resume(self.cfg.cso_agent)
                    session_id, workdir = await self._free_session(
                        self.cfg.cso_agent, req.get("cso_session_id") if resumable else None,
                        req.get("cso_workdir") if resumable else None, rid=rid, step="replan")
                    planned = await self.run_step(Task(
                        agent_id=self.cfg.cso_agent, request_id=rid, output_schema=replan_schema(vocab is not None),
                        resume_session_id=session_id,
                        prompt=REPLAN_PROMPT.format(
                            roster=format_roster(roster), capabilities=capabilities or "No workers available",
                            trigger=why, retired=", ".join(unfinished) or "none", drop_rule=drop_rule,
                            used=", ".join(sorted(used)), max_steps=self.cfg.max_steps,
                            output_types_rule=output_types.prompt_rule(vocab) if vocab else "",
                            empty_rule=empty_rule, request=text, plan=json.dumps(steps, ensure_ascii=False),
                            results=self.format_results(steps, results, n)),
                        # revision and parse_attempt keep each CSO call distinct for ledger recovery after a restart.
                        meta={**refs, "kind": "replan", "trigger": trigger, "revision": attempt,
                              "parse_attempt": parse_attempt, "request": text, "roster": roster,
                              "title": f"남은 DAG 재계획 #{attempt}", **({"workdir": workdir} if workdir else {})}))
                    if planned.session_id:
                        req["cso_session_id"] = planned.session_id
                        req["cso_workdir"] = planned.workdir
                        self.hub.save_request(rid)
                    if not planned.ok:
                        raise ValueError(f"CSO re-plan failed: {planned.error or 'unknown error'}")
                    if rid in self.budget_denials:
                        raise ValueError(self.budget_denials[rid])
                    candidate = (planned.structured if isinstance(planned.structured, dict)
                                 else extract_json(planned.text))
                    if not isinstance(candidate, dict):
                        raise ValueError("CSO re-plan is not a JSON object")
                    return candidate

                try:
                    candidate = await ask_cso(1)
                    details = normalize_questions(candidate.get("clarifying_questions"))
                    if details:  # a changed scope, cost or approval goes back through the clarify gate
                        req["pending_questions"] = [q["question"] for q in details]
                        if has_structure(details):
                            req["pending_question_details"] = details
                        self.hub.save_request(rid)
                        await self._emit(rid, "request.questions",
                                         {"questions": req["pending_questions"], "details": details})
                        if self.cfg.wait_for_clarification:
                            decision = await self.hub.request_approval(
                                kind="clarify", request_id=rid, summary=questions_summary(details),
                                detail={"questions": details})
                            if not decision.get("approved") or not str(decision.get("note") or "").strip():
                                return record("failed", attempt,
                                              reason="re-plan needs PI clarification that was denied or unanswered")
                            entry = {"questions": req["pending_questions"], "answer": str(decision["note"]).strip()}
                            if has_structure(details):
                                entry["question_details"] = details
                            req.setdefault("clarifications", []).append(entry)
                            req["pending_questions"] = []
                            req.pop("pending_question_details", None)
                            text += "\n\nPI clarification (questions and answer):\n" + qa_text(entry)
                            self.hub.save_request(rid)
                            candidate = await ask_cso(2)
                            still = normalize_questions(candidate.get("clarifying_questions"))
                            if still:
                                req["pending_questions"] = [q["question"] for q in still]
                                return record("failed", attempt, reason="re-plan still needs PI clarification")
                    raw, drop = candidate.get("steps") or [], candidate.get("drop") or []
                    if not isinstance(raw, list) or not isinstance(drop, list):
                        raise ValueError("re-plan steps and drop must be lists")
                    if not raw:
                        if review is not None and not drop:
                            return record("declined", attempt, notes=str(candidate.get("notes") or ""))
                        raise ValueError(str(candidate.get("notes") or "CSO returned no replacement steps"))
                    ids = [step.get("id") if isinstance(step, dict) else None for step in raw]
                    if any(not isinstance(sid, str) or not sid.strip() for sid in ids):
                        raise ValueError("every re-plan step needs a non-empty id")
                    if len(ids) != len(set(ids)):
                        raise ValueError("re-plan step ids must be unique")
                    if used & set(ids):
                        raise ValueError(f"re-plan reuses step ids already in this request: {sorted(used & set(ids))}")
                    if not set(drop) <= set(flagged):
                        raise ValueError(f"re-plan may drop only reviewer-flagged completed steps {flagged}; "
                                         f"got {drop}")
                    bad_agents = sorted({str(step.get("agent_id")) for step in raw
                                         if step.get("agent_id") not in known - orchestration})
                    if bad_agents:
                        raise ValueError(f"re-plan uses unavailable or orchestration agents: {bad_agents}")
                    retired = set(unfinished) | set(drop)
                    retired |= {sid for sid in completed if any(_reaches(steps, sid, gone) for gone in drop)}
                    kept = [dict(by_id[sid]) for sid in completed if sid not in retired]
                    if len(kept) + len(raw) > self.cfg.max_steps:
                        raise ValueError(f"re-plan has {len(kept) + len(raw)} steps with the kept ones; "
                                         f"maximum is {self.cfg.max_steps}")
                    type_stats: dict = {}
                    merged, warnings = validate_steps(kept + raw, known, self.cfg.max_steps, orchestration,
                                                      vocab=vocab, stats=type_stats)
                    # A kept step already ran. A dependency inferred from its old instruction on a new step is not
                    # one it ran with, so it keeps the dependencies it had and the inference warning is dropped.
                    for step in merged:
                        if step["id"] in by_id:
                            step["depends_on"] = list(by_id[step["id"]]["depends_on"])
                    warnings = [w for w in warnings if not any(
                        w.startswith((f"step {sid}: added dependency on ", f"step {sid}: reference to "))
                        for sid in by_id)]
                except (BudgetExceeded, TypeError, ValueError) as error:  # PlanOutputsError is a ValueError
                    return record("failed", attempt, reason=str(error))

                retired_ids = [sid for sid in by_id if sid in retired]
                prior = {sid: {"ok": results[sid].ok, "error": results[sid].error,
                               "error_kind": results[sid].error_kind, "outputs": list(results[sid].outputs),
                               "workdir_id": results[sid].workdir_id} for sid in retired_ids if sid in results}
                for sid in retired_ids:
                    # Not SavedResults.pop: it saves at once, and the old plan without this result would rerun
                    # the step after a restart. record() below saves the new plan and these removals together.
                    dict.pop(results, sid, None)
                    (req.get("results") or {}).pop(sid, None)
                    (req.get("step_decisions") or {}).pop(sid, None)
                    (req.get("pending_revisions") or {}).pop(sid, None)
                steps = merged
                req["plan"] = {**(req.get("plan") or {}), "steps": steps,
                               "warnings": [*((req.get("plan") or {}).get("warnings") or []), *warnings]}
                if vocab is not None:
                    req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                if review_progress is not None:
                    req["review_progress"] = {**review_progress, "phase": "replanned"}
                outcome = record("applied", attempt, retired=retired_ids, added=ids, prior_results=prior,
                                 notes=str(candidate.get("notes") or ""))
                await self._emit(rid, "request.plan", req["plan"])
                return outcome

            async def recover_failures() -> None:
                """Opt-in (#271): re-plan around failed steps until the DAG succeeds or the cap stops it."""
                while rid not in self.budget_denials and any(not r.ok for r in results.values()):
                    if await attempt_replan() != "applied":
                        return
                    await self.run_dag(rid, text, steps, results, only={s["id"] for s in steps} - set(results))

            if remaining:
                await self.run_dag(rid, text, steps, results, only=remaining,
                                   feedback=resume_feedback or None)
            def serialized_results() -> dict[str, dict]:
                return {k: {**v.model_dump(mode="json"),
                            "status": "skipped" if (v.error or "").startswith("skipped:") else
                                      ("done" if v.ok else "incomplete" if v.missing_outputs else "failed"),
                            "attempts": self.attempts.get(rid, {}).get(k, 0)} for k, v in results.items()}

            if research_lane:  # its own end: never the generic re-plan, review or synthesis below (#90 CP2)
                await self._research_after_steps(rid, text, steps, results, n, serialized_results, packs)
                return
            await recover_failures()
            if rid in self.budget_denials or any(not r.ok for r in results.values()):
                self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False)
                return

            progress = req.get("review_progress") or {}
            if progress.get("phase") in {"revision", "replanned"}:  # its steps ran above; review them next
                progress.update(phase="review", last_completed_revision=progress["next_revision"])
                req["review_progress"] = progress
                self.hub.save_request(rid)
            elif progress.get("phase") == "replan":  # restarted while the CSO re-planned: review again, then re-plan
                progress.update(phase="review", next_revision=progress["last_completed_review"])
                req["review_progress"] = progress
                self.hub.save_request(rid)
            review: dict = progress.get("review") or {}
            reviewer = self.cfg.reviewer_agent
            start_rev = (self.cfg.max_revisions + 1 if progress.get("phase") in {"synthesis", "unresolved"}
                         else int(progress.get("next_revision") or 0))
            for rev in range(start_rev, self.cfg.max_revisions + 1):
                if not reviewer or reviewer not in known:
                    break
                prompt = REVIEW_PROMPT.format(request=text, results=self.format_results(steps, results, n))
                prompt += replan_history_note(req)  # retired steps are no longer in the results above (#271)
                review = {}
                for parse_attempt in (1, 2):
                    r = await self.run_step(Task(
                        agent_id=reviewer, request_id=rid, output_schema=REVIEW_SCHEMA,
                        prompt=prompt if parse_attempt == 1 else prompt +
                        '\n\nReturn ONLY a JSON object with verdict exactly "accept" or "revise", scores, and issues. '
                        'Do not omit verdict or add prose.',
                        meta={**refs, "kind": "review", "revision": rev, "parse_attempt": parse_attempt,
                              "request": text, "title": f"과학 리뷰 #{rev}"}))
                    if rid in self.budget_denials:
                        self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                     review={"status": "budget_denied"})
                        return
                    parsed = r.structured if valid_review(r.structured) else None
                    if parsed is None:
                        parsed = extract_json(r.text)
                    if r.ok and valid_review(parsed):
                        review = parsed
                        break
                if not review:
                    review = {"status": "review_unparsed", "reason": r.error or "missing or invalid verdict"}
                    await self._emit(rid, "request.review", {"revision": rev, **review})
                    self._finish(rid, self.report_results(steps, results, n) +
                                 f"\n\nReview: review_unparsed ({review['reason']})",
                                 serialized_results(), ok=False, review=review)
                    return
                await self._emit(rid, "request.review", {"revision": rev, **review})
                progress = {"phase": "review", "next_revision": rev + 1, "review": review,
                            "last_completed_review": rev,
                            "last_completed_revision": progress.get("last_completed_revision", 0)}
                if review.get("verdict") != "revise":
                    progress["phase"] = "synthesis"
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    break
                if rev >= self.cfg.max_revisions:
                    progress["phase"] = "unresolved"
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    break
                if await attempt_replan(review, progress) == "applied":
                    await self.run_dag(rid, text, steps, results, only={s["id"] for s in steps} - set(results))
                    await recover_failures()
                    if rid in self.budget_denials or any(not r.ok for r in results.values()):
                        self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                     review=review)
                        return
                    progress.update(phase="review", last_completed_revision=rev + 1)
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    continue
                # Off, declined or failed: the reviewer's notes go to the flagged steps in place, as before #271.
                feedback: dict[str, str] = {}
                for issue in review.get("issues") or []:
                    if issue.get("step_id") in {s["id"] for s in steps}:
                        feedback.setdefault(issue["step_id"], "")
                        feedback[issue["step_id"]] += f"- {issue.get('problem')}: {issue.get('request')}\n"
                if not feedback:
                    progress["phase"] = "unresolved"
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    break
                feedback = with_downstream_revisions(steps, feedback)
                progress["phase"] = "revision"
                req["review_progress"] = progress
                pending = req.setdefault("pending_revisions", {})
                for sid, note in feedback.items():
                    pending[sid] = {"revision": rev + 1, "feedback": note,
                                    **({"previous_result": results[sid].model_dump(mode="json")}
                                       if sid in results else {})}
                    req.setdefault("results", {}).pop(sid, None)
                self.hub.save_request(rid)
                await self.run_dag(rid, text, steps, results, only=set(feedback), feedback=feedback)
                if rid in self.budget_denials or any(not r.ok for r in results.values()):
                    self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                 review=review)
                    return
                progress.update(phase="review", last_completed_revision=rev + 1)
                self.hub.save_request(rid)

            # Still "revise" when revising stopped (F5): the CSO writes the report with the open issues in their own
            # section and the request stays failed. A failed or budget-denied synthesis ends with the step results.
            unresolved = review.get("verdict") == "revise"
            if unresolved:
                req["outcome"] = "review_unresolved"
                self.hub.save_request(rid)
            resumable = self.hub.supports_resume(self.cfg.cso_agent)
            # After a restart a consult the gateway lost can still run in this session and workdir (#112).
            session_id, workdir = await self._free_session(
                self.cfg.cso_agent, req.get("cso_session_id") if resumable else None,
                req.get("cso_workdir") if resumable else None, rid=rid, step="synthesis")
            synthesis = Task(
                agent_id=self.cfg.cso_agent, request_id=rid, resume_session_id=session_id,
                prompt=SYNTH_PROMPT.format(request=text, results=self.format_results(steps, results, n),
                                           review=short(review, 3000)) + replan_history_note(req) +
                       (UNRESOLVED_REVIEW_NOTE if unresolved else ""),
                meta={**refs, "kind": "synthesis", "request": text, "title": "최종 보고서 작성",
                      **({"workdir": workdir} if workdir else {})})
            if unresolved:
                try:
                    final = await self.run_step(synthesis)
                except BudgetExceeded as error:
                    final = TaskResult(task_id=synthesis.id, agent_id=synthesis.agent_id, ok=False, error=str(error))
                # The CSO sees the review clipped to 3,000 characters, so labhq appends every open issue itself:
                # the report always carries the full list, whatever the synthesis left out (PR #338 review).
                open_issues = "\n".join(
                    f"- {issue.get('step_id')}: {issue.get('problem')} → {issue.get('request')}"
                    for issue in review.get("issues") or [])
                self._finish(rid, (final.text if final.ok else self.report_results(steps, results, n) +
                                   f"\n\nSynthesis failed: {final.error}") +
                             "\n\nReview: revisions unresolved. The reviewer's open issues, verbatim:\n" +
                             (open_issues or "- (no issue text)"),
                             serialized_results(), ok=False, review=review,
                             error="리뷰 지적이 수정 상한 뒤에도 남아 있습니다")
                return
            final = await self.run_step(synthesis)
            self._finish(rid, final.text if final.ok else self.report_results(steps, results, n) +
                         f"\n\nSynthesis failed: {final.error}", serialized_results(),
                         ok=final.ok and rid not in self.budget_denials, review=review)
        except Exception as e:
            req.update(status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            if req.get("plan", {}).get("steps"):
                saved = {k: TaskResult.model_validate(v) for k, v in (req.get("results") or {}).items()}
                req["report"] = self.report_results(req["plan"]["steps"], saved,
                                                    self.cfg.context_chars_per_step)
            else:
                req["report"] = req["error"]
            if req.get("pending_questions"):
                req["report"] += "\n\nPending PI decisions/questions:\n" + "\n".join(
                    f"- {question}" for question in req["pending_questions"])
            # The preserved partial report goes with the event so a connected (or reconnecting) office shows it.
            failed = {"error": req["error"], "report": clip(req.get("report") or "", 20000),
                      "cost_usd": float(req.get("cost_usd") or 0),
                      "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary")}
            if hasattr(self.hub, "commit_terminal"):
                self.hub.commit_terminal(rid, "request.failed", failed)
            else:
                await self._emit(rid, "request.failed", failed)

    def _finish(self, rid: str, report: str, results: dict, ok: bool, review: dict | None = None,
                error: str | None = None) -> None:
        req = self.hub.requests[rid]
        metadata = []
        if req.get("plan", {}).get("steps") and results:
            audit = []
            for step in req["plan"]["steps"]:
                entry = results.get(step["id"], {})
                outputs = entry.get("outputs") or []
                paths = ", ".join(f"{entry.get('workdir_id') or 'unknown-workdir'}/{p}" for p in outputs)
                line = f"- {step['id']}: {entry.get('status', 'not run')}; outputs: {paths or 'none'}"
                if entry.get("missing_outputs"):
                    line += f"; missing: {', '.join(entry['missing_outputs'])}"
                if entry.get("revision_failed"):
                    line += f"; revision failed: {short(entry['revision_failed'], 200)}"
                if entry.get("partial_results"):
                    line += "; failed with partial results"
                if entry.get("error"):
                    line += f"; cause: {entry.get('error_kind') or 'terminal'}: {short(entry['error'], 200)}"
                audit.append(line)
            metadata.append("Step status and output paths:\n" + "\n".join(audit))
        if req.get("pending_questions"):
            metadata.append("Pending PI decisions/questions:\n" + "\n".join(
                f"- {question}" for question in req["pending_questions"]))
        if req.get("replan_history"):  # only with orchestrator.max_replans on (#271)
            metadata.append("Re-plan history:\n" + "\n".join(replan_history_lines(req["replan_history"])))
        cost_summary = req.get("cost_summary")
        if cost_summary and (cost_summary.get("unknown_count") or cost_summary.get("estimated_usd")
                             or cost_summary.get("warnings")):
            metadata.append(f"비용: {format_cost(cost_summary)} {cost_detail(cost_summary)}")
        elif req.get("cost_known") is False:
            known = float(req.get("cost_usd") or 0)
            metadata.append(f"비용: {f'${known:.2f} + ' if known else ''}비용 미집계")
        for outcome in self.budget_outcomes.get(rid, []):
            decision = "approved" if outcome["approved"] else "denied"
            unknown = int(outcome.get("unknown_count") or 0)
            pending = f" + 미집계 {unknown}건" if unknown else ""
            relation = ">" if outcome["spent_usd"] > outcome["limit_usd"] else "/"
            metadata.append(f"Budget: ${outcome['spent_usd']:.2f}{pending} {relation} "
                            f"${outcome['limit_usd']:.2f}; {decision}.")
        report = _append_report_metadata(report, metadata)
        req.update(status="done" if ok else "failed", report=report, results=results, review=review,
                   cost_usd=self.cost.get(rid, 0.0), finished_at=time.time())
        data = {"ok": ok, "report": clip(report, 20000), "cost_usd": req["cost_usd"],
                "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary"),
                "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}
        if error:  # one readable office-feed line; `labhq send` prints report first, or error when report is absent
            req["error"] = data["error"] = error
        if hasattr(self.hub, "commit_terminal"):
            self.hub.commit_terminal(rid, "request.completed", data)
        else:  # Lightweight orchestration test doubles do not persist state.
            self.hub.save_request(rid)
            asyncio.get_running_loop().create_task(self._emit(rid, "request.completed", data))
