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
from typing import TYPE_CHECKING, Any, Callable, Literal

from ..adapters import READ_ONLY_OVERRIDES, is_read_only_task, read_only_refusal
from ..ask_results import ask_result, read_ask_results, rejected_step
from ..costs import cost_detail, format_cost, task_cost_item
from ..evidence.claims import RESULT_CONTRACT_FIELD_RULES
from ..evidence.report_check import (FAILED_LOOKUP_TITLE, anchor, check_report, claim_rows, failed_lookup_lines,
                                     failed_lookups)
from ..evidence.verify import LiveSourceResolver, verification_lines, verify_sources
from ..facilities import signatures as env_signatures
from ..facilities import fixes as facility_fixes
from ..intake import (CLARIFYING_QUESTION_SCHEMA, QUESTION_RULE, has_structure,
                      question_detail_lines, questions_summary, reference_dirs, render_references,
                      unanswered_questions)
from ..models import AskRequest, RunnerUnavailable, Task, TaskResult, hard_stop_kind, new_id, waiting
from ..quota import is_quota_error, received_quota_wait
from ..login import is_login_error
from ..request_status import is_terminal_request
from ..research.contract import (EVIDENCE_CHOICES, RESEARCH_PLAN_SCHEMA, RESEARCH_STEP_SCHEMA, ResearchPlan,
                                 bind_result_artifacts,
                                 canonical_plan_json, classify_intake, freeze_plan, read_evidence_decision,
                                 plan_sha256 as research_plan_sha256,
                                 refresh_plan_approval, research_plan_errors, research_plan_schema,
                                 research_result_errors, salvage_research_result, validate_research_plan,
                                 validate_research_result, with_pack_refs)
from ..research import continuation as research_continuation
from ..security import redact_tokens
from ..research.packs import (assess_pack_applicability, configured_packs, normalize_pack_keys, pack_refs,
                              pack_snapshot, packs_for_snapshot, render_pack_catalog, render_pack_review,
                              select_applied_packs, select_legacy_applied_packs)
from ..util import clip, extract_json, input_relpath, output_relpath, short
from .. import vocab as output_vocab
from ..vocab import declare as output_types
from ..vocab import public_resources, topic_checklists
from ..vocab import topics as topic_types

if TYPE_CHECKING:
    from ..gateway.server import Hub

# The CSO's judgment of a general request against lab.scope (#36 PI decision 2026-10-01, option C): "out" waits for
# the PI's go-ahead before any step runs, "borderline" runs with one report line, "in" runs as before.
SCOPE_VERDICTS = ("in", "borderline", "out")
SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"verdict": {"type": "string", "enum": list(SCOPE_VERDICTS)}, "reason": {"type": "string"}},
    "required": ["verdict", "reason"],
}
DEFAULT_LAB_SCOPE = ("one-PI bioinformatics lab: genomics, transcriptomics, epigenomics, proteomics, single-cell, "
                     "population and clinical genetics, related literature and methods")

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "scope": SCOPE_SCHEMA,
        "topics": {"type": "array", "maxItems": 24, "items": {"type": "string"}},
        "clarifying_questions": {"type": "array", "maxItems": 4, "items": CLARIFYING_QUESTION_SCHEMA},
        "assumptions": {"type": "array", "maxItems": 8,
                        "items": {"type": "string", "maxLength": 300}},
        "checklist": {"type": "object", "additionalProperties": {"type": "string"}},
        "suggested_next": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "route": {"type": "string", "enum": ["team", "solo"]},
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
    "required": ["scope", "topics", "clarifying_questions", "steps", "recruit", "notes"],
}

ASSUMPTIONS_RULE = ("Record each scientific design choice you made instead of asking in `assumptions`. Use at most "
                    "8 short strings, each formatted `what was decided — one-line reason`.")


def normalize_assumptions(raw: Any) -> list[str]:
    """Keep a fresh general plan's short string assumptions, following its bounded-list convention."""
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()][:8]


def plan_topics(raw: Any, vocab: output_vocab.Vocab, *, strict: bool) -> tuple[list[str], list[str]]:
    """The one place that turns declared topics into (sorted approved keys, new warnings). Every path that saves a
    plan uses it, so no path can keep the keys and drop the warning (PR #390 review)."""
    normalized, unknown = topic_types.normalize(raw, vocab)
    if strict and unknown:
        raise ValueError(f"unknown research topics: {unknown}")
    return normalized, ([f"topics ignored (unknown_key {len(unknown)})"] if unknown else [])


def normalize_plan_topics(plan: Any, vocab: output_vocab.Vocab, *, strict: bool) -> Any:
    """Sort/deduplicate approved topics; research rejects unknowns, while general plans warn and drop them."""
    if not isinstance(plan, dict):
        return plan
    normalized, added = plan_topics(plan.get("topics"), vocab, strict=strict)
    warnings = list(plan.get("warnings") or []) if isinstance(plan.get("warnings"), list) else []
    warnings.extend(added)
    return {**plan, "topics": normalized, **({"warnings": warnings} if warnings else {})}


def normalize_plan_keys(plan: Any, packs: dict[str, Any], catalog: dict[str, list[topic_checklists.ChecklistItem]]) \
        -> Any:
    """Exact keys for the spellings a planner naturally writes: a pack id without its one configured @version, and
    a checklist answer keyed ``topic.id``. Runs after topic normalization, before validation and freezing."""
    if not isinstance(plan, dict):
        return plan
    updates = {}
    values = normalize_pack_keys(packs, plan.get("pack_values")) if packs else plan.get("pack_values")
    if values != plan.get("pack_values"):
        updates["pack_values"] = values
    answers = topic_checklists.normalize_answers(plan.get("checklist"), plan.get("topics"), catalog)
    if answers != plan.get("checklist"):
        updates["checklist"] = answers
    return {**plan, **updates} if updates else plan


def route_decision(plan: Any, solo_agent: str | None, roster: list[dict], *, requested: str = "auto",
                   research: bool = False) -> dict[str, Any]:
    """Resolve an optional general-plan hint to a durable execution mode; every uncertain case stays team."""
    planned = plan.get("route") if isinstance(plan, dict) else None
    roster_ids = {agent.get("id") for agent in roster if isinstance(agent, dict)}
    solo = bool(not research and requested != "team" and planned == "solo" and solo_agent in roster_ids)
    return {"mode": "solo" if solo else "team", "planned": planned if planned in {"team", "solo"} else "team",
            "requested": requested if requested in {"auto", "team"} else "auto",
            **({"agent_id": solo_agent} if solo else {})}


def _carry_assumptions(previous: Any, candidate: Any) -> Any:
    """Carry general-lane design choices across clarification, correction, and re-planning.

    A candidate that states `assumptions` replaces the earlier list: a choice it changed must not linger next to its
    replacement (PR #387 review). One that omits the field keeps the earlier list."""
    if not isinstance(candidate, dict):
        return candidate
    if "assumptions" in candidate:
        return {**candidate, "assumptions": list(dict.fromkeys(normalize_assumptions(candidate["assumptions"])))}
    if isinstance(previous, dict) and "assumptions" in previous:
        return {**candidate, "assumptions": normalize_assumptions(previous["assumptions"])}
    return candidate


def _assumptions_prompt(plan: Any) -> str:
    assumptions = normalize_assumptions(plan.get("assumptions") if isinstance(plan, dict) else None)
    return "\n".join(f"- {value}" for value in assumptions) if assumptions else "(none recorded)"


def plan_schema(declare: bool) -> dict[str, Any]:
    """PLAN_SCHEMA itself when output type declarations are off; with them, steps take optional output_types."""
    if not declare:
        return PLAN_SCHEMA
    schema = json.loads(json.dumps(PLAN_SCHEMA))
    schema["properties"]["steps"]["items"] = output_types.with_output_types(PLAN_SCHEMA["properties"]["steps"]["items"])
    return schema


def replan_schema(declare: bool) -> dict[str, Any]:
    """A PLAN of new steps plus ``drop``: completed reviewer-flagged steps to retire (#271).

    Scope is judged once, on the first plan; a re-plan neither returns nor re-asks it (#36)."""
    schema = json.loads(json.dumps(plan_schema(declare)))
    schema["properties"].pop("scope")
    schema["properties"]["drop"] = {"type": "array", "items": {"type": "string"}}
    schema["required"] = [*(key for key in schema["required"] if key != "scope"), "drop"]
    return schema


def scope_verdict(plan: Any) -> dict[str, str] | None:
    """The CSO's scope verdict from a general plan (#36), or None when it is missing or malformed."""
    scope = plan.get("scope") if isinstance(plan, dict) else None
    if not isinstance(scope, dict) or scope.get("verdict") not in SCOPE_VERDICTS:
        return None
    return {"verdict": scope["verdict"], "reason": short(str(scope.get("reason") or "").strip(), 500)}

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
            "properties": {"step_id": {"type": "string"},
                           "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                           "problem": {"type": "string"}, "request": {"type": "string"}},
            "required": ["step_id", "priority", "problem", "request"]}},
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

BRIEFING_PROMPT = """Prepare a briefing (≤400 words) for the CSO on this research request:
field context, recent developments (search the web if available), which datasets exist and how they can be
accessed (public vs controlled access, DUA constraints), and feasibility risks.

If the request reanalyzes a public GEO, SRA or ArrayExpress accession, or a named cohort, open the original study
full text at PMC or the publisher. In at most five lines summarize preprocessing and normalization, paired or
repeated-measures handling, covariates and batch, the statistical model and thresholds, and validation. If the full
text cannot be opened, say so explicitly.

Request: {request}"""

PRECEDENT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "papers": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"title": {"type": "string"},
                           "citations": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "citations"]}},
        "required": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"analysis": {"type": "string"}, "why": {"type": "string"},
                           "citations": {"type": "array", "items": {"type": "string"}}},
            "required": ["analysis", "why", "citations"]}},
        "recommended": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"analysis": {"type": "string"}, "why": {"type": "string"},
                           "citations": {"type": "array", "items": {"type": "string"}}},
            "required": ["analysis", "why", "citations"]}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["papers", "required", "recommended", "limitations"],
}

PRECEDENT_PROMPT = """Find 2–4 recent papers or best-practice reviews that used the same assay and question type as
this request. Look across the analysis branch, not only at the original study. Extract analyses the field treats as
required and analyses commonly added as recommendations. For every item give analysis, why, and citations using a
PMID, DOI, or direct URL. Verify the sources rather than relying on search-result titles or snippets. If you cannot
find something, say so in limitations; never invent it. Return only the structured JSON.

Request: {request}"""

# Plan and re-plan questions of the general lane go to the PI's phone card. Nothing validates their length, so
# both prompts carry the same rule.
PI_CARD_QUESTION_RULE = "Each question must fit the PI's phone card: at most 700 characters, the question itself first."
# One environment per request (2nd mock trial 2026-10-03): steps that each built a venv duplicated installs, and
# a later step could not add a package to another step's environment.
ENV_LOCK_OUTPUT = "outputs/env/requirements.lock.txt"
ENV_STEP_RULE = ("If the work needs packages the runner does not have, plan one environment step first: it creates a "
                 "virtual environment in its own workspace, installs only packages the PI approved with "
                 "`.venv/bin/python -m pip install --only-binary=:all:` (Windows: "
                 "`.venv/Scripts/python.exe -m pip install --only-binary=:all:`), and saves "
                 "outputs/env/requirements.lock.txt with the interpreter's path. Later steps depend on it and run "
                 "that interpreter by path instead of building their own. For every package that may need compilation, "
                 "put an alternative in the plan. If wheel installation or a build fails, do not ask the PI for build "
                 "tools: use the alternative and record the change under outputs/env/. Ask only when no alternative exists.")

LOCAL_PACKAGE_NAMES = ("pandas", "numpy", "scipy", "matplotlib", "statsmodels", "scikit-learn", "gseapy", "pydeseq2")
LOCAL_TOOL_NAMES = ("docker", "nextflow", "java", "wsl")


def _capability_version(value: object) -> str:
    match = re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?(?:[-.][A-Za-z0-9]+)*", str(value or ""))
    return match.group(0) if match else "unknown"


def _availability(values: object, name: str) -> str:
    if not isinstance(values, dict) or name not in values:
        return "unknown"
    return "yes" if values[name] is True else "no"


def _format_local_software(summary: object) -> str:
    if not isinstance(summary, dict) or not summary:
        return "unknown"
    r = summary.get("r") if isinstance(summary.get("r"), dict) else {}
    python = summary.get("python") if isinstance(summary.get("python"), dict) else {}
    r_label = _capability_version(r.get("version")) if r.get("available") is True else "missing"
    packages = ", ".join(f"{name}={_availability(python.get('packages'), name)}"
                         for name in LOCAL_PACKAGE_NAMES)
    tools = ", ".join(f"{name}={_availability(summary.get('tools'), name)}" for name in LOCAL_TOOL_NAMES)
    command = python.get("command")
    run_as = f" run as `{command}`" if command in ("python3", "python", "py") else ""
    return (f"R={r_label}; Python={_capability_version(python.get('version'))}{run_as}; packages[{packages}]; "
            f"tools[{tools}]")


def format_capabilities(roster: list[dict], runner_capabilities: dict | None = None) -> str:
    lines = [
        f"- {a['id']}: scheduler={a.get('scheduler', 'none')}, "
        f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}, "
        f"other compute={', '.join(a.get('compute_backends') or ['local CLI'])}"
        for a in roster
    ]
    capabilities = runner_capabilities if isinstance(runner_capabilities, dict) else {}
    for runner_id in sorted({a.get("runner_id") for a in roster if a.get("runner_id")}):
        runner = capabilities.get(runner_id) if isinstance(capabilities.get(runner_id), dict) else {}
        lines.append(f"- runner {runner_id} local software: {_format_local_software(runner.get('local_software'))}")
    return "\n".join(lines)

DECLARED_OUTPUTS_FALLBACK_RULE = (
    "Every permitted execution path writes the step's declared output to the same filename. If paths change the "
    "content, scale, or source, declare one route-independent filename (for example "
    "outputs/data/expression_matrix.tsv.gz) and record the chosen path, scale, and source in the same step's record "
    "(for example outputs/data/fetch_log.md or the file header). Do not declare different filenames per path.")

ANALYSIS_REPRODUCIBILITY_PLAN_RULE = (
    "Every analysis step declares the scripts it runs under outputs/scripts/ (for example "
    "outputs/scripts/analyze.py), as well as result-determining intermediate artifacts under outputs/reference/.")

ROUTE_PLAN_RULE = ("Set route to solo only when one employee can finish the whole request in one turn of about "
                   "30 minutes or less: a lookup, a table, a single QC check or figure, a literature list. Use team "
                   "when the work needs analysis design choices that benefit from independent review, several "
                   "dependent stages, HPC, or controlled data. When in doubt, use team. Plan the steps either way: "
                   "they are the fallback.")

SOLO_PROMPT = """Complete this request by yourself in one turn.

Original request:
{request}

Planning assumptions:
{assumptions}

Rules:
- Lead with the conclusion and write a short report, about 2,000 Korean characters or less.
- Save every deliverable under outputs/ using relative paths.
- Save analysis code under outputs/scripts/ and use only relative paths inside it.
- In the limitations, give one line for each thing you could not do and each assumption you had to make.
- Follow every supplied topic or precedent checklist item. Add a short cited "선행 연구 기준" section when
  Analysis precedents are supplied, and put omitted recommendations under "다음에 할 수 있는 분석".
"""


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
  or a file at the workspace root; a file the request asks to save in the work folder also goes under outputs/.{output_types_rule}{topics_rule}
- """ + DECLARED_OUTPUTS_FALLBACK_RULE + """
- """ + ANALYSIS_REPRODUCIBILITY_PLAN_RULE + """
- Use HPC jobs only when the assigned agent has labhq_hpc tools and a scheduler is available.
  Local CLI is available for light work. If a step needs unavailable compute, ask the PI in
  clarifying_questions before planning execution. Put a QC step after any data generation.
- """ + ENV_STEP_RULE + """
- If no roster member covers a required method, add a contract hire to `recruit` (paper + code repo +
  focus) and plan the step for whoever is closest; the PI decides whether to hire.
- {question_rule} """ + PI_CARD_QUESTION_RULE + """
- """ + ASSUMPTIONS_RULE + """
- The original study's methods are reference, not a template: the request may ask a different question of the same
  data. Reuse a choice when it fits this request's question; do not copy the original design otherwise. Record in
  `assumptions` only a different choice that could change the result.
- """ + ROUTE_PLAN_RULE + """
- Judge the request against the lab's scope ({lab_scope}) in `scope`: verdict "in" when it fits, "borderline"
  when it is adjacent work the lab can still do, "out" when it is outside the lab's field; reason is one sentence.
  Plan the steps whatever the verdict (for "out" the PI decides whether they run), and do not ask about scope in
  clarifying_questions.

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
  path. Never declare an absolute path, home path, `..`, or a file at the workspace root.{output_types_rule}{topics_rule}
- """ + DECLARED_OUTPUTS_FALLBACK_RULE + """
- """ + ANALYSIS_REPRODUCIBILITY_PLAN_RULE + """
- Put QC after data generation. {question_rule} Record the scientific design choices you make in `protocol` rather
  than adding another top-level field. Each question is at most 500 characters (a longer one fails plan
  validation), the question itself first.
- The original study's methods are reference, not a template: the request may ask a different question of the same
  data. Reuse a choice when it fits this request's question; do not copy the original design otherwise. Record in
  `protocol` only a different choice that could change the result and its evidence.
- """ + ENV_STEP_RULE + """
- LabHQ applies a pack when its structured `applies_when.topics_any` intersects top-level `topics`; fill
  `pack_values[key]` for applied packs only and add nothing for topic packs that do not match. A pack whose `applies_when` is
  a plain sentence has no topic condition: answer it with values, or `{{"not_applicable": "<reason>"}}` when it does
  not fit this request.
  Each applied value must contain exactly the keys in its `pack_values_keys`: a value for each field, a non-empty
  explanation for each validator
  id, and a non-empty outcome for each acceptance id. Do not add values for packs that do not apply.
  Acceptance ids are the pack's rule ids; reviewer questions are not acceptance ids.
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

RESEARCH_STEP_PROMPT = """Overall request (context only): {request}

Your step ({step_id}): {instruction}

Teammates' upstream results are in the context section. Deliver: what you did, key results with file
paths, caveats and open questions. Do not quietly switch to a weaker method when one fails: keep debugging,
and if you give it up, say what you tried and why you stopped. If you cannot proceed without a PI decision,
return JSON with "blocking_decision": "the specific question and choices", written for the PI's phone card:
at most 700 characters, the question itself in the first sentence, then each choice on its own line starting
with "- ". Inside the JSON string write each line break as \\n. Do not proceed with the blocked work.

For reproducibility, save every analysis script under outputs/scripts/ and every result-determining reference or
intermediate artifact (for example a gene mapping table or a copy of the gene set file) under outputs/reference/.
A script reads upstream step files by their relative paths under inputs/<step id>/, exactly as the context lists
them, collected in one variable block at the top of the script; never write an absolute path into a script.
If you run scripts, save the version and package list of the interpreter that ran them to
outputs/env/<your step id>.txt (`<that interpreter> --version` and `<that interpreter> -m pip freeze`), unless an
earlier environment step's lock covers exactly what you used; packages you installed yourself (for example into
./.pylib) always go in your own record.
Use .tmp only for disposable temporary files. In the method details, record the seed and tool and data versions."""

STEP_PROMPT = RESEARCH_STEP_PROMPT + """

Unless you are returning blocking_decision, end the answer with these exact headings, in this order:
## Findings
## Evidence
## Not established
## Method changes
Before making a factual claim, save it to a file and cite its exact workspace path under ## Evidence. A failed
lookup is neither evidence nor proof of absence. Under ## Method changes, state any weaker method you used and why;
write None when there was no change. Keep a heading even when its section is empty."""

