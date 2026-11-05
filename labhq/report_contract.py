"""Common result structure and pre-publication checks for every report surface.

The source report keeps its claim ledger anchors for audit.  Reader surfaces use
``reader_markdown`` so those internal markers become short evidence notes.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from .report_markdown import markdown_body, render_marker_message, rendered_markers, replace_markdown_body
from .reproduction_receipt import receipt_note, receipt_summary


FRONT_SECTION_TITLES = (
    "질문",
    "답",
    "한계",
    "대표 그림",
    "결과표",
    "재현 안내",
    "근거 각주",
    "실행 기록",
)
PIPELINE_TITLE = "분석 파이프라인"  # between 답 and 한계 when labhq drew the executed steps (#756)
FRONT_START = "<!-- labhq-report-package:v1 -->"
FRONT_END = "<!-- labhq-report-package:end -->"

_ANCHOR = re.compile(r"\[\[\s*claim\s*:([^\[\]\n]+)\]\]", re.I)
_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


def _balanced_link_target(depth: int = 4) -> str:
    inner = r"[^()\s]*"
    for _ in range(depth - 1):
        inner = rf"(?:[^()\s]|\({inner}\))*"
    return rf"(?:[^()\s]|\({inner}\))+"


_MARKDOWN_LINK = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\]\n]*)\]\(\s*(?P<target><[^>\n]*>|" + _balanced_link_target()
    + r")(?P<title>\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
_CODE_RUN = re.compile(r"`+|~{3,}")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_FOOTNOTE_DEFINITION = re.compile(r"^\[\^([^\]\n]+)\]:\s*(.*)$")
_FOOTNOTE_REFERENCE = re.compile(r"\[\^([^\]\n]+)\]")
_QUESTION_CUTOFF = re.compile(
    r"(?im)^\s*(?:reference pointers?\b.*|참고\s*(?:자료\s*)?경로\b.*|운영\s*지시\b.*|"
    r"PI가 이미 답한 질문\b.*|never write\b.*)$"
)
_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
_FIGURE_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TABLE_SUFFIXES = {".tsv", ".csv", ".xlsx", ".xls"}

REQUIRED_BY_KIND: dict[str, dict[str, str]] = {
    "research": {
        "question": "required",
        "answer": "required",
        "limitations": "required",
        "figures": "required_or_excluded",
        "tables": "required_or_excluded",
        "reproduction": "required",
        "evidence_notes": "required",
        "execution_record": "required",
    },
    "general": {
        "question": "required",
        "answer": "required",
        "limitations": "required",
        "figures": "optional",
        "tables": "optional",
        "reproduction": "required_or_excluded",
        "evidence_notes": "optional",
        "execution_record": "required",
    },
    "failure": {
        "question": "required",
        "answer": "required",
        "limitations": "required",
        "figures": "not_applicable",
        "tables": "not_applicable",
        "reproduction": "not_applicable",
        "evidence_notes": "optional",
        "execution_record": "required",
    },
}


@dataclass(frozen=True)
class ReportAsset:
    label: str
    path: str


@dataclass
class ReportPackage:
    schema_version: int
    kind: str
    title: str
    question: str
    answer: str
    limitations: str
    figures: list[ReportAsset] = field(default_factory=list)
    tables: list[ReportAsset] = field(default_factory=list)
    files: list[ReportAsset] = field(default_factory=list)
    reproduction: str = ""
    reproduction_receipt: dict[str, Any] = field(default_factory=dict)
    evidence_notes: list[str] = field(default_factory=list)
    execution_record: str = ""
    omissions: dict[str, str] = field(default_factory=dict)
    source_report: str = ""
    required: dict[str, str] = field(default_factory=dict)
    markdown_warnings: dict[str, list[str]] = field(default_factory=dict)
    pipeline: list[ReportAsset] = field(default_factory=list)
    pipeline_note: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReportPackage":
        data = dict(value)
        for key in ("figures", "tables", "files", "pipeline"):
            data[key] = [item if isinstance(item, ReportAsset) else ReportAsset(**item)
                         for item in data.get(key) or []]
        return cls(**data)


def _without_front(report: str) -> str:
    start = report.find(FRONT_START)
    if start < 0:
        return report
    end = report.find(FRONT_END, start)
    return report if end < 0 else (report[:start].rstrip() + "\n\n" + report[end + len(FRONT_END):].lstrip())


def _plain(value: str) -> str:
    value = _ANCHOR.sub("", value)
    value = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[`*_>#]", "", value)
    return " ".join(value.split()).strip()


def research_question(value: str) -> str:
    """Return the reader-facing scientific question, without appended path or operating instructions."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if cutoff := _QUESTION_CUTOFF.search(text):
        text = text[:cutoff.start()]
    return _plain(text)[:1000] or "질문 기록 없음"


