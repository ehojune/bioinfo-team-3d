"""Put files on the HPC and bring small ones back, through the session's shell (#1308, #1295 E).

Some sites do not share a file system with the user's PC: a job script written here does not exist on the cluster. The
session shell (``hpc_session``) is already open, so files cross it as text, one command line at a time, and every
transfer is checked by a sha256 taken on the far side. It costs no login: the shell is the same one the scheduler
commands use.

* Small files only. A file over the limit is refused with the way out: the site profile's own transfer method, which is
  a login of its own and is the user's to run.
* Paths. A remote path is relative to the profile's ``remote_workdir`` and stays under it: ``..``, absolute paths and odd
  characters are refused here, and the far side resolves the folder (``pwd -P``) and refuses a link that leads out.
  A local path written by a fetch stays under the folder the caller names, the same way.
* A name is never put into a command unquoted.

The module knows no site: it needs a ``run(command)`` that returns an object with ``exit_code`` and ``output`` (the
session's ``run``), and the remote root.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shlex
import stat
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .adapters.held_dir import HeldDir

SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+=,:%-]*")  # one path component: no space, quote, glob or leading dash


class TransferError(RuntimeError):
    """A transfer did not happen. Nothing half-written is left in place."""


class PathRejected(TransferError):
    """A path leaves the allowed folder or has characters this module will not put into a command."""


class TooLarge(TransferError):
    """The file is over the limit. Use the site's own transfer method for it."""


class HashMismatch(TransferError):
    """The far side holds different bytes than were sent (or than it reports)."""


@dataclass(frozen=True)
class Limits:
    upload_max_bytes: int = 512 * 1024  # one file; a larger one is the site transfer method's job
    fetch_max_bytes: int = 1024 * 1024  # one file
    fetch_total_bytes: int = 8 * 1024 * 1024  # all files of one job
    chunk_chars: int = 2400  # one command line (a terminal line holds about 4096 characters)
    fetch_chunk_bytes: int = 45000  # decoded bytes per fetch command (about 60,000 characters back)
    list_depth: int = 3


def confine(rel: str) -> PurePosixPath:
    """A relative POSIX path made of safe components, or ``PathRejected``."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        raise PathRejected(f"path must be relative and plain: {rel!r}")
    parts = rel.split("/")
    for part in parts:
        if part in ("", ".", "..") or not SAFE_PART.fullmatch(part):
            raise PathRejected(f"path component {part!r} is not allowed in {rel!r}")
    return PurePosixPath(*parts)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


LOG_SUFFIXES = (".log", ".out", ".err")  # job logs and the logs a job writes among its results are hidden (#1357)
_GENERIC_FOLDERS = {"home", "tmp", "scratch", "data", "work", "users", "user", "var", "mnt", "opt", "usr", "lib",
                    "bin", "share", "local", "lustre", "gpfs", "project", "projects", "group", "groups"}
# Terminal colour and control sequences: a log keeps its text, and a path cannot hide behind "\x1b[31m".
_ANSI = re.compile("\x1b\\[[0-?]*[ -/]*[@-~]|\x1b\\][^\x07\x1b]*(?:\x07|\x1b\\\\)?|\x1b[@-Z\\\\-_]")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")  # public addresses stay readable
# Any absolute POSIX path, one part or many, wherever it starts: after any character that cannot belong to a relative
# path or a word (so "a/b" and "1/2" stay), up to whitespace, a quote, a bracket or a separator such as ':' or ';'.
_ABSOLUTE_PATH = re.compile(r"""(?<![\w.~\-/])/[^\s"'<>|;,:()\[\]{}`]+""")
_WORKDIR_TAIL = re.compile(r"""<workdir>/[^\s"'<>|;,:()\[\]{}`]*""")  # inside the hidden work folder: kept as is


