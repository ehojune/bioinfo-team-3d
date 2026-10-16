"""A graphical abstract for a report that has none (#608).

One flow diagram of the whole project: question -> data -> methods and steps -> key findings. labhq draws it from what
the request already holds (its text, references, the plan's steps and the report's conclusion), so a request whose steps
made no figure still gets one. The picture is plain SVG: short Korean labels, the web office's design-token colors, a
system font stack, no script, no external font, no network. Every label is escaped, and the builder never raises on
odd input (an empty plan, a report without headings, markup in a step's instruction).
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from .report_figures import ABSTRACT_FILE, classify, image_links, is_abstract_target

CAPTION = "Graphical abstract — 질문 → 데이터 → 방법 → 핵심 결과"

# Light-theme values of labhq/web/ui/tokens.css.
PAPER, SURFACE, INK, INK_2, LINE = "#FAF8F4", "#FFFFFF", "#1C2430", "#47525F", "#E2DDD3"
COLUMNS = (  # (header, accent, soft background): accent, work, hpc and warn tokens
    ("1 질문", "#2848A6", "#E9EDF9"),
    ("2 데이터", "#1D6B40", "#E6F1EA"),
    ("3 방법·단계", "#7A3E9D", "#F1E9F6"),
    ("4 핵심 결과", "#7A4A00", "#FFF0D0"),
)
FONT = "'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif"

AGENT_LABELS = {
    "analyst": "분석", "bioinfo-agent": "반복 분석", "biologist": "생물학 해석", "chief_of_staff": "브리핑",
    "cso": "총괄", "data_steward": "데이터 확보", "editor": "정리", "engineer": "개발", "facilities": "실행 환경",
    "lit_scout": "문헌 조사", "qc_reviewer": "QC", "recruiter": "채용", "sci_reviewer": "과학 검토",
}

# Four stacked stages, top to bottom: the page is portrait, and 800 px wide the text is still about 9 pt once the picture
# is scaled to the width of an A4 page.
WIDTH, PAD, GAP = 800, 28, 30
BAND_W, TITLE_H, ROW_MIN, LINE_H = 130, 54, 62, 21
TEXT_X = PAD + BAND_W + 16
UNITS = 74  # one ASCII letter is 1 unit, a Hangul or other wide letter 2: at most 7.5 px per unit at 14 px (586 px)
FONT_PX = 14
MAX_STEPS, MAX_SOURCES, MAX_FINDINGS = 5, 5, 3

_XML_BAD = re.compile("[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_LINK = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")
_ANCHOR = re.compile(r"\[\[\s*claim\s*:[^\]\n]*\]\]", re.IGNORECASE)
_MARKS = re.compile(r"`|\*\*|__|~~|(?<!\w)\*|\*(?!\w)")
_SENTENCE = re.compile(r"(?<=[.!?。])\s+")
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_MARK = re.compile(r" +([.,;:!?)」』])")


def _esc(text: str) -> str:
    return html.escape(_XML_BAD.sub("", text), quote=True)


def _units(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def _width(text: str) -> int:
    return sum(_units(char) for char in text)


def _clip(text: str, units: int) -> str:
    """``text`` cut to ``units`` of width, with an ellipsis when something was cut."""
    if _width(text) <= units:
        return text
    kept, used = [], 0
    for char in text:
        if used + _units(char) > units - 1:
            break
        kept.append(char)
        used += _units(char)
    return "".join(kept).rstrip() + "…"


def _wrap(text: str, units: int = UNITS, max_lines: int = 3) -> list[str]:
    """Greedy wrap by width; a word wider than a line is cut by letters, and what exceeds ``max_lines`` ends in ``…``."""
    atoms: list[str] = []
    for word in text.split(" "):
        while _width(word) > units:
            head = ""
            for char in word:
                if _width(head) + _units(char) > units:
                    break
                head += char
            head = head or word[:1]
            atoms.append(head)
            word = word[len(head):]
        if word:
            atoms.append(word)
    lines: list[str] = []
    current = ""
    for atom in atoms:
        joined = f"{current} {atom}" if current else atom
        if _width(joined) <= units:
            current = joined
        else:
            lines.append(current)
            current = atom
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _clip(lines[-1] + "…", units)
    return lines or [""]


def _plain(text: Any) -> str:
    """One line of plain text from a markdown fragment: no images, link targets, claim anchors or emphasis marks."""
    value = _IMAGE.sub(" ", str(text or ""))
    value = _LINK.sub(r"\1", value)
    value = _ANCHOR.sub("", value)
    value = _MARKS.sub("", value)
    value = _SPACE_BEFORE_MARK.sub(r"\1", _SPACES.sub(" ", _XML_BAD.sub("", value)))  # "p=0.003 [[claim]]." -> "p=0.003."
    return value.strip(" -·|")


def _first_clause(text: str, units: int) -> str:
    plain = _plain(text)
    cut = _SENTENCE.split(plain, maxsplit=1)[0]
    return _clip(cut, units)


def _question(req: Mapping[str, Any]) -> str:
    for line in str(req.get("text") or "").splitlines():
        line = _plain(_HEADING.sub(r"\1", line))
        if line:
            return line
    return "요청 내용이 기록되지 않았어요"


def _source_label(ref: Mapping[str, Any]) -> str:
    kind, value = str(ref.get("kind") or ""), str(ref.get("value") or "").strip()
    if not value:
        return ""
    if kind in ("url", "github"):
        host = urlsplit(value).netloc or urlsplit("//" + value).netloc
        return f"웹 · {host}" if host else "웹 주소"
    if kind == "doi":
        return f"DOI {value.removeprefix('https://doi.org/')}"
    if kind == "pmid":
        return f"PMID {value}"
    name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]  # a local path shows its last name only
    return f"폴더·파일 · {name}" if kind == "path" and name else f"{kind or '입력'} · {name or value}"


def _sources(req: Mapping[str, Any]) -> list[str]:
    labels = [_source_label(ref) for ref in req.get("references") or [] if isinstance(ref, Mapping)]
    labels += [f"프로젝트 폴더 · {str(path).replace(chr(92), '/').rstrip('/').rsplit('/', 1)[-1]}"
               for path in req.get("project_dirs") or [] if str(path).strip()]
    return list(dict.fromkeys(label for label in labels if label.strip(" ·")))


def _steps(req: Mapping[str, Any]) -> list[tuple[str, str]]:
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    found = []
    for step in plan.get("steps") or []:
        if isinstance(step, Mapping):
            agent = str(step.get("agent_id") or "")
            found.append((f"{len(found) + 1}. {AGENT_LABELS.get(agent, agent or '단계')}", str(step.get("instruction") or "")))
    return found


def _sections(report: str) -> list[tuple[str, list[str]]]:
    """(heading, body lines) for each heading of the report, outside code fences; text before one has heading ""."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    fenced = False
    for line in (report or "").replace("\r\n", "\n").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else _HEADING.match(line)
        if found:
            sections.append((_plain(found.group(1)), []))
        elif not fenced:
            sections[-1][1].append(line)
    return sections