def _title(report: str, request: dict[str, Any]) -> str:
    found = _HEADING.search(markdown_body(report))
    return _plain(found.group(2)) if found else _plain(str(request.get("text") or ""))[:120] or "결과 보고서"


def _section_blocks(report: str) -> list[tuple[str, str]]:
    body = markdown_body(report)
    matches = list(_HEADING.finditer(body))
    return [(match.group(2).strip().casefold(), body[match.end(): matches[index + 1].start()
                                                      if index + 1 < len(matches) else len(body)].strip())
            for index, match in enumerate(matches)]


def _first_text(report: str, names: tuple[str, ...], fallback: str) -> str:
    for heading, body in _section_blocks(report):
        if any(name in heading for name in names):
            for block in re.split(r"\n\s*\n", body):
                plain = _plain(block)
                if plain and not block.lstrip().startswith(("|", "![", "```", "~~~")):
                    return plain[:1000]
    return _plain(fallback)[:1000]


def _target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    split = urlsplit(value)
    if split.scheme or split.netloc or value.startswith("#"):
        return ""
    path = unquote(split.path).replace("\\", "/")
    try:
        clean = PurePosixPath(path).as_posix()
    except ValueError:
        return ""
    return clean.removeprefix("./")


def _markdown_links(report: str):
    return _MARKDOWN_LINK.finditer(markdown_body(report))


def _assets(report: str) -> tuple[list[ReportAsset], list[ReportAsset], list[ReportAsset]]:
    figures: list[ReportAsset] = []
    tables: list[ReportAsset] = []
    files: list[ReportAsset] = []
    for match in _markdown_links(report):
        path = _target(match.group("target"))
        if not path:
            continue
        suffix = PurePosixPath(path).suffix.casefold()
        asset = ReportAsset(_plain(match.group("label")) or PurePosixPath(path).name, path)
        if match.group("image"):
            if suffix in _FIGURE_SUFFIXES:
                figures.append(asset)
        else:
            (tables if suffix in _TABLE_SUFFIXES else files).append(asset)
    unique = lambda rows: list({row.path: row for row in rows}.values())
    return unique(figures), unique(tables), unique(files)


def report_kind(request: dict[str, Any]) -> str:
    if request.get("status") in {"failed", "cancelled", "rejected"} or request.get("error"):
        return "failure"
    return "research" if request.get("research_contract") else "general"


def build_report_package(request: dict[str, Any], report: str | None = None,
                         results: dict[str, Any] | None = None) -> ReportPackage:
    """Derive the stable front-page structure without changing the audited source report."""
    source = _without_front(str(report if report is not None else request.get("report") or ""))
    kind = report_kind(request)
    figures, tables, files = _assets(source)
    anchors = list(dict.fromkeys(
        value.strip() for value in _ANCHOR.findall(markdown_body(source))
    ))
    omissions: dict[str, str] = {}
    if kind == "research" and not figures:
        omissions["figures"] = "보고서가 대표 그림을 참조하지 않았습니다."
    if kind == "research" and not tables:
        omissions["tables"] = "보고서가 공개 결과표를 참조하지 않았습니다."
    if kind == "general":
        omissions["reproduction"] = "일반 응답은 독립 재실행 대상이 아닐 수 있습니다."
    return ReportPackage(
        schema_version=1,
        kind=kind,
        title=_title(source, request),
        question=research_question(str(request.get("text") or "")),
        answer=_first_text(source, ("요약", "abstract", "결론", "결과"),
                           "요청을 완료하지 못했습니다." if kind == "failure" else "답이 기록되지 않았습니다."),
        limitations=_first_text(source, ("한계", "limitation", "논의", "discussion"),
                                str(request.get("error") or "해석 범위는 본문을 확인하세요.")),
        figures=figures,
        tables=tables,
        files=files,
        reproduction="README.md#다시-실행" if kind != "failure" else "",
        reproduction_receipt=receipt_summary(request.get("reproduction_receipt")),
        evidence_notes=anchors,
        execution_record="execution_record.md",
        omissions=omissions,
        source_report=source,
        required=dict(REQUIRED_BY_KIND[kind]),
        markdown_warnings={step_id: result["markdown_warnings"] for step_id, result in (results or {}).items()
                           if isinstance(result, dict) and result.get("markdown_warnings")},
    )


