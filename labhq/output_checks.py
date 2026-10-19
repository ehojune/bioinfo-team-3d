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
            identifiers = _source_paper_ids(row.get("source") if isinstance(row, Mapping) else None)
            if identifiers:
                cited_sources.append(identifiers)
    return _paper_groups(cited_sources)


def conclusion_literature_line(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any],
                               ledgers: Mapping[str, Any], *, unsupported: Iterable[Any] = (),
                               refused: Iterable[Any] = ()) -> str | None:
    """Read-depth warning for papers that actually ground CP2-citable conclusion claims."""
    papers = _conclusion_papers(ledgers, unsupported, refused)
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


def literature_ledger_line(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any],
                           ledgers: Mapping[str, Any], *, unsupported: Iterable[Any] = (),
                           refused: Iterable[Any] = ()) -> str | None:
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
    candidates = _paper_groups(candidate_sets, limit=None)
    selected = _paper_groups(selected_sets)
    papers = _conclusion_papers(ledgers, unsupported, refused)
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
    if missed:
        shown = ", ".join(missed[:5]) + (f" 외 {len(missed) - 5}편" if len(missed) > 5 else "")
        line += f" 못 읽은 핵심 논문: {shown}."
    return line


def quality_lines(steps: Iterable[Mapping[str, Any]], results: Mapping[str, Any]) -> list[str]:
    """Report-warning lines: one per step with table problems, one for literature read below full text."""
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
        for key, level in (_field(result, "literature_access") or {}).items():
            identifiers = _access_paper_ids(key)
            if identifiers and level in LEVELS:
                _merge_access_group(access, identifiers, level)
        for key, reason in (_field(result, "literature_access_failures") or {}).items():
            failures[str(key)] = str(reason)
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