def is_log(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in LOG_SUFFIXES


class Transfer:
    def __init__(self, run: Callable[[str], object], root: str, limits: Limits = Limits(),
                 redact: Callable[[str], str] | None = None):
        if not isinstance(root, str) or not root.startswith("/") or ".." in root.split("/") or "\n" in root:
            raise PathRejected("the remote work folder is an absolute POSIX path without '..'")
        self.run, self.root, self.limits = run, root.rstrip("/") or "/", limits
        self._real_root: str | None = None
        self.deadline: float | None = None  # time.monotonic() after which no further command is sent
        self._redact = redact  # hides the site's host, port and user in what the cluster printed

    # ---- the far side ------------------------------------------------------------------------------------------
    def _do(self, command: str, what: str) -> str:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TransferError(f"{what}: the time allowed for this transfer is used up; nothing more was sent")
        result = self.run(command)
        if getattr(result, "exit_code", 1) != 0:
            raise TransferError(f"{what} failed on the cluster: {self._hide(str(getattr(result, 'output', ''))[:200].strip())}")
        return str(result.output)

    def _hide(self, text: str) -> str:
        """What the cluster printed, for an error message the agent and the web feed may show: the site's host, port and
        user names, and the work folder's own path (which names the user's account there), are hidden."""
        for path in sorted({self.root, self._real_root or self.root}, key=len, reverse=True):
            if path and path != "/":
                text = text.replace(path, "<workdir>")  # first: the path holds the user name the redactor would cut up
        return self._redact(text) if self._redact is not None else text

    def real_root(self) -> str:
        if self._real_root is None:
            out = self._do(f"mkdir -p -- {shlex.quote(self.root)} && cd -- {shlex.quote(self.root)} && pwd -P",
                           "opening the work folder").strip().splitlines()
            if not out or not out[-1].startswith("/"):
                raise TransferError("the cluster did not report the work folder")
            self._real_root = out[-1]
        return self._real_root

    def path(self, rel: str) -> str:
        return f"{self.root}/{confine(rel)}"

    def ensure_dir(self, rel_dir: str) -> str:
        """Make a folder under the root and return its real path. A folder that is, or leads through, a link out of the
        root is refused."""
        real_root = real = self.real_root()
        parts = [] if rel_dir in ("", ".") else list(confine(rel_dir).parts)
        current = self.root
        for part in parts:  # one level at a time: a link that leads out is found before anything is made beyond it
            current = f"{current}/{part}"
            quoted = shlex.quote(current)
            out = self._do(f"test -d {quoted} && cd -- {quoted} && pwd -P || echo missing", "checking a folder").strip().splitlines()
            if not out or out[-1] == "missing":
                # Independent jobs can discover a missing request parent at the same time.
                out = self._do(f"mkdir -p -- {quoted} && cd -- {quoted} && pwd -P", "making a folder").strip().splitlines()
            real = out[-1] if out else ""
            if real != real_root and not real.startswith(real_root.rstrip("/") + "/"):
                raise PathRejected(f"{rel_dir!r} leads outside the work folder")
        return real

    def make_fresh_dir(self, rel_dir: str) -> str:
        """Make a folder that did not exist (mode 700) and return its real path. ``mkdir`` without ``-p`` fails on any
        existing name, a link included, so the folder is the one just made: nothing could be placed in it, and its name
        is unguessable. Its parent is checked on the way; the real path must lie under the work folder."""
        path = confine(rel_dir)
        parent = str(path.parent) if str(path.parent) != "." else ""
        self.ensure_dir(parent)
        real_root = self.real_root()
        target = self.path(rel_dir)
        out = self._do(f"mkdir -m 700 -- {shlex.quote(target)} && cd -- {shlex.quote(target)} && pwd -P",
                       "making the job folder").strip().splitlines()
        real = out[-1] if out else ""
        if real != real_root and not real.startswith(real_root.rstrip("/") + "/"):
            raise PathRejected(f"{rel_dir!r} leads outside the work folder")
        return real

    def _is_link(self, remote: str) -> bool:
        return self._do(f"test -L {shlex.quote(remote)} && echo link || echo plain", "checking a file").strip() == "link"

    def plain_dir(self, rel: str) -> None:
        """A persisted job folder must still have its original plain path, including links inside the root."""
        parts = confine(rel).parts
        for count in range(1, len(parts) + 1):
            if self._is_link(self.path('/'.join(parts[:count]))):
                raise PathRejected("remote job folder was replaced by a link")
        if self.ensure_dir(rel) != self.real_root().rstrip('/') + '/' + rel:
            raise PathRejected("remote job folder changed its physical path")

    def exists(self, rel: str) -> bool:
        """Is there a plain file at ``rel`` (a link does not count)?"""
        return self._do(f"test -f {shlex.quote(self.path(rel))} && echo yes || echo no", "checking a file").strip() == "yes"

    def remote_sha256(self, rel: str) -> str:
        out = self._do(f"sha256sum -- {shlex.quote(self.path(rel))}", "reading a checksum").split()
        if not out or not re.fullmatch(r"[0-9a-f]{64}", out[0]):
            raise TransferError("the cluster returned no checksum")
        return out[0]

    # ---- upload ------------------------------------------------------------------------------------------------
    def upload_bytes(self, rel: str, data: bytes, *, executable: bool = False) -> dict:
        """Write ``data`` to ``rel`` under the root. The file appears only once the far side's sha256 equals ours."""
        if len(data) > self.limits.upload_max_bytes:
            raise TooLarge(f"{rel}: {len(data)} bytes is over the {self.limits.upload_max_bytes} byte limit; use the site "
                           "profile's file transfer method (a login of its own) and put it under the work folder")
        path = confine(rel)
        folder = str(path.parent) if str(path.parent) != "." else ""
        self.ensure_dir(folder)
        final = self.path(rel)
        if self._is_link(final):
            raise PathRejected(f"{rel!r} is a link on the cluster; it is not overwritten")
        token = uuid.uuid4().hex[:10]
        staged, encoded = f"{final}.up-{token}", f"{final}.up-{token}.b64"
        want = sha256_hex(data)
        text = base64.b64encode(data).decode("ascii")
        try:
            self._do(f": > {shlex.quote(encoded)}", "starting the upload")
            for at in range(0, len(text), self.limits.chunk_chars):
                self._do(f"printf %s {shlex.quote(text[at:at + self.limits.chunk_chars])} >> {shlex.quote(encoded)}",
                         "sending a block")
            self._do(f"base64 -d < {shlex.quote(encoded)} > {shlex.quote(staged)}", "decoding the upload")
            got = self._do(f"sha256sum -- {shlex.quote(staged)}", "checking the upload").split()
            if not got or got[0] != want:
                raise HashMismatch(f"{rel}: the cluster holds {got[0] if got else 'nothing'}, sent {want}")
            if executable:
                self._do(f"chmod 0700 {shlex.quote(staged)}", "setting the mode")
            self._do(f"mv -f -- {shlex.quote(staged)} {shlex.quote(final)}", "placing the file")
        finally:
            try:
                self.run(f"rm -f -- {shlex.quote(encoded)} {shlex.quote(staged)}")
            except Exception:  # noqa: BLE001 - the leftover is a temporary file; the original error is what matters
                pass
        return {"path": rel, "bytes": len(data), "sha256": want}

    def upload_file(self, local: Path, rel: str, *, root: Path | None = None, executable: bool = False) -> dict:
        """Upload a regular local file. With ``root``, the file is read inside it through folder handles held open (no
        link on the way, none swapped in after the check, no ``..``)."""
        local = Path(local)
        if root is not None:
            try:
                relative = local.resolve(strict=False).relative_to(Path(root).resolve())
            except (ValueError, OSError) as error:
                raise PathRejected(f"{local} is outside {root}") from error
            data = read_confined(Path(root), str(PurePosixPath(*relative.parts)), self.limits.upload_max_bytes)
            return self.upload_bytes(rel, data, executable=executable)
        try:
            if local.is_symlink() or not local.is_file():
                raise PathRejected(f"{local} is not a plain file")
            size = local.stat().st_size
            if size > self.limits.upload_max_bytes:
                raise TooLarge(f"{local.name}: {size} bytes is over the {self.limits.upload_max_bytes} byte limit; use the "
                               "site profile's file transfer method")
            data = local.read_bytes()
        except OSError as error:
            raise TransferError(f"cannot read {local.name}: {error}") from error
        return self.upload_bytes(rel, data, executable=executable)

    # ---- fetch -------------------------------------------------------------------------------------------------
    def list_files(self, rel_dir: str) -> list[tuple[str, int]]:
        """(relative path, size) of the plain files under ``rel_dir``, a few levels deep. Links are not listed."""
        folder = self.ensure_dir(rel_dir)
        out = self._do(f"find {shlex.quote(folder)} -maxdepth {self.limits.list_depth} -type f -printf '%s %P\\n'",
                       "listing results")
        files = []
        for line in out.splitlines():
            size, _, name = line.partition(" ")
            if size.isdigit() and name:
                files.append((name, int(size)))
        return sorted(files, key=lambda item: item[0])

    def list_deeper(self, rel_dir: str, limit: int = 50) -> list[str]:
        """Files below the depth ``list_files`` looks at (at most ``limit`` names), so they can be named as left behind."""
        folder = self.ensure_dir(rel_dir)
        out = self._do(f"find {shlex.quote(folder)} -mindepth {self.limits.list_depth + 1} -type f -printf '%P\\n' | head -n {limit}",
                       "listing deeper results")
        return [line for line in out.splitlines() if line.strip()]

    def fetch_bytes(self, rel: str, *, tail: bool = False, max_bytes: int | None = None) -> tuple[bytes, dict]:
        """The file's bytes and ``{"sha256", "bytes", "truncated"}``. A file over the limit is refused, except with
        ``tail`` (a log): then its last ``max_bytes`` come back and ``truncated`` says so. The checksum is of what came."""
        limit = max_bytes or self.limits.fetch_max_bytes
        remote = self.path(rel)
        self.ensure_dir(str(confine(rel).parent) if str(confine(rel).parent) != "." else "")
        if self._is_link(remote):
            raise PathRejected(f"{rel!r} is a link on the cluster; it is not read")
        size_text = self._do(f"wc -c < {shlex.quote(remote)}", "reading the size").strip()
        if not size_text.isdigit():
            raise TransferError(f"the cluster did not report the size of {rel}")
        size = int(size_text)
        if size > limit and not tail:
            raise TooLarge(f"{rel}: {size} bytes is over the {limit} byte limit; use the site profile's file transfer method")
        start, take = (max(size - limit, 0), min(size, limit))
        pieces, done = [], 0
        while done < take:
            n = min(self.limits.fetch_chunk_bytes, take - done)
            out = self._do(f"tail -c +{start + done + 1} {shlex.quote(remote)} | head -c {n} | base64 | tr -d '\\n'",
                           "reading a block").strip()
            try:
                block = base64.b64decode(out, validate=True)
            except ValueError as error:
                raise TransferError(f"{rel}: the cluster sent a block that is not valid") from error
            if not block:
                break
            pieces.append(block)
            done += len(block)
        data = b"".join(pieces)
        if len(data) != take:
            raise HashMismatch(f"{rel}: expected {take} bytes, received {len(data)}")
        if start == 0 and take == size:  # the whole file: compare with the cluster's own checksum
            remote_sha = self.remote_sha256(rel)
            if remote_sha != sha256_hex(data):
                raise HashMismatch(f"{rel}: the received bytes do not match the cluster's checksum")
        return data, {"sha256": sha256_hex(data), "bytes": len(data), "truncated": start > 0}

    def hide_log(self, text: str) -> str:
        """A job log as it may be kept on this PC (#1357): ``_hide``, then every folder name of the work folder's path
        (the account and site names it carries) and every absolute path left. Stricter than an error message, because
        a log is a file that can reach a report or a result repository."""
        text = self._hide(_ANSI.sub("", text).replace("\x00", ""))
        words = {part for path in {self.root, self._real_root or self.root} for part in path.split("/")
                 if len(part) >= 3 and part.lower() not in _GENERIC_FOLDERS}
        for word in sorted(words, key=len, reverse=True):
            text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", "<site>", text, flags=re.I)
        urls: list[str] = []

        def keep(match: re.Match[str]) -> str:
            urls.append(match.group(0))
            return f"\x00{len(urls) - 1}\x00"

        text = _ABSOLUTE_PATH.sub("<remote-path>", _WORKDIR_TAIL.sub(keep, _URL.sub(keep, text)))
        return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], text)

    def fetch_log(self, rel: str, into: Path, *, tail: bool = False, max_bytes: int | None = None,
                  name: str | None = None) -> dict:
        """``fetch_file`` for a log: the text is hidden with ``hide_log`` before anything is written, so the cluster's
        bytes never land on this PC. ``sha256``/``bytes`` describe the kept file, ``remote_sha256`` what came."""
        data, info = self.fetch_bytes(rel, tail=tail, max_bytes=max_bytes)
        kept = self.hide_log(data.decode("utf-8", errors="replace")).encode("utf-8")
        target = Path(into) / (name or str(confine(rel)))
        write_confined(Path(into), target, kept)
        return {"path": str(confine(rel)), "local": str(target), "sha256": sha256_hex(kept), "bytes": len(kept),
                "truncated": info["truncated"], "remote_sha256": info["sha256"], "remote_bytes": info["bytes"],
                "redacted": kept != data}

    def fetch_file(self, rel: str, into: Path, *, tail: bool = False, max_bytes: int | None = None,
                   name: str | None = None) -> dict:
        """Fetch ``rel`` to ``into / name`` (default: the same relative path). The local path stays under ``into``."""
        data, info = self.fetch_bytes(rel, tail=tail, max_bytes=max_bytes)
        target = Path(into) / (name or str(confine(rel)))
        write_confined(Path(into), target, data)
        return {"path": str(confine(rel)), "local": str(target), **info}


