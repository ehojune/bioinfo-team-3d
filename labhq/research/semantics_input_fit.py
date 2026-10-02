"""Shadow-only input-kind versus operation fit counts (#149 decision 17, #151).

The relation table uses local vocabulary keys. EDAM identifiers are optional metadata and never drive a
verdict. This module returns counts only; callers must not publish step ids, values, paths or declarations.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..vocab import Vocab, current
from ..yaml_unique import UniqueKeyError, load_yaml_unique

log = logging.getLogger("labhq.semantics.input_fit")
RELATION_FILE = Path(__file__).parents[1] / "vocab" / "input_fit.yaml"
VERDICTS = ("fit", "mismatch", "unknown")
EDAM_RELATION = re.compile(r"^relation_[0-9]{4}$")
_CACHE: list[dict[str, Any] | None] = []


def _unknown(plan: Any) -> dict[str, int]:
    steps = (plan.get("steps") or []) if isinstance(plan, Mapping) else []
    return {"fit": 0, "mismatch": 0, "unknown": sum(isinstance(step, Mapping) for step in steps)}


def _load(vocab: Vocab) -> dict[str, Any]:
    raw = load_yaml_unique(RELATION_FILE.read_text(encoding="utf-8"), RELATION_FILE.name)
    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        raise ValueError("input_fit_shape")
    packs, agents, operations = raw.get("pack_operations"), raw.get("agent_operations"), raw.get("operations")
    if not all(isinstance(item, Mapping) for item in (packs, agents, operations)):
        raise ValueError("input_fit_shape")
    operation_keys = set(vocab.keys("operation"))
    data_keys = set(vocab.keys("data"))
    if set(operations) != operation_keys or any(value not in operation_keys for value in (*packs.values(), *agents.values())):
        raise ValueError("input_fit_operation")
    checked: dict[str, dict[str, Any]] = {}
    for operation, body in operations.items():
        if not isinstance(body, Mapping) or set(body) != {"fit", "mismatch", "edam_relation"}:
            raise ValueError("input_fit_rule")
        yes, no, relation = body["fit"], body["mismatch"], body["edam_relation"]
        if (not isinstance(yes, list) or not isinstance(no, list)
                or any(not isinstance(key, str) or key not in data_keys for key in [*yes, *no])
                or len(set(yes)) != len(yes) or len(set(no)) != len(no) or set(yes) & set(no)
                or relation is not None and (not isinstance(relation, str) or not EDAM_RELATION.fullmatch(relation))):
            raise ValueError("input_fit_rule")
        checked[str(operation)] = {"fit": frozenset(yes), "mismatch": frozenset(no),
                                   "edam_relation": relation}
    return {"pack_operations": dict(packs), "agent_operations": dict(agents), "operations": checked}


def relation_table(vocab: Vocab) -> dict[str, Any] | None:
    if not _CACHE:
        try:
            _CACHE.append(_load(vocab))
        except (OSError, UnicodeDecodeError, UniqueKeyError, ValueError) as exc:
            log.warning("input fit relation table unavailable (%s); verdicts stay unknown", type(exc).__name__)
            _CACHE.append(None)
    return _CACHE[0]


def reset_cache() -> None:
    _CACHE.clear()


def _norm(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parts = [part for part in value.replace("\\", "/").split("/") if part not in ("", ".")]
    return None if not parts or ".." in parts else "/".join(parts)


def _operation(step: Mapping[str, Any], plan: Mapping[str, Any], table: Mapping[str, Any]) -> str | None:
    protocol = plan.get("protocol") if isinstance(plan.get("protocol"), Mapping) else {}
    packs = protocol.get("packs") or []
    found = {table["pack_operations"].get(f"{pack.get('id')}@{pack.get('version')}")
             for pack in packs if isinstance(pack, Mapping)}
    found.discard(None)
    if len(found) == 1:
        return next(iter(found))
    if found:
        return None
    return table["agent_operations"].get(step.get("agent_id"))


def evaluate(plan: Any, *, vocab: Vocab | None = None) -> dict[str, int]:
    """Return one verdict per plan step, as aggregate counts only."""
    if not isinstance(plan, Mapping):
        return {key: 0 for key in VERDICTS}
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    vocab = vocab or current()
    if vocab is None:
        return _unknown(plan)
    table = relation_table(vocab)
    if table is None:
        return _unknown(plan)

    declared: dict[tuple[str, str], str] = {}
    for source in steps:
        sid = source.get("id")
        if not isinstance(sid, str):
            continue
        for entry in source.get("output_types") or []:
            if not isinstance(entry, Mapping):
                continue
            name, data_type = _norm(entry.get("name")), entry.get("data_type")
            if name and vocab.is_key("data", data_type):
                declared[(sid, name)] = data_type

    counts = {key: 0 for key in VERDICTS}
    for step in steps:
        operation = _operation(step, plan, table)
        refs = step.get("input_refs") if isinstance(step.get("input_refs"), list) else []
        states: list[str] = []
        rule = table["operations"].get(operation)
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith("step:"):
                states.append("unknown")
                continue
            source, sep, name = ref[5:].partition("/")
            normalized = _norm(name) if sep else None
            data_type = declared.get((source, normalized)) if normalized else None
            if not data_type or not rule:
                states.append("unknown")
            elif data_type in rule["mismatch"]:
                states.append("mismatch")
            elif data_type in rule["fit"]:
                states.append("fit")
            else:
                states.append("unknown")
        verdict = ("mismatch" if "mismatch" in states else "fit" if states and all(s == "fit" for s in states)
                   else "unknown")
        counts[verdict] += 1
    return counts
