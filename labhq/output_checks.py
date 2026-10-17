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


def _paper_key(answer: Mapping[str, Any], fallback: Any) -> str | None:
    for prefix, field in (("PMID", "pmid"), ("PMCID", "pmcid"), ("DOI", "doi")):
        value = answer.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return f"{prefix}:{str(value).strip()[:100]}"
    return f"input:{str(fallback).strip()[:100]}" if isinstance(fallback, (str, int)) and str(fallback).strip() else None


def _better(level: str, current: str | None) -> bool:
    return current is None or LEVELS.index(level) < LEVELS.index(current)


def literature_access(root: Path) -> dict[str, str]:
    """{paper id: best access_level} from the fulltext lookups recorded in this workdir."""
    root = Path(root)
    log = _inside(root, QUERY_LOG)
    if log is None or log.stat().st_size > MAX_RECORD_BYTES:
        return {}
    levels: dict[str, str] = {}
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
            key = _paper_key(answer, item.get("input"))
            if key is None or (key not in levels and len(levels) >= MAX_PAPERS):
                continue
            if _better(answer["access_level"], levels.get(key)):
                levels[key] = answer["access_level"]
    return levels


def _field(result: Any, name: str) -> Any:
    return getattr(result, name, None) if not isinstance(result, Mapping) else result.get(name)


def _normal_paper_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    prefix, separator, raw = value.strip().partition(":")
    if not separator:
        return None
    if prefix.upper() == "PMID" and raw.strip().isdigit():
        return f"PMID:{raw.strip()}"
    if prefix.upper() == "PMCID" and PMCID.fullmatch(raw.strip()):
        return f"PMCID:{raw.strip().upper()}"
    if prefix.upper() == "DOI" and DOI.fullmatch(raw.strip().rstrip(".,;")):
        return f"DOI:{raw.strip().rstrip('.,;').lower()}"
    return None


def _source_paper_ids(source: Any) -> set[str]:
    """Paper identifiers written in an evidence source's URI or locator."""
    if not isinstance(source, Mapping):
        return set()
    ids: set[str] = set()
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

    access: dict[str, str] = {}
    for step in steps:
        result = results.get(step["id"])
        if result is None:
            continue
        for key, level in (_field(result, "literature_access") or {}).items():
            paper_id = _normal_paper_id(key)
            if paper_id and level in LEVELS and _better(level, access.get(paper_id)):
                access[paper_id] = level

    counts = {level: 0 for level in LEVELS}
    missing = 0
    for paper in papers:
        found = [access[paper_id] for paper_id in paper if paper_id in access]
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
    levels: dict[str, str] = {}
    for step in steps:
        result = results.get(step["id"])
        if result is None:
            continue
        problems = [str(line) for line in _field(result, "table_warnings") or []]
        if problems:
            shown = "; ".join(problems[:3]) + (f" (+{len(problems) - 3}개)" if len(problems) > 3 else "")
            lines.append(f"{step['id']}: 표로 읽히지 않는 산출물 {len(problems)}개 — {shown}")
        for key, level in (_field(result, "literature_access") or {}).items():
            if level in LEVELS and _better(level, levels.get(key)):
                levels[key] = level
    below = sum(level != "full_text" for level in levels.values())
    if below:
        counts = ", ".join(f"{LEVEL_NAMES[name]} {n}편" for name in LEVELS
                           if (n := sum(level == name for level in levels.values())))
        lines.append(f"문헌을 읽은 수준(fulltext로 조회한 {len(levels)}편): {counts}. "
                     f"본문을 읽지 못한 {below}편에서 가져온 수치는 한계로 적는다")
    return lines