def read_confined(root: Path, rel: str, limit: int) -> bytes:
    """The bytes of a regular file at ``rel`` under ``root``, read through handles: ``root`` and each folder are opened
    once without following a link and the file is opened relative to its folder, so a link swapped in after any check
    is refused (never followed). Size and type come from the same open descriptor that is read."""
    parts = confine(rel).parts
    try:
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(HeldDir.hold(Path(root)))
            for part in parts[:-1]:
                held = stack.enter_context(held.child(part))
            fd = held.open_read_file(parts[-1])
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise PathRejected(f"{rel!r} is not a plain file")
                if info.st_size > limit:
                    raise TooLarge(f"{rel}: {info.st_size} bytes is over the {limit} byte limit; use the site profile's "
                                   "file transfer method")
                data = handle.read(limit + 1)
    except TransferError:
        raise
    except OSError as error:  # a link, a missing file or folder, or one that is not what it was
        raise PathRejected(f"{rel!r} is not a plain file inside the task folder: {error}") from error
    if len(data) > limit:
        raise TooLarge(f"{rel}: grew past the {limit} byte limit while it was read")
    return data


def _inside(path: Path, root: Path) -> bool:
    try:
        resolved, base = path.resolve(), root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def write_confined(root: Path, target: Path, data: bytes) -> None:
    """Write ``data`` to ``target``, which must lie under ``root`` and must not pass through or replace a link."""
    root, target = Path(root), Path(target)
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise PathRejected(f"{target} is outside {root}") from error
    if ".." in relative.parts:
        raise PathRejected(f"{target} is outside {root}")
    root.mkdir(parents=True, exist_ok=True)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise PathRejected(f"{current} is a link; nothing is written through it")
        current.mkdir(exist_ok=True)
    if target.is_symlink():
        raise PathRejected(f"{target} is a link; it is not overwritten")
    if not _inside(target.parent, root):
        raise PathRejected(f"{target} is outside {root}")
    staged = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        staged.write_bytes(data)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


