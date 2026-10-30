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
import math
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePath
from typing import Any

from .adapters.owned import is_link
from .evidence.report_check import accepted_evidence_ids, claim_rows
from .models import (EXTERNAL_LOOKUP_MAX_S, EXTERNAL_LOOKUP_MAX_TOOLS, EXTERNAL_LOOKUP_TOOL, ExternalLookupRecord,
                     ExternalLookupTool)
from .report_markdown import READER_FILENAMES, render_marker_message, rendered_markers
from .tools.external_wait import STATUS_FILE as LOOKUP_STATUS_LOG

TABLE_SEPARATORS = {".tsv": "\t", ".tab": "\t", ".csv": ","}
MAX_TABLE_BYTES = 4_000_000  # enough rows to see the shape; a larger table is checked on its first 4 MB
MAX_TABLE_WARNINGS = 20
MAX_MARKDOWN_WARNINGS = 20
# Python's default field limit (131,072) rejects a valid CSV with one long field (a sequence, a JSON cell).
CSV_FIELD_LIMIT = MAX_TABLE_BYTES
csv.field_size_limit(max(csv.field_size_limit(), CSV_FIELD_LIMIT))
QUERY_LOG = "outputs/annotation_queries.jsonl"
LITERATURE_ACCESS_LOG = "outputs/literature_access.jsonl"
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
OPENALEX = re.compile(
    r"(?:https?://(?:api\.)?openalex\.org/(?:works/)?|\bOpenAlex\s*[:=]\s*)(W\d+)\b", re.IGNORECASE)
OPENALEX_VALUE = re.compile(r"(?:https?://(?:api\.)?openalex\.org/(?:works/)?)?(W\d+)", re.IGNORECASE)
PAPER_ALIAS_SEPARATOR = "|"  # not valid in the identifiers above; old one-identifier keys remain valid
PAPER_ID_ORDER = {"PMID": 0, "PMCID": 1, "DOI": 2, "OpenAlex": 3, "source": 4, "input": 5}
DISCOVERY_TOOLS = frozenset({"openalex_works", "citation_chase", "preprints"})
UNREAD_NAMES = {"subscription_required": "구독 필요", "no_copy": "사본 없음", "access_denied": "접근 거부",
                "format_failure": "형식 실패", "not_looked_up": "조회 기록 없음", "unknown": "사유 미기록"}


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


def markdown_warnings(root: Path, outputs: Iterable[str]) -> list[str]:
    """Check reader-facing files on the runner; return locations and fixes, never document content."""
    warnings: list[str] = []
    for rel in dict.fromkeys(outputs):
        if Path(rel).name.casefold() not in READER_FILENAMES:
            continue
        path = _inside(Path(root), rel)
        if path is None:
            continue
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_RECORD_BYTES + 1)
            if len(data) > MAX_RECORD_BYTES:
                warnings.append(f"{rel}: Markdown 전체를 검사하지 못했습니다. 문서를 나누고 다시 검사하세요.")
            else:
                # One extra hit detects overflow without building every hit/message in a repetitive document.
                hits = rendered_markers(data.decode("utf-8-sig", errors="replace"),
                                        limit=MAX_MARKDOWN_WARNINGS + 1 - len(warnings))
                warnings.extend(render_marker_message(rel, hit) for hit in hits)
        except Exception as exc:  # noqa: BLE001 - retain a failed check without failing the completed task
            warnings.append(f"{rel}: Markdown 검사 실패({type(exc).__name__}). 문서를 확인하고 다시 검사하세요.")
        if len(warnings) > MAX_MARKDOWN_WARNINGS:
            return [*warnings[:MAX_MARKDOWN_WARNINGS],
                    "추가 Markdown 경고와 남은 파일 검사는 생략했습니다. 표시된 문제를 고친 뒤 다시 검사하세요."]
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
    if kind == "openalex":
        match = OPENALEX_VALUE.fullmatch(raw)
        return f"OpenAlex:{match.group(1).upper()}" if match else None
    return None


def _normal_paper_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    prefix, separator, raw = value.strip().partition(":")
    return _normal_named_paper_id(prefix, raw) if separator else None


