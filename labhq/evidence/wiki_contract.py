"""Consumer of the v1 wiki audit record: checks wiki-linked evidence of a step result against it (#1037)."""

import datetime
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from typing import Literal

from .claims import STATUS_NEEDS


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _utc(value: str) -> bool:
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value))
    except ValueError:
        return False


class WikiHit(StrictRecord):
    rel_path: StrictStr
    title: StrictStr = Field(max_length=300)
    heading: StrictStr
    line_start: StrictInt = Field(ge=1)
    line_end: StrictInt = Field(ge=1)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    mtime: StrictStr
    origin: dict[StrictStr, StrictStr] | None
    access_level: Literal["wiki_summary"]
    source_level: Literal["tertiary"]
    verified_against_primary: StrictBool
    freshness: Literal["ok", "stale", "source_missing", "disputed"]
    snippet_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    snippet_chars: StrictInt = Field(ge=0, le=20000)

    @model_validator(mode="after")
    def validate_hit(self):
        path = self.rel_path
        if (not path or "\\" in path or PurePosixPath(path).is_absolute()
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or re.match(r"^[A-Za-z]:", path)):
            raise ValueError("invalid wiki relative path")
        if self.line_end < self.line_start or not _utc(self.mtime) or self.verified_against_primary:
            raise ValueError("invalid wiki range, mtime, or verification")
        if self.origin is not None:
            if len(self.origin) != 1:
                raise ValueError("invalid origin")
            key, value = next(iter(self.origin.items()))
            valid = (key == "doi" and bool(re.fullmatch(r"10\.\d{4,9}/\S+", value)) and value == value.lower()
                     or key == "pmid" and bool(re.fullmatch(r"[0-9]+", value))
                     or key == "url" and urlsplit(value).scheme == "https" and bool(urlsplit(value).netloc))
            if not valid:
                raise ValueError("invalid origin")
        if (self.origin is None) != (self.freshness == "source_missing") and self.freshness != "disputed":
            raise ValueError("origin and freshness disagree")
        return self

    @property
    def locator(self):
        return f"wiki:{self.rel_path}#L{self.line_start}-L{self.line_end}@{self.sha256[:12]}"


class WikiCall(StrictRecord):
    v: StrictInt
    ts: StrictStr
    tool: Literal["search", "read"]
    query_sha256: StrictStr | None
    hits: list[WikiHit] = Field(max_length=10)
    error: Literal["wiki_disabled", "root_problem", "index_failed", "not_found", "range_invalid"] | None = None

    @model_validator(mode="after")
    def validate_call(self):
        if self.v != 1 or not _utc(self.ts):
            raise ValueError("invalid version or timestamp")
        if self.tool == "search":
            if not self.query_sha256 or not re.fullmatch(r"[0-9a-f]{64}", self.query_sha256):
                raise ValueError("invalid search hash")
        elif self.query_sha256 is not None:
            raise ValueError("read query hash must be null")
        if self.tool == "search" and any(hit.snippet_chars > 600 for hit in self.hits):
            raise ValueError("search snippet exceeds producer limit")
        if self.error and self.hits or self.tool == "read" and not self.error and len(self.hits) != 1:
            raise ValueError("invalid hits for call")
        return self


def read_wiki_jsonl(text: str) -> list[WikiCall]:
    """Reject the whole record on malformed or unrecognized lines."""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    calls = []
    for line in text.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line, object_pairs_hook=unique_pairs)
        if isinstance(raw, dict) and "error" in raw and raw["error"] is None:
            raise ValueError("error must be a string when present")
        calls.append(WikiCall.model_validate(raw))
    return calls


def resolve_locator(calls: list[WikiCall], locator: str) -> WikiHit:
    matches = [hit for call in calls for hit in call.hits if hit.locator == locator]
    if not matches:
        raise ValueError("wiki locator has no recorded hit")
    # A short hash collision or conflicting provenance is not a trustworthy match.
    if any(hit != matches[0] for hit in matches[1:]):
        raise ValueError("wiki locator has conflicting hits")
    return matches[0]