# ---- a job's files (the runner's side) ---------------------------------------------------------------------------------

MAX_INPUTS = 20
INPUTS_TOTAL_BYTES = 4 * 1024 * 1024
UPLOAD_BUDGET_S = 300.0  # all the commands of one job's upload together; the caller's wait is sized from it


def job_spec(task_id: str, stem: str, request_id: str | None = None) -> dict:
    """Where a job's files live on the cluster, relative to the work folder. Every job gets a folder of its own, named
    with a random token and made fresh for it (``make_fresh_dir``): nothing can be laid there beforehand, and no other
    job of the task shares it. Its script, logs and run folder are inside. The task id and the script name are checked
    like any path."""
    base = str(confine(str(task_id or "task")))
    if "/" in base:
        raise PathRejected(f"task id {task_id!r} is not a plain name")
    folder = f"{base}/job-{uuid.uuid4().hex[:12]}"
    if request_id:
        base = request_data_root(request_id)
        folder = f"{base}/job-{uuid.uuid4().hex[:12]}"
    return {"base": base, "dir": folder, "script": f"{folder}/{confine(stem + '.sh')}", "work": f"{folder}/work",
            "logs": [f"{folder}/logs/{confine(stem + '.out')}", f"{folder}/logs/{confine(stem + '.err')}"],
            "stem": stem, **({"request_id": request_id, "data": f"{folder}/work"} if request_id else {})}


