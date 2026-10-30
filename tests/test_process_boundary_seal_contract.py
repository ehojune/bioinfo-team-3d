"""Independent, data-only acceptance cases; never launch the proposed supervisor."""
from __future__ import annotations

from copy import deepcopy
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
    assert CORPUS["version"] == 1
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


@pytest.mark.parametrize("phase", ["seal", "final_snapshot"])
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
    for event in witness["events"]:
        if "objects" in event:
            event["objects"] = deepcopy(trusted["objects"])
        if "child_id" in event:
            event["child_id"] = trusted["child_id"]
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