def _normal_paper_input(value: Any) -> str | None:
    """Normalize a fulltext input even when it used the documented bare PMID or DOI form."""
    normal = _normal_paper_id(value)
    if normal or not isinstance(value, (str, int)):
        return normal
    raw = str(value).strip()
    if raw.isdigit():
        return _normal_named_paper_id("pmid", raw)
    if PMCID.fullmatch(raw):
        return _normal_named_paper_id("pmcid", raw)
    return _normal_named_paper_id("doi", raw)


def _paper_key(identifiers: set[str]) -> str:
    def order(identifier: str) -> tuple[int, str]:
        return PAPER_ID_ORDER.get(identifier.partition(":")[0], len(PAPER_ID_ORDER)), identifier

    return PAPER_ALIAS_SEPARATOR.join(sorted(identifiers, key=order))


def _answer_paper_ids(answer: Mapping[str, Any], fallback: Any) -> set[str]:
    identifiers = {paper_id for scheme in ("pmid", "pmcid", "doi")
                   if (paper_id := _normal_named_paper_id(scheme, answer.get(scheme)))}
    openalex = _normal_named_paper_id("openalex", answer.get("openalex_id"))
    if openalex:
        identifiers.add(openalex)
    if identifiers:
        return identifiers
    normal_fallback = _normal_paper_input(fallback)
    if normal_fallback:
        return {normal_fallback}
    if isinstance(fallback, (str, int)) and str(fallback).strip():
        return {f"input:{str(fallback).strip()[:100]}"}
    return set()


def _candidate_paper_ids(candidate: Any) -> set[str]:
    if not isinstance(candidate, Mapping):
        return set()
    identifiers = _answer_paper_ids(candidate, None)
    source = candidate.get("source")
    if not identifiers and isinstance(source, Mapping) and source.get("code") and source.get("id"):
        identifiers.add(f"source:{str(source['code'])[:20]}:{str(source['id'])[:80]}")
    return identifiers


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
    for entry in _literature_access_entries(root):
        level = entry.get("access_level")
        identifiers = _access_paper_ids(str(entry.get("paper_id") or ""))
        if identifiers and level in LEVELS:
            _merge_access_group(groups, identifiers, level)
    return {_paper_key(identifiers): level for identifiers, level in groups}


def _literature_access_entries(root: Path) -> list[dict]:
    path = _inside(Path(root), LITERATURE_ACCESS_LOG)
    if path is None or path.stat().st_size > MAX_RECORD_BYTES:
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines[-MAX_PAPERS:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            entries.append(row)
    return entries


def literature_access_failures(root: Path) -> dict[str, str]:
    """Latest failed permitted-access outcome per paper; no title, path, or body is returned to the gateway."""
    failures: dict[str, str] = {}
    for entry in _literature_access_entries(root):
        key = _paper_key(_access_paper_ids(str(entry.get("paper_id") or "")))
        if not key:
            continue
        reason = str(entry.get("failure_reason") or "")
        if reason:
            failures[key] = reason[:100]
        elif entry.get("access_level") == "full_text":
            failures.pop(key, None)
    return failures


def literature_ledger(root: Path) -> dict[str, Any]:
    """Bounded public-ID ledger reconstructed from this step's discovery and fulltext query records."""
    root = Path(root)
    log = _inside(root, QUERY_LOG)
    if log is None or log.stat().st_size > MAX_RECORD_BYTES:
        return {}
    candidates: list[set[str]] = []
    selected: list[set[str]] = []
    unread: list[tuple[set[str], str]] = []
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("tool") not in DISCOVERY_TOOLS | {FULLTEXT_TOOL}:
            continue
        record = _inside(root, str(entry.get("result_file") or ""))
        if record is None or record.stat().st_size > MAX_RECORD_BYTES:
            continue
        try:
            rows = json.loads(record.read_text(encoding="utf-8")).get("results") or []
        except (OSError, ValueError, AttributeError):
            continue
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, Mapping):
                continue
            answer = item.get("answer")
            if not isinstance(answer, Mapping):
                continue
            if entry.get("tool") == FULLTEXT_TOOL:
                identifiers = _answer_paper_ids(answer, item.get("input"))
                if not identifiers:
                    continue
                selected.append(identifiers)
                level = answer.get("access_level")
                if level in {"abstract", "metadata"}:
                    unread.append((identifiers, str(answer.get("unread_reason") or "unknown")))
                elif answer.get("found") is False:
                    unread.append((identifiers, str(answer.get("unread_reason") or "no_copy")))
                continue
            blocks = ([answer.get("candidates")]
                      if entry.get("tool") in {"openalex_works", "preprints"}
                      else [(answer.get("backward") or {}).get("candidates"),
                            (answer.get("forward") or {}).get("candidates")])
            for block in blocks:
                for candidate in block if isinstance(block, list) else []:
                    identifiers = _candidate_paper_ids(candidate)
                    if identifiers:
                        candidates.append(identifiers)
    candidate_groups = _paper_groups(candidates, limit=None)
    selected_groups = _paper_groups(selected)
    unread_groups: list[tuple[set[str], str]] = []
    for identifiers, reason in unread:
        overlap = [index for index, (aliases, _reason) in enumerate(unread_groups) if aliases & identifiers]
        if not overlap:
            unread_groups.append((set(identifiers), reason))
            continue
        aliases = set(identifiers)
        reasons = [reason]
        insert_at = overlap[0]
        for index in reversed(overlap):
            previous_aliases, previous_reason = unread_groups.pop(index)
            aliases.update(previous_aliases)
            reasons.append(previous_reason)
        chosen = next((value for value in reasons if value != "unknown"), "unknown")
        unread_groups.insert(insert_at, (aliases, chosen))
    return {
        "candidate_ids": [_paper_key(group) for group in candidate_groups],
        "selected_ids": [_paper_key(group) for group in selected_groups],
        "unread": {_paper_key(group): reason for group, reason in unread_groups},
    }


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
        ids.update(f"OpenAlex:{match.upper()}" for match in OPENALEX.findall(value))
    return ids


