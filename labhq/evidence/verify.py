"""Source resolution and evidence verification for research results (#90 R06, #58 items 3-4).

Two questions stay separate. Did the lookup run (``lookup``: succeeded / failed / skipped)? And,
only when it ran, what did the authority say about the identifier (``status``)? A network error,
timeout, or disabled lookup is ``requires_verification`` and never ``not_found``: a failed lookup
is neither evidence nor proof of absence. Resolving an ID also says nothing about whether the
source supports the sentence; that stays with the reviewer (``support_review``).

Live network resolvers are not part of this PR. ``resolver=None`` means live lookup is off. Lookups run
concurrently under a cap and a report deadline, so a slow authority leaves sources unverified instead of
holding the report for minutes (#116).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .claims import (  # noqa: F401 - ID_FORMATS stays importable from here
    ID_FORMATS, STATUS_NEEDS, SourceRef, normalize_artifact_path, normalize_id, registry_id)

if TYPE_CHECKING:
    from ..research.contract import ResearchResult

LookupState = Literal["succeeded", "failed", "skipped"]
IdStatus = Literal["found", "not_found", "insufficient", "conflicting", "requires_verification"]
FAILURE_KINDS = frozenset({"network", "timeout", "rate_limited", "auth", "server", "invalid_response",
                           "resolver_error"})
SKIP_KINDS = frozenset({"disabled", "unsupported_scheme", "malformed_id", "manifest_unavailable",
                        "uri_unmapped"})


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
        self.records = {(scheme, normalize_id(scheme, value)): [SourceRecord.model_validate(r) for r in rows]
                        for (scheme, value), rows in (records or {}).items()}
        self.failures = {(scheme, normalize_id(scheme, value)): kind
                         for (scheme, value), kind in (failures or {}).items()}
        self.schemes = set(schemes) if schemes is not None else {key[0] for key in [*self.records, *self.failures]}
        self.calls: list[tuple[str, str]] = []

    def supports(self, scheme: str) -> bool:
        return scheme in self.schemes

    async def lookup(self, scheme: str, value: str) -> list[SourceRecord]:
        key = (scheme, normalize_id(scheme, value))
        self.calls.append(key)
        if key in self.failures:
            raise LookupFailed(self.failures[key], f"fixture {self.failures[key]} for {scheme}:{value}")
        return list(self.records.get(key, []))


class EvidenceCheck(StrictModel):
    evidence_id: str
    source_location: str | None  # where in the source the observation sits
    resolution: Resolution  # the worst of ``resolutions``: defect, then unverified, then found
    resolutions: list[Resolution]  # one per identifier the source carries (external ID or URI, artifact)


class ClaimCheck(StrictModel):
    claim: str  # "<id>@<revision>"
    declared_status: str
    # verified: every supporting/contradicting source resolved and at least one carries the status.
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
    defective_evidence: list[str]  # evidence ids citing a not_found, malformed or conflicting source
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
    other = [r for r in records if normalize_id(scheme, r.id_value) != normalize_id(scheme, value)]
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


def _failed(scheme: str, value: str, resolver: str, kind: str, detail: str) -> Resolution:
    return Resolution(id_scheme=scheme, id_value=value, lookup="failed", status="requires_verification",
                      resolver=resolver, error_kind=kind, detail=detail)


class _Lookups:
    """Every authority call of one report: one per identifier, at most ``concurrency`` at once, none after
    the report deadline. A lookup the deadline cuts off is ``failed/timeout``, never ``not_found``."""

    def __init__(self, resolver: SourceResolver | None, *, timeout_s: float, deadline_s: float,
                 concurrency: int) -> None:
        self.resolver = resolver
        self.timeout_s = timeout_s
        self.deadline_s = deadline_s
        self._loop = asyncio.get_running_loop()
        self._deadline = self._loop.time() + deadline_s
        self._gate = asyncio.Semaphore(concurrency)
        self._calls: dict[tuple[str, str], asyncio.Future[list[SourceRecord] | Resolution]] = {}

    async def fetch(self, scheme: str, value: str) -> list[SourceRecord] | Resolution:
        """The authority's records for one identifier, or the failed resolution when the lookup did not end."""
        key = (scheme, normalize_id(scheme, value))
        if key not in self._calls:
            self._calls[key] = asyncio.ensure_future(self._call(scheme, value))
        outcome = await self._calls[key]
        if isinstance(outcome, Resolution):
            return outcome.model_copy(update={"id_value": value})
        return outcome

    async def _call(self, scheme: str, value: str) -> list[SourceRecord] | Resolution:
        assert self.resolver is not None
        name = self.resolver.name
        async with self._gate:
            remaining = self._deadline - self._loop.time()
            if remaining <= 0:
                return _failed(scheme, value, name, "timeout",
                               f"the report deadline of {self.deadline_s:g}s passed before this lookup started")
            limit = min(self.timeout_s, remaining)
            try:
                raw = await asyncio.wait_for(self.resolver.lookup(scheme, value), limit)
            except (asyncio.TimeoutError, TimeoutError):
                if limit < self.timeout_s:
                    return _failed(scheme, value, name, "timeout",
                                   f"no answer before the report deadline of {self.deadline_s:g}s")
                return _failed(scheme, value, name, "timeout", f"no answer within {self.timeout_s:g}s")
            except LookupFailed as error:
                return _failed(scheme, value, name, error.kind, error.detail or error.kind)
            except Exception as error:  # noqa: BLE001 - any resolver crash means the lookup did not complete
                return _failed(scheme, value, name, "resolver_error", f"{type(error).__name__}: {error}")
        try:
            if not isinstance(raw, list):
                raise TypeError(f"expected a list of records, got {type(raw).__name__}")
            return [SourceRecord.model_validate(r if isinstance(r, dict) else r.model_dump()) for r in raw]
        except (TypeError, AttributeError, ValidationError) as error:
            return _failed(scheme, value, name, "invalid_response", str(error)[:300])