GENERAL_SECTION_KEYS = {
    "Findings": "findings",
    "Evidence": "evidence",
    "Not established": "not_established",
    "Method changes": "method_changes",
}
_SECOND_LEVEL_HEADING = re.compile(r"^##[ \t]+([^\r\n#]+?)[ \t]*$", re.MULTILINE)
_PLAIN_OUTPUT_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(?:\./)?outputs[\\/][^\s`\"'<>()\[\]{}]+")


def parse_general_result(text: str) -> dict[str, str]:
    """Read the optional ordinary-step result block. Legacy free text remains unstructured."""
    matches = list(_SECOND_LEVEL_HEADING.finditer(text or ""))
    if not any(match.group(1).strip() in GENERAL_SECTION_KEYS for match in matches):
        return {}
    sections = {key: "" for key in GENERAL_SECTION_KEYS.values()}
    for index, match in enumerate(matches):
        key = GENERAL_SECTION_KEYS.get(match.group(1).strip())
        if key:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections[key] = text[match.end():end].strip()
    return sections


def _general_evidence_paths(text: str) -> list[str]:
    """Workspace output paths cited in Evidence, in first-seen order."""
    # Inline code is a path only when it names one (`outputs/x.tsv`): `pandas 2.2` or `GSE123` is not (PR #363 review).
    quoted = [code for code in re.findall(r"`([^`\r\n]+)`", text or "") if re.match(r"(?:\./)?outputs/", code.strip())]
    candidates = quoted + _PLAIN_OUTPUT_PATH.findall(text or "")
    found = []
    for candidate in candidates:
        candidate = candidate.rstrip(".,;:")
        relative = output_relpath(candidate)
        if relative and relative.startswith("outputs/") and relative not in found:
            found.append(relative)
    return found


def attach_general_result(result: TaskResult) -> TaskResult:
    """Attach the lightweight contract and warn, but never reject, when Evidence names an uncollected path."""
    sections = parse_general_result(result.text)
    if not sections:
        return result
    collected = {path for raw in [*result.outputs, *result.output_sha256] if (path := output_relpath(raw))}
    missing = [path for path in _general_evidence_paths(sections["evidence"]) if path not in collected]
    return result.model_copy(update={"general_sections": sections, "evidence_path_warnings": missing})


STEP_OUTPUTS_RULE = ("\n\nDeclared outputs: save each at exactly this path in your workspace; "
                     "labhq collects only these: {paths}")

RESEARCH_RESULT_FIELD_RULES = "\n".join(f"- {rule}" for rule in RESULT_CONTRACT_FIELD_RULES)

REVIEW_PROMPT = """You are the scientific reviewer. Evaluate the team's work on the request below with three
criteria scored 1–5: addresses_question, evidence (how well conclusions are supported), thoroughness.
List concrete issues per step_id with a priority and a specific revision request. Priority P1 means fixing the issue
would change a conclusion; P2 means the conclusion stays the same but its evidence or wording is weak; P3 is minor.
Use verdict "revise" only when there is at least one P1 issue; otherwise use "accept".
Check the declared topic checklist and Analysis precedents supplied below. File an issue for anything omitted or
handled incorrectly; it is P1 when fixing it would change a conclusion.
A checklist item answered assumption with a stated, correct reason is acceptable (PI 2026-10-07): file an
issue for it only when the reason is wrong or the missing check would change a conclusion (then P1), not
merely because it was skipped.

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
- Declare each output as outputs/<name> inside that step's own workspace and save it at that path.{output_types_rule}{topics_rule}
- """ + DECLARED_OUTPUTS_FALLBACK_RULE + """
- """ + ANALYSIS_REPRODUCIBILITY_PLAN_RULE + """
- Stay within the request, permissions, data boundaries and PI approvals. If scope, cost, compute, data access or an
  approval must change, ask in clarifying_questions and do not plan the blocked work. """ + PI_CARD_QUESTION_RULE + """
- {question_rule}
- """ + ASSUMPTIONS_RULE + """ The current plan's `assumptions` are below. Return the complete updated list: keep
  the choices that still hold and replace any choice this re-plan changes.
- """ + ENV_STEP_RULE + """ If the plan already has an environment step, new steps depend on it instead.
- {empty_rule}

PI's request: {request}

Current plan:
{plan}

Team results so far:
{results}"""

SYNTH_PROMPT = """Write the final report for the PI.
Use these sections in this order: 1) "결론과 권고", 2) "결과" with evidence and file paths, 3) "방법 요약"
including seeds and tool and data versions, 4) "한계" including reviewer concerns and what would change the
conclusion. Put concrete next steps in the recommendation. Start with the report's first heading: no preamble.
The PI reads the body once (#373): aim for about 4,000 characters, longer only when the results need it. State each
result statistic once, in "결과", and refer to it elsewhere instead of repeating it (seeds, versions and cutoffs
still belong in "방법 요약"); keep "방법 요약" to about eight lines, and under "한계" keep only what could change the
conclusion.
Do not turn a failed lookup into evidence or proof of absence. LabHQ stores warnings, review records and execution
details in the separate execution record; do not copy their details into the body. When the warning preview is not
"(none)", summarize its importance in one line under "한계" and end that line with "실행 기록 참고".
Warning preview ("(none)" means there is no warning section):
{warnings}

Scientific design assumptions: {assumptions}
Under "방법 요약", add a subsection titled "가정" and list these choices with their reasons. If none were recorded,
do not invent any.
When Analysis precedents are supplied below, add a short "선행 연구 기준" section: one cited line for each required
analysis done or not done (with the reason), and put omitted recommended analyses under "다음에 할 수 있는 분석".
Under "한계", include one line for every checklist answer that used assumption. Do not list checks that do not apply
to this request in the report body.

Request: {request}

Team results:
{results}

Reviewer: {review}"""

# Appended to SYNTH_PROMPT only when the generic review loop ended with the verdict still "revise" (2nd mock
# trial F5); the request still fails, but the PI gets the CSO's conclusion instead of a step dump.
UNRESOLVED_REVIEW_NOTE = (
    "\n\nThe reviewer still asked for revisions after the last revision labhq could run. Summarize the effect "
    "on the conclusion under \"한계\", but do not copy the reviewer's original text into the PI report. LabHQ "
    "stores every open issue verbatim in the separate execution record.")

REVIEW_REFERENCE_NOTE = (
    "\n\nThe remaining P2 and P3 issues do not change the conclusion. LabHQ appends a \"리뷰 참고\" section with "
    "one line per P2 issue and the P3 count, so do not write that section or repeat those issues elsewhere.")

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
plan: after a revise the PI may continue through a new plan, approved at a new CP1, that carries your P1 issues.
Check the declared topic checklist, its plan answers and Analysis precedents supplied below. File an issue for
anything omitted or handled incorrectly; it is P1 when fixing it would change a conclusion.
A checklist item answered assumption with a stated, correct reason is acceptable (PI 2026-10-07): file an
issue for it only when the reason is wrong or the missing check would change a conclusion (then P1), not
merely because it was skipped.

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
    """Set revise exactly for P1 issues; old stored issues without priority remain P1-compatible."""
    verdict = "revise" if any(issue.get("priority", "P1") == "P1"
                              for issue in review.get("issues") or []) else "accept"
    if review.get("verdict") == verdict:
        return review
    return {**review, "verdict": verdict, "reviewer_verdict": review.get("verdict")}


def issues_at_priority(review: dict, priority: str) -> list[dict]:
    """Return one priority tier, treating legacy stored issues without a priority as P1."""
    return [issue for issue in review.get("issues") or []
            if isinstance(issue, dict) and issue.get("priority", "P1") == priority]


def review_at_priority(review: dict, priority: str) -> dict:
    """Keep review context while exposing only one tier to a re-plan or revision round."""
    issues = issues_at_priority(review, priority)
    return {**review, "verdict": "revise" if priority == "P1" and issues else "accept", "issues": issues}


RESEARCH_SYNTH_PROMPT = """Write the final research report for the PI from the frozen plan, the evidence the PI
approved at CP2 and the research review below.

Claim anchors (labhq checks them by machine):
- End every sentence that states a conclusion or a number with the anchor of the claim it rests on, written
  exactly [[claim:<step_id>/<claim_id>]].
- Anchor only the citable claims listed below, each with the anchor shown there. A claim that is not listed as
  citable is not established: do not state it as a conclusion.
- A failed or empty lookup is neither evidence nor proof of absence. LabHQ appends its details, CP2 records and the
  claim check in the separate execution record. Summarize an important warning in one line under "한계" and
  end that line with "실행 기록 참고", without copying the raw warning into the body.
- Report the reviewer's P1 and P2 issues as limitations.
Use these sections in this order: 1) "결론과 권고", 2) "결과" with claim anchors and file paths, 3) "방법 요약"
including seeds and tool and data versions, 4) "한계" including not-established claims and what would change the
conclusion. Put concrete next steps in the recommendation. Start with the report's first heading: no preamble.
The PI reads the body once (#373): aim for about 4,000 characters, longer only when the results need it. State each
result statistic once, in "결과", and refer to it elsewhere instead of repeating it (seeds, versions and cutoffs
still belong in "방법 요약"); keep "방법 요약" to about eight lines, and under "한계" keep only what could change the
conclusion.
When Analysis precedents are supplied below, add a short "선행 연구 기준" section: one cited line for each required
analysis done or not done (with the reason), and put omitted recommended analyses under "다음에 할 수 있는 분석".
Under "한계", include one line for every checklist answer that used assumption. Do not list checks that do not apply
to this request in the report body.

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
# Saving files, writing the status note and answering take more than two turns: a 2-turn wrap-up stopped at its
# third turn with nothing saved (7th mock trial, 2026-10-03).
WRAP_TURNS = 4

FINISH_PROMPT = """Your turn limit was reached before this step finished. Continue the same step in this session:
do not redo work that is already done. Finish only what remains, write the declared outputs, and give your final
answer in the required format."""

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


REVISION_RESULT_RULE = ("\nReturn your complete revised result, not only the changes: it replaces your previous "
                        "result for later steps and the report, so anything you leave out is lost.")


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


_PI_NOTE_TASK_KINDS = frozenset({"plan", "replan", "step", "wrap_up", "review", "synthesis", "direct"})


def with_pi_notes(task: Task, request: dict) -> Task:
    """Attach the notes visible at dispatch time; a turn already in the runner is never changed."""
    notes = request.get("pi_notes") or []
    kind = task.meta.get("kind", "step")
    if not notes or kind not in _PI_NOTE_TASK_KINDS:
        return task
    lines = ["## PI notes sent during this request",
             "Use these notes in this newly dispatched turn. They do not change work that already finished."]
    for note in notes:
        try:
            sent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(note.get("at") or 0)))
        except (TypeError, ValueError, OverflowError):
            sent = "time unknown"
        lines.append(f"- [{note.get('id') or 'note'} · {sent}] {note.get('text') or ''}")
    if request.get("research_contract"):
        lines.extend(["", "This is a research request with a frozen plan. Treat the notes as reference only; do not "
                      "change the frozen plan. If a note needs a plan change, state that a new CP1 approval is required."])
    if kind == "synthesis":
        lines.extend(["", "In the final report, include one line per PI note saying whether it was incorporated. If it "
                      "was not, give the reason and what new request is needed. If a note changed a choice listed "
                      "under 가정, list the choice that was actually used there and name the note that changed it."])
    return task.model_copy(update={"prompt": task.prompt + "\n\n" + "\n".join(lines)})


_HEADING = re.compile(r"^#{1,6} \S", re.MULTILINE)


def report_body(text: str) -> str:
    """The report from its first heading: a short lead-in before it ("Writing the report now... ---") is talk to the
    lab, not part of the report (10th mock trial). Text with an anchor or longer than a few lines is kept."""
    heading = _HEADING.search(text or "")
    if not heading or heading.start() == 0:
        return text
    lead = text[:heading.start()]
    # A fence before the "heading" means it may sit inside a code block (PR #361 review).
    if "[[claim:" in lead or "```" in lead or "~~~" in lead or len(lead.strip()) > 400:
        return text
    return text[heading.start():]


def step_ancestors(steps: list[dict]) -> dict[str, set[str]]:
    """Every step a plan step waits on, directly or through other steps."""
    by_id = {s["id"]: s for s in steps}
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
    return ancestors


def ancestor_artifacts(steps: list[dict], ancestors: dict[str, set[str]], results: dict[str, TaskResult],
                       step_id: str) -> list[tuple]:
    """Collected outputs of every finished ancestor, as bind_result_artifacts reads them.

    Any ancestor counts: an interpretation step reads the analyses its QC step checked, not only the QC verdict
    (8th mock trial: 6 of 8 refusals cited a grandparent's file)."""
    return [(results[d].workdir_id, results[d].workdir, list(results[d].outputs), dict(results[d].output_sha256))
            for d in (s["id"] for s in steps if s["id"] in ancestors[step_id])
            if d in results and results[d].ok]


def unbound_evidence_problems(ledger: dict[str, Any], result: TaskResult, upstream: list[tuple]) -> list[str]:
    """Evidence CP2 would refuse because it cites a file labhq did not collect, as correction problems (#485).

    v0.5 trial (2026-10-08): a QC verdict cited a JSON file the step wrote but the plan never declared; CP2 refused it
    and the claim lost its support with no turn left to point it at the collected verdict file."""
    bound = bind_result_artifacts(ledger, outputs=list(result.outputs), upstream=upstream,
                                  output_sha256=dict(result.output_sha256))
    if not bound["refused_evidence"]:
        return []
    collected = ", ".join(result.outputs) or "none"
    return [*(f"evidence {row['evidence_id']} {row['reason']}; CP2 would refuse it"
              for row in bound["refused_evidence"]),
            f"Cite only files labhq collected: this step's outputs ({collected}) or an earlier step's artifact as "
            "<workdir_id>/<path>, or remove the row and every link that needs it. A file the plan did not declare "
            "is not collected, and no file written now is collected."]


def merged_turn(first: TaskResult, later: TaskResult) -> TaskResult:
    """A later turn of the same step in the same workspace. The runner lists and hashes the declared outputs as they
    are after that turn, so that view replaces the first turn's: a file the later turn removed, or grew past the hash
    limit, keeps no stale digest (PR #355 review). Files the step wrote without reporting them stay listed."""
    return later.model_copy(update={
        "unreported_outputs": sorted((set(first.unreported_outputs) | set(later.unreported_outputs))
                                     - set(later.outputs)),
        "tool_errors": [*first.tool_errors, *later.tool_errors],
        "workdir": later.workdir or first.workdir,
        "workdir_id": later.workdir_id or first.workdir_id,
    })


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
                     f"{'labhq_annot=yes (VEP, gnomAD, ClinVar, ChIP-Atlas, ENCODE cCRE, GTEx lookups; AlphaGenome when its key is set); ' if 'annot' in (a.get('builtin_mcp') or []) else ''}"
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
MAX_CLARIFY_CARDS = 2
ANSWERED_QUESTIONS_HEADING = "PI가 이미 답한 질문 — 다시 묻지 말 것"


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


def answered_questions_prompt(entries: Any) -> str:
    """A prompt section that makes prior PI answers explicit on every plan and re-plan."""
    entries = entries if isinstance(entries, list) else []
    if not entries:
        return ""
    return "\n\n" + ANSWERED_QUESTIONS_HEADING + ":\n" + "\n\n".join(qa_text(entry) for entry in entries)


_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\s,;]+")
_HOME_PATH = re.compile(r"(?<!\w)~[\\/][^\s,;]+")
_POSIX_PATH = re.compile(r"(?<![:\w])/(?:[^/\s]+/)+[^\s,;]*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:authorization|bearer|api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+")


def safe_failure_cause(value: Any, limit: int = 280) -> str:
    """One readable cause line for the PI, with paths and credentials removed."""
    text = " ".join(str(value or "원인을 기록하지 못했습니다.").split())
    text = redact_tokens(_SECRET_ASSIGNMENT.sub("[가림]", text))
    text = _WINDOWS_PATH.sub("<경로>", text)
    text = _HOME_PATH.sub("<경로>", text)
    text = _POSIX_PATH.sub("<경로>", text)
    command_line = re.search(
        r"command line has ([\d,]+) Windows command-line UTF-16 units, over the limit of ([\d,]+)", text,
        re.IGNORECASE)
    if command_line:
        text = (f"Windows 명령줄 길이 제한을 넘었습니다"
                f"({command_line.group(1)} units, 제한 {command_line.group(2)}).")
    elif "invalid research result contract" in text.casefold():
        text = re.sub(r"(?i)invalid research result contract", "연구 결과 계약이 유효하지 않습니다", text)
    elif "question was rejected or unanswered" in text.casefold():
        text = re.sub(r"(?i)question was rejected or unanswered", "질문이 거절되었거나 답을 받지 못했습니다", text)
    return short(text, limit)


def _developer_action(cause: str, error_kind: str = "") -> bool:
    internal = (
        "command line", "utf-16", "명령줄 길이 제한", "result contract", "결과 계약", "schema", "traceback", "internal",
        "configuration", "config", "no runner hosts", "module not found", "modulenotfounderror",
        "같은 대상", "질문 상한", "plan invalid", "not a json object",
    )
    lowered = f"{cause} {error_kind}".casefold()
    return any(marker in lowered for marker in internal)


def failed_request_summary(req: dict, results: dict, fallback: Any = "") -> tuple[str, str]:
    """Three Korean lines for the top of an executed request's failed report."""
    steps = (req.get("plan") or {}).get("steps") or []
    by_id = {str(step.get("id")): step for step in steps if isinstance(step, dict) and step.get("id")}
    candidates: list[tuple[str, dict, dict]] = []
    for sid, step in by_id.items():
        entry = results.get(sid) if isinstance(results, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        failed = (entry.get("ok") is False or entry.get("status") in {"failed", "skipped", "incomplete"}
                  or bool(entry.get("error")) or sid not in results)
        if failed:
            candidates.append((sid, step, entry))
    roots = [item for item in candidates if not str(item[2].get("error") or "").startswith("skipped:")]
    if roots or candidates:
        sid, step, entry = (roots or candidates)[0]
        agent = str(step.get("agent_id") or entry.get("agent_id") or "담당 미기록")
        cause_raw = entry.get("error") or entry.get("revision_failed") or fallback
        error_kind = str(entry.get("error_kind") or "")
    else:
        sid, agent, cause_raw, error_kind = "request", "cso", fallback, ""
    cause = safe_failure_cause(cause_raw)
    action = "개발자에게 알리기" if _developer_action(cause, error_kind) else "다시 보내기"
    summary = "\n".join([
        f"실패한 단계: {sid} · {agent} — {cause}",
        f"원인: {cause}",
        f"PI가 할 일: {action}",
    ])
    return summary, cause if sid == "request" else f"{sid} 실패: {cause}"


class PlanOutputsError(ValueError):
    """A declared step output that no normalization can bring under the step's outputs/ folder (#220)."""


class PlanAgentError(ValueError):
    """A plan assigned work to staff outside the current worker roster."""


def unavailable_plan_agents(steps: Any, known: set[str], excluded: frozenset[str] | set[str]) -> list[str]:
    """Agent ids a worker plan cannot dispatch: absent from this roster or reserved for orchestration."""
    if not isinstance(steps, list):
        return []
    bad = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        agent = step.get("agent_id")
        if not isinstance(agent, str) or agent not in known or agent in excluded:
            bad.append(str(agent))
    return sorted(set(bad))


EXECUTION_APPENDIX_TITLE = "## 부록: 실행 기록"


def _append_report_metadata(report: str, sections: list[str]) -> str:
    """Collect LabHQ audit text in one trailing appendix while keeping a benchmark result block last (#229)."""
    if not sections:
        return report
    metadata = "\n\n".join(section.strip() for section in sections if section.strip())
    if not metadata:
        return report
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(report))
    benchmark = ""
    if len(blocks) == 1 and not report[blocks[0].end():].strip():
        benchmark = blocks[0].group(0)
        report = report[:blocks[0].start()].rstrip()
    if EXECUTION_APPENDIX_TITLE in report:
        report = report.rstrip() + "\n\n" + metadata
    else:
        report = report.rstrip() + "\n\n" + EXECUTION_APPENDIX_TITLE + "\n\n" + metadata
    return report + (("\n\n" + benchmark) if benchmark else "")


def _split_report_appendix(report: str) -> tuple[str, str]:
    """Separate the PI report from the execution record, including old/model-written combined reports."""
    report = str(report or "").strip()
    if EXECUTION_APPENDIX_TITLE not in report:
        return report, ""
    body, raw = report.split(EXECUTION_APPENDIX_TITLE, 1)
    appendix = raw.strip()
    # #229 benchmark parsers require their machine block to remain the last part of the PI report.
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(appendix))
    if len(blocks) == 1 and not appendix[blocks[0].end():].strip():
        benchmark = blocks[0].group(0)
        appendix = appendix[:blocks[0].start()].rstrip()
        body = body.rstrip() + "\n\n" + benchmark
    return body.strip(), (EXECUTION_APPENDIX_TITLE + ("\n\n" + appendix if appendix else ""))


def _terminal_reports(rid: str, report: str, report_appendix: str) -> dict[str, Any]:
    """Bound terminal copies while pointing every truncated field at the durable request."""
    data = {}
    for field, value in (("report", report), ("report_appendix", report_appendix)):
        data[field] = clip(value, 20000)
        if len(value) > 20000:
            data[f"{field}_truncated"] = True
            data[f"{field}_chars"] = len(value)
            data[f"{field}_api"] = f"/api/requests/{rid}"
    return data


def _execution_warning_summary(report: str) -> str:
    """Ensure a warning moved out of the PI report still leaves one visible limitation line."""
    if "실행 기록 참고" in report:
        return report
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(report))
    benchmark = ""
    if len(blocks) == 1 and not report[blocks[0].end():].strip():
        benchmark = blocks[0].group(0)
        report = report[:blocks[0].start()].rstrip()
    line = "실행 경고가 있습니다. 실행 기록 참고."
    limitations = re.search(r"(?m)^## 한계\s*$", report)
    if not limitations:
        report = report.rstrip() + "\n\n" + line
    else:
        following = re.search(r"(?m)^## ", report[limitations.end():])
        end = limitations.end() + (following.start() if following else len(report[limitations.end():]))
        report = report[:end].rstrip() + "\n\n" + line + "\n\n" + report[end:].lstrip()
    return report + (("\n\n" + benchmark) if benchmark else "")


def _review_reference(review: dict | None) -> str:
    """One short PI-facing line per P2 issue and a count of P3 (wording-only) issues (#373); requests and verbatim
    text stay in the appendix."""
    issues = [issue for issue in (review or {}).get("issues") or [] if isinstance(issue, dict)]
    p2 = [issue for issue in issues if issue.get("priority") == "P2"]
    p3 = sum(issue.get("priority") == "P3" for issue in issues)
    if not p2 and not p3:
        return ""
    lines = [f"- P2 · {issue.get('step_id') or '-'}: {short(issue.get('problem') or '-', 180)}" for issue in p2]
    if p3:
        lines.append(f"- P3(표현) {p3}건: 실행 기록 참고")
    return "## 리뷰 참고\n" + "\n".join(lines)


# Any heading level: a model that wrote "# 리뷰 참고" kept its copy beside labhq's (bench C t6, #373).
REVIEW_REFERENCE_HEADING = re.compile(r"(?m)^#{1,6}[ \t]*리뷰 참고[ \t]*$")


def _with_review_reference(report: str, review: dict | None) -> str:
    """Replace model-written review notes with bounded P2/P3 lines at the end of the PI report."""
    reference = _review_reference(review)
    if not reference:
        return report
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(report))
    benchmark = ""
    if len(blocks) == 1 and not report[blocks[0].end():].strip():
        benchmark = blocks[0].group(0)
        report = report[:blocks[0].start()].rstrip()
    report = re.sub(r"(?ms)^#{1,6}[ \t]*리뷰 참고[ \t]*$.*?(?=^#{1,6} |\Z)", "", report).rstrip()
    report = report + "\n\n" + reference
    return report + (("\n\n" + benchmark) if benchmark else "")