def wiki_support(hit: WikiHit, fulltext_origins: list[dict[str, str]]) -> tuple[bool, list[str]]:
    """Return whether direct origin reading is recorded and required limitations."""
    if hit.origin is None or hit.freshness == "disputed":
        return False, [hit.freshness, "excluded from support"]
    limitations = [] if hit.freshness == "ok" else [hit.freshness]
    direct = hit.origin in fulltext_origins
    if not direct:
        limitations.append("wiki summary only")
    return direct, limitations


def public_wiki_metadata(hit: WikiHit, *, approved_urls: set[str]) -> dict:
    """Produce a narrow metadata allowlist; does not sanitize report or snippet text."""
    origin = hit.origin
    if origin is None:
        url = None
    elif "doi" in origin:
        url = "https://doi.org/" + origin["doi"]
    elif "pmid" in origin:
        url = "https://pubmed.ncbi.nlm.nih.gov/" + origin["pmid"] + "/"
    else:
        url = origin["url"]
    # A title can contain private text; absent a separate public classification omit it.
    return {"origin_url": url if url in approved_urls else None, "freshness": hit.freshness}


AUDIT = "outputs/wiki_queries.jsonl"
MAX_AUDIT_BYTES = 4 << 20


def _wiki_row(row: dict) -> bool:
    source = row.get("source") or {}
    return source.get("access_status") == "wiki_summary" or (source.get("locator") or "").startswith("wiki:")


def wiki_findings(ledger: dict, calls: list[WikiCall]) -> tuple[dict[str, str], dict[str, str]]:
    """Evidence ids and claim ids the audit record does not bear out, each with the reason.

    Checks wiki-linked evidence against recorded hits and linked full-text evidence. ``calls`` must come from the
    audit file the wiki tool wrote, never from model-generated JSON. A row that cites no recorded locator, states a
    freshness the hit does not have, or names another origin is refused. A claim that a stale, source-missing or
    disputed hit would support, or that rests on a wiki summary alone, is unsupported."""
    bad_evidence: dict[str, str] = {}
    bad_claims: dict[str, str] = {}
    refs = {ref.get("artifact_id"): ref.get("path") for ref in ledger.get("artifact_refs", [])}
    evidence = {row.get("id"): row for row in ledger.get("evidence", [])}
    wiki_hits = {}
    for ident, row in evidence.items():
        source = row.get("source") or {}
        locator = source.get("locator") or ""
        if not _wiki_row(row):
            continue
        if (source.get("access_status") != "wiki_summary" or row.get("kind") != "literature_claim"
                or row.get("source_level") != "tertiary" or row.get("directness") != "indirect"
                or refs.get(source.get("artifact_id")) != AUDIT):
            bad_evidence[ident] = "invalid wiki classification or artifact reference"
            continue
        try:
            hit = resolve_locator(calls, locator)
        except ValueError as exc:
            bad_evidence[ident] = str(exc)
            continue
        if source.get("wiki_freshness") != hit.freshness:
            bad_evidence[ident] = "freshness does not match hit"
            continue
        scheme, value = source.get("id_scheme"), source.get("id_value")
        if scheme or value:
            expected = {scheme: value.lower() if scheme == "doi" and isinstance(value, str) else value}
            if expected != hit.origin:
                bad_evidence[ident] = "origin does not match hit"
                continue
        wiki_hits[ident] = hit
    for claim in ledger.get("claims", []):
        links = [link for link in ledger.get("links", []) if link.get("claim_id") == claim.get("id")
                 and link.get("relation") == "supports"]
        linked = [evidence.get(link.get("evidence_id"), {}) for link in links]
        hits = [wiki_hits[row.get("id")] for row in linked if row.get("id") in wiki_hits]
        if not hits:
            continue
        origins = []
        for row in linked:
            source = row.get("source") or {}
            if row.get("status") != "observed" or source.get("access_status") != "full_text":
                continue
            scheme, value = source.get("id_scheme"), source.get("id_value")
            if scheme in {"doi", "pmid"} and isinstance(value, str):
                origins.append({scheme: value.lower() if scheme == "doi" else value})
            if source.get("uri"):
                origins.append({"url": source["uri"]})
        for hit in hits:
            direct, limitations = wiki_support(hit, origins)
            if "excluded from support" in limitations:
                bad_claims[claim.get("id")] = f"{hit.freshness} wiki hit cannot support"
            elif claim.get("status") == "supported" and len(hits) == len(linked) and not direct:
                bad_claims[claim.get("id")] = "wiki-only support cannot be supported"
    return bad_evidence, bad_claims


