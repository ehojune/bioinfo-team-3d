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
_MARKDOWN_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.DOTALL)
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
    evidence_notes: list[str] = field(default_factory=list)
    execution_record: str = ""
    omissions: dict[str, str] = field(default_factory=dict)
    source_report: str = ""
    required: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReportPackage":
        data = dict(value)
        for key in ("figures", "tables", "files"):
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


def _title(report: str, request: dict[str, Any]) -> str:
    found = _HEADING.search(report)
    return _plain(found.group(2)) if found else _plain(str(request.get("text") or ""))[:120] or "결과 보고서"


def _section_blocks(report: str) -> list[tuple[str, str]]:
    matches = list(_HEADING.finditer(report))
    return [(match.group(2).strip().casefold(), report[match.end(): matches[index + 1].start()
                                                        if index + 1 < len(matches) else len(report)].strip())
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


def _without_markdown_code(report: str) -> str:
    return _MARKDOWN_CODE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        report,
    )


def _markdown_links(report: str):
    return _MARKDOWN_LINK.finditer(_without_markdown_code(report))


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
        value.strip() for value in _ANCHOR.findall(_without_markdown_code(source))
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
        question=_plain(str(request.get("text") or "질문 기록 없음"))[:1000],
        answer=_first_text(source, ("요약", "abstract", "결론", "결과"),
                           "요청을 완료하지 못했습니다." if kind == "failure" else "답이 기록되지 않았습니다."),
        limitations=_first_text(source, ("한계", "limitation", "논의", "discussion"),
                                str(request.get("error") or "해석 범위는 본문을 확인하세요.")),
        figures=figures,
        tables=tables,
        files=files,
        reproduction="README.md#다시-실행" if kind != "failure" else "",
        evidence_notes=anchors,
        execution_record="execution_record.md",
        omissions=omissions,
        source_report=source,
        required=dict(REQUIRED_BY_KIND[kind]),
    )


def _asset_lines(rows: list[ReportAsset], omission: str, label: str, *, image: bool = False) -> list[str]:
    if rows:
        return [f"![{row.label}]({row.path})" if image else f"- [{row.label}]({row.path})" for row in rows]
    return [f"- 해당 없음 — {omission or '이 항목이 기록되지 않았습니다.'}"]


def render_report_front(package: ReportPackage, *, figures_as_images: bool = False) -> str:
    """Compact first screen shared by the stored report, bundle, edition, PDF and web view."""
    evidence = ([f"- [근거 {index}] 근거 원장 `{value}`" for index, value in enumerate(package.evidence_notes, 1)]
                or ["- 근거 각주가 없습니다."])
    reproduction = (f"- [요청 묶음 재현 안내]({package.reproduction})" if package.reproduction else
                    "- 해당 없음 — 실패 요청은 재현 절차를 요구하지 않습니다.")
    execution = (f"- [실행 기록]({package.execution_record})" if package.execution_record else "- 실행 기록이 없습니다.")
    sections: list[tuple[str, list[str]]] = [
        ("질문", [package.question]),
        ("답", [package.answer]),
        ("한계", [package.limitations]),
        ("대표 그림", _asset_lines(package.figures, package.omissions.get("figures", ""), "대표 그림",
                                image=figures_as_images)),
        ("결과표", _asset_lines(package.tables, package.omissions.get("tables", ""), "결과표")),
        ("재현 안내", [reproduction]),
        ("근거 각주", evidence),
        ("실행 기록", [execution]),
    ]
    return "\n\n".join(f"## {title}\n\n" + "\n".join(lines) for title, lines in sections) + "\n"


def with_report_front(report: str, package: ReportPackage) -> str:
    source = _without_front(report).strip()
    title = re.match(r"^#\s+.+(?:\n|$)", source)
    lead = title.group(0).strip() if title else f"# {package.title}"
    body = source[title.end():].lstrip() if title else source
    return f"{lead}\n\n{FRONT_START}\n\n{render_report_front(package)}\n{FRONT_END}\n\n{body}".rstrip() + "\n"


def reader_markdown(report: str) -> str:
    """Replace audit-only claim syntax while retaining a numbered evidence trail."""
    seen: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        value = match.group(1).strip()
        seen.setdefault(value, len(seen) + 1)
        return f"[근거 {seen[value]}]"

    rendered = _ANCHOR.sub(replace, report)
    if seen and FRONT_START not in rendered:
        notes = "\n".join(f"- [근거 {number}] 근거 원장 `{value}`" for value, number in seen.items())
        rendered = rendered.rstrip() + "\n\n## 근거 각주\n\n" + notes + "\n"
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
    scope = ""
    problems: list[str] = []
    masked = _without_markdown_code(report)
    for line in masked.splitlines():
        heading = _HEADING.match(line)
        if heading:
            scope = heading.group(2).strip().casefold()
        for match in _MARKDOWN_LINK.finditer(line):
            path = _target(match.group("target"))
            if path and PurePosixPath(path).suffix.casefold() in _AUDIO_SUFFIXES \
                    and not any(word in scope for word in ("결과", "result")):
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
        if _ANCHOR.search(document or ""):
            problems.append({"code": "internal_marker", "field": name,
                             "message": f"독자 화면에 내부 claim 표기가 남았습니다: {name}"})
    return {"status": "FAIL" if problems else "PASS", "schema_version": package.schema_version,
            "kind": package.kind, "problems": problems}
