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
    association = copy.deepcopy(pr)
    for side in ("base", "head"):
        association[side]["repo"] = {"id": 123, "name": "bioinfo-team-3d",
                                      "url": "https://api.github.com/repos/" + cs.REPOSITORY}
    run = {"id": 42, "run_attempt": 1, "workflow_id": 9, "path": cs.WORKFLOW,
           "event": "pull_request", "status": "completed", "conclusion": "success",
           "head_sha": sha("d"), "repository": repo, "head_repository": repo,
           "updated_at": "2026-10-20T00:00:00Z", "pull_requests": [association]}
    manifest = {"version": cs.VERSION, "repository": cs.REPOSITORY, "run_id": 42,
                "attempt": 1, "event": "pull_request", "checkout_sha": sha("e"),
                "workflow_sha": sha("e"), "tree": identity["tree"], "policy": identity["policy"],
                "workflow_ref": cs.REPOSITORY + "/" + cs.WORKFLOW + "@refs/pull/788/merge",
                "workflow_blob": identity["policy"][cs.WORKFLOW],
                "pr": {"number": 788, "head_sha": sha("d"), "base_sha": sha("c"), "repository_id": 123},
                "gates": {name: {"result": "success", "tree": identity["tree"]}
                          for name in cs.GATE_IDS}}
    jobs = [{"name": name, "status": "completed", "conclusion": "success", "run_attempt": 1,
             "run_id": 42, "head_sha": sha("d")}
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
        self.artifact = {"id": 7, "name": f"ci-full-v{cs.VERSION}-42-1", "expired": False,
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
        if path == "git/commits/" + sha("d"):
            # Real REST run.head_sha is the branch commit, often with ONE parent.
            return {"sha": sha("d"), "tree": {"sha": sha("9")}, "parents": [{"sha": sha("8")}]}
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
    assert api.run["head_sha"] == api.pr["head"]["sha"]
    assert "full_name" not in api.run["pull_requests"][0]["base"]["repo"]
    assert api.manifest["checkout_sha"] != api.run["head_sha"]
    assert api.identity["checkout_sha"] != api.manifest["checkout_sha"]  # squash on main
    assert reuse(api)
    assert "git/commits/" + api.manifest["checkout_sha"] in api.calls
    assert any("attempts/1/jobs" in path for path in api.calls)


@pytest.mark.parametrize("target,key,value", [
    ("run", "conclusion", "failure"), ("run", "conclusion", "cancelled"),
    ("run", "status", "in_progress"), ("run", "event", "workflow_dispatch"),
    ("run", "path", ".github/workflows/other.yml"), ("run", "workflow_id", 99),
    ("run", "updated_at", "2026-10-01T00:00:00Z"), ("run", "updated_at", "2099-01-01T00:00:00Z"),
    ("run", "run_attempt", 2), ("pr", "merged_at", None),
    ("pr", "merge_commit_sha", sha("f")), ("artifact", "expired", True),
    ("artifact", "digest", "sha256:" + "0" * 64), ("artifact", "name", f"ci-full-v{cs.VERSION}-42-0"),
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


def test_native_status_fixture_remains_document_only_without_running_git(monkeypatch):
    def git(*args):
        if args[0] == "diff":
            return ":000000 100644 zero new A\0docs/status/2026-10-02-1000-pr.md\0"
        assert args == ("show", "head:docs/status/2026-10-02-1000-pr.md")
        return "## done\n"
    monkeypatch.setattr(cs, "git", git)
    assert cs.can_skip("head", "base")


@pytest.mark.parametrize("path", ["AGENTS.md", "HANDOFF.md", ".github/workflows/test.yml", "pyproject.toml",
    "config/example.yaml", "agents/core/example.yaml", "tests/example.md", "labhq/example.md",
    "docs/example.py", "docs/examples/usage.md", "requirements.txt", "docs/policy.md", "docs/AGENTS.md",
    "docs/HANDOFF.md", "docs/manual.md", "docs/runtime.md", "docs/config.md", "docs/tests.md"])
def test_paths_fail_closed(path):
    assert not cs.document_change(path, "old", "new")


@pytest.mark.parametrize("example", ["```python\nprint(1)\n```", "~~~sh\necho hi\n~~~", "    echo hi",
    "Run `python demo.py`.", "<script>demo()</script>", ".. code-block:: python", "$ python demo.py",
    ">>> demo()", "python demo.py", "<!-- doctest -->"])
def test_added_or_deleted_executable_examples_run_full(example):
    assert not cs.document_change("docs/guide.md", "Old prose.\n", example + "\nNew prose.\n")
    assert not cs.document_change("docs/guide.md", example + "\nOld prose.\n", "New prose.\n")


@pytest.mark.parametrize("example", ["```python\nprint(1)\n```", "~~~sh\necho hi\n~~~", "    echo hi",
    "Run `python demo.py`.", "<script>demo()</script>", ".. code-block:: python", "$ python demo.py",
    ">>> demo()", "python demo.py", "<!-- doctest -->"])
def test_any_executable_example_runs_full_even_if_only_prose_changed(example):
    # Keep the original negative: bare old/new fragments remain ambiguous.
    # The new positive cases above use complete prose around stable examples.
    assert not cs.document_change("docs/guide.md", example + "\nold", example + "\nnew")


@pytest.mark.parametrize("example", ["```python\nprint(1)\n```", "~~~sh\necho hi\n~~~",
                                     "    echo hi\n", "Run `python demo.py`."])
def test_prose_with_unchanged_executable_examples_is_document_only(example):
    assert cs.document_change("README.md", "Old prose.\n\n" + example,
                              "New prose.\n\n" + example)
    assert not cs.document_change("README.md", "Old prose.\n\n" + example,
                                  "New prose.\n\n" + example.replace("hi", "bye").replace("1", "2").replace("demo", "other"))


def test_actual_readme_prose_edit_preserves_existing_examples():
    before = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "```" in before and "`" in before
    sentence = "단계마다 점검하고 근거를 다시 확인하도록 남깁니다."
    assert sentence in before
    after = before.replace(sentence, "단계마다 점검하고 남긴 근거를 다시 확인합니다.", 1)
    assert cs.document_change("README.md", before, after)


@pytest.mark.parametrize("text", ["```python\nprint(1)", "```", "Text with `unclosed code.",
                                  "<!-- unclosed comment", "<p>Unclosed HTML.", ".. code-block:: python",
                                  "> ```sh\n> echo hi\n> ```"])
def test_ambiguous_markdown_is_full_even_with_only_prose_changes(text):
    assert not cs.document_change("README.md", "Old prose.\n\n" + text,
                                  "New prose.\n\n" + text)


@pytest.mark.parametrize("path", ["STATUS.md", "patch_notes/README.md"])
def test_generated_markdown_prose_with_unchanged_identifiers(path):
    before = "<!-- generated -->\n# Progress\n\n| Head | Result |\n|---|---|\n| `abcdef0` | Old result. |\n"
    assert cs.document_change(path, before, before.replace("Old result.", "New result."))
    # New/changed inline identifiers are deliberately not assumed non-executable.
    assert not cs.document_change(path, before, before.replace("abcdef0", "1234567"))


@pytest.mark.parametrize("before,after", [
    ("```sh\necho hi\n```", "```python\necho hi\n```"),
    ("Run ``echo hi``.", "Run `echo hi`."),
    ("Run `python\ndemo.py`.", "Run `python demo.py`."),
    ("Run ` python demo.py `.", "Run `python demo.py`."),
    ("[Guide](guide.md)", "[Guide](other.md)"),
    ("[Guide][ref]\n\n[ref]: guide.md\n", "[Guide][ref]\n\n[ref]: other.md\n"),
    ("<!-- stable -->", "<!-- changed -->"),
])
def test_changed_protected_markup_is_full(before, after):
    assert not cs.document_change("README.md", "Old prose.\n\n" + before, "New prose.\n\n" + after)


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
    event = {"pull_request": evidence[1]}
    for key, value in {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": current["checkout_sha"],
                       "GITHUB_REPOSITORY": cs.REPOSITORY,
                       "GITHUB_WORKFLOW_REF": cs.REPOSITORY + "/" + cs.WORKFLOW + "@refs/pull/788/merge",
                       "GITHUB_WORKFLOW_SHA": current["checkout_sha"], "GITHUB_RUN_ID": "42",
                       "GITHUB_RUN_ATTEMPT": "1"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cs, "git", lambda *args: (
        f"{current['checkout_sha']} {sha('c')} {sha('d')}" if args[0] == "rev-list"
        else current["policy"][cs.WORKFLOW]))
    needs = {name: {"result": "success", "outputs": {"tree": current["tree"]}}
             for name in (*cs.GATE_IDS, "changes", "readme", "patch-notes")}
    needs["changes"]["outputs"]["skip"] = "false"
    manifest = cs.produce_manifest(current, needs, event)
    assert manifest["tree"] == current["tree"]
    assert manifest["pr"]["head_sha"] == evidence[2]["head_sha"]
    assert manifest["checkout_sha"] != manifest["pr"]["head_sha"]
    for gate in cs.GATE_IDS:
        for bad_result in ("failure", "cancelled", "skipped"):
            changed = copy.deepcopy(needs)
            changed[gate]["result"] = bad_result
            with pytest.raises(ValueError):
                cs.produce_manifest(current, changed, event)
        changed = copy.deepcopy(needs)
        changed[gate]["outputs"]["tree"] = sha("f")
        with pytest.raises(ValueError):
            cs.produce_manifest(current, changed, event)
    needs["changes"]["outputs"]["skip"] = "true"
    with pytest.raises(ValueError):
        cs.produce_manifest(current, needs, event)


@pytest.mark.parametrize("case", ["checkout", "coherent-checkout", "branch-as-checkout", "base-as-checkout", "workflow-ref",
                                  "workflow-blob", "snapshot-head", "snapshot-base", "snapshot-number",
                                  "snapshot-repository", "attempt", "run-head", "one-parent", "reverse-parents",
                                  "wrong-base-parent", "source-blob", "missing-gate", "boolean-attempt", "old-manifest",
                                  "workflow-base-commit", "association-url"])
def test_real_rest_shape_identity_tampering_runs_full(evidence, case):
    api = API(evidence)
    if case in {"checkout", "coherent-checkout"}:
        api.manifest["checkout_sha"] = sha("f")
        if case == "coherent-checkout":
            api.manifest["workflow_sha"] = sha("f")
    elif case in {"branch-as-checkout", "base-as-checkout"}:
        api.manifest["checkout_sha"] = sha("d") if case == "branch-as-checkout" else sha("c")
        api.manifest["workflow_sha"] = api.manifest["checkout_sha"]
    elif case == "workflow-ref":
        api.manifest["workflow_ref"] = api.manifest["workflow_ref"].replace("788", "789")
    elif case == "workflow-blob":
        api.manifest["workflow_blob"] = sha("f")
    elif case.startswith("snapshot-"):
        key = {"head": "head_sha", "base": "base_sha", "number": "number", "repository": "repository_id"}[case[9:]]
        api.manifest["pr"][key] = sha("f") if key.endswith("sha") else 999
    elif case == "attempt":
        api.manifest["attempt"] = 2
    elif case == "run-head":
        api.run["head_sha"] = sha("f")
    elif case == "one-parent":
        api.commit["parents"].pop()
    elif case == "reverse-parents":
        api.commit["parents"].reverse()
    elif case == "wrong-base-parent":
        api.commit["parents"][0]["sha"] = sha("f")
    elif case == "missing-gate":
        del api.manifest["gates"]["pytest-slow"]
    elif case == "boolean-attempt":
        api.manifest["attempt"] = True
    elif case == "old-manifest":
        api.manifest["version"] = 1
    elif case == "workflow-base-commit":
        api.manifest["workflow_sha"] = sha("c")
    elif case == "association-url":
        api.run["pull_requests"][0]["base"]["repo"]["url"] = "https://api.github.com/repos/someone/fork"
    api.zip = archive(api.manifest)
    api.artifact["digest"] = "sha256:" + hashlib.sha256(api.zip).hexdigest()
    def request(path, binary=False):
        if case == "source-blob" and path.startswith("contents/") and path.endswith(sha("e")):
            return {"type": "file", "sha": sha("f")}
        if case == "coherent-checkout" and path == "git/commits/" + sha("f"):
            return api.commit  # API contradicts the altered producer identity
        return api(path, binary)
    assert not cs.reuse_main(api.identity, request, now=datetime(2026, 10, 20, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("case", ["checkout", "workflow-sha", "workflow-ref", "workflow-blob", "parents",
                                  "head", "base", "fork", "attempt", "run", "readme", "patch-notes"])
def test_producer_rejects_unbound_identity_or_incomplete_receipts(monkeypatch, evidence, case):
    current, pr = evidence[:2]
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": cs.REPOSITORY,
           "GITHUB_SHA": current["checkout_sha"], "GITHUB_WORKFLOW_SHA": current["checkout_sha"],
           "GITHUB_WORKFLOW_REF": cs.REPOSITORY + "/" + cs.WORKFLOW + "@refs/pull/788/merge",
           "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
    env_key = {"checkout": "GITHUB_SHA", "workflow-sha": "GITHUB_WORKFLOW_SHA",
               "workflow-ref": "GITHUB_WORKFLOW_REF", "attempt": "GITHUB_RUN_ATTEMPT", "run": "GITHUB_RUN_ID"}
    if case in env_key:
        env[env_key[case]] = "0" if case in {"attempt", "run"} else sha("f")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    def git(*args):
        if args[0] == "rev-list":
            return f"{current['checkout_sha']} {sha('c')} {sha('f') if case == 'parents' else sha('d')}"
        assert args == ("rev-parse", current["checkout_sha"] + ":" + cs.WORKFLOW)
        return sha("f") if case == "workflow-blob" else current["policy"][cs.WORKFLOW]
    monkeypatch.setattr(cs, "git", git)
    if case in {"head", "base"}:
        pr[case]["sha"] = sha("f")
    if case == "fork":
        pr["head"]["repo"] = {"id": 999, "full_name": "someone/fork"}
    needs = {name: {"result": "success", "outputs": {"tree": current["tree"]}}
             for name in (*cs.GATE_IDS, "changes", "readme", "patch-notes")}
    needs["changes"]["outputs"]["skip"] = "false"
    if case in {"readme", "patch-notes"}:
        needs[case]["result"] = "skipped"
    with pytest.raises(ValueError):
        cs.produce_manifest(current, needs, {"pull_request": pr})


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


@pytest.mark.parametrize("binary", [False, True])
def test_api_reads_bounded_binary_response_with_original_handlers(monkeypatch, binary):
    monkeypatch.setenv("GH_TOKEN", "synthetic-fixture")
    monkeypatch.setenv("GITHUB_REPOSITORY", cs.REPOSITORY)
    opener = object()
    calls = []
    payload = b"\xff\x00" if binary else '{"text":"한국어"}'.encode("utf-8")
    class Response(io.BytesIO):
        def read(self, size):
            calls.append(size)
            return super().read(size)
    def build(handler):
        assert isinstance(handler, cs.ArtifactRedirect)
        return opener
    def open_response(actual_opener, request, *, timeout):
        assert actual_opener is opener and timeout == 15
        assert request.full_url == f"https://api.github.com/repos/{cs.REPOSITORY}/fixture"
        assert request.get_header("Authorization") == "Bearer synthetic-fixture"
        return Response(payload)
    monkeypatch.setattr(cs.urllib.request, "build_opener", build)
    monkeypatch.setattr(cs.urllib.request.OpenerDirector, "open", open_response)
    assert cs.api("fixture", binary=binary) == (payload if binary else {"text": "한국어"})
    assert calls == [(cs.MAX_BYTES if binary else cs.MAX_API_BYTES) + 1]


@pytest.mark.parametrize("binary", [False, True])
def test_api_rejects_oversized_response(monkeypatch, binary):
    monkeypatch.setenv("GH_TOKEN", "synthetic-fixture")
    monkeypatch.setenv("GITHUB_REPOSITORY", cs.REPOSITORY)
    limit = cs.MAX_BYTES if binary else cs.MAX_API_BYTES
    monkeypatch.setattr(cs.urllib.request.OpenerDirector, "open",
                        lambda *args, **kwargs: io.BytesIO(b"x" * (limit + 1)))
    with pytest.raises(ValueError, match="oversized CI evidence"):
        cs.api("fixture", binary=binary)


def test_api_json_requires_utf8_not_implicit_utf16_detection(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-fixture")
    monkeypatch.setenv("GITHUB_REPOSITORY", cs.REPOSITORY)
    monkeypatch.setattr(cs.urllib.request.OpenerDirector, "open",
                        lambda *args, **kwargs: io.BytesIO('{"text":"fixture"}'.encode("utf-16")))
    with pytest.raises(UnicodeDecodeError):
        cs.api("fixture")


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
    checkout = next(s for s in producer["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 2  # rev-list must see both parents
    assert set(producer["needs"]) == {*cs.GATE_IDS, "changes", "readme", "patch-notes"}
    assert "needs.changes.outputs.skip != 'true'" in producer["if"]
    assert any(s.get("uses", "").startswith("actions/upload-artifact@v4") for s in producer["steps"])
