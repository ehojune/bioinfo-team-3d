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
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

TABLE_SUFFIXES = {".tsv", ".csv"}
MAX_TABLE_BYTES = 2_000_000  # a larger table is not read for the check (said so in the result)
MAX_FINDINGS = 40
# A Markdown link: "<...>" destinations may hold spaces (the report contract allows them), plain ones may not.
_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^<>\n]+)>|([^()\s<>]+))(?:\s+\"[^\"]*\")?\s*\)")
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_ANCHOR = re.compile(r"\[\[[^\]]*\]\]")
# Korean particles follow a number directly ("43%였고", "4.9였다"), so only Latin letters and digits end it badly.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-−–]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?\s*%?(?![A-Za-z0-9_.])")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|\n")
_UP = re.compile(r"증가|상향|높(?:았|다|은|게|아)|늘(?:었|어|은)|올라|커(?:졌|짐)|up-?regulat|\bincreas\w*|\bhigher\b|"
                 r"\belevat\w*|\binduc\w*|\bupward\b|\bgain\w*", re.I)
_DOWN = re.compile(r"감소|하향|낮(?:았|다|은|게|아)|줄(?:었|어|은)|떨어|작아|down-?regulat|\bdecreas\w*|\blower\b|"
                   r"\breduc\w*|\brepress\w*|\bsuppress\w*|\bdownward\b|\bloss\b", re.I)
_LOG_EFFECT = re.compile(r"\b(?:log2\s*\(?\s*fc\)?|log2\s*fold[\s-]*change|log2FoldChange|l2fc|lfc|logfc|log\s*fc)\b"
                         r"\s*(?:\(|=|:|of|은|는|이|가|값)?\s*([-−–]?\d+(?:\.\d+)?)", re.I)
_RATIO_EFFECT = re.compile(r"\b(?:HR|OR|RR|IRR|hazard\s+ratio|odds\s+ratio|risk\s+ratio|fold[\s-]*change|FC)\b"
                           r"\s*(?:\(|=|:|of|은|는|이|가|값)?\s*(\d+(?:\.\d+)?)", re.I)
# Words that flip what "up" means for a ratio (a hazard ratio below 1 is a lower risk but a longer survival).
_FLIPS = re.compile(r"생존|survival|\bOS\b|\bPFS\b|\bDFS\b|\bEFS\b|반응률|response|관해|remission", re.I)
_LOG_COLUMN = re.compile(r"^(?:log2[\s_.]*f(?:old)?[\s_.]*c(?:hange)?|log2fc|l2fc|lfc|logfc|log2foldchange)$", re.I)
_YEARS = range(1900, 2101)


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


def _linked_tables(sentence: str, tables: Mapping[str, list[list[str]]]) -> list[str]:
    found = []
    for target in _targets(sentence):
        path = target.replace("\\", "/").removeprefix("./")
        for name in tables:
            if path == name or path.endswith("/" + name) or name.endswith("/" + path):
                found.append(name)
    return list(dict.fromkeys(found))


def _table_numbers(rows: list[list[str]]) -> list[float]:
    return [value for row in rows for cell in row if (value := _value(cell)) is not None]


def _matches(text: str, value: float, pool: Iterable[float]) -> str | None:
    """"exact" when the sentence's number is a table value as the sentence rounds it (or a fraction shown as %),
    "sign" when only its magnitude is (the sentence flipped or dropped the sign), else None."""
    tolerance = 0.5 * 10 ** -_decimals(text) + 1e-9
    targets = [(value, tolerance)] + ([(value / 100, tolerance / 100)] if text.strip().endswith("%") else [])
    pool = list(pool)
    if any(abs(number - target) <= allowed for number in pool for target, allowed in targets):
        return "exact"
    if any(abs(abs(number) - abs(target)) <= allowed for number in pool for target, allowed in targets):
        return "sign"
    return None


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
    up, down = bool(_UP.search(sentence)), bool(_DOWN.search(sentence))
    return "up" if up and not down else "down" if down and not up else None


def _row_effects(sentence: str, tables: Mapping[str, list[list[str]]], names: list[str]) -> list[tuple[str, float]]:
    """(identifier, log fold change) of each linked table row whose identifier the sentence names."""
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}", sentence))
    effects = []
    for name in names:
        rows = tables[name]
        if not rows:
            continue
        header = [cell.strip() for cell in rows[0]]
        columns = [i for i, cell in enumerate(header) if _LOG_COLUMN.fullmatch(cell)]
        if not columns:
            continue
        for row in rows[1:]:
            ids = [cell.strip() for i, cell in enumerate(row) if i not in columns and cell.strip() in words]
            if ids and (value := _value(row[columns[0]] if columns[0] < len(row) else "")) is not None:
                effects.append((ids[0], value))
    return effects