def request_data_root(request_id: str) -> str:
    if not isinstance(request_id, str) or not request_id:
        raise PathRejected("a remote data folder requires a request identity")
    return "requests/" + hashlib.sha256(request_id.encode()).hexdigest()


def bounded_path_preview(lines: Iterable[str], max_chars: int = 4096, max_entries: int = 32) -> str:
    """Whole preview lines only; the runner supplies a separately readable full inventory."""
    marker = "... (preview truncated; read the full inventory)"
    kept, used = [], 0
    for line in lines:
        if len(kept) >= max_entries or used + len(line) + len(marker) + 2 > max_chars:
            return "\n".join([*kept, marker])[:max_chars]
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def local_copy_conflicts(root: Path, paths: list[str], hashes: dict, receipts: dict) -> list[str]:
    """Reject local aliases without a matching small-file transport receipt; never hash large outputs."""
    conflicts = []
    try:
        with HeldDir.hold(root) as base:
            for path in paths:
                if path in hashes and (path not in receipts or hashes[path] != receipts[path]):
                    conflicts.append(path)
                    continue
                parts = confine(path).parts
                with contextlib.ExitStack() as opened:
                    parent = base
                    try:
                        for part in parts[:-1]:
                            parent = opened.enter_context(parent.child(part))
                        fd = parent.open_read_file(parts[-1])
                        try:
                            plain = os.fstat(fd).st_nlink == 1
                        finally:
                            os.close(fd)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        conflicts.append(path)
                        continue
                    if not plain or path not in receipts or hashes.get(path) != receipts[path]:
                        conflicts.append(path)
    except OSError:
        return list(paths)  # an unavailable local namespace cannot establish absence of aliases
    return conflicts


