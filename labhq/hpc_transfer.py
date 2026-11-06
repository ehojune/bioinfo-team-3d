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
import hashlib
import os
import re
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

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


class Transfer:
    def __init__(self, run: Callable[[str], object], root: str, limits: Limits = Limits()):
        if not isinstance(root, str) or not root.startswith("/") or ".." in root.split("/") or "\n" in root:
            raise PathRejected("the remote work folder is an absolute POSIX path without '..'")
        self.run, self.root, self.limits = run, root.rstrip("/") or "/", limits
        self._real_root: str | None = None

    # ---- the far side ------------------------------------------------------------------------------------------
    def _do(self, command: str, what: str) -> str:
        result = self.run(command)
        if getattr(result, "exit_code", 1) != 0:
            raise TransferError(f"{what} failed on the cluster: {str(getattr(result, 'output', ''))[:200].strip()}")
        return str(result.output)

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
        real_root = self.real_root()
        target = self.root if rel_dir in ("", ".") else self.path(rel_dir)
        out = self._do(f"mkdir -p -- {shlex.quote(target)} && cd -- {shlex.quote(target)} && pwd -P",
                       "making a folder").strip().splitlines()
        real = out[-1] if out else ""
        if real != real_root and not real.startswith(real_root.rstrip("/") + "/"):
            raise PathRejected(f"{rel_dir!r} leads outside the work folder")
        return real

    def _is_link(self, remote: str) -> bool:
        return self._do(f"test -L {shlex.quote(remote)} && echo link || echo plain", "checking a file").strip() == "link"

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
        """Upload a regular local file. With ``root``, the file must lie inside it (no link, no ``..``)."""
        local = Path(local)
        if root is not None and not _inside(local, Path(root)):
            raise PathRejected(f"{local} is outside {root}")
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

    def fetch_file(self, rel: str, into: Path, *, tail: bool = False, max_bytes: int | None = None,
                   name: str | None = None) -> dict:
        """Fetch ``rel`` to ``into / name`` (default: the same relative path). The local path stays under ``into``."""
        data, info = self.fetch_bytes(rel, tail=tail, max_bytes=max_bytes)
        target = Path(into) / (name or str(confine(rel)))
        write_confined(Path(into), target, data)
        return {"path": str(confine(rel)), "local": str(target), **info}


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


def job_spec(task_id: str, stem: str) -> dict:
    """Where a job's files live on the cluster, relative to the work folder: its script, its logs and the folder it
    runs in. The task id and the script name are checked like any path."""
    base = str(confine(str(task_id or "task")))
    if "/" in base:
        raise PathRejected(f"task id {task_id!r} is not a plain name")
    return {"base": base, "script": f"{base}/jobs/{confine(stem + '.sh')}", "work": f"{base}/work",
            "logs": [f"{base}/jobs/logs/{confine(stem + '.out')}", f"{base}/jobs/logs/{confine(stem + '.err')}"],
            "stem": stem}


def remote_script(text: str, remote_work: str) -> str:
    """The job script with its ``cd`` (which build_script wrote with a local folder) pointing at the cluster's work
    folder. A script without that line is refused: it would run wherever the shell happens to be."""
    lines = text.split("\n")
    for at, line in enumerate(lines):
        if line.startswith("cd "):
            lines[at] = f"cd {shlex.quote(remote_work)}"
            return "\n".join(lines)
    raise TransferError("the job script has no 'cd' line to point at the cluster's work folder")


def upload_job(transfer: Transfer, spec: dict, script_text: str, local_root: Path, inputs: list | None = None) -> list[dict]:
    """Put the script (and any small inputs, relative to ``local_root``) on the cluster. Raises before anything is
    submitted; what was written stays under the job's own folder."""
    inputs = list(inputs or [])
    if len(inputs) > MAX_INPUTS:
        raise TooLarge(f"{len(inputs)} inputs is more than {MAX_INPUTS}; use the site profile's file transfer method")
    sent, total = [], 0
    transfer.ensure_dir(f"{spec['base']}/jobs/logs")
    transfer.ensure_dir(spec["work"])
    for rel in inputs:
        local = Path(local_root) / confine(str(rel))
        total += local.stat().st_size if local.is_file() else 0
        if total > INPUTS_TOTAL_BYTES:
            raise TooLarge(f"the inputs total more than {INPUTS_TOTAL_BYTES} bytes; use the site profile's file transfer method")
        sent.append(transfer.upload_file(local, f"{spec['work']}/{confine(str(rel))}", root=Path(local_root)))
    sent.append(transfer.upload_bytes(spec["script"], script_text.encode("utf-8"), executable=True))
    return sent


def fetch_results(transfer: Transfer, spec: dict, logs_dir: Path, fetch_to: Path) -> dict:
    """Bring a finished job's logs and small results home. Logs come back as their tail when long; a result file over
    the limit is left on the cluster and named in the manifest. Each file's sha256 is recorded."""
    manifest: dict = {"logs": [], "fetched": [], "skipped": []}
    for rel in spec["logs"]:
        try:
            manifest["logs"].append(transfer.fetch_file(rel, logs_dir, tail=True, name=PurePosixPath(rel).name))
        except TransferError as error:
            manifest["skipped"].append({"path": rel, "reason": str(error)[:200]})  # a job that never started has no log
    total = 0
    for name, size in transfer.list_files(spec["work"]):
        rel = f"{spec['work']}/{name}"
        try:
            confine(name)
        except PathRejected:
            manifest["skipped"].append({"path": name, "reason": "name not allowed"})
            continue
        if size > transfer.limits.fetch_max_bytes or total + size > transfer.limits.fetch_total_bytes:
            manifest["skipped"].append({"path": name, "reason": f"{size} bytes: use the site profile's file transfer method"})
            continue
        try:
            info = transfer.fetch_file(rel, fetch_to, name=name)
        except TransferError as error:
            manifest["skipped"].append({"path": name, "reason": str(error)[:200]})
            continue
        total += info["bytes"]
        manifest["fetched"].append({"path": name, "bytes": info["bytes"], "sha256": info["sha256"]})
    import json

    write_confined(Path(logs_dir), Path(logs_dir) / f"{spec['stem']}.fetch.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return manifest
