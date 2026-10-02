"""Files labhq itself writes inside a task workspace, never through a link (#165).

The runner and the adapters write these from outside every engine sandbox. A reused workspace (retry, HPC wake-up,
follow-up) was writable to an earlier agent, which could have replaced one of them, or a folder above it, with a
symlink or Windows junction leading out of the workspace. Writing through it would overwrite the target with role
instructions or results. One rule for every such write: the folders on the way must be plain folders, and a link at
the file itself is replaced, never followed.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePath

from .held_dir import HeldDir, NotPlainFolder

# Folders labhq creates in a workspace and the files it writes there (TaskWorkspace, the adapters' prepare(), the
# runner's result files). `.labhq/*` is the adapters' scratch folder: every entry in it is labhq's.
OWNED_DIRS = (".labhq", "outputs", "jobs", "jobs/logs", ".gemini")
OWNED_FILES = ("TASK*.md", "manifest.json", "events.jsonl", "jobs.jsonl", "AGENTS.md", "GEMINI.md",
               ".gemini/settings.json", "outputs/RESULT*.md", ".labhq/*")


class OwnedPathError(RuntimeError):
    """A path labhq writes in a workspace is a link or not a folder; nothing was written through it."""


def case_sensitive_directory(path: Path) -> bool:
    """Whether differently cased names identify different entries on this folder's volume (#315)."""
    path = Path(path)
    name = path.name
    alternate = next((name[:i] + char.swapcase() + name[i + 1:]
                      for i, char in enumerate(name) if char.isascii() and char.isalpha()), name)
    if alternate == name:
        return os.path.normcase("A") != os.path.normcase("a")
    try:
        return not os.path.samefile(path, path.with_name(alternate))
    except FileNotFoundError:
        return True
    except OSError:
        return os.path.normcase("A") != os.path.normcase("a")


def is_link(path: Path) -> bool:
    """A symlink or Windows reparse point (including a junction), without following it."""
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def remove_entry(path: Path) -> None:
    """Remove one entry without following a symlink or Windows junction."""
    path = Path(path)
    if not os.path.lexists(path):
        return
    if path.is_symlink():
        path.unlink()  # POSIX directory symlinks need unlink(), not rmdir()
    elif is_link(path):
        path.rmdir()  # Windows junction: rmdir unlinks the reparse point, not its target
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def plain_directory(root: Path, relative: PurePath | str) -> Path | None:
    """`root/relative` as plain folders, creating missing ones. None when a component is a link or not a folder:
    that component is left as it is, so the caller refuses instead of writing through it (#190)."""
    current = Path(root)
    for part in PurePath(relative).parts:
        current /= part
        if not os.path.lexists(current):
            current.mkdir(exist_ok=True)
        if is_link(current) or not current.is_dir():
            return None
    return current


def _parent(root: Path, relative: PurePath) -> Path:
    parent = plain_directory(root, relative.parent)
    if parent is None:
        raise OwnedPathError(f"labhq does not write {relative.as_posix()}: a folder on the way is a link or not "
                             "a folder in the workspace")
    return parent


@contextmanager
def _held_parent(root: Path, relative: PurePath):
    """Hold the checked parent so later file opens cannot be redirected by replacing a folder."""
    _parent(root, relative)
    held = None
    try:
        held = HeldDir.hold(Path(root))
        for part in relative.parent.parts:
            child = held.child(part)
            held.close()
            held = child
    except (OSError, NotPlainFolder) as exc:
        if held is not None:
            held.close()
        raise OwnedPathError(f"labhq does not write {relative.as_posix()}: its parent was replaced or is not plain") from exc
    try:
        yield held
    finally:
        held.close()