# List names and sizes through held directories. Large outputs stay exactly where the job wrote them;
# listing never reads file contents, changes permissions, copies or moves them.
REMOTE_DATA_HELPER = r'''
import os, sys, json, stat, contextlib, uuid, re
root, job, action = sys.argv[1:4]
with contextlib.ExitStack() as stack:
 skipped=[]
 @contextlib.contextmanager
 def folder(base, path, create=False):
  with contextlib.ExitStack() as opened:
   fd=base
   for part in path.split('/') if path else []:
    if not part or part in ('.','..'): raise ValueError('unsafe remote path')
    if create:
     try: os.mkdir(part, 0o700, dir_fd=fd)
     except FileExistsError: pass
    fd=os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
    opened.callback(os.close, fd)
   yield fd
 def read(base, name):
  fd=os.open(name, os.O_RDONLY|os.O_NOFOLLOW, dir_fd=base)
  s=os.fstat(fd)
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:
   os.close(fd); raise ValueError('not a plain remote file')
  return fd, s
 def walk(base, directories, prefix='', depth=0):
  if depth>16:
   skipped.append({'path':prefix.rstrip('/'),'reason':'depth limit'}); return
  for name in sorted(os.listdir(base)):
   if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@+=,:%-]*',name):
    skipped.append({'path':prefix+name,'reason':'name not allowed'}); continue
   s=os.stat(name,dir_fd=base,follow_symlinks=False)
   path=prefix+name
   if stat.S_ISDIR(s.st_mode):
    directories.append(path)
    with folder(base,name) as child: yield from walk(child,directories,path+'/',depth+1)
   elif stat.S_ISREG(s.st_mode) and s.st_nlink==1:
    yield path,base,name
   else: skipped.append({'path':path,'reason':'link or special file'})
 def info(base,name):
  fd,s=read(base,name)
  try:
   return {'bytes':s.st_size}
  finally: os.close(fd)
 r=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); stack.callback(os.close,r)
 j=stack.enter_context(folder(r,job))
 f,_=read(j,sys.argv[4])
 with os.fdopen(f,'rb') as cfg_file: cfg=json.loads(cfg_file.read(4*1024*1024+1))
 data=stack.enter_context(folder(r,cfg['data']))
 if action=='scan':
  rows=[]; directories=[]
  for path,parent,name in walk(data,directories):
   rows.append(dict(path=path,**info(parent,name)))
   if len(rows)>20000: raise ValueError('too many remote outputs')
  cfg['files']=rows; cfg['directories']=directories; cfg['bytes']=sum(x['bytes'] for x in rows)
  cfg['skipped']=skipped
  cfg.pop('inputs',None)
  payload=json.dumps(cfg,separators=(',',':')).encode()
  if len(payload)>4*1024*1024: raise ValueError('remote manifest too large')
  temporary='remote-data-'+uuid.uuid4().hex+'.tmp'
  fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=j)
  try:
   with os.fdopen(fd,'wb') as out: out.write(payload)
   os.rename(temporary,'remote-data.json',src_dir_fd=j,dst_dir_fd=j)
  finally:
   try: os.unlink(temporary,dir_fd=j)
   except FileNotFoundError: pass
 else:
  for row in cfg['files']:
   parts=row['path'].split('/')
   with folder(data,'/'.join(parts[:-1])) as parent:
    if info(parent,parts[-1])['bytes']!=row['bytes']: raise ValueError('remote input size differs')
  for path in cfg.get('directories',[]):
   with folder(data,path): pass
'''


def validate_remote_manifest(manifest: dict, request_id: str) -> dict:
    """Validate the wire inventory before exposing any path to a consumer."""
    base = request_data_root(request_id)
    if (not isinstance(manifest, dict) or manifest.get("request_id") != request_id or
            not re.fullmatch(re.escape(base) + r"/job-[a-f0-9]{12}/(?:work|data)", str(manifest.get("data", "")))):
        raise PathRejected("remote data belongs to another request or has an invalid folder")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) > 20000:
        raise TransferError("invalid remote manifest file count")
    names = set()
    for row in files:
        if not isinstance(row, dict):
            raise TransferError("invalid remote manifest file")
        name = str(confine(row.get("path")))
        if name.casefold() in names or type(row.get("bytes")) is not int or row["bytes"] < 0:
            raise TransferError("invalid remote manifest file")
        names.add(name.casefold())
    directories = manifest.get("directories", [])
    if not isinstance(directories, list) or len(directories) > 20000:
        raise TransferError("invalid remote directory count")
    for directory in directories:
        name = str(confine(directory))
        if name.casefold() in names:
            raise TransferError("duplicate remote directory")
        names.add(name.casefold())
    if manifest.get("bytes") != sum(row["bytes"] for row in files):
        raise TransferError("remote manifest total size differs")
    unchanged = manifest.get("unchanged_inputs", [])
    if (not isinstance(unchanged, list) or not all(isinstance(name, str) for name in unchanged) or
            not set(unchanged).issubset({row["path"] for row in files})):
        raise TransferError("invalid unchanged input listing")
    skipped = manifest.get("skipped", [])
    if not isinstance(skipped, list) or len(skipped) > 20000:
        raise TransferError("invalid remote omission count")
    for row in skipped:
        path = row.get("path") if isinstance(row, dict) else None
        if (not isinstance(path, str) or not path or len(path) > 4096 or path.startswith("/") or
                "\x00" in path or any(p in ("", ".", "..") for p in path.split("/")) or
                not isinstance(row.get("reason"), str) or len(row["reason"]) > 200):
            raise TransferError("invalid remote omission")
    return manifest


def _remote_data_command(transfer: Transfer, folder: str, action: str, config: str) -> None:
    transfer.plain_dir(folder)
    transfer._do("python3 " + " ".join(map(shlex.quote, [transfer.path(folder + '/remote-data.py'),
                 transfer.real_root(), folder, action, config])), "checking remote data")


