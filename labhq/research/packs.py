from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

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

    @model_validator(mode="after")
    def exactly_one_operator(self) -> "PackPredicate":
        operators = {"value", "in_", "not_in"} & self.model_fields_set
        if len(operators) != 1:
            raise ValueError("pack predicate requires exactly one operator: value, in, or not_in")
        return self

    @model_serializer(mode="plain")
    def serialize_predicate(self) -> dict[str, Any]:
        result = {"field": self.field}
        if "value" in self.model_fields_set:
            result["value"] = self.value
        elif "in_" in self.model_fields_set:
            result["in"] = self.in_
        else:
            result["not_in"] = self.not_in
        return result


class PackRule(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    description: str = Field(min_length=1)
    # One predicate, or a list of predicates that must all hold (e.g. model x likelihood family).
    when: PackPredicate | list[PackPredicate] | None = None
    require: PackPredicate | None = None
    forbid: PackPredicate | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> "PackRule":
        if (self.require is None) == (self.forbid is None):
            raise ValueError("pack rule requires exactly one of require or forbid")
        if isinstance(self.when, list) and not self.when:
            raise ValueError("pack rule when list requires at least one predicate")
        return self

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
    "protocol.statistics.analysis_unit", "protocol.statistics.multiple_testing",
    "protocol.statistics.missing_and_exclusions", "protocol.statistics.effect_size_and_interval",
    "notes",
}


class DomainRulePack(StrictModel):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    core_contract: Literal["extend_only"]
    applies_when: str = Field(min_length=1)
    sources: list[PackSource] = Field(min_length=1)
    fields: list[PackField] = Field(min_length=1)
    validators: list[PackValidator] = Field(min_length=1)
    reviewer_questions: list[str] = Field(min_length=1)
    fixtures: list[PackFixture] = Field(min_length=1)
    rules: list[PackRule] = Field(min_length=1)

    @model_validator(mode="after")
    def declarative_rules_are_well_formed(self) -> "DomainRulePack":
        field_names = [field.name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("domain pack field names must be unique")
        known = set(field_names)
        for field in self.fields:
            if field.allowed_values and field.value_type != "string":
                raise ValueError(f"domain pack field {field.name}: allowed_values requires string type")
            if field.minimum is not None and field.value_type != "integer":
                raise ValueError(f"domain pack field {field.name}: minimum requires integer type")
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


def configured_packs(settings: Any) -> dict[str, LoadedPack]:
    builtin = Path(__file__).with_name("packs")
    directories = [builtin, *(settings.path(path) for path in settings.research.pack_dirs)]
    return select_packs(load_pack_catalog(directories), settings.research.active_packs)


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
                                "applies_when": pack.applies_when,
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
