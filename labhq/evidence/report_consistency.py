"""Report text against its own result tables: numbers and directions, deterministically (#1374).

The KRAS 11th run (#910) passed its claim check and review, yet an outside reader found sentences whose numbers were
not in the table they cited and a sentence that said "decreased" where the table's effect was positive. A claim
anchor says which claim a sentence rests on; it does not say the sentence copied the claim's numbers or direction
right. This module reads the report and the tables it links, without a model:

- **number**: a number in a sentence that links a result table (``.tsv``/``.csv``) must appear in one of the linked
  tables, allowing the rounding the sentence shows and a fraction written as a percentage.
- **direction**: a sentence that says up/down (increase, 증가, higher, 상향, ...) about an effect must agree with its
  sign: a log fold change by its sign, a ratio (HR, OR, RR, fold change) by being above or below 1. A sentence that
  names an identifier of a linked table with a log fold change column is compared with that row.

It only reports. The writer gets the findings back once (``correction_prompt``); what is left is shown on the report
front as an unverified draft (``front_lines``) and in ``labhq verify``. A sentence the checker cannot read is skipped,
never guessed, so silence is not proof the report is consistent.
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .literal_check import _open_source

TABLE_SUFFIXES = {".tsv", ".csv"}
MAX_TABLE_BYTES = 3_000_000  # bounded reads by the standalone preregistration checker
MAX_FINDINGS = 40
_TABLE_LINE = re.compile(r"[^\r\n]*(?:\r\n?|\n|$)")
# A Markdown link: "<...>" destinations may hold spaces (the report contract allows them), plain ones may not.
_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^<>\n]+)>|([^()\s<>]+))(?:\s+\"[^\"]*\")?\s*\)")
_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.S)  # backtick and tilde fences, inline code
_ANCHOR = re.compile(r"\[\[[^\]]*\]\]")
# Korean particles follow a number directly ("43%였고", "4.9였다"), so only Latin letters and digits end it badly.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-−–]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?\s*%?(?![A-Za-z0-9_.])")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|\n")
_UP = re.compile(r"증가|상향|높(?:았|다|은|게|아)|늘(?:었|어|은)|올라|커(?:졌|짐)|up-?regulat|\bincreas\w*|\bhigher\b|"
                 r"\belevat\w*|\binduc\w*|\bupward\b|\bgain\w*", re.I)
_DOWN = re.compile(r"감소|하향|낮(?:았|다|은|게|아)|줄(?:었|어|은)|떨어|작아|down-?regulat|\bdecreas\w*|\blower\b|"
                   r"\breduc\w*|\brepress\w*|\bsuppress\w*|\bdownward\b|\bloss\b", re.I)
# Direction words in a named gene group describe how it was selected, not the effect measured in it.
_GENE_GROUP = re.compile(r"(?:상향|하향)(?=\s*(?:(?:조절\s*)?유전자\s*(?:군|그룹|집단|집합|에서)"
                         r"|(?:이른\s*[·-]\s*늦은\s*)?(?:OR|HR|RR)\b))", re.I)
_LOG_EFFECT = re.compile(r"\b(?:log2\s*\(?\s*fc\)?|log2\s*fold[\s-]*change|log2FoldChange|l2fc|lfc|logfc|log\s*fc)\b"
                         r"\s*(?:\(|=|:|of|은|는|이|가|값)?\s*([-−–]?\d+(?:\.\d+)?)", re.I)
_RATIO_EFFECT = re.compile(r"\b(?:HR|OR|RR|IRR|hazard\s+ratio|odds\s+ratio|risk\s+ratio|fold[\s-]*change|FC)\b"
                           r"\s*(?:\(|=|:|of|은|는|이|가|값)?\s*(\d+(?:\.\d+)?)", re.I)
# Words that flip what "up" means for a ratio (a hazard ratio below 1 is a lower risk but a longer survival).
# A ratio's direction is checked only where its meaning is plain. A hazard ratio about survival (OS, PFS, ...) means
# the event is death or progression: HR < 1 is survival going up, so the check inverts it. Any other ratio about a
# survival or response endpoint (an odds ratio of response, an HR of response) has a modelled event the sentence does
# not state, so it is not checked: a wrong "mismatch" would send a valid conclusion back for rewriting.
_SURVIVAL = re.compile(r"생존|survival|\bOS\b|\bPFS\b|\bDFS\b|\bEFS\b|\bRFS\b", re.I)
_ENDPOINT = re.compile(r"반응|response|관해|remission|\bORR\b|\bCR\b|\bPR\b", re.I)
_HAZARD = re.compile(r"^(?:HR|hazard\s+ratio)", re.I)
_LOG_COLUMN = re.compile(r"^(?:log2[\s_.]*f(?:old)?[\s_.]*c(?:hange)?|log2fc|l2fc|lfc|logfc|log2foldchange)$", re.I)
_YEARS = range(1900, 2101)


@dataclass(frozen=True)
class _LocalTable:
    workdir: Path
    output: str

    @contextmanager
    def open_lines(self):
        with _open_source(self.workdir, self.output) as source:
            before = os.fstat(source.fileno())
            with io.TextIOWrapper(source, encoding="utf-8-sig", newline="") as text:
                yield text
                after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("table changed during the scan")


Table = str | _LocalTable | None


def _value(text: str) -> float | None:
    raw = text.strip().replace("−", "-").replace("–", "-").rstrip("%").strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        raw = raw.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _decimals(text: str) -> int:
    """Places after the point the number shows, counting its exponent: "1.2e-5" shows 6, "3.4e2" shows -1."""
    raw = text.strip().rstrip("%").strip()
    mantissa, _, exponent = raw.replace("E", "e").partition("e")
    places = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
    try:
        return places - int(exponent) if exponent else places
    except ValueError:
        return places


def _targets(text: str) -> list[str]:
    return [(angle or plain).strip() for angle, plain in _LINK.findall(text)]


def _sentences(report: str) -> list[str]:
    body = _CODE.sub(" ", report)
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(("#", "|"))]  # tables are data
    return [part.strip() for part in _SENTENCE_END.split("\n".join(lines)) if part and part.strip()]


def _linked_tables(sentence: str, tables: Mapping[str, Table]) -> list[str]:
    found = []
    for target in _targets(sentence):
        path = target.replace("\\", "/").removeprefix("./")
        for name in tables:
            if path == name or path.endswith("/" + name) or name.endswith("/" + path):
                found.append(name)
    return list(dict.fromkeys(found))


def _numeric_targets(text: str, value: float) -> list[tuple[float, float]]:
    tolerance = 0.5 * 10 ** -_decimals(text) + 1e-9
    return [(value, tolerance)] + ([(value / 100, tolerance / 100)] if text.endswith("%") else [])


def _checkable_numbers(sentence: str) -> list[tuple[str, float]]:
    text = _ANCHOR.sub(" ", _LINK.sub(" ", sentence))
    text = re.sub(r"(?:PMID|PMC|DOI|doi)[:\s]*\S+|10\.\d{4,9}/\S+|https?://\S+", " ", text)
    text = re.sub(r"\b[A-Za-z]+[-_]?\d+[A-Za-z\d-]*\b", " ", text)  # identifiers such as G12C, SRR1039508, s3_step
    # Thresholds and interval levels are settings, not results: "padj < 0.05", "|log2FC| > 1", "95% CI", "1 이상".
    text = re.sub(r"[<>≤≥]=?\s*[-−]?\d+(?:\.\d+)?%?|\d+(?:\.\d+)?\s*%?\s*(?:미만|이하|이상|초과)", " ", text)
    text = re.sub(r"\d+(?:\.\d+)?\s*%\s*(?:CI|신뢰\s*구간|confidence|credible)", " ", text, flags=re.I)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*[-–~]\s*(\d+(?:\.\d+)?)\s*%", r"\1% ~ \2%", text)  # "30–60%": both are percent
    found = []
    for match in _NUMBER.finditer(text):
        raw = match.group(0)
        value = _value(raw)
        if value is None:
            continue
        if "." not in raw and "%" not in raw and (abs(value) < 10 or int(abs(value)) in _YEARS):
            continue  # a small count or a year carries no checkable quantity here
        found.append((raw.strip(), value))
    return found


def _direction(sentence: str) -> str | None:
    sentence = _GENE_GROUP.sub(" ", sentence)
    up, down = bool(_UP.search(sentence)), bool(_DOWN.search(sentence))
    return "up" if up and not down else "down" if down and not up else None


@dataclass
class _RowCheck:
    words: set[str]
    said: str
    checked: int = 0
    conflict: tuple[str, float] | None = None

    def compare(self, identifiers: list[str], effect: float) -> None:
        identifier = next((word for word in identifiers if word in self.words), None)
        if identifier is not None:
            self.checked += 1
            if (self.said == "up" and effect < 0) or (self.said == "down" and effect > 0):
                self.conflict = identifier, effect


def _match_row(row: list[str], targets: dict[str, list[tuple[float, float]]], matches: dict[str, str]) -> None:
    pool = [value for cell in row if (value := _value(cell)) is not None]
    found = []
    for raw, ranges in targets.items():
        if any(abs(number - target) <= allowed for number in pool for target, allowed in ranges):
            matches[raw] = "exact"
            found.append(raw)
        elif raw not in matches and any(abs(abs(number) - abs(target)) <= allowed
                                        for number in pool for target, allowed in ranges):
            matches[raw] = "sign"
    for raw in found:
        del targets[raw]


@dataclass
class _TableScan:
    numbers: dict[str, str]
    directions: dict[int, _RowCheck]


def _scan_table(lines: Iterable[str], delimiter: str, sentences: dict[int, str]) -> _TableScan:
    """Read to EOF, keeping only matches for claims in the report."""
    targets = {raw: _numeric_targets(raw, value) for sentence in sentences.values()
               for raw, value in _checkable_numbers(sentence)}
    matches: dict[str, str] = {}
    row_checks = {i: _RowCheck(set(re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}", sentence)), said)
                  for i, sentence in sentences.items() if (said := _direction(sentence))}
    rows = (row for row in csv.reader(lines, delimiter=delimiter, strict=True) if any(cell.strip() for cell in row))
    header = next(rows, [])
    columns = [i for i, cell in enumerate(header) if _LOG_COLUMN.fullmatch(cell.strip())]
    _match_row(header, targets, matches)
    for row in rows:
        if targets:
            _match_row(row, targets, matches)
        if not row_checks or not columns or columns[0] >= len(row):
            continue
        effect = _value(row[columns[0]])
        if effect is not None:
            ids = [cell.strip() for i, cell in enumerate(row) if i not in columns]
            for row_check in row_checks.values():
                if row_check.conflict is None:
                    row_check.compare(ids, effect)
    return _TableScan(matches, row_checks)


def _direction_finding(shown: str, said: str, label: str, value: float) -> dict[str, Any]:
    return {"kind": "direction", "sentence": shown, "value": f"{label} {value:g}",
            "detail": f"본문은 {'증가' if said == 'up' else '감소'}라고 썼지만 "
                      f"{label} {value:g}은(는) 반대 방향입니다"}


def check(report: str, tables: Mapping[str, Table]) -> dict[str, Any]:
    """Compare the report with in-memory copies or local files, reading each table to EOF."""
    sentences = _sentences(report)
    links = [_linked_tables(sentence, tables) for sentence in sentences]
    parsed: dict[str, _TableScan] = {}
    unread = []
    for name, table in tables.items():
        if table is None:
            unread.append(name)
            continue
        delimiter = "\t" if PurePosixPath(name).suffix.casefold() == ".tsv" else ","
        relevant = {i: sentence for i, sentence in enumerate(sentences) if name in links[i]}
        try:
            if isinstance(table, _LocalTable):
                with table.open_lines() as lines:
                    result = _scan_table(lines, delimiter, relevant)
            else:
                lines = (line[0] for line in _TABLE_LINE.finditer(table, int(table.startswith("\ufeff"))))
                result = _scan_table(lines, delimiter, relevant)
        except (OSError, csv.Error, ValueError):
            unread.append(name)
        else:
            parsed[name] = result

    findings: list[dict[str, Any]] = []
    numbers = directions = 0
    for i, sentence in enumerate(sentences):
        shown = re.sub(r"\s+", " ", sentence)[:240]
        linked = [name for name in links[i] if name in parsed]
        said = _direction(sentence)
        if linked:
            for raw, _ in _checkable_numbers(sentence):
                numbers += 1
                matches = {parsed[name].numbers.get(raw) for name in linked}
                if "exact" not in matches and "sign" not in matches:
                    findings.append({"kind": "number", "sentence": shown, "value": raw, "tables": linked,
                                     "detail": f"본문 수치 {raw}이(가) 연결된 표({', '.join(linked)})에 없습니다"})
                elif "exact" not in matches and said is None:
                    # "2.1배 감소" may drop a sign the words carry (checked below); a bare "log2FC 1.8" may not.
                    findings.append({"kind": "sign", "sentence": shown, "value": raw, "tables": linked,
                                     "detail": f"본문 수치 {raw}은(는) 연결된 표의 값과 부호가 다릅니다"})
        if said is None:
            continue
        effects = [("log2FC", value, value > 0, value < 0) for value in
                   (_value(m.group(1)) for m in _LOG_EFFECT.finditer(sentence)) if value is not None]
        survival, endpoint = bool(_SURVIVAL.search(sentence)), bool(_ENDPOINT.search(sentence))
        for m in _RATIO_EFFECT.finditer(sentence):
            if (value := _value(m.group(1))) is None:
                continue
            hazard = bool(_HAZARD.match(m.group(0)))
            if endpoint or (survival and not hazard):
                continue  # the modelled event is not stated: not checked
            flipped = survival and hazard
            effects.append((m.group(0).split()[0], value, (value < 1) if flipped else (value > 1),
                            (value > 1) if flipped else (value < 1)))
        for label, value, up, down in effects:
            directions += 1
            if (said == "up" and down) or (said == "down" and up):
                findings.append(_direction_finding(shown, said, label, value))
                break  # one direction finding per sentence: the stated value and its table row are the same fact
        else:
            for name in linked:
                row_check = parsed[name].directions[i]
                directions += row_check.checked
                if row_check.conflict:
                    findings.append(_direction_finding(shown, said, *row_check.conflict))
                    break
    return {"schema_version": 1, "findings": findings[:MAX_FINDINGS], "more": max(0, len(findings) - MAX_FINDINGS),
            "checked": {"sentences": len(sentences), "numbers": numbers, "directions": directions,
                        "tables": sorted(parsed), "unread_tables": unread}}


def _plain_file(base: Path, relative: str) -> Path | None:
    try:
        parts = PurePosixPath(relative).parts
        if not parts or ".." in parts or PurePosixPath(relative).is_absolute():
            return None
        path = Path(base)
        for part in parts:
            path = path / part
            if path.is_symlink():
                return None
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


def tables_for(report: str, results: Mapping[str, Any]) -> dict[str, _LocalTable | None]:
    """Locate each linked result table in the step folder that produced it.

    A link names ``steps/<step>/<output>``, ``<workdir_id>/<output>`` or just ``<output>``; only an output a step
    declared is used, from that step's own folder, without following links. Unresolved tables map to None."""
    wanted = {target.replace("\\", "/").removeprefix("./") for target in _targets(report)
              if PurePosixPath(target).suffix.casefold() in TABLE_SUFFIXES and "://" not in target}
    found: dict[str, _LocalTable | None] = {}
    for step_id, result in (results or {}).items():
        get = result.get if isinstance(result, Mapping) else (lambda key, r=result: getattr(r, key, None))
        workdir, outputs = get("workdir"), get("outputs") or []
        if not workdir:
            continue
        owner = str(get("workdir_id") or step_id)
        for raw in outputs:
            output = str(raw).replace("\\", "/").removeprefix("./")
            for link in wanted:
                if link in {output, f"{owner}/{output}", f"steps/{step_id}/{output}"} and link not in found:
                    path = _plain_file(Path(workdir), output)
                    if path is not None:
                        found[link] = _LocalTable(Path(workdir), output)
    return {**{link: None for link in sorted(wanted)}, **found}


