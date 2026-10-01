"""Source resolution and evidence verification for research results (#90 R06, #58 items 3-4).

Two questions stay separate. Did the lookup run (``lookup``: succeeded / failed / skipped)? And,
only when it ran, what did the authority say about the identifier (``status``)? A network error,
timeout, or disabled lookup is ``requires_verification`` and never ``not_found``: a failed lookup
is neither evidence nor proof of absence. Resolving an ID also says nothing about whether the
source supports the sentence; that stays with the reviewer (``support_review``).

Live network resolvers are not part of this PR. ``resolver=None`` means live lookup is off.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .claims import STATUS_NEEDS, SourceRef

if TYPE_CHECKING:
    from ..research.contract import ResearchResult

LookupState = Literal["succeeded", "failed", "skipped"]
IdStatus = Literal["found", "not_found", "insufficient", "conflicting", "requires_verification"]
FAILURE_KINDS = frozenset({"network", "timeout", "rate_limited", "auth", "server", "invalid_response",
                           "resolver_error"})
SKIP_KINDS = frozenset({"disabled", "unsupported_scheme", "malformed_id", "manifest_unavailable"})

# Format checks run before any lookup so a malformed ID is reported, not guessed at or "corrected".
ID_FORMATS: dict[str, str] = {
    "doi": r"10\.\d{4,9}/\S+",
    "pmid": r"[1-9]\d{0,8}",
    "pmcid": r"PMC\d+",
    "geo": r"G(?:SE|SM|PL|DS)\d+",
    "sra": r"[SED]R[APRSX]\d{6,}",
    "bioproject": r"PRJ[DEN][A-Z]\d+",
    "biosample": r"SAM[DEN][A-Z]?\d+",
    "refseq": r"[A-Z]{2}_(?:[A-Z]{2,6})?\d{6,}(?:\.\d+)?",  # NM_004985.5, WP_000000001.1, NZ_CP012345.1
    "ensembl": r"ENS[A-Z]*[EGPTR]\d{11}(?:\.\d+)?",
    "uniprot": r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-\d+)?",
    "dbsnp": r"rs[1-9]\d*",
    "clinvar": r"(?:[RSV]CV\d{9}(?:\.\d+)?|[1-9]\d*)",
    "pdb": r"[1-9][A-Za-z0-9]{3}",
    "chembl": r"CHEMBL\d+",
    "hgnc": r"HGNC:\d+",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRecord(StrictModel):
    id_scheme: str
    id_value: str
    version: str | None = None
    title: str | None = None
    url: str | None = None


class Resolution(StrictModel):
    id_scheme: str
    id_value: str
    lookup: LookupState
    status: IdStatus
    resolver: str
    error_kind: str | None = None
    detail: str = ""
    record: SourceRecord | None = None
    candidates: list[SourceRecord] = []
    checked_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def lookup_and_status_agree(self) -> "Resolution":
        if self.lookup == "succeeded":
            if self.status not in {"found", "not_found", "conflicting"} or self.error_kind:
                raise ValueError("a completed lookup reports found, not_found, or conflicting without an error")
            if self.status == "found" and self.record is None:
                raise ValueError("found needs the matching record")
            if self.status == "not_found" and (self.record or self.candidates):
                raise ValueError("not_found cannot carry records")
            if self.status == "conflicting" and not self.candidates:
                raise ValueError("conflicting needs the disagreeing candidates")
        elif self.lookup == "failed":
            if self.status != "requires_verification" or self.error_kind not in FAILURE_KINDS:
                raise ValueError("a failed lookup is requires_verification with a failure kind, never not_found")
        elif self.status not in {"insufficient", "requires_verification"} or self.error_kind not in SKIP_KINDS:
            raise ValueError("a skipped lookup is insufficient or requires_verification with a skip reason")
        return self


class LookupFailed(Exception):
    """Raised by a resolver when the lookup itself did not complete."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind if kind in FAILURE_KINDS else "resolver_error"
        self.detail = detail


class SourceResolver(Protocol):
    """Looks an identifier up at its authority. Raise ``LookupFailed`` when the lookup did not complete;
    return ``[]`` only when the authority answered that no such record exists."""

    name: str

    def supports(self, scheme: str) -> bool: ...

    async def lookup(self, scheme: str, value: str) -> list[SourceRecord]: ...