def check_wiki_ledger(ledger: dict, calls: list[WikiCall]) -> list[str]:
    """`wiki_findings` as one line per defect."""
    bad_evidence, bad_claims = wiki_findings(ledger, calls)
    return ([f"evidence {ident}: {reason}" for ident, reason in bad_evidence.items()]
            + [f"claim {ident}: {reason}" for ident, reason in bad_claims.items()])


def load_audit(workdir: str | None) -> tuple[list[WikiCall] | None, str]:
    """The step's audit record read from its workdir, or None with the reason. One malformed line rejects it all."""
    if not workdir:
        return None, "the step has no workdir"
    path = Path(workdir) / AUDIT
    try:
        if not path.is_file():
            return None, f"{AUDIT} is not readable by the gateway"
        if path.stat().st_size > MAX_AUDIT_BYTES:
            return None, f"{AUDIT} is larger than {MAX_AUDIT_BYTES} bytes"
        return read_wiki_jsonl(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:  # UnicodeDecodeError and JSON errors are ValueErrors
        return None, f"{AUDIT} is not a valid v1 record ({type(exc).__name__})"


def step_findings(ledger: dict, workdir: str | None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Rows and claims CP2 must refuse for one accepted step result, shaped as ``result_salvage`` records them.

    Fail-closed: a ledger with wiki-linked evidence and no readable, valid audit record has every such row refused,
    because the only proof of what the wiki returned is that record. Nothing is read when no row is wiki-linked."""
    rows = [row for row in ledger.get("evidence", []) if isinstance(row, dict)]
    if not any(_wiki_row(row) for row in rows):
        return [], []
    calls, reason = load_audit(workdir)
    if calls is None:
        bad_evidence = {row.get("id"): f"no usable wiki audit record: {reason}" for row in rows if _wiki_row(row)}
        bad_claims: dict[str, str] = {}
    else:
        bad_evidence, bad_claims = wiki_findings(ledger, calls)
    pending = True
    while pending:  # everything derived from a refused row is refused with it
        pending = False
        for row in rows:
            parents = sorted(ref for ref in row.get("derived_from") or [] if ref in bad_evidence)
            if row.get("id") not in bad_evidence and parents:
                bad_evidence[row.get("id")] = f"derived from refused evidence {', '.join(parents)}"
                pending = True
    unsupported = dict(bad_claims)
    for claim in ledger.get("claims", []):
        needed = STATUS_NEEDS.get(claim.get("status"))
        cited = sorted({link.get("evidence_id") for link in ledger.get("links", [])
                        if link.get("claim_id") == claim.get("id") and link.get("relation") == needed})
        if needed and cited and all(ident in bad_evidence for ident in cited):
            unsupported.setdefault(claim.get("id"), f"{claim.get('status')} rests only on refused evidence "
                                                    f"{', '.join(cited)}")
    return ([{"row_type": "evidence", "row_id": ident, "reason": "wiki audit: " + reason}
             for ident, reason in bad_evidence.items()],
            [{"claim_id": ident, "reason": "wiki audit: " + reason} for ident, reason in unsupported.items()])
