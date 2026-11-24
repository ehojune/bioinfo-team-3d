"""Bounded, hash-checked text artifacts for the internal compact report handoff."""

from hashlib import sha256
from pathlib import PurePosixPath

from ..evidence.literal_check import MAX_SOURCE_BYTES, _source_bytes
from ..evidence.report_consistency import MAX_TABLE_BYTES, TABLE_SUFFIXES
from ..models import TaskResult, waiting
from ..util import output_relpath

RESULT_FIELD = "report_artifacts"
MAX_TRANSFER_BYTES = 4_000_000  # UTF-8 contents and paths per task result
MAX_ARTIFACTS = 32


def _bounded(meta, result, read_bytes) -> dict[str, str]:
    draft = meta.get(RESULT_FIELD)
    if not draft or not result.ok or waiting(result):
        return {}
    payload, remaining = {}, MAX_TRANSFER_BYTES
    for path in sorted(result.outputs, key=lambda path: path != draft):
        digest = result.output_sha256.get(path)
        if not digest or output_relpath(path) != path or (path != draft
                and PurePosixPath(path).suffix.casefold() not in TABLE_SUFFIXES):
            continue
        limit = MAX_SOURCE_BYTES if path == draft else MAX_TABLE_BYTES
        try:
            data = read_bytes(path, limit)
            content = verified_text(data, digest, limit)
            size = len(path.encode("utf-8")) + len(data)
        except (OSError, ValueError):
            continue
        if size <= remaining:
            payload[path] = content
            remaining -= size
            if len(payload) == MAX_ARTIFACTS:
                break
    return payload


def collect(root, meta: dict, result: TaskResult) -> dict[str, str]:
    return _bounded(meta, result, lambda path, limit: _source_bytes(root, path, limit))


def _wire_bytes(text, limit: int) -> bytes:
    if not isinstance(text, str) or len(text) > limit:
        raise ValueError("no bounded transferred copy of the collected output")
    return text.encode("utf-8")


def verified_text(data: bytes, digest: str, limit: int) -> str:
    if len(data) > limit or sha256(data).hexdigest() != digest:
        raise ValueError("the transferred output differs from the collected SHA-256")
    return data.decode("utf-8")


def transferred_text(payload: dict | None, path: str, digest: str, limit: int) -> str:
    return verified_text(_wire_bytes((payload or {}).get(path), limit), digest, limit).removeprefix("\ufeff")


def receive(payload: object, meta: dict, result: TaskResult) -> dict[str, str]:
    """Validate against the dispatched task and final result before private storage."""
    if not isinstance(payload, dict) or len(payload) > MAX_ARTIFACTS:
        return {}
    return _bounded(meta, result, lambda path, limit: _wire_bytes(payload.get(path), limit))
