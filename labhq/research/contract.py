from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    @model_validator(mode="after")
    def complete_if_applicable(self) -> "StatisticsPlan":
        if self.applicable and not (self.estimand and self.analysis_unit and self.primary_outcomes):
            raise ValueError("applicable statistics requires estimand, analysis_unit, and primary_outcomes")
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


class RecruitProposal(StrictModel):
    paper: str
    repo: str
    focus: str
    reason: str


class PackPlanValue(StrictModel):
    fields: dict[str, Any]
    validators: dict[str, str]
    acceptance: dict[str, str]


class ResearchPlan(StrictModel):
    schema_version: Literal[2]
    intake: IntakeDecision
    brief: ResearchBrief
    protocol: ProtocolContract
    pack_values: dict[str, PackPlanValue]
    clarifying_questions: list[str]
    steps: list[ResearchStep] = Field(min_length=1)
    recruit: list[RecruitProposal]
    notes: str

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


class Finding(StrictModel):
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    kind: Literal["finding", "inference", "hypothesis"]
    evidence_refs: list[str]
    limitations: list[str]


class EvidenceResult(StrictModel):
    evidence_id: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    status: Literal["observed", "unavailable", "not_found", "failed"]
    source_ref: str = Field(min_length=1)


class ArtifactRef(StrictModel):
    artifact_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


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
    findings: list[Finding]
    evidence: list[EvidenceResult]
    artifact_refs: list[ArtifactRef]
    not_established: list[str]
    failures: list[str]
    method_changes: list[MethodChange]


RESEARCH_PLAN_SCHEMA: dict[str, Any] = ResearchPlan.model_json_schema()
RESEARCH_RESULT_SCHEMA: dict[str, Any] = ResearchResult.model_json_schema()


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _pack_predicate_matches(predicate: Any, pack_fields: dict[str, Any], plan_values: dict[str, Any]) -> bool:
    if predicate.field in pack_fields:
        current = pack_fields[predicate.field]
    elif predicate.field in plan_values:
        current = plan_values[predicate.field]
    elif "." not in predicate.field:
        return False
    else:
        current: Any = plan_values
        for part in predicate.field.split("."):
            current = current[part]
    if "value" in predicate.model_fields_set:
        return current == predicate.value
    if "in_" in predicate.model_fields_set:
        return current in predicate.in_
    return current not in predicate.not_in


def _validate_pack_values(plan: ResearchPlan, active_packs: dict[str, str],
                          pack_definitions: dict[str, Any] | None) -> None:
    if set(plan.pack_values) != set(active_packs):
        raise ValueError(f"research plan pack_values must equal the configured snapshot: {sorted(active_packs)}")
    if not active_packs:
        return
    if pack_definitions is None or set(pack_definitions) != set(active_packs):
        raise ValueError("active research packs require their definitions before PLAN validation")
    for key, loaded in pack_definitions.items():
        if loaded.sha256 != active_packs[key]:
            raise ValueError(f"research pack definition hash changed for {key}")
        supplied = plan.pack_values[key]
        pack = loaded.pack
        declared = {field.name: field for field in pack.fields}
        unknown = sorted(set(supplied.fields) - set(declared))
        if unknown:
            raise ValueError(f"pack_values[{key}].fields contains undeclared fields: {unknown}")
        for name, field in declared.items():
            if field.required and (name not in supplied.fields or not _present(supplied.fields.get(name))):
                raise ValueError(f"pack_values[{key}].fields.{name} is required")
            if name not in supplied.fields:
                continue
            current = supplied.fields[name]
            valid_type = ((field.value_type == "string" and isinstance(current, str)) or
                          (field.value_type == "integer" and isinstance(current, int) and not isinstance(current, bool)) or
                          (field.value_type == "boolean" and isinstance(current, bool)))
            if not valid_type:
                raise ValueError(f"pack_values[{key}].fields.{name} must be {field.value_type}")
            if field.allowed_values and current not in field.allowed_values:
                raise ValueError(f"pack_values[{key}].fields.{name} must be one of {field.allowed_values}")
            if field.minimum is not None and current < field.minimum:
                raise ValueError(f"pack_values[{key}].fields.{name} must be at least {field.minimum:g}")

        validator_ids = {rule.id for rule in pack.validators}
        if set(supplied.validators) != validator_ids:
            raise ValueError(f"pack_values[{key}].validators must contain {sorted(validator_ids)}")
        for rule in pack.validators:
            if not _present(supplied.validators[rule.id]):
                raise ValueError(f"pack_values[{key}].validators.{rule.id} must explain how it passes")
            missing = [name for name in rule.required_fields if not _present(supplied.fields.get(name))]
            if missing:
                raise ValueError(f"pack_values[{key}].validators.{rule.id} missing fields {missing}")

        acceptance_ids = {rule.id for rule in pack.rules}
        if set(supplied.acceptance) != acceptance_ids:
            raise ValueError(f"pack_values[{key}].acceptance must contain {sorted(acceptance_ids)}")
        for rule in pack.rules:
            if not _present(supplied.acceptance[rule.id]):
                raise ValueError(f"pack_values[{key}].acceptance.{rule.id} must describe the rule outcome")

        plan_values = plan.model_dump(mode="python")
        for rule in pack.rules:
            if rule.when is not None and not _pack_predicate_matches(rule.when, supplied.fields, plan_values):
                continue
            if rule.require is not None:
                passed = _pack_predicate_matches(rule.require, supplied.fields, plan_values)
                outcome = "require"
                predicate = rule.require
            else:
                passed = not _pack_predicate_matches(rule.forbid, supplied.fields, plan_values)
                outcome = "forbid"
                predicate = rule.forbid
            if not passed:
                raise ValueError(f"pack rule {rule.id} failed: {outcome} {predicate.field}")


def validate_research_plan(value: Any, *, max_steps: int, active_packs: dict[str, str],
                           expected_intake: IntakeDecision | None = None,
                           pack_definitions: dict[str, Any] | None = None) -> ResearchPlan:
    plan = ResearchPlan.model_validate(value)
    if len(plan.steps) > max_steps:
        raise ValueError(f"research plan has {len(plan.steps)} steps; maximum is {max_steps}; re-plan without truncation")
    selected = {ref.key: ref.sha256 for ref in plan.protocol.packs}
    if selected != active_packs:
        raise ValueError(f"research plan packs must equal the configured snapshot: {sorted(active_packs)}")
    _validate_pack_values(plan, active_packs, pack_definitions)
    if expected_intake and (plan.intake.work_kind != expected_intake.work_kind or
                            plan.intake.scope_status != expected_intake.scope_status):
        raise ValueError("research PLAN intake does not match the request intake decision")
    return plan


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
