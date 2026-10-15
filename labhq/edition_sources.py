"""Sources a repository edition names: web links, papers, public datasets and resource versions (#607).

`SOURCES.md` used to list only ``http`` URLs, so a report that cites GSE132465 or PMID 25706627 got "no source".
This reads the report, its appendix and the bundle's ``resource_versions`` files, finds each kind of identifier, links
it to its public record, removes duplicates and groups by kind. Only identifiers and links leave the text."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from ipaddress import ip_address
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

# ASCII only: a Korean particle glued to a link ("https://x.org/a를") is not part of it. ")" and "]" end a markdown link.
_STOP = "\\s<>\"'`\\])\u0080-\U0010ffff"
_URL = re.compile(rf"https?://[^{_STOP}]+")
_DOI = re.compile(rf"(?<![A-Za-z0-9.])10\.\d{{4,9}}/[^{_STOP}]+")
_DOI_ONLY = re.compile(r"10\.\d{4,9}/\S+")
_PMID = re.compile(r"(?<![A-Za-z])PMIDs?\s*[:#]?\s*(\d{5,9}(?:\s*(?:,|/|·|&|and|및|와|과)\s*\d{5,9})*)", re.IGNORECASE)
_PMC = re.compile(r"(?<![A-Za-z0-9])PMC\d{5,9}(?![A-Za-z0-9])")
_GEO = re.compile(r"(?<![A-Za-z0-9])(?:GSE|GSM|GPL)\d{3,9}(?![A-Za-z0-9])")
_SRA = re.compile(r"(?<![A-Za-z0-9])(?:SR[RPXS]|ER[RPX]|DR[RPX])\d{5,}(?![A-Za-z0-9])")
_ARRAYEXPRESS = re.compile(r"(?<![A-Za-z0-9])E-[A-Z]{3,5}-\d+(?![A-Za-z0-9])")
_BIOPROJECT = re.compile(r"(?<![A-Za-z0-9])PRJ(?:NA|EB|DB)\d+(?![A-Za-z0-9])")
_NUMBER = re.compile(r"\d+")
_SECRET_KEY = re.compile(r"(?i)token|key|sig|secret|pass|auth|credential|session|expires")
_TRAILING = ".,;:!?*_"
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".test", ".home.arpa")
_DOI_HOSTS = {"doi.org", "dx.doi.org", "www.doi.org"}

# Order = how SOURCES.md lists them: papers, public data, then web addresses.
KINDS = (("doi", "논문 DOI"), ("pmid", "PubMed"), ("pmc", "PubMed Central"), ("geo", "GEO 데이터"),
         ("sra", "SRA 시퀀싱 기록"), ("arrayexpress", "ArrayExpress"), ("bioproject", "BioProject"),
         ("web", "웹 주소"))
_PATTERNS = (("pmc", _PMC), ("geo", _GEO), ("sra", _SRA), ("arrayexpress", _ARRAYEXPRESS), ("bioproject", _BIOPROJECT))
_GEO_ORDER = {"GSE": 0, "GSM": 1, "GPL": 2}


def record_url(kind: str, value: str) -> str:
    """The public page of an identifier."""
    if kind == "doi":
        return "https://doi.org/" + value.replace("(", "%28").replace(")", "%29")
    if kind == "pmid":
        return f"https://pubmed.ncbi.nlm.nih.gov/{value}/"
    if kind == "pmc":
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{value}/"
    if kind == "geo":
        return f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={value}"
    if kind == "sra":
        return f"https://www.ncbi.nlm.nih.gov/sra/?term={value}"
    if kind == "arrayexpress":
        return f"https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{value}"
    if kind == "bioproject":
        base = "https://www.ebi.ac.uk/ena/browser/view/" if value.startswith("PRJEB") else "https://www.ncbi.nlm.nih.gov/bioproject/"
        return base + value
    raise ValueError(kind)


def _private_host(host: str) -> bool:
    host = host.lower().strip("[]")
    try:
        address = ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(_PRIVATE_SUFFIXES)
    return not address.is_global


def _clean_doi(value: str) -> str:
    """A DOI without the query or fragment a link put after it (``10.1234/x?utm_source=y`` is DOI ``10.1234/x``)."""
    return re.split(r"[?#]", value, maxsplit=1)[0].rstrip(_TRAILING)


def _public_link(url: str) -> str | None:
    """The web address to publish: None for a private host or one with credentials, else without a credential query."""
    url = url.rstrip(_TRAILING)
    parts = urlsplit(url)
    if not parts.hostname or _private_host(parts.hostname) or parts.username or parts.password:
        return None
    return _public(url)


def _mask_urls(text: str) -> str:
    """``text`` with each link made publishable; a private one becomes a note."""
    def mask(match: re.Match) -> str:
        core = match.group().rstrip(_TRAILING)
        return (_public_link(core) or "(비공개 주소 생략)") + match.group()[len(core):]
    return _URL.sub(mask, text)


def _identity(url: str) -> tuple[str, str] | None:
    """The ``(kind, id)`` a link to a public record stands for, or None for an ordinary web address."""
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path
    if host in _DOI_HOSTS:
        doi = _clean_doi(unquote(path.strip("/")))
        return ("doi", doi) if _DOI_ONLY.fullmatch(doi) else None
    if host == "pubmed.ncbi.nlm.nih.gov":
        match = re.fullmatch(r"/(\d+)/?", path)
        return ("pmid", match.group(1)) if match else None
    if host in {"pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}:
        match = re.fullmatch(r"(?:/pmc)?/articles/(PMC\d+)/?", path)
        if match:
            return "pmc", match.group(1)
    if host == "www.ncbi.nlm.nih.gov":
        match = re.fullmatch(r"/pubmed/(\d+)/?", path)
        if match:
            return "pmid", match.group(1)
        acc = dict(parse_qsl(parts.query)).get("acc", "")
        if path == "/geo/query/acc.cgi" and _GEO.fullmatch(acc.upper()):
            return "geo", acc.upper()
    return None


def _public(url: str) -> str:
    """A web address without a query that names a credential (a signed link carries it there)."""
    parts = urlsplit(url)
    if any(_SECRET_KEY.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return url


class Sources:
    """Identifiers found so far, one list per kind, each without repeats."""

    def __init__(self) -> None:
        self.found: dict[str, dict[str, str]] = {kind: {} for kind, _ in KINDS}  # normalized key -> as written
        self.versions: dict[tuple[str, str], str] = {}  # (resource, version) -> source link or ""

    def add(self, kind: str, value: str) -> None:
        value = value.strip()
        if value:
            key = value if kind == "web" else value.lower() if kind == "doi" else value.upper()
            self.found[kind].setdefault(key, value)

    def add_url(self, url: str) -> None:
        link = _public_link(url)
        if link is None:
            return
        identity = _identity(link)
        if identity:
            self.add(*identity)
            return
        self.add("web", link)
        for match in _DOI.finditer(unquote(urlsplit(link).path)):  # a DOI inside a link comes from its path alone
            self.add("doi", _clean_doi(match.group()))

    def scan(self, text: str) -> None:
        """Every identifier in free text."""
        for match in _URL.finditer(text):
            self.add_url(match.group())
        for match in _DOI.finditer(_URL.sub(" ", text)):  # links were read above; a link's query is no part of a DOI
            self.add("doi", _clean_doi(match.group()))
        for match in _PMID.finditer(text):
            for number in _NUMBER.findall(match.group(1)):
                self.add("pmid", number)
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                self.add(kind, match.group())

    def add_reference(self, kind: str, value: str) -> None:
        """A reference the PI attached to the request (intake has already normalized it)."""
        if kind == "pmid":
            self.add("pmid", "".join(_NUMBER.findall(value)))
        elif kind == "doi":
            match = _DOI_ONLY.search(value)
            if match:
                self.add("doi", _clean_doi(match.group()))
        elif kind in {"url", "github"}:
            self.scan(value)

    def scan_versions(self, text: str) -> None:
        """``resource -> version`` rows of a ``resource_versions`` file, and the links beside them."""
        for name, version, source in parse_versions(text):
            link = (_public_link(source) if source else None) or ""  # a private or credentialed source is dropped
            self.versions[(name, version)] = self.versions.get((name, version)) or link

    def listing(self, kind: str) -> list[str]:
        values = list(self.found[kind].values())
        if kind == "geo":
            return sorted(values, key=lambda v: (_GEO_ORDER[v[:3]], int(v[3:])))
        if kind in {"pmid", "pmc"}:
            return sorted(values, key=lambda v: int(_NUMBER.search(v).group()))
        return values if kind == "web" else sorted(values, key=str.lower)

    def empty(self) -> bool:
        return not self.versions and not any(self.found.values())


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        return [cell.strip() for cell in line.strip("|").split("|")]
    return [cell.strip() for cell in line.split("\t")] if "\t" in line else []


def _plain(cell: str) -> str:
    return _mask_urls(" ".join(re.sub(r"`|\*\*", "", cell).split()))


def _column(header: list[str], pattern: str) -> int | None:
    return next((i for i, cell in enumerate(header) if re.search(pattern, cell)), None)  # "resource" is not a source


def parse_versions(text: str) -> list[tuple[str, str, str]]:
    """``(resource, version, source link)`` from the tables of a versions record (markdown or TSV).

    A table qualifies when a header cell says version; its first column names the resource and a header cell that says
    source or 출처 gives the link. A bullet ``- name: 1.2.3`` is read as well. Rows with no version are skipped."""
    rows: list[tuple[str, str, str]] = []
    header: list[str] | None = None  # None: not in a table; []: in a table that has no version column
    version = source = None
    for raw in text.lstrip("\ufeff").splitlines():
        cells = _cells(raw)
        if len(cells) >= 2:
            if header is None:
                lowered = [cell.lower() for cell in cells]
                version = _column(lowered, r"\bversion|버전")
                source = _column(lowered, r"\bsource|출처")
                header = lowered if version is not None else []
            elif header and not all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
                if version < len(cells) and cells[0] and cells[version]:
                    link = _URL.search(cells[source]) if source is not None and source < len(cells) else None
                    rows.append((_plain(cells[0]), _plain(cells[version]), link.group().rstrip(_TRAILING) if link else ""))
            continue
        header = None
        bullet = re.fullmatch(r"\s*[-*]\s+([A-Za-z][\w .+/-]{0,40}?)\s*(?::|=|==)\s*(v?\d[\w.+-]*)\s*", raw)
        if bullet:
            rows.append((bullet.group(1).strip(), bullet.group(2), ""))
    return rows


def is_versions_file(name: str) -> bool:
    return "resource_versions" in name.lower().rsplit("/", 1)[-1]


def collect(texts: Iterable[str], references: Iterable[Mapping] = (), versions: Iterable[str] = ()) -> Sources:
    """Sources of the report texts, the request's references and the bundle's ``resource_versions`` files."""
    found = Sources()
    for ref in references:
        if isinstance(ref, Mapping):
            found.add_reference(str(ref.get("kind") or ""), str(ref.get("value") or ""))
    for text in texts:
        found.scan(text)
    for text in versions:
        found.scan(text)  # the links of a versions record are sources too
        found.scan_versions(text)
    return found


def render(found: Sources, citations: str = "") -> str:
    """The body of SOURCES.md."""
    parts = ["# 출처·인용"]
    for kind, title in KINDS:
        values = found.listing(kind)
        if kind == "web":
            lines = [f"- {value}" for value in values]
        else:
            lines = [f"- [{'PMID ' if kind == 'pmid' else ''}{value}]({record_url(kind, value)})" for value in values]
        if lines:
            parts.append(f"## {title}\n\n" + "\n".join(lines))
    if found.versions:
        lines = [f"- {name}: {version}" + (f" ({source})" if source else "")
                 for (name, version), source in found.versions.items()]
        parts.append("## 소프트웨어·자료 버전\n\n" + "\n".join(lines))
    if citations.strip():
        parts.append("## 보고서 문헌\n\n" + citations.strip())
    if len(parts) == 1:
        parts.append("출처를 찾지 못했습니다. 보고서의 인용을 확인하세요.")
    return "\n\n".join(parts) + "\n"
