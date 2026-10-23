"""Exercise repository hooks with inert global hooks and disposable Git histories."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
TRAILER = "Co-Authored-By: Original Collaborator <collaborator@example.com>"


@pytest.fixture
def history(tmp_path, monkeypatch):
    # No operator config, signing, hooks, identity files or agent credentials.
    for key in list(os.environ):
        if key.startswith("GIT_") or key.startswith("LABHQ_COMMIT_"):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("LABHQ_COMMIT_AGENT", "codex")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, check=True, cwd=repo):
        return subprocess.run(["git", *args], cwd=cwd, check=check,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Operator")
    git("config", "user.email", "operator@example.com")
    git("config", "core.logAllRefUpdates", "true")
    git("config", "core.hooksPath", ".githooks")
    hooks = repo / ".githooks"
    hooks.mkdir()
    for name in ("prepare-commit-msg", "commit-msg", "post-commit"):
        target = hooks / name
        shutil.copyfile(ROOT / ".githooks" / name, target)
        target.chmod(0o755)
    git("add", ".githooks")
    git("commit", "-qm", "base")
    return repo, home, git


def global_hooks(home):
    hooks = home / ".git-template" / "hooks"
    hooks.mkdir(parents=True)
    scripts = {
        "commit-msg": '#!/bin/sh\nsed -i.bak "/^[Cc]o-[Aa]uthored-[Bb]y:/d" "$1"\n',
        "post-commit": '''#!/bin/sh
[ -n "$LABHQ_POSTCOMMIT_AMEND" ] && exit 0
LABHQ_POSTCOMMIT_AMEND=1 git -c user.name=Agent -c user.email=agent@example.com commit --amend --allow-empty --no-edit --no-verify --quiet --author="Agent <agent@example.com>"
''',
    }
    for name, text in scripts.items():
        hook = hooks / name
        hook.write_text(text, encoding="utf-8", newline="\n")
        hook.chmod(0o755)


def original_commit(repo, git, name):
    (repo / name).write_text(name, encoding="utf-8")
    git("add", name)
    git("commit", "-qm", f"{name}\n\n{TRAILER}", "--author", "Original <original@example.com>")
    return git("rev-parse", "HEAD").stdout.strip()


def test_new_commit_delegates_to_global_hooks(history):
    repo, home, git = history
    global_hooks(home)
    git("commit", "--allow-empty", "-qm", f"new\n\n{TRAILER}")
    assert git("log", "-1", "--format=%ae|%ce").stdout.strip() == "agent@example.com|agent@example.com"
    assert TRAILER not in git("log", "-1", "--format=%B").stdout
    assert "chatgpt-codex-connector" not in git("log", "-1", "--format=%B").stdout


def test_no_global_hooks_keeps_local_trailer_fallback(history):
    _, _, git = history
    git("commit", "--allow-empty", "-qm", "new\n\nCo-Authored-By: Codex <noreply@openai.com>")
    assert git("log", "-1", "--format=%ae").stdout.strip() == "operator@example.com"
    assert "199175422+chatgpt-codex-connector[bot]@users.noreply.github.com" in git("log", "-1", "--format=%B").stdout


@pytest.mark.parametrize("explicit_commit", [False, True], ids=["automatic", "no-commit"])
def test_merge_keeps_agent_trailer_without_global_author_rewrite(history, explicit_commit):
    repo, home, git = history
    git("checkout", "-qb", "topic")
    original_commit(repo, git, "topic")
    git("checkout", "-q", "main")
    original_commit(repo, git, "main")
    global_hooks(home)
    if explicit_commit:
        git("merge", "--no-commit", "topic")
        git("commit", "-qm", "merge topic")
    else:
        git("merge", "--no-edit", "topic")
    assert git("log", "-1", "--format=%ae").stdout.strip() == "operator@example.com"
    assert "Co-authored-by: Codex <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>" in git("log", "-1", "--format=%B").stdout


@pytest.mark.parametrize("multiple", [False, True], ids=["single", "sequence"])
def test_revert_new_inverse_commits_keep_agent_attribution(history, multiple):
    repo, home, git = history
    first = original_commit(repo, git, "one")
    second = original_commit(repo, git, "two") if multiple else None
    global_hooks(home)
    git("revert", "--no-edit", *([second, first] if multiple else [first]))
    for offset in range(2 if multiple else 1):
        assert git("log", "-1", "--format=%ae|%ce", f"HEAD~{offset}").stdout.strip() == "agent@example.com|agent@example.com"


def test_cherry_pick_sequence_preserves_both_original_commits(history):
    repo, home, git = history
    git("checkout", "-qb", "topic")
    first = original_commit(repo, git, "one")
    second = original_commit(repo, git, "two")
    messages = [git("log", "-1", "--format=%B", sha).stdout for sha in (second, first)]
    git("checkout", "-q", "main")
    global_hooks(home)
    git("cherry-pick", first, second)
    for offset, message in enumerate(messages):
        assert git("log", "-1", "--format=%ae", f"HEAD~{offset}").stdout.strip() == "original@example.com"
        assert git("log", "-1", "--format=%B", f"HEAD~{offset}").stdout == message


@pytest.mark.parametrize("message", [False, True], ids=["no-edit", "message"])
def test_amend_preserves_another_author_and_adds_agent_trailer(history, message):
    repo, home, git = history
    original_commit(repo, git, "one")
    global_hooks(home)
    args = ["-m", "amended"] if message else ["--no-edit"]
    git("commit", "--amend", *args)
    assert git("log", "-1", "--format=%ae").stdout.strip() == "original@example.com"
    assert "Co-authored-by: Codex <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>" in git("log", "-1", "--format=%B").stdout


def test_explicit_author_on_new_commit_is_preserved(history):
    repo, home, git = history
    global_hooks(home)
    original_commit(repo, git, "one")
    assert git("log", "-1", "--format=%ae").stdout.strip() == "original@example.com"


AGENT_TRAILER = "Co-authored-by: Codex <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>"


@pytest.mark.parametrize("operation", ["amend-no-edit", "amend-message", "reuse", "reedit"])
@pytest.mark.parametrize("linked", [False, True], ids=["checkout", "worktree"])
@pytest.mark.parametrize("with_global", [False, True], ids=["local", "global"])
def test_same_identity_reuse_preserves_author_and_agent(history, tmp_path, operation, linked, with_global):
    repo, home, git = history
    git("-c", "core.hooksPath=disabled", "commit", "--allow-empty", "-qm", f"operator work\n\n{TRAILER}")
    source = git("rev-parse", "HEAD").stdout.strip()
    author = git("log", "-1", "--format=%an|%ae|%aI").stdout
    cwd = repo
    if linked:
        cwd = tmp_path / "linked"
        git("worktree", "add", "-qb", "linked", str(cwd))
    if with_global:
        global_hooks(home)
    args = {
        "amend-no-edit": ["--allow-empty", "--amend", "--no-edit"],
        "amend-message": ["--allow-empty", "--amend", "-m", "updated work"],
        "reuse": ["--allow-empty", "-C", source],
        "reedit": ["--allow-empty", "-c", source],
    }[operation]
    git("-c", "core.editor=true", "commit", *args, cwd=cwd)
    assert git("log", "-1", "--format=%an|%ae|%aI", cwd=cwd).stdout == author
    message = git("log", "-1", "--format=%B", cwd=cwd).stdout
    assert AGENT_TRAILER in message
    if operation != "amend-message":
        assert TRAILER in message
    # The next ordinary commit in either worktree must still use the bot.
    git("commit", "--allow-empty", "-qm", "fresh", cwd=cwd)
    expected = "agent@example.com" if with_global else "operator@example.com"
    assert git("log", "-1", "--format=%ae", cwd=cwd).stdout.strip() == expected


@pytest.mark.parametrize("operation", ["amend", "reuse", "reedit"])
def test_reset_author_honors_configured_identity_and_keeps_agent(history, operation):
    repo, home, git = history
    original_commit(repo, git, "one")
    git("-c", "core.hooksPath=disabled", "commit", "--amend", "--no-edit", "--date", "2001-02-03T04:05:06+00:00")
    source = git("rev-parse", "HEAD").stdout.strip()
    global_hooks(home)
    args = {"amend": ["--amend", "--no-edit"], "reuse": ["--allow-empty", "-C", source],
            "reedit": ["--allow-empty", "-c", source]}[operation]
    git("-c", "core.editor=true", "commit", "--reset-author", *args)
    assert git("log", "-1", "--format=%ae").stdout.strip() == "operator@example.com"
    assert not git("log", "-1", "--format=%aI").stdout.startswith("2001-")
    assert AGENT_TRAILER in git("log", "-1", "--format=%B").stdout


@pytest.mark.parametrize("next_operation", ["commit", "revert"])
def test_aborted_reuse_does_not_affect_next_commit_or_other_worktree(history, tmp_path, next_operation):
    repo, home, git = history
    source = original_commit(repo, git, "one")
    linked = tmp_path / "linked"
    git("worktree", "add", "-qb", "linked", str(linked))
    global_hooks(home)
    result = git("-c", "core.editor=false", "commit", "--allow-empty", "-c", source, check=False)
    assert result.returncode != 0
    git("commit", "--allow-empty", "-qm", "other worktree", cwd=linked)
    assert git("log", "-1", "--format=%ae", cwd=linked).stdout.strip() == "agent@example.com"
    if next_operation == "commit":
        git("commit", "--allow-empty", "-qm", "fresh")
    else:
        git("revert", "--no-edit", source)
    assert git("log", "-1", "--format=%ae|%ce").stdout.strip() == "agent@example.com|agent@example.com"


@pytest.mark.parametrize("operation", ["cherry-pick", "rebase", "rebase-apply", "cherry-pick-continue", "no-reflog-continue"])
@pytest.mark.parametrize("linked", [False, True], ids=["checkout", "worktree"])
def test_replay_preserves_author_and_message(history, tmp_path, operation, linked):
    repo, home, git = history
    base = git("rev-parse", "HEAD").stdout.strip()
    git("checkout", "-qb", "topic")
    sha = original_commit(repo, git, "change")
    message = git("log", "-1", "--format=%B").stdout
    git("checkout", "-q", "main")
    continuing = operation.endswith("continue")
    if continuing:
        (repo / "change").write_text("conflicting", encoding="utf-8")
        git("add", "change")
        git("commit", "-qm", "conflict")
    else:
        git("commit", "--allow-empty", "-qm", "advance")
    if linked:
        worktree = tmp_path / "linked"
        git("worktree", "add", "-q", "-b", "destination", str(worktree), "main")
    else:
        worktree = repo
    global_hooks(home)
    if operation == "no-reflog-continue":
        git("config", "core.logAllRefUpdates", "false")
    if operation.startswith("cherry-pick") or continuing:
        result = git("cherry-pick", sha, cwd=worktree, check=not continuing)
        if continuing:
            assert result.returncode != 0
            (worktree / "change").write_text("change", encoding="utf-8")
            git("add", "change", cwd=worktree)
            git("-c", "core.editor=true", "cherry-pick", "--continue", cwd=worktree)
    else:
        git("checkout", "-q", "topic", cwd=worktree)
        flags = ["--apply"] if operation == "rebase-apply" else []
        git("rebase", *flags, "--onto", "main", base, cwd=worktree)
    assert git("log", "-1", "--format=%ae", cwd=worktree).stdout.strip() == "original@example.com"
    assert git("log", "-1", "--format=%B", cwd=worktree).stdout == message
