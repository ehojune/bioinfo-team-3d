"""Typed claim, evidence and link rows for research results (#90 R04-R06, #58 claim ledger).

The schema keeps three things apart: what is asserted (claim), what was observed or looked up
(evidence), and how one bears on the other (link). Code checks enums, revisions and references;
whether a source really supports a sentence stays a reviewer judgment.
"""

from __future__ import annotations

import datetime as _dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$"

ClaimKind = Literal["finding", "inference", "hypothesis"]
ClaimStatus = Literal["proposed", "supported", "partially_supported", "contradicted", "unresolved", "withdrawn"]
# inco's six evidence types. Inference and hypothesis rows may be recorded as context but are never
# counted as evidence: they cannot support or contradict a claim.
EvidenceKind = Literal["observation", "database_annotation", "experimental", "literature_claim",
                       "inference", "hypothesis"]
COUNTABLE_EVIDENCE_KINDS = frozenset({"observation", "database_annotation", "experimental", "literature_claim"})
EvidenceStatus = Literal["observed", "unavailable", "not_found", "failed"]
Relation = Literal["supports", "contradicts", "context"]
Directness = Literal["direct", "indirect"]
SourceLevel = Literal["primary", "secondary", "tertiary"]

# Which countable link relation a claim status asserts.
STATUS_NEEDS = {"supported": "supports", "partially_supported": "supports", "contradicted": "contradicts"}


class SourceRef(StrictModel):
    uri: str | None = None
    id_scheme: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    id_value: str | None = None
    artifact_id: str | None = None
    version: str | None = None  # database release / record version, when the source has one
    accessed_at: str | None = None  # YYYY-MM-DD the external source was read
    locator: str | None = None  # where in the source: section, table, row, line, JSON pointer
    query: str | None = None  # search scope and filters, required for a zero-result search

    @model_validator(mode="after")
    def identifies_something(self) -> "SourceRef":
        if (self.id_scheme is None) != (self.id_value is None or not self.id_value.strip()):
            raise ValueError("source id_scheme and id_value must be given together")
        if not any(_present(value) for value in (self.uri, self.id_value, self.artifact_id)):
            raise ValueError("source needs a uri, an id_scheme/id_value pair, or an artifact_id")
        if self.accessed_at is not None:
            try:
                _dt.date.fromisoformat(self.accessed_at)
            except ValueError:
                raise ValueError("source accessed_at must be a YYYY-MM-DD date") from None
            if len(self.accessed_at) != 10:
                raise ValueError("source accessed_at must be a YYYY-MM-DD date")
        return self

    @property
    def external(self) -> bool:
        return _present(self.uri) or _present(self.id_value)

    def identity(self) -> str:
        """Stable key for 'the same source', used to detect re-citation of one dataset."""
        if self.id_scheme and self.id_value:
            return f"{self.id_scheme}:{self.id_value.strip().casefold()}"
        if self.artifact_id:
            return f"artifact:{self.artifact_id}"
        return f"uri:{(self.uri or '').strip().casefold()}"


