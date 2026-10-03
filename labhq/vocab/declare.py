"""One bounded normalization contract for output type declarations (#221 todo 2), used on every path.

Plan → dispatch meta → runner result → snapshot → both semantics models all go through these functions:

- ``normalize_entries``: a step's raw ``output_types`` from the CSO becomes entries whose ``name`` is one of the
  step's declared outputs and whose fields are vocabulary keys. Anything else is dropped and counted under a
  fixed issue code; the raw text is never kept, warned about or echoed.
- ``meta_declarations``: what the dispatched task carries (keys only, plus the vocabulary version).
- ``runner_records``: what the runner attaches to the outputs it actually collected. A declared field is copied
  (basis ``declared``); a missing format is inferred from the file extension (basis ``inferred``, a name check,
  never a content check). Data types are never inferred.
- ``read``: legacy ``{path: key}`` meta, the meta above and runner records, back into per-field values. A value
  made under another vocabulary version, two values for one file, or a malformed record reads as ``unknown``.

Declarations are hints for the shadow models. Nothing here grants, approves, dispatches or validates anything.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..util import output_relpath
from . import BRANCHES, UNKNOWN, Vocab

FIELDS = ("data_type", "format")
BRANCH = {"data_type": "data", "format": "format"}
BASIS = {"data_type": ("declared", "unknown"), "format": ("declared", "inferred", "unknown")}
SOURCES = ("plan", "staff", "extension")
ISSUES = ("bad_shape", "bad_entry", "bad_name", "bad_value", "too_long", "too_many", "over_budget", "unknown_key",
          "wrong_branch", "unpaired_name", "duplicate_declaration")
REASONS = ("not_declared", "declaration_conflict", "vocab_changed", "vocab_mismatch", "invalid_declaration",
           "no_vocab")
MAX_NAME = 256          # characters of one declared name
MAX_KEY = 64            # characters of one declared key
MAX_STEP_BYTES = 8192   # JSON bytes of one step's raw output_types
MAX_RECORDS = 1000      # declarations or records read from one task row
MAX_ENTRIES = 64        # entries of one step: the research contract (ResearchStep.output_types) has the same cap
CONFLICT = "\x00conflict"  # a staff field declared more than once for one file; never a vocabulary key
_ENTRY_KEYS = {"name", *FIELDS}
# Bare legacy declarations have no vocabulary hash. These names changed meaning or were renamed in the PI review.
LEGACY_CHANGED_TYPES = frozenset({"normalized_counts", "features"})


@dataclass(frozen=True)
class Field:
    value: str                  # a vocabulary key, or "unknown"
    basis: str                  # declared | inferred | unknown
    reason: str | None = None   # why unknown (REASONS)
    source: str | None = None   # plan | staff | extension
    legacy: bool = False        # a bare `{path: key}` from before typed declarations: judged by the reader's rules


def unknown(reason: str) -> Field:
    return Field(UNKNOWN, "unknown", reason)


# ---------------------------------------------------------------- plan side

def normalize_entries(outputs: Iterable[str], raw: Any, vocab: Vocab) -> tuple[list[dict], Counter]:
    """A step's raw declarations as normalized entries, plus issue counts. Order of the raw list never matters."""
    issues: Counter = Counter()
    if raw is None:
        return [], issues
    if not isinstance(raw, list):
        issues["bad_shape"] += 1
        return [], issues
    try:
        size = len(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        issues["bad_shape"] += 1
        return [], issues
    if size > MAX_STEP_BYTES:
        issues["over_budget"] += 1
        return [], issues
    declared = {rel for rel in map(output_relpath, outputs) if rel and rel != "outputs"}
    if len(raw) > min(len(declared), MAX_ENTRIES):  # dropping all keeps the result order-free
        issues["too_many"] += 1
        return [], issues
    found: dict[str, dict[str, list[str | None]]] = {}
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - _ENTRY_KEYS:
            issues["bad_entry"] += 1
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            issues["bad_name"] += 1
            continue
        if len(name) > MAX_NAME:
            issues["too_long"] += 1
            continue
        rel = output_relpath(name.strip())
        if rel not in declared:
            issues["unpaired_name"] += 1
            continue
        slot = found.setdefault(rel, {})
        for field in FIELDS:
            value = entry.get(field)
            if value is None:
                continue
            key: str | None = None
            if not isinstance(value, str):
                issues["bad_value"] += 1
            elif len(value) > MAX_KEY:
                issues["too_long"] += 1
            elif vocab.is_key(BRANCH[field], value.strip()):
                key = value.strip()
            elif any(vocab.is_key(b, value.strip()) for b in BRANCHES):
                issues["wrong_branch"] += 1
            else:
                issues["unknown_key"] += 1
            slot.setdefault(field, []).append(key)  # an invalid value still counts as a second declaration
    entries = []
    for rel in sorted(found):
        entry: dict[str, str] = {"name": rel}
        for field, values in found[rel].items():
            if len(values) > 1:
                issues["duplicate_declaration"] += 1
            elif values[0] is not None:
                entry[field] = values[0]
        if len(entry) > 1:
            entries.append({**entry, "vocab": vocab.sha256})
    return entries, issues


def stats(outputs: Iterable[str], entries: Iterable[Mapping[str, Any]], issues: Mapping[str, int]) -> dict:
    """Counts for one step: declared outputs are the denominator; issues are fixed codes."""
    entries = list(entries)
    return {"outputs": len({r for r in map(output_relpath, outputs) if r and r != "outputs"}),
            "data_declared": sum(1 for e in entries if "data_type" in e),
            "format_declared": sum(1 for e in entries if "format" in e),
            "issues": {k: int(issues[k]) for k in ISSUES if issues.get(k)}}


def add_stats(total: dict | None, step: Mapping[str, Any]) -> dict:
    total = dict(total or {"outputs": 0, "data_declared": 0, "format_declared": 0, "issues": {}})
    for key in ("outputs", "data_declared", "format_declared"):
        total[key] = int(total.get(key) or 0) + int(step.get(key) or 0)
    issues = dict(total.get("issues") or {})
    for code, n in (step.get("issues") or {}).items():
        issues[code] = int(issues.get(code) or 0) + int(n)
    total["issues"] = {k: issues[k] for k in ISSUES if issues.get(k)}
    return total


def issue_warning(step_id: str, issues: Mapping[str, int]) -> str | None:
    """A plan warning with fixed codes and counts only: never the declared name or value."""
    if not issues:
        return None
    parts = ", ".join(f"{code} {issues[code]}" for code in ISSUES if issues.get(code))
    return f"step {step_id}: output_types ignored ({parts})"


def meta_declarations(entries: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """Dispatch meta: {output relpath: {data_type?, format?}}, keys only."""
    return {e["name"]: {f: e[f] for f in FIELDS if f in e} for e in entries if isinstance(e, Mapping)}


def prompt_rule(vocab: Vocab) -> str:
    """The one PLAN rule line, generated from the local keys (never from EDAM labels)."""
    return ("\n- Optionally add output_types entries {name, data_type, format} for outputs whose kind you know, "
            "using only these names. data_type: " + ", ".join(vocab.keys("data")) + ". format: "
            + ", ".join(vocab.keys("format")) + ". Leave a field out when unsure; other names are ignored.")


ENTRY_SCHEMA: dict[str, Any] = {
    "type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"name": {"type": "string"}, "data_type": {"type": "string"}, "format": {"type": "string"}},
        "required": ["name"]}}


def with_output_types(step_schema: Mapping[str, Any]) -> dict[str, Any]:
    """A step object schema with the optional ``output_types`` property (free strings, never an enum)."""
    out = json.loads(json.dumps(step_schema))
    out["properties"]["output_types"] = json.loads(json.dumps(ENTRY_SCHEMA))
    return out


# ---------------------------------------------------------------- runner side

def _field_record(value: Any, basis: str, source: str) -> dict:
    return {"value": value, "basis": basis, "source": source}


def _unknown_record(reason: str) -> dict:
    return {"value": UNKNOWN, "basis": "unknown", "reason": reason}


def runner_records(collected: Iterable[str], meta: Mapping[str, Any], vocab: Vocab | None) -> dict[str, dict]:
    """Records for the outputs the runner actually collected. Only declared fields are copied (keys, unchecked:
    the gateway checked them against its vocabulary); a format is inferred only under the same version."""
    version = meta.get("output_types_vocab")
    declared = meta.get("output_types") if isinstance(meta.get("output_types"), Mapping) else {}
    out: dict[str, dict] = {}
    for rel in list(dict.fromkeys(collected))[:MAX_RECORDS]:
        given = declared.get(rel) if isinstance(declared.get(rel), Mapping) else {}
        record: dict[str, Any] = {"v": 1, "vocab": version if isinstance(version, str) else None}
        data = given.get("data_type")
        record["data_type"] = (_field_record(data, "declared", "plan") if isinstance(data, str) and len(data) <= MAX_KEY
                               else _unknown_record("not_declared"))
        fmt = given.get("format")
        if isinstance(fmt, str) and len(fmt) <= MAX_KEY:
            record["format"] = _field_record(fmt, "declared", "plan")
        elif vocab is None:
            record["format"] = _unknown_record("no_vocab")
        elif vocab.sha256 != version:
            record["format"] = _unknown_record("vocab_mismatch")
        else:
            key = vocab.format_of(rel)
            record["format"] = (_field_record(key, "inferred", "extension") if key else _unknown_record("not_declared"))
        out[rel] = record
    return out


def bounded_records(value: Any) -> dict[str, Any]:
    """TaskResult.output_types as received: a mapping of at most MAX_RECORDS small entries, else empty."""
    if not isinstance(value, Mapping) or len(value) > MAX_RECORDS:
        return {}
    try:
        if len(json.dumps(value, ensure_ascii=False)) > MAX_RECORDS * 512:
            return {}
    except (TypeError, ValueError):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(k, str) and len(k) <= MAX_NAME}


