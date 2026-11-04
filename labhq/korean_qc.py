"""Deterministic Korean writing QC and meaning-preservation checks.

The humanizer skill makes stylistic decisions. This module only measures stable
markers and protects facts when a later polishing stage is connected.
"""

from __future__ import annotations

import collections
import difflib
import re
from pathlib import Path
from urllib.parse import unquote


HANGUL = re.compile(r"[가-힣]")
QUOTE_OR_CODE = re.compile(r'`[^`\n]*`|"[^"\n]*"|“[^”\n]*”')
PATTERNS: dict[str, tuple[re.Pattern[str], bool]] = {
    "에 대해/대한": (re.compile(r"에 대(?:해|하여|한)"), False),
    "통해/통한": (re.compile(r"[을를] 통(?:해|하여|한)"), False),
    "에 있어서": (re.compile(r"에 있어(?:서)?"), True),
    "되어진다": (re.compile(r"되어지|되어진"), True),
    "가지고 있다": (re.compile(r"가지고 있"), True),
    "에 의해/의한": (re.compile(r"에 의(?:해|하여|한)"), False),
    "줄임 전문어": (re.compile(r"비유의(?:적)?|유의차|미보정|무보정|비보정"), True),
    "할 수 있다": (re.compile(r"[ㄹ을를]? ?수 있(?:다|습니다|는|었|어|음|게)"), False),
    "것이다": (re.compile(r"것(?:이다|입니다)"), False),
    "핵심적": (re.compile(r"핵심적"), False),
    "효과적": (re.compile(r"효과적"), False),
    "복수 -들": (re.compile(r"[가-힣]{2,}들(?:이|은|을|의|에|과|와|도|로|만)\b"), False),
    "~적 N": (re.compile(r"[가-힣]{2,}적 [가-힣]{2,}"), False),
}
MUST_ZERO = tuple(name for name, (_pattern, required) in PATTERNS.items() if required)

NUM = re.compile(r"(?<![A-Za-z_])[−+\-]?\d[\d,]*(?:\.\d+)?(?:[eE][−\-]?\d+)?%?")
LINK = re.compile(r"\]\(([^)\s]+)\)")
URL = re.compile(r"https?://[^\s)>\]]+")
CLAIM = re.compile(r"\[\[claim:[^\]]+\]\]")
CODE = re.compile(r"`[^`\n]+`")
QUOTE = re.compile(r'"([^"\n]{2,})"|“([^”\n]{2,})”')
IDENT = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Za-z0-9_\-]*[A-Z0-9][A-Za-z0-9_\-]*(?![A-Za-z0-9_])")
NEGATION = re.compile(r"없|않|못|아니|불가")


def prose_lines(text: str):
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if (fenced or not stripped or stripped.startswith((">", "![", "<!--"))
                or re.fullmatch(r"\|?[\s:|-]+\|?", stripped)):
            continue
        if HANGUL.search(stripped):
            yield stripped


def _outside_quotes(text: str) -> str:
    return "\n".join(QUOTE_OR_CODE.sub("", line) for line in prose_lines(text))


def measure_text(text: str) -> dict[str, object]:
    lines = list(prose_lines(text))
    body = "\n".join(lines)
    outside = _outside_quotes(text)
    hangul = len(HANGUL.findall(body))
    prose = re.sub(r"\([^()]*\d[^()]*\)", "", outside)
    prose = re.sub(r"(?<=\d),(?=\d)", "", prose).replace("|", "\n")
    row: dict[str, object] = {
        "ko_lines": len(lines),
        "ko_chars": hangul,
        "commas_per_line": round(prose.count(",") / max(1, len(lines)), 2),
        "comma_after_ending": len(re.findall(
            r"(?:고|며|면|서|지만|는데|니까|어서|아서|해서|하고|하며|으며|되어|하여),", prose)),
    }
    for name, (pattern, _required) in PATTERNS.items():
        row[name] = len(pattern.findall(outside))
    row["must_zero_hits"] = sum(int(row[name]) for name in MUST_ZERO)
    return row


def check_document(text: str) -> dict[str, object]:
    """PASS, or WARN when a must-be-zero expression occurs outside quoted/code text."""
    measures = measure_text(text)
    found = {name: int(measures[name]) for name in MUST_ZERO if measures[name]}
    return {
        "status": "WARN" if found else "PASS",
        "must_zero": found,
        "measures": measures,
    }


def _bag(pattern: re.Pattern[str], text: str) -> collections.Counter[str]:
    out: collections.Counter[str] = collections.Counter()
    for match in pattern.finditer(text):
        group = next((value for value in match.groups() if value), None) if match.groups() else None
        out[group if group is not None else match.group(0)] += 1
    return out