def _paper_groups(identifier_sets: Iterable[set[str]], *, limit: int | None = MAX_PAPERS) -> list[set[str]]:
    """Merge identifiers that co-occur on one evidence row, so PMID plus DOI still counts as one paper."""
    groups: list[set[str]] = []
    for identifiers in identifier_sets:
        if not identifiers:
            continue
        overlap = [index for index, group in enumerate(groups) if group & identifiers]
        if not overlap:
            if limit is None or len(groups) < limit:
                groups.append(set(identifiers))
            continue
        merged = set(identifiers)
        for index in reversed(overlap):
            merged.update(groups.pop(index))
        groups.append(merged)
    return groups


def _conclusion_papers(ledgers: Mapping[str, Any], unsupported: Iterable[Any],
                       refused: Iterable[Any]) -> list[set[str]]:
    refused = list(refused)
    citable = {(str(row["step_id"]), str(row["claim"].get("id")))
               for row in claim_rows(ledgers, unsupported, refused=refused)[0]}
    cited_sources: list[set[str]] = []
    for step_id, ledger in ledgers.items():
        if not isinstance(ledger, Mapping):
            continue
        evidence = {str(row.get("id")): row for row in ledger.get("evidence") or []
                    if isinstance(row, Mapping) and row.get("id")}
        accepted = accepted_evidence_ids(ledger, str(step_id), refused)
        for link in ledger.get("links") or []:
            if (not isinstance(link, Mapping) or link.get("relation") not in {"supports", "contradicts"}
                    or (str(step_id), str(link.get("claim_id"))) not in citable):
                continue
            evidence_id = str(link.get("evidence_id"))
            if evidence_id not in accepted:
                continue
            row = evidence.get(evidence_id)
            identifiers = _source_paper_ids(row.get("source") if isinstance(row, Mapping) else None)
            if identifiers:
                cited_sources.append(identifiers)
    return _paper_groups(cited_sources)


