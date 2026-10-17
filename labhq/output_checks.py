"""Deterministic checks of what a step left behind, shown as report warnings (#697, #710).

Table form (#710): in the 10-17 trial `mechanism_literature.tsv` separated its columns with the two characters
``\\t`` instead of a tab. Nothing read it as a table, so its access_level column could not be counted, yet the
step passed. The runner now checks each collected .tsv/.tab/.csv output for a separator that is not there and for
rows whose column count differs from the header.

Literature depth (#697): staff read papers through ``labhq_annot.fulltext``, which records every lookup in
``outputs/annotation_queries.jsonl`` and the answer, with its ``access_level``, in ``outputs/annotation/``. The
runner reads those records back as {paper id: best level}; the gateway merges the steps and writes one line with
how many papers were read in full, as abstract only, or as bibliographic record only. The count covers papers
looked up through that tool, not every paper the report cites.

Both run on the runner's disk after the task and return short Korean lines; they never put file content into a
prompt or a record.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .evidence.report_check import claim_rows

TABLE_SEPARATORS = {".tsv": "\t", ".tab": "\t", ".csv": ","}
MAX_TABLE_BYTES = 4_000_000  # enough rows to see the shape; a larger table is checked on its first 4 MB
MAX_TABLE_WARNINGS = 20
# Python's default field limit (131,072) rejects a valid CSV with one long field (a sequence, a JSON cell).
CSV_FIELD_LIMIT = MAX_TABLE_BYTES
csv.field_size_limit(max(csv.field_size_limit(), CSV_FIELD_LIMIT))
QUERY_LOG = "outputs/annotation_queries.jsonl"
FULLTEXT_TOOL = "fulltext"
MAX_RECORD_BYTES = 5_000_000
MAX_PAPERS = 1000
LEVELS = ("full_text", "abstract", "metadata")  # best first
LEVEL_NAMES = {"full_text": "본문", "abstract": "초록", "metadata": "서지만"}
PMCID = re.compile(r"\bPMC\d+\b", re.IGNORECASE)
PMID = re.compile(r"\bPMID\s*[:=]?\s*(\d+)\b", re.IGNORECASE)
PUBMED_URL = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov/pubmed)/(\d+)", re.IGNORECASE)
DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
DOI_VALUE = re.compile(r"10\.\d{1,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)  # old records allowed short registrants
PAPER_ALIAS_SEPARATOR = "|"  # not valid in the identifiers above; old one-identifier keys remain valid
PAPER_ID_ORDER = {"PMID": 0, "PMCID": 1, "DOI": 2, "input": 3}


def _inside(root: Path, rel: str) -> Path | None:
    try:
        path = (root / rel).resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def _head_text(path: Path) -> str:
    with open(path, "rb") as handle:
        data = handle.read(MAX_TABLE_BYTES + 1)
    text = data[:MAX_TABLE_BYTES].decode("utf-8-sig", errors="replace")
    if len(data) > MAX_TABLE_BYTES and "\n" in text:
        text = text[:text.rindex("\n")]  # a cut last line is not a short row
    return text


def table_problem(path: Path) -> str | None:
    """One Korean sentence when a table output cannot be read as a table, else None."""
    separator = TABLE_SEPARATORS.get(path.suffix.lower())
    if separator is None:
        return None
    try:
        text = _head_text(path)
    except OSError as exc:
        return f"읽지 못했습니다({type(exc).__name__})"
    # A "##" metadata line may hold a tab. A one-column TSV may hold an escaped "\t" in a cell: the warning needs a
    # header and every row split by the two characters, as in the 10-17 table (PR #712 review).
    body = [line for line in text.splitlines() if line.strip() and not line.startswith("##")]
    if (separator == "\t" and len(body) >= 2 and not any("\t" in line for line in body)
            and all("\\t" in line for line in body)):
        return "칸 구분이 탭이 아니라 글자 \\t입니다"
    # A hand-joined TSV may hold a lone quote mark in a cell; only CSV quoting is read as quoting.
    quoting = csv.QUOTE_NONE if separator == "\t" else csv.QUOTE_MINIMAL
    try:
        rows = [(number, row) for number, row in enumerate(csv.reader(io.StringIO(text), delimiter=separator,
                                                                      quoting=quoting), start=1)
                if row and any(cell.strip() for cell in row) and not row[0].startswith("##")]
    except csv.Error as exc:  # a NUL byte or a broken quote; a long field fits CSV_FIELD_LIMIT
        return f"표로 읽지 못했습니다({exc})"
    if len(rows) < 2:
        return None
    header = len(rows[0][1])
    widths = {len(row) for _, row in rows[1:]}
    # R write.table(sep="\t") writes a header without the row-name column; write.csv names that column "".
    if widths == {header} or (separator == "\t" and widths == {header + 1}):
        return None
    number, row = next((number, row) for number, row in rows[1:] if len(row) != header)
    return f"줄마다 칸 수가 다릅니다(머리줄 {header}칸, {number}번째 줄 {len(row)}칸)"


def table_warnings(root: Path, outputs: Iterable[str]) -> list[str]:
    """``"<output>: <problem>"`` for each collected table output that does not read as a table."""
    warnings = []
    for rel in outputs:
        if Path(rel).suffix.lower() not in TABLE_SEPARATORS:
            continue
        path = _inside(Path(root), rel)
        try:
            problem = table_problem(path) if path is not None else None
        except Exception as exc:  # noqa: BLE001 - a check never turns a finished task into a failure (PR #712)
            problem = f"검사하지 못했습니다({type(exc).__name__})"
        if problem:
            warnings.append(f"{rel}: {problem}")
            if len(warnings) >= MAX_TABLE_WARNINGS:
                break
    return warnings


def _normal_named_paper_id(scheme: Any, value: Any) -> str | None:
    """One comparison spelling for a PMID, PMCID or DOI, including DOI URL input."""
    if not isinstance(scheme, str) or not isinstance(value, (str, int)):
        return None
    kind = scheme.strip().casefold()
    raw = str(value).strip()
    if kind == "pmid":
        raw = re.sub(r"^pmid\s*[:=]?\s*", "", raw, flags=re.IGNORECASE)
        return f"PMID:{raw}" if raw.isdigit() else None
    if kind == "pmcid":
        raw = re.sub(r"^pmcid\s*[:=]?\s*", "", raw, flags=re.IGNORECASE)
        return f"PMCID:{raw.upper()}" if PMCID.fullmatch(raw) else None
    if kind == "doi":
        raw = re.sub(r"^(?:doi\s*[:=]?\s*|https?://(?:dx\.)?doi\.org/)", "", raw, flags=re.IGNORECASE)
        raw = raw.rstrip(".,;")
        return f"DOI:{raw.casefold()}" if DOI_VALUE.fullmatch(raw) else None
    return None


def _normal_paper_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    prefix, separator, raw = value.strip().partition(":")
    return _normal_named_paper_id(prefix, raw) if separator else None


def _paper_key(identifiers: set[str]) -> str:
    def order(identifier: str) -> tuple[int, str]:
        return PAPER_ID_ORDER.get(identifier.partition(":")[0], len(PAPER_ID_ORDER)), identifier

    return PAPER_ALIAS_SEPARATOR.join(sorted(identifiers, key=order))


def _answer_paper_ids(answer: Mapping[str, Any], fallback: Any) -> set[str]:
    identifiers = {paper_id for scheme in ("pmid", "pmcid", "doi")
                   if (paper_id := _normal_named_paper_id(scheme, answer.get(scheme)))}
    if identifiers:
        return identifiers
    normal_fallback = _normal_paper_id(fallback)
    if normal_fallback:
        return {normal_fallback}
    if isinstance(fallback, (str, int)) and str(fallback).strip():
        return {f"input:{str(fallback).strip()[:100]}"}
    return set()


def _access_paper_ids(key: Any) -> set[str]:
    """Read a new alias-group key or an old single-identifier key."""
    if not isinstance(key, str) or not key.strip():
        return set()
    identifiers = {paper_id for part in key.split(PAPER_ALIAS_SEPARATOR)
                   if (paper_id := _normal_paper_id(part))}
    return identifiers or {key.strip()[:100]}


def _merge_access_group(groups: list[tuple[set[str], str]], identifiers: set[str], level: str) -> None:
    overlap = [index for index, (aliases, _level) in enumerate(groups) if aliases & identifiers]
    if not overlap:
        if len(groups) < MAX_PAPERS:
            groups.append((set(identifiers), level))
        return
    aliases, levels = set(identifiers), [level]
    insert_at = overlap[0]
    for index in reversed(overlap):
        previous_aliases, previous_level = groups.pop(index)
        aliases.update(previous_aliases)
        levels.append(previous_level)
    groups.insert(insert_at, (aliases, min(levels, key=LEVELS.index)))


def literature_access(root: Path) -> dict[str, str]:
    """{paper identifier aliases: best access_level} from this workdir's fulltext lookups."""
    root = Path(root)
    log = _inside(root, QUERY_LOG)
    if log is None or log.stat().st_size > MAX_RECORD_BYTES:
        return {}
    groups: list[tuple[set[str], str]] = []
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("tool") != FULLTEXT_TOOL:
            continue
        record = _inside(root, str(entry.get("result_file") or ""))
        if record is None or record.stat().st_size > MAX_RECORD_BYTES:
            continue
        try:
            results = json.loads(record.read_text(encoding="utf-8")).get("results") or []
        except (OSError, ValueError, AttributeError):
            continue
        for item in results if isinstance(results, list) else []:
            answer = item.get("answer") if isinstance(item, dict) else None
            if not isinstance(answer, dict) or not answer.get("found") or answer.get("access_level") not in LEVELS:
                continue
            identifiers = _answer_paper_ids(answer, item.get("input"))
            if not identifiers:
                continue
            _merge_access_group(groups, identifiers, answer["access_level"])
    return {_paper_key(identifiers): level for identifiers, level in groups}


