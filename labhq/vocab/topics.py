"""Normalize request topics and render their closed vocabulary for planning prompts."""

from __future__ import annotations

from typing import Any

from . import Vocab


def normalize(raw: Any, vocab: Vocab) -> tuple[list[str], list[str]]:
    """Return sorted unique topic keys and sorted unknown values without echoing them in warnings."""
    if not isinstance(raw, list):
        return [], ["bad_shape"] if raw is not None else []
    known: set[str] = set()
    unknown: set[str] = set()
    for value in raw:
        if isinstance(value, str) and vocab.is_key("topic", value):
            known.add(value)
        elif isinstance(value, str) and value:
            unknown.add(value)
        else:
            unknown.add("bad_value")
    return sorted(known), sorted(unknown)


def prompt_rule(vocab: Vocab) -> str:
    """A compact key-and-definition catalog shared by the general and research planning prompts."""
    entries = "; ".join(f"{key} — {vocab.terms[key].definition}" for key in sorted(vocab.keys("topic")))
    return ("\n- Set top-level `topics` to the approved assay/modality or domain keys that describe the request; "
            "use multiple keys when needed and [] when none is known. LabHQ sorts and deduplicates them. "
            "topics do not replace data, format, or operation declarations. Approved topics: " + entries)