def _appendix_sections(existing: str, sections: list[str]) -> str:
    """Add sections to an already separated execution record without duplicating its heading."""
    parts = []
    if existing:
        _, tail = existing.split(EXECUTION_APPENDIX_TITLE, 1)
        if tail.strip():
            parts.append(tail.strip())
    parts.extend(section.strip() for section in sections if section and section.strip())
    return EXECUTION_APPENDIX_TITLE + (("\n\n" + "\n\n".join(parts)) if parts else "")


def general_report_warnings(steps: list[dict], results: dict[str, TaskResult | dict], plan: Any = None) -> str:
    """Short deterministic warnings for ordinary reports; tool payloads never pass their first bounded line."""
    lines = []
    skips = checklist_skip_line(plan)
    if skips:
        lines.append(skips)
    for step in steps:
        sid = step["id"]
        result = results.get(sid)
        if result is None:
            continue
        paths = (result.evidence_path_warnings if isinstance(result, TaskResult)
                 else result.get("evidence_path_warnings") or [])
        if paths:
            shown = ", ".join(str(path) for path in paths[:5])
            suffix = f" (+{len(paths) - 5}개)" if len(paths) > 5 else ""
            lines.append(f"- {sid}: Evidence 경로 불일치 {len(paths)}건: {shown}{suffix}")
        ok = result.ok if isinstance(result, TaskResult) else result.get("ok")
        environment = None if ok else environment_problem(result)
        if environment:
            lines.append(f"- {sid}: {env_signatures.problem_text(environment)}")
        errors = result.tool_errors if isinstance(result, TaskResult) else result.get("tool_errors") or []
        if errors:
            first = str(errors[0]).splitlines()[0].strip() or "tool failed"
            lines.append(f"- {sid}: {FAILED_LOOKUP_TITLE} {len(errors)}건; 첫 줄: {short(first, 160)}")
    return "## 보고서 경고\n" + "\n".join(lines) if lines else ""


def _research_plan_digest(plan: dict) -> str:
    """The frozen plan as the research reviewer and report writer read it: question, protocol, pack values, steps.
    Only the step list is clipped: a real plan runs past 20,000 characters, and clipping the whole JSON cut the
    middle of the protocol the reviewer judges against (PR #358 review)."""
    steps = [{key: step.get(key) for key in ("id", "agent_id", "phase", "instruction", "claim_ids", "outputs",
                                             "evidence_slots", "depends_on")} for step in plan.get("steps") or []]
    frozen = {"brief": plan.get("brief"),
              "protocol": {k: v for k, v in (plan.get("protocol") or {}).items() if k != "packs"},
              "pack_values": plan.get("pack_values") or {}}
    return (json.dumps(frozen, ensure_ascii=False) + "\nSteps: " +
            clip(json.dumps(steps, ensure_ascii=False), 8000))


def _research_protocol_digest(plan: dict) -> str:
    """The frozen question and protocol a research step must follow, whole: a clipped middle could drop the very
    criterion the step needs (PR #358 review). A step instruction names a rule ("after the low-expression filter")
    without its criteria (8th mock trial: the analyst invented its own filter)."""
    # The same fields a continuation compares before it reuses a step (research/continuation.py).
    return json.dumps(research_continuation.frozen_context(plan), ensure_ascii=False)


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
    unless one of the orchestrator re-plan caps recorded an attempt (#271, #373)."""
    if not req.get("replan_history"):
        return ""
    return ("\n\nPlan changes during this request (labhq re-plan history):\n" +
            "\n".join(replan_history_lines(req["replan_history"])))


def with_downstream_revisions(steps: list[dict], feedback: dict[str, str]) -> dict[str, str]:
    """Reviewer feedback plus every step downstream of a flagged one (PR #337 review).

    A revised step changes what its dependents read, so a dependent the reviewer did not flag re-runs too, with a
    note naming the revised upstream steps; otherwise a later revision or the report reads a bridge step (s5 → s8
    → s9) built on the old result. A flagged step below a revised one gets the same note after its own: in the 12th
    mock trial the report step fixed only its flagged sentences and kept the numbers s8 and s9 had just withdrawn.
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
            roots = {sid} if sid in feedback else revised_above.get(sid, set())
            if not roots <= revised_above.get(child, set()):
                revised_above.setdefault(child, set()).update(roots)
                if child not in feedback:  # a flagged step is already pending as a root of its own
                    pending.append(child)
    extended = dict(feedback)
    for sid in (s["id"] for s in steps):
        if sid in revised_above:
            extended[sid] = feedback.get(sid, "") + (
                f"- Upstream step(s) {', '.join(sorted(revised_above[sid]))} were revised after the scientific "
                "review. Redo your step on their new results and update your outputs.\n")
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
    bad_agents = unavailable_plan_agents(raw[:max_steps], known, excluded)
    if bad_agents:
        raise PlanAgentError(f"plan uses unavailable or orchestration agents: {bad_agents}; "
                             f"use roster ids {sorted(known - set(excluded))}")
    warnings, steps, seen = [], [], set()
    raw_types: dict[str, Any] = {}
    for i, s in enumerate(raw[:max_steps]):
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


def result_correction(problems: list[str]) -> str:
    """Ask for ledger JSON only: the completed analysis and its files must not run again."""
    return "\n".join([
        "The previous research result JSON failed contract validation. Fix every problem below and return only one "
        "complete corrected result JSON object.",
        "Do not recreate or modify output files, rerun the analysis, or change the method. Fix the result JSON only.",
        *_problem_lines(problems, "1."),
    ])


def plan_invalid_report(problems: list[str], packs: dict[str, Any]) -> str:
    """What the PI reads when the corrected PLAN still fails: the problems, and the packs it was held to."""
    lines = [f"연구 계획 검증 실패: CSO가 한 번 고친 계획도 계약 검사를 통과하지 못해 CP1 승인 카드를 만들지 "
             f"않았습니다(남은 문제 {len(problems)}건).", "", "남은 문제:", *_problem_lines(problems, "- ")]
    if packs:
        lines += ["", "설정된 domain pack: " + "; ".join(f"{key} ({loaded.pack.applies_when})"
                                                       for key, loaded in sorted(packs.items())),
                  "모든 configured pack에 값 또는 `not_applicable` 사유를 답합니다."]
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


def normalize_precedents(value: Any) -> dict[str, Any] | None:
    """Bound a valid literature-scout result and give its required analyses stable checklist ids."""
    if not valid_review(value, PRECEDENT_SCHEMA):
        return None
    papers = [dict(row) for row in value["papers"][:4]]
    required = [{**dict(row), "id": f"precedent.{index}"}
                for index, row in enumerate(value["required"][:12], 1)]
    recommended = [dict(row) for row in value["recommended"][:12]]
    limitations = [str(item) for item in value["limitations"][:12]]
    if not papers:  # a search that found nothing is not a precedent basis (PR #395 review): say so, keep its reasons
        return {**precedent_warning("no_papers"), "limitations": limitations}
    return {"status": "ok", "papers": papers, "required": required,
            "recommended": recommended, "limitations": limitations}


# How long an early-finishing request waits for the literature scout's cancelled turn to report back.
PRECEDENT_CANCEL_GRACE_S = 10.0


def precedent_warning(code: str) -> dict[str, Any]:
    return {"status": "warning", "warning": f"analysis precedents unavailable ({code})",
            "papers": [], "required": [], "recommended": [], "limitations": []}


def _citations(row: dict[str, Any]) -> str:
    return "; ".join(str(item) for item in row.get("citations") or []) or "citation unavailable"


def analysis_precedents_text(record: Any) -> str:
    """Compact context shared by planning, solo execution, review and report prompts."""
    if not isinstance(record, dict):
        return ""
    if record.get("status") != "ok":
        return "Analysis precedents:\n- unavailable; continue without precedent requirements."
    lines = ["Analysis precedents:", "Papers:"]
    lines.extend(f"- {row.get('title')}: {_citations(row)}" for row in record.get("papers") or [])
    lines.append("Required analyses (answer each id in `checklist`; if one cannot be done, say why):")
    lines.extend(f"- {row.get('id')}: {row.get('analysis')} — {row.get('why')} [{_citations(row)}]"
                 for row in record.get("required") or [])
    lines.append("Recommended analyses:")
    lines.extend(f"- {row.get('analysis')} — {row.get('why')} [{_citations(row)}]"
                 for row in record.get("recommended") or [])
    if record.get("limitations"):
        lines.append("Search limitations: " + "; ".join(map(str, record["limitations"])))
    return "\n".join(lines)


def _precedent_items(record: Any) -> list[topic_checklists.ChecklistItem]:
    if not isinstance(record, dict) or record.get("status") != "ok":
        return []
    return [topic_checklists.ChecklistItem(str(row["id"]), str(row.get("analysis") or ""),
                                           str(row.get("why") or ""), "precedent")
            for row in record.get("required") or [] if row.get("id")]


def required_checklist_items(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                             precedents: Any) -> list[topic_checklists.ChecklistItem]:
    body = plan if isinstance(plan, dict) else {}
    items = topic_checklists.requirements(body.get("topics"), catalog)
    by_id = {item.id: item for item in items}
    for item in _precedent_items(precedents):
        by_id.setdefault(item.id, item)
    return list(by_id.values())


def checklist_errors(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                     precedents: Any) -> list[str]:
    body = plan if isinstance(plan, dict) else {}
    steps = body.get("steps") if isinstance(body.get("steps"), list) else []
    ids = [step.get("id") for step in steps if isinstance(step, dict) and isinstance(step.get("id"), str)]
    return topic_checklists.answer_errors(body.get("checklist"),
                                          required_checklist_items(body, catalog, precedents), ids)


def checklist_skip_warnings(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                             precedents: Any) -> list[str]:
    """One warning per required check the plan answered assumption with a reason (PI 2026-10-07, #446)."""
    body = plan if isinstance(plan, dict) else {}
    return topic_checklists.skip_warnings(body.get("checklist"), required_checklist_items(body, catalog, precedents))


def with_checklist_skip_warnings(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                                 precedents: Any) -> Any:
    """The plan with exactly the skip warnings of its current checklist, one per item.

    Earlier skip warnings (a re-plan's old plan, a CSO that copied them) are dropped first, so a check the new plan
    does, or skips for a changed reason, is not still counted (PR #452 review)."""
    if not isinstance(plan, dict):
        return plan
    prefix = topic_checklists.SKIP_WARNING + " "
    current = checklist_skip_warnings(plan, catalog, precedents)
    previous = plan.get("warnings") or []
    kept = [warning for warning in previous if not (isinstance(warning, str) and warning.startswith(prefix))]
    if not current and len(kept) == len(previous):
        return plan
    return {**plan, "warnings": [*kept, *current]}


def checklist_skip_line(plan: Any) -> str:
    """The report warning preview line counting the skip warnings the plan carries."""
    body = plan if isinstance(plan, dict) else {}
    prefix = topic_checklists.SKIP_WARNING + " "
    names = [str(warning)[len(prefix):].split(":", 1)[0] for warning in body.get("warnings") or []
             if isinstance(warning, str) and warning.startswith(prefix)]
    if not names:
        return ""
    shown = ", ".join(names[:8]) + (f" (+{len(names) - 8}개)" if len(names) > 8 else "")
    return f"- 점검표: 못 한 점검 {len(names)}건 (이유는 계획 경고와 한계): {shown}"


def checklist_meta(catalog: dict[str, list[topic_checklists.ChecklistItem]]) -> dict[str, Any]:
    """Task meta that has the runner write the checklist TSV the plan prompt names (#420)."""
    files = topic_checklists.workspace_files(catalog)
    return {"workspace_files": files} if files else {}


def planning_guidance(catalog: dict[str, list[topic_checklists.ChecklistItem]], precedents: Any) -> str:
    # Every plan sees the public resource reference (PI 2026-10-06, #435 C), with or without a topic checklist.
    resources = public_resources.prompt_rule()
    if not catalog and not isinstance(precedents, dict):
        return ("\n" + resources) if resources else ""
    lines = [topic_checklists.prompt_rule(catalog)]
    rendered = analysis_precedents_text(precedents)
    if rendered:
        lines += ["", rendered,
                  "Put every recommended analysis in the plan when scope and budget allow. Otherwise add one short "
                  "cited reason to top-level `suggested_next` (maximum 8)."]
    if resources:
        lines += ["", resources]
    return "\n".join(lines)


def plan_review_context(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]], precedents: Any) -> str:
    body = plan if isinstance(plan, dict) else {}
    required = required_checklist_items(body, catalog, precedents)
    rendered = (analysis_precedents_text(precedents)
                if isinstance(precedents, dict) and precedents.get("status") == "ok" else "")
    if not required and not rendered:
        return ""
    lines = ["\n\nDeclared topic checklist and plan answers:"]
    lines.extend(f"- {item.id}: {item.check} Why: {item.why}" for item in required)
    answers = body.get("checklist") if isinstance(body.get("checklist"), dict) else {}
    lines.append("Answers: " + json.dumps(answers, ensure_ascii=False, sort_keys=True))
    if rendered:
        lines += ["", rendered]
    return "\n".join(lines)


def plan_report_context(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]], precedents: Any) -> str:
    body = plan if isinstance(plan, dict) else {}
    limits = topic_checklists.limitations(body.get("checklist"))
    # Only checks this plan requires (PR #427 review): a stray key or one left from an earlier topic must not tell the
    # writer to drop a QC or analysis description from the body.
    required = {item.id for item in required_checklist_items(body, catalog, precedents)}
    skipped = [item_id for item_id in topic_checklists.not_applicable(body.get("checklist")) if item_id in required]
    rendered = (analysis_precedents_text(precedents)
                if isinstance(precedents, dict) and precedents.get("status") == "ok" else "")
    if not limits and not skipped and not rendered:
        return ""
    lines = []
    if limits:
        lines += ["\n\nChecklist limitations (one line each under 한계):", *(f"- {item}" for item in limits)]
    if skipped:
        # Bench C (#373): a literature table listed batch, pairing and gene-set checks as limitations and lost
        # readability. Checks that do not apply stay out of the body; the plan keeps the record.
        lines.append("\n\nChecks that do not apply to this request (not limitations; leave them out of the report "
                     "body): " + ", ".join(skipped))
    if rendered:
        lines += ["", rendered]
    return "\n".join(lines)


SoloPhase = Literal["solo", "review", "fallback", "done"]


def _solo_result_problem(result: TaskResult) -> str:
    if not result.ok:
        return result.error or "task failed"
    if not result.text.strip():
        return "empty answer"
    if not result.outputs:
        return "no outputs"
    return ""


def solo_phase(req: dict, cfg: Any) -> SoloPhase | None:
    """Choose the next stored solo-route phase; ``None`` is the ordinary team route."""
    route = req.get("route_decision") or {}
    if is_terminal_request(req.get("status")):
        return "done" if route.get("mode") == "solo" or route.get("fallback") else None
    if route.get("fallback"):
        return "fallback"
    if route.get("mode") != "solo":
        return None
    stored = req.get("solo_result")
    if not isinstance(stored, dict):
        return "solo"
    result = TaskResult.model_validate(stored)
    if _solo_result_problem(result):
        return "fallback"
    if not cfg.solo_review:
        return "done"
    stored_review = req.get("solo_review")
    if valid_review(stored_review):
        return "done" if with_p1_verdict(stored_review).get("verdict") == "accept" else "fallback"
    if isinstance(stored_review, dict) and stored_review.get("status") == "review_unparsed":
        return "fallback"
    return "review"


def _object_with_key(text: str, key: str) -> dict | None:
    """The largest JSON object in ``text`` that has ``key`` at its top level, read leniently, else None."""
    decoder, best = json.JSONDecoder(strict=False), None
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            obj, end = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and key in obj and (best is None or end > best[1]):
            best = (obj, end)
    return best[0] if best else None


def blocking_question(result: TaskResult) -> str | None:
    """The PI decision a step stopped for (STEP_PROMPT), from the runner field or its JSON, else None.

    STEP_PROMPT puts each choice on its own line, so a step may write a real newline inside the JSON string;
    that still reads as the question instead of dropping it (#342 review).
    """
    structured = (result.structured if isinstance(result.structured, dict)
                  else extract_json(result.text, strict=False))
    # Only free text is searched: a structured result is authoritative, and its text may quote a sample JSON
    # (a CLI engine's log line) that is not a question (PR #344 review).
    if not isinstance(result.structured, dict) and not (
            isinstance(structured, dict) and "blocking_decision" in structured) and result.text:
        # A larger unrelated object (with a raw newline, read leniently) must not hide the question (PR #343 review).
        structured = _object_with_key(result.text, "blocking_decision") or structured
    question = result.blocking_decision or (structured.get("blocking_decision") if isinstance(structured, dict)
                                            else None)
    return question.strip() if isinstance(question, str) and question.strip() else None