_STORED_URL_IDS = (
    (re.compile(r"https?://(?:dx\.)?doi\.org/(10\.\S+)", re.I), "doi"),
    (re.compile(r"https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.I), "pmid"),
    (re.compile(r"https?://(?:www\.)?ncbi\.nlm\.nih\.gov/pmc/articles/(PMC\d+)", re.I), "pmcid"),
)


def _stored_aliases(key: Any) -> set[str]:
    """Identifiers of one precedent citation: a named id (``PMID:1``), a bare PMID, PMCID or DOI, or a DOI, PubMed or
    PMC URL, which the precedent prompt allows."""
    if isinstance(key, str):
        for pattern, name in _STORED_URL_IDS:
            if (found := pattern.match(key.strip())) and (normal := _normal_named_paper_id(name, found.group(1))):
                return {normal}
    if isinstance(key, (str, int)) and (normal := _normal_paper_input(key)):
        return {normal}
    return _access_paper_ids(key)


def stored_access(analysis_precedents: Any) -> list[tuple[set[str], str, bool]]:
    """Papers whose text the precedent search stored before CP1, as (alias identifiers, access level, partial).

    The step ``literature_access`` records only what a step's own lookups wrote; the precedent search's receipts
    (``fulltext_receipt``) live on the request and reach a step as ``inputs/precedent/`` (#951). Reading them here
    lets the report count what was really read (#1090). Only a record with status ok and an access level labhq
    knows counts; the aliases are the receipt's paper id and the paper's ``citations`` (PMID, PMCID, DOI)."""
    if not isinstance(analysis_precedents, Mapping) or analysis_precedents.get("status") != "ok":
        return []
    found: list[tuple[set[str], str, bool]] = []
    seen_hashes: set[str] = set()
    for paper in analysis_precedents.get("papers") or []:
        receipt = paper.get("fulltext_receipt") if isinstance(paper, Mapping) else None
        if not isinstance(receipt, Mapping) or receipt.get("access_level") not in LEVELS:
            continue
        # Only a receipt a step could be handed counts: a stored file and its sha256, once per hash (precedent_texts.rows).
        digest = receipt.get("text_sha256")
        if (not isinstance(receipt.get("text_file"), str) or not receipt["text_file"]
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) or digest in seen_hashes):
            continue
        seen_hashes.add(digest)
        citations = paper.get("citations")
        aliases: set[str] = set()
        for key in [receipt.get("paper_id"), *(citations if isinstance(citations, list) else [])]:
            aliases |= _stored_aliases(key)
        if aliases:
            found.append((aliases, str(receipt["access_level"]), receipt.get("extraction") == "partial_text"))
    return found


def _merge_stored(papers: list[set[str]], stored: Iterable[tuple[set[str], str, bool]]) -> list[set[str]]:
    """Join conclusion papers that one stored paper names with two identifiers (PMID and DOI). Papers the report does
    not cite are never added."""
    cited_aliases = [aliases for aliases, _level, _partial in stored if any(aliases & paper for paper in papers)]
    return _paper_groups([*papers, *cited_aliases]) if cited_aliases else papers


def conclusion_literature_line(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any],
                               ledgers: Mapping[str, Any], *, unsupported: Iterable[Any] = (),
                               refused: Iterable[Any] = (),
                               stored: Iterable[tuple[set[str], str, bool]] = ()) -> str | None:
    """Read-depth warning for papers that actually ground CP2-citable conclusion claims.

    ``stored`` is ``stored_access(analysis_precedents)``; without it the line is what the steps' own records say."""
    stored = list(stored)
    papers = _merge_stored(_conclusion_papers(ledgers, unsupported, refused), stored)
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
    for aliases, level, _partial in stored:
        _merge_access_group(access, set(aliases), level)

    counts = {level: 0 for level in LEVELS}
    missing = from_stored = partial = 0
    for paper in papers:
        found = [level for aliases, level in access if aliases & paper]
        if not found:
            missing += 1
            continue
        counts[min(found, key=LEVELS.index)] += 1
        matched = [partial_text for aliases, _level, partial_text in stored if aliases & paper]
        from_stored += bool(matched)
        partial += any(matched)
    if not (counts["abstract"] or counts["metadata"] or missing):
        return None
    origin = ""
    if from_stored:  # where the read came from and when stays visible: a receipt of the precedent search before CP1
        origin = f"(저장된 선행 검색 기록 {from_stored}편 포함·CP1 전 기록" + (f", 그중 부분 추출 {partial}편)" if partial else ")")
    return (f"결론 근거 논문 {len(papers)}편: 본문 {counts['full_text']}편·초록 {counts['abstract']}편·"
            f"서지만 {counts['metadata']}편·조회 기록 없음 {missing}편{origin}. 결론 claim에 연결된 논문만 센 값이며, "
            "'fulltext로 조회한 논문 전체'와 분모가 다르다")


