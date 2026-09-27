"""Pure decisions for the PR gate; no GitHub access."""

import pytest

from scripts.pr_gate import GATE_MARKER, apply, decide, select_pr_numbers


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
        "issue_comments": [comment(
            "<!-- codex-pull-request-review-summary -->\n| Review | Status | Commit | Review trigger |\n"
            "| --- | --- | --- | --- |\n| Code Review | Completed | `abcdef0` | PR opened |",
            BOT, "NONE", id=1, updated_at="2026-09-28T09:10:00Z",
        )],
        "review_comments": [], "reactions": [],
        "check_runs": [{"name": "pytest", "conclusion": "success"}],
    }


def finding(badge="P1", body="Fix the parser", **extra):
    comment_id = extra.pop("id", 1)
    url = extra.pop("html_url", f"https://github.com/team/repo/pull/42#discussion_r{comment_id}")
    return comment(f"[{badge}] {body}", BOT, "NONE", commit_id=HEAD,
                   id=comment_id, html_url=url, **extra)


class FakeGitHub:
    def __init__(self, issues=(), fail_issue_number=None):
        self.issues = list(issues)
        self.fail_issue_number = fail_issue_number
        self.issue_posts = 0
        self.calls = []

    def pages(self, path):
        assert path == "/issues?state=all"
        self.calls.append(("GET", path))
        return self.issues

    def request(self, method, path, payload=None):
        self.calls.append((method, path))
        if (method, path) == ("POST", "/issues"):
            self.issue_posts += 1
            if self.issue_posts == self.fail_issue_number:
                raise RuntimeError("issue creation failed")
            self.issues.append({"body": payload["body"], "state": "open",
                                "user": {"login": "github-actions[bot]"}})
            return self.issues[-1]
        if (method, path) == ("PUT", "/pulls/42/merge"):
            return {"merged": True}
        raise AssertionError((method, path))


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


def test_reaction_age_does_not_gate_p2():
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    assert decide(snap).kind == "merge"
    snap["reactions"] = [comment("", BOT, "NONE", content="+1", created_at="2026-09-28T08:59:59Z")]
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
    action = decide(snap)
    assert action.kind == "merge"
    assert action.followups == [{
        "title": "PR #42 follow-up: Improve errors",
        "body": "원본 지적:\n\n[P2] Improve errors\n\n링크: https://github.com/team/repo/pull/42#discussion_r1"
                "\n\n<!-- labhq-pr-gate followup pr=42 comment=1 -->",
    }]


def test_followup_is_created_before_merge_and_retry_creates_only_one():
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    action = decide(snap)
    api = FakeGitHub()
    apply(api, snap, action)
    assert api.calls == [("GET", "/issues?state=all"), ("POST", "/issues"),
                         ("PUT", "/pulls/42/merge")]
    apply(api, snap, action)
    assert api.issue_posts == 1
    assert len(api.issues) == 1


def test_issue_creation_failure_prevents_merge_and_retry_finishes_remaining_issue():
    snap = snapshot()
    snap["review_comments"] = [finding("P2", "First", id=1), finding("P2", "Second", id=2)]
    action = decide(snap)
    api = FakeGitHub(fail_issue_number=2)
    with pytest.raises(RuntimeError, match="issue creation failed"):
        apply(api, snap, action)
    assert ("PUT", "/pulls/42/merge") not in api.calls
    assert len(api.issues) == 1
    apply(api, snap, action)
    assert len(api.issues) == 2
    assert api.issue_posts == 3  # First retry finds the first issue by marker.
    assert api.calls[-1] == ("PUT", "/pulls/42/merge")


def test_manual_closed_issue_with_original_link_prevents_duplicate():
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    manual = {"state": "closed", "body": "Tracked at https://github.com/team/repo/pull/42#discussion_r1",
              "user": {"login": "maintainer"}, "author_association": "OWNER"}
    api = FakeGitHub(issues=[manual])
    apply(api, snap, decide(snap))
    assert api.issue_posts == 0
    assert api.calls[-1] == ("PUT", "/pulls/42/merge")


def test_untrusted_issue_cannot_suppress_followup():
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    forged = {"state": "closed", "body": "<!-- labhq-pr-gate followup pr=42 comment=1 -->",
              "user": {"login": "outsider"}, "author_association": "NONE"}
    api = FakeGitHub(issues=[forged])
    apply(api, snap, decide(snap))
    assert api.issue_posts == 1


def test_unbadged_bot_finding_blocks_merge_and_calls_pi_at_cap():
    snap = snapshot()
    snap["review_comments"] = [comment("Investigate edge case", BOT, "NONE", commit_id=HEAD)]
    assert decide(snap).kind == "none"
    snap["issue_comments"] += [comment("@codex review") for _ in range(9)]
    action = decide(snap)
    assert action.kind == "needs_pi"
    assert "Investigate edge case" in " ".join(action.reasons)


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


def test_spoofed_summary_does_not_count_and_reaction_does_not_gate():
    snap = snapshot()
    snap["issue_comments"][0]["user"]["login"] = "outsider"
    assert decide(snap).kind == "none"
    snap = snapshot()
    snap["review_comments"] = [finding("P2")]
    snap["reactions"] = [comment("", "outsider", "NONE", content="+1", created_at="2026-09-28T10:00:00Z")]
    assert decide(snap).kind == "merge"


def test_event_selection_covers_ci_completion_schedule_and_existing_triggers():
    event = {"workflow_run": {"pull_requests": [{"number": 7}, {"number": 3}, {"number": 7}]}}
    assert select_pr_numbers("workflow_run", event) == [3, 7]
    assert select_pr_numbers("schedule", {}, open_pr_numbers=[5, 2, 5]) == [2, 5]
    assert select_pr_numbers("issue_comment", {"issue": {"number": 4}}) == []
    assert select_pr_numbers("issue_comment", {"issue": {"number": 4, "pull_request": {}}}) == [4]
    assert select_pr_numbers("issue_comment", {"issue": {"number": 4, "pull_request": {"url": "pr"}}}) == [4]
    for name in ("pull_request_review", "pull_request_review_comment"):
        assert select_pr_numbers(name, {"pull_request": {"number": 6}}) == [6]
    assert select_pr_numbers("workflow_dispatch", {"inputs": {"pr": "9"}}) == [9]
