"""No git processes or network: CI decisions use synthetic trees and API receipts."""
import copy
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts import ci_skip as cs


def sha(char):
    return char * 40


@pytest.fixture
def evidence():
    identity = {"checkout_sha": sha("a"), "tree": sha("b"),
                "policy": {p: sha(str(i + 1)) for i, p in enumerate(cs.POLICY_FILES)}}
    repo = {"id": 123, "full_name": cs.REPOSITORY}
    pr = {"number": 788, "merged_at": "2026-10-20T00:00:00Z", "merge_commit_sha": sha("a"),
          "base": {"ref": "main", "repo": repo, "sha": sha("c")},
          "head": {"repo": repo, "sha": sha("d")}}
    run = {"id": 42, "run_attempt": 1, "workflow_id": 9, "path": cs.WORKFLOW,
           "event": "pull_request", "status": "completed", "conclusion": "success",
           "head_sha": sha("e"), "repository": repo, "head_repository": repo,
           "updated_at": "2026-10-20T00:00:00Z", "pull_requests": [pr]}
    manifest = {"version": cs.VERSION, "repository": cs.REPOSITORY, "run_id": 42,
                "attempt": 1, "event": "pull_request", "checkout_sha": sha("e"),
                "workflow_sha": sha("e"), "tree": identity["tree"], "policy": identity["policy"],
                "gates": {name: {"result": "success", "tree": identity["tree"]}
                          for name in cs.GATE_IDS}}
    jobs = [{"name": name, "status": "completed", "conclusion": "success", "run_attempt": 1,
             "run_id": 42, "head_sha": sha("e")}
            for name in cs.REQUIRED_JOBS]
    commit = {"sha": sha("e"), "tree": {"sha": identity["tree"]},
              "parents": [{"sha": sha("c")}, {"sha": sha("d")}]}
    return identity, pr, run, manifest, jobs, commit


