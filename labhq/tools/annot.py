"""Variant lookups in public resources for the labhq_annot MCP server (#435 C ②): Ensembl VEP, gnomAD, ClinVar.

Every result carries `source: {db, release_or_version, url, queried_at, request_sha256}`. The same record, without
the input variants (only their count and the request hash), is appended to the task's
`outputs/annotation_queries.jsonl`; the full database answer goes to `outputs/annotation/<tool>-<hash>.json`.
Answers are cached per (tool, release, options, variant) under the runner state folder, so a new release asks again.

Variants from restricted zones are looked up like any other (PI decision 2026-10-06): no warning, approval or refusal.

API formats and limits (checked 2026-10-06):
- Ensembl REST VEP: POST /vep/:species/region|hgvs|id, at most 200 entries per request; 55,000 requests per hour
  (~15 per second), 429 with Retry-After when exceeded. Release from GET /info/data.
- gnomAD GraphQL (https://gnomad.broadinstitute.org/api): `variant(variantId|rsid, dataset)`, one variant per
  query; the gnomAD team asks for at most 10 queries per minute.
- NCBI E-utilities: 3 requests per second without an API key. ClinVar build and last update from einfo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from ..adapters.owned import OwnedPathError, append_owned, write_owned

ENSEMBL = {"GRCh38": "https://rest.ensembl.org", "GRCh37": "https://grch37.rest.ensembl.org"}
GNOMAD_URL = "https://gnomad.broadinstitute.org/api"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
USER_AGENT = "labhq-annot (+https://github.com/ehojune/bioinfo-team-3d)"

VEP_BATCH = 200  # Ensembl's POST limit for vep/:species/{region,hgvs,id}
VEP_OPTIONS = {"canonical": 1, "hgvs": 1, "mane": 1}
GNOMAD_MAX_PER_CALL = 50  # 10 queries per minute: 50 new variants take about 5 minutes
CLINVAR_MAX_PER_CALL = 200
CLINVAR_RETMAX = 20
ESUMMARY_BATCH = 100
MIN_INTERVAL_S = {"ensembl": 1 / 15, "gnomad": 6.0, "ncbi": 1 / 3}
RETRY_STATUS = frozenset({429, 502, 503, 504})
MAX_ATTEMPTS = 5
MAX_WAIT_S = 120.0  # a Retry-After longer than this (an hourly quota spent) fails the lookup instead of waiting

# Part of every cache key: raise it when a query or answer shape changes, so old answers are not reused.
CACHE_SCHEMA = 1
QUERY_LOG = "outputs/annotation_queries.jsonl"
RESULT_DIR = "outputs/annotation"

_VCF = re.compile(r"^(?:chr)?([0-9]{1,2}|X|Y|MT|M):([0-9]+):([ACGTN]+):([ACGTN]+)$", re.IGNORECASE)
_RSID = re.compile(r"^rs([0-9]+)$", re.IGNORECASE)
_HGVS = re.compile(r"^[A-Za-z0-9_.\-]+(?:\([A-Za-z0-9_.\-]+\))?:[cgmnpr]\.\S+$")
_VCV = re.compile(r"^VCV([0-9]+)(?:\.[0-9]+)?$", re.IGNORECASE)
_UID = re.compile(r"^[0-9]+$")
_SPECIES = re.compile(r"^[a-z][a-z_]*$")
_DATASET = re.compile(r"^gnomad_[a-z0-9_]+$")
FORMAT_HINT = "chr:pos:ref:alt (예: 17:43045712:T:C), HGVS (예: NM_007294.4:c.68_69del) 또는 rsID (예: rs80357906)"


class LookupFailed(Exception):
    """A lookup that did not reach an answer (network, server, quota). Never a negative result."""


@dataclass(frozen=True)
class Variant:
    raw: str
    kind: str  # vcf | hgvs | rsid | vcv | uid
    key: str
    chrom: str = ""
    pos: int = 0
    ref: str = ""
    alt: str = ""


def parse_variant(value: object, clinvar_ids: bool = False) -> Variant:
    """One input as chr:pos:ref:alt, HGVS or rsID (plus ClinVar VCV accession or Variation ID when asked).
    Raises ValueError with the reason; the caller reports it for that item only."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"변이는 문자열이어야 합니다: {FORMAT_HINT}")
    raw = value.strip()
    match = _VCF.match(raw)
    if match:
        chrom, pos, ref, alt = match.group(1).upper(), int(match.group(2)), match.group(3).upper(), match.group(4).upper()
        chrom = "MT" if chrom in ("M", "MT") else chrom
        if chrom.isdigit():
            if not 1 <= int(chrom) <= 22:
                raise ValueError(f"알 수 없는 염색체: {match.group(1)}")
            chrom = str(int(chrom))
        if pos < 1:
            raise ValueError("위치는 1 이상이어야 합니다")
        if ref == alt:
            raise ValueError("ref와 alt가 같습니다")
        return Variant(raw, "vcf", f"{chrom}:{pos}:{ref}:{alt}", chrom, pos, ref, alt)
    match = _RSID.match(raw)
    if match:
        return Variant(raw, "rsid", f"rs{int(match.group(1))}")
    if _HGVS.match(raw):
        return Variant(raw, "hgvs", raw)
    if clinvar_ids:
        match = _VCV.match(raw)
        if match:
            return Variant(raw, "vcv", f"VCV{int(match.group(1)):09d}")
        if _UID.match(raw):
            return Variant(raw, "uid", str(int(raw)))
        raise ValueError(f"형식을 알 수 없습니다. {FORMAT_HINT}, ClinVar VCV 번호나 Variation ID")
    raise ValueError(f"형식을 알 수 없습니다. {FORMAT_HINT}")