def correction_prompt(findings: list[dict[str, Any]], report: str) -> str:
    """The writer's one chance to fix what the check found (#1374): sentences only, never the analysis."""
    lines = [f"{i}. [{row['kind']}] {row['detail']} — \"{row['sentence']}\"" for i, row in enumerate(findings, 1)]
    return "\n".join([
        "labhq compared the report below with the result tables it links and found sentences that do not match them.",
        "For each item, correct the sentence so its numbers and its direction (increase/decrease, higher/lower) match "
        "the linked table exactly, or remove the number if the table does not contain it. Do not redo the analysis, "
        "change a finding, add a conclusion, or remove or change any link, heading or claim anchor [[claim:...]]. "
        "Return the complete corrected report.", "", "Mismatches:", *lines, "", "Report to correct:", report])


def front_lines(consistency: Mapping[str, Any] | None, receipt: Mapping[str, Any] | None, *,
                hpc: bool = False) -> list[str]:
    """The status lines at the top of every report surface."""
    lines = []
    if isinstance(consistency, Mapping) and "findings" in consistency:
        left = len(consistency.get("findings") or []) + int(consistency.get("more") or 0)
        checked = consistency.get("checked") or {}
        unread = list(checked.get("unread_tables") or [])
        if unread:
            lines.append(f"- 본문·결과표 대조: 연결된 표 {len(unread)}개는 읽지 못해 대조하지 않았습니다"
                         f"({', '.join(unread[:3])}{' 외' if len(unread) > 3 else ''})")
        if left:
            lines.append(f"- 상태: **검증 전 초안** — 본문과 결과표가 맞지 않는 곳 {left}건(실행 기록 참조)")
        elif not unread:  # a table that was never read is not a clean result
            lines.append(f"- 본문·결과표 대조: 불일치 없음(문장 {checked.get('sentences', 0)}, 수치 "
                         f"{checked.get('numbers', 0)}, 방향 {checked.get('directions', 0)}건 확인; 읽지 못한 문장은 건너뜀)")
    execution = (receipt or {}).get("execution") if isinstance(receipt, Mapping) else None
    rerun = (receipt or {}).get("rerun") if isinstance(receipt, Mapping) else None
    if isinstance(rerun, Mapping) and rerun.get("status"):  # the PI's `labhq verify --rerun` (#1374)
        status = str(rerun["status"])
        checked = str(rerun.get("checked_at") or "")[:10]
        lines.append(f"- 재현 재실행: {RERUN_FRONT.get(status, status)}" + (f" ({checked})" if checked else ""))
    elif execution != "pass":
        lines.append("- 재현 실행 안 함" + ("(HPC)" if hpc else "") +
                     (f" — 영수증 실행 상태 {execution}" if execution else " — 재현 스크립트를 다시 실행한 기록이 없습니다"))
    return lines