def literature_ledger_line(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any],
                           ledgers: Mapping[str, Any], *, unsupported: Iterable[Any] = (),
                           refused: Iterable[Any] = (),
                           stored: Iterable[tuple[set[str], str, bool]] = ()) -> str | None:
    """One CP2/report line with four deliberately separate denominators and unread key-paper reasons."""
    candidate_sets: list[set[str]] = []
    selected_sets: list[set[str]] = []
    unread: list[tuple[set[str], str]] = []
    access: list[tuple[set[str], str]] = []
    for step in steps:
        result = results.get(step["id"])
        if result is None:
            continue
        ledger = _field(result, "literature_ledger") or {}
        for key in ledger.get("candidate_ids") or []:
            candidate_sets.append(set(str(key).split(PAPER_ALIAS_SEPARATOR)))
        for key in ledger.get("selected_ids") or []:
            selected_sets.append(set(str(key).split(PAPER_ALIAS_SEPARATOR)))
        for key, reason in (ledger.get("unread") or {}).items():
            unread.append((set(str(key).split(PAPER_ALIAS_SEPARATOR)), str(reason)))
        for key, level in (_field(result, "literature_access") or {}).items():
            identifiers = _access_paper_ids(key)
            if identifiers and level in LEVELS:
                _merge_access_group(access, identifiers, level)
    if not candidate_sets and not selected_sets:
        return None
    stored = list(stored)
    for aliases, level, _partial in stored:
        _merge_access_group(access, set(aliases), level)
    candidates = _paper_groups(candidate_sets, limit=None)
    selected = _paper_groups(selected_sets)
    papers = _merge_stored(_conclusion_papers(ledgers, unsupported, refused), stored)
    counts = {level: sum(level == found for _aliases, found in access) for level in LEVELS}
    missed = []
    for paper in papers:
        found = next((level for aliases, level in access if aliases & paper), None)
        if found == "full_text":
            continue
        reason = next((value for aliases, value in unread if aliases & paper), "not_looked_up" if found is None else "unknown")
        display = min(paper, key=lambda value: (PAPER_ID_ORDER.get(value.partition(":")[0], 99), value))
        missed.append(f"{display}({UNREAD_NAMES.get(reason, reason)})")
    acquired = sum(counts.values())
    line = (f"문헌 접근 실패 원장(서로 다른 분모): 후보 {len(candidates)}편·선정 {len(selected)}편·"
            f"확보 {acquired}편(본문 {counts['full_text']}·초록 {counts['abstract']}·서지 {counts['metadata']})·"
            f"결론 근거 {len(papers)}편.")
    if stored:
        line += f" 확보에는 저장된 선행 검색 기록 {len(_paper_groups([set(aliases) for aliases, _l, _p in stored], limit=None))}편(CP1 전 기록)이 들어 있습니다."
    if missed:
        shown = ", ".join(missed[:5]) + (f" 외 {len(missed) - 5}편" if len(missed) > 5 else "")
        line += f" 못 읽은 핵심 논문: {shown}."
    return line


MAX_LOOKUP_STATUS_BYTES = 1_000_000  # ~4,000 records; the tail keeps the latest outcomes
MAX_LOOKUP_STATUS_ROWS = 5000
MAX_LOOKUP_STATUS_LINE = 4096
LOOKUP_ID = re.compile(r"[0-9a-f]{12}")
LOOKUP_OUTCOMES = ("ok", "timeout", "failed", "cancelled")
LOOKUP_STATUSES = frozenset({"waiting", *LOOKUP_OUTCOMES})
LOOKUP_MISSING_NAMES = {"timeout": "조회 불가(시간 초과)", "failed": "조회 실패", "cancelled": "조회 취소"}
LOOKUP_RECORD_NOTE = "직원 workspace의 조회 상태 기록이며 호출·본문 독해 증명은 아님"


class UnreadableRecord(Exception):
    """The record is there but is a link, not a plain file under plain folders, or could not be read."""