def write_owned(root: Path, relative: str, text: str) -> Path:
    """Replace one labhq file in a workspace. A link at the file itself is replaced, not followed."""
    rel = PurePath(relative)
    target = Path(root) / rel
    temporary = f".{rel.name}.labhq-{uuid.uuid4().hex}.tmp"
    try:
        with _held_parent(root, rel) as parent:
            if os.path.lexists(target):
                linked = is_link(target)
                if (linked and target.is_dir()) or (not linked and not target.is_file()):
                    raise OwnedPathError(f"labhq does not write {rel.as_posix()}: a folder or non-file occupies it")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = parent.open_file(temporary, flags, 0o666)
            try:
                with open(fd, "w", encoding="utf-8") as out:
                    fd = -1
                    out.write(text)
                    out.flush()
                    os.fsync(out.fileno())
                parent.replace(temporary, rel.name)
                parent.fsync()
            finally:
                if fd >= 0:
                    os.close(fd)
                parent.unlink(temporary)
    except OwnedPathError:
        raise
    except OSError as exc:
        raise OwnedPathError(f"labhq does not write {rel.as_posix()}: {exc}") from exc
    return target


def read_owned(root: Path, relative: str) -> str | None:
    """Text of one labhq file in a workspace; None when it is missing, a link or not a file, or a folder on the way
    is a link. An agent can swap manifest.json for a link to a file it may not read; reading it back into labhq's own
    records would copy that file into the workspace (#165)."""
    rel = PurePath(relative)
    current = Path(root)
    try:
        for part in rel.parent.parts:
            current /= part
            if is_link(current) or not current.is_dir():
                return None
        target = current / rel.name
        if is_link(target) or not target.is_file():
            return None
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError:  # missing, or swapped for a link after the check (O_NOFOLLOW)
        return None
    with open(fd, encoding="utf-8") as source:
        return source.read()


def append_owned(root: Path, relative: str, text: str) -> None:
    """Append to one labhq log in a workspace. A link at the file is removed first; the append never follows one."""
    rel = PurePath(relative)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_BINARY", 0))  # Python's text layer translates newlines; the CRT must not as well
    try:
        with _held_parent(root, rel) as parent:
            target = Path(root) / rel
            if os.path.lexists(target) and is_link(target):
                remove_entry(target)
            with open(parent.open_file(rel.name, flags, 0o666), "a", encoding="utf-8") as out:
                out.write(text)
    except OwnedPathError:
        raise
    except OSError as exc:
        raise OwnedPathError(f"labhq does not append {rel.as_posix()}: {exc}") from exc


def chmod_owned(root: Path, relative: str, mode: int, gid: int | None = None) -> None:
    """Change one regular owned file through its held parent, never through a replacement link."""
    rel = PurePath(relative)
    try:
        with _held_parent(root, rel) as parent:
            fd = parent.open_file(rel.name, os.O_WRONLY | getattr(os, "O_BINARY", 0))
            try:
                if gid is not None and os.name != "nt":
                    os.fchown(fd, -1, gid)
                os.fchmod(fd, mode)
            finally:
                os.close(fd)
    except OwnedPathError:
        raise
    except OSError as exc:
        raise OwnedPathError(f"labhq does not change {rel.as_posix()}: {exc}") from exc


def owned_link_error(workdir: Path) -> str | None:
    """Why labhq must not write in this reused workspace: one of its own paths is a link or the wrong type.

    Only names relative to the workspace are returned; the message reaches reports.
    """
    workdir = Path(workdir)
    for folder in OWNED_DIRS:
        current = workdir
        for part in PurePath(folder).parts:
            current /= part
            if not os.path.lexists(current):
                break
            name = current.relative_to(workdir).as_posix()
            if is_link(current):
                return f"labhq가 쓰는 경로가 링크임: {name}"
            if not current.is_dir():
                return f"labhq가 쓰는 폴더 자리에 다른 것이 있음: {name}"
    for pattern in OWNED_FILES:
        folder, _, name_glob = pattern.rpartition("/")
        directory = workdir / folder if folder else workdir
        if not directory.is_dir() or is_link(directory):
            continue
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return f"labhq가 쓰는 폴더를 읽을 수 없음: {folder or '.'}"
        for entry in entries:
            candidate, pattern = ((entry.name, name_glob) if case_sensitive_directory(directory)
                                  else (entry.name.casefold(), name_glob.casefold()))
            if fnmatch.fnmatchcase(candidate, pattern) and is_link(Path(entry.path)):
                return f"labhq가 쓰는 경로가 링크임: {Path(entry.path).relative_to(workdir).as_posix()}"
    return None
