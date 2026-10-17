"""Paper text for the labhq_annot MCP server (#697): Europe PMC open-access full text, else the abstract.

In the 10-16 trial every literature step read abstracts only: PubMed search and metadata 143 calls, the PubMed
full-text tool 0, and PMC pages opened on the web came back as reCAPTCHA screens. `fulltext` gives staff one recorded
route to the body of a paper: Europe PMC finds the record for a PMID, PMCID or DOI; an open-access copy in Europe PMC is
fetched as JATS XML and written as Markdown (sections, tables, figure captions, references) to
`.tmp/literature/<id>.md`. Without such a copy the file holds the abstract, and `access_level` says which one the
staff member read: full_text, abstract or metadata.

The files are third-party text under the paper's license, so they live in the task's `.tmp/`, not `outputs/`: a direct
run lists every file under outputs/ as a result and the edition publishes reported results (PR #698 review), while
nothing under .tmp/ is ever an output, bundled or published. The query log keeps each text's sha256 and IDs, so the
same text can be fetched again. Paywalled papers are not fetched from anywhere else.

API (checked 2026-10-16, REST version 6.9): GET /search?query=EXT_ID:<pmid> AND SRC:MED | PMCID:<pmcid> | DOI:"<doi>"
&resultType=core&format=json gives pmid, pmcid, doi, isOpenAccess, inEPMC, license, abstractText and fullTextUrlList;
GET /<PMCID>/fullTextXML answers 200 with JATS for an open-access copy and 404 when there is none; any other error
is a failed read, answered with the abstract for that call only and never cached (PR #698 review). A record with a
CC license can still lack a Europe PMC copy (the publisher hosts it); its publisher link is returned for one manual try.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from ..adapters.owned import OwnedPathError, write_owned
from .annot import Annotator, Item, LookupFailed, _now

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
LIT_DIR = ".tmp/literature"  # never outputs/: see the module docstring
TOOL = "fulltext"
CACHE_RELEASE = "europepmc-rest"  # Europe PMC has no data release; answers age out instead (annot.CACHE_MAX_AGE_S)
MAX_PER_CALL = 20
MAX_XML_BYTES = 20_000_000
MAX_CITATION_RESULTS = 1000
MAX_PREPRINT_RESULTS = 100

_PMID = re.compile(r"^(?:pmid:?\s*)?([0-9]{1,9})$", re.IGNORECASE)
_PMCID = re.compile(r"^(?:pmcid:?\s*)?(PMC[0-9]{1,9})$", re.IGNORECASE)
_DOI = re.compile(r"^(?:doi:?\s*|https?://(?:dx\.)?doi\.org/)?(10\.[0-9]{4,9}/\S+)$", re.IGNORECASE)
FORMAT_HINT = "PMID(예: 31868601), PMCID(예: PMC13248341) 또는 DOI(예: 10.1186/s12889-025-12345-6)"


@dataclass(frozen=True)
class PaperId:
    kind: str  # pmid | pmcid | doi
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"

    def query(self) -> str:
        if self.kind == "pmid":
            return f"EXT_ID:{self.value} AND SRC:MED"
        if self.kind == "pmcid":
            return f"PMCID:{self.value}"
        return f'DOI:"{self.value}"'

    def file_name(self) -> str:
        if self.kind != "doi":
            return f"{self.kind}-{self.value}.md"
        # A DOI may hold any character; the readable part alone can collide (10.1000/a:b, 10.1000/a/b), the hash cannot.
        readable = re.sub(r"[^A-Za-z0-9._-]+", "_", self.value)[:80]
        return f"doi-{readable}-{hashlib.sha256(self.value.encode('utf-8')).hexdigest()[:10]}.md"


@dataclass(frozen=True)
class LiteratureSearch:
    value: str

    @property
    def key(self) -> str:
        return hashlib.sha256(self.value.encode("utf-8")).hexdigest()


def parse_paper_id(value: object) -> PaperId:
    text = str(value or "").strip()
    for kind, pattern in (("pmcid", _PMCID), ("pmid", _PMID), ("doi", _DOI)):
        match = pattern.match(text)
        if match:
            found = match.group(1)
            return PaperId(kind, found.upper() if kind == "pmcid" else found.lower() if kind == "doi" else found)
    raise ValueError(f"논문 식별자를 읽지 못했습니다: {text!r}. {FORMAT_HINT}로 주세요")


def _yes(value: object) -> bool:
    return str(value or "").upper() == "Y"


def _bibliography(record: dict, *, preprint: bool = False) -> dict:
    """One compact discovery candidate. access_level_candidate is where to try reading, not proof it was read."""
    code = str(record.get("source") or record.get("src") or "").upper()
    ident = str(record.get("id") or record.get("extId") or "")
    pmid = record.get("pmid") or (ident if code == "MED" else None)
    pmcid = record.get("pmcid") or (ident if code == "PMC" else None)
    doi = record.get("doi")
    journal_info = record.get("journalInfo") or {}
    nested_journal = (journal_info.get("journal") or {}).get("title")
    journal = (record.get("journalTitle") or record.get("journalAbbreviation")
               or record.get("publicationTitle") or nested_journal)
    preprint_server = (record.get("bookOrReportDetails") or {}).get("publisher")
    full_text = _yes(record.get("isOpenAccess")) or _yes(record.get("inEPMC")) or bool(pmcid)
    abstract = bool(record.get("abstractText")) or bool(pmid) or code in {"MED", "PPR"}
    out = {
        "source": {"code": code, "id": ident},
        "pmid": str(pmid) if pmid else None,
        "pmcid": str(pmcid) if pmcid else None,
        "doi": str(doi) if doi else None,
        "title": record.get("title"),
        "authors": record.get("authorString"),
        "journal": journal,
        "year": str(record["pubYear"]) if record.get("pubYear") is not None else None,
        "access_level_candidate": "full_text" if full_text else "abstract" if abstract else "metadata",
    }
    if preprint:
        out["server"] = preprint_server
        out["review_status"] = "심사 전"
    return {key: value for key, value in out.items() if value not in (None, "")}


# ----- JATS to Markdown -----

def _text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _cell(node: ET.Element) -> str:
    return _text(node).replace("|", "\\|")


def _span(cell: ET.Element, attribute: str, limit: int) -> int:
    try:
        return max(1, min(int(cell.get(attribute) or 1), limit))
    except ValueError:
        return 1


def _grid(wrap: ET.Element) -> list[list[str]]:
    """Table rows on a column grid: a cell spanning columns or rows leaves empty cells where it reaches, so every
    value stays under its own header (an effect size must not slide under the next column; PR #698 review)."""
    rows, below = [], {}  # below: column -> rows a cell from an earlier row still covers
    for tr in wrap.iter("tr"):
        cells = [c for c in tr if c.tag in ("td", "th")]
        row, col = [], 0
        while cells or any(c >= col for c in below):
            if below.get(col):
                row.append("")
                below[col] -= 1
                if not below[col]:
                    del below[col]
                col += 1
                continue
            if not cells:
                row.append("")
                col += 1
                continue
            cell = cells.pop(0)
            width, height = _span(cell, "colspan", 50), _span(cell, "rowspan", 500)
            row += [_cell(cell)] + [""] * (width - 1)
            if height > 1:
                for c in range(col, col + width):
                    below[c] = height - 1
            col += width
        if row:
            rows.append(row)
    return rows


def _table(wrap: ET.Element) -> list[str]:
    label, caption = _text(wrap.find("label")), _text(wrap.find("caption"))
    lines = [f"**{label}** {caption}".strip(), ""]
    rows = _grid(wrap)
    if rows:
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines += ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    foot = _text(wrap.find("table-wrap-foot"))
    if foot:
        lines += ["", foot]
    return lines + [""]


def _section(sec: ET.Element, level: int, out: list[str], counts: dict[str, int], titles: list[str]) -> None:
    title = _text(sec.find("title"))
    if title:
        out += [f"{'#' * min(level, 6)} {title}", ""]
        if level == 2:
            titles.append(title)
    for child in sec:
        if child.tag == "p":
            _paragraph(child, out, counts)
        elif child.tag == "sec":
            _section(child, level + 1, out, counts, titles)
        elif child.tag in ("table-wrap", "fig", "list", "boxed-text", "disp-quote"):
            _block(child, out, counts)


def _paragraph(p: ET.Element, out: list[str], counts: dict[str, int]) -> None:
    """A paragraph in document order: a table or figure inside it becomes its own block where it stands, and the
    sentences before and after it (the element's tail) stay on either side (PR #698 review)."""
    parts = [p.text or ""]

    def flush() -> None:
        text = " ".join("".join(parts).split())
        if text:
            out.extend([text, ""])
        parts.clear()

    for node in p:
        if node.tag in ("table-wrap", "fig"):
            flush()
            _block(node, out, counts)
        else:
            parts.append("".join(node.itertext()))
        parts.append(node.tail or "")
    flush()


def _block(node: ET.Element, out: list[str], counts: dict[str, int]) -> None:
    if node.tag == "table-wrap":
        counts["tables"] += 1
        out += _table(node)
    elif node.tag == "fig":
        counts["figures"] += 1
        out += [f"*{_text(node.find('label'))} {_text(node.find('caption'))}*".replace("* ", "*", 1), ""]
    elif node.tag == "list":
        out += [f"- {_text(item)}" for item in node.findall("list-item")] + [""]
    else:
        out += [_text(node), ""]


def jats_markdown(xml: str, header: list[str]) -> tuple[str, dict[str, Any]]:
    """The article body as Markdown, and what it holds. Raises ValueError when the XML cannot be used."""
    if "<!ENTITY" in xml:  # internal entities are how expansion attacks start; JATS from Europe PMC has none
        raise ValueError("본문 XML에 엔티티 선언이 있어 읽지 않았습니다")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"본문 XML을 읽지 못했습니다: {exc}") from exc
    body = root.find("body")
    if body is None:
        raise ValueError("본문 XML에 body가 없습니다")
    out = list(header)
    abstract = root.find(".//front//abstract")
    if abstract is not None:
        out += ["## Abstract", "", _text(abstract), ""]
    counts, titles = {"tables": 0, "figures": 0}, []
    for child in body:
        if child.tag == "sec":
            _section(child, 2, out, counts, titles)
        elif child.tag == "p":
            _paragraph(child, out, counts)
        elif child.tag in ("table-wrap", "fig", "list"):
            _block(child, out, counts)
    # Tables and figures kept in floats-group (some publishers) instead of the body.
    floats = root.find("floats-group")
    for node in floats if floats is not None else []:
        if node.tag in ("table-wrap", "fig"):
            _block(node, out, counts)
    refs = list(root.iter("ref"))
    if refs:
        out += ["## References", ""]
        for ref in refs:
            ids = {pid.get("pub-id-type"): _text(pid) for pid in ref.iter("pub-id")}
            cite = _text(next((c for c in ref if c.tag in ("mixed-citation", "element-citation", "citation")), ref))
            extra = "; ".join(f"{k.upper()} {v}" for k, v in ids.items()
                              if k in ("pmid", "doi", "pmcid") and v and v not in cite)
            out.append(f"- {cite}" + (f" ({extra})" if extra else ""))
        out.append("")
    license_node = root.find(".//permissions/license")
    info = {"sections": titles, "n_tables": counts["tables"], "n_figures": counts["figures"],
            "n_references": len(refs), "license_text": _text(license_node)[:300] if license_node is not None else ""}
    return "\n".join(out).rstrip() + "\n", info


