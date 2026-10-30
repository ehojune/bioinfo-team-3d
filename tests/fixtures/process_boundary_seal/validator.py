"""Synthetic acceptance contract, NOT a supervisor or real protection verifier.

The future producer adapter must authenticate the witness outside the child and
pin launch identity/objects independently. This fixture models that input only.
No paths are opened, processes launched, or host protection claims made here.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


# Fixed probe topology; an adapter maps actual pinned paths to these roles.
# Arbitrary source trees require a separately reviewed topology contract.
REQUIRED_OBJECTS = {
    "parent:root": "directory",
    "parent:source": "directory",
    "source:tool.py": "file",
}


@dataclass(frozen=True)
class Verdict:
    contract: str
    status: str
    reasons: tuple[str, ...]
    protection: str = "unverified"


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_expectations(trusted: dict[str, Any]) -> bool:
    if not all(_text(trusted.get(key)) for key in ("run_id", "launch_nonce", "witness_id", "child_id")):
        return False
    supervisor = trusted.get("supervisor")
    if (not isinstance(supervisor, dict) or not _text(supervisor.get("subject_id"))
            or not _digest(supervisor.get("executable_sha256"))
            or supervisor["subject_id"] == trusted["child_id"]):
        return False
    objects = trusted.get("objects")
    if not isinstance(objects, dict) or set(objects) != set(REQUIRED_OBJECTS):
        return False
    identities = set()
    for role, obj in objects.items():
        if (not isinstance(obj, dict) or not _text(obj.get("object_id"))
                or obj.get("kind") != REQUIRED_OBJECTS[role] or obj.get("symlink") is not False
                or type(obj.get("nlink")) is not int or obj["nlink"] < 1):
            return False
        identities.add(obj["object_id"])
        if obj["kind"] == "file" and (obj["nlink"] != 1 or not _digest(obj.get("sha256"))):
            return False
    return len(identities) == len(objects)


def validate(evidence: dict[str, Any], trusted: dict[str, Any]) -> Verdict:
    """Validate synthetic observations against caller-owned launch expectations.

    ``trusted`` must never be constructed from the child receipt. Its provenance
    is assumed ONLY inside this fixture. Production authentication is unbuilt.
    Missing or malformed observations fail closed; labels in evidence are ignored.
    """
    reasons: list[str] = []

    def reject(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if not isinstance(evidence, dict) or not isinstance(trusted, dict):
        return Verdict("rejected", "unverified", ("malformed_evidence",))
    if not _valid_expectations(trusted):
        return Verdict("rejected", "unverified", ("malformed_launch_expectations",))
    if evidence.get("adapter") != "synthetic-v1":
        return Verdict("rejected", "unsupported", ("producer_adapter_unavailable",))
    witness = evidence.get("witness")
    if not isinstance(witness, dict):
        return Verdict("rejected", "unverified", ("missing_independent_witness",))
    if evidence.get("authority") != "independent_witness":
        reject("child_receipt_is_not_authority")
    witness_id = trusted.get("witness_id")
    supervisor = trusted.get("supervisor")
    if (not isinstance(witness_id, str) or not witness_id
            or witness_id in {trusted.get("child_id"),
                             supervisor.get("subject_id") if isinstance(supervisor, dict) else None}
            or witness.get("observer_id") != witness_id
            or witness.get("origin") != "independent"):
        reject("witness_not_independent")
    for key in ("run_id", "launch_nonce"):
        if not isinstance(trusted.get(key), str) or not trusted[key] or witness.get(key) != trusted[key]:
            reject(f"{key}_mismatch")
    if (not isinstance(supervisor, dict) or not supervisor.get("subject_id")
            or not supervisor.get("executable_sha256")
            or witness.get("supervisor") != supervisor):
        reject("untrusted_supervisor")

    expected = trusted["objects"]

    events = witness.get("events")
    if not isinstance(events, list) or not events or any(not isinstance(e, dict) for e in events):
        return Verdict("rejected", "unverified", tuple(reasons + ["missing_observations"]))
    times = [e.get("time_ns") for e in events]
    if (any(type(e.get("sequence")) is not int for e in events)
            or [e.get("sequence") for e in events] != list(range(len(events)))
            or any(type(t) is not int or t < 0 for t in times)
            or any(a >= b for a, b in zip(times, times[1:]) if type(a) is int and type(b) is int)):
        reject("observation_gap_or_order")
    phases = ("monitor_start", "seal", "exec_start", "exec_end", "cleanup", "final_snapshot", "monitor_stop")
    positions: dict[str, int] = {}
    for phase in phases:
        matches = [i for i, event in enumerate(events) if event.get("type") == phase]
        if len(matches) != 1:
            reject(f"missing_or_duplicate_{phase}")
        else:
            positions[phase] = matches[0]
    if len(positions) == len(phases) and [positions[p] for p in phases] != sorted(positions.values()):
        reject("observation_gap_or_order")

    for event in events:
        kind = event.get("type")
        if not isinstance(kind, str):
            reject("malformed_observation")
            continue
        if kind not in phases:
            reject("mutation_observed" if kind == "mutation" else "unknown_observation")
        if kind in {"exec_start", "exec_end", "cleanup"} and event.get("child_id") != trusted["child_id"]:
            reject("child_identity_mismatch")
        if kind in {"seal", "final_snapshot"}:
            objects = event.get("objects")
            if not isinstance(objects, dict) or set(objects) != set(expected):
                reject("snapshot_scope_mismatch")
                continue
            for name, pinned in expected.items():
                actual = objects[name]
                if not isinstance(actual, dict) or not isinstance(pinned, dict):
                    reject("malformed_object")
                    continue
                # Identity binds the source AND each declared ancestor, not just bytes.
                required = {"object_id", "kind", "symlink", "nlink"}
                if pinned.get("kind") == "file":
                    required.add("sha256")
                if not required <= pinned.keys() or not required <= actual.keys():
                    reject("incomplete_object_identity")
                if actual.get("symlink") is not False:
                    reject("symlink_substitution")
                if type(actual.get("nlink")) is not int or actual.get("nlink", 0) < 1:
                    reject("incomplete_object_identity")
                if actual.get("kind") == "file" and (type(actual.get("nlink")) is not int or actual["nlink"] != 1):
                    reject("hardlink_substitution")
                if actual != pinned:
                    reject("object_replaced")
        if kind in {"monitor_start", "monitor_stop"}:
            if type(event.get("loss_count")) is not int or event["loss_count"] != 0:
                reject("monitor_lost_events")
            if event.get("mechanism") != "synthetic-continuous-events":
                reject("continuous_monitor_unverified")
            scope = event.get("scope")
            if not isinstance(scope, list) or scope != sorted(expected):
                reject("monitor_scope_mismatch")
        if kind == "exec_end" and (event.get("reason") != "normal" or type(event.get("returncode")) is not int
                                    or event["returncode"] != 0):
            reject("execution_incomplete")
        if kind == "cleanup":
            if event.get("scope") != "whole_process_tree":
                reject("cleanup_scope_unverified")
            if event.get("descendants") != []:
                reject("descendants_not_cleared")
            if event.get("sockets") != []:
                reject("sockets_not_cleared")
    if reasons:
        status = "unsupported" if "untrusted_supervisor" in reasons else "unverified"
        return Verdict("rejected", status, tuple(reasons))
    return Verdict("accepted", "synthetic_only", ())


def adapt_producer(_receipt: object) -> Verdict:
    """Reserved integration point: protected_launch has no trusted producer yet."""
    return Verdict("rejected", "unsupported", ("producer_adapter_unavailable",))