def failure_kind(outcome: TaskResult | BaseException, engine: str = "") -> str | None:
    """Classify dispatch outcomes with this retry table.

    Outcome                                      Kind
    Successful result                            None
    Known engine login expiry                   login
    Environment signature in the error, error   environment
      kind or CLI stderr (labhq/facilities);
      a stderr-only one yields to an explicit
      terminal or transient cause in the error
      below and to an engine limit
    Offline, timeout, empty result/CLI stream,  transient
      explicit rate-limit/overload/5xx/network signal
    Policy/approval/budget/cancel/invalid input, terminal
      ordinary nonzero exit, other error
    Environment signature only in the last       environment
      failed command's output (no later command
      succeeded), no engine limit, no other rule

    An exit code alone is never evidence that another run is safe. An environment failure is not retried: the same
    PC fails the same way until someone installs or frees what is missing (#35).
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
    if is_login_error(engine, error):
        return "login"
    if outcome.quota_reset_at is not None or is_quota_error("", error):
        return "quota"
    strong = _strong_environment(outcome, engine)
    # A signature seen only in the CLI's stderr is older than an explicit cause in the error (cancel, permission,
    # approval, budget, policy; timeout, rate limit, 5xx) and than an engine limit, which win (PR #447 review).
    if strong and (strong.get("source") == "error"
                   or not (_terminal_signal(error) or _transient_signal(error) or _engine_limit(outcome))):
        return "environment"
    if _terminal_signal(error):
        return "terminal"
    if (("invalid model selection" in error and "is not recognized" in error)
            or any(word in error for word in ("model catalog", "model catalogue", "failed to fetch models"))):
        return "transient"
    if any(word in error for word in ("validat", "invalid", "not found")):
        return "terminal"
    if outcome.ok or (not outcome.text.strip() and
                      (not error or "empty cli stream" in error or "no result event" in error)):
        return "transient"
    if _transient_signal(error):
        return "transient"
    if outcome.environment and outcome.environment.get("source") == "command" and not _engine_limit(outcome):
        # The last failed command, with no successful command after it, and nothing else explains the failure. An
        # engine limit (turns, budget) is its own cause even when the agent last tripped on a missing tool.
        return "environment"
    return "terminal"


def _terminal_signal(error: str) -> bool:
    """An explicit cause in a lower-cased error that another run does not fix: policy, permission, approval,
    budget, cancel."""
    return any(word in error for word in ("policy", "permission", "denied", "approval", "auth",
                                          "budget", "cancel", "ineligibletier", "401"))


def _engine_limit(outcome: TaskResult) -> bool:
    """The engine stopped at its own limit (turns, budget): that is the cause, whatever else the run saw."""
    return (outcome.error_kind or "").startswith("error_max_")


def _transient_signal(error: str) -> bool:
    """An explicit retry-worthy signal in a lower-cased error: timeout, rate limit, overload, network drop, 5xx."""
    if any(word in error for word in ("timeout", "timed out", "rate limit", "rate-limit", "429",
                                      "overload", "capacity", "temporar", "resource exhausted",
                                      "too many requests", "connection reset", "connection refused",
                                      "connection aborted", "broken pipe", "network unreachable",
                                      "runner restarted", "try again")):
        return True
    return bool(re.search(r"\b5\d{2}\b|\b5xx\b", error))


def _strong_environment(outcome: TaskResult, engine: str) -> dict | None:
    """An environment signature in the step's own error or error kind, or one the runner read in the CLI's stderr."""
    found = env_signatures.match(engine, outcome.error, error_kind=outcome.error_kind)
    if found:
        return found.record("error")
    env = outcome.environment
    return env if env and env.get("source") != "command" else None


def environment_problem(outcome: TaskResult | dict | None, engine: str = "") -> dict | None:
    """The environment signature ({id, cause, hint, source}) of a failed step, or None when it failed otherwise.

    A skipped step carries its upstream's error after "skipped: "; the upstream step is the one with the problem,
    so the skipped one has none of its own (PR #447 review)."""
    if isinstance(outcome, dict):
        try:
            outcome = TaskResult.model_validate(outcome)
        except ValueError:
            return None
    if (not isinstance(outcome, TaskResult) or (outcome.error or "").startswith("skipped:")
            or failure_kind(outcome, engine) != "environment"):
        return None
    return _strong_environment(outcome, engine) or outcome.environment


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

    async def _plan_clarification(self, rid: str, candidate: dict) -> tuple[str, dict | None]:
        """Expose only new plan questions and store one answered card; the caller performs the next re-plan."""
        req = self.hub.requests[rid]
        details = unanswered_questions(candidate.get("clarifying_questions"), req.get("clarifications"))
        candidate["clarifying_questions"] = details
        if not details:
            req["pending_questions"] = []
            req.pop("pending_question_details", None)
            req.pop("clarification_failure", None)
            self.hub.save_request(rid)
            return "done", None
        req["pending_questions"] = [question["question"] for question in details]
        if has_structure(details):
            req["pending_question_details"] = details
        else:
            req.pop("pending_question_details", None)
        self.hub.save_request(rid)
        await self._emit(rid, "request.questions", {"questions": req["pending_questions"], "details": details})
        if not self.cfg.wait_for_clarification:
            return "open", None
        if len(req.get("clarifications") or []) >= MAX_CLARIFY_CARDS:
            return "limit", None
        detail = {"questions": details}
        assumptions = normalize_assumptions(candidate.get("assumptions"))
        if assumptions:
            detail["assumptions"] = assumptions
        decision = await self.hub.request_approval(
            kind="clarify", request_id=rid, summary=questions_summary(details), detail=detail)
        if not decision.get("approved") or not str(decision.get("note") or "").strip():
            return "unanswered", None
        entry = {"questions": list(req["pending_questions"]), "answer": str(decision["note"]).strip()}
        if has_structure(details) or any(question.get("id") for question in details):
            entry["question_details"] = details
        req.setdefault("clarifications", []).append(entry)
        req["pending_questions"] = []
        req.pop("pending_question_details", None)
        self.hub.save_request(rid)
        return "answered", entry

    def _output_vocab(self) -> output_vocab.Vocab | None:
        """The vocabulary when plan.declare_output_types is on and it loads; None means today's plan, unchanged."""
        plan = getattr(self.hub.s, "plan", None)
        return output_vocab.current() if getattr(plan, "declare_output_types", False) else None

    def _lab_scope(self) -> str:
        scope = str(getattr(getattr(self.hub.s, "lab", None), "scope", None) or "").strip()
        return scope or DEFAULT_LAB_SCOPE

    async def _scope_gate(self, rid: str, req: dict) -> bool:
        """Hold an "out" general request until the PI says to proceed (#36, option C). False: the request ended.

        The plan is saved before the card, so "proceed" runs that plan without re-planning. After a gateway restart
        the card is gone and the request resumes here: a saved decision is used, a pending one is asked again from
        the stored plan, and no step has run either way. Without wait_for_clarification nothing waits for the PI,
        so the verdict is only recorded, as with clarifying questions."""
        check = req.get("scope_check") or {}
        if check.get("verdict") != "out" or check.get("decision") in ("proceed", "not_asked"):
            return True
        if check.get("decision") not in ("declined", "timed_out"):
            if not self.cfg.wait_for_clarification:
                check["decision"] = "not_asked"
                self.hub.save_request(rid)
                return True
            check["decision"] = "pending"
            self.hub.save_request(rid)  # the plan and the pending card survive a restart together
            reason = check.get("reason", "").rstrip(" .") or "사유 없음"
            decision = await self.hub.request_approval(
                kind="scope", request_id=rid, summary=short(f"이 요청은 랩 범위 밖으로 보입니다: {reason}. 진행할까요?", 700),
                detail={"gate": "scope", "verdict": "out", "reason": check.get("reason", ""),
                        "steps": len((req.get("plan") or {}).get("steps") or [])})
            check["decision"] = ("proceed" if decision.get("approved") else
                                 "timed_out" if decision.get("state") == "timed_out" else "declined")
            self.hub.save_request(rid)
            if check["decision"] == "proceed":
                return True
        req["outcome"] = "out_of_scope_declined"
        why = "the approval timed out" if check["decision"] == "timed_out" else "the PI declined it"
        self._finish(rid, f"Out-of-scope request stopped: {why}; no step was dispatched.", {}, ok=False,
                     error="범위 밖 요청이라 실행하지 않았습니다")
        return False

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

    def _session_origin(self, request_id: str | None, agent_id: str, session_id: str | None) -> str | None:
        """The runner whose task made this session, from the durable ledger; None when unknown (old ledger rows,
        test hubs without a ledger)."""
        store = getattr(self.hub, "store", None)
        if not (session_id and store is not None):
            return None
        for entry in store.all("task").values():
            result = entry.get("result") or {}
            if (entry.get("request_id") == request_id and (entry.get("payload") or {}).get("agent_id") == agent_id
                    and result.get("session_id") == session_id):
                return entry.get("runner_id") or None
        return None

    def _foreign_session_runner(self, request_id: str | None, agent_id: str, session_id: str | None) -> str | None:
        """The runner that made this session when another runner now hosts the agent id, else None.

        Dispatch goes to whichever runner registered the agent id last, so resuming there would open the first
        runner's session id and absolute workdir on another PC (PR #391 review). This early check gives the PI a
        clear refusal; the binding guard is the ``session_runner`` pin that Hub.dispatch checks (PR #393 review)."""
        origin = self._session_origin(request_id, agent_id, session_id)
        current = getattr(self.hub, "agent_runner", {}).get(agent_id)
        return origin if origin and current and origin != current else None

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
                # A consult does not need the old workspace, so another runner's session is not refused: it opens fresh.
                if (prior or self._consult_resource_busy(routed, session_id, workdir)
                        or self._foreign_session_runner(ask.request_id, routed, session_id)):
                    session_id, workdir = None, None
                origin = self._session_origin(ask.request_id, routed, session_id)
                consult = Task(
                    agent_id=routed, request_id=ask.request_id, prompt=prompt,
                    resume_session_id=session_id if self.hub.supports_resume(routed) else None,
                    meta={"kind": "consult", "ask_id": ask.id, "title": f"{ask.agent_id} 질의 답변",
                          "agent_overrides": overrides,
                          **({**({"source_workdir": source_workdir} if source_workdir else {}),
                              "consult_refs": refs} if refs else {}),
                          **({"workdir": workdir} if workdir else {}),
                          **({"session_runner": origin} if origin and (session_id or workdir) else {})},
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
        agent = entry["agent_id"]
        direct = req.get("mode") == "direct" or req.get("followup_agent_id") == agent
        async def refuse(reason: str, outcome: str) -> None:
            entry.update(status="failed", answer="", error=reason, answered_at=time.time())
            self.hub.save_request(rid)
            if getattr(self.hub, "semantics_shadow", None) is not None:  # semantics-hook: actions
                self.hub.semantics_shadow.after_followup(rid, fid, "ended", outcome)  # semantics-hook: actions
            await self._emit(rid, "request.followup_done", {
                "id": fid, "ok": False, "answer": "", "error": reason,
                "cost_usd": float(req.get("cost_usd") or 0), "cost_known": req.get("cost_known", True),
                **_cost_summary_field(req)})

        try:
            refusal = read_only_refusal(agent, self._engine(rid, agent))
        except ValueError as error:  # its request's CSO model cannot be honored: never resume as another model
            refusal = str(error)
        if refusal:  # the same workspace and session, with an engine that would not keep it read-only
            await refuse(refusal, "refused_read_only")
            return
        if direct:
            session_id, workdir = self._last_agent_session(rid, agent)
        else:
            session_id, workdir = req.get("cso_session_id"), req.get("cso_workdir")
        foreign = self._foreign_session_runner(rid, agent, session_id)
        if foreign:  # the workspace it would read is on that runner's PC
            await refuse(f"이 요청의 작업 세션은 runner {foreign}에 있는데, 지금 {agent}는 다른 runner에 연결돼 "
                         f"있어요. runner {foreign}가 연결된 뒤 다시 물어 주세요.", "failed")
            return
        await self._emit(rid, "request.followup", {"id": fid, "text": entry["text"], "agent_id": agent,
                                                   "status": "running"})
        # A restart marks the running follow-up interrupted, but its runner may still answer it in this
        # session and workdir (#144).
        origin = self._session_origin(rid, agent, session_id)
        session_id, workdir = await self._free_session(agent, session_id, workdir, rid=rid, step="followup")
        # Whatever is still resumed (session or absolute workdir) is pinned to its runner at dispatch time.
        pin = {"session_runner": origin} if origin and (session_id or workdir) else {}
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
                          **({"workdir": workdir} if workdir else {}), **pin})
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
    def _with_research_round(self, task: Task) -> Task:
        """A continuation round's tasks carry ``research_round`` (2 and up), so restart recovery never hands a step,
        plan or review the result of the same id from an earlier round (gateway ``_matches_recovery``)."""
        contract = ((self.hub.requests.get(task.request_id or "") or {}).get("research_contract") or {})
        round_no = int(contract.get("round") or 1)
        if round_no <= 1 or "research_round" in task.meta:
            return task
        return task.model_copy(update={"meta": {**task.meta, "research_round": round_no}})

    async def run_step(self, task: Task, first_attempt: int = 1) -> TaskResult:
        task = self._with_research_round(self._with_request_identity(task))
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

        def login_deadline(current: Task) -> float:
            """The request's window for this engine, joined by the turn about to park on it (every login park
            goes through here, so leave_login_window sees every recovering turn)."""
            req = self.hub.requests[rid]
            windows = req.setdefault("login_windows", {})
            if engine not in windows:
                hold = getattr(self.hub, "login_hold", lambda _engine: None)(engine)
                started = float(hold["waiting_since"]) if hold else time.time()
                windows[engine] = {"started_at": started,
                                   "deadline_at": (float(hold["deadline_at"]) if hold else
                                                   started + self.cfg.login_wait_max_s)}
                self.hub.save_request(rid)
            window = windows[engine]
            if turn_key(current) not in (window.get("turns") or []):
                window["turns"] = [*(window.get("turns") or []), turn_key(current)]
                self.hub.save_request(rid)
            return float(window["deadline_at"])

        def turn_key(current: Task) -> str:
            """One logical turn, the same through its retries and wake turns (they copy the meta). Consults to
            different agents run in parallel under one kind, so each ask is its own turn (PR #401 review)."""
            base = str(current.meta.get("step_id") or current.meta.get("kind") or current.id)
            ask_id = current.meta.get("ask_id") if not current.meta.get("step_id") else None
            return f"{base}:{ask_id}" if ask_id else base

        def leave_login_window(current: Task) -> None:
            """The window is per request and engine and lives while any of its turns is recovering, parked or
            retrying (PR #398 review): a turn removes only itself, and the last one out drops the window."""
            req = self.hub.requests.get(rid) or {}
            window = (req.get("login_windows") or {}).get(engine)
            if window is None:
                return
            mine = {turn_key(current)}
            if current.meta.get("ask_id") and not current.meta.get("step_id"):
                mine.add(str(current.meta.get("kind")))  # a window saved before PR #404 holds the bare kind
            turns = [key for key in window.get("turns") or [] if key not in mine]
            if turns:
                if turns != window.get("turns"):
                    window["turns"] = turns
                    self.hub.save_request(rid)
                return
            req["login_windows"].pop(engine, None)
            if not req.get("login_windows"):
                req.pop("login_windows", None)
            self.hub.save_request(rid)

        async def login_failure(current: Task, reason: str) -> TaskResult:
            leave_login_window(current)
            finished = getattr(self.hub, "login_recovered", None)
            if finished is not None:
                await finished(engine, reason="expired")
            return TaskResult(task_id=current.id, agent_id=current.agent_id, ok=False,
                              error_kind="login_wait_limit", error=reason)

        async def dispatch_with_retry(current: Task, max_attempts: int | None = None,
                                      start: int = 1) -> TaskResult:
            key = turn_key(current)
            limit = max_attempts or self.cfg.step_max_attempts
            first_attempt = min(max(getattr(self.hub, "recovery_attempt", lambda _task: 1)(current), start),
                                limit)
            previous_workdir = current.meta.get("workdir")
            previous_session = current.resume_session_id
            previous_result = None
            retry_answers = []
            tool_errors: list[str] = []
            for attempt in range(first_attempt, limit + 1):
                await self._check_budget(rid)
                login_hold = getattr(self.hub, "login_hold", lambda _engine: None)(engine)
                if login_hold:
                    deadline = login_deadline(current)
                    if not await self.hub.wait_login(rid, key, engine,
                                                     resume_at=float(login_hold["resume_at"]),
                                                     deadline_at=deadline,
                                                     reason="same engine account is waiting for login",
                                                     agent_id=current.agent_id):
                        return await login_failure(current, "engine login wait exceeded the configured maximum")
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
                    dispatched = with_pi_notes(attempt_task, self.hub.requests.get(rid) or {})
                    res = await self.hub.dispatch(dispatched)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    res = TaskResult(task_id=attempt_task.id, agent_id=current.agent_id, ok=False, error=str(exc))
                    kind = failure_kind(exc, engine)
                    offline = isinstance(exc, RunnerUnavailable)
                else:
                    kind = failure_kind(res, engine)
                    if kind != "login":  # a rejected login never starts a billed model turn
                        self._count_cost(rid, res.task_id, res)
                    previous_workdir = res.workdir or previous_workdir
                    if res.session_id and self.hub.supports_resume(current.agent_id):
                        previous_session = res.session_id
                    if kind == "environment":  # the record the step result, its event and the report show (#35)
                        res = res.model_copy(update={"environment": environment_problem(res, engine)})
                if res.tool_errors:
                    tool_errors.extend(res.tool_errors)
                if tool_errors:  # a retry that succeeds keeps the earlier attempts' failed lookups (PR #363 review)
                    res = res.model_copy(update={"tool_errors": list(tool_errors)})
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
            tool_errors = list(res.tool_errors)
            while True:
                login = not res.ok and is_login_error(engine, res.error)
                if login:
                    deadline = login_deadline(current)
                    window = self.hub.requests[rid]["login_windows"][engine]
                    if window.get("task_id") == res.task_id:
                        resume_at = float(window["resume_at"])
                    else:
                        resume_at = time.time() + self.cfg.login_retry_s
                        window.update(task_id=res.task_id, resume_at=resume_at)
                        self.hub.save_request(rid)
                    if resume_at > deadline:
                        return await login_failure(current, "engine login wait exceeded the configured maximum; "
                                                   f"retry={resume_at:.0f}, deadline={deadline:.0f}")
                    key = turn_key(current)
                    if not await self.hub.wait_login(rid, key, engine, resume_at=resume_at,
                                                     deadline_at=deadline,
                                                     reason=res.error or "engine login required",
                                                     agent_id=current.agent_id):
                        return await login_failure(current, "engine login wait exceeded the configured maximum")
                    can_resume = bool(res.session_id and self.hub.supports_resume(current.agent_id))
                    current = current.model_copy(update={
                        "id": new_id("task"),
                        "prompt": continuation_prompt(turn, "The engine account is signed in again. "
                                                               "Continue the same task.",
                                                          resumable=can_resume, previous_result=res,
                                                          context_chars=self.cfg.context_chars_per_step),
                        "meta": {**current.meta, "kind": current.meta.get("kind", "step"),
                                 "parent_task": res.task_id,
                                 **({"workdir": res.workdir} if res.workdir else {})},
                        "resume_session_id": res.session_id if can_resume else None,
                    })
                    res = await dispatch_with_retry(current, max_attempts)
                    tool_errors.extend(res.tool_errors)
                    res = res.model_copy(update={"tool_errors": list(tool_errors)})
                    continue
                recovered = getattr(self.hub, "login_recovered", None)
                if recovered is not None:
                    await recovered(engine)
                leave_login_window(current)
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
                key = turn_key(current)
                if not await self.hub.wait_quota(rid, key, engine, resume_at=resume_at,
                                                 deadline_at=deadline, reason=res.error or "subscription quota"):
                    return quota_failure(current, "subscription quota wait exceeded the configured maximum")
                can_resume = bool(res.session_id and self.hub.supports_resume(current.agent_id))
                # The turn that hit the quota is the base, so a wake turn keeps its job results and ask answers.
                current = current.model_copy(update={
                    "id": new_id("task"),
                    "prompt": continuation_prompt(turn, "The subscription quota has reset. "
                                                           "Continue the same task.",
                                                     resumable=can_resume, previous_result=res,
                                                     context_chars=self.cfg.context_chars_per_step),
                    "meta": {**current.meta, "kind": current.meta.get("kind", "step"),
                             "parent_task": res.task_id,
                             **({"workdir": res.workdir} if res.workdir else {})},
                    "resume_session_id": res.session_id if can_resume else None,
                })
                res = await dispatch_with_retry(current, max_attempts)  # its first gate rechecks the budget
                tool_errors.extend(res.tool_errors)
                res = res.model_copy(update={"tool_errors": list(tool_errors)})

        res = await dispatch_turn(task, max_attempts=task.meta.get("max_attempts"), start=initial_attempt)
        read_only = is_read_only_task(task.meta)
        environment = None if res.ok else environment_problem(res, engine)
        propose = facility_fixes.proposal(environment, res.error, res.tool_errors)
        approve_fix = getattr(self.hub, "request_facilities_fix", None)
        if propose and not read_only and callable(approve_fix):
            # Use the logical turn key, not a generated task id: direct requests must keep their one-fix limit
            # when the gateway restarts while the approval or repair is in flight.
            key = turn_key(task)
            decision = await approve_fix(rid, key, propose)
            record = (decision.get("execution")
                      if decision.get("status") in {"applied", "succeeded"} else None)
            if not decision.get("approved"):
                record = {**propose, "ok": False, "status": decision.get("status") or "declined",
                          "approval_id": decision.get("approval_id"), "note": decision.get("note") or ""}
                res = res.model_copy(update={"facilities_fix": record})
            else:
                remember = getattr(self.hub, "record_facilities_fix", None)
                if not record:
                    if propose["execution"] in {"python_package", "r_package"}:
                        instruction = facility_fixes.retry_instruction(propose)
                        record = {**propose, "ok": None, "status": "applied",
                                  "mode": "task_local_instruction", "instruction": instruction}
                    else:
                        fix_task = Task(agent_id=task.agent_id, request_id=rid, prompt="",
                                        meta={**task.meta, "kind": "facilities_fix", "step_id": key,
                                              "parent_task": res.task_id, "title": f"{key}: 승인된 환경 수정",
                                              "facilities_fix": propose,
                                              **({"workdir": res.workdir} if res.workdir else {})})
                        try:
                            fixed = await self.hub.dispatch(fix_task)
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            raise
                        except Exception as exc:
                            fixed = TaskResult(task_id=fix_task.id, agent_id=task.agent_id, ok=False,
                                               error=f"facilities fix dispatch failed: {exc}")
                        record = fixed.facilities_fix or {**propose, "ok": fixed.ok,
                                                           "status": "succeeded" if fixed.ok else "failed",
                                                           **({"error": fixed.error} if fixed.error else {})}
                    if callable(remember):
                        await remember(rid, key, record)
                can_rerun = record.get("status") == "applied" or bool(record.get("ok"))
                if not can_rerun:
                    res = res.model_copy(update={"facilities_fix": record})
                else:
                    can_resume = bool(res.session_id and self.hub.supports_resume(task.agent_id))
                    repair_instruction = (record.get("instruction") or
                                          (facility_fixes.retry_instruction(propose)
                                           if propose["execution"] in {"python_package", "r_package"} else ""))
                    retry_note = (repair_instruction + "\n\n" if repair_instruction else
                                  "The PI approved and the runner completed the allowlisted environment fix. ")
                    retry = task.model_copy(update={
                        "id": new_id("task"),
                        "prompt": continuation_prompt(
                            task, retry_note + "Retry the same task once now.",
                            resumable=can_resume, previous_result=res,
                            context_chars=self.cfg.context_chars_per_step),
                        "context": "", "resume_session_id": res.session_id if can_resume else None,
                        "meta": {**task.meta, "parent_task": res.task_id, "facilities_rerun": True,
                                 **({"workdir": res.workdir} if res.workdir else {})},
                    })
                    retried = await dispatch_turn(retry, max_attempts=1)
                    final_record = record
                    if propose["execution"] in {"python_package", "r_package"}:
                        rerun_environment = None if retried.ok else environment_problem(retried, engine)
                        if retried.ok:
                            final_record = {**record, "ok": True, "status": "succeeded"}
                        elif (rerun_environment and
                              rerun_environment.get("id") == propose.get("signature_id")):
                            final_record = {**record, "ok": False, "status": "failed",
                                            "error": retried.error or rerun_environment.get("cause") or
                                                     "same environment failure after repair"}
                        if final_record is not record and callable(remember):
                            await remember(rid, key, final_record)
                    res = retried.model_copy(update={
                        "facilities_fix": final_record,
                        "tool_errors": list(dict.fromkeys([*res.tool_errors, *retried.tool_errors])),
                    })
        overrides = task.meta.get("agent_overrides") or {}
        # A read-only task (consult, follow-up) has nothing to save, and a wrap-up must never lift its limits.
        turn_limit = int(overrides.get("max_turns") or (self.hub.agents.get(task.agent_id) or {}).get("max_turns") or 0)
        # A research step cannot be re-planned, so one that hits its turn limit first finishes in the same session
        # under a smaller limit (7th mock trial: QC had reproduced every number when its 40 turns ran out). Jobs and
        # questions belong to the turn that made them, so a turn still waiting on them keeps the old path
        # (PR #355 review).
        finishes = int(task.meta.get("finish_turns") or 0) if task.meta.get("kind") == "step" else 0
        while (finishes > 0 and not res.ok and res.error_kind == "error_max_turns" and res.session_id
               and not read_only and not waiting(res) and self.hub.supports_resume(task.agent_id)):
            finishes -= 1
            finish = Task(agent_id=task.agent_id, request_id=rid, output_schema=task.output_schema,
                          prompt=continuation_prompt(task, FINISH_PROMPT, resumable=True, previous_result=res,
                                                     context_chars=self.cfg.context_chars_per_step),
                          resume_session_id=res.session_id,
                          meta={**task.meta, "parent_task": res.task_id,
                                "title": f"{task.meta.get('step_id') or task.id}: 턴 상한 뒤 마무리",
                                **({"workdir": res.workdir} if res.workdir else {}),
                                **({"agent_overrides": {**overrides, "max_turns": max(turn_limit // 2,
                                                                                      min(turn_limit, 10))}}
                                   if turn_limit else {})})
            try:
                res = merged_turn(res, await dispatch_turn(finish, max_attempts=1))
            except BudgetExceeded:
                break
        if (not res.ok and res.error_kind == "error_max_turns" and res.session_id and not read_only
                and self.hub.supports_resume(task.agent_id)):
            wrap = Task(agent_id=task.agent_id, request_id=rid,
                        prompt=continuation_prompt(task, WRAP_PROMPT, resumable=True,
                                                   previous_result=res, context_chars=self.cfg.context_chars_per_step),
                        resume_session_id=res.session_id,
                        meta={**task.meta, "kind": "wrap_up", "parent_task": res.task_id,
                              "workdir": res.workdir,
                              "agent_overrides": {**overrides, "max_turns": min(WRAP_TURNS, turn_limit or WRAP_TURNS)},
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
                                             "tool_errors": [*res.tool_errors, *partial.tool_errors],
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
            res = merged_turn(res, await dispatch_turn(wake))
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
        ancestors = step_ancestors(steps)
        req_state = self.hub.requests.get(rid)
        research_plan = ((req_state or {}).get("plan") if
                         ((req_state or {}).get("research_contract") or {}).get("execution_enabled") else None)
        shared_environment_protected = any(
            ENV_LOCK_OUTPUT in step.get("outputs", []) for step in steps)
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

        def input_steps(step: dict) -> list[str]:
            """Direct dependencies, then every other ancestor in plan order.

            A step may read an ancestor it reaches only through another step: the v0.5 trial's preprocessing read
            the fetch step's matrix through the pairing and QC steps, found no inputs/<fetch> link and failed
            (2026-10-08). Every ancestor's outputs are linked; only direct dependencies get their full result text.
            """
            direct = list(step["depends_on"])
            return direct + [s["id"] for s in steps if s["id"] in ancestors[step["id"]] and s["id"] not in direct]

        def upstream(step: dict) -> str:
            parts = []
            for d in step["depends_on"]:
                r = results.get(d)
                if r:
                    head = f"## {d} · {by_id[d]['agent_id']}" + ("" if r.ok else f" (FAILED: {short(r.error, 200)})")
                    artifacts = [{"workdir_id": r.workdir_id, "path": path}
                                 for path in r.outputs]
                    # Relative to the step folder, where the runner links inputs/<step id> (#423): a script that
                    # names these paths still runs from a request bundle on another machine.
                    paths = "\n".join(input_relpath(d, path) or str(Path(r.workdir) / path)
                                      for path in r.outputs) if r.workdir else ""
                    parts.append(f"{head}\nDeclared output artifacts: {json.dumps(artifacts)}\n"
                                 f"Readable files (relative to your folder):\n{paths}\n"
                                 f"{clip(r.text, self.cfg.context_chars_per_step)}")
            for d in input_steps(step)[len(step["depends_on"]):]:
                r = results.get(d)
                if r and r.ok and r.workdir and r.outputs:
                    artifacts = [{"workdir_id": r.workdir_id, "path": path} for path in r.outputs]
                    paths = "\n".join(input_relpath(d, path) or str(Path(r.workdir) / path) for path in r.outputs)
                    parts.append(f"## {d} · {by_id[d]['agent_id']} (earlier step, files only)\n"
                                 f"Declared output artifacts: {json.dumps(artifacts)}\n"
                                 f"Readable files (relative to your folder):\n{paths}")
            return "\n\n".join(parts)

        async def run_one(step: dict) -> TaskResult:
            template = RESEARCH_STEP_PROMPT if research_plan else STEP_PROMPT
            prompt = template.format(request=request, step_id=step["id"], instruction=step["instruction"])
            if research_plan:
                contract = ((req_state or {}).get("research_contract") or {})
                prompt += ("\n\nReturn the structured research result contract required by the output schema. "
                           f"Copy plan_sha256={contract.get('plan_sha256')}, step_id={step['id']}, "
                           f"claim_ids={json.dumps(step.get('claim_ids') or [])}, and fill evidence_slots="
                           f"{json.dumps(step.get('evidence_slots') or [])}. Keep claims and evidence separate. "
                           "Each artifact_refs path is one of your declared outputs (outputs/<name>) or an upstream "
                           "artifact written as <workdir_id>/<path>; evidence citing any other path is refused at CP2. "
                           "If you cannot proceed without a PI decision, return the same schema with every list "
                           "empty and the question with its choices in blocking_decision; you re-run with the answer."
                           "\n\nCross-field result rules (the JSON schema cannot express these):\n" +
                           RESEARCH_RESULT_FIELD_RULES +
                           "\n\nFrozen protocol, approved by the PI at CP1:\n" + _research_protocol_digest(research_plan) +
                           "\nApply its selection and exclusion criteria, analysis unit and statistics exactly as "
                           "written. If the data force a different rule, use the closest workable one, record it in "
                           "method_changes (field, planned, actual, reason, affects_conclusion), and never describe "
                           "the result as following the pre-specified rule.")
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
                updates.append(f"[Scientific reviewer feedback — revise your step]\n{feedback[step['id']]}"
                               + REVISION_RESULT_RULE)
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
            if revising and can_resume and ctx:
                # A resumed session gets only the updates; without this it keeps the upstream results it read before
                # the revision round (12th mock trial). Always, not only when `feedback` names a dependency: after a
                # restart mid-round a finished upstream revision is no longer in it (PR #368 review).
                updates.append(f"[Current upstream results — they replace what you read before]\n{ctx}")
            linked = input_steps(step)
            upstream_dirs = [results[d].workdir for d in linked
                             if d in results and results[d].workdir and results[d].outputs]
            upstream_steps = {d: results[d].workdir for d in linked
                              if d in results and results[d].workdir and results[d].outputs}
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
                              "upstream_dirs": upstream_dirs, "upstream_steps": upstream_steps,
                              "outputs": step.get("outputs", []),
                              "environment_step": ENV_LOCK_OUTPUT in step.get("outputs", []),
                              "shared_environment_protected": shared_environment_protected,
                               **({"general_result_contract": True} if not research_plan else {}),
                               **self._type_meta(step),
                               **({"finish_turns": self.hub.s.research.finish_turns} if research_plan else {}),
                               **({"workdir": workdir} if workdir else {})})
            if updates:
                task = task.model_copy(update={
                    "prompt": continuation_prompt(task, "\n\n".join(updates), resumable=can_resume,
                                                   previous_result=previous,
                                                   context_chars=self.cfg.context_chars_per_step),
                    "context": ""})
            async with sem:
                outcome = await self.run_step(task)
                if decision and previous and previous.tool_errors:
                    outcome = outcome.model_copy(update={"tool_errors": list(dict.fromkeys([
                        *previous.tool_errors, *outcome.tool_errors,
                    ]))})
                if not research_plan:
                    return attach_general_result(outcome)
                if not outcome.ok or blocking_question(outcome):
                    return outcome
                if step.get("outputs"):
                    missing = [name for name in step["outputs"] if output_relpath(name) not in outcome.outputs]
                    if missing:
                        return outcome.model_copy(update={"ok": False, "missing_outputs": missing,
                                                          "error": f"incomplete: missing outputs: {', '.join(missing)}"})

                original = outcome
                current = outcome
                limit = self.hub.s.research.result_corrections

                def save_salvage(refused_rows: list[dict], unsupported_claims: list[dict]) -> None:
                    if req_state is None:
                        return
                    contract = req_state.get("research_contract") or {}
                    salvage = contract.setdefault("result_salvage", {})
                    if refused_rows or unsupported_claims:
                        salvage[step["id"]] = {"refused_rows": refused_rows,
                                               "unsupported_claims": unsupported_claims}
                    else:
                        salvage.pop(step["id"], None)
                        if not salvage:
                            contract.pop("result_salvage", None)
                    self.hub.save_request(rid)

                # The last result that passed the contract and only cited a file labhq did not collect (#485). An
                # optional correction that fails, touches no collected file but writes others, or ends worse falls
                # back to it: asking to re-point a citation never fails a step that would have passed before.
                bindable: tuple[Any, TaskResult] | None = None

                def accept(ledger: Any, via: TaskResult) -> TaskResult:
                    save_salvage([], [])
                    return original.model_copy(update={
                        "structured": ledger.model_dump(mode="json"),
                        "session_id": via.session_id or original.session_id,
                        "workdir": via.workdir or original.workdir,
                    })

                for correction in range(limit + 1):
                    asked = blocking_question(current)
                    structured = (current.structured if isinstance(current.structured, dict)
                                  else extract_json(current.text))
                    if isinstance(structured, dict):  # the step schema's empty question field is not ledger
                        structured = {k: v for k, v in structured.items() if k != "blocking_decision"}
                    problems = (["a result correction cannot ask a new blocking_decision; return corrected JSON"]
                                if asked else research_result_errors(structured, plan=research_plan))
                    validated_result = None
                    if not problems:
                        validated_result = validate_research_result(structured, plan=research_plan)
                        if validated_result.step_id != step["id"]:
                            problems = [f"research result step_id {validated_result.step_id} does not match {step['id']}"]
                    if not problems and validated_result is not None and correction < limit and bindable is None:
                        # Asked once: a row still unbound after that, or after the last correction, stays for CP2
                        # to refuse; it never fails the step.
                        problems = unbound_evidence_problems(validated_result.model_dump(mode="json"), original,
                                                             ancestor_artifacts(steps, ancestors, results, step["id"]))
                        if problems:
                            bindable = (validated_result, current)
                    if not problems and validated_result is not None:
                        return accept(validated_result, current)
                    if correction == limit and bindable is not None:
                        return accept(*bindable)
                    if correction == limit:
                        # A result that still asks the PI a blocking question is never salvaged into CP2: the
                        # employee said it cannot go on without that decision (PR #353 review).
                        salvaged, refused_rows, unsupported_claims, salvage_problems = (
                            (None, [], [], []) if asked else
                            salvage_research_result(structured, plan=research_plan, expected_step_id=step["id"]))
                        if salvaged is not None and refused_rows:
                            save_salvage(refused_rows, unsupported_claims)
                            return original.model_copy(update={
                                "structured": salvaged.model_dump(mode="json"),
                                "session_id": current.session_id or original.session_id,
                                "workdir": current.workdir or original.workdir,
                            })
                        return original.model_copy(update={
                            "ok": False, "error": "invalid research result contract: " +
                            "; ".join(dict.fromkeys([*problems, *salvage_problems])),
                            "session_id": current.session_id or original.session_id,
                            "workdir": current.workdir or original.workdir,
                        })

                    can_resume_correction = bool(current.session_id and self.hub.supports_resume(step["agent_id"]))
                    correction_task = Task(
                        agent_id=step["agent_id"], request_id=rid, output_schema=RESEARCH_STEP_SCHEMA,
                        resume_session_id=current.session_id if can_resume_correction else None,
                        prompt=continuation_prompt(task, result_correction(problems),
                                                   resumable=can_resume_correction, previous_result=current,
                                                   context_chars=self.cfg.context_chars_per_step),
                        meta={**task.meta, "kind": "result_correction", "parse_attempt": correction + 1,
                              "parent_task": current.task_id, "outputs": [],
                              "title": f"{step['id']}: 결과 계약 교정 #{correction + 1}",
                              **({"workdir": current.workdir} if current.workdir else {})},
                    )
                    current = await self.run_step(correction_task)
                    if not current.ok and bindable is not None:
                        return accept(*bindable)
                    if not current.ok:
                        return current.model_copy(update={
                            "outputs": original.outputs, "output_sha256": original.output_sha256,
                            "unreported_outputs": original.unreported_outputs,
                            "workdir": current.workdir or original.workdir,
                            "workdir_id": current.workdir_id or original.workdir_id,
                        })
                    if (current.unreported_outputs and bindable is not None
                            and not set(current.unreported_outputs) & set(original.outputs)):
                        return accept(*bindable)  # collected files and their hashes are untouched
                    if current.unreported_outputs:
                        # A correction only rewrites the result JSON. A file it changed would no longer match the
                        # hash CP2 binds evidence to, so the step fails instead (PR #352 review).
                        return original.model_copy(update={
                            "ok": False,
                            "error": "invalid research result contract: the result correction changed output files "
                                     "it must not touch: " + ", ".join(current.unreported_outputs),
                            "session_id": current.session_id or original.session_id,
                            "workdir": current.workdir or original.workdir,
                        })

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
                    previous = results.get(sid)
                    if (feedback and sid in feedback and previous and previous.ok and not outcome.ok
                            and outcome.error_kind not in {"ask_rejected", "wake_limit"}):
                        outcome = previous.model_copy(update={"revision_failed":
                            f"{outcome.error_kind or failure_kind(outcome) or 'terminal'}: {outcome.error or 'unknown error'}"})
                    results[sid] = outcome
                    environment = None if outcome.ok else environment_problem(outcome)
                    await self._emit(rid, "request.step_done", {"step_id": sid, "ok": results[sid].ok,
                                                                "agent_id": by_id[sid]["agent_id"],
                                                                "attempts": self.attempts.get(rid, {}).get(sid, 0),
                                                                "reason": results[sid].error,
                                                                **({"environment": environment} if environment
                                                                   else {}),
                                                                **({"facilities_fix": results[sid].facilities_fix}
                                                                   if results[sid].facilities_fix else {})})
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
        # CP2 approves these ledgers as results of the plan frozen at CP1, so each must validate under it. A reused
        # continuation result is a copy rebound to the new plan (``carry_over``); this catches any other drift
        # (PR #448 review).
        unbound = self._unbound_research_results(req, steps, results)
        if unbound:
            contract["failure"] = {"steps": sorted(unbound), "plan_sha256": contract["plan_sha256"],
                                   "unbound_results": unbound}
            req["outcome"] = "research_failed"
            self.hub.save_request(rid)
            self._finish(rid, self.report_results(steps, results, n) +
                         "\n\nResearch stopped before CP2: these step results do not validate under the frozen plan "
                         f"{contract['plan_sha256'][:12]}:\n" +
                         "\n".join(f"- {sid}: {'; '.join(problems)}" for sid, problems in sorted(unbound.items())),
                         serialized(), ok=False)
            return
        ledgers = {s["id"]: results[s["id"]].structured for s in steps}
        ancestors = step_ancestors(steps)
        refused: list[dict[str, str]] = []
        refused_rows: list[dict[str, str]] = []
        unsupported: list[dict[str, str]] = []
        artifact_sha256: dict[str, str | None] = {}
        unreported_outputs = {s["id"]: list(results[s["id"]].unreported_outputs) for s in steps}
        source_verification: list[str] = []
        source_reports: list[dict[str, Any]] = []
        live_resolver = None
        if self.hub.s.research.live_source_check:
            live_resolver = LiveSourceResolver(
                contact=self.hub.s.research.live_source_contact,
                request_timeout_s=self.hub.s.research.live_source_timeout_s)
        for step in steps:
            result = results[step["id"]]
            salvaged = (contract.get("result_salvage") or {}).get(step["id"]) or {}
            step_refused = [{"step_id": step["id"], **row} for row in salvaged.get("refused_rows") or []]
            refused_rows += step_refused
            refused += [{"step_id": step["id"], "evidence_id": row["row_id"], "reason": row["reason"]}
                        for row in step_refused if row.get("row_type") == "evidence"]
            unsupported += [{"step_id": step["id"], **row}
                            for row in salvaged.get("unsupported_claims") or []]
            bound = bind_result_artifacts(result.structured if isinstance(result.structured, dict) else {},
                                           outputs=list(result.outputs),
                                           upstream=ancestor_artifacts(steps, ancestors, results, step["id"]),
                                           output_sha256=dict(result.output_sha256))
            refused += [{"step_id": step["id"], **row} for row in bound["refused_evidence"]]
            unsupported += [{"step_id": step["id"], **row} for row in bound["unsupported_claims"]]
            artifact_sha256.update({f"{step['id']}/{artifact_id}": value
                                    for artifact_id, value in bound["artifact_sha256"].items()})
            if live_resolver is not None:
                parsed = validate_research_result(result.structured, plan=req["plan"])
                checked = await verify_sources(
                    parsed, live_resolver,
                    timeout_s=self.hub.s.research.live_source_timeout_s,
                    deadline_s=self.hub.s.research.live_source_deadline_s)
                source_reports.append(checked.model_dump(mode="json"))
                source_verification += verification_lines(checked)
                for evidence_id in checked.defective_evidence:
                    evidence_check = next(item for item in checked.evidence if item.evidence_id == evidence_id)
                    reason = (", ".join(evidence_check.source_defects) or
                              f"{evidence_check.resolution.status}: {evidence_check.resolution.detail}")
                    refused.append({"step_id": step["id"], "evidence_id": evidence_id,
                                    "reason": "live source defect: " + reason})
                unsupported += [{"step_id": step["id"], "claim_id": claim.claim.split("@", 1)[0],
                                 "reason": "live source defect: " + "; ".join(claim.defects)}
                                for claim in checked.claims if claim.state == "defective"]
                for evidence_id, reason in zip(checked.recited_evidence, checked.recitations):
                    refused.append({"step_id": step["id"], "evidence_id": evidence_id,
                                    "reason": "live source defect: " + reason})
                    unsupported += [
                        {"step_id": step["id"], "claim_id": claim.claim.split("@", 1)[0],
                         "reason": "live source defect: " + reason}
                        for claim in checked.claims if evidence_id in claim.verified_evidence]
        claims = sum(len((ledger or {}).get("claims") or []) for ledger in ledgers.values())
        rows = sum(len((ledger or {}).get("evidence") or []) for ledger in ledgers.values())
        summary = (f"CP2 evidence review: {len(ledgers)} step(s), {claims} claim(s), {rows} evidence row(s)" +
                   (f", {len(refused_rows) or len(refused)} refused" if refused_rows or refused else "") +
                   ". Choose approve, revise or deny.")
        detail = {"gate": "research_evidence", "plan_sha256": contract["plan_sha256"],
                   "choices": list(EVIDENCE_CHOICES),
                   **({"refused_rows": refused_rows} if refused_rows else {}),
                   **({"refused_evidence": refused} if refused else {}),
                  **({"unsupported_claims": unsupported} if unsupported else {}),
                  **({"source_verification": source_verification} if source_verification else {}),
                  "artifact_sha256": artifact_sha256, "unreported_outputs": unreported_outputs,
                  "results": ledgers}
        carried = contract.get("continuation") or {}
        reuse_lines: list[str] = []
        if carried.get("plan_sha256") == contract["plan_sha256"]:  # a continuation round (#90)
            detail["continuation"] = {key: carried.get(key) for key in
                                      ("round", "from_plan_sha256", "reuse", "reused_from", "refused_reuse")}
            reuse_lines.append(f"Continuation round {carried.get('round')}: reused from plan "
                               f"{str(carried.get('from_plan_sha256'))[:12]} with output hashes checked again, "
                               "each result rebound to this plan: "
                               f"{', '.join(carried.get('reuse') or []) or 'none'}.")
            reuse_lines += [f"- reuse refused, re-ran {row['step_id']}: {row['reason']}"
                            for row in carried.get("refused_reuse") or []]
        recorded = (contract.get("checkpoints") or {}).get("cp2") or {}
        source_changed = (live_resolver is not None and bool(recorded.get("decision"))
                          and recorded.get("plan_sha256") == contract["plan_sha256"]
                          and list(recorded.get("source_verification") or []) != source_verification)
        if source_changed:
            detail["previous_source_verification"] = list(recorded.get("source_verification") or [])
            summary = "Live source verification changed after the recorded CP2 decision. " + summary
        if (recorded.get("decision") and recorded.get("plan_sha256") == contract["plan_sha256"]
                and not source_changed):
            # A restart after the receipt was saved: the PI already decided this plan's CP2, so it is not asked again.
            decided, note, asks = recorded["decision"], str(recorded.get("note") or ""), recorded.get("asks")
            refused = recorded.get("refused_evidence") or []
            refused_rows = recorded.get("refused_rows") or []
            unsupported = recorded.get("unsupported_claims") or []
            source_verification = recorded.get("source_verification") or source_verification
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
            # Kept per step in the receipt: a continuation reuses a step together with its salvage records (#90).
            salvage = contract.pop("result_salvage", None) or {}
            contract.setdefault("checkpoints", {})["cp2"] = {
                "gate": "research_evidence", "decision": decided, "choice": decision.get("choice"), "note": note,
                "approval_id": decision.get("approval_id"), "decided_at": decision.get("decided_at"),
                "plan_sha256": contract["plan_sha256"], "asks": asks,
                **({"refused_rows": refused_rows} if refused_rows else {}),
                "refused_evidence": refused, "unsupported_claims": unsupported,
                "artifact_sha256": artifact_sha256, "unreported_outputs": unreported_outputs,
                **({"source_verification": source_verification,
                    "source_verification_reports": source_reports} if live_resolver is not None else {}),
                **({"result_salvage": salvage} if salvage else {})}
        req["outcome"] = f"evidence_{decided}"
        self.hub.save_request(rid)
        audit = f"CP2 evidence review: {decided}."
        if refused_rows:
            audit += "\n계약에 맞지 않아 뺀 근거:\n" + "\n".join(
                f"- {row['step_id']}/{row['row_type']} {row['row_id']}: {row['reason']}" for row in refused_rows)
        if refused:
            audit += "\nRefused evidence (not approved at CP2):\n" + "\n".join(
                f"- {row['step_id']}/{row['evidence_id']}: {row['reason']}" for row in refused)
        if unsupported:
            audit += "\nUnsupported claims:\n" + "\n".join(
                f"- {row['step_id']}/{row['claim_id']}: {row['reason']}" for row in unsupported)
        source_audit = (("Live source verification:\n" +
                         "\n".join(f"- {line}" for line in source_verification))
                        if source_verification else "")
        if reuse_lines:
            audit += "\n" + "\n".join(reuse_lines)
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
            review_audit = audit + (("\n" + source_audit) if source_audit else "")
            await self._research_report(rid, text, steps, results, n, serialized, packs, report, review_audit, ledgers,
                                        refused=refused, unsupported=unsupported,
                                        artifact_sha256=receipt.get("artifact_sha256") or artifact_sha256)
            return
        if decided == "approved":
            report += ("\nNo reviewer agent is configured (orchestrator.reviewer_agent), so the research review and "
                       "report did not run.")
        if source_audit:
            report = _append_report_metadata(report, [source_audit])
        self._finish(rid, report, serialized(), ok=decided == "approved")

    async def _research_report(self, rid: str, text: str, steps: list[dict], results: dict[str, TaskResult], n: int,
                               serialized: Callable[[], dict[str, dict]], packs: dict, cp2_report: str,
                               cp2_audit: str, ledgers: dict[str, Any], *, refused: list[dict],
                               unsupported: list[dict],
                               artifact_sha256: dict[str, str | None]) -> None:
        """After CP2 approval: one research review, then the CSO's report and its claim-anchor check (#58 ③⑤).

        The plan stays frozen, so a review that asks for revision ends the request unless the PI continues through
        a new plan and CP1 (``_research_continue``). The review is saved in the contract and reused after a
        restart; the report is written again.
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
            if lookup_section:
                report = _execution_warning_summary(report)
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
            catalog = topic_checklists.load()
            prompt = RESEARCH_REVIEW_PROMPT.format(
                request=text, plan_sha256=plan_hash, plan=_research_plan_digest(req["plan"]),
                packs=render_pack_review(packs),
                ledgers=self._research_ledgers(steps, results, ledgers, refused, unsupported, artifact_sha256, n)) + \
                plan_review_context(req.get("plan"), catalog, req.get("analysis_precedents"))
            carried = contract.get("continuation") or {}
            if carried.get("plan_sha256") == plan_hash:
                prompt += research_continuation.review_prompt(carried.get("p1_issues") or [])
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
            note = await self._research_continue(rid, review, results)
            if note is None:  # the PI continued: a new plan, CP1 and run took this request over
                return
            end("research_review_revise", "\n".join([
                cp2_report, "", f"Research review ({reviewer}): revise.", *issues,
                "labhq does not re-run a frozen research PLAN; a fixed plan needs a new CP1 approval of its hash.",
                *([note] if note else [])]),
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
                issues="\n".join(issues) or "(none)", results=self.format_results(steps, results, n)) +
                plan_report_context(req.get("plan"), topic_checklists.load(),
                                    req.get("analysis_precedents")),
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
        body, model_appendix = _split_report_appendix(report_body(final.text))
        check = check_report(body, ledgers, unsupported=unsupported, refused=refused,
                             artifact_sha256=artifact_sha256)
        contract["report_check"] = check
        claim_check = (["Claim check: the report is incomplete.\n" +
                        "\n".join(_problem_lines(check["problems"], "- "))] if check["problems"] else [])
        if check["problems"]:
            body = _execution_warning_summary(body)
        body_reference = _review_reference(review)
        if body_reference and not REVIEW_REFERENCE_HEADING.search(body):
            body = body.rstrip() + "\n\n" + body_reference
        # The body keeps only P2 lines and a P3 count, so the record holds every remaining issue verbatim (PR #438).
        open_reference = [issue for issue in (review or {}).get("issues") or []
                          if isinstance(issue, dict) and issue.get("priority") in {"P2", "P3"}]
        review_record = (["남은 P2·P3 지적 원문:\n" + "\n".join(
            f"- {issue.get('priority')} · {issue.get('step_id') or '-'}: {issue.get('problem')} → {issue.get('request')}"
            for issue in open_reference)] if open_reference else [])
        report = body.rstrip() + "\n\n" + _appendix_sections(model_appendix,
                                                             [*claim_check, *review_record, cp2_audit])
        end("report_incomplete" if check["problems"] else "research_reported", report,
            not check["problems"] and rid not in self.budget_denials, review)

    async def _research_continue(self, rid: str, review: dict, results: dict[str, TaskResult]) -> str | None:
        """After a research review "revise": ask the PI whether to continue through a new CP1 (#90, #58).

        Returns None when the PI continued (this request then re-plans and runs again here), else a line for the
        revise report ("" when no card was shown). The decision is saved with the plan hash it answers, so a restart
        neither asks again nor forgets an approval. On approval the finished round (plan, results, CP2 receipt,
        review) is archived in ``research_contract.rounds`` in the same save that raises ``round``; the results stay
        in place until the new plan is adopted (``_adopt_continuation_plan``).
        """
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        plan_hash = contract["plan_sha256"]
        rounds = list(contract.get("rounds") or [])
        decided = contract.get("continue_decision") or {}
        if decided.get("plan_sha256") != plan_hash:
            limit = self.hub.s.research.revise_continuations
            if len(rounds) >= limit:
                return (f"이어 가기 상한(research.revise_continuations={limit})에 닿아 새 CP1을 묻지 않았습니다."
                        if limit else "")
            issues = research_continuation.p1_issues(review)
            round_no = len(rounds) + 2
            decision = await self.hub.request_approval(
                kind=research_continuation.CONTINUE_GATE, request_id=rid,
                summary=(f"연구 리뷰가 revise(P1 {len(issues)}건)로 끝났습니다. 리뷰 지적을 넣은 새 계획으로 "
                         f"이어 갈까요({round_no}차)? 승인하면 CSO가 새 계획을 쓰고 새 CP1을 받습니다. 사양이 같고 "
                         "산출 hash가 그대로인 완료 단계는 다시 돌리지 않습니다. 거절하면 지금처럼 끝납니다."),
                detail={"gate": research_continuation.CONTINUE_GATE, "plan_sha256": plan_hash, "round": round_no,
                        "limit": limit, "p1_issues": issues})
            approved = bool(decision.get("approved"))
            decided = {"plan_sha256": plan_hash, "decision": "approved" if approved else "declined",
                       "approval_id": decision.get("approval_id"), "decided_at": decision.get("decided_at"),
                       "note": str(decision.get("note") or "").strip(),
                       **({"state": decision["state"]} if decision.get("state") else {})}
            contract["continue_decision"] = decided
            if approved:
                # Everything this round's frozen plan owns, so an unstarted continuation can put it back whole.
                rounds.append({**research_continuation.archive_round(
                    req, {sid: result.model_dump(mode="json") for sid, result in results.items()}),
                    "round": len(rounds) + 1})
                contract["rounds"] = rounds
                contract["round"] = round_no
                contract["continuation"] = {"round": round_no, "from_plan_sha256": plan_hash, "p1_issues": issues,
                                            "approval_id": decision.get("approval_id")}
            self.hub.save_request(rid)
        if decided.get("decision") != "approved":
            return ("이어 가기 카드가 답 없이 닫혀 새 CP1을 열지 않았습니다." if decided.get("state") == "timed_out"
                    else "PI가 새 CP1로 이어 가기를 거절했습니다.")
        await self._emit(rid, "request.continued", {"round": contract.get("round"), "from_plan_sha256": plan_hash})
        await self.run_request(rid, continuation=True)
        return None

    def _adopt_continuation_plan(self, rid: str, plan: dict, pack_hashes: dict[str, str]) -> None:
        """Pair the new plan with the previous round's results: keep only reusable steps' results (#90).

        Called before the save that stores the new plan, so a restart sees the plan and its pruned results together.
        The reuse is not final until the output hashes are checked after the new CP1 (``_verify_research_reuse``).
        ``pack_hashes`` is the pack snapshot the new plan is frozen with; a changed pack re-runs every step.

        The previous round's CP1 approval, CP2 receipt, review and continue decision are dropped in the same save:
        they are keyed by plan_sha256, and a new plan byte-identical to the revised one (a P1 execution mistake in an
        unchanged step) has the same hash. Kept, they would skip the new CP1 and CP2, reuse the "revise" review and
        continue again without the PI (PR #448 review). The round archive in ``rounds`` keeps them."""
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        carried = contract["continuation"]
        carry = research_continuation.carry_over(contract["rounds"][-1], plan, pack_hashes,
                                                 carried.get("p1_issues") or [])
        carried.update(plan_sha256=research_plan_sha256(plan), verified=False)
        carried.pop("refused_reuse", None)
        contract["pack_snapshot"] = dict(pack_hashes)  # the new plan's packs, saved with it
        for key in ("approval", "review", "continue_decision"):
            contract.pop(key, None)
        (contract.get("checkpoints") or {}).pop("cp2", None)
        self._apply_carry(rid, carry, set(req.get("step_decisions") or {}) | set(req.get("pending_revisions") or {}))
        req["pending_questions"] = []

    def _apply_carry(self, rid: str, carry: research_continuation.Carry, also_forget: set[str] = frozenset()) -> None:
        """Make ``carry`` the request's state: only the reused steps' results, each bound to the current plan, their
        salvage rows (CP2 shows them again) and the reuse record; every other step starts fresh (#90)."""
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        contract["continuation"].update(reuse=carry.reuse, rerun=carry.rerun, reused_from=carry.reused_from)
        if carry.salvage:
            contract["result_salvage"] = carry.salvage
        else:
            contract.pop("result_salvage", None)
        req["results"] = carry.results
        self._forget_step_state(rid, (set(carry.rerun) | set(also_forget)) - set(carry.reuse))

    def _forget_step_state(self, rid: str, steps: set[str]) -> None:
        """A step that runs again in a continuation starts fresh: no earlier PI decision, pending revision or
        attempt count, so it never resumes an earlier round's session in that round's work folder (#90)."""
        req = self.hub.requests[rid]
        for key in ("step_decisions", "pending_revisions"):
            entries = req.get(key) or {}
            for sid in [sid for sid in entries if sid in steps]:
                entries.pop(sid)
        for sid in steps:
            self.attempts.get(rid, {}).pop(sid, None)

    async def _verify_research_reuse(self, rid: str, plan: dict) -> None:
        """After the new CP1, before dispatch: hash every reused step's recorded outputs again (#90).

        A step whose files changed, vanished or cannot be checked on this PC, and every step below it, loses its
        reused result and runs. The decision is ``carry_over`` again, from the round archive and the frozen pack
        snapshot, with the hash refusals added: the same rule as at adoption, so a restart in between agrees with it,
        and reuse can only shrink from what the CP1 card showed."""
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        carried = contract["continuation"]
        saved = req.get("results") or {}
        reused = [sid for sid in carried.get("reuse") or [] if sid in saved]
        try:
            hashes = (await asyncio.to_thread(research_continuation.verify_reuse,
                                              {sid: saved[sid] for sid in reused}, self.hub.s)
                      if reused else {})
        except Exception as error:  # nothing reused that labhq could not check
            hashes = {sid: f"hash check failed: {type(error).__name__}: {error}" for sid in reused}
        refused = {sid: f"output hash check: {why}" for sid, why in hashes.items()}
        adopted = carried.get("rerun") or {}
        refused.update({step["id"]: adopted.get(step["id"]) or "not reused at CP1" for step in plan.get("steps") or []
                        if step["id"] not in reused and step["id"] not in refused})
        carry = research_continuation.carry_over(contract["rounds"][-1], plan, contract.get("pack_snapshot"),
                                                 carried.get("p1_issues") or [], refused=refused)
        # A refused step runs fresh, not as the round-1 session in the folder whose file changed (PR #448 review).
        self._apply_carry(rid, carry)
        carried.update(verified=True, refused_reuse=[{"step_id": sid, "reason": carry.rerun[sid]}
                                                     for sid in reused if sid not in carry.reuse])
        self.hub.save_request(rid)

    @staticmethod
    def _unbound_research_results(req: dict, steps: list[dict],
                                  results: dict[str, TaskResult]) -> dict[str, list[str]]:
        """{step: problems} for each result that does not validate under the request's frozen plan, which must hash
        to the contract's CP1-frozen plan_sha256."""
        frozen = ResearchPlan.model_validate(req["plan"])
        if research_plan_sha256(frozen) != req["research_contract"]["plan_sha256"]:
            return {s["id"]: ["the request plan does not hash to the CP1-frozen plan_sha256"] for s in steps}
        unbound: dict[str, list[str]] = {}
        for step in steps:
            problems = research_result_errors(results[step["id"]].structured, plan=frozen)
            if problems:
                unbound[step["id"]] = problems
        return unbound

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
            environment = environment_problem(r)
            # A failed command's output is weaker evidence than the missing outputs themselves (PR #447 review).
            if environment and not (r.missing_outputs and environment.get("source") == "command"):
                return env_signatures.problem_text(environment)
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

    def _ensure_route_decision(self, req: dict, roster: list[dict], *, research: bool) -> tuple[dict, bool]:
        """Keep a decision made before a restart; research and malformed stored values stay on the team path."""
        stored = req.get("route_decision")
        if (not research and isinstance(stored, dict) and stored.get("mode") in {"team", "solo"}
                and (stored.get("mode") != "solo" or stored.get("agent_id"))):
            return stored, False
        decision = route_decision(req.get("plan"), self.cfg.solo_agent, roster,
                                  requested=req.get("route", "auto"), research=research)
        req["route_decision"] = decision
        return decision, True

    @staticmethod
    def _solo_notes(req: dict) -> list[dict]:
        started = float(req.get("solo_started_at") or 0)
        return [note for note in req.get("pi_notes") or [] if float(note.get("at") or 0) > started]

    async def _run_solo(self, rid: str, text: str, refs: dict) -> bool:
        """Run or recover the planned single employee turn. True means the request ended; False falls back to DAG."""
        req = self.hub.requests[rid]
        decision = req.get("route_decision") or {}
        phase = solo_phase(req, self.cfg)
        if phase is None:
            return False
        agent = decision["agent_id"]
        result = None
        review = None

        if phase == "solo":
            req.setdefault("solo_started_at", time.time())
            self.hub.save_request(rid)
            catalog = topic_checklists.load()
            solo_context = (plan_review_context(req.get("plan"), catalog, req.get("analysis_precedents")) +
                            plan_report_context(req.get("plan"), catalog, req.get("analysis_precedents")))
            result = await self.run_step(Task(
                agent_id=agent, request_id=rid, budget_usd=req.get("budget_usd"),
                prompt=SOLO_PROMPT.format(request=text, assumptions=_assumptions_prompt(req.get("plan"))) +
                       solo_context,
                meta={**refs, "kind": "direct", "title": f"단독 처리: {req.get('text', '')[:80]}",
                      "project_dirs": req.get("project_dirs", []), "max_attempts": 1}))
            req["solo_result"] = result.model_dump(mode="json")
            self.hub.save_request(rid)
            phase = solo_phase(req, self.cfg)
            if rid in self.budget_denials:
                self._finish(rid, result.text or result.error or "task failed",
                             {"direct": result.model_dump(mode="json")}, ok=False,
                             error=self.budget_denials[rid])
                return True

        stored = req.get("solo_result")
        result = TaskResult.model_validate(stored) if isinstance(stored, dict) else None
        if phase == "review" and result is not None:
            reviewer = self.cfg.reviewer_agent
            if not reviewer or reviewer not in self.hub.agents:
                req["route_decision"] = {**decision, "mode": "team", "fallback": True,
                                         "reason": "solo science reviewer unavailable"}
                self.hub.save_request(rid)
                await self._emit(rid, "request.route", req["route_decision"])
                return False
            reviewed = await self.run_step(Task(
                agent_id=reviewer, request_id=rid, output_schema=REVIEW_SCHEMA,
                prompt=REVIEW_PROMPT.format(request=text, results=result.text) +
                       plan_review_context(req.get("plan"), topic_checklists.load(),
                                           req.get("analysis_precedents")),
                meta={**refs, "kind": "review", "revision": 0, "request": text,
                      "title": "단독 결과 과학 리뷰"}))
            parsed = reviewed.structured if valid_review(reviewed.structured) else extract_json(reviewed.text)
            review = with_p1_verdict(parsed) if reviewed.ok and valid_review(parsed) else {
                "status": "review_unparsed", "reason": reviewed.error or "missing or invalid verdict"}
            req["solo_review"] = review
            self.hub.save_request(rid)
            await self._emit(rid, "request.review", {"revision": 0, **review})
            phase = solo_phase(req, self.cfg)
            if rid in self.budget_denials:
                self._finish(rid, result.text or result.error or "task failed",
                             {"direct": result.model_dump(mode="json")}, ok=False,
                             review=review, error=self.budget_denials[rid])
                return True

        if phase == "fallback":
            reason = decision.get("reason") or (_solo_result_problem(result) if result else "")
            reason = reason or "solo science review did not accept the result"
            req["route_decision"] = {**decision, "mode": "team", "fallback": True, "reason": reason}
            self.hub.save_request(rid)
            await self._emit(rid, "request.route", req["route_decision"])
            return False

        if phase == "done" and result is None:
            return True

        report = result.text.strip()
        stored_review = req.get("solo_review")
        review = with_p1_verdict(stored_review) if valid_review(stored_review) else review
        late_notes = self._solo_notes(req)
        if late_notes:
            report += "\n\n단독 턴이 시작된 뒤 온 메모는 반영되지 않았다.\n" + "\n".join(
                f"- {note.get('id') or 'note'}: {note.get('text') or ''}" for note in late_notes)
        cost = (f"${float(result.cost_usd):.2f}" if result.cost_known and result.cost_usd is not None
                else "비용 미집계")
        paths = ", ".join(f"{result.workdir_id or 'unknown-workdir'}/{path}" for path in result.outputs)
        report = _append_report_metadata(report, [f"처리 방식: 단독 ({agent})", f"단독 턴 비용: {cost}",
                                                   f"산출 경로: {paths or 'none'}"])
        req["followup_agent_id"] = agent
        self._finish(rid, report, {"direct": result.model_dump(mode="json")}, ok=True, review=review)
        return True

    # ---------- request entry point ----------
    async def run_request(self, rid: str, resume: bool = False, continuation: bool = False) -> None:
        """``continuation``: a research request whose review ended "revise" goes on in this process through a new
        plan and CP1 (``_research_continue``); its running cost is kept, and no briefing runs again."""
        req = self.hub.requests[rid]
        refs = reference_meta(req)
        # Pointers ride with the request text, so briefing, plan, steps, review and report all see them (#36).
        text = (req["text"] + render_references(req.get("references")) +
                answered_questions_prompt(req.get("clarifications")))
        if not continuation:
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
            orchestration = set(ORCHESTRATION_ROLES) | {
                x for x in (self.cfg.cso_agent, self.cfg.chief_of_staff_agent, self.cfg.reviewer_agent) if x}
            roster = [a for a in all_agents if a["id"] not in orchestration]
            topic_vocab_for_checks = output_vocab.current()
            checklist_catalog = (topic_checklists.load(vocab=topic_vocab_for_checks)
                                 if topic_vocab_for_checks is not None else {})
            n = self.cfg.context_chars_per_step
            frozen_pack_snapshot = ((req.get("research_contract") or {}).get("pack_snapshot")
                                    if resume and research_lane else None)
            # A contract frozen before topic selection has no pack_applicability record: it resumes by the pack rule
            # it was approved under and never gains the record, so a second restart takes the same path (#390 review).
            legacy_pack_contract = (frozen_pack_snapshot is not None
                                    and "pack_applicability" not in (req.get("research_contract") or {}))
            configured_pack_defs = (packs_for_snapshot(self.hub.s, frozen_pack_snapshot)
                                    if frozen_pack_snapshot is not None else
                                    configured_packs(self.hub.s) if research_lane else {})
            packs = configured_pack_defs
            active_pack_hashes = pack_snapshot(packs)
            # The first plan, research plan and re-plan after resume (#271) all use this same snapshot.
            capabilities = format_capabilities(roster, getattr(self.hub, "runner_capabilities", None))

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
                    **({} if legacy_pack_contract else {"pack_applicability": plan.get("pack_applicability") or {}}),
                    "approval": approval,
                }
                self.hub.save_request(rid)
                plan_hash = req["research_contract"]["plan_sha256"]
                carried = req["research_contract"].get("continuation") or {}
                # This plan continues a revised one and its reuse is not yet checked against the files (#90).
                reuse_pending = carried.get("plan_sha256") == plan_hash and not carried.get("verified")
                if approval.get("status") != "approved":
                    summary = ("CP1 research plan approval: approve the frozen question, methods, completion/stop "
                               f"conditions, data boundary, and selected packs. plan_sha256={plan_hash}")
                    if reuse_pending:
                        summary = (f"이어 가기 {carried.get('round')}차 계획(리뷰 P1 반영). 재사용: "
                                   f"{', '.join(carried.get('reuse') or []) or '없음'} · 다시 실행: "
                                   f"{', '.join(carried.get('rerun') or {}) or '없음'}. ") + summary
                    decision = await self.hub.request_approval(
                        kind="research_plan", request_id=rid, summary=summary[:700],
                        detail={"gate": "research_plan", "target_sha256": plan_hash,
                                "plan_canonical": canonical_plan_json(plan),
                                "pack_applicability": plan.get("pack_applicability") or {},
                                "warnings": plan.get("warnings") or [],
                                "protocol_revision": plan["protocol"]["revision"],
                                "packs": plan["protocol"]["packs"],
                                "scope_status": plan["intake"]["scope_status"],
                                **({"continuation": {key: carried.get(key) for key in
                                                     ("round", "from_plan_sha256", "reuse", "rerun", "p1_issues")}}
                                   if reuse_pending else {})})
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
                if reuse_pending:
                    await self._verify_research_reuse(rid, plan)
                req["outcome"] = "research_running"
                self.hub.save_request(rid)
                return True

            if resume and req.get("plan", {}).get("steps"):
                if research_lane:
                    # A frozen contract resumes by its CP1 snapshot, never by a fresh applicability decision: the
                    # snapshot holds the applied packs, and a pack answered not_applicable (pre-topic, or a sentence-
                    # condition pack after topics) is absent from it (PR #390 review, both eras).
                    packs = (select_legacy_applied_packs(configured_pack_defs, req["plan"].get("pack_values"))
                             if frozen_pack_snapshot is not None else
                             select_applied_packs(configured_pack_defs, req["plan"].get("pack_values"),
                                                  topics=req["plan"].get("topics")))
                    active_pack_hashes = pack_snapshot(packs)
                    req["plan"], _ = _normalize_plan_outputs(req["plan"])
                    validated = validate_research_plan(req["plan"], max_steps=self.cfg.max_steps,
                                                       active_packs=active_pack_hashes,
                                                       expected_intake=intake, pack_definitions=packs)
                    req["plan"] = validated.model_dump(mode="json")
                    if req.get("checklist_contract"):
                        stored_problems = checklist_errors(req["plan"], checklist_catalog,
                                                           req.get("analysis_precedents"))
                        # A plan approved at CP1 before #446 may skip with a placeholder reason ("none", "n/a")
                        # the old rule accepted. The approval stands; the request records which items lack a reason
                        # instead of failing on restart (PR #452 review). The frozen plan itself is not changed.
                        frozen_answers = req["plan"].get("checklist") or {}
                        reasonless = [item for item in (problem.split(" must ", 1)[0].removeprefix("checklist.")
                                                        for problem in stored_problems
                                                        if " must answer with a reason" in problem)
                                      if isinstance(frozen_answers.get(item), str) and
                                      topic_checklists.ANSWER.fullmatch(frozen_answers[item].strip())]
                        if reasonless:
                            req["checklist_reasonless"] = reasonless
                            self.hub.save_request(rid)
                        stored_problems = [problem for problem in stored_problems
                                           if problem.split(" must ", 1)[0].removeprefix("checklist.")
                                           not in reasonless]
                        if stored_problems:
                            raise ValueError("frozen research plan checklist invalid: " +
                                             "; ".join(stored_problems))
                    if not await finish_research_plan(req["plan"]):
                        return
                    steps = req["plan"]["steps"]
                    warnings = req["plan"].get("warnings") or []
                elif req["mode"] == "plan_only":  # restarted after the plan was saved: still no step runs
                    req["outcome"] = "plan_only"
                    self._finish(rid, "Plan completed.", {}, ok=True)
                    return
                else:
                    req["plan"] = _carry_assumptions(None, req["plan"])
                    topic_vocab = output_vocab.current()
                    if topic_vocab is not None:
                        req["plan"] = normalize_plan_topics(req["plan"], topic_vocab, strict=False)
                    type_stats: dict = {}
                    vocab = self._output_vocab()
                    steps, warnings = validate_steps(req["plan"]["steps"], known, self.cfg.max_steps,
                                                     orchestration, vocab=vocab, stats=type_stats,
                                                     reject_excess=True)
                    req["plan"] = {**req["plan"], "steps": steps,
                                   "warnings": [*(req["plan"].get("warnings") or []), *warnings]}
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                decision, created = self._ensure_route_decision(req, roster, research=research_lane)
                self.hub.save_request(rid)
                if created:
                    await self._emit(rid, "request.route", decision)
                if not research_lane and not await self._scope_gate(rid, req):  # a card the restart closed (#36)
                    return
                if not research_lane and await self._run_solo(rid, text, refs):
                    return
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
                brief_job = (asyncio.create_task(self.run_step(Task(
                    agent_id=cos, request_id=rid, prompt=BRIEFING_PROMPT.format(request=text),
                    meta={**refs, "kind": "briefing", "request": text, "title": "CSO용 브리핑 준비"})))
                             if cos and cos in known and not continuation else None)
                # A continuation's plan prompt carries the revised plan and the reviewer's P1 issues (#90).
                carried = (req.get("research_contract") or {}).get("continuation") or {}
                rounds = (req.get("research_contract") or {}).get("rounds") or []
                continuation_note = (research_continuation.plan_prompt(
                    rounds[-1]["plan"], rounds[-1]["plan_sha256"], carried.get("p1_issues") or [],
                    int(carried.get("round") or 2)) if continuation and research_lane and rounds else "")
                precedent_job = None
                precedent_agent = self.cfg.precedent_agent
                if "analysis_precedents" not in req and precedent_agent:
                    if precedent_agent in known:
                        precedent_task = Task(
                            agent_id=precedent_agent, request_id=rid, output_schema=PRECEDENT_SCHEMA,
                            prompt=PRECEDENT_PROMPT.format(request=text),
                            meta={**refs, "kind": "precedent", "request": text,
                                  "title": "선행 연구 분석 기준 조사", "max_attempts": 1})
                        precedent_job = asyncio.create_task(self.run_step(precedent_task))
                    else:
                        req["analysis_precedents"] = precedent_warning("agent_unavailable")
                        self.hub.save_request(rid)

                async def stop_precedent_job() -> None:
                    """A request that ends before planning must not leave the literature scout running or its
                    exception unread (PR #395 review): ask its runner to cancel the turn, give the cancelled result
                    a short while to come back (so its cost is recorded), then cancel and reap the job."""
                    if precedent_job is None:
                        return
                    if not precedent_job.done():
                        cancel = getattr(self.hub, "cancel_task", None)
                        runner = getattr(self.hub, "task_runner", {}).get(precedent_task.id)
                        try:
                            if cancel is not None:  # kept until the turn ends, resent if its runner reconnects
                                await cancel(precedent_task.id)
                            elif runner:
                                await self.hub.send_runner(runner, {"type": "task.cancel",
                                                                    "task_id": precedent_task.id})
                        except Exception:  # an unreachable runner cannot run it either; the job is reaped below
                            pass
                    await asyncio.wait([precedent_job], timeout=PRECEDENT_CANCEL_GRACE_S)
                    if not precedent_job.done():
                        precedent_job.cancel()
                    await asyncio.gather(precedent_job, return_exceptions=True)

                if brief_job:
                    b = await brief_job
                    if not b.ok:
                        await stop_precedent_job()
                        self._finish(rid, f"브리핑 실패: {b.error}", {"briefing": b.model_dump(mode="json")}, ok=False)
                        return
                    if rid in self.budget_denials:
                        await stop_precedent_job()
                        self._finish(rid, "브리핑 뒤 예산 승인 거부", {"briefing": b.model_dump(mode="json")}, ok=False)
                        return
                    briefing = b.text
                if precedent_job:
                    try:
                        precedent_result = await precedent_job
                    except Exception:
                        precedent_result = None
                    parsed = None
                    if precedent_result is not None and precedent_result.ok:
                        raw = (precedent_result.structured if isinstance(precedent_result.structured, dict)
                               else extract_json(precedent_result.text))
                        parsed = normalize_precedents(raw)
                    req["analysis_precedents"] = (parsed if parsed is not None else precedent_warning(
                        "parse_failed" if precedent_result is not None and precedent_result.ok else "failed"))
                    self.hub.save_request(rid)
                req["checklist_contract"] = 1
                self.hub.save_request(rid)

                reuse_advisory = ""  # semantics-hook

                async def make_plan(plan_request: str) -> TaskResult:
                    continuation = self.hub.supports_resume(self.cfg.cso_agent)
                    session_id, workdir = await self._free_session(
                        self.cfg.cso_agent, req.get("cso_session_id") if continuation else None,
                        req.get("cso_workdir") if continuation else None, rid=rid, step="plan")
                    if research_lane:
                        vocab = self._output_vocab()
                        topic_vocab = output_vocab.current()
                        template = RESEARCH_CP2_PLAN_PROMPT if research_execution else RESEARCH_PLAN_PROMPT
                        prompt = template.format(
                            request=plan_request, roster=format_roster(roster),
                            capabilities=capabilities or "No workers available",
                            briefing=clip(briefing, 4000) or "(none)", max_steps=self.cfg.max_steps,
                            intake=json.dumps(intake.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                            packs=render_pack_catalog(configured_pack_defs), question_rule=QUESTION_RULE,
                            output_types_rule=output_types.prompt_rule(vocab) if vocab else "",
                            topics_rule=(topic_types.prompt_rule(topic_vocab, include_definitions=False)
                                         if topic_vocab else ""))
                        prompt += planning_guidance(checklist_catalog, req.get("analysis_precedents"))
                        prompt += reuse_advisory  # semantics-hook
                        prompt += continuation_note
                        schema = research_plan_schema(vocab is not None, output_types.ENTRY_SCHEMA)
                    else:
                        vocab = self._output_vocab()
                        topic_vocab = output_vocab.current()
                        prompt = PLAN_PROMPT.format(request=plan_request, roster=format_roster(roster),
                                                    capabilities=capabilities or "No workers available",
                                                    briefing=clip(briefing, 4000) or "(none)",
                                                    max_steps=self.cfg.max_steps, question_rule=QUESTION_RULE,
                                                    lab_scope=self._lab_scope(),
                                                    output_types_rule=output_types.prompt_rule(vocab) if vocab else "",
                                                    # Names only (PI 2026-10-05, #420): 43+ topics with
                                                    # definitions would crowd the plan prompt.
                                                    topics_rule=topic_types.prompt_rule(topic_vocab,
                                                                                        include_definitions=False)
                                                    if topic_vocab else "")
                        prompt += planning_guidance(checklist_catalog, req.get("analysis_precedents"))
                        schema = plan_schema(vocab is not None)
                    planned = await self.run_step(Task(
                        agent_id=self.cfg.cso_agent, request_id=rid, output_schema=schema,
                        resume_session_id=session_id, prompt=prompt,
                        meta={**refs, "kind": "plan", "roster": roster, "request": plan_request,
                              "title": "업무 분해·배정 계획 수립", **({"workdir": workdir} if workdir else {}),
                              **checklist_meta(checklist_catalog)}))
                    if planned.session_id:
                        req["cso_session_id"] = planned.session_id
                        req["cso_workdir"] = planned.workdir
                        self.hub.save_request(rid)
                    return planned

                async def resolve_plan_questions(candidate: dict, current: TaskResult,
                                                   suffix: str = "") -> tuple[dict | None, TaskResult]:
                    """Ask at most two cards for this request, filtering answered questions after each re-plan."""
                    nonlocal text
                    while True:
                        status, entry = await self._plan_clarification(rid, candidate)
                        if status in {"done", "open"}:
                            return candidate, current
                        if status in {"limit", "unanswered"}:
                            reason = ("확인 질문을 두 번 드렸지만 새 질문이 남아 요청을 멈췄습니다."
                                      if status == "limit" else
                                      "PI 확인 답변을 받지 못해 요청을 멈췄습니다.")
                            questions = "\n".join(f"- {question}" for question in req.get("pending_questions") or [])
                            req["clarification_failure"] = reason
                            self._finish(rid, reason + ("\n\n남은 질문:\n" + questions if questions else ""), {},
                                         ok=False, error=reason)
                            return None, current
                        text += answered_questions_prompt([entry])
                        current = await make_plan(text + suffix)
                        if not current.ok:
                            reason = f"답변 반영 계획을 만들지 못했습니다: {safe_failure_cause(current.error)}"
                            self._finish(rid, reason, {}, ok=False, error=reason)
                            return None, current
                        if rid in self.budget_denials:
                            reason = "답변 반영 계획의 예산 승인이 거부되었습니다."
                            self._finish(rid, reason, {"plan": current.model_dump(mode="json")},
                                         ok=False, error=reason)
                            return None, current
                        candidate = _carry_assumptions(
                            candidate, current.structured if isinstance(current.structured, dict)
                            else extract_json(current.text) or {})

                plan_res = await make_plan(text)
                if not plan_res.ok:
                    self._finish(rid, f"계획 실패: {plan_res.error}", {"plan": plan_res.model_dump(mode="json")}, ok=False)
                    return
                if rid in self.budget_denials:
                    self._finish(rid, "계획 뒤 예산 승인 거부", {"plan": plan_res.model_dump(mode="json")}, ok=False)
                    return
                plan = _carry_assumptions(
                    None, plan_res.structured if isinstance(plan_res.structured, dict)
                    else extract_json(plan_res.text) or {})
                if scope_verdict(plan) and "scope_first" not in req:
                    # Kept for a later plan (clarification, correction, A/B) that comes back without a verdict from an
                    # engine that does not enforce the schema: an out request must not run unasked (#346 review).
                    req["scope_first"] = scope_verdict(plan)
                # semantics-shadow: begin (#149 decision 15 advisory A/B)
                if research_lane and not continuation:  # the A/B arm was fixed by the request's first plan
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
                            plan = _carry_assumptions(
                                plan, plan_res.structured if isinstance(plan_res.structured, dict)
                                else extract_json(plan_res.text) or {})
                # semantics-shadow: end
                plan, plan_res = await resolve_plan_questions(plan, plan_res)
                if plan is None:
                    return
                if research_lane:
                    vocab = self._output_vocab()
                    topic_vocab = output_vocab.current()
                    workers = sorted(known - orchestration)

                    def plan_problems(candidate: Any, candidate_packs: dict[str, Any]) -> list[str]:
                        try:
                            problems = research_plan_errors(candidate, max_steps=self.cfg.max_steps,
                                                            active_packs=pack_snapshot(candidate_packs),
                                                            expected_intake=intake, pack_definitions=candidate_packs)
                        except (ValueError, TypeError) as error:
                            problems = [str(error)]
                        drafted = candidate.get("steps") if isinstance(candidate, dict) else None
                        bad_agents = unavailable_plan_agents(drafted, known, orchestration)
                        if bad_agents:
                            problems.append(f"research plan uses unavailable or orchestration agents: {bad_agents}; "
                                            f"use roster ids {workers}")
                        problems.extend(checklist_errors(candidate, checklist_catalog,
                                                         req.get("analysis_precedents")))
                        return problems

                    for attempt in (1, 2):
                        type_stats = {}
                        selection_problems = []
                        assessed = None
                        try:
                            if topic_vocab is None:
                                raise ValueError("research topics vocabulary is unavailable")
                            plan = normalize_plan_topics(plan, topic_vocab, strict=True)
                            plan = normalize_plan_keys(plan, configured_pack_defs, checklist_catalog)
                            candidate_packs, applicability, topic_warnings = assess_pack_applicability(
                                configured_pack_defs, plan.get("topics"),
                                plan.get("pack_values") if isinstance(plan, dict) else None)
                            assessed = candidate_packs
                            candidate_packs = select_applied_packs(
                                configured_pack_defs,
                                plan.get("pack_values") if isinstance(plan, dict) else None,
                                topics=plan.get("topics"),
                            )
                            plan = {**plan, "pack_applicability": applicability,
                                    "warnings": [*(plan.get("warnings") or []), *topic_warnings]}
                        except ValueError as error:
                            supplied = plan.get("pack_values") if isinstance(plan, dict) else None
                            # The correction names the packs the topics apply. Expecting only the supplied keys told
                            # a CSO that wrote one wrong key "expected []", and it dropped its pack values (v0.5 trial).
                            candidate_packs = assessed if assessed is not None else {
                                key: loaded for key, loaded in configured_pack_defs.items()
                                if isinstance(supplied, dict) and key in supplied and
                                not (isinstance(supplied[key], dict) and "not_applicable" in supplied[key])
                            }
                            selection_problems = [str(error)]
                        # The CSO answers every pack; labhq freezes exact refs only for applicable entries (#222).
                        precedent_state = req.get("analysis_precedents") or {}
                        if precedent_state.get("warning"):
                            plan = {**plan, "warnings": list(dict.fromkeys([
                                *(plan.get("warnings") or []), precedent_state["warning"]]))}
                        plan = with_pack_refs(plan, pack_refs(candidate_packs))
                        output_problems = []
                        try:
                            plan, _ = _normalize_plan_outputs(plan)
                        except PlanOutputsError as error:
                            output_problems = [str(error)]
                        # Declarations are normalized (or, when off, removed) after output paths, so names pair
                        # with the exact artifacts the runner will collect.
                        plan = prepare_research_declarations(plan, vocab, type_stats)
                        problems = selection_problems + output_problems + plan_problems(plan, candidate_packs)
                        if not problems:
                            # Part of the frozen plan, so CP1 shows them and the hash covers them (#446).
                            plan = with_checklist_skip_warnings(plan, checklist_catalog,
                                                                req.get("analysis_precedents"))
                            validated = validate_research_plan(plan, max_steps=self.cfg.max_steps,
                                                               active_packs=pack_snapshot(candidate_packs),
                                                               expected_intake=intake, pack_definitions=candidate_packs)
                            packs = candidate_packs
                            active_pack_hashes = pack_snapshot(packs)
                            break
                        if attempt == 2:
                            req["outcome"] = "plan_invalid"
                            req["plan_validation"] = {"attempts": attempt, "errors": problems}
                            report = plan_invalid_report(problems, configured_pack_defs)
                            self._finish(rid, report, {}, ok=False, error=report.split("\n", 1)[0])
                            return
                        correction_suffix = "\n\n" + plan_correction(problems)
                        plan_res = await make_plan(text + correction_suffix)
                        if not plan_res.ok:
                            self._finish(rid, f"Research re-plan failed: {plan_res.error}", {}, ok=False)
                            return
                        if rid in self.budget_denials:
                            self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                         {"plan": plan_res.model_dump(mode="json")}, ok=False)
                            return
                        plan = _carry_assumptions(
                            plan, plan_res.structured if isinstance(plan_res.structured, dict)
                            else extract_json(plan_res.text) or {})
                        plan, plan_res = await resolve_plan_questions(plan, plan_res, correction_suffix)
                        if plan is None:
                            return
                    req["plan"] = validated.model_dump(mode="json")
                    if continuation:  # saved below with the plan, so a restart sees both or neither
                        self._adopt_continuation_plan(rid, req["plan"], active_pack_hashes)
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                    warnings: list[str] = []
                    steps = req["plan"]["steps"]
                else:
                    vocab = self._output_vocab()
                    topic_vocab = output_vocab.current()
                    for attempt in (1, 2):
                        type_stats: dict = {}
                        try:
                            if topic_vocab is not None:
                                plan = normalize_plan_topics(plan, topic_vocab, strict=False)
                            plan = normalize_plan_keys(plan, {}, checklist_catalog)
                            steps, step_warnings = validate_steps(plan.get("steps") or [], known, self.cfg.max_steps,
                                                                  orchestration, vocab=vocab, stats=type_stats)
                            precedent_state = req.get("analysis_precedents") or {}
                            warnings = list(dict.fromkeys([
                                *(plan.get("warnings") or []), *step_warnings,
                                *([precedent_state["warning"]] if precedent_state.get("warning") else [])]))
                            problems = checklist_errors(plan, checklist_catalog, req.get("analysis_precedents"))
                            if not problems:
                                break
                            if attempt == 2:
                                warnings.append("checklist unanswered after correction: " +
                                                ", ".join(problem.split(" must ", 1)[0]
                                                          for problem in problems))
                                break
                            correction_suffix = ("\n\nThe previous PLAN failed validation:\n" +
                                                 "\n".join(f"- {problem}" for problem in problems) +
                                                 "\nReturn a complete corrected PLAN.")
                            plan_res = await make_plan(text + correction_suffix)
                            if not plan_res.ok:
                                self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False)
                                return
                            if rid in self.budget_denials:
                                self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False)
                                return
                            plan = _carry_assumptions(
                                plan, plan_res.structured if isinstance(plan_res.structured, dict)
                                else extract_json(plan_res.text) or {})
                            plan, plan_res = await resolve_plan_questions(plan, plan_res, correction_suffix)
                            if plan is None:
                                return
                        except (PlanOutputsError, PlanAgentError) as error:
                            if attempt == 2:
                                raise ValueError(f"plan invalid after correction: {error}") from error
                            # Before any step runs: one corrected plan, as the research lane does (#220).
                            correction_suffix = ("\n\nThe previous PLAN failed validation: " + str(error) +
                                                 "\nReturn a complete corrected PLAN.")
                            plan_res = await make_plan(text + correction_suffix)
                            if not plan_res.ok:
                                self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False)
                                return
                            if rid in self.budget_denials:
                                self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False)
                                return
                            plan = _carry_assumptions(
                                plan, plan_res.structured if isinstance(plan_res.structured, dict)
                                else extract_json(plan_res.text) or {})
                            plan, plan_res = await resolve_plan_questions(plan, plan_res, correction_suffix)
                            if plan is None:
                                return
                    req["plan"] = with_checklist_skip_warnings({**plan, "steps": steps, "warnings": warnings},
                                                               checklist_catalog, req.get("analysis_precedents"))
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                    # The verdict of the plan that runs, else the first one given (#36, #346 review)
                    scope = scope_verdict(plan) or req.get("scope_first")
                    if scope:
                        req["scope_check"] = scope
                decision, created = self._ensure_route_decision(req, roster, research=research_lane)
                self.hub.save_request(rid)
                if created:
                    await self._emit(rid, "request.route", decision)
                await self._emit(rid, "request.plan", {**req["plan"], "processing": decision})
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
                elif not await self._scope_gate(rid, req):
                    return
                elif await self._run_solo(rid, text, refs):
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
                trigger = "step_failure" if review is None else "review_revise"
                limit = (self.cfg.max_failure_replans if trigger == "step_failure"
                         else self.cfg.max_replans)
                # An approved research plan changes only through a new CP1 approval of its hash, never here.
                if limit <= 0 or research_lane:
                    return "disabled"
                progress = req.setdefault("replan_progress", {"attempts": 0, "max": limit, "in_flight": False})
                history = req.setdefault("replan_history", [])

                def record(status: str, attempt: int | None = None, **entry: Any) -> str:
                    history.append({"attempt": attempt, "trigger": trigger, "status": status, **entry})
                    progress["in_flight"] = False
                    progress.pop("trigger", None)
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
                used_attempts = max((int(entry.get("attempt") or 0) for entry in history
                                     if entry.get("trigger") == trigger), default=0)
                # A restart re-asks the attempt it interrupted; a cap lowered meanwhile still applies to it.
                same_in_flight = progress.get("in_flight") and progress.get("trigger", trigger) == trigger
                if same_in_flight:
                    used_attempts = max(used_attempts, int(progress.get("attempts") or 0))
                attempt = max(used_attempts, 1) if same_in_flight else used_attempts + 1
                if attempt > limit:
                    return record("limit", reason=f"re-plan limit reached ({used_attempts}/{limit})")
                progress.update(attempts=attempt, max=limit, in_flight=True, trigger=trigger)
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
                    topic_vocab = output_vocab.current()
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
                            topics_rule=(topic_types.prompt_rule(topic_vocab, include_definitions=False)
                                         if topic_vocab else ""),
                            empty_rule=empty_rule, question_rule=QUESTION_RULE, request=text,
                            plan=json.dumps(req.get("plan") or {"steps": steps}, ensure_ascii=False),
                            results=self.format_results(steps, results, n)) +
                        planning_guidance(checklist_catalog, req.get("analysis_precedents")),
                        # revision and parse_attempt keep each CSO call distinct for ledger recovery after a restart.
                        meta={**refs, "kind": "replan", "trigger": trigger, "revision": attempt,
                              "parse_attempt": parse_attempt, "request": text, "roster": roster,
                              "title": f"남은 DAG 재계획 #{attempt}", **({"workdir": workdir} if workdir else {}),
                              **checklist_meta(checklist_catalog)}))
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
                    candidate = _carry_assumptions(req.get("plan"), await ask_cso(1))
                    parse_attempt = 1
                    while True:
                        status, entry = await self._plan_clarification(rid, candidate)
                        if status in {"done", "open"}:
                            break
                        if status in {"limit", "unanswered"}:
                            req["clarification_failure"] = (
                                "확인 질문을 두 번 드렸지만 새 질문이 남았습니다."
                                if status == "limit" else "PI 확인 답변을 받지 못했습니다.")
                            reason = ("re-plan still needs PI clarification" if status == "limit" else
                                      "re-plan needs PI clarification that was denied or unanswered")
                            return record("failed", attempt, reason=reason)
                        text += answered_questions_prompt([entry])
                        parse_attempt += 1
                        candidate = _carry_assumptions(candidate, await ask_cso(parse_attempt))
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
                    bad_agents = unavailable_plan_agents(raw, known, orchestration)
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
                prior = {sid: {"ok": results[sid].ok,
                               "error": results[sid].error or results[sid].revision_failed,
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
                topic_vocab = output_vocab.current()
                topic_source = candidate if "topics" in candidate else (req.get("plan") or {})
                normalized_topics, topic_warnings = (plan_topics(topic_source.get("topics"), topic_vocab, strict=False)
                                                     if topic_vocab is not None else ([], []))
                req["plan"] = {**(req.get("plan") or {}),
                               **({"assumptions": normalize_assumptions(candidate.get("assumptions"))}
                                  if "assumptions" in candidate else {}),
                               **({"checklist": candidate.get("checklist")}
                                  if isinstance(candidate.get("checklist"), dict) else {}),
                               **({"suggested_next": candidate.get("suggested_next")}
                                  if isinstance(candidate.get("suggested_next"), list) else {}),
                               "steps": steps,
                               "topics": normalized_topics,
                               "warnings": [*((req.get("plan") or {}).get("warnings") or []), *warnings,
                                            *topic_warnings]}
                replan_checklist_problems = checklist_errors(req["plan"], checklist_catalog,
                                                              req.get("analysis_precedents"))
                if replan_checklist_problems:
                    req["plan"]["warnings"].append("checklist unanswered after re-plan: " +
                                                   ", ".join(problem.split(" must ", 1)[0]
                                                             for problem in replan_checklist_problems))
                req["plan"] = with_checklist_skip_warnings(req["plan"], checklist_catalog,
                                                           req.get("analysis_precedents"))
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

            def has_failures() -> bool:
                # A failed in-place revision keeps the step's last good result (revision_failed) and is not a
                # failure; only a step without a good result re-plans or ends the request (PR #378 review).
                return any(not result.ok for result in results.values())

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
            if rid in self.budget_denials or has_failures():
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
            if review.get("verdict") in {"accept", "revise"} and isinstance(review.get("issues"), list):
                # A review saved before generic priorities existed resumes with its missing priorities as P1.
                review = with_p1_verdict(review)
            reviewer = self.cfg.reviewer_agent
            start_rev = (self.cfg.max_revisions + 1 if progress.get("phase") in {"synthesis", "unresolved"}
                         else int(progress.get("next_revision") or 0))
            for rev in range(start_rev, self.cfg.max_revisions + 1):
                if not reviewer or reviewer not in known:
                    break
                prompt = REVIEW_PROMPT.format(request=text, results=self.format_results(steps, results, n))
                prompt += plan_review_context(req.get("plan"), checklist_catalog,
                                              req.get("analysis_precedents"))
                prompt += replan_history_note(req)  # retired steps are no longer in the results above (#271)
                review = {}
                for parse_attempt in (1, 2):
                    r = await self.run_step(Task(
                        agent_id=reviewer, request_id=rid, output_schema=REVIEW_SCHEMA,
                        prompt=prompt if parse_attempt == 1 else prompt +
                        '\n\nReturn ONLY a JSON object with verdict exactly "accept" or "revise", scores, and issues. '
                        'Every issue must include step_id, priority, problem, and request. Do not add prose.',
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
                        review = with_p1_verdict(parsed)
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
                p1_review = review_at_priority(review, "P1")
                if await attempt_replan(p1_review, progress) == "applied":
                    await self.run_dag(rid, text, steps, results, only={s["id"] for s in steps} - set(results))
                    await recover_failures()
                    if rid in self.budget_denials or has_failures():
                        self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                     review=review)
                        return
                    progress.update(phase="review", last_completed_revision=rev + 1)
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    continue
                # Off, declined or failed: the reviewer's notes go to the flagged steps in place, as before #271.
                feedback: dict[str, str] = {}
                for issue in issues_at_priority(review, "P1"):
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
                await recover_failures()
                if rid in self.budget_denials or has_failures():
                    self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                 review=review)
                    return
                progress.update(phase="review", last_completed_revision=rev + 1)
                self.hub.save_request(rid)

            # Still "revise" when revising stopped (F5): the CSO writes the report with the open issues in their own
            # section and the request stays failed. A failed or budget-denied synthesis ends with the step results.
            unresolved = review.get("verdict") == "revise"
            reference_issues = [*issues_at_priority(review, "P2"), *issues_at_priority(review, "P3")]
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
                                           review=short(review, 3000),
                                           warnings=general_report_warnings(steps, results, req.get("plan")) or "(none)",
                                           assumptions=_assumptions_prompt(req.get("plan"))) +
                       plan_report_context(req.get("plan"), checklist_catalog,
                                           req.get("analysis_precedents")) +
                       replan_history_note(req) +
                       (UNRESOLVED_REVIEW_NOTE if unresolved else "") +
                       (REVIEW_REFERENCE_NOTE if reference_issues else ""),
                meta={**refs, "kind": "synthesis", "request": text, "title": "최종 보고서 작성",
                      **({"workdir": workdir} if workdir else {})})
            if unresolved:
                try:
                    final = await self.run_step(synthesis)
                except BudgetExceeded as error:
                    final = TaskResult(task_id=synthesis.id, agent_id=synthesis.agent_id, ok=False, error=str(error))
                # The CSO sees the review clipped to 3,000 characters, so labhq appends every open issue itself:
                # the execution record always carries the full list, whatever synthesis left out (PR #338 review).
                open_issues = "\n".join(
                    f"- {issue.get('step_id')}: {issue.get('problem')} → {issue.get('request')}"
                    for issue in review.get("issues") or [])
                body = (final.text if final.ok else self.report_results(steps, results, n) +
                        f"\n\nSynthesis failed: {final.error}")
                report = _append_report_metadata(
                    body, ["Review: revisions unresolved. The reviewer's open issues, verbatim:\n" +
                           (open_issues or "- (no issue text)")])
                self._finish(rid, report,
                             serialized_results(), ok=False, review=review,
                             error="리뷰 지적이 수정 상한 뒤에도 남아 있습니다")
                return
            final = await self.run_step(synthesis)
            review_appendix = ("남은 P2·P3 지적 원문:\n" + "\n".join(
                f"- {issue.get('priority')} · {issue.get('step_id')}: {issue.get('problem')} → {issue.get('request')}"
                for issue in reference_issues)) if reference_issues else ""
            body = (final.text if final.ok else self.report_results(steps, results, n) +
                    f"\n\nSynthesis failed: {final.error}")
            reference = _review_reference(review)
            if reference and not REVIEW_REFERENCE_HEADING.search(body):
                body = body.rstrip() + "\n\n" + reference
            report = _append_report_metadata(body, [review_appendix]) if review_appendix else body
            self._finish(rid, report, serialized_results(),
                         ok=final.ok and rid not in self.budget_denials, review=review)
        except Exception as e:
            if self._continuation_unstarted(req):
                # Ends as the revise it continued, with that round's plan and results put back (``_finish``).
                self._finish(rid, f"{type(e).__name__}: {e}", {}, ok=False, error=f"{type(e).__name__}: {e}")
                return
            req.update(status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            execution = []
            saved: dict[str, TaskResult] = {}
            if req.get("plan", {}).get("steps"):
                saved = {k: TaskResult.model_validate(v) for k, v in (req.get("results") or {}).items()}
                execution.append(self.report_results(req["plan"]["steps"], saved,
                                                     self.cfg.context_chars_per_step))
            if req.get("pending_questions"):
                execution.append("Pending PI decisions/questions:\n" + "\n".join(
                    f"- {question}" for question in req["pending_questions"]))
            serialized_saved = {key: value.model_dump(mode="json") for key, value in saved.items()}
            summary, _feed_error = failed_request_summary(req, serialized_saved, req["error"])
            req["report"] = (summary + "\n\n## 결론과 권고\n요청을 완료하지 못했습니다. 실행 기록의 원인과 "
                             "다음 조치를 확인하세요.\n\n## 결과\n확정할 최종 결과가 없습니다.\n\n## 방법 요약\n"
                             "완료된 단계까지 실행했습니다.\n\n## 한계\n요청 처리 오류가 있습니다. 실행 기록 참고.")
            req["report_appendix"] = _appendix_sections("", [req["error"], *execution])
            # The preserved partial report goes with the event so a connected (or reconnecting) office shows it.
            failed = {"error": safe_failure_cause(req["error"]),
                      **_terminal_reports(rid, req.get("report") or "", req.get("report_appendix") or ""),
                      "cost_usd": float(req.get("cost_usd") or 0),
                      "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary")}
            if hasattr(self.hub, "schedule_terminal"):
                self.hub.schedule_terminal(rid, "request.failed", failed)
            elif hasattr(self.hub, "commit_terminal"):
                self.hub.commit_terminal(rid, "request.failed", failed)
            else:
                await self._emit(rid, "request.failed", failed)

    @staticmethod
    def _continuation_unstarted(req: dict) -> bool:
        """A continuation round is on and none of its steps has been dispatched (its reuse is not verified yet)."""
        contract = req.get("research_contract") or {}
        carried = contract.get("continuation") or {}
        return bool(carried and not carried.get("verified") and contract.get("rounds"))

    @staticmethod
    def _unstarted_continuation_end(req: dict, report: str, results: dict,
                                    review: dict | None) -> tuple[str, dict, dict | None]:
        """A continuation round that ends before its new CP1 let any step run (#90, PR #448 review).

        A rejected or timed-out CP1, a failed or invalid plan, or an error ends the request as the revise it
        continued: outcome ``research_review_revise``, the previous round's review on top, and its results kept,
        so the record and ``labhq verify`` still show what ran. The previous round's plan, hash, CP1 approval, pack
        snapshot and CP2 receipt come back with them (``restore_round``): the results never ran under the new draft,
        which stays in ``continuation.declined_plan`` (PR #448 review). Why the round ended is kept in
        ``continuation.ended_before_dispatch``."""
        if not Orchestrator._continuation_unstarted(req):
            return report, results, review
        contract = req["research_contract"]
        carried = contract["continuation"]
        previous = contract["rounds"][-1]
        stored = previous.get("review") or {}
        reason = (report.strip().splitlines() or ["-"])[0]
        draft = research_continuation.restore_round(req, previous)
        if draft:
            carried["declined_plan"] = draft
        # Results of a round that never dispatched are not step results: a failed plan task or a refused budget
        # card arrives as results={"plan": ...}. Keep it for diagnosis here, not beside the restored steps (PR #448).
        step_ids = {str(s.get("id")) for s in (req.get("plan") or {}).get("steps") or [] if isinstance(s, dict)}
        stray = {key: value for key, value in (results or {}).items() if key not in step_ids}
        results = {key: value for key, value in (results or {}).items() if key in step_ids}
        carried["ended_before_dispatch"] = {"outcome": req.get("outcome"), "reason": reason,
                                            **({"task_results": stray} if stray else {})}
        req["outcome"] = "research_review_revise"
        report = "\n".join([
            f"이어 가기 {carried.get('round')}차가 단계 실행 전에 끝났습니다: {reason}",
            f"{previous.get('round')}차 결과와 리뷰가 이 요청의 결과입니다.", "",
            f"Research review ({stored.get('reviewer') or '-'}): revise.",
            *_research_issue_lines(stored.get("issues") or []), "", report])
        return report, {**(previous.get("results") or {}), **results}, review or (stored or None)

    def _finish(self, rid: str, report: str, results: dict, ok: bool, review: dict | None = None,
                error: str | None = None) -> None:
        req = self.hub.requests[rid]
        report, results, review = self._unstarted_continuation_end(req, report, results, review)
        report, report_appendix = _split_report_appendix(report)
        if report.lstrip().startswith("### ") and "Full instructions, outputs and errors per step:" in report:
            report_appendix = _appendix_sections(report_appendix, [report])
            summary, feed_error = failed_request_summary(req, results, error or report)
            report = (summary + "\n\n## 결론과 권고\n요청을 완료하지 못했습니다. 실행 기록의 원인과 다음 조치를 "
                      "확인하세요.\n\n## 결과\n확정할 최종 결과가 없습니다.\n\n## 방법 요약\n"
                      "완료된 단계까지 실행했습니다.\n\n## 한계\n요청 처리 경고가 있습니다. 실행 기록 참고.")
            error = error or feed_error
        if not ok and req.get("clarification_failure") and req.get("pending_questions"):
            pending = "\n".join(f"- {question}" for question in req["pending_questions"])
            if "남은 질문:" not in report:
                report = f"{req['clarification_failure']}\n\n남은 질문:\n{pending}\n\n{report}"
            error = error or str(req["clarification_failure"])
        report = _with_review_reference(report, review)
        metadata = []
        route = req.get("route_decision") or {}
        if route.get("fallback"):
            metadata.append("처리 방식: 단독 실패 → 팀" +
                            (f"; 원인: {route['reason']}" if route.get("reason") else ""))
        if req.get("plan", {}).get("steps") and results:
            if not req.get("research_contract"):
                warnings = general_report_warnings(req["plan"]["steps"], results, req["plan"])
                if warnings:
                    metadata.append(warnings)
                    report = _execution_warning_summary(report)
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
        if req.get("replan_history"):  # only when a re-plan cap recorded an attempt (#271, #373)
            metadata.append("Re-plan history:\n" + "\n".join(replan_history_lines(req["replan_history"])))
        scope = req.get("scope_check") or {}
        if scope.get("verdict") in ("borderline", "out"):  # "in" adds nothing (#36)
            decision = f" PI decision: {scope['decision']}." if scope.get("decision") else ""
            metadata.append(f"Scope verdict: {scope['verdict']}; {scope.get('reason') or '-'}{decision}")
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
        report_appendix = _appendix_sections(report_appendix, metadata)
        req.update(status="done" if ok else "failed", report=report, report_appendix=report_appendix,
                   results=results, review=review,
                   cost_usd=self.cost.get(rid, 0.0), finished_at=time.time())
        data = {"ok": ok, **_terminal_reports(rid, report, report_appendix), "cost_usd": req["cost_usd"],
                "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary"),
                "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}
        if error:  # one readable office-feed line; `labhq send` prints report first, or error when report is absent
            req["error"] = data["error"] = error
        if hasattr(self.hub, "schedule_terminal"):
            self.hub.schedule_terminal(rid, "request.completed", data)
        elif hasattr(self.hub, "commit_terminal"):
            self.hub.commit_terminal(rid, "request.completed", data)
        else:  # Lightweight orchestration test doubles do not persist state.
            self.hub.save_request(rid)
            asyncio.get_running_loop().create_task(self._emit(rid, "request.completed", data))
