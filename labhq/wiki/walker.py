"""Which files of a linked wiki folder may be indexed, listed without following links.

The folder itself must pass the reference rules (inside ``runner.reference_roots``, no link or mount leading out of
it, outside restricted zones). Below it a link or a mount is never read, even when it points inside the folder, so
every indexed byte lives exactly where its relative path says.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from ..intake import _is_link, check_reference_path, scan_reference_dir
from ..policy import restricted_paths
from ..private_paths import configured_private_paths, credential_path, inside_any


@dataclass(frozen=True)
class WikiFile:
    rel_path: str
    path: Path
    size: int
    mtime_ns: int
    file_id: str


def root_path(settings: Any) -> Path:
    wiki = settings.wiki
    if not wiki.root or not wiki.root.strip():
        raise ValueError("wiki.root is not set")
    return settings.path(wiki.root)


def root_problem(settings: Any, root: Path | None = None) -> str | None:
    """Why this folder cannot be linked as the wiki, or None. The folder is only stat-ed and listed."""
    root = root or root_path(settings)
    try:
        if root.is_symlink() or _is_link(_entry(root)):
            return "the wiki root is a link"
        if not root.is_dir():
            return "the wiki root is not a folder"
        check_reference_path(str(root), settings)
    except (OSError, ValueError) as error:
        return str(error)
    real = root.resolve()
    for private, _label in configured_private_paths(settings):
        if inside_any(str(real), [private]) or inside_any(private, [str(real)]):
            return "the wiki root overlaps a private path"
    for name in ("workspace_root", "state_dir"):
        folder = str(settings.path(getattr(settings.runner, name)).resolve())
        if inside_any(str(real), [folder]) or inside_any(folder, [str(real)]):
            return f"the wiki root overlaps runner.{name}"
    zones = [Path(z) for z in restricted_paths(settings.policy)]
    return scan_reference_dir(real, zones, settings.runner.reference_scan_max_entries,
                              settings.runner.reference_scan_max_depth)


class _Entry:
    """The few DirEntry methods `_is_link` needs, for a path that was not listed through scandir."""

    def __init__(self, path: Path):
        self.path = str(path)
        self._path = path

    def is_symlink(self) -> bool:
        return self._path.is_symlink()

    def stat(self, follow_symlinks: bool = False):
        return self._path.lstat()


def _entry(path: Path) -> _Entry:
    return _Entry(path)


def list_files(settings: Any, root: Path | None = None) -> tuple[list[WikiFile], Counter]:
    """(indexable files in stable order, counts of what was skipped and why). Raises ValueError for a bad root."""
    wiki = settings.wiki
    root = root or root_path(settings)
    problem = root_problem(settings, root)
    if problem:
        raise ValueError(problem)
    real = root.resolve()
    extensions = {e.casefold() for e in wiki.extensions}
    files: list[WikiFile] = []
    skipped: Counter = Counter()
    root_dev = real.stat().st_dev
    stack = [real]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as iterator:
                entries = sorted(iterator, key=lambda e: e.name)
        except OSError:
            skipped["unreadable_folder"] += 1
            continue
        for entry in entries:
            path = Path(entry.path)
            if _is_link(entry):
                skipped["link"] += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name.startswith(".") or (os.name != "nt" and os.path.ismount(entry.path)):
                    skipped["hidden_or_mount_folder"] += 1
                    continue
                stack.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                skipped["special"] += 1
                continue
            if path.suffix.casefold() not in extensions:
                skipped["other_type"] += 1
                continue
            rel = path.relative_to(real).as_posix()
            if credential_path(rel):
                skipped["credential_like"] += 1
                continue
            info = entry.stat(follow_symlinks=False)
            if getattr(info, "st_dev", root_dev) != root_dev and os.name != "nt":
                skipped["other_filesystem"] += 1
                continue
            if info.st_size > wiki.max_file_bytes:
                skipped["too_large"] += 1
                continue
            if len(files) >= wiki.max_files:
                skipped["over_max_files"] += 1
                continue
            files.append(WikiFile(rel, path, info.st_size, info.st_mtime_ns,
                                  f"{getattr(info, 'st_ino', 0)}:{getattr(info, 'st_ctime_ns', 0)}"))
    files.sort(key=lambda f: f.rel_path)
    return files, skipped
