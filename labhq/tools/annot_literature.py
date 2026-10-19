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

import asyncio
import hashlib
import io
import ipaddress
import re
import socket
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from ..adapters.owned import OwnedPathError, write_owned
from .annot import Annotator, Item, LookupFailed, ResponseTooLarge, _now

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
UNPAYWALL = "https://api.unpaywall.org/v2"
LIT_DIR = ".tmp/literature"  # never outputs/: see the module docstring
TOOL = "fulltext"
CACHE_RELEASE = "europepmc-rest"  # Europe PMC has no data release; answers age out instead (annot.CACHE_MAX_AGE_S)
FULLTEXT_CACHE_RELEASE = "europepmc-rest-oa-v2"
MAX_PER_CALL = 20
MAX_XML_BYTES = 20_000_000
MAX_CITATION_RESULTS = 1000
MAX_PREPRINT_RESULTS = 100
MAX_OPENALEX_RESULTS = 200
MAX_DOI_RESULTS = 50
MAX_OA_COPY_BYTES = 25_000_000
MAX_PDF_EXPANDED_BYTES = 50_000_000
MAX_OA_REDIRECTS = 5

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


def _url_tail(value: object, marker: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lower = text.casefold()
    index = lower.rfind(marker.casefold())
    return text[index + len(marker):].strip("/ ") if index >= 0 else text


def _oa_location(value: object, *, openalex: bool = False) -> dict | None:
    if not isinstance(value, dict):
        return None
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    out = {
        "landing_page_url": value.get("landing_page_url") if openalex else value.get("url"),
        "pdf_url": value.get("pdf_url") if openalex else value.get("url_for_pdf"),
        "source": source.get("display_name") if openalex else value.get("host_type"),
        "host_type": None if openalex else value.get("host_type"),
        "license": value.get("license"),
        "version": value.get("version"),
    }
    compact = {key: val for key, val in out.items() if val not in (None, "")}
    return compact or None


def _openalex_oa_locations(record: dict) -> list[dict]:
    """Normalized legal-copy candidates; closed locations are never requested."""
    locations = []
    for row in record.get("locations") or []:
        if not isinstance(row, dict) or row.get("is_oa") is not True:
            continue
        location = _oa_location(row, openalex=True)
        if location:
            locations.append(location)
    return locations


def _openalex_candidate(record: dict) -> dict:
    ids = record.get("ids") if isinstance(record.get("ids"), dict) else {}
    openalex_id = _url_tail(record.get("id"), "openalex.org/")
    doi = _url_tail(record.get("doi") or ids.get("doi"), "doi.org/")
    pmid = _url_tail(ids.get("pmid"), "pubmed.ncbi.nlm.nih.gov/")
    access = record.get("open_access") if isinstance(record.get("open_access"), dict) else {}
    references = [_url_tail(value, "openalex.org/") for value in record.get("referenced_works") or []]
    authors = [str((row.get("author") or {}).get("display_name")) for row in record.get("authorships") or []
               if isinstance(row, dict) and (row.get("author") or {}).get("display_name")]
    out = {
        "openalex_id": openalex_id,
        "doi": doi.casefold() if doi else None,
        "pmid": pmid,
        "title": record.get("title") or record.get("display_name"),
        "authors": authors,
        "year": record.get("publication_year"),
        "type": record.get("type"),
        "is_oa": bool(access.get("is_oa")),
        "oa_status": access.get("oa_status"),
        "oa_url": access.get("oa_url"),
        "best_oa_location": _oa_location(record.get("best_oa_location"), openalex=True),
        "cited_by_count": int(record.get("cited_by_count") or 0),
        "cited_by_url": record.get("cited_by_api_url") or (
            f"{OPENALEX}/works?filter=cites:{openalex_id}" if openalex_id else None),
        "references": [value for value in references if value],
    }
    return {key: value for key, value in out.items() if value not in (None, "", [])}


def _crossref_answer(message: dict) -> dict:
    def first(name: str) -> object:
        value = message.get(name)
        return value[0] if isinstance(value, list) and value else value

    licenses = []
    for row in message.get("license") or []:
        if not isinstance(row, dict):
            continue
        start = row.get("start") if isinstance(row.get("start"), dict) else {}
        licenses.append({key: value for key, value in {
            "url": row.get("URL"), "start": start.get("date-time"), "delay_days": row.get("delay-in-days"),
            "content_version": row.get("content-version"),
        }.items() if value not in (None, "")})
    updates = []
    for row in message.get("update-to") or []:
        if not isinstance(row, dict):
            continue
        updated = row.get("updated") if isinstance(row.get("updated"), dict) else {}
        updates.append({key: value for key, value in {
            "doi": str(row.get("DOI") or "").casefold() or None,
            "type": row.get("type"), "label": row.get("label"), "updated": updated.get("date-time"),
        }.items() if value not in (None, "")})
    update_types = {str(row.get("type") or "").casefold() for row in updates}
    kind = str(message.get("type") or "").casefold()
    subtype = str(message.get("subtype") or "").casefold()
    return {key: value for key, value in {
        "found": True,
        "doi": str(message.get("DOI") or "").casefold() or None,
        "title": first("title"),
        "type": message.get("type"),
        "subtype": message.get("subtype"),
        "publisher": message.get("publisher"),
        "journal": first("container-title"),
        "version": message.get("content-version") or message.get("version"),
        "licenses": licenses,
        "updates": updates,
        "relations": message.get("relation") or {},
        "retracted": "retraction" in kind or "retraction" in subtype or "retraction" in update_types,
    }.items() if value not in (None, "", [], {})}


def _unpaywall_answer(data: dict) -> dict:
    locations = [_oa_location(row) for row in data.get("oa_locations") or []]
    return {key: value for key, value in {
        "found": True,
        "doi": str(data.get("doi") or "").casefold() or None,
        "title": data.get("title"),
        "genre": data.get("genre"),
        "published_date": data.get("published_date"),
        "is_oa": bool(data.get("is_oa")),
        "oa_status": data.get("oa_status"),
        "best_oa_location": _oa_location(data.get("best_oa_location")),
        "oa_locations": [row for row in locations if row],
    }.items() if value not in (None, "", [])}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def _pdf_unescape(value: bytes) -> str:
    value = re.sub(rb"\\([0-7]{1,3})", lambda match: bytes([int(match.group(1), 8)]), value)
    value = re.sub(rb"\\([nrtbf])", lambda match: {b"n": b"\n", b"r": b"\r", b"t": b"\t",
                                                        b"b": b"\b", b"f": b"\f"}[match.group(1)], value)
    value = re.sub(rb"\\([()\\])", rb"\1", value)
    return value.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    """Extract ordinary PDF text without adding a package. pypdf is used when already installed; the bounded
    fallback handles literal Tj/TJ strings in plain or FlateDecode streams and otherwise reports format failure."""
    chunks = [data]
    expanded_bytes = 0
    for match in re.finditer(rb"(?s)(<<.{0,2000}?>>)?\s*stream\r?\n(.*?)\r?\nendstream", data):
        block, stream = match.group(1) or b"", match.group(2)
        if b"/FlateDecode" in block:
            try:
                decompressor = zlib.decompressobj()
                stream = decompressor.decompress(stream, MAX_PDF_EXPANDED_BYTES - expanded_bytes + 1)
            except zlib.error:
                continue
            expanded_bytes += len(stream)
            if expanded_bytes > MAX_PDF_EXPANDED_BYTES:
                raise ValueError(
                    f"PDF 압축 해제 결과가 전체 {MAX_PDF_EXPANDED_BYTES:,} bytes를 넘어 읽지 않았습니다")
        chunks.append(stream)

    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        except Exception:  # noqa: BLE001 - the bounded fallback may still read a simple PDF
            text = ""
        if text.strip():
            return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    pieces = []
    literal = rb"\(((?:\\.|[^\\()])*)\)"
    for chunk in chunks:
        pieces.extend(_pdf_unescape(match.group(1)) for match in re.finditer(literal + rb"\s*Tj\b", chunk))
        for array in re.finditer(rb"(?s)\[(.*?)\]\s*TJ\b", chunk):
            pieces.extend(_pdf_unescape(match.group(1)) for match in re.finditer(literal, array.group(1)))
    text = " ".join(" ".join(pieces).split())
    if not text:
        raise ValueError("PDF에서 텍스트를 추출하지 못했습니다")
    compact = "".join(text.split())
    folded = text.casefold()
    sections = sum(term in folded for term in ("introduction", "method", "result", "discussion", "references"))
    if len(compact) < 1000 or sections < 2:
        raise ValueError("PDF fallback이 논문 본문 구조를 충분히 확인하지 못했습니다")
    return text


def _copy_text(content: bytes, content_type: str) -> tuple[str, str]:
    if content.startswith(b"%PDF") or "application/pdf" in content_type:
        return _pdf_text(content), "PDF"
    if "html" in content_type or content.lstrip().startswith((b"<!DOCTYPE html", b"<html", b"<HTML")):
        parser = _HTMLText()
        parser.feed(content.decode("utf-8", errors="replace"))
        text = "\n".join(parser.parts)
        folded = text.casefold()
        if any(term in folded for term in ("access denied", "checking your browser", "captcha")):
            raise PermissionError("OA 주소가 접근 거부 화면을 돌려줬습니다")
        if any(term in folded for term in ("subscribe to read", "purchase access", "institutional login")):
            raise LookupFailed("OA 주소가 구독·로그인 화면을 돌려줬습니다")
        compact = "".join(text.split())
        sections = sum(term in folded for term in ("introduction", "method", "result", "discussion", "references"))
        if len(compact) < 1000 or sections < 2:
            raise ValueError("HTML이 논문 본문 구조를 갖추지 않아 추출하지 않았습니다")
        return text, "HTML"
    raise ValueError(f"지원하지 않는 본문 형식입니다({content_type or 'content-type 없음'})")


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
    def __init__(self, *args: Any, unpaywall_email: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.unpaywall_email = str(unpaywall_email or "").strip()

    @staticmethod
    def _doi_items(values: object) -> list[Item]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not values:
            raise ValueError("DOI 목록이 비었습니다")
        if len(values) > MAX_DOI_RESULTS:
            raise ValueError(f"한 번에 DOI {MAX_DOI_RESULTS}개까지 받습니다")
        items = []
        for value in values:
            try:
                paper = parse_paper_id(value)
                if paper.kind != "doi":
                    raise ValueError("DOI가 아닙니다")
                items.append(Item(paper, value))
            except ValueError as exc:
                items.append(Item(None, value, error=f"DOI를 읽지 못했습니다: {exc}"))
        return items

    async def openalex_works(self, query: object, filters: object = None, limit: int = 25) -> dict:
        """Broad works search. OA fields describe possible access locations and never filter candidates."""
        terms = str(query or "").strip()
        if not terms:
            raise ValueError("OpenAlex 검색어가 비었습니다")
        if len(terms) > 500 or "\n" in terms or "\r" in terms:
            raise ValueError("OpenAlex 검색어는 줄바꿈 없이 500자까지 받습니다")
        if filters is None:
            filter_rows: list[str] = []
        elif isinstance(filters, list) and len(filters) <= 20:
            filter_rows = [str(value).strip() for value in filters]
            if any(not value or len(value) > 200 or "\n" in value or "\r" in value or "," in value
                   for value in filter_rows):
                raise ValueError("OpenAlex filter는 쉼표·줄바꿈 없이 항목마다 200자까지 받습니다")
        else:
            raise ValueError("OpenAlex filters는 문자열 목록(최대 20개)이어야 합니다")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_OPENALEX_RESULTS:
            raise ValueError(f"limit은 1..{MAX_OPENALEX_RESULTS}이어야 합니다")
        options = {"filters": filter_rows, "limit": limit}
        item = Item(LiteratureSearch(terms), terms)
        items = [item]
        self._from_cache("openalex_works", "openalex-api", options, items)
        if item.cached_at is None:
            params = {"search": terms, "per-page": limit}
            if filter_rows:
                params["filter"] = ",".join(filter_rows)
            response = await self.http.request("openalex", "GET", f"{OPENALEX}/works", params=params)
            if response.status_code != 200:
                raise LookupFailed(f"OpenAlex works 검색이 HTTP {response.status_code}로 답했습니다")
            data = response.json()
            records = data.get("results") or []
            item.answer = {"found_count": int((data.get("meta") or {}).get("count") or len(records)),
                           "returned_count": len(records),
                           "candidates": [_openalex_candidate(row) for row in records if isinstance(row, dict)]}
        return self._finish(
            "openalex_works", "OpenAlex", "API (30-day cache)", OPENALEX, options, items,
            lambda found: dict(found.answer or {}),
            note=("OA 여부는 후보 제외 조건이 아니다. best_oa_location은 합법 사본 후보이며 실제 확보 수준은 "
                  "fulltext로 확인한다."))

    async def crossref_doi(self, dois: object) -> dict:
        items = self._doi_items(dois)
        self._from_cache("crossref_doi", "crossref-rest", {}, items)
        lookup_errors = 0
        for item in items:
            if item.error is not None or item.cached_at is not None:
                continue
            try:
                response = await self.http.request(
                    "crossref", "GET", f"{CROSSREF}/works/{quote(item.variant.value, safe='')}")
                if response.status_code == 404:
                    item.answer = {"found": False}
                elif response.status_code != 200:
                    raise LookupFailed(f"Crossref DOI 조회가 HTTP {response.status_code}로 답했습니다")
                else:
                    item.answer = _crossref_answer(response.json().get("message") or {})
            except LookupFailed as exc:
                item.error, lookup_errors = str(exc), lookup_errors + 1
        if self._all_failed(items, lookup_errors):
            raise LookupFailed("; ".join(item.error for item in items if item.error))
        return self._finish(
            "crossref_doi", "Crossref", "REST API (30-day cache)", CROSSREF, {}, items,
            lambda found: dict(found.answer or {}),
            note="version·license·update-to와 철회 표시는 Crossref 등록 메타데이터이며 원문 확보 여부와 다르다.")

    async def unpaywall(self, dois: object) -> dict:
        items = self._doi_items(dois)
        if not self.unpaywall_email or "@" not in self.unpaywall_email:
            reason = "annot.unpaywall_email 설정이 없어 Unpaywall 조회가 비활성입니다"
            for item in items:
                if item.error is None:
                    item.answer = {"enabled": False, "reason": reason}
            out = self._finish(
                "unpaywall", "Unpaywall", "disabled", UNPAYWALL, {}, items,
                lambda found: dict(found.answer or {}), cache=False,
                note="email 값은 요청에만 쓰며 조회 기록·결과·캐시 키에 남기지 않는다.")
            out.update(enabled=False, disabled_reason=reason)
            return out
        self._from_cache("unpaywall", "unpaywall-v2", {}, items)
        lookup_errors = 0
        for item in items:
            if item.error is not None or item.cached_at is not None:
                continue
            try:
                response = await self.http.request(
                    "unpaywall", "GET", f"{UNPAYWALL}/{quote(item.variant.value, safe='')}",
                    params={"email": self.unpaywall_email})
                if response.status_code == 404:
                    item.answer = {"found": False}
                elif response.status_code != 200:
                    raise LookupFailed(f"Unpaywall DOI 조회가 HTTP {response.status_code}로 답했습니다")
                else:
                    item.answer = _unpaywall_answer(response.json())
            except LookupFailed as exc:
                item.error, lookup_errors = str(exc), lookup_errors + 1
        if self._all_failed(items, lookup_errors):
            raise LookupFailed("; ".join(item.error for item in items if item.error))
        out = self._finish(
            "unpaywall", "Unpaywall", "API v2 (30-day cache)", UNPAYWALL, {}, items,
            lambda found: dict(found.answer or {}),
            note="합법 OA 위치 후보이며 실제로 읽은 수준은 fulltext 결과로 확인한다. email은 기록하지 않는다.")
        out["enabled"] = True
        return out

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
        options = {"unpaywall_enabled": bool(self.unpaywall_email and "@" in self.unpaywall_email)}
        self._from_cache(TOOL, FULLTEXT_CACHE_RELEASE, options, items)
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
        self._to_cache(TOOL, FULLTEXT_CACHE_RELEASE, options, items, _now())
        for item in valid:
            if item.error is None:
                item.answer = self._write_text(item.variant, item.answer)
        return self._finish(TOOL, "Europe PMC",
                            f"REST {version}" if version else "REST (cached answers)",
                            EUROPEPMC, options, items, _summary, cache=False,
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
            meta = {
                "found": True,
                "pmid": paper.value if paper.kind == "pmid" else None,
                "pmcid": paper.value if paper.kind == "pmcid" else None,
                "doi": paper.value if paper.kind == "doi" else None,
                "title": None,
                "journal": None,
                "year": None,
                "authors": None,
                "license": None,
                "is_open_access": False,
                "in_europepmc": False,
            }
            header = [f"# {paper.value}", "", self._identifier_line(meta), "- Europe PMC record: -"]
            oa_body, oa_state = await self._legal_oa_copy(paper, meta, header)
            if oa_body is not None:
                return ({**meta, **oa_state, "access_level": "full_text", "markdown": oa_body}, version, False)
            answer = {**meta, "found": False, "unread_reason": oa_state.get("unread_reason") or "no_copy"}
            if oa_state.get("unread_detail"):
                answer["unread_detail"] = oa_state["unread_detail"]
            if oa_state.get("oa_locations"):
                answer["oa_locations"] = oa_state["oa_locations"]
            if oa_state.get("temporary_error"):
                answer["fulltext_error"] = oa_state["temporary_error"]
            return answer, version, bool(oa_state.get("temporary_error"))
        journal = ((rec.get("journalInfo") or {}).get("journal") or {}).get("title")
        meta = {"found": True, "pmid": rec.get("pmid"), "pmcid": rec.get("pmcid"), "doi": rec.get("doi"),
                "title": rec.get("title"), "journal": journal, "year": rec.get("pubYear"),
                "authors": rec.get("authorString"), "license": rec.get("license"),
                "is_open_access": rec.get("isOpenAccess") == "Y", "in_europepmc": rec.get("inEPMC") == "Y"}
        links = [{"url": u.get("url"), "availability": u.get("availability")}
                 for u in ((rec.get("fullTextUrlList") or {}).get("fullTextUrl") or []) if u.get("url")]
        subscription_hint = any("subscription" in str(link.get("availability") or "").casefold() for link in links)
        header = [f"# {meta['title'] or paper.value}", "", self._identifier_line(meta),
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
        oa_body, oa_state = await self._legal_oa_copy(paper, meta, header)
        if oa_body is not None:
            return ({**meta, **oa_state, "access_level": "full_text", "markdown": oa_body}, version, False)
        if oa_state.get("temporary_error"):
            volatile = True
            previous = meta.get("fulltext_error")
            meta["fulltext_error"] = "; ".join(value for value in (previous, oa_state["temporary_error"]) if value)
        unread_reason = oa_state.get("unread_reason")
        if not unread_reason:
            if subscription_hint or oa_state.get("closed"):
                unread_reason = "subscription_required"
            elif meta["is_open_access"] or meta["in_europepmc"] or oa_state.get("oa_known"):
                unread_reason = "no_copy"
            else:
                unread_reason = "no_copy"
        meta["unread_reason"] = unread_reason
        meta["unread_detail"] = oa_state.get("unread_detail") or {
            "subscription_required": "합법 OA 사본 위치를 찾지 못했고 구독이 필요한 논문입니다",
            "no_copy": "OA로 표시됐지만 읽을 수 있는 합법 사본 위치가 없습니다",
            "access_denied": "합법 OA 사본 주소가 접근을 거부했습니다",
            "format_failure": "합법 OA 사본을 받았지만 본문 형식을 읽지 못했습니다",
        }.get(unread_reason, "본문을 확보하지 못했습니다")
        if oa_state.get("oa_locations"):
            meta["oa_locations"] = oa_state["oa_locations"]
        abstract = " ".join((rec.get("abstractText") or "").split())
        abstract = re.sub(r"<[^>]+>", "", abstract)
        level = "abstract" if abstract else "metadata"
        if (meta["license"] or "").lower().startswith("cc") and links:
            meta["publisher_copy"] = links  # a CC license without a Europe PMC copy: the publisher may host it
        markdown = "\n".join(header + [f"- access_level: {level} (Europe PMC에 본문 사본 없음)", ""]
                             + (["## Abstract", "", abstract, ""] if abstract else [])) + "\n"
        return {**meta, "access_level": level, "markdown": markdown}, version, volatile

    @staticmethod
    def _identifier_line(meta: dict) -> str:
        return f"- PMID {meta.get('pmid') or '-'} · PMCID {meta.get('pmcid') or '-'} · DOI {meta.get('doi') or '-'}"

    @staticmethod
    async def _require_public_oa_url(url: str) -> None:
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
        except ValueError as exc:
            raise PermissionError("OA 사본 주소 형식이 올바르지 않습니다") from exc
        if parts.scheme.casefold() not in {"http", "https"} or not host or parts.username or parts.password:
            raise PermissionError("OA 사본 주소가 공개 HTTP(S) URL이 아닙니다")
        try:
            addresses = {ipaddress.ip_address(host)}
        except ValueError:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host, port or (443 if parts.scheme.casefold() == "https" else 80),
                    type=socket.SOCK_STREAM)
            except OSError as exc:
                raise PermissionError(f"OA 사본 host를 확인하지 못했습니다: {host}") from exc
            addresses = {ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos}
        if not addresses or any(not address.is_global for address in addresses):
            raise PermissionError(f"OA 사본 주소가 public network가 아닙니다: {host}")

    async def _request_oa_copy(self, url: str, headers: dict[str, str]):
        current = url
        for redirect_count in range(MAX_OA_REDIRECTS + 1):
            await self._require_public_oa_url(current)
            response = await self.http.request(
                "oa_copy", "GET", current, headers=headers, follow_redirects=False,
                response_limit=MAX_OA_COPY_BYTES)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            if redirect_count == MAX_OA_REDIRECTS:
                raise LookupFailed(f"OA 사본 주소가 redirect {MAX_OA_REDIRECTS}회를 넘었습니다")
            current = urljoin(current, location)
        raise AssertionError("unreachable")

    async def _legal_oa_copy(self, paper: PaperId, meta: dict, header: list[str]) -> tuple[str | None, dict]:
        """Try legal OA locations only. Provider/API failures are transient; an absent location or a 404 copy is a
        stable access outcome. The contact email is never returned or recorded."""
        locations: list[dict] = []
        state: dict[str, Any] = {"oa_known": False, "oa_locations": []}
        temporary = []
        doi = str(meta.get("doi") or "").strip().casefold()

        if doi and self.unpaywall_email and "@" in self.unpaywall_email:
            try:
                response = await self.http.request(
                    "unpaywall", "GET", f"{UNPAYWALL}/{quote(doi, safe='')}", params={"email": self.unpaywall_email})
                if response.status_code == 200:
                    answer = _unpaywall_answer(response.json())
                    state["oa_known"] = state["oa_known"] or bool(answer.get("is_oa"))
                    state["closed"] = state.get("closed", False) or answer.get("oa_status") == "closed"
                    for row in [answer.get("best_oa_location"), *(answer.get("oa_locations") or [])]:
                        if isinstance(row, dict):
                            locations.append({"provider": "Unpaywall", **row})
                elif response.status_code != 404:
                    temporary.append(f"Unpaywall 조회 HTTP {response.status_code}")
            except LookupFailed as exc:
                temporary.append(f"Unpaywall 조회 실패: {exc}")

        filter_value = f"doi:{doi}" if doi else f"{paper.kind}:{paper.value}"
        try:
            response = await self.http.request(
                "openalex", "GET", f"{OPENALEX}/works", params={"filter": filter_value, "per-page": 1})
            if response.status_code == 200:
                records = response.json().get("results") or []
                if records:
                    record = records[0]
                    candidate = _openalex_candidate(record)
                    state["oa_known"] = state["oa_known"] or bool(candidate.get("is_oa"))
                    for location in [candidate.get("best_oa_location"), *_openalex_oa_locations(record)]:
                        if isinstance(location, dict):
                            locations.append({"provider": "OpenAlex", **location})
            else:
                temporary.append(f"OpenAlex 조회 HTTP {response.status_code}")
        except LookupFailed as exc:
            temporary.append(f"OpenAlex 조회 실패: {exc}")

        unique = []
        seen = set()
        for location in locations:
            for key in ("pdf_url", "landing_page_url"):
                url = str(location.get(key) or "").strip()
                if not url or url in seen or not url.casefold().startswith(("https://", "http://")):
                    continue
                seen.add(url)
                unique.append({"provider": location["provider"], "url": url, "kind": key,
                               **{name: location[name] for name in ("source", "license", "version")
                                  if location.get(name)}})
        unique.sort(key=lambda row: row["kind"] != "pdf_url")
        state["oa_locations"] = unique[:20]

        failures: list[tuple[str, str]] = []
        for location in unique:
            try:
                response = await self._request_oa_copy(
                    location["url"],
                    {"Accept": "application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"})
            except PermissionError as exc:
                failures.append(("access_denied", f"{location['provider']} 주소 차단: {exc}"))
                continue
            except ResponseTooLarge:
                failures.append(("format_failure", f"본문이 {MAX_OA_COPY_BYTES:,} bytes를 넘어 읽지 않음"))
                continue
            except LookupFailed as exc:
                temporary.append(f"{location['provider']} 사본 조회 실패: {exc}")
                continue
            if response.status_code in {401, 403, 451}:
                failures.append(("access_denied", f"{location['provider']} 주소가 HTTP {response.status_code}로 거부됨"))
                continue
            if response.status_code == 404:
                failures.append(("no_copy", f"{location['provider']} 주소에 사본 없음(HTTP 404)"))
                continue
            if 500 <= response.status_code < 600:
                temporary.append(f"{location['provider']} 사본 조회 HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                failures.append(("access_denied", f"{location['provider']} 주소가 HTTP {response.status_code}로 답함"))
                continue
            try:
                text, body_format = _copy_text(response.content, response.headers.get("content-type", "").casefold())
            except PermissionError as exc:
                failures.append(("access_denied", str(exc)))
                continue
            except LookupFailed as exc:
                failures.append(("subscription_required", str(exc)))
                continue
            except ValueError as exc:
                failures.append(("format_failure", str(exc)))
                continue
            markdown = "\n".join(header + [
                f"- access_level: full_text ({location['provider']} legal OA {body_format})",
                f"- source_url: {location['url']}", "", "## Full text", "", text, "",
            ])
            copy = {key: value for key, value in location.items() if key != "kind"}
            return markdown, {"read_source": location["provider"], "oa_copy": copy}

        if temporary:
            state["temporary_error"] = "; ".join(temporary)
        if failures:
            priority = {"access_denied": 0, "format_failure": 1, "subscription_required": 2, "no_copy": 3}
            code, detail = min(failures, key=lambda row: priority[row[0]])
            state.update(unread_reason=code, unread_detail=detail)
        return None, state

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
        keep = ("found", "pmid", "pmcid", "doi", "oa_locations", "unread_reason", "unread_detail",
                "fulltext_error")
        return {key: answer[key] for key in keep if answer.get(key) not in (None, "", [])}
    keep = ("found", "access_level", "pmid", "pmcid", "doi", "title", "journal", "year", "license", "is_open_access",
            "sections", "n_tables", "n_figures", "n_references", "chars", "text_file", "text_error", "fulltext_error",
            "publisher_copy", "read_source", "oa_copy", "oa_locations", "unread_reason", "unread_detail")
    return {key: answer[key] for key in keep if answer.get(key) not in (None, "", [])}
