from __future__ import annotations

import copy
import json
import os
import re
import socket
import tempfile
from pathlib import Path
from typing import Any


CLAUDE_ENV_PASSTHROUGH = frozenset({
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
})


def parent_claude_markers(env: dict[str, str]) -> list[str]:
    """Claude host-session variables that must not reach staff subprocesses."""
    return [key for key in env if (key.upper().startswith("CLAUDE_") or
                                    key.upper().startswith("CLAUDECODE"))
            and key.upper() not in CLAUDE_ENV_PASSTHROUGH]


# A runner started from a Codex terminal inherits that session's CODEX_* variables. codex-cli 0.159.2 reads several
# that move or loosen what a staff Codex runs (CODEX_EXEC_SERVER_URL, CODEX_SANDBOX*, CODEX_PERMISSION_PROFILE,
# CODEX_SQLITE_HOME, ...), so only the config home, the API key and the CA bundle pass (#146). A staff setting that
# needs another one names it in engines.codex.env, which is applied after this.
CODEX_ENV_PASSTHROUGH = frozenset({"CODEX_HOME", "CODEX_API_KEY", "CODEX_CA_CERTIFICATE"})


def parent_codex_markers(env: dict[str, str]) -> list[str]:
    """Codex host-session variables that must not reach staff subprocesses."""
    return [key for key in env if key.upper().startswith("CODEX_") and key.upper() not in CODEX_ENV_PASSTHROUGH]


def strip_parent_session_env(env: dict[str, str]) -> dict[str, str]:
    blocked = {*parent_claude_markers(env), *parent_codex_markers(env)}
    return {key: value for key, value in env.items() if key not in blocked}


def merge_staff_env(parent: dict[str, str], *overrides: dict[str, str]) -> dict[str, str]:
    """Drop inherited session markers before applying operator-provided overrides."""
    merged = strip_parent_session_env(parent)
    for override in overrides:
        merged.update(override)
    return merged


def atomic_write_text(path: str | Path, text: str) -> None:
    """Replace a text file from a same-directory temporary file."""
    target = Path(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as out:
            temporary = Path(out.name)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# Windows cannot open a folder with os.open, and Python has no other folder flush. There a new or renamed entry
# rests on NTFS's metadata journal instead, so fsync_dir does nothing rather than fail.
DIR_FSYNC = os.name != "nt"


def fsync_dir(path: str | Path) -> None:
    """Make a folder's entries durable: a file just created or renamed in it survives a power loss (POSIX)."""
    if not DIR_FSYNC:
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def short(obj: Any, n: int = 300) -> str:
    """Compact one-line preview of any object, for event payloads and logs."""
    if obj is None:
        return ""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def clip(text: str | None, n: int) -> str:
    """Keep head and tail of long text (tails usually hold the conclusion)."""
    if not text:
        return ""
    if len(text) <= n:
        return text
    head = int(n * 0.6)
    tail = n - head - 40
    return f"{text[:head]}\n\n[… {len(text) - head - tail} chars clipped …]\n\n{text[-tail:]}"


def slugify(s: str, max_len: int = 40) -> str:
    s = re.sub(r"\.git$", "", s.strip().rstrip("/"))
    s = s.split("/")[-1] or s
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s or "contract")[:max_len]


