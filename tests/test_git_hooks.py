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


@pytest.mark.parametrize("linked", [False, True], ids=["checkout", "worktree"])
def test_global_model_handoff_is_consumed_before_bot_rewrite(history, tmp_path, linked):
    repo, home, git = history
    cwd = repo
    if linked:
        cwd = tmp_path / "linked"
        git("worktree", "add", "-qb", "linked", str(cwd))
    global_hooks(home)
    hooks = home / ".git-template" / "hooks"
    (hooks / "commit-msg").write_text('''#!/bin/sh
model=$(sed -n 's/^Co-[Aa]uthored-[Bb]y: Codex (\\([^)]*\\)).*/\\1/p' "$1")
if [ -n "$model" ]; then
  state=$(git rev-parse --git-path labhq-commit-model)
  printf '%s\\n' "$model" > "$state"
fi
sed -i.bak '/^[Cc]o-[Aa]uthored-[Bb]y:/d' "$1"
''', encoding="utf-8", newline="\n")
    (hooks / "post-commit").write_text('''#!/bin/sh
[ -n "$LABHQ_POSTCOMMIT_AMEND" ] && exit 0
state=$(git rev-parse --git-path labhq-commit-model)
name=Agent
if [ -f "$state" ]; then name="Agent ($(cat "$state"))"; rm -f "$state"; fi
LABHQ_POSTCOMMIT_AMEND=1 git -c user.name="$name" -c user.email=agent@example.com commit --amend --allow-empty --no-edit --no-verify --quiet --author="$name <agent@example.com>"
''', encoding="utf-8", newline="\n")
    git("commit", "--allow-empty", "-qm", "new\n\nCo-Authored-By: Codex (test-model) <noreply@openai.com>", cwd=cwd)
    assert git("log", "-1", "--format=%an", cwd=cwd).stdout.strip() == "Agent (test-model)"
    state = Path(git("rev-parse", "--git-path", "labhq-commit-model", cwd=cwd).stdout.strip())
    assert not (cwd / state).exists()
    git("commit", "--allow-empty", "-qm", "next without model", cwd=cwd)
    assert git("log", "-1", "--format=%an", cwd=cwd).stdout.strip() == "Agent"


@pytest.mark.parametrize("returncode", [0, 1], ids=["declined", "failed"])
def test_global_rewrite_without_amend_keeps_message_and_cleans_state(history, returncode):
    repo, home, git = history
    global_hooks(home)
    (home / ".git-template" / "hooks" / "post-commit").write_text(
        f"#!/bin/sh\nexit {returncode}\n", encoding="utf-8", newline="\n")
    state = repo / git("rev-parse", "--git-path", "labhq-commit-model").stdout.strip()
    state.write_text("stale-model", encoding="utf-8")
    git("commit", "--allow-empty", "-qm", f"new\n\n{TRAILER}")
    assert git("log", "-1", "--format=%ae").stdout.strip() == "operator@example.com"
    message = git("log", "-1", "--format=%B").stdout
    assert TRAILER in message and AGENT_TRAILER in message
    assert not state.exists()
    assert not list((repo / ".git").glob("labhq-commit-context.message.*"))


def test_staff_session_keeps_global_trailer_without_bot_identity(history, monkeypatch):
    _, home, git = history
    global_hooks(home)
    hooks = home / ".git-template" / "hooks"
    (hooks / "commit-msg").write_text('''#!/bin/sh
git interpret-trailers --in-place --trailer 'Co-Authored-By: Staff Agent <staff@example.com>' "$1"
''', encoding="utf-8", newline="\n")
    monkeypatch.setenv("LABHQ_COMMIT_AGENT", "none")
    git("commit", "--allow-empty", "-qm", "staff work")
    assert git("log", "-1", "--format=%ae|%ce").stdout.strip() == "operator@example.com|operator@example.com"
    assert "Staff Agent <staff@example.com>" in git("log", "-1", "--format=%B").stdout


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


# ---- #808: only a real trailer counts, and a model is filled in only when the session names it ----
OLD_CODEX = "<noreply@openai.com>"
CODEX_BOT = "<199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>"