def _owned_tail(root: Path, relative: str, limit: int) -> tuple[str, bool] | None:
    """The last ``limit`` bytes of a plain file under plain folders, as text, and whether earlier bytes were skipped.
    None when it is missing. A link is never followed, as ``read_owned`` treats labhq's own records (#165); it and a
    failed read raise ``UnreadableRecord``, so a record that could not be read is not taken for no record."""
    rel = PurePath(relative)
    current = Path(root)
    try:
        for part in rel.parent.parts:
            current /= part
            if is_link(current) or not current.is_dir():  # is_link raises FileNotFoundError when it is missing
                raise UnreadableRecord
        target = current / rel.name
        if is_link(target) or not target.is_file():
            raise UnreadableRecord
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with open(fd, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            truncated = size > limit
            if truncated:
                handle.seek(size - limit)
            data = handle.read(limit)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnreadableRecord from exc
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text = text.partition("\n")[2]  # the first line was cut
    return text, truncated


def _seconds(value: Any, *, positive: bool) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0) or value > EXTERNAL_LOOKUP_MAX_S:
        return None
    return value


def _lookup_row(line: str) -> tuple[str, str | None, str, float, float] | None:
    if len(line) > MAX_LOOKUP_STATUS_LINE:
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    tool, status = row.get("tool"), row.get("status")
    if not isinstance(tool, str) or not re.fullmatch(EXTERNAL_LOOKUP_TOOL, tool) or status not in LOOKUP_STATUSES:
        return None
    # Records written before the per-call id have none; a present id must be the writer's form.
    lookup = row.get("lookup")
    if lookup is not None and (not isinstance(lookup, str) or not LOOKUP_ID.fullmatch(lookup)):
        return None
    elapsed, limit = _seconds(row.get("elapsed_s"), positive=False), _seconds(row.get("timeout_s"), positive=True)
    if elapsed is None or limit is None:
        return None
    return tool, lookup, status, elapsed, limit  # the row's free "text" is never read back


def external_lookups(root: Path) -> ExternalLookupRecord | None:
    """Outcome counts per tool from this workspace's outputs/external_lookup_status.jsonl (#676); None without one.

    The file is the step workspace's record: labhq_annot appends to it, but the employee can also write there. It
    shows which lookups ended without an answer; it never proves a call or a reading and never fills a slot."""
    try:
        read = _owned_tail(Path(root), LOOKUP_STATUS_LOG, MAX_LOOKUP_STATUS_BYTES)
    except UnreadableRecord:
        return ExternalLookupRecord(unreadable=True)
    if read is None:
        return None
    text, truncated = read
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_LOOKUP_STATUS_ROWS:
        lines, truncated = lines[-MAX_LOOKUP_STATUS_ROWS:], True
    tools: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str | None, str, float, float]] = set()
    dropped: set[str] = set()
    open_calls: dict[str, set[str]] = {}
    legacy_open: dict[str, bool] = {}
    invalid = duplicate = 0
    for line in lines:
        row = _lookup_row(line)
        if row is None:
            invalid += 1
            continue
        if row in seen:  # the same record twice (a repeated append), counted once
            duplicate += 1
            continue
        seen.add(row)
        tool, lookup, status, elapsed, limit = row
        entry = tools.get(tool)
        if entry is None:
            if len(tools) >= EXTERNAL_LOOKUP_MAX_TOOLS:
                dropped.add(tool)
                continue
            entry = tools[tool] = {"tool": tool, "elapsed_s": 0.0}
            open_calls[tool], legacy_open[tool] = set(), False
        # A call whose last record is "waiting" stays unfinished; a later call of the same tool does not end it.
        # Records without an id keep the per-tool reading: the next outcome of that tool ends the wait.
        if status == "waiting":
            if lookup is None:
                legacy_open[tool] = True
            else:
                open_calls[tool].add(lookup)
        else:
            entry[status] = entry.get(status, 0) + 1
            if lookup is None:
                legacy_open[tool] = False
            else:
                open_calls[tool].discard(lookup)
        entry["elapsed_s"] = max(entry["elapsed_s"], elapsed)
        entry["timeout_s"] = limit
    for tool, entry in tools.items():
        entry["unfinished"] = bool(open_calls[tool]) or legacy_open[tool]
    return ExternalLookupRecord(tools=[ExternalLookupTool(**entry) for entry in tools.values()],
                                invalid_rows=invalid, duplicate_rows=duplicate, dropped_tools=len(dropped),
                                truncated=truncated)