def _bag_diff(before: collections.Counter[str], after: collections.Counter[str]) -> dict[str, object]:
    return {"missing": dict((before - after).most_common(12)),
            "added": dict((after - before).most_common(12))}


def _headings(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.startswith("#")]


def _slug(heading: str) -> str:
    value = heading.lstrip("#").strip().lower()
    value = "".join(char for char in value if char.isalnum() or char in " -_")
    return value.replace(" ", "-")


def _linked_anchors(repo: Path, document_path: Path | None) -> set[str]:
    anchors: set[str] = set()
    excluded = document_path.resolve() if document_path is not None else None
    for markdown in Path(repo).rglob("*.md"):
        try:
            if excluded is not None and markdown.resolve() == excluded:
                continue
            text = markdown.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"\]\([^\)\s]*#([^\)\s]+)\)", text):
            anchors.add(unquote(match.group(1)).lower())
    return anchors


def _heading_failure(before: str, after: str, repo: Path | None,
                     document_path: Path | None) -> dict[str, object] | None:
    old, new = _headings(before), _headings(after)
    levels = lambda rows: [len(row) - len(row.lstrip("#")) for row in rows]
    if levels(old) != levels(new):
        return {"check": "headings", "why": "heading count or levels changed"}
    if repo is None:
        changed = [left for left, right in zip(old, new) if left != right]
        return {"check": "headings", "orig": changed[:8]} if changed else None
    anchors = _linked_anchors(repo, document_path)
    locked = [left for left, right in zip(old, new) if left != right and _slug(left) in anchors]
    return ({"check": "headings", "why": "linked heading changed", "orig": locked[:8]}
            if locked else None)


def _table_shapes(text: str) -> list[tuple[int, tuple[int, ...]]]:
    shapes, rows = [], []
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith("|"):
            rows.append(line.strip().strip("|").count("|") + 1)
        elif rows:
            shapes.append((len(rows), tuple(rows)))
            rows = []
    return shapes


def _words(text: str) -> list[str]:
    return [word for line in prose_lines(text) for word in line.split()]


def check_preservation(original: str, rewritten: str, *, repo: Path | None = None,
                       document_path: Path | None = None,
                       locked_phrases: tuple[str, ...] | list[str] = ()) -> dict[str, object]:
    """Reject a rewrite that changes protected evidence or more than half of its prose."""
    fails: list[dict[str, object]] = []
    warns: list[dict[str, object]] = []
    for name, pattern in (("numbers", NUM), ("links", LINK), ("urls", URL),
                          ("claim anchors", CLAIM), ("code spans", CODE),
                          ("quotes", QUOTE), ("identifiers", IDENT)):
        before, after = _bag(pattern, original), _bag(pattern, rewritten)
        if before != after:
            fails.append({"check": name, **_bag_diff(before, after)})
    heading_problem = _heading_failure(original, rewritten, repo, document_path)
    if heading_problem:
        fails.append(heading_problem)
    if _table_shapes(original) != _table_shapes(rewritten):
        fails.append({"check": "table shapes", "orig": _table_shapes(original)[:6],
                      "new": _table_shapes(rewritten)[:6]})
    for phrase in locked_phrases:
        if original.count(phrase) != rewritten.count(phrase):
            fails.append({"check": "locked phrase", "phrase": phrase,
                          "orig": original.count(phrase), "new": rewritten.count(phrase)})
    qc = check_document(rewritten)
    if qc["status"] == "WARN":
        fails.append({"check": "must-be-zero patterns", "found": qc["must_zero"]})
    before_words, after_words = _words(original), _words(rewritten)
    matcher = difflib.SequenceMatcher(None, before_words, after_words, autojunk=False)
    changed = sum(max(i2 - i1, j2 - j1)
                  for operation, i1, i2, j1, j2 in matcher.get_opcodes() if operation != "equal")
    rate = round(100 * changed / max(1, len(before_words)), 1)
    if rate > 50:
        fails.append({"check": "change rate", "rate": rate})
    elif rate > 30:
        warns.append({"check": "change rate", "rate": rate})
    old_negation, new_negation = len(NEGATION.findall(original)), len(NEGATION.findall(rewritten))
    if old_negation != new_negation:
        warns.append({"check": "negation words", "orig": old_negation, "new": new_negation})
    return {"accepted": not fails, "change_rate": rate, "fails": fails, "warns": warns,
            "before": measure_text(original), "after": measure_text(rewritten)}