def run_commit_msg(history, message, **env):
    """Run the repository commit-msg hook on a temporary message file, exactly as git would."""
    repo, _home, _git = history
    path = repo / "MSG.txt"
    path.write_text(message, encoding="utf-8", newline="\n")
    child = {k: v for k, v in os.environ.items() if not k.startswith(("LABHQ_COMMIT_", "CODEX_", "CLAUDECODE", "GEMINI_"))}
    child.update({"LABHQ_COMMIT_AGENT": "codex"} if "LABHQ_COMMIT_AGENT" not in env else {})
    child.update({k: v for k, v in env.items() if v is not None})
    done = subprocess.run(["sh", str(repo / ".githooks" / "commit-msg"), str(path)], cwd=repo, env=child,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr  # the hook never blocks a commit
    return path.read_text(encoding="utf-8")


def trailers(text):
    return subprocess.run(["git", "interpret-trailers", "--parse"], input=text, capture_output=True, text=True,
                          encoding="utf-8", timeout=30).stdout.splitlines()


def test_a_trailer_example_in_the_body_is_text_and_a_real_trailer_is_still_added(history):
    # The example names this very agent's address: the old whole-message grep took it for the real attribution.
    body = f"Add the hook\n\nExample of the format:\n\nCo-authored-by: Codex (gpt-6) {CODEX_BOT}\n\nMore prose here.\n"
    out = run_commit_msg(history, body, LABHQ_COMMIT_MODEL="gpt-6")
    assert out.startswith(body)  # the example and every other body line survive untouched
    assert trailers(out) == ["Co-authored-by: Codex (gpt-6) " + CODEX_BOT]


def test_the_old_codex_address_is_replaced_only_in_the_trailer_block(history):
    message = (f"Explain the old address\n\nSet Co-authored-by lines like this:\n\n"
               f"Co-authored-by: Codex {OLD_CODEX}\n\nafter prose\n\nCo-Authored-By: Codex (gpt-6) {OLD_CODEX}\n")
    out = run_commit_msg(history, message)
    assert f"\nCo-authored-by: Codex {OLD_CODEX}\n" in out  # the body example keeps the address it showed
    assert trailers(out) == ["Co-Authored-By: Codex (gpt-6) " + CODEX_BOT]  # the real trailer got the bot address
    assert out.count(CODEX_BOT) == 1


@pytest.mark.parametrize("model", [None, "gpt-6"])
def test_a_normal_trailer_is_left_alone_and_a_second_run_changes_nothing(history, model):
    once = run_commit_msg(history, f"Subject\n\nbody\n\nCo-Authored-By: Codex (gpt-6) {CODEX_BOT}\n", LABHQ_COMMIT_MODEL=model)
    assert once == f"Subject\n\nbody\n\nCo-Authored-By: Codex (gpt-6) {CODEX_BOT}\n"
    assert run_commit_msg(history, once, LABHQ_COMMIT_MODEL=model) == once  # an amend reruns the hook: idempotent


def test_a_missing_trailer_is_added_once_and_an_amend_does_not_add_it_again(history):
    once = run_commit_msg(history, "Subject\n\nbody\n", LABHQ_COMMIT_MODEL="gpt-6")
    assert trailers(once) == ["Co-authored-by: Codex (gpt-6) " + CODEX_BOT]
    assert run_commit_msg(history, once, LABHQ_COMMIT_MODEL="gpt-6") == once


def test_a_bare_agent_trailer_gets_the_model_only_when_the_session_names_it(history):
    bare = "Subject\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    env = {"LABHQ_COMMIT_AGENT": "claude"}
    assert run_commit_msg(history, bare, **env) == bare  # no model in the environment: nothing is guessed
    filled = run_commit_msg(history, bare, LABHQ_COMMIT_MODEL="Fable 5.1", **env)
    assert trailers(filled) == ["Co-Authored-By: Claude (Fable 5.1) <noreply@anthropic.com>"]
    assert filled.startswith("Subject\n\nbody\n\n")
    assert run_commit_msg(history, filled, LABHQ_COMMIT_MODEL="Fable 5.1", **env) == filled  # idempotent
    named = "Subject\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n"
    assert run_commit_msg(history, named, LABHQ_COMMIT_MODEL="Fable 5.1", **env) == named  # another model name stays


def test_an_unknown_environment_never_blocks_and_leaves_the_message(history):
    message = "Subject\n\nCo-authored-by: Someone <someone@example.com>\n"
    assert run_commit_msg(history, message, LABHQ_COMMIT_AGENT=None) == message
    assert run_commit_msg(history, message, LABHQ_COMMIT_AGENT="unknown-agent") == message


def test_trailing_comment_lines_do_not_hide_the_trailer_block(history):
    message = f"Subject\n\nbody\n\nCo-Authored-By: Codex {OLD_CODEX}\n\n# Please enter the commit message\n# Lines starting with '#' are ignored\n"
    out = run_commit_msg(history, message)
    assert f"Co-Authored-By: Codex {CODEX_BOT}\n" in out and OLD_CODEX not in out
    assert out.endswith("# Lines starting with '#' are ignored\n")


@pytest.mark.parametrize("comment_char,comments", [(";", "; Please enter the commit message\n; Lines starting with ';' are ignored\n"),
                                                    ("auto", "@ Please enter the commit message\n")])
def test_the_configured_comment_character_does_not_hide_the_trailer_block(history, comment_char, comments):
    _repo, _home, git = history
    git("config", "core.commentChar", comment_char)
    out = run_commit_msg(history, f"Subject\n\nbody\n\nCo-Authored-By: Codex {OLD_CODEX}\n\n{comments}")
    assert f"Co-Authored-By: Codex {CODEX_BOT}\n" in out and OLD_CODEX not in out
    assert out.endswith(comments)


def test_a_scissors_divider_keeps_the_diff_below_it_out_of_the_message(history):
    """`git commit -v` appends `# ---- >8 ----` and the diff; only the message above is read or rewritten."""
    scissors = "# ------------------------ >8 ------------------------\n"
    diff = (f"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+Co-Authored-By: Codex {OLD_CODEX}\n\n"
            f"+Co-authored-by: Example <example@example.com>\n")
    out = run_commit_msg(history, f"Subject\n\nbody\n\nCo-Authored-By: Codex {OLD_CODEX}\n\n{scissors}{diff}")
    assert f"Co-Authored-By: Codex {CODEX_BOT}\n\n{scissors}" in out  # the real trailer above the divider was rewritten
    assert out.endswith(scissors + diff)  # the diff below it, with its trailer-like lines, is byte for byte the same
    added = run_commit_msg(history, f"Subject\n\nbody\n\n{scissors}{diff}", LABHQ_COMMIT_MODEL="gpt-6")
    assert added.endswith(scissors + diff) and any(line.startswith("Co-authored-by: Codex (gpt-6)") for line in added.splitlines()[:6])


def test_with_auto_only_the_character_the_template_names_is_a_comment(history):
    _repo, _home, git = history
    git("config", "core.commentChar", "auto")
    # No template line (git commit -m): an ordinary paragraph starting with a candidate character is message text.
    prose = "@ordinary final paragraph\n"
    out = run_commit_msg(history, f"Subject\n\nCo-Authored-By: Codex {OLD_CODEX}\n\n{prose}")
    assert f"Co-Authored-By: Codex {OLD_CODEX}\n" in out and prose in out  # not mistaken for a trailer block
    assert trailers(out)[-1].startswith("Co-authored-by: Codex")  # the real trailer is added after the text
    # With a template, its first line names the comment character; other characters stay text.
    template = "# Please enter the commit message for your changes.\n"
    out = run_commit_msg(history, f"Subject\n\nCo-Authored-By: Codex {OLD_CODEX}\n\n{template}")
    assert f"Co-Authored-By: Codex {CODEX_BOT}\n" in out and out.endswith(template)


@pytest.mark.parametrize("gap", ["   ", "\t", " \t "])
def test_a_bare_trailer_with_irregular_spacing_still_gets_the_model(history, gap):
    bare = f"Subject\n\nbody\n\nCo-Authored-By: Claude{gap}<noreply@anthropic.com>\n"
    out = run_commit_msg(history, bare, LABHQ_COMMIT_AGENT="claude", LABHQ_COMMIT_MODEL="Fable 5.1")
    assert trailers(out) == ["Co-Authored-By: Claude (Fable 5.1) <noreply@anthropic.com>"]
    assert run_commit_msg(history, out, LABHQ_COMMIT_AGENT="claude", LABHQ_COMMIT_MODEL="Fable 5.1") == out