def check(report: str, tables: Mapping[str, str | None]) -> dict[str, Any]:
    """``tables`` maps a report link path to the table's text. Returns ``{"findings": [...], "checked": {...}}``."""
    parsed: dict[str, list[list[str]]] = {}
    unread = []
    for name, text in tables.items():
        suffix = PurePosixPath(name).suffix.casefold()
        if text is None:  # linked but not readable here (too large, not a declared output, missing): not checked
            unread.append(name)
            continue
        try:
            parsed[name] = [row for row in csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
                            if any(cell.strip() for cell in row)]
        except csv.Error:
            unread.append(name)
    pools = {name: _table_numbers(rows) for name, rows in parsed.items()}
    findings: list[dict[str, Any]] = []
    sentences = numbers = directions = 0
    for sentence in _sentences(report):
        sentences += 1
        shown = re.sub(r"\s+", " ", sentence)[:240]
        linked = _linked_tables(sentence, parsed)
        if linked:
            pool = [value for name in linked for value in pools[name]]
            worded = _direction(sentence) is not None
            for raw, value in _checkable_numbers(sentence):
                numbers += 1
                matched = _matches(raw, value, pool)
                if matched is None:
                    findings.append({"kind": "number", "sentence": shown, "value": raw, "tables": linked,
                                     "detail": f"본문 수치 {raw}이(가) 연결된 표({', '.join(linked)})에 없습니다"})
                elif matched == "sign" and not worded:
                    # "2.1배 감소" may drop a sign the words carry (checked below); a bare "log2FC 1.8" may not.
                    findings.append({"kind": "sign", "sentence": shown, "value": raw, "tables": linked,
                                     "detail": f"본문 수치 {raw}은(는) 연결된 표의 값과 부호가 다릅니다"})
        said = _direction(sentence)
        if said is None:
            continue
        effects = [("log2FC", value, value > 0, value < 0) for value in
                   (_value(m.group(1)) for m in _LOG_EFFECT.finditer(sentence)) if value is not None]
        flipped = bool(_FLIPS.search(sentence))  # HR < 1 means survival or response went up, risk went down
        effects += [(m.group(0).split()[0], value, (value < 1) if flipped else (value > 1),
                     (value > 1) if flipped else (value < 1)) for m in _RATIO_EFFECT.finditer(sentence)
                    if (value := _value(m.group(1))) is not None]
        effects += [(identifier, value, value > 0, value < 0)
                    for identifier, value in _row_effects(sentence, parsed, linked)]
        for label, value, up, down in effects:
            directions += 1
            if (said == "up" and down) or (said == "down" and up):
                findings.append({"kind": "direction", "sentence": shown, "value": f"{label} {value:g}",
                                 "detail": f"본문은 {'증가' if said == 'up' else '감소'}라고 썼지만 "
                                           f"{label} {value:g}은(는) 반대 방향입니다"})
                break  # one direction finding per sentence: the stated value and its table row are the same fact
    return {"schema_version": 1, "findings": findings[:MAX_FINDINGS], "more": max(0, len(findings) - MAX_FINDINGS),
            "checked": {"sentences": sentences, "numbers": numbers, "directions": directions,
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
        return path if path.is_file() and path.stat().st_size <= MAX_TABLE_BYTES else None
    except (OSError, ValueError):
        return None


def tables_for(report: str, results: Mapping[str, Any]) -> dict[str, str | None]:
    """The text of every result table the report links, read from the step folder that produced it.

    A link names ``steps/<step>/<output>``, ``<workdir_id>/<output>`` or just ``<output>``; only an output a step
    declared is read, from that step's own folder, without following links. A linked table that cannot be read that
    way (not declared, too large, a link, missing) maps to None, so the check reports it as not compared."""
    wanted = {target.replace("\\", "/").removeprefix("./") for target in _targets(report)
              if PurePosixPath(target).suffix.casefold() in TABLE_SUFFIXES and "://" not in target}
    found: dict[str, str | None] = {}
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
                        try:
                            found[link] = path.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            pass
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
    if execution != "pass":
        lines.append("- 재현 실행 안 함" + ("(HPC)" if hpc else "") +
                     (f" — 영수증 실행 상태 {execution}" if execution else " — 재현 스크립트를 다시 실행한 기록이 없습니다"))
    return lines


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
    return sorted(_targets(body)), headings, sorted(_ANCHOR.findall(body))


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
