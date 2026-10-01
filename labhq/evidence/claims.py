"""Typed claim, evidence and link rows for research results (#90 R04-R07, #58 claim ledger).

The schema keeps three things apart: what is asserted (claim), what was observed or looked up
(evidence), and how one bears on the other (link). Code checks enums, revisions and references;
whether a source really supports a sentence stays a reviewer judgment.
"""

from __future__ import annotations

import datetime as _dt
import posixpath
import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

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

# R07: a recorded quantity states these, or names each one it cannot give with the impact of not knowing.
QUANTITY_REQUIRED = ("value", "unit", "conditions", "denominator")
QUANTITY_UNKNOWABLE = (*QUANTITY_REQUIRED, "method", "uncertainty")

# Which countable link relation a claim status asserts.
STATUS_NEEDS = {"supported": "supports", "partially_supported": "supports", "contradicted": "contradicts"}

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DOI_HOSTS = frozenset({"doi.org", "dx.doi.org"})
_NCBI = "ncbi.nlm.nih.gov"
# Registry pages whose path names exactly one record: (host without www., path pattern, scheme).
_REGISTRY_PATHS: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (host, re.compile(pattern), scheme) for host, pattern, scheme in (
        ("pubmed.ncbi.nlm.nih.gov", r"/(\d+)/?", "pmid"),
        (_NCBI, r"/pubmed/(\d+)/?", "pmid"),
        (_NCBI, r"/pmc/articles/(PMC\d+)/?", "pmcid"),
        ("pmc.ncbi.nlm.nih.gov", r"/articles/(PMC\d+)/?", "pmcid"),
        (_NCBI, r"/bioproject/([^/]+)/?", "bioproject"),
        (_NCBI, r"/biosample/([^/]+)/?", "biosample"),
        (_NCBI, r"/sra/([^/]+)/?", "sra"),
        (_NCBI, r"/snp/([^/]+)/?", "dbsnp"),
        (_NCBI, r"/clinvar/variation/(\d+)/?", "clinvar"),
        ("uniprot.org", r"/(?:uniprot|uniprotkb)/([^/]+?)(?:/entry)?/?", "uniprot"),
        ("rest.uniprot.org", r"/uniprotkb/([^/.]+)(?:\.[a-z]+)?", "uniprot"),
        ("rcsb.org", r"/structure/([^/]+)/?", "pdb"),
        ("ebi.ac.uk", r"/chembl/(?:compound_report_card|target_report_card|explore/compound|explore/target)"
                      r"/([^/]+)/?", "chembl"),
        ("ensembl.org", r"/id/([^/]+)/?", "ensembl"),
    ))
# identifiers.org prefixes -> labhq schemes (https://identifiers.org/<prefix>:<id> or /<prefix>/<id>).
_IDENTIFIERS_ORG = {"doi": "doi", "pubmed": "pmid", "pmc": "pmcid", "geo": "geo", "bioproject": "bioproject",
                    "biosample": "biosample", "insdc.sra": "sra", "refseq": "refseq", "ensembl": "ensembl",
                    "uniprot": "uniprot", "dbsnp": "dbsnp", "clinvar": "clinvar", "pdb": "pdb",
                    "chembl.compound": "chembl", "chembl.target": "chembl", "hgnc": "hgnc"}


def normalize_uri(uri: str) -> str:
    """Fold only what is case-insensitive in a URI (scheme and host); path and query keep their case."""
    uri = uri.strip()
    parts = urlsplit(uri)
    if not parts.scheme or not parts.netloc:
        return uri
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))


def normalize_id(scheme: str, value: str) -> str:
    """Comparison form of an identifier. Registry accessions are case-insensitive; URIs are not."""
    return normalize_uri(value) if scheme == "uri" else value.strip().casefold()