def verify_remote_data(transfer: Transfer, manifest: dict, request_id: str) -> str:
    validate_remote_manifest(manifest, request_id)
    folder = str(PurePosixPath(manifest["data"]).parent)
    transfer.plain_dir(folder)
    # Check the runner's saved names/sizes without reading the large files.
    config = f"verify-{uuid.uuid4().hex[:12]}.json"
    payload = json.dumps(manifest).encode()
    metadata = Transfer(transfer.run, transfer.root, replace(transfer.limits, upload_max_bytes=4 * 1024 * 1024),
                        redact=transfer._redact)
    metadata.deadline = transfer.deadline
    metadata.upload_bytes(f"{folder}/{config}", payload)
    transfer.upload_bytes(f"{folder}/remote-data.py", REMOTE_DATA_HELPER.encode())
    _remote_data_command(transfer, folder, "verify", config)
    return transfer.path(manifest["data"])


def remote_script(text: str, remote_work: str) -> str:
    """The job script with its ``cd`` (which build_script wrote with a local folder) pointing at the cluster's work
    folder. A script without that line is refused: it would run wherever the shell happens to be."""
    lines = text.split("\n")
    for at, line in enumerate(lines):
        if line.startswith("cd "):
            lines[at] = f"cd {shlex.quote(remote_work)}"
            return "\n".join(lines)
    raise TransferError("the job script has no 'cd' line to point at the cluster's work folder")


def upload_job(transfer: Transfer, spec: dict, script_text: str, local_root: Path, inputs: list | None = None,
               budget_s: float = UPLOAD_BUDGET_S) -> list[dict]:
    """Put the script (and any small inputs, relative to ``local_root``) on the cluster. Raises before anything is
    submitted; what was written stays under the job's own folder."""
    inputs = list(inputs or [])
    if len(inputs) > MAX_INPUTS:
        raise TooLarge(f"{len(inputs)} inputs is more than {MAX_INPUTS}; use the site profile's file transfer method")
    sent, total = [], 0
    spec["inputs"] = {}  # work-relative name -> sha256 of what was uploaded: those are not results when the job ends
    transfer.deadline = time.monotonic() + budget_s  # a slow link stops the upload before the caller gives up waiting
    try:
        transfer.make_fresh_dir(spec["dir"])  # this job's own folder: nothing laid there beforehand, nobody else in it
        transfer.ensure_dir(f"{spec['dir']}/logs")
        transfer.ensure_dir(spec["work"])
        if spec.get("request_id"):
            for manifest in spec.get("remote_inputs", {}).values():
                verify_remote_data(transfer, manifest, spec["request_id"])
        for rel in inputs:
            name = str(confine(str(rel)))
            info = transfer.upload_file(Path(local_root) / name, f"{spec['work']}/{name}", root=Path(local_root))
            total += info["bytes"]
            if total > INPUTS_TOTAL_BYTES:
                raise TooLarge(f"the inputs total more than {INPUTS_TOTAL_BYTES} bytes; use the site profile's file "
                               "transfer method")
            spec["inputs"][name] = info["sha256"]
            sent.append(info)
        sent.append(transfer.upload_bytes(spec["script"], script_text.encode("utf-8"), executable=True))
        if spec.get("request_id"):
            transfer.upload_bytes(f"{spec['dir']}/remote-data.py", REMOTE_DATA_HELPER.encode())
            transfer.upload_bytes(f"{spec['dir']}/remote-config.json", json.dumps(
                {"request_id": spec["request_id"], "data": spec["data"], "inputs": spec["inputs"]}).encode())
    finally:
        transfer.deadline = None
    return sent


def safe_local_root(base: Path, root: Path) -> Path:
    """``root`` (a folder results are written into) made ready under ``base`` (the task's folder): every part of it from
    ``base`` down is a real folder, not a link, so nothing written there can land elsewhere. Missing parts are made one
    level at a time."""
    base, root = Path(base), Path(root)
    try:
        relative = root.relative_to(base)
    except ValueError as error:
        raise PathRejected(f"{root} is not inside {base}") from error
    if ".." in relative.parts or base.is_symlink():
        raise PathRejected(f"{root} is not a plain folder inside {base}")
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PathRejected(f"{current} is a link; results are not written through it")
        if not current.exists():
            current.mkdir()
        elif not current.is_dir():
            raise PathRejected(f"{current} is not a folder")
    return root


