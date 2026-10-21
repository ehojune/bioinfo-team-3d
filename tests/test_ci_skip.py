import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ci_skip", Path(__file__).resolve().parents[1] / "scripts" / "ci_skip.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, files, message):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    run(tmp_path, "init", "-q", "-b", "main")
    run(tmp_path, "config", "user.email", "t@example.com")
    run(tmp_path, "config", "user.name", "t")
    commit(tmp_path, {"app.py": "a = 1\n", "lib.py": "b = 1\n", "STATUS.md": "# s\n", "patch_notes/README.md": "# n\n"},
           "start")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_patch_notes_do_not_hide_unvalidated_code_in_the_entire_diff(repo):
    run(repo, "checkout", "-q", "-b", "pr")
    code = commit(repo, {"app.py": "a = 2\n"}, "code")
    head = commit(repo, {"patch_notes/README.md": "# n\nNew row.\n"}, "notes")
    assert not cs.can_skip(head, 'main')  # an earlier check name cannot prove this checkout tree
    assert cs.can_skip(head, code)  # genuinely document-only diff
    assert not cs.can_skip(code, 'main')


def test_per_pr_status_file_does_not_hide_code(repo):
    run(repo, "checkout", "-q", "-b", "pr")
    code = commit(repo, {"app.py": "a = 2\n"}, "code")
    head = commit(repo, {"docs/status/2026-10-02-1000-pr.md": "## done\n"}, "status")
    assert not cs.can_skip(head, "main")
    assert cs.can_skip(head, code)


def test_merging_main_does_not_prove_the_combination_was_validated(repo):
    run(repo, "checkout", "-q", "-b", "pr")
    tested = commit(repo, {"app.py": "a = 2\n", "STATUS.md": "# s\npr\n"}, "pr")
    run(repo, "checkout", "-q", "main")
    commit(repo, {"lib.py": "b = 2\n", "STATUS.md": "# s\nmain\n"}, "other pr on main")
    run(repo, "checkout", "-q", "pr")
    subprocess.run(["git", "merge", "-q", "--no-edit", "main"], cwd=repo, capture_output=True)  # STATUS conflicts
    (repo / "STATUS.md").write_text("# s\npr\nmain\n", encoding="utf-8")
    run(repo, "add", "STATUS.md")
    run(repo, "commit", "-q", "--no-edit")
    head = run(repo, "rev-parse", "HEAD")
    assert not cs.can_skip(head, 'main')  # lib.py coming from main does not validate the combination
    assert not cs.can_skip(head, tested)


def test_a_file_both_sides_changed_reruns(repo):
    lines = "".join(f"line{i}\n" for i in range(10))
    base = commit(repo, {"app.py": lines}, "longer file")
    run(repo, "checkout", "-q", "-b", "pr")
    tested = commit(repo, {"app.py": lines.replace("line9", "pr9")}, "pr edits the end")
    run(repo, "checkout", "-q", "main")
    commit(repo, {"app.py": lines.replace("line0", "main0")}, "main edits the start")
    run(repo, "checkout", "-q", "pr")
    run(repo, "merge", "-q", "--no-edit", "main")  # clean auto-merge of both edits
    head = run(repo, "rev-parse", "HEAD")
    assert base and tested and not cs.can_skip(head, 'main')  # app.py matches neither side


def test_merging_another_feature_branch_reruns(repo):
    run(repo, "checkout", "-q", "-b", "pr")
    tested = commit(repo, {"app.py": "a = 2\n"}, "pr")
    run(repo, "checkout", "-q", "-b", "other", "main")
    commit(repo, {"new.py": "c = 1\n"}, "untested feature")
    run(repo, "checkout", "-q", "pr")
    run(repo, "merge", "-q", "--no-edit", "other")
    head = run(repo, "rev-parse", "HEAD")
    assert tested and not cs.can_skip(head, "main")  # new.py came from a non-base branch


def test_no_token_means_no_skip(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert cs.reuse_main({"checkout_sha": "a" * 40}) is False


def test_a_readme_only_change_skips_the_suite_with_document_checks_always_retained(repo):
    commit(repo, {"README.md": "# labhq\n"}, "readme")
    run(repo, "checkout", "-q", "-b", "pr")
    head = commit(repo, {"README.md": "# labhq\nMore prose.\n"}, "docs")
    assert cs.can_skip(head, "main")  # workflow always retains README/document validators
    code = commit(repo, {"app.py": "a = 2\n"}, "code")
    assert not cs.can_skip(code, "main")
