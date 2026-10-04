import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

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


def entry(*shas):
    rows = [{"sha": sha[:7], "at": "2026-10-02 10:00", "text": f"note {sha[:7]}"} for sha in shas]
    return yaml.safe_dump({"pr": 7, "rows": rows}, allow_unicode=True, sort_keys=False)


@pytest.fixture
def repo(tmp_path):
    run(tmp_path, "init", "-q", "-b", "main")
    run(tmp_path, "config", "user.email", "t@example.com")
    run(tmp_path, "config", "user.name", "t")
    commit(tmp_path, {"README.md": "v1\n", "STATUS.md": "# status\n", "patch_notes/README.md": "# notes\n",
                      "patch_notes/entries/_legacy.yaml": "pr: null\nrows: []\n"}, "start")
    return tmp_path


def test_every_code_commit_needs_a_note_but_note_only_commits_do_not(repo):
    run(repo, "checkout", "-q", "-b", "feature")
    fix = commit(repo, {"a.py": "x = 1\n"}, "fix a")
    assert any(fix[:7] in problem for problem in pn.check("main", "HEAD", repo))
    commit(repo, {"patch_notes/entries/feature.yaml": entry(fix)}, "패치노트")
    assert pn.check("main", "HEAD", repo) == []
    later = commit(repo, {"a.py": "x = 2\n"}, "fix a again")
    assert any(later[:7] in problem for problem in pn.check("main", "HEAD", repo))


def test_readme_must_change_at_least_once_every_three_main_commits(repo):
    commit(repo, {"b.py": "1\n"}, "main change 1")
    run(repo, "checkout", "-q", "-b", "second")
    change = commit(repo, {"c.py": "1\n"}, "second change")
    commit(repo, {"patch_notes/entries/second.yaml": entry(change)}, "패치노트")
    assert pn.check("main", "HEAD", repo) == []
    run(repo, "checkout", "-q", "main")
    run(repo, "merge", "-q", "--squash", "second")
    run(repo, "commit", "-q", "-m", "second (#2)")
    run(repo, "checkout", "-q", "-b", "third")
    third = commit(repo, {"d.py": "1\n"}, "third change")
    commit(repo, {"patch_notes/entries/third.yaml": entry(third)}, "패치노트")
    assert any("README" in problem for problem in pn.check("main", "HEAD", repo))
    commit(repo, {"README.md": "v2\n"}, "README 갱신")
    problems = pn.check("main", "HEAD", repo)
    assert not any("를 안 고친 커밋" in problem for problem in problems)
    assert any("README 갱신" in problem for problem in problems)


def test_a_manual_edit_counts_as_the_documentation_refresh(repo):
    commit(repo, {"b.py": "1\n"}, "main change 1")
    commit(repo, {"b.py": "2\n"}, "main change 2")
    run(repo, "checkout", "-q", "-b", "feature")
    change = commit(repo, {"c.py": "1\n"}, "feature change")
    commit(repo, {"patch_notes/entries/feature.yaml": entry(change)}, "패치노트")
    assert any("를 안 고친 커밋이 2개" in problem for problem in pn.check("main", "HEAD", repo))
    manual = commit(repo, {"docs/manual.md": "details\n"}, "매뉴얼 갱신")
    commit(repo, {"patch_notes/entries/feature.yaml": entry(change, manual)}, "패치노트")
    assert pn.check("main", "HEAD", repo) == []
    run(repo, "checkout", "-q", "main")
    commit(repo, {"docs/manual.md": "main details\n"}, "manual on main")
    assert pn.counted_main_commits(run(repo, "log", "-1", "--format=%H", "--", *pn.DOCS), "main", repo) == 0
    commit(repo, {"docs/other.md": "x\n"}, "another doc")  # only README and the manual count
    assert pn.counted_main_commits(run(repo, "log", "-1", "--format=%H", "--", *pn.DOCS), "main", repo) == 1


def test_rows_lists_commits_newest_first_as_yaml(repo):
    run(repo, "checkout", "-q", "-b", "feature")
    first = commit(repo, {"a.py": "1\n"}, "first")
    second = commit(repo, {"a.py": "2\n"}, "second")
    commit(repo, {"patch_notes/entries/feature.yaml": "pr: 7\nrows: []\n"}, "패치노트")
    out = yaml.safe_load(pn.rows("main", "HEAD", 7, repo))
    assert [out[0]["sha"], out[1]["sha"], len(out)] == [second[:7], first[:7], 2]
    assert out[1]["text"] == "first"


def test_a_readme_edit_reverted_later_is_not_a_refresh(repo):
    commit(repo, {"b.py": "1\n"}, "main change 1")
    commit(repo, {"b.py": "2\n"}, "main change 2")
    run(repo, "checkout", "-q", "-b", "feature")
    edit = commit(repo, {"README.md": "v2\n"}, "README edit")
    revert = commit(repo, {"README.md": "v1\n"}, "README revert")
    commit(repo, {"patch_notes/entries/feature.yaml": entry(edit, revert)}, "패치노트")
    assert any("를 안 고친 커밋" in problem for problem in pn.check("main", "HEAD", repo))