def _asset_lines(rows: list[ReportAsset], omission: str, label: str, *, image: bool = False) -> list[str]:
    if rows:
        return [f"![{row.label}]({row.path})" if image else f"- [{row.label}]({row.path})" for row in rows]
    return [f"- 해당 없음 — {omission or '이 항목이 기록되지 않았습니다.'}"]


def render_report_front(package: ReportPackage, *, figures_as_images: bool = False,
                        pipeline_images: bool = False) -> str:
    """Compact first screen shared by the stored report, bundle, edition, PDF and web view."""
    evidence = ([f"- [근거 {index}] 근거 원장 `{value}`" for index, value in enumerate(package.evidence_notes, 1)]
                or ["- 근거 각주가 없습니다."])
    reproduction = (f"- [요청 묶음 재현 안내]({package.reproduction})" if package.reproduction else
                    "- 해당 없음 — 실패 요청은 재현 절차를 요구하지 않습니다.")
    if package.reproduction_receipt:
        reproduction += "\n- " + receipt_note(package.reproduction_receipt)
        if path := package.reproduction_receipt.get("path"):
            reproduction += f"\n- [재현 검증 receipt]({path})"
    execution = (f"- [실행 기록]({package.execution_record})" if package.execution_record else "- 실행 기록이 없습니다.")
    sections: list[tuple[str, list[str]]] = [
        ("질문", [package.question]),
        ("답", [package.answer]),
        *([(PIPELINE_TITLE, pipeline_lines(package, images=pipeline_images))] if package.pipeline else []),
        ("한계", [package.limitations]),
        ("대표 그림", _asset_lines(package.figures, package.omissions.get("figures", ""), "대표 그림",
                                image=figures_as_images)),
        ("결과표", _asset_lines(package.tables, package.omissions.get("tables", ""), "결과표")),
        ("재현 안내", [reproduction]),
        ("근거 각주", evidence),
        ("실행 기록", [execution]),
    ]
    return "\n\n".join(f"## {title}\n\n" + "\n".join(lines) for title, lines in sections) + "\n"


def pipeline_lines(package: ReportPackage, *, images: bool = False) -> list[str]:
    """The drawn pipeline and timeline, then the notes. Markdown links them so the graphical abstract stays the first
    figure (#608); the PDF, which cannot follow a link, shows them."""
    shown = [f"![{row.label}]({row.path})" if images else f"- [{row.label}]({row.path})" for row in package.pipeline]
    return shown + [f"- {note}" for note in package.pipeline_note]


def with_pipeline_section(report: str, package: ReportPackage, *, images: bool = False) -> str:
    """Add the pipeline section to a front rendered without one (a stored bundle report), before its 한계."""
    start = report.find(FRONT_START)
    end = report.find(FRONT_END, start)
    if not package.pipeline or start < 0 or end < 0 or f"## {PIPELINE_TITLE}\n" in report[start:end]:
        return report
    limit = report.find("\n## 한계\n", start, end)
    at = limit + 1 if limit >= 0 else end
    section = f"## {PIPELINE_TITLE}\n\n" + "\n".join(pipeline_lines(package, images=images)) + "\n\n"
    return report[:at] + section + report[at:]