def _field(result: Any, name: str) -> Any:
    return getattr(result, name, None) if not isinstance(result, Mapping) else result.get(name)


def _source_paper_ids(source: Any) -> set[str]:
    """Paper identifiers written in an evidence source's structured id, URI or locator."""
    if not isinstance(source, Mapping):
        return set()
    ids: set[str] = set()
    structured = _normal_named_paper_id(source.get("id_scheme"), source.get("id_value"))
    if structured:
        ids.add(structured)
    for field in ("uri", "locator"):
        value = source.get(field)
        if not isinstance(value, str):
            continue
        ids.update(f"PMID:{match}" for match in PMID.findall(value))
        ids.update(f"PMID:{match}" for match in PUBMED_URL.findall(value))
        ids.update(f"PMCID:{match.upper()}" for match in PMCID.findall(value))
        ids.update(f"DOI:{match.rstrip('.,;').lower()}" for match in DOI.findall(value))
    return ids


def _paper_groups(identifier_sets: Iterable[set[str]]) -> list[set[str]]:
    """Merge identifiers that co-occur on one evidence row, so PMID plus DOI still counts as one paper."""
    groups: list[set[str]] = []
    for identifiers in identifier_sets:
        if not identifiers:
            continue
        overlap = [index for index, group in enumerate(groups) if group & identifiers]
        if not overlap:
            if len(groups) < MAX_PAPERS:
                groups.append(set(identifiers))
            continue
        merged = set(identifiers)
        for index in reversed(overlap):
            merged.update(groups.pop(index))
        groups.append(merged)
    return groups