@pytest.mark.parametrize("files,needs_note", [
    (["a.py"], True),
    (["docs/status/x.md"], False),
    (["patch_notes/entries/x.yaml"], False),
    (["docs/status/x.md", "patch_notes/entries/x.yaml"], False),
    (["a.py", "docs/status/x.md", "patch_notes/entries/x.yaml"], True),
])
def test_merge_resolution_requires_note_only_for_own_code_change(repo, files, needs_note):
    commit(repo, {name: "base\n" for name in files}, "base files")
    run(repo, "checkout", "-q", "-b", "feature")
    feature = commit(repo, {name: "feature\n" for name in files}, "feature side")
    run(repo, "checkout", "-q", "main")
    commit(repo, {name: "main\n" for name in files}, "main side")
    run(repo, "checkout", "-q", "feature")
    result = subprocess.run(["git", "merge", "main", "--no-ff", "--no-commit"], cwd=repo, capture_output=True)
    assert result.returncode == 1
    merge = commit(repo, {name: "resolved\n" for name in files}, "resolve both parents")
    assert (merge in pn.pr_commits("main", "HEAD", repo)) is needs_note
    assert any(merge[:7] in problem for problem in pn.check("main", "HEAD", repo)) is needs_note
    assert (merge[:7] in pn.rows("main", "HEAD", 99, repo)) is needs_note
    if needs_note:
        commit(repo, {"patch_notes/entries/feature.yaml": entry(feature, merge), "README.md": "refreshed\n"},
               "document resolution")
        assert not any(merge[:7] in problem for problem in pn.check("main", "HEAD", repo))


def test_merge_without_own_changes_is_excluded(repo):
    run(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"a.py": "feature\n"}, "feature")
    run(repo, "checkout", "-q", "main")
    commit(repo, {"b.py": "main\n"}, "main")
    run(repo, "checkout", "-q", "feature")
    run(repo, "merge", "-q", "--no-ff", "main", "-m", "sync main")
    merge = run(repo, "rev-parse", "HEAD")
    assert merge not in pn.pr_commits("main", "HEAD", repo)


def test_clean_merge_of_a_file_both_sides_changed_is_excluded(repo):
    commit(repo, {"a.py": "one\ntwo\nthree\nfour\nfive\n"}, "base")
    run(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"a.py": "ONE\ntwo\nthree\nfour\nfive\n"}, "feature edits the top")
    run(repo, "checkout", "-q", "main")
    commit(repo, {"a.py": "one\ntwo\nthree\nfour\nFIVE\n"}, "main edits the bottom")
    run(repo, "checkout", "-q", "feature")
    run(repo, "merge", "-q", "--no-ff", "main", "-m", "sync main")
    merge = run(repo, "rev-parse", "HEAD")
    assert "a.py" in pn.changed_files(merge, repo)
    assert merge not in pn.pr_commits("main", "HEAD", repo)


def test_extra_edit_inside_a_clean_merge_needs_a_note(repo):
    commit(repo, {"a.py": "base\n"}, "base")
    run(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"b.py": "feature\n"}, "feature")
    run(repo, "checkout", "-q", "main")
    commit(repo, {"c.py": "main\n"}, "main")
    run(repo, "checkout", "-q", "feature")
    run(repo, "merge", "-q", "--no-ff", "--no-commit", "main")
    merge = commit(repo, {"a.py": "edited during merge\n"}, "merge with an extra edit")
    assert merge in pn.pr_commits("main", "HEAD", repo)


def test_generated_indexes_cannot_be_changed_directly(repo):
    run(repo, "checkout", "-q", "-b", "feature")
    generated = commit(repo, {"STATUS.md": "changed\n"}, "edit generated index")
    commit(repo, {"patch_notes/entries/feature.yaml": entry(generated)}, "패치노트")
    assert any("entries·docs/status에 쓰세요" in problem for problem in pn.check("main", "HEAD", repo))


def test_bootstrap_migration_may_add_generated_indexes(tmp_path):
    run(tmp_path, "init", "-q", "-b", "main")
    run(tmp_path, "config", "user.email", "t@example.com")
    run(tmp_path, "config", "user.name", "t")
    commit(tmp_path, {"README.md": "v1\n", "STATUS.md": "old\n", "patch_notes/README.md": "old\n"}, "start")
    run(tmp_path, "checkout", "-q", "-b", "migration")
    migration = commit(tmp_path, {"STATUS.md": "generated\n", "patch_notes/README.md": "generated\n"}, "migrate")
    commit(tmp_path, {"patch_notes/entries/_legacy.yaml": entry(migration)}, "legacy entry")
    assert pn.check("main", "HEAD", tmp_path) == []


def test_generated_only_main_commits_do_not_count_toward_readme_limit(repo):
    last = run(repo, "log", "-1", "--format=%H", "--", "README.md")
    commit(repo, {"STATUS.md": "generated 1\n"}, "index 1")
    commit(repo, {"patch_notes/README.md": "generated 2\n"}, "index 2")
    assert pn.counted_main_commits(last, "main", repo) == 0


def test_an_all_digit_sha_read_as_a_yaml_integer_is_named(repo):
    # PR #410: a hand-written row `sha: 7999708` loads as an int; the check names that, not a missing commit.
    run(repo, "checkout", "-q", "-b", "feature")
    note = ("pr: 7\nrows:\n- sha: 7999708\n  at: 2026-10-05 06:02\n  text: note\n"
            "- sha: 0123456\n  at: 2026-10-05 06:03\n  text: octal\n")
    commit(repo, {"patch_notes/entries/feature.yaml": note}, "패치노트")
    _, problems = pn.entry_shas("HEAD", repo)
    # PR #412 review: 0123456 loads as the octal 42798, so the hint quotes the sha as typed.
    assert problems == [f"patch_notes/entries/feature.yaml: sha {sha}가 숫자로 읽힙니다. 숫자로만 된 sha는 따옴표로 "
                        f"감싸세요 (sha: '{sha}')" for sha in ("7999708", "0123456")]
    # The rows command quotes such a sha itself, so it round-trips as text.
    assert yaml.safe_load(yaml.safe_dump([{"sha": "7999708"}])) == [{"sha": "7999708"}]
