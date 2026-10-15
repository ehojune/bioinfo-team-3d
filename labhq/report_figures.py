"""Figures a report links to as markdown images (#584).

The report writer links a step's figure as ``![설명](<workdir_id>/outputs/<file>)``, the spelling the step results list.
The request bundle rewrites that to ``steps/<step_id>/outputs/<file>`` so the link resolves from the bundle root, keeps
the linked file, and `labhq verify` checks that every local image link points at a plain file inside the bundle.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

FIGURE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                   ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_REPORT_FIGURES = 11  # PI 10-15 (#608): up to 10 result figures plus the graphical abstract, in a report and its PDF
MAX_LINKED_FIGURES = MAX_REPORT_FIGURES  # the bundle keeps this many linked figures past its total limits
ABSTRACT_STEM = "graphical_abstract"  # a figure with this file stem is the report's graphical abstract (#608)
ABSTRACT_FILE = ABSTRACT_STEM + ".svg"  # what labhq draws when the report has none
DOCUMENTS = ("report.md", "report_appendix.md")


def _balanced(depth: int = 4) -> str:
    """A link destination without spaces whose parentheses are balanced up to ``depth`` levels (``plot(1).png``)."""
    inner = r"[^()\s]*"
    for _ in range(depth - 1):
        inner = rf"(?:[^()\s]|\({inner}\))*"
    return rf"(?:[^()\s]|\({inner}\))+"


IMAGE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\(\s*(?P<target><[^>\n]*>|" + _balanced() +
    r")(?P<title>\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
CODE_PATTERN = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.DOTALL)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class ImageLink:
    alt: str
    target: str  # as written, without the angle brackets of a spaced path
    start: int
    end: int
    title: str = ""


def image_links(markdown: str) -> list[ImageLink]:
    """Markdown image links outside code, in document order."""
    masked = CODE_PATTERN.sub(lambda match: " " * len(match.group()), markdown or "")
    found = []
    for match in IMAGE_PATTERN.finditer(masked):
        target = match.group("target")
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        found.append(ImageLink(match.group("alt"), target, match.start(), match.end(), match.group("title") or ""))
    return found


def _relative(value: str) -> str:
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def classify(target: str, exists: Callable[[str], bool] | None = None) -> tuple[str, str]:
    """(kind, value): ``remote`` for an http(s) or data URL, ``outside`` for an absolute path, else ``local`` with the
    decoded relative POSIX path.

    A local path loses its ``#fragment`` and ``?query`` unless ``exists`` says the whole name (``a#1.png``) is a file:
    ``exists`` takes the decoded relative path. Without ``exists`` they are dropped, as a browser would."""
    value = target.strip()
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "data:")):
        return "remote", value
    if value.startswith(("/", "\\")) or _DRIVE.match(value) or _SCHEME.match(value):
        return "outside", value
    stripped = _relative(unquote(value.split("#", 1)[0].split("?", 1)[0]))
    if exists is not None and ("#" in value or "?" in value):
        whole = _relative(unquote(value))
        if whole != stripped and exists(whole):
            return "local", whole
    return "local", stripped


def is_abstract_target(value: str) -> bool:
    """Whether a local link's path names a graphical abstract: the stem ``graphical_abstract`` and a figure suffix."""
    path = PurePosixPath(value.replace("\\", "/"))
    return path.stem.casefold() == ABSTRACT_STEM and path.suffix.casefold() in FIGURE_SUFFIXES


def has_abstract_link(report: str, resolves: Callable[[str], bool]) -> bool:
    """Whether the report links a graphical abstract that ``resolves`` (takes the decoded relative path) can open."""
    for link in image_links(report):
        kind, value = classify(link.target, resolves)
        if kind == "local" and is_abstract_target(value) and resolves(value):
            return True
    return False


def drop_dead_abstract_links(report: str, resolves: Callable[[str], bool]) -> str:
    """Remove the links to a graphical abstract that ``resolves`` cannot open (a writer that could not make the file).

    Another figure is never touched, and a link inside code stays as written."""
    pieces: list[str] = []
    last = 0
    for link in image_links(report):
        kind, value = classify(link.target, resolves)
        if kind == "local" and is_abstract_target(value) and not resolves(value):
            pieces += [report[last:link.start]]
            last = link.end
    return "".join(pieces) + report[last:] if pieces else report


def figure_owners(results: Mapping[str, object]) -> dict[str, str]:
    """Each step result's workdir id mapped to its step id (a revision shares its step's folder)."""
    return {str(result["workdir_id"]): str(step_id) for step_id, result in results.items()
            if isinstance(result, Mapping) and result.get("workdir_id")}


