"""Collect annotation snapshots even when staff omit them from their output list."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..private_paths import credential_path
from .step_inputs import _folder, staging_problem


@dataclass
class AnnotationOutputs:
    outputs: list[str]
    hashes: dict[str, str]
    excluded: dict[str, str]


def _annotation(path: str) -> bool:
    return (path == "outputs/annotation_queries.jsonl" or path == "outputs/annotation"
            or path.startswith("outputs/annotation/"))


def collect_annotation_outputs(workdir: Path, records: list[dict], outputs: list[str],
                               hashes: dict[str, str], *, max_file: int, max_total: int,
                               scan_notes: list[str] = ()) -> AnnotationOutputs:
    """Merge the runner's bounded, no-follow output scan into collected artifacts.

    Only scan-proven hashes are adopted; source handles recheck links and content.
    Existing non-annotation outputs keep their order and budget priority. The query log
    comes next, then response paths in lexical order. Every excluded candidate has a
    reason; a truncated scan also records that unobserved paths may be missing.
    ``stage_inputs`` still verifies content hashes and the cumulative consumer budget.
    """
    by_path = {row["path"]: row for row in records}
    candidates = {path for path in by_path if _annotation(path)}
    candidates.update(path for path in outputs if _annotation(path))
    kept = list(dict.fromkeys(path for path in outputs if not _annotation(path)))
    kept_hashes = {path: hashes[path] for path in kept if path in hashes}
    sizes = {path: row.get("size") for path, row in by_path.items()}
    excluded = {}
    baseline = staging_problem(kept, kept_hashes, sizes, max_file, max_total)

    for path in sorted(candidates, key=lambda p: (p != "outputs/annotation_queries.jsonl", p)):
        if path == "outputs/annotation":
            excluded[path] = "annotation root is a link or not a response directory"
            continue
        row = by_path.get(path)
        if row is None:
            excluded[path] = "not present in the bounded output scan"
            continue
        digest = row.get("sha256")
        if not isinstance(digest, str):
            excluded[path] = str(row.get("skipped") or row.get("reason") or "no verified hash")
            continue
        proposed = [*kept, path]
        proposed_hashes = {**kept_hashes, path: digest}
        problem = baseline or staging_problem(proposed, proposed_hashes, sizes, max_file, max_total)
        if problem:
            excluded[path] = problem
            continue
        if credential_path(path):
            excluded[path] = "protected artifact path"
            continue
        try:
            parts = path.split("/")  # staging_problem already checked portable, traversal-free names
            with _folder(workdir, parts[:-1]) as parent:
                with os.fdopen(parent.open_read_file(parts[-1]), "rb") as source:
                    info = os.fstat(source.fileno())
                    if info.st_nlink != 1:
                        raise ValueError("hard-linked file")
                    if info.st_size != row.get("size") or info.st_mtime_ns != row.get("mtime_ns"):
                        raise ValueError("file changed since the output scan")
                    actual, read = hashlib.sha256(), 0
                    while chunk := source.read(min(1024 * 1024, max_file + 1 - read)):
                        read += len(chunk)
                        actual.update(chunk)
                        if read > max_file:
                            raise ValueError("file grew beyond the per-file input limit")
                    after = os.fstat(source.fileno())
                    if (read != info.st_size or actual.hexdigest() != digest or after.st_nlink != 1
                            or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns)):
                        raise ValueError("file content changed since the output scan")
        except (OSError, ValueError) as exc:
            excluded[path] = str(exc) if isinstance(exc, ValueError) else "link or unreadable artifact"
            continue
        kept, kept_hashes = proposed, proposed_hashes

    if scan_notes:
        excluded["outputs/annotation/* (scan)"] = (
            "output scan incomplete; additional annotation paths may be missing: "
            + "; ".join(dict.fromkeys(scan_notes)))
    return AnnotationOutputs(kept, kept_hashes, excluded)