def _sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        wait = float(value) if value is not None else None
    except ValueError:
        return None
    return wait if wait is not None and math.isfinite(wait) and wait >= 0 else None


class Http:
    """Paced requests with retries. One pace per service: Ensembl, gnomAD and NCBI have separate limits."""

    def __init__(self, client: httpx.AsyncClient, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.client, self.sleep, self.clock = client, sleep, clock
        self.last: dict[str, float] = {}

    async def _pace(self, service: str) -> None:
        last = self.last.get(service)
        if last is not None:
            gap = MIN_INTERVAL_S[service] - (self.clock() - last)
            if gap > 0:
                await self.sleep(gap)
        self.last[service] = self.clock()

    async def request(self, service: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._pace(service)
            try:
                response = await self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise LookupFailed(f"{url}에 연결하지 못했습니다({MAX_ATTEMPTS}회): {exc}") from exc
                await self.sleep(min(2.0 ** attempt, MAX_WAIT_S))
                continue
            if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                wait = _retry_after(response)
                wait = min(2.0 ** attempt, MAX_WAIT_S) if wait is None else wait
                if wait > MAX_WAIT_S:
                    raise LookupFailed(f"{url}: 요청 한도를 다 썼습니다(HTTP {response.status_code}, "
                                       f"{wait:.0f}초 뒤 다시 가능)")
                await self.sleep(wait)
                continue
            if response.status_code in RETRY_STATUS:
                raise LookupFailed(f"{url}: HTTP {response.status_code}가 {MAX_ATTEMPTS}회 이어졌습니다")
            return response
        raise AssertionError("unreachable")


class Cache:
    """Answers per (tool, release, options, variant) in the runner state folder. Best effort: a broken entry is a miss."""

    def __init__(self, root: Path | None) -> None:
        self.root = Path(root) if root else None

    @staticmethod
    def key(tool: str, release: str, options: dict, variant_key: str) -> str:
        return _sha256({"schema": CACHE_SCHEMA, "tool": tool, "release": release, "options": options,
                        "variant": variant_key})

    def _path(self, tool: str, key: str) -> Path | None:
        return self.root / tool / key[:2] / f"{key}.json" if self.root else None

    def get(self, tool: str, key: str) -> dict | None:
        path = self._path(tool, key)
        if path is None:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and "answer" in value else None

    def put(self, tool: str, key: str, answer: object, queried_at: str) -> None:
        path = self._path(tool, key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps({"answer": answer, "queried_at": queried_at}, ensure_ascii=False),
                                 encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            pass


@dataclass
class Item:
    variant: Variant | None
    raw: object
    error: str | None = None
    answer: object = None
    cached_at: str | None = None


class Annotator:
    def __init__(self, client: httpx.AsyncClient, workdir: Path | None, cache_dir: Path | None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.http = Http(client, sleep, clock)
        self.workdir = Path(workdir) if workdir else None
        self.cache = Cache(cache_dir)

    # ----- shared -----
    @staticmethod
    def _items(values: object, clinvar_ids: bool = False) -> list[Item]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not values:
            raise ValueError("변이 목록이 비었습니다")
        items = []
        for value in values:
            try:
                items.append(Item(parse_variant(value, clinvar_ids), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        return items

    def _from_cache(self, tool: str, release: str, options: dict, items: list[Item]) -> None:
        for item in items:
            if item.variant and item.error is None:
                hit = self.cache.get(tool, Cache.key(tool, release, options, item.variant.key))
                if hit is not None:
                    item.answer, item.cached_at = hit["answer"], hit.get("queried_at")

    def _to_cache(self, tool: str, release: str, options: dict, items: list[Item], queried_at: str) -> None:
        stored: set[str] = set()
        for item in items:
            if item.variant and item.error is None and item.cached_at is None and item.variant.key not in stored:
                stored.add(item.variant.key)
                self.cache.put(tool, Cache.key(tool, release, options, item.variant.key), item.answer, queried_at)

    def _finish(self, tool: str, db: str, release: str, url: str, options: dict, items: list[Item],
                summarize: Callable[[Item], dict], note: str | None = None) -> dict:
        queried_at = _now()
        self._to_cache(tool, release, options, items, queried_at)
        request_sha256 = _sha256({"tool": tool, "options": options,
                                  "inputs": [item.raw if isinstance(item.raw, str) else repr(item.raw)
                                             for item in items]})
        source = {"db": db, "release_or_version": release, "url": url, "queried_at": queried_at,
                  "request_sha256": request_sha256}
        results = []
        for item in items:
            row: dict[str, Any] = {"input": item.raw}
            if item.error is not None:
                row.update(ok=False, error=item.error)
            else:
                row.update(ok=True, **summarize(item))
                if item.cached_at:
                    row["cached_from"] = item.cached_at
            results.append(row)
        counts = {"n_inputs": len(items), "n_ok": sum(r["ok"] for r in results),
                  "n_errors": sum(not r["ok"] for r in results),
                  "n_cached": sum(1 for item in items if item.cached_at and item.error is None)}
        out: dict[str, Any] = {"source": source, **counts}
        if note:
            out["note"] = note
        full = {"source": source, "options": options,
                "results": [{"input": item.raw, "error": item.error} if item.error is not None else
                            {"input": item.raw, "answer": item.answer,
                             **({"cached_from": item.cached_at} if item.cached_at else {})} for item in items]}
        recorded = self._record(tool, source, counts, full)
        out.update(recorded)
        out["results"] = results
        return out

    def _record(self, tool: str, source: dict, counts: dict, full: dict) -> dict:
        if self.workdir is None:
            return {"record_error": "LABHQ_WORKDIR가 없어 기록하지 않았습니다"}
        relative = f"{RESULT_DIR}/{tool}-{source['request_sha256'][:12]}.json"
        try:
            write_owned(self.workdir, relative, json.dumps(full, ensure_ascii=False, indent=1) + "\n")
            # Only counts and the request hash: the variant list stays in the result file above, not the log.
            append_owned(self.workdir, QUERY_LOG, json.dumps(
                {"tool": tool, **source, **counts, "result_file": relative}, ensure_ascii=False) + "\n")
        except (OwnedPathError, OSError) as exc:
            return {"record_error": f"조회 기록을 남기지 못했습니다: {exc}"}
        return {"full_result": relative, "query_log": QUERY_LOG}

    @staticmethod
    def _all_failed(items: list[Item], lookup_errors: int) -> bool:
        return lookup_errors > 0 and not any(item.error is None for item in items)

    # ----- Ensembl VEP -----
    async def vep(self, variants: object, assembly: str = "GRCh38", species: str = "human") -> dict:
        if assembly not in ENSEMBL:
            raise ValueError(f"assembly는 {', '.join(ENSEMBL)} 중 하나입니다")
        species = species.strip().lower() if isinstance(species, str) else ""
        if not _SPECIES.match(species):
            raise ValueError("species는 Ensembl 이름이어야 합니다(예: human, homo_sapiens)")
        if assembly == "GRCh37" and species not in ("human", "homo_sapiens"):
            raise ValueError("GRCh37 서버는 사람(human)만 다룹니다")
        base = ENSEMBL[assembly]
        items = self._items(variants)
        release = await self._ensembl_release(base, assembly)
        options = {"assembly": assembly, "species": species, **VEP_OPTIONS}
        self._from_cache("vep", release, options, items)
        lookup_errors = 0
        endpoints = {"vcf": ("region", "variants"), "hgvs": ("hgvs", "hgvs_notations"), "rsid": ("id", "ids")}
        for kind, (endpoint, body_key) in endpoints.items():
            pending = list(dict.fromkeys(item.variant.key for item in items
                                         if item.variant and item.variant.kind == kind and item.error is None
                                         and item.cached_at is None))
            by_key = {item.variant.key: item.variant for item in items if item.variant}
            for start in range(0, len(pending), VEP_BATCH):
                batch = pending[start:start + VEP_BATCH]
                # A region line carries its own token as the VCF ID, so each answer maps back to its variant.
                entries = [(f"labhq{start + i}", _vep_entry(by_key[key], f"labhq{start + i}"), key) if kind == "vcf"
                           else (key, key, key) for i, key in enumerate(batch)]
                try:
                    answers = await self._vep_post(base, species, endpoint, body_key, entries)
                except LookupFailed as exc:
                    lookup_errors += len(batch)
                    answers = {key: LookupFailed(str(exc)) for _token, _payload, key in entries}
                for item in items:
                    if item.variant and item.variant.key in answers and item.cached_at is None:
                        answer = answers[item.variant.key]
                        if isinstance(answer, Exception):
                            item.error = f"VEP 조회 실패: {answer}"
                        else:
                            item.answer = answer
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"VEP 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        url = f"{base}/vep/{'homo_sapiens' if species == 'human' else species}"
        return self._finish("vep", "Ensembl VEP", release, url, options, items, _vep_summary)

    async def _ensembl_release(self, base: str, assembly: str) -> str:
        response = await self.http.request("ensembl", "GET", f"{base}/info/data",
                                           headers={"Accept": "application/json"})
        try:
            response.raise_for_status()
            releases = [int(r) for r in response.json()["releases"]]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise LookupFailed(f"Ensembl release를 읽지 못했습니다({base}/info/data): {exc}") from exc
        if not releases:
            raise LookupFailed(f"Ensembl release가 비었습니다({base}/info/data)")
        return f"Ensembl {max(releases)} ({assembly})"

    async def _vep_post(self, base: str, species: str, endpoint: str, body_key: str,
                        entries: list[tuple[str, str, str]]) -> dict[str, object]:
        """Answers by variant key. A 400 for a batch is split until the entries Ensembl refuses stand alone."""
        response = await self.http.request(
            "ensembl", "POST", f"{base}/vep/{species}/{endpoint}", params=VEP_OPTIONS,
            json={body_key: [payload for _token, payload, _key in entries]},
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        if response.status_code == 400:
            if len(entries) > 1:
                half = len(entries) // 2
                return {**await self._vep_post(base, species, endpoint, body_key, entries[:half]),
                        **await self._vep_post(base, species, endpoint, body_key, entries[half:])}
            return {entries[0][2]: ValueError(f"Ensembl이 거부했습니다: {_error_text(response)}")}
        if response.status_code != 200:
            raise LookupFailed(f"HTTP {response.status_code}: {_error_text(response)}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LookupFailed(f"VEP 응답이 JSON이 아닙니다: {exc}") from exc
        if not isinstance(data, list):
            raise LookupFailed("VEP 응답이 목록이 아닙니다")
        tokens = {token: key for token, _payload, key in entries}
        lowered = {token.lower(): key for token, key in tokens.items()}
        answers: dict[str, object] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            for field in ("id", "input"):
                value = row.get(field)
                key = tokens.get(value) or (lowered.get(value.lower()) if isinstance(value, str) else None)
                if key is not None:
                    answers.setdefault(key, row)
                    break
        for _token, _payload, key in entries:
            answers.setdefault(key, ValueError("VEP가 이 변이의 결과를 돌려주지 않았습니다(참조 염기 불일치나 "
                                               "알 수 없는 ID일 수 있습니다)"))
        return answers

    # ----- gnomAD -----
    async def gnomad(self, variants: object, dataset: str = "gnomad_r4") -> dict:
        if not isinstance(dataset, str) or not _DATASET.match(dataset):
            raise ValueError("dataset은 gnomAD dataset ID여야 합니다(예: gnomad_r4, gnomad_r3, gnomad_r2_1)")
        items = self._items(variants)
        for item in items:
            if item.variant and item.variant.kind == "hgvs":
                item.error = "gnomAD API는 HGVS를 받지 않습니다. chr:pos:ref:alt나 rsID로 주세요"
        # gnomAD names no finer version through the API than the dataset ID, so the dataset is the release.
        release = dataset
        options = {"dataset": dataset}
        self._from_cache("gnomad", release, options, items)
        pending = list(dict.fromkeys(item.variant.key for item in items
                                     if item.variant and item.error is None and item.cached_at is None))
        over = set(pending[GNOMAD_MAX_PER_CALL:])
        pending = pending[:GNOMAD_MAX_PER_CALL]
        by_key = {item.variant.key: item.variant for item in items if item.variant}
        answers: dict[str, object] = {}
        lookup_errors = 0
        for key in pending:
            try:
                answers[key] = await self._gnomad_one(by_key[key], dataset)
            except LookupFailed as exc:
                lookup_errors += 1
                answers[key] = exc
            except ValueError as exc:
                answers[key] = exc
        for item in items:
            if not item.variant or item.error is not None or item.cached_at is not None:
                continue
            if item.variant.key in over:
                item.error = (f"한 번에 새로 조회하는 변이는 {GNOMAD_MAX_PER_CALL}개까지입니다(gnomAD 한도 분당 10회). "
                              "나머지는 다시 호출하세요")
                continue
            answer = answers.get(item.variant.key)
            if isinstance(answer, Exception):
                item.error = f"gnomAD 조회 실패: {answer}"
            else:
                item.answer = answer
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"gnomAD 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        note = (f"한 호출에 새 변이 {GNOMAD_MAX_PER_CALL}개까지 조회합니다" if over else None)
        return self._finish("gnomad", "gnomAD", release, GNOMAD_URL, options, items, _gnomad_summary, note)

    async def _gnomad_one(self, variant: Variant, dataset: str) -> dict:
        variables: dict[str, Any] = {"dataset": dataset}
        if variant.kind == "rsid":
            variables["rsid"] = variant.key
        else:
            chrom = "M" if variant.chrom == "MT" else variant.chrom
            variables["variantId"] = f"{chrom}-{variant.pos}-{variant.ref}-{variant.alt}"
        response = await self.http.request("gnomad", "POST", GNOMAD_URL,
                                           json={"query": GNOMAD_QUERY, "variables": variables},
                                           headers={"Content-Type": "application/json"})
        try:
            body = response.json()
        except ValueError as exc:
            raise LookupFailed(f"HTTP {response.status_code}, JSON이 아닌 응답: {exc}") from exc
        if not isinstance(body, dict):
            raise LookupFailed(f"HTTP {response.status_code}, 알 수 없는 응답")
        errors = [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in body.get("errors") or []]
        variant_data = (body.get("data") or {}).get("variant") if isinstance(body.get("data"), dict) else None
        if variant_data:
            return {"found": True, "variant": variant_data}
        if errors and all("not found" in message.lower() for message in errors):
            return {"found": False}
        if errors:
            raise ValueError("; ".join(errors)[:500])
        if response.status_code != 200:
            raise LookupFailed(f"HTTP {response.status_code}")
        return {"found": False}

    # ----- ClinVar -----
    async def clinvar(self, variants_or_ids: object, assembly: str = "GRCh38") -> dict:
        if assembly not in ("GRCh38", "GRCh37"):
            raise ValueError("assembly는 GRCh38 또는 GRCh37입니다")
        items = self._items(variants_or_ids, clinvar_ids=True)
        release = await self._clinvar_release()
        options = {"assembly": assembly, "retmax": CLINVAR_RETMAX}
        self._from_cache("clinvar", release, options, items)
        pending = list(dict.fromkeys(item.variant.key for item in items
                                     if item.variant and item.error is None and item.cached_at is None))
        over = set(pending[CLINVAR_MAX_PER_CALL:])
        pending = pending[:CLINVAR_MAX_PER_CALL]
        by_key = {item.variant.key: item.variant for item in items if item.variant}
        found: dict[str, object] = {}
        lookup_errors = 0
        for key in pending:
            variant = by_key[key]
            if variant.kind == "uid":
                found[key] = {"uids": [variant.key], "count": 1}
                continue
            try:
                found[key] = await self._esearch(_clinvar_term(variant, assembly))
            except LookupFailed as exc:
                lookup_errors += 1
                found[key] = exc
            except ValueError as exc:
                found[key] = exc
        uids = list(dict.fromkeys(uid for value in found.values() if isinstance(value, dict) for uid in value["uids"]))
        docs: dict[str, dict] = {}
        summary_error: LookupFailed | None = None
        for start in range(0, len(uids), ESUMMARY_BATCH):
            try:
                docs.update(await self._esummary(uids[start:start + ESUMMARY_BATCH]))
            except LookupFailed as exc:
                summary_error = exc
        for item in items:
            if not item.variant or item.error is not None or item.cached_at is not None:
                continue
            if item.variant.key in over:
                item.error = f"한 번에 새로 조회하는 항목은 {CLINVAR_MAX_PER_CALL}개까지입니다. 나머지는 다시 호출하세요"
                continue
            value = found.get(item.variant.key)
            if isinstance(value, Exception):
                item.error = f"ClinVar 조회 실패: {value}"
                continue
            assert isinstance(value, dict)
            missing = [uid for uid in value["uids"] if uid not in docs]
            if missing and summary_error is not None:
                lookup_errors += 1
                item.error = f"ClinVar esummary 실패: {summary_error}"
                continue
            item.answer = {"count": value["count"], "records": [docs[uid] for uid in value["uids"] if uid in docs]}
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"ClinVar 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        note = f"한 호출에 새 항목 {CLINVAR_MAX_PER_CALL}개까지 조회합니다" if over else None
        return self._finish("clinvar", "ClinVar (NCBI E-utilities)", release, EUTILS, options, items,
                            lambda item: _clinvar_summary(item, assembly), note)

    async def _eutils(self, tool: str, params: dict) -> dict:
        response = await self.http.request("ncbi", "GET", f"{EUTILS}/{tool}.fcgi",
                                           params={**params, "retmode": "json", "tool": "labhq"})
        if response.status_code != 200:
            raise LookupFailed(f"{tool} HTTP {response.status_code}: {_error_text(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LookupFailed(f"{tool} 응답이 JSON이 아닙니다: {exc}") from exc
        if not isinstance(body, dict):
            raise LookupFailed(f"{tool} 응답을 읽지 못했습니다")
        return body

    async def _clinvar_release(self) -> str:
        body = await self._eutils("einfo", {"db": "clinvar"})
        info = (body.get("einforesult") or {}).get("dbinfo")
        info = info[0] if isinstance(info, list) and info else info
        if not isinstance(info, dict) or not (info.get("lastupdate") or info.get("dbbuild")):
            raise LookupFailed("ClinVar 판본(einfo dbbuild·lastupdate)을 읽지 못했습니다")
        parts = [str(info[k]) for k in ("dbbuild",) if info.get(k)]
        if info.get("lastupdate"):
            parts.append(f"last update {info['lastupdate']}")
        return "ClinVar " + ", ".join(parts)

    async def _esearch(self, term: str) -> dict:
        body = await self._eutils("esearch", {"db": "clinvar", "term": term, "retmax": CLINVAR_RETMAX})
        result = body.get("esearchresult")
        if not isinstance(result, dict):
            raise LookupFailed("esearch 결과가 없습니다")
        if result.get("ERROR"):
            raise ValueError(f"esearch 오류: {result['ERROR']}")
        ids = [str(uid) for uid in result.get("idlist") or []]
        try:
            count = int(result.get("count", len(ids)))
        except (TypeError, ValueError):
            count = len(ids)
        return {"uids": ids, "count": count}

    async def _esummary(self, uids: list[str]) -> dict[str, dict]:
        body = await self._eutils("esummary", {"db": "clinvar", "id": ",".join(uids)})
        result = body.get("result")
        if not isinstance(result, dict):
            raise LookupFailed(f"esummary 결과가 없습니다: {str(body.get('error') or body)[:200]}")
        return {uid: result[uid] for uid in result.get("uids") or [] if isinstance(result.get(uid), dict)}


GNOMAD_FIELDS = ("ac an homozygote_count hemizygote_count filters "
                 "populations { id ac an homozygote_count hemizygote_count }")
GNOMAD_QUERY = (
    "query LabhqVariant($variantId: String, $rsid: String, $dataset: DatasetId!) { "
    "variant(variantId: $variantId, rsid: $rsid, dataset: $dataset) { "
    "variant_id reference_genome chrom pos ref alt rsids "
    f"exome {{ {GNOMAD_FIELDS} }} genome {{ {GNOMAD_FIELDS} }} }} }}")


def _vep_entry(variant: Variant, token: str) -> str:
    return f"{variant.chrom} {variant.pos} {token} {variant.ref} {variant.alt} . . ."


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        return str(body.get("error") or body.get("message") or body)[:300]
    return str(body)[:300]


def _vep_summary(item: Item) -> dict:
    row = item.answer if isinstance(item.answer, dict) else {}
    transcripts = [t for t in row.get("transcript_consequences") or [] if isinstance(t, dict)]
    chosen = (next((t for t in transcripts if t.get("mane_select")), None)
              or next((t for t in transcripts if t.get("canonical")), None)
              or (transcripts[0] if transcripts else None))
    summary: dict[str, Any] = {
        "most_severe_consequence": row.get("most_severe_consequence"),
        "location": f"{row.get('seq_region_name')}:{row.get('start')}" if row.get("seq_region_name") else None,
        "allele_string": row.get("allele_string"),
        "genes": sorted({t["gene_symbol"] for t in transcripts if t.get("gene_symbol")}),
        "colocated_ids": [c["id"] for c in row.get("colocated_variants") or []
                          if isinstance(c, dict) and c.get("id")][:10],
    }
    if chosen:
        summary["transcript"] = {k: chosen.get(k) for k in ("transcript_id", "gene_symbol", "consequence_terms",
                                                            "impact", "hgvsc", "hgvsp", "mane_select", "canonical")
                                 if chosen.get(k) is not None}
    return summary


def _frequency(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    ac, an = data.get("ac"), data.get("an")
    out: dict[str, Any] = {"ac": ac, "an": an, "af": (ac / an) if isinstance(ac, int) and isinstance(an, int) and an
                           else None, "homozygote_count": data.get("homozygote_count"),
                           "filters": data.get("filters") or []}
    out["populations"] = [{"id": p.get("id"), "ac": p.get("ac"), "an": p.get("an"),
                           "af": p["ac"] / p["an"] if isinstance(p.get("ac"), int) and isinstance(p.get("an"), int)
                           and p["an"] else None}
                          for p in data.get("populations") or []
                          # Genetic ancestry groups only: subgroups carry "_" (afr_XX), XX/XY are the sex split.
                          if isinstance(p, dict) and isinstance(p.get("id"), str) and "_" not in p["id"]
                          and p["id"] not in ("XX", "XY")]
    return out


def _gnomad_summary(item: Item) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    if not answer.get("found"):
        return {"found": False, "meaning": "이 dataset에서 관찰되지 않았습니다(조회는 성공)"}
    variant = answer.get("variant") or {}
    exome, genome = _frequency(variant.get("exome")), _frequency(variant.get("genome"))
    ac = sum(x["ac"] for x in (exome, genome) if x and isinstance(x["ac"], int))
    an = sum(x["an"] for x in (exome, genome) if x and isinstance(x["an"], int))
    return {"found": True, "variant_id": variant.get("variant_id"), "rsids": variant.get("rsids") or [],
            "af_exome_plus_genome": ac / an if an else None, "exome": exome, "genome": genome}


def _clinvar_term(variant: Variant, assembly: str) -> str:
    if variant.kind == "vcf":
        field = "chrpos38" if assembly == "GRCh38" else "chrpos37"
        return f"{variant.chrom}[chr] AND {variant.pos}[{field}]"
    if variant.kind == "rsid":
        return variant.key
    if variant.kind == "vcv":  # [clv_acc] finds no VCV accession (checked 2026-10-06); all fields does
        return variant.key
    return f'"{variant.key}"[varnam]'


def _spdi_of(variant: Variant) -> tuple[int, str, str]:
    """VCF-style allele as SPDI position (0-based) and trimmed alleles, for comparing with ClinVar canonical_spdi."""
    shared = 0
    while shared < min(len(variant.ref), len(variant.alt)) and variant.ref[shared] == variant.alt[shared]:
        shared += 1
    return variant.pos - 1 + shared, variant.ref[shared:], variant.alt[shared:]


def _classification(doc: dict, field: str) -> dict | None:
    value = doc.get(field)
    if not isinstance(value, dict) or not value.get("description"):
        return None
    return {k: value.get(k) for k in ("description", "review_status", "last_evaluated") if value.get(k)}


def _clinvar_record(doc: dict, variant: Variant, assembly: str) -> dict:
    variation_set = [v for v in doc.get("variation_set") or [] if isinstance(v, dict)]
    spdi = next((v.get("canonical_spdi") for v in variation_set if v.get("canonical_spdi")), None)
    record: dict[str, Any] = {"uid": doc.get("uid"), "accession": doc.get("accession"), "title": doc.get("title"),
                              "genes": [g.get("symbol") for g in doc.get("genes") or []
                                        if isinstance(g, dict) and g.get("symbol")]}
    germline = _classification(doc, "germline_classification")
    if germline is None and isinstance(doc.get("clinical_significance"), dict):
        germline = _classification(doc, "clinical_significance")
    for name, value in (("germline_classification", germline),
                        ("clinical_impact_classification", _classification(doc, "clinical_impact_classification")),
                        ("oncogenicity_classification", _classification(doc, "oncogenicity_classification"))):
        if value:
            record[name] = value
    traits = [t.get("trait_name") for t in (doc.get("germline_classification") or {}).get("trait_set") or []
              if isinstance(t, dict) and t.get("trait_name")] if isinstance(doc.get("germline_classification"),
                                                                            dict) else []
    if traits:
        record["traits"] = traits[:10]
    if spdi:
        record["canonical_spdi"] = spdi
    if variant.kind == "vcf":
        exact = False
        if assembly == "GRCh38" and isinstance(spdi, str) and spdi.count(":") == 3:
            _seq, pos0, deleted, inserted = spdi.split(":")
            exact = pos0.isdigit() and (int(pos0), deleted.upper(), inserted.upper()) == _spdi_of(variant)
        # ClinVar's canonical SPDI is GRCh38 and fully justified: an indel in a repeat may differ from the input.
        record["match"] = "exact" if exact else "same_position"
    return record


def _clinvar_summary(item: Item, assembly: str) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    assert item.variant is not None
    records = [_clinvar_record(doc, item.variant, assembly) for doc in answer.get("records") or []
               if isinstance(doc, dict)]
    if item.variant.kind == "vcf":
        records.sort(key=lambda r: r.get("match") != "exact")
    out: dict[str, Any] = {"found": bool(records), "count": answer.get("count", len(records)), "records": records}
    if not records:
        out["meaning"] = "ClinVar에 이 검색어로 등록된 항목이 없습니다(조회는 성공)"
    if isinstance(out["count"], int) and out["count"] > len(records):
        out["truncated"] = f"검색 결과 {out['count']}개 중 {len(records)}개만 가져왔습니다"
    return out