DEFECT_STATUSES = frozenset({"not_found", "conflicting", "insufficient"})


def _severity(resolution: Resolution) -> int:
    if resolution.status in DEFECT_STATUSES:
        return 2
    return 1 if resolution.status == "requires_verification" else 0


def _resolve_artifact(artifact_id: str, cited_sha256: str | None, artifact_paths: Mapping[str, str],
                      observed: Mapping[str, set[str]] | None) -> Resolution:
    path = artifact_paths.get(artifact_id, "")
    if observed is None:
        return _skipped("artifact", artifact_id, "manifest", "requires_verification",
                        "manifest_unavailable", "artifact hashes need the observed task manifest")
    digests = observed.get(path)
    base = {"id_scheme": "artifact", "id_value": artifact_id, "lookup": "succeeded", "resolver": "manifest"}
    if not digests:
        return Resolution(**base, status="not_found", detail=f"{path} is not in the observed manifest")
    records = [SourceRecord(id_scheme="artifact", id_value=artifact_id, version=d, url=path) for d in sorted(digests)]
    if len(records) > 1:
        return Resolution(**base, status="conflicting", candidates=records,
                          detail=f"the manifest spells {path} several ways with different hashes")
    digest = records[0].version or ""
    if cited_sha256 and cited_sha256.strip().casefold() != digest.casefold():
        # The file at this path changed after the result cited it.
        return Resolution(**base, status="conflicting", candidates=records,
                          detail=f"cited sha256 {cited_sha256} differs from the observed {digest}")
    return Resolution(**base, status="found", record=records[0])


async def _resolve_external(scheme: str, value: str, version: str | None, lookups: _Lookups) -> Resolution:
    resolver = lookups.resolver
    pattern = ID_FORMATS.get(scheme)
    if pattern and not re.fullmatch(pattern, value, re.IGNORECASE):
        return _skipped(scheme, value, resolver.name if resolver else "none", "insufficient",
                        "malformed_id", f"{value!r} is not a valid {scheme} identifier")
    if resolver is None:
        return _skipped(scheme, value, "none", "requires_verification", "disabled", "live source lookup is off")
    if not resolver.supports(scheme):
        return _skipped(scheme, value, resolver.name, "requires_verification", "unsupported_scheme",
                        f"{resolver.name} cannot look up {scheme}")
    outcome = await lookups.fetch(scheme, value)
    if isinstance(outcome, Resolution):
        return outcome
    return _judge(scheme, value, version, outcome, resolver.name)


# Schemes where one record has several valid spellings: a version suffix (ENSG...17, NM_...5), a UniProt
# isoform (P04637-2) or ClinVar's VCV accession beside its numeric variation id.
_SEVERAL_SPELLINGS = frozenset({"ensembl", "refseq", "uniprot", "clinvar"})


async def _resolve_uri_for_id(uri: str, named: tuple[str, str] | None, cited: tuple[str, str],
                              lookups: _Lookups) -> Resolution | None:
    """Does the URI beside a cited ID point at that ID? None when it is that ID's own registry address.

    Code alone calls a registry URL ``conflicting`` only when it names another accession of the same scheme,
    and that scheme spells each record one way. A DOI beside its PubMed URL, or ``ENSG...17`` beside
    ``/id/ENSG...``, may be one record: only the authority can tell, so those go to the resolver like any URL.
    """
    scheme, value = cited
    if named is not None and named[0] == scheme:
        if normalize_id(*named) == normalize_id(scheme, value):
            return None  # already checked as the ID itself
        if scheme not in _SEVERAL_SPELLINGS:
            return Resolution(id_scheme="uri", id_value=uri, lookup="succeeded", status="conflicting",
                              resolver="registry_url",
                              candidates=[SourceRecord(id_scheme=named[0], id_value=named[1], url=uri)],
                              detail=f"the uri names {named[0]}:{named[1]}, not the cited {scheme}:{value}")
    resolver = lookups.resolver
    if resolver is None:
        return _skipped("uri", uri, "none", "requires_verification", "disabled", "live source lookup is off")
    if not resolver.supports("uri"):
        return _skipped("uri", uri, resolver.name, "requires_verification", "unsupported_scheme",
                        f"{resolver.name} cannot resolve the uri to compare it with {scheme}:{value}")
    outcome = await lookups.fetch("uri", uri)
    if isinstance(outcome, Resolution):
        return outcome
    base = {"id_scheme": "uri", "id_value": uri, "lookup": "succeeded", "resolver": resolver.name}
    if not outcome:
        return Resolution(**base, status="not_found", detail="the uri does not resolve")
    named_records = [record for record in outcome if record.id_scheme == scheme]
    if not named_records:
        return _skipped("uri", uri, resolver.name, "requires_verification", "uri_unmapped",
                        f"{resolver.name} resolved the uri but did not say which {scheme} it is")
    same = [r for r in named_records if normalize_id(scheme, r.id_value) == normalize_id(scheme, value)]
    if not same:
        return Resolution(**base, status="conflicting", candidates=named_records,
                          detail=f"the uri resolves to a different {scheme} than the cited {value}")
    return Resolution(**base, status="found", record=same[0])


