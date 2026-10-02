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


def test_a_patch_note_commit_after_a_passed_code_commit_skips(repo):
    run(repo, "checkout", "-q", "-b", "pr")
    code = commit(repo, {"app.py": "a = 2\n"}, "code")
    head = commit(repo, {"patch_notes/README.md": "# n\nrow\n"}, "notes")
    assert cs.can_skip(head, 'main', passed=lambda sha: sha == code)
    assert not cs.can_skip(head, 'main', passed=lambda sha: False)  # nothing passed yet: run
    assert not cs.can_skip(code, 'main', passed=lambda sha: True)  # the code commit itself always runs


def test_merging_main_skips_only_when_every_file_came_from_main_unchanged(repo):
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
    assert cs.can_skip(head, 'main', passed=lambda sha: sha == tested)  # lib.py came from main as is


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
    assert base and not cs.can_skip(head, 'main', passed=lambda sha: sha == tested)  # app.py matches neither side


def test_merging_another_feature_branch_reruns(repo):
    run(repo, "checkout", "-q", "-b", "pr")
    tested = commit(repo, {"app.py": "a = 2\n"}, "pr")
    run(repo, "checkout", "-q", "-b", "other", "main")
    commit(repo, {"new.py": "c = 1\n"}, "untested feature")
    run(repo, "checkout", "-q", "pr")
    run(repo, "merge", "-q", "--no-edit", "other")
    head = run(repo, "rev-parse", "HEAD")
    assert not cs.can_skip(head, "main", passed=lambda sha: sha == tested)  # new.py came from a non-base branch


def test_no_token_means_no_skip(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert cs.pytest_passed("deadbeef") is False


def test_a_readme_only_change_skips_the_suite_but_flags_the_readme_check(repo):
    commit(repo, {"README.md": "# labhq\n"}, "readme")
    run(repo, "checkout", "-q", "-b", "pr")
    code = commit(repo, {"app.py": "a = 2\n"}, "code")
    head = commit(repo, {"README.md": "# labhq\nmore prose\n"}, "docs")
    touched = set()
    assert cs.can_skip(head, "main", passed=lambda sha: sha == code, touched=touched)
    assert touched == {"README.md"}  # the workflow then runs tests/test_integrations.py only
    assert not cs.can_skip(head, "main", passed=lambda sha: sha == code)  # without the flag set, README still reruns