# ---------------------------------------------------------------- readers (both semantics models)

def _from_record(record: Any, field: str, vocab: Vocab | None) -> Field:
    if not isinstance(record, Mapping) or record.get("v") != 1:
        return unknown("invalid_declaration")
    if vocab is None:
        return unknown("no_vocab")
    if record.get("vocab") != vocab.sha256:
        return unknown("vocab_changed")
    body = record.get(field)
    if not isinstance(body, Mapping):
        return unknown("invalid_declaration")
    basis = body.get("basis")
    if basis == "unknown":
        reason = body.get("reason")
        return unknown(reason if reason in REASONS else "invalid_declaration")
    if (basis not in BASIS[field] or body.get("source") not in SOURCES
            or not vocab.is_key(BRANCH[field], body.get("value"))):
        return unknown("invalid_declaration")
    return Field(body["value"], basis, source=body["source"])


def _from_meta(given: Any, field: str, version: Any, vocab: Vocab | None) -> Field:
    if isinstance(given, str):  # legacy {path: key}: a data type only, judged once here for both shadow models
        if field != "data_type":
            return unknown("not_declared")
        if vocab is None:
            return unknown("no_vocab")
        if given in LEGACY_CHANGED_TYPES:
            return unknown("vocab_changed")
        return (Field(given, "declared", source="plan", legacy=True)
                if vocab.is_key("data", given) else unknown("not_declared"))
    if not isinstance(given, Mapping) or field not in given:
        return unknown("not_declared" if isinstance(given, Mapping) else "invalid_declaration")
    if vocab is None:
        return unknown("no_vocab")
    if version != vocab.sha256:
        return unknown("vocab_changed")
    value = given[field]
    return Field(value, "declared", source="plan") if vocab.is_key(BRANCH[field], value) else unknown("invalid_declaration")


