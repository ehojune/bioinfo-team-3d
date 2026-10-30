"""Reported REST/merge shape with fake identity, receipts and API; no processes."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

from scripts import ci_skip as cs
from tests.test_ci_skip_policy import API, archive, evidence

CHECKOUT = "f480761d132c5ac50d0dc9c1bf750874e6448e44"
TREE = "3decb2118e9fa6a290a0b123fc08c9eb2453d63e"
EVENT_BASE = "d0c834af1c52b8396ad2cf37dc27af69e6c90b76"
TESTED_BASE = "2ce877863945b514495963e66c1d261e30381ca8"
HEAD = "b842e5628286e5526cfe6e0f75420c5cd5fd5495"
RUN = 39461236144
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def drift(monkeypatch, tmp_path):
    from tests.test_ci_windows_phases import fake_context, make_receipts
    from scripts import ci_windows_phases as phases
    current = {"checkout_sha": CHECKOUT, "tree": TREE,
               "policy": {p: f'{i + 1:x}' * 40 for i, p in enumerate(cs.POLICY_FILES)}}
    repo = {"id": 1386409627, "full_name": cs.REPOSITORY}
    pr = {"number": 793, "base": {"ref": "main", "sha": EVENT_BASE, "repo": repo},
          "head": {"ref": "codex/ci-reuse-docs", "sha": HEAD, "repo": repo}}
    event = {"pull_request": pr}
    association = copy.deepcopy(pr)
    for side in ("base", "head"):
        association[side]["repo"] = {"id": repo["id"], "name": "bioinfo-team-3d",
                                     "url": "https://api.github.com/repos/" + cs.REPOSITORY}
    run = {"id": RUN, "run_attempt": 1, "workflow_id": 9, "path": cs.WORKFLOW,
           "event": "pull_request", "status": "in_progress", "conclusion": None,
           "head_sha": HEAD, "repository": repo, "head_repository": repo, "pull_requests": [association]}
    jobs = [{"name": n, "status": "completed", "conclusion": "success",
             "run_id": RUN, "run_attempt": 1, "head_sha": HEAD} for n in cs.REQUIRED_JOBS]
    jobs[-1].update(status="in_progress", conclusion=None)
    data = {
        "actions/workflows/test.yml": {"id": 9, "path": cs.WORKFLOW, "state": "active"},
        f"actions/runs/{RUN}": run,
        "git/commits/" + CHECKOUT: {"sha": CHECKOUT, "tree": {"sha": TREE},
                                  "parents": [{"sha": TESTED_BASE}, {"sha": HEAD}]},
        "pulls/793": {**copy.deepcopy(pr), "merge_commit_sha": CHECKOUT},
        "git/ref/heads/main": {"ref": "refs/heads/main", "object": {"type": "commit", "sha": TESTED_BASE}},
        f"compare/{EVENT_BASE}...{TESTED_BASE}": {
            "status": "ahead", "ahead_by": 2, "behind_by": 0, "total_commits": 2,
            "base_commit": {"sha": EVENT_BASE}, "merge_base_commit": {"sha": EVENT_BASE},
            "commits": [{"sha": "8" * 40}, {"sha": TESTED_BASE}]},
        f"actions/runs/{RUN}/attempts/1/jobs?per_page=100": {"total_count": len(jobs), "jobs": jobs},
    }
    calls = []
    def request(path):
        calls.append(path)
        return data[path]
    values = {"parents": f"{CHECKOUT} {TESTED_BASE} {HEAD}", "blob": current["policy"][cs.WORKFLOW]}
    def git(*args):
        if args == ("rev-list", "--parents", "-n", "1", "HEAD"):
            return values["parents"]
        assert args == ("rev-parse", CHECKOUT + ":" + cs.WORKFLOW)
        return values["blob"]
    needs = {n: {"result": "success", "outputs": {"tree": TREE, **({"profile": cs.COVERAGE[n]} if n in cs.COVERAGE else {})}}
             for n in (*cs.GATE_IDS, "changes", "readme", "patch-notes")}
    needs["changes"]["outputs"]["skip"] = "false"
    context = fake_context(current, checkout=CHECKOUT, head=HEAD)
    context.update(run_id=RUN, workflow_ref=f'{cs.REPOSITORY}/{cs.WORKFLOW}@refs/pull/793/merge')
    directory = tmp_path/'windows'
    proof = make_receipts(directory, context)
    monkeypatch.setenv('CI_WINDOWS_RECEIPT_DIR', str(directory))
    needs['pytest-windows']['outputs'].update(execution=phases.EXECUTION, receipt=phases.digest(proof))
    event_path, output = tmp_path / "event.json", tmp_path / "github-output"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    for key, value in {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": cs.REPOSITORY,
                       "GITHUB_SHA": CHECKOUT, "GITHUB_WORKFLOW_SHA": CHECKOUT,
                       "GITHUB_WORKFLOW_REF": f"{cs.REPOSITORY}/{cs.WORKFLOW}@refs/pull/793/merge",
                       "GITHUB_RUN_ID": str(RUN), "GITHUB_RUN_ATTEMPT": "1", "CI_NEEDS": json.dumps(needs),
                       "GITHUB_EVENT_PATH": str(event_path), "GITHUB_OUTPUT": str(output)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cs, "git", git)
    monkeypatch.setattr(cs, "api", request)
    monkeypatch.setattr(cs, "identity", lambda: current)
    monkeypatch.setattr(sys, "argv", ["ci_skip.py", "manifest"])
    return current, needs, event, values, data, calls, output


def test_independently_confirmed_actual_drift_finishes_without_a_reusable_manifest(drift):
    *_, calls, output = drift
    cs.main()
    assert output.read_text(encoding="utf-8") == "reusable=false\nreason=base-drift\n"
    assert not Path("ci-result.json").exists()
    assert len(calls) == 7


def test_bound_producer_still_writes_only_the_original_reusable_manifest(drift):
    current, _, event, values, _, calls, output = drift
    values["parents"] = f"{CHECKOUT} {EVENT_BASE} {HEAD}"
    cs.main()
    assert output.read_text(encoding="utf-8") == "reusable=true\nreason=bound\n"
    result = json.loads(Path("ci-result.json").read_text(encoding="utf-8"))
    assert result == cs.expected_manifest(
        {"id": RUN, "run_attempt": 1}, current, cs.pr_snapshot(event["pull_request"]), CHECKOUT, result['windows_execution'])
    assert not calls


@pytest.mark.parametrize("case", ["tree", "checkout", "ordered-parents", "missing-parent", "pr-merge",
    "pr-head", "pr-repository", "main", "main-type", "association", "run-head", "run-id", "attempt",
    "run-repository", "fork", "run-event", "run-state", "workflow", "workflow-path", "workflow-state",
    "compare-base", "compare-merge-base", "compare-head", "compare-status", "compare-partial",
    "missing-job", "duplicate-job", "failed-job", "cancelled-job", "job-attempt", "job-run", "job-head",
    "boolean-job-attempt", "compare-behind", "main-ref"])
def test_drift_does_not_mask_unbound_or_incomplete_evidence(drift, case):
    _, _, _, _, data, _, output = drift
    commit = data["git/commits/" + CHECKOUT]
    pr, main, run = data["pulls/793"], data["git/ref/heads/main"], data[f"actions/runs/{RUN}"]
    workflow = data["actions/workflows/test.yml"]
    compare = data[f"compare/{EVENT_BASE}...{TESTED_BASE}"]
    jobs = data[f"actions/runs/{RUN}/attempts/1/jobs?per_page=100"]
    if case == "tree": commit["tree"]["sha"] = "f" * 40
    elif case == "checkout": commit["sha"] = "f" * 40
    elif case == "ordered-parents": commit["parents"].reverse()
    elif case == "missing-parent": commit["parents"].pop()
    elif case == "pr-merge": pr["merge_commit_sha"] = "f" * 40
    elif case == "pr-head": pr["head"]["sha"] = "f" * 40
    elif case == "pr-repository": pr["base"]["repo"]["id"] = 999
    elif case == "main": main["object"]["sha"] = "f" * 40
    elif case == "main-type": main["object"]["type"] = "tag"
    elif case == "association": run["pull_requests"][0]["base"]["sha"] = TESTED_BASE
    elif case == "run-head": run["head_sha"] = "f" * 40
    elif case == "run-id": run["id"] += 1
    elif case == "attempt": run["run_attempt"] += 1
    elif case == "run-repository": run["repository"] = {"id": 999, "full_name": cs.REPOSITORY}
    elif case == "fork": run["head_repository"] = {"id": 999, "full_name": "other/fork"}
    elif case == "run-event": run["event"] = "workflow_dispatch"
    elif case == "run-state": run["status"] = "completed"
    elif case == "workflow": workflow["id"] += 1
    elif case == "workflow-path": workflow["path"] = ".github/workflows/other.yml"
    elif case == "workflow-state": workflow["state"] = "disabled_manually"
    elif case == "compare-base": compare["base_commit"]["sha"] = TESTED_BASE
    elif case == "compare-merge-base": compare["merge_base_commit"]["sha"] = TESTED_BASE
    elif case == "compare-head": compare["commits"][-1]["sha"] = "f" * 40
    elif case == "compare-status": compare["status"] = "diverged"
    elif case == "compare-partial": compare["total_commits"] += 1
    elif case == "missing-job": jobs["jobs"].pop()
    elif case == "duplicate-job": jobs["jobs"].append(copy.deepcopy(jobs["jobs"][0]))
    elif case == "failed-job": jobs["jobs"][0]["conclusion"] = "failure"
    elif case == "cancelled-job": jobs["jobs"][0]["conclusion"] = "cancelled"
    elif case == "job-attempt": jobs["jobs"][0]["run_attempt"] += 1
    elif case == "job-run": jobs["jobs"][0]["run_id"] += 1
    elif case == "job-head": jobs["jobs"][0]["head_sha"] = "f" * 40
    elif case == "boolean-job-attempt": jobs["jobs"][0]["run_attempt"] = True
    elif case == "compare-behind": compare["behind_by"] = 1
    elif case == "main-ref": main["ref"] = "refs/heads/other"
    with pytest.raises(ValueError, match="checkout-parents"):
        cs.main()
    assert not output.exists() and not Path("ci-result.json").exists()


@pytest.mark.parametrize("case", ["unavailable", "malformed", "unexpected-error", "wrong-local-head",
                                  "reversed-local-parents", "extra-local-parent"])
def test_uncertain_or_malformed_drift_proof_never_emits_no_reuse(drift, monkeypatch, case):
    _, _, _, values, data, calls, output = drift
    if case in {"unavailable", "unexpected-error"}:
        error = OSError if case == "unavailable" else RuntimeError
        def broken(*args):
            raise error("proof unavailable")
        monkeypatch.setattr(cs, "api", broken)
    elif case == "malformed":
        data[f"actions/runs/{RUN}"] = {}
    elif case == "wrong-local-head": values["parents"] = f"{CHECKOUT} {TESTED_BASE} {'f' * 40}"
    elif case == "reversed-local-parents": values["parents"] = f"{CHECKOUT} {HEAD} {TESTED_BASE}"
    else: values["parents"] += " " + "f" * 40
    with pytest.raises(RuntimeError if case == "unexpected-error" else ValueError):
        cs.main()
    assert not output.exists() and not Path("ci-result.json").exists()
    if "local" in case:
        assert not calls


def test_wrong_blob_and_incomplete_gate_still_fail_instead_of_no_reuse(drift, monkeypatch):
    _, needs, _, values, _, _, output = drift
    values["blob"] = "f" * 40
    with pytest.raises(ValueError, match="workflow-blob"):
        cs.main()
    needs["pytest"]["result"] = "skipped"
    monkeypatch.setenv("CI_NEEDS", json.dumps(needs))
    with pytest.raises(ValueError, match="incomplete full gate"):
        cs.main()
    assert not output.exists()


def test_arbitrary_manifest_errors_are_not_converted_to_success(drift, monkeypatch):
    def broken(*args):
        raise ValueError("unrelated failure")
    monkeypatch.setattr(cs, "produce_manifest", broken)
    with pytest.raises(ValueError, match="unrelated failure"):
        cs.main()


def test_no_reuse_cannot_upload_a_stray_old_manifest(drift):
    path = Path("ci-result.json")
    path.write_text("old file must not be uploaded", encoding="utf-8")
    cs.main()
    assert path.read_text(encoding="utf-8") == "old file must not be uploaded"
    workflow = yaml.safe_load((ROOT / cs.WORKFLOW).read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validation-manifest"]["steps"]
    produce = next(s for s in steps if s.get("id") == "manifest")
    assert produce["env"]["GH_TOKEN"] == "${{ github.token }}"
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["if"] == "steps.manifest.outputs.reusable == 'true'"


@pytest.mark.parametrize("kind", ["absent", "not-a-manifest"])
def test_consumer_runs_full_without_reusable_producer_evidence(evidence, kind):
    api = API(evidence)
    api.zip = archive({"reusable": False, "reason": "base-drift"})
    api.artifact["digest"] = "sha256:" + hashlib.sha256(api.zip).hexdigest()
    def request(path, binary=False):
        if kind == "absent" and path.endswith("/artifacts?per_page=100"):
            return {"total_count": 0, "artifacts": []}
        return api(path, binary)
    from datetime import datetime, timezone
    assert not cs.reuse_main(api.identity, request, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


MAIN_NOW = "e" * 40


def main_moved_ahead(data, status="ahead", behind_by=0, base=TESTED_BASE, merge_base=TESTED_BASE):
    """main moved on after the checkout parent was taken: the extra index commit that lands during a run."""
    data["git/ref/heads/main"]["object"]["sha"] = MAIN_NOW
    data[f"compare/{TESTED_BASE}...{MAIN_NOW}"] = {
        "status": status, "ahead_by": 1, "behind_by": behind_by, "total_commits": 1,
        "base_commit": {"sha": base}, "merge_base_commit": {"sha": merge_base}, "commits": [{"sha": MAIN_NOW}]}


def test_main_moving_ahead_of_the_checkout_parent_is_still_recognised_as_base_drift(drift):
    """#1073: a main that is the parent plus later commits is the same forward drift, not an unbound checkout."""
    *_, data, calls, output = drift
    main_moved_ahead(data)
    cs.main()
    assert output.read_text(encoding="utf-8") == "reusable=false\nreason=base-drift\n"
    assert not Path("ci-result.json").exists()
    assert f"compare/{TESTED_BASE}...{MAIN_NOW}" in calls and len(calls) == 8


