"""Bounded copies of approved runner-local request inputs for whole-request HPC."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from ..adapters.owned import plain_directory
from ..hpc_transfer import confine
from ..research.input_proof import capture, _logical_path
from .step_inputs import MAX_FILES, _copy, _folder, _parts


def preflight(references: list[str], projects: list[str], settings, transfer_max_bytes=None) -> dict | None:
    """Prove the inputs and refuse a file inventory that the snapshot stage cannot copy."""
    proof = capture({"reference_dirs": references, "project_dirs": projects}, settings)
    if proof is None:
        return None
    count, total, entries = 0, 0, 0
    children, seen = {}, set()
    try:
        for index, raw in enumerate([*references, *projects], 1):
            label = f"ref-{index}"
            children[""] = children.get("", 0) + 1
            entries += 1
            if children[""] > MAX_FILES + 1 or entries > MAX_FILES * 16:
                return None
            original = Path(os.path.expanduser(str(raw))).absolute()
            is_file = original.is_file()
            source = original.parent if is_file else original
            root = _logical_path(source)
            found_file = False
            for row_root, name, digest in proof["files"]:
                if row_root != root or name == ".":
                    continue
                name = name.removeprefix("./")
                if is_file and name != original.name:
                    continue
                _parts(name)
                target = str(confine(f"{label}/{name}"))
                parent = target.rpartition("/")[0]
                children[parent] = children.get(parent, 0) + 1
                entries += 1
                if (children[parent] > MAX_FILES + 1 or entries > MAX_FILES * 16
                        or target.casefold() in seen or len(target.split("/")) > (16 if digest == "directory" else 17)):
                    return None
                seen.add(target.casefold())
                if digest != "directory":
                    count += 1
                    found_file = True
                    size = (source / name).stat().st_size
                    total += size
                    if (count > MAX_FILES or size > settings.runner.input_hash_max_file_bytes
                            or total > settings.runner.input_hash_max_total_bytes
                            or (transfer_max_bytes is not None and size > transfer_max_bytes)):
                        return None
            if is_file and not found_file:
                return None
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None
    return proof


def stage(workdir: Path, references: list[str], projects: list[str], settings, expected_sha256=None) -> dict:
    """Create fresh snapshots only after the existing input policy proves each source.

    A file reference copies only that file, although the existing proof also inspects its parent.
    The returned manifest is runner-owned state, not an employee-supplied inventory.
    """
    descriptor = {"reference_dirs": references, "project_dirs": projects}
    if expected_sha256 is not None:
        current = capture(descriptor, settings)
        if not current or current.get("sha256") != expected_sha256:
            raise ValueError("request input differs from the approved preflight")
    workdir = Path(workdir)
    relative = f"inputs/request-{uuid.uuid4().hex}"
    if plain_directory(workdir, relative) is None:
        raise ValueError("request input destination is not a plain folder")
    remaining = [settings.runner.input_hash_max_total_bytes]
    rows, dirs, roots = [], [], []
    seen = set()
    for index, (raw, kind) in enumerate([(r, "reference_dirs") for r in references] +
                                       [(p, "project_dirs") for p in projects], 1):
        original = Path(os.path.expanduser(str(raw))).absolute()
        proof = capture({kind: [str(original)]}, settings)
        if proof is None:
            raise ValueError("request input is not an admissible complete snapshot")
        is_file = original.is_file()
        source = original.parent if is_file else original
        label = f"ref-{index}"
        dirs.append(label)
        roots.append({"original": str(original), "path": label + ("/" + original.name if is_file else "")})
        prefix = _logical_path(source)
        for root, name, digest in proof["files"]:
            if root != prefix:
                raise ValueError("request input proof root differs")
            if name == ".":
                continue
            name = name.removeprefix("./")
            if is_file and name != original.name:
                continue
            _parts(name)
            target = str(confine(label + "/" + name))
            if target.casefold() in seen:
                raise ValueError("request input names collide")
            seen.add(target.casefold())
            destination = f"{relative}/{target}"
            if digest == "directory":
                if plain_directory(workdir, destination) is None:
                    raise ValueError("request input folder is not plain")
                dirs.append(target)
            else:
                if len(rows) >= MAX_FILES:
                    raise ValueError("too many request input files")
                _copy(source, tuple(name.split("/")), workdir, destination, digest,
                      settings.runner.input_hash_max_file_bytes, remaining)
                size = (workdir / destination).stat().st_size
                rows.append({"path": target, "sha256": digest, "bytes": size})
        if is_file and not any(row["path"] == label + "/" + original.name for row in rows):
            raise ValueError("request input file is missing")
        if capture({kind: [str(original)]}, settings) != proof:
            raise ValueError("request input changed while snapshotting")
    result = {"root": relative, "files": rows, "directories": dirs, "roots": roots}
    verify(workdir, result)
    if expected_sha256 is not None:
        current = capture(descriptor, settings)
        if not current or current.get("sha256") != expected_sha256:
            raise ValueError("request input changed while staging")
    return result


def verify(workdir: Path, manifest: dict) -> None:
    """Refuse missing, added, linked or altered local snapshot files before submission."""
    root = Path(workdir) / manifest["root"]
    directories = set()
    budget = [MAX_FILES * 16]

    def inventory(held, prefix="", depth=0):
        if depth > 16:
            raise ValueError("input nesting exceeds limit")
        found = []
        entries = held.entries(MAX_FILES + 2)
        if len(entries) > MAX_FILES + 1:
            raise ValueError("too many input entries")
        for entry in entries:
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError("too many input entries")
            name = prefix + entry.name
            if entry.kind == "dir":
                directories.add(name)
                with held.child(entry.name, expect=entry.ident) as child:
                    if held.dev is not None and child.dev != held.dev:
                        raise OSError("input crosses a mount")
                    found.extend(inventory(child, name + "/", depth + 1))
            elif entry.kind == "file":
                found.append(name)
            else:
                raise ValueError("input contains a link or special file")
        return found

    with _folder(root, ()) as held:
        names = set(inventory(held))
    expected = {row["path"] for row in manifest["files"]}
    if names != expected or directories != set(manifest["directories"]):
        raise ValueError("request input inventory changed")
    for row in manifest["files"]:
        parts = tuple(_parts(row["path"]))
        with _folder(root, parts[:-1]) as held, os.fdopen(held.open_read_file(parts[-1]), "rb") as stream:
            info = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if (info.st_nlink != 1 or info.st_size != row["bytes"] or digest.hexdigest() != row["sha256"]
                    or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                raise ValueError("request input changed")
    for name in manifest["directories"]:
        with _folder(root, tuple(_parts(name))):
            pass
