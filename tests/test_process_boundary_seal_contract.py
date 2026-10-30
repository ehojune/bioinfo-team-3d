"""Independent, data-only acceptance cases; never launch the proposed supervisor."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parent / "fixtures" / "process_boundary_seal"
SPEC = importlib.util.spec_from_file_location("seal_fixture_validator", ROOT / "validator.py")
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)
CORPUS = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
CONTROLS = json.loads((ROOT / "controls.json").read_text(encoding="utf-8"))["cases"]


def record_external_ledger(evidence, trusted):
    """Test-only observer recording, never called by validate or a child adapter."""
    witness = evidence["witness"]
    previous, hashes = "0" * 64, []
    for event in witness["events"]:
        payload = [witness["run_id"], witness["launch_nonce"], previous, event]
        previous = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        hashes.append(previous)
    trusted["ledger"] = {
        "store_id": trusted["store_id"], "observer_id": witness["observer_id"],
        "run_id": witness["run_id"], "launch_nonce": witness["launch_nonce"], "hashes": hashes,
    }


def materialize(case):
    """Apply data mutations without passing expected labels to the validator."""
    evidence = deepcopy(CORPUS["positive"])
    for change in case["changes"]:
        parent = evidence
        for part in change["path"][:-1]:
            parent = parent[part]
        key = change["path"][-1]
        if change.get("remove"):
            del parent[key]
        elif "insert" in change:
            parent.insert(key, deepcopy(change["insert"]))
            for i, event in enumerate(parent):
                event["sequence"] = i
        else:
            parent[key] = deepcopy(change["value"])
    return evidence


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_acceptance_corpus(case):
    evidence = materialize(case)
    before = deepcopy(evidence)
    result = validator.validate(evidence, deepcopy(CORPUS["trusted"]))
    assert result.contract == case["expected"]["contract"]
    assert result.status == case["expected"]["status"]
    if case["expected"]["reason"]:
        assert case["expected"]["reason"] in result.reasons
    else:
        assert result.reasons == ()
    assert result.protection == "unverified"
    assert evidence == before


def test_corpus_is_unique_and_every_negative_changes_valid_observations():
    assert CORPUS["version"] == 3
    cases = CORPUS["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    positive = CORPUS["positive"]
    assert validator.validate(positive, CORPUS["trusted"]).contract == "accepted"
    for case in cases[1:]:
        evidence = materialize(case)
        assert evidence != positive
        assert validator.validate(evidence, CORPUS["trusted"]).contract == "rejected"


@pytest.mark.parametrize("phase", ["seal", "monitor_start", "exec_start", "exec_end", "cleanup", "final_snapshot", "monitor_stop"])
def test_removing_any_required_observation_rejects(phase):
    evidence = deepcopy(CORPUS["positive"])
    evidence["witness"]["events"] = [e for e in evidence["witness"]["events"] if e["type"] != phase]
    result = validator.validate(evidence, CORPUS["trusted"])
    assert result.contract == "rejected"
    assert f"missing_or_duplicate_{phase}" in result.reasons


@pytest.mark.parametrize("phase", validator.PHASES)
@pytest.mark.parametrize("resource", list(CORPUS["trusted"]["objects"]))
def test_mutating_each_pinned_identity_at_either_endpoint_rejects(phase, resource):
    evidence = deepcopy(CORPUS["positive"])
    event = next(e for e in evidence["witness"]["events"] if e["type"] == phase)
    event["objects"][resource]["object_id"] += ":replaced"
    assert "object_replaced" in validator.validate(evidence, CORPUS["trusted"]).reasons


def test_matching_endpoint_hashes_do_not_erase_a_mid_execution_write():
    case = next(c for c in CORPUS["cases"] if c["id"] == "transient_mutation_restored")
    evidence = materialize(case)
    events = evidence["witness"]["events"]
    assert (next(e["objects"] for e in events if e["type"] == "seal")
            == next(e["objects"] for e in events if e["type"] == "final_snapshot"))
    assert "mutation_observed" in validator.validate(evidence, CORPUS["trusted"]).reasons


@pytest.mark.parametrize("receipt", [None, {}, {"status": "PASS", "trusted": True}, {"status": "FAILED"}])
def test_child_receipt_cannot_supply_or_override_witness(receipt):
    evidence = deepcopy(CORPUS["positive"])
    evidence["child_receipt"] = receipt
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "accepted"
    evidence.pop("witness")
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "rejected"
    assert validator.adapt_producer(receipt).status == "unsupported"


@pytest.mark.parametrize("mutation", ["late_start", "early_stop", "duplicate_phase", "lost_sequence", "unknown_event", "missing_parent", "missing_cleanup", "bad_event"])
def test_incomplete_or_ambiguous_observations_reject(mutation):
    evidence = deepcopy(CORPUS["positive"])
    events = evidence["witness"]["events"]
    if mutation == "late_start":
        events[0], events[1] = events[1], events[0]
    elif mutation == "early_stop":
        events[4], events[6] = events[6], events[4]
    elif mutation == "duplicate_phase":
        events.append(deepcopy(events[0]))
    elif mutation == "lost_sequence":
        events[3]["sequence"] = 99
    elif mutation == "unknown_event":
        events.append({"sequence": 7, "time_ns": 70, "type": "new_unreviewed_kind"})
    elif mutation == "missing_parent":
        del events[5]["objects"]["parent:root"]
    elif mutation == "missing_cleanup":
        del events[4]["descendants"]
    else:
        events[1] = None
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "rejected"


def test_caller_launch_expectations_are_independent_of_receipt():
    evidence = deepcopy(CORPUS["positive"])
    for key in ("run_id", "launch_nonce", "witness_id", "supervisor", "objects"):
        trusted = deepcopy(CORPUS["trusted"])
        del trusted[key]
        assert validator.validate(evidence, trusted).contract == "rejected"


@pytest.mark.parametrize("value", [None, ["bad"], {}, True, "bad"])
@pytest.mark.parametrize("path", [
    ["witness", "events"], ["witness", "events", 0, "type"],
    ["witness", "events", 0, "sequence"], ["witness", "events", 0, "time_ns"],
    ["witness", "events", 0, "scope"], ["witness", "events", 0, "loss_count"],
    ["witness", "events", 1, "objects"], ["witness", "events", 3, "returncode"],
    ["witness", "events", 4, "descendants"], ["witness", "events", 4, "sockets"],
    ["witness", "events", 2, "child_id"], ["witness", "events", 3, "child_id"],
    ["witness", "events", 4, "child_id"],
])
def test_malformed_required_observations_fail_closed(path, value):
    evidence = deepcopy(CORPUS["positive"])
    parent = evidence
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "rejected"


def test_witness_identity_cannot_be_the_supervisor_or_child_even_if_pinned():
    for subject in (CORPUS["trusted"]["child_id"], CORPUS["trusted"]["supervisor"]["subject_id"]):
        trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
        trusted["witness_id"] = evidence["witness"]["observer_id"] = subject
        assert "witness_not_independent" in validator.validate(evidence, trusted).reasons


@pytest.mark.parametrize("key", ["run_id", "launch_nonce", "witness_id", "child_id", "supervisor", "objects"])
@pytest.mark.parametrize("value", [None, [], {}, True])
def test_malformed_launch_expectations_fail_closed(key, value):
    trusted = deepcopy(CORPUS["trusted"])
    trusted[key] = value
    result = validator.validate(CORPUS["positive"], trusted)
    assert result.contract == "rejected"
    assert result.protection == "unverified"


def test_consistent_independent_rebinding_accepts_a_different_run():
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    witness = evidence["witness"]
    for key in ("run_id", "launch_nonce"):
        trusted[key] = witness[key] = "different-" + trusted[key]
    trusted["witness_id"] = witness["observer_id"] = "different-external-observer"
    trusted["child_id"] = "different-child"
    trusted["supervisor"]["subject_id"] = "different-supervisor"
    trusted["supervisor"]["executable_sha256"] = "d" * 64
    witness["supervisor"] = deepcopy(trusted["supervisor"])
    for obj in trusted["objects"].values():
        obj["object_id"] += ":new"
        if "sha256" in obj:
            obj["sha256"] = "e" * 64
    for obj in trusted["objects"].values():
        if obj["parent_ref"] is not None:
            obj["parent_identity"] = validator.identity(trusted["objects"][obj["parent_ref"]])
    for event in witness["events"]:
        event["observer_id"] = trusted["witness_id"]
        if "objects" in event:
            event["objects"] = deepcopy(trusted["objects"])
        if "child_id" in event:
            event["child_id"] = trusted["child_id"]
    witness["store"]["appenders"] = [trusted["witness_id"]]
    witness["events"][2]["deadline"]["supervisor_id"] = trusted["supervisor"]["subject_id"]
    witness["events"][2]["broker"]["token"].update(run_id=trusted["run_id"], audience=trusted["child_id"])
    witness["events"][3]["allowed_work"]["read_object_id"] = trusted["objects"]["source:tool.py"]["object_id"]
    record_external_ledger(evidence, trusted)
    assert validator.validate(evidence, trusted).contract == "accepted"
    # A receipt cannot replay against independently pinned expectations for another run.
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "rejected"


@pytest.mark.parametrize("phase", ["exec_start", "exec_end", "cleanup", "trusted"])
def test_child_substitution_rejects_even_with_matching_run_and_nonce(phase):
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    if phase == "trusted":
        trusted["child_id"] = "substituted-child"
    else:
        event = next(e for e in evidence["witness"]["events"] if e["type"] == phase)
        event["child_id"] = "substituted-child"
    result = validator.validate(evidence, trusted)
    assert result.contract == "rejected"
    assert "child_identity_mismatch" in result.reasons


def test_supervisor_cannot_be_the_child_even_when_all_events_agree():
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    trusted["child_id"] = trusted["supervisor"]["subject_id"]
    for event in evidence["witness"]["events"]:
        if "child_id" in event:
            event["child_id"] = trusted["child_id"]
    result = validator.validate(evidence, trusted)
    assert result.contract == "rejected"
    assert result.reasons == ("malformed_launch_expectations",)


@pytest.mark.parametrize("first,second", list(combinations(CORPUS["trusted"]["objects"], 2)))
def test_pinned_objects_cannot_alias_even_when_snapshots_agree(first, second):
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    trusted["objects"][second]["object_id"] = trusted["objects"][first]["object_id"]
    for event in evidence["witness"]["events"]:
        if "objects" in event:
            event["objects"] = deepcopy(trusted["objects"])
    result = validator.validate(evidence, trusted)
    assert result.contract == "rejected"
    assert result.reasons == ("malformed_launch_expectations",)


@pytest.mark.parametrize("role", list(CORPUS["trusted"]["objects"]))
@pytest.mark.parametrize("mutation", ["remove", "wrong_kind"])
def test_required_source_and_ancestor_roles_cannot_be_weakened_coherently(role, mutation):
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    if mutation == "remove":
        del trusted["objects"][role]
    else:
        obj = trusted["objects"][role]
        if obj["kind"] == "file":
            obj.update(kind="directory", nlink=2)
            del obj["sha256"]
        else:
            obj.update(kind="file", nlink=1, sha256="f" * 64)
    for event in evidence["witness"]["events"]:
        if "objects" in event:
            event["objects"] = deepcopy(trusted["objects"])
        if event["type"] in {"monitor_start", "monitor_stop"}:
            event["scope"] = sorted(trusted["objects"])
    assert validator.validate(evidence, trusted).contract == "rejected"


ANCESTRY_CASES = [case for case in CORPUS["cases"] if case["id"] in {
    "wrong_parent_identity", "valid_identity_under_wrong_parent", "missing_parent_edge",
    "self_parent_edge", "cyclic_parent_graph", "detached_subtree", "extra_parent_edge",
    "coherent_root_substitution", "transient_relationship_restored",
}]


@pytest.mark.parametrize("phase", validator.PHASES)
@pytest.mark.parametrize("case", ANCESTRY_CASES, ids=lambda case: case["id"])
def test_ancestry_mutations_reject_at_every_phase(case, phase):
    evidence = deepcopy(CORPUS["positive"])
    mutated = materialize(case)["witness"]["events"][2]["objects"]
    event = next(e for e in evidence["witness"]["events"] if e["type"] == phase)
    event["objects"] = mutated
    result = validator.validate(evidence, CORPUS["trusted"])
    assert result.contract == "rejected"
    assert case["expected"]["reason"] in result.reasons
    if phase not in {"seal", "final_snapshot"}:
        assert evidence["witness"]["events"][1]["objects"] == evidence["witness"]["events"][5]["objects"]


@pytest.mark.parametrize("case", [c for c in ANCESTRY_CASES if c["id"] != "coherent_root_substitution"],
                         ids=lambda case: case["id"])
def test_agreement_with_malformed_trusted_graph_does_not_grant_acceptance(case):
    trusted, evidence = deepcopy(CORPUS["trusted"]), materialize(case)
    trusted["objects"] = deepcopy(evidence["witness"]["events"][2]["objects"])
    for event in evidence["witness"]["events"]:
        event["objects"] = deepcopy(trusted["objects"])
    assert validator.validate(evidence, trusted).reasons == ("malformed_launch_expectations",)


@pytest.mark.parametrize("phase", validator.PHASES)
def test_every_phase_requires_ancestry_observation(phase):
    evidence = deepcopy(CORPUS["positive"])
    next(e for e in evidence["witness"]["events"] if e["type"] == phase).pop("objects")
    assert "snapshot_scope_mismatch" in validator.validate(evidence, CORPUS["trusted"]).reasons


@pytest.mark.parametrize("role", list(CORPUS["trusted"]["objects"]))
@pytest.mark.parametrize("key", ["parent_ref", "parent_identity"])
@pytest.mark.parametrize("value", [[], {}, True, 1, "unknown-role", None])
def test_malformed_parent_fields_fail_closed(role, key, value):
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    if trusted["objects"][role][key] == value:
        return  # The root's two explicit nulls are the sole permitted null edges.
    trusted["objects"][role][key] = value
    for event in evidence["witness"]["events"]:
        event["objects"] = deepcopy(trusted["objects"])
    assert validator.validate(evidence, trusted).contract == "rejected"


def test_v1_adapter_cannot_claim_v2_ancestry_contract():
    evidence = deepcopy(CORPUS["positive"])
    evidence["adapter"] = "synthetic-v1"
    assert validator.validate(evidence, CORPUS["trusted"]).status == "unsupported"


@pytest.mark.parametrize("phase", validator.PHASES)
@pytest.mark.parametrize("role", list(CORPUS["trusted"]["objects"]))
@pytest.mark.parametrize("key,value", [("symlink", True), ("nlink", 99), ("kind", "socket")])
def test_leaf_and_ancestor_links_and_types_are_pinned_in_every_phase(phase, role, key, value):
    evidence = deepcopy(CORPUS["positive"])
    event = next(e for e in evidence["witness"]["events"] if e["type"] == phase)
    event["objects"][role][key] = value
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "rejected"


@pytest.mark.parametrize("role", ["parent:source", "source:tool.py"])
@pytest.mark.parametrize("key", ["object_id", "kind", "symlink", "nlink"])
def test_parent_identity_requires_every_pinned_field(role, key):
    trusted = deepcopy(CORPUS["trusted"])
    del trusted["objects"][role]["parent_identity"][key]
    assert validator.validate(CORPUS["positive"], trusted).reasons == ("malformed_launch_expectations",)


def test_parent_identity_equality_is_type_strict():
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    trusted["objects"]["parent:source"]["parent_identity"]["symlink"] = 0
    for event in evidence["witness"]["events"]:
        event["objects"] = deepcopy(trusted["objects"])
    assert validator.validate(evidence, trusted).reasons == ("malformed_launch_expectations",)


@pytest.mark.parametrize("case", CONTROLS, ids=lambda case: case["id"])
@pytest.mark.parametrize("recorded_by_observer", [False, True])
def test_v3_controls_reject_even_when_independently_recorded(case, recorded_by_observer):
    evidence, trusted = materialize(case), deepcopy(CORPUS["trusted"])
    if recorded_by_observer:
        record_external_ledger(evidence, trusted)
    before = deepcopy((evidence, trusted))
    result = validator.validate(evidence, trusted)
    assert result.contract == "rejected"
    assert case["reason"] in result.reasons
    assert result.protection == "unverified"
    assert (evidence, trusted) == before
    assert validator.validate(CORPUS["positive"], CORPUS["trusted"]).contract == "accepted"


@pytest.mark.parametrize("field", ["observer_id", "run_id", "launch_nonce", "store_id", "hashes"])
def test_external_ledger_is_required_and_cannot_be_replaced_by_child(field):
    evidence, trusted = deepcopy(CORPUS["positive"]), deepcopy(CORPUS["trusted"])
    evidence["child_receipt"]["ledger"] = deepcopy(trusted["ledger"])
    del trusted["ledger"][field]
    assert "external_evidence_mismatch" in validator.validate(evidence, trusted).reasons


def test_replayed_or_rewritten_events_cannot_replace_external_commitments():
    trusted, evidence = deepcopy(CORPUS["trusted"]), deepcopy(CORPUS["positive"])
    evidence["witness"]["events"][3]["allowed_work"]["output_sha256"] = "d" * 64
    assert "external_evidence_mismatch" in validator.validate(evidence, trusted).reasons
    # Independent re-recording of valid different output passes; a child cannot do this.
    record_external_ledger(evidence, trusted)
    assert validator.validate(evidence, trusted).contract == "accepted"
    trusted["run_id"] = evidence["witness"]["run_id"] = "fresh-run"
    assert "external_evidence_mismatch" in validator.validate(evidence, trusted).reasons


@pytest.mark.parametrize("failure", ["broker_death", "broker_timeout", "broker_transport_failure",
                                     "observer_death", "supervisor_interrupted", "deadline_expired", "mutation"])
@pytest.mark.parametrize("with_stop", [False, True])
def test_post_launch_failures_require_reasoned_stop_and_independent_cleanup(failure, with_stop):
    evidence, trusted = deepcopy(CORPUS["positive"]), deepcopy(CORPUS["trusted"])
    events = evidence["witness"]["events"]
    fault_time = trusted["deadline_ns"] if failure == "deadline_expired" else 25
    fault = {"type": failure, "time_ns": fault_time, "observer_id": trusted["witness_id"],
             "sequence_source": "observer", "time_source": "observer_wall", "clock_id": trusted["clock_id"]}
    events.insert(3, fault)
    if with_stop:
        events.insert(4, {**fault, "type": "stop", "reason": failure, "time_ns": fault_time + 1,
                          "child_id": trusted["child_id"], "supervisor_id": trusted["supervisor"]["subject_id"]})
    for index, event in enumerate(events):
        event["sequence"] = index
        if failure == "deadline_expired" and event["type"] in ("exec_end", "cleanup", "final_snapshot", "monitor_stop"):
            event["time_ns"] += 10
    record_external_ledger(evidence, trusted)
    result = validator.validate(evidence, trusted)
    assert failure + "_observed" in result.reasons
    assert ("reasoned_stop_unverified" in result.reasons) is not with_stop
    assert result.contract == "rejected" and result.protection == "unverified"
    if with_stop:
        events[6]["handles"] = ["retained-fd"]
        record_external_ledger(evidence, trusted)
        assert "cleanup_resources_not_cleared" in validator.validate(evidence, trusted).reasons


def test_deadline_expiry_is_detected_without_self_reported_timeout():
    evidence, trusted = deepcopy(CORPUS["positive"]), deepcopy(CORPUS["trusted"])
    for event in evidence["witness"]["events"][3:]:
        event["time_ns"] += 10
    record_external_ledger(evidence, trusted)
    result = validator.validate(evidence, trusted)
    assert "deadline_exceeded" in result.reasons
    assert "reasoned_stop_unverified" in result.reasons


@pytest.mark.parametrize("actor", ["child_id", "writer_id", "broker_id"])
def test_observer_cannot_share_an_actor_identity_even_when_ledger_agrees(actor):
    evidence, trusted = deepcopy(CORPUS["positive"]), deepcopy(CORPUS["trusted"])
    trusted["witness_id"] = evidence["witness"]["observer_id"] = trusted[actor]
    evidence["witness"]["store"]["appenders"] = [trusted[actor]]
    for event in evidence["witness"]["events"]:
        event["observer_id"] = trusted[actor]
    record_external_ledger(evidence, trusted)
    assert validator.validate(evidence, trusted).contract == "rejected"


def test_labels_cannot_turn_missing_control_into_acceptance():
    evidence = deepcopy(CORPUS["positive"])
    evidence.update(expected={"contract": "rejected"}, status="FAIL", protection="PASS")
    assert validator.validate(evidence, CORPUS["trusted"]).contract == "accepted"
    del evidence["witness"]["events"][1]["seal"]
    evidence["expected"] = {"contract": "accepted"}
    assert "write_authority_not_revoked" in validator.validate(evidence, CORPUS["trusted"]).reasons


@pytest.mark.parametrize("value", [None, [], {}, True, 0, "bad"])
@pytest.mark.parametrize("path", [
    ["witness", "store"], ["witness", "events", 1, "seal"],
    ["witness", "events", 2, "deadline"], ["witness", "events", 2, "broker"],
    ["witness", "events", 2, "broker", "token"], ["witness", "events", 3, "allowed_work"],
    ["witness", "events", 4, "mounts"], ["witness", "events", 4, "handles"],
    ["witness", "events", 4, "broker_endpoints"], ["witness", "events", 4, "active_tokens"],
])
def test_malformed_v3_controls_fail_closed(path, value):
    case = {"changes": [{"path": path, "value": value}]}
    evidence, trusted = materialize(case), deepcopy(CORPUS["trusted"])
    record_external_ledger(evidence, trusted)
    result = validator.validate(evidence, trusted)
    # Empty inventories are valid observations; all other malformed inputs reject.
    if path[-1] in {"mounts", "handles", "broker_endpoints", "active_tokens"} and value == []:
        assert result.contract == "accepted"
    else:
        assert result.contract == "rejected" and result.reasons


@pytest.mark.parametrize("key", ["writer_id", "broker_id", "request_id", "token_id", "store_id", "clock_id", "deadline_ns", "ledger"])
@pytest.mark.parametrize("value", [None, [], {}, True, ""])
def test_v3_trusted_fields_cannot_be_missing_or_malformed(key, value):
    trusted = deepcopy(CORPUS["trusted"])
    trusted[key] = value
    result = validator.validate(CORPUS["positive"], trusted)
    assert result.contract == "rejected" and result.reasons


@pytest.mark.parametrize("adapter", ["synthetic-v1", "synthetic-v2", "producer-v3"])
def test_legacy_or_real_adapter_cannot_bypass_v3_controls(adapter):
    evidence = deepcopy(CORPUS["positive"])
    evidence["adapter"] = adapter
    assert validator.validate(evidence, CORPUS["trusted"]).status == "unsupported"
