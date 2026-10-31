"""Synthetic acceptance contract, NOT a supervisor or real protection verifier.

The future producer adapter must authenticate the witness outside the child and
pin launch identity/objects independently. This fixture models that input only.
No paths are opened, processes launched, or host protection claims made here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Any


# Fixed probe topology; an adapter maps actual pinned paths to these roles.
# Arbitrary source trees require a separately reviewed topology contract.
REQUIRED_OBJECTS = {
    "parent:root": "directory",
    "parent:source": "directory",
    "source:tool.py": "file",
}
PARENTS = {
    "parent:root": None,
    "parent:source": "parent:root",
    "source:tool.py": "parent:source",
}
PHASES = ("monitor_start", "seal", "exec_start", "exec_end", "cleanup", "final_snapshot", "monitor_stop")


def identity(obj: dict[str, Any]) -> dict[str, Any]:
    """Complete object identity, excluding its separately validated graph edge."""
    return {key: value for key, value in obj.items() if key not in {"parent_ref", "parent_identity"}}


def _valid_ancestry(objects: dict[str, Any]) -> bool:
    if set(objects) != set(PARENTS) or any(not isinstance(obj, dict) for obj in objects.values()):
        return False
    for role, obj in objects.items():
        parent = PARENTS[role]
        if not {"parent_ref", "parent_identity"} <= obj.keys() or obj["parent_ref"] != parent:
            return False
        if parent is None:
            if obj["parent_identity"] is not None:
                return False
        else:
            pinned = identity(objects[parent])
            actual = obj["parent_identity"]
            if (objects[parent].get("kind") != "directory" or not isinstance(actual, dict)
                    or actual != pinned
                    or any(type(actual[key]) is not type(value) for key, value in pinned.items())):
                return False
    # The exact edge map above makes every role reach the independently pinned
    # root, excluding missing/extra edges, self/cycles and detached subtrees.
    return True


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
        fields = {"object_id", "kind", "symlink", "nlink", "parent_ref", "parent_identity"}
        if REQUIRED_OBJECTS[role] == "file":
            fields.add("sha256")
        if (not isinstance(obj, dict) or not _text(obj.get("object_id"))
                or set(obj) != fields
                or obj.get("kind") != REQUIRED_OBJECTS[role] or obj.get("symlink") is not False
                or type(obj.get("nlink")) is not int or obj["nlink"] < 1):
            return False
        identities.add(obj["object_id"])
        if obj["kind"] == "file" and (obj["nlink"] != 1 or not _digest(obj.get("sha256"))):
            return False
    return len(identities) == len(objects) and _valid_ancestry(objects)


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
    if evidence.get("adapter") != "synthetic-v3":
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
    _validate_controls(witness, trusted, reject)
    times = [e.get("time_ns") for e in events]
    if (any(type(e.get("sequence")) is not int for e in events)
            or [e.get("sequence") for e in events] != list(range(len(events)))
            or any(type(t) is not int or t < 0 for t in times)
            or any(a >= b for a, b in zip(times, times[1:]) if type(a) is int and type(b) is int)):
        reject("observation_gap_or_order")
    phases = PHASES
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
        if kind in phases:
            objects = event.get("objects")
            if not isinstance(objects, dict) or set(objects) != set(expected):
                reject("snapshot_scope_mismatch")
                continue
            if not _valid_ancestry(objects):
                reject("parent_ancestry_mismatch")
            for name, pinned in expected.items():
                actual = objects[name]
                if not isinstance(actual, dict) or not isinstance(pinned, dict):
                    reject("malformed_object")
                    continue
                # Identity binds the source AND each declared ancestor, not just bytes.
                required = {"object_id", "kind", "symlink", "nlink", "parent_ref", "parent_identity"}
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


def adapt_producer(receipt: object, *, evidence=None, trusted=None, tags=None,
                   producer_key=None, observer_key=None, consumed=None) -> Verdict:
    """Offline authenticated-envelope model; does not authenticate a real producer.

    The test authority must supply separate keys, expectations and replay state
    outside the producer and observer. A synthetic MAC proves only possession of
    those fixture keys, not process identity, isolation, or OS cleanup.
    """
    unsupported = Verdict("rejected", "unsupported", ("producer_adapter_unavailable",))
    if any(value is None for value in (evidence, trusted, tags, producer_key, observer_key, consumed)):
        return unsupported
    rejected = Verdict("rejected", "unverified", ("authentication_or_receipt_invalid",))
    fields = {"version", "run_id", "snapshot_digest", "runtime_digest", "policy_digest",
              "supervisor_identity", "child_identity", "namespace_identities", "preparation",
              "launch", "cleanup", "seal_status", "unsupported_reasons"}
    if (not isinstance(receipt, dict) or set(receipt) != fields or type(receipt["version"]) is not int
            or receipt["version"] != 1 or not isinstance(trusted, dict) or not isinstance(evidence, dict)
            or not isinstance(tags, dict) or set(tags) != {"producer", "observer"}
            or not isinstance(consumed, set) or not isinstance(producer_key, bytes)
            or not isinstance(observer_key, bytes) or len(producer_key) < 32
            or len(observer_key) < 32 or hmac.compare_digest(producer_key, observer_key)):
        return rejected
    if (receipt["run_id"] != trusted.get("run_id") or receipt["seal_status"] != "unsealed"
            or receipt["preparation"] != "diagnostic_ready" or receipt["launch"] != "exited_0"
            or receipt["cleanup"] != "broker_closed_socket_absent; descendants_unverified"
            or any(receipt[key] is not None for key in ("snapshot_digest", "runtime_digest", "policy_digest",
                                                          "supervisor_identity", "child_identity"))
            or receipt["namespace_identities"] != {} or not isinstance(receipt["unsupported_reasons"], list)
            or "diagnostic only; runtime and storage are unsealed" not in receipt["unsupported_reasons"]
            or not all(isinstance(reason, str) for reason in receipt["unsupported_reasons"])
            or not isinstance(evidence.get("child_receipt"), dict)
            or evidence["child_receipt"].get("run_id") != receipt["run_id"]):
        return rejected
    nonce = trusted.get("launch_nonce")
    run_id = trusted.get("run_id")
    if not _text(nonce) or not _text(run_id) or (run_id, nonce) in consumed:
        return rejected
    try:
        def encoded(domain, value):
            return json.dumps([domain, run_id, nonce, value], sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode()
        for role, key, value in (("producer", producer_key, receipt), ("observer", observer_key, evidence)):
            tag = tags[role]
            if not isinstance(tag, str) or not hmac.compare_digest(
                    tag, hmac.new(key, encoded(role, value), hashlib.sha256).hexdigest()):
                return rejected
    except (TypeError, ValueError):
        return rejected
    # Consume even rejected observations so a signed failure cannot be retried
    # with different evidence under the same independently issued nonce.
    consumed.add((run_id, nonce))
    return validate(evidence, trusted)


def _validate_controls(witness, trusted, reject):
    """Model external observer evidence; this is not an OS/authentication adapter.

    Ledger commitments come from the caller's independent synthetic observer,
    never from the child envelope. Hashes model tamper/replay detection only:
    authenticated transport and protected storage still need production seams.
    """
    events = witness["events"]
    actors = [trusted.get(key) for key in ("child_id", "writer_id", "broker_id", "witness_id")]
    actors.append(trusted["supervisor"]["subject_id"])
    if not all(_text(actor) for actor in actors) or len(set(a for a in actors if _text(a))) != len(actors):
        reject("control_identities_not_independent")
    if any(not _text(trusted.get(key)) for key in ("request_id", "token_id", "store_id", "clock_id")):
        reject("control_expectations_missing")
    deadline = trusted.get("deadline_ns")
    if type(deadline) is not int or deadline <= 0:
        reject("supervisor_deadline_unverified")
        deadline = -1

    expected_store = {
        "id": trusted.get("store_id"), "location": "outside_run", "mode": "append_only",
        "appenders": [trusted["witness_id"]], "rewriters": [], "deleters": [],
    }
    if witness.get("store") != expected_store:
        reject("external_append_only_unverified")
    for event in events:
        if (event.get("observer_id") != trusted["witness_id"]
                or event.get("sequence_source") != "observer"
                or event.get("time_source") != "observer_wall"
                or event.get("clock_id") != trusted.get("clock_id")):
            reject("observer_provenance_unverified")
    previous, hashes = "0" * 64, []
    try:
        for event in events:
            payload = [witness.get("run_id"), witness.get("launch_nonce"), previous, event]
            previous = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                 allow_nan=False).encode()).hexdigest()
            hashes.append(previous)
    except (TypeError, ValueError):
        reject("external_evidence_mismatch")
    expected_ledger = {
        "store_id": trusted.get("store_id"), "observer_id": trusted["witness_id"],
        "run_id": trusted["run_id"], "launch_nonce": trusted["launch_nonce"], "hashes": hashes,
    }
    if trusted.get("ledger") != expected_ledger:
        reject("external_evidence_mismatch")

    def phase(name):
        return next((event for event in events if event.get("type") == name), {})

    def mapping(value):
        return value if isinstance(value, dict) else {}

    seal = mapping(phase("seal").get("seal"))
    if (seal.get("writer_id") != trusted.get("writer_id")
            or seal.get("enforcement") != "synthetic_readonly_mount"
            or seal.get("handle_inventory") != "complete"
            or seal.get("retained_writable_handles") != []):
        reject("write_authority_not_revoked")
    if seal.get("attempts") != {kind: "denied" for kind in ("write", "rename", "link", "inherited_fd")}:
        reject("write_attempt_not_denied")
    start, end, cleanup = phase("exec_start"), phase("exec_end"), phase("cleanup")
    expected_deadline = {
        "request_id": trusted.get("request_id"), "supervisor_id": trusted["supervisor"]["subject_id"],
        "clock": "independent_wall", "clock_id": trusted.get("clock_id"), "expires_ns": deadline,
    }
    observed_deadline = mapping(start.get("deadline"))
    if observed_deadline != expected_deadline or type(observed_deadline.get("expires_ns")) is not int:
        reject("supervisor_deadline_unverified")
    broker = mapping(start.get("broker"))
    if (broker.get("id") != trusted.get("broker_id") or broker.get("state") != "ready"
            or broker.get("credential_holders") != [trusted.get("broker_id")]
            or broker.get("network_route") != "broker_only" or broker.get("direct_auth") is not False):
        reject("broker_boundary_unverified")
    token = mapping(broker.get("token"))
    expiry = token.get("expires_ns")
    start_time, end_time = start.get("time_ns"), end.get("time_ns")
    if (token.get("id") != trusted.get("token_id") or token.get("run_id") != trusted["run_id"]
            or token.get("audience") != trusted["child_id"] or token.get("scopes") != ["model:synthetic"]
            or type(expiry) is not int or type(start_time) is not int or type(end_time) is not int
            or not start_time < expiry <= deadline or end_time >= expiry):
        reject("broker_token_invalid")
    work = mapping(end.get("allowed_work"))
    if (work.get("read_object_id") != trusted["objects"]["source:tool.py"]["object_id"]
            or not _digest(work.get("output_sha256")) or work.get("output_scope") != "run"
            or work.get("completed") is not True):
        reject("positive_work_unverified")
    if (cleanup.get("inventory_source") != "independent_observer"
            or cleanup.get("inventory_scope") != "all_owned_resources"):
        reject("cleanup_inventory_unverified")
    if any(cleanup.get(resource) != [] for resource in
           ("descendants", "sockets", "mounts", "handles", "broker_endpoints", "active_tokens")):
        reject("cleanup_resources_not_cleared")

    failures = {"broker_death", "broker_timeout", "broker_transport_failure", "observer_death",
                "supervisor_interrupted", "deadline_expired", "mutation"}
    faults = [event for event in events if isinstance(event.get("type"), str) and event["type"] in failures]
    for fault in faults:
        reject(fault["type"] + "_observed")
    if type(end_time) is int and end_time >= deadline:
        reject("deadline_exceeded")
        if not any(fault["type"] == "deadline_expired" for fault in faults):
            faults.append({"type": "deadline_expired", "time_ns": deadline})
    # A reason-coded stop cannot substitute for independent cleanup evidence.
    for fault in faults:
        stops = [event for event in events if event.get("type") == "stop" and event.get("reason") == fault["type"]]
        if len(stops) != 1:
            reject("reasoned_stop_unverified")
            continue
        stop = stops[0]
        timing = [fault.get("time_ns"), stop.get("time_ns"), end_time, cleanup.get("time_ns")]
        if (not all(type(t) is int for t in timing) or not timing[0] <= timing[1] <= timing[2] < timing[3]
                or stop.get("child_id") != trusted["child_id"]
                or stop.get("supervisor_id") != trusted["supervisor"]["subject_id"]):
            reject("reasoned_stop_unverified")
