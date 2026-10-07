"""RO-Crate 1.1 / Process Run Crate 0.5 export and request-bundle checks."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit


METADATA_FILE = "ro-crate-metadata.json"
RO_CRATE_PROFILE = "https://w3id.org/ro/crate/1.1"
PROCESS_RUN_PROFILE = "https://w3id.org/ro/wfrun/process/0.5"
FORMAT_MARKER = "<!-- labhq-ro-crate: required -->"
MAX_METADATA_BYTES = 16 * 1024 * 1024
_DRIVE_PATH = re.compile(r"(?i)(?:^|[^A-Za-z0-9_])[A-Z]:[\\/]")
_POSIX_PATH = re.compile(r"(?:^|[\s=(\[{:>\"'`])/(?!/)")
_PROTECTED = frozenset({"private", "restricted"})
_SCHEMA_TERMS = (
    "about", "actionStatus", "applicationCategory", "contentSize", "conformsTo", "datePublished",
    "description", "encodingFormat", "endTime", "hasPart", "identifier", "instrument", "license",
    "measurementTechnique", "mentions", "name", "propertyID", "result", "softwareVersion", "startTime",
    "unitText", "value", "valueReference", "variableMeasured", "version",
)
INLINE_CONTEXT = {
    "@vocab": "http://schema.org/",
    "schema": "http://schema.org/",
    "File": "schema:MediaObject",
    "sha256": "https://w3id.org/ro/terms/workflow-run#sha256",
    **{term: f"schema:{term}" for term in _SCHEMA_TERMS},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _uri(path: str) -> str:
    return "/".join(quote(part, safe="-._~") for part in PurePosixPath(path).parts)


def _ref(identifier: str) -> dict[str, str]:
    return {"@id": identifier}


def _iso_time(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return value
    return None


def _unsafe_string(value: str) -> bool:
    lower = value.casefold()
    decoded = unquote(value)
    has_parent = any(part == ".." for candidate in (value, decoded)
                     for part in re.split(r"[\\/]", candidate))
    return ("file:" in lower or has_parent or value.startswith(("\\\\", "//"))
            or bool(_DRIVE_PATH.search(value)) or bool(_POSIX_PATH.search(value)))


def _assert_private_strings_absent(value: Any) -> None:
    if isinstance(value, str):
        if _unsafe_string(value):
            raise ValueError("RO-Crate 문자열에 안전하지 않은 경로가 남았습니다")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_private_strings_absent(str(key))
            _assert_private_strings_absent(item)
    elif isinstance(value, list):
        for item in value:
            _assert_private_strings_absent(item)


def _file_entity(row: Mapping[str, Any]) -> dict[str, Any]:
    relative = str(row["relative_path"])
    entity: dict[str, Any] = {
        "@id": _uri(relative),
        "@type": "File",
        "name": PurePosixPath(relative).name,
        "contentSize": int(row["size"]),
        "sha256": str(row["sha256"]),
    }
    suffix = PurePosixPath(relative).suffix.casefold()
    media = {
        ".csv": "text/csv", ".html": "text/html", ".json": "application/json",
        ".md": "text/markdown", ".py": "text/x-python", ".sh": "application/x-sh",
        ".tsv": "text/tab-separated-values", ".txt": "text/plain", ".xml": "application/xml",
        ".yaml": "application/yaml", ".yml": "application/yaml",
    }.get(suffix)
    if media:
        entity["encodingFormat"] = media
    return entity


def _public_input(row: Mapping[str, Any]) -> bool:
    return str(row.get("skipped") or "").casefold() not in _PROTECTED


def _input_entities(step_id: str, rows: list[Mapping[str, Any]], action_id: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    scan_id = f"#input-scan-{quote(step_id, safe='-._~')}"
    values: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    protected = sum(not _public_input(row) for row in rows)
    for index, row in enumerate(rows, 1):
        if not _public_input(row):
            continue
        value_id = f"#input-scan-{quote(step_id, safe='-._~')}-{index}"
        path = str(row.get("path") or "").replace("\\", "/")
        name = PurePosixPath(path).name if path else "unnamed input"
        entity: dict[str, Any] = {
            "@id": value_id,
            "@type": "PropertyValue",
            "name": name,
            "description": "Observed in the accessible input area before execution; use is not established.",
        }
        if row.get("sha256"):
            entity.update(propertyID="sha256", value=str(row["sha256"]))
        if row.get("size") not in (None, ""):
            size_id = value_id + "-size"
            entity["valueReference"] = _ref(size_id)
            entities.append({"@id": size_id, "@type": "PropertyValue", "name": "content size",
                             "value": int(row["size"]), "unitText": "byte"})
        if row.get("skipped"):
            entity["measurementTechnique"] = f"scan omitted content: {row['skipped']}"
        values.append(_ref(value_id))
        entities.append(entity)
    description = "Pre-execution scan of the input area; it does not assert that any listed file was read."
    if protected:
        description += f" {protected} protected item(s) were omitted without names or hashes."
    scan: dict[str, Any] = {
        "@id": scan_id,
        "@type": "Observation",
        "name": f"Pre-execution input-area scan for {step_id}",
        "description": description,
        "about": _ref(action_id),
        "measurementTechnique": "bounded pre-execution scan of the accessible input area",
    }
    if values:
        scan["variableMeasured"] = values
    return [scan, *entities]


def build_ro_crate(req: Mapping[str, Any], rows: list[Mapping[str, Any]],
                   input_rows: list[Mapping[str, Any]],
                   run_records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build deterministic JSON-LD from the bundle's final, rewritten inventory."""
    rid = str(req.get("id") or "")
    payload_rows = [row for row in rows
                    if row.get("relative_path") not in ("", ".", METADATA_FILE, "MANIFEST.tsv")
                    and row.get("status") in ("copied", "copied (unreported)", "generated")
                    and row.get("size") not in (None, "") and row.get("sha256")]
    file_entities = [_file_entity(row) for row in sorted(payload_rows, key=lambda item: str(item["relative_path"]))]
    manifest_entity = {"@id": "MANIFEST.tsv", "@type": "File", "name": "MANIFEST.tsv",
                       "encodingFormat": "text/tab-separated-values"}
    graph: list[dict[str, Any]] = [
        {
            "@id": METADATA_FILE,
            "@type": "CreativeWork",
            "about": _ref("./"),
            "conformsTo": [_ref(RO_CRATE_PROFILE), _ref(PROCESS_RUN_PROFILE)],
        },
        {
            "@id": PROCESS_RUN_PROFILE,
            "@type": "CreativeWork",
            "name": "Process Run Crate",
            "version": "0.5",
        },
        {
            "@id": RO_CRATE_PROFILE,
            "@type": "CreativeWork",
            "name": "RO-Crate Metadata Specification",
            "version": "1.1",
        },
    ]
    root: dict[str, Any] = {
        "@id": "./",
        "@type": "Dataset",
        "identifier": rid,
        "name": f"labhq request bundle {rid}",
        "description": "Portable copy of one labhq request and its recorded process outputs.",
        "license": "Not supplied by the request record.",
        "conformsTo": _ref(PROCESS_RUN_PROFILE),
        "hasPart": [_ref(entity["@id"]) for entity in [*file_entities, manifest_entity]],
    }
    published = _iso_time(req.get("finished_at") or req.get("updated_at"))
    if published:
        root["datePublished"] = published

    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    mentions: list[dict[str, str]] = []
    software_ids: set[str] = set()
    by_step_inputs: dict[str, list[Mapping[str, Any]]] = {}
    for row in input_rows:
        by_step_inputs.setdefault(str(row.get("step_id") or ""), []).append(row)
    for step_id in sorted(str(item) for item in results):
        result = results.get(step_id)
        if not isinstance(result, Mapping) or str(result.get("status") or "").casefold() == "skipped":
            continue
        run = run_records.get(step_id) if isinstance(run_records.get(step_id), Mapping) else {}
        identity = "\0".join(str(run.get(key) or result.get(key) or "")
                             for key in ("agent_id", "engine", "model", "engine_cli_version"))
        software_id = "#software-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        software: dict[str, Any] = {
            "@id": software_id,
            "@type": "SoftwareApplication",
            "name": "labhq staff CLI" + (f" ({run.get('engine')})" if run.get("engine") else ""),
            "applicationCategory": "AI staff execution engine",
        }
        if run.get("engine_cli_version"):
            software["softwareVersion"] = str(run["engine_cli_version"])
        action_id = "#run-" + hashlib.sha256(step_id.encode("utf-8")).hexdigest()[:16]
        action: dict[str, Any] = {
            "@id": action_id,
            "@type": "CreateAction",
            "name": f"Run step {step_id}",
            "instrument": _ref(software_id),
        }
        if result.get("ok") is True:
            action["actionStatus"] = _ref("https://schema.org/CompletedActionStatus")
        elif result.get("ok") is False:
            action["actionStatus"] = _ref("https://schema.org/FailedActionStatus")
        for source, target in (("started_at", "startTime"), ("ended_at", "endTime")):
            if value := _iso_time(run.get(source)):
                action[target] = value
        outputs = [_ref(_uri(str(row["relative_path"]))) for row in payload_rows
                   if row.get("step_id") == step_id and row.get("status") == "copied"]
        if outputs:
            action["result"] = outputs
        if software_id not in software_ids:
            graph.append(software)
            software_ids.add(software_id)
        graph.append(action)
        mentions.append(_ref(action_id))
        scan_entities = _input_entities(step_id, by_step_inputs.get(step_id, []), action_id)
        if scan_entities:
            graph.extend(scan_entities)
            mentions.append(_ref(scan_entities[0]["@id"]))
    if mentions:
        root["mentions"] = mentions
    graph.insert(1, root)
    graph.extend([*file_entities, manifest_entity])
    # Inline the pinned vocab mappings so CI validation can run with --offline and a cold HTTP cache.
    crate = {"@context": INLINE_CONTEXT, "@graph": graph}
    _assert_private_strings_absent(crate)
    return crate


