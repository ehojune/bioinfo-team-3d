from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from typing import Any, ClassVar, Literal

from pydantic import (BaseModel, ConfigDict, Field, ValidationError, field_validator, model_serializer,
                      model_validator)

from ..intake import ClarifyingQuestion
from ..vocab.declare import MAX_ENTRIES, MAX_KEY, MAX_NAME
from .packs import field_value_problem
from ..evidence.claims import (STATUS_NEEDS, Claim, Evidence, EvidenceLink, ledger_errors,
                               normalize_artifact_path)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntakeDecision(StrictModel):
    work_kind: Literal["simple", "research"]
    reason: str = Field(min_length=1)
    scope_status: Literal["in_scope", "needs_pi_confirmation"] = "in_scope"
    confidence: Literal["clear", "ambiguous"] = "clear"
    source: Literal["explicit", "rule", "cso"] = "rule"


_RESEARCH_SIGNALS = (
    r"가설|연구\s*질문|새\s*결론|후보\s*(선택|우선순위)|방법\s*(선택|비교)|인과|기전|바이오마커",
    r"차등\s*발현|연관\s*분석|통계\s*검정|실험\s*설계|반증|재현성",
    r"\bhypothes(?:is|es)\b|\bresearch question\b|\bcausal|\bmechanism|\bbiomarker",
    r"\bdifferential expression\b|\bassociation stud|\bselect (?:a )?(?:candidate|method)",
)
_SIMPLE_SIGNALS = (
    r"형식\s*변환|파일\s*변환|집계|개수\s*(세기|계산)|원문\s*요약|그대로\s*요약|발췌",
    r"\bconvert\b|\breformat\b|\baggregate\b|\bcount\b|\bsummarize (?:the )?(?:source|text)",
)


def classify_intake(text: str, requested: str = "auto", *, scope_status: str = "in_scope") -> IntakeDecision:
    """Classify without an extra model call; ambiguous requests enter the research planning lane."""
    if requested in {"simple", "research"}:
        return IntakeDecision(work_kind=requested, reason=f"PI specified work_kind={requested}",
                              scope_status=scope_status, source="explicit")
    research = any(re.search(pattern, text, re.IGNORECASE) for pattern in _RESEARCH_SIGNALS)
    simple = any(re.search(pattern, text, re.IGNORECASE) for pattern in _SIMPLE_SIGNALS)
    if simple and not research:
        return IntakeDecision(work_kind="simple", reason="request is a fixed transformation, aggregation, or source summary",
                              scope_status=scope_status)
    if research:
        return IntakeDecision(work_kind="research", reason="request asks for a new conclusion, hypothesis, or method choice",
                              scope_status=scope_status)
    return IntakeDecision(work_kind="research", reason="request is not clearly limited to a simple operation",
                          scope_status=scope_status, confidence="ambiguous")


class PackRef(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


class ResearchBrief(StrictModel):
    question: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)
    completion_conditions: list[str] = Field(min_length=1)
    study_type: Literal["explanatory", "comparative", "exploratory", "technical"]
    primary_hypothesis: str | None = None
    null_or_alternatives: list[str] = []
    distinguishing_observations: list[str] = []

    @model_validator(mode="after")
    def hypotheses_match_study_type(self) -> "ResearchBrief":
        if self.study_type in {"explanatory", "comparative"}:
            if not self.primary_hypothesis or not self.null_or_alternatives or not self.distinguishing_observations:
                raise ValueError("explanatory/comparative research requires a hypothesis, alternatives, and distinguishing observations")
        return self


class StatisticsPlan(StrictModel):
    applicable: bool
    reason: str = Field(min_length=1)
    estimand: str | None = None
    analysis_unit: str | None = None
    comparison_groups: list[str] = []
    primary_outcomes: list[str] = []
    multiple_testing: str | None = None
    missing_and_exclusions: str | None = None
    effect_size_and_interval: str | None = None
    sensitivity_analyses: list[str] = []
    # Decisions that may be waived only with a stated reason (field -> reason).
    not_applicable: dict[str, str] = {}

    CORE: ClassVar[tuple[str, ...]] = ("estimand", "analysis_unit", "primary_outcomes")
    WAIVABLE: ClassVar[tuple[str, ...]] = ("comparison_groups", "multiple_testing", "missing_and_exclusions", "effect_size_and_interval")

    @model_validator(mode="after")
    def complete_if_applicable(self) -> "StatisticsPlan":
        if not self.applicable:
            return self
        missing_core = [name for name in self.CORE if not getattr(self, name)]
        if missing_core:
            raise ValueError("applicable statistics requires estimand, analysis_unit, and primary_outcomes; "
                             "missing: " + ", ".join(missing_core))
        unknown = set(self.not_applicable) - set(self.WAIVABLE)
        if unknown:
            raise ValueError(f"statistics not_applicable may only waive {', '.join(self.WAIVABLE)}")
        # Freezing these before analysis is the point: an unstated choice can be made after seeing results.
        missing = [name for name in self.WAIVABLE
                   if not getattr(self, name) and not str(self.not_applicable.get(name, "")).strip()]
        if missing:
            raise ValueError("applicable statistics must fix or give a not_applicable reason for: " + ", ".join(missing))
        return self


class ProtocolContract(StrictModel):
    revision: int = Field(ge=1)
    analysis_unit: str = Field(min_length=1)
    selection_criteria: list[str]
    exclusion_criteria: list[str]
    comparators: list[str]
    primary_metrics: list[str] = Field(min_length=1)
    validation_methods: list[str] = Field(min_length=1)
    resource_limits: list[str] = Field(min_length=1)
    stop_conditions: list[str] = Field(min_length=1)
    approval_conditions: list[str] = Field(min_length=1)
    data_boundaries: list[str] = Field(min_length=1)
    not_applicable: dict[str, str] = {}
    statistics: StatisticsPlan
    packs: list[PackRef] = []

    @field_validator("not_applicable")
    @classmethod
    def nonempty_na_reasons(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not str(reason).strip() for reason in value.values()):
            raise ValueError("not_applicable entries require a reason")
        return value