def extract_json(text: str | None, *, strict: bool = True) -> Any | None:
    """Best-effort: parse a JSON object from model output (whole text, fenced block, or last object).

    ``strict=False`` also accepts raw control characters (a real newline or tab) inside strings, as
    ``json.loads(strict=False)`` does. Only callers that read free text a model was asked to lay out on lines
    pass it.
    """
    if not text:
        return None
    t = text.strip()
    dec = json.JSONDecoder(strict=strict)
    try:
        return dec.decode(t)
    except ValueError:
        pass
    for block in reversed(re.findall(r"```(?:json)?\s*(.*?)```", t, flags=re.S)):
        try:
            return dec.decode(block)
        except ValueError:
            continue
    best = None
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, end = dec.raw_decode(t[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and (best is None or end > best[1]):
            best = (obj, end)
    return best[0] if best else None


def _allows_null(schema: dict[str, Any]) -> bool:
    typ = schema.get("type")
    if typ == "null" or isinstance(typ, list) and "null" in typ:
        return True
    return any(isinstance(choice, dict) and _allows_null(choice)
               for key in ("anyOf", "oneOf") for choice in schema.get(key, []))


def _nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if _allows_null(schema):
        return schema
    if "$ref" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    typ = schema.get("type")
    # The Codex backend rejects a nullable array type inside a nested object array (for example
    # Claim.comparisons[].assumptions). The equivalent anyOf form is accepted by the supported subset.
    if typ == "array":
        return {"anyOf": [schema, {"type": "null"}]}
    if isinstance(typ, str):
        return {**schema, "type": [typ, "null"]}
    return {"anyOf": [schema, {"type": "null"}]}


_OPENAI_STRICT_UNSUPPORTED_TYPE_CONSTRAINTS = {
    "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minItems", "maxItems",
}


def openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a strict Structured Outputs schema without changing the application's contract.

    OpenAI requires every object property in ``required`` and forbids additional properties. Fields that the
    application contract leaves optional become nullable only in this transport copy; the original schema stays
    available for validation and stable hashes.
    """
    out = copy.deepcopy(schema)
    definition_names = set(out.get("$defs", {})) if isinstance(out.get("$defs"), dict) else set()

    def visit(node: Any, path: str = "$schema") -> Any:
        if isinstance(node, list):
            return [visit(item, f"{path}[]") for item in node]
        if not isinstance(node, dict):
            return node
        # The Codex Structured Outputs backend rejects type-specific constraints for this model family. The
        # application's unchanged Pydantic schema enforces them after transport validation.
        for keyword in _OPENAI_STRICT_UNSUPPORTED_TYPE_CONSTRAINTS:
            node.pop(keyword, None)
        # Structured Outputs rejects annotation defaults; optionality is represented by null instead.
        node.pop("default", None)
        properties = node.get("properties")
        additional = node.get("additionalProperties")
        object_type = node.get("type") == "object" or (
                isinstance(node.get("type"), list) and "object" in node["type"])
        if (isinstance(additional, dict) or additional is True) and isinstance(properties, dict) and properties:
            raise ValueError(f"{path}: objects with properties and schema-valued additionalProperties are unsupported")
        if isinstance(additional, dict) or object_type and not properties and additional is not False:
            allowed = {"type", "additionalProperties", "properties", "title", "description", "$defs",
                       "definitions", "$id", "$schema"}
            unsupported = sorted(set(node) - allowed)
            if unsupported:
                raise ValueError(f"{path}: dictionary schema keywords are unsupported: {unsupported}")
            typ = node.get("type")
            types = set(typ) if isinstance(typ, list) else {typ}
            if typ is not None and "object" not in types:
                raise ValueError(f"{path}: schema-valued additionalProperties requires object type")
            value_schema = visit(additional, f"{path}.additionalProperties") if isinstance(additional, dict) else {}
            converted = {
                key: ({name: visit(schema, f"{path}.{key}.{name}") for name, schema in value.items()}
                      if key in {"$defs", "definitions"} and isinstance(value, dict)
                      else visit(value, f"{path}.{key}"))
                for key, value in node.items()
                if key not in {"type", "additionalProperties", "properties"}
            }
            converted.update({
                "type": ["array", "null"] if "null" in types else "array",
                "_labhq_dictionary_transport": True,
                "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "value": value_schema},
                    "required": ["key", "value"],
                    "additionalProperties": False,
                },
            })
            return converted
        original_required = set(node.get("required", []))
        if isinstance(properties, dict):
            for key, child in list(properties.items()):
                child = visit(child, f"{path}.properties.{key}")
                if key not in original_required:
                    child = _nullable_schema(child)
                properties[key] = child
            node["required"] = list(properties)
            node["additionalProperties"] = False
        elif object_type:
            if additional is not False:
                raise ValueError(f"{path}: arbitrary object requires a schema-valued additionalProperties")
            node["properties"] = {}
            node["required"] = []
            node["additionalProperties"] = False
        for key, child in list(node.items()):
            if key != "properties":
                if key in {"$defs", "definitions"} and isinstance(child, dict):
                    node[key] = {name: visit(schema, f"{path}.{key}.{name}")
                                 for name, schema in child.items()}
                else:
                    node[key] = visit(child, f"{path}.{key}")
        return node

    converted = visit(out)
    dictionary_arrays: list[dict[str, Any]] = []

    def find_dictionary_arrays(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                find_dictionary_arrays(item)
        elif isinstance(node, dict):
            if node.pop("_labhq_dictionary_transport", False):
                dictionary_arrays.append(node)
            for child in node.values():
                find_dictionary_arrays(child)

    find_dictionary_arrays(converted)
    if dictionary_arrays:
        definitions = converted.setdefault("$defs", {})
        for index, dictionary in enumerate(dictionary_arrays, 1):
            name = "LabhqDictionaryEntry" if index == 1 else f"LabhqDictionaryEntry{index}"
            while name in definition_names:
                index += 1
                name = f"LabhqDictionaryEntry{index}"
            definition_names.add(name)
            definitions[name] = dictionary["items"]
            # Hoisting keeps deeply nested dictionaries within the Structured Outputs schema-depth limit; the
            # referenced object is still the lossless key/value transport contract.
            dictionary["items"] = {"$ref": f"#/$defs/{name}"}
    return converted


def strip_optional_nulls(value: Any, schema: dict[str, Any]) -> Any:
    """Drop transport-only nulls for fields optional in ``schema``; preserve required nulls for validation."""
    root = schema

    def dictionary_value_schema(node: Any) -> dict[str, Any] | None:
        node = dereference(node)
        if not isinstance(node, dict) or node.get("properties"):
            return None
        additional = node.get("additionalProperties")
        typ = node.get("type")
        types = set(typ) if isinstance(typ, list) else {typ}
        if isinstance(additional, dict):
            return additional
        if "object" in types and additional is not False:
            return {}
        return None

    def dereference(node: Any) -> Any:
        seen: set[str] = set()
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if ref in seen or not ref.startswith("#/"):
                break
            seen.add(ref)
            target: Any = root
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            node = target
        return node

    def matches(candidate: Any, current: Any) -> bool:
        candidate = dereference(candidate)
        if not isinstance(candidate, dict):
            return False
        typ = candidate.get("type")
        types = set(typ) if isinstance(typ, list) else {typ}
        if current is None:
            return "null" in types
        if isinstance(current, dict):
            return "object" in types or "properties" in candidate
        if isinstance(current, list):
            return "array" in types or dictionary_value_schema(candidate) is not None
        if isinstance(current, bool):
            return "boolean" in types
        if isinstance(current, str):
            return "string" in types
        if isinstance(current, int):
            return "integer" in types or "number" in types
        if isinstance(current, float):
            return "number" in types
        return False

    def shape(node: Any, current: Any) -> Any:
        node = dereference(node)
        if not isinstance(node, dict):
            return node
        choices = node.get("anyOf") or node.get("oneOf")
        if isinstance(choices, list):
            return next((shape(choice, current) for choice in choices if matches(choice, current)), node)
        return node

    def clean(current: Any, node: Any) -> Any:
        node = shape(node, current)
        dictionary_value = dictionary_value_schema(node)
        if dictionary_value is not None:
            if not isinstance(current, list):
                return copy.deepcopy(current)
            cleaned = {}
            for index, entry in enumerate(current):
                if not isinstance(entry, dict) or set(entry) != {"key", "value"} or not isinstance(entry["key"], str):
                    raise ValueError(f"invalid dictionary entry at index {index}")
                key = entry["key"]
                if key in cleaned:
                    raise ValueError(f"duplicate dictionary key {key!r}")
                cleaned[key] = clean(entry["value"], dictionary_value)
            return cleaned
        if isinstance(current, dict) and isinstance(node, dict):
            properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            required = set(node.get("required", []))
            cleaned = {}
            for key, child in current.items():
                if child is None and key in properties and key not in required:
                    continue
                cleaned[key] = clean(child, properties.get(key, {}))
            return cleaned
        if isinstance(current, list) and isinstance(node, dict):
            return [clean(item, node.get("items", {})) for item in current]
        return copy.deepcopy(current)

    return clean(value, schema)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def output_relpath(name: str) -> str | None:
    """A declared step output as `outputs/...` (POSIX, lexically normalized), or None if unsafe.

    Runner and orchestrator both use this, so `./t.tsv` and `outputs\t.tsv` match. Parent traversal is
    rejected before normalization: an agent told to write `tmp/../t.tsv` would otherwise write outside
    `outputs/` even though its normalized declaration appeared contained.
    """
    s = str(name).strip().replace("\\", "/")
    parts = s.split("/")
    if not s or s.startswith("/") or re.match(r"^[A-Za-z]:", s) or parts[0] == "~" or ".." in parts:
        return None
    import posixpath

    n = posixpath.normpath(s)
    if n in (".", "..") or n.startswith("../"):
        return None
    return n if n == "outputs" or n.startswith("outputs/") else f"outputs/{n}"


# A step id that can name the inputs/<step id> link in a downstream step folder (#423): the request bundle's own
# step-folder rule, so an id that links here also bundles (PR #429 review).
INPUT_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def input_relpath(step_id: str, output: str) -> str | None:
    """An upstream step's output as the downstream folder reads it, `inputs/<step id>/...`, or None."""
    relative = output_relpath(output)
    if not INPUT_STEP_ID.fullmatch(str(step_id)) or relative is None or relative == "outputs":
        return None
    return f"inputs/{step_id}/{relative[len('outputs/'):]}"
