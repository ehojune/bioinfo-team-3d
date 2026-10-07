"""Source resolution and evidence verification for research results (#90 R06, #58 items 3-4).

Two questions stay separate. Did the lookup run (``lookup``: succeeded / failed / skipped)? And,
only when it ran, what did the authority say about the identifier (``status``)? A network error,
timeout, or disabled lookup is ``requires_verification`` and never ``not_found``: a failed lookup
is neither evidence nor proof of absence. Resolving an ID also says nothing about whether the
source supports the sentence; that stays with the reviewer (``support_review``).

``resolver=None`` means live lookup is off. Lookups run concurrently under a cap and a report deadline, so a
slow authority leaves sources unverified instead of holding the report for minutes (#116, #422).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .claims import (  # noqa: F401 - ID_FORMATS stays importable from here
    ID_FORMATS, SEVERAL_SPELLINGS, STATUS_NEEDS, SourceRef, accession_base, accession_parts,
    normalize_artifact_path, normalize_id, registry_id)

if TYPE_CHECKING:
    from ..research.contract import ResearchResult

LookupState = Literal["succeeded", "failed", "skipped"]
IdStatus = Literal["found", "not_found", "insufficient", "conflicting", "requires_verification"]
FAILURE_KINDS = frozenset({"network", "timeout", "rate_limited", "auth", "server", "invalid_response",
                           "resolver_error"})
SKIP_KINDS = frozenset({"disabled", "unsupported_scheme", "malformed_id", "manifest_unavailable",
                        "uri_unmapped", "base_match_only"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordId(StrictModel):
    id_scheme: str
    id_value: str


class SourceUpdate(StrictModel):
    """A post-publication update as seen from the cited record.

    Crossref calls the original paper's direction ``updated-by`` and the notice's direction ``update-to``.
    Keeping that direction prevents a retraction notice from being mistaken for a retracted research paper.
    """

    direction: Literal["updated_by", "update_to"]
    update_type: str
    related: RecordId | None = None
    label: str | None = None
    source: str | None = None


class EnrichmentFailure(StrictModel):
    """An optional alias/update lookup that failed after the cited record was found."""

    stage: Literal["ncbi_id_converter", "pubmed_summary"]
    error_kind: str
    detail: str = ""


class SourceRecord(StrictModel):
    id_scheme: str
    id_value: str
    version: str | None = None
    title: str | None = None
    url: str | None = None
    # The authority's own mapping to the same work under other schemes (DOI <-> PMID <-> PMCID). The ledger
    # cannot know a DOI row and a PMID row are one paper; the verifier reads it here (#168).
    same_as: list[RecordId] = []
    updates: list[SourceUpdate] = []
    enrichment_failures: list[EnrichmentFailure] = []


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
    return ``[]`` only when the authority answered that no such record exists. A record lists in ``same_as``
    the identifiers the authority maps to the same work, such as a paper's PMID and PMCID beside its DOI."""

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


RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504})


