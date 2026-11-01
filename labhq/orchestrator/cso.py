"""Virtual-Biotech-style orchestration, adapted to a one-PI bioinformatics lab.

briefing (chief of staff) → CSO plan (JSON DAG + optional contract hires) → steps run in parallel
where dependencies allow (hibernate on HPC jobs, resume when they finish) → scientific reviewer
(3 criteria) → targeted revisions → CSO synthesis report.
"""

from __future__ import annotations
from ..request_persistence import create_revised_request, persist_request, remove_result, save_result

import asyncio
import copy
import hashlib
import inspect
import json
import math
import re
import time
from contextlib import asynccontextmanager
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path, PurePosixPath
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable, Literal

from .. import output_checks
from ..adapters import READ_ONLY_OVERRIDES, is_read_only_task, read_only_refusal
from ..ask_results import ask_result, read_ask_results, rejected_step
from ..artifact_policy import (OUTPUT_ENV_DIR, OUTPUT_REFERENCE_DIR, OUTPUT_SCRIPTS_DIR,
                               unreported_warning_paths)
from ..costs import cost_detail, format_cost, historical_plan_cost_estimate, task_cost_item
from ..interruption import ShutdownInterrupted, shutdown_interruption, stopping_marker
from ..model_refusal import REASON as MODEL_REFUSAL_REASON, ModelRefused, is_model_refusal
from ..pi_text import PI_TEXT_RULE
from ..evidence.claims import RESULT_CONTRACT_FIELD_RULES
from ..evidence.report_check import (FAILED_LOOKUP_TITLE, anchor, check_report, claim_rows, failed_lookup_lines,
                                     failed_lookups, claim_evidence_rows, claim_evidence_section)
from ..evidence.verify import LiveSourceResolver, verification_lines, verify_sources
from ..runner.lookup_admission import admission_for, usage_note_for
from ..runner.structured_output import GATEWAY_RETRY, GATEWAY_RETRY_META, repair_prompt
from ..facilities import signatures as env_signatures
from ..facilities import fixes as facility_fixes
from ..intake import (CLARIFYING_QUESTION_SCHEMA, QUESTION_RULE, has_structure,
                      question_detail_lines, questions_summary, reference_dirs, render_references,
                      unanswered_questions)
from ..models import (AskRequest, RunnerUnavailable, Task, TaskResult, ToolCallCount, hard_stop_kind, new_id,
                      waiting)
from ..quota import is_quota_error, received_quota_wait
from ..login import is_login_error
from ..report_format import (PAPER_RULE, ABSTRACT_PLAN_RULE, split_appendix, scientific_appendix, add_references)
from ..report_contract import (available_result_files, build_report_package, reader_markdown,
                               validate_report_package, with_report_front)
from ..request_status import failed_step_ids, is_terminal_request
from ..research.contract import (EVIDENCE_CHOICES, RESEARCH_PLAN_SCHEMA, RESEARCH_STEP_SCHEMA, ResearchPlan,
                                 bind_result_artifacts, branch_plan_topology,
                                 canonical_plan_json, classify_intake, freeze_plan, read_evidence_decision,
                                 normalize_result_plan_sha256,
                                 plan_sha256 as research_plan_sha256,
                                 refresh_plan_approval, research_plan_errors, research_plan_schema,
                                 research_result_errors, salvage_research_result, validate_research_plan,
                                 missing_result_slots, repair_result_slots, repair_synthesis_links,
                                 unnegated_analysis_text, validate_research_result, with_pack_refs)
from ..research import blind_draft
from ..research import precedent_texts
from ..research import continuation as research_continuation
from ..research import semantic_device_report
from ..research import input_fit
from ..research import semantic_devices
from ..security import redact_tokens
from ..recruit.provenance import (consult_refusal, external_contract_conflicts, external_hosts, not_yet_active,
                                  probation_refusal, restricted_input_steps, touches_restricted)
from ..research.packs import (assess_pack_applicability, configured_packs, normalize_pack_keys, pack_refs,
                              pack_snapshot, packs_for_snapshot, render_pack_catalog, render_pack_review,
                              select_applied_packs, select_legacy_applied_packs)
from ..util import INPUT_STEP_ID, clip, extract_json, input_relpath, output_relpath, short
from .. import vocab as output_vocab
from ..recruit import paper_skills, planning as recruit_planning
from ..bioinfo_policy import BIOINFO_ASSIGNMENT_RULE
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

# Explicit analyses and action verbs in the PI's request are a scope contract, not prose the CSO may generalize away.
REQUEST_ANALYSIS_TERMS: tuple[tuple[str, str, str], ...] = (
    ("DEG", r"(?i)(?:(?<![A-Za-z0-9])DE(?:Gs?)?(?![A-Za-z0-9])|차등\s*발현|differential(?:ly)?\s+(?:gene\s+)?expression)",
     r"(?i)(?:(?<![A-Za-z0-9])DE(?:Gs?)?(?![A-Za-z0-9])|차등\s*발현|differential(?:ly)?\s+(?:gene\s+)?expression)"),
    ("DMP/차등 메틸화", r"(?i)(?:(?<![A-Za-z0-9])DMPs?(?![A-Za-z0-9])|차등\s*메틸화|메틸화\s*분석|differential(?:ly)?\s+methylat)",
     r"(?i)(?:(?<![A-Za-z0-9])DMPs?(?![A-Za-z0-9])|차등\s*메틸화|메틸화\s*분석|differential(?:ly)?\s+methylat)"),
    ("경로 분석", r"(?i)(?:경로\s*(?:분석|농축)|pathway\s+(?:analysis|enrichment)|(?<![A-Za-z0-9])GSEA(?![A-Za-z0-9])|(?<![A-Za-z0-9])ORA(?![A-Za-z0-9]))",
     r"(?i)(?:경로\s*(?:분석|농축)|pathway\s+(?:analysis|enrichment)|(?<![A-Za-z0-9])GSEA(?![A-Za-z0-9])|(?<![A-Za-z0-9])ORA(?![A-Za-z0-9]))"),
    ("회귀", r"(?i)(?:회귀|regress(?:ion|ed|ing)?)", r"(?i)(?:회귀|regress(?:ion|ed|ing)?)"),
    ("메타분석", r"(?i)(?:메타\s*분석|meta[ -]?analys(?:is|es))",
     r"(?i)(?:메타\s*분석|meta[ -]?analys(?:is|es))"),
    ("생존분석", r"(?i)(?:생존\s*분석|survival\s+analysis|\bCox\b)",
     r"(?i)(?:생존\s*분석|survival\s+analysis|\bCox\b)"),
    ("GWAS", r"(?i)(?:(?<![A-Za-z0-9])GWAS(?![A-Za-z0-9])|genome[ -]wide\s+association)",
     r"(?i)(?:(?<![A-Za-z0-9])GWAS(?![A-Za-z0-9])|genome[ -]wide\s+association)"),
    ("분석", r"(?i)(?:분석|analy[sz](?:e|ed|ing|is))", r"(?i)(?:분석|analy[sz](?:e|ed|ing|is))"),
    ("비교", r"(?i)(?:비교|compar(?:e|ed|ing|ison))", r"(?i)(?:비교|compar(?:e|ed|ing|ison))"),
    ("검정", r"(?i)(?:검정|\btest(?:ed|ing)?\b)", r"(?i)(?:검정|\btest(?:ed|ing)?\b)"),
    ("추정", r"(?i)(?:추정|estimat(?:e|ed|ing|ion))", r"(?i)(?:추정|estimat(?:e|ed|ing|ion))"),
    ("예측", r"(?i)(?:예측|predict(?:ed|ing|ion)?)", r"(?i)(?:예측|predict(?:ed|ing|ion)?)"),
    ("분류", r"(?i)(?:분류|classif(?:y|ied|ication))", r"(?i)(?:분류|classif(?:y|ied|ication))"),
    ("군집", r"(?i)(?:군집|cluster(?:ed|ing)?)", r"(?i)(?:군집|cluster(?:ed|ing)?)"),
    ("연관", r"(?i)(?:연관|associat(?:e|ed|ing|ion))", r"(?i)(?:연관|associat(?:e|ed|ing|ion))"),
    ("상관", r"(?i)(?:상관|correlat(?:e|ed|ing|ion))", r"(?i)(?:상관|correlat(?:e|ed|ing|ion))"),
    ("검증", r"(?i)(?:검증|validat(?:e|ed|ing|ion))", r"(?i)(?:검증|validat(?:e|ed|ing|ion))"),
    ("평가", r"(?i)(?:평가|evaluat(?:e|ed|ing|ion))", r"(?i)(?:평가|evaluat(?:e|ed|ing|ion))"),
)
SCOPE_COVERAGE_WARNING = "요청 범위 누락: 계획의 범위·단계에서 찾지 못한 분석 항목 — "
REQUEST_SCOPE_RULE = (
    "Copy every analysis name and analysis action explicitly written in the PI's request into the scope and at least "
    "one planned step. Keep names such as DEG, DMP/differential methylation, pathway analysis, regression, "
    "meta-analysis, survival analysis and GWAS verbatim or with an unambiguous equivalent; never reduce a requested "
    "analysis to data discovery."
)

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
                           "optional_outputs": {"type": "array", "items": {"type": "string"}},
                           "key_figures": {"type": "array", "minItems": 1, "maxItems": 2,
                                           "items": {"type": "string"}},
                           "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "agent_id", "instruction", "depends_on"]}},
        "recruit": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"paper": {"type": "string"}, "repo": {"type": "string"},
                           "focus": {"type": "string"}, "reason": {"type": "string"},
                           "step_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            "required": ["paper", "repo", "focus", "reason", "step_ids"]}},
        "paper_skills": paper_skills.SELECTION_SCHEMA,
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
    solo = bool(not research and requested != "team" and planned == "solo" and solo_agent in roster_ids
                and not (plan or {}).get("paper_skill_results") and not (plan or {}).get("recruit_results"))
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
    schema["properties"].pop("paper_skills")  # new paper selections belong before the first plan/CP1
    schema["properties"]["drop"] = {"type": "array", "items": {"type": "string"}}
    schema["required"] = [*(key for key in schema["required"] if key != "scope"), "drop"]
    return schema


def scope_verdict(plan: Any) -> dict[str, str] | None:
    """The CSO's scope verdict from a general plan (#36), or None when it is missing or malformed."""
    scope = plan.get("scope") if isinstance(plan, dict) else None
    if not isinstance(scope, dict) or scope.get("verdict") not in SCOPE_VERDICTS:
        return None
    return {"verdict": scope["verdict"], "reason": short(str(scope.get("reason") or "").strip(), 500)}


def missing_request_analysis_terms(request: str, plan: Any) -> list[str]:
    """Requested analysis names/actions absent from the plan's scope and executable description (#707)."""
    request = unnegated_analysis_text(request or "")
    if not isinstance(plan, dict):
        return [label for label, request_pattern, _ in REQUEST_ANALYSIS_TERMS
                if re.search(request_pattern, request)]
    brief = plan.get("brief") if isinstance(plan.get("brief"), dict) else {}

    def strings(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from strings(child)

    scope_text = "\n".join(strings(brief.get("scope")))
    instruction_text = "\n".join(
        strings([step.get("instruction") for step in plan.get("steps") or [] if isinstance(step, dict)]))
    requested = [(label, request_pattern, plan_pattern) for label, request_pattern, plan_pattern in REQUEST_ANALYSIS_TERMS
                 if re.search(request_pattern, request)]
    scope_matches = {label: bool(re.search(plan_pattern, scope_text))
                     for label, _request_pattern, plan_pattern in requested}
    instruction_matches = {label: bool(re.search(plan_pattern, instruction_text))
                           for label, _request_pattern, plan_pattern in requested}
    # A named analysis carried with an unambiguous equivalent also carries the generic request verb "analysis",
    # but it must do so independently in the scope and in an executable instruction.
    for matches in (scope_matches, instruction_matches):
        if matches.get("분석") is False and any(matches.get(label) for label, _, _ in requested[:7]):
            matches["분석"] = True
    return [label for label, _request_pattern, _plan_pattern in requested
            if not scope_matches[label] or not instruction_matches[label]]


def with_scope_coverage_warning(plan: Any, request: str) -> Any:
    """Replace the deterministic scope warning with the one for this plan, without rejecting CP1."""
    if not isinstance(plan, dict):
        return plan
    missing = missing_request_analysis_terms(request, plan)
    previous = plan.get("warnings") if isinstance(plan.get("warnings"), list) else []
    kept = [warning for warning in previous
            if not (isinstance(warning, str) and warning.startswith(SCOPE_COVERAGE_WARNING))]
    if missing:
        kept.append(SCOPE_COVERAGE_WARNING + ", ".join(missing))
    return {**plan, "warnings": kept}

# The reviewer's answer to each declared checklist item and required precedent item (#586). Optional in both review
# schemas: a review saved before it existed, or one from a request with no checklist, stays valid.
CHECKLIST_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"id": {"type": "string"},
                       "status": {"type": "string", "enum": list(topic_checklists.REVIEW_STATUSES)},
                       "evidence": {"type": "string"}},
        "required": ["id", "status", "evidence"]}}

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
        "checklist": CHECKLIST_REVIEW_SCHEMA,
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
        "checklist": CHECKLIST_REVIEW_SCHEMA,
        "blind_comparison": blind_draft.COMPARISON_SCHEMA,
    },
    "required": ["verdict", "issues"],
}

BRIEFING_PROMPT = """Prepare a briefing (≤400 words) for the CSO on this research request:
field context, recent developments (search the web if available), which datasets exist and how they can be
accessed (public vs controlled access, DUA constraints), and feasibility risks.
Include a short scope line that follows this rule: """ + REQUEST_SCOPE_RULE + """

Current runner capabilities (sanitized summary):
{capabilities}

Use only this summary for compute feasibility. Separate reported scheduler, tool, and environment facts from
unknowns. Treat scheduler=none, labhq_hpc=no, and tool=no as unavailable; treat unknown or a missing capability
report as unknown, never as available. Do not add a numeric feasibility score or grade.

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
                           "repo": {"type": "string"}, "data_url": {"type": "string"},
                           "fulltext_receipt": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "coverage_version": {"type": "integer", "minimum": 2, "maximum": 2},
                                   "paper_id": {"type": "string"}, "source": {"type": "string"},
                                   "access_level": {"type": "string"}, "extraction": {"type": "string"},
                                   "text_file": {"type": "string"}, "text_sha256": {"type": "string"},
                                   "supplements_required": {"type": ["boolean", "null"]},
                                   "supplements": {"type": "string"},
                                   "access_status": {"type": "string", "enum": ["full_text", "abstract_only",
                                       "not_obtained", "paywalled", "undetermined"]},
                                   "methods_locator": {"type": ["string", "null"]},
                                   "qc_locator": {"type": ["string", "null"]},
                                   "supplementary_status": {"type": "string", "enum": ["available", "missing",
                                       "not_applicable", "undetermined"]},
                                   "pdf_file": {"type": "string"}, "pdf_sha256": {"type": "string"},
                                   "unread_reason": {"type": "string"}, "unread_detail": {"type": "string"},
                                   "oa_copy": {"type": "object", "additionalProperties": False,
                                       "properties": {key: {"type": "string"} for key in (
                                           "provider", "url", "source", "license", "version", "format", "content_sha256")}},
                                   "evidence": {"type": "array", "items": {
                                       "type": "object", "additionalProperties": False,
                                       "properties": {"kind": {"type": "string", "enum": ["methods", "qc"]},
                                                      "start_line": {"type": "integer", "minimum": 1},
                                                      "end_line": {"type": "integer", "minimum": 1}},
                                       "required": ["kind", "start_line", "end_line"]}}},
                               "required": ["coverage_version", "paper_id", "source", "access_level", "extraction", "text_file",
                                            "text_sha256", "evidence", "supplements_required", "supplements"]},
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
For each paper include its DOI/PMID when available and optional repo/data_url links when verified, so the CSO can
prefer a method with accessible code or data for a request-scoped Paper2Skill.
When a stored fulltext tool receipt exists, copy it as fulltext_receipt and locate the actual methods/QC evidence
with 1-based inclusive line ranges in that exact text_file. Preserve all original receipt fields;
only add evidence line ranges and the supplements_required dependency decision. Never relabel access or extraction.
Set supplements_required only when those claims depend
on supplements, and mark their availability honestly. A receipt establishes artifact access, not comprehension.
Without readable evidence, leave methods/QC and parameters unverified and request material; never infer them from
an access_level string, abstract, title, or memory. Omit fulltext_receipt when no stored artifact exists.

Request: {request}"""

# Plan and re-plan questions of the general lane go to the PI's phone card. Nothing validates their length, so
# both prompts carry the same rule.
PI_CARD_QUESTION_RULE = "Each question must fit the PI's phone card: at most 700 characters, the question itself first." + PI_TEXT_RULE
# One environment per request (2nd mock trial 2026-10-03): steps that each built a venv duplicated installs, and
# a later step could not add a package to another step's environment.
ENV_LOCK_OUTPUT = OUTPUT_ENV_DIR + "requirements.lock.txt"
ENV_MANIFEST_OUTPUT = OUTPUT_ENV_DIR + "runner-manifest.json"


def scheduler_clone_extra(steps: Any) -> int:
    """Steps the scheduler added by splitting one environment step into a clone per consuming runner (#674).

    They are not the planner's steps, so they never count toward ``max_steps``: a plan that fills the limit with its
    own steps still gets its runner-local environments. Bounded by the number of runners."""
    if not isinstance(steps, list):
        return 0
    clones: dict[str, int] = {}
    for step in steps:
        binding = step.get("environment_binding") if isinstance(step, dict) else getattr(step, "environment_binding", None)
        logical = binding.get("logical_step_id") if isinstance(binding, dict) else getattr(binding, "logical_step_id", None)
        if isinstance(logical, str):
            clones[logical] = clones.get(logical, 0) + 1
    return sum(count - 1 for count in clones.values())


def split_runner_environments(steps: list[dict], agent_runner: dict[str, str],
                              runner_runtime: dict[str, str], can_create: Callable[[str], bool]) -> list[dict]:
    """Prepare runner-local environment steps; never mutate the source plan.

    This helper requires already validated dependencies and explicit capability evidence.
    Its caller must persist the returned bindings and pin every bound dispatch.
    """
    planned = copy.deepcopy(steps)
    used = {s["id"] for s in planned}
    envs = [s for s in planned if ENV_LOCK_OUTPUT in s.get("outputs", [])]
    if len(envs) > 1:
        raise ValueError("multiple logical environment steps need an explicit replan")
    for env in envs:
        logical = env["id"]
        if not INPUT_STEP_ID.fullmatch(logical):
            raise ValueError(f"environment {logical}: invalid step ID")
        consumers = [s for s in planned if s is not env and _reaches(planned, s["id"], logical)]
        if not consumers:
            raise ValueError(f"environment {logical}: no direct consumers")
        groups: dict[str, list[dict]] = {}
        for step in consumers:
            runner = agent_runner.get(step["agent_id"])
            if (not isinstance(runner, str) or not 0 < len(runner) <= 256
                    or runner_runtime.get(runner) not in {"windows", "linux", "wsl"}):
                raise ValueError(f"step {step['id']}: runner or runtime unknown")
            groups.setdefault(runner, []).append(step)
        clones = []
        for index, (runner, members) in enumerate(groups.items()):
            candidates = [env["agent_id"], *(s["agent_id"] for s in members)]
            eligible = next((agent for agent in candidates
                             if agent_runner.get(agent) == runner and can_create(agent)), None)
            if eligible is None:
                raise ValueError(f"environment {logical}: no authorized creator on runner {runner}")
            clone_id = logical if index == 0 else f"env_{hashlib.sha256((logical + chr(0) + runner).encode()).hexdigest()[:24]}"
            if index and clone_id in used:
                raise ValueError(f"environment clone ID collision: {clone_id}")
            used.add(clone_id)
            runtime = runner_runtime[runner]
            python_path = ".venv/Scripts/python.exe" if runtime == "windows" else ".venv/bin/python"
            binding = {"version": 1, "logical_step_id": logical, "step_id": clone_id,
                       "runner_id": runner, "runtime": runtime, "venv_path": ".venv", "python_path": python_path}
            clone = copy.deepcopy(env)
            clone.update(id=clone_id, agent_id=eligible, environment_binding=binding)
            clone["outputs"] = list(dict.fromkeys([*clone["outputs"], ENV_MANIFEST_OUTPUT]))
            clone["instruction"] += ("\nCreate the runner-local environment and write this exact JSON object to "
                                     f"{ENV_MANIFEST_OUTPUT}: {json.dumps(binding, ensure_ascii=True)}")
            clones.append(clone)
            for step in members:
                step["depends_on"] = [clone_id if d == logical else d for d in step["depends_on"]]
                if clone_id not in step["depends_on"]:
                    step["depends_on"].append(clone_id)
                step.setdefault("environment_bindings", []).append(copy.deepcopy(binding))
        position = planned.index(env)
        planned[position:position + 1] = clones
    return planned


def environment_runner_pin(step: dict) -> str | None:
    """Fail closed on malformed or conflicting persisted environment bindings."""
    bindings = step.get("environment_bindings", [])
    if "environment_binding" in step:
        bindings = [*bindings, step["environment_binding"]] if isinstance(bindings, list) else None
    if not isinstance(bindings, list):
        raise ValueError("invalid environment bindings")
    runners = set()
    for item in bindings:
        if not isinstance(item, dict):
            raise ValueError("invalid environment binding")
        runtime = item.get("runtime")
        runner = item.get("runner_id")
        expected_python = ".venv/Scripts/python.exe" if runtime == "windows" else ".venv/bin/python"
        if (type(item.get("version")) is not int or item["version"] != 1
                or not isinstance(runtime, str) or runtime not in {"windows", "linux", "wsl"}
                or any(not isinstance(item.get(key), str) or not INPUT_STEP_ID.fullmatch(item[key])
                       for key in ("logical_step_id", "step_id"))
                or not isinstance(runner, str) or not 0 < len(runner) <= 256
                or item.get("venv_path") != ".venv" or item.get("python_path") != expected_python):
            raise ValueError("invalid environment binding")
        runners.add(runner)
    if len(runners) > 1:
        raise ValueError("conflicting environment runners")
    return next(iter(runners), None)


def validate_environment_bindings(steps: list[dict]) -> None:
    """Check saved assignments without consulting today's possibly changed roster."""
    by_id = {s["id"]: s for s in steps}
    for step in steps:
        environment_runner_pin(step)
        binding = step.get("environment_binding")
        if binding is not None:
            if (binding["step_id"] != step["id"] or ENV_LOCK_OUTPUT not in step.get("outputs", [])
                    or ENV_MANIFEST_OUTPUT not in step.get("outputs", [])):
                raise ValueError(f"step {step['id']}: invalid environment producer")
        for item in step.get("environment_bindings", []):
            producer = by_id.get(item["step_id"])
            if (not producer or producer.get("environment_binding") != item
                    or item["step_id"] not in step.get("depends_on", [])):
                raise ValueError(f"step {step['id']}: environment producer mismatch")


ENV_STEP_RULE = ("If the work needs packages the runner does not have, plan one environment step first: it creates a "
                 "virtual environment in its own workspace, installs only packages the PI approved with "
                 "`.venv/bin/python -m pip install --only-binary=:all:` (Windows: "
                 "`.venv/Scripts/python.exe -m pip install --only-binary=:all:`), and saves "
                 f"{OUTPUT_ENV_DIR}requirements.lock.txt with the interpreter's path. Later steps depend on it and run "
                 "that interpreter by path instead of building their own. For every package that may need compilation, "
                 "put an alternative in the plan. If wheel installation or a build fails, do not ask the PI for build "
                 f"tools: use the alternative and record the change under {OUTPUT_ENV_DIR}. Ask only when no alternative exists.")

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


def _hub_has(hub: Any, name: str) -> bool:
    """A real Hub method, not an attribute a test double or mock happens to answer."""
    return inspect.isfunction(getattr(type(hub), name, None))


def hub_agents(hub: Any, request_id: str | None) -> dict[str, dict]:
    """The staff of this request (a request pinned to WSL sees the WSL runner's entry for bioinfo-agent, #1204)."""
    return hub.request_agents(request_id) if _hub_has(hub, "request_agents") else hub.agents


def hub_runner(hub: Any, agent_id: str, request_id: str | None) -> str | None:
    """The runner this request uses for ``agent_id``."""
    return hub.request_runner(agent_id, request_id) if _hub_has(hub, "request_runner") else hub.agent_runner.get(agent_id)


def format_capabilities(roster: list[dict], runner_capabilities: dict | None = None,
                        run_where: str | None = None, run_where_note: str | None = None) -> str:
    lines = [
        f"- {a['id']}: scheduler={a.get('scheduler', 'none')}, "
        f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}, "
        f"other compute={', '.join(a.get('compute_backends') or ['local CLI'])}"
        for a in roster
    ]
    capabilities = runner_capabilities if isinstance(runner_capabilities, dict) else {}
    if lines and not capabilities:
        lines.append("- runner capability report: unknown (no current runner capability report; do not infer resources)")
    for runner_id in sorted({a.get("runner_id") for a in roster if a.get("runner_id")}):
        runner = capabilities.get(runner_id) if isinstance(capabilities.get(runner_id), dict) else {}
        lines.append(f"- runner {runner_id} local software: {_format_local_software(runner.get('local_software'))}")
        if runner.get("runtime_env") == "wsl":
            lines.append(f"- runner {runner_id}: 로컬 WSL. bioinfo-agent 분석은 이 runner에서 실행한다. "
                         "위 tools의 nextflow/docker가 yes이면 로컬 Linux 파이프라인을 계획에 넣는다.")
    if run_where != "wsl" and run_where_note:  # the PI chose WSL and did not get it: plan for what will run
        lines.append("- PI가 WSL에서 실행하라고 골랐지만 WSL을 쓸 수 없어 WSL 없이 실행한다: "
                     f"{' '.join(str(run_where_note).split())[:300]}")
    if run_where == "wsl":
        lines.append("- PI가 이 PC의 WSL에서 실행하라고 골랐다. bioinfo-agent가 roster에 있으면 분석 단계에 쓰고, "
                     "없으면 그 이유를 계획에 적는다.")
    return "\n".join(lines)

DECLARED_OUTPUTS_FALLBACK_RULE = (
    "Every permitted execution path writes the step's declared output to the same filename. If paths change the "
    "content, scale, or source, declare one route-independent filename (for example "
    "outputs/data/expression_matrix.tsv.gz) and record the chosen path, scale, and source in the same step's record "
    "(for example outputs/data/fetch_log.md or the file header). Do not declare different filenames per path.")

OPTIONAL_OUTPUTS_PLAN_RULE = (
    "Keep completion-critical deliverables in outputs (the default and required list). Put only dispensable logs, "
    "conversion records and previews in optional_outputs; missing optional outputs warn but do not fail the step.")

DETERMINISTIC_TRANSFORM_RULE = (
    "For literal text replacement or format conversion, tell the employee to write and run one Python script in "
    "its own work folder. Do not iterate with sed or shell escaping.")

FIGURE_OUTPUT_RULE = (
    "Figures default to SVG and keep Korean labels as SVG <text>. With matplotlib set "
    "rcParams[\"svg.fonttype\"] = \"none\" and font.family to ['Malgun Gothic', 'Apple SD Gothic Neo', "
    "'Noto Sans KR', 'sans-serif']. Do not use rasterized=True even for dense scatter plots, do not request PNG "
    "as a deliverable, and never make a figure from a screen or browser capture (preview-only capture is allowed).")

ANALYSIS_FIGURE_PLAN_RULE = (
    "Every analysis step (differential analysis, regression or trend, meta-analysis, dimensionality reduction, "
    "pathway analysis, or QC summary) declares 1–2 key figures in outputs and repeats their exact paths in "
    "key_figures. Standard figures: differential "
    "analysis -> volcano and PCA; regression or trend -> trend with interval; meta-analysis -> forest and funnel; "
    "dimensionality reduction -> PCA; pathway analysis -> GSEA NES bar chart; QC summary -> QC summary chart. "
    + FIGURE_OUTPUT_RULE)

ANALYSIS_REPRODUCIBILITY_PLAN_RULE = (
    f"Every analysis step declares the scripts it runs under {OUTPUT_SCRIPTS_DIR} (for example "
    f"{OUTPUT_SCRIPTS_DIR}analyze.py), as well as result-determining intermediate artifacts under "
    f"{OUTPUT_REFERENCE_DIR}. " + ANALYSIS_FIGURE_PLAN_RULE + " " + ABSTRACT_PLAN_RULE)

# A script a step runs but does not declare is not published and cannot be re-run from the edition (#792): the
# result repository of the sixth run listed commands for two scripts it did not contain.
SCRIPT_OUTPUTS_PLAN_RULE = (
    f"Any step that runs a script, whatever its kind (a QC re-computation, a table or confidence-interval check, a "
    f"literature extraction), declares that script under {OUTPUT_SCRIPTS_DIR}. A script that is not a declared output "
    f"is left out of the published edition, so nobody can re-run that step from it.")

ROUTE_PLAN_RULE = ("Set route to solo only when one employee can finish the whole request in one turn of about "
                   "30 minutes or less: a lookup, a table, a single QC check or figure, a literature list. Use team "
                   "when the work needs analysis design choices that benefit from independent review, several "
                   "dependent stages, HPC, or controlled data. When in doubt, use team. Plan the steps either way: "
                   "they are the fallback.")

SOLO_PROMPT = PAPER_RULE + """Complete this request by yourself in one turn.

Original request:
{request}

Planning assumptions:
{assumptions}

Rules:
- Write a short paper report, about 2,000 Korean characters or less. Create and declare outputs/graphical_abstract.svg with code and link it as the first figure.
- """ + FIGURE_OUTPUT_RULE + """
- Save every deliverable under outputs/ using relative paths.
- Save analysis code under """ + OUTPUT_SCRIPTS_DIR + """ and use only relative paths inside it.
- """ + SCRIPT_OUTPUTS_PLAN_RULE + """
- In the limitations, give one line for each thing you could not do and each assumption you had to make.
- Follow every supplied topic or precedent checklist item. Add a short cited "선행 연구 기준" section when
  Analysis precedents are supplied, and put omitted recommendations under "다음에 할 수 있는 분석".
"""


PLAN_PROMPT = BIOINFO_ASSIGNMENT_RULE + "\n\n" + """Decompose the PI's request into steps for your team. You do not analyze anything yourself.

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
- """ + OPTIONAL_OUTPUTS_PLAN_RULE + """
- """ + DETERMINISTIC_TRANSFORM_RULE + """
- """ + ANALYSIS_REPRODUCIBILITY_PLAN_RULE + """
- """ + SCRIPT_OUTPUTS_PLAN_RULE + """
- Use HPC jobs only when the assigned agent has labhq_hpc tools and a scheduler is available.
  Local CLI is available for light work. If a step needs unavailable compute, ask the PI in
  clarifying_questions before planning execution. Put a QC step after any data generation.
- """ + ENV_STEP_RULE + """
- If no roster member covers a required method, add a contract hire to `recruit` (paper + code repo +
  focus) and plan the step for whoever is closest; the PI decides whether to hire.
- """ + recruit_planning.PLAN_RULE + """
- """ + paper_skills.PLAN_RULE + """
- {question_rule} """ + PI_CARD_QUESTION_RULE + """
- """ + ASSUMPTIONS_RULE + """
- The original study's methods are reference, not a template: the request may ask a different question of the same
  data. Reuse a choice when it fits this request's question; do not copy the original design otherwise. Record in
  `assumptions` only a different choice that could change the result.
- """ + ROUTE_PLAN_RULE + """
- """ + REQUEST_SCOPE_RULE + """
- Judge the request against the lab's scope ({lab_scope}) in `scope`: verdict "in" when it fits, "borderline"
  when it is adjacent work the lab can still do, "out" when it is outside the lab's field; reason is one sentence.
  Plan the steps whatever the verdict (for "out" the PI decides whether they run), and do not ask about scope in
  clarifying_questions.

PI's request: {request}"""

SELECTION_RECORD_RULE = (
    "`selection_record` (optional object, #890): write it now, while you choose the design, so it is frozen with the "
    "plan at CP1: `question` as the PI asked it, `premises`, `success_criteria`, `alternatives` (each `option` you "
    "actually weighed with `rejected_because`) and `change_signals` (observations that would change your judgment). "
    "Record only alternatives you really considered; leave the field out when you weighed none rather than "
    "inventing some.")
# A step's result cannot add a claim the plan did not declare (#1066): ask the CSO to declare each one up front.
CLAIM_IDS_PLAN_RULE = ("A step's result can state only the claims in that step's claim_ids; it cannot add one. When "
                       "the request asks for hypotheses, mechanisms or a grade-by-grade summary as findings, declare a "
                       "claim_id for each of them in the step that produces it.")

RESEARCH_PLAN_PROMPT = BIOINFO_ASSIGNMENT_RULE + "\n\n" + recruit_planning.PLAN_RULE + "\n\n" + paper_skills.PLAN_RULE + "\n\n" + """Create a frozen research contract for the PI's request. Do not execute or analyze.

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
- Return schema_version 2. A plan without branches has at most {max_steps} steps, exactly as before.
  When the request explicitly lists independent evidence types, or naturally splits into independent questions that
  use different evidence types, add `branches` with id, question, evidence_type, data_boundary and optional
  estimated_budget_usd. Assign every step to one branch with `branch`, and assign the final integration steps to
  `branch: "synthesis"`. Do not create branches for a request with one method or one evidence stream.
  A literature branch or step reads the full text of every paper its conclusions use (labhq_annot.fulltext) and
  records each paper's access_level; set a data_boundary of abstracts only when the PI asked for it (#697).
  A branched plan has at most {max_branches} branches, {max_branch_steps} steps per branch, and 3 synthesis steps;
  its total limit is actual branch count × {max_branch_steps} + 3 (configured maximum {branched_max_steps}).
  Never silently drop a step to fit any limit: return a complete smaller
  plan. The synthesis reads every branch result and still runs when at least one branch finishes. If a branch needs
  a PI-provided extract or an internal-data folder, ask for it in clarifying_questions before planning that work.
- The brief states question, purpose, subject, scope, deliverables, and observable completion conditions.
- """ + REQUEST_SCOPE_RULE + """
- Explanatory/comparative work states a primary hypothesis, alternatives, and distinguishing observations.
  Exploratory/technical work uses its purpose and does not invent H0/H1.
- Freeze analysis unit, selection/exclusion, comparators, metrics, validation, resources, stop/approval
  conditions, data boundaries, and statistics applicability before execution. Every not_applicable item needs a reason.
  Applicable statistics needs estimand, analysis_unit, and primary_outcomes.
- Installing packages inside a step's own workspace (`pip install --target ./.pylib`, a workspace venv, `./.rlib`,
  versions pinned) is allowed by default. Do not write "no new packages" or the like into protocol.resource_limits:
  a step that cannot read a received file for lack of a package would give up. Forbid only changes to the system or a
  shared environment; stricter limits come only from the PI's request.
- """ + SELECTION_RECORD_RULE + """
- `budget_usd` (optional number): the additional execution budget in USD after this plan's CP1 approval.
  Exclude briefing, clarification, planning and all earlier-round spending already incurred. On approval labhq adds
  that spending to this budget and clamps the total to the lab's policy cap; when the PI wrote a budget on the request,
  that budget is the cap and this amount is only the estimate on the CP1 card. Exceeding the cap waits for a PI
  budget card. Write this additional amount in protocol.resource_limits. Leave it out when you cannot estimate it.
- Each step declares phase, claim_ids, input_refs, outputs, checks, evidence_slots, and depends_on. Every output is
  inside that step's own workspace outputs/ folder, written as outputs/<name>, and the instruction uses that exact
  path. Never declare an absolute path, home path, `..`, or a file at the workspace root.{output_types_rule}{topics_rule}
- """ + CLAIM_IDS_PLAN_RULE + """
- """ + DECLARED_OUTPUTS_FALLBACK_RULE + """
- """ + OPTIONAL_OUTPUTS_PLAN_RULE + """
- """ + DETERMINISTIC_TRANSFORM_RULE + """
- """ + ANALYSIS_REPRODUCIBILITY_PLAN_RULE + """
- """ + SCRIPT_OUTPUTS_PLAN_RULE + """
- Put QC after data generation. {question_rule} Record the scientific design choices you make in `protocol` rather
  than adding another top-level field. Each question is at most 500 characters (a longer one fails plan
  validation), the question itself first.
- The original study's methods are reference, not a template: the request may ask a different question of the same
  data. Reuse a choice when it fits this request's question; do not copy the original design otherwise. Record in
  `protocol` only a different choice that could change the result and its evidence.
- When a hypothesis identifies an exposed or focal group, make the primary contrast that group versus all others,
  or versus the least-exposed group. Record one line in the protocol explaining the reference category. Use the
  largest category only when category size, rather than the hypothesis, determines the comparison.
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
    "and evidence kept apart. An artifact_refs path must be one of that step's declared outputs (a file inside a declared folder, "
    "not the folder) or an upstream "
    "step's collected artifact; evidence citing any other path is refused. The request then stops at CP2, where "
    "the PI approves, requests revision of, or denies the evidence. After approval one reviewer checks the claims "
    "against their ledgers and files, and the final report ties every conclusion to a ledger claim as "
    "[[claim:<step_id>/<claim_id>]], which labhq checks. In a branched plan, a failed branch blocks only later "
    "steps in that branch; synthesis still runs from completed branches and names every failed branch, its reason "
    "and last completed step. A requested revision or a reviewer's revise ends the request, and a changed plan "
    "needs a new CP1 approval.")
RESEARCH_CP2_PLAN_PROMPT = RESEARCH_PLAN_PROMPT.replace(RESEARCH_CP1_ONLY_RULE, RESEARCH_CP2_RULE)
# CP2 cards an answer without a readable choice gets before the request ends as not approved.
CP2_MAX_ASKS = 3
# One readable reason per research ending that is not a success. The office feed prints it after "요청이 실패했어요: ",
# so a request that ends with a report (report_incomplete) or at a PI card (CP2) no longer shows an empty reason.
RESEARCH_END_ERRORS = {
    "report_incomplete": "보고서는 나왔지만 claim 검사에서 문제가 있어요(실행 기록 참고)",
    "research_reported": "예산 승인을 받지 못해 보고서를 완료로 보지 않았어요",
    "research_review_revise": "리뷰가 수정을 요구했고 이어 가지 않아 요청이 끝났어요(실행 기록 참고)",
    "research_review_unparsed": "연구 리뷰 결과를 읽지 못해 요청이 끝났어요(실행 기록 참고)",
    "research_failed": "연구가 끝까지 가지 못했어요(실행 기록 참고)",
    "evidence_rejected": "CP2에서 근거를 거절해 요청이 끝났어요",
    "evidence_revision_requested": "CP2에서 수정을 요청했지만 새 계획으로 이어지지 않아 요청이 끝났어요",
    "evidence_timed_out": "CP2 카드에 기한 안에 답이 없어 근거를 승인하지 않고 끝냈어요",
    "evidence_unreadable": "CP2 카드의 선택을 읽지 못해 근거를 승인하지 않고 끝냈어요",
}

RESEARCH_STEP_PROMPT = """Overall request (context only): {request}

Your step ({step_id}): {instruction}

Teammates' upstream results are in the context section. If a received file needs a package you lack, install it
into your own workspace (./.pylib, pinned version) and read it; never give a step up for that. If no tool can read
a received file even then, record an evidence row with status "unavailable" whose status_detail starts with
"도구 없음: <file> · <tool needed>" so the PI sees it at CP2. Deliver: what you did, key results with file
paths, caveats and open questions. Do not quietly switch to a weaker method when one fails: keep debugging,
and if you give it up, say what you tried and why you stopped. If you cannot proceed without a blocking decision,
return JSON with "blocking_decision": "the specific question and choices". The CSO answers first; only hard-stop
questions go to the PI. Write the question so either can answer it:
at most 700 characters, the question itself in the first sentence, then each choice on its own line starting
with "- ". Inside the JSON string write each line break as \\n. Do not proceed with the blocked work.

For reproducibility, save every analysis script under """ + OUTPUT_SCRIPTS_DIR + """ and every result-determining reference or
intermediate artifact (for example a gene mapping table or a copy of the gene set file) under """ + OUTPUT_REFERENCE_DIR + """.
A script reads upstream step files by their relative paths under inputs/<step id>/, exactly as the context lists
them, collected in one variable block at the top of the script; never write an absolute path into a script.
If you run scripts, save the version and package list of the interpreter that ran them to
""" + OUTPUT_ENV_DIR + """<your step id>.txt (`<that interpreter> --version` and `<that interpreter> -m pip freeze`), unless an
earlier environment step's lock covers exactly what you used; packages you installed yourself (for example into
./.pylib) always go in your own record.
If an HPC job runs the analysis, put the same version and package-list commands inside its job script so the compute
environment, not only the runner login environment, writes the record under outputs/env/.
Use .tmp only for disposable temporary files. In the method details, record the seed and tool and data versions.
""" + FIGURE_OUTPUT_RULE

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


def apply_declared_output_status(step: dict, result: TaskResult) -> TaskResult:
    """Apply the legacy required-output rule and the optional-output warning rule once a turn succeeded."""
    if not result.ok or blocking_question(result):
        return result
    collected = {rel for name in result.outputs if (rel := output_relpath(name))}
    required = [(str(name), rel) for name in step.get("outputs") or [] if (rel := output_relpath(name))]
    optional = [(str(name), rel) for name in step.get("optional_outputs") or [] if (rel := output_relpath(name))]
    hashed = {rel for name in result.output_sha256 if (rel := output_relpath(name))}

    def has_output(rel: str) -> bool:  # a declared folder is collected as its hashed files (#1058)
        return rel in collected or any(path.startswith(rel.rstrip("/") + "/") for path in hashed)

    missing = [name for name, rel in required if not has_output(rel)]
    missing_optional = [name for name, rel in optional if not has_output(rel)]
    warnings = list(result.step_warnings)
    if missing_optional:
        warnings.append("선택 산출 누락: " + ", ".join(missing_optional))
    updates: dict[str, Any] = {
        "missing_optional_outputs": list(dict.fromkeys([*result.missing_optional_outputs, *missing_optional])),
        "step_warnings": list(dict.fromkeys(warnings)),
    }
    if missing:
        updates.update(ok=False, missing_outputs=list(dict.fromkeys([*result.missing_outputs, *missing])),
                       error="incomplete: missing outputs: " + ", ".join(missing))
    return result.model_copy(update=updates)


def auxiliary_turn_meta(meta: dict, kind: str, outputs: list[str], **updates: Any) -> dict:
    """Build a helper turn without inheriting the parent step's output contract."""
    return {**meta, "kind": kind, "outputs": outputs, "required_outputs": outputs,
            "optional_outputs": [], **updates}


STEP_OUTPUTS_RULE = ("\n\nDeclared required outputs: save each at exactly this path in your workspace; "
                     "missing one makes the step incomplete: {required}. Optional outputs are collected when "
                     "present, but absence is only a warning: {optional}")

RESEARCH_RESULT_FIELD_RULES = "\n".join(f"- {rule}" for rule in RESULT_CONTRACT_FIELD_RULES) + (
    "\n- Evidence rows that cite the same source (the same DOI, PMID, accession or artifact path) must carry the "
    "same independence_group: a source cited twice is one independent source, and with two groups the later row is "
    "refused and its claim loses that evidence."
    "\n- For literature evidence, carry the fulltext tool's source_access_metadata into SourceRef "
    "(access_status, methods_locator, qc_locator, supplementary_status), with DOI/PMID and the exact "
    "source.locator you read. A stored receipt records access, not support: locate Methods/QC after reading, "
    "retain missing supplements, and set Evidence.lineage only from verified empirical origins or explicit "
    "re-citation IDs; otherwise leave it unknown.")

# What both reviews are asked about the declared checklist (#586). The context block that follows the prompt lists
# the ids (plan_review_context); labhq reads the answers in review_with_checklist and checklist_appendix.
CHECKLIST_REVIEW_RULE = (
    "When the declared topic checklist or Analysis precedents below list items, also fill `checklist` with one\n"
    "entry for every listed checklist item and required precedent item, using its id exactly as listed (a check\n"
    "listed once per topic as `topic.id` gets one entry per listed id). Each entry\n"
    "has `status` (done, partly or not_done) and `evidence`: the step id or output path in the results that shows\n"
    "it, or a short reason when not_done. One line each. Judge from the results, not from the plan's answer.\n"
    "For an item the plan answered step:<id> that you mark partly or not_done, labhq adds a P2 issue unless you\n"
    "already filed a P1 or P2 issue that names the item id; name the id when you file one.")

REVIEW_PROMPT = """Write only in your own outputs/ and .tmp/; never edit reviewed files or inputs.
Save the final JSON to outputs/review.json and return the same JSON as your final answer.
You are the scientific reviewer. Evaluate the team's work on the request below with three
criteria scored 1–5: addresses_question, evidence (how well conclusions are supported), thoroughness.
List concrete issues per step_id with a priority and a specific revision request. Priority P1 means fixing the issue
would change a conclusion; P2 means the conclusion stays the same but its evidence or wording is weak; P3 is minor.
Use verdict "revise" only when there is at least one P1 issue; otherwise use "accept".
Check the declared topic checklist and Analysis precedents supplied below. File an issue for anything omitted or
handled incorrectly; it is P1 when fixing it would change a conclusion.
A checklist item answered assumption with a stated, correct reason is acceptable (PI 2026-10-07): file an
issue for it only when the reason is wrong or the missing check would change a conclusion (then P1), not
merely because it was skipped.
Check that analysis steps declared key figures, the report embeds them under results or gives a one-line reason,
and every figure follows the SVG, Korean <text>, raster-fallback, and no-capture rules.
""" + CHECKLIST_REVIEW_RULE + """

Request: {request}

Team results:
{results}"""

REPLAN_PROMPT = BIOINFO_ASSIGNMENT_RULE + "\n\n" + """Re-plan only the unfinished part of the PI's request. You do not analyze anything yourself.

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
- """ + OPTIONAL_OUTPUTS_PLAN_RULE + """
- """ + DETERMINISTIC_TRANSFORM_RULE + """
- """ + ANALYSIS_REPRODUCIBILITY_PLAN_RULE + """
- """ + SCRIPT_OUTPUTS_PLAN_RULE + """
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

# Figures in the PI report (#584, #608): the request bundle rewrites <workdir_id>/outputs/<file> to
# steps/<step_id>/outputs/<file>. At most 11 figures: up to 10 result figures plus one graphical abstract.
REPORT_FIGURE_RULE = (
    "Figures: embed at most 11 figures in the whole report: up to 10 key result figures under \"결과\" plus one "
    "graphical abstract. You must embed every key figure declared by a step under \"결과\"; if one is omitted, give "
    "a one-line reason. Embed each as a markdown image on its own line with a short alt, exactly \"그림 N. 이름\", "
    "where N is its report order. SVG is the default. Use ![그림 N. 이름](<workdir_id>/outputs/<file>) and link "
    "only supported figure files copied exactly from the "
    "\"Outputs:\" lists of the team results below, never a path that is not listed, and use image links nowhere else. "
    "When no step produced a figure, embed no result figure. The short name says what the figure shows.\n"
    "Graphical abstract: one figure that summarizes the whole project as question -> data -> methods and steps -> key "
    "findings, with short Korean labels kept as SVG <text>. Link the final report step's listed graphical_abstract.svg "
    "as the first figure just below the title, using its exact Outputs path. Do not create files during "
    "read-only synthesis. If no step produced it, write no link: labhq draws the existing fallback flowchart.")

SYNTH_PROMPT = PAPER_RULE + """Write the final report for the PI.
""" + REPORT_FIGURE_RULE + """
Do not turn a failed lookup into evidence or proof of absence. LabHQ stores warnings and execution
details in the separate execution record; do not copy their details into the body. When the warning preview is not
"(none)", summarize its importance in one line under "논의" and end that line with "실행 기록 참고".
Warning preview ("(none)" means there is no warning section):
{warnings}

Scientific design assumptions: {assumptions}
Under "방법", add a subsection titled "가정" and list these choices with their reasons. If none were recorded,
do not invent any.
When Analysis precedents are supplied below, add a short "선행 연구 기준" subsection under "방법": one cited line for each required
analysis done or not done (with the reason), and put omitted recommended analyses under a "다음에 할 수 있는 분석" subsection in "논의".
Under "논의", include one line for every checklist answer that used assumption. Do not list checks that do not apply
to this request in the report body.

Request: {request}

Team results:
{results}

Reviewer: {review}"""

# Appended to SYNTH_PROMPT only when the generic review loop ended with the verdict still "revise" (2nd mock
# trial F5); the request still fails, but the PI gets the CSO's conclusion instead of a step dump.
UNRESOLVED_REVIEW_NOTE = (
    "\n\nThe reviewer still asked for revisions after the last revision labhq could run. Summarize the effect "
    "on the conclusion under \"논의\", but do not copy the reviewer's original text into the PI report. LabHQ "
    "stores every open issue verbatim in the scientific appendix.")

REVIEW_REFERENCE_NOTE = (
    "\n\nThe remaining P2 and P3 issues do not change the conclusion. LabHQ appends a \"리뷰 참고\" section with "
    "one line per P2 issue and the P3 count, so do not write that section or repeat those issues elsewhere.")

# The research lane after CP2 approval (#58 ③⑤). SYNTH_PROMPT and REVIEW_PROMPT above stay the generic ones.
RESEARCH_REVIEW_PROMPT = """Write only in your own outputs/ and .tmp/; never edit reviewed files or inputs.
Save the final JSON to outputs/review.json and return the same JSON as your final answer.
You are the scientific reviewer of a research request that ran under a frozen,
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
Check that analysis steps declared key figures, the report embeds them under results or gives a one-line reason,
and every figure follows the SVG, Korean <text>, raster-fallback, and no-capture rules.
For a branched plan, WARN (as a P2 issue whose problem starts with "WARN:") when a branch has an analysis-result table
in its step artifacts but that branch's actual analysis and result are absent from the summary or conclusion.
WARN the same way when the reported reference category is on the same exposure side as the hypothesis's focal
group. The primary contrast should be the focal group versus all others or the least-exposed group; a largest-group
reference is valid only when the hypothesis is unrelated to exposure ordering.
""" + CHECKLIST_REVIEW_RULE + """

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


RESEARCH_SYNTH_PROMPT = PAPER_RULE + """Write the final research report for the PI from the frozen plan, the evidence the PI
approved at CP2 and the research review below.

Claim anchors (labhq checks them by machine):
- End every sentence that states a conclusion or a number with the anchor of the claim it rests on, written
  exactly [[claim:<step_id>/<claim_id>]].
- Anchor only the citable claims listed below, each with the anchor shown there. A claim that is not listed as
  citable is not established: do not state it as a conclusion.
- A failed or empty lookup is neither evidence nor proof of absence. LabHQ appends its details, CP2 records and the
  claim check in the separate execution record. Summarize an important warning in one line under "논의" and
  end that line with "실행 기록 참고", without copying the raw warning into the body.
- Report the reviewer's P1 and P2 issues as limitations.
- Preserve claim-to-source identity (DOI/PMID/URI), source.locator and recorded access_status separately from
  whether evidence supports a claim. Use stored fulltext receipts and their actual Methods/QC locations;
  never infer a read, missing location, supplement availability or original empirical source from a title.
  Re-citations of one original are one independent source. Cycles, dangling lineage and unknown origins stay
  undetermined; evidence refused at CP2 cannot support a conclusion. LabHQ appends the recorded source table.
- For every branch, put the analysis actually performed and its result in the 요약(Abstract), 결론, and graphical
  abstract. A non-significant result is still a result: give the number of tests, threshold, and number passing.
- When a direct test could not be done and a substitute analysis was used, separate three statements: what was
  done and found; what it means and does not mean; and why the direct test was unavailable. In the branch comparison
  table's 판정 cell, include a one-line substitute-analysis result instead of only "supports neither side".
- State in one line whether each representative contrast matches the hypothesis and why its reference category was
  chosen. If the reference category is on the same exposure side as the focal group, mark it WARN rather than
  presenting it as the primary contrast.
""" + REPORT_FIGURE_RULE + """
When Analysis precedents are supplied below, add a short "선행 연구 기준" subsection under "방법": one cited line for each required
analysis done or not done (with the reason), and put omitted recommended analyses under a "다음에 할 수 있는 분석" subsection in "논의".
Under "논의", include one line for every checklist answer that used assumption. Do not list checks that do not apply
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

HALT_RESUME_PROMPT = ("The PI halted this task and has now resumed it. Continue the same task from where you "
                      "stopped. If the PI changed direction meanwhile, that note comes first in this message.")
WRAP_PROMPT = "Your turn limit was reached. Save any partial results under outputs/ and write outputs/PARTIAL_STATUS.md with what is done and what remains unfinished."
# Saving files, writing the status note and answering take more than two turns: a 2-turn wrap-up stopped at its
# third turn with nothing saved (7th mock trial, 2026-10-03).
WRAP_TURNS = 4

FINISH_PROMPT = """Your turn limit was reached before this step finished. Continue the same step in this session:
do not redo work that is already done. Finish only what remains, write the declared outputs, and give your final
answer in the required format."""

ASK_WAKE_PROMPT = """A blocking question from your previous turn has been answered:
Your earlier blocking question and its answer:
{answers}

Continue the same step in this session. Treat an employee's answer as advice, not as PI approval.
Do not repeat the same question."""

CONSULT_PROMPT = """Answer one blocked employee's question using the request, plan and policy context below.
Write only in your own outputs/ and .tmp/; existing work stays read-only. Answer once. Do not call labhq_ask or approve installations,
destructive work, restricted-data access, budget overruns or work outside the request.
{scope_advice}

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
this request's report, its output files and your earlier session. Write only in your own outputs/ and .tmp/; do not start new
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


# The longest card note one prompt carries (PI 점검 R11). The CP receipts and the audit appendix keep the whole note.
APPROVAL_NOTE_CHARS = 2000


def approval_note(note: Any) -> str:
    """A note the PI typed on an approved research card, bounded for a prompt."""
    text = str(note or "").strip()
    if len(text) <= APPROVAL_NOTE_CHARS:
        return text
    return (text[:APPROVAL_NOTE_CHARS].rstrip() +
            f" …(메모가 길어 {APPROVAL_NOTE_CHARS}자에서 잘랐습니다. 전문은 승인 기록에 있습니다.)")


def approval_note_block(gate: str, notes: list[tuple[str, str]], rule: str) -> str:
    """A prompt section with the PI's notes from approved ``gate`` cards ((label, note) pairs); "" without a note.

    A note is read by the people who act next, never as a decision: the gate's structured choice decided."""
    lines = [f"- {label}: {text}" if label else text
             for label, text in ((label, approval_note(note)) for label, note in notes) if text]
    if not lines:
        return ""
    return f"\n\n## PI 승인 메모 ({gate})\n" + "\n".join(lines) + "\n" + rule


CP1_NOTE_RULE = ("The PI wrote this note when approving the frozen plan at CP1. Follow it where the frozen plan "
                 "allows. It does not change the frozen plan: if it asks for something the plan does not allow, do "
                 "the plan's work and name the conflict in `failures`.")
CP2_REVIEW_NOTE_RULE = ("The PI wrote this when approving the evidence at CP2. Check each point it raises against the "
                        "ledgers and files and file an issue where one holds. It does not change the CP2 decision or "
                        "the frozen plan.")
CP2_REPORT_NOTE_RULE = ("The PI wrote this when approving the evidence at CP2. Cover every point it asks of the report; "
                        "a point the evidence cannot support goes under \"한계\" with the reason. It never makes an "
                        "uncitable claim citable and does not change the frozen plan.")
CONTINUE_NOTE_RULE = ("The PI wrote these on the previous round's cards. Use them in the new plan where they fit; the "
                      "PI approves the new plan at its own CP1.")


def _plan_cap_sum(budget: dict[str, Any]) -> str:
    """The two parts of an approved plan's cap, in the words the CP1 card and the staff prompts share."""
    return (f"계획 예산 ${float(budget.get('plan_budget_usd') or 0):.2f} + "
            f"계획까지 쓴 ${float(budget.get('spent_before_plan_usd') or 0):.2f}")


TOOL_MISSING = "도구 없음"


def tool_missing_lines(step_id: str, ledger: dict[str, Any]) -> list[str]:
    """CP2 warnings for evidence a step could not read for lack of a tool (#669): "도구 없음" at the start of an
    unavailable row's status_detail, so the PI or lead sees a given-up read instead of a silent gap."""
    rows = (ledger or {}).get("evidence") or []
    return [f"{step_id}/{','.join(row.get('slots') or [])}: {TOOL_MISSING} — "
            f"{str(row.get('status_detail')).strip()[len(TOOL_MISSING):].lstrip(':： ·-—')}"
            for row in rows if isinstance(row, dict) and row.get("status") == "unavailable"
            and str(row.get("status_detail") or "").strip().startswith(TOOL_MISSING)]


def _budget_line(budget: dict[str, Any]) -> str:
    """The CP1 card's one line on the cap its approval enforces (PI 점검 R17): the plan's budget_usd on top of what
    the request spent up to this CP1, never above the configured cap."""
    origin = {"request": "요청 예산", "policy": "정책 per_request_usd"}
    cap, planned = float(budget.get("cap_usd") or 0), budget.get("plan_budget_usd")
    if not cap:
        return "집행 상한 없음(정책 per_request_usd 0)."
    if planned is None:
        return f"집행 상한 ${cap:.2f}({origin.get(budget.get('source'), '요청 예산')}, 계획에 budget_usd 없음)."
    total = float(planned) + float(budget.get("spent_before_plan_usd") or 0)
    if budget.get("source") == "plan":
        return f"집행 상한 ${cap:.2f}({_plan_cap_sum(budget)}). 넘으면 예산 카드가 뜹니다."
    if budget.get("source") == "request" and total <= cap:
        return (f"집행 상한 ${cap:.2f}(요청 예산 그대로). 계획 추정은 참고만 합니다: "
                f"{_plan_cap_sum(budget)} = ${total:.2f}. 넘으면 예산 카드가 뜹니다.")
    return (f"집행 상한 ${cap:.2f}({origin.get(budget.get('source'), '요청 예산')} ${cap:.2f}에 맞춰 줄임: "
            f"{_plan_cap_sum(budget)} = ${total:.2f}).")


def unreceipted_references(contract: dict, reused: list[str]) -> list[str]:
    """DOI and PMID references that the reused steps ran under with only their identifier pinned (#1061).

    The earlier round's input proof lists them under ``unreceipted``: reuse is allowed while the identifiers are the
    same, but nothing checked what they point to, which the PI deciding on the reuse should be told."""
    proofs = ((contract.get("rounds") or [{}])[-1].get("reuse_input_proof")) or {}
    found: set[str] = set()
    for sid in reused:
        proof = proofs.get(sid)
        if isinstance(proof, dict):
            found.update(str(item) for item in proof.get("unreceipted") or [])
    return sorted(found)


def _cost_estimate_line(estimate: dict[str, Any]) -> str:
    """Short CP1 evidence for the request estimate; the enforced cap remains the separate budget line."""
    amount = estimate.get("estimate_usd")
    if estimate.get("basis") == "historical" and amount is not None:
        return (f"요청 총비용 추정 ${float(amount):.2f}(비슷한 완료 요청 "
                f"{int(estimate.get('similar_requests') or 0)}건 · 단계당 평균 "
                f"${float(estimate.get('average_per_step_usd') or 0):.2f} · 현재 "
                f"{int(estimate.get('current_steps') or 0)}단계).")
    if amount is not None:
        return f"요청 비용은 기존 계획 추정 ${float(amount):.2f} 사용(실측 기록 부족)."
    return "요청 비용 추정: 실측 기록 부족(계획 추정 없음)."


def selection_record_line(plan: Any) -> str:
    """The CP1 card's one line on the plan's selection record (#890); a missing record is shown, never blocking."""
    record = plan.get("selection_record") if isinstance(plan, dict) else None
    if not isinstance(record, dict) or not record:
        return "선택 기록 없음(검토한 대안·배제 이유가 계획에 고정되지 않았습니다)."
    return (f"선택 기록: 검토한 대안 {len(record.get('alternatives') or [])}개(배제 이유 포함) · "
            f"판단을 바꿀 관측 {len(record.get('change_signals') or [])}개.")


def research_cp2_notes(contract: dict) -> list[tuple[str, str]]:
    """The notes of every approved CP2 of this request, earlier rounds first: the PI approved each for its report."""
    receipts = [(f"round {archived.get('round')}", archived.get("cp2"))
                for archived in contract.get("rounds") or [] if isinstance(archived, dict)]
    receipts.append(("", (contract.get("checkpoints") or {}).get("cp2")))
    notes = [(label, str(receipt.get("note") or "")) for label, receipt in receipts
             if isinstance(receipt, dict) and receipt.get("decision") == "approved" and receipt.get("note")]
    if len(notes) == 1 and not notes[0][0]:  # this round's note alone needs no round label
        return notes
    return [(label or f"round {contract.get('round') or 1}", note) for label, note in notes]


def earlier_round_lines(contract: dict) -> list[str]:
    """What each earlier round of a continued request was asked to fix: its review's P1 issues, or the PI's CP2
    revision request. The final report says what this round changed (web trial 2026-10-08: the round-2 report never
    named round 1's five P1 issues, though the PI's CP2 note asked for that table)."""
    lines: list[str] = []
    for archived in contract.get("rounds") or []:
        if not isinstance(archived, dict):
            continue
        number, review = archived.get("round"), archived.get("review") or {}
        issues = research_continuation.p1_issues(review)
        if issues:
            lines.append(f"Round {number} review: {review.get('verdict')} with {len(issues)} P1 issue(s):")
            lines += [f"- {issue.get('step_id')}{'/' + str(issue['claim_id']) if issue.get('claim_id') else ''}: "
                      f"{short(str(issue.get('problem') or ''), 600)} Request: {short(str(issue.get('request') or ''), 400)}"
                      for issue in issues]
        receipt = archived.get("cp2") or {}
        if receipt.get("decision") == "revision_requested" and receipt.get("note"):
            lines.append(f"Round {number} CP2 revision request by the PI: {short(str(receipt['note']), 1200)}")
    return lines


EARLIER_ROUNDS_RULE = ("This request continued from the earlier rounds above. Under \"논의\", add a short "
                       "subsection \"이전 차수에서 고친 것\": one line per issue saying how this round addressed it, ending "
                       "with the claim anchor that shows the fix, or saying plainly that it was not addressed.")


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


def _turn_tool_calls(result: TaskResult) -> dict[str, dict[str, ToolCallCount]]:
    if result.tool_call_turns:
        return result.tool_call_turns
    return {result.task_id: result.tool_calls} if result.tool_calls else {}


def merged_tool_call_update(*results: TaskResult) -> dict[str, Any]:
    turns: dict[str, dict[str, ToolCallCount]] = {}
    for result in results:
        for task_id, counts in _turn_tool_calls(result).items():
            if task_id not in turns:
                turns[task_id] = {
                    name: count if isinstance(count, ToolCallCount) else ToolCallCount.model_validate(count)
                    for name, count in counts.items()
                }
    totals: dict[str, list[int]] = {}
    for counts in turns.values():
        for name, count in counts.items():
            row = totals.setdefault(name, [0, 0])
            row[0] += count.calls
            row[1] += count.failures
    return {
        "tool_calls": {
            name: ToolCallCount(calls=row[0], failures=row[1]) for name, row in sorted(totals.items())
        },
        "tool_call_turns": turns,
    }


def merged_tool_calls(*results: TaskResult) -> dict[str, ToolCallCount]:
    return merged_tool_call_update(*results)["tool_calls"]


def merged_turn(first: TaskResult, later: TaskResult) -> TaskResult:
    """A later turn of the same step in the same workspace. The runner lists and hashes the declared outputs as they
    are after that turn, so that view replaces the first turn's: a file the later turn removed, or grew past the hash
    limit, keeps no stale digest (PR #355 review). Files the step wrote without reporting them stay listed."""
    return later.model_copy(update={
        "unreported_outputs": sorted((set(first.unreported_outputs) | set(later.unreported_outputs))
                                     - set(later.outputs)),
        "tool_errors": [*first.tool_errors, *later.tool_errors],
        **merged_tool_call_update(first, later),
        "workdir": later.workdir or first.workdir,
        "workdir_id": later.workdir_id or first.workdir_id,
    })


def _sum_repair_turns(first: TaskResult, later: TaskResult) -> TaskResult:
    """A StructuredOutput repair dispatched after ``first``: the step's result carries both turns' cost and usage, as
    when the runner repaired inside one turn. Each dispatch was already counted on its own, never from this sum."""
    known = (first.cost_known is not False and first.cost_usd is not None
             and later.cost_known is not False and later.cost_usd is not None)
    usage = dict(first.usage)
    for key, value in later.usage.items():
        usage[key] = usage.get(key, 0) + value
    return later.model_copy(update={
        "cost_usd": first.cost_usd + later.cost_usd if known else None, "cost_known": known,
        "usage": usage, "usage_known": first.usage_known and later.usage_known})


def merged_workspace_rerun(first: TaskResult | None, later: TaskResult) -> TaskResult:
    """Keep unreported files when another task id reuses the same step folder."""
    if first and first.workdir and later.workdir == first.workdir:
        return merged_turn(first, later)
    return later


def reference_meta(request: dict | None) -> dict[str, list[str]]:
    """Path references travel as read-only directories; the runner re-checks them against its roots."""
    dirs = reference_dirs((request or {}).get("references"))
    return {"reference_dirs": dirs} if dirs else {}


_ALPHAGENOME_TOOLS = frozenset({"alphagenome", "alphagenome_variant"})  # registered only with a key file and the client


def _annot_roster(a: dict, alphagenome: bool = True) -> str:
    """The labhq_annot part of a roster line: everything the server has, or only the tools this agent was given (#1200).
    An allowed AlphaGenome tool is listed only where it can be used, since the server registers it only then."""
    if "annot" not in (a.get("builtin_mcp") or []):
        return ""
    tools = a.get("annot_tools")
    if tools is not None and not alphagenome:
        tools = [tool for tool in tools if tool not in _ALPHAGENOME_TOOLS]
    if tools is None:
        return ("labhq_annot=yes (VEP with AlphaMissense for human GRCh38, gnomAD, ClinVar, ChIP-Atlas, ENCODE cCRE, "
                "GTEx, RCSB PDB structure and ChEMBL compound lookups; AlphaGenome when its key is set); ")
    return f"labhq_annot=yes (tools: {', '.join(tools)}); " if tools else ""


def format_roster(agents: list[dict], *, alphagenome: bool | Callable[[dict], bool] = True) -> str:
    """One line per agent. ``alphagenome`` is a flag for every agent or a function of the agent: AlphaGenome is usable on
    the runner that serves the agent, not everywhere (#1200)."""
    lines = []
    for a in agents:
        tag = " [파견직]" if a.get("employment") == "contract" else ""
        tools = f" · tools: {', '.join(a['mcp'])}" if a.get("mcp") else ""
        hosts = external_hosts(a)
        if hosts:
            tag += f" [sends data to {', '.join(hosts)}; never on restricted-zone inputs]"
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
                     f"{'own outputs/ and .tmp/ writable; ' if access == 'read-only' else ''}"
                     f"tools={allowed or builtin or 'default'}; denied={a.get('disallowed_tools') or []}; "
                     f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}; "
                     f"{_annot_roster(a, alphagenome(a) if callable(alphagenome) else alphagenome)}"
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


ORCHESTRATION_ROLES = frozenset({"cso", "chief_of_staff", "sci_reviewer", "editor", "facilities"})
MAX_CLARIFY_CARDS = 2
ANSWERED_QUESTIONS_HEADING = "PI가 이미 답한 질문 — 다시 묻지 말 것"


def qa_text(entry: Any) -> str:
    """An answer, its source and what it answers (older records stored the answer alone)."""
    if isinstance(entry, dict):
        qs = entry.get("questions") or ([entry["question"]] if entry.get("question") else [])
        details = entry.get("question_details") or []
        asked = "\n".join("\n".join([f"Q{i}. {q}", *(question_detail_lines(details[i - 1])
                                                    if i <= len(details) and isinstance(details[i - 1], dict)
                                                    else [])])
                          for i, q in enumerate(qs, 1))
        answerer = {"pi": "PI", "cso": "CSO"}.get(str(entry.get("from") or "").lower(),
                                                      str(entry.get("from") or "PI"))
        return (f"{asked}\n{answerer} answer: {entry.get('answer', '')}" if asked
                else f"{answerer} answer: {entry.get('answer', '')}")
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
    action = "개발팀에 자동 보고됨" if req.get("dev_reports") else "다시 보내기"
    summary = "\n".join([
        f"실패한 단계: {sid} · {agent} — {cause}",
        f"원인: {cause}",
        action if req.get("dev_reports") else f"PI가 할 일: {action}",
    ])
    return summary, cause if sid == "request" else f"{sid} 실패: {cause}"


class PlanOutputsError(ValueError):
    """A declared step output that no normalization can bring under the step's outputs/ folder (#220)."""


class PlanAgentError(ValueError):
    """A plan assigned work to staff outside the current worker roster."""


EXTERNAL_CONTRACT_RULE = ("contract staff that send data to external services cannot take steps whose inputs touch a "
                          "restricted data zone; give those steps to core staff")


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
    match = re.search(r"(?m)^## 부록(?:: 실행 기록)?[ \t]*$", report)
    if not match:
        return report, ""
    title = match.group()
    body, raw = report[:match.start()], report[match.end():]
    appendix = raw.strip()
    # #229 benchmark parsers require their machine block to remain the last part of the PI report.
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(appendix))
    if len(blocks) == 1 and not appendix[blocks[0].end():].strip():
        benchmark = blocks[0].group(0)
        appendix = appendix[:blocks[0].start()].rstrip()
        body = body.rstrip() + "\n\n" + benchmark
    return body.strip(), (title + ("\n\n" + appendix if appendix else ""))


def _terminal_reports(rid: str, report: str, report_appendix: str, execution_record: str = "") -> dict[str, Any]:
    """Bound terminal copies while pointing every truncated field at the durable request."""
    data = {}
    for field, value in (("report", report), ("report_appendix", report_appendix), ("execution_record", execution_record)):
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
    limitations = re.search(r"(?m)^## (?:한계|논의)\s*$", report)
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
EARLIER_ROUNDS_HEADING = re.compile(r"(?m)^#{1,6}[ \t]*이전 차수에서 고친 것[ \t]*#*[ \t]*$")  # ATX closing #s too


def _earlier_rounds_fallback(contract: dict) -> str:
    """The "이전 차수에서 고친 것" section labhq writes when the report writer left it out (#512 review): the PI still
    sees what each earlier round was asked to fix. The claim check reads anchors only, so a missing section is not a
    report problem and does not fail the request."""
    rows: list[str] = []
    for archived in contract.get("rounds") or []:
        if not isinstance(archived, dict):
            continue
        number = archived.get("round")
        rows += [f"- {number}차 리뷰 P1 · {issue.get('step_id') or '-'}: {short(str(issue.get('problem') or ''), 200)}"
                 for issue in research_continuation.p1_issues(archived.get("review"))]
        receipt = archived.get("cp2") or {}
        if receipt.get("decision") == "revision_requested" and receipt.get("note"):
            rows.append(f"- {number}차 CP2 수정 요청: {short(str(receipt['note']), 200)}")
    if not rows:
        return ""
    return ("## 이전 차수에서 고친 것\n보고서 작성자가 이 절을 쓰지 않아 labhq가 이전 차수의 지적만 옮겼습니다. "
            "이번 차수에서 어떻게 고쳤는지는 결과 절과 실행 기록을 보세요.\n" + "\n".join(rows))


def _before_bench_block(report: str, section: str) -> str:
    """Add ``section`` at the end of the PI report, before a trailing benchmark result block, which bench scoring
    requires to stay last (labhq/bench.py; #517 review)."""
    discussion = re.search(r"(?m)^## 논의[ \t]*$", report)
    if discussion:
        following = re.search(r"(?m)^## ", report[discussion.end():])
        end = discussion.end() + (following.start() if following else len(report[discussion.end():]))
        section = re.sub(r"(?m)^## ", "### ", section)
        return report[:end].rstrip() + "\n\n" + section + "\n\n" + report[end:]
    marker = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)
    blocks = list(marker.finditer(report))
    if len(blocks) == 1 and not report[blocks[0].end():].strip():
        return report[:blocks[0].start()].rstrip() + "\n\n" + section + "\n\n" + blocks[0].group(0)
    return report.rstrip() + "\n\n" + section


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
    discussion = re.search(r"(?m)^## 논의[ \t]*$", report)
    if discussion:
        following = re.search(r"(?m)^## ", report[discussion.end():])
        end = discussion.end() + (following.start() if following else len(report[discussion.end():]))
        report = report[:end].rstrip() + "\n\n" + reference.replace("## 리뷰 참고", "### 리뷰 참고", 1) + "\n\n" + report[end:]
    else:
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
        step_warnings = result.step_warnings if isinstance(result, TaskResult) else result.get("step_warnings") or []
        lines.extend(f"- {sid}: {short(str(warning), 300)}" for warning in step_warnings[:5])
    lines += [f"- {line}" for line in output_checks.quality_lines(steps, results)]
    return "## 보고서 경고\n" + "\n".join(lines) if lines else ""


def _research_plan_digest(plan: dict) -> str:
    """The frozen plan as the research reviewer and report writer read it: question, protocol, pack values, steps.
    Only the step list is clipped: a real plan runs past 20,000 characters, and clipping the whole JSON cut the
    middle of the protocol the reviewer judges against (PR #358 review)."""
    steps = plan.get("steps") or []
    step_table = [{key: step.get(key) for key in ("id", "branch", "depends_on", "phase")} for step in steps]
    step_details = [{key: step.get(key) for key in ("id", "agent_id", "instruction", "claim_ids", "outputs",
                                                    "optional_outputs", "evidence_slots")}
                    for step in steps]
    frozen = {"brief": plan.get("brief"),
              "branches": plan.get("branches") or [],
              "protocol": {k: v for k, v in (plan.get("protocol") or {}).items() if k != "packs"},
              "pack_values": plan.get("pack_values") or {}}
    return (json.dumps(frozen, ensure_ascii=False) +
            "\nStep table (complete): " + json.dumps(step_table, ensure_ascii=False) +
            "\nStep details (instructions may be clipped): " +
            clip(json.dumps(step_details, ensure_ascii=False), 8000))


def _branch_terminal_states(topology: dict[str, Any], steps: list[dict],
                            results: dict[str, TaskResult]) -> list[dict[str, Any]]:
    """Summarize execution against the terminal sets from the shared branch topology."""
    states = []
    for branch_id in topology["branch_ids"]:
        terminals = topology["terminals"].get(branch_id, [])
        successful = [step_id for step_id in terminals if step_id in results and results[step_id].ok]
        branch_steps = [step["id"] for step in steps if step.get("branch") == branch_id]
        last_completed = next((step_id for step_id in reversed(branch_steps)
                               if step_id in results and results[step_id].ok), "none")
        failures = [f"{step_id}: {safe_failure_cause(results[step_id].error if step_id in results else 'not run')}"
                    for step_id in terminals if step_id not in successful]
        states.append({"branch": branch_id, "status": ("complete" if len(successful) == len(terminals) else
                                                         "partial" if successful else "failed"),
                       "terminals": terminals, "successful_terminals": successful,
                       "reason": "; ".join(failures) or "completed",
                       "last_completed_step": last_completed})
    return states


def _branch_terminal_status_table(states: list[dict[str, Any]]) -> str:
    lines = ["## 갈래별 terminal 상태", "", "| 갈래 | 상태 | terminal | 이유 | 마지막으로 끝난 단계 |",
             "|---|---|---|---|---|"]
    for state in states:
        reason = str(state["reason"]).replace("|", "\\|")
        lines.append(f"| {state['branch']} | {state['status']} | {', '.join(state['terminals']) or 'none'} | "
                     f"{reason} | {state['last_completed_step']} |")
    return "\n".join(lines)


def _research_protocol_digest(plan: dict, step: dict | None = None) -> str:
    """The frozen question and protocol a research step must follow, whole: a clipped middle could drop the very
    criterion the step needs (PR #358 review). A step instruction names a rule ("after the low-expression filter")
    without its criteria (8th mock trial: the analyst invented its own filter)."""
    # The same fields a continuation compares before it reuses a step (research/continuation.py).
    return (research_continuation.node_context(plan, step) if step is not None else
            json.dumps(research_continuation.frozen_context(plan), ensure_ascii=False))


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


# A reserved name as a whole file name: not the start of a longer one (`external_lookup_status.jsonl.md`, `-x`) and not the
# tail of another folder's name (`my_outputs/...`). A full stop that ends a sentence still ends the name, and so does a
# Korean particle (`...jsonl에`): only ASCII name characters continue it.
_RESERVED_WRITE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(re.escape(name) for name in sorted(output_checks.RESERVED_OUTPUTS)) + r")(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_])")


def _reserved_names(step: dict) -> list[str]:
    """Names labhq writes itself that this step declares as an output or names in its instruction (#676).

    The instruction may not name one at all: whether a sentence tells the staff to write it, read it or report it is
    a reading of wording that has variants without end, so a plan that names it is sent back and the planner words the
    step without it. The one name that stays is an upstream step's record reached through that step's folder
    (`inputs/<step>/outputs/<name>`), which the step reads and cannot write."""
    found = [rel for field in ("outputs", "optional_outputs") for name in step.get(field) or []
             if (rel := output_relpath(str(name))) in output_checks.RESERVED_OUTPUTS]
    text = step.get("instruction") or ""
    found += [match.group(0) for match in _RESERVED_WRITE.finditer(text)
              if not re.search(r"inputs/[^/\s]+/$", text[:match.start()])]
    return list(dict.fromkeys(found))


def _contain_outputs(step: dict) -> tuple[list[str], str | None]:
    """Keep every declared output under outputs/ before dispatch (#220).

    The runner collects a declared output only from the workspace outputs/ folder (`output_relpath`). A plan
    that declares `answer.md` but tells the agent to write `./answer.md` gets an INCOMPLETE step even though
    the file exists. A root reference to the step's own output is rewritten to `./outputs/...` and the
    declaration made explicit. An output that leaves outputs/ (absolute, drive, `..`) cannot be fixed here;
    it comes back as a problem for the caller to reject.
    """
    warnings, ambiguous, problems = [], [], []
    if reserved := _reserved_names(step):
        problems.append(f"step {step['id']}: {reserved} is a record labhq writes itself; name the staff's own file "
                        "differently (for example outputs/lookup_ledger.jsonl) and do not tell the staff to write it")
    all_seen: dict[str, str] = {}
    for field in ("outputs", "optional_outputs"):
        if field not in step:
            continue
        bad, normalized, seen = [], [], set()
        for name in step.get(field) or []:
            rel = output_relpath(name)
            if rel is None:
                bad.append(name)
                continue
            if rel in seen:
                warnings.append(f"step {step['id']}: duplicate {field} entry {name!r} removed as {rel}")
                continue
            seen.add(rel)
            if rel in all_seen:
                warnings.append(f"step {step['id']}: {rel} is already in {all_seen[rel]}; removed from {field}")
                continue
            all_seen[rel] = field
            inner = rel[len("outputs/"):] if rel.startswith("outputs/") else rel
            instruction, refs, unclear = _rewrite_output_references(step["instruction"], inner, rel)
            ambiguous.extend(unclear)
            if refs or name.strip().replace("\\", "/") != rel:
                step["instruction"] = instruction
                warnings.append(f"step {step['id']}: {field} entry {name!r} moved under outputs/ as {rel}")
            normalized.append(rel)
        step[field] = normalized + bad
        if bad:
            problems.append(f"step {step['id']}: {field} {bad} are outside its outputs/ folder")
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
                all(isinstance(name, str) for name in step["outputs"]) and
                isinstance(step.get("optional_outputs", []), list) and
                all(isinstance(name, str) for name in step.get("optional_outputs", []))):
            continue
        contained, problem = _contain_outputs(step)
        warnings.extend(contained)
        if problem:
            problems.append(problem)
    if problems:
        raise PlanOutputsError("; ".join(problems) + ". Declare each output as outputs/<name> inside the "
                               "step's own workspace and save it at that path.")
    return normalized, warnings


MODEL_TIER_ORDER = ("fast", "balanced", "reasoning")
# A step that checks or reports never runs below this tier (#625): its output is what the PI reads and trusts.
MODEL_TIER_FLOOR = {"validation": "balanced", "report": "balanced"}


def clamp_step_tier(sid: str, requested: Any, declared: Any, phase: Any = None) -> tuple[str | None, str | None]:
    """The tier a step really runs on, and a warning when the request was changed or dropped (#625).

    A step may only be lowered from its staff member's declared tier, never raised. A phase with a floor
    (validation, report) cannot go below it. A staff member with no declared tier takes no step tier, and a floor
    above the declared tier leaves the step on its own. Returns (tier or None, warning or None); None means "run as
    declared" and the field is left out."""
    if requested not in MODEL_TIER_ORDER:
        return None, f"step {sid}: model_tier {requested!r} is not fast, balanced or reasoning; ignored"
    if declared not in MODEL_TIER_ORDER:
        return None, f"step {sid}: model_tier {requested} ignored: its staff member declares no model tier"
    ceiling = MODEL_TIER_ORDER.index(declared)
    chosen = min(MODEL_TIER_ORDER.index(requested), ceiling)
    floor = MODEL_TIER_ORDER.index(MODEL_TIER_FLOOR[phase]) if phase in MODEL_TIER_FLOOR else 0
    if floor > ceiling:
        return None, f"step {sid}: model_tier {requested} ignored: a {phase} step cannot go below {MODEL_TIER_FLOOR[phase]}, above its staff member's {declared}"
    chosen = max(chosen, floor)
    tier = MODEL_TIER_ORDER[chosen]
    note = None if tier == requested else (
        f"step {sid}: model_tier {requested} changed to {tier} (staff member declares {declared}"
        + (f", {phase} steps stay at {MODEL_TIER_FLOOR[phase]} or above" if phase in MODEL_TIER_FLOOR else "") + ")")
    return (None if tier == declared else tier), note


def validate_steps(raw: list[dict], known: set[str], max_steps: int,
                   excluded: frozenset[str] | set[str] = ORCHESTRATION_ROLES, *,
                   vocab: output_vocab.Vocab | None = None, stats: dict | None = None,
                   reject_excess: bool = False,
                   stored_environment: bool = False,
                   guard: Callable[[list[dict]], list[str]] | None = None,
                   tier_rules: Mapping[str, Any] | None = None) -> tuple[list[dict], list[str]]:
    """Steps ready to dispatch, plus warnings. With ``vocab`` (plan.declare_output_types on), each step's
    ``output_types`` is normalized against its final outputs and per-request counts go into ``stats``; without it,
    any ``output_types`` the CSO sent is dropped and the steps are exactly what they were before #221.

    A fresh CSO plan is cut to ``max_steps``. A stored plan passes ``reject_excess``: cutting it would drop steps
    the request already promised, so a plan over the limit raises instead (#282). ``guard`` names steps the
    roster may not take as planned (contract staff that send data out on restricted inputs, #424).

    ``tier_rules`` (#625, orchestrator.step_model_tiers on) is ``{"agents": {staff id: declared tier}}``: a step's
    ``model_tier`` is kept only as clamp_step_tier allows. Without it the field is dropped, exactly as before."""
    # Scheduler-made runner environment clones are not counted against the limit (#674).
    extra = scheduler_clone_extra(raw) if stored_environment else 0
    if reject_excess and len(raw) > max_steps + extra:
        raise ValueError(f"stored plan has {len(raw)} steps; maximum is {max_steps}"
                         + (f" plus {extra} runner environment steps" if extra else "")
                         + "; raise orchestrator.max_steps to resume it")
    max_steps += extra
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
                      "outputs": [str(o) for o in s.get("outputs") or []],
                      **({"optional_outputs": [str(o) for o in s.get("optional_outputs") or []]}
                         if "optional_outputs" in s else {}),
                      **({"key_figures": [str(o) for o in s.get("key_figures") or []]}
                         if "key_figures" in s else {}),
                      **({key: copy.deepcopy(s[key]) for key in ("environment_binding", "environment_bindings")
                          if key in s} if stored_environment else {})})
        if tier_rules is not None and s.get("model_tier") is not None:
            tier, note = clamp_step_tier(sid, s.get("model_tier"), (tier_rules.get("agents") or {}).get(steps[-1]["agent_id"]),
                                         s.get("phase"))
            if tier:
                steps[-1]["model_tier"] = tier
            if note:
                warnings.append(note)
        raw_types[sid] = s.get("output_types")
    problems = []
    for s in steps:
        contained, problem = _contain_outputs(s)
        warnings.extend(contained)
        problems += [problem] if problem else []
    if problems:
        raise PlanOutputsError("; ".join(problems) + ". Declare each output as outputs/<name> inside the "
                               "step's own workspace and save it at that path.")
    for step in steps:
        if "key_figures" not in step:
            continue
        figures = list(dict.fromkeys(filter(None, map(output_relpath, step["key_figures"]))))
        invalid = [name for name in step["key_figures"] if output_relpath(name) not in step["outputs"]]
        if invalid or not 1 <= len(figures) <= 2:
            raise PlanOutputsError(
                f"step {step['id']}: key_figures must contain 1–2 paths also declared in outputs")
        step["key_figures"] = figures
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
            if stored_environment and other.get("environment_binding"):
                # Logical IDs in manifest instructions do not create cross-runner dependencies.
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
            declared = [*s["outputs"], *s.get("optional_outputs", [])]
            entries, issues = output_types.normalize_entries(declared, raw_types.get(s["id"]), vocab)
            if entries:
                s["output_types"] = entries
            warning = output_types.issue_warning(s["id"], issues)
            if warning:
                warnings.append(warning)
            if stats is not None:
                stats.update(output_types.add_stats(stats, output_types.stats(declared, entries, issues)))
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
    if stored_environment:
        validate_environment_bindings(steps)
    conflicts = guard(steps) if guard else []
    if conflicts:
        raise PlanAgentError(EXTERNAL_CONTRACT_RULE + f": {conflicts}")
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
        outputs = [str(o) for o in [*(step.get("outputs") or []), *(step.get("optional_outputs") or [])]] \
            if isinstance(step.get("outputs"), list) and isinstance(step.get("optional_outputs", []), list) else []
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


REPORT_CORRECTIONS = 2  # the CSO's report may be sent back this often when the claim check finds problems (#956)


def report_correction(problems: list[str], citable: str, report: str) -> str:
    """Send a report that failed the claim check back to its writer: the problems and the anchors it may use (#956)."""
    return "\n".join([
        "The report below failed labhq's machine claim check. Fix every problem listed and return the complete "
        "corrected report.",
        "Do not redo the analysis, change a finding, or add a conclusion: only anchor each sentence that states a "
        "conclusion or number with the claim it rests on, written exactly [[claim:<step_id>/<claim_id>]], using only "
        "the citable claims below. Do not state a claim that is not listed as citable.",
        "", "Problems:", *_problem_lines(problems, "1."), "", "Citable claims:", citable, "",
        "Report to correct:", report])


def result_correction(problems: list[str], claim_ids: list[str] | None = None) -> str:
    """Ask for ledger JSON only: the completed analysis and its files must not run again.

    ``claim_ids`` are the claims the frozen plan declared for this step. A correction that said only "record it as a
    claim" made a worker add a claim id the plan never declared, and the step then failed on that (#1066)."""
    claims = [
        "The claims you may return are exactly this step's claim_ids: " + json.dumps(list(claim_ids)) + ". Never "
        "create a claim id outside that list. A row that is not evidence (a hypothesis, mechanism or background row) "
        "is linked to one of these claims with relation context, or left out of the links; it is never a new claim. "
        "Where a problem below says to record something as a claim, that applies only to a claim id on this list."
    ] if claim_ids is not None else []
    return "\n".join([
        "The previous research result JSON failed contract validation. Fix every problem below and return only one "
        "complete corrected result JSON object.",
        "Do not recreate or modify output files, rerun the analysis, or change the method. Fix the result JSON only.",
        *claims,
        *_problem_lines(problems, "1."),
    ])


PLAN_CORRECTIONS = 3  # corrections the CSO gets for a PLAN that fails validation, each with the problems verbatim (#948)


def plan_invalid_report(problems: list[str], packs: dict[str, Any], corrections: int = 1) -> str:
    """What the PI reads when the corrected PLAN still fails: the problems, and the packs it was held to."""
    lines = [f"연구 계획 검증 실패: CSO가 {corrections}번 고친 계획도 계약 검사를 통과하지 못해 CP1 승인 카드를 만들지 "
             f"않았습니다(남은 문제 {len(problems)}건).", "", "남은 문제:", *_problem_lines(problems, "- ")]
    if packs:
        lines += ["", "설정된 domain pack: " + "; ".join(f"{key} ({loaded.pack.applies_when})"
                                                       for key, loaded in sorted(packs.items())),
                  "모든 configured pack에 값 또는 `not_applicable` 사유를 답합니다."]
    return "\n".join(lines)


class BudgetExceeded(RuntimeError):
    pass


def _usd_amount(value: Any) -> bool:
    """A positive, finite dollar amount; bools, strings, NaN, infinity, zero and negatives are not (#862)."""
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def valid_review(value: Any, schema: dict[str, Any] = REVIEW_SCHEMA) -> bool:
    """Check every required field of ``schema`` (REVIEW_SCHEMA unless given) without a new JSON Schema dependency."""
    def matches(item: Any, schema: dict) -> bool:
        typ = schema.get("type")
        if isinstance(typ, list):
            return any(matches(item, {**schema, "type": choice}) for choice in typ)
        if typ == "null":
            return item is None
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
        if typ == "boolean":
            return type(item) is bool
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
    lines = ["Analysis precedents:",
             "Paper access/receipt declarations are unverified until artifact hash/ranges are checked after approval. "
             "Do not invent method/QC parameters; request missing evidence.", "Papers:"]
    lines.extend(f"- {row.get('title')}: {_citations(row)}" +
                 "".join(f"; {key}: {row[key]}" for key in ("repo", "data_url", "fulltext_receipt") if row.get(key))
                 for row in record.get("papers") or [])
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


def checklist_snapshot(plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                       precedents: Any) -> list[dict[str, str]]:
    """The exact checklist requirements used by this frozen plan."""
    return [{"id": item.id, "check": item.check, "why": item.why, "topic": item.topic}
            for item in required_checklist_items(plan, catalog, precedents)]


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
    answers = body.get("checklist") if isinstance(body.get("checklist"), dict) else {}
    # A shared id the plan answered per topic is listed once per topic as topic.id, with the plan's answer, so the
    # reviewer's rows say which topic's step missed the check (PR #602 review).
    lines.extend(f"- {entry.id}: {entry.check} Why: {entry.why}" + (f" — 계획 답: {entry.answer or '없음'}"
                                                                  if entry.topic else "")
                 for entry in topic_checklists.review_entries(answers, required, catalog))
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


def lenient_review(value: Any, schema: dict[str, Any]) -> dict | None:
    """``value`` when it is a valid review, or valid once an unreadable optional ``checklist`` is dropped (#586).

    A malformed verdict row must not cost the whole review: it gets two parse attempts, then ends review_unparsed."""
    if valid_review(value, schema):
        return value
    if isinstance(value, dict):
        # An unreadable optional field costs only itself (#586 checklist, #889 blind_comparison).
        for dropped in (("checklist",), ("blind_comparison",), ("checklist", "blind_comparison")):
            if any(key in value for key in dropped):
                rest = {key: item for key, item in value.items() if key not in dropped}
                if valid_review(rest, schema):
                    return rest
    return None


def _names_check(issue: dict, miss: topic_checklists.ReviewMiss) -> bool:
    """Whether a review issue names the check the miss is about. ASCII boundaries: a Korean particle may follow the id.

    When the check is shared by several topics, an issue about another topic's ``topic/id`` or ``topic.id`` does not
    stand in for this topic's miss (PR #600 review)."""
    text = " ".join(str(issue.get(key) or "") for key in ("problem", "request", "evidence_quote"))

    def named(spelling: str) -> bool:
        return re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(spelling)}(?![A-Za-z0-9_-])", text) is not None

    qualified = {topic: named(f"{topic}/{miss.item.id}") or named(f"{topic}.{miss.item.id}")
                 for topic in miss.item.topic.split(",")}
    if any(qualified[topic] for topic in miss.topics or qualified):
        return True
    return not any(qualified.values()) and named(miss.item.id)


# The solo route reviews one direct result, not the plan's steps: those are only the fallback (route_decision).
SOLO_OWNER = "단독 처리"


def review_with_checklist(review: dict, plan: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                          precedents: Any, *, research: bool = False, owner: str | None = None) -> dict:
    """The review plus a P2 issue for each check the plan answered with a step that the reviewer found partly or not
    done (#586). In the 10-13 trial the reviewers got the checklist and cited it in no finding.

    A P1 or P2 issue that already names the check's id stands in for it; a P3 one does not. assumption and
    not_applicable answers add nothing: the plan stated why, and the review already files a wrong reason. A check
    several topics share, answered per topic, gets one issue per topic on that topic's own step. The research lane's
    issues carry its extra fields (claim_id, category, evidence_quote).

    ``owner`` is the result a solo review judged (SOLO_OWNER). The plan's steps never ran then, so the issue goes
    to that result: it is neither filed on a plan step nor tells the PI to fix one (PR #600 review)."""
    body = plan if isinstance(plan, dict) else {}
    required = required_checklist_items(body, catalog, precedents)
    issues = [issue for issue in review.get("issues") or [] if isinstance(issue, dict)]
    added: list[dict] = []
    for miss in topic_checklists.review_misses(body.get("checklist"), required, review.get("checklist")):
        if any(issue.get("priority") in ("P1", "P2") and _names_check(issue, miss) for issue in [*issues, *added]):
            continue
        verdict = "일부만 했다" if miss.status == "partly" else "하지 않았다"
        if owner:
            issue = {"step_id": owner, "priority": "P2",
                     "problem": f"점검 {miss.name}: {owner} 결과에서 리뷰어는 {verdict}고 판정했습니다. "
                                f"근거: {miss.evidence or '없음'}",
                     "request": f"{owner} 결과에서 점검 {miss.name}을(를) 끝내거나, 못 하는 이유를 한계에 남기세요."}
        else:
            steps = ", ".join(miss.steps)
            issue = {"step_id": miss.steps[0], "priority": "P2",
                     "problem": f"점검 {miss.name}: 계획은 {steps} 단계로 답했지만 리뷰어는 {verdict}고 "
                                f"판정했습니다. 근거: {miss.evidence or '없음'}",
                     "request": f"{steps} 단계에서 점검 {miss.name}을(를) 끝내거나, 못 하는 이유를 계획에 "
                                "assumption으로 남기세요."}
        if research:
            issue.update(claim_id="", category="other", evidence_quote=miss.evidence)
        added.append(issue)
    return {**review, "issues": [*issues, *added]} if added else review


def checklist_appendix(plan: Any, review: Any, catalog: dict[str, list[topic_checklists.ChecklistItem]],
                       precedents: Any, *, owner: str | None = None) -> str:
    """The execution-record table of every required check: item, owning step, reviewer verdict, evidence (#586).
    The report body never named the checks; only assumption answers reached it as limitations. ``owner`` names the
    result that did the checks when it is not a plan step (the solo route: SOLO_OWNER)."""
    body = plan if isinstance(plan, dict) else {}
    required = required_checklist_items(body, catalog, precedents)
    if not required:
        return ""
    rows = review.get("checklist") if isinstance(review, dict) else None
    return ("점검표 (분야 점검 항목, 담당 단계, 리뷰 판정):\n\n" +
            topic_checklists.report_table(body.get("checklist"), required, rows, owner=owner))


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


PI_STOP_ERROR_KINDS = frozenset({"ask_rejected", "halted"})


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
    Policy/approval/budget/cancel/PI halt/       terminal
      invalid input,
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
    if is_model_refusal(outcome):
        return "terminal"
    if outcome.error_kind in PI_STOP_ERROR_KINDS or outcome.error_kind == "structured_output_format":
        # PI stops and exhausted JSON repair must not enter another retry/facilities loop.
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
    if outcome.error_kind == "contract" or "invalid research result contract" in error:
        return "contract"
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


def _session_cost_fields(hub: Any, req: dict) -> dict:
    fields = getattr(hub, "request_session_cost_fields", None)
    return fields(req) if fields else {}


RESTRICTED_APPROVAL_LIMIT = 5  # calls the CSO lets through while one restriction lasts (#1008)
RESTRICTED_STALE_S = 90.0  # a request older than this was already refused at the hook (30 s wait): never judged


# A hold below one cent cannot be enforced (Claude's CLI renders cents), so a request with less left than that is not clipped
# to it: the turn asks like any other (#1139).
_HOLD_EPSILON = 1e-9


_CENT = Decimal("0.01")
_FLOAT_NOISE = Decimal("1e-9")  # what a subtraction of two amounts (2.0 - 1.98, 5.0 - 4.99) can be off by


_CENTS_MATTER_BELOW = 1e12  # above this a float has no cents left to round, and Decimal's 28 digits would overflow


def _floor_cents(amount: float) -> float:
    """An amount rounded down to whole cents, as the Claude adapter writes a call cap in cents (a $0.015 hold sent as $0.02
    would spend more than was held). Decimal arithmetic with an explicit float-noise tolerance: $0.0199996 is $0.01, while
    2.0 - 1.98 (0.020000000000000018) is $0.02 and 5.0 - 4.99 is a cent. A huge amount is returned as it is."""
    if not math.isfinite(amount) or amount >= _CENTS_MATTER_BELOW:
        return amount
    return float((Decimal(repr(float(amount))) + _FLOAT_NOISE).quantize(_CENT, rounding=ROUND_FLOOR))


def _clip_to_remaining(cap: float | None, remaining: float) -> float | None:
    """The hold for one turn: its cap, cut to what the request still has when that is enough to hold at all. A cut hold is
    rounded down to whole cents (`_floor_cents`); under one cent it is not cut."""
    if cap is None or not math.isfinite(remaining) or cap <= remaining:
        return cap
    if _floor_cents(remaining) < float(_CENT):
        return cap
    return _floor_cents(remaining)


class Orchestrator:
    def __init__(self, hub: "Hub"):
        self.hub = hub
        self.cfg = hub.s.orchestrator
        # task id -> {episode, seen fingerprints, approved count} of the restriction the CSO is judging calls for; the
        # store keeps it ("restricted_ledger") so a gateway restart does not hand the restriction a fresh budget
        self.restricted_calls: dict[str, dict[str, Any]] = {}
        self.cost: dict[str, float] = {}
        # Task IDs whose cost is already in self.cost; durable totals can run ahead of run_step.
        self.cost_tasks: dict[str, set[str]] = {}
        # Counted tasks whose cost is unaccounted: each is held at per_task_usd when the cap is judged (#270).
        self.cost_unknown: dict[str, set[str]] = {}
        self.attempts: dict[str, dict[str, int]] = {}
        self.budget_locks: dict[str, asyncio.Lock] = {}
        self.budget_denials: dict[str, str] = {}
        self.budget_outcomes: dict[str, list[dict]] = {}
        self.step_tasks: dict[str, set[asyncio.Task]] = {}  # tasks a request runs beside its own (R13 cancel)
        self.paper_builds: dict[str, paper_skills.ApprovedBuilds] = {}
        self.paper_budget_holds: dict[str, dict[str, float]] = {}
        # #862: attempts this process reserved and is dispatching now; a restart prune never touches them.
        self.dispatch_inflight: dict[str, set[str]] = {}
        # request -> task id -> the most that dispatch may still spend, for every turn in flight (#673). Costs only
        # appear in the request record when a turn ends, so a StructuredOutput repair reads this to avoid spending
        # what a parallel sibling may yet use.
        self.inflight_caps: dict[str, dict[str, float]] = {}
        self.paper_task_ids: dict[str, dict[str, str]] = {}
        self.consult_locks: dict[tuple[str | None, str], tuple[asyncio.Lock, int]] = {}

    def _step_task(self, rid: str, coro) -> asyncio.Task:
        """A step or briefing the request runs as its own task; request cancel stops it with the request (R13)."""
        task = asyncio.create_task(coro)
        tasks = self.step_tasks.setdefault(rid, set())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    def cancel_steps(self, rid: str) -> list[asyncio.Task]:
        """Cancel every step task of a request; returns them so the caller can wait for them to unwind."""
        tasks = [task for task in self.step_tasks.pop(rid, set()) if not task.done()]
        for task in tasks:
            task.cancel()
        return tasks

    async def _emit(self, rid: str, typ: str, data: dict) -> None:
        await self.hub.publish({"type": typ, "ts": time.time(), "request_id": rid, "data": data})

    async def _semantic_devices_check(self, rid: str, phase: str) -> dict[str, Any]:
        """Refresh the request-scoped device record before a PI checkpoint."""
        req = self.hub.requests[rid]
        previous = req.get("semantic_devices") if isinstance(req.get("semantic_devices"), dict) else {}
        previous_errors = {(row.get("code"), row.get("stage"))
                           for row in previous.get("errors") or [] if isinstance(row, dict)}
        service = None
        service = getattr(self.hub, "semantics_shadow", None)  # semantics-hook
        record = semantic_devices.inspect(self.hub.s, service=service, worker_known=True, phase=phase)
        # Runtime-only errors such as an advisory projection failure must survive the next durable health refresh.
        durable = {(row.get("code"), row.get("stage")) for row in record["errors"]}
        for row in previous.get("errors") or []:
            key = (row.get("code"), row.get("stage")) if isinstance(row, dict) else None
            if key and key not in durable and row.get("stage") in {
                    "advisory_snapshot", "advisory_projection", "advisory_boundary", "load", "render", "resume"}:
                record["errors"].append(row)
                record["status"] = "error"
        record["advisory"] = semantic_devices.advisory_state(
            req.get("semantics_ab"), record.get(semantic_devices.DEVICE))
        req["semantic_devices"] = record
        checks = req.setdefault("semantic_device_checks", [])
        checks.append(record)
        if len(checks) > 4:
            del checks[:-4]
        await persist_request(self.hub, rid)
        current_errors = {(row.get("code"), row.get("stage"))
                          for row in record["errors"] if isinstance(row, dict)}
        if current_errors and current_errors != previous_errors:
            await self._emit(rid, "request.semantic_devices", record)
        return record

    async def _semantic_device_error(self, rid: str, *, code: str) -> None:
        req = self.hub.requests[rid]
        record = req.get("semantic_devices") if isinstance(req.get("semantic_devices"), dict) else {
            "version": 1, "phase": "runtime", "checked_at": time.time(), "status": "ok", "errors": []}
        semantic_devices.add_error(record, code=code)
        record["advisory"] = semantic_devices.advisory_state(
            req.get("semantics_ab"), record.get(semantic_devices.DEVICE))
        req["semantic_devices"] = record
        await persist_request(self.hub, rid)
        await self._emit(rid, "request.semantic_devices", record)

    async def _plan_clarification(self, rid: str, candidate: dict) -> tuple[str, dict | None]:
        """Expose only new plan questions and store one answered card; the caller performs the next re-plan."""
        req = self.hub.requests[rid]
        details = unanswered_questions(candidate.get("clarifying_questions"), req.get("clarifications"))
        candidate["clarifying_questions"] = details
        storage_service = getattr(self.hub, "result_storage", None)
        storage = await storage_service.card(rid) if storage_service else None
        if storage and not details:
            decision = await self.hub.request_approval(
                kind="result_storage", request_id=rid, summary="결과를 GitHub 저장소로 정리할까요?",  # STORAGE_QUESTION (#688)
                detail={"storage": storage})
            if not req.get("result_storage"):
                await storage_service.select(rid, decision.get("storage") or {"mode": "local"})
        if not details:
            req["pending_questions"] = []
            req.pop("pending_question_details", None)
            req.pop("clarification_failure", None)
            await persist_request(self.hub, rid)
            return "done", None
        req["pending_questions"] = [question["question"] for question in details]
        if has_structure(details):
            req["pending_question_details"] = details
        else:
            req.pop("pending_question_details", None)
        await persist_request(self.hub, rid)
        await self._emit(rid, "request.questions", {"questions": req["pending_questions"], "details": details})
        if not self.cfg.wait_for_clarification:
            if storage and not req.get("result_storage"):
                await storage_service.select(rid, {"mode": "local"})
            return "open", None
        # Answers a CP1 수정 요청 carried over from the old request filter questions but used none of this one's cards.
        contract = req.get("research_contract") or {}
        research_round = int(contract.get("round") or 1)
        # Round 1 asks before its contract exists, so the research intake marks it. An answer saved before rounds
        # were tagged may belong to this round, so it counts here: a resumed round never gets two more cards
        # (PR #527 review).
        tagged = bool(contract) or (req.get("intake") or {}).get("work_kind") == "research"
        if len([entry for entry in req.get("clarifications") or []
                if isinstance(entry, dict) and not entry.get("inherited")
                and (not contract or int(entry.get("research_round") or research_round) == research_round)
                ]) >= MAX_CLARIFY_CARDS:
            return "limit", None
        detail = {"questions": details}
        if storage:
            detail["storage"] = storage
        assumptions = normalize_assumptions(candidate.get("assumptions"))
        if assumptions:
            detail["assumptions"] = assumptions
        decision = await self.hub.request_approval(
            kind="clarify", request_id=rid, summary=questions_summary(details), detail=detail)
        if storage and not req.get("result_storage"):
            await storage_service.select(rid, decision.get("storage") or {"mode": "local"})
        if decision.get("state") == "timed_out":  # no answer within the bound is not a deny (R5)
            return "timed_out", None
        if not decision.get("approved") or not str(decision.get("note") or "").strip():
            return "unanswered", None
        entry = {"questions": list(req["pending_questions"]), "answer": str(decision["note"]).strip()}
        if tagged:
            entry["research_round"] = research_round
        if has_structure(details) or any(question.get("id") for question in details):
            entry["question_details"] = details
        req.setdefault("clarifications", []).append(entry)
        req["pending_questions"] = []
        req.pop("pending_question_details", None)
        await persist_request(self.hub, rid)
        return "answered", entry

    def _output_vocab(self) -> output_vocab.Vocab | None:
        """The vocabulary when plan.declare_output_types is on and it loads; None means today's plan, unchanged."""
        plan = getattr(self.hub.s, "plan", None)
        return output_vocab.current() if getattr(plan, "declare_output_types", False) else None

    def _request_inputs(self, rid: str) -> tuple[list[str], list[str]]:
        """The request text and folders every step of it reads, for the restricted-input rule (#424)."""
        req = self.hub.requests.get(rid) or {}
        return [str(req.get("text") or "")], [*(req.get("project_dirs") or []), *reference_dirs(req.get("references"))]

    def _tier_rules(self) -> dict[str, Any] | None:
        """What validate_steps needs to keep a step's model_tier (#625): None while orchestrator.step_model_tiers is off."""
        if not getattr(self.cfg, "step_model_tiers", False):
            return None
        return {"agents": {agent_id: (info or {}).get("model_tier")
                           for agent_id, info in (getattr(self.hub, "agents", None) or {}).items()}}

    def _dispatch_step_tier(self, step: dict) -> str | None:
        """The tier a step's task is sent with (#625): the one place that decides, whatever plan the step came from.

        A stored research plan resumes with its own step dicts, not the ones validate_steps returned, so the tier is
        checked again here: nothing while orchestrator.step_model_tiers is off, otherwise clamp_step_tier against the
        staff member's declared tier and the phase floor."""
        rules = self._tier_rules()
        if rules is None or step.get("model_tier") is None:
            return None
        tier, _note = clamp_step_tier(str(step.get("id") or ""), step.get("model_tier"),
                                      rules["agents"].get(step.get("agent_id")), step.get("phase"))
        return tier

    def _external_guard(self, rid: str) -> Callable[[list[dict]], list[str]] | None:
        """validate_steps guard: contract staff that send data out stay off restricted-input steps (#424)."""
        settings = getattr(self.hub, "s", None)
        if settings is None or not any(z.level == "restricted" for z in settings.policy.data_zones):
            return None
        texts, dirs = self._request_inputs(rid)
        return lambda steps: external_contract_conflicts(steps, list(self.hub.agents.values()), texts, dirs, settings)

    def _request_restricted(self, rid: str | None) -> bool:
        """The request's own text, references or project folders touch a restricted zone (#424)."""
        if not rid or not self._external_guard(rid):
            return False
        texts, dirs = self._request_inputs(rid)
        return touches_restricted(texts, dirs, self.hub.s)

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
                await persist_request(self.hub, rid)
                return True
            check["decision"] = "pending"
            await persist_request(self.hub, rid)  # the plan and the pending card survive a restart together
            reason = check.get("reason", "").rstrip(" .") or "사유 없음"
            decision = await self.hub.request_approval(
                kind="scope", request_id=rid, summary=short(f"이 요청은 랩 범위 밖으로 보입니다: {reason}. 진행할까요?", 700),
                detail={"gate": "scope", "verdict": "out", "reason": check.get("reason", ""),
                        "steps": len((req.get("plan") or {}).get("steps") or [])})
            check["decision"] = ("proceed" if decision.get("approved") else
                                 "timed_out" if decision.get("state") == "timed_out" else "declined")
            await persist_request(self.hub, rid)
            if check["decision"] == "proceed":
                return True
        req["outcome"] = "out_of_scope_timed_out" if check["decision"] == "timed_out" else "out_of_scope_declined"
        why = "the approval timed out" if check["decision"] == "timed_out" else "the PI declined it"
        (await self._finish(rid, f"Out-of-scope request stopped: {why}; no step was dispatched.", {}, ok=False,
                     error="범위 밖 요청이라 실행하지 않았습니다"))
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

    def _bind_request_environments(self, rid: str, steps: list[dict]) -> list[dict]:
        """``_prepare_runner_environments`` for one request: the runners and staff it sees are the request's (#1204)."""
        self._environment_rid = rid
        try:
            return self._prepare_runner_environments(steps)
        finally:
            self._environment_rid = None

    def _prepare_runner_environments(self, steps: list[dict]) -> list[dict]:
        rid = getattr(self, "_environment_rid", None)
        agents = hub_agents(self.hub, rid)

        def can_create(agent_id: str) -> bool:
            agent = agents.get(agent_id) or {}
            if agent.get("engine") == "codex":
                return agent.get("sandbox") == "workspace-write"
            if agent.get("engine") == "claude_code":
                builtin = agent.get("builtin_tools")
                denied = agent.get("disallowed_tools") or []
                return (agent.get("permission_mode") != "plan"
                        and (builtin is None or "Bash" in builtin.split(","))
                        and not any(x.startswith("Bash") for x in denied))
            return False

        runners = getattr(self.hub, "agent_runner", {})
        online = getattr(self.hub, "runners", {})
        capabilities = getattr(self.hub, "runner_capabilities", {})
        live = {agent: runner for agent in runners if (runner := hub_runner(self.hub, agent, rid)) in online}
        runtimes = {runner: caps.get("runtime_env") for runner, caps in capabilities.items()
                    if isinstance(caps, dict)}
        result = split_runner_environments(steps, live, runtimes, can_create)
        validate_environment_bindings(result)
        return result

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

    def _step_revision(self, rid: str, step_id: str) -> int:
        """The revision a step turn is dispatched at: a review revise's own, or the gateway's floor for the step.

        A continuation round runs the same step ids again; the gateway keeps the first round's closed step at its
        revision and refuses anything not above it ("step ended before dispatch", #1053)."""
        pending = (self.hub.requests.get(rid, {}).get("pending_revisions", {}).get(step_id, {}).get("revision", 0))
        floor = getattr(self.hub, "step_revision_floor", None)
        return max(int(pending or 0), floor(rid, step_id) if floor else 0)

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

    @staticmethod
    def _facilities_fallback_answer(answer: dict) -> dict:
        """Keep the missing target visible even in adopted or legacy cached replies."""
        notice = ("시설팀(facilities)이 현재 roster에 없거나 재접속하지 않아 CSO가 대체 담당입니다. "
                  "시설팀에 다시 문의하지 마세요. 아래 답변의 시설팀 재문의 안내는 따르지 마세요. "
                  "CSO 답변은 시설팀 진단·수정·복구 성공을 뜻하지 않습니다. "
                  "해결 근거가 없으면 현재 단계를 중단하고 PI에게 지원 복구 판단을 요청하세요.")
        field = "answer" if answer.get("status") == "answered" else "reason"
        text = answer.get(field) or ""
        return {**answer, "requested_to": "facilities", "fallback_reason": "facilities_unavailable",
                field: text if text.startswith(notice) else notice + "\n\n" + text}

    def decide_restricted_call(self, call: dict) -> tuple[bool, str]:
        """One tool call a restricted task wants to make (#1008): (approved, reason shown to the staff member).

        Deterministic, no model call. It stops the task from reopening the loop the breaker closed: a call is judged
        once per restriction (the same call again is refused whatever the first answer was), at most
        ``RESTRICTED_APPROVAL_LIMIT`` calls pass, and an answer only exists for the restriction the runner named. A yes
        does not lift the restriction and does not skip the normal permission gate; the PI lifts it.
        """
        tid, episode, fingerprint = call.get("task_id"), call.get("episode"), call.get("fingerprint")
        known = self.hub.breaker_state(tid).get("episode") if tid else None
        if not (tid and episode and fingerprint and episode == known):
            return False, "제한 회차를 확인하지 못해 거절합니다"
        at = call.get("at")
        if not isinstance(at, (int, float)) or time.time() - at > RESTRICTED_STALE_S:
            # A request the runner's outbox replayed after a disconnect: its hook has long been refused, and judging it
            # now would spend this restriction's budget on a call that never ran. It is answered, not counted.
            return False, "답을 기다리던 호출이 아니라서 판정하지 않습니다"
        task = self.hub.store.get("task", tid) or {}
        if (task.get("completed") and not task.get("abandoned")) or                 (self.hub.requests.get(task.get("request_id") or "") or {}).get("cancelling"):
            return False, "이 작업은 이미 끝났거나 취소 중입니다"
        entry = self.restricted_calls.get(tid)
        if entry is None:
            saved = self.hub.store.get("restricted_ledger", tid) or {}
            entry = {"episode": saved.get("episode"), "seen": set(saved.get("seen") or []),
                     "approved": int(saved.get("approved") or 0)}
        if entry["episode"] != episode:
            entry = {"episode": episode, "seen": set(), "approved": 0}
        self.restricted_calls[tid] = entry

        def judged(approved: bool, why: str) -> tuple[bool, str]:
            self.hub.store.put("restricted_ledger", tid, {"episode": episode, "seen": sorted(entry["seen"]),
                                                          "approved": entry["approved"]})
            return approved, why

        if fingerprint in entry["seen"]:
            return False, "같은 호출을 이미 판정했습니다. 같은 일을 되풀이하지 말고 지금까지 한 것으로 마무리하거나 다른 방법을 쓰세요"
        entry["seen"].add(fingerprint)
        if entry["approved"] >= RESTRICTED_APPROVAL_LIMIT:
            return judged(False, f"이번 제한에서 허용하는 호출 {RESTRICTED_APPROVAL_LIMIT}번을 이미 썼습니다. 지금까지 한 것으로 마무리하세요")
        entry["approved"] += 1
        return judged(True, "이 호출 한 번만 허용합니다. 제한은 PI가 풀 때까지 유지됩니다")

    async def answer_ask(self, ask: AskRequest, runner_id: str) -> None:
        """Route one bounded question. Hard stops are classified before any model runs."""
        normalized_refs = [str(PurePosixPath(ref.replace("\\", "/"))) for ref in ask.refs]
        signature = hashlib.sha256(
            (f"{ask.task_id}\0{ask.to}\0{ask.question.strip().casefold()}\0"
             + json.dumps(normalized_refs, ensure_ascii=False, separators=(",", ":"))).encode("utf-8")
        ).hexdigest()
        entries = self.hub.store.all("ask")
        current = entries.get(ask.id) or {}
        automatic = bool(current.get("facilities_diagnosis"))
        scope = tuple(current["scope"]) if automatic else self._ask_scope(ask.model_dump())
        self.hub.store.put("ask", ask.id, {"ask": ask.model_dump(mode="json"), "origin": runner_id,
                                           **current, "signature": signature, "scope": scope})
        for other_id, entry in entries.items():
            if other_id != ask.id and entry.get("signature") == signature and entry.get("state") == "resolved":
                cached = ask_result(**{**entry["answer"], "cached": True})
                if ask.to == "facilities" and cached.get("routed_to") == "cso":
                    if "facilities" in self.hub.agents:
                        continue  # Support returned: the old fallback must not hide the available target.
                    cached = self._facilities_fallback_answer(cached)
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

        stop = None if automatic else hard_stop_kind(ask, self.hub.s.policy.bioinfo_agent.hard_stops)
        requested = ask.to
        # Who this ask went to before a gateway restart. The roster lacks that employee until its
        # runner reconnects, and the runner may still be answering it (#113).
        previous = current.get("routed_to")
        status = self.hub.requests.get(ask.request_id or "", {}).get("status")
        if automatic:
            host = self.hub.agent_runner.get("facilities")
            req = self.hub.requests.get(ask.request_id or "", {})
            if (req.get("cancelling") or host not in self.hub.runners or
                    "facilities" not in self.hub.agents or not self._diagnosis_budget_available(ask.request_id)):
                await self.hub.resolve_ask(ask, runner_id, ask_result(
                    reason="시설팀 진단을 시작할 수 없습니다(취소·offline·예산)", **{"from": "labhq"}))
                return
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
            answer = note or ("PI가 기한 안에 답하지 않았습니다(시간 초과)" if decision.get("state") == "timed_out" else
                              "PI가 진행을 허용하지 않았습니다" if not decision.get("approved") else
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

        fallback = requested == "facilities" and routed == "cso"

        async def resolve_consult(answer: dict) -> None:
            await self.hub.resolve_ask(ask, runner_id,
                                      self._facilities_fallback_answer(answer) if fallback else answer)

        if routed not in self.hub.agents:
            await resolve_consult(ask_result(
                reason=f"대상 직원 {routed!r}이 roster에 없습니다",
                **{"from": "labhq", "routed_to": routed}))
            return
        try:
            refusal = read_only_refusal(routed, self._engine(ask.request_id, routed))
        except ValueError as error:  # its request's CSO model cannot be honored: never answer as another model
            refusal = str(error)
        if refusal:  # a consult runs as the runner's read-only profile; an engine that ignores it could write
            await resolve_consult(ask_result(
                reason=refusal, **{"from": "labhq", "routed_to": routed}))
            return
        # The question and refs come from the asking task: restricted inputs there never reach staff that send data
        # out (#424). The runner's token says so; the request's own text and folders count as well.
        restricted = ask.source_restricted or self._request_restricted(ask.request_id)
        hosts = external_hosts(self.hub.agents.get(routed) or {})
        if not_yet_active(self.hub.agents.get(routed) or {}):
            await resolve_consult(ask_result(
                reason=probation_refusal(routed), **{"from": "labhq", "routed_to": routed}))
            return
        if restricted and hosts:
            await resolve_consult(ask_result(
                reason=consult_refusal(hosts), **{"from": "labhq", "routed_to": routed}))
            return

        # A task workdir belongs to its runner's filesystem. The gateway only forwards it when the
        # consult will run on that same runner; the runner validates the path and refs before launch.
        same_runner = hub_runner(self.hub, routed, ask.request_id) == runner_id
        refs = normalized_refs if same_runner else []
        source_workdir = ask.source_workdir if refs and ask.source_workdir else None
        reference_note = ("참고 파일은 다른 runner에 있어 읽을 수 없다"
                          if ask.refs and not same_runner else "")
        request = self.hub.requests.get(ask.request_id or "", {})
        prompt = CONSULT_PROMPT.format(
            scope_advice=("Diagnose environment failures only." if routed == "facilities" else
                          BIOINFO_ASSIGNMENT_RULE),
            sender=ask.agent_id, question=ask.question, why_blocked=ask.why_blocked,
            tried=json.dumps(ask.tried, ensure_ascii=False), options=json.dumps(ask.options, ensure_ascii=False),
            refs=json.dumps(refs, ensure_ascii=False), reference_note=reference_note,
            request=clip(request.get("text") or "", 4000), plan=clip(json.dumps(request.get("plan") or {},
                                                                                 ensure_ascii=False), 6000),
        )
        if fallback:
            prompt += ("\n\nThe requested target is facilities, but facilities is unavailable in the current roster "
                       "after any reconnect wait. You (CSO) are the actual fallback responder. "
                       "State that facilities is unavailable and that CSO is answering instead. "
                       "Do not ask or tell the employee to contact facilities again, including via a colleague ask. "
                       "Give only evidence-backed advice within existing permissions. If you cannot resolve the "
                       "blocker, tell the employee to stop the blocked step and ask PI to decide how to restore "
                       "support. Do not claim facilities diagnosis, repair or recovery occurred.")
        if routed == "facilities":
            if automatic:
                # No research request, plan, paths, artifacts or prior conversation enters automatic diagnosis.
                prompt = ask.question + "\n" + ask.why_blocked
            prompt += ("\n\nFacilities diagnosis only: use the supplied doctor snapshot and permitted error evidence. "
                       "Do not build a pipeline, run tools or write files. Reply briefly with cause, evidence and "
                       "what PI should do. Answer environment questions yourself; only authority, scope or policy "
                       "decisions belong to CSO/PI. Omit raw logs, tokens and personal paths.")
            if not same_runner:
                prompt += "\nThe doctor snapshot describes your runner, not necessarily the blocked employee's host."
        overrides = dict(READ_ONLY_OVERRIDES)
        async with self._consult_lock(ask.request_id, routed):
            # Re-read after acquiring the lock: another ask may have answered while we waited.
            # Publish the answer before releasing this same lock below.
            if fallback and any(
                    other_id != ask.id and (entry.get("ask") or {}).get("to") == "facilities" and
                    (entry.get("answer") or {}).get("routed_to") == "cso" and
                    (entry.get("answer") or {}).get("status") == "answered" and
                    tuple(entry.get("scope") or self._ask_scope(entry.get("ask") or {})) == scope
                    for other_id, entry in self.hub.store.all("ask").items()):
                await resolve_consult(ask_result(
                    reason="이 단계에는 이미 CSO 대체 답변이 있습니다. 부재한 시설팀으로 재문의하지 않습니다.",
                    **{"from": "labhq", "routed_to": routed}))
                return
            # After a gateway restart the runner may still be running this ask's consult.
            # Adopt its result; when its outcome is unknown, isolate the new consult.
            adopt = getattr(self.hub, "adopt_consult", None)
            prior, result = await adopt(ask.id, routed) if adopt else (0, None)
            if automatic and prior and result is None:
                await self.hub.resolve_ask(ask, runner_id, ask_result(
                    reason="이전 시설팀 진단 결과를 확인할 수 없어 다시 실행하지 않습니다", **{"from": "labhq"}))
                return
            first_attempt = 1
            if result is not None:
                self._count_adopted_cost(ask.request_id, result.task_id)
                if not automatic and failure_kind(result) == "transient" and prior < self.cfg.step_max_attempts:
                    result, first_attempt = None, prior + 1  # only the retries that are left
            elif prior:
                first_attempt = min(prior, self.cfg.step_max_attempts)  # its outcome was never observed
            if result is None:
                session_id, workdir = self._last_agent_session(ask.request_id, routed)
                if automatic or requested.startswith("colleague:"):
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
                          **({"facilities_diagnosis": True, "max_attempts": 1} if automatic else {}),
                          **({"restricted_inputs": True} if restricted else {}),
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
                    await persist_request(self.hub, ask.request_id)
            answered = result.ok and bool(result.text.strip())
            answer = result.text.strip() if answered else f"상담 실패: {result.error or 'empty answer'}"
            await resolve_consult(ask_result(
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
            await persist_request(self.hub, rid)
            if getattr(self.hub, "semantics_shadow", None) is not None:  # semantics-hook: actions
                self.hub.semantics_shadow.after_followup(rid, fid, "ended", outcome)  # semantics-hook: actions
            await self._emit(rid, "request.followup_done", {
                "id": fid, "ok": False, "answer": "", "error": reason,
                "cost_usd": float(req.get("cost_usd") or 0), "cost_known": req.get("cost_known", True),
                **_cost_summary_field(req), **_session_cost_fields(self.hub, req)})

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
        completed = [f for f in req.get("followups") or [] if f.get("id") != fid and f.get("status") == "done"]
        for followup in completed:
            if not (followup.get("result") or {}).get("workdir") and getattr(self.hub, "store", None):
                prior = self.hub.store.get("task", followup.get("task_id")) or {}
                if (prior.get("result") or {}).get("workdir"):
                    followup["result"] = {"workdir": prior["result"]["workdir"]}
        earlier = completed[-3:]
        history = "".join(f"\nEarlier follow-up: {f.get('text')}\nYour answer: {clip(f.get('answer') or '', 1500)}\n"
                          + (f"Answer workspace (read-only): {f['result']['workdir']}\n"
                             if (f.get("result") or {}).get("workdir") else "")
                          for f in earlier)
        outputs = [r["workdir"] for r in (req.get("results") or {}).values()
                   if isinstance(r, dict) and r.get("workdir") and r.get("outputs")]
        outputs += [f["result"]["workdir"] for f in completed if (f.get("result") or {}).get("workdir")]
        task = Task(agent_id=agent, request_id=rid, resume_session_id=session_id if resumable else None,
                    prompt=FOLLOWUP_PROMPT.format(
                        request=clip((req.get("text") or "") + render_references(req.get("references")), 4000),
                        report=clip(req.get("report") or "(no report)", 6000), history=history,
                        question=entry["text"]) + (
                            f"\nPrevious workspace (read-only): {workdir}\n"
                            "Resolve earlier output paths against each answer's recorded workspace. Save new files only in this "
                            "task's own outputs/ and .tmp/." if workdir else ""),
                    meta={**reference_meta(req), "kind": "followup", "followup_id": fid,
                          "title": f"이어 묻기: {entry['text'][:80]}", "request": req.get("text") or "",
                          "agent_overrides": dict(READ_ONLY_OVERRIDES),
                          "upstream_dirs": list(dict.fromkeys([*outputs, *([workdir] if workdir else [])])),
                          **({"source_workdir": workdir} if workdir else {}), **pin})
        self._restore_cost_cache(rid)
        try:
            result = await self.run_step(task)
        except ShutdownInterrupted as error:
            entry["shutdown_evidence"] = error.evidence
            result = TaskResult(task_id=task.id, agent_id=agent, ok=False,
                                error="runner 종료로 이어 묻기가 중단됐습니다. 연결된 뒤 다시 물어 주세요.",
                                shutdown_evidence=error.evidence)
        except BudgetExceeded as error:
            result = TaskResult(task_id=task.id, agent_id=agent, ok=False, error=str(error))
        except Exception as error:  # the follow-up must end in a recorded state, never stay "running"
            result = TaskResult(task_id=task.id, agent_id=agent, ok=False, error=f"{type(error).__name__}: {error}")
        answered = result.ok and bool(result.text.strip())
        status = "interrupted" if result.shutdown_evidence else "done" if answered else "failed"
        entry.update(status=status, answer=clip(result.text.strip(), 20000) if answered else "",
                     error=None if answered else (result.error or "empty answer"), task_id=result.task_id,
                     result={"workdir": result.workdir},
                     resumed_session=task.resume_session_id, answered_at=time.time())
        if not direct and result.session_id and self.hub.supports_resume(agent):
            req["cso_session_id"], req["cso_workdir"] = result.session_id, result.workdir or workdir
        await persist_request(self.hub, rid)
        if getattr(self.hub, "semantics_shadow", None) is not None:  # semantics-hook: actions
            self.hub.semantics_shadow.after_followup(rid, fid, "ended", status)  # semantics-hook: actions
        await self._emit(rid, "request.followup_done", {
            "id": fid, "ok": answered, "answer": clip(entry["answer"], 20000), "error": entry["error"],
            **({"status": status} if status == "interrupted" else {}),
            "cost_usd": float(req.get("cost_usd") or 0), "cost_known": req.get("cost_known", True),
            **_cost_summary_field(req), **_session_cost_fields(self.hub, req)})

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

    def _count_recorded_costs(self, rid: str) -> None:
        """Include nested conversion turns and parallel completions, deduplicated by task ID."""
        req = self.hub.requests.get(rid) or {}
        for tid in set(req.get("cost_items") or {}) | set(req.get("cost_by_task") or {}):
            self._count_cost(rid, tid)

    def _restore_cost_cache(self, rid: str) -> None:
        """Restore aggregate and its task identities together, without adding them twice."""
        req = self.hub.requests.get(rid) or {}
        self.cost[rid] = max(self.cost.get(rid, 0.0), float(req.get("cost_usd") or 0))
        self.cost_tasks.setdefault(rid, set()).update(
            set(req.get("cost_by_task") or {}) | set(req.get("cost_items") or {}))
        self._seed_cost(rid, req)

    # ---------- one agent step, including HPC hibernate/wake cycles ----------
    def _with_research_round(self, task: Task) -> Task:
        """A continuation round's tasks carry ``research_round`` (2 and up), so restart recovery never hands a step,
        plan or review the result of the same id from an earlier round (gateway ``_matches_recovery``)."""
        contract = ((self.hub.requests.get(task.request_id or "") or {}).get("research_contract") or {})
        round_no = int(contract.get("round") or 1)
        meta = dict(task.meta)
        if round_no > 1:
            meta.setdefault("research_round", round_no)
        carried = contract.get("continuation") or {}
        retry = carried.get("refusal_retry") or {}
        if carried.get("round") == round_no and retry.get("approval_id"):
            # A new explicit model choice must not adopt the refused plan from the same round. A restart of this
            # very choice still adopts its own durable tasks under the existing recovery/budget identity rules.
            meta["continuation_refusal_recovery"] = retry["approval_id"]
        return task.model_copy(update={"meta": meta}) if meta != task.meta else task

    async def run_step(self, task: Task, first_attempt: int = 1) -> TaskResult:
        task = self._with_research_round(self._with_request_identity(task))
        rid = task.request_id or ""
        initial_attempt = first_attempt
        engine = str((hub_agents(self.hub, rid).get(task.agent_id) or {}).get("engine") or "")
        final_step = bool(task.meta.get("final_budget_turn"))  # nothing runs after this step but its own turns
        optional_support = bool(task.meta.get("facilities_diagnosis") or task.meta.get("kind") in
                                {"paper_skill", "recruit_check", "plan_recruit"})

        async def quota_deadline() -> float:
            req = self.hub.requests[rid]
            windows = req.setdefault("quota_windows", {})
            if engine not in windows:
                started = time.time()
                windows[engine] = {"started_at": started,
                                   "deadline_at": started + self.cfg.quota_max_wait_s}
                await persist_request(self.hub, rid)
            return float(windows[engine]["deadline_at"])

        async def clear_quota_window() -> None:
            req = self.hub.requests.get(rid) or {}
            if (req.get("quota_windows") or {}).pop(engine, None) is not None:
                if not req.get("quota_windows"):
                    req.pop("quota_windows", None)
                await persist_request(self.hub, rid)

        def quota_failure(current: Task, reason: str) -> TaskResult:
            return TaskResult(task_id=current.id, agent_id=current.agent_id, ok=False,
                              error_kind="quota_wait_limit", error=reason)

        async def login_deadline(current: Task) -> float:
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
                await persist_request(self.hub, rid)
            window = windows[engine]
            if turn_key(current) not in (window.get("turns") or []):
                window["turns"] = [*(window.get("turns") or []), turn_key(current)]
                await persist_request(self.hub, rid)
            return float(window["deadline_at"])

        def turn_key(current: Task) -> str:
            """One logical turn, the same through its retries and wake turns (they copy the meta). Consults to
            different agents run in parallel under one kind, so each ask is its own turn (PR #401 review)."""
            base = str(current.meta.get("step_id") or current.meta.get("kind") or current.id)
            ask_id = current.meta.get("ask_id") if not current.meta.get("step_id") else None
            return f"{base}:{ask_id}" if ask_id else base

        async def leave_login_window(current: Task) -> None:
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
                    await persist_request(self.hub, rid)
                return
            req["login_windows"].pop(engine, None)
            if not req.get("login_windows"):
                req.pop("login_windows", None)
            await persist_request(self.hub, rid)

        async def login_failure(current: Task, reason: str) -> TaskResult:
            (await leave_login_window(current))
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
                if optional_support:
                    if not self._diagnosis_budget_available(rid):
                        raise BudgetExceeded("facilities diagnosis budget exhausted")
                else:
                    await self._check_budget(rid)
                login_hold = getattr(self.hub, "login_hold", lambda _engine: None)(engine)
                if optional_support and login_hold:
                    return TaskResult(task_id=current.id, agent_id=current.agent_id, ok=False,
                                      error="facilities engine login required")
                if login_hold:
                    deadline = (await login_deadline(current))
                    if not await self.hub.wait_login(rid, key, engine,
                                                     resume_at=float(login_hold["resume_at"]),
                                                     deadline_at=deadline,
                                                     reason="same engine account is waiting for login",
                                                     agent_id=current.agent_id):
                        return await login_failure(current, "engine login wait exceeded the configured maximum")
                    await self._check_budget(rid)
                hold = getattr(self.hub, "quota_hold", lambda _engine: None)(engine)
                if optional_support and hold:
                    return quota_failure(current, "facilities engine quota unavailable")
                if hold:
                    deadline = (await quota_deadline())
                    if not await self.hub.wait_quota(rid, key, engine,
                                                     resume_at=float(hold["resume_at"]),
                                                     deadline_at=deadline,
                                                     reason="same engine account is waiting for quota"):
                        return quota_failure(current, "subscription quota wait exceeded the configured maximum")
                    await self._check_budget(rid)  # a parallel step may have spent or been denied it meanwhile
                self.attempts.setdefault(rid, {})[key] = self.attempts.get(rid, {}).get(key, 0) + 1
                attempt_task = current.model_copy(update={"id": current.id if attempt == 1 else new_id("task"),
                                                  "resume_session_id": previous_session,
                                                  "request_budget_remaining_usd": (
                                                      current.request_budget_remaining_usd if optional_support else
                                                      self._request_budget_remaining(rid)),
                                                  # The runner makes one attempt and reports a StructuredOutput format
                                                  # error; dispatch_turn sends the repair as its own dispatch (#673).
                                                  "meta": {**current.meta, "attempt": attempt,
                                                           GATEWAY_RETRY_META: GATEWAY_RETRY,
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
                if current.meta.get('kind') == 'step':
                    req = self.hub.requests.get(rid) or {}
                    contract = req.get('research_contract') or {}
                    carried = contract.get('continuation') or {}
                    if (carried.get('plan_sha256')
                            and carried['plan_sha256'] == contract.get('plan_sha256')):
                        if not await self._verify_research_reuse(rid, req['plan']):
                            raise research_continuation.ReuseApprovalRequired('reuse proof changed; renewed CP1 required')
                        if not carried.get('dispatch_started'):
                            carried['dispatch_started'] = True
                            await persist_request(self.hub, rid)
                if current.meta.get('kind') in {'step', 'result_correction', 'slot_repair'} and current.meta.get('paper_skills'):
                    attempt_task = await self._resolve_step_paper_skills(attempt_task)
                if current.meta.get('kind') == 'paper_skill':
                    selected = current.meta['paper_skill']
                    plan = (self.hub.requests.get(rid) or {}).get('plan') or {}
                    approved = paper_skills.attachments(plan, selected['step_id'], selected['agent_id'], rid)
                    if selected not in approved:
                        (await self._check_paper_selection(rid, [selected]))  # rejects drift, never substitutes a paper
                    (await self._check_paper_selection(rid, approved))  # after queued/attempt-event awaits, before dispatch
                offline = False
                # Observe the configuration at the actual dispatch boundary, including every retry/wake/repair.
                # A mixed or unproved execution must never inherit the configuration shown before CP1 waited.
                execution_req = self.hub.requests.get(rid) or {}
                execution_step = (current.meta.get('step_id') if current.meta.get('kind') in
                                  {'step', 'result_correction', 'slot_repair'} else None)
                execution_signature = None
                if execution_step and execution_req.get('research_contract'):
                    execution_signature = self._research_reuse_tools(rid, execution_req.get('plan') or {}).get(
                        execution_step)
                    recorded = execution_req.setdefault('reuse_tools', {})
                    if execution_step not in recorded:
                        recorded[execution_step] = execution_signature
                    elif recorded[execution_step] != execution_signature:
                        recorded[execution_step] = None  # sticky: this node ran with mixed configurations
                    # Request references/project directories reach every step, even without input_refs.
                    # Record absence at execution, never infer it later from mutable request metadata.
                    absence = execution_req.setdefault('reuse_no_request_inputs', {})
                    absence[execution_step] = (absence.get(execution_step, True) is True
                        and not research_continuation.has_request_inputs(execution_req)
                        and not research_continuation.has_request_inputs(attempt_task.meta))
                    from ..research.input_proof import capture
                    execution_inputs = await asyncio.to_thread(capture, execution_req, self.hub.s, attempt_task.meta)
                    attempt_task = attempt_task.model_copy(update={'meta': {**attempt_task.meta,
                        'reuse_input_request': {key: copy.deepcopy(execution_req.get(key) or [])
                            for key in ('references', 'reference_dirs', 'project_dirs')},
                        'reuse_input_meta': {key: copy.deepcopy(attempt_task.meta.get(key) or [])
                            for key in ('reference_dirs', 'project_dirs')}}})
                    proofs = execution_req.setdefault('reuse_input_proof', {})
                    if execution_step not in proofs:
                        proofs[execution_step] = execution_inputs
                    elif proofs[execution_step] != execution_inputs:
                        proofs[execution_step] = None  # sticky mixed-input attempts
                    await persist_request(self.hub, rid)
                # Last await before the send: hold this attempt's whole cap now (#862, opt-in).
                attempt_task = await self._reserve_dispatch(rid, attempt_task, optional=optional_support)
                self._note_inflight_cap(rid, attempt_task)
                try:
                    dispatched = (attempt_task if current.meta.get("facilities_diagnosis") else
                                  with_pi_notes(attempt_task, self.hub.requests.get(rid) or {}))
                    tracked = getattr(self.hub, 'dispatch_with_identity', None)
                    if current.meta.get('kind') == 'paper_skill' and tracked is not None:
                        res = await tracked(dispatched, lambda tid: self._bind_paper_dispatch_identity(
                            rid, current.id, tid))
                    else:
                        res = await self.hub.dispatch(dispatched)
                    res = paper_skills.with_preparation_outcome(res, attempt_task)
                except asyncio.CancelledError:
                    (await self._release_if_unsent(rid, attempt_task.id))
                    raise
                except Exception as exc:
                    (await self._release_if_unsent(rid, attempt_task.id))
                    res = TaskResult(task_id=attempt_task.id, agent_id=current.agent_id, ok=False, error=str(exc))
                    kind = failure_kind(exc, engine)
                    offline = isinstance(exc, RunnerUnavailable)
                    if execution_step and execution_req.get('research_contract'):
                        execution_req['reuse_tools'][execution_step] = None
                        execution_req['reuse_no_request_inputs'][execution_step] = False
                        execution_req['reuse_input_proof'][execution_step] = None
                        await persist_request(self.hub, rid)
                else:
                    if execution_step and execution_req.get('research_contract'):
                        after_inputs = await asyncio.to_thread(capture, execution_req, self.hub.s, dispatched.meta)
                        if (after_inputs != execution_inputs or execution_inputs is None
                                or res.request_input_sha256 != execution_inputs['sha256']):
                            execution_req['reuse_input_proof'][execution_step] = None
                        await persist_request(self.hub, rid)
                        after = self._research_reuse_tools(rid, execution_req.get('plan') or {}).get(execution_step)
                        if (research_continuation.has_request_inputs(execution_req)
                                or research_continuation.has_request_inputs(dispatched.meta)):
                            execution_req['reuse_no_request_inputs'][execution_step] = False
                            await persist_request(self.hub, rid)
                        if after != execution_signature or res.task_id != attempt_task.id:
                            execution_req['reuse_tools'][execution_step] = None
                            await persist_request(self.hub, rid)
                    if is_model_refusal(res):
                        res = res.model_copy(update={"ok": False, "error_kind": "model_refused",
                                                     "error": MODEL_REFUSAL_REASON})
                    kind = failure_kind(res, engine)
                    if kind != "login":  # a rejected login never starts a billed model turn
                        self._count_cost(rid, res.task_id, res)
                    self._count_recorded_costs(rid)
                    (await self._release_dispatch_hold(rid, attempt_task.id))
                    previous_workdir = res.workdir or previous_workdir
                    if res.session_id and self.hub.supports_resume(current.agent_id):
                        previous_session = res.session_id
                    res = merged_workspace_rerun(previous_result, res)
                    if kind == "environment":  # the record the step result, its event and the report show (#35)
                        res = res.model_copy(update={"environment": environment_problem(res, engine)})
                finally:
                    self.dispatch_inflight.get(rid, set()).discard(attempt_task.id)  # #862: this send is over here
                    (self.inflight_caps.get(rid) or {}).pop(attempt_task.id, None)
                evidence = res.shutdown_evidence or {"exit_code": res.exit_code, "error": res.error,
                    "stopping": bool(getattr(self.hub, "stopping", False)) or stopping_marker(
                        self.hub.s, getattr(self.hub, "session_started_at", time.time())), "source": "gateway"}
                if not res.ok and shutdown_interruption(exit_code=evidence.get("exit_code"),
                        error=evidence.get("error"), stopping=evidence.get("stopping"),
                        cancelled=evidence.get("cancelled")):
                    evidence = {**evidence, "task_id": res.task_id, "kind": current.meta.get("kind"), "at": time.time()}
                    raise ShutdownInterrupted(evidence)
                if res.tool_errors:
                    tool_errors = list(dict.fromkeys([*tool_errors, *res.tool_errors]))
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
                # A finished turn of the last step is judged once run_step knows no wake turn follows it: one that
                # still waits on an answer or job is not final, and the next turn's gate decides (PR #527 review).
                if not optional_support and not (kind is None and final_step):
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
            if optional_support:
                return res  # diagnosis itself never parks on login/quota or invokes another recovery
            tool_errors = list(res.tool_errors)
            format_tries = 0
            while True:
                if (res.error_kind == "structured_output_format" and res.format_retry
                        and format_tries < self.hub.s.runner.structured_output_retries and res.session_id):
                    # #673: the repair is a new dispatch of the same session, so it passes the request budget gate (and
                    # its reservation) with every other turn instead of being decided inside one runner turn. res holds
                    # the cost of every attempt so far (see _sum_repair_turns).
                    format_tries += 1
                    known = res.cost_known is not False and res.cost_usd is not None
                    cap = next((float(v) for v in (turn.budget_usd,
                                                   (hub_agents(self.hub, turn.request_id).get(turn.agent_id) or {}).get("max_budget_usd"),
                                                   self.hub.s.policy.budget.per_task_usd) if _usd_amount(v)), None)
                    left = None if cap is None or not known else cap - res.cost_usd
                    request_left = self._request_budget_remaining(rid)
                    if request_left is not None:
                        request_left -= self._inflight_unheld(rid)  # what parallel siblings may still spend
                    if (not known or (left is not None and left <= 0)
                            or (request_left is not None and request_left <= 0) or rid in self.budget_denials):
                        return res  # an automatic repair never asks the PI for more budget: stop with the failure
                    current = current.model_copy(update={
                        "id": new_id("task"), "prompt": repair_prompt(res.format_retry), "context": "",
                        "resume_session_id": res.session_id,
                        **({"budget_usd": left} if left is not None else {}),
                        "meta": {**current.meta, "kind": current.meta.get("kind", "step"), "parent_task": res.task_id,
                                 **({"workdir": res.workdir} if res.workdir else {})},
                    })
                    earlier = res
                    try:
                        res = _sum_repair_turns(earlier, merged_turn(earlier, await dispatch_with_retry(
                            current, max_attempts)))
                    except BudgetExceeded:
                        return earlier  # denied before the repair was sent; the failed turn keeps its recorded cost
                    tool_errors.extend(res.tool_errors)
                    res = res.model_copy(update={"tool_errors": list(dict.fromkeys(tool_errors))})
                    continue
                if res.error_kind == "halted":  # #59 ①: parked until the PI resumes, then the same session goes on
                    wait_halted = getattr(self.hub, "wait_halted", None)
                    if wait_halted is None or not await wait_halted(rid, turn_key(current), res):
                        return res
                    can_resume = bool(res.session_id and self.hub.supports_resume(current.agent_id))
                    current = current.model_copy(update={
                        "id": new_id("task"),
                        "prompt": continuation_prompt(turn, HALT_RESUME_PROMPT, resumable=can_resume,
                                                      previous_result=res,
                                                      context_chars=self.cfg.context_chars_per_step),
                        "meta": {**current.meta, "kind": current.meta.get("kind", "step"),
                                 "parent_task": res.task_id,
                                 **({"workdir": res.workdir} if res.workdir else {})},
                        "resume_session_id": res.session_id if can_resume else None,
                    })
                    earlier = res
                    res = merged_workspace_rerun(earlier, await dispatch_with_retry(current, max_attempts))
                    tool_errors.extend(res.tool_errors)
                    res = res.model_copy(update={"tool_errors": list(tool_errors)})
                    continue
                login = not res.ok and is_login_error(engine, res.error)
                if login:
                    deadline = (await login_deadline(current))
                    window = self.hub.requests[rid]["login_windows"][engine]
                    if window.get("task_id") == res.task_id:
                        resume_at = float(window["resume_at"])
                    else:
                        resume_at = time.time() + self.cfg.login_retry_s
                        window.update(task_id=res.task_id, resume_at=resume_at)
                        await persist_request(self.hub, rid)
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
                    earlier = res
                    res = merged_workspace_rerun(earlier, await dispatch_with_retry(current, max_attempts))
                    tool_errors.extend(res.tool_errors)
                    res = res.model_copy(update={"tool_errors": list(dict.fromkeys(tool_errors))})
                    continue
                recovered = getattr(self.hub, "login_recovered", None)
                if recovered is not None:
                    await recovered(engine)
                (await leave_login_window(current))
                quota = None if res.ok else received_quota_wait(engine, res.error, res.quota_reset_at,
                                                                 default_wait_s=self.cfg.quota_default_wait_s)
                if quota is None:
                    (await clear_quota_window())
                    return res
                deadline = (await quota_deadline())
                window = self.hub.requests[rid]["quota_windows"][engine]
                if window.get("task_id") == res.task_id:
                    resume_at = float(window["resume_at"])
                else:
                    resume_at = quota.resume_at
                    window.update(task_id=res.task_id, resume_at=resume_at, reset_time_parsed=quota.parsed)
                    await persist_request(self.hub, rid)
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
                earlier = res
                res = merged_workspace_rerun(
                    earlier, await dispatch_with_retry(current, max_attempts))  # its first gate rechecks the budget
                tool_errors.extend(res.tool_errors)
                res = res.model_copy(update={"tool_errors": list(dict.fromkeys(tool_errors))})

        res = await dispatch_turn(task, max_attempts=task.meta.get("max_attempts"), start=initial_attempt)
        if task.meta.get("kind") in {"paper_skill", "recruit_check", "plan_recruit"}:
            return res  # optional advice never adds wake/wrap-up turns or delays CP1 on a failed conversion
        if task.meta.get("facilities_diagnosis"):
            return res  # one consult turn, even if a malformed reply claims pending jobs/questions
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
                        **merged_tool_call_update(res, retried),
                    })
        diagnose = getattr(self.hub, "request_facilities_diagnosis", None)
        if (environment and not propose and not read_only and not res.facilities_fix and
                task.meta.get("kind") in {"step", "direct"} and not task.meta.get("facilities_rerun") and
                callable(diagnose)):
            await diagnose(task, res, turn_key(task))
        overrides = task.meta.get("agent_overrides") or {}
        # A read-only task (consult, follow-up) has nothing to save, and a wrap-up must never lift its limits.
        turn_limit = int(overrides.get("max_turns") or (hub_agents(self.hub, task.request_id).get(task.agent_id) or {}).get("max_turns") or 0)
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
                        meta=auxiliary_turn_meta(
                            task.meta, "wrap_up", ["PARTIAL_STATUS.md"], parent_task=res.task_id,
                            workdir=res.workdir,
                            agent_overrides={**overrides,
                                             "max_turns": min(WRAP_TURNS, turn_limit or WRAP_TURNS)},
                            collect_direct_outputs=task.meta.get("kind") == "direct"))
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
                                             "output_types": {**{path: record for path, record in
                                                                 res.output_types.items() if path not in rewritten},
                                                              **partial.output_types},
                                             "output_types_stats": (partial.output_types_stats or
                                                                    res.output_types_stats),
                                             "tool_errors": [*res.tool_errors, *partial.tool_errors],
                                             **merged_tool_call_update(res, partial),
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
                    res = rejected_step(res, outcome["reason"])
                    break
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
        if final_step:  # every turn of the last step has run: an overrun now is recorded, not asked about
            await self._check_budget(rid, block=False, final_turn=True)
        if res.error_kind == "structured_output_format":
            await self.hub.request_approval(
                kind="clarify", request_id=rid, summary="직원 응답의 형식 오류로 자동 재시도를 마쳤습니다.",
                detail={"questions": [{"question": "형식 오류로 결과를 받지 못했습니다. 요청을 멈춥니다.",
                                       "options": ["확인"], "allow_free_text": False}],
                        "tool_errors": res.tool_errors, "error": res.error})
        return res

    async def _check_budget(self, rid: str, *, block: bool = True, final_turn: bool = False,
                            reserve: float = 0.0) -> None:
        # A completed attempt keeps its result; a denied budget only blocks the next attempt. ``reserve`` is the cap
        # a pre-dispatch reservation still has to fit (#862); 0 keeps the judgement on what is already held.
        async with self.budget_locks.setdefault(rid, asyncio.Lock()):
            self._count_recorded_costs(rid)
            if rid in self.budget_denials:
                if block:
                    raise BudgetExceeded(self.budget_denials[rid])
                return
            req = self.hub.requests.get(rid, {})
            limit = req.get("budget_usd") or self.hub.s.policy.budget.per_request_usd
            if not limit:
                return
            if self.hub.s.policy.budget.reserve_before_dispatch and not _usd_amount(limit):
                # #862: a cap that is not an amount never admits work or asks the PI to raise it.
                reason = f"invalid request budget {limit!r}; nothing was dispatched"
                self.budget_denials[rid] = reason
                if block:
                    raise BudgetExceeded(reason)
                return
            spent, unknown, bound = self._budget_bound(rid, limit)
            if bound + reserve <= limit:
                return
            over = spent > limit
            if final_turn:
                outcome = {"spent_usd": round(spent, 4), "limit_usd": limit, "approved": None,
                           "final_turn": True, **({"unknown_count": unknown} if unknown else {})}
                self.budget_outcomes.setdefault(rid, []).append(outcome)
                await self._emit(rid, "request.budget_exceeded", outcome)
                return
            pending = f" + 비용 모름 {unknown}건" if unknown else ""
            reserve_each, needed = self._unknown_reserve(limit), reserve
            # With pre-dispatch reservations the raised cap leaves room for one more task reservation: at needed = 0 a
            # cap equal to the bound would make the very next dispatch open a second card (#940, attempt 9).
            # A disabled per-task cap (0) makes the reserve the request cap itself, which grows with every raise: no
            # fixed room can be left then, so the cap stays as before.
            budget_policy = self.hub.s.policy.budget
            headroom = reserve_each if budget_policy.reserve_before_dispatch and budget_policy.per_task_usd > 0 else 0.0
            raised = max(limit * 2, bound + max(needed, headroom))
            # What each answer does, so the PI need not read detail.requested_budget_usd (trial 2026-10-08).
            then = f" 승인하면 상한을 ${raised:.2f}로 올리고, 거절하면 새 작업을 보내지 않습니다."
            summary = (f"예산 초과: ${spent:.2f}{pending} / ${limit:.2f} — 계속 진행할까요?{then}" if over else
                       f"지금까지 ${spent:.2f} 썼고 진행 중인 작업 예약을 더하면 ${bound:.2f}입니다. 다음 작업 상한 "
                       f"${needed:.2f}를 미리 잡으면 상한 ${limit:.2f}를 넘습니다.{then}"
                       if needed > 0 and bound <= limit else
                       f"지금까지 ${spent:.2f} 썼고, 비용을 아직 모르는 작업이 {unknown}건 있어 "
                       f"상한 ${limit:.2f}를 넘을 수 있습니다 "
                       f"(모르는 작업을 건당 ${reserve_each:.2f}로 잡으면 ${bound:.2f}).{then}")
            try:
                detail = {"spent_usd": spent, "limit_usd": limit, "requested_budget_usd": raised,
                          **({"unknown_count": unknown, "unknown_reserve_usd": reserve_each} if unknown else {}),
                          **({"reserve_usd": needed} if needed > 0 else {})}
                dec = await self.hub.request_approval(kind="budget", request_id=rid, summary=summary, detail=detail)
            except Exception as exc:
                dec = {"approved": False, "note": str(exc)}
            # include concurrently completed attempts in this decision
            spent, unknown, bound = self._budget_bound(rid, limit)
            approved = bool(dec.get("approved"))
            timed_out = not approved and dec.get("state") == "timed_out"  # no answer is not a deny (R5)
            outcome = {"spent_usd": round(spent, 4), "limit_usd": limit, "approved": approved,
                       **({"timed_out": True} if timed_out else {}),
                       **({"unknown_count": unknown} if unknown else {})}
            self.budget_outcomes.setdefault(rid, []).append(outcome)
            await self._emit(rid, "request.budget_exceeded", outcome)
            if approved:
                self.hub.requests[rid]["budget_usd"] = max(limit * 2, bound + max(needed, headroom))
            else:
                pending = f" + {unknown} unaccounted task(s)" if unknown else ""
                verdict = "no PI answer before the card timed out" if timed_out else "approval denied"
                reason = (f"budget exceeded (${spent:.2f}{pending} > ${limit:.2f}); {verdict}" if over else
                          f"budget reservation (${bound:.2f} held + ${needed:.2f} next turn > ${limit:.2f}); {verdict}"
                          if needed > 0 and bound <= limit else
                          f"budget unaccounted (${spent:.2f}{pending} may pass ${limit:.2f}); {verdict}")
                self.budget_denials[rid] = reason
                state = (getattr(self.hub, "requests", None) or {}).get(rid)
                if timed_out and state is not None and not state.get("research_contract"):
                    state["outcome"] = "budget_timed_out"
                if block:
                    raise BudgetExceeded(reason)

    def _alphagenome_ready(self, agent: dict | None = None) -> bool:
        """Whether the AlphaGenome tools exist for the staff, as the runners say at registration (``alphagenome_ready`` in
        their capabilities): a runner registers them only with the key file and the client, and it may run on another
        account or PC than the gateway, so the gateway's own look would drop tools a runner has.

        For one agent (a roster row, which carries the ``runner_id`` that serves it, a request's WSL pin included) it is
        that runner's report: a runner that does not serve the agent says nothing about it. Without an agent, any runner
        that reports ready is enough. A runner that has reported nothing (an older runner) leaves the gateway to look
        for itself, as before: the roster the CSO plans from must not list the tools when nothing can serve them (#1200)."""
        capabilities = getattr(self.hub, "runner_capabilities", None) or {}
        runner = (agent or {}).get("runner_id")
        if runner is not None:
            caps = capabilities.get(runner)
            if isinstance(caps, dict) and "alphagenome_ready" in caps:
                return caps["alphagenome_ready"] is True
        else:
            reports = [caps["alphagenome_ready"] for caps in capabilities.values()
                       if isinstance(caps, dict) and "alphagenome_ready" in caps]
            if reports:
                return any(report is True for report in reports)
        try:
            from ..tools import annot_keys, annot_regulatory
            return bool(annot_keys.read_key(self.hub.s)) and annot_regulatory.alphagenome_client_available()
        except Exception:
            return False

    def _note_inflight_cap(self, rid: str, task: Task) -> None:
        """Remember the most this dispatch may spend while it runs (#673). Nothing is recorded for a task without a cap."""
        remaining = self._request_budget_remaining(rid)
        cap = self._turn_cap(task, remaining if remaining is not None else 0.0)
        if cap is not None and cap > 0:
            self.inflight_caps.setdefault(rid, {})[task.id] = cap

    def _inflight_unheld(self, rid: str) -> float:
        """The caps of turns in flight that hold nothing in the request's budget_holds (reservations count already)."""
        held = set((self.hub.requests.get(rid) or {}).get("budget_holds") or {})
        return sum(cap for tid, cap in (self.inflight_caps.get(rid) or {}).items() if tid not in held)

    def _request_budget_remaining(self, rid: str) -> float | None:
        """Request allowance still available to a runner call after recorded and reserved costs."""
        req = self.hub.requests.get(rid, {})
        limit = req.get("budget_usd") or self.hub.s.policy.budget.per_request_usd
        if not limit:
            return None
        _spent, _unknown, bound = self._budget_bound(rid, float(limit))
        return max(0.0, float(limit) - bound)

    def _apply_plan_cost_estimate(self, rid: str, plan: dict) -> dict:
        """Use completed request costs for the total estimate; preserve the plan estimate when none qualify (#708)."""
        estimate = historical_plan_cost_estimate(self.hub.requests, plan, hub_agents(self.hub, rid),
                                                 exclude_request_id=rid)
        if estimate.get("basis") == "historical" and estimate.get("estimate_usd") is not None:
            # budget_usd is post-CP1 spending. The historical estimate is the whole request, so subtract the planning
            # spend already recorded; keep a cent when the observed total has already been reached.
            remaining = max(float(estimate["estimate_usd"]) - float(self.cost.get(rid, 0.0)), 0.01)
            planned = plan.get("budget_usd")
            planned_amount = (float(planned)
                              if isinstance(planned, (int, float)) and not isinstance(planned, bool) else None)
            plan = {**plan, "budget_usd": round(max(remaining, planned_amount or 0.0), 2)}
        else:
            planned = plan.get("budget_usd")
            amount = (float(planned) if isinstance(planned, (int, float)) and not isinstance(planned, bool)
                      else None)
            estimate = {**estimate, "basis": "fallback", "estimate_usd": amount}
        self.hub.requests[rid]["plan_cost_estimate"] = estimate
        return plan

    def _research_budget(self, rid: str, req: dict, plan: dict) -> tuple[dict[str, Any], str]:
        """The spending cap a CP1 approval of ``plan`` enforces (PI 점검 R17), and where its configured cap came from.

        The plan's budget_usd is what execution may spend, so the cap is what the request has spent by this CP1 (the
        briefing, precedent, clarify and planning turns, and an earlier round's work in a continuation) plus that
        budget (trial 2026-10-12: a $3.00 plan cut the cap to $3.00 while $3.16 was already spent, and a budget card
        opened 4 minutes after approval). The configured cap is the request's own budget, else policy per_request_usd.
        It is fixed when a plan budget is first applied (``research_contract.budget_cap``), so a later round's plan is
        held to it and not to an earlier round's plan budget, and it stays the hard ceiling: a cap above it is cut to
        it. A plan without ``budget_usd`` keeps the request's current cap; the card shows that cap either way."""
        policy = float(self.hub.s.policy.budget.per_request_usd or 0)
        own = float(req.get("budget_usd") or 0)
        fixed = (req.get("research_contract") or {}).get("budget_cap") or {}
        if fixed:
            configured, source = float(fixed.get("configured_cap_usd") or 0), str(fixed.get("source") or "request")
        else:
            # A limit a budget card raised before CP1 is still the lab's policy cap: only a budget the PI wrote on the
            # request (kept in requested_budget_usd; older requests have only budget_usd) is the cap itself.
            asked = req.get("requested_budget_usd", req.get("budget_usd"))
            configured, source = (own, "request" if asked else "policy") if own else (policy, "policy")
        planned = plan.get("budget_usd")
        if not planned:
            return {"cap_usd": own or policy, "source": "request" if own else "policy",
                    "configured_cap_usd": configured}, source
        planned = float(planned)
        spent = round(self.cost.get(rid, 0.0), 4)
        budget = {"plan_budget_usd": planned, "spent_before_plan_usd": spent, "cap_usd": round(spent + planned, 4),
                  "source": "plan", "configured_cap_usd": configured}
        if configured and (source == "request" or configured < budget["cap_usd"]):
            # A budget the PI wrote on the request is the cap whatever the plan estimates (#944: a $40 request fell to
            # the plan's $9.70, so two tasks' reservations already passed it). Only the lab's policy cap is cut to
            # the plan's own sum.
            budget.update(cap_usd=configured, source=source)
        return budget, source

    def resume_plan_card(self, rid: str, req: dict) -> dict[str, Any] | None:
        """What a resume card shows when it also stands for CP1 (#654), else None.

        Only a first research plan with no result yet and no CP1 receipt for its current hash qualifies. A
        continuation, a plan after steps ran, or a hash that differs from the receipt keep their own CP1 card."""
        plan = req.get("plan")
        contract = req.get("research_contract") or {}
        if (not isinstance(plan, dict) or not plan.get("steps") or req.get("results") or contract.get("continuation")
                or not isinstance(plan.get("protocol"), dict) or not isinstance(plan.get("intake"), dict)):
            return None
        if refresh_plan_approval(plan, contract.get("approval")).get("status") == "approved":
            return None
        # What the CP1 card shows beyond this card's text keeps CP1 as its own question (see finish_research_plan).
        if paper_skills.card_text(plan) or any("재배정" in w for w in plan.get("warnings") or [] if isinstance(w, str)):
            return None
        # A restarted gateway has an empty ledger: seed the stored cost as run_request does, so the cap shown here
        # is the cap CP1 will enforce (#654 review).
        self._restore_cost_cache(rid)
        budget, _ = self._research_budget(rid, req, plan)
        steps = [f"{step['id']}·{step['agent_id']}: {short(' '.join(str(step['instruction']).split()), 40)}"
                 for step in plan["steps"]]
        tail = (f"\n{_cost_estimate_line(req.get('plan_cost_estimate') or {})} {_budget_line(budget)}"
                "\n승인하면 아래 동결 계획(CP1)도 승인한 것으로 기록하고 CP1 카드를 다시 묻지 않습니다. "
                "계획이 바뀌거나 의미 장치가 멈춰 있으면 CP1을 다시 묻습니다.")
        warnings = [w for w in plan.get("warnings") or [] if isinstance(w, str)]
        if warnings:
            tail = f"\n계획 경고 {len(warnings)}건: {short(warnings[0], 80)}" + tail
        room = 520 - len(tail)
        body, used = "", 0
        for number, line in enumerate(steps):
            if used + len(line) + 1 > room and number:
                body += f"\n…외 {len(steps) - number}단계"
                break
            body += "\n" + line
            used += len(line) + 1
        return {"text": f"\n연구 계획 {len(steps)}단계:" + body + tail, "target_sha256": research_plan_sha256(plan),
                # The same frozen-plan fields the CP1 card carries, so the web renders the whole plan (#654 review).
                "detail": {"plan_canonical": canonical_plan_json(plan),
                           "pack_applicability": plan.get("pack_applicability") or {},
                           "warnings": plan.get("warnings") or [],
                           "protocol_revision": plan["protocol"].get("revision"), "packs": plan["protocol"].get("packs"),
                           "scope_status": plan["intake"].get("scope_status"),
                           # What the CP1 card also carries, so the one card is not a thinner view of the same plan.
                           "budget": budget, "cost_estimate": req.get("plan_cost_estimate") or {},
                           **({"plan_warnings": req.get("plan_warnings") or [], "plan_validation": req["plan_validation"]}
                              if (req.get("plan_validation") or {}).get("semantic_checks") else {})}}

    def _lookup_usage_line(self, rid: str, *, stage: str | None = None) -> str:
        """This request's public lookup usage as a fact for plan, re-plan, precedent and step prompts (#1088).

        "" when no cap is configured; UNKNOWN (never zero) when a configured counter cannot be read. A new request
        starts at zero: usage is not carried over from another request."""
        note = usage_note_for(self.hub.s, rid, stage=stage)
        return f"{chr(10)}{chr(10)}{note}" if note else ""

    def _spending_cap_line(self, rid: str) -> str:
        """The cap ``_check_budget`` enforces now, for research step and review prompts (#499): staff otherwise read
        only the free-text protocol.resource_limits, which may name another amount. "" without a cap."""
        req = self.hub.requests.get(rid) or {}
        cap = float(req.get("budget_usd") or self.hub.s.policy.budget.per_request_usd or 0)
        if not cap:
            return ""
        plan_budget = ((req.get("research_contract") or {}).get("approval") or {}).get("budget") or {}
        approved = plan_budget.get("cap_usd")
        if approved == cap and "spent_before_plan_usd" in plan_budget:  # older approvals had the plan budget alone
            clamped = plan_budget.get("source") != "plan"
            total = float(plan_budget.get("plan_budget_usd") or 0) + float(plan_budget.get("spent_before_plan_usd") or 0)
            if clamped and plan_budget.get("source") == "request" and total <= cap:
                origin = f"the request's own budget; the plan's estimate ({_plan_cap_sum(plan_budget)}) is not the cap"
            else:
                origin = f"{_plan_cap_sum(plan_budget)}{'가 설정 상한을 넘어 상한으로 줄임' if clamped else ''}, CP1 승인"
        else:
            origin = ("the plan's budget_usd approved at CP1" if approved == cap else
                      f"CP1 approved ${float(approved):.2f}; the PI raised it on a budget card" if approved else
                      "the lab's request cap; the plan states no budget_usd")
        return (f"\n\nEnforced spending cap for this request: ${cap:.2f} ({origin}). It is the cap labhq enforces, "
                "whatever amount protocol.resource_limits states; spending past it waits for a PI budget card.")

    def _unknown_reserve(self, limit: float) -> float:
        """What an unaccounted task is assumed to have spent for the cap (#270): the per-task budget, else the cap."""
        per_task = self.hub.s.policy.budget.per_task_usd
        return float(per_task) if per_task and per_task > 0 else float(limit)

    def _diagnosis_budget_available(self, rid: str) -> bool:
        """Optional diagnosis never opens a budget card; include durable costs and dispatch holds."""
        req = self.hub.requests.get(rid, {})
        limit = req.get("budget_usd") or self.hub.s.policy.budget.per_request_usd
        if rid in self.budget_denials:
            return False
        if not limit:
            return True
        self._restore_cost_cache(rid)
        unknown = {tid for tid, item in (req.get("cost_items") or {}).items() if item.get("status") == "unknown"}
        unknown.update(self.cost_unknown.get(rid) or ())
        count = max(len(unknown), int(req.get("cost_known") is False))
        spent = max(self.cost.get(rid, 0), float(req.get("cost_usd") or 0))
        return spent + count * self._unknown_reserve(limit) + self._dispatch_held(rid, limit) < limit

    def _optional_turn_budget_available(self, rid: str) -> bool:
        """A nonessential normal turn runs only when one conservative task reservation fits under the request cap."""
        self._count_recorded_costs(rid)
        if rid in self.budget_denials:
            return False
        req = self.hub.requests.get(rid, {})
        limit = req.get("budget_usd") or self.hub.s.policy.budget.per_request_usd
        if not limit:
            return True
        _spent, _unknown, bound = self._budget_bound(rid, limit)
        return bound + self._unknown_reserve(limit) <= limit

    def _budget_bound(self, rid: str, limit: float) -> tuple[float, int, float]:
        """Counted dollars, unaccounted tasks, and the total the cap is judged on: never $0 for an unknown task."""
        spent = self.cost.get(rid, 0.0)
        unknown = len(self.cost_unknown.get(rid) or ())
        # Parallel paper preparations reserve dollars before dispatch. A recorded result replaces its hold;
        # cancellation/delivery uncertainty retains it until the durable task cost can be counted.
        held = sum(amount for tid, amount in self.paper_budget_holds.get(rid, {}).items()
                   if tid not in self.cost_tasks.get(rid, set()))
        return spent, unknown, spent + unknown * self._unknown_reserve(limit) + held + self._dispatch_held(rid, limit)

    # ---------- pre-dispatch reservation (#862, policy.budget.reserve_before_dispatch) ----------
    # A hold lives on the request record, so a gateway restart keeps it. It counts until the gateway records the
    # task's cost (cost_by_task, an unknown outcome included) or this orchestrator counted the result; then the cost,
    # or the unknown reserve, takes its place. Paper preparations keep their own paper_budget_holds.
    def _dispatch_held(self, rid: str, limit: float) -> float:
        # Settled only once counted here (a known amount or the unknown reserve), never on the gateway record alone,
        # so no caller can see the hold gone before its cost is in the bound.
        req = self.hub.requests.get(rid) or {}
        settled = self.cost_tasks.get(rid, set())
        holds = dict(req.get("budget_holds") or {})
        store = getattr(self.hub, "store", None)
        reservations = store.get("pi_translation_reservations", rid) if store else None
        if reservations is None:
            reservations = {}
        if not isinstance(reservations, dict):
            return float("inf")
        translation_unknown_extra = 0.0
        for key, row in reservations.items():
            if not isinstance(row, dict) or row.get("request_id") != rid:
                return float("inf")
            tid, ceiling = row.get("task_id"), row.get("ceiling_usd")
            valid = (isinstance(tid, str) and 0 < len(tid) <= 128
                     and tid == key == row.get("reservation_id") and row.get("source") == "pi_translation"
                     and row.get("state") == "reserved" and type(row.get("attempt")) is int
                     and row["attempt"] == 1 and isinstance(row.get("agent_id"), str) and row["agent_id"]
                     and _usd_amount(ceiling))
            if not valid:
                return float("inf")
            entry = store.get("task", tid)
            if entry is not None:
                payload = entry.get("payload") if isinstance(entry, dict) else None
                valid = valid and (isinstance(payload, dict) and entry.get("request_id") == rid
                    and entry.get("kind") == "pi_interpretation" and type(entry.get("attempt")) is int
                    and entry["attempt"] == row["attempt"]
                    and payload.get("id") == tid and payload.get("request_id") == rid
                    and payload.get("agent_id") == row["agent_id"]
                    and _usd_amount(payload.get("budget_usd")) and payload["budget_usd"] == ceiling)
            if not valid or (tid in holds and holds[tid] != ceiling):
                # Conflicting provenance cannot admit another paid call.
                return float("inf")
            holds[tid] = ceiling
            if tid in settled and tid in self.cost_unknown.get(rid, set()):
                # _budget_bound already counted this unknown task at the generic reserve. A translation holds only
                # its own enforced ceiling, so swap the generic reserve for it, up or down (#940: two unknown
                # translations tied up 2 x the per-task cap although each is capped at its $0.05 ceiling).
                translation_unknown_extra += ceiling - self._unknown_reserve(limit)
        return sum(amount if _usd_amount(amount) else self._unknown_reserve(limit)
                   for tid, amount in holds.items() if tid not in settled) + translation_unknown_extra

    async def _prune_dispatch_holds(self, rid: str) -> None:
        req = self.hub.requests.get(rid) or {}
        holds = req.get("budget_holds")
        if not holds:
            return
        settled = self.cost_tasks.get(rid, set())
        if settled & set(holds):
            req["budget_holds"] = {tid: amount for tid, amount in holds.items() if tid not in settled}
            await persist_request(self.hub, rid)

    def _turn_cap(self, task: Task, remaining: float) -> float | None:
        """The most this turn may cost: its own, its agent's and the per-task cap, else what the request has left."""
        agent = (getattr(self.hub, "agents", None) or {}).get(task.agent_id) or {}
        caps = [value for value in (task.budget_usd, agent.get("max_budget_usd"),
                                    self.hub.s.policy.budget.per_task_usd) if _usd_amount(value)]
        if caps:
            return float(min(caps))
        return remaining if _usd_amount(remaining) else None

    async def _reserve_dispatch(self, rid: str, task: Task, *, optional: bool) -> Task:
        """Hold this attempt's whole cap before it is sent and cap the turn at that amount (#862).

        It fits when spent + unknown reserves + every open hold + this cap stays within the request cap. Otherwise an
        ordinary turn goes through the existing budget card and an optional one is refused; nothing is sent either
        way. A turn without a positive amount to hold is never sent as free or unlimited."""
        budget = self.hub.s.policy.budget
        if not budget.reserve_before_dispatch or not rid or task.meta.get("kind") == "paper_skill":
            return task  # off by default; paper preparations already hold their allowance
        while True:
            async with self.budget_locks.setdefault(rid, asyncio.Lock()):
                self._count_recorded_costs(rid)
                (await self._prune_dispatch_holds(rid))
                if rid in self.budget_denials:
                    raise BudgetExceeded(self.budget_denials[rid])
                req = self.hub.requests.get(rid) or {}
                limit = req.get("budget_usd") or budget.per_request_usd
                if not limit:
                    return task  # no request cap: nothing to reserve against (unchanged)
                if not _usd_amount(limit):
                    raise BudgetExceeded(f"invalid request budget {limit!r}; nothing was dispatched")
                limit = float(limit)
                _spent, _unknown, bound = self._budget_bound(rid, limit)
                full = self._turn_cap(task, limit - bound)
                if full is not None:
                    # Whole cents, whatever set the cap (task, agent or per_task_usd); a cap that floors to nothing is not
                    # a call that can be held.
                    full = _floor_cents(full)
                    if full < float(_CENT):
                        raise BudgetExceeded(f"호출 상한이 1센트보다 작아 보내지 않았습니다(one cent: ${full:.4f}); "
                                             "nothing was dispatched")
                # #1139: a turn holds no more than the request still has, so a request budget smaller than the per-turn
                # cap (the default $5) is not asked about at its first turn; what it may spend is still the hold.
                cap = _clip_to_remaining(full, limit - bound)
                adopted = getattr(self.hub, "recovery_task_id", lambda _task: None)(task)
                if adopted is not None:
                    # Restart recovery: the gateway answers this turn with an earlier task instead of sending it (same
                    # rule, no await between here and that decision), so this attempt holds nothing more. The earlier
                    # task keeps its hold from before the restart; one without a hold (or cost) is held now, without a
                    # card, because it may still be spending.
                    holds = req.setdefault("budget_holds", {})
                    if adopted not in self.cost_tasks.get(rid, set()) and not _usd_amount(holds.get(adopted)):
                        holds[adopted] = cap if cap is not None else self._unknown_reserve(limit)
                        await persist_request(self.hub, rid)
                    return task
                if cap is not None and bound + cap <= limit + _HOLD_EPSILON:
                    holds = req.setdefault("budget_holds", {})
                    previous = holds.get(task.id)
                    holds[task.id] = max(cap, previous) if _usd_amount(previous) else cap
                    await persist_request(self.hub, rid)
                    self.dispatch_inflight.setdefault(rid, set()).add(task.id)
                    return task.model_copy(update={"budget_usd": cap, "request_budget_remaining_usd": cap})
                if optional:
                    raise BudgetExceeded("facilities diagnosis budget exhausted" if task.meta.get("facilities_diagnosis")
                                         else "optional turn budget exhausted before dispatch")
                needed = full if full is not None else self._unknown_reserve(limit)  # the whole cap: what a raise must make room for
            await self._check_budget(rid, reserve=needed)  # approval raises the cap; a denial raises here

    async def _drop_unsent_holds(self, rid: str) -> None:
        """At a request's (re)start, release holds of attempts the gateway provably never sent (#862 review P2-2).

        The gateway stores a task before sending it and records an unknown outcome when a send fails, so an id with
        neither a stored task nor a cost record was never sent: a restart or cancel during the pre-send waits (PI pause,
        offline runner). Ids being dispatched by this process and every possibly-sent hold are kept."""
        store = getattr(self.hub, "store", None)
        req = self.hub.requests.get(rid) or {}
        holds = req.get("budget_holds")
        if store is None or not holds:
            return
        recorded = req.get("cost_by_task") or {}
        inflight = self.dispatch_inflight.get(rid, set())
        kept = {tid: amount for tid, amount in holds.items()
                if tid in inflight or tid in recorded or store.get("task", tid) is not None}
        if len(kept) != len(holds):
            req["budget_holds"] = kept
            await persist_request(self.hub, rid)

    async def _release_if_unsent(self, rid: str, task_id: str) -> None:
        """A cancelled or failed dispatch: release the hold only when the gateway neither stored the task (it stores
        before sending) nor recorded a cost for it; a possibly sent turn keeps its hold."""
        store = getattr(self.hub, "store", None)
        req = self.hub.requests.get(rid) or {}
        if (store is not None and task_id in (req.get("budget_holds") or {})
                and task_id not in (req.get("cost_by_task") or {}) and store.get("task", task_id) is None):
            (await self._release_dispatch_hold(rid, task_id))

    async def _release_dispatch_hold(self, rid: str, task_id: str) -> None:
        """After dispatch returned: the gateway recorded the turn's cost before answering, or never sent it (a
        refusal, cancel or offline result, or an earlier task id from restart recovery). Never on an exception or
        cancellation, where the turn may still be running."""
        holds = (self.hub.requests.get(rid) or {}).get("budget_holds")
        if holds and task_id in holds:
            holds.pop(task_id)
            await persist_request(self.hub, rid)

    # ---------- DAG ----------
    def _bind_paper_dispatch_identity(self, rid: str, requested: str, durable: str) -> None:
        """Gateway-selected identity, before its recovery wait; no claim of remote termination."""
        self.paper_task_ids.setdefault(rid, {})[requested] = durable
        holds = self.paper_budget_holds.setdefault(rid, {})
        amount = holds.pop(requested, 0.0)
        if amount:
            holds[durable] = max(holds.get(durable, 0.0), amount)

    async def _check_paper_selection(self, rid: str, selected: list[dict],
                               expected: tuple[str, str] | None = None) -> None:
        req = self.hub.requests[rid]
        if req.get('status') == 'cancelled':
            raise asyncio.CancelledError
        plan = req.get('plan') or {}
        contract = req.get('research_contract') or {}
        approval = contract.get('approval') or {}
        scopes = {(row['step_id'], row['agent_id']) for row in selected}
        matches = all([row for row in selected if (row['step_id'], row['agent_id']) == (sid, aid)]
                      == paper_skills.attachments(plan, sid, aid, rid) for sid, aid in scopes)
        if expected is not None:
            matches = matches and selected == paper_skills.attachments(plan, expected[0], expected[1], rid)
        if contract:
            matches = (matches and approval.get('approved') is True
                       and approval.get('target_sha256') == contract.get('plan_sha256')
                       == research_plan_sha256(plan))
        if not matches:
            if contract:
                contract['approval'] = {**approval, 'approved': False, 'status': 'approval_required'}
            req['outcome'] = 'reuse_approval_required'
            await persist_request(self.hub, rid)
            raise research_continuation.ReuseApprovalRequired('paper selection changed; renewed approval required')

    def _paper_build_allowance(self, rid: str, execution_slots: int) -> float:
        per_task = self.hub.s.policy.budget.per_task_usd
        caps = [self.hub.s.recruit.max_budget_usd]
        if per_task and per_task > 0:
            caps.append(per_task)
        remaining = self._request_budget_remaining(rid)
        if remaining is not None:
            req = self.hub.requests.get(rid) or {}
            limit = req.get('budget_usd') or self.hub.s.policy.budget.per_request_usd
            # Optional preparation must leave a conservative normal-turn allowance for the DAG slots.
            reserve = self._unknown_reserve(limit) * max(1, execution_slots)
            caps.append(max(0.0, remaining - reserve))
        return min(caps)

    async def _start_paper_builds(self, rid: str, rows: list[dict],
                                 execution_slots: int = 1) -> paper_skills.ApprovedBuilds:
        batch = paper_skills.ApprovedBuilds(rows, self.hub.s)
        # Admission cannot open a budget card or raise the CP1 cap. Reserve before starting any build.
        async with self.budget_locks.setdefault(rid, asyncio.Lock()):
            (await self._check_paper_selection(rid, rows))  # approval may change while waiting for this lock
            self._count_recorded_costs(rid)
            for key, task in batch.tasks.items():
                if key in batch.disabled:
                    continue
                cap = self._paper_build_allowance(rid, execution_slots)
                if rid in self.budget_denials or not 0 < cap < float('inf'):
                    batch.disabled[key] = 'paper_skill_budget_unavailable'
                    continue
                batch.tasks[key] = task.model_copy(update={
                    'budget_usd': cap, 'request_budget_remaining_usd': cap})
                self.paper_budget_holds.setdefault(rid, {})[task.id] = cap

        async def run(task):
            (await self._check_paper_selection(rid, rows))
            result = await self.run_step(task)
            if task.id not in self.paper_task_ids.get(rid, {}):  # legacy/fake dispatch without the binding hook
                self._bind_paper_dispatch_identity(rid, task.id, result.task_id)
            self._count_cost(rid, result.task_id, result)
            durable = self.paper_task_ids[rid][task.id]
            if result.task_id == durable:
                self.paper_budget_holds.get(rid, {}).pop(durable, None)
            return result

        async def cancel(task):
            # Durable runner cancellation survives disconnect/reconnect; cancelling an asyncio waiter alone
            # is not proof that a remotely dispatched converter has stopped spending.
            cancel_task = getattr(self.hub, 'cancel_task', None)
            if cancel_task is not None:
                await cancel_task(self.paper_task_ids.get(rid, {}).get(task.id, task.id))

        async def progress(done, total):
            text = (f'논문 스킬 준비 종료 {min(done, total)}/{total}(생략 포함); '
                    f'준비 수명 상한 {self.hub.s.runner.task_timeout_s}초; 단계 시작 때 미완료면 생략')
            await self.hub.publish({'type': 'agent.log', 'ts': time.time(), 'request_id': rid,
                'agent_id': self.cfg.cso_agent, 'data': {'level': 'info', 'text': text}})
            for row in batch.rows.values():
                for sid in row['step_ids']:
                    await self._emit(rid, 'request.step_wait', {'step_id': sid,
                                                              'reason': text})

        try:
            await batch.start(run, lambda coro: self._step_task(rid, coro), cancel, progress,
                              PRECEDENT_CANCEL_GRACE_S)
        except BaseException:
            await self._close_paper_builds(rid, batch)
            raise
        return batch

    async def _close_paper_builds(self, rid: str, batch: paper_skills.ApprovedBuilds) -> None:
        await batch.close()
        for key, task in batch.tasks.items():
            if key not in batch.started:  # no run_step/dispatch was ever entered
                self.paper_budget_holds.get(rid, {}).pop(task.id, None)

    async def _resolve_step_paper_skills(self, task: Task) -> Task:
        """The approved dispatch boundary, never CP1 preparation. Keep the frozen plan unchanged."""
        rid = task.request_id
        req = self.hub.requests[rid]
        selected = task.meta['paper_skills']

        async def check_selection() -> None:
            (await self._check_paper_selection(rid, selected, (task.meta['step_id'], task.agent_id)))

        (await check_selection())
        # No lead time outside a DAG cohort: omit deferred advice rather than start and wait here.
        batch = self.paper_builds.get(rid) or paper_skills.ApprovedBuilds(selected, self.hub.s)
        resolved = await batch.resolve(selected)
        (await check_selection())  # approval or selection may change while the bounded conversion awaits
        await self._check_budget(rid)
        contract = req.get('research_contract') or {}
        if (contract.get('continuation') or {}).get('reuse_snapshot'):
            if not await self._verify_research_reuse(rid, req['plan']):
                raise research_continuation.ReuseApprovalRequired('reuse proof changed during paper build')
        (await check_selection())  # budget/reuse checks above may themselves await an approval or file proof
        return task.model_copy(update={'meta': {**task.meta, 'paper_skills': resolved}})

    async def run_dag(self, rid: str, request: str, steps: list[dict], results: dict[str, TaskResult],
                      only: set[str] | None = None, feedback: dict[str, str] | None = None) -> None:
        plan = (self.hub.requests.get(rid) or {}).get('plan') or {}
        rows = [row for step in steps if (only is None or step['id'] in only)
                and (only is not None or not (results.get(step['id']) and results[step['id']].ok))
                for row in paper_skills.attachments(plan, step['id'], step['agent_id'], rid)]
        if not any(row.get('status') == 'deferred' for row in rows):
            return await self._run_dag(rid, request, steps, results, only, feedback)
        req = self.hub.requests[rid]
        contract = req.get('research_contract') or {}
        approval = contract.get('approval') or {}
        if contract and not (approval.get('approved') is True
                             and approval.get('target_sha256') == contract.get('plan_sha256')
                             == research_plan_sha256(plan)):
            raise research_continuation.ReuseApprovalRequired('paper preparation requires renewed CP1 approval')
        if (contract.get('continuation') or {}).get('reuse_snapshot'):
            if not await self._verify_research_reuse(rid, plan):
                raise research_continuation.ReuseApprovalRequired('reuse proof changed before paper preparation')
        batch = await self._start_paper_builds(rid, rows, min(self.cfg.max_parallel_steps, len(steps)))
        self.paper_builds[rid] = batch
        try:
            await self._run_dag(rid, request, steps, results, only, feedback)
        finally:
            self.paper_builds.pop(rid, None)
            await self._close_paper_builds(rid, batch)  # end/replan keeps durable runner cancellation

    async def _run_dag(self, rid: str, request: str, steps: list[dict], results: dict[str, TaskResult],
                       only: set[str] | None = None, feedback: dict[str, str] | None = None) -> None:
        by_id = {s["id"]: s for s in steps}
        todo = {s["id"] for s in steps if only is None or s["id"] in only}
        running: dict[str, asyncio.Task] = {}
        ancestors = step_ancestors(steps)
        req_state = self.hub.requests.get(rid)
        research_plan = ((req_state or {}).get("plan") if
                         ((req_state or {}).get("research_contract") or {}).get("execution_enabled") else None)
        branch_topology = branch_plan_topology((research_plan or {}).get("branches") or [], steps)
        branch_ids = branch_topology["branch_ids"]
        branch_by_step = branch_topology["branch_by_step"]
        branch_terminals = branch_topology["terminals"]
        shared_environment_protected = any(
            ENV_LOCK_OUTPUT in step.get("outputs", []) for step in steps)
        # Answers to blocking questions survive a gateway restart (the re-run step still needs them).
        decisions: dict[str, Any] = dict((req_state or {}).get("step_decisions") or {})
        sem = asyncio.Semaphore(self.cfg.max_parallel_steps)
        # Ancestors' instructions count as inputs; the runner sees only this step's text, so it gets the verdict (#424).
        restricted: set[str] = set()
        if self._external_guard(rid):
            texts, dirs = self._request_inputs(rid)
            restricted = restricted_input_steps(steps, [request, *texts], dirs, self.hub.s)

        async def skip(sid: str, reason: str, upstream_ids: list[str] | None = None) -> None:
            previous = results.get(sid)
            if feedback and sid in feedback and previous and previous.ok:
                await save_result(results, sid, previous.model_copy(update={"revision_failed": f"skipped: {reason}"}))
            else:
                await save_result(results, sid, TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False,
                                                          error=f"skipped: {reason}"))
            await self._emit(rid, "request.step_skipped", {"step_id": sid, "status": "skipped",
                                                            "upstream": upstream_ids or [], "reason": reason})

        def input_steps(step: dict) -> list[str]:
            """Direct dependencies, then every other ancestor in plan order.

            A step may read an ancestor it reaches only through another step: the v0.5 trial's preprocessing read
            the fetch step's matrix through the pairing and QC steps, found no inputs/<fetch> link and failed
            (2026-10-08). Every ancestor's outputs are linked; only direct dependencies get their full result text.
            """
            direct = list(step["depends_on"])
            pin = environment_runner_pin(step)
            def local_environment(s: dict) -> bool:
                binding = s.get("environment_binding")
                return not binding or not pin or environment_runner_pin(s) == pin

            return direct + [s["id"] for s in steps if s["id"] in ancestors[step["id"]]
                             and s["id"] not in direct and local_environment(s)]

        def upstream(step: dict) -> str:
            parts = []
            for d in step["depends_on"]:
                r = results.get(d)
                if r and r.ok:
                    head = f"## {d} · {by_id[d]['agent_id']}"
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
            if step.get("branch") == "synthesis" and branch_ids:
                failed_lines = []
                for branch_id in branch_ids:
                    failed = [sid for sid in by_id if branch_by_step.get(sid) == branch_id and sid in results
                              and not results[sid].ok]
                    if not failed:
                        continue
                    last = next((sid for sid in reversed(list(by_id)) if branch_by_step.get(sid) == branch_id
                                 and sid in results and results[sid].ok), "none")
                    reasons = "; ".join(f"{sid}: {safe_failure_cause(results[sid].error or 'unknown error')}"
                                        for sid in failed)
                    failed_lines.append(f"- 실패한 갈래: {branch_id} — 이유: {reasons}; "
                                        f"마지막으로 끝난 단계: {last}")
                if failed_lines:
                    parts.append("## Failed branch memo\n" + "\n".join(failed_lines))
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
                           "Every claim you return must use one of those claim_ids; never add a claim id of your "
                           "own. A hypothesis, mechanism or background row is not evidence: link it to one of "
                           "those claims as context. "
                           "Each artifact_refs path is one of your declared outputs (outputs/<name>; for a declared folder, "
                           "a file inside it as outputs/<folder>/<file>, never the folder itself) or an upstream "
                           "artifact written as <workdir_id>/<path>; evidence citing any other path is refused at CP2. "
                           "If you cannot proceed without a blocking decision, return the same schema with every "
                           "list empty and the question with its choices in blocking_decision. The CSO answers first; "
                           "only hard-stop questions go to the PI. You re-run with the answer."
                           "\n\nCross-field result rules (the JSON schema cannot express these):\n" +
                           RESEARCH_RESULT_FIELD_RULES +
                           "\n\nFrozen question, hypotheses and protocol, approved by the PI at CP1:\n" +
                           _research_protocol_digest(research_plan, step) +
                           "\nApply its selection and exclusion criteria, analysis unit and statistics exactly as "
                           "written. If the data force a different rule, use the closest workable one, record it in "
                           "method_changes (field, planned, actual, reason, affects_conclusion), and never describe "
                           "the result as following the pre-specified rule. When you judge or name a hypothesis, use "
                           "hypotheses.primary and hypotheses.null_or_alternatives word for word: do not rename, "
                           "merge, split or redefine them.")
                cp1 = contract.get("approval") or {}
                if cp1.get("approved"):  # the receipt of the CP1 this plan's steps run under (PI 점검 R11)
                    prompt += approval_note_block("CP1", [("", cp1.get("note") or "")], CP1_NOTE_RULE)
                prompt += self._spending_cap_line(rid)
            prompt += self._lookup_usage_line(rid)  # at the start of this step: what is left to spend (#1088)
            declared = [rel for rel in map(output_relpath, step.get("outputs") or []) if rel]
            optional = [rel for rel in map(output_relpath, step.get("optional_outputs") or []) if rel]
            if declared or optional:
                prompt += STEP_OUTPUTS_RULE.format(
                    required=", ".join(f"./{rel}" for rel in declared) or "(none)",
                    optional=", ".join(f"./{rel}" for rel in optional) or "(none)")
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
            if updates and (usage := self._lookup_usage_line(rid).strip()):
                # A resumed session gets only these updates, and its earlier turn holds an older count (#1088).
                updates.append(usage)
            linked = input_steps(step)
            # The precedent search's stored texts reach every research step as read-only inputs (#951).
            stored_precedents = (precedent_texts.rows(self.hub.requests.get(rid, {}).get("analysis_precedents"))
                                 if research_plan else [])
            upstream_dirs = [results[d].workdir for d in linked
                             if d in results and results[d].ok and results[d].workdir and results[d].outputs]
            upstream_steps = {d: results[d].workdir for d in linked
                              if d in results and results[d].ok and results[d].workdir and results[d].outputs}
            pin = environment_runner_pin(step)
            if pin and (session_id or workdir):
                origin = self._session_origin(rid, step["agent_id"], session_id)
                if origin != pin:
                    return TaskResult(task_id="", agent_id=step["agent_id"], ok=False,
                                      error="environment runner differs from or cannot verify resumed workspace runner")
            task = Task(agent_id=step["agent_id"], request_id=rid, prompt=prompt, context=ctx,
                        output_schema=RESEARCH_STEP_SCHEMA if research_plan else None,
                        resume_session_id=session_id if can_resume else None,
                        meta={**reference_meta(self.hub.requests.get(rid)),
                              "kind": "step", "step_id": step["id"], "request": request,
                              **({"model_tier": tier} if (tier := self._dispatch_step_tier(step)) else {}),
                              "paper_skills": paper_skills.attachments(
                                  self.hub.requests.get(rid, {}).get("plan") or {}, step["id"], step["agent_id"], rid),
                              "instruction": step["instruction"],
                              "revision": self._step_revision(rid, step["id"]),
                              "title": f"{step['id']}: {step['instruction'][:100]}" + (" (리뷰 반영 수정)" if feedback else ""),
                              "project_dirs": self.hub.requests.get(rid, {}).get("project_dirs", []),
                              "upstream_dirs": upstream_dirs, "upstream_steps": upstream_steps,
                              "upstream_artifacts": {d: {
                                  "task_id": results[d].task_id,
                                  "workdir": results[d].workdir, "outputs": list(results[d].outputs),
                                  "output_sha256": dict(results[d].output_sha256),
                              } for d in upstream_steps},
                              **({"precedent_texts": stored_precedents} if stored_precedents else {}),
                              "outputs": [*step.get("outputs", []), *step.get("optional_outputs", [])],
                              "required_outputs": step.get("outputs", []),
                              "optional_outputs": step.get("optional_outputs", []),
                              "environment_step": ENV_LOCK_OUTPUT in [*step.get("outputs", []),
                                                                        *step.get("optional_outputs", [])],
                              **({"session_runner": pin} if pin else {}),
                              **({"environment_binding": step["environment_binding"]}
                                 if "environment_binding" in step else {}),
                              **({"environment_bindings": step["environment_bindings"]}
                                 if "environment_bindings" in step else {}),
                              "shared_environment_protected": shared_environment_protected,
                               **({"restricted_inputs": True} if step["id"] in restricted else {}),
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
                outcome = merged_workspace_rerun(previous, outcome)
                if decision and previous and previous.tool_errors:
                    outcome = outcome.model_copy(update={"tool_errors": list(dict.fromkeys([
                        *previous.tool_errors, *outcome.tool_errors,
                    ]))})
                if not research_plan:
                    return attach_general_result(outcome)
                if not outcome.ok or blocking_question(outcome):
                    return outcome
                outcome = apply_declared_output_status(step, outcome)
                if not outcome.ok:
                    return outcome

                original = outcome
                current = outcome
                limit = self.hub.s.research.result_corrections

                async def save_salvage(refused_rows: list[dict], unsupported_claims: list[dict]) -> None:
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
                    await persist_request(self.hub, rid)

                # The last result that passed the contract and only cited a file labhq did not collect (#485). An
                # optional correction that fails, touches no collected file but writes others, or ends worse falls
                # back to it: asking to re-point a citation never fails a step that would have passed before.
                bindable: tuple[Any, TaskResult] | None = None
                normalization_warnings: list[str] = []

                def collect_paper_usage(via: TaskResult) -> None:
                    if task.meta.get('paper_skills'):
                        original.provenance = {**original.provenance, 'runs': {
                            **original.provenance.get('runs', {}), **via.provenance.get('runs', {})}}

                async def accept(ledger: Any, via: TaskResult) -> TaskResult:
                    collect_paper_usage(via)
                    await save_salvage([], [])
                    return original.model_copy(update={
                        "structured": ledger.model_dump(mode="json"),
                        "session_id": via.session_id or original.session_id,
                        "workdir": via.workdir or original.workdir,
                        "tool_errors": list(dict.fromkeys([*original.tool_errors, *via.tool_errors])),
                        **merged_tool_call_update(original, via),
                        "step_warnings": list(dict.fromkeys([
                            *original.step_warnings, *via.step_warnings, *normalization_warnings])),
                    })

                async def repair_slots(structured: dict, slots: list[str]) -> TaskResult:
                    # Persist before dispatch: a restarted DAG must not ask a second time.
                    answer: TaskResult | None = None
                    records = req_state["research_contract"].setdefault("slot_repairs", {})
                    record = records.get(step["id"])
                    if record and record.get("plan_sha256") != research_plan_sha256(research_plan):
                        record = None
                    if record is None:
                        record = records[step["id"]] = {"slots": slots, "asked": False, "answers": {},
                                                      "plan_sha256": research_plan_sha256(research_plan)}
                        await persist_request(self.hub, rid)
                        if (current.session_id and self.hub.supports_resume(step["agent_id"]) and
                                self._optional_turn_budget_available(rid)):
                            request_limit = (req_state.get("budget_usd") or
                                             self.hub.s.policy.budget.per_request_usd)
                            schema = {"type": "object", "additionalProperties": False,
                                      "required": slots, "properties": {slot: {
                                          "type": "object", "additionalProperties": False,
                                          "required": ["status", "reason"], "properties": {
                                              "status": {"type": "string", "enum": [
                                                  "attempted_found", "attempted_none", "not_attempted"]},
                                              "reason": {"type": "string", "minLength": 1, "maxLength": 280}}}
                                          for slot in slots}}
                            question = Task(
                                agent_id=step["agent_id"], request_id=rid, output_schema=schema,
                                resume_session_id=current.session_id,
                                budget_usd=(self._unknown_reserve(request_limit) if request_limit else None),
                                prompt=("누락된 증거 칸의 시도 여부만 답하세요. 파일·분석·전체 결과 JSON은 고치지 마세요. "
                                        "각 칸 id → {status: attempted_found/attempted_none/not_attempted, "
                                        "reason: 한 줄 이유}. 칸: " + ", ".join(slots)),
                                meta=auxiliary_turn_meta(
                                    task.meta, "slot_repair", [], max_attempts=1,
                                    parent_task=current.task_id, title=f"{step['id']}: 누락 칸 확인",
                                    **({"workdir": current.workdir} if current.workdir else {})))
                            record["asked"] = True
                            await persist_request(self.hub, rid)
                            try:
                                answer = await self.run_step(question)
                            except (ShutdownInterrupted, research_continuation.ReuseApprovalRequired):
                                raise
                            except BudgetExceeded:
                                answer = None
                            except Exception:
                                answer = None
                            if answer is not None and answer.unreported_outputs:
                                return original.model_copy(update={
                                    "ok": False, "error_kind": "contract",
                                    "error": "invalid research result contract: slot repair changed output files",
                                    **merged_tool_call_update(current, answer),
                                })
                            if answer is not None and answer.ok:
                                collect_paper_usage(answer)
                                record["answers"] = (answer.structured if isinstance(answer.structured, dict)
                                                     else extract_json(answer.text))
                            await persist_request(self.hub, rid)
                    ledger = repair_result_slots(structured, slots, record.get("answers"))
                    validate_research_result(ledger.model_dump(mode="json"), plan=research_plan)
                    via = (current if answer is None else current.model_copy(update={
                        "tool_errors": list(dict.fromkeys([*current.tool_errors, *answer.tool_errors])),
                        **merged_tool_call_update(current, answer),
                    }))
                    return await accept(ledger, via)

                for correction in range(limit + 1):
                    asked = blocking_question(current)
                    structured = (current.structured if isinstance(current.structured, dict)
                                  else extract_json(current.text))
                    if isinstance(structured, dict):  # the step schema's empty question field is not ledger
                        structured = {k: v for k, v in structured.items() if k != "blocking_decision"}
                    structured, plan_hash_warning = normalize_result_plan_sha256(structured, plan=research_plan)
                    if plan_hash_warning:
                        normalization_warnings.append(plan_hash_warning)
                        current = current.model_copy(update={
                            "structured": structured,
                            "step_warnings": list(dict.fromkeys([*current.step_warnings, plan_hash_warning])),
                        })
                    problems = (["a result correction cannot ask a new blocking_decision; return corrected JSON"]
                                if asked else research_result_errors(structured, plan=research_plan))
                    slots = ([] if asked else missing_result_slots(
                        structured, plan=research_plan, expected_step_id=step["id"]))
                    if slots:
                        return await repair_slots(structured, slots)
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
                        return await accept(validated_result, current)
                    if correction == limit and bindable is not None:
                        return await accept(bindable[0], current)
                    if correction == limit:
                        # A result that still asks the PI a blocking question is never salvaged into CP2: the
                        # employee said it cannot go on without that decision (PR #353 review).
                        salvaged, refused_rows, unsupported_claims, salvage_problems = (
                            (None, [], [], []) if asked else
                            salvage_research_result(structured, plan=research_plan, expected_step_id=step["id"],
                                                    allow_slot_gaps=True))
                        if salvaged is not None:
                            candidate = salvaged.model_dump(mode="json")
                            slots = missing_result_slots(candidate, plan=research_plan, expected_step_id=step["id"])
                            if slots:
                                repaired = await repair_slots(candidate, slots)
                                await save_salvage(refused_rows, unsupported_claims)
                                return repaired
                        if salvaged is not None and refused_rows:
                            await save_salvage(refused_rows, unsupported_claims)
                            return original.model_copy(update={
                                "structured": salvaged.model_dump(mode="json"),
                                "session_id": current.session_id or original.session_id,
                                "workdir": current.workdir or original.workdir,
                                **merged_tool_call_update(original, current),
                            })
                        return original.model_copy(update={
                            "ok": False, "error_kind": "contract", "error": "invalid research result contract: " +
                            "; ".join(dict.fromkeys([*problems, *salvage_problems])),
                            "session_id": current.session_id or original.session_id,
                            "workdir": current.workdir or original.workdir,
                            **merged_tool_call_update(original, current),
                        })

                    can_resume_correction = bool(current.session_id and self.hub.supports_resume(step["agent_id"]))
                    correction_task = Task(
                        agent_id=step["agent_id"], request_id=rid, output_schema=RESEARCH_STEP_SCHEMA,
                        resume_session_id=current.session_id if can_resume_correction else None,
                        prompt=continuation_prompt(task, result_correction(problems, list(step.get("claim_ids") or [])),
                                                   resumable=can_resume_correction, previous_result=current,
                                                   context_chars=self.cfg.context_chars_per_step),
                        meta=auxiliary_turn_meta(
                            task.meta, "result_correction", [], parse_attempt=correction + 1,
                            parent_task=current.task_id,
                            title=f"{step['id']}: 결과 계약 교정 #{correction + 1}",
                            **({"workdir": current.workdir} if current.workdir else {})),
                    )
                    corrected = await self.run_step(correction_task)
                    collect_paper_usage(corrected)
                    current = corrected.model_copy(update={
                        "tool_errors": list(dict.fromkeys([*current.tool_errors, *corrected.tool_errors])),
                        **merged_tool_call_update(current, corrected),
                    })
                    if not current.ok and bindable is not None:
                        return await accept(bindable[0], current)
                    if not current.ok:
                        return current.model_copy(update={
                            "outputs": original.outputs, "output_sha256": original.output_sha256,
                            "unreported_outputs": original.unreported_outputs,
                            "workdir": current.workdir or original.workdir,
                            **merged_tool_call_update(original, current),
                            "workdir_id": current.workdir_id or original.workdir_id,
                        })
                    if (current.unreported_outputs and bindable is not None
                            and not set(current.unreported_outputs) & set(original.outputs)):
                        return await accept(bindable[0], current)  # collected files and their hashes are untouched
                    if current.unreported_outputs:
                        # A correction only rewrites the result JSON. A file it changed would no longer match the
                        # hash CP2 binds evidence to, so the step fails instead (PR #352 review).
                        return original.model_copy(update={
                            "ok": False,
                            "error": "invalid research result contract: the result correction changed output files "
                                     "it must not touch: " + ", ".join(current.unreported_outputs),
                            "session_id": current.session_id or original.session_id,
                            "workdir": current.workdir or original.workdir,
                            **merged_tool_call_update(original, current),
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
                    synthesis_after_branch_failure = (
                        branch_by_step.get(sid) == "synthesis" and branch_ids and
                        not any(branch_by_step.get(dep) == "synthesis" for dep in blocked) and
                        any(any(terminal in results and results[terminal].ok
                                for terminal in branch_terminals.get(branch_id, []))
                            for branch_id in branch_ids)
                    )
                    if synthesis_after_branch_failure:
                        running[sid] = self._step_task(rid, run_one(by_id[sid]))
                        continue
                    reason = ", ".join(f"{d}: {results[d].error if d in results else 'not run'}" for d in blocked)
                    await skip(sid, f"upstream {reason}", blocked)
                    continue
                running[sid] = self._step_task(rid, run_one(by_id[sid]))
            if not running:
                if ready:
                    continue
                for sid in todo:
                    await save_result(results, sid, TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False,
                                                               error="unmet dependencies"))
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
                    except (ShutdownInterrupted, research_continuation.ReuseApprovalRequired):
                        for pending in running.values():
                            pending.cancel()
                        await asyncio.gather(*running.values(), return_exceptions=True)
                        raise
                    except asyncio.CancelledError:
                        outcome = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error="cancelled")
                    except Exception as e:
                        outcome = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error=str(e))
                    # A step that stops for a PI decision did not do the blocked work: its question goes to the
                    # PI below and the step re-runs, so outputs and the research ledger are checked on that run.
                    asked = blocking_question(outcome) if outcome.ok else None
                    if outcome.ok and not asked:
                        outcome = apply_declared_output_status(by_id[sid], outcome)
                    previous = results.get(sid)
                    if (feedback and sid in feedback and previous and previous.ok and not outcome.ok
                            and outcome.error_kind not in PI_STOP_ERROR_KINDS | {"wake_limit"}):
                        outcome = previous.model_copy(update={"revision_failed":
                            f"{outcome.error_kind or failure_kind(outcome) or 'terminal'}: {outcome.error or 'unknown error'}",
                            "tool_errors": list(dict.fromkeys([*previous.tool_errors, *outcome.tool_errors])),
                            **merged_tool_call_update(previous, outcome)})
                    await save_result(results, sid, outcome)
                    if not outcome.ok and failure_kind(outcome) not in {"environment", "login", "quota"}:
                        (await self._record_dev_failure(rid, sid, outcome.error or "unknown error",
                                                 outcome.error_kind or failure_kind(outcome) or "terminal"))
                    progress_storage = getattr(self.hub, "result_storage", None)
                    if progress_storage is not None:
                        try:
                            await progress_storage.progress_step(rid, sid, outcome)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            saved = (self.hub.requests.get(rid, {}).get("result_storage") or {})
                            saved["progress_warning"] = (
                                "단계별 GitHub 진행 게시에 실패했습니다. 요청은 계속 진행합니다.")
                            await persist_request(self.hub, rid)
                    environment = None if outcome.ok else environment_problem(outcome)
                    # A question-only turn continues at the same revision after
                    # its answer. Closing it here would reject that next turn.
                    if not asked:
                        await self._emit(rid, "request.step_done", {"step_id": sid, "ok": results[sid].ok,
                            "agent_id": by_id[sid]["agent_id"],
                            "attempts": self.attempts.get(rid, {}).get(sid, 0),
                            "reason": results[sid].error,
                            **({"warnings": results[sid].step_warnings} if results[sid].step_warnings else {}),
                            **({"environment": environment} if environment else {}),
                            **({"facilities_fix": results[sid].facilities_fix} if results[sid].facilities_fix else {})})
                    res = results[sid]
                    question = blocking_question(res)
                    if res.ok and question:
                        if sid in decisions:
                            if req_state is not None:
                                req_state["pending_questions"] = [question.strip()]
                                await persist_request(self.hub, rid)
                            raise RuntimeError(f"step {sid} still requires a blocking decision after its answer")
                        # Forget the question-only result before waiting (durably): a restart during the wait must
                        # re-run the step, not treat it as done and feed dependents a question.
                        await remove_result(results, sid, None)
                        if req_state is not None:
                            req_state["pending_questions"] = [question.strip()]
                            await persist_request(self.hub, rid)
                        if not hasattr(self.hub, "store"):  # lightweight unit-test hubs
                            decision = await self.hub.request_approval(
                                kind="clarify", request_id=rid,
                                summary=f"Step {sid} needs a blocking decision:\n{question.strip()}")
                            answer = ask_result(decision=decision, **{"from": "pi"})
                            answers = [answer]
                        else:
                            ask = AskRequest(task_id=res.task_id, agent_id=res.agent_id, request_id=rid,
                                             to="cso", question=question.strip(),
                                             why_blocked=f"step {sid}의 묻고 멈추는 게이트", wait="hibernate")
                            origin = hub_runner(self.hub, res.agent_id, rid) or ""
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
                            await save_result(results, sid, rejected_step(res, f"{question.strip()}: {outcome['reason']}"))
                            if req_state is not None:
                                req_state["pending_questions"] = []
                                await persist_request(self.hub, rid)
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
                            await persist_request(self.hub, rid)
                        todo.add(sid)

    async def _research_after_steps(self, rid: str, text: str, steps: list[dict], results: dict[str, TaskResult],
                                    n: int, serialized: Callable[[], dict[str, dict]], packs: dict) -> None:
        """Review successful independent steps at CP2, retaining failed steps as explicit limitations (#657).

        The generic re-plan, review and synthesis would change or judge the frozen PLAN without a new CP1 approval,
        so the research lane never reaches them. Evidence whose artifact labhq did not collect is refused and shown
        on the card. CP2 reads only the structured approve/revise/deny choice; an unreadable answer is not approved
        and is asked again. Only an approval goes on to the research review and report (``_research_report``). A
        revise continues through a new plan and CP1 written from the PI's note (``_research_continue``, R10); one
        without a note is asked again while a continuation is still allowed.
        """
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        failed = [s["id"] for s in steps if s["id"] not in results or not results[s["id"]].ok]
        successful = [s for s in steps if s["id"] not in failed]
        branch_of = {s["id"]: s.get("branch") for s in steps}
        warnings = [f"{sid}{f' (갈래 {branch_of[sid]})' if branch_of.get(sid) else ''}: 단계 실패 또는 의존 단계 건너뜀 — " +
                    safe_failure_cause(results[sid].error if sid in results else "not run") for sid in failed]
        warnings += [f"{step['id']}: {warning}" for step in successful
                     for warning in results[step["id"]].step_warnings]
        pi_stopped = any(sid in results and results[sid].error_kind in PI_STOP_ERROR_KINDS for sid in failed)
        branch_topology = branch_plan_topology(req["plan"].get("branches") or [], steps)
        branch_states = _branch_terminal_states(branch_topology, steps, results)
        no_successful_branch_terminal = bool(branch_states and
                                             not any(state["successful_terminals"] for state in branch_states))
        if rid in self.budget_denials or pi_stopped or not successful or no_successful_branch_terminal:
            contract["failure"] = {"steps": failed, "plan_sha256": contract["plan_sha256"],
                                   **({"reason": "no branch terminal completed", "branch_status": branch_states}
                                      if no_successful_branch_terminal else {}),
                                   **({"budget": self.budget_denials[rid]} if rid in self.budget_denials else {})}
            req["outcome"] = "research_failed"
            await persist_request(self.hub, rid)
            branch_report = (("\n\n" + _branch_terminal_status_table(branch_states))
                             if no_successful_branch_terminal else "")
            (await self._finish(rid, self.report_results(steps, results, n) + branch_report +
                         "\n\nResearch stopped before CP2. labhq does not re-plan a frozen research PLAN; a changed "
                         "plan needs a new CP1 approval of its hash.", serialized(), ok=False))
            return
        all_steps = steps
        steps = successful
        for step in steps:
            ledger = results[step["id"]].structured or {}
            for row in ledger.get("evidence", []):
                if row.get("origin"):
                    warnings.append(f"{step['id']}/{','.join(row['slots'])}: origin: {row['origin']}; "
                                    f"{row.get('attempt_status') or 'unknown'} — {row.get('status_detail')}")
            warnings += tool_missing_lines(step["id"], ledger)
        warnings += output_checks.quality_lines(steps, results)  # shown on the CP2 card and under 한계 (#697, #710)
        contract["result_warnings"] = warnings
        # CP2 approves these ledgers as results of the plan frozen at CP1, so each must validate under it. A reused
        # continuation result is a copy rebound to the new plan (``carry_over``); this catches any other drift
        # (PR #448 review).
        unbound = self._unbound_research_results(req, steps, results)
        if unbound:
            contract["failure"] = {"steps": sorted(unbound), "plan_sha256": contract["plan_sha256"],
                                   "unbound_results": unbound}
            req["outcome"] = "research_failed"
            await persist_request(self.hub, rid)
            (await self._finish(rid, self.report_results(steps, results, n) +
                         "\n\nResearch stopped before CP2: these step results do not validate under the frozen plan "
                         f"{contract['plan_sha256'][:12]}:\n" +
                         "\n".join(f"- {sid}: {'; '.join(problems)}" for sid, problems in sorted(unbound.items())),
                         serialized(), ok=False))
            return
        ledgers = {s["id"]: results[s["id"]].structured for s in steps}
        ancestors = step_ancestors(all_steps)
        refused: list[dict[str, str]] = []
        refused_rows: list[dict[str, str]] = []
        unsupported: list[dict[str, str]] = []
        artifact_sha256: dict[str, str | None] = {}
        unreported_outputs = {s["id"]: list(results[s["id"]].unreported_outputs) for s in steps}
        warning_outputs = {step_id: shown for step_id, paths in unreported_outputs.items()
                           if (shown := unreported_warning_paths(paths))}
        source_verification: list[str] = []
        source_reports: list[dict[str, Any]] = []
        live_resolver = None
        if self.hub.s.research.live_source_check:
            live_resolver = LiveSourceResolver(
                contact=self.hub.s.research.live_source_contact,
                request_timeout_s=self.hub.s.research.live_source_timeout_s,
                admission=admission_for(self.hub.s), request_id=rid)  # counted with labhq_annot's requests (#891)
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
                                           upstream=ancestor_artifacts(all_steps, ancestors, results, step["id"]),
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
        def update_conclusion_depth() -> None:
            stored = output_checks.stored_access(self.hub.requests.get(rid, {}).get("analysis_precedents"))
            warnings[:] = [line for line in warnings if not line.startswith(("결론 근거 논문 ", "문헌 접근 실패 원장("))]
            conclusion_depth = output_checks.conclusion_literature_line(
                steps, results, ledgers, unsupported=unsupported, refused=refused, stored=stored)
            if conclusion_depth:
                warnings.append(conclusion_depth)
            access_ledger = output_checks.literature_ledger_line(
                steps, results, ledgers, unsupported=unsupported, refused=refused, stored=stored)
            if access_ledger:
                warnings.append(access_ledger)
            contract["result_warnings"] = warnings

        update_conclusion_depth()
        claims = sum(len((ledger or {}).get("claims") or []) for ledger in ledgers.values())
        rows = sum(len((ledger or {}).get("evidence") or []) for ledger in ledgers.values())
        # A revise continues through a new plan and CP1 while research.revise_continuations allows (R10).
        limit = self.hub.s.research.revise_continuations
        revise_continues = len(contract.get("rounds") or []) < limit
        summary = (f"CP2 근거 검토: 단계 {len(ledgers)}개, claim {claims}개, 근거 {rows}행" +
                   (f", 거부 {len(refused_rows) or len(refused)}건" if refused_rows or refused else "") +
                   ". 증거 승인·수정 요청·거부 중 고르세요. " +
                   ("수정 요청은 메모에 적은 고칠 점으로 CSO가 새 계획을 세워 새 CP1을 받습니다(바뀐 단계만 다시 실행)."
                    if revise_continues else
                    f"이어 가기 상한(research.revise_continuations={limit})에 닿아 수정 요청도 요청을 끝냅니다."))
        if warnings:
            summary += f" 경고 {len(warnings)}건: 누락 칸·실패 단계는 한계로 남습니다."
        device_record = await self._semantic_devices_check(rid, "cp2")
        device_warning = semantic_devices.warning(device_record)
        if device_warning:
            summary = device_warning + "\n" + semantic_devices.card_line(device_record) + "\n\n" + summary
        else:
            summary = semantic_devices.card_line(device_record) + "\n" + summary
        device_report = None
        if semantic_device_report.applies(req, self.hub.s):
            device_report = semantic_device_report.build_report(req, self.hub.s)
            summary += "\n\n" + semantic_device_report.summary(device_report)
        detail = {"gate": "research_evidence", "plan_sha256": contract["plan_sha256"],
                  "semantic_devices": device_record,
                  **({"semantic_report": {"summary": semantic_device_report.summary(device_report),
                                            "path": semantic_device_report.REPORT_MD,
                                            "content": semantic_device_report.render_report(device_report)}}
                     if device_report is not None else {}),
                  **({"warnings": warnings} if warnings else {}),
                   "choices": list(EVIDENCE_CHOICES), "revise_continues": revise_continues,
                   **({"refused_rows": refused_rows} if refused_rows else {}),
                   **({"refused_evidence": refused} if refused else {}),
                  **({"unsupported_claims": unsupported} if unsupported else {}),
                  **({"source_verification": source_verification} if source_verification else {}),
                   "artifact_sha256": artifact_sha256, "unreported_outputs": warning_outputs,
                  "results": ledgers,
                  **({"branches": req["plan"].get("branches"),
                      "step_branches": {step["id"]: step.get("branch") for step in steps}}
                     if req["plan"].get("branches") else {})}
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
            pinned = unreceipted_references(contract, carried.get("reuse") or [])
            if pinned:
                reuse_lines.append(f"- {len(pinned)} DOI/PMID reference(s) of the reused steps are pinned by identifier "
                                   f"only; their content was not checked ({', '.join(pinned[:5])}"
                                   f"{', ...' if len(pinned) > 5 else ''}).")
        recorded = (contract.get("checkpoints") or {}).get("cp2") or {}
        source_changed = (live_resolver is not None and bool(recorded.get("decision"))
                          and recorded.get("plan_sha256") == contract["plan_sha256"]
                          and not self._archived_continuation_decision(req)
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
            update_conclusion_depth()  # the saved CP2 refusal set is authoritative after a restart
        else:
            cp2: str | None = None
            decision: dict[str, Any] = {}
            asks = 0
            retry = ""
            while cp2 is None and asks < CP2_MAX_ASKS:
                asks += 1
                decision = await self.hub.request_approval(
                    kind="research_evidence", request_id=rid, detail=detail, summary=retry + summary)
                cp2 = read_evidence_decision(decision)
                if (cp2 == "revision_requested" and revise_continues and asks < CP2_MAX_ASKS
                        and not str(decision.get("note") or "").strip()):
                    # The new plan is written from the note, so a revise without one is asked again (R10).
                    cp2, retry = None, ("수정 요청에 메모가 없어 다시 묻습니다. 무엇을 고칠지 메모에 적고 수정 요청을 "
                                        "누르세요. ")
                elif cp2 is None:
                    retry = ("The previous CP2 answer had no readable approve/revise/deny choice and was not "
                             "approved. ")
            decided = cp2 or "unreadable"
            note = str(decision.get("note") or "").strip()
            # Kept per step in the receipt: a continuation reuses a step together with its salvage records (#90).
            salvage = contract.pop("result_salvage", None) or {}
            contract.setdefault("checkpoints", {})["cp2"] = {
                "gate": "research_evidence", "decision": decided, "choice": decision.get("choice"), "note": note,
                "approval_id": decision.get("approval_id"), "decided_at": decision.get("decided_at"),
                "plan_sha256": contract["plan_sha256"], "asks": asks,
                **({"warnings": warnings} if warnings else {}),
                **({"refused_rows": refused_rows} if refused_rows else {}),
                "refused_evidence": refused, "unsupported_claims": unsupported,
                "artifact_sha256": artifact_sha256, "unreported_outputs": unreported_outputs,
                **({"source_verification": source_verification,
                    "source_verification_reports": source_reports} if live_resolver is not None else {}),
                **({"result_salvage": salvage} if salvage else {})}
        req["outcome"] = f"evidence_{decided}"
        await persist_request(self.hub, rid)
        audit = f"CP2 evidence review: {decided}."
        if warnings:
            audit += "\n결과 한계:\n" + "\n".join(f"- {line}" for line in warnings)
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
            # The PI's note is the revision request: the CSO re-plans from it and the PI approves a new CP1 (R10).
            ended = (await self._research_continue(rid, None, results, cp2_note=note) if note else
                     "수정 요청에 메모가 없어 새 계획을 세우지 않았습니다.")
            if ended is None:  # a new plan, CP1 and run took this request over
                return
            if ended:
                audit += "\n" + ended
        elif decided == "unreadable":
            audit += f"\nNo readable approve/revise/deny choice after {asks} card(s); the evidence is not approved."
        elif decided == "timed_out":
            audit += ("\nCP2 카드에 기한 안에 답이 없어 근거를 승인하지 않고 끝냈습니다(거절이 아님, "
                      "policy.approvals.pi_decision_timeout_s).")
        if note:
            audit += f"\nPI note: {note}"
        # What the earlier rounds were asked to fix, as the report writer saw it (web trial 2026-10-08).
        audit += "".join(f"\n{line}" for line in earlier_round_lines(contract))
        # An earlier round's CP2 note still reaches this round's review and report, so the record keeps it whole (#499).
        for archived in contract.get("rounds") or []:
            receipt = (archived.get("cp2") or {}) if isinstance(archived, dict) else {}
            if receipt.get("decision") == "approved" and receipt.get("note"):
                audit += f"\nPI note (round {archived.get('round')} CP2): {receipt['note']}"
            elif receipt.get("decision") == "revision_requested" and receipt.get("note"):  # what this round fixed
                audit += f"\nPI 수정 요청 (round {archived.get('round')} CP2): {receipt['note']}"
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
        (await self._finish(rid, report, serialized(), ok=decided == "approved",
                     error=None if decided == "approved" else RESEARCH_END_ERRORS.get(f"evidence_{decided}")))

    async def _blind_draft(self, rid: str, req: dict, text: str, refs: dict) -> bool:
        """The sealed blind draft (#889): the reviewer answers the original request alone, after CP1 approval and
        before the first step, so no team result can exist yet. Off, or already decided (sealed, late, unavailable:
        kept over a restart): nothing runs. A failed draft is recorded and never stops the request.
        Returns False only when the draft's own budget card was denied and the request ended."""
        reviewer = self.cfg.reviewer_agent
        contract = req["research_contract"]
        if not blind_draft.enabled(self.hub.s) or not reviewer or blind_draft.settled(contract.get(blind_draft.KEY)):
            return True
        if req.get("results"):  # a draft written after a team result would not be blind
            contract[blind_draft.KEY] = blind_draft.unavailable("results_exist")
            await persist_request(self.hub, rid)
            return True
        denied: str | None = None
        reply: TaskResult | None = None
        try:
            reply = await self.run_step(Task(
                agent_id=reviewer, request_id=rid,
                prompt=blind_draft.prompt(text) + self._spending_cap_line(rid),
                meta={**refs, "kind": "blind_draft", "outputs": ["outputs/blind_draft.md"], "request": text,
                      "title": "블라인드 초안"}))
        except BudgetExceeded as error:  # a reservation the PI's budget card refused: ends like a denied CP1 cost
            denied = str(error)
        if denied is not None or rid in self.budget_denials:
            contract["failure"] = {"steps": [], "plan_sha256": contract["plan_sha256"], "stage": "blind_draft",
                                   "budget": self.budget_denials.get(rid, denied)}
            req["outcome"] = "research_failed"
            await persist_request(self.hub, rid)
            (await self._finish(rid, "Research plan approved, but the blind draft's budget card was not approved; no "
                         "employee research step was dispatched.", {}, ok=False))
            return False
        contract[blind_draft.KEY] = (
            blind_draft.seal(reply.text or "", results_exist=bool(req.get("results"))) if reply.ok
            else blind_draft.unavailable(short(reply.error or "draft task failed", 200)))
        await persist_request(self.hub, rid)
        return True

    async def _research_report(self, rid: str, text: str, steps: list[dict], results: dict[str, TaskResult], n: int,
                               serialized: Callable[[], dict[str, dict]], packs: dict, cp2_report: str,
                               cp2_audit: str, ledgers: dict[str, Any], *, refused: list[dict],
                               unsupported: list[dict],
                               artifact_sha256: dict[str, str | None]) -> None:
        """After CP2 approval: one research review, then the CSO's report and its claim-anchor check (#58 ③⑤).

        The plan stays frozen, so a review that asks for revision ends the request unless the PI continues through
        a new plan and CP1 (``_research_continue``). The review is saved in the contract and reused after a
        restart; a frozen CP4 draft is reused without another synthesis.
        Failed lookups are attached to every report here, whether or not the CSO listed them (#58 ④).
        """
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        from ..research import final_checkpoint as cp4
        if cp4.enabled(req) and contract.get("final_draft"):
            cp4.reconcile(req)
            await persist_request(self.hub, rid)
            return
        plan_hash = contract["plan_sha256"]
        refs = reference_meta(req)
        reviewer = self.cfg.reviewer_agent
        lookups = failed_lookups(ledgers)
        lookup_section = [f"{FAILED_LOOKUP_TITLE}:\n" + "\n".join(failed_lookup_lines(lookups))] if lookups else []
        cp2_notes = research_cp2_notes(contract)  # PI 점검 R11: the notes reach the reviewer and the report writer
        source_section = claim_evidence_section(claim_evidence_rows(ledgers, refused=refused,
                                                                    unsupported=unsupported))

        async def end(outcome: str, report: str, ok: bool, review: dict | None, failure: dict | None = None,
                error: str | None = None) -> None:
            if failure is not None:
                contract["failure"] = {"steps": [], "plan_sha256": plan_hash, **failure}
            req["outcome"] = outcome
            await persist_request(self.hub, rid)
            if source_section:
                report = _before_bench_block(report, source_section)
            if lookup_section:
                report = _execution_warning_summary(report)
            (await self._finish(rid, _append_report_metadata(report, lookup_section), serialized(), ok=ok, review=review,
                         error=None if ok else error or RESEARCH_END_ERRORS.get(outcome)))

        async def budget_denied(stage: str, review: dict | None) -> None:
            (await end("research_failed", cp2_report + f"\n\nResearch {stage} stopped: the budget was not approved.",
                False, review, {"stage": stage, "budget": self.budget_denials[rid]},
                error="예산 승인을 받지 못해 연구를 멈췄어요"))

        stored = contract.get("review") or {}
        review: dict = {}
        if stored.get("plan_sha256") == plan_hash:
            saved = {"verdict": stored.get("verdict"), "issues": stored.get("issues"),
                     **{key: stored[key] for key in ("checklist", "blind_comparison") if key in stored}}
            review = lenient_review(saved, RESEARCH_LANE_REVIEW_SCHEMA) or {}
        if not review:
            catalog = topic_checklists.load()
            prompt = RESEARCH_REVIEW_PROMPT.format(
                request=text, plan_sha256=plan_hash, plan=_research_plan_digest(req["plan"]),
                packs=render_pack_review(packs),
                ledgers=self._research_ledgers(steps, results, ledgers, refused, unsupported, artifact_sha256, n)) + \
                plan_review_context(req.get("plan"), catalog, req.get("analysis_precedents"))
            carried = contract.get("continuation") or {}
            if carried.get("plan_sha256") == plan_hash:
                prompt += research_continuation.review_prompt(carried.get("p1_issues") or [],
                                                              approval_note(carried.get("pi_request")))
            prompt += blind_draft.review_block(contract.get(blind_draft.KEY))
            prompt += approval_note_block("CP2", cp2_notes, CP2_REVIEW_NOTE_RULE) + self._spending_cap_line(rid)
            if contract.get("result_warnings"):
                prompt += "\nCP2 result limitations:\n" + "\n".join(contract["result_warnings"])
            reply: TaskResult | None = None
            for parse_attempt in (1, 2):
                reply = await self.run_step(Task(
                    agent_id=reviewer, request_id=rid, output_schema=RESEARCH_LANE_REVIEW_SCHEMA,
                    prompt=prompt if parse_attempt == 1 else prompt + RESEARCH_REVIEW_RETRY,
                    meta={**refs, "kind": "review", "outputs": ["outputs/review.json"], "parse_attempt": parse_attempt, "request": text,
                          "title": "연구 리뷰"}))
                if rid in self.budget_denials:
                    (await budget_denied("review", None))
                    return
                parsed = (lenient_review(reply.structured, RESEARCH_LANE_REVIEW_SCHEMA)
                          or lenient_review(extract_json(reply.text), RESEARCH_LANE_REVIEW_SCHEMA))
                if reply.ok and parsed:
                    review = blind_draft.without_unfounded_comparison(with_p1_verdict(review_with_checklist(
                        parsed, req.get("plan"), catalog, req.get("analysis_precedents"), research=True)),
                        contract.get(blind_draft.KEY))
                    break
            if not review:
                reason = (reply.error if reply else None) or "missing or invalid research review"
                (await end("research_review_unparsed", cp2_report + f"\n\nResearch review: unparsed ({reason}).", False,
                    {"status": "review_unparsed", "reason": reason}))
                return
            contract["review"] = {"plan_sha256": plan_hash, "reviewer": reviewer, **review}
            await persist_request(self.hub, rid)
        issues = _research_issue_lines(review["issues"])
        if review["verdict"] == "revise":
            note = await self._research_continue(rid, review, results)
            if note is None:  # the PI continued: a new plan, CP1 and run took this request over
                return
            (await end("research_review_revise", "\n".join([
                cp2_report, "", f"Research review ({reviewer}): revise.", *issues,
                "labhq does not re-run a frozen research PLAN; a fixed plan needs a new CP1 approval of its hash.",
                *([note] if note else [])]),
                False, review))
            return

        citable, other = claim_rows(ledgers, unsupported, refused=refused)
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
                gaps="\n".join([*_research_gap_lines(ledgers, lookups),
                                *(contract.get("result_warnings") or [])]) or "(none)", verdict=review["verdict"],
                issues="\n".join(issues) or "(none)", results=self.format_results(steps, results, n)) +
                plan_report_context(req.get("plan"), topic_checklists.load(),
                                    req.get("analysis_precedents")) +
                precedent_texts.synthesis_note(req.get("analysis_precedents")) +
                approval_note_block("CP2", cp2_notes, CP2_REPORT_NOTE_RULE) +
                ("\n\nRecorded claim/source metadata (not a new support judgment):\n" + clip(source_section, n)
                 if source_section else "") +
                ("\n\nEarlier rounds of this request:\n" + "\n".join(earlier) + "\n" + EARLIER_ROUNDS_RULE
                 if (earlier := earlier_round_lines(contract)) else ""),
            meta={**refs, "kind": "synthesis", "request": text, "title": "연구 보고서 작성",
                  "final_budget_turn": True,
                  **({"workdir": workdir} if workdir else {})}))
        if not final.ok:
            if rid in self.budget_denials:
                (await budget_denied("synthesis", review))
            else:
                (await end("research_failed", cp2_report + f"\n\nSynthesis failed: {final.error}", False, review,
                    {"stage": "synthesis", "error": final.error or "unknown error"},
                    error=f"보고서 작성에 실패했어요: {short(final.error or '원인을 알 수 없어요', 120)}"))
            return
        # A report that finished keeps its text and check even if the budget card after it was denied; the denial
        # only fails the request, as in the generic synthesis (run_step: a completed attempt keeps its result).
        body, model_appendix = _split_report_appendix(report_body(final.text))
        check = check_report(body, ledgers, unsupported=unsupported, refused=refused,
                             artifact_sha256=artifact_sha256)
        # One failed claim check used to end the request after the whole study ran (17th run: no anchor at all).
        # The writer gets the problems and the citable anchors back, up to REPORT_CORRECTIONS times (#956).
        corrections = 0
        while check["problems"] and corrections < REPORT_CORRECTIONS:
            corrections += 1
            try:
                # The whole report, never clipped: the corrected text replaces it, so nothing may be elided (#956).
                fixed = await self.run_step(Task(
                    agent_id=self.cfg.cso_agent, request_id=rid, resume_session_id=session_id,
                    prompt=report_correction(check["problems"],
                                             "\n".join(map(_citable_claim_line, citable)) or "(none)", body),
                    meta={**refs, "kind": "synthesis", "request": text, "title": "연구 보고서 교정",
                          "report_correction": corrections, "final_budget_turn": True,
                          **({"workdir": workdir} if workdir else {})}))
            except BudgetExceeded:
                break  # a refused budget card: the finished report and its problems stand, as for a late denial
            if rid in self.budget_denials or not fixed.ok:
                break  # the report written so far and its problems stand
            body, model_appendix = _split_report_appendix(report_body(fixed.text))
            check = check_report(body, ledgers, unsupported=unsupported, refused=refused,
                                 artifact_sha256=artifact_sha256)
        if corrections:
            contract["report_corrections"] = corrections
        contract["report_check"] = check
        claim_check = (["Claim check: the report is incomplete.\n" +
                        "\n".join(_problem_lines(check["problems"], "- "))] if check["problems"] else [])
        if check["problems"]:
            body = _execution_warning_summary(body)
        fallback = "" if EARLIER_ROUNDS_HEADING.search(body) else _earlier_rounds_fallback(contract)
        if fallback:
            body = _before_bench_block(body, fallback)
        body_reference = _review_reference(review)
        if body_reference and not REVIEW_REFERENCE_HEADING.search(body):
            body = _before_bench_block(body, body_reference)
        carried = contract.get("continuation") or {}
        pinned = (unreceipted_references(contract, carried.get("reuse") or [])
                  if carried.get("plan_sha256") == contract["plan_sha256"] else [])
        if pinned:  # the PI reads the report, not the audit: say it there too (#1061 review)
            body = _before_bench_block(
                body, f"참고: 재사용한 단계가 쓴 DOI·PMID 참조 {len(pinned)}건은 식별자만 고정했고 내용은 확인하지 "
                      f"않았습니다({', '.join(pinned[:5])}{', ...' if len(pinned) > 5 else ''}).")
        req["execution_record"] = _appendix_sections("", [*claim_check, cp2_audit])
        report = body.rstrip() + ("\n\n" + model_appendix if model_appendix else "")
        (await end("report_incomplete" if check["problems"] else "research_reported", report,
            not check["problems"] and rid not in self.budget_denials, review))

    async def _research_continue(self, rid: str, review: dict | None, results: dict[str, TaskResult], *,
                                 cp2_note: str | None = None) -> str | None:
        """Continue through a new plan and CP1 after a research review "revise" or a CP2 revise (#90, #58, R10).

        After a review "revise" the PI is asked on a ``research_continue`` card. After a CP2 revise (``cp2_note``, the
        PI's CP2 note) no card is shown: the PI already chose to revise there, so the note is the revision request
        and the new CP1 is where the PI approves the new plan. Both count against research.revise_continuations.

        Returns None when the request continued (it then re-plans and runs again here), else a line for the report
        ("" when no card was shown). The decision is saved with the plan hash it answers, so a restart neither asks
        again nor forgets an approval. On approval the finished round (plan, results, CP2 receipt, review) is archived
        in ``research_contract.rounds`` in the same save that raises ``round``; the results stay in place until the
        new plan is adopted (``_adopt_continuation_plan``).
        """
        req = self.hub.requests[rid]
        contract = req["research_contract"]
        plan_hash = contract["plan_sha256"]
        rounds = list(contract.get("rounds") or [])
        decided = contract.get("continue_decision") or {}
        cp2_revise = cp2_note is not None
        if decided.get("plan_sha256") != plan_hash:
            limit = self.hub.s.research.revise_continuations
            if len(rounds) >= limit:
                if cp2_revise:
                    return (f"이어 가기 상한(research.revise_continuations={limit})에 닿아 수정 요청으로 새 계획을 "
                            "세우지 않았습니다.")
                return (f"이어 가기 상한(research.revise_continuations={limit})에 닿아 새 CP1을 묻지 않았습니다."
                        if limit else "")
            issues = [] if cp2_revise else research_continuation.p1_issues(review)
            round_no = len(rounds) + 2
            if cp2_revise:  # the CP2 card that asked for the revision is the decision
                receipt = (contract.get("checkpoints") or {}).get("cp2") or {}
                decision = {"approved": True, "approval_id": receipt.get("approval_id"),
                            "decided_at": receipt.get("decided_at")}
            else:
                decision = await self.hub.request_approval(
                    kind=research_continuation.CONTINUE_GATE, request_id=rid,
                    summary=(f"연구 리뷰가 revise(P1 {len(issues)}건)로 끝났습니다. 리뷰 지적을 넣은 새 계획으로 "
                             f"이어 갈까요({round_no}차)? 승인하면 CSO가 새 계획을 쓰고 새 CP1을 받습니다. 사양이 같고 "
                             "산출 hash가 그대로인 완료 단계는 다시 돌리지 않습니다. 거절하면 지금처럼 끝납니다."),
                    detail={"gate": research_continuation.CONTINUE_GATE, "plan_sha256": plan_hash, "round": round_no,
                            "limit": limit, "p1_issues": issues})
            approved = bool(decision.get("approved"))
            trigger = {"trigger": "cp2_revise"} if cp2_revise else {}
            decided = {"plan_sha256": plan_hash, "decision": "approved" if approved else "declined",
                       "approval_id": decision.get("approval_id"), "decided_at": decision.get("decided_at"),
                       "note": "" if cp2_revise else str(decision.get("note") or "").strip(), **trigger,
                       **({"state": decision["state"]} if decision.get("state") else {})}
            contract["continue_decision"] = decided
            if approved:
                # Everything this round's frozen plan owns, so an unstarted continuation can put it back whole.
                rounds.append({**research_continuation.archive_round(
                    req, {sid: result.model_dump(mode="json") for sid, result in results.items()}),
                    "round": len(rounds) + 1})
                contract["rounds"] = rounds
                contract["round"] = round_no
                # The note rides with the continuation: adopting the new plan drops continue_decision (R11).
                contract["continuation"] = {"round": round_no, "from_plan_sha256": plan_hash, "p1_issues": issues,
                                            "approval_id": decision.get("approval_id"), **trigger,
                                            **({"pi_request": cp2_note} if cp2_revise else {}),
                                            **({"note": decided["note"]} if decided["note"] else {})}
            await persist_request(self.hub, rid)
        if decided.get("decision") != "approved":
            return ("이어 가기 카드가 답 없이 닫혀 새 CP1을 열지 않았습니다." if decided.get("state") == "timed_out"
                    else "PI가 새 CP1로 이어 가기를 거절했습니다.")
        await self._emit(rid, "request.continued", {"round": contract.get("round"), "from_plan_sha256": plan_hash,
                                                    **({"trigger": "cp2_revise"} if cp2_revise else {})})
        await self.run_request(rid, continuation=True)
        return None

    def _adopt_continuation_plan(self, rid: str, plan: dict, pack_hashes: dict[str, str]) -> None:
        """Pair the new plan with the previous round's results: keep only reusable steps' results (#90).

        Called before the save that stores the new plan, so a restart sees the plan and its pruned results together.
        The reuse is provisional until input/output proof is checked before CP1 (``_prepare_research_reuse``).
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
        if (carried.get("refusal_retry") or {}).get("state") in {"approved", "planning"}:
            carried["refusal_retry"]["state"] = "planned"  # ordinary CP1/reuse resume from this point
        carried.update(plan_sha256=research_plan_sha256(plan), verified=False, dispatch_started=False)
        carried.pop("refused_reuse", None)
        carried.pop("reuse_snapshot", None)
        carried.pop("approval_required", None)
        carried.pop("completed", None)
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
        # Preserve only the execution proof belonging to retained results. A rerun starts without any
        # historical signature; dispatch will record what it actually uses.
        completed = contract['continuation'].get('completed') or []
        for key in ('reuse_tools', 'reuse_no_request_inputs', 'reuse_input_proof'):
            recorded = req.get(key) or {}
            previous = (contract.get('rounds') or [{}])[-1].get(key) or {}
            req[key] = {sid: (recorded if sid in completed else previous).get(sid) for sid in carry.reuse}
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

    def _research_reuse_tools(self, rid: str, plan: dict) -> dict[str, str]:
        """Declared execution configuration, including the request's pinned agent identity."""
        agents = dict(hub_agents(self.hub, rid))
        for step in plan.get('steps') or []:
            aid = step['agent_id']
            identity = self._request_identity(rid, aid)
            if identity:
                agents[aid] = {**agents.get(aid, {}), **identity}
        return research_continuation.tool_signatures(plan, agents, self.hub.s.engines)

    async def _continuation_candidates(self, rid: str, plan: dict, sealed: dict | None = None):
        """Structural identity, declared-input proof, then the existing safe output walker."""
        req = self.hub.requests[rid]
        contract = req['research_contract']
        carried = contract['continuation']
        previous = contract['rounds'][-1]
        tools = self._research_reuse_tools(rid, plan)
        from ..research.input_proof import capture
        input_proof = await asyncio.to_thread(capture, req, self.hub.s)
        refused = dict(sealed.get('rerun') or {}) if sealed else {}
        carry = research_continuation.carry_over(previous, plan, contract.get('pack_snapshot'),
                                                carried.get('p1_issues') or [], refused)
        refused.update(research_continuation.proof_problems(previous, plan, carry, tools, req, input_proof))
        carry = research_continuation.carry_over(previous, plan, contract.get('pack_snapshot'),
                                                carried.get('p1_issues') or [], refused)
        try:
            failures = (await asyncio.to_thread(research_continuation.verify_reuse, carry.results, self.hub.s)
                        if carry.reuse else {})
        except Exception:
            failures = {sid: 'output verification unavailable' for sid in carry.reuse}
        refused.update({sid: f'output hash check: {reason}' for sid, reason in failures.items()})
        carry = research_continuation.carry_over(previous, plan, contract.get('pack_snapshot'),
                                                carried.get('p1_issues') or [], refused)
        # A stopped, partially executed round owns its completed results. Reapproval may seal them as
        # current-round reuse, but must not replace them with the previous archive or silently run them again.
        completed = set(carried.get('completed') or [])
        if carried.get('dispatch_started') and sealed is None:
            completed.update(sid for sid, row in (req.get('results') or {}).items()
                             if sid not in (carried.get('reuse') or []) and row.get('ok'))
        if completed:
            current = {**req, 'plan_sha256': contract['plan_sha256'],
                       'pack_snapshot': contract.get('pack_snapshot'), 'round': contract.get('round')}
            retained = research_continuation.carry_over(current, plan, contract.get('pack_snapshot'), [])
            problems = research_continuation.proof_problems(current, plan, retained, tools, req, input_proof)
            for step in plan['steps']:
                sid = step['id']
                if sid not in completed:
                    continue
                if (sid not in retained.reuse or sid in problems
                        or any(dep not in set(carry.reuse) | completed for dep in step.get('depends_on') or [])):
                    raise research_continuation.ReuseApprovalRequired(
                        'completed current-round result lacks unchanged input/tool proof; restore proof or revise plan')
                if sid not in carry.reuse:
                    carry.reuse.append(sid)
                carry.rerun.pop(sid, None)
                carry.results[sid] = retained.results[sid]
                carry.reused_from[sid] = retained.reused_from[sid]
                if sid in retained.salvage:
                    carry.salvage[sid] = retained.salvage[sid]
            order = {step['id']: i for i, step in enumerate(plan['steps'])}
            carry.reuse.sort(key=order.__getitem__)
            try:
                failures = await asyncio.to_thread(research_continuation.verify_reuse,
                                                   {sid: carry.results[sid] for sid in completed}, self.hub.s)
            except Exception as exc:
                raise research_continuation.ReuseApprovalRequired(
                    'completed current-round output verification unavailable') from exc
            if failures:
                raise research_continuation.ReuseApprovalRequired('completed current-round artifacts changed')
        snapshot = research_continuation.reuse_snapshot(plan, contract.get('pack_snapshot') or {}, carry, tools)
        return carry, snapshot

    async def _prepare_research_reuse(self, rid: str, plan: dict) -> None:
        """Before CP1: only verified executable candidates enter the card and saved result set."""
        carry, snapshot = await self._continuation_candidates(rid, plan)
        carried = self.hub.requests[rid]['research_contract']['continuation']
        if carried.get('dispatch_started'):
            carried['completed'] = [sid for sid in carry.reuse
                                    if carry.reused_from[sid].get('round') == carried.get('round')]
        self._apply_carry(rid, carry)
        self.hub.requests[rid]['research_contract']['continuation'].update(
            reuse_snapshot=snapshot, verified=False)
        await persist_request(self.hub, rid)

    async def _verify_research_reuse(self, rid: str, plan: dict) -> bool:
        """Recheck the CP1 seal on every resume/dispatch boundary; never downgrade reuse to a rerun."""
        req = self.hub.requests[rid]
        contract = req['research_contract']
        carried = contract['continuation']
        sealed = carried.get('reuse_snapshot')
        approval = contract.get('approval') or {}
        valid = False
        try:
            if (approval.get('approved') and isinstance(sealed, dict)
                    and approval.get('target_sha256') == contract['plan_sha256']
                    and approval.get('reuse_snapshot_sha256') == sealed.get('sha256')
                    and sealed.get('plan_sha256') == research_plan_sha256(plan) == contract['plan_sha256']
                    and research_plan_sha256(req['plan']) == contract['plan_sha256']):
                _, current = await self._continuation_candidates(rid, plan, sealed)
                saved = req.get('results') or {}
                valid = (current == sealed and carried.get('reuse') == sealed['reuse']
                         and all(research_continuation._digest(saved.get(sid)) == node['result_sha256']
                                 for sid, node in sealed['nodes'].items()))
        except (KeyError, TypeError, ValueError, research_continuation.ReuseApprovalRequired):
            valid = False
        if not valid:
            carried['verified'] = False
            carried['approval_required'] = 'reuse snapshot/proof changed after CP1; renewed approval required'
            contract['approval'] = {**approval, 'approved': False, 'status': 'approval_required'}
            req['outcome'] = 'reuse_approval_required'
            await persist_request(self.hub, rid)
            return False
        carried['refused_reuse'] = []
        await persist_request(self.hub, rid)
        return True

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
            if r and r.step_warnings:
                detail += "\nWarnings: " + "; ".join(r.step_warnings)
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
            if r and r.step_warnings:
                lines.append("Warnings: " + short("; ".join(r.step_warnings), 300))
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
            await persist_request(self.hub, rid)
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
            await persist_request(self.hub, rid)
            phase = solo_phase(req, self.cfg)
            if rid in self.budget_denials:
                (await self._finish(rid, result.text or result.error or "task failed",
                             {"direct": result.model_dump(mode="json")}, ok=False,
                             error=self.budget_denials[rid]))
                return True

        stored = req.get("solo_result")
        result = TaskResult.model_validate(stored) if isinstance(stored, dict) else None
        if phase == "review" and result is not None:
            reviewer = self.cfg.reviewer_agent
            if not reviewer or reviewer not in self.hub.agents:
                req["route_decision"] = {**decision, "mode": "team", "fallback": True,
                                         "reason": "solo science reviewer unavailable"}
                await persist_request(self.hub, rid)
                await self._emit(rid, "request.route", req["route_decision"])
                return False
            reviewed = await self.run_step(Task(
                agent_id=reviewer, request_id=rid, output_schema=REVIEW_SCHEMA,
                prompt=REVIEW_PROMPT.format(request=text, results=result.text) +
                       plan_review_context(req.get("plan"), topic_checklists.load(),
                                           req.get("analysis_precedents")),
                meta={**refs, "kind": "review", "outputs": ["outputs/review.json"], "revision": 0, "request": text,
                      "title": "단독 결과 과학 리뷰"}))
            parsed = lenient_review(reviewed.structured, REVIEW_SCHEMA) or lenient_review(
                extract_json(reviewed.text), REVIEW_SCHEMA)
            review = with_p1_verdict(review_with_checklist(
                parsed, req.get("plan"), topic_checklists.load(), req.get("analysis_precedents"),
                owner=SOLO_OWNER)
            ) if reviewed.ok and parsed else {
                "status": "review_unparsed", "reason": reviewed.error or "missing or invalid verdict"}
            req["solo_review"] = review
            await persist_request(self.hub, rid)
            await self._emit(rid, "request.review", {"revision": 0, **review})
            phase = solo_phase(req, self.cfg)
            if rid in self.budget_denials:
                (await self._finish(rid, result.text or result.error or "task failed",
                             {"direct": result.model_dump(mode="json")}, ok=False,
                             review=review, error=self.budget_denials[rid]))
                return True

        if phase == "fallback":
            reason = decision.get("reason") or (_solo_result_problem(result) if result else "")
            reason = reason or "solo science review did not accept the result"
            req["route_decision"] = {**decision, "mode": "team", "fallback": True, "reason": reason}
            await persist_request(self.hub, rid)
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
                else "비용 모름")
        paths = ", ".join(f"{result.workdir_id or 'unknown-workdir'}/{path}" for path in result.outputs)
        report = _append_report_metadata(report, [f"처리 방식: 단독 ({agent})", f"단독 턴 비용: {cost}",
                                                   f"산출 경로: {paths or 'none'}"])
        req["followup_agent_id"] = agent
        (await self._finish(rid, report, {"direct": result.model_dump(mode="json")}, ok=True, review=review))
        return True

    # ---------- request entry point ----------
    async def run_request(self, rid: str, resume: bool = False, continuation: bool = False) -> None:
        """``continuation``: a research request whose review ended "revise", or whose CP2 the PI answered with revise,
        goes on in this process through a new plan and CP1 (``_research_continue``); its running cost is kept, and no
        briefing runs again."""
        req = self.hub.requests[rid]
        from ..research import final_checkpoint as cp4
        if cp4.enabled(req) and (req.get("research_contract") or {}).get("final_draft"):
            cp4.reconcile(req)
            await persist_request(self.hub, rid)
            return  # a saved final draft never goes through synthesis again, even with changed settings
        req.pop("shutdown_evidence", None)  # a new attempt must not inherit the previous shutdown verdict
        refs = reference_meta(req)
        # Pointers ride with the request text, so briefing, plan, steps, review and report all see them (#36).
        text = (req["text"] + render_references(req.get("references")) +
                answered_questions_prompt(req.get("clarifications")))
        if not continuation or resume:
            self.cost[rid] = float(req.get("cost_usd") or 0)
            self.cost_tasks[rid] = set(req.get("cost_by_task") or {})
            self._seed_cost(rid, req)
            (await self._drop_unsent_holds(rid))
        try:
            research_pilot = bool(self.hub.s.research.enabled)
            intake = (classify_intake(req["text"], req.get("work_kind", "auto"),
                                      scope_status=req.get("scope_status", "in_scope"), mode=req["mode"])
                      if research_pilot else None)
            # A request that already carries a research contract stays research whatever the config now says,
            # so it never reaches the generic re-plan, review or synthesis paths (#90 CP2).
            research_lane = bool(intake and intake.work_kind == "research") or bool(req.get("research_contract"))
            research_execution = bool(research_lane and self.hub.s.research.evidence_checkpoint
                                      and req["mode"] != "plan_only")
            if research_lane:
                await self._semantic_devices_check(rid, "request_start")
            if req.get("research_contract") and not research_pilot:
                req["outcome"] = "research_disabled"
                (await self._finish(rid, "This request carries a frozen research contract, but research.enabled is now "
                             "off. No step was dispatched and nothing was re-planned; turn the research pilot "
                             "back on to resume it.", {}, ok=False))
                return
            if intake:
                req["intake"] = intake.model_dump(mode="json")
                await persist_request(self.hub, rid)
            if req["mode"] == "direct":
                if research_lane:
                    req["outcome"] = "needs_research"
                    (await self._finish(rid, "연구로 지정한 요청은 직원 한 명에게 직접 보낼 수 없습니다. 팀(CSO)에게 보내면 "
                                 "계획을 세워 CP1 승인을 받습니다.", {}, ok=False))
                    return
                await self._plan_clarification(rid, {})
                vocab = self._output_vocab()
                res = await self.run_step(Task(
                    agent_id=req["agent_id"], request_id=rid,
                    prompt=text + (output_types.direct_prompt_rule(vocab) if vocab else ""),
                    output_schema=output_types.direct_result_schema() if vocab else None,
                    budget_usd=req.get("budget_usd"),
                    meta={**refs, "kind": "direct", "title": text[:100],
                          "project_dirs": req.get("project_dirs", []),
                          **({"output_types_vocab": vocab.sha256} if vocab else {})}))
                if res.output_types_stats:
                    req["output_types_stats"] = res.output_types_stats
                    await persist_request(self.hub, rid)
                (await self._finish(rid, res.text, {"direct": res.model_dump(mode="json")},
                             ok=res.ok and rid not in self.budget_denials))
                return

            # A contract hire on probation stays on the web roster as 수습 but is not offered to the CSO (#424).
            all_agents = [a for a in hub_agents(self.hub, rid).values() if not not_yet_active(a)]
            known = {a["id"] for a in all_agents}
            # The configured orchestration agents, not only the default ids, stay out of the worker roster.
            orchestration = set(ORCHESTRATION_ROLES) | {
                x for x in (self.cfg.cso_agent, self.cfg.chief_of_staff_agent, self.cfg.reviewer_agent) if x}
            roster = [a for a in all_agents if a["id"] not in orchestration]
            topic_vocab_for_checks = output_vocab.current()
            try:
                checklist_catalog = (topic_checklists.load(vocab=topic_vocab_for_checks)
                                     if topic_vocab_for_checks is not None else {})
            except (OSError, ValueError) as exc:
                checklist_catalog = {}
                if research_lane:
                    semantic_devices.log_exception("checklist_load_failed", exc)
                    record = req.get("semantic_devices") or {}
                    semantic_devices.add_error(record, code="checklist_load_failed")
                    req["semantic_devices"] = record
                    await persist_request(self.hub, rid)
                    await self._emit(rid, "request.semantic_devices", record)
            n = self.cfg.context_chars_per_step
            frozen_pack_snapshot = ((req.get("research_contract") or {}).get("pack_snapshot")
                                    if resume and not continuation and research_lane else None)
            # A contract frozen before topic selection has no pack_applicability record: it resumes by the pack rule
            # it was approved under and never gains the record, so a second restart takes the same path (#390 review).
            legacy_pack_contract = (frozen_pack_snapshot is not None
                                    and "pack_applicability" not in (req.get("research_contract") or {}))
            try:
                configured_pack_defs = (packs_for_snapshot(self.hub.s, frozen_pack_snapshot)
                                        if frozen_pack_snapshot is not None else
                                        configured_packs(self.hub.s) if research_lane else {})
            except (OSError, ValueError) as exc:
                if research_lane:
                    code = "pack_snapshot_missing" if frozen_pack_snapshot is not None else "pack_load_failed"
                    semantic_devices.log_exception(code, exc)
                    record = req.get("semantic_devices") or {}
                    semantic_devices.add_error(record, code=code)
                    req["semantic_devices"] = record
                    await persist_request(self.hub, rid)
                    await self._emit(rid, "request.semantic_devices", record)
                    if frozen_pack_snapshot is not None:
                        req["outcome"] = "pack_snapshot_missing"
                        (await self._finish(rid, "CP1에서 고정한 research pack snapshot을 복원하지 못해 재개를 중단했습니다. "
                                     "기존 snapshot은 그대로 보존했습니다.", {}, ok=False,
                                     error="고정한 research pack snapshot을 복원하지 못했습니다."))
                        return
                configured_pack_defs = {}
            packs = configured_pack_defs
            active_pack_hashes = pack_snapshot(packs)
            # The first plan, research plan and re-plan after resume (#271) all use this same snapshot.
            capabilities = format_capabilities(roster, getattr(self.hub, "runner_capabilities", None),
                                               run_where="wsl" if req.get("wsl_pinned") else None,
                                               run_where_note=req.get("run_where_note"))

            async def finish_research_plan(plan: dict[str, Any]) -> bool:
                async def end_revised(new_rid: str, note: str) -> None:
                    # Not a failure: the PI asked for another plan, which the new request writes (CP1 수정 요청).
                    req["outcome"] = "plan_revision_requested"
                    (await self._finish(rid, f"CP1에서 계획 수정을 요청해 이 요청은 단계를 돌리지 않고 끝냅니다. PI 메모를 넣은 "
                                 f"새 요청 {new_rid}에서 계획을 다시 세우고 새 CP1을 받습니다."
                                 + (f"\nPI 메모: {note}" if note.strip() else ""), {}, ok=True))

                stored = req.get("research_contract") or {}
                previous = stored.get("approval")
                approval = refresh_plan_approval(plan, previous)
                plan_sha256 = approval.get("current_sha256") or approval.get("target_sha256")
                # A resume card that showed this plan and was approved stands for CP1 once (#654). It is dropped in the
                # save that records the receipt, so a stop before that save keeps the PI's answer.
                resume_cp1 = req.get("resume_cp1")
                if approval.get("status") == "approved":
                    req.pop("resume_cp1", None)
                frozen_checklist = (stored.get("checklist_snapshot")
                                    if stored.get("plan_sha256") == plan_sha256
                                    and isinstance(stored.get("checklist_snapshot"), list)
                                    else checklist_snapshot(plan, checklist_catalog, req.get("analysis_precedents")))
                # Fixed with the request: a resume keeps its own execution flag, checkpoint receipts and failure
                # record. The current config applies only to a request that has no contract yet (#90 CP2).
                execution_enabled = (bool(stored["execution_enabled"]) if "execution_enabled" in stored
                                     else research_execution)
                req["research_contract"] = {
                    **stored,
                    "schema_version": stored.get("schema_version", (req.get("research_options") or {}).get("contract_version", 1)),
                    "work_kind": "research",
                    "execution_enabled": execution_enabled,
                    "final_checkpoint": bool(stored.get("final_checkpoint", (req.get("research_options") or {}).get("final_checkpoint", False)) and execution_enabled),
                    "plan_sha256": approval.get("current_sha256") or approval.get("target_sha256"),
                    "pack_snapshot": (frozen_pack_snapshot if frozen_pack_snapshot is not None else active_pack_hashes),
                    "checklist_snapshot": frozen_checklist,
                    **({} if legacy_pack_contract else {"pack_applicability": plan.get("pack_applicability") or {}}),
                    "approval": approval,
                }
                await persist_request(self.hub, rid)
                plan_hash = req["research_contract"]["plan_sha256"]
                carried = req["research_contract"].get("continuation") or {}
                # Every continuation CP1/resume uses the same sealed executable reuse contract (#797).
                reuse_pending = carried.get("plan_sha256") == plan_hash
                archived_before_adoption = (carried and not carried.get('plan_sha256')
                                            and self._archived_continuation_decision(req))
                if carried and not reuse_pending and not archived_before_adoption:
                    await self._verify_research_reuse(rid, plan)
                    (await self._finish(rid, "이어 가기 계획과 재사용 snapshot이 다릅니다. 새 CP1 승인이 필요합니다.",
                                 {}, ok=False, error="reuse approval required"))
                    return False
                if reuse_pending and approval.get("status") != "approved":
                    await self._prepare_research_reuse(rid, plan)
                # Restarted after a CP1 수정 요청 had already opened its new request (found by its first save).
                lookup = getattr(self.hub, "revised_request_of", None)
                revised_to = req.get("revised_to") or (lookup(rid) if callable(lookup) else None)
                if revised_to:
                    req["revised_to"] = revised_to  # saved by _finish: the link survives a stop before it was written
                    (await end_revised(revised_to, str((stored.get("approval") or {}).get("note") or "")))
                    return False
                # CP1 수정 요청 opens a new request from the PI's note (Hub.create_revised_request), so only at a first
                # CP1 before any step: not in a continuation round (its plan rests on that round's results), and not
                # on a re-approval after steps ran (a new request would drop their results).
                can_revise = (not carried and not req.get("results") and approval.get("status") == "approval_required"
                              and callable(getattr(self.hub, "create_revised_request", None)))
                # Restarted after the PI chose 수정 요청 on this very plan but before its new request opened: carry the
                # saved decision out instead of asking CP1 again (#514 review).
                decided = previous if isinstance(previous, dict) else {}
                if (can_revise and decided.get("choice") == "revise" and decided.get("target_sha256") == plan_hash
                        and str(decided.get("note") or "").strip()):
                    approval = dict(decided)
                    req["research_contract"]["approval"] = approval
                    await persist_request(self.hub, rid)
                elif approval.get("status") != "approved":
                    offered_reuse_sha = ((carried.get('reuse_snapshot') or {}).get('sha256')
                                         if reuse_pending else None)
                    device_record = await self._semantic_devices_check(rid, "cp1")
                    device_warning = semantic_devices.warning(device_record)
                    # The hash stays in detail.target_sha256; the card shows the plan itself (PI 점검 R10).
                    summary = ("CP1 연구 계획 승인: 고정할 질문·방법·완료/중단 조건·데이터 범위·적용 pack을 "
                               "확인하고 승인하세요." + (" 고칠 점이 있으면 메모와 함께 수정 요청을 고르세요."
                                                      if can_revise else ""))
                    if device_warning:
                        summary = (device_warning + "\n" + semantic_devices.card_line(device_record)
                                   + "\n\n" + summary)
                    else:
                        summary = semantic_devices.card_line(device_record) + "\n" + summary
                    scope_warnings = [warning for warning in plan.get("warnings", [])
                                      if isinstance(warning, str) and warning.startswith(SCOPE_COVERAGE_WARNING)]
                    if scope_warnings:
                        summary += "\n" + "\n".join(scope_warnings)
                    summary += "\n" + _cost_estimate_line(req.get("plan_cost_estimate") or {})
                    paper_line = paper_skills.card_text(plan)
                    hiring_warnings = [w for w in plan.get("warnings", []) if "재배정" in w]
                    if hiring_warnings:
                        summary = "\n".join(hiring_warnings) + "\n" + summary
                    if paper_line:
                        summary = paper_line + "\n" + summary
                    pinned_note = ""
                    if reuse_pending:
                        source = ("CP2 수정 요청 반영" if carried.get("trigger") == "cp2_revise"
                                  else "리뷰 P1 반영")
                        reasons = list(dict.fromkeys((carried.get('rerun') or {}).values()))
                        reason_line = ('재사용 거부 이유: ' + '; '.join(reasons)[:240] + '. ' if reasons else '')
                        pinned = unreceipted_references(req["research_contract"], carried.get('reuse') or [])
                        if pinned:  # kept whole beyond the cut below, like the budget line: the PI approves the reuse here
                            pinned_note = (f"재사용한 단계가 쓴 DOI·PMID 참조 {len(pinned)}건은 식별자만 고정했고 "
                                           "내용은 확인하지 않았습니다.")
                        summary = (f"이어 가기 {carried.get('round')}차 계획({source}). 재사용: "
                                   f"{', '.join(carried.get('reuse') or []) or '없음'} · 다시 실행: "
                                   f"{', '.join(carried.get('rerun') or {}) or '없음'}. ") + reason_line + summary
                    budget, configured_source = self._research_budget(rid, req, plan)
                    # Kept whole beyond the 699-char cut: the web shows the summary, not detail.budget; the selection
                    # record line (#890) rides with it, so long paper-skill or continuation text cannot cut it off.
                    budget_line = (selection_record_line(plan) + " " + _budget_line(budget)
                                   + (" " + pinned_note if pinned_note else ""))

                    async def ask_cp1(prefix: str = "") -> dict:
                        card = prefix + summary
                        return await self.hub.request_approval(
                            kind="research_plan", request_id=rid,
                            summary=f"{card[:699 - len(budget_line)]} {budget_line}",
                            detail={"gate": "research_plan", "target_sha256": plan_hash,
                                    "plan_canonical": canonical_plan_json(plan),
                                    "pack_applicability": plan.get("pack_applicability") or {},
                                    "warnings": plan.get("warnings") or [],
                                    **({"plan_warnings": req.get("plan_warnings") or [],
                                        "plan_validation": req["plan_validation"]}
                                       if req.get("plan_validation", {}).get("semantic_checks") else {}),
                                    "paper_skills": paper_skills.records(plan),
                                    "protocol_revision": plan["protocol"]["revision"],
                                    "packs": plan["protocol"]["packs"],
                                    "scope_status": plan["intake"]["scope_status"],
                                    "budget": budget, "cost_estimate": req.get("plan_cost_estimate") or {},
                                    "semantic_devices": device_record,
                                    "revise_allowed": can_revise,
                                    **({"continuation": {
                                        **{key: copy.deepcopy(carried.get(key)) for key in
                                           ("round", "from_plan_sha256", "reuse", "rerun", "p1_issues")},
                                        **{key: copy.deepcopy(carried[key]) for key in
                                           ("trigger", "pi_request", "reuse_snapshot") if key in carried}}}
                                       if reuse_pending else {})})

                    # Only the plan the card showed (same hash), with nothing the card left out: a stopped device
                    # (the always-present configured_off note is not one), a paper-skill line, a re-hire note and a
                    # continuation keep the CP1 card (#654).
                    device_stopped = any(isinstance(e, dict) and e.get("code") != "configured_off"
                                         for e in (device_record or {}).get("errors") or [])
                    from_resume = (isinstance(resume_cp1, dict) and resume_cp1.get("approved") is True
                                   and resume_cp1.get("target_sha256") == plan_hash and not reuse_pending
                                   and not device_stopped and not paper_line and not hiring_warnings)
                    decision = ({"approved": True, "choice": "approve", "approval_id": resume_cp1.get("approval_id"),
                                 # The PI's own memo on the resume card reaches the step prompts as the CP1 memo.
                                 "note": str(resume_cp1.get("note") or "").strip() or "재개 카드에서 이 계획과 함께 승인",
                                 "decided_at": resume_cp1.get("decided_at", time.time())}
                                if from_resume else await ask_cp1())
                    # A 수정 요청 needs the note it re-plans from; the web and CLI refuse an empty one first (R10 likewise).
                    for _ in range(2):
                        if not (can_revise and decision.get("choice") == "revise"
                                and not str(decision.get("note") or "").strip()):
                            break
                        decision = await ask_cp1("수정 요청에 메모가 없어 다시 묻습니다. 고칠 점을 메모에 적어 주세요. ")
                    approval = freeze_plan(plan, decision)
                    if from_resume:
                        approval["via"] = "resume_card"
                    if reuse_pending:
                        approval['reuse_snapshot_sha256'] = offered_reuse_sha
                    if can_revise and decision.get("choice") == "revise" and not approval["approved"]:
                        approval["choice"] = "revise"
                    approval.update(request_id=rid, protocol_revision=plan["protocol"]["revision"])
                    if approval.get("approved") and "plan_budget_usd" in budget:
                        # PI 점검 R17: the plan's budget on top of what is spent now becomes the enforced cap, in the
                        # save that records CP1, and only here: a resume of an approved plan keeps a cap the PI raised
                        # on a budget card since. Counted again at approval, not as the card showed it.
                        budget, configured_source = self._research_budget(rid, req, plan)
                        approval["budget"] = budget
                        req["budget_usd"] = budget["cap_usd"]
                        req["research_contract"].setdefault("budget_cap", {
                            "configured_cap_usd": budget["configured_cap_usd"], "source": configured_source})
                    req["research_contract"]["approval"] = approval
                    req.pop("resume_cp1", None)  # consumed by this very save
                    await persist_request(self.hub, rid)
                approved = bool(approval.get("approved"))
                if not approved and approval.get("status") == "timed_out":
                    req["outcome"] = "plan_timed_out"  # no answer is not a rejection (R5)
                    (await self._finish(rid, "CP1 계획 카드에 기한 안에 답이 없어 연구 단계를 보내지 않고 끝냈습니다"
                                 "(policy.approvals.pi_decision_timeout_s). 같은 요청을 다시 보내면 새 CP1이 뜹니다.",
                                 {}, ok=False, error="CP1 계획 카드 시간 초과"))
                    return False
                revision_note = str(approval.get("note") or "").strip()
                if not approved and approval.get("choice") == "revise" and can_revise and revision_note:
                    try:
                        new_rid = await create_revised_request(self.hub, rid, revision_note)
                    except (KeyError, ValueError) as error:  # a project, CSO model or reference no longer allowed
                        req["outcome"] = "plan_revision_failed"
                        (await self._finish(rid, "CP1 수정 요청으로 새 요청을 열지 못해 이 요청은 단계를 돌리지 않고 끝납니다"
                                     f"({safe_failure_cause(str(error))}). 아래 메모를 넣어 요청을 다시 보내 주세요."
                                     f"\nPI 메모: {revision_note}", {}, ok=False, error="CP1 수정 요청 실패"))
                        return False
                    (await end_revised(new_rid, revision_note))
                    return False
                if not approved:
                    req["outcome"] = "plan_rejected"
                    note = str(approval.get("note") or "").strip()
                    (await self._finish(rid, "연구 계획을 승인하지 않아 단계를 실행하지 않았습니다. " +
                                 ("계획만 고치려면 다음에는 거절 대신 CP1 수정 요청을 메모와 함께 고르세요(새 요청으로 다시 "
                                  "계획합니다)." if can_revise else "계획을 바꾸려면 요청 문장을 고쳐 새로 보내세요.") +
                                 (f"\nPI 메모: {note}" if note else ""), {}, ok=False,
                                 error="연구 계획(CP1) 거절"))
                    return False
                if reuse_pending and not await self._verify_research_reuse(rid, plan):
                    (await self._finish(rid, "재사용 산출 또는 승인 snapshot의 증거가 바뀌어 중단했습니다. "
                                 "재실행하지 않았으며, 새 CP1 승인이 필요합니다.", {}, ok=False,
                                 error="reuse approval required"))
                    return False
                # The cost so far is judged against the cap CP1 just set, before any end without dispatch: a plan_only
                # or checkpoint-off request would otherwise finish over its approved budget without a card (#499).
                await self._check_budget(rid, block=False)
                if rid in self.budget_denials:
                    req["research_contract"]["failure"] = {"steps": [], "plan_sha256": plan_hash, "stage": "cp1",
                                                           "budget": self.budget_denials[rid]}
                    req["outcome"] = "research_failed"
                    await persist_request(self.hub, rid)
                    (await self._finish(rid, "Research plan approved, but the cost so far passed its spending cap and the "
                                 "budget card was not approved; no employee research step was dispatched.", {},
                                 ok=False))
                    return False
                if not execution_enabled:
                    req["outcome"] = "plan_approved"
                    (await self._finish(rid, "Research plan frozen and approved. Evidence checkpoint is off, so the "
                                 "request stops before employee dispatch.", {}, ok=True))
                    return False
                if reuse_pending:
                    # Proof alone does not authorize execution: rollback must still cover a denied budget card.
                    carried['verified'] = True
                if not await self._blind_draft(rid, req, text, refs):
                    return False
                req["outcome"] = "research_running"
                await persist_request(self.hub, rid)
                return True

            async def prepare_recruits():
                nonlocal roster, known
                req["plan"] = await recruit_planning.prepare_plan(
                    req["plan"], rid, self.hub, lambda task: self._step_task(rid, self.run_step(task)))
                all_available = [a for a in self.hub.agents.values() if not not_yet_active(a)]
                known = {a["id"] for a in all_available}
                roster = [a for a in all_available if a["id"] not in orchestration]
                # Apply the existing agent, DAG and external-data checks to the final assignments too.
                research_limit = (len(req["plan"].get("branches") or []) * self.cfg.max_branch_steps + 3
                                  if req["plan"].get("branches") else self.cfg.max_steps)
                validate_steps(req["plan"]["steps"], known, research_limit, orchestration,
                               stored_environment=any("environment_binding" in s or "environment_bindings" in s
                                                      for s in req["plan"]["steps"]),
                               guard=self._external_guard(rid), tier_rules=self._tier_rules())
                await persist_request(self.hub, rid)

            if resume and not continuation and req.get("plan", {}).get("steps"):
                if not research_lane and not await self._scope_gate(rid, req):
                    return
                if not req.get("research_contract") and not req.get("results"):
                    await prepare_recruits()
                    saved_steps = req["plan"]["steps"]
                    if (any(ENV_LOCK_OUTPUT in s.get("outputs", []) for s in saved_steps)
                            and not any("environment_binding" in s or "environment_bindings" in s
                                        for s in saved_steps)):
                        req["plan"]["steps"] = self._bind_request_environments(rid, saved_steps)
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
                                                       max_branches=self.cfg.max_branches,
                                                       max_branch_steps=self.cfg.max_branch_steps,
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
                            await persist_request(self.hub, rid)
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
                    (await self._finish(rid, "Plan completed.", {}, ok=True))
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
                                                     reject_excess=True,
                                                     stored_environment=any(
                                                         "environment_binding" in s or "environment_bindings" in s
                                                         for s in req["plan"]["steps"]),
                                                     tier_rules=self._tier_rules())
                    req["plan"] = {**req["plan"], "steps": steps,
                                   "warnings": [*(req["plan"].get("warnings") or []), *warnings]}
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                decision, created = self._ensure_route_decision(req, roster, research=research_lane)
                await persist_request(self.hub, rid)
                if created:
                    await self._emit(rid, "request.route", decision)
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
                brief_task = Task(
                    agent_id=cos, request_id=rid,
                    prompt=BRIEFING_PROMPT.format(request=text, capabilities=capabilities or "No workers available") + self._lookup_usage_line(rid, stage="briefing"),
                    meta={**refs, "kind": "briefing", "request": text, "title": "CSO용 브리핑 준비"}) if cos and cos in known and not continuation else None
                brief_job = (self._step_task(rid, self.run_step(brief_task))
                             if brief_task is not None else None)
                remaining = self._request_budget_remaining(rid) if brief_task else None
                brief_cap = self._turn_cap(brief_task, remaining or 0.0) if brief_task else None
                defer_precedent = bool(brief_job and self.hub.s.policy.budget.reserve_before_dispatch
                                       and remaining is not None and brief_cap is not None
                                       and brief_cap >= remaining - _HOLD_EPSILON)
                # A continuation's plan prompt carries the revised plan and the reviewer's P1 issues (#90), or the
                # PI's CP2 revise note as the revision request (R10).
                carried = (req.get("research_contract") or {}).get("continuation") or {}
                rounds = (req.get("research_contract") or {}).get("rounds") or []
                cp2_revise = carried.get("trigger") == "cp2_revise"
                continuation_note = (research_continuation.plan_prompt(
                    rounds[-1]["plan"], rounds[-1]["plan_sha256"], carried.get("p1_issues") or [],
                    int(carried.get("round") or 2), pi_request=approval_note(carried.get("pi_request")))
                    if continuation and research_lane and rounds else "")
                if continuation_note:  # the previous round's CP2 note and the 이어 가기 note (PI 점검 R11)
                    continuation_note += approval_note_block("이어 가기", [
                        (f"CP2 (round {rounds[-1].get('round')})",
                         "" if cp2_revise else (rounds[-1].get("cp2") or {}).get("note") or ""),
                        ("이어 가기", carried.get("note") or "")], CONTINUE_NOTE_RULE)
                revision = req.get("revision_of") or {}
                if research_lane and not continuation and isinstance(revision.get("plan"), dict):
                    # CP1 수정 요청: the note names steps of the plan the PI did not approve, so the planner revises that
                    # plan instead of writing an unrelated one (Claude review of the CP1 수정 요청 change, P1).
                    continuation_note += (
                        f"\n\nCP1 revision request: the PI did not approve the plan below at CP1 of request "
                        f"{revision.get('request_id')} and asked for this change:\n{approval_note(revision.get('note'))}\n"
                        "Revise that plan: make the change the PI asked for and keep everything else unless the change "
                        "requires otherwise. The new plan goes to a new CP1.\n"
                        f"Previous plan (plan_sha256 {revision.get('plan_sha256')}):\n"
                        # Whole: the reviewer's digest clips steps at 8,000 characters, and the note may name a late
                        # step (#514 review).
                        + json.dumps(revision["plan"], ensure_ascii=False, sort_keys=True))
                precedent_job = None
                precedent_task = None
                precedent_agent = self.cfg.precedent_agent
                if "analysis_precedents" not in req and precedent_agent:
                    if precedent_agent in known:
                        precedent_task = Task(
                            agent_id=precedent_agent, request_id=rid, output_schema=PRECEDENT_SCHEMA,
                            prompt=PRECEDENT_PROMPT.format(request=text) + self._lookup_usage_line(rid, stage="precedent"),
                            meta={**refs, "kind": "precedent", "request": text,
                                  "title": "선행 연구 분석 기준 조사", "max_attempts": 1})
                        if not defer_precedent:
                            precedent_job = self._step_task(rid, self.run_step(precedent_task))
                    else:
                        req["analysis_precedents"] = precedent_warning("agent_unavailable")
                        await persist_request(self.hub, rid)

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
                        (await self._finish(rid, f"브리핑 실패: {b.error}", {"briefing": b.model_dump(mode="json")}, ok=False))
                        return
                    if rid in self.budget_denials:
                        await stop_precedent_job()
                        (await self._finish(rid, "브리핑 뒤 예산 승인 거부", {"briefing": b.model_dump(mode="json")}, ok=False))
                        return
                    briefing = b.text
                if defer_precedent and precedent_task is not None:
                    precedent_job = self._step_task(rid, self.run_step(precedent_task))
                if precedent_job:
                    try:
                        precedent_result = await precedent_job
                    except ShutdownInterrupted:
                        raise
                    except Exception:
                        precedent_result = None
                    parsed = None
                    if precedent_result is not None and precedent_result.ok:
                        raw = (precedent_result.structured if isinstance(precedent_result.structured, dict)
                               else extract_json(precedent_result.text))
                        parsed = normalize_precedents(raw)
                    req["analysis_precedents"] = (parsed if parsed is not None else precedent_warning(
                        "parse_failed" if precedent_result is not None and precedent_result.ok else "failed"))
                    await persist_request(self.hub, rid)
                req["checklist_contract"] = 1
                await persist_request(self.hub, rid)

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
                            request=plan_request, roster=format_roster(roster, alphagenome=self._alphagenome_ready),
                            capabilities=capabilities or "No workers available",
                            briefing=clip(briefing, 4000) or "(none)", max_steps=self.cfg.max_steps,
                            max_branches=self.cfg.max_branches,
                            max_branch_steps=self.cfg.max_branch_steps,
                            branched_max_steps=self.cfg.max_branches * self.cfg.max_branch_steps + 3,
                            intake=json.dumps(intake.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                            packs=render_pack_catalog(configured_pack_defs), question_rule=QUESTION_RULE,
                            output_types_rule=output_types.prompt_rule(vocab) if vocab else "",
                            topics_rule=(topic_types.prompt_rule(topic_vocab, include_definitions=False)
                                         if topic_vocab else ""))
                        prompt += planning_guidance(checklist_catalog, req.get("analysis_precedents"))
                        prompt += self._lookup_usage_line(rid)
                        prompt += reuse_advisory  # semantics-hook
                        prompt += continuation_note
                        schema = research_plan_schema(vocab is not None, output_types.ENTRY_SCHEMA)
                    else:
                        vocab = self._output_vocab()
                        topic_vocab = output_vocab.current()
                        prompt = PLAN_PROMPT.format(request=plan_request,
                                                    roster=format_roster(roster, alphagenome=self._alphagenome_ready),
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
                        prompt += self._lookup_usage_line(rid)
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
                        await persist_request(self.hub, rid)
                    if planned.error_kind == "model_refused":
                        raise ModelRefused(planned)
                    return planned

                async def resolve_plan_questions(candidate: dict, current: TaskResult,
                                                   suffix: str = "") -> tuple[dict | None, TaskResult]:
                    """Ask at most two cards for this request, filtering answered questions after each re-plan."""
                    nonlocal text
                    while True:
                        status, entry = await self._plan_clarification(rid, candidate)
                        if status in {"done", "open"}:
                            return candidate, current
                        if status in {"limit", "unanswered", "timed_out"}:
                            reason = ("확인 질문을 두 번 드렸지만 새 질문이 남아 요청을 멈췄습니다."
                                      if status == "limit" else
                                      "PI 질문 카드에 기한 안에 답이 없어 요청을 멈췄습니다"
                                      "(policy.approvals.pi_decision_timeout_s). 같은 요청을 다시 보내세요."
                                      if status == "timed_out" else
                                      "PI 확인 답변을 받지 못해 요청을 멈췄습니다.")
                            if status == "timed_out":
                                req["outcome"] = "clarify_timed_out"
                            questions = "\n".join(f"- {question}" for question in req.get("pending_questions") or [])
                            req["clarification_failure"] = reason
                            (await self._finish(rid, reason + ("\n\n남은 질문:\n" + questions if questions else ""), {},
                                         ok=False, error=reason))
                            return None, current
                        text += answered_questions_prompt([entry])
                        current = await make_plan(text + suffix)
                        if not current.ok:
                            reason = f"답변 반영 계획을 만들지 못했습니다: {safe_failure_cause(current.error)}"
                            (await self._finish(rid, reason, {}, ok=False, error=reason))
                            return None, current
                        if rid in self.budget_denials:
                            reason = "답변 반영 계획의 예산 승인이 거부되었습니다."
                            (await self._finish(rid, reason, {"plan": current.model_dump(mode="json")},
                                         ok=False, error=reason))
                            return None, current
                        candidate = _carry_assumptions(
                            candidate, current.structured if isinstance(current.structured, dict)
                            else extract_json(current.text) or {})

                plan_res = await make_plan(text)
                if not plan_res.ok:
                    reason = plan_res.error or "계획 결과를 받지 못했습니다."
                    (await self._finish(rid, f"계획 실패: {reason}", {"plan": plan_res.model_dump(mode="json")},
                                 ok=False, error=reason))
                    return
                if rid in self.budget_denials:
                    (await self._finish(rid, "계획 뒤 예산 승인 거부", {"plan": plan_res.model_dump(mode="json")}, ok=False))
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
                    if service is None:
                        health = req.get("semantic_devices") if isinstance(req.get("semantic_devices"), dict) else {}
                        sem = health.get(semantic_devices.DEVICE)
                        sem = sem if isinstance(sem, dict) else {}
                        reasons = [row.get("code") for row in health.get("errors") or []
                                   if isinstance(row, dict) and row.get("device") == semantic_devices.DEVICE]
                        reason = str(sem.get("reason") or (reasons[0] if reasons else "worker_unavailable"))
                        exclusion = "not_evaluated" if sem.get("status") == "off" else "error"
                        arm = None
                        if sem.get("setting") == "ab":
                            shadow_module = __import__(f"labhq.research.{semantic_devices.DEVICE}_shadow",
                                                       fromlist=["record_ab_evaluation"])
                            arm = shadow_module.record_ab_evaluation(self.hub.s, rid, exclusion)
                        offer = ({"status": "not_evaluated", "reason": reason}
                                 if sem.get("status") == "off" else
                                 {"status": "error", "error": {"code": reasons[0] if reasons else
                                                                  "semantics_worker_unavailable"}})
                        if arm:
                            offer["arm"] = arm
                    else:
                        try:
                            draft, _ = _normalize_plan_outputs(plan)
                            draft = prepare_research_declarations(draft, self._output_vocab(), {})
                            snap = service.advisory_snapshot(rid, draft)  # live state, copied on the loop
                            offer = await asyncio.to_thread(service.advisory_offer, rid, snap)
                            if offer is None:
                                offer = {"status": "not_evaluated",
                                         "reason": str(getattr(service, "latched", None) or
                                                       "snapshot_unavailable")}
                        except (ValueError, TypeError, PlanOutputsError) as exc:
                            offer = {"status": "error",
                                     "error": {"code": "semantics_advisory_snapshot_failed"}}
                            semantic_devices.log_exception("semantics_advisory_snapshot_failed", exc)
                    if offer is not None and offer.get("status") == "error":
                        error = offer.get("error") if isinstance(offer.get("error"), dict) else {}
                        req["semantics_ab"] = {"arm": offer.get("arm"), "offered": [], "status": "error",
                                               "error": {"code": error.get("code") or
                                                         "semantics_advisory_projection_failed"}}
                        await persist_request(self.hub, rid)
                        await self._semantic_device_error(
                            rid, code=str(error.get("code") or "semantics_advisory_projection_failed"))
                    elif offer is not None and offer.get("status") == "not_evaluated":
                        req["semantics_ab"] = {
                            **({"arm": offer["arm"]} if offer.get("arm") else {}),
                            "status": "not_evaluated",
                            "reason": str(offer.get("reason") or "snapshot_unavailable"),
                        }
                        await persist_request(self.hub, rid)
                    elif offer is not None:  # ab: ids offered (shadow arm: would be), frozen for the end record
                        req["semantics_ab"] = {"arm": offer["arm"], "offered": list(offer["offered"]),
                                               "status": "ok"}
                        await persist_request(self.hub, rid)
                    if offer is not None and offer.get("status", "ok") == "ok" and offer["offered"]:
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
                            (await self._finish(rid, f"A/B re-plan failed: {plan_res.error}", {}, ok=False))
                            return
                        if rid in self.budget_denials:
                            (await self._finish(rid, "A/B 재계획 뒤 예산 승인 거부",
                                         {"plan": plan_res.model_dump(mode="json")}, ok=False))
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
                                                            max_branches=self.cfg.max_branches,
                                                            max_branch_steps=self.cfg.max_branch_steps,
                                                            active_packs=pack_snapshot(candidate_packs),
                                                            expected_intake=intake, pack_definitions=candidate_packs)
                        except (ValueError, TypeError) as error:
                            problems = [str(error)]
                        problems.extend(recruit_planning.proposal_errors(candidate))
                        drafted = candidate.get("steps") if isinstance(candidate, dict) else None
                        bad_agents = unavailable_plan_agents(drafted, known, orchestration)
                        if bad_agents:
                            problems.append(f"research plan uses unavailable or orchestration agents: {bad_agents}; "
                                            f"use roster ids {workers}")
                        guard = self._external_guard(rid)
                        conflicts = guard(drafted) if guard and isinstance(drafted, list) else []
                        if conflicts:
                            problems.append(f"{EXTERNAL_CONTRACT_RULE}: {conflicts}")
                        problems.extend(checklist_errors(candidate, checklist_catalog,
                                                         req.get("analysis_precedents")))
                        return problems

                    semantic_checks: list[dict[str, Any]] = []
                    for attempt in range(1, PLAN_CORRECTIONS + 2):
                        # These are scheduler-owned fields, never model-provided runner assignments.
                        if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
                            plan = {**plan, "steps": [
                                {k: v for k, v in step.items() if k not in {"environment_binding", "environment_bindings"}}
                                if isinstance(step, dict) else step for step in plan["steps"]]}
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
                        plan = repair_synthesis_links(plan)  # before validation: one missed edge must not end the request
                        fit_counts = input_fit.evaluate(plan, vocab=topic_vocab)
                        mismatches = input_fit.mismatch_diagnostics(plan, vocab=topic_vocab)
                        fit_warnings = [
                            f"input_fit mismatch: step {row['step_id']} input {row['input_ref']} "
                            f"has {row['data_type']}, incompatible with operation {row['operation']}. "
                            "Correct the input/method or evidence-backed declaration; do not merely relabel it."
                            for row in mismatches]
                        initial = semantic_checks[0]['mismatches'] if semantic_checks else []
                        resolved = input_fit.resolved_mismatches(plan, initial, vocab=topic_vocab)
                        required = required_checklist_items(plan, checklist_catalog, req.get("analysis_precedents"))
                        step_ids = [step.get('id') for step in plan.get('steps', []) if isinstance(step, dict)]
                        checklist_slots = [
                            (f"{slot.topic}.{item.id}" if slot.topic else item.id,
                             not topic_checklists.answer_errors({item.id: slot.answer}, [item], step_ids))
                            for item in required for slot in topic_checklists.check_slots(plan.get('checklist'), item, {})]
                        semantic_checks.append({
                            "attempt": attempt, "input_fit": fit_counts, "mismatches": mismatches,
                            "resolved_mismatches": resolved, "warnings": fit_warnings,
                            "vocab_sha256": topic_vocab.sha256 if topic_vocab else None,
                            "table_sha256": input_fit.table_sha256(topic_vocab),
                            "checklist": {"required": [key for key, _ in checklist_slots],
                                          "missing": [key for key, answered in checklist_slots if not answered]},
                        })
                        req["plan_validation"] = {"attempts": attempt, "semantic_checks": semantic_checks}
                        req["plan_warnings"] = list(dict.fromkeys(
                            warning for check in semantic_checks for warning in check['warnings']))
                        raw_warnings = plan.get('warnings') or []
                        if any(not isinstance(w, str) for w in raw_warnings):
                            output_problems.append('plan warnings must contain only strings')
                        current_warnings = [w for w in raw_warnings
                                            if isinstance(w, str) and not w.startswith('input_fit ')]
                        if initial:
                            current_warnings.append(
                                f"input_fit pre-CP1: {len(initial)} initial mismatching inputs; "
                                f"{len(resolved)} same inputs now fit; remaining resolutions unverified. "
                                "This trace is not evidence of scientific performance gain.")
                        plan = {**plan, 'warnings': list(dict.fromkeys([*current_warnings, *fit_warnings]))}
                        req['plan_warnings'] = list(dict.fromkeys([*req['plan_warnings'], *plan['warnings']]))
                        problems = selection_problems + output_problems + plan_problems(plan, candidate_packs)
                        problems.extend(fit_warnings)
                        if not problems:
                            plan = with_scope_coverage_warning(plan, str(req.get("text") or text))
                            plan = self._apply_plan_cost_estimate(rid, plan)
                            # Part of the frozen plan, so CP1 shows them and the hash covers them (#446).
                            plan = with_checklist_skip_warnings(plan, checklist_catalog,
                                                                req.get("analysis_precedents"))
                            validated = validate_research_plan(plan, max_steps=self.cfg.max_steps,
                                                               max_branches=self.cfg.max_branches,
                                                               max_branch_steps=self.cfg.max_branch_steps,
                                                               active_packs=pack_snapshot(candidate_packs),
                                                               expected_intake=intake, pack_definitions=candidate_packs)
                            packs = candidate_packs
                            active_pack_hashes = pack_snapshot(packs)
                            break
                        if attempt == PLAN_CORRECTIONS + 1:
                            req["outcome"] = "plan_invalid"
                            req["plan_validation"].update(errors=problems)
                            report = plan_invalid_report(problems, configured_pack_defs, PLAN_CORRECTIONS)
                            (await self._finish(rid, report, {}, ok=False, error=report.split("\n", 1)[0]))
                            return
                        correction_suffix = "\n\n" + plan_correction(problems)
                        plan_res = await make_plan(text + correction_suffix)
                        if not plan_res.ok:
                            (await self._finish(rid, f"Research re-plan failed: {plan_res.error}", {}, ok=False))
                            return
                        if rid in self.budget_denials:
                            (await self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                         {"plan": plan_res.model_dump(mode="json")}, ok=False))
                            return
                        plan = _carry_assumptions(
                            plan, plan_res.structured if isinstance(plan_res.structured, dict)
                            else extract_json(plan_res.text) or {})
                        plan, plan_res = await resolve_plan_questions(plan, plan_res, correction_suffix)
                        if plan is None:
                            return
                    req["plan"] = validated.model_dump(mode="json")
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
                            recruit_errors = recruit_planning.proposal_errors(plan)
                            if recruit_errors:
                                raise PlanAgentError("; ".join(recruit_errors))
                            steps, step_warnings = validate_steps(plan.get("steps") or [], known, self.cfg.max_steps,
                                                                  orchestration, vocab=vocab, stats=type_stats,
                                                                  guard=self._external_guard(rid),
                                                                  tier_rules=self._tier_rules())
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
                                (await self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False))
                                return
                            if rid in self.budget_denials:
                                (await self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False))
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
                                (await self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False))
                                return
                            if rid in self.budget_denials:
                                (await self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False))
                                return
                            plan = _carry_assumptions(
                                plan, plan_res.structured if isinstance(plan_res.structured, dict)
                                else extract_json(plan_res.text) or {})
                            plan, plan_res = await resolve_plan_questions(plan, plan_res, correction_suffix)
                            if plan is None:
                                return
                    req["plan"] = with_scope_coverage_warning(with_checklist_skip_warnings(
                        {**plan, "steps": steps, "warnings": warnings}, checklist_catalog,
                        req.get("analysis_precedents")), str(req.get("text") or text))
                    if vocab is not None:
                        req["output_types_stats"] = {**type_stats, "vocab": vocab.sha256}
                    # The verdict of the plan that runs, else the first one given (#36, #346 review)
                    scope = scope_verdict(plan) or req.get("scope_first")
                    if scope:
                        req["scope_check"] = scope
                if not research_lane and (req["mode"] != "plan_only" or req["plan"].get("recruit")):
                    if not await self._scope_gate(rid, req):
                        return
                await prepare_recruits()
                steps = req["plan"]["steps"]
                # A plan-only request never executes, so it must not wait for a consumer runner (#674).
                if req["mode"] != "plan_only" and any(ENV_LOCK_OUTPUT in step.get("outputs", []) for step in steps):
                    steps = self._bind_request_environments(rid, steps)
                    req["plan"] = {**req["plan"], "steps": steps}
                    if research_lane:
                        req["plan"] = validate_research_plan(
                            req["plan"], max_steps=self.cfg.max_steps, max_branches=self.cfg.max_branches,
                            max_branch_steps=self.cfg.max_branch_steps, active_packs=active_pack_hashes,
                            expected_intake=intake, pack_definitions=packs).model_dump(mode="json")
                        steps = req["plan"]["steps"]
                    else:
                        steps, _ = validate_steps(steps, known, self.cfg.max_steps, orchestration,
                                                  vocab=self._output_vocab(), reject_excess=True,
                                                  stored_environment=True, guard=self._external_guard(rid),
                                                  tier_rules=self._tier_rules())
                        req["plan"]["steps"] = steps
                req["plan"] = await paper_skills.prepare_plan(
                    req["plan"], req.get("analysis_precedents") or {}, roster, rid, self.hub.s,
                    lambda task: self._step_task(rid, self.run_step(task)))
                if research_lane and continuation:
                    self._adopt_continuation_plan(rid, req["plan"], active_pack_hashes)
                decision, created = self._ensure_route_decision(req, roster, research=research_lane)
                await persist_request(self.hub, rid)
                if created:
                    await self._emit(rid, "request.route", decision)
                await self._emit(rid, "request.plan", {**req["plan"], "processing": decision})
                if req["plan"].get("recruit_results"):
                    await self._emit(rid, "recruit.suggested", {
                        "handled": True, "recruits": req["plan"]["recruit_results"]})
                if not steps:
                    (await self._finish(rid, plan_res.text or "CSO returned no steps.", {}, ok=False))
                    return
                if research_lane:
                    if not await finish_research_plan(req["plan"]):
                        return
                elif req["mode"] == "plan_only":
                    req["outcome"] = "plan_only"
                    (await self._finish(rid, plan_res.text or "Plan completed.", {}, ok=True))
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
                if any("environment_binding" in s or "environment_bindings" in s for s in steps):
                    # Completed environments cannot be silently rebound by the generic replan path.
                    return "disabled"
                limit = (self.cfg.max_failure_replans if trigger == "step_failure"
                         else self.cfg.max_replans)
                # An approved research plan changes only through a new CP1 approval of its hash, never here.
                if limit <= 0 or research_lane:
                    return "disabled"
                progress = req.setdefault("replan_progress", {"attempts": 0, "max": limit, "in_flight": False})
                history = req.setdefault("replan_history", [])

                async def record(status: str, attempt: int | None = None, **entry: Any) -> str:
                    history.append({"attempt": attempt, "trigger": trigger, "status": status, **entry})
                    progress["in_flight"] = False
                    progress.pop("trigger", None)
                    await persist_request(self.hub, rid)
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
                        if outcome.error_kind == "structured_output_format":
                            return "JSON format retries exhausted; re-planning would reset the retry limit"
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
                        return (await record("blocked", reason="; ".join(blocked)))
                used_attempts = max((int(entry.get("attempt") or 0) for entry in history
                                     if entry.get("trigger") == trigger), default=0)
                # A restart re-asks the attempt it interrupted; a cap lowered meanwhile still applies to it.
                same_in_flight = progress.get("in_flight") and progress.get("trigger", trigger) == trigger
                if same_in_flight:
                    used_attempts = max(used_attempts, int(progress.get("attempts") or 0))
                attempt = max(used_attempts, 1) if same_in_flight else used_attempts + 1
                if attempt > limit:
                    return (await record("limit", reason=f"re-plan limit reached ({used_attempts}/{limit})"))
                progress.update(attempts=attempt, max=limit, in_flight=True, trigger=trigger)
                if review_progress is not None:
                    req["review_progress"] = {**review_progress, "phase": "replan"}
                await persist_request(self.hub, rid)  # counted before the CSO call, so a restart cannot reset the cap

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
                            roster=format_roster(roster, alphagenome=self._alphagenome_ready),
                            capabilities=capabilities or "No workers available",
                            trigger=why, retired=", ".join(unfinished) or "none", drop_rule=drop_rule,
                            used=", ".join(sorted(used)), max_steps=self.cfg.max_steps,
                            output_types_rule=output_types.prompt_rule(vocab) if vocab else "",
                            topics_rule=(topic_types.prompt_rule(topic_vocab, include_definitions=False)
                                         if topic_vocab else ""),
                            empty_rule=empty_rule, question_rule=QUESTION_RULE, request=text,
                            plan=json.dumps(req.get("plan") or {"steps": steps}, ensure_ascii=False),
                            results=self.format_results(steps, results, n)) +
                        planning_guidance(checklist_catalog, req.get("analysis_precedents")) +
                        self._lookup_usage_line(rid),
                        # revision and parse_attempt keep each CSO call distinct for ledger recovery after a restart.
                        meta={**refs, "kind": "replan", "trigger": trigger, "revision": attempt,
                              "parse_attempt": parse_attempt, "request": text, "roster": roster,
                              "title": f"남은 DAG 재계획 #{attempt}", **({"workdir": workdir} if workdir else {}),
                              **checklist_meta(checklist_catalog)}))
                    if planned.session_id:
                        req["cso_session_id"] = planned.session_id
                        req["cso_workdir"] = planned.workdir
                        await persist_request(self.hub, rid)
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
                        if status in {"limit", "unanswered", "timed_out"}:
                            req["clarification_failure"] = (
                                "확인 질문을 두 번 드렸지만 새 질문이 남았습니다." if status == "limit" else
                                "PI 질문 카드에 기한 안에 답이 없었습니다." if status == "timed_out" else
                                "PI 확인 답변을 받지 못했습니다.")
                            reason = ("re-plan still needs PI clarification" if status == "limit" else
                                      "re-plan needs PI clarification; the card timed out unanswered"
                                      if status == "timed_out" else
                                      "re-plan needs PI clarification that was denied or unanswered")
                            return (await record("failed", attempt, reason=reason))
                        text += answered_questions_prompt([entry])
                        parse_attempt += 1
                        candidate = _carry_assumptions(candidate, await ask_cso(parse_attempt))
                    raw, drop = candidate.get("steps") or [], candidate.get("drop") or []
                    if not isinstance(raw, list) or not isinstance(drop, list):
                        raise ValueError("re-plan steps and drop must be lists")
                    if not raw:
                        if review is not None and not drop:
                            return (await record("declined", attempt, notes=str(candidate.get("notes") or "")))
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
                                                      vocab=vocab, stats=type_stats, guard=self._external_guard(rid),
                                                      tier_rules=self._tier_rules())
                    # A kept step already ran. A dependency inferred from its old instruction on a new step is not
                    # one it ran with, so it keeps the dependencies it had and the inference warning is dropped.
                    for step in merged:
                        if step["id"] in by_id:
                            step["depends_on"] = list(by_id[step["id"]]["depends_on"])
                    warnings = [w for w in warnings if not any(
                        w.startswith((f"step {sid}: added dependency on ", f"step {sid}: reference to "))
                        for sid in by_id)]
                except (BudgetExceeded, TypeError, ValueError) as error:  # PlanOutputsError is a ValueError
                    return (await record("failed", attempt, reason=str(error)))

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
                outcome = (await record("applied", attempt, retired=retired_ids, added=ids, prior_results=prior,
                                 notes=str(candidate.get("notes") or "")))
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
                (await self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False))
                return

            progress = req.get("review_progress") or {}
            if progress.get("phase") in {"revision", "replanned"}:  # its steps ran above; review them next
                progress.update(phase="review", last_completed_revision=progress["next_revision"])
                req["review_progress"] = progress
                await persist_request(self.hub, rid)
            elif progress.get("phase") == "replan":  # restarted while the CSO re-planned: review again, then re-plan
                progress.update(phase="review", next_revision=progress["last_completed_review"])
                req["review_progress"] = progress
                await persist_request(self.hub, rid)
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
                        meta={**refs, "kind": "review", "outputs": ["outputs/review.json"], "revision": rev, "parse_attempt": parse_attempt,
                              "request": text, "title": f"과학 리뷰 #{rev}"}))
                    if rid in self.budget_denials:
                        (await self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                     review={"status": "budget_denied"}))
                        return
                    parsed = lenient_review(r.structured, REVIEW_SCHEMA) or lenient_review(
                        extract_json(r.text), REVIEW_SCHEMA)
                    if r.ok and parsed:
                        review = with_p1_verdict(review_with_checklist(
                            parsed, req.get("plan"), checklist_catalog, req.get("analysis_precedents")))
                        break
                if not review:
                    review = {"status": "review_unparsed", "reason": r.error or "missing or invalid verdict"}
                    await self._emit(rid, "request.review", {"revision": rev, **review})
                    (await self._finish(rid, self.report_results(steps, results, n) +
                                 f"\n\nReview: review_unparsed ({review['reason']})",
                                 serialized_results(), ok=False, review=review))
                    return
                await self._emit(rid, "request.review", {"revision": rev, **review})
                progress = {"phase": "review", "next_revision": rev + 1, "review": review,
                            "last_completed_review": rev,
                            "last_completed_revision": progress.get("last_completed_revision", 0)}
                if review.get("verdict") != "revise":
                    progress["phase"] = "synthesis"
                    req["review_progress"] = progress
                    await persist_request(self.hub, rid)
                    break
                if rev >= self.cfg.max_revisions:
                    progress["phase"] = "unresolved"
                    req["review_progress"] = progress
                    await persist_request(self.hub, rid)
                    break
                p1_review = review_at_priority(review, "P1")
                if await attempt_replan(p1_review, progress) == "applied":
                    await self.run_dag(rid, text, steps, results, only={s["id"] for s in steps} - set(results))
                    await recover_failures()
                    if rid in self.budget_denials or has_failures():
                        (await self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                     review=review))
                        return
                    progress.update(phase="review", last_completed_revision=rev + 1)
                    req["review_progress"] = progress
                    await persist_request(self.hub, rid)
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
                    await persist_request(self.hub, rid)
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
                await persist_request(self.hub, rid)
                await self.run_dag(rid, text, steps, results, only=set(feedback), feedback=feedback)
                await recover_failures()
                if rid in self.budget_denials or has_failures():
                    (await self._finish(rid, self.report_results(steps, results, n), serialized_results(), ok=False,
                                 review=review))
                    return
                progress.update(phase="review", last_completed_revision=rev + 1)
                await persist_request(self.hub, rid)

            # Still "revise" when revising stopped (F5): the CSO writes the report with the open issues in their own
            # section and the request stays failed. A failed or budget-denied synthesis ends with the step results.
            unresolved = review.get("verdict") == "revise"
            reference_issues = [*issues_at_priority(review, "P2"), *issues_at_priority(review, "P3")]
            if unresolved:
                req["outcome"] = "review_unresolved"
                await persist_request(self.hub, rid)
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
                body = (final.text if final.ok else self.report_results(steps, results, n) +
                        f"\n\nSynthesis failed: {final.error}")
                report = _append_report_metadata(body, ["Review: revisions unresolved."])
                (await self._finish(rid, report,
                             serialized_results(), ok=False, review=review,
                             error="리뷰 지적이 수정 상한 뒤에도 남아 있습니다"))
                return
            final = await self.run_step(synthesis)
            review_appendix = ("남은 P2·P3 지적 원문:\n" + "\n".join(
                f"- {issue.get('priority')} · {issue.get('step_id')}: {issue.get('problem')} → {issue.get('request')}"
                for issue in reference_issues)) if reference_issues else ""
            body = (final.text if final.ok else self.report_results(steps, results, n) +
                    f"\n\nSynthesis failed: {final.error}")
            reference = _review_reference(review)
            if reference and not REVIEW_REFERENCE_HEADING.search(body):
                body = _before_bench_block(body, reference)
            report = _append_report_metadata(body, [review_appendix]) if review_appendix else body
            (await self._finish(rid, report, serialized_results(),
                         ok=final.ok and rid not in self.budget_denials, review=review))
        except research_continuation.ReuseApprovalRequired:
            (await self._finish(rid, "재사용 증거가 바뀌어 단계를 보내지 않았습니다. 새 CP1 승인이 필요합니다.",
                         req.get('results') or {}, ok=False, error="reuse approval required"))
        except ModelRefused as exc:
            req["outcome"] = "model_refused"
            (await self._finish(rid, MODEL_REFUSAL_REASON, {"plan": exc.result.model_dump(mode="json")},
                         ok=False, error=MODEL_REFUSAL_REASON))
            await self.hub.offer_model_refusal(rid)
        except ShutdownInterrupted as exc:
            await asyncio.gather(*self.cancel_steps(rid), return_exceptions=True)
            await self.hub._set_status(rid, "interrupted", evidence=exc.evidence)
        except Exception as e:
            if req.get("status") == "cancelled" or req.get("cancelling") is not None:
                return  # a cancel is committing its own terminal record; an error after it does not end the request as failed (#1168)
            if self._continuation_unstarted(req):
                # Ends as the revise it continued, with that round's plan and results put back (``_finish``).
                (await self._finish(rid, f"{type(e).__name__}: {e}", {}, ok=False, error=f"{type(e).__name__}: {e}"))
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
            (await self._record_dev_failure(rid, "request", req["error"], "internal"))
            summary, _feed_error = failed_request_summary(req, serialized_saved, req["error"])
            req["report"] = (summary + "\n\n## 결론과 권고\n요청을 완료하지 못했습니다. 실행 기록의 원인과 "
                             "다음 조치를 확인하세요.\n\n## 결과\n확정할 최종 결과가 없습니다.\n\n## 방법 요약\n"
                             "완료된 단계까지 실행했습니다.\n\n## 한계\n요청 처리 오류가 있습니다. 실행 기록 참고.")
            devices = (semantic_devices.execution_record(req.get("semantic_devices"))
                       if req.get("semantic_devices") else "")
            req["execution_record"] = "\n\n".join(part for part in [req["error"], *execution, devices] if part)
            req["report_appendix"] = scientific_appendix("", req, req.get("review"), "")
            package = build_report_package(req, req["report"], serialized_saved)
            req["report_package"] = package.to_dict()
            req["report_contract_check"] = validate_report_package(
                package,
                available_files=available_result_files(serialized_saved),
                reader_documents={"web·bundle·PDF": reader_markdown(with_report_front(req["report"], package))},
            )
            # The preserved partial report goes with the event so a connected (or reconnecting) office shows it.
            failed = {"error": safe_failure_cause(req["error"]),
                      "report_package": req["report_package"],
                      **_terminal_reports(rid, req.get("report") or "", req.get("report_appendix") or "", req.get("execution_record") or ""),
                      "cost_usd": float(req.get("cost_usd") or 0),
                      "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary")}
            if hasattr(self.hub, "aschedule_terminal"):
                await self.hub.aschedule_terminal(rid, "request.failed", failed)
            elif hasattr(self.hub, "schedule_terminal"):
                self.hub.schedule_terminal(rid, "request.failed", failed)
            elif hasattr(self.hub, "commit_terminal"):
                self.hub.commit_terminal(rid, "request.failed", failed)
            else:
                await self._emit(rid, "request.failed", failed)

    @staticmethod
    def _continuation_unstarted(req: dict, dispatched: bool | None = None) -> bool:
        """A continuation round is on and none of its steps has been dispatched.

        ``dispatched`` is the gateway task ledger's authoritative answer. In-process, ``verified`` means the CP1
        proof AND budget gates passed; ``dispatch_started`` preserves a dispatch attempt even if later drift
        invalidates the proof. A gateway stopped between these boundaries must ask the ledger (PR #527 review)."""
        contract = req.get("research_contract") or {}
        carried = contract.get("continuation") or {}
        if not (carried and contract.get("rounds")):
            return False
        return (not dispatched if dispatched is not None else
                not (carried.get("verified") or carried.get("dispatch_started")))

    @staticmethod
    def _archived_continuation_decision(req: dict) -> bool:
        """The archived round already passed CP2 and the continuation decision before its new plan was adopted."""
        if not Orchestrator._continuation_unstarted(req):
            return False
        contract = req.get("research_contract") or {}
        rounds = contract.get("rounds") or []
        current = contract.get("plan_sha256")
        decided = contract.get("continue_decision") or {}
        return bool(current and ((rounds and rounds[-1].get("plan_sha256") == current)
                                 or decided.get("plan_sha256") == current))

    @staticmethod
    def _unstarted_continuation_end(req: dict, report: str, results: dict,
                                    review: dict | None) -> tuple[str, dict, dict | None]:
        """A continuation round that ends before its new CP1 let any step run (#90, PR #448 review).

        A rejected or timed-out CP1, a failed or invalid plan, a refused budget card, or an error ends the request as
        the revise it continued: outcome ``research_review_revise`` with the previous round's review on top, or
        ``evidence_revision_requested`` with the PI's CP2 note after a CP2 revise (R10), and its results kept, so the
        record and ``labhq verify`` still show what ran. The previous round's plan, hash, CP1 approval, pack snapshot,
        CP2 receipt and request spending cap come back with them (``restore_round``): the results never ran under the
        new draft, which stays in ``continuation.declined_plan`` (PR #448 review, PR #499 review). Why the round ended
        is kept in ``continuation.ended_before_dispatch``."""
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
        # The plan control task stays in that diagnostic namespace even if an archived step is also named plan.
        step_ids = {str(s.get("id")) for s in (req.get("plan") or {}).get("steps") or [] if isinstance(s, dict)}
        stray = {key: value for key, value in (results or {}).items() if key == "plan" or key not in step_ids}
        results = {key: value for key, value in (results or {}).items() if key != "plan" and key in step_ids}
        carried["ended_before_dispatch"] = {"outcome": req.get("outcome"), "reason": reason,
                                            **({"task_results": stray} if stray else {})}
        if req.get("outcome") == "model_refused" and carried.get("refusal_retry"):
            carried["refusal_retry"]["state"] = "refused"
        if carried.get("trigger") == "cp2_revise":  # the CP2 revise it continued (R10)
            req["outcome"] = "evidence_revision_requested"
            ended = [f"{previous.get('round')}차 결과와 CP2 수정 요청이 이 요청의 결과입니다.", "",
                     "CP2 evidence review: revision_requested.", f"PI 수정 요청: {carried.get('pi_request') or '-'}"]
        else:
            req["outcome"] = "research_review_revise"
            ended = [f"{previous.get('round')}차 결과와 리뷰가 이 요청의 결과입니다.", "",
                     f"Research review ({stored.get('reviewer') or '-'}): revise.",
                     *_research_issue_lines(stored.get("issues") or [])]
        report = "\n".join([f"이어 가기 {carried.get('round')}차가 단계 실행 전에 끝났습니다: {reason}", *ended, "",
                            report])
        return report, {**(previous.get("results") or {}), **results}, review or (stored or None)

    @staticmethod
    def _checklist_section(req: dict, review: dict | None) -> str:
        """The checklist table for the execution record (#586), or an explicit load error."""
        plan = req.get("plan") if isinstance(req.get("plan"), dict) else {}
        precedents = req.get("analysis_precedents")
        if not plan.get("topics") and not (isinstance(precedents, dict) and precedents.get("status") == "ok"):
            return ""
        try:
            catalog = topic_checklists.load()
        except (OSError, ValueError) as exc:
            semantic_devices.log_exception("checklist_render_failed", exc)
            record = req.get("semantic_devices") if isinstance(req.get("semantic_devices"), dict) else {
                "version": 1, "phase": "report", "status": "ok", "errors": []}
            semantic_devices.add_error(record, code="checklist_render_failed")
            req["semantic_devices"] = record
            return ("체크리스트 오류: 불러오기 실패. 빈 점검표로 간주하지 않습니다. "
                    "체크리스트를 고친 뒤 같은 요청을 다시 보내세요.")
        route = req.get("route_decision") or {}
        solo = route.get("mode") == "solo" and not route.get("fallback")  # the plan's steps never ran
        return checklist_appendix(plan, review, catalog, precedents, owner=SOLO_OWNER if solo else None)

    async def _record_dev_failure(self, rid: str, step_id: str, cause: str, kind: str) -> None:
        """Record an error fingerprint in the configured round log, or a local JSONL outbox."""
        import hashlib

        req = self.hub.requests[rid]
        signature = hashlib.sha256(f"{kind}:{safe_failure_cause(cause)}".encode()).hexdigest()[:20]
        records = req.setdefault("dev_reports", [])
        if any(row["step_id"] == step_id and row["signature"] == signature for row in records):
            return
        record = {"request_id": rid, "step_id": step_id, "signature": signature, "kind": kind}
        cfg = self.hub.s.dev_log
        if not (cfg.enabled and cfg.repo):
            path = self.hub.s.path(self.hub.s.gateway.state_dir) / "dev-reports.jsonl"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                req["dev_report_error"] = "개발 기록 저장 실패"
                await persist_request(self.hub, rid)
                return
        records.append(record)
        await persist_request(self.hub, rid)

    async def _finish(self, rid: str, report: str, results: dict, ok: bool, review: dict | None = None,
                error: str | None = None) -> None:
        before = copy.deepcopy(self.hub.requests[rid])
        req = copy.deepcopy(before)
        if req.get("status") == "cancelled":
            return  # request cancel already wrote its report and terminal record (R13)
        from ..research import final_checkpoint as cp4
        if cp4.enabled(req) and (req.get("research_contract") or {}).get("final_draft"):
            await persist_request(self.hub, rid)
            return  # preserve the exact body and audit the PI saw
        report, results, review = self._unstarted_continuation_end(req, report, results, review)
        if not ok and _developer_action(error or report) and not req.get("dev_reports"):
            (await self._record_dev_failure(rid, "request", error or report, "internal"))
            req["dev_reports"] = copy.deepcopy(self.hub.requests[rid].get("dev_reports") or [])
        report, report_appendix = _split_report_appendix(report)
        report_appendix, legacy_execution = split_appendix(report_appendix)
        execution = [req.get("execution_record") or "", legacy_execution]
        if report.lstrip().startswith("### ") and "Full instructions, outputs and errors per step:" in report:
            execution.append(report)
            summary, feed_error = failed_request_summary(req, results, error or report)
            branch_status = ((req.get("research_contract") or {}).get("failure") or {}).get("branch_status") or []
            if branch_status:
                summary += "\n\n" + _branch_terminal_status_table(branch_status)
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
        warnings = (req.get("research_contract") or {}).get("result_warnings") or []
        if warnings:
            section = "\n".join(f"- {line}" for line in warnings)
            heading = re.search(r"(?m)^## 한계\s*$", report)
            if heading:
                report = report[:heading.end()] + "\n" + section + "\n" + report[heading.end():]
            else:
                report = _before_bench_block(report, "## 한계\n" + section)
        checklist_section = self._checklist_section(req, review)
        metadata = []
        if req.get("semantic_devices"):
            metadata.append(semantic_devices.execution_record(req["semantic_devices"]))
        if semantic_device_report.applies(req, self.hub.s):
            report_request = {**req, "results": results, "review": review}
            metadata.append(semantic_device_report.summary(
                semantic_device_report.build_report(report_request, self.hub.s)))
        paper_record = paper_skills.appendix(req.get("plan") or {}, results)
        if paper_record:
            metadata.append(paper_record)
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
                if entry.get("missing_optional_outputs"):
                    line += f"; optional missing: {', '.join(entry['missing_optional_outputs'])}"
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
            metadata.append(f"비용: {f'${known:.2f} + ' if known else ''}비용 모름")
        for outcome in self.budget_outcomes.get(rid, []):
            decision = ("final turn; no further work" if outcome.get("final_turn") else
                        "approved" if outcome["approved"] else
                        "timed out" if outcome.get("timed_out") else "denied")
            unknown = int(outcome.get("unknown_count") or 0)
            pending = f" + 비용 모름 {unknown}건" if unknown else ""
            relation = ">" if outcome["spent_usd"] > outcome["limit_usd"] else "/"
            metadata.append(f"Budget: ${outcome['spent_usd']:.2f}{pending} {relation} "
                            f"${outcome['limit_usd']:.2f}; {decision}.")
        req["execution_record"] = "\n\n".join(p for p in [*execution, *metadata] if p).replace(EXECUTION_APPENDIX_TITLE, "## 실행 기록")
        report_appendix = scientific_appendix(report_appendix, req, review, checklist_section)
        report = add_references(report, report_appendix, req.get("references") or [])
        from ..report_style import check_report
        report, style_note = check_report(report)
        if style_note:
            req["execution_record"] += "\n\n" + style_note
        package = build_report_package(req, report, results)
        contract_check = validate_report_package(
            package,
            available_files=available_result_files(results),
            reader_documents={"web·bundle·PDF": reader_markdown(with_report_front(report, package))},
        )
        req["report_package"] = package.to_dict()
        req["report_contract_check"] = contract_check
        if contract_check["problems"]:
            lines = "\n".join(f"- {problem['message']}" for problem in contract_check["problems"])
            req["execution_record"] += "\n\n## 공통 보고 계약 경고\n\n" + lines
        req.update(status="done" if ok else "failed", report=report, report_appendix=report_appendix,
                   results=results, review=review,
                   cost_usd=max(self.cost.get(rid, 0.0), float(req.get("cost_usd") or 0)), finished_at=time.time())
        if cp4.enabled(req) and ok:
            req.setdefault("id", rid)
            contract = req.get("research_contract") or {}
            check, accepted = contract.get("report_check") or {}, contract.get("review") or {}
            if (accepted.get("verdict") == "accept" and "problems" in check and not check["problems"]
                    and not any(i.get("priority") == "P1" for i in accepted.get("issues") or [])
                    and contract_check["status"] == "PASS"):
                cp4.freeze(req, self.hub.s.policy.approvals.pi_decision_timeout_s)
                req.update(status="review_pending", outcome="research_final_pending",
                           pi_review_pending=True, pi_reviewed_at=None)
            else:
                req.update(status="failed", outcome="research_final_unverified")
                ok = False
        data = {"ok": ok, "report_package": req["report_package"],
                **_terminal_reports(rid, report, report_appendix, req.get("execution_record") or ""), "cost_usd": req["cost_usd"],
                **({"failed_steps": failed} if (failed := failed_step_ids(req)) else {}),
                "cost_known": req.get("cost_known", True), "cost_summary": req.get("cost_summary"),
                "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}
        if cp4.enabled(req):
            data.update(status=req["status"], final_checkpoint=cp4.summary(req),
                        pi_review_pending=True, pi_reviewed_at=None)
        if error:  # one readable office-feed line; `labhq send` prints report first, or error when report is absent
            req["error"] = data["error"] = error
        if hasattr(self.hub, "aschedule_terminal"):
            await self.hub.aschedule_terminal(rid, "request.completed", data, request=req, before=before)
            return
        from ..request_persistence import apply_changes
        apply_changes(self.hub.requests[rid], before, req)
        if hasattr(self.hub, "schedule_terminal"):
            self.hub.schedule_terminal(rid, "request.completed", data)
        elif hasattr(self.hub, "commit_terminal"):
            self.hub.commit_terminal(rid, "request.completed", data)
        else:  # Lightweight orchestration test doubles do not persist state.
            await persist_request(self.hub, rid)
            asyncio.get_running_loop().create_task(self._emit(rid, "request.completed", data))