class StaticResolver:
    """Offline resolver over fixed authority answers, for tests and bench fixtures. Never touches the network."""

    def __init__(self, records: Mapping[tuple[str, str], Iterable[SourceRecord | dict]] | None = None, *,
                 failures: Mapping[tuple[str, str], str] | None = None, schemes: Iterable[str] | None = None,
                 name: str = "static") -> None:
        self.name = name
        self.records = {(scheme, value.casefold()): [SourceRecord.model_validate(r) for r in rows]
                        for (scheme, value), rows in (records or {}).items()}
        self.failures = {(scheme, value.casefold()): kind for (scheme, value), kind in (failures or {}).items()}
        self.schemes = set(schemes) if schemes is not None else {key[0] for key in [*self.records, *self.failures]}
        self.calls: list[tuple[str, str]] = []

    def supports(self, scheme: str) -> bool:
        return scheme in self.schemes

    async def lookup(self, scheme: str, value: str) -> list[SourceRecord]:
        key = (scheme, value.casefold())
        self.calls.append(key)
        if key in self.failures:
            raise LookupFailed(self.failures[key], f"fixture {self.failures[key]} for {scheme}:{value}")
        return list(self.records.get(key, []))


class EvidenceCheck(StrictModel):
    evidence_id: str
    source_location: str | None  # where in the source the observation sits
    resolution: Resolution


class ClaimCheck(StrictModel):
    claim: str  # "<id>@<revision>"
    declared_status: str
    state: Literal["verified", "unverified", "defective", "not_asserted"]
    verified_evidence: list[str]
    unverified_evidence: list[str]
    defects: list[str]
    independent_groups: list[str]
    support_review: Literal["reviewer_required"] = "reviewer_required"


class VerificationReport(StrictModel):
    plan_sha256: str
    step_id: str
    resolver: str
    evidence: list[EvidenceCheck]
    claims: list[ClaimCheck]
    lookup_failures: list[str]  # evidence ids whose lookup did not complete; not absence
    ok: bool


def _skipped(scheme: str, value: str, resolver: str, status: IdStatus, kind: str, detail: str) -> Resolution:
    return Resolution(id_scheme=scheme, id_value=value, lookup="skipped", status=status, resolver=resolver,
                      error_kind=kind, detail=detail)


def _judge(scheme: str, value: str, version: str | None, records: list[SourceRecord], resolver: str) -> Resolution:
    base = {"id_scheme": scheme, "id_value": value, "lookup": "succeeded", "resolver": resolver}
    if not records:
        return Resolution(**base, status="not_found", detail="the authority has no record for this identifier")
    if any(r.id_scheme != scheme for r in records):
        # Numeric IDs collide across databases (PMID 12345 vs ClinVar 12345).
        return Resolution(**base, status="conflicting", candidates=records,
                          detail="the authority returned a record from a different scheme")
    other = [r for r in records if r.id_value.casefold() != value.casefold()]
    if other:
        return Resolution(**base, status="conflicting", candidates=records,
                          detail="the authority returned a different identifier")
    matching = [r for r in records if version is None or r.version == version]
    if not matching:
        return Resolution(**base, status="conflicting", candidates=records,
                          detail=f"cited version {version} is not among {[r.version for r in records]}")
    if len(matching) > 1:
        return Resolution(**base, status="conflicting", candidates=matching,
                          detail="the identifier matches more than one record")
    return Resolution(**base, status="found", record=matching[0])