def registry_id(uri: str) -> tuple[str, str] | None:
    """The (scheme, value) a major registry URL names, or None for any other URL, search page or listing.

    doi.org, identifiers.org, PubMed, PMC, GEO, NCBI BioProject/BioSample/SRA/dbSNP/ClinVar, UniProt, RCSB
    PDB, ChEMBL and Ensembl. The value is not format-checked here; a malformed one is reported by the
    verifier, never corrected.
    """
    try:
        parts = urlsplit(uri.strip())
        host = (parts.hostname or "").removeprefix("www.")
    except ValueError:  # e.g. an unbalanced IPv6 bracket: not a registry address
        return None
    if parts.scheme.lower() not in {"http", "https"} or not host:
        return None
    path = unquote(parts.path)
    found: tuple[str, str] | None = None
    if host in _DOI_HOSTS:
        found = ("doi", path.strip("/")) if path.strip("/") else None
    elif host == "identifiers.org":
        match = re.fullmatch(r"/([A-Za-z][A-Za-z0-9._]*)[:/](.+?)/?", path)
        scheme = _IDENTIFIERS_ORG.get(match[1].casefold()) if match else None
        if match and scheme:
            value = match[2]
            found = (scheme, f"HGNC:{value}" if scheme == "hgnc" and value.isdigit() else value)
    elif host == _NCBI and path.rstrip("/") == "/geo/query/acc.cgi":
        accession = (parse_qs(parts.query).get("acc") or [""])[0].strip()
        found = ("geo", accession) if accession else None
    else:
        for registry_host, pattern, scheme in _REGISTRY_PATHS:
            match = pattern.fullmatch(path) if host == registry_host else None
            if match:
                found = (scheme, match[1])
                break
    return (found[0], found[1].strip()) if found and found[1].strip() else None


def normalize_artifact_path(path: str) -> str:
    """One spelling per workspace path: forward slashes, no leading ./ or redundant segments."""
    cleaned = path.strip().replace("\\", "/")
    return posixpath.normpath(cleaned) if cleaned else cleaned


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
            # fromisoformat alone is not enough: 3.11+ also reads week dates such as 2026-W40-4.
            try:
                if not _ISO_DATE.fullmatch(self.accessed_at):
                    raise ValueError
                _dt.date.fromisoformat(self.accessed_at)
            except ValueError:
                raise ValueError("source accessed_at must be a YYYY-MM-DD date") from None
        return self

    @property
    def external(self) -> bool:
        return _present(self.uri) or _present(self.id_value)

    def identities(self, artifact_paths: Mapping[str, str] | None = None) -> list[str]:
        """Every key under which this source is 'the same source', used to detect re-citation.

        A row may name one dataset several ways (identifier, registry URL, downloaded artifact), and two
        artifact ids may point at one file, so each spelling gets its own key. A registry URL also gets the
        key of the identifier it names (``registry_id``).
        """
        keys: list[str] = []
        if self.id_scheme and _present(self.id_value):
            keys.append(f"{self.id_scheme}:{normalize_id(self.id_scheme, self.id_value or '')}")
        if _present(self.uri):
            uri = normalize_uri(self.uri or "")
            named = registry_id(uri)
            if named:
                keys.append(f"{named[0]}:{normalize_id(*named)}")
            keys.append(f"uri:{uri}")
        if _present(self.artifact_id):
            path = (artifact_paths or {}).get(self.artifact_id or "")
            keys.append(f"artifact:{normalize_artifact_path(path)}" if path else f"artifact_id:{self.artifact_id}")
        return list(dict.fromkeys(keys))


