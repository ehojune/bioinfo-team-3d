"""Bounded manifest diagnostics with fake git only; no CI or process execution."""
import copy

import pytest

from scripts import ci_skip as cs


@pytest.fixture
def producer(monkeypatch):
    sha = lambda char: char * 40
    current = {"checkout_sha": sha("a"), "tree": sha("b"),
               "policy": {path: sha(str(i + 1)) for i, path in enumerate(cs.POLICY_FILES)}}
    repo = {"id": 123, "full_name": cs.REPOSITORY}
    event = {"pull_request": {"number": 793,
             "base": {"ref": "main", "sha": sha("c"), "repo": repo},
             "head": {"ref": "feature", "sha": sha("d"), "repo": repo}}}
    needs = {name: {"result": "success", "outputs": {"tree": current["tree"], **({"profile": cs.COVERAGE[name]} if name in cs.COVERAGE else {})}}
             for name in (*cs.GATE_IDS, "changes", "readme", "patch-notes")}
    needs["changes"]["outputs"]["skip"] = "false"
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": cs.REPOSITORY,
           "GITHUB_SHA": current["checkout_sha"], "GITHUB_WORKFLOW_SHA": current["checkout_sha"],
           "GITHUB_WORKFLOW_REF": f"{cs.REPOSITORY}/{cs.WORKFLOW}@refs/pull/793/merge",
           "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    git_results = {"parents": f"{sha('a')} {sha('c')} {sha('d')}",
                   "blob": current["policy"][cs.WORKFLOW]}
    calls = []
    def git(*args):
        calls.append(args)
        if args == ("rev-list", "--parents", "-n", "1", "HEAD"):
            return git_results["parents"]
        assert args == ("rev-parse", current["checkout_sha"] + ":" + cs.WORKFLOW)
        return git_results["blob"]
    monkeypatch.setattr(cs, "git", git)
    return current, needs, event, git_results, calls


def test_bound_identity_and_all_receipts_still_produce_the_same_manifest(producer):
    current, needs, event, _, calls = producer
    result = cs.produce_manifest(current, needs, event)
    assert result == cs.expected_manifest({"id": 42, "run_attempt": 1}, current,
                                         cs.pr_snapshot(event["pull_request"]), current["checkout_sha"])
    assert len(calls) == 2


@pytest.mark.parametrize("case,reason", [
    ("checkout-shape", "checkout-sha"), ("run", "run-id"), ("attempt", "run-attempt"),
    ("workflow-sha", "workflow-sha"), ("workflow-ref", "workflow-ref"),
    ("one-parent", "checkout-parents"), ("reverse-parents", "checkout-parents"),
    ("base-parent", "checkout-parents"), ("head-parent", "checkout-parents"),
    ("blob", "workflow-blob"),
])
def test_each_identity_failure_names_its_exact_failed_check(monkeypatch, producer, case, reason):
    current, needs, event, values, calls = producer
    if case == "checkout-shape":
        current["checkout_sha"] = "not-a-sha"
        monkeypatch.setenv("GITHUB_SHA", current["checkout_sha"])
    elif case in {"run", "attempt"}:
        monkeypatch.setenv("GITHUB_RUN_ID" if case == "run" else "GITHUB_RUN_ATTEMPT", "0")
    elif case.startswith("workflow-"):
        monkeypatch.setenv("GITHUB_WORKFLOW_SHA" if case.endswith("sha") else "GITHUB_WORKFLOW_REF",
                           "untrusted-value-that-must-not-be-logged")
    elif case == "blob":
        values["blob"] = "f" * 40
    else:
        parts = values["parents"].split()
        if case == "one-parent":
            parts.pop()
        elif case == "reverse-parents":
            parts[1:] = reversed(parts[1:])
        else:
            parts[1 if case == "base-parent" else 2] = "f" * 40
        values["parents"] = " ".join(parts)
    with pytest.raises(ValueError, match="unbound checkout/workflow identity: " + reason) as error:
        cs.produce_manifest(current, needs, event)
    assert len(str(error.value)) < 200
    assert "untrusted-value" not in str(error.value)
    if case not in {"one-parent", "reverse-parents", "base-parent", "head-parent", "blob"}:
        assert not calls  # no git lookup of an unbound workflow revision


@pytest.mark.parametrize("source,role", [("d", "pr-head"), ("c", "pr-base"), ("f", "other")])
def test_source_roles_are_diagnostic_only_not_new_allow_branches(monkeypatch, producer, source, role):
    current, needs, event, _, calls = producer
    monkeypatch.setenv("GITHUB_WORKFLOW_SHA", source * 40)
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", f"{cs.REPOSITORY}/{cs.WORKFLOW}@refs/heads/feature")
    with pytest.raises(ValueError) as error:
        cs.produce_manifest(current, needs, event)
    assert str(error.value) == ("unbound checkout/workflow identity: workflow-sha, workflow-ref "
                                f"(source={role}, ref=pr-head)")
    assert not calls


@pytest.mark.parametrize("gate", [*cs.GATE_IDS, "readme", "patch-notes"])
def test_identity_diagnostics_do_not_bypass_any_gate(producer, gate):
    current, needs, event, _, calls = producer
    changed = copy.deepcopy(needs)
    changed[gate]["result"] = "skipped"
    with pytest.raises(ValueError, match="incomplete full gate receipts"):
        cs.produce_manifest(current, changed, event)
    assert not calls


@pytest.mark.parametrize("gate", cs.GATE_IDS)
@pytest.mark.parametrize("profile", [None, "windows-full-v1", "serial-legacy"])
def test_coverage_receipts_must_match_every_expected_gate(producer, gate, profile):
    current, needs, event, _, calls = producer
    needs[gate]["outputs"]["profile"] = profile
    with pytest.raises(ValueError, match="coverage profile receipts"):
        cs.produce_manifest(current, needs, event)
    assert not calls


def test_parent_mismatch_stays_denied_and_logs_only_bounded_public_shas(producer, capsys):
    current, needs, event, values, _ = producer
    values["parents"] = f"{current['checkout_sha']} {'f' * 40} {'d' * 40} arbitrary-private-value"
    with pytest.raises(ValueError, match="checkout-parents"):
        cs.produce_manifest(current, needs, event)
    output = capsys.readouterr().err
    assert '"length": 4' in output and '"expected":' in output
    assert 'f' * 40 in output and 'c' * 40 in output
    assert "arbitrary-private-value" not in output and len(output) < 500
