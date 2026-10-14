"""Figures a report links to as markdown images (#584).

The report writer links a step's figure as ``![설명](<workdir_id>/outputs/<file>)``, the spelling the step results list.
The request bundle rewrites that to ``steps/<step_id>/outputs/<file>`` so the link resolves from the bundle root, keeps
the linked file, and `labhq verify` checks that every local image link points at a plain file inside the bundle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

FIGURE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                   ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_LINKED_FIGURES = 24  # the bundle keeps this many linked figures past its total limits
DOCUMENTS = ("report.md", "report_appendix.md")

IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\(\s*(?P<target><[^>\n]*>|[^)\s]+)(?P<title>\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
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


def classify(target: str) -> tuple[str, str]:
    """(kind, value): ``remote`` for an http(s) or data URL, ``outside`` for an absolute path, else ``local`` with the
    decoded relative POSIX path (query and fragment dropped)."""
    value = target.strip()
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "data:")):
        return "remote", value
    if value.startswith(("/", "\\")) or _DRIVE.match(value) or _SCHEME.match(value):
        return "outside", value
    value = unquote(value.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return "local", value


def figure_owners(results: Mapping[str, object]) -> dict[str, str]:
    """Each step result's workdir id mapped to its step id (a revision shares its step's folder)."""
    return {str(result["workdir_id"]): str(step_id) for step_id, result in results.items()
            if isinstance(result, Mapping) and result.get("workdir_id")}


def link_workdir_figures(report: str, owners: Mapping[str, str]) -> str:
    """Rewrite ``<workdir_id>/outputs/<file>`` image links to the bundle's ``steps/<step_id>/outputs/<file>``.

    ``owners`` maps a step's workdir id to its step id. Other links, and every link inside code, stay as written."""
    pieces: list[str] = []
    last = 0
    for link in image_links(report):
        kind, value = classify(link.target)
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

    linked = 0
    problems: list[str] = []
    for name in DOCUMENTS:
        try:
            text = _plain_file(root, name).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for link in image_links(text):
            kind, value = classify(link.target)
            if kind == "remote":
                continue
            linked += 1
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
    return {"linked": linked, "problems": list(dict.fromkeys(problems))}