def with_report_front(report: str, package: ReportPackage, *, pipeline_images: bool = False) -> str:
    source = _without_front(report).strip()
    title = re.match(r"^#\s+.+(?:\n|$)", source)
    lead = title.group(0).strip() if title else f"# {package.title}"
    body = source[title.end():].lstrip() if title else source
    front = render_report_front(package, pipeline_images=pipeline_images)
    return f"{lead}\n\n{FRONT_START}\n\n{front}\n{FRONT_END}\n\n{body}".rstrip() + "\n"


def code_spans(text: str) -> list[tuple[int, int]]:
    """The `(start, end)` of every fenced and inline code span in `text`, in order and without overlap.

    A fence opens with three or more backticks or tildes and closes with a run of the same character at least as long
    (a four-backtick fence holds a three-backtick one: the progress record's JSON blocks do, #1193); a fence that never
    closes is not a fence. A long run of backticks with nothing to close it costs a little more than linear (20,000 in
    a row take about 0.1 s), which no real document has."""
    spans: list[tuple[int, int]] = []
    position = 0
    size = len(text)
    unclosed: dict[str, int] = {}  # the shortest run length of a character that found no closing run
    while position < size:
        run = _CODE_RUN.search(text, position)
        if run is None:
            break
        char, length = text[run.start()], run.end() - run.start()
        if length >= 3 and length < unclosed.get(char, size + 1):
            close = _closing_fence(text, char, length, run.end())
            if close is None:  # the position only moves on, so no later run of this length or more can close either
                unclosed[char] = length
            else:
                spans.append((run.start(), close))
                position = close
                continue
        if char == "`":
            inline = _INLINE_CODE.match(text, run.start())
            if inline is not None:
                spans.append((inline.start(), inline.end()))
                position = inline.end()
                continue
        position = run.end()
    return spans


def mask_code(text: str) -> str:
    """`text` with every code span blanked out to spaces, so a link inside code is not a link."""
    out: list[str] = []
    last = 0
    for start, end in code_spans(text):
        out.append(text[last:start])
        out.append(" " * (end - start))
        last = end
    out.append(text[last:])
    return "".join(out)


def _closing_fence(text: str, char: str, length: int, start: int) -> int | None:
    """The end of the first run of `char` at least `length` long at or after `start`, else None."""
    found = re.compile(re.escape(char) + "{" + str(length) + ",}").search(text, start)
    return None if found is None else found.end()


def reader_markdown(report: str, *, footnotes: bool = False) -> str:
    """Replace audit-only claim syntax; PDFs may also render Markdown footnotes as endnotes."""
    code: list[str] = []

    def stash(value: str) -> str:
        code.append(value)
        return f"\x00labhq-code-{len(code) - 1}\x00"

    rendered = report
    if footnotes:
        pieces: list[str] = []
        last = 0
        for start, end in code_spans(rendered):
            pieces.append(rendered[last:start])
            pieces.append(stash(rendered[start:end]))
            last = end
        pieces.append(rendered[last:])
        rendered = "".join(pieces)
        definitions: dict[str, str] = {}
        body: list[str] = []
        for line in rendered.splitlines():
            found = _FOOTNOTE_DEFINITION.match(line)
            if found:
                definitions.setdefault(found.group(1).strip(), found.group(2).strip())
            else:
                body.append(line)
        rendered = "\n".join(body)
        numbered: dict[str, int] = {}

        def replace_footnote(match: re.Match[str]) -> str:
            value = match.group(1).strip()
            numbered.setdefault(value, len(numbered) + 1)
            return f"[근거 {numbered[value]}]"

        rendered = _FOOTNOTE_REFERENCE.sub(replace_footnote, rendered)
        if numbered:
            notes = "\n".join(
                f"{number}. {definitions.get(value, '근거 원장은 저장소의 docs/reference/claim_evidence.md에 있습니다.')}"
                for value, number in numbered.items()
            )
            rendered = rendered.rstrip() + "\n\n## Claim 근거\n\n" + notes + "\n"

    seen: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        value = match.group(1).strip()
        seen.setdefault(value, len(seen) + 1)
        return f"[근거 {seen[value]}]"

    rendered = replace_markdown_body(rendered, lambda body: _ANCHOR.sub(replace, body))
    if seen and FRONT_START not in rendered:
        notes = "\n".join(f"- [근거 {number}] 근거 원장 `{value}`" for value, number in seen.items())
        rendered = rendered.rstrip() + "\n\n## 근거 각주\n\n" + notes + "\n"
    for index, value in enumerate(code):
        rendered = rendered.replace(f"\x00labhq-code-{index}\x00", value)
    return rendered