class LiveSourceResolver:
    """Crossref/DOI and NCBI resolver. Each call is bounded and failures are never absence.

    The optional transport and sleeper are seams for recorded-response tests; production uses httpx's network
    transport and ``asyncio.sleep``. Only the status codes in ``RETRYABLE_HTTP`` are retried.
    """

    name = "crossref-ncbi"

    def __init__(self, *, contact: str = "", request_timeout_s: float = 10.0, max_retries: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.contact = contact.strip()
        self.request_timeout_s = request_timeout_s
        self.max_retries = max_retries
        self.transport = transport
        self.sleep = sleep

    def supports(self, scheme: str) -> bool:
        return scheme in {"doi", "pmid", "pmcid"}

    @property
    def _headers(self) -> dict[str, str]:
        agent = "labhq-source-verifier/1.0"
        if self.contact:
            agent += f" (mailto:{self.contact})"
        return {"User-Agent": agent, "Accept": "application/json"}

    def _params(self, **values: str) -> dict[str, str]:
        params = {key: value for key, value in values.items() if value}
        if self.contact:
            params["email"] = self.contact
        return params

    async def lookup(self, scheme: str, value: str) -> list[SourceRecord]:
        if not self.supports(scheme):
            raise LookupFailed("resolver_error", f"unsupported source scheme {scheme}")
        normalized = normalize_id(scheme, value)
        async with httpx.AsyncClient(timeout=self.request_timeout_s, headers=self._headers,
                                     follow_redirects=False, transport=self.transport) as client:
            record = await self._lookup_primary(client, scheme, normalized)
            if record is None:
                return []
            await self._enrich_record(client, record)
            return [record]

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(method, url, **kwargs)
            except httpx.TimeoutException as error:
                raise LookupFailed("timeout", f"{method} {url} timed out") from error
            except httpx.HTTPError as error:
                raise LookupFailed("network", f"{method} {url} failed: {type(error).__name__}") from error
            if response.status_code not in RETRYABLE_HTTP:
                return response
            if attempt == self.max_retries:
                kind = "rate_limited" if response.status_code == 429 else "server"
                raise LookupFailed(kind, f"{method} {url} returned HTTP {response.status_code}")
            await self.sleep(_retry_after(response.headers.get("Retry-After")))
        raise AssertionError("retry loop did not return")

    async def _lookup_primary(self, client: httpx.AsyncClient, scheme: str,
                              value: str) -> SourceRecord | None:
        """Read the cited authority first; aliases and cross-database metadata never gate this result."""
        if scheme == "doi":
            return await self._lookup_doi_primary(client, value)
        if scheme == "pmid":
            summary = await self._pubmed_summary(client, value)
            return self._record_from_ncbi_summary(scheme, value, summary) if summary else None
        summary = await self._pmc_summary(client, value)
        return self._record_from_ncbi_summary(scheme, value, summary) if summary else None

    async def _lookup_doi_primary(self, client: httpx.AsyncClient, doi: str) -> SourceRecord:
        response = await self._request(client, "GET", f"https://api.crossref.org/works/{quote(doi, safe='')}",
                                       params=self._params(mailto=self.contact))
        message: Mapping[str, object] = {}
        if response.status_code == 404:
            fallback = await self._request(client, "HEAD", f"https://doi.org/{quote(doi, safe='/')}")
            if fallback.status_code == 404:
                return []
            _require_success(fallback)
        else:
            _require_success(response)
            payload = _json_object(response)
            raw_message = payload.get("message")
            if not isinstance(raw_message, Mapping):
                raise LookupFailed("invalid_response", "Crossref response has no message object")
            message = raw_message
        return SourceRecord(
            id_scheme="doi", id_value=str(message.get("DOI") or doi),
            title=_first_text(message.get("title")), url=str(message.get("URL") or f"https://doi.org/{doi}"),
            updates=_crossref_updates(message))

    def _record_from_ncbi_summary(self, scheme: str, value: str,
                                  summary: Mapping[str, object]) -> SourceRecord:
        return SourceRecord(
            id_scheme=scheme, id_value=value,
            title=_first_text(summary.get("title")),
            url=(f"https://pubmed.ncbi.nlm.nih.gov/{value}/" if scheme == "pmid" else
                 f"https://pmc.ncbi.nlm.nih.gov/articles/{value}/"),
            same_as=_dedupe_ids(_summary_ids(summary), (scheme, value)),
            updates=_pubmed_updates(summary))

    async def _enrich_record(self, client: httpx.AsyncClient, record: SourceRecord) -> None:
        """Add aliases and PubMed metadata without replacing a successful primary lookup."""
        mapping: dict[str, str] | None = None
        try:
            mapping = await self._id_converter(client, record.id_value)
        except LookupFailed as error:
            record.enrichment_failures.append(EnrichmentFailure(
                stage="ncbi_id_converter", error_kind=error.kind, detail=error.detail))
        if mapping:
            record.same_as = _dedupe_ids(
                [*record.same_as, *_record_ids(mapping, exclude=(record.id_scheme, record.id_value))],
                (record.id_scheme, record.id_value))
        if record.id_scheme == "pmid":
            return
        pmid = next((alias.id_value for alias in record.same_as if alias.id_scheme == "pmid"), "")
        if not pmid:
            return
        try:
            summary = await self._pubmed_summary(client, pmid)
        except LookupFailed as error:
            record.enrichment_failures.append(EnrichmentFailure(
                stage="pubmed_summary", error_kind=error.kind, detail=error.detail))
            return
        if summary:
            record.same_as = _dedupe_ids(
                [*record.same_as, *_summary_ids(summary)], (record.id_scheme, record.id_value))
            record.updates = _dedupe_updates([*record.updates, *_pubmed_updates(summary)])
            record.title = record.title or _first_text(summary.get("title"))

    async def _id_converter(self, client: httpx.AsyncClient, value: str) -> dict[str, str] | None:
        response = await self._request(
            client, "GET", "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
            params=self._params(ids=value, format="json", tool="labhq"))
        _require_success(response)
        payload = _json_object(response)
        records = payload.get("records")
        if not isinstance(records, list) or not records or not isinstance(records[0], Mapping):
            raise LookupFailed("invalid_response", "PMC ID Converter response has no records list")
        row = records[0]
        if row.get("errmsg"):
            return None
        return {key: str(row[key]) for key in ("doi", "pmid", "pmcid") if row.get(key) is not None}

    async def _pubmed_summary(self, client: httpx.AsyncClient, pmid: str) -> Mapping[str, object] | None:
        return await self._ncbi_summary(client, "pubmed", pmid, "PMID")

    async def _pmc_summary(self, client: httpx.AsyncClient, pmcid: str) -> Mapping[str, object] | None:
        uid = pmcid[3:] if pmcid.upper().startswith("PMC") else pmcid
        return await self._ncbi_summary(client, "pmc", uid, "PMCID", requested=pmcid)

    async def _ncbi_summary(self, client: httpx.AsyncClient, database: str, uid: str, label: str, *,
                            requested: str | None = None) -> Mapping[str, object] | None:
        response = await self._request(
            client, "GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params=self._params(db=database, id=uid, retmode="json", tool="labhq"))
        _require_success(response)
        payload = _json_object(response)
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise LookupFailed("invalid_response", f"NCBI {database} response has no result object")
        row = result.get(uid)
        if not isinstance(row, Mapping):
            raise LookupFailed("invalid_response", f"NCBI {database} response has no requested {label} "
                               f"{requested or uid}")
        return None if row.get("error") else row


def _retry_after(value: str | None) -> float:
    if not value:
        return 1.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 1.0


def _require_success(response: httpx.Response) -> None:
    if 200 <= response.status_code < 400:
        return
    if response.status_code in {401, 403}:
        detail = f"{response.request.method} {response.request.url} returned HTTP {response.status_code}"
        raise LookupFailed("auth", detail)
    raise LookupFailed("invalid_response",
                       f"{response.request.method} {response.request.url} returned HTTP {response.status_code}")


def _json_object(response: httpx.Response) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as error:
        raise LookupFailed("invalid_response", f"{response.request.url} returned invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise LookupFailed("invalid_response", f"{response.request.url} returned non-object JSON")
    return payload


def _first_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _crossref_updates(message: Mapping[str, object]) -> list[SourceUpdate]:
    updates: list[SourceUpdate] = []
    for field, direction in (("updated-by", "updated_by"), ("update-to", "update_to")):
        raw = message.get(field)
        rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, Mapping) else []
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("type"):
                continue
            related = RecordId(id_scheme="doi", id_value=str(row["DOI"])) if row.get("DOI") else None
            updates.append(SourceUpdate(direction=direction, update_type=str(row["type"]).casefold(),
                                        related=related, label=str(row["label"]) if row.get("label") else None,
                                        source=str(row["source"]) if row.get("source") else "crossref"))
    return updates


def _summary_ids(summary: Mapping[str, object]) -> list[RecordId]:
    names = {"doi": "doi", "pubmed": "pmid", "pmid": "pmid", "pmc": "pmcid", "pmcid": "pmcid"}
    rows = summary.get("articleids")
    result: list[RecordId] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or row.get("idtype") not in names or not row.get("value"):
            continue
        scheme = names[str(row["idtype"])]
        value = str(row["value"])
        if scheme == "pmcid":
            match = re.search(r"PMC\d+", value, re.IGNORECASE)
            if not match:
                continue
            value = match.group(0).upper()
        result.append(RecordId(id_scheme=scheme, id_value=value))
    return _dedupe_ids(result, ("", ""))


def _pubmed_updates(summary: Mapping[str, object]) -> list[SourceUpdate]:
    types = {str(value).casefold() for value in summary.get("pubtype", []) if isinstance(value, str)} \
        if isinstance(summary.get("pubtype"), list) else set()
    direction: Literal["updated_by", "update_to"] | None = None
    if "retracted publication" in types:
        direction = "updated_by"
    elif types & {"retraction of publication", "retraction notice"}:
        direction = "update_to"
    if direction is None:
        return []
    related: RecordId | None = None
    references = summary.get("references")
    expected = "retraction in" if direction == "updated_by" else "retraction of"
    for row in references if isinstance(references, list) else []:
        if not isinstance(row, Mapping) or str(row.get("reftype") or "").casefold() != expected:
            continue
        if row.get("pmid"):
            related = RecordId(id_scheme="pmid", id_value=str(row["pmid"]))
        match = re.search(r"\bdoi:\s*(10\.\S+)", str(row.get("refsource") or ""), re.IGNORECASE)
        if match:
            related = RecordId(id_scheme="doi", id_value=match.group(1).rstrip(".,;"))
        break
    return [SourceUpdate(direction=direction, update_type="retraction", related=related, source="pubmed")]


def _record_ids(mapping: Mapping[str, str], *, exclude: tuple[str, str]) -> list[RecordId]:
    return _dedupe_ids([RecordId(id_scheme=key, id_value=value) for key, value in mapping.items()
                        if key in {"doi", "pmid", "pmcid"}], exclude)


def _dedupe_ids(values: Iterable[RecordId], exclude: tuple[str, str]) -> list[RecordId]:
    seen = {(exclude[0], normalize_id(*exclude))}
    result: list[RecordId] = []
    for value in values:
        key = (value.id_scheme, normalize_id(value.id_scheme, value.id_value))
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _dedupe_updates(values: Iterable[SourceUpdate]) -> list[SourceUpdate]:
    result: list[SourceUpdate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for value in values:
        related = value.related
        key = (value.direction, value.update_type, related.id_scheme if related else "",
               normalize_id(related.id_scheme, related.id_value) if related else "")
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


class EvidenceCheck(StrictModel):
    evidence_id: str
    source_location: str | None  # where in the source the observation sits
    resolution: Resolution  # the worst of ``resolutions``: defect, then unverified, then found
    resolutions: list[Resolution]  # one per identifier the source carries (external ID or URI, artifact)
    source_defects: list[str] = []
    source_warnings: list[str] = []


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
    warnings: list[str] = []
    # R05 across schemes: rows the authority maps to one work but that declare different independence groups.
    recitations: list[str] = []
    recited_evidence: list[str] = []  # duplicate rows refused at CP2, in the order of ``recitations``
    ok: bool


def _skipped(scheme: str, value: str, resolver: str, status: IdStatus, kind: str, detail: str) -> Resolution:
    return Resolution(id_scheme=scheme, id_value=value, lookup="skipped", status=status, resolver=resolver,
                      error_kind=kind, detail=detail)


AccessionMatch = Literal["same", "base_only", "different"]


def compare_accessions(scheme: str, cited: str, other: str) -> AccessionMatch:
    """One judgment for every place that compares a cited accession with another spelling (#167).

    ``same``: one spelling. ``base_only``: one base accession where one side spells no version or isoform,
    VCV padding, or ClinVar records of different kinds (RCV beside a variation id); only the authority can say
    they agree. ``different``: another record, including two explicit versions or isoforms of one base
    (``ENSG...17`` and ``ENSG...16``, ``P04637-2`` and ``P04637-3``), as a mismatched ``version`` field is.
    """
    if normalize_id(scheme, cited) == normalize_id(scheme, other):
        return "same"
    if scheme not in SEVERAL_SPELLINGS:
        return "different"
    (left, left_suffix), (right, right_suffix) = accession_parts(scheme, cited), accession_parts(scheme, other)
    if left == right:
        if left_suffix is not None and right_suffix is not None and left_suffix != right_suffix:
            return "different"
        return "base_only"
    if scheme == "clinvar" and _clinvar_kind(left) != _clinvar_kind(right):
        return "base_only"
    return "different"


def _clinvar_kind(base: str) -> str:
    return "variation" if base.isdigit() else base[:3]  # rcv / scv


def _base_only(scheme: str, value: str, resolver: str, records: list[SourceRecord], source: str) -> Resolution:
    named = ", ".join(sorted({r.id_value for r in records}))
    return _skipped(scheme, value, resolver, "requires_verification", "base_match_only",
                    f"{source} names {named}, which shares only the base accession with the cited {value}; "
                    "whether they are one record is unconfirmed")


def _judge(scheme: str, value: str, version: str | None, records: list[SourceRecord], resolver: str) -> Resolution:
    base = {"id_scheme": scheme, "id_value": value, "lookup": "succeeded", "resolver": resolver}
    if not records:
        return Resolution(**base, status="not_found", detail="the authority has no record for this identifier")
    if any(r.id_scheme != scheme for r in records):
        # Numeric IDs collide across databases (PMID 12345 vs ClinVar 12345).
        return Resolution(**base, status="conflicting", candidates=records,
                          detail="the authority returned a record from a different scheme")
    kinds = [compare_accessions(scheme, value, r.id_value) for r in records]
    if "different" in kinds:
        return Resolution(**base, status="conflicting", candidates=records,
                          detail="the authority returned a different identifier")
    if "same" not in kinds:
        return _base_only(scheme, value, resolver, records, "the authority")
    if "base_only" in kinds:
        # The cited spelling beside another spelling of its base: dropping either would pick one unasked.
        return Resolution(**base, status="conflicting", candidates=records,
                          detail="the authority returned the cited identifier beside another spelling of its base")
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


RETRACTION_TYPES = frozenset({"retraction", "partial_retraction", "withdrawal", "removal"})
CORRECTION_TYPES = frozenset({"correction", "corrigendum", "erratum"})
CONCERN_TYPES = frozenset({"expression_of_concern"})


def source_update_findings(record: SourceRecord | None) -> tuple[list[str], list[str]]:
    """Defects and warnings for one found record; a notice is not the paper it updates."""
    defects: list[str] = []
    warnings: list[str] = []
    if record is None:
        return defects, warnings
    for update in record.updates:
        target = (f" {update.related.id_scheme}:{update.related.id_value}" if update.related else "")
        if update.update_type in RETRACTION_TYPES:
            if update.direction == "updated_by":
                defects.append(f"retracted_by{target}".rstrip())
            else:
                warnings.append(f"is_retraction_notice{target}".rstrip())
        elif update.update_type in CORRECTION_TYPES:
            label = "corrected_by" if update.direction == "updated_by" else "is_correction_notice"
            warnings.append(f"{label}{target}".rstrip())
        elif update.update_type in CONCERN_TYPES:
            label = "concern_raised_by" if update.direction == "updated_by" else "is_expression_of_concern"
            warnings.append(f"{label}{target}".rstrip())
    return list(dict.fromkeys(defects)), list(dict.fromkeys(warnings))


def source_record_findings(record: SourceRecord | None) -> tuple[list[str], list[str]]:
    """Defects from the primary record plus warnings for optional enrichment that did not complete."""
    defects, warnings = source_update_findings(record)
    if record is not None:
        warnings += [f"enrichment_unverified={failure.stage}:{failure.error_kind}"
                     + (f": {failure.detail}" if failure.detail else "")
                     for failure in record.enrichment_failures]
    return defects, list(dict.fromkeys(warnings))


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


async def _resolve_uri_for_id(uri: str, named: tuple[str, str] | None, cited: tuple[str, str],
                              lookups: _Lookups) -> Resolution | None:
    """Does the URI beside a cited ID point at that ID? None when it is that ID's own registry address.

    Code alone calls a registry URL ``conflicting`` when it names another base accession of the same scheme
    (``compare_accessions``). A DOI beside its PubMed URL, or ``ENSG...17`` beside ``/id/ENSG...``, may be
    one record: only the authority can tell, so those go to the resolver like any URL.
    """
    scheme, value = cited
    if named is not None and named[0] == scheme:
        match = compare_accessions(scheme, value, named[1])
        if match == "same":
            return None  # already checked as the ID itself
        if match == "different":
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
    kinds = [compare_accessions(scheme, value, r.id_value) for r in named_records]
    same = [r for r, kind in zip(named_records, kinds) if kind == "same"]
    if same:
        if scheme in SEVERAL_SPELLINGS and "different" in kinds:
            return Resolution(**base, status="conflicting", candidates=named_records,
                              detail=f"the uri resolves to the cited {scheme} and a different accession")
        return Resolution(**base, status="found", record=same[0])
    if "different" in kinds:
        return Resolution(**base, status="conflicting", candidates=named_records,
                          detail=f"the uri resolves to a different {scheme} than the cited {value}")
    return _base_only("uri", uri, resolver.name, named_records, "the uri")


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
        defects: list[str] = []
        warnings: list[str] = []
        for resolution in resolutions:
            found_defects, found_warnings = source_record_findings(resolution.record)
            defects += found_defects
            warnings += found_warnings
        checks[row.id] = EvidenceCheck(evidence_id=row.id, source_location=row.source.locator,
                                       resolution=worst, resolutions=resolutions,
                                       source_defects=list(dict.fromkeys(defects)),
                                       source_warnings=list(dict.fromkeys(warnings)))

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
            elif checks[link.evidence_id].source_defects:
                defects.append(f"{link.evidence_id} {link.relation}: "
                               + ", ".join(checks[link.evidence_id].source_defects))
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

    defective = [eid for eid, check in checks.items()
                 if check.resolution.status in DEFECT_STATUSES or check.source_defects]
    recitations, recited_evidence = _recitations(result, checks, artifact_paths)
    return VerificationReport(
        plan_sha256=result.plan_sha256, step_id=result.step_id,
        resolver=resolver.name if resolver else "none",
        evidence=list(checks.values()), claims=claim_checks,
        lookup_failures=[eid for eid, check in checks.items()
                         if any(r.lookup == "failed" for r in check.resolutions)],
        defective_evidence=defective,
        warnings=[f"{eid}: {warning}" for eid, check in checks.items() for warning in check.source_warnings],
        recitations=recitations, recited_evidence=recited_evidence,
        ok=not defective and not recitations
        and all(check.state in {"verified", "not_asserted"} for check in claim_checks))


def verification_lines(report: VerificationReport) -> list[str]:
    """Compact, stable lines shared by CP2, report appendices and ``labhq verify``."""
    lines: list[str] = []
    for check in report.evidence:
        for resolution in check.resolutions:
            findings = []
            defects, warnings = source_record_findings(resolution.record)
            findings += [f"defect={value}" for value in defects]
            findings += [f"warning={value}" for value in warnings]
            suffix = f"; {'; '.join(findings)}" if findings else ""
            lines.append(f"{report.step_id}/{check.evidence_id}: {resolution.id_scheme}:"
                         f"{resolution.id_value} {resolution.status}{suffix}")
    lines += [f"{report.step_id}/independence: recitation={message}" for message in report.recitations]
    return lines


def _recitations(result: "ResearchResult", checks: Mapping[str, EvidenceCheck],
                 artifact_paths: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """The ledger's re-citation rule with the identifiers the authority added (#168).

    The ledger keys each row by the spellings it writes (``SourceRef.identities``) and already rejects two
    groups on one key. A found record adds the identifiers it is ``same_as``, so a DOI row and a PMID row of
    one paper share a key here. Only found records count: an unchecked row keeps the keys it wrote.
    """
    errors: list[str] = []
    duplicate_rows: list[str] = []
    first: dict[str, tuple[str, str]] = {}
    for row in result.evidence:
        if not (row.countable and row.source and row.independence_group):
            continue
        keys = row.source.identities(artifact_paths)
        check = checks.get(row.id)
        for resolution in check.resolutions if check else []:
            record = resolution.record
            if resolution.status != "found" or record is None or record.id_scheme == "artifact":
                continue
            for alias in (record, *record.same_as):
                keys.append(f"{alias.id_scheme}:{normalize_id(alias.id_scheme, alias.id_value)}")
        for key in dict.fromkeys(keys):
            seen = first.setdefault(key, (row.id, row.independence_group))
            if seen[1] != row.independence_group:
                errors.append(f"evidence {seen[0]} and {row.id} cite one source ({key}, per the resolver) but "
                              f"declare independence groups {seen[1]} and {row.independence_group}; re-citation "
                              "is not independent")
                duplicate_rows.append(row.id)
                break
    return errors, duplicate_rows
