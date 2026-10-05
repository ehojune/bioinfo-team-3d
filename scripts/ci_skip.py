"""Can this PR run skip pytest? Prints `skip=true` or `skip=false` for $GITHUB_OUTPUT.

PI 2026-10-02: a push that only resolves STATUS.md / patch_notes conflicts, or only adds patch notes, should not
wait for pytest again. Walking back from the head along first parents, every commit must have changed only
STATUS.md or patch_notes/, or (for a merge of the base branch) files that arrived from the merged side unchanged,
until a commit whose pytest jobs all passed. The merged-in combination itself is not re-tested; main's own CI
runs again after the squash merge.

usage: python scripts/ci_skip.py HEAD_SHA BASE_SHA   (env GITHUB_REPOSITORY, GH_TOKEN)
A merge counts only when its second parent is on the base branch (an ancestor of BASE_SHA).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

JOBS = ("pytest (3.10)", "pytest (3.12)", "pytest-windows", "pytest-slow")  # every job the skip output gates
MAX_WALK = 5


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def blob(rev: str, path: str) -> str | None:
    run = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{rev}:{path}"], capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else None


def notes_only(path: str) -> bool:
    return path == "STATUS.md" or path.startswith("patch_notes/") or path.startswith("docs/status/")


def on_base(sha: str, base: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, base], capture_output=True).returncode == 0


# Only tests/test_integrations.py reads the root READMEs (badge blocks, title, no local paths),
# so a README change runs that one file instead of the whole suite (PI 2026-10-02).
README_ONLY = ("README.md", "README.en.md")


def safe_step(commit: str, base: str, touched: set[str] | None = None) -> bool:
    """The commit's own change against its first parent is notes, README, or files taken unchanged from the base."""
    parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
    if not parents or len(parents) > 2 or (len(parents) == 2 and not on_base(parents[1], base)):
        return False  # a merge of anything but the base branch (another feature branch) always reruns
    for path in filter(None, git("diff", "--name-only", parents[0], commit).splitlines()):
        if notes_only(path):
            continue
        if len(parents) == 2 and blob(commit, path) == blob(parents[1], path):
            continue
        if path in README_ONLY and touched is not None:
            touched.add(path)
            continue
        return False
    return True


def pytest_passed(sha: str) -> bool:
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GH_TOKEN")
    if not (repo and token):
        return False
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        runs = json.load(resp).get("check_runs", [])
    passed = {r.get("name") for r in runs if r.get("conclusion") == "success"}
    return all(job in passed for job in JOBS)


def can_skip(head: str, base: str, passed=pytest_passed, touched: set[str] | None = None) -> bool:
    """True when the full suite can be skipped; README paths that still need their own check land in `touched`."""
    commit = head
    for _ in range(MAX_WALK):
        if not safe_step(commit, base, touched):
            return False
        commit = git("rev-parse", f"{commit}^1")
        if passed(commit):
            return True
    return False


if __name__ == "__main__":
    touched: set[str] = set()
    try:
        skip = len(sys.argv) > 2 and can_skip(sys.argv[1], sys.argv[2], touched=touched)
    except Exception as exc:  # noqa: BLE001 - any doubt runs the tests
        print(f"ci_skip: {type(exc).__name__}: {exc}", file=sys.stderr)
        skip = False
    print(f"skip={'true' if skip else 'false'}")
    print(f"readme={'true' if skip and touched else 'false'}")