class Claim(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    revision: int = Field(ge=1)
    statement: str = Field(min_length=1, max_length=600)
    scope: str = Field(min_length=1)
    kind: ClaimKind
    status: ClaimStatus
    importance: Literal["major", "minor"]
    status_reason: str = Field(min_length=1)  # why the evidence earns this status, not a probability
    limitations: list[str] = []
    supersedes: str | None = None  # "<claim id>@<earlier revision>"

    @field_validator("statement")
    @classmethod
    def one_statement(cls, value: str) -> str:
        value = value.strip()
        if not value or "\n" in value:
            raise ValueError("claim statement must be one non-empty line asserting one thing")
        return value

    @model_validator(mode="after")
    def revision_chain(self) -> "Claim":
        if self.revision > 1 and not self.supersedes:
            raise ValueError(f"claim {self.id} revision {self.revision} must name the revision it supersedes")
        if self.supersedes:
            claim_id, _, revision = self.supersedes.rpartition("@")
            if claim_id != self.id or not revision.isdigit() or not 1 <= int(revision) < self.revision:
                raise ValueError(f"claim {self.id}: supersedes must be {self.id}@<revision below {self.revision}>")
        return self

    @property
    def key(self) -> str:
        return f"{self.id}@{self.revision}"


class Evidence(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    kind: EvidenceKind
    observation: str = Field(min_length=1)
    status: EvidenceStatus | None = None  # retrieval outcome; countable kinds only
    status_detail: str | None = None  # what was unavailable or failed; never a guess at the answer
    source: SourceRef | None = None
    derived_from: list[str] = []  # evidence ids an inference/hypothesis row reasons from
    method: str | None = None
    conditions: list[str] = []
    # R05 assessment: fit to the question comes first, then these. Counts never add up to a grade.
    directness: Directness | None = None
    source_level: SourceLevel | None = None
    independence_group: str | None = Field(default=None, pattern=ID_PATTERN)  # same data -> same group
    assessment_reason: str | None = None

    @model_validator(mode="after")
    def kind_shape(self) -> "Evidence":
        if self.countable:
            if self.status is None or self.source is None:
                raise ValueError(f"evidence {self.id} ({self.kind}) needs status and source")
            missing = [name for name in ("directness", "source_level", "independence_group", "assessment_reason")
                       if not _present(getattr(self, name))]
            if missing:
                raise ValueError(f"evidence {self.id} ({self.kind}) must state {', '.join(missing)}")
            source = self.source
            if source.external and not _present(source.accessed_at):
                raise ValueError(f"evidence {self.id} cites an external source without accessed_at")
            if self.status == "observed" and not _present(source.locator):
                raise ValueError(f"evidence {self.id} is observed but its source has no locator")
            if self.status == "not_found" and not _present(source.query):
                raise ValueError(f"evidence {self.id} is not_found; record the searched scope and filters in "
                                 "source.query")
            if self.status in {"failed", "unavailable"} and not _present(self.status_detail):
                raise ValueError(f"evidence {self.id} is {self.status}; say what failed in status_detail")
        else:
            if self.status is not None:
                raise ValueError(f"evidence {self.id} ({self.kind}) is not a retrieval; leave status empty")
            if not self.derived_from:
                raise ValueError(f"evidence {self.id} ({self.kind}) must list the evidence it is derived_from")
        return self

    @property
    def countable(self) -> bool:
        return self.kind in COUNTABLE_EVIDENCE_KINDS

    @property
    def counts(self) -> bool:
        """Whether this row may support or contradict a claim at all."""
        return self.countable and self.status == "observed"


class EvidenceLink(StrictModel):
    claim_id: str = Field(pattern=ID_PATTERN)
    claim_revision: int = Field(ge=1)
    evidence_id: str = Field(pattern=ID_PATTERN)
    relation: Relation
    rationale: str = Field(min_length=1)  # why this evidence bears on the claim this way


def _present(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def ledger_errors(claims: list[Claim], evidence: list[Evidence], links: list[EvidenceLink], *,
                  artifact_ids: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Every reference, revision and countability defect in one result, so one correction can fix all."""
    errors: list[str] = []
    claim_by_id: dict[str, Claim] = {}
    for claim in claims:
        if claim.id in claim_by_id:
            errors.append(f"duplicate claim id {claim.id}")
        claim_by_id[claim.id] = claim
    evidence_by_id: dict[str, Evidence] = {}
    for row in evidence:
        if row.id in evidence_by_id:
            errors.append(f"duplicate evidence id {row.id}")
        evidence_by_id[row.id] = row

    for row in evidence:
        for ref in row.derived_from:
            if ref == row.id:
                errors.append(f"evidence {row.id} cannot be derived_from itself")
            elif ref not in evidence_by_id:
                errors.append(f"evidence {row.id} derived_from unknown evidence {ref}")
        if row.source and row.source.artifact_id and row.source.artifact_id not in artifact_ids:
            errors.append(f"evidence {row.id} cites artifact {row.source.artifact_id} missing from artifact_refs")

    # Re-citing one dataset is not independent support: one source identity, one independence group.
    groups_by_source: dict[str, tuple[str, str]] = {}
    for row in evidence:
        if not (row.countable and row.source and row.independence_group):
            continue
        identity = row.source.identity()
        first = groups_by_source.setdefault(identity, (row.id, row.independence_group))
        if first[1] != row.independence_group:
            errors.append(f"evidence {first[0]} and {row.id} cite the same source {identity} but declare "
                          f"independence groups {first[1]} and {row.independence_group}; re-citation is not "
                          "independent")

    seen_pairs: set[tuple[str, str]] = set()
    asserted: dict[str, set[str]] = {}
    for link in links:
        claim = claim_by_id.get(link.claim_id)
        row = evidence_by_id.get(link.evidence_id)
        if claim is None:
            errors.append(f"link to unknown claim {link.claim_id}")
        elif link.claim_revision != claim.revision:
            errors.append(f"link {link.claim_id}@{link.claim_revision}->{link.evidence_id} targets a stale "
                          f"revision; current is {claim.key}")
        if row is None:
            errors.append(f"link {link.claim_id}->{link.evidence_id} cites unknown evidence")
        pair = (link.claim_id, link.evidence_id)
        if pair in seen_pairs:
            errors.append(f"evidence {link.evidence_id} is linked to claim {link.claim_id} more than once")
        seen_pairs.add(pair)
        if row is None or link.relation == "context":
            continue
        if not row.countable:
            errors.append(f"{row.kind} row {row.id} is not evidence and cannot {link.relation[:-1]} a claim; "
                          "link it as context or record it as a claim")
        elif row.status != "observed":
            errors.append(f"evidence {row.id} is {row.status}; a failed or empty lookup is neither evidence "
                          f"nor proof of absence and cannot {link.relation[:-1]} a claim")
        else:
            asserted.setdefault(link.claim_id, set()).add(link.relation)

    for link in links:
        if not link.rationale.strip():
            errors.append(f"link {link.claim_id}->{link.evidence_id} needs a rationale")
    for claim in claims:
        if not claim.status_reason.strip():
            errors.append(f"claim {claim.key} needs a status_reason")
        needed = STATUS_NEEDS.get(claim.status)
        relations = asserted.get(claim.id, set())
        if needed and needed not in relations:
            errors.append(f"claim {claim.key} is {claim.status} but no observed countable evidence {needed} it")
        if claim.status == "supported" and "contradicts" in relations:
            errors.append(f"claim {claim.key} has contradicting evidence; use partially_supported or contradicted")
    return errors


def independent_groups(claim_id: str, evidence: list[Evidence], links: list[EvidenceLink],
                       relation: str = "supports") -> list[str]:
    """Distinct independence groups among observed countable rows linked to a claim with ``relation``."""
    by_id = {row.id: row for row in evidence}
    groups = {by_id[link.evidence_id].independence_group for link in links
              if link.claim_id == claim_id and link.relation == relation
              and link.evidence_id in by_id and by_id[link.evidence_id].counts}
    return sorted(group for group in groups if group)
