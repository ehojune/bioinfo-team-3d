"""A finished request's report as one self-contained HTML page and, when a local Edge or Chrome exists, a PDF (#584).

The page embeds every figure the report links (data: URIs read from the request bundle, never from outside it), uses a
Korean-capable font stack and print CSS, and carries a content security policy that allows no network. The PDF comes from
the installed browser in headless mode; with no browser, or when printing fails, the caller gets the HTML and a plain
Korean reason. No markdown library, LaTeX or GTK is involved: the converter below covers what reports use (headings,
paragraphs, lists, tables, links, images, code, quotes).

Figure policy (#608, #690): a figure has no size limit and is always the bundle's original, at most 11 go in (the
graphical abstract included, drawn by `report_abstract` when the report has none), and printing never re-encodes or
shrinks a figure. A configured PDF size is warning-only.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .report_abstract import abstract_first, build_graphical_abstract, with_abstract_link
from .report_figures import (ABSTRACT_FILE, FIGURE_SUFFIXES, IMAGE_PATTERN, MAX_REPORT_FIGURES, classify,
                             drop_dead_abstract_links, figure_owners, has_abstract_link, link_workdir_figures,
                             output_exists)

RENDER_VERSION = "2"  # bump when the page's markup or CSS changes: a cached PDF of an older one is not reused
PRINT_TIMEOUT_S = 60.0
_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"  # no network
PRINT_SLOW_BYTES_PER_S = 2 * 1024 * 1024  # a page of embedded figures gets one more second of printing per 2 MB (#608)
MAX_REQUEST_CHARS = 2000
_LOCK = threading.Lock()  # one print at a time: a headless browser is heavy and the PI has one PC

NO_BROWSER = ("이 PC에서 Edge나 Chrome을 찾지 못해 PDF 대신 HTML로 드립니다. HTML을 브라우저로 열어 인쇄(PDF로 저장)하거나 "
              "gateway.report_browser에 브라우저 실행 파일 경로를 적어 주세요.")


class ReportRefused(Exception):
    """The report is not built for this request; the message is plain Korean for the PI."""


@dataclass
class ReportFile:
    kind: str  # "pdf" or "html"
    data: bytes
    reason: str  # why an HTML came instead of a PDF; "" for a PDF
    filename: str
    media_type: str
    cached: bool = False
    notes: tuple[str, ...] = ()  # figures the page could not embed and warning-only configured size notices
    shrunk: int = 0  # always 0: report figures are never re-encoded or shrunk (#690)
    over_cap: bool = False  # a warning-only configured size was exceeded


# ---------------------------------------------------------------------------------------------------------------------
# markdown -> HTML

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_ANCHOR = re.compile(r"\[\[\s*claim\s*:\s*([^\[\]\n]*?)\s*\]\]", re.IGNORECASE)
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)(?:\s+&quot;.*?&quot;)?\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# ASCII-only word guards: `2*3*4` stays text and `*TP53*의` is italic (a Hangul particle is not a word here)
_ITALIC = re.compile(r"(?<![A-Za-z0-9_*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![A-Za-z0-9_*])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_STASH = re.compile(r"\x00(\d+)\x00")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


class FigureEmbedder:
    """Reads linked figures from the bundle and returns <img> or <figure> markup. ``root`` None: no bundle.

    A figure has no size limit (#608): the PDF is brought under its cap by shrinking, not by leaving a figure out. At most
    ``limit`` figures go in (MAX_REPORT_FIGURES, the graphical abstract included); the rest are named as not embedded.
    ``extra`` holds figures that are not in the bundle (the drawn abstract), by the name the report links them with."""

    def __init__(self, root: Path | None, *, extra: Mapping[str, bytes] | None = None,
                 limit: int = MAX_REPORT_FIGURES) -> None:
        self.root = root
        self.extra = dict(extra or {})
        self.limit = limit
        self.embedded = 0
        self.missing: list[str] = []

    def _uri(self, kind: str, value: str) -> tuple[str | None, str]:
        if kind == "outside":
            return None, "묶음 밖 경로라 넣지 않음"
        if kind == "remote":
            if not value.lower().startswith("data:image/"):
                return None, "외부 주소의 그림은 넣지 않음"
            if self.embedded >= self.limit:
                return None, f"그림은 {self.limit}장까지만 넣음"
            return value, ""
        suffix = PurePosixPath(value).suffix.casefold()
        if suffix not in FIGURE_SUFFIXES:
            return None, "그림 형식이 아님"
        payload: bytes | None = self.extra.get(value)
        path = None
        if payload is None:
            if self.root is None:
                return None, "요청 묶음이 없어 넣지 못함"
            from .ro_crate import _plain_file

            try:
                path = _plain_file(self.root, value)
                size = path.stat().st_size
            except OSError:
                return None, "묶음에 없음"
            if size == 0:
                return None, "빈 파일"
        elif not payload:
            return None, "빈 파일"
        if self.embedded >= self.limit:
            return None, f"그림은 {self.limit}장까지만 넣음"  # decided before the file is read: it may be large
        if payload is None:
            try:
                payload = path.read_bytes()
            except OSError:
                return None, "읽지 못함"
        return f"data:{FIGURE_SUFFIXES[suffix]};base64,{base64.b64encode(payload).decode('ascii')}", ""

    def _holds(self, path: str) -> bool:
        if path in self.extra:
            return True
        from .ro_crate import _plain_file

        try:
            return self.root is not None and _plain_file(self.root, path).is_file()
        except OSError:
            return False

    def markup(self, alt: str, target: str, *, block: bool) -> str:
        """``alt`` and ``target`` are the raw markdown text; the markup returned is escaped."""
        kind, value = classify(target, self._holds)
        uri, reason = self._uri(kind, value)
        shown = alt.strip() or PurePosixPath(value.replace("\\", "/")).name or "그림"
        if uri is None:
            self.missing.append(f"{shown} ({reason})")
            return f'<span class="figure-missing">[그림 없음: {_esc(shown)} — {_esc(reason)}]</span>'
        self.embedded += 1
        image = f'<img src="{_esc(uri)}" alt="{_esc(shown)}">'
        if block:
            return f"<figure>{image}<figcaption>{_esc(shown)}</figcaption></figure>"
        return image


def _emphasis(text: str) -> str:
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _STRIKE.sub(r"<del>\1</del>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


def _inline(text: str, figures: FigureEmbedder) -> str:
    """One run of markdown text as safe HTML: everything is escaped first and only the markup below is added."""
    stash: list[str] = []

    def keep(markup: str) -> str:
        stash.append(markup)
        return f"\x00{len(stash) - 1}\x00"

    text = text.replace("\x00", "")
    text = _INLINE_CODE.sub(lambda match: keep(f"<code>{_esc(match.group(1))}</code>"), text)
    text = _ANCHOR.sub(lambda match: keep(f'<sup class="anchor">[{_esc(match.group(1))}]</sup>'), text)
    text = IMAGE_PATTERN.sub(lambda match: keep(figures.markup(
        match.group("alt"), match.group("target").strip("<>"), block=False)), text)
    text = _esc(text)

    def link(match: re.Match[str]) -> str:
        label, url = _emphasis(match.group(1)), match.group(2)
        if re.match(r"(?i)https?://", html.unescape(url)):
            return keep(f'<a href="{url}">{label}</a>')
        return keep(f'<span class="ref">{label}</span>')

    text = _emphasis(_LINK.sub(link, text))
    for _ in range(4):  # a stashed link can hold a stashed code span
        if "\x00" not in text:
            break
        text = _STASH.sub(lambda match: stash[int(match.group(1))], text)
    return text


def _cells(row: str) -> list[str]:
    """Split a table row on unescaped pipes outside code spans."""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells, current, code = [], [], False
    chars = iter(range(len(row)))
    for index in chars:
        char = row[index]
        if char == "`":
            code = not code
        if char == "\\" and index + 1 < len(row) and row[index + 1] == "|":
            current.append("|")
            next(chars, None)
        elif char == "|" and not code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def _starts_block(lines: list[str], index: int) -> bool:
    """Whether the line at ``index`` begins a block, so a paragraph or a list item's own text stops before it."""
    line = lines[index]
    item = _ITEM.match(line)
    if item and item.group(2)[0].isdigit() and int(item.group(2)[:-1]) != 1:
        item = None  # only an ordered list starting at 1 may interrupt a paragraph: "2026. 10. 14." stays text
    return bool(_FENCE.match(line) or _HEADING.match(line) or _RULE.match(line) or _QUOTE.match(line)
                or item or _is_table(lines, index))


def _is_table(lines: list[str], index: int) -> bool:
    return (index + 1 < len(lines) and "|" in lines[index] and "|" in lines[index + 1]
            and bool(_TABLE_SEPARATOR.match(lines[index + 1])) and len(_cells(lines[index])) >= 1)


def _fence_state(fence: str, text: str) -> str:
    """The marker of the code fence still open after one more line of an item's content ("" when none is open)."""
    stripped = text.strip()
    if fence:
        return "" if stripped.startswith(fence) and not stripped.strip(fence[0]) else fence
    opened = _FENCE.match(text)
    return opened.group(1) if opened else ""


def _item_html(content: list[str], figures: FigureEmbedder, shift: int) -> str:
    """One list item's content as blocks; its first paragraph goes without ``<p>`` (a tight list)."""
    out = _blocks(content, figures, shift)
    if out.startswith("<p>"):
        end = out.index("</p>")  # the text is escaped, so the first "</p>" closes the first paragraph
        out = out[3:end] + out[end + 4:].lstrip("\n")
    return out


def _list(lines: list[str], index: int, figures: FigureEmbedder, shift: int) -> tuple[str, int]:
    first = _ITEM.match(lines[index])
    assert first is not None
    base, ordered = _indent(lines[index]), first.group(2)[0].isdigit()
    start = int(first.group(2)[:-1]) if ordered else 1
    items: list[list[str]] = []  # each item's content lines; continuation lines lose the marker's indent
    offset, fence, plain = 0, "", False  # plain: the last line is paragraph text, which a lazy line may continue
    count = len(lines)

    def add(text: str) -> None:
        nonlocal fence, plain
        items[-1].append(text)
        fence = _fence_state(fence, text)
        plain = bool(text.strip()) and not fence and not _FENCE.match(text)

    while index < count:
        line = lines[index]
        if not line.strip():  # a blank line ends the list unless the next line belongs to it
            ahead = index + 1
            while ahead < count and not lines[ahead].strip():
                ahead += 1
            if ahead >= count:
                break
            following = _ITEM.match(lines[ahead])
            if _indent(lines[ahead]) > base:  # more of this item (a second paragraph, a fence, a table)
                for _ in range(ahead - index):
                    add("")
            elif not (following and _indent(lines[ahead]) == base
                      and following.group(2)[0].isdigit() == ordered):
                break
            index = ahead
            continue
        item, depth = _ITEM.match(line), _indent(line)
        if item and depth == base and item.group(2)[0].isdigit() == ordered:
            items.append([])
            offset, fence = base + len(item.group(2)) + 1, ""
            add(item.group(3))
        elif depth > base:  # this item's content: nested lists, fences, tables, more paragraphs
            add(" " * (depth - min(depth, offset)) + line.lstrip())
        elif plain and not _starts_block(lines, index):  # a lazy continuation of the item's paragraph
            add(line.strip())
        else:
            break
        index += 1
    tag, number = ("ol", f' start="{start}"' if start != 1 else "") if ordered else ("ul", "")
    body = "".join(f"<li>{_item_html(content, figures, shift)}</li>" for content in items)
    return f"<{tag}{number}>{body}</{tag}>", index


def _blocks(lines: list[str], figures: FigureEmbedder, shift: int) -> str:
    out: list[str] = []
    index, count = 0, len(lines)
    while index < count:
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            mark = fence.group(1)
            index += 1
            code: list[str] = []
            while index < count and not (lines[index].strip().startswith(mark[0] * len(mark))
                                         and not lines[index].strip().strip(mark[0])):
                code.append(lines[index])
                index += 1
            index += 1
            out.append(f"<pre><code>{_esc(chr(10).join(code))}</code></pre>")
            continue
        heading = _HEADING.match(line)
        if heading:
            level = min(6, max(1, len(heading.group(1)) + shift))
            out.append(f"<h{level}>{_inline(heading.group(2), figures)}</h{level}>")
            index += 1
            continue
        if _RULE.match(line):
            out.append("<hr>")
            index += 1
            continue
        if _is_table(lines, index):
            header = _cells(line)
            index += 2
            rows = []
            while index < count and lines[index].strip() and "|" in lines[index]:
                rows.append(_cells(lines[index]))
                index += 1
            width = len(header)
            head = "".join(f"<th>{_inline(cell, figures)}</th>" for cell in header)
            body = "".join("<tr>" + "".join(
                f"<td>{_inline(row[column] if column < len(row) else '', figures)}</td>"
                for column in range(width)) + "</tr>" for row in rows)
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if _QUOTE.match(line):
            quoted = []
            while index < count and (inner := _QUOTE.match(lines[index])):
                quoted.append(inner.group(1))
                index += 1
            out.append(f"<blockquote>{_blocks(quoted, figures, shift)}</blockquote>")
            continue
        if _ITEM.match(line):
            markup, index = _list(lines, index, figures, shift)
            out.append(markup)
            continue
        paragraph = [line.strip()]
        index += 1
        while index < count and lines[index].strip() and not _starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        text = " ".join(paragraph)
        alone = IMAGE_PATTERN.fullmatch(text)
        if alone:
            out.append(figures.markup(alone.group("alt"), alone.group("target").strip("<>"), block=True))
        else:
            out.append(f"<p>{_inline(text, figures)}</p>")
    return "\n".join(out)


def markdown_to_html(markdown: str, figures: FigureEmbedder, *, top_level: int = 2) -> str:
    """HTML for the markdown subset reports use. The shallowest heading becomes ``<h{top_level}>``."""
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    levels, fenced = [], False
    for line in lines:
        if _FENCE.match(line.lstrip()):  # a fence inside a list item may be indented past three spaces
            fenced = not fenced
        elif not fenced and (found := _HEADING.match(line)):
            levels.append(len(found.group(1)))
    return _blocks(lines, figures, top_level - min(levels) if levels else 0)


# ---------------------------------------------------------------------------------------------------------------------
# the page

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; @bottom-center { content: counter(page); font-size: 9pt; color: #666; } }
* { box-sizing: border-box; }
html { font-family: "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR",
  "NanumGothic", "Nanum Gothic", "Segoe UI", sans-serif; color: #1c2430; background: #fff; }
body { margin: 0; font-size: 10.5pt; line-height: 1.65; word-break: keep-all; overflow-wrap: anywhere; }
h1, h2, h3, h4, h5, h6 { line-height: 1.35; break-after: avoid; margin: 1.5em 0 .5em; }
h1 { font-size: 22pt; } h2 { font-size: 15pt; border-bottom: 1.5px solid #c9d3e0; padding-bottom: .2em; }
h3 { font-size: 12.5pt; } h4, h5, h6 { font-size: 11pt; }
p, ul, ol, blockquote, pre, table, figure { margin: .6em 0; }
li { margin: .15em 0; }
li > ul, li > ol { margin: .1em 0; }
code { font-family: Consolas, "D2Coding", "Cascadia Mono", monospace; font-size: 9.5pt; background: #f1f4f8;
  padding: 0 .25em; border-radius: 3px; }
pre { background: #f1f4f8; padding: .7em .9em; border-radius: 4px; white-space: pre-wrap; overflow-wrap: anywhere;
  break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #b8c4d6; padding: .1em .9em; color: #3c4858; background: #f7f9fc; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
thead { display: table-header-group; }
th, td { border: 1px solid #c9d3e0; padding: .3em .55em; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #eef2f8; }
tr { break-inside: avoid; }
figure { break-inside: avoid; text-align: center; }
figure img, p img { max-width: 100%; }
figcaption { font-size: 9.5pt; color: #4a5568; margin-top: .3em; }
a { color: #1d4ed8; text-decoration: none; }
.anchor { font-size: 7.5pt; color: #8a94a6; }
.figure-missing { color: #9a3412; font-size: 9.5pt; }
.ref { color: #1c2430; }
.cover { min-height: 235mm; display: flex; flex-direction: column; justify-content: center; break-after: page; }
.cover .kicker { color: #1d4ed8; font-weight: 700; letter-spacing: .04em; margin: 0; }
.cover h1 { margin: .2em 0 1em; }
.cover dl { display: grid; grid-template-columns: 8em 1fr; gap: .35em 1em; margin: 0 0 1.5em; }
.cover dt { color: #4a5568; } .cover dd { margin: 0; }
.cover .request { white-space: pre-wrap; background: #f7f9fc; border-left: 3px solid #b8c4d6; padding: .7em 1em; margin: 0; }
.cover .notes { color: #9a3412; font-size: 9.5pt; margin-top: 1.5em; }
.appendix { break-before: page; }
.appendix h2:first-child { margin-top: 0; }
"""


def _stamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "-"


def _title(request_text: str, rid: str) -> str:
    first = next((line.strip() for line in request_text.splitlines() if line.strip()), "")
    first = re.sub(r"^#+\s*", "", first)
    return (first[:88] + "…") if len(first) > 88 else (first or rid)


def _read_document(bundle: Path | None, name: str, fallback: str) -> str:
    if bundle is not None:
        from .ro_crate import _plain_file

        try:
            return _plain_file(bundle, name).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    return fallback


def build_report_html(req: Mapping[str, Any], bundle: Path | None, *, now: datetime | None = None,
                      report_text: str | None = None, scrub: Callable[[str], str] | None = None
                      ) -> tuple[str, list[str]]:
    """The single-file report page and the figures it could not embed. ``bundle`` is the request bundle folder.

    The report always opens with a graphical abstract (#608): the one the report links, or else the diagram labhq draws
    from the request. ``report_text``: the report as given, with no appendix, instead of the bundle's documents (the
    edition's PDF prints its own cleaned text over the bundle's original figures); ``scrub`` masks the drawn
    abstract's labels the same way."""
    from .request_status import request_display_label

    rid = str(req.get("id") or "")
    request_text = str(req.get("text") or "").strip()
    figures = FigureEmbedder(bundle)
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    # A bundle made before the link rewrite, or the stored report itself, still names figures <workdir_id>/outputs/<file>.
    owners = figure_owners(results)
    source = report_text if report_text is not None else _read_document(bundle, "report.md", str(req.get("report") or ""))
    linked = link_workdir_figures(source, owners, output_exists(results, bundle))
    if has_abstract_link(linked, figures._holds):
        linked = abstract_first(linked, figures._holds)  # the report's own abstract, wherever the writer put it
    else:
        linked = drop_dead_abstract_links(linked, figures._holds)  # a link to a file nobody could make
        figures.extra[ABSTRACT_FILE] = build_graphical_abstract(req, linked, scrub=scrub).encode("utf-8")
        linked = with_abstract_link(linked)
    body = markdown_to_html(linked, figures)
    appendix_text = ("" if report_text is not None
                     else _read_document(bundle, "report_appendix.md", str(req.get("report_appendix") or "")))
    appendix = markdown_to_html(appendix_text, figures, top_level=3) if appendix_text.strip() else ""
    appendix_label = "과학 부록" if appendix_text.startswith("## 부록\n") else "부록 · 실행 기록"
    made = (now or datetime.now()).astimezone().strftime("%Y-%m-%d %H:%M")
    shown_request = request_text if len(request_text) <= MAX_REQUEST_CHARS else request_text[:MAX_REQUEST_CHARS] + "…"
    rows = [("요청 번호", rid or "-"), ("요청 시각", _stamp(req.get("created_at"))),
            ("보고서 만든 시각", made), ("상태", request_display_label(dict(req)))]
    if req.get("bundle_grade"):
        rows.append(("재현 등급", str(req["bundle_grade"])))
    cover_rows = "".join(f"<dt>{_esc(key)}</dt><dd>{_esc(value)}</dd>" for key, value in rows)
    notes = ""
    if figures.missing:
        shown = ", ".join(_esc(item) for item in figures.missing[:8])
        more = f" 외 {len(figures.missing) - 8}개" if len(figures.missing) > 8 else ""
        notes = f'<p class="notes">그림 {len(figures.missing)}개를 넣지 못했어요: {shown}{more}</p>'
    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{_CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc("labhq 결과 보고서 · " + (rid or _title(request_text, rid)))}</title>
<style>{CSS}</style></head><body>
<section class="cover">
<p class="kicker">labhq 결과 보고서</p>
<h1>{_esc(_title(request_text, rid))}</h1>
<dl>{cover_rows}</dl>
{f'<p class="request">{_esc(shown_request)}</p>' if shown_request else ''}
{notes}
</section>
<main class="report">
{body}
</main>
{f'<section class="appendix"><h2>{appendix_label}</h2>{appendix}</section>' if appendix else ''}
</body></html>
"""
    return page, figures.missing


# ---------------------------------------------------------------------------------------------------------------------
# the browser

def _windows_candidates() -> list[str]:
    roots = [os.environ.get(name) for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    suffixes = ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")
    return [str(Path(root) / suffix) for suffix in suffixes for root in roots if root]


def find_browser(configured: str | None = None) -> str | None:
    """An installed Edge or Chrome: the configured path first, then the PATH, then the standard install folders."""
    if configured and Path(os.path.expandvars(os.path.expanduser(configured))).is_file():
        return str(Path(os.path.expandvars(os.path.expanduser(configured))))
    for name in ("msedge", "microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
                 "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    standard = [*_windows_candidates(),
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((path for path in standard if Path(path).is_file()), None)


def print_pdf(browser: str, html_path: Path, pdf_path: Path, *, timeout: float = PRINT_TIMEOUT_S) -> str | None:
    """Print ``html_path`` to ``pdf_path`` with a headless browser. None on success, else a plain Korean reason."""
    profile = Path(tempfile.mkdtemp(prefix="labhq-browser-"))  # a fresh profile: never attach to the PI's open browser
    command = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--no-pdf-header-footer", "--print-to-pdf-no-header",
               f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command.insert(2, "--no-sandbox")
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return f"브라우저가 {int(timeout)}초 안에 PDF를 끝내지 못해 HTML로 드립니다."
    except OSError as exc:
        return f"브라우저를 실행하지 못해 HTML로 드립니다({exc.__class__.__name__})."
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    try:
        head = pdf_path.read_bytes()[:5] if pdf_path.stat().st_size >= 1024 else b""
    except OSError:
        head = b""
    if head != b"%PDF-":
        return f"브라우저가 PDF를 만들지 못해 HTML로 드립니다(종료 코드 {done.returncode})."
    return None


def print_report(browser: str, page: str, *, timeout: float = PRINT_TIMEOUT_S) -> tuple[bytes, str]:
    """Print the original page once. No figure is re-encoded, resized, or retried at a lower quality (#690)."""
    work = Path(tempfile.mkdtemp(prefix="labhq-report-"))
    try:
        data = page.encode("utf-8")
        source, target = work / "report.html", work / "report.pdf"
        source.write_bytes(data)
        failed = print_pdf(browser, source, target, timeout=timeout + len(data) / PRINT_SLOW_BYTES_PER_S)
        return (b"", failed) if failed else (target.read_bytes(), "")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------------
# one request

def _plain_dir(path: Path) -> bool:
    from .adapters.owned import is_link

    try:
        return not is_link(path) and path.is_dir()
    except OSError:
        return False


def _tasks_restricted(req: Mapping[str, Any], tasks: Mapping[str, Any] | None) -> bool:
    for entry in (tasks or {}).values():
        if isinstance(entry, Mapping) and entry.get("request_id") == req.get("id"):
            meta = (entry.get("payload") or {}).get("meta") if isinstance(entry.get("payload"), Mapping) else None
            if isinstance(meta, Mapping) and meta.get("restricted_inputs"):
                return True
    return False


def check_buildable(req: Mapping[str, Any], settings: Any, tasks: Mapping[str, Any] | None = None) -> None:
    """Raise ReportRefused unless this request may have a PDF report: finished, with a report, no controlled data."""
    from .request_status import is_terminal_request
    from .result_storage import controlled_request

    if not is_terminal_request(req.get("status")):
        raise ReportRefused("아직 끝나지 않은 요청이라 보고서 PDF를 만들 수 없어요.")
    if req.get("bundle_pending"):
        raise ReportRefused("요청 묶음을 만드는 중이에요. 잠시 뒤에 다시 눌러 주세요.")
    if not str(req.get("report") or "").strip():
        raise ReportRefused("이 요청에는 결과 보고서가 없어요.")
    if controlled_request(dict(req), settings) or _tasks_restricted(req, tasks):
        raise ReportRefused("통제 데이터가 포함된 요청은 PDF 보고서를 만들지 않아요. 로컬 요청 묶음의 report.md를 보세요.")


def pdf_cap_bytes(settings: Any) -> int | None:
    """The optional warning threshold from ``github.edition_pdf_max_mb``; ``None`` means unlimited."""
    value = getattr(getattr(settings, "github", None), "edition_pdf_max_mb", None)
    if value is None:
        return None
    try:
        megabytes = float(value)
    except (TypeError, ValueError):
        return None
    return int(megabytes * 1024 * 1024) if megabytes > 0 else None


def _cache_key(req: Mapping[str, Any], bundle: Path | None, cap_bytes: int | None = None) -> str:
    """Everything the page is built from: the cover's fields and the two documents as `build_report_html` reads them."""
    from .request_status import request_display_label

    manifest = b""
    if bundle is not None:
        try:
            manifest = (bundle / "MANIFEST.tsv").read_bytes()
        except OSError:
            manifest = b""
    documents = [hashlib.sha256(_read_document(bundle, name, str(req.get(field) or "")).encode("utf-8")).hexdigest()
                 for name, field in (("report.md", "report"), ("report_appendix.md", "report_appendix"))]
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    return hashlib.sha256(json.dumps(
        [RENDER_VERSION, req.get("id"), req.get("text"), req.get("created_at"), req.get("status"),
         request_display_label(dict(req)), req.get("pi_review_pending"), req.get("pi_reviewed_at"),
         req.get("report"), req.get("report_appendix"), req.get("bundle_grade"), figure_owners(results), documents,
         hashlib.sha256(manifest).hexdigest(), cap_bytes],
        ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _cache_usable(requests_root: Path, cache: Path) -> bool:
    """The cache folder is a plain folder (never a link or junction) under a plain ``requests`` folder, or absent."""
    from .adapters.owned import is_link

    try:
        return not any(os.path.lexists(path) and (is_link(path) or not path.is_dir()) for path in (requests_root, cache))
    except OSError:
        return False


def _save(folder: Path, name: str, data: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".{name}.partial"
    partial.write_bytes(data)
    os.replace(partial, folder / name)


def size_notes(pdf: bytes, cap_bytes: int | None) -> tuple[str, ...]:
    if cap_bytes is None or len(pdf) <= cap_bytes:
        return ()
    megabytes, cap = len(pdf) / 1024 / 1024, cap_bytes / 1024 / 1024
    return (f"PDF가 {megabytes:.1f} MB로 설정 알림 기준 {cap:g} MB를 넘지만 그림과 PDF를 줄이지 않았습니다.",)


def build_report_file(req: Mapping[str, Any], settings: Any, tasks: Mapping[str, Any] | None = None, *,
                      timeout: float = PRINT_TIMEOUT_S, now: datetime | None = None,
                      source: Path | None = None, report_text: str | None = None,
                      scrub: Callable[[str], str] | None = None) -> ReportFile:
    """The PDF of a finished request's report, or its HTML and the reason there is no PDF.

    The output is cached next to the request bundle (``requests/<id>-report/``) and reused while the report, the bundle
    manifest, the optional size-warning threshold and RENDER_VERSION are unchanged. Raises ReportRefused for an unfinished or controlled-data
    request.

    The figures are the originals in the bundle, whatever their size (#608, #690). The page is printed once without
    raster re-encoding. ``github.edition_pdf_max_mb`` is optional and warning-only.

    ``source``: read the documents and the figures from this folder instead of the request bundle, and neither read nor
    write the cache. ``report_text``: use this report, with no appendix, instead of the documents (the repository
    edition's cleaned text over the bundle's figures, #608); ``scrub`` masks the drawn graphical abstract's labels."""
    check_buildable(req, settings, tasks)
    rid = str(req.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", rid):
        raise ReportRefused("요청 번호가 올바르지 않아요.")
    requests_root = settings.path(settings.runner.workspace_root).resolve() / "requests"
    bundle = source if source is not None else requests_root / rid
    bundle = bundle if _plain_dir(bundle) else None
    cache = requests_root / f"{rid}-report"
    cap = pdf_cap_bytes(settings)
    key = _cache_key(req, bundle, cap)
    usable = source is None and report_text is None and _cache_usable(requests_root, cache)
    with _LOCK:
        try:
            if not usable:
                raise OSError("cache folder is not a plain folder")
            meta = json.loads((cache / "cache.json").read_text(encoding="utf-8"))
            if meta.get("key") == key and meta.get("kind") == "pdf":
                return ReportFile("pdf", (cache / "report.pdf").read_bytes(), "", f"{rid}-report.pdf",
                                  "application/pdf", cached=True, notes=tuple(meta.get("notes") or ()),
                                  shrunk=int(meta.get("shrunk") or 0), over_cap=bool(meta.get("over_cap")))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        page, missing = build_report_html(req, bundle, now=now, report_text=report_text, scrub=scrub)
        data = page.encode("utf-8")
        notes = tuple(missing)
        browser = find_browser(getattr(getattr(settings, "gateway", None), "report_browser", None))
        reason, pdf, level, over_cap = NO_BROWSER, b"", 0, False
        if browser:
            pdf, reason = print_report(browser, page, timeout=timeout)
            over_cap = cap is not None and len(pdf) > cap
        kind = "pdf" if pdf else "html"
        if pdf:
            notes += size_notes(pdf, cap)
        try:
            if not usable:
                raise OSError("cache folder is not a plain folder")
            _save(cache, "report.html", data)
            if pdf:
                _save(cache, "report.pdf", pdf)
            else:
                (cache / "report.pdf").unlink(missing_ok=True)
            _save(cache, "cache.json", json.dumps({"key": key, "kind": kind, "reason": reason, "notes": list(notes),
                                                   "shrunk": level, "over_cap": over_cap},
                                                  ensure_ascii=False).encode("utf-8"))
        except OSError:
            pass  # the cache is a convenience; the PI still gets the file
        if pdf:
            return ReportFile("pdf", pdf, "", f"{rid}-report.pdf", "application/pdf", notes=notes, shrunk=level,
                              over_cap=over_cap)
        return ReportFile("html", data, reason, f"{rid}-report.html", "text/html; charset=utf-8", notes=notes)