async def _lookup(resolver: SourceResolver, scheme: str, value: str, version: str | None,
                  timeout_s: float) -> Resolution:
    def failed(kind: str, detail: str) -> Resolution:
        return Resolution(id_scheme=scheme, id_value=value, lookup="failed", status="requires_verification",
                          resolver=resolver.name, error_kind=kind, detail=detail)

    try:
        raw = await asyncio.wait_for(resolver.lookup(scheme, value), timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        return failed("timeout", f"no answer within {timeout_s:g}s")
    except LookupFailed as error:
        return failed(error.kind, error.detail or error.kind)
    except Exception as error:  # noqa: BLE001 - any resolver crash means the lookup did not complete
        return failed("resolver_error", f"{type(error).__name__}: {error}")
    try:
        if not isinstance(raw, list):
            raise TypeError(f"expected a list of records, got {type(raw).__name__}")
        records = [SourceRecord.model_validate(r if isinstance(r, dict) else r.model_dump()) for r in raw]
    except (TypeError, AttributeError, ValidationError) as error:
        return failed("invalid_response", str(error)[:300])
    return _judge(scheme, value, version, records, resolver.name)


async def _resolve(source: SourceRef, resolver: SourceResolver | None, artifact_paths: Mapping[str, str],
                   observed_artifacts: Mapping[str, str] | None, timeout_s: float,
                   cache: dict[tuple[str, str, str | None], Resolution]) -> Resolution:
    if source.id_scheme and source.id_value:
        scheme, value = source.id_scheme, source.id_value.strip()
    elif source.artifact_id:
        path = artifact_paths.get(source.artifact_id, "")
        if observed_artifacts is None:
            return _skipped("artifact", source.artifact_id, "manifest", "requires_verification",
                            "manifest_unavailable", "artifact hashes need the observed task manifest")
        digest = observed_artifacts.get(path)
        base = {"id_scheme": "artifact", "id_value": source.artifact_id, "lookup": "succeeded",
                "resolver": "manifest"}
        if digest is None:
            return Resolution(**base, status="not_found", detail=f"{path} is not in the observed manifest")
        record = SourceRecord(id_scheme="artifact", id_value=source.artifact_id, version=digest, url=path)
        if source.version and source.version.strip().casefold() != digest.casefold():
            # The file at this path changed after the result cited it.
            return Resolution(**base, status="conflicting", candidates=[record],
                              detail=f"cited sha256 {source.version} differs from the observed {digest}")
        return Resolution(**base, status="found", record=record)
    else:
        scheme, value = "uri", (source.uri or "").strip()
    key = (scheme, value.casefold(), source.version)
    if key in cache:
        return cache[key]
    pattern = ID_FORMATS.get(scheme)
    if pattern and not re.fullmatch(pattern, value, re.IGNORECASE):
        resolution = _skipped(scheme, value, resolver.name if resolver else "none", "insufficient",
                              "malformed_id", f"{value!r} is not a valid {scheme} identifier")
    elif resolver is None:
        resolution = _skipped(scheme, value, "none", "requires_verification", "disabled",
                              "live source lookup is off")
    elif not resolver.supports(scheme):
        resolution = _skipped(scheme, value, resolver.name, "requires_verification", "unsupported_scheme",
                              f"{resolver.name} cannot look up {scheme}")
    else:
        resolution = await _lookup(resolver, scheme, value, source.version, timeout_s)
    cache[key] = resolution
    return resolution


async def verify_sources(result: "ResearchResult", resolver: SourceResolver | None = None, *,
                         observed_artifacts: Mapping[str, str] | None = None,
                         timeout_s: float = 20.0) -> VerificationReport:
    """Resolve every observed countable source in a result and judge each claim's declared status.

    ``observed_artifacts`` maps artifact paths to the SHA-256 the runner observed; without it artifact
    evidence stays ``requires_verification``.
    """
    artifact_paths = {ref.artifact_id: ref.path for ref in result.artifact_refs}
    cache: dict[tuple[str, str, str | None], Resolution] = {}
    checks: dict[str, EvidenceCheck] = {}
    for row in result.evidence:
        if not row.counts or row.source is None:
            continue
        resolution = await _resolve(row.source, resolver, artifact_paths, observed_artifacts, timeout_s, cache)
        checks[row.id] = EvidenceCheck(evidence_id=row.id, source_location=row.source.locator,
                                       resolution=resolution)

    groups = {row.id: row.independence_group for row in result.evidence}
    claim_checks: list[ClaimCheck] = []
    for claim in result.claims:
        needed = STATUS_NEEDS.get(claim.status)
        verified: list[str] = []
        unverified: list[str] = []
        defects: list[str] = []
        for link in result.links:
            if link.claim_id != claim.id or link.relation == "context" or link.evidence_id not in checks:
                continue
            resolution = checks[link.evidence_id].resolution
            if resolution.status in {"not_found", "conflicting", "insufficient"}:
                defects.append(f"{link.evidence_id} {link.relation} via {resolution.id_scheme}:"
                               f"{resolution.id_value} which is {resolution.status}: {resolution.detail}")
            elif resolution.status == "requires_verification":
                unverified.append(link.evidence_id)
            elif link.relation == needed:
                verified.append(link.evidence_id)
        if defects:
            state = "defective"
        elif needed is None:
            state = "not_asserted"
        else:
            state = "verified" if verified else "unverified"
        claim_checks.append(ClaimCheck(
            claim=claim.key, declared_status=claim.status, state=state, verified_evidence=verified,
            unverified_evidence=unverified, defects=defects,
            independent_groups=sorted({groups[e] for e in verified if groups.get(e)})))

    return VerificationReport(
        plan_sha256=result.plan_sha256, step_id=result.step_id,
        resolver=resolver.name if resolver else "none",
        evidence=list(checks.values()), claims=claim_checks,
        lookup_failures=[eid for eid, check in checks.items() if check.resolution.lookup == "failed"],
        ok=all(check.state in {"verified", "not_asserted"} for check in claim_checks))