class EvidenceSlot(StrictModel):
    id: str = Field(min_length=1)
    required: bool
    description: str = Field(min_length=1)


class OutputTypeEntry(StrictModel):
    """One normalized output type declaration (#221 todo 2): keys only, under the vocabulary version it names.

    labhq.vocab.declare builds these from the CSO's raw entries before validation, so a malformed declaration is
    dropped there and never fails the contract. ``vocab`` freezes the meaning the PI approves at CP1."""

    name: str = Field(min_length=1, max_length=MAX_NAME)
    data_type: str | None = Field(default=None, max_length=MAX_KEY)
    format: str | None = Field(default=None, max_length=MAX_KEY)
    vocab: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_serializer(mode="wrap")
    def _drop_missing(self, handler: Any) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class ResearchStep(StrictModel):
    id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    phase: Literal["question", "plan", "evidence", "analysis", "validation", "report"]
    claim_ids: list[str]
    input_refs: list[str]
    outputs: list[str]
    checks: list[str] = Field(min_length=1)
    evidence_slots: list[EvidenceSlot]
    depends_on: list[str]
    # Pack rules and contract checks never read this; pack fields never fill it (no_type_inheritance).
    output_types: list[OutputTypeEntry] = Field(default_factory=list, max_length=MAX_ENTRIES)

    @model_serializer(mode="wrap")
    def _drop_empty_output_types(self, handler: Any) -> dict[str, Any]:
        # An empty list is left out, so plans approved before #221 keep their canonical JSON and plan_sha256.
        data = handler(self)
        if not data.get("output_types"):
            data.pop("output_types", None)
        return data

    @model_validator(mode="after")
    def unique_slot_ids(self) -> "ResearchStep":
        # Results bind to slots by id; two required slots of one id would be met by a single row (#187).
        ids = [slot.id for slot in self.evidence_slots]
        duplicates = sorted({slot_id for slot_id in ids if ids.count(slot_id) > 1})
        if duplicates:
            raise ValueError(f"step {self.id} declares evidence slot {', '.join(duplicates)} more than once")
        return self


class RecruitProposal(StrictModel):
    paper: str
    repo: str
    focus: str
    reason: str


class PackPlanValue(StrictModel):
    # Domain pack declarations support only these scalar value types; keeping that in the core schema also gives
    # Structured Outputs a concrete lossless value schema for arbitrary field names.
    fields: dict[str, str | int | bool]
    validators: dict[str, str]
    acceptance: dict[str, str]


class PackNotApplicable(StrictModel):
    not_applicable: str = Field(min_length=1)

    @field_validator("not_applicable")
    @classmethod
    def reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("not_applicable reason must not be blank")
        return value


class PackApplicabilityRecord(StrictModel):
    applied: bool
    topics_any: list[str]
    matched_topics: list[str]
    reason: Literal["topic_match", "no_topic_match", "topics_empty", "no_topic_condition", "not_applicable"]


class ResearchPlan(StrictModel):
    schema_version: Literal[2]
    topics: list[str] = Field(default_factory=list, max_length=24)
    intake: IntakeDecision
    brief: ResearchBrief
    protocol: ProtocolContract
    pack_values: dict[str, PackPlanValue | PackNotApplicable]
    # LabHQ writes these after the CSO draft. They enter the CP1 hash and audit record.
    pack_applicability: dict[str, PackApplicabilityRecord] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    checklist: dict[str, str] = Field(default_factory=dict)
    suggested_next: list[str] = Field(default_factory=list, max_length=8)
    # Same structure as the general PLAN; plain strings from older plans keep their canonical hash.
    clarifying_questions: list[str | ClarifyingQuestion]
    steps: list[ResearchStep] = Field(min_length=1)
    recruit: list[RecruitProposal]
    notes: str

    @field_validator("topics")
    @classmethod
    def approved_topics(cls, value: list[str]) -> list[str]:
        from .. import vocab as output_vocab
        from ..vocab import topics as topic_vocab

        loaded = output_vocab.current()
        if loaded is None:
            raise ValueError("research topics vocabulary is unavailable")
        normalized, unknown = topic_vocab.normalize(value, loaded)
        if unknown:
            raise ValueError(f"unknown research topics: {unknown}")
        return normalized

    @model_serializer(mode="wrap")
    def _drop_empty_topic_metadata(self, handler: Any) -> dict[str, Any]:
        # Plans frozen before topic routing must keep their canonical JSON and plan hash.
        data = handler(self)
        for field in ("topics", "pack_applicability", "warnings", "checklist", "suggested_next"):
            if not data.get(field):
                data.pop(field, None)
        return data

    @model_validator(mode="after")
    def research_only(self) -> "ResearchPlan":
        if self.intake.work_kind != "research":
            raise ValueError("research PLAN must classify work_kind as research")
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("research step ids must be unique")
        known = set(ids)
        dependencies = {step.id: step.depends_on for step in self.steps}
        for step in self.steps:
            invalid = [dep for dep in step.depends_on if dep not in known or dep == step.id]
            if invalid:
                raise ValueError(f"step {step.id}: invalid dependencies {invalid}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("research plan has a dependency cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)
        return self


