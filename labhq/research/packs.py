from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class PackAcceptance(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    requirement: str = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)


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
    acceptance: list[PackAcceptance] = Field(min_length=1)

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
        for section, rules in (("validator", self.validators), ("acceptance", self.acceptance)):
            ids = [rule.id for rule in rules]
            if len(ids) != len(set(ids)):
                raise ValueError(f"domain pack {section} ids must be unique")
            for rule in rules:
                unknown = sorted(set(rule.required_fields) - known)
                if unknown:
                    raise ValueError(f"domain pack {section} {rule.id}: unknown required fields {unknown}")
        return self

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


class LoadedPack(StrictModel):
    pack: DomainRulePack
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def pack_sha256(pack: DomainRulePack) -> str:
    canonical = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
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
        raise ValueError(f"unknown research packs: {missing}")
    selected = {key: catalog[key] for key in keys}
    validators: dict[str, str] = {}
    for key, loaded in selected.items():
        for validator in loaded.pack.validators:
            previous = validators.get(validator.id)
            if previous is not None and previous != validator.requirement:
                raise ValueError(f"research packs conflict on validator {validator.id!r} ({key})")
            validators[validator.id] = validator.requirement
    return selected


def configured_packs(settings: Any) -> dict[str, LoadedPack]:
    builtin = Path(__file__).with_name("packs")
    directories = [builtin, *(settings.path(path) for path in settings.research.pack_dirs)]
    return select_packs(load_pack_catalog(directories), settings.research.active_packs)


def pack_snapshot(packs: dict[str, LoadedPack]) -> dict[str, str]:
    return {key: loaded.sha256 for key, loaded in sorted(packs.items())}


def render_pack_catalog(packs: dict[str, LoadedPack]) -> str:
    if not packs:
        return "(no domain packs selected)"
    rows = []
    for key, loaded in sorted(packs.items()):
        pack = loaded.pack
        rows.append(json.dumps({"key": key, "sha256": loaded.sha256,
                                "applies_when": pack.applies_when,
                                "fields": [field.model_dump(mode="json") for field in pack.fields],
                                "validators": [v.model_dump(mode="json") for v in pack.validators],
                                "reviewer_questions": pack.reviewer_questions,
                                "acceptance": [a.model_dump(mode="json") for a in pack.acceptance]},
                               ensure_ascii=False, sort_keys=True))
    return "\n".join(rows)