def archive(manifest, name="ci-result.json", extra=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(name, json.dumps(manifest))
        if extra:
            z.writestr("run.py", "raise SystemExit('must never execute')")
    return stream.getvalue()


class API:
    def __init__(self, evidence):
        self.identity, self.pr, self.run, self.manifest, self.jobs, self.commit = evidence
        self.zip = archive(self.manifest)
        self.artifact = {"id": 7, "name": "ci-full-v1-42-1", "expired": False,
                         "size_in_bytes": len(self.zip),
                         "digest": "sha256:" + hashlib.sha256(self.zip).hexdigest()}
        self.calls = []

    def __call__(self, path, binary=False):
        self.calls.append(path)
        if path.endswith("/pulls"):
            return [self.pr]
        if path == "actions/workflows/test.yml":
            return {"id": 9, "path": cs.WORKFLOW, "state": "active"}
        if path.startswith("actions/workflows/9/runs?"):
            return {"workflow_runs": [self.run]}
        if path == "git/commits/" + sha("e"):
            return self.commit
        if path.startswith("contents/"):
            name = path.removeprefix("contents/").split("?")[0]
            return {"type": "file", "sha": self.identity["policy"][name]}
        if path == "actions/runs/42/attempts/1/jobs?per_page=100":
            return {"total_count": len(self.jobs), "jobs": self.jobs}
        if path == "actions/runs/42/artifacts?per_page=100":
            return {"total_count": 1, "artifacts": [self.artifact]}
        if path == "actions/artifacts/7/zip" and binary:
            return self.zip
        raise AssertionError(f"Unexpected API: {path}")


def reuse(api):
    return cs.reuse_main(api.identity, api, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


def test_exact_synthetic_merge_tree_can_be_reused_after_squash(evidence):
    api = API(evidence)
    assert api.identity["checkout_sha"] != api.run["head_sha"] != api.pr["head"]["sha"]
    assert reuse(api)
    assert "git/commits/" + api.run["head_sha"] in api.calls
    assert any("attempts/1/jobs" in path for path in api.calls)


@pytest.mark.parametrize("target,key,value", [
    ("run", "conclusion", "failure"), ("run", "conclusion", "cancelled"),
    ("run", "status", "in_progress"), ("run", "event", "workflow_dispatch"),
    ("run", "path", ".github/workflows/other.yml"), ("run", "workflow_id", 99),
    ("run", "updated_at", "2026-10-01T00:00:00Z"), ("run", "updated_at", "2099-01-01T00:00:00Z"),
    ("run", "run_attempt", 2), ("pr", "merged_at", None),
    ("pr", "merge_commit_sha", sha("f")), ("artifact", "expired", True),
    ("artifact", "digest", "sha256:" + "0" * 64), ("artifact", "name", "ci-full-v1-42-0"),
])
def test_untrusted_or_incomplete_run_falls_back(evidence, target, key, value):
    api = API(evidence)
    getattr(api, target)[key] = value
    assert not reuse(api)


@pytest.mark.parametrize("case", ["fork", "repository", "partial", "skipped", "cancelled", "duplicate",
                                  "tree", "commit-tree", "parent", "workflow", "policy", "artifact",
                                  "traversal", "extra-file", "wrong-version", "wrong-run", "bad-json"])
def test_negative_evidence(evidence, case):
    api = API(evidence)
    if case == "fork":
        api.run["head_repository"] = {"id": 999, "full_name": "someone/fork"}
    elif case == "repository":
        api.run["repository"] = {"id": 999, "full_name": "someone/other"}
    elif case == "partial":
        api.jobs.pop()
    elif case in {"skipped", "cancelled"}:
        api.jobs[0]["conclusion"] = case
    elif case == "duplicate":
        api.jobs.append(copy.deepcopy(api.jobs[0]))
    elif case == "tree":
        api.manifest["tree"] = sha("f")
    elif case == "commit-tree":
        api.commit["tree"]["sha"] = sha("f")
    elif case == "parent":
        api.commit["parents"][1]["sha"] = sha("f")
    elif case == "workflow":
        api.manifest["workflow_sha"] = sha("f")
    elif case == "policy":
        api.manifest["policy"] = {p: sha("f") for p in cs.POLICY_FILES}
    elif case == "artifact":
        api.manifest["gates"]["clean-install"]["result"] = "skipped"
    elif case == "wrong-version":
        api.manifest["version"] += 1
    elif case == "wrong-run":
        api.manifest["run_id"] += 1
    api.zip = archive(api.manifest, name="../ci-result.json" if case == "traversal" else "ci-result.json",
                      extra=case == "extra-file")
    if case == "bad-json":
        api.zip = b"not a zip"
    api.artifact["digest"] = "sha256:" + hashlib.sha256(api.zip).hexdigest()
    assert not reuse(api)


def test_changed_trusted_base_policy_cannot_self_certify(evidence):
    api = API(evidence)
    def request(path, binary=False):
        if path.startswith("contents/"):
            return {"type": "file", "sha": sha("f")}
        return api(path, binary)
    assert not cs.reuse_main(api.identity, request, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("path", ["README.md", "README.en.md", "docs/status/progress.md", "docs/guide.md"])
def test_prose_documents(path):
    assert cs.document_change(path, "# Guide\nOld prose.\n", "# Guide\nNew prose.\n")


@pytest.mark.parametrize("path", ["AGENTS.md", "HANDOFF.md", ".github/workflows/test.yml", "pyproject.toml",
    "config/example.yaml", "agents/core/example.yaml", "tests/example.md", "labhq/example.md",
    "docs/example.py", "docs/examples/usage.md", "requirements.txt", "docs/policy.md"])
def test_paths_fail_closed(path):
    assert not cs.document_change(path, "old", "new")


@pytest.mark.parametrize("example", ["```python\nprint(1)\n```", "~~~sh\necho hi\n~~~", "    echo hi",
    "Run `python demo.py`.", "<script>demo()</script>", ".. code-block:: python", "$ python demo.py",
    ">>> demo()", "python demo.py", "<!-- doctest -->"])
def test_any_executable_example_runs_full_even_if_only_prose_changed(example):
    assert not cs.document_change("docs/guide.md", example + "\nold", example + "\nnew")


@pytest.mark.parametrize("example", ["echo hello", "Remove-Item example.txt", "Python demo.py", "print('hi')",
                                      "./demo --example", "> echo hi", "#!/bin/bash", "ENV=value",
                                      "import os", "custom-command argument", "timeout: 20",
                                      "## python demo.py", "- echo hello.", "1. git status.",
                                      "## mode: enforce", "- labhq doctor."])
def test_unfenced_commands_also_run_full(example):
    assert not cs.document_change("docs/guide.md", "Old prose.", example)


def test_record_yaml_is_data_only():
    old = "pr: null\nrows: []\n"
    new = "pr: 788\nrows:\n- sha: abcdef0\n  at: '2026-10-20 12:00'\n  text: Prose update\n"
    assert cs.document_change("patch_notes/entries/example.yaml", old, new)
    for bad in ("rows: []\nrun: echo hi\n", "!!python/object:example {}", "pr: null\nrows: [true]",
                "pr: null\nrows: []\nrows: []\n"):
        assert not cs.document_change("patch_notes/entries/example.yaml", old, bad)
    assert not cs.document_change("patch_notes/config.yaml", old, new)


def test_fake_git_checks_entire_diff_and_modes(monkeypatch):
    raw = ":100644 100644 old new M\0docs/guide.md\0"
    def git(*args):
        if args[0] == "diff":
            return raw
        if args[0] == "show":
            return "Plain prose.\n"
        raise AssertionError(args)
    monkeypatch.setattr(cs, "git", git)
    assert cs.can_skip("head", "base")
    for mode in ("100755", "120000", "160000"):
        raw = f":100644 {mode} old new M\0docs/guide.md\0"
        assert not cs.can_skip("head", "base")
    raw = ":100644 100644 old new M\0docs/guide.md\0:100644 100644 a b M\0labhq/app.py\0"
    assert not cs.can_skip("head", "base")
    raw = ""
    assert not cs.can_skip("head", "base")


def test_api_failure_defaults_full(evidence):
    def unavailable(*args, **kwargs):
        raise TimeoutError("synthetic timeout")
    assert not cs.reuse_main(evidence[0], unavailable)


def test_never_reuse_an_older_success_past_a_new_failed_or_running_run(evidence):
    api = API(evidence)
    for conclusion in ("failure", "cancelled", None):
        latest = copy.deepcopy(api.run)
        latest["id"] = 99
        latest["conclusion"] = conclusion
        def request(path, binary=False):
            if path.startswith("actions/workflows/9/runs?"):
                return {"workflow_runs": [latest, api.run]}
            return api(path, binary)
        assert not cs.reuse_main(api.identity, request, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


def test_a_recent_failed_rerun_of_an_older_run_also_prevents_reuse(evidence):
    api = API(evidence)
    rerun = copy.deepcopy(api.run)
    rerun.update(id=1, run_attempt=2, conclusion="failure", updated_at="2026-10-20T00:30:00Z")
    def request(path, binary=False):
        if path.startswith("actions/workflows/9/runs?"):
            return {"workflow_runs": [api.run, rerun]}
        return api(path, binary)
    assert not cs.reuse_main(api.identity, request, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("case", ["no-pr", "old-pr-head", "fork-pr", "base-ref", "base-repo", "missing-digest",
                                  "jobs-truncated", "artifacts-truncated", "job-wrong-tree", "duplicate-artifact"])
def test_other_provenance_gaps(evidence, case):
    api = API(evidence)
    if case == "no-pr":
        api.run["pull_requests"] = []
    elif case == "old-pr-head":
        api.run["pull_requests"] = copy.deepcopy(api.run["pull_requests"])
        api.run["pull_requests"][0]["head"]["sha"] = sha("f")
    elif case == "fork-pr":
        api.pr["head"]["repo"] = {"id": 999, "full_name": "someone/fork"}
    elif case == "base-ref":
        api.pr["base"]["ref"] = "untrusted"
    elif case == "base-repo":
        api.pr["base"]["repo"] = {"id": 999, "full_name": "someone/other"}
    elif case == "missing-digest":
        del api.artifact["digest"]
    elif case == "job-wrong-tree":
        api.jobs[0]["head_sha"] = sha("f")
    def request(path, binary=False):
        result = api(path, binary)
        if case == "jobs-truncated" and "/jobs?" in path:
            result["total_count"] += 1
        if case == "artifacts-truncated" and "/artifacts?" in path:
            result["total_count"] += 1
        if case == "duplicate-artifact" and "/artifacts?" in path:
            result = {"total_count": 2, "artifacts": [api.artifact, api.artifact]}
        return result
    assert not cs.reuse_main(api.identity, request, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


def test_event_decisions_bind_actual_checkout_and_entire_push(monkeypatch, evidence):
    current = evidence[0]
    monkeypatch.setenv("GITHUB_SHA", current["checkout_sha"])
    seen = []
    monkeypatch.setattr(cs, "can_skip", lambda head, base: seen.append((head, base)) or True)
    monkeypatch.setattr(cs, "reuse_main", lambda current: False)
    base, head = sha("c"), sha("d")
    monkeypatch.setattr(cs, "git", lambda *args: f"{current['checkout_sha']} {base} {head}")
    event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    assert cs.decide("pull_request", event, current) == "documents"
    assert seen == [(current["checkout_sha"], base)]
    event["pull_request"]["base"]["sha"] = sha("f")
    assert cs.decide("pull_request", event, current) == "full"
    seen.clear()
    assert cs.decide("push", {"before": base, "ref": "refs/heads/main"}, current) == "documents"
    assert seen == [(current["checkout_sha"], base)]  # entire push, not HEAD^1
    for name, event in (("workflow_dispatch", {}), ("push", {"ref": "refs/heads/main", "before": "0" * 40}),
                        ("push", {"ref": "refs/heads/feature", "before": base})):
        assert cs.decide(name, event, current) == "full"
    monkeypatch.setenv("GITHUB_SHA", sha("f"))
    assert cs.decide("push", {"before": base, "ref": "refs/heads/main"}, current) == "full"


def test_manifest_requires_fresh_full_gates_on_the_checkout(monkeypatch, evidence):
    current = evidence[0]
    for key, value in {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": current["checkout_sha"],
                       "GITHUB_REPOSITORY": cs.REPOSITORY,
                       "GITHUB_WORKFLOW_SHA": current["checkout_sha"], "GITHUB_RUN_ID": "42",
                       "GITHUB_RUN_ATTEMPT": "1"}.items():
        monkeypatch.setenv(key, value)
    needs = {name: {"result": "success", "outputs": {"tree": current["tree"]}}
             for name in (*cs.GATE_IDS, "changes", "readme", "patch-notes")}
    needs["changes"]["outputs"]["skip"] = "false"
    assert cs.produce_manifest(current, needs)["tree"] == current["tree"]
    for gate in cs.GATE_IDS:
        for bad_result in ("failure", "cancelled", "skipped"):
            changed = copy.deepcopy(needs)
            changed[gate]["result"] = bad_result
            with pytest.raises(ValueError):
                cs.produce_manifest(current, changed)
        changed = copy.deepcopy(needs)
        changed[gate]["outputs"]["tree"] = sha("f")
        with pytest.raises(ValueError):
            cs.produce_manifest(current, changed)
    needs["changes"]["outputs"]["skip"] = "true"
    with pytest.raises(ValueError):
        cs.produce_manifest(current, needs)


def test_artifact_is_bounded_data_and_redirect_carries_no_credential():
    req = cs.urllib.request.Request("https://api.github.com/example", headers={"Authorization": "synthetic"})
    redirect = cs.ArtifactRedirect().redirect_request(req, None, 302, "", {},
                                                     "https://store.blob.core.windows.net/example")
    assert not redirect.has_header("Authorization")
    for url in ("http://store.blob.core.windows.net/example", "https://evil.example/data",
                "https://store.blob.core.windows.net.evil.example/data"):
        with pytest.raises(ValueError):
            cs.ArtifactRedirect().redirect_request(req, None, 302, "", {}, url)
    with pytest.raises(ValueError):
        cs.manifest_json(archive({"oversized": "x" * 33000}))
    with pytest.raises(ValueError):
        cs.manifest_json(b"x" * (cs.MAX_BYTES + 1))


def test_workflow_preserves_all_required_gates_and_pins_checkout():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / cs.WORKFLOW).read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert jobs["pytest"]["strategy"]["matrix"]["python-version"] == ["3.10", "3.12"]
    assert jobs["pytest"]["runs-on"] == "ubuntu-latest"
    assert jobs["pytest-windows"]["runs-on"] == jobs["clean-install"]["runs-on"] == "windows-latest"
    windows = jobs["pytest-windows"]["steps"]
    assert any(s.get("with", {}).get("python-version") == "3.12" for s in windows)
    assert any(s.get("run", "").startswith("pytest -q --durations=15") for s in windows)
    assert any(s.get("run") == "python scripts/clean_install_check.py" for s in jobs["clean-install"]["steps"])
    assert jobs["ro-crate-validator"]["env"] == {"LABHQ_ROCRATE_VALIDATOR": "1"}
    assert any(s.get("run") == "pytest -q tests/test_request_bundle.py -k official_ro_crate_validator"
               for s in jobs["ro-crate-validator"]["steps"])
    assert jobs["pytest-slow"]["env"] == {"LABHQ_SLOW_TESTS": "1"}
    assert any(s.get("run") == "pytest -q tests/test_long_mcp_call.py" for s in jobs["pytest-slow"]["steps"])
    for name in (*cs.GATE_IDS, "changes", "readme", "validation-manifest"):
        checkouts = [s for s in jobs[name]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        assert len(checkouts) == 1 and checkouts[0]["with"]["ref"] == "${{ github.sha }}"
    for name in cs.GATE_IDS:
        assert jobs[name]["if"] == "needs.changes.outputs.skip != 'true'"
        assert jobs[name]["outputs"]["tree"] == "${{ steps.identity.outputs.tree }}"
    assert "if" not in jobs["readme"]
    producer = jobs["validation-manifest"]
    assert set(producer["needs"]) == {*cs.GATE_IDS, "changes", "readme", "patch-notes"}
    assert "needs.changes.outputs.skip != 'true'" in producer["if"]
    assert any(s.get("uses", "").startswith("actions/upload-artifact@v4") for s in producer["steps"])