def available_result_files(results: dict[str, Any] | None) -> set[str]:
    available: set[str] = set()
    for step_id, result in (results or {}).items():
        if not isinstance(result, dict):
            continue
        owner = str(result.get("workdir_id") or step_id)
        for raw in result.get("outputs") or []:
            path = str(raw).replace("\\", "/").removeprefix("./")
            available.update({path, f"{owner}/{path}", f"steps/{step_id}/{path}"})
    return available


def _table_shape(text: str, suffix: str) -> bool:
    delimiter = "\t" if suffix == ".tsv" else ","
    try:
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)
                if any(cell.strip() for cell in row)]
    except (csv.Error, TypeError):
        return False
    return bool(rows and rows[0] and all(rows[0]) and len(set(rows[0])) == len(rows[0])
                and all(len(row) == len(rows[0]) for row in rows[1:]))


def _audio_outside_results(report: str) -> list[str]:
    scopes: list[tuple[int, str]] = []
    problems: list[str] = []
    masked = markdown_body(report)
    for line in masked.splitlines():
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            scopes = [(old_level, title) for old_level, title in scopes if old_level < level]
            scopes.append((level, heading.group(2).strip().casefold()))
        for match in _MARKDOWN_LINK.finditer(line):
            path = _target(match.group("target"))
            if path and PurePosixPath(path).suffix.casefold() in _AUDIO_SUFFIXES \
                    and not any((level >= 2 or title in {"결과", "result", "results"}) and word in title
                                for level, title in scopes
                                for word in ("결과", "result")):
                problems.append(path)
    return problems


def validate_report_package(package: ReportPackage, *, available_files: set[str] | None = None,
                            file_contents: dict[str, str] | None = None,
                            reader_documents: dict[str, str] | None = None) -> dict[str, Any]:
    """Return deterministic problems before CP4, terminal reporting, bundling or publication."""
    problems: list[dict[str, str]] = []
    for field_name, rule in package.required.items():
        value = getattr(package, field_name)
        if rule == "required" and not value:
            problems.append({"code": "required_missing", "field": field_name,
                             "message": f"필수 항목이 없습니다: {field_name}"})
        if rule == "required_or_excluded" and not value and not package.omissions.get(field_name):
            problems.append({"code": "exclusion_reason_missing", "field": field_name,
                             "message": f"항목 또는 제외 사유가 필요합니다: {field_name}"})
    if available_files is not None:
        normal = {str(path).replace("\\", "/").removeprefix("./") for path in available_files}
        for kind, rows in (("figure", package.figures), ("table", package.tables), ("file", package.files)):
            for row in rows:
                if row.path not in normal and row.path.removeprefix("./") not in normal:
                    problems.append({"code": f"missing_{kind}", "field": row.path,
                                     "message": f"참조한 {kind} 파일이 없습니다: {row.path}"})
    for row in package.tables:
        if file_contents is not None and row.path in file_contents \
                and not _table_shape(file_contents[row.path], PurePosixPath(row.path).suffix.casefold()):
            problems.append({"code": "table_shape", "field": row.path,
                             "message": f"결과표의 열 또는 행 모양이 맞지 않습니다: {row.path}"})
    for path in _audio_outside_results(package.source_report):
        problems.append({"code": "audio_outside_results", "field": path,
                         "message": f"음성 결과는 결과 절에 두어야 합니다: {path}"})
    for name, document in (reader_documents or {}).items():
        for hit in rendered_markers(document or ""):
            internal = hit.marker == "[[" and _ANCHOR.search(markdown_body(document or ""))
            problems.append({
                "code": "internal_marker" if internal else "markdown_render",
                "field": name,
                "message": render_marker_message(name, hit),
            })
    for step_id, warnings in package.markdown_warnings.items():
        problems.extend({"code": "markdown_render", "field": step_id, "message": message} for message in warnings)
    return {"status": "FAIL" if problems else "PASS", "schema_version": package.schema_version,
            "kind": package.kind, "problems": problems}
