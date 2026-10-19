"""CommonMark-aware source regions and reader-visible marker checks."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Callable

from markdown_it import MarkdownIt
from markdown_it.rules_inline.backticks import backtick


def _record_backtick(state, silent: bool) -> bool:
    """Keep the parser's source span; never reimplement its delimiter rules."""
    start, count = state.pos, len(state.tokens)
    matched = backtick(state, silent)
    if not silent and len(state.tokens) > count and state.tokens[-1].type == "code_inline":
        state.tokens[-1].meta["code_span"] = (start, state.pos)
    return matched


_COMMONMARK = MarkdownIt("commonmark")
_COMMONMARK.inline.ruler.at("backticks", _record_backtick)
_CODE_BLOCKS = {"code_block", "fence"}
RENDERED_MARKERS = ("**", "__", "[[")
READER_FILENAMES = {"readme.md", "report.md", "result.md"}


@dataclass(frozen=True)
class MarkdownRegion:
    start: int
    end: int
    code: bool


@dataclass(frozen=True)
class RenderedMarker:
    marker: str
    line: int


def render_marker_message(name: str, hit: RenderedMarker) -> str:
    fix = ("내부 claim 표기는 독자용 근거 번호로 치환하고, 남은 [[ 자리표시를 고치세요."
           if hit.marker == "[[" else
           "강조는 짧은 낱말만 감싸고, 문장부호 뒤의 닫는 강조 기호 다음에 조사를 붙이지 마세요. "
           "띄우거나 문장을 바꾸세요.")
    return f"Markdown 렌더 화면에 {hit.marker!r}가 남습니다: {name} {hit.line}줄. {fix}"


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    offsets.extend(match.end() for match in re.finditer(r"\r\n?|\n", source))
    offsets.append(len(source))
    return offsets


def _inline_positions(source: str, offsets: list[int], token) -> list[int]:
    """Map inline content back to physical lines, retaining list/quote prefixes."""
    positions: list[int] = []
    for number, line in enumerate(token.content.split("\n"), token.map[0]):
        start, end = offsets[number:number + 2]
        raw = source[start:end].rstrip("\r\n")
        # CommonMark normalizes NUL and CRLF before parsing. Container prefixes
        # are removed by the block parser; the remaining content stays in order.
        normalized = raw.replace("\x00", "\ufffd")
        column = normalized.find(line)
        if column < 0:
            # Tabs in container indentation can be expanded to spaces. Align
            # the unindented suffix; leading whitespace cannot contain a code delimiter.
            stripped = line.lstrip(" \t")
            column = normalized.find(stripped) - (len(line) - len(stripped))
        positions.extend(start + max(0, column + index) for index in range(len(line)))
        positions.append(end - (2 if source[max(start, end - 2):end] == "\r\n" else 1))
    return positions[:len(token.content)]


def markdown_regions(source: str) -> list[MarkdownRegion]:
    """Split Markdown source into body and code regions using one CommonMark parser."""
    if not source:
        return []
    offsets = _line_offsets(source)
    code_spans: list[tuple[int, int]] = []
    for token in _COMMONMARK.parse(source):
        if token.map is None:
            continue
        if token.type in _CODE_BLOCKS:
            first, last = token.map
            code_spans.append((offsets[first], offsets[last]))
        elif token.type == "inline" and any(child.type == "code_inline" for child in token.children or []):
            positions = _inline_positions(source, offsets, token)
            for child in token.children or []:
                if child.type == "code_inline":
                    start, end = child.meta["code_span"]
                    code_spans.append((positions[start], positions[end - 1] + 1))
    code_spans.sort()

    block_spans: list[tuple[int, int]] = []
    for start, end in code_spans:
        if block_spans and start <= block_spans[-1][1]:
            block_spans[-1] = (block_spans[-1][0], max(end, block_spans[-1][1]))
        else:
            block_spans.append((start, end))

    regions: list[MarkdownRegion] = []
    cursor = 0
    for start, end in block_spans:
        if cursor < start:
            regions.append(MarkdownRegion(cursor, start, False))
        if cursor < end:
            regions.append(MarkdownRegion(max(cursor, start), end, True))
        cursor = max(cursor, end)
    if cursor < len(source):
        regions.append(MarkdownRegion(cursor, len(source), False))
    return regions


def markdown_body(source: str) -> str:
    """Keep body source positions and newlines while blanking every code region."""
    return "".join(
        ("".join(char if char in "\r\n" else " " for char in source[region.start:region.end])
         if region.code else source[region.start:region.end])
        for region in markdown_regions(source)
    )


def replace_markdown_body(source: str, replace: Callable[[str], str]) -> str:
    """Apply a text replacement only to non-code CommonMark source regions."""
    return "".join(
        source[region.start:region.end] if region.code else replace(source[region.start:region.end])
        for region in markdown_regions(source)
    )


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - HTMLParser callback
        if tag.casefold() == "img" and not self.hidden:
            self.parts.append(" ")
        if tag.casefold() in {"code", "pre", "script", "style"}:
            if not self.hidden:
                self.parts.append(" ")
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"code", "pre", "script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _html_text(value: str) -> str:
    parser = _VisibleHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def rendered_markers(source: str) -> list[RenderedMarker]:
    """Find marker text that remains visible after CommonMark rendering, excluding code."""
    found: list[RenderedMarker] = []
    for token in _COMMONMARK.parse(source):
        visible = ""
        if token.type == "inline":
            visible = _html_text(_COMMONMARK.renderer.renderInline(token.children or [], _COMMONMARK.options, {}))
        elif token.type == "html_block":
            visible = _html_text(token.content)
        if not visible:
            continue
        line = (token.map[0] + 1) if token.map else 1
        for marker in RENDERED_MARKERS:
            if marker in visible:
                found.append(RenderedMarker(marker, line + visible[:visible.index(marker)].count("\n")))
    return found
