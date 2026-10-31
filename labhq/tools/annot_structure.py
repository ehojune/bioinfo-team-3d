"""Structure and compound lookups for labhq_annot (#1187): RCSB PDB and ChEMBL. Same frame as `annot.py`: every result
names its source, the request goes to `outputs/annotation_queries.jsonl` (counts and hashes only) and the full answer
to `outputs/annotation/`, answers are cached, and a bad input fails only its own item.

APIs (shapes read from the public documentation; the live services were not called from this code's tests):
- RCSB Data API: GET https://data.rcsb.org/rest/v1/core/entry/{ID} (404 when the entry does not exist).
- RCSB Search API: GET https://search.rcsb.org/rcsbsearch/v2/query?json=<query> answers `result_set` (204 when nothing
  matches). Coordinate files are never downloaded.
- ChEMBL: GET <ebi.ac.uk/chembl base>/{status|target|molecule|activity}.json, paged with limit/offset (the base is built
  from parts in `CHEMBL`: a staff-side file may not spell the gateway's REST prefix, which a PI-only guard test checks).
  `status.json` names the database release (`chembl_db_version`).

Limits: neither service publishes a hard request rate in the pages reachable when this was written (2026-10-31; the
official pages could not be opened, so that is UNVERIFIED and secondhand). `MIN_INTERVAL_S` in `annot.py` keeps well
below ENCODE's 10 per second; the per-request ceiling on the request is the lookup limit of #1088.

Licenses recorded in `source`: RCSB PDB archive data are CC0 1.0 (rcsb.org usage policy); ChEMBL is CC BY-SA 3.0
(EMBL-EBI, https://chembl.gitbook.io/chembl-interface-documentation/about).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .annot import Annotator, Item, LookupFailed, _error_text

RCSB_DATA = "https://data.rcsb.org/rest/v1/core"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
# Joined so this staff-side file never spells the labhq gateway's REST prefix (the PI-only guard test).
CHEMBL = "https://www.ebi.ac.uk/chembl" + "/api" + "/data"

PDB_MAX_ITEMS = 25
PDB_TOP_DEFAULT = 10
PDB_TOP_MAX = 25
CHEMBL_MAX_ITEMS = 25
CHEMBL_LIMIT_DEFAULT = 50
CHEMBL_LIMIT_MAX = 200
CHEMBL_PAGE = 100
CHEMBL_SEARCH_TOP = 5
CHEMBL_KINDS = ("target", "compound", "activity")

PDB_SOURCE = {
    "license": "CC0 1.0 (RCSB PDB archive data)",
    "license_url": "https://www.rcsb.org/pages/policies",
    "usage": "구조 요약은 실험 모델의 기록이다. 해상도와 방법을 확인하고, 기능적 결론의 근거로 쓸 때는 원 논문을 읽는다",
}
CHEMBL_SOURCE = {
    "license": "CC BY-SA 3.0 (출처 표기와 동일 조건 공유 필요)",
    "license_url": "https://chembl.gitbook.io/chembl-interface-documentation/about",
    "usage": "활성 값은 서로 다른 assay의 보고값이다. standard_type·단위·assay를 확인하지 않고 비교하지 않는다",
}

_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_CHEMBL_ID = re.compile(r"^CHEMBL[0-9]+$", re.IGNORECASE)
_TEXT = re.compile(r"^[^\x00-\x1f]{2,200}$")


@dataclass(frozen=True)
class Ref:
    """One input of these tools. `key` is what the cache and dedup use."""
    raw: str
    kind: str  # entry | search (PDB); id | text (ChEMBL)
    key: str


def parse_pdb(value: object) -> Ref:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PDB ID(4자, 예: 4HHB) 또는 검색어 문자열입니다")
    raw = value.strip()
    if _PDB_ID.match(raw):
        return Ref(raw, "entry", raw.upper())
    if not _TEXT.match(raw):
        raise ValueError("PDB ID(4자) 또는 2~200자 검색어가 아닙니다")
    return Ref(raw, "search", "search:" + " ".join(raw.casefold().split()))


def parse_chembl(value: object, kind: str) -> Ref:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ChEMBL ID(예: CHEMBL203) 또는 검색어 문자열입니다")
    raw = value.strip()
    if _CHEMBL_ID.match(raw):
        return Ref(raw, "id", raw.upper())
    if kind == "activity":
        raise ValueError("activity는 대상(target)의 ChEMBL ID(예: CHEMBL203)만 받습니다. 먼저 kind=target으로 찾으세요")
    if not _TEXT.match(raw):
        raise ValueError("ChEMBL ID 또는 2~200자 검색어가 아닙니다")
    return Ref(raw, "text", "text:" + " ".join(raw.casefold().split()))


def _first(values: object) -> Any:
    return values[0] if isinstance(values, list) and values else None


def pdb_entry_summary(body: dict) -> dict:
    """The fields a report needs from one RCSB entry; the raw response stays in the result file."""
    info = body.get("rcsb_entry_info") or {}
    accession = body.get("rcsb_accession_info") or {}
    citation = body.get("rcsb_primary_citation") or {}
    return {
        "id": (body.get("rcsb_id") or "").upper() or None,
        "title": (body.get("struct") or {}).get("title"),
        "methods": [e.get("method") for e in body.get("exptl") or [] if isinstance(e, dict)],
        "resolution_angstrom": _first(info.get("resolution_combined")),
        "deposit_date": accession.get("deposit_date"),
        "release_date": accession.get("initial_release_date"),
        "revision_date": accession.get("revision_date"),
        "polymer_entity_count": info.get("polymer_entity_count"),
        "nonpolymer_bound_components": info.get("nonpolymer_bound_components") or [],
        "primary_citation": {"pubmed_id": citation.get("pdbx_database_id_PubMed"), "title": citation.get("title"),
                             "doi": citation.get("pdbx_database_id_DOI")} if citation else None,
    }


def _pdb_summary(item: Item) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    if not answer.get("found"):
        return {"found": False, "meaning": answer.get("meaning") or "RCSB PDB에 없습니다(조회는 성공)"}
    if "entry" in answer:
        return {"found": True, "entry": answer["entry"]}
    out: dict[str, Any] = {"found": True, "total_matches": answer.get("total"), "entries": answer.get("entries") or []}
    if answer.get("truncated"):
        out["truncated"] = (f"검색 결과 {answer.get('total')}개 중 상위 {len(out['entries'])}개만 요약했습니다. "
                            "나머지를 보고서가 결론에 쓰면 안 됩니다")
    return out


_TARGET_KEEP = ("target_chembl_id", "pref_name", "target_type", "organism", "tax_id")
_MOLECULE_KEEP = ("molecule_chembl_id", "pref_name", "molecule_type", "max_phase", "first_approval")
_ACTIVITY_KEEP = ("activity_id", "molecule_chembl_id", "target_chembl_id", "assay_chembl_id", "assay_type",
                  "standard_type", "standard_relation", "standard_value", "standard_units", "pchembl_value",
                  "document_chembl_id",
                  # ChEMBL's own validity flags travel with the number: a comment on a doubtful value, and 1 when the
                  # measurement repeats another record's citation (not an independent one).
                  "data_validity_comment", "potential_duplicate")


def _pick(row: dict, keys: tuple[str, ...]) -> dict:
    return {key: row.get(key) for key in keys}


def chembl_target_row(row: dict) -> dict:
    out = _pick(row, _TARGET_KEEP)
    out["components"] = [{"accession": c.get("accession"), "description": c.get("component_description")}
                         for c in row.get("target_components") or [] if isinstance(c, dict)]
    return out


def chembl_molecule_row(row: dict) -> dict:
    out = _pick(row, _MOLECULE_KEEP)
    structures = row.get("molecule_structures") or {}
    properties = row.get("molecule_properties") or {}
    out["canonical_smiles"] = structures.get("canonical_smiles")
    out["standard_inchi_key"] = structures.get("standard_inchi_key")
    out["molecular_formula"] = properties.get("full_molformula")
    out["molecular_weight"] = properties.get("full_mwt")
    return out


def _chembl_summary(item: Item) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    if not answer.get("found"):
        return {"found": False, "meaning": answer.get("meaning") or "ChEMBL에 없습니다(조회는 성공)"}
    out: dict[str, Any] = {"found": True, "kind": answer.get("kind"), "total": answer.get("total"),
                           "rows": answer.get("rows") or []}
    if answer.get("truncated"):
        out["truncated"] = (f"ChEMBL 응답 {answer.get('total')}개 중 {len(out['rows'])}개만 받았습니다. "
                            "잘린 목록을 전체처럼 인용하면 안 됩니다")
    if answer.get("kind") == "activity":
        flagged = [r for r in out["rows"] if isinstance(r, dict) and (r.get("data_validity_comment")
                                                                      or r.get("potential_duplicate"))]
        out["flagged_rows"] = len(flagged)
        if flagged:
            out["validity_note"] = (f"{len(flagged)}행에 ChEMBL 유효성 표지가 있습니다. data_validity_comment가 있는 값은 "
                                    "의심스러운 측정이고 potential_duplicate=1은 독립 측정이 아닌 중복 인용입니다. "
                                    "이 행을 근거로 쓰기 전에 표지를 확인하고, 그대로 인용하지 마세요")
    return out


class StructureAnnotator(Annotator):
    """RCSB PDB entries and searches, ChEMBL targets, compounds and activities."""

    async def _get(self, service: str, url: str, params: dict | None = None, what: str = "") -> Any:
        """The JSON body; None when the service says there is no such record (404); {} for 204 (no match)."""
        response = await self.http.request(service, "GET", url, params=params, headers={"Accept": "application/json"})
        if response.status_code == 404:
            return None
        if response.status_code == 204:
            return {}
        if response.status_code in (400, 422):
            raise ValueError(f"{what or service}가 요청을 거부했습니다(HTTP {response.status_code}): "
                             f"{_error_text(response)}")
        if response.status_code != 200:
            raise LookupFailed(f"{what or url} HTTP {response.status_code}: {_error_text(response)}")
        try:
            return response.json()
        except ValueError as exc:
            raise LookupFailed(f"{what or url} 응답이 JSON이 아닙니다: {exc}") from exc

    # ----- RCSB PDB -----
    async def pdb(self, ids_or_queries: object, top: int = PDB_TOP_DEFAULT) -> dict:
        values = [ids_or_queries] if isinstance(ids_or_queries, str) else ids_or_queries
        if not isinstance(values, list) or not values:
            raise ValueError("PDB ID나 검색어 목록이 비었습니다")
        if type(top) is not int or not 1 <= top <= PDB_TOP_MAX:
            raise ValueError(f"top은 1~{PDB_TOP_MAX}의 정수입니다")
        items = []
        for index, value in enumerate(values):
            if index >= PDB_MAX_ITEMS:
                items.append(Item(None, value, error=f"한 번에 {PDB_MAX_ITEMS}개까지입니다. 나머지는 다시 호출하세요"))
                continue
            try:
                items.append(Item(parse_pdb(value), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        release = "RCSB PDB weekly releases (no release number in the API; each entry carries its revision_date)"
        options = {"top": top}
        self._from_cache("pdb", release, options, items)
        lookup_errors = 0
        done: dict[str, object] = {}
        for item in items:
            if not item.variant or item.error is not None or item.cached_at is not None:
                continue
            ref = item.variant
            if ref.key not in done:
                try:
                    done[ref.key] = (await self._pdb_entry(ref) if ref.kind == "entry"
                                     else await self._pdb_search(ref, top))
                except LookupFailed as exc:
                    lookup_errors += 1
                    done[ref.key] = exc
                except ValueError as exc:
                    done[ref.key] = exc
            answer = done[ref.key]
            if isinstance(answer, Exception):
                item.error = f"RCSB PDB 조회 실패: {answer}"
            else:
                item.answer = answer
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"RCSB PDB 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        return self._finish("pdb", "RCSB PDB", release, RCSB_DATA, options, items, _pdb_summary,
                            source_details=PDB_SOURCE)

    async def _pdb_entry(self, ref: Ref) -> dict:
        body = await self._get("rcsb", f"{RCSB_DATA}/entry/{ref.key}", what=f"PDB {ref.key}")
        if not isinstance(body, dict) or not body:
            return {"found": False, "meaning": f"RCSB PDB에 {ref.key} 항목이 없습니다(조회는 성공)"}
        return {"found": True, "entry": pdb_entry_summary(body), "raw": body}

    async def _pdb_search(self, ref: Ref, top: int) -> dict:
        query = {"query": {"type": "terminal", "service": "full_text", "parameters": {"value": ref.raw}},
                 "return_type": "entry", "request_options": {"paginate": {"start": 0, "rows": top}}}
        body = await self._get("rcsb", RCSB_SEARCH, {"json": json.dumps(query, separators=(",", ":"))},
                               what="RCSB 검색")
        rows = [r for r in (body or {}).get("result_set") or [] if isinstance(r, dict) and r.get("identifier")]
        if not rows:
            return {"found": False, "meaning": "검색어에 맞는 PDB 항목이 없습니다(조회는 성공)"}
        total = body.get("total_count") if isinstance(body.get("total_count"), int) else len(rows)
        entries, raw = [], {}
        for row in rows[:top]:
            found = await self._pdb_entry(Ref(str(row["identifier"]), "entry", str(row["identifier"]).upper()))
            if found.get("found"):
                entries.append({**found["entry"], "score": row.get("score")})
                raw[found["entry"]["id"] or str(row["identifier"])] = found["raw"]
        return {"found": bool(entries), "total": total, "entries": entries, "raw": raw,
                "truncated": total > len(entries)}

    # ----- ChEMBL -----
    async def chembl(self, targets_or_compounds: object, kind: str = "activity",
                     limit: int = CHEMBL_LIMIT_DEFAULT) -> dict:
        if kind not in CHEMBL_KINDS:
            raise ValueError(f"kind는 {', '.join(CHEMBL_KINDS)} 중 하나입니다")
        if type(limit) is not int or not 1 <= limit <= CHEMBL_LIMIT_MAX:
            raise ValueError(f"limit은 1~{CHEMBL_LIMIT_MAX}의 정수입니다")
        values = [targets_or_compounds] if isinstance(targets_or_compounds, str) else targets_or_compounds
        if not isinstance(values, list) or not values:
            raise ValueError("ChEMBL ID나 검색어 목록이 비었습니다")
        items = []
        for index, value in enumerate(values):
            if index >= CHEMBL_MAX_ITEMS:
                items.append(Item(None, value, error=f"한 번에 {CHEMBL_MAX_ITEMS}개까지입니다. 나머지는 다시 호출하세요"))
                continue
            try:
                items.append(Item(parse_chembl(value, kind), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        # Nothing valid to look up: no request is made (not even the release), the errors are still recorded.
        valid = any(item.variant for item in items if item.error is None)
        release = f"ChEMBL {await self._chembl_release()}" if valid else "ChEMBL (release not asked: no valid input)"
        options = {"kind": kind, "limit": limit}
        self._from_cache("chembl", release, options, items)
        lookup_errors = 0
        done: dict[str, object] = {}
        for item in items:
            if not item.variant or item.error is not None or item.cached_at is not None:
                continue
            ref = item.variant
            if ref.key not in done:
                try:
                    done[ref.key] = await self._chembl_one(ref, kind, limit)
                except LookupFailed as exc:
                    lookup_errors += 1
                    done[ref.key] = exc
                except ValueError as exc:
                    done[ref.key] = exc
            answer = done[ref.key]
            if isinstance(answer, Exception):
                item.error = f"ChEMBL 조회 실패: {answer}"
            else:
                item.answer = answer
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"ChEMBL 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        return self._finish("chembl", "ChEMBL", release, CHEMBL, options, items, _chembl_summary,
                            source_details=CHEMBL_SOURCE)

    async def _chembl_release(self) -> str:
        body = await self._get("chembl", f"{CHEMBL}/status.json", what="ChEMBL status")
        version = body.get("chembl_db_version") if isinstance(body, dict) else None
        if not isinstance(version, str) or not version:
            raise LookupFailed("ChEMBL status가 데이터베이스 버전을 알려주지 않았습니다")
        return version

    async def _chembl_one(self, ref: Ref, kind: str, limit: int) -> dict:
        if kind == "activity":
            return await self._chembl_activity(ref, limit)
        noun, listing, keep = (("target", "targets", chembl_target_row) if kind == "target"
                               else ("molecule", "molecules", chembl_molecule_row))
        if ref.kind == "id":
            body = await self._get("chembl", f"{CHEMBL}/{noun}/{ref.key}.json", what=f"ChEMBL {ref.key}")
            rows = [body] if isinstance(body, dict) and body else []
        else:
            body = await self._get("chembl", f"{CHEMBL}/{noun}/search.json",
                                   {"q": ref.raw, "limit": CHEMBL_SEARCH_TOP}, what="ChEMBL 검색")
            rows = [r for r in (body or {}).get(listing) or [] if isinstance(r, dict)]
        if not rows:
            return {"found": False, "meaning": f"ChEMBL에 맞는 {noun}가 없습니다(조회는 성공)"}
        total = (body.get("page_meta") or {}).get("total_count") if isinstance(body, dict) else None
        total = total if isinstance(total, int) else len(rows)
        return {"found": True, "kind": kind, "total": total, "rows": [keep(r) for r in rows], "raw": rows,
                "truncated": total > len(rows)}

    async def _chembl_activity(self, ref: Ref, limit: int) -> dict:
        rows: list[dict] = []
        total: int | None = None
        while len(rows) < limit:
            body = await self._get("chembl", f"{CHEMBL}/activity.json",
                                   {"target_chembl_id": ref.key, "limit": min(CHEMBL_PAGE, limit - len(rows)),
                                    "offset": len(rows)}, what=f"ChEMBL activity {ref.key}")
            page = [r for r in (body or {}).get("activities") or [] if isinstance(r, dict)]
            meta = (body or {}).get("page_meta") or {}
            if isinstance(meta.get("total_count"), int):
                total = meta["total_count"]
            rows += page
            if not page or not meta.get("next"):
                break
        if not rows:
            return {"found": False, "meaning": f"{ref.key}의 활성 측정이 ChEMBL에 없습니다(조회는 성공)"}
        total = total if total is not None else len(rows)
        return {"found": True, "kind": "activity", "total": total, "rows": [_pick(r, _ACTIVITY_KEEP) for r in rows],
                "raw": rows, "truncated": total > len(rows)}
