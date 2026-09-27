"""Pure decisions for the PR gate; no GitHub access."""

from scripts.pr_gate import GATE_MARKER, decide


HEAD = "abcdef0123456789abcdef0123456789abcdef01"
BOT = "chatgpt-codex-connector[bot]"


def comment(body, login="maintainer", association="OWNER", **extra):
    return {"body": body, "user": {"login": login}, "author_association": association, **extra}


def snapshot():
    return {
        "pr": {
            "number": 42, "title": "Fix review gate", "draft": False, "mergeable": True,
            "head": {"sha": HEAD, "repo": {"full_name": "team/repo"}},
            "base": {"repo": {"full_name": "team/repo"}}, "labels": [],
        },
        "head_committed_at": "2026-09-28T09:00:00Z",
        "issue_comments": [comment(
            "<!-- codex-pull-request-review-summary -->\n| Review | Status | Commit | Review trigger |\n"
            "| --- | --- | --- | --- |\n| Code Review | Completed | `abcdef0` | PR opened |",
            BOT, "NONE", id=1, updated_at="2026-09-28T09:10:00Z",
        )],
        "review_comments": [], "reactions": [],
        "check_runs": [{"name": "pytest", "conclusion": "success"}],
    }


def finding(badge="P1", body="Fix the parser", **extra):
    return comment(f"[{badge}] {body}", BOT, "NONE", commit_id=HEAD,
                   html_url="https://github.com/team/repo/pull/42#discussion_r1", **extra)


def test_external_comments_cannot_raise_round_count_or_merge():
    snap = snapshot()
    snap["issue_comments"] += [comment("@codex review", f"stranger-{i}", "NONE") for i in range(30)]
    snap["review_comments"] = [finding()]
    assert decide(snap).kind == "none"


def test_fork_never_merges():
    snap = snapshot()
    snap["pr"]["head"]["repo"]["full_name"] = "outsider/repo"
    assert decide(snap).kind == "none"
    assert "포크" in " ".join(decide(snap).reasons)


def test_draft_or_unmergeable_pr_waits():
    snap = snapshot()
    snap["pr"]["draft"] = True
    assert decide(snap).kind == "none"
    snap["pr"]["draft"] = False
    snap["pr"]["mergeable"] = None
    assert decide(snap).kind == "none"


def test_running_latest_review_waits_even_at_cap():
    snap = snapshot()
    snap["issue_comments"][0]["body"] = (
        "<!-- codex-pull-request-review-summary -->\n"
        "| Review | Status | Commit |\n| --- | --- | --- |\n"
        "| Code Review | Running | `abcdef0` |\n"
        "| Code Review | Completed | `abcdef0` |"
    )
    snap["issue_comments"] += [comment("@codex review") for _ in range(9)]
    assert decide(snap).kind == "none"


def test_old_thumb_is_invalid_and_head_thumb_allows_p2():
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    snap["reactions"] = [comment("", BOT, "NONE", content="+1", created_at="2026-09-28T08:59:59Z")]
    assert decide(snap).kind == "none"
    snap["reactions"][0]["created_at"] = "2026-09-28T09:00:01Z"
    assert decide(snap).kind == "merge"


def test_p1_at_cap_calls_pi_with_title_and_same_head_is_idempotent():
    snap = snapshot()
    snap["review_comments"] = [finding("P1", "Prevent data loss")]
    snap["issue_comments"] += [comment("@codex review") for _ in range(9)]
    action = decide(snap)
    assert action.kind == "needs_pi"
    assert "Prevent data loss" in " ".join(action.reasons)
    snap["issue_comments"].append(comment(
        f"{GATE_MARKER}\nneeds_pi head={HEAD}", "github-actions[bot]", "NONE"))
    assert decide(snap).kind == "none"


def test_p2_only_merges_and_creates_linked_followup():
    snap = snapshot()
    snap["review_comments"] = [finding("P2", "Improve errors")]
    snap["reactions"] = [comment("", BOT, "NONE", content="+1", created_at="2026-09-28T09:01:00Z")]
    action = decide(snap)
    assert action.kind == "merge"
    assert action.followups == [{
        "title": "PR #42 follow-up: Improve errors",
        "body": "원본 지적:\n\n[P2] Improve errors\n\n링크: https://github.com/team/repo/pull/42#discussion_r1",
    }]


def test_failed_or_pending_check_blocks_merge():
    for conclusion in ("failure", None, "cancelled"):
        snap = snapshot()
        snap["check_runs"] = [{"name": "pytest", "conclusion": conclusion}]
        assert decide(snap).kind == "none"
    snap = snapshot()
    snap["check_runs"] = []
    assert decide(snap).kind == "none"


def test_warn_once_with_trusted_top_level_mentions_only():
    snap = snapshot()
    snap["review_comments"] = [finding()]
    snap["issue_comments"] += [comment("@codex review", association="MEMBER") for _ in range(7)]
    snap["review_comments"].append(finding(body="@codex review", in_reply_to_id=7))
    assert decide(snap).kind == "warn"
    snap["issue_comments"].append(comment(
        f"{GATE_MARKER}\nwarn cap=10", "github-actions[bot]", "NONE"))
    assert decide(snap).kind == "none"
    snap["issue_comments"][-1]["user"]["login"] = "maintainer"
    snap["issue_comments"][-1]["author_association"] = "OWNER"
    assert decide(snap).kind == "none"


def test_stale_review_and_old_findings_do_not_merge_wrong_head():
    snap = snapshot()
    snap["issue_comments"][0]["body"] = snap["issue_comments"][0]["body"].replace("abcdef0", "1234567")
    assert decide(snap).kind == "none"
    snap = snapshot()
    old = finding()
    old["commit_id"] = "1234567890123456789012345678901234567890"
    snap["review_comments"] = [old]
    assert decide(snap).kind == "merge"


def test_spoofed_summary_and_reaction_do_not_count():
    snap = snapshot()
    snap["issue_comments"][0]["user"]["login"] = "outsider"
    assert decide(snap).kind == "none"
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    snap["reactions"] = [comment("", "outsider", "NONE", content="+1", created_at="2026-09-28T10:00:00Z")]
    assert decide(snap).kind == "none"
