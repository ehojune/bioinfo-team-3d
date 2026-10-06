"""Public resources the CSO can connect results with (PI decision 2026-10-06, #435 C).

The table is a reference, not a closed list: the planner is told to choose any other resource a request needs.
It lives in ``public_resources.tsv`` (one row per resource) so people can extend it without touching code.
"""

from __future__ import annotations

import csv
from pathlib import Path

RESOURCES_FILE = Path(__file__).with_name("public_resources.tsv")
COLUMNS = ("result", "resource", "use")


def load(path: Path | None = None) -> list[dict[str, str]]:
    """Rows in file order; a missing or malformed file yields no rows rather than breaking planning."""
    source = Path(path) if path is not None else RESOURCES_FILE
    try:
        with source.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != COLUMNS:
                return []
            return [row for row in reader if all((row.get(c) or "").strip() for c in COLUMNS)]
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def prompt_rule(rows: list[dict[str, str]] | None = None) -> str:
    """Resource names grouped by result kind; uses stay in the file to keep the plan prompt short."""
    rows = load() if rows is None else rows
    if not rows:
        return ""
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(row["result"].strip(), []).append(row["resource"].strip())
    listed = "; ".join(f"{kind}: {', '.join(dict.fromkeys(names))}" for kind, names in groups.items())
    return ("Public resources: when results name specific variants, genes, regions, proteins or compounds, plan a "
            "step that looks them up in fitting public resources and records each resource's release or version. "
            "This list is a reference, not a limit: choose any other resource the request needs (for example "
            "COSMIC for somatic variants or DisGeNET for gene-disease links). Reference list - " + listed + ".")