def output_exists(results: Mapping[str, object], root: Path | None = None) -> Callable[[str, str], bool]:
    """``(step_id, "outputs/<file>") -> bool``: the step's result lists that output, or the bundle at ``root`` holds it."""
    listed = {(str(step_id), str(name).replace("\\", "/")) for step_id, result in results.items()
              if isinstance(result, Mapping) and isinstance(result.get("outputs"), (list, tuple))
              for name in result["outputs"]}

    def exists(step_id: str, relative: str) -> bool:
        if (step_id, relative) in listed:
            return True
        if root is None:
            return False
        from .ro_crate import _plain_file

        try:
            return _plain_file(root, f"steps/{step_id}/{relative}").is_file()
        except OSError:
            return False

    return exists


def link_workdir_figures(report: str, owners: Mapping[str, str],
                         exists: Callable[[str, str], bool] | None = None) -> str:
    """Rewrite ``<workdir_id>/outputs/<file>`` image links to the bundle's ``steps/<step_id>/outputs/<file>``.

    ``owners`` maps a step's workdir id to its step id. ``exists`` (see `output_exists`) tells a file named ``a#1.png``
    from ``a.png`` plus a fragment. Other links, and every link inside code, stay as written."""
    def whole_name(path: str) -> bool:
        head, _, rest = path.partition("/")
        return exists is not None and head in owners and exists(owners[head], rest)

    pieces: list[str] = []
    last = 0
    for link in image_links(report):
        kind, value = classify(link.target, whole_name)
        head, _, rest = value.partition("/")
        if kind != "local" or head not in owners or not rest.startswith("outputs/") or ".." in rest.split("/"):
            continue
        new = f"steps/{owners[head]}/{quote(rest, safe='/')}"
        pieces += [report[last:link.start], f"![{link.alt}]({new}{link.title})"]
        last = link.end
    return "".join(pieces) + report[last:] if pieces else report


def linked_figure_paths(report: str) -> list[str]:
    """Bundle paths ``steps/<step>/outputs/<file>`` the (already rewritten) report links as figures, first-seen order."""
    paths: dict[str, None] = {}
    for link in image_links(report):
        kind, value = classify(link.target)
        parts = PurePosixPath(value).parts
        if (kind == "local" and len(parts) >= 4 and parts[0] == "steps" and parts[2] == "outputs"
                and PurePosixPath(value).suffix.casefold() in FIGURE_SUFFIXES and ".." not in parts):
            paths[value] = None
    return list(paths)[:MAX_LINKED_FIGURES]


def bundle_figure_check(root: Path) -> dict[str, object]:
    """``{"linked": n, "problems": [...]}`` for the local image links in a request bundle's documents.

    A problem: a link to a path outside the bundle, to a file type a browser does not show, or to a file the bundle does
    not hold as a plain, non-empty file (a link or junction on the way counts as missing)."""
    from .ro_crate import _plain_file

    def holds(path: str) -> bool:
        try:
            return _plain_file(root, path).is_file()
        except OSError:
            return False

    linked = 0
    problems: list[str] = []
    local: dict[str, None] = {}  # distinct local figures across both documents: the PDF shows MAX_REPORT_FIGURES
    for name in DOCUMENTS:
        try:
            text = _plain_file(root, name).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for link in image_links(text):
            kind, value = classify(link.target, holds)
            if kind == "remote":
                continue
            linked += 1
            local[value] = None
            if kind == "outside":
                problems.append(f"{name}: 그림 링크가 묶음 밖 경로입니다: {PurePosixPath(value.replace(chr(92), '/')).name}")
            elif PurePosixPath(value).suffix.casefold() not in FIGURE_SUFFIXES:
                problems.append(f"{name}: 그림으로 보여 줄 수 없는 형식입니다: {value}")
            else:
                try:
                    empty = _plain_file(root, value).stat().st_size == 0
                except OSError:
                    problems.append(f"{name}: 링크한 그림이 묶음에 없습니다: {value}")
                    continue
                if empty:
                    problems.append(f"{name}: 링크한 그림 파일이 비어 있습니다: {value}")
    # Without an abstract of its own the PDF and the edition draw one, which takes the 11th place: 10 result figures at
    # most (#611 review). With one, the 11 are its own and 10 results.
    own_abstract = any(is_abstract_target(value) and holds(value) for value in local)
    limit = MAX_REPORT_FIGURES if own_abstract else MAX_REPORT_FIGURES - 1
    if len(local) > limit:
        problems.append(
            f"보고서 그림이 {len(local)}장이에요. 한 보고서에는 {MAX_REPORT_FIGURES}장까지(결과 그림 {MAX_REPORT_FIGURES - 1}장과 "
            f"graphical abstract 1장)만 넣고, PDF에는 앞의 {MAX_REPORT_FIGURES}장만 들어갑니다."
            if own_abstract else
            f"보고서 그림이 {len(local)}장이에요. graphical abstract가 없으면 labhq가 한 장을 그려 넣으므로 결과 그림은 "
            f"{limit}장까지만 넣고, PDF에는 앞의 {limit}장만 들어갑니다.")
    return {"linked": linked, "problems": list(dict.fromkeys(problems))}