RERUN_FRONT = {"pass": "재현됨(산출 hash 일치)", "partial": "일부 단계만 재현(나머지는 HPC 등으로 실행 안 함)",
               "hash_mismatch": "**산출 hash 불일치**", "missing_output": "**산출 없음**", "fail": "**스크립트 실패**",
               "timeout": "**시간 초과**", "unavailable": "실행 도구 없음", "wrote_outside": "**임시 폴더 밖 파일이 바뀜**",
               "hpc": "재현 실행 안 함(HPC)", "inputs_too_large": "입력이 커서 실행 안 함",
               "not_located": "작업 폴더를 찾지 못해 실행 안 함", "not_rerun": "실행한 단계 없음",
               "script_changed": "**스크립트가 실행 뒤 바뀜**", "input_changed": "**선행 산출이 기록과 다름**"}


def record_lines(consistency: Mapping[str, Any] | None) -> str:
    """The execution-record section listing what is left."""
    if not isinstance(consistency, Mapping) or not consistency.get("findings"):
        return ""
    rows = [f"- [{row['kind']}] {row['detail']} — \"{row['sentence']}\"" for row in consistency["findings"]]
    more = int(consistency.get("more") or 0)
    return "## 본문·결과표 대조\n\n" + "\n".join(rows) + (f"\n- 외 {more}건" if more else "")