def fetch_results(transfer: Transfer, spec: dict, logs_dir: Path, fetch_to: Path, base: Path | None = None) -> dict:
    """Bring a finished job's logs and small results home. Logs come back as their tail when long; a result file over
    the limit is left on the cluster and named in the manifest. Each file's sha256 is recorded.

    ``base`` is the task's folder: both local folders must be real folders under it. A file that cannot be brought for a
    lasting reason (too large, a name not allowed) is skipped and named; a transfer that failed (the shell dropped, a
    checksum differed) fails the whole fetch, so the caller retries instead of calling it done."""
    if base is not None:
        logs_dir, fetch_to = safe_local_root(base, logs_dir), safe_local_root(base, fetch_to)
    manifest: dict = {"logs": [], "fetched": [], "skipped": []}
    for rel in spec["logs"]:
        if not transfer.exists(rel):
            manifest["skipped"].append({"path": rel, "reason": "no such log (the job may not have started)"})
            continue
        manifest["logs"].append(transfer.fetch_log(rel, logs_dir, tail=True, name=PurePosixPath(rel).name))
    if spec.get("request_id"):
        transfer.plain_dir(spec["dir"])
        transfer.upload_bytes(f"{spec['dir']}/remote-data.py", REMOTE_DATA_HELPER.encode())
        transfer.upload_bytes(f"{spec['dir']}/remote-config.json", json.dumps(
            {"request_id": spec["request_id"], "data": spec["data"], "inputs": spec.get("inputs", {})}).encode())
        _remote_data_command(transfer, spec["dir"], "scan", "remote-config.json")
        data, _ = transfer.fetch_bytes(f"{spec['dir']}/remote-data.json", max_bytes=4 * 1024 * 1024)
        remote = validate_remote_manifest(json.loads(data), spec["request_id"])
        if remote["data"] != spec["data"]:
            raise PathRejected("remote manifest folder differs from the submitted job")
        manifest["skipped"].extend(remote.get("skipped", []))
        # Reuse the existing small-upload checksum to distinguish an unchanged input from a produced file.
        # This never hashes a large output; large files retain the name/size-only contract.
        uploaded = spec.get("inputs") or {}
        remote["unchanged_inputs"] = [row["path"] for row in remote["files"]
            if row["path"] in uploaded and row["bytes"] <= transfer.limits.upload_max_bytes
            and transfer.remote_sha256(f"{remote['data']}/{row['path']}") == uploaded[row["path"]]]
        manifest["remote"] = remote
        total = 0
        for row in remote["files"]:
            name, size = row["path"], row["bytes"]
            if name in remote["unchanged_inputs"]:
                continue
            if size > transfer.limits.fetch_max_bytes or total + size > transfer.limits.fetch_total_bytes:
                manifest["skipped"].append({"path": name, "bytes": size,
                                             "reason": "retained in request remote data"})
                continue
            try:
                fetch = transfer.fetch_log if is_log(name) else transfer.fetch_file
                info = fetch(f"{remote['data']}/{name}", fetch_to, name=name)
            except (TooLarge, PathRejected) as error:
                # Local retrieval is optional; the remote file is rechecked before admission/use.
                manifest["skipped"].append({"path": name, "bytes": size, "reason": str(error)[:200]})
                continue
            if info.get("remote_bytes", info["bytes"]) != size:
                raise TransferError(f"{name}: size differs from remote listing")
            total += size
            manifest["fetched"].append({"path": name, "bytes": info["bytes"], "sha256": info["sha256"],
                                        **({"redacted": True, "remote_sha256": info["remote_sha256"]}
                                           if info.get("redacted") else {})})
        write_confined(Path(logs_dir), Path(logs_dir) / f"{spec['stem']}.fetch.json",
                       json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        return manifest
    total = 0
    for name in transfer.list_deeper(spec["work"]):  # too deep to list: not fetched, and said so
        manifest["skipped"].append({"path": name, "reason": f"deeper than {transfer.limits.list_depth} folders: use the "
                                                            "site profile's file transfer method"})
    uploaded = spec.get("inputs") or {}
    for name, size in transfer.list_files(spec["work"]):
        rel = f"{spec['work']}/{name}"
        if name in uploaded and size <= transfer.limits.upload_max_bytes and transfer.remote_sha256(rel) == uploaded[name]:
            continue  # an input the job did not change is not a result
        try:
            confine(name)
        except PathRejected:
            manifest["skipped"].append({"path": name, "reason": "name not allowed"})
            continue
        if size > transfer.limits.fetch_max_bytes or total + size > transfer.limits.fetch_total_bytes:
            manifest["skipped"].append({"path": name, "reason": f"{size} bytes: use the site profile's file transfer method"})
            continue
        try:
            info = (transfer.fetch_log if is_log(name) else transfer.fetch_file)(rel, fetch_to, name=name)
        except (TooLarge, PathRejected) as error:  # lasting reasons: skip and say so
            manifest["skipped"].append({"path": name, "reason": str(error)[:200]})
            continue
        total += info.get("remote_bytes", info["bytes"])
        manifest["fetched"].append({"path": name, "bytes": info["bytes"], "sha256": info["sha256"],
                                    **({"redacted": True, "remote_sha256": info["remote_sha256"]}
                                       if info.get("redacted") else {})})
    write_confined(Path(logs_dir), Path(logs_dir) / f"{spec['stem']}.fetch.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return manifest