def write_ro_crate(path: Path, req: Mapping[str, Any], rows: list[Mapping[str, Any]],
                   input_rows: list[Mapping[str, Any]],
                   run_records: Mapping[str, Mapping[str, Any]]) -> None:
    crate = build_ro_crate(req, rows, input_rows, run_records)
    path.write_text(json.dumps(crate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def _local_id(identifier: Any) -> str | None:
    if not isinstance(identifier, str) or identifier in ("./", METADATA_FILE):
        return None
    if identifier.startswith("#"):
        return None
    try:
        parsed = urlsplit(identifier)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError
        parts = _bundle_relative_parts(unquote(parsed.path))
    except ValueError:
        raise ValueError(f"unsafe local entity id: {identifier}")
    return "/".join(parts)


def _bundle_relative_parts(relative: str) -> tuple[str, ...]:
    """Validate an untrusted bundle path without touching the filesystem."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative or "\0" in relative:
        raise ValueError("unsafe bundle path")
    parts = tuple(relative.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe bundle path")
    return parts


def _plain_file(root: Path, relative: str) -> Path:
    from .adapters.owned import is_link

    try:
        parts = _bundle_relative_parts(relative)
    except ValueError:
        raise OSError(f"unsafe bundle path: {relative}") from None
    path = root
    for part in parts:
        path = path / part
        if is_link(path):
            raise OSError(f"link or junction: {relative}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise OSError(f"missing or outside bundle: {relative}")
    return path


def _read_manifest(root: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    problems: list[str] = []
    try:
        path = _plain_file(root, "MANIFEST.tsv")
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise OSError("too large")
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except (OSError, UnicodeError, csv.Error) as exc:
        return {}, [f"묶음 MANIFEST.tsv를 읽지 못했습니다: {exc}"]
    found: dict[str, dict[str, str]] = {}
    for row in rows:
        relative = row.get("relative_path")
        if not relative:
            problems.append("묶음 MANIFEST.tsv에 relative_path가 없는 행이 있습니다")
        elif relative == "." and row.get("status") == "summary":
            found[relative] = row
        else:
            try:
                _bundle_relative_parts(relative)
            except ValueError:
                problems.append(f"묶음 MANIFEST.tsv 경로가 안전하지 않습니다: {relative}")
                continue
            if relative in found:
                problems.append(f"묶음 MANIFEST.tsv 경로가 중복됩니다: {relative}")
            else:
                found[relative] = row
    return found, problems


def _bundle_inventory(root: Path) -> tuple[set[str], list[str]]:
    """List plain files without following symlinks or Windows junctions."""
    from .adapters.owned import is_link

    files: set[str] = set()
    problems: list[str] = []
    pending: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    while pending:
        directory, parent_parts = pending.pop()
        try:
            with os.scandir(directory) as scanned:
                entries = sorted(scanned, key=lambda item: item.name)
        except OSError as exc:
            relative = "/".join(parent_parts) or "."
            problems.append(f"묶음 폴더를 열거하지 못했습니다: {relative} ({exc})")
            continue
        for entry in entries:
            parts = (*parent_parts, entry.name)
            relative = "/".join(parts)
            try:
                _bundle_relative_parts(relative)
            except ValueError:
                problems.append(f"묶음 트리 경로가 안전하지 않습니다: {relative}")
                continue
            path = Path(entry.path)
            try:
                if is_link(path):
                    problems.append(f"묶음 트리에 link 또는 junction이 있습니다: {relative}")
                elif entry.is_dir(follow_symlinks=False):
                    pending.append((path, parts))
                elif entry.is_file(follow_symlinks=False):
                    files.add(relative)
                else:
                    problems.append(f"묶음 트리에 일반 파일이나 폴더가 아닌 항목이 있습니다: {relative}")
            except OSError as exc:
                problems.append(f"묶음 트리 항목을 확인하지 못했습니다: {relative} ({exc})")
    return files, problems


def _expected_input_values(root: Path) -> tuple[dict[str, tuple[str, str]], int]:
    try:
        path = _plain_file(root, "INPUTS.tsv")
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except (OSError, UnicodeError, csv.Error):
        return {}, 0
    expected: dict[str, tuple[str, str]] = {}
    recorded = 0
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("step_id") or ""), []).append(row)
    for step_id, items in grouped.items():
        for index, row in enumerate(items, 1):
            if not _public_input(row):
                continue
            identifier = f"#input-scan-{quote(step_id, safe='-._~')}-{index}"
            expected[identifier] = (PurePosixPath(str(row.get("path") or "").replace("\\", "/")).name,
                                    str(row.get("sha256") or ""))
            recorded += bool(row.get("sha256"))
    return expected, recorded


def verify_bundle_copy(root: Path) -> dict[str, Any]:
    """Check a request-bundle copy. Old bundles without the format marker remain compatible."""
    from .adapters.owned import is_link

    report: dict[str, Any] = {"path": str(root), "present": False, "crate": False,
                              "external_input_hashes": 0, "problems": []}
    try:
        if is_link(root) or not root.is_dir():
            return report
    except OSError:
        return report
    report["present"] = True
    problems: list[str] = report["problems"]
    try:
        if is_link(root.parent):
            problems.append("요청 묶음 부모가 link 또는 junction입니다")
            return report
    except OSError:
        problems.append("요청 묶음 부모 경로를 확인하지 못했습니다")
        return report
    try:
        metadata = _plain_file(root, METADATA_FILE)
    except FileNotFoundError:
        try:
            marked = FORMAT_MARKER in _plain_file(root, "README.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            marked = False
        if marked:
            problems.append(f"새 요청 묶음에 {METADATA_FILE}이 없습니다")
        return report
    except OSError as exc:
        report["crate"] = True
        problems.append(f"RO-Crate JSON 구조가 올바르지 않습니다: {exc}")
        return report
    report["crate"] = True
    try:
        if metadata.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError("metadata too large")
        crate = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(crate, dict) or not isinstance(crate.get("@graph"), list):
            raise ValueError("@graph is not an array")
        _assert_private_strings_absent(crate)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"RO-Crate JSON 구조가 올바르지 않습니다: {exc}")
        return report
    graph = crate["@graph"]
    entities = [item for item in graph if isinstance(item, dict)]
    if len(entities) != len(graph):
        problems.append("RO-Crate @graph에 object가 아닌 entity가 있습니다")
    ids = [item.get("@id") for item in entities]
    if any(not isinstance(item, str) or not item for item in ids):
        problems.append("RO-Crate entity에 @id가 없습니다")
    duplicate_ids = sorted(item for item, count in Counter(
        identifier for identifier in ids if isinstance(identifier, str)
    ).items() if count > 1)
    problems.extend(f"RO-Crate @id가 중복됩니다: {item}" for item in duplicate_ids)
    known = {item for item in ids if isinstance(item, str)}
    for entity in entities:
        pending = [value for key, value in entity.items() if key != "@id"]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                if "@id" in value:
                    reference = value.get("@id")
                    if not isinstance(reference, str):
                        problems.append("RO-Crate reference @id가 문자열이 아닙니다")
                    else:
                        try:
                            external = bool(urlsplit(reference).scheme)
                        except ValueError as exc:
                            problems.append(f"RO-Crate reference URL이 올바르지 않습니다: {reference} ({exc})")
                        else:
                            if not external and reference not in known:
                                problems.append(f"RO-Crate reference가 해소되지 않습니다: {reference}")
                pending.extend(item for key, item in value.items() if key != "@id")
    by_id = {item.get("@id"): item for item in entities if isinstance(item.get("@id"), str)}
    descriptor, root_entity = by_id.get(METADATA_FILE), by_id.get("./")
    if not isinstance(descriptor, Mapping) or descriptor.get("@type") != "CreativeWork" \
            or descriptor.get("about") != _ref("./"):
        problems.append("RO-Crate metadata descriptor가 root Dataset을 올바르게 가리키지 않습니다")
    declared = descriptor.get("conformsTo") if isinstance(descriptor, Mapping) else []
    declared = declared if isinstance(declared, list) else [declared]
    if _ref(RO_CRATE_PROFILE) not in declared or _ref(PROCESS_RUN_PROFILE) not in declared:
        problems.append("RO-Crate metadata descriptor의 고정 profile 선언이 없습니다")
    if not isinstance(root_entity, Mapping) or root_entity.get("@type") != "Dataset" \
            or root_entity.get("conformsTo") != _ref(PROCESS_RUN_PROFILE):
        problems.append("RO-Crate root Dataset의 Process Run Crate 선언이 올바르지 않습니다")
    manifest, manifest_problems = _read_manifest(root)
    problems.extend(manifest_problems)
    inventory, inventory_problems = _bundle_inventory(root)
    problems.extend(inventory_problems)
    problems.extend(f"MANIFEST에 등록되지 않은 파일이 있습니다: {relative}"
                    for relative in sorted(inventory - {"MANIFEST.tsv"} - manifest.keys()))
    files: dict[str, Mapping[str, Any]] = {}
    for entity in entities:
        types = entity.get("@type")
        if types == "File" or isinstance(types, list) and "File" in types:
            try:
                if local := _local_id(entity.get("@id")):
                    files[local] = entity
            except ValueError as exc:
                problems.append(str(exc))
    if isinstance(root_entity, Mapping):
        parts = root_entity.get("hasPart")
        parts = parts if isinstance(parts, list) else [parts]
        part_ids = {identifier for item in parts if isinstance(item, Mapping)
                    if isinstance(identifier := item.get("@id"), str)}
        missing_parts = sorted(identifier for identifier, entity in files.items()
                               if entity.get("@id") not in part_ids)
        problems.extend(f"RO-Crate File entity가 root hasPart에 없습니다: {item}" for item in missing_parts)
    recorded_statuses = {"copied", "copied (unreported)", "generated"}
    for relative, row in sorted(manifest.items()):
        if relative == ".":
            continue
        recorded_file = relative in inventory or row.get("status") in recorded_statuses
        size = str(row.get("size") or "")
        sha256 = str(row.get("sha256") or "")
        valid_fields = True
        if recorded_file and not re.fullmatch(r"[0-9]+", size):
            problems.append(f"MANIFEST 파일 행의 size가 정수가 아닙니다: {relative}")
            valid_fields = False
        if recorded_file and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            problems.append(f"MANIFEST 파일 행의 sha256이 64자리 hex가 아닙니다: {relative}")
            valid_fields = False
        if not recorded_file or not valid_fields:
            continue
        try:
            actual = _plain_file(root, relative)
            actual_size, actual_sha = actual.stat().st_size, _sha256(actual)
        except OSError as exc:
            problems.append(f"묶음 파일을 확인하지 못했습니다: {relative} ({exc})")
            continue
        if str(actual_size) != size or actual_sha.casefold() != sha256.casefold():
            problems.append(f"묶음 파일과 MANIFEST가 다릅니다: {relative}")
        if relative == METADATA_FILE:
            continue
        entity = files.get(relative)
        if entity is None:
            problems.append(f"MANIFEST 파일이 RO-Crate에 없습니다: {relative}")
        elif (str(entity.get("contentSize")) != size
              or str(entity.get("sha256") or "").casefold() != sha256.casefold()):
            problems.append(f"MANIFEST와 RO-Crate metadata가 다릅니다: {relative}")
    for relative in sorted(files):
        if relative != "MANIFEST.tsv" and relative not in manifest:
            problems.append(f"RO-Crate File entity가 MANIFEST에 없습니다: {relative}")
    crate_row = manifest.get(METADATA_FILE)
    if not crate_row or not crate_row.get("sha256") or not crate_row.get("size"):
        problems.append(f"MANIFEST에 {METADATA_FILE}의 size·sha256이 없습니다")
    if "MANIFEST.tsv" not in files:
        problems.append("RO-Crate에 MANIFEST.tsv File entity가 없습니다")
    expected_inputs, recorded = _expected_input_values(root)
    report["external_input_hashes"] = recorded
    for identifier, (name, sha256) in expected_inputs.items():
        entity = by_id.get(identifier)
        if entity is None:
            problems.append(f"INPUTS.tsv 기록이 RO-Crate 입력 스캔에 없습니다: {identifier}")
        elif entity.get("name") != name or str(entity.get("value") or "") != sha256:
            problems.append(f"INPUTS.tsv 기록과 RO-Crate 입력 스캔이 다릅니다: {identifier}")
    return report
