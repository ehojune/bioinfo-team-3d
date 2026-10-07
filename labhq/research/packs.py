from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PackSource(StrictModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    license: str = Field(min_length=1)


class PackField(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    required: bool = True
    value_type: Literal["string", "integer", "boolean"] = "string"
    allowed_values: list[str] = []
    minimum: float | None = None
    pattern: str | None = None

    @model_serializer(mode="plain")
    def serialize_field(self) -> dict[str, Any]:
        # Keep hashes of packs without the new constraint byte-for-byte stable.
        result = {"name": self.name, "description": self.description, "required": self.required,
                  "value_type": self.value_type, "allowed_values": self.allowed_values, "minimum": self.minimum}
        if self.pattern is not None:
            result["pattern"] = self.pattern
        return result


def field_value_problem(field: PackField, value: Any) -> str | None:
    """Why ``value`` cannot answer ``field``, or None. Plan answers and allowed_combinations cells share it."""
    valid_type = ((field.value_type == "string" and isinstance(value, str)) or
                  (field.value_type == "integer" and isinstance(value, int) and not isinstance(value, bool)) or
                  (field.value_type == "boolean" and isinstance(value, bool)))
    if not valid_type:
        return f"must be {field.value_type}"
    if field.allowed_values and value not in field.allowed_values:
        return f"must be one of {field.allowed_values}"
    if field.minimum is not None and value < field.minimum:
        return f"must be at least {field.minimum:g}"
    if field.pattern is not None and re.fullmatch(field.pattern, value) is None:
        return f"must match {field.pattern}"
    return None


def _model_in(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return next((arg for arg in get_args(annotation) if isinstance(arg, type) and issubclass(arg, BaseModel)), None)


def plan_field_problem(path: str, value: Any) -> str | None:
    """Why ``value`` can never equal the core PLAN field at ``path``, or None (PR #403 review): a closed-table
    cell outside the PLAN schema type (a Literal choice, int, bool, or a list field) is a dead row."""
    from pydantic import TypeAdapter, ValidationError

    from .contract import ResearchPlan  # contract imports this module, so the schema is looked up late

    model: type[BaseModel] | None = ResearchPlan
    annotation: Any = None
    metadata: list[Any] = []
    for part in path.split("."):
        if model is None or part not in model.model_fields:
            return None
        annotation, metadata = model.model_fields[part].annotation, model.model_fields[part].metadata
        model = _model_in(annotation)
    # The Field constraints (ge=1, min_length=1) live in the metadata, not the annotation (PR #406 review).
    schema = Annotated[(annotation, *metadata)] if metadata else annotation
    try:
        TypeAdapter(schema).validate_python(value, strict=True)
    except ValidationError:
        return f"does not fit the PLAN schema type {annotation!r}"
    return None


class PackValidator(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    requirement: str = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def cannot_override_core(cls, value: str) -> str:
        if value.startswith("core."):
            raise ValueError("domain packs cannot override core validators")
        return value


class PackFixture(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    purpose: str = Field(min_length=1)


class PackPredicate(StrictModel):
    field: str = Field(min_length=1)
    value: Any = None
    in_: list[Any] | None = Field(default=None, alias="in", min_length=1)
    not_in: list[Any] | None = Field(default=None, min_length=1)
    present: bool | None = None
    # At least this many distinct non-blank list items: `present` passes a list with one group or [""] (PR #366 review).
    min_items: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exactly_one_operator(self) -> "PackPredicate":
        operators = {"value", "in_", "not_in", "present", "min_items"} & self.model_fields_set
        if len(operators) != 1:
            raise ValueError("pack predicate requires exactly one operator: value, in, not_in, present, or min_items")
        return self

    @model_serializer(mode="plain")
    def serialize_predicate(self) -> dict[str, Any]:
        result = {"field": self.field}
        if "value" in self.model_fields_set:
            result["value"] = self.value
        elif "in_" in self.model_fields_set:
            result["in"] = self.in_
        elif "not_in" in self.model_fields_set:
            result["not_in"] = self.not_in
        elif "min_items" in self.model_fields_set:
            result["min_items"] = self.min_items
        else:
            result["present"] = self.present
        return result


class PackAllowedCombinations(StrictModel):
    fields: list[str] = Field(min_length=2)
    rows: list[list[str | int | bool | None]] = Field(min_length=1)

    @model_validator(mode="after")
    def rectangular_unique_table(self) -> "PackAllowedCombinations":
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("pack allowed_combinations fields must be unique")
        wrong = [index for index, row in enumerate(self.rows) if len(row) != len(self.fields)]
        if wrong:
            raise ValueError(f"pack allowed_combinations rows must have {len(self.fields)} cells; bad rows: {wrong}")
        encoded = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in self.rows]
        if len(encoded) != len(set(encoded)):
            raise ValueError("pack allowed_combinations rows must be unique")
        return self


class PackRule(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    description: str = Field(min_length=1)
    # One predicate, or a list of predicates that must all hold (e.g. model x likelihood family).
    when: PackPredicate | list[PackPredicate] | None = None
    require: PackPredicate | None = None
    forbid: PackPredicate | None = None
    allowed_combinations: PackAllowedCombinations | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> "PackRule":
        outcomes = sum(item is not None for item in (self.require, self.forbid, self.allowed_combinations))
        if outcomes != 1:
            raise ValueError("pack rule requires exactly one of require, forbid, or allowed_combinations")
        if isinstance(self.when, list) and not self.when:
            raise ValueError("pack rule when list requires at least one predicate")
        return self

    @model_serializer(mode="wrap")
    def serialize_rule(self, handler: Any) -> dict[str, Any]:
        result = handler(self)
        if self.allowed_combinations is None:
            result.pop("allowed_combinations", None)
        return result

    @property
    def conditions(self) -> list[PackPredicate]:
        if self.when is None:
            return []
        return self.when if isinstance(self.when, list) else [self.when]


_PLAN_RULE_FIELDS = {
    "intake.work_kind", "intake.scope_status", "intake.confidence", "intake.source",
    "brief.question", "brief.purpose", "brief.subject", "brief.scope", "brief.study_type",
    "brief.primary_hypothesis", "protocol.revision", "protocol.analysis_unit",
    "protocol.statistics.applicable", "protocol.statistics.reason", "protocol.statistics.estimand",
    "protocol.statistics.analysis_unit", "protocol.statistics.comparison_groups",
    "protocol.statistics.primary_outcomes", "protocol.statistics.multiple_testing",
    "protocol.statistics.missing_and_exclusions", "protocol.statistics.effect_size_and_interval",
    "protocol.statistics.sensitivity_analyses",
    "notes",
}


class PackApplicability(StrictModel):
    description: str = Field(min_length=1)
    topics_any: list[str] = Field(min_length=1, max_length=24)

    @field_validator("topics_any")
    @classmethod
    def sorted_unique(cls, value: list[str]) -> list[str]:
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("topics_any must contain non-empty topic keys")
        return sorted(set(value))


class DomainRulePack(StrictModel):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    core_contract: Literal["extend_only"]
    applies_when: str | PackApplicability
    sources: list[PackSource] = Field(min_length=1)
    fields: list[PackField] = Field(min_length=1)
    validators: list[PackValidator] = Field(min_length=1)
    reviewer_questions: list[str] = Field(min_length=1)
    fixtures: list[PackFixture] = Field(min_length=1)
    rules: list[PackRule] = Field(min_length=1)

    @model_validator(mode="after")
    def declarative_rules_are_well_formed(self) -> "DomainRulePack":
        if isinstance(self.applies_when, str) and not self.applies_when.strip():
            raise ValueError("domain pack legacy applies_when must not be blank")
        field_names = [field.name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("domain pack field names must be unique")
        known = set(field_names)
        for field in self.fields:
            if field.allowed_values and field.value_type != "string":
                raise ValueError(f"domain pack field {field.name}: allowed_values requires string type")
            if field.minimum is not None and field.value_type != "integer":
                raise ValueError(f"domain pack field {field.name}: minimum requires integer type")
            if field.pattern is not None:
                if field.value_type != "string":
                    raise ValueError(f"domain pack field {field.name}: pattern requires string type")
                try:
                    re.compile(field.pattern)
                except re.error as error:
                    raise ValueError(f"domain pack field {field.name}: invalid pattern: {error}") from error
        for section, rules in (("validator", self.validators), ("rule", self.rules)):
            ids = [rule.id for rule in rules]
            if len(ids) != len(set(ids)):
                raise ValueError(f"domain pack {section} ids must be unique")
        for rule in self.validators:
            unknown = sorted(set(rule.required_fields) - known)
            if unknown:
                raise ValueError(f"domain pack validator {rule.id}: unknown required fields {unknown}")
        for rule in self.rules:
            for predicate in (*rule.conditions, rule.require, rule.forbid):
                if predicate is None:
                    continue
                if predicate.field not in known and predicate.field not in _PLAN_RULE_FIELDS:
                    raise ValueError(f"domain pack rule {rule.id}: unknown rule field {predicate.field!r}")
            if rule.allowed_combinations is not None:
                for name in rule.allowed_combinations.fields:
                    if name not in known and name not in _PLAN_RULE_FIELDS:
                        raise ValueError(f"domain pack rule {rule.id}: unknown combination field {name!r}")
                declared = {field.name: field for field in self.fields}
                # A cell no valid answer can equal is a dead row (PR #366 review): check it like an answer.
                for row in rule.allowed_combinations.rows:
                    for name, value in zip(rule.allowed_combinations.fields, row):
                        field = declared.get(name)
                        if field is None:
                            problem = plan_field_problem(name, value)
                        else:
                            # An omitted answer never equals null and an explicit null fails the type check.
                            problem = ("cannot be null" if value is None else field_value_problem(field, value))
                        if problem:
                            raise ValueError(f"domain pack rule {rule.id}: combination value {value!r} is not "
                                             f"allowed for {name} ({name} {problem})")
        return self

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


class LoadedPack(StrictModel):
    pack: DomainRulePack
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def pack_sha256(pack: DomainRulePack) -> str:
    canonical = json.dumps(pack.model_dump(mode="json", by_alias=True), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_pack(path: Path) -> LoadedPack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    pack = DomainRulePack.model_validate(raw)
    if isinstance(pack.applies_when, PackApplicability):
        from .. import vocab as output_vocab

        loaded = output_vocab.current()
        if loaded is None:
            raise ValueError(f"domain pack {pack.key}: topic vocabulary is unavailable")
        unknown = sorted(set(pack.applies_when.topics_any) - set(loaded.keys("topic")))
        if unknown:
            raise ValueError(f"domain pack {pack.key}: unknown topics_any keys {unknown}")
    return LoadedPack(pack=pack, sha256=pack_sha256(pack))


def load_pack_catalog(directories: list[Path]) -> dict[str, LoadedPack]:
    catalog: dict[str, LoadedPack] = {}
    for directory in directories:
        if not directory.exists():
            raise ValueError(f"research pack directory does not exist: {directory}")
        for path in sorted(directory.glob("*.yaml")):
            loaded = load_pack(path)
            previous = catalog.get(loaded.pack.key)
            if previous and previous.sha256 != loaded.sha256:
                raise ValueError(f"conflicting research pack definitions for {loaded.pack.key}")
            catalog[loaded.pack.key] = loaded
    return catalog


def select_packs(catalog: dict[str, LoadedPack], keys: list[str]) -> dict[str, LoadedPack]:
    missing = [key for key in keys if key not in catalog]
    if missing:
        # A config written for a retired version should say which version replaced it.
        ids = {key.rpartition("@")[0] for key in missing}
        available = sorted(key for key, loaded in catalog.items() if loaded.pack.id in ids)
        hint = f" (available: {', '.join(available)})" if available else ""
        raise ValueError(f"unknown research packs: {missing}{hint}")
    selected = {key: catalog[key] for key in keys}
    validators: dict[str, str] = {}
    rules: dict[str, dict[str, Any]] = {}
    for key, loaded in selected.items():
        for validator in loaded.pack.validators:
            previous = validators.get(validator.id)
            if previous is not None and previous != validator.requirement:
                raise ValueError(f"research packs conflict on validator {validator.id!r} ({key})")
            validators[validator.id] = validator.requirement
        for rule in loaded.pack.rules:
            value = rule.model_dump(mode="json", by_alias=True)
            previous_rule = rules.get(rule.id)
            if previous_rule is not None and previous_rule != value:
                raise ValueError(f"research packs conflict on rule {rule.id!r} ({key})")
            rules[rule.id] = value
    return selected


def _waiver(value: Any) -> bool:
    """A ``{"not_applicable": "<reason>"}`` answer: exactly that key and a non-blank reason."""
    return (isinstance(value, dict) and set(value) == {"not_applicable"}
            and isinstance(value["not_applicable"], str) and bool(value["not_applicable"].strip()))


def assess_pack_applicability(configured: dict[str, LoadedPack], topics: Any, pack_values: Any = None) \
        -> tuple[dict[str, LoadedPack], dict[str, dict[str, Any]], list[str]]:
    """Select packs from normalized topics and return an auditable decision for every configured pack.

    A pack whose ``applies_when`` is a legacy string has no topic condition, so topics cannot decide it: it keeps
    the pre-topic rule and the plan answers it with values or a ``not_applicable`` reason (PR #390 review: forcing
    it on would freeze single-cell rules into a bulk study)."""
    selected_topics = sorted(set(item for item in topics or [] if isinstance(item, str)))
    supplied = pack_values if isinstance(pack_values, dict) else {}
    applied: dict[str, LoadedPack] = {}
    decisions: dict[str, dict[str, Any]] = {}
    conditioned = False
    for key, loaded in configured.items():
        condition = loaded.pack.applies_when
        if isinstance(condition, str):
            waived = _waiver(supplied.get(key))
            if not waived:
                applied[key] = loaded
            decisions[key] = {"applied": not waived, "topics_any": [], "matched_topics": [],
                              "reason": "not_applicable" if waived else "no_topic_condition"}
            continue
        conditioned = True
        expected = list(condition.topics_any)
        matched = sorted(set(expected) & set(selected_topics))
        is_applied = bool(matched)
        if is_applied:
            applied[key] = loaded
        decisions[key] = {"applied": is_applied, "topics_any": expected, "matched_topics": matched,
                          "reason": "topic_match" if matched else
                                    "topics_empty" if not selected_topics else "no_topic_match"}
    warnings = (["topics is empty; topic-conditioned packs were not applied"]
                if conditioned and not selected_topics else [])
    return applied, decisions, warnings


def normalize_pack_keys(configured: dict[str, LoadedPack], pack_values: Any) -> Any:
    """Re-key a value written under a bare pack id to its one configured ``id@version`` key.

    The v0.5 trial CSO wrote ``bulk_tumor_normal`` for ``bulk_tumor_normal@3`` (2026-10-07). Only an id with exactly
    one configured version is re-keyed, and never when the exact key is also present: the version stays the
    snapshot's, not the planner's guess."""
    if not isinstance(pack_values, dict):
        return pack_values
    versions: dict[str, list[str]] = {}
    for key in configured:
        versions.setdefault(key.split("@", 1)[0], []).append(key)
    normalized: dict[str, Any] = {}
    for key, value in pack_values.items():
        exact = versions.get(key) if isinstance(key, str) and "@" not in key else None
        if exact and len(exact) == 1 and exact[0] not in pack_values:
            normalized[exact[0]] = value
        else:
            normalized[key] = value
    return normalized


def select_applied_packs(configured: dict[str, LoadedPack], pack_values: Any, *, topics: Any) \
        -> dict[str, LoadedPack]:
    """Topic-conditioned packs apply by topics alone; a legacy string-condition pack keeps the pre-topic answer
    (values, or one ``not_applicable`` reason)."""
    if not isinstance(pack_values, dict):
        raise ValueError("research plan pack_values must be an object")
    applied, _decisions, _warnings = assess_pack_applicability(configured, topics, pack_values)
    legacy = {key for key, loaded in configured.items() if isinstance(loaded.pack.applies_when, str)}
    unknown = sorted(set(pack_values) - set(applied) - legacy)
    if unknown:
        raise ValueError(f"research plan supplied values for packs that do not apply: {unknown}; "
                         f"the applied pack keys are {sorted(set(applied) | legacy)}")
    missing = sorted((set(applied) | legacy) - set(pack_values))
    if missing:
        raise ValueError(f"research plan pack_values is missing applied packs: {missing}; a pack without a topic "
                         'condition needs values or {"not_applicable": "<reason>"}')
    for key in applied:
        value = pack_values[key]
        if isinstance(value, dict) and "not_applicable" in value:
            raise ValueError(f"research plan pack_values[{key}] requires one non-empty not_applicable reason"
                             if key in legacy else
                             f"research plan pack_values[{key}] cannot be not_applicable after topic selection")
    return applied


def select_legacy_applied_packs(frozen: dict[str, LoadedPack], pack_values: Any) -> dict[str, LoadedPack]:
    """Resume a frozen contract by its CP1 snapshot instead of a fresh applicability decision (PR #390 review).

    The snapshot holds only the packs it applied, so a pack it answered ``not_applicable`` (before topics, or a
    sentence-condition pack after them) is absent; that answer keeps the one-reason form. Every frozen pack still
    needs its values."""
    if not isinstance(pack_values, dict):
        raise ValueError("research plan pack_values must be an object")
    missing = sorted(set(frozen) - set(pack_values))
    if missing:
        raise ValueError(f"research plan pack_values is missing frozen packs: {missing}")
    for key, value in pack_values.items():
        waived = isinstance(value, dict) and "not_applicable" in value
        if key in frozen:
            if waived:
                raise ValueError(f"research plan pack_values[{key}] cannot waive a pack frozen as applied")
            continue
        reason = value.get("not_applicable") if waived else None
        if not waived or set(value) != {"not_applicable"} or not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"research plan supplied values for packs that do not apply: [{key!r}]")
    return dict(frozen)


def configured_packs(settings: Any, keys: list[str] | None = None) -> dict[str, LoadedPack]:
    builtin = Path(__file__).with_name("packs")
    directories = [builtin, *(settings.path(path) for path in settings.research.pack_dirs)]
    return select_packs(load_pack_catalog(directories), settings.research.active_packs if keys is None else keys)


def packs_for_snapshot(settings: Any, snapshot: Any) -> dict[str, LoadedPack]:
    """Load the exact pack versions frozen at CP1 and reject any content drift."""
    if not isinstance(snapshot, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                                 for key, value in snapshot.items()):
        raise ValueError("invalid frozen research pack snapshot")
    selected = configured_packs(settings, sorted(snapshot))
    actual = pack_snapshot(selected)
    if actual != snapshot:
        raise ValueError("frozen research pack snapshot does not match the installed definitions")
    return selected


def check_configured_packs(settings: Any) -> None:
    """Load the configured packs when the gateway starts, so a retired ``id@version`` or a missing pack
    directory stops it with the available versions instead of failing the first research plan (#170)."""
    if settings.research.enabled or settings.research.active_packs or settings.research.pack_dirs:
        configured_packs(settings)


def pack_snapshot(packs: dict[str, LoadedPack]) -> dict[str, str]:
    return {key: loaded.sha256 for key, loaded in sorted(packs.items())}


def pack_refs(packs: dict[str, LoadedPack]) -> list[dict[str, str]]:
    """The `protocol.packs` entries of a PLAN made under this snapshot; labhq writes them, not the CSO (#222)."""
    return [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for _key, loaded in sorted(packs.items())]


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, [], {})}


def render_pack_catalog(packs: dict[str, LoadedPack]) -> str:
    if not packs:
        return "(no domain packs selected)"
    rows = []
    for key, loaded in sorted(packs.items()):
        pack = loaded.pack
        rows.append(json.dumps({"key": key, "sha256": loaded.sha256,
                                "applies_when": (pack.applies_when if isinstance(pack.applies_when, str)
                                                 else pack.applies_when.model_dump(mode="json")),
                                # The exact keys of pack_values[key]: acceptance ids are the rule ids (#222).
                                "pack_values_keys": {"fields": [field.name for field in pack.fields],
                                                     "validators": [v.id for v in pack.validators],
                                                     "acceptance": [rule.id for rule in pack.rules]},
                                "fields": [_compact(field.model_dump(mode="json")) for field in pack.fields],
                                "validators": [v.model_dump(mode="json") for v in pack.validators],
                                "rules": [_compact(rule.model_dump(mode="json", by_alias=True)) for rule in pack.rules],
                                "reviewer_questions": pack.reviewer_questions},
                               ensure_ascii=False))
    return "\n".join(rows)


def render_pack_review(packs: dict[str, LoadedPack]) -> str:
    """What the research reviewer holds the results to: each selected pack's reviewer questions and rules."""
    if not packs:
        return "(no domain packs selected)"
    return "\n".join(json.dumps({"key": key, "reviewer_questions": loaded.pack.reviewer_questions,
                                 "rules": [_compact(rule.model_dump(mode="json", by_alias=True))
                                           for rule in loaded.pack.rules]}, ensure_ascii=False)
                     for key, loaded in sorted(packs.items()))