def _findings(report: str) -> list[str]:
    """Up to three conclusion lines: the "결론" section's bullets or sentences, else the first section with text."""
    sections = _sections(report)
    body = next((lines for head, lines in sections if "결론" in head or "conclusion" in head.casefold()), None)
    if body is None or not any(line.strip() for line in body):
        body = next((lines for _head, lines in sections if any(line.strip() and not line.lstrip().startswith("|")
                                                                for line in lines)), [])
    bullets = [match.group(1) for line in body if (match := _BULLET.match(line))]
    if bullets:
        items = bullets
    else:
        paragraphs = _SPACES.sub(" ", " ".join(line for line in body if not line.lstrip().startswith("|")))
        items = _SENTENCE.split(paragraphs)
    cleaned = [_plain(item) for item in items]
    return [item for item in cleaned if _width(item) >= 6][:MAX_FINDINGS]


Segment = tuple[str, bool, str]  # (text, bold, color)
BOLD = ' font-weight="700"'


def build_graphical_abstract(req: Mapping[str, Any], report: str, *,
                             scrub: Callable[[str], str] | None = None) -> str:
    """The SVG text of the flow diagram for ``req`` and its ``report``.

    ``scrub``: applied to every label before it is drawn, so a caller that publishes the picture can mask private text
    as it masks the report. All text is XML-escaped here."""
    def fit(text: str) -> str:
        return _plain(scrub(text) if scrub else text)

    def plain_lines(lines: list[str], color: str = INK) -> list[list[Segment]]:
        return [[(line, False, color)] for line in lines]

    rows: list[list[list[Segment]]] = [[], [], [], []]  # each stage: lines, each line a list of segments
    rows[0] = plain_lines(_wrap(_clip(fit(_question(req)), UNITS * 4), UNITS, 4))

    sources = _sources(req)
    if sources:
        for label in sources[:MAX_SOURCES]:
            rows[1] += plain_lines(_wrap("· " + _clip(fit(label), UNITS * 2 - 4), UNITS, 2))
        if len(sources) > MAX_SOURCES:
            rows[1] += plain_lines([f"외 {len(sources) - MAX_SOURCES}개"], INK_2)
    else:
        rows[1] = plain_lines(["요청에 적힌 입력 출처가 없어요"], INK_2)

    steps = _steps(req)
    if steps:
        for title, instruction in steps[:MAX_STEPS]:
            head = _clip(fit(title), 24)
            rest = _first_clause(fit(instruction), max(UNITS - _width(head) - 3, 10))
            rows[2].append([(head, True, INK)] + ([(" · " + rest, False, INK_2)] if rest else []))
        if len(steps) > MAX_STEPS:
            rows[2] += plain_lines([f"외 {len(steps) - MAX_STEPS}단계"], INK_2)
    else:
        rows[2] = plain_lines(["계획된 단계가 없어요"], INK_2)

    findings = _findings(report)
    if findings:
        for item in findings:
            rows[3] += plain_lines(_wrap("· " + fit(item), UNITS, 3))
    else:
        rows[3] = plain_lines(["결론 문장을 찾지 못했어요"], INK_2)

    heights = [max(ROW_MIN, len(lines) * LINE_H + 26) for lines in rows]
    body = sum(heights) + GAP * (len(rows) - 1)
    height = PAD + TITLE_H + body + 40
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        f'role="img" aria-label="{_esc("프로젝트 전체 요약: 질문, 데이터, 방법과 단계, 핵심 결과")}" xml:lang="ko">',
        f"<title>{_esc('Graphical abstract')}</title>",
        f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="{PAPER}"/>',
        f'<text x="{PAD}" y="{PAD + 22}" font-family="{FONT}" font-size="22" font-weight="700" fill="{INK}">'
        f"{_esc('프로젝트 한눈에 보기')}</text>",
    ]
    width = WIDTH - 2 * PAD
    top = PAD + TITLE_H
    for index, (lines, row_h, (header, accent, soft)) in enumerate(zip(rows, heights, COLUMNS)):
        out.append(f'<rect x="{PAD}" y="{top}" width="{width}" height="{row_h}" rx="12" fill="{soft}" '
                   f'stroke="{accent}" stroke-width="1.5"/>')
        out.append(f'<path d="M{PAD + 12} {top} H{PAD + BAND_W} V{top + row_h} H{PAD + 12} '
                   f'a12 12 0 0 1 -12 -12 V{top + 12} a12 12 0 0 1 12 -12 Z" fill="{accent}"/>')
        out.append(f'<text x="{PAD + 16}" y="{top + row_h // 2 + 6}" font-family="{FONT}" font-size="16" '
                   f'font-weight="700" fill="{SURFACE}">{_esc(header)}</text>')
        y = top + (row_h - len(lines) * LINE_H) // 2 + 15
        for segments in lines:
            spans = "".join(
                f'<tspan{BOLD if bold else ""} fill="{color}">{_esc(text)}</tspan>'
                for text, bold, color in segments)
            out.append(f'<text x="{TEXT_X}" y="{y}" font-family="{FONT}" font-size="{FONT_PX}">{spans}</text>')
            y += LINE_H
        top += row_h
        if index < len(rows) - 1:
            x = WIDTH // 2
            out.append(f'<polygon points="{x - 14},{top + 6} {x + 14},{top + 6} {x},{top + GAP - 6}" fill="{INK_2}"/>')
            top += GAP
    out.append(f'<text x="{PAD}" y="{height - 16}" font-family="{FONT}" font-size="12" fill="{INK_2}">'
               f"{_esc('labhq가 요청, 입력 출처, 계획의 단계, 보고서의 결론에서 자동으로 그린 요약입니다.')}</text>")
    out.append("</svg>")
    return "\n".join(out)


