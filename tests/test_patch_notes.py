import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("patch_notes", Path(__file__).resolve().parents[1] / "scripts" / "patch_notes.py")
pn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pn)


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
def repo(tmp_path):
    run(tmp_path, "init", "-q", "-b", "main")
    run(tmp_path, "config", "user.email", "t@example.com")
    run(tmp_path, "config", "user.name", "t")
    commit(tmp_path, {"README.md": "v1\n", pn.NOTES: "# 패치노트\n"}, "start")
    return tmp_path


def test_every_code_commit_needs_a_note_but_note_only_commits_do_not(repo):
    run(repo, "checkout", "-q", "-b", "feature")
    fix = commit(repo, {"a.py": "x = 1\n"}, "fix a")
    problems = pn.check("main", "HEAD", repo)
    assert any(fix[:7] in p for p in problems)
    commit(repo, {pn.NOTES: f"# 패치노트\n| 10:00 | [`{fix[:7]}`](u) | **fix a** |\n"}, "패치노트")
    assert pn.check("main", "HEAD", repo) == []
    later = commit(repo, {"a.py": "x = 2\n"}, "fix a again")  # a commit after the note needs its own line
    assert any(later[:7] in p for p in pn.check("main", "HEAD", repo))


def test_readme_must_change_at_least_once_every_three_main_commits(repo):
    commit(repo, {"b.py": "1\n"}, "main change 1")
    run(repo, "checkout", "-q", "-b", "second")
    change = commit(repo, {"c.py": "1\n"}, "second change")
    commit(repo, {pn.NOTES: f"# 패치노트\n{change[:7]}\n"}, "패치노트")
    assert pn.check("main", "HEAD", repo) == []  # main is one commit past README: this one may skip it
    run(repo, "checkout", "-q", "main")
    run(repo, "merge", "-q", "--squash", "second")
    run(repo, "commit", "-q", "-m", "second (#2)")
    run(repo, "checkout", "-q", "-b", "third")
    third = commit(repo, {"d.py": "1\n"}, "third change")
    commit(repo, {pn.NOTES: f"# 패치노트\n{change[:7]}\n{third[:7]}\n"}, "패치노트")
    assert any("README" in p for p in pn.check("main", "HEAD", repo))
    commit(repo, {"README.md": "v2\n"}, "README 갱신")
    problems = pn.check("main", "HEAD", repo)
    assert not any("README.md를 안 고친" in p for p in problems)
    assert any("README 갱신" in p for p in problems)  # and the README commit itself needs a note


def test_rows_lists_commits_newest_first_and_links_the_pr(repo):
    run(repo, "checkout", "-q", "-b", "feature")
    first = commit(repo, {"a.py": "1\n"}, "first")
    second = commit(repo, {"a.py": "2\n"}, "second")
    commit(repo, {pn.NOTES: "# 패치노트\n\n(작성 중)\n"}, "패치노트")
    out = pn.rows("main", "HEAD", 7, repo).splitlines()
    assert [second[:7] in out[0], first[:7] in out[1], len(out)] == [True, True, 2]
    assert f"/pull/7/commits/{first[:7]}" in out[1]


def test_a_readme_edit_reverted_later_is_not_a_refresh(repo):
    commit(repo, {"b.py": "1\n"}, "main change 1")
    commit(repo, {"b.py": "2\n"}, "main change 2")
    run(repo, "checkout", "-q", "-b", "feature")
    edit = commit(repo, {"README.md": "v2\n"}, "README edit")
    revert = commit(repo, {"README.md": "v1\n"}, "README revert")
    commit(repo, {pn.NOTES: f"# 패치노트\n{edit[:7]}\n{revert[:7]}\n"}, "패치노트")
    assert any("README.md를 안 고친" in p for p in pn.check("main", "HEAD", repo))
