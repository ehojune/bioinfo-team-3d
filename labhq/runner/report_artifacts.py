"""Bounded, hash-checked text artifacts for the internal compact report handoff."""

from base64 import b64decode, b64encode
from hashlib import sha256
from pathlib import PurePosixPath

from ..evidence.literal_check import MAX_SOURCE_BYTES, _source_bytes
from ..evidence.report_consistency import MAX_TABLE_BYTES, TABLE_SUFFIXES
from ..models import TaskResult, waiting
from ..util import output_relpath


RESULT_FIELD = "report_artifacts"
MAX_TRANSFER_BYTES = 4_000_000  # encoded contents and paths per task result
MAX_ARTIFACTS = 32


def _eligible_paths(meta: dict, result: TaskResult):
    draft = meta.get(RESULT_FIELD)
    if not draft or not result.ok or waiting(result):
        return
    for path in sorted(result.outputs, key=lambda path: path != draft):
        if (path in result.output_sha256 and path.startswith("outputs/") and output_relpath(path) == path
                and (path == draft or PurePosixPath(path).suffix.lower() in TABLE_SUFFIXES)):
            yield path, MAX_SOURCE_BYTES if path == draft else MAX_TABLE_BYTES


def _bounded(meta, result, read_bytes) -> dict[str, str]:
    payload = {}
    remaining = MAX_TRANSFER_BYTES
    for path, limit in _eligible_paths(meta, result):
        try:
            data = read_bytes(path, limit)
            _verified(data, result.output_sha256[path], limit)
        except (OSError, ValueError):
            continue
        encoded = b64encode(data).decode("ascii")
        size = len(path.encode("utf-8")) + len(encoded)
        if size <= remaining:
            payload[path] = encoded
            remaining -= size
            if len(payload) == MAX_ARTIFACTS:
                break
    return payload


def collect(root, meta: dict, result: TaskResult) -> dict[str, str]:
    return _bounded(meta, result, lambda path, limit: _source_bytes(root, path, limit))


def _decode(encoded, limit: int) -> bytes:
    if not isinstance(encoded, str):
        raise ValueError("no transferred copy of the collected output")
    if len(encoded) > 4 * ((limit + 2) // 3):
        raise ValueError("the transferred output is too large")
    return b64decode(encoded, validate=True)


def _verified(data: bytes, digest: str, limit: int) -> str:
    if len(data) > limit or sha256(data).hexdigest() != digest:
        raise ValueError("the transferred output differs from the collected SHA-256")
    return data.decode("utf-8-sig")


def verified_text(payload: dict, path: str, digest: str, limit: int) -> str:
    return _verified(_decode(payload.get(path), limit), digest, limit)


def receive(payload: object, meta: dict, result: TaskResult) -> dict[str, str]:
    """Validate against the dispatched task and final result before private storage."""
    if not isinstance(payload, dict) or len(payload) > MAX_ARTIFACTS:
        return {}

    return _bounded(meta, result, lambda path, limit: _decode(payload.get(path), limit))