class ArtifactRef(StrictModel):
    artifact_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    # Optional staff declarations (#221). A value that is not a short string is dropped, never a failed result.
    data_type: str | None = None
    format: str | None = None

    @field_validator("data_type", "format", mode="before")
    @classmethod
    def _short_key(cls, value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= MAX_KEY else None

    @model_serializer(mode="wrap")
    def _drop_missing(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        for key in ("data_type", "format"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class MethodChange(StrictModel):
    field: str = Field(min_length=1)
    planned: str = Field(min_length=1)
    actual: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    affects_conclusion: bool


class ResearchResult(StrictModel):
    schema_version: Literal[2]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_id: str = Field(min_length=1)
    claims: list[Claim]
    evidence: list[Evidence]
    links: list[EvidenceLink]
    artifact_refs: list[ArtifactRef]
    not_established: list[str]
    failures: list[str]
    method_changes: list[MethodChange]

    @model_validator(mode="after")
    def ledger_is_consistent(self) -> "ResearchResult":
        artifact_ids = [ref.artifact_id for ref in self.artifact_refs]
        errors = [f"duplicate artifact id {artifact_id}" for artifact_id in sorted(set(artifact_ids))
                  if artifact_ids.count(artifact_id) > 1]
        errors += ledger_errors(self.claims, self.evidence, self.links,
                                artifact_paths={ref.artifact_id: ref.path for ref in self.artifact_refs})
        if errors:
            raise ValueError("; ".join(errors))
        return self


def _without(schema: dict[str, Any], definition: str, fields: tuple[str, ...], drop: tuple[str, ...] = ()) -> dict[str, Any]:
    out = json.loads(json.dumps(schema))
    for name in fields:
        out["$defs"][definition]["properties"].pop(name, None)
    for name in drop:
        out["$defs"].pop(name, None)
    return out


# The engine-facing schemas stay those of main before #221: declarations are offered only when switched on.
RESEARCH_PLAN_SCHEMA: dict[str, Any] = _without(ResearchPlan.model_json_schema(), "ResearchStep", ("output_types",),
                                                ("OutputTypeEntry", "PackApplicabilityRecord"))
for _system_field in ("pack_applicability", "warnings"):
    RESEARCH_PLAN_SCHEMA["properties"].pop(_system_field, None)
    if _system_field in RESEARCH_PLAN_SCHEMA.get("required", []):
        RESEARCH_PLAN_SCHEMA["required"].remove(_system_field)
if "topics" not in RESEARCH_PLAN_SCHEMA["required"]:
    RESEARCH_PLAN_SCHEMA["required"].append("topics")
RESEARCH_RESULT_SCHEMA: dict[str, Any] = _without(ResearchResult.model_json_schema(), "ArtifactRef",
                                                  ("data_type", "format"))
# What a research step's engine is held to: the result, or the same shape with empty ledger lists and the question
# in ``blocking_decision`` when the step cannot go on without a PI decision (STEP_PROMPT). The question is handled
# before the ledger is validated, and the step re-runs with the answer (#90 CP2).
RESEARCH_STEP_SCHEMA: dict[str, Any] = json.loads(json.dumps(RESEARCH_RESULT_SCHEMA))
RESEARCH_STEP_SCHEMA["properties"]["blocking_decision"] = {
    "type": "string", "title": "Blocking Decision",
    "description": "Only when the step cannot proceed without a PI decision: the question and its choices."}


def research_plan_schema(declare: bool, entry_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """RESEARCH_PLAN_SCHEMA, or with ``declare`` a copy whose steps take optional ``output_types`` entries."""
    if not declare:
        return RESEARCH_PLAN_SCHEMA
    out = json.loads(json.dumps(RESEARCH_PLAN_SCHEMA))
    out["$defs"]["ResearchStep"]["properties"]["output_types"] = json.loads(json.dumps(entry_schema))
    return out


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_present(item) for item in value)  # [""] names nothing (PR #366 review)
    if isinstance(value, dict):
        return bool(value)
    return True


_MISSING = object()


def _pack_rule_value(field: str, pack_fields: dict[str, Any], plan_values: dict[str, Any]) -> Any:
    if field in pack_fields:
        return pack_fields[field]
    if field in plan_values:
        return plan_values[field]
    if "." not in field:
        return _MISSING
    current: Any = plan_values
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _pack_predicate_matches(predicate: Any, pack_fields: dict[str, Any], plan_values: dict[str, Any]) -> bool:
    current = _pack_rule_value(predicate.field, pack_fields, plan_values)
    if "present" in predicate.model_fields_set:
        return _present(None if current is _MISSING else current) is predicate.present
    if "min_items" in predicate.model_fields_set:
        if not isinstance(current, (list, tuple)):
            return False
        distinct = {item.strip().casefold() if isinstance(item, str) else json.dumps(item, sort_keys=True, default=str)
                    for item in current if _present(item)}
        return len(distinct) >= predicate.min_items
    if current is _MISSING:
        return False
    if "value" in predicate.model_fields_set:
        return current == predicate.value
    if "in_" in predicate.model_fields_set:
        return current in predicate.in_
    return current not in predicate.not_in


def _key_set_error(where: str, expected: list[str], supplied: Any, note: str = "") -> str | None:
    if set(supplied) == set(expected):
        return None
    message = f"{where} must contain {sorted(expected)}{note}"
    missing = sorted(set(expected) - set(supplied))
    unexpected = sorted(set(supplied) - set(expected))
    if missing and len(missing) < len(expected):  # all missing is already the list above
        message += f"; missing {missing}"
    if unexpected:
        message += f"; unexpected {unexpected}"
    return message


def _section(values: dict[str, Any], name: str) -> dict[str, Any] | None:
    section = values.get(name)
    return section if isinstance(section, dict) else None  # another shape is a schema error reported elsewhere


def _one_pack_errors(key: str, pack: Any, supplied: dict[str, Any], plan_values: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    invalid_fields: set[str] = set()
    fields = _section(supplied, "fields")
    if fields is not None:
        declared = {field.name: field for field in pack.fields}
        unknown = sorted(set(fields) - set(declared))
        if unknown:
            errors.append(f"pack_values[{key}].fields contains undeclared fields: {unknown}")
        for name, field in declared.items():
            if field.required and (name not in fields or not _present(fields.get(name))):
                errors.append(f"pack_values[{key}].fields.{name} is required")
                invalid_fields.add(name)
                continue
            if name not in fields:
                continue
            problem = field_value_problem(field, fields[name])
            if problem:
                errors.append(f"pack_values[{key}].fields.{name} {problem}")
                invalid_fields.add(name)
    fields_ok = fields is not None and not errors

    validators = _section(supplied, "validators")
    if validators is not None:
        mismatch = _key_set_error(f"pack_values[{key}].validators", [rule.id for rule in pack.validators], validators)
        if mismatch:
            errors.append(mismatch)
        for rule in pack.validators:
            if rule.id in validators and not _present(validators[rule.id]):
                errors.append(f"pack_values[{key}].validators.{rule.id} must explain how it passes")
            missing = [name for name in rule.required_fields if not _present((fields or {}).get(name))]
            if fields_ok and missing:
                errors.append(f"pack_values[{key}].validators.{rule.id} missing fields {missing}")

    acceptance = _section(supplied, "acceptance")
    if acceptance is not None:
        mismatch = _key_set_error(f"pack_values[{key}].acceptance", [rule.id for rule in pack.rules], acceptance,
                                  " (acceptance keys are the pack rule ids)")
        if mismatch:
            errors.append(mismatch)
        for rule in pack.rules:
            if rule.id in acceptance and not _present(acceptance[rule.id]):
                errors.append(f"pack_values[{key}].acceptance.{rule.id} must describe the rule outcome")

    if fields is not None and plan_values is not None:
        for rule in pack.rules:
            predicates = [*rule.conditions, rule.require or rule.forbid]
            rule_fields = [predicate.field for predicate in predicates if predicate is not None]
            if rule.allowed_combinations is not None:
                rule_fields += rule.allowed_combinations.fields
            if any(name in declared and name in invalid_fields for name in rule_fields):
                continue  # only a rule whose own input is broken must wait for the corrected draft
            if not all(_pack_predicate_matches(condition, fields, plan_values) for condition in rule.conditions):
                continue
            if rule.allowed_combinations is not None:
                current = [_pack_rule_value(name, fields, plan_values)
                           for name in rule.allowed_combinations.fields]
                passed = current in rule.allowed_combinations.rows
                if not passed:
                    names = ", ".join(rule.allowed_combinations.fields)
                    errors.append(f"pack rule {rule.id} failed: allowed combination of {names}")
                continue
            if rule.require is not None:
                passed = _pack_predicate_matches(rule.require, fields, plan_values)
                outcome, predicate = "require", rule.require
            else:
                passed = not _pack_predicate_matches(rule.forbid, fields, plan_values)
                outcome, predicate = "forbid", rule.forbid
            if not passed:
                errors.append(f"pack rule {rule.id} failed: {outcome} {predicate.field}")
    return errors


def _pack_value_errors(supplied_all: Any, plan: ResearchPlan | None, active_packs: dict[str, str],
                       pack_definitions: dict[str, Any] | None) -> list[str]:
    if not isinstance(supplied_all, dict):
        return []
    errors: list[str] = []
    applied = {key: value for key, value in supplied_all.items()
               if not (isinstance(value, dict) and set(value) == {"not_applicable"})}
    if set(applied) != set(active_packs):
        missing = sorted(set(active_packs) - set(applied))
        unexpected = sorted(set(applied) - set(active_packs))
        errors.append(f"research plan pack_values must equal the configured snapshot: {sorted(active_packs)}" +
                      (f"; missing {missing}" if missing else "") + (f"; unexpected {unexpected}" if unexpected else ""))
    if not active_packs:
        return errors
    if pack_definitions is None or set(pack_definitions) != set(active_packs):
        return errors + ["active research packs require their definitions before PLAN validation"]
    plan_values = plan.model_dump(mode="python") if plan is not None else None
    for key, loaded in pack_definitions.items():
        if loaded.sha256 != active_packs[key]:
            errors.append(f"research pack definition hash changed for {key}")
            continue
        supplied = supplied_all.get(key)
        if isinstance(supplied, dict):
            errors += _one_pack_errors(key, loaded.pack, supplied, plan_values)
    return errors


def _contract_errors(plan: ResearchPlan | None, value: Any, *, max_steps: int, active_packs: dict[str, str],
                     expected_intake: IntakeDecision | None,
                     pack_definitions: dict[str, Any] | None) -> list[str]:
    """Every check after the schema. A draft that fails the schema still gets its pack checks."""
    raw = value if isinstance(value, dict) else {}
    errors: list[str] = []
    steps = plan.steps if plan is not None else raw.get("steps")
    if isinstance(steps, list) and len(steps) > max_steps:
        errors.append(f"research plan has {len(steps)} steps; maximum is {max_steps}; re-plan without truncation")
    if plan is not None:
        selected = {ref.key: ref.sha256 for ref in plan.protocol.packs}
        if selected != active_packs:
            changed = sorted(key for key in set(selected) & set(active_packs) if selected[key] != active_packs[key])
            errors.append(f"research plan packs must equal the configured snapshot: {sorted(active_packs)}; "
                          f"protocol.packs lists {sorted(selected)}" +
                          (f"; sha256 differs for {changed}" if changed else ""))
    supplied = ({key: item.model_dump(mode="python") for key, item in plan.pack_values.items()}
                if plan is not None else raw.get("pack_values"))
    errors += _pack_value_errors(supplied, plan, active_packs, pack_definitions)
    if plan is not None and expected_intake and (plan.intake.work_kind != expected_intake.work_kind or
                                                 plan.intake.scope_status != expected_intake.scope_status):
        errors.append("research PLAN intake does not match the request intake decision")
    return errors


def schema_error_lines(error: ValidationError) -> list[str]:
    """One `location: reason` line per schema problem, without pydantic's input echo and help URL."""
    lines = []
    for item in error.errors():
        where = ".".join(str(part) for part in item.get("loc") or ()) or "plan"
        reason = str(item.get("msg") or "invalid value")
        lines.append(f"{where}: {reason[len('Value error, '):] if reason.startswith('Value error, ') else reason}")
    return lines


def research_plan_errors(value: Any, *, max_steps: int, active_packs: dict[str, str],
                         expected_intake: IntakeDecision | None = None,
                         pack_definitions: dict[str, Any] | None = None) -> list[str]:
    """All problems of a PLAN at once, so one correction can fix them together (#222)."""
    try:
        plan: ResearchPlan | None = ResearchPlan.model_validate(value)
        errors: list[str] = []
    except ValidationError as error:
        plan, errors = None, schema_error_lines(error)
    return errors + _contract_errors(plan, value, max_steps=max_steps, active_packs=active_packs,
                                     expected_intake=expected_intake, pack_definitions=pack_definitions)


def with_pack_refs(value: Any, refs: list[dict[str, str]]) -> Any:
    """`protocol.packs` is the configured snapshot, not a CSO choice, so labhq writes it (#222)."""
    if not isinstance(value, dict) or not isinstance(value.get("protocol"), dict):
        return value
    return {**value, "protocol": {**value["protocol"], "packs": [dict(ref) for ref in refs]}}


def validate_research_plan(value: Any, *, max_steps: int, active_packs: dict[str, str],
                           expected_intake: IntakeDecision | None = None,
                           pack_definitions: dict[str, Any] | None = None) -> ResearchPlan:
    plan = ResearchPlan.model_validate(value)
    errors = _contract_errors(plan, value, max_steps=max_steps, active_packs=active_packs,
                              expected_intake=expected_intake, pack_definitions=pack_definitions)
    if errors:
        raise ValueError("; ".join(errors))
    return plan


def validate_research_result(value: Any, *, plan: ResearchPlan | dict[str, Any]) -> ResearchResult:
    """Parse one step result and bind it to the frozen plan revision and step it claims to answer."""
    result = ResearchResult.model_validate(value)
    parsed = plan if isinstance(plan, ResearchPlan) else ResearchPlan.model_validate(plan)
    if result.plan_sha256 != plan_sha256(parsed):
        raise ValueError("research result plan_sha256 does not match the frozen plan")
    step = next((step for step in parsed.steps if step.id == result.step_id), None)
    if step is None:
        raise ValueError(f"research result step_id {result.step_id} is not in the frozen plan")
    errors = step_binding_errors(result, step)
    if errors:
        raise ValueError("; ".join(errors))
    return result


def research_result_errors(value: Any, *, plan: ResearchPlan | dict[str, Any]) -> list[str]:
    """Every readable result-contract problem for the one correction turn.

    Step binding (claim ids, evidence slots) is read from the raw JSON too, so a correction learns it with the field
    errors: checked only after the model parsed, a missing slot surfaced after the last correction (9th mock trial)."""
    try:
        validate_research_result(value, plan=plan)
    except ValidationError as error:
        problems = schema_error_lines(error)
    except (TypeError, ValueError) as error:
        problems = [part for part in (item.strip() for item in str(error).split(";")) if part]
    else:
        return []
    return list(dict.fromkeys([*problems, *_raw_binding_errors(value, plan)]))


def _raw_binding_errors(value: Any, plan: ResearchPlan | dict[str, Any]) -> list[str]:
    """step_binding_errors on result JSON that does not parse yet."""
    if not isinstance(value, dict):
        return []
    try:
        parsed = plan if isinstance(plan, ResearchPlan) else ResearchPlan.model_validate(plan)
    except ValidationError:
        return []
    step = next((step for step in parsed.steps if step.id == value.get("step_id")), None)
    claim_rows, evidence_rows = value.get("claims"), value.get("evidence")
    # A scalar ledger is a schema error already; iterating it would raise instead of asking for a correction.
    if step is None or not isinstance(claim_rows, list) or not isinstance(evidence_rows, list):
        return []
    claims = [str(row["id"]) for row in claim_rows if isinstance(row, dict) and row.get("id")]
    rows = [(str(row.get("id")), [slot for slot in row["slots"] if isinstance(slot, str)]
             if isinstance(row.get("slots"), list) else [])
            for row in evidence_rows if isinstance(row, dict)]
    return _binding_errors(claims, rows, step)


def _row_id(row_type: str, row: Any, index: int) -> str:
    if not isinstance(row, dict):
        return f"{row_type}[{index}]"
    if row_type in {"claim", "evidence"}:
        return str(row.get("id") or f"{row_type}[{index}]")
    return (f"{row.get('claim_id') or '?'}@{row.get('claim_revision') or '?'}->"
            f"{row.get('evidence_id') or '?'}:{row.get('relation') or '?'}")


def _root_salvage_targets(problem: str, value: dict[str, Any]) -> list[tuple[str, int]] | None:
    """Map one cross-row validator message to the row(s) that can be refused safely."""
    evidence = value.get("evidence") or []
    claims = value.get("claims") or []
    links = value.get("links") or []

    # Before the generic evidence pattern below, which would refuse the whole row (PR #353 review).
    duplicate = re.match(r"evidence (\S+) is linked to claim (\S+) more than once", problem)
    if duplicate:
        evidence_id, claim_id = duplicate.groups()
        matches = [index for index, row in enumerate(links) if isinstance(row, dict)
                   and row.get("claim_id") == claim_id and row.get("evidence_id") == evidence_id]
        return [("link", index) for index in matches[1:]]
    same_source = re.match(r"evidence (\S+) and (\S+) cite the same source", problem)
    if same_source:
        row_id = same_source.group(2)
        return [("evidence", index) for index, row in enumerate(evidence)
                if isinstance(row, dict) and row.get("id") == row_id]
    row_problem = re.match(r"(?:evidence|(?:inference|hypothesis) row) (\S+)", problem)
    if row_problem:
        row_id = row_problem.group(1)
        return [("evidence", index) for index, row in enumerate(evidence)
                if isinstance(row, dict) and row.get("id") == row_id]
    claim_problem = re.match(r"claim ([A-Za-z][A-Za-z0-9_.:-]{0,79})(?:@\d+)?", problem)
    if claim_problem:
        claim_id = claim_problem.group(1)
        return [("claim", index) for index, row in enumerate(claims)
                if isinstance(row, dict) and row.get("id") == claim_id]
    unknown_claim = re.match(r"link to unknown claim (\S+)", problem)
    if unknown_claim:
        claim_id = unknown_claim.group(1)
        return [("link", index) for index, row in enumerate(links)
                if isinstance(row, dict) and row.get("claim_id") == claim_id]
    link_problem = re.match(r"link ([^@\s]+)(?:@\d+)?->([^\s]+)", problem)
    if link_problem:
        claim_id, evidence_id = link_problem.groups()
        return [("link", index) for index, row in enumerate(links) if isinstance(row, dict)
                and row.get("claim_id") == claim_id and row.get("evidence_id") == evidence_id]
    return None


def _validation_salvage_targets(error: ValidationError, value: dict[str, Any]) \
        -> list[tuple[str, int, str]] | None:
    targets: list[tuple[str, int, str]] = []
    names = {"claims": "claim", "evidence": "evidence", "links": "link"}
    for item in error.errors():
        loc = tuple(item.get("loc") or ())
        message = str(item.get("msg") or "invalid value")
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]
        where = ".".join(str(part) for part in loc) or "result"
        reason = f"{where}: {message}"
        if len(loc) >= 2 and loc[0] in names and isinstance(loc[1], int):
            targets.append((names[loc[0]], loc[1], reason))
            continue
        if loc:
            return None
        for problem in (part.strip() for part in message.split(";") if part.strip()):
            if problem in {"link it as context or record it as a claim",
                           "use partially_supported or contradicted"}:
                continue  # continuation of the preceding row-local error
            mapped = _root_salvage_targets(problem, value)
            if not mapped:
                return None
            targets.extend((row_type, index, problem) for row_type, index in mapped)
    return targets or None


def salvage_research_result(value: Any, *, plan: ResearchPlan | dict[str, Any], expected_step_id: str) \
        -> tuple[ResearchResult | None, list[dict[str, str]], list[dict[str, str]], list[str]]:
    """Refuse row-local contract defects after correction turns, without weakening structural validation.

    Evidence refusals cascade through ``derived_from`` and links. A claim that loses its only relation required by
    its status is removed from the valid ledger and recorded as unsupported for CP2. The frozen plan identity,
    result envelope, step binding and required evidence slots are never salvaged.
    """
    problems = research_result_errors(value, plan=plan)
    if not problems:
        result = validate_research_result(value, plan=plan)
        if result.step_id != expected_step_id:
            return None, [], [], [f"research result step_id {result.step_id} does not match {expected_step_id}"]
        return result, [], [], []
    if not isinstance(value, dict):
        return None, [], [], problems
    parsed_plan = plan if isinstance(plan, ResearchPlan) else ResearchPlan.model_validate(plan)
    required = {name for name, field in ResearchResult.model_fields.items() if field.is_required()}
    list_fields = {"claims", "evidence", "links", "artifact_refs", "not_established", "failures", "method_changes"}
    structural = []
    structural += [f"research result is missing required field {name}" for name in sorted(required - set(value))]
    structural += [f"research result has unknown field {name}" for name in sorted(set(value) - set(ResearchResult.model_fields))]
    if value.get("schema_version") != 2:
        structural.append("research result schema_version must be 2")
    if value.get("plan_sha256") != plan_sha256(parsed_plan):
        structural.append("research result plan_sha256 does not match the frozen plan")
    if value.get("step_id") != expected_step_id:
        structural.append(f"research result step_id {value.get('step_id')} does not match {expected_step_id}")
    if not any(step.id == expected_step_id for step in parsed_plan.steps):
        structural.append(f"research result step_id {expected_step_id} is not in the frozen plan")
    structural += [f"research result {name} must be a list" for name in sorted(list_fields)
                   if name in value and not isinstance(value[name], list)]
    if structural:
        return None, [], [], structural

    candidate = copy.deepcopy(value)
    refused: list[dict[str, str]] = []
    unsupported: list[dict[str, str]] = []
    refused_index: dict[tuple[str, str], int] = {}

    def refuse(row_type: str, row_id: str, reason: str) -> None:
        key = row_type, row_id
        if key in refused_index:
            old = refused[refused_index[key]]["reason"]
            if reason not in old:
                refused[refused_index[key]]["reason"] = old + "; " + reason
            return
        refused_index[key] = len(refused)
        refused.append({"row_type": row_type, "row_id": row_id, "reason": reason})

    limit = sum(len(candidate.get(name) or []) for name in ("claims", "evidence", "links")) + 1
    for _ in range(limit):
        try:
            result = validate_research_result(candidate, plan=parsed_plan)
        except ValidationError as error:
            targets = _validation_salvage_targets(error, candidate)
            if not targets:
                return None, [], [], research_result_errors(candidate, plan=parsed_plan)
        except (TypeError, ValueError):
            # These are frozen-plan/step binding failures, including a required slot lost during refusal.
            return None, [], [], research_result_errors(candidate, plan=parsed_plan)
        else:
            if result.step_id != expected_step_id:
                return None, [], [], [f"research result step_id {result.step_id} does not match {expected_step_id}"]
            return result, refused, unsupported, []

        evidence_rows = candidate.get("evidence") or []
        claim_rows = candidate.get("claims") or []
        link_rows = candidate.get("links") or []
        evidence_targets = {index: reason for kind, index, reason in targets if kind == "evidence"}
        claim_targets = {index: reason for kind, index, reason in targets if kind == "claim"}
        link_targets = {index: reason for kind, index, reason in targets if kind == "link"}
        removed_evidence: dict[str, str] = {}
        for index, reason in evidence_targets.items():
            if 0 <= index < len(evidence_rows):
                row_id = _row_id("evidence", evidence_rows[index], index)
                removed_evidence[row_id] = reason
        changed = True
        while changed:
            changed = False
            for index, row in enumerate(evidence_rows):
                if not isinstance(row, dict):
                    continue
                row_id = _row_id("evidence", row, index)
                parents = [str(parent) for parent in row.get("derived_from") or [] if str(parent) in removed_evidence]
                if row_id not in removed_evidence and parents:
                    removed_evidence[row_id] = f"derived from contract-refused evidence {', '.join(sorted(parents))}"
                    changed = True
        for index, row in enumerate(evidence_rows):
            row_id = _row_id("evidence", row, index)
            if row_id in removed_evidence:
                refuse("evidence", row_id, removed_evidence[row_id])

        removed_claims: dict[str, str] = {}
        for index, reason in claim_targets.items():
            if 0 <= index < len(claim_rows):
                claim_id = _row_id("claim", claim_rows[index], index)
                removed_claims[claim_id] = reason
                unsupported.append({"claim_id": claim_id, "reason": reason})

        removed_link_indexes = set(index for index in link_targets if 0 <= index < len(link_rows))
        for index, link in enumerate(link_rows):
            if not isinstance(link, dict):
                continue
            if str(link.get("evidence_id")) in removed_evidence or str(link.get("claim_id")) in removed_claims:
                removed_link_indexes.add(index)

        remaining_links = [row for index, row in enumerate(link_rows) if index not in removed_link_indexes]
        for index, claim in enumerate(claim_rows):
            if not isinstance(claim, dict):
                continue
            claim_id = _row_id("claim", claim, index)
            if claim_id in removed_claims:
                continue
            needed = STATUS_NEEDS.get(claim.get("status"))
            if needed and not any(isinstance(link, dict) and link.get("claim_id") == claim_id
                                  and link.get("relation") == needed for link in remaining_links):
                lost = sorted({str(link.get("evidence_id")) for index, link in enumerate(link_rows)
                               if index in removed_link_indexes and isinstance(link, dict)
                               and link.get("claim_id") == claim_id and link.get("relation") == needed})
                reason = (f"{claim.get('status')} rests only on contract-refused evidence {', '.join(lost)}"
                          if lost else f"{claim.get('status')} has no remaining valid {needed} link after contract salvage")
                removed_claims[claim_id] = reason
                unsupported.append({"claim_id": claim_id, "reason": reason})
                for link_index, link in enumerate(link_rows):
                    if isinstance(link, dict) and link.get("claim_id") == claim_id:
                        removed_link_indexes.add(link_index)

        for index in sorted(removed_link_indexes):
            link = link_rows[index]
            reason = link_targets.get(index)
            if reason is None and isinstance(link, dict) and str(link.get("evidence_id")) in removed_evidence:
                reason = f"points to contract-refused evidence {link.get('evidence_id')}"
            if reason is None:
                reason = f"points to contract-refused claim {link.get('claim_id') if isinstance(link, dict) else '?'}"
            refuse("link", _row_id("link", link, index), reason)
        for index, claim in enumerate(claim_rows):
            claim_id = _row_id("claim", claim, index)
            if claim_id in removed_claims:
                refuse("claim", claim_id, removed_claims[claim_id])

        before = (len(evidence_rows), len(claim_rows), len(link_rows))
        candidate["evidence"] = [row for index, row in enumerate(evidence_rows)
                                 if _row_id("evidence", row, index) not in removed_evidence]
        candidate["claims"] = [row for index, row in enumerate(claim_rows)
                               if _row_id("claim", row, index) not in removed_claims]
        candidate["links"] = [row for index, row in enumerate(link_rows) if index not in removed_link_indexes]
        after = (len(candidate["evidence"]), len(candidate["claims"]), len(candidate["links"]))
        if before == after:
            return None, [], [], research_result_errors(candidate, plan=parsed_plan)
    return None, [], [], research_result_errors(candidate, plan=parsed_plan)


def step_binding_errors(result: ResearchResult, step: ResearchStep) -> list[str]:
    """What the step declared is what its result answers: only its claims, and every required slot addressed."""
    return _binding_errors([claim.id for claim in result.claims], [(row.id, row.slots) for row in result.evidence],
                           step)


def _binding_errors(claim_ids: list[str], rows: list[tuple[str, list[str]]], step: ResearchStep) -> list[str]:
    errors = [f"claim {claim_id} is outside the claim_ids {step.claim_ids} that step {step.id} declared"
              for claim_id in claim_ids if claim_id not in step.claim_ids]
    declared = {slot.id for slot in step.evidence_slots}
    filled: set[str] = set()
    for row_id, slots in rows:
        for slot in slots:
            if slot not in declared:
                errors.append(f"evidence {row_id} fills slot {slot} that step {step.id} does not declare")
            filled.add(slot)
    row_ids = {row_id for row_id, _ in rows}
    # 9th mock trial: rows named after their slots, with no "slots" field, three turns in a row.
    errors += [f"required evidence slot {slot.id} of step {step.id} has no evidence row; list it in evidence.slots "
               "of the row that tried it, even when the attempt failed or found nothing" +
               (f' (evidence row {slot.id} is named after the slot but lists no "slots": add "slots": ["{slot.id}"])'
                if slot.id in row_ids else "")
               for slot in step.evidence_slots if slot.required and slot.id not in filled]
    return errors


def bind_result_artifacts(result: dict[str, Any], *, outputs: list[str],
                          upstream: list[tuple], output_sha256: dict[str, str] | None = None) -> dict[str, Any]:
    """Evidence CP2 refuses because its artifact is not a file labhq collected, and claims left without support.

    A normalized ``artifact_refs[].path`` binds when it is one of the step's collected ``outputs``, or an output
    collected by a finished upstream step (``(workdir_id, workdir, outputs)``) written as ``<workdir_id>/<path>`` or
    under that step's workdir, as the step prompt lists them. Rows citing an unbound artifact are refused, and so
    are rows derived from a refused row. A claim whose status rests only on refused rows is listed as unsupported.
    """
    known = {normalize_artifact_path(path) for path in outputs}
    hashes = {normalize_artifact_path(path): value for path, value in (output_sha256 or {}).items()}
    for item in upstream:
        workdir_id, workdir, paths, *rest = item
        upstream_hashes = rest[0] if rest and isinstance(rest[0], dict) else {}
        root = normalize_artifact_path(workdir or "")  # forward slashes, so the join below needs no backslash
        for path in map(normalize_artifact_path, paths):
            if workdir_id:
                joined = normalize_artifact_path(f"{workdir_id}/{path}")
                known.add(joined)
                if path in upstream_hashes:
                    hashes[joined] = upstream_hashes[path]
            if root:
                joined = normalize_artifact_path(f"{root}/{path}")
                known.add(joined)
                if path in upstream_hashes:
                    hashes[joined] = upstream_hashes[path]
    known.discard("")
    artifact_sha256 = {
        str(ref.get("artifact_id")): hashes.get(normalized)
        for ref in result.get("artifact_refs") or []
        if (normalized := normalize_artifact_path(str(ref.get("path") or ""))) in known
    }
    unbound = {str(ref.get("artifact_id")): str(ref.get("path") or "")
               for ref in result.get("artifact_refs") or []
               if normalize_artifact_path(str(ref.get("path") or "")) not in known}
    rows = [row for row in result.get("evidence") or [] if isinstance(row, dict)]
    refused: dict[str, str] = {}
    for row in rows:
        artifact = (row.get("source") or {}).get("artifact_id")
        if artifact in unbound:
            refused[row["id"]] = (f"cites artifact {artifact} at {unbound[artifact]!r}, which is neither a collected "
                                  "output of this step nor a verified upstream artifact")
    pending = True
    while pending:
        pending = False
        for row in rows:
            parents = sorted(ref for ref in row.get("derived_from") or [] if ref in refused)
            if row["id"] not in refused and parents:
                refused[row["id"]] = f"derived from refused evidence {', '.join(parents)}"
                pending = True
    unsupported: list[dict[str, str]] = []
    for claim in result.get("claims") or []:
        needed = STATUS_NEEDS.get(claim.get("status"))
        cited = sorted({link.get("evidence_id") for link in result.get("links") or []
                        if link.get("claim_id") == claim.get("id") and link.get("relation") == needed})
        if needed and cited and all(evidence_id in refused for evidence_id in cited):
            unsupported.append({"claim_id": claim["id"],
                                "reason": f"{claim['status']} rests only on refused evidence {', '.join(cited)}"})
    return {"refused_evidence": [{"evidence_id": evidence_id, "reason": reason}
                                  for evidence_id, reason in refused.items()],
            "unsupported_claims": unsupported, "artifact_sha256": artifact_sha256}


EVIDENCE_CHOICES = ("approve", "revise", "deny")


def read_evidence_decision(decision: dict[str, Any]) -> str | None:
    """The CP2 outcome from the structured choice alone: approved, revision_requested, rejected, or None.

    The note is a memo and never decides. A deny, timeout or expiry without a choice stops the request. An
    approval without a choice, an unknown choice, or one that contradicts ``approved`` cannot be read: it is not
    approved, and the PI is asked again."""
    choice, approved = decision.get("choice"), decision.get("approved") is True
    if choice is None:
        return None if approved else "rejected"
    if not isinstance(choice, str):
        return None
    return {("approve", True): "approved", ("revise", False): "revision_requested",
            ("deny", False): "rejected"}.get((choice, approved))


def canonical_plan_json(plan: ResearchPlan | dict[str, Any]) -> str:
    value = plan.model_dump(mode="json") if isinstance(plan, ResearchPlan) else ResearchPlan.model_validate(plan).model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_sha256(plan: ResearchPlan | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest()


def freeze_plan(plan: ResearchPlan | dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    target = plan_sha256(plan)
    return {
        "gate": "research_plan",
        "target_sha256": target,
        "approved": bool(decision.get("approved")),
        "status": "approved" if decision.get("approved") else "rejected",
        "approval_id": decision.get("approval_id"),
        "decider": "PI",
        "delegation_scope": decision.get("delegation_scope") or [],
        "note": str(decision.get("note") or ""),
        "decided_at": decision.get("decided_at", time.time()),
    }


def refresh_plan_approval(plan: ResearchPlan | dict[str, Any], receipt: dict[str, Any] | None) -> dict[str, Any]:
    current = plan_sha256(plan)
    if receipt and receipt.get("approved") and receipt.get("target_sha256") == current:
        return {**receipt, "status": "approved", "reapproval_required": False}
    if receipt and receipt.get("approved"):
        return {**receipt, "status": "needs_reapproval", "reapproval_required": True,
                "current_sha256": current}
    return {**(receipt or {}), "status": "approval_required", "reapproval_required": True,
            "current_sha256": current}


def task_round_plan(request: dict[str, Any], task: dict[str, Any]) -> tuple[Any, Any]:
    """(plan, plan_sha256) a research task ran under (#90 continuation).

    A request that continued after a review "revise" keeps each earlier round's frozen plan in
    ``research_contract.rounds``; a task of such a round (task meta ``research_round``, absent in round 1) ran under
    that plan, not the request's current one. Any other task ran under the current plan."""
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    contract = request.get("research_contract") if isinstance(request.get("research_contract"), dict) else {}
    try:
        round_no, current = int(meta.get("research_round") or 1), int(contract.get("round") or 1)
    except (TypeError, ValueError):
        round_no = current = 1
    if round_no != current:
        for archived in contract.get("rounds") or []:
            if isinstance(archived, dict) and archived.get("round") == round_no:
                return archived.get("plan"), archived.get("plan_sha256")
    return request.get("plan"), contract.get("plan_sha256")