MIN_KEPT_SHARE = 0.8  # a correction rewrites sentences; one that drops a fifth of the report is not a correction


def structure(text: str) -> tuple[list[str], list[str], list[str]]:
    """What a sentence-level correction must leave as it was: the links, the headings and the claim anchors."""
    body = _CODE.sub(" ", text)
    headings = [line.strip() for line in body.splitlines() if re.match(r"\s{0,3}#{1,6}\s", line)]
    # In document order: a fix that swaps two sentences' links or anchors keeps the multiset but not the order.
    marks = [m.group(0) for m in re.finditer(r"\[\[[^\]]*\]\]|!?\[[^\]]*\]\([^)]*\)", body)]
    return ([mark for mark in marks if not mark.startswith("[[")], headings,
            [mark for mark in marks if mark.startswith("[[")])


async def correct_once(body: str, results: Mapping[str, Any], run_fix, accept=None) -> tuple[str, dict[str, Any]]:
    """Check ``body``; with findings, ask the writer once (``run_fix(prompt) -> text | None``) and keep the fix only
    when it leaves fewer findings, keeps every link, heading and claim anchor and nearly all of the text (removing a
    table link would "fix" a number by hiding it), and ``accept(text)`` (the caller's own checks) allows it."""
    found = check(body, tables_for(body, results))
    if not found["findings"]:
        return body, found
    fixed = await run_fix(correction_prompt(found["findings"], body))
    if not isinstance(fixed, str) or not fixed.strip():
        return body, {**found, "correction": "not_returned"}
    if structure(fixed) != structure(body) or len(fixed.strip()) < MIN_KEPT_SHARE * len(body.strip()):
        return body, {**found, "correction": "rejected_structure"}
    again = check(fixed, tables_for(fixed, results))
    if (len(again["findings"]) + again["more"] < len(found["findings"]) + found["more"]
            and (accept is None or accept(fixed))):
        return fixed, {**again, "correction": "applied", "before": len(found["findings"]) + found["more"]}
    return body, {**found, "correction": "rejected"}