def insert_after_first_heading(report: str, markup: str) -> str:
    """``report`` with ``markup`` as its own paragraph under the first heading outside a code fence, else on top."""
    lines = (report or "").split("\n")
    fenced = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _HEADING.match(line):
            tail = lines[index + 1:]
            while tail and not tail[0].strip():
                tail.pop(0)
            return "\n".join([*lines[:index + 1], "", markup, "", *tail])
    return markup + "\n\n" + (report or "")


def with_abstract_link(report: str, name: str = ABSTRACT_FILE) -> str:
    """``report`` with the abstract as its first figure: under the first heading outside a code fence, else on top."""
    return insert_after_first_heading(report, f"![{CAPTION}]({name})")


def abstract_first(report: str, resolves: Callable[[str], bool]) -> str:
    """Move the report's own graphical abstract to be its first figure, under the first heading (#611 review).

    ``resolves`` (the decoded relative path -> bool) says which abstract link points at a file that exists. Nothing moves
    when there is none, or when it already comes before every other figure; the link keeps its caption and target."""
    links = image_links(report)
    found = None
    for link in links:
        kind, value = classify(link.target, resolves)
        if kind == "local" and is_abstract_target(value) and resolves(value):
            found = link
            break
    if found is None or found is links[0]:
        return report
    before, after = report[:found.start], report[found.end:]
    start = before.rfind("\n") + 1
    end = after.find("\n")
    end = len(after) if end < 0 else end
    if not before[start:].strip() and not after[:end].strip():  # alone on its line: the line goes, and one blank beside it
        before, after = before[:start], after[end + 1:]
        if before.endswith("\n\n"):
            if after.startswith("\n"):
                after = after[1:]
            elif not after:
                before = before[:-1]
    return insert_after_first_heading(before + after, report[found.start:found.end])