async def _resolve(source: SourceRef, lookups: _Lookups, artifact_paths: Mapping[str, str],
                   observed: Mapping[str, set[str]] | None) -> list[Resolution]:
    """Check every identifier the source carries; skipping one would let it stand unchecked.

    A registry URL is checked as the identifier it names. A URI beside an external ID must point at that
    ID (#117). ``version`` describes the external record when there is one; on an artifact-only source it
    is the cited sha256.
    """
    resolutions: list[Resolution] = []
    uri = (source.uri or "").strip() or None
    named = registry_id(uri) if uri else None
    external: tuple[str, str] | None = None
    if source.id_scheme and source.id_value and source.id_value.strip():
        external = (source.id_scheme, source.id_value.strip())
        resolutions.append(await _resolve_external(*external, source.version, lookups))
        if uri:
            uri_check = await _resolve_uri_for_id(uri, named, external, lookups)
            if uri_check is not None:
                resolutions.append(uri_check)
    elif uri:
        external = named or ("uri", uri)
        resolutions.append(await _resolve_external(*external, source.version, lookups))
    if source.artifact_id:
        resolutions.append(_resolve_artifact(source.artifact_id, None if external else source.version,
                                             artifact_paths, observed))
    return resolutions


async def verify_sources(result: "ResearchResult", resolver: SourceResolver | None = None, *,
                         observed_artifacts: Mapping[str, str] | None = None,
                         timeout_s: float = 20.0, deadline_s: float = 120.0,
                         concurrency: int = 4) -> VerificationReport:
    """Resolve every cited source in a result and judge each claim's declared status.

    ``observed_artifacts`` maps artifact paths to the SHA-256 the runner observed; without it artifact
    evidence stays ``requires_verification``. ``ok`` needs every asserted claim verified and no defective
    source anywhere in the result, context and reasoning rows included.

    Lookups run ``concurrency`` at a time, each within ``timeout_s`` and all within ``deadline_s`` of the
    call. A resolver that blocks the event loop or ignores cancellation is bounded by neither.
    """
    if concurrency < 1:
        raise ValueError("concurrency must allow at least one lookup")
    artifact_paths = {ref.artifact_id: normalize_artifact_path(ref.path) for ref in result.artifact_refs}
    observed: dict[str, set[str]] | None = None
    if observed_artifacts is not None:
        observed = {}
        for path, digest in observed_artifacts.items():
            observed.setdefault(normalize_artifact_path(path), set()).add(digest)
    lookups = _Lookups(resolver, timeout_s=timeout_s, deadline_s=deadline_s, concurrency=concurrency)
    # Every row that relied on its source is checked: context, reasoning and zero-result rows included.
    # A made-up ID is a defect anywhere; only a retrieval that never reached its source is skipped.
    rows = [row for row in result.evidence if row.cites_source]
    resolved = await asyncio.gather(*(_resolve(row.source, lookups, artifact_paths, observed) for row in rows))
    checks: dict[str, EvidenceCheck] = {}
    for row, resolutions in zip(rows, resolved):
        if not resolutions:
            continue
        worst = max(resolutions, key=_severity)  # max keeps the first of equal severity
        checks[row.id] = EvidenceCheck(evidence_id=row.id, source_location=row.source.locator,
                                       resolution=worst, resolutions=resolutions)

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
            # One resolved source does not vouch for the others: an unchecked supporting or contradicting
            # source could still be invented, and a partial status depends on both sides.
            state = "verified" if verified and not unverified else "unverified"
        claim_checks.append(ClaimCheck(
            claim=claim.key, declared_status=claim.status, state=state, verified_evidence=verified,
            unverified_evidence=unverified, defects=defects,
            independent_groups=sorted({groups[e] for e in verified if groups.get(e)})))

    defective = [eid for eid, check in checks.items() if check.resolution.status in DEFECT_STATUSES]
    return VerificationReport(
        plan_sha256=result.plan_sha256, step_id=result.step_id,
        resolver=resolver.name if resolver else "none",
        evidence=list(checks.values()), claims=claim_checks,
        lookup_failures=[eid for eid, check in checks.items()
                         if any(r.lookup == "failed" for r in check.resolutions)],
        defective_evidence=defective,
        ok=not defective and all(check.state in {"verified", "not_asserted"} for check in claim_checks))
