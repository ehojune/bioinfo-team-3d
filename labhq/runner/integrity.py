"""Backstop for read-only tasks: did the run change files it could reach? (#36)

The read-only profile is the guard; this only notices a channel the profile missed. Every entry under the watched
roots is listed without following links, before the CLI starts and after it ends, by type, size, mtime and (on
POSIX) ctime. ctime cannot be set back by the writer, so restoring an mtime does not hide a change there; Windows has
no such field. Nothing is restored: the run fails and the PI is told what changed.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

# What labhq itself writes in a task workspace while a run is going: the adapter's scratch folder, the run
# manifest and the event log. They are left out of the comparison at the top of any labhq workspace.
LABHQ_OWNED = frozenset({".labhq", "manifest.json", "events.jsonl"})

Entry = tuple


def _is_link(item: os.DirEntry) -> bool:
    """A symlink, or on Windows any reparse point (junction, mount point): never followed."""
    if item.is_symlink():
        return True
    return bool(getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _entry(item: os.DirEntry, link: bool) -> Entry:
    st = item.stat(follow_symlinks=False)
    kind = "link" if link else "dir" if item.is_dir(follow_symlinks=False) else "file"
    target = ""
    if link:
        try:
            target = os.readlink(item.path)
        except (OSError, ValueError):
            target = "?"
    if kind == "dir":  # a listing change shows up as added/removed entries; labhq's own atomic manifest
        return (kind, 0, 0, 0, "")  # rewrite would otherwise change a workspace folder's mtime and ctime
    ctime = st.st_ctime_ns if os.name != "nt" else 0  # Windows st_ctime is the creation time
    size = st.st_size if kind == "file" else 0
    return (kind, size, st.st_mtime_ns, ctime, target)


def snapshot(roots: list[tuple[str, Path]], max_entries: int, owned_roots: set[Path] = frozenset(),
             skip: list[Path] = ()) -> tuple[dict[str, Entry], str | None]:
    """{"label/relative/path": entry} for every entry under `roots`, or an error that refuses the check.

    `owned_roots` are labhq workspaces (their LABHQ_OWNED names are left out). `skip` are subtrees another task is
    writing right now; their changes cannot be told apart from this run's, so they are not compared.
    """
    seen: dict[str, Entry] = {}
    skipped = set(skip)
    for label, root in roots:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    listing = list(entries)
            except OSError:
                return seen, f"{label}/{Path(current).relative_to(root).as_posix()} 폴더를 읽을 수 없음"
            for item in listing:
                if item.name in LABHQ_OWNED and current in owned_roots:
                    continue
                path = Path(item.path)
                if path in skipped:
                    continue
                if len(seen) >= max_entries:
                    return seen, f"하위 항목이 상한 {max_entries}개를 넘음"
                try:
                    link = _is_link(item)
                    seen[f"{label}/{path.relative_to(root).as_posix()}"] = _entry(item, link)
                    descend = not link and item.is_dir(follow_symlinks=False)
                except OSError:
                    continue  # gone between listing and stat: the other listing records it as removed
                if descend:
                    stack.append(path)
    return seen, None


def changes(before: dict[str, Entry], after: dict[str, Entry]) -> list[str]:
    """Readable change list: `+ added`, `- removed`, `~ modified`, sorted."""
    out = [f"+ {k}" for k in after.keys() - before.keys()]
    out += [f"- {k}" for k in before.keys() - after.keys()]
    out += [f"~ {k}" for k in before.keys() & after.keys() if before[k] != after[k]]
    return sorted(out, key=lambda line: line[2:])


def watch_roots(workdir: Path, dirs: list[str], busy: list[Path]) -> tuple[list[tuple[str, Path]], list[Path], list[str]]:
    """(labelled roots, subtrees to skip, notes) for a read-only run in `workdir` that can reach `dirs`.

    Only folders the runner account can write are watched. A root inside another is covered by it. `busy` are
    folders another task is writing now: a root inside one is not compared, one inside a root is skipped. Labels
    (`workdir`, `dir1`…) stand in for local paths in errors, which reach reports.
    """
    candidates = [Path(workdir).resolve()]
    for d in dirs:
        path = Path(d).resolve()
        if path.is_dir() and os.access(path, os.W_OK) and path not in candidates:
            candidates.append(path)
    kept: list[Path] = []
    for path in sorted(candidates, key=lambda p: len(p.parts)):
        if not any(path == k or path.is_relative_to(k) for k in kept):
            kept.append(path)
    labels = {candidates[0]: "workdir"}
    labels.update({p: f"dir{i}" for i, p in enumerate((c for c in candidates[1:]), start=1)})
    roots, skip, notes = [], [], []
    for path in kept:
        if any(path == b or path.is_relative_to(b) for b in busy):
            notes.append(f"{labels[path]} (다른 작업이 쓰는 중)")
            continue
        roots.append((labels[path], path))
        for b in busy:
            if b.is_relative_to(path):
                skip.append(b)
                notes.append(f"{labels[path]}/{b.relative_to(path).as_posix()} (다른 작업이 쓰는 중)")
    return roots, skip, notes


@dataclass
class ReadOnlyWatch:
    roots: list[tuple[str, Path]]
    max_entries: int
    owned: set[Path] = field(default_factory=set)
    skip: list[Path] = field(default_factory=list)
    baseline: dict[str, Entry] | None = None
    started: float = field(default_factory=time.time)

    def take_baseline(self) -> str | None:
        """None, or why the run must not start (too many entries, unreadable folder)."""
        listing, error = snapshot(self.roots, self.max_entries, self.owned, self.skip)
        if error:
            self.baseline = None
            return (f"읽기 전용 실행 거부: 쓰기 확인을 할 수 없음 ({error}). 작업 폴더·프로젝트 폴더를 줄이거나 "
                    "runner.read_only_check_max_entries를 올리세요 (read-only policy)")
        self.baseline = listing
        return None

    def changed(self, ignore: list[Path] = ()) -> tuple[list[str], int]:
        """(changes, how many were left out) since the baseline. `ignore` are folders a task that started during
        the run was writing. A folder that cannot be listed afterwards counts as a change."""
        listing, error = snapshot(self.roots, self.max_entries, self.owned, self.skip)
        found = changes(self.baseline or {}, listing)
        if error:  # an incomplete listing: what it did not reach is not "removed"
            found = [line for line in found if not line.startswith("- ")]
        roots = dict(self.roots)

        def other_writer(line: str) -> bool:
            label, _, relative = line[2:].partition("/")
            path = roots[label] / relative
            return any(path == b or path.is_relative_to(b) for b in ignore)

        kept = [line for line in found if not other_writer(line)]
        return [*kept, f"! {error}"] if error else kept, len(found) - len(kept)