def staff_declarations(refs: Iterable[Any], normalize: Callable[[str], str]) -> dict[str, dict[str, str]]:
    """Staff declarations of validated research artifact refs, per normalized path. A field declared by more than
    one ref for the same file is CONFLICT whatever the values and their order (as plan duplicates are)."""
    seen: dict[str, dict[str, list[str]]] = {}
    for ref in refs:
        path = getattr(ref, "path", None)
        if not isinstance(path, str) or not path.strip():
            continue
        for field in FIELDS:
            value = getattr(ref, field, None)
            if isinstance(value, str):
                seen.setdefault(normalize(path), {}).setdefault(field, []).append(value)
    return {path: {f: (vs[0] if len(vs) == 1 else CONFLICT) for f, vs in fields.items()}
            for path, fields in seen.items()}


def _by_path(value: Any, normalize: Callable[[str], str]) -> dict[str, Any]:
    """Normalized path -> entry; two spellings of one file make that file conflicting (None)."""
    out: dict[str, Any] = {}
    if not isinstance(value, Mapping):
        return out
    for raw, entry in list(value.items())[:MAX_RECORDS]:
        if not isinstance(raw, str):
            continue
        path = normalize(raw)
        out[path] = None if path in out else entry
    return out


def read(meta: Mapping[str, Any] | None, result: Mapping[str, Any] | None, vocab: Vocab | None, *,
         normalize: Callable[[str], str], staff: Mapping[str, Mapping[str, Any]] | None = None
         ) -> dict[str, dict[str, Field]]:
    """Per output path: {data_type: Field, format: Field}. Paths nobody declared or recorded are absent.

    Runner records win over the dispatch meta they were copied from (not a second witness); a record that disagrees
    with the meta, two spellings of one path, or a staff declaration that disagrees all leave that field unknown."""
    meta = meta if isinstance(meta, Mapping) else {}
    version = meta.get("output_types_vocab")
    declared = _by_path(meta.get("output_types"), normalize)
    records = _by_path(bounded_records((result or {}).get("output_types") if isinstance(result, Mapping) else None),
                       normalize)
    out: dict[str, dict[str, Field]] = {}
    for path in sorted(set(declared) | set(records)):
        fields: dict[str, Field] = {}
        for field in FIELDS:
            if (path in declared and declared[path] is None) or (path in records and records[path] is None):
                fields[field] = unknown("declaration_conflict")
                continue
            planned = _from_meta(declared[path], field, version, vocab) if path in declared else None
            if path in records:
                got = _from_record(records[path], field, vocab)
                if (planned is not None and planned.basis == "declared" and got.basis != "unknown"
                        and planned.value != got.value):
                    got = unknown("declaration_conflict")
                fields[field] = got
            else:
                fields[field] = planned or unknown("not_declared")
        out[path] = fields
    for path, given in (staff or {}).items():
        fields = out.setdefault(path, {f: unknown("not_declared") for f in FIELDS})
        for field in FIELDS:
            value = given.get(field) if isinstance(given, Mapping) else None
            if value is None:
                continue
            current = fields[field]
            if value == CONFLICT:
                fields[field] = unknown("declaration_conflict")
            elif current.basis in ("declared", "inferred") and current.value != value:
                fields[field] = unknown("declaration_conflict")
            elif current.basis == "unknown" and current.reason == "not_declared":
                # a staff key counts under the version the task was dispatched with, never under today's
                fields[field] = (unknown("no_vocab") if vocab is None else
                                 unknown("vocab_changed") if version != vocab.sha256 else
                                 Field(value, "declared", source="staff")
                                 if vocab.is_key(BRANCH[field], value) else unknown("invalid_declaration"))
    return out