class Quantity(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    measure: str = Field(min_length=1)  # what was measured on what subject
    value: float | int | str | None = None  # a number or the reported form, e.g. "<0.001"
    unit: str | None = None  # "dimensionless" for ratios; never implied
    conditions: list[str] = []  # assay, population, timepoint, threshold...
    denominator: str | None = None  # the base of the value: "6 vs 6 donors", "312 of 4,500 cells"
    method: str | None = None
    uncertainty: str | None = None  # CI, SE, range
    unknown: dict[str, str] = {}  # field -> how not knowing it limits the conclusion

    @field_validator("value", mode="before")
    @classmethod
    def not_a_flag(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("quantity value must be a number or the reported string")
        return value

    @model_validator(mode="after")
    def stated_or_declared_unknown(self) -> "Quantity":
        invalid = sorted(set(self.unknown) - set(QUANTITY_UNKNOWABLE))
        if invalid:
            raise ValueError(f"quantity {self.id} unknown may only name {', '.join(QUANTITY_UNKNOWABLE)}")
        for name, impact in self.unknown.items():
            if not _present(impact):
                raise ValueError(f"quantity {self.id} unknown {name} needs its impact on the conclusion")
            if self.given(name):
                raise ValueError(f"quantity {self.id} gives {name} and also marks it unknown")
        missing = [name for name in QUANTITY_REQUIRED if not self.given(name) and name not in self.unknown]
        if missing:
            raise ValueError(f"quantity {self.id} needs {', '.join(missing)}, or an unknown entry with its impact")
        return self

    def given(self, name: str) -> bool:
        value = getattr(self, name)
        if isinstance(value, list):
            return bool(value) and all(_present(item) for item in value)
        return _present(value)


class Comparison(StrictModel):
    quantity_ids: list[str] = Field(min_length=2)
    comparability: Literal["comparable", "comparable_with_assumptions", "not_comparable"]
    assumptions: list[str] = []  # what must hold to pool or rank values measured differently
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def assumptions_stated(self) -> "Comparison":
        if len(set(self.quantity_ids)) != len(self.quantity_ids):
            raise ValueError("comparison quantity_ids must be distinct")
        if self.comparability == "comparable_with_assumptions" and not any(_present(a) for a in self.assumptions):
            raise ValueError("comparable_with_assumptions must list the assumptions")
        return self


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
    comparisons: list[Comparison] = []

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
    quantities: list[Quantity] = []
    # R05 assessment: fit to the question comes first, then these. Counts never add up to a grade.
    directness: Directness | None = None
    source_level: SourceLevel | None = None
    independence_group: str | None = Field(default=None, pattern=ID_PATTERN)  # same data -> same group
    assessment_reason: str | None = None
    # Evidence slots of the plan step this row fills. A failed or empty attempt still fills its slot, so
    # the gap shows as tried rather than silently absent.
    slots: list[str] = []

    @model_validator(mode="after")
    def kind_shape(self) -> "Evidence":
        if self.slots and not self.countable:
            raise ValueError(f"{self.kind} row {self.id} is not evidence and cannot fill evidence slots {self.slots}")
        for slot in self.slots:
            if not _present(slot):
                raise ValueError(f"evidence {self.id} lists an empty slot id")
            if self.slots.count(slot) > 1:
                raise ValueError(f"evidence {self.id} lists slot {slot} more than once")
        # R06 holds for every row kind: an inference that read an external page keeps the day it read it.
        if self.source and self.source.external and not _present(self.source.accessed_at):
            raise ValueError(f"evidence {self.id} cites an external source without accessed_at")
        if self.countable:
            if self.status is None or self.source is None:
                raise ValueError(f"evidence {self.id} ({self.kind}) needs status and source")
            missing = [name for name in ("directness", "source_level", "independence_group", "assessment_reason")
                       if not _present(getattr(self, name))]
            if missing:
                raise ValueError(f"evidence {self.id} ({self.kind}) must state {', '.join(missing)}")
            source = self.source
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
    def cites_source(self) -> bool:
        """Whether the row relies on its source, so the source must resolve.

        A failed or unavailable retrieval never reached its source. A finished search with no hits did: the
        place it searched is cited and must exist, while what it looked for belongs in ``source.query``.
        """
        return self.source is not None and not (self.countable and self.status in {"failed", "unavailable"})

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
                  artifact_paths: Mapping[str, str] | None = None) -> list[str]:
    """Every reference, revision and countability defect in one result, so one correction can fix all.

    ``artifact_paths`` maps each artifact id in the result's ``artifact_refs`` to its workspace path.
    """
    artifact_paths = artifact_paths or {}
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
        if row.source and row.source.artifact_id and row.source.artifact_id not in artifact_paths:
            errors.append(f"evidence {row.id} cites artifact {row.source.artifact_id} missing from artifact_refs")
    errors += _grounding_errors(evidence, evidence_by_id)

    # Re-citing one dataset is not independent support: one source, one independence group, however the
    # rows spell that source.
    groups_by_source: dict[str, tuple[str, str]] = {}
    for row in evidence:
        if not (row.countable and row.source and row.independence_group):
            continue
        for identity in row.source.identities(artifact_paths):
            first = groups_by_source.setdefault(identity, (row.id, row.independence_group))
            if first[1] != row.independence_group:
                errors.append(f"evidence {first[0]} and {row.id} cite the same source {identity} but declare "
                              f"independence groups {first[1]} and {row.independence_group}; re-citation is not "
                              "independent")
                break

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

    errors += _comparison_errors(claims, evidence, links)
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


def _grounding_errors(evidence: list[Evidence], evidence_by_id: dict[str, Evidence]) -> list[str]:
    """An inference or hypothesis row must reach a recorded retrieval through derived_from, not a loop."""
    errors: list[str] = []
    for row in evidence:
        if row.countable or not all(ref in evidence_by_id for ref in row.derived_from):
            continue  # unknown refs are already reported
        seen: set[str] = set()
        pending = list(row.derived_from)
        grounded = False
        while pending and not grounded:
            ref = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            parent = evidence_by_id[ref]
            grounded = parent.countable
            pending.extend(r for r in parent.derived_from if r in evidence_by_id)
        if not grounded:
            errors.append(f"evidence {row.id} ({row.kind}) does not trace back to any observation or lookup "
                          "through derived_from")
    return errors


def _comparison_errors(claims: list[Claim], evidence: list[Evidence], links: list[EvidenceLink]) -> list[str]:
    errors: list[str] = []
    owner: dict[str, str] = {}
    quantity: dict[str, Quantity] = {}
    for row in evidence:
        for item in row.quantities:
            if item.id in quantity:
                errors.append(f"duplicate quantity id {item.id}")
            owner[item.id], quantity[item.id] = row.id, item
    linked = {(link.claim_id, link.evidence_id) for link in links}
    for claim in claims:
        for comparison in claim.comparisons:
            unknown_ids = [qid for qid in comparison.quantity_ids if qid not in quantity]
            if unknown_ids:
                errors.append(f"claim {claim.key} compares unknown quantities {unknown_ids}")
                continue
            unlinked = [qid for qid in comparison.quantity_ids if (claim.id, owner[qid]) not in linked]
            if unlinked:
                errors.append(f"claim {claim.key} compares {unlinked} from evidence it does not link")
            if comparison.comparability != "comparable":
                continue
            items = [quantity[qid] for qid in comparison.quantity_ids]
            gaps = sorted({name for item in items for name in QUANTITY_REQUIRED if name in item.unknown})
            units = {(item.unit or "").strip().casefold() for item in items}
            conditions = {frozenset(c.strip().casefold() for c in item.conditions) for item in items}
            # A different assay or model is a different measurement even when unit and conditions match.
            methods = {(item.method or "").strip().casefold() for item in items if _present(item.method)}
            differs = [name for name, values in (("unit", units), ("conditions", conditions), ("method", methods))
                       if len(values) > 1]
            if gaps or differs:
                problem = (f"unknown {', '.join(gaps)}" if gaps else f"different {' and '.join(differs)}")
                errors.append(f"claim {claim.key} calls {comparison.quantity_ids} comparable with {problem}; "
                              "mark not_comparable or comparable_with_assumptions")
    return errors


def independent_groups(claim_id: str, evidence: list[Evidence], links: list[EvidenceLink],
                       relation: str = "supports") -> list[str]:
    """Distinct independence groups among observed countable rows linked to a claim with ``relation``."""
    by_id = {row.id: row for row in evidence}
    groups = {by_id[link.evidence_id].independence_group for link in links
              if link.claim_id == claim_id and link.relation == relation
              and link.evidence_id in by_id and by_id[link.evidence_id].counts}
    return sorted(group for group in groups if group)
