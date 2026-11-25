"""Bounded, hash-checked text artifacts for the internal compact report handoff."""

import json
from hashlib import sha256
from pathlib import PurePosixPath

from ..evidence.literal_check import MAX_SOURCE_BYTES, _source_bytes
from ..evidence.report_consistency import TABLE_SUFFIXES
from ..models import TaskResult, waiting
from ..util import output_relpath

RESULT_FIELD = "report_artifacts"
LARGE_TRANSFER_FIELD = "report_artifacts_large"
MAX_TABLE_BYTES = 60_000_000
MAX_TRANSFER_BYTES = 64_000_000  # UTF-8 contents and paths per task result
MAX_WIRE_BYTES = 80 * 2**20  # JSON artifact object, including escaping
GATEWAY_WS_MAX_BYTES = MAX_WIRE_BYTES + 16 * 2**20  # leave the original frame budget for the rest of the result
MAX_ARTIFACTS = 32


def _json_string_size(text: str) -> int:
    # The runner sends JSON with ensure_ascii=False. Count escaping in chunks to avoid building another full frame.
    chunk_size = 64 * 1024
    return 2 + sum(len(json.dumps(text[i:i + chunk_size], ensure_ascii=False).encode("utf-8")) - 2
                   for i in range(0, len(text), chunk_size))


def _bounded(meta, result, read_bytes) -> dict[str, str]:
    draft = meta.get(RESULT_FIELD)
    if not draft or not result.ok or waiting(result):
        return {}
    if meta.get(LARGE_TRANSFER_FIELD) is True:
        table_limit, remaining, wire_limit = MAX_TABLE_BYTES, MAX_TRANSFER_BYTES, MAX_WIRE_BYTES
    else:  # older gateways dispatch the same task without the large-frame opt-in
        table_limit, remaining, wire_limit = 3_000_000, 4_000_000, 8 * 2**20
    payload, wire_remaining = {}, wire_limit - 2
    for path in sorted(result.outputs, key=lambda path: path != draft):
        digest = result.output_sha256.get(path)
        if not digest or output_relpath(path) != path or (path != draft
                and PurePosixPath(path).suffix.casefold() not in TABLE_SUFFIXES):
            continue
        try:
            path_size = len(path.encode("utf-8"))
            limit = min(MAX_SOURCE_BYTES if path == draft else table_limit, remaining - path_size)
            if limit < 0:
                continue
            data = read_bytes(path, limit)
            content = verified_text(data, digest, limit)
            wire_size = _json_string_size(path) + _json_string_size(content) + 4  # key and item separators
        except (OSError, ValueError):
            continue
        if wire_size <= wire_remaining:
            payload[path] = content
            remaining -= path_size + len(data)
            wire_remaining -= wire_size
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
