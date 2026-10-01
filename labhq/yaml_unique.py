"""YAML loading that refuses a mapping key given twice, shared by core vocab code and the semantics pilot.

Kept apart from ``labhq.util`` so a module that must import little (the semantics pilot, the vocabulary) gets
the loader and nothing else. Removing the pilot never takes this loader with it.
"""

from __future__ import annotations

from typing import Any

import yaml


class UniqueKeyError(ValueError):
    """A YAML text that gives one mapping key twice, or does not parse. The message names no path."""


class _UniqueKeyLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # type: ignore[misc]
    """SafeLoader that rejects a mapping key given twice instead of keeping the last one."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError:
            raise yaml.constructor.ConstructorError(
                None, None, "unhashable key", key_node.start_mark) from None
        if duplicate:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def load_yaml_unique(text: str, where: str) -> Any:
    """yaml.safe_load that refuses duplicate keys. Errors carry ``where`` and a line, never a path."""
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" line {mark.line + 1}" if mark is not None else ""
        raise UniqueKeyError(f"{where}:{line} {getattr(exc, 'problem', None) or exc}") from None