def external_lookup_lines(value: Any) -> list[str]:
    """Short Korean lines for lookups that ended without an answer and for records that were not read.

    Built only from validated tool names, counts and seconds, so no text the workspace wrote reaches a report."""
    if value is None:
        return []
    try:
        record = value if isinstance(value, ExternalLookupRecord) else ExternalLookupRecord.model_validate(value)
    except ValueError:
        return []
    lines = []
    for item in record.tools:
        if not item.missing:
            continue
        parts = [f"{name} {count}건" for key, name in LOOKUP_MISSING_NAMES.items() if (count := getattr(item, key))]
        if item.unfinished:
            parts.append("끝난 기록 없음")
        lines.append(f"`{item.tool}` {', '.join(parts)} · 단계 조회 경과 {item.elapsed_s:.0f}초/상한 "
                     f"{item.timeout_s:.0f}초")
    if record.invalid_rows:
        lines.append(f"읽지 않은 잘못된 기록 {record.invalid_rows}줄")
    if record.duplicate_rows:
        lines.append(f"같은 기록 {record.duplicate_rows}줄은 한 번만 셈")
    if record.dropped_tools:
        lines.append(f"도구 {record.dropped_tools}개는 상한 {EXTERNAL_LOOKUP_MAX_TOOLS}개를 넘어 세지 않음")
    if record.truncated:
        lines.append("기록이 길어 마지막 부분만 읽음")
    if record.unreadable:
        lines.append("기록을 읽지 못함(link이거나 일반 파일이 아니거나 읽기 오류)")
    return lines


def quality_lines(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any]) -> list[str]:
    """Report-warning lines: one per step with table problems, one per step with lookups that ended without an
    answer, one for literature read below full text."""
    lines: list[str] = []
    access: list[tuple[set[str], str]] = []
    failures: dict[str, str] = {}
    for step in steps:
        result = results.get(step["id"])
        if result is None:
            continue
        problems = [str(line) for line in _field(result, "table_warnings") or []]
        if problems:
            shown = "; ".join(problems[:3]) + (f" (+{len(problems) - 3}개)" if len(problems) > 3 else "")
            lines.append(f"{step['id']}: 표로 읽히지 않는 산출물 {len(problems)}개 — {shown}")
        lookups = external_lookup_lines(_field(result, "external_lookups"))
        if lookups:
            shown = "; ".join(lookups[:5]) + (f" (+{len(lookups) - 5}개)" if len(lookups) > 5 else "")
            lines.append(f"{step['id']}: 외부 조회 상태({LOOKUP_RECORD_NOTE}) — {shown}")
        for key, level in (_field(result, "literature_access") or {}).items():
            identifiers = _access_paper_ids(key)
            if identifiers and level in LEVELS:
                _merge_access_group(access, identifiers, level)
        for key, reason in (_field(result, "literature_access_failures") or {}).items():
            failures[str(key)] = str(reason)
    full_text = [identifiers for identifiers, level in access if level == "full_text"]
    failures = {key: reason for key, reason in failures.items()
                if not any(_access_paper_ids(key) & identifiers for identifiers in full_text)}
    levels = [level for _identifiers, level in access]
    below = sum(level != "full_text" for level in levels)
    if below:
        counts = ", ".join(f"{LEVEL_NAMES[name]} {n}편" for name in LEVELS
                           if (n := sum(level == name for level in levels)))
        lines.append(f"문헌을 읽은 수준(fulltext로 조회한 {len(levels)}편): {counts}. "
                     f"본문을 읽지 못한 {below}편에서 가져온 수치는 한계로 적는다")
    timed_out = [key for key, reason in failures.items() if reason == "pdf_request_timed_out"]
    if timed_out:
        shown = ", ".join(timed_out[:5]) + (f" 외 {len(timed_out) - 5}편" if len(timed_out) > 5 else "")
        lines.append(f"핵심 논문 PDF 요청이 설정된 대기 상한 뒤 만료되어 초록 또는 다른 OA 논문으로 진행함: {shown}")
    return lines