# ----- lookups -----

class LiteratureAnnotator(Annotator):
    async def fulltext(self, ids: object) -> dict:
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list) or not ids:
            raise ValueError("논문 식별자 목록이 비었습니다")
        items = []
        for value in ids:
            try:
                items.append(Item(parse_paper_id(value), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        valid = [item for item in items if item.error is None]
        if len(valid) > MAX_PER_CALL:
            raise ValueError(f"한 번에 {MAX_PER_CALL}편까지 받습니다({len(valid)}편을 받음). 나눠서 부르세요")
        self._from_cache(TOOL, CACHE_RELEASE, {}, items)
        version, lookup_errors = None, 0
        for item in valid:
            if item.cached_at is not None:
                continue
            try:
                item.answer, seen, item.volatile = await self._paper(item.variant)
                version = version or seen
            except LookupFailed as exc:
                item.error, lookup_errors = str(exc), lookup_errors + 1
        if self._all_failed(items, lookup_errors):
            raise LookupFailed("; ".join(item.error for item in items if item.error))
        self._to_cache(TOOL, CACHE_RELEASE, {}, items, _now())
        for item in valid:
            if item.error is None:
                item.answer = self._write_text(item.variant, item.answer)
        return self._finish(TOOL, "Europe PMC", f"REST {version}" if version else "REST (cached answers)",
                            EUROPEPMC, {}, items, _summary, cache=False,
                            note="본문 파일은 논문 라이선스를 따르는 제3자 글이다. 경로로 인용하고 결과물에 옮겨 싣지 않는다.")

    async def citation_chase(self, paper_id: object, limit: int = 100) -> dict:
        """References (backward) and citing papers (forward) for one seed publication."""
        paper = parse_paper_id(paper_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_CITATION_RESULTS:
            raise ValueError(f"limit은 1..{MAX_CITATION_RESULTS}이어야 합니다")
        options = {"limit_per_direction": limit}
        item = Item(paper, paper_id)
        items = [item]
        self._from_cache("citation_chase", CACHE_RELEASE, options, items)
        version = None
        if item.cached_at is None:
            record, version = await self._record_for(paper)
            if record is None:
                item.answer = {"found": False}
            else:
                source, ident = self._canonical_id(record)
                backward, backward_version = await self._citation_list(source, ident, "references", limit)
                forward, forward_version = await self._citation_list(source, ident, "citations", limit)
                version = version or backward_version or forward_version
                item.answer = {"found": True, "seed": _bibliography(record),
                               "backward": backward, "forward": forward}
        self._to_cache("citation_chase", CACHE_RELEASE, options, items, _now())
        return self._finish(
            "citation_chase", "Europe PMC", f"REST {version}" if version else "REST (cached answers)",
            EUROPEPMC, options, items, lambda found: dict(found.answer or {}),
            note=("backward는 참고문헌, forward는 이 논문을 인용한 논문이다. access_level_candidate는 읽을 "
                  "경로 후보이며 실제 access_level은 fulltext로 확인한다."), cache=False)

    async def preprints(self, query: object, limit: int = 25) -> dict:
        """Search bioRxiv and medRxiv records indexed by Europe PMC as SRC:PPR."""
        terms = str(query or "").strip()
        if not terms:
            raise ValueError("preprint 검색어가 비었습니다")
        if len(terms) > 500:
            raise ValueError("preprint 검색어는 500자까지 받습니다")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PREPRINT_RESULTS:
            raise ValueError(f"limit은 1..{MAX_PREPRINT_RESULTS}이어야 합니다")
        search = LiteratureSearch(terms)
        item = Item(search, terms)
        items = [item]
        options = {"limit": limit, "sources": ["bioRxiv", "medRxiv"]}
        self._from_cache("preprints", CACHE_RELEASE, options, items)
        version = None
        if item.cached_at is None:
            expression = f'SRC:PPR AND (PUBLISHER:"bioRxiv" OR PUBLISHER:"medRxiv") AND ({terms})'
            response = await self.http.request("europepmc", "GET", f"{EUROPEPMC}/search", params={
                "query": expression, "resultType": "core", "format": "json", "pageSize": limit})
            if response.status_code != 200:
                raise LookupFailed(f"Europe PMC preprint 검색이 HTTP {response.status_code}로 답했습니다")
            data = response.json()
            version = data.get("version")
            records = (data.get("resultList") or {}).get("result") or []
            candidates = []
            for record in records:
                publisher = str((record.get("bookOrReportDetails") or {}).get("publisher") or "")
                if publisher.casefold() not in {"biorxiv", "medrxiv"}:
                    continue
                candidates.append(_bibliography(record, preprint=True))
            item.answer = {"found_count": int(data.get("hitCount") or len(candidates)),
                           "returned_count": len(candidates), "candidates": candidates}
        self._to_cache("preprints", CACHE_RELEASE, options, items, _now())
        return self._finish(
            "preprints", "Europe PMC", f"REST {version}" if version else "REST (cached answers)", EUROPEPMC,
            options, items, lambda found: dict(found.answer or {}),
            note="모든 결과는 bioRxiv·medRxiv의 심사 전 preprint다. peer-reviewed 논문처럼 다루지 않는다.",
            cache=False)

    async def _record_for(self, paper: PaperId) -> tuple[dict | None, str | None]:
        response = await self.http.request("europepmc", "GET", f"{EUROPEPMC}/search", params={
            "query": paper.query(), "resultType": "core", "format": "json", "pageSize": 1})
        if response.status_code != 200:
            raise LookupFailed(f"Europe PMC 검색이 HTTP {response.status_code}로 답했습니다")
        data = response.json()
        hits = (data.get("resultList") or {}).get("result") or []
        return (hits[0] if hits else None), data.get("version")

    @staticmethod
    def _canonical_id(record: dict) -> tuple[str, str]:
        source = str(record.get("source") or "").upper()
        ident = str(record.get("id") or "")
        if not source or not ident:
            if record.get("pmid"):
                source, ident = "MED", str(record["pmid"])
            elif record.get("pmcid"):
                source, ident = "PMC", str(record["pmcid"])
        if not source or not ident:
            raise LookupFailed("Europe PMC가 인용 추적용 source와 id를 주지 않았습니다")
        return source, ident

    async def _citation_list(self, source: str, ident: str, endpoint: str, limit: int) -> tuple[dict, str | None]:
        response = await self.http.request(
            "europepmc", "GET", f"{EUROPEPMC}/{source}/{ident}/{endpoint}",
            params={"format": "json", "page": 1, "pageSize": limit})
        if response.status_code != 200:
            raise LookupFailed(f"Europe PMC {endpoint} 조회가 HTTP {response.status_code}로 답했습니다")
        data = response.json()
        if data.get("errCode") == 404:
            return {"found_count": 0, "returned_count": 0, "candidates": []}, data.get("version")
        list_key, row_key = (("referenceList", "reference") if endpoint == "references"
                             else ("citationList", "citation"))
        records = (data.get(list_key) or {}).get(row_key) or []
        return {"found_count": int(data.get("hitCount") or len(records)), "returned_count": len(records),
                "candidates": [_bibliography(record) for record in records]}, data.get("version")

    async def _paper(self, paper: PaperId) -> tuple[dict, str | None, bool]:
        """The answer, the REST version, and whether the answer must not be cached: a body that should exist but
        could not be read this time (server error, unusable XML) falls back to the abstract for this call only."""
        rec, version = await self._record_for(paper)
        if rec is None:
            return {"found": False}, version, False
        journal = ((rec.get("journalInfo") or {}).get("journal") or {}).get("title")
        meta = {"found": True, "pmid": rec.get("pmid"), "pmcid": rec.get("pmcid"), "doi": rec.get("doi"),
                "title": rec.get("title"), "journal": journal, "year": rec.get("pubYear"),
                "authors": rec.get("authorString"), "license": rec.get("license"),
                "is_open_access": rec.get("isOpenAccess") == "Y", "in_europepmc": rec.get("inEPMC") == "Y"}
        links = [{"url": u.get("url"), "availability": u.get("availability")}
                 for u in ((rec.get("fullTextUrlList") or {}).get("fullTextUrl") or []) if u.get("url")]
        header = [f"# {meta['title'] or paper.value}", "",
                  f"- PMID {meta['pmid'] or '-'} · PMCID {meta['pmcid'] or '-'} · DOI {meta['doi'] or '-'}",
                  f"- {meta['journal'] or '-'} {meta['year'] or ''} · license {meta['license'] or '-'}"]
        volatile = False
        if meta["pmcid"] and (meta["is_open_access"] or meta["in_europepmc"]):
            xml, problem = await self._fulltext_xml(meta["pmcid"])
            if xml is not None:
                try:
                    markdown, info = jats_markdown(xml, header + ["- access_level: full_text (Europe PMC)", ""])
                except ValueError as exc:
                    problem = str(exc)
                else:
                    return ({**meta, **info, "access_level": "full_text", "markdown": markdown}, version,
                            False)
            if problem:
                meta["fulltext_error"] = problem + " 이번 답은 초록 수준이고 저장하지 않으므로 다시 부르면 본문을 다시 받습니다"
                volatile = True
        abstract = " ".join((rec.get("abstractText") or "").split())
        abstract = re.sub(r"<[^>]+>", "", abstract)
        level = "abstract" if abstract else "metadata"
        if (meta["license"] or "").lower().startswith("cc") and links:
            meta["publisher_copy"] = links  # a CC license without a Europe PMC copy: the publisher may host it
        markdown = "\n".join(header + [f"- access_level: {level} (Europe PMC에 본문 사본 없음)", ""]
                             + (["## Abstract", "", abstract, ""] if abstract else [])) + "\n"
        return {**meta, "access_level": level, "markdown": markdown}, version, volatile

    async def _fulltext_xml(self, pmcid: str) -> tuple[str | None, str | None]:
        """(XML, None) for a body; (None, None) when Europe PMC says there is none (404); (None, problem) otherwise."""
        try:
            response = await self.http.request("europepmc", "GET", f"{EUROPEPMC}/{pmcid}/fullTextXML")
        except LookupFailed as exc:  # retries spent (502-504, connection): the record still has its abstract
            return None, f"본문 조회가 실패했습니다: {exc}."
        if response.status_code == 404:
            return None, None
        if response.status_code != 200:
            return None, f"본문 조회가 HTTP {response.status_code}로 실패했습니다."
        if len(response.content) > MAX_XML_BYTES:
            return None, f"본문 XML이 {MAX_XML_BYTES:,} bytes를 넘어 읽지 않았습니다."
        return response.text, None

    def _write_text(self, paper: PaperId, answer: dict) -> dict:
        """The answer without its text: the text goes to .tmp/literature/, the record keeps its path and hash."""
        answer = dict(answer)
        markdown = answer.pop("markdown", None)
        if markdown is None:
            return answer
        relative = f"{LIT_DIR}/{paper.file_name()}"
        answer.update(chars=len(markdown), text_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest())
        if self.workdir is None:
            answer["text_error"] = "LABHQ_WORKDIR가 없어 본문 파일을 쓰지 않았습니다"
            return answer
        try:
            write_owned(self.workdir, relative, markdown)
        except (OwnedPathError, OSError) as exc:
            answer["text_error"] = f"본문 파일을 쓰지 못했습니다: {exc}"
        else:
            answer["text_file"] = relative
        return answer


def _summary(item: Item) -> dict:
    answer = item.answer or {}
    if not answer.get("found"):
        return {"found": False}
    keep = ("found", "access_level", "pmid", "pmcid", "doi", "title", "journal", "year", "license", "is_open_access",
            "sections", "n_tables", "n_figures", "n_references", "chars", "text_file", "text_error", "fulltext_error",
            "publisher_copy")
    return {key: answer[key] for key in keep if answer.get(key) not in (None, "", [])}
