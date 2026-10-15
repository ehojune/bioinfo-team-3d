"""What a new `labhq init` config turns on (#613).

`REFERENCE_BEHAVIOUR` is the behaviour of the PI's tested config as setting paths and values: no token, key, path or
person's name. `config/labhq.example.yaml` (and its packaged copy) carry the same values for `labhq init` and for
anyone copying the example by hand; tests/test_init_defaults.py fails when they drift apart.

Left out on purpose, because each is a person's or a PC's value that `init` asks or detects:
policy.bioinfo_agent.pipeline_pr (a consent), dev_log.repo (a repository), engines.claude_code.env.BIOINFO_AGENT_DIR
(this PC's checkout), hpc.scheduler (detected), engines.*.bin and *.env (this PC's installs).
"""

from __future__ import annotations

from typing import Any

# (setting path, value). Lists compare as written.
REFERENCE_BEHAVIOUR: tuple[tuple[str, Any], ...] = (
    ("research.enabled", True),
    ("research.evidence_checkpoint", True),
    ("research.active_packs", ["bulk_tumor_normal@3", "single_cell_de@3"]),
    ("semantics.mode", "ab"),
    ("semantics.actions", "confirm"),
    ("plan.declare_output_types", True),
)

# The semantics model reads an output's hash only inside a public or internal zone, so the run folder is one.
WORKSPACE_ZONE_NOTE = "labhq 작업 폴더 — 의미 모델 hash용"
DEFAULT_WORKSPACE = "~/.labhq/runs"

MISSING = object()


def lookup(data: Any, dotted: str) -> Any:
    """The value at a dotted setting path of a loaded YAML mapping, or MISSING."""
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def drift(data: dict) -> list[str]:
    """Reference settings a config mapping does not carry, as `path: found, wanted` lines. Empty when it matches."""
    problems = []
    for dotted, wanted in REFERENCE_BEHAVIOUR:
        found = lookup(data, dotted)
        if found != wanted:
            problems.append(f"{dotted}: {'없음' if found is MISSING else repr(found)}, 기준 {wanted!r}")
    return problems


def workspace_zone(data: dict) -> dict:
    """The internal zone for the config's own run folder."""
    workspace = ((data.get("runner") or {}).get("workspace_root")) or DEFAULT_WORKSPACE
    return {"path": workspace, "level": "internal", "note": WORKSPACE_ZONE_NOTE}


def ensure_workspace_zone(data: dict) -> None:
    """Point the run-folder zone at this config's workspace_root (an instance has its own). Other zones stay."""
    policy = data.setdefault("policy", {})
    zones = [zone for zone in (policy.get("data_zones") or [])
             if not (isinstance(zone, dict) and zone.get("note") == WORKSPACE_ZONE_NOTE)]
    zones.append(workspace_zone(data))
    policy["data_zones"] = zones