@pytest.mark.parametrize("case", ["diverged", "behind", "behind-by", "base-mismatch", "merge-base-mismatch", "unrelated",
                                  "partial-count", "partial-tail", "missing-commits", "empty-commits", "boolean-count"])
def test_a_main_that_is_not_ahead_of_the_checkout_parent_stays_an_error(drift, case):
    *_, data, _, output = drift
    if case == "unrelated":
        data["git/ref/heads/main"]["object"]["sha"] = MAIN_NOW  # no compare answer: it cannot be proved ahead
    elif case == "diverged": main_moved_ahead(data, status="diverged")
    elif case == "behind": main_moved_ahead(data, status="behind")
    elif case == "behind-by": main_moved_ahead(data, behind_by=1)
    elif case == "base-mismatch": main_moved_ahead(data, base=EVENT_BASE)
    elif case == "merge-base-mismatch": main_moved_ahead(data, merge_base=EVENT_BASE)
    else:
        main_moved_ahead(data)
        moved = data[f"compare/{TESTED_BASE}...{MAIN_NOW}"]
        if case == "partial-count": moved["total_commits"] = 2  # more commits exist than were returned
        elif case == "partial-tail": moved["commits"][-1]["sha"] = "f" * 40  # the answer does not end at main
        elif case == "missing-commits": del moved["commits"]
        elif case == "empty-commits": moved.update(total_commits=0, ahead_by=0, commits=[])
        else: moved["total_commits"] = True
    with pytest.raises(ValueError, match="checkout-parents"):
        cs.main()
    assert not output.exists() and not Path("ci-result.json").exists()


def test_a_main_that_is_not_a_commit_sha_is_refused_without_asking_the_api_about_it(drift):
    *_, data, calls, output = drift
    data["git/ref/heads/main"]["object"]["sha"] = "main"
    with pytest.raises(ValueError, match="checkout-parents"):
        cs.main()
    assert not any(path.startswith(f"compare/{TESTED_BASE}...") for path in calls)