def conclusion_literature_line(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any],
                               ledgers: Mapping[str, Any], *, unsupported: Iterable[Any] = (),
                               refused: Iterable[Any] = ()) -> str | None:
    """Read-depth warning for papers that actually ground CP2-citable conclusion claims."""
    citable = {(str(row["step_id"]), str(row["claim"].get("id")))
               for row in claim_rows(ledgers, unsupported)[0]}
    refused_rows = {(str(row.get("step_id")), str(row.get("evidence_id")))
                    for row in refused if isinstance(row, Mapping)}
    cited_sources: list[set[str]] = []
    for step_id, ledger in ledgers.items():
        if not isinstance(ledger, Mapping):
            continue
        evidence = {str(row.get("id")): row for row in ledger.get("evidence") or []
                    if isinstance(row, Mapping) and row.get("id")}
        for link in ledger.get("links") or []:
            if (not isinstance(link, Mapping) or link.get("relation") not in {"supports", "contradicts"}
                    or (str(step_id), str(link.get("claim_id"))) not in citable):
                continue
            evidence_id = str(link.get("evidence_id"))
            if (str(step_id), evidence_id) in refused_rows:
                continue
            row = evidence.get(evidence_id)
            ids = _source_paper_ids(row.get("source") if isinstance(row, Mapping) else None)
            if ids:
                cited_sources.append(ids)
    papers = _paper_groups(cited_sources)
    if not papers:
        return None

    access: list[tuple[set[str], str]] = []
    for step in steps:
        result = results.get(step["id"])
        if result is None:
            continue
        for key, level in (_field(result, "literature_access") or {}).items():
            identifiers = _access_paper_ids(key)
            if identifiers and level in LEVELS:
                _merge_access_group(access, identifiers, level)

    counts = {level: 0 for level in LEVELS}
    missing = 0
    for paper in papers:
        found = [level for aliases, level in access if aliases & paper]
        if not found:
            missing += 1
            continue
        counts[min(found, key=LEVELS.index)] += 1
    if not (counts["abstract"] or counts["metadata"] or missing):
        return None
    return (f"결론 근거 논문 {len(papers)}편: 본문 {counts['full_text']}편·초록 {counts['abstract']}편·"
            f"서지만 {counts['metadata']}편·조회 기록 없음 {missing}편. 결론 claim에 연결된 논문만 센 값이며, "
            "'fulltext로 조회한 논문 전체'와 분모가 다르다")


def quality_lines(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any]) -> list[str]:
    """Report-warning lines: one per step with table problems, one for literature read below full text."""
    lines: list[str] = []
    access: list[tuple[set[str], str]] = []
    for step in steps:
        result = results.get(step["id"])
        if result is None:
            continue
        problems = [str(line) for line in _field(result, "table_warnings") or []]
        if problems:
            shown = "; ".join(problems[:3]) + (f" (+{len(problems) - 3}개)" if len(problems) > 3 else "")
            lines.append(f"{step['id']}: 표로 읽히지 않는 산출물 {len(problems)}개 — {shown}")
        for key, level in (_field(result, "literature_access") or {}).items():
            identifiers = _access_paper_ids(key)
            if identifiers and level in LEVELS:
                _merge_access_group(access, identifiers, level)
    levels = [level for _identifiers, level in access]
    below = sum(level != "full_text" for level in levels)
    if below:
        counts = ", ".join(f"{LEVEL_NAMES[name]} {n}편" for name in LEVELS
                           if (n := sum(level == name for level in levels)))
        lines.append(f"문헌을 읽은 수준(fulltext로 조회한 {len(levels)}편): {counts}. "
                     f"본문을 읽지 못한 {below}편에서 가져온 수치는 한계로 적는다")
    return lines
