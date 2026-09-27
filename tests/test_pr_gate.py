"""Pure decisions for the PR gate; no GitHub access."""

from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts import pr_gate as gate_module
from scripts.pr_gate import GATE_MARKER, apply, decide, select_pr_numbers


HEAD = "abcdef0123456789abcdef0123456789abcdef01"
NEW_HEAD = "1234567890123456789012345678901234567890"
BOT = "chatgpt-codex-connector[bot]"
FIXTURES = Path(__file__).parent / "fixtures" / "pr_gate"


def real_sample(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
    return comment(f"[{badge}] {body}", BOT, "NONE", commit_id=HEAD, original_commit_id=HEAD,
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


def test_stale_completed_review_at_cap_calls_pi_once_for_new_head():
    snap = snapshot()
    snap["pr"]["head"]["sha"] = NEW_HEAD
    snap["issue_comments"] += [comment("@codex review") for _ in range(9)]
    action = decide(snap)
    assert action.kind == "needs_pi"
    assert "상한 도달 뒤 새 커밋을 봇이 보지 않았음" in action.reasons
    snap["issue_comments"].append(comment(
        f"{GATE_MARKER}\nneeds_pi head={NEW_HEAD}", "github-actions[bot]", "NONE"))
    assert decide(snap).kind == "none"


def test_stale_review_before_cap_still_waits():
    snap = snapshot()
    snap["pr"]["head"]["sha"] = NEW_HEAD
    snap["issue_comments"] += [comment("@codex review") for _ in range(8)]
    assert decide(snap).kind == "none"


def test_running_stale_review_at_cap_still_waits():
    snap = snapshot()
    snap["pr"]["head"]["sha"] = NEW_HEAD
    snap["issue_comments"][0]["body"] = snap["issue_comments"][0]["body"].replace("Completed", "Running")
    snap["issue_comments"] += [comment("@codex review") for _ in range(9)]
    assert decide(snap).kind == "none"


def test_running_review_without_commit_sha_at_cap_still_waits():
    snap = snapshot()
    snap["issue_comments"][0]["body"] = (
        "<!-- codex-pull-request-review-summary -->\n"
        "| Review | Status | Commit |\n| --- | --- | --- |\n"
        "| Code Review | Running | — |"
    )
    snap["issue_comments"] += [comment("@codex review") for _ in range(9)]
    assert decide(snap).kind == "none"


def test_missing_review_at_cap_calls_pi_once():
    snap = snapshot()
    snap["pr"]["head"]["sha"] = NEW_HEAD
    snap["issue_comments"] = [comment("@codex review") for _ in range(9)]
    action = decide(snap)
    assert action.kind == "needs_pi"
    assert "봇 리뷰 요약이 없음" in " ".join(action.reasons)
    snap["issue_comments"].append(comment(
        f"{GATE_MARKER}\nneeds_pi head={NEW_HEAD}", "github-actions[bot]", "NONE"))
    assert decide(snap).kind == "none"


def test_unparsed_review_at_cap_calls_pi_but_before_cap_waits():
    snap = snapshot()
    snap["issue_comments"][0]["body"] = "<!-- codex-pull-request-review-summary -->\nNo table yet"
    snap["issue_comments"] += [comment("@codex review") for _ in range(8)]
    assert decide(snap).kind == "none"
    snap["issue_comments"].append(comment("@codex review"))
    assert decide(snap).kind == "needs_pi"


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
    snap["review_comments"] = [comment("Investigate edge case", BOT, "NONE",
                                       commit_id=HEAD, original_commit_id=HEAD)]
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


def test_gate_jobs_do_not_block_passing_tests():
    snap = snapshot()
    snap["check_runs"] = [
        {"name": "select", "conclusion": "failure"},
        {"name": "gate", "conclusion": None},
        {"name": "gate (42)", "conclusion": "cancelled"},
        {"name": "test", "conclusion": "success"},
    ]
    assert decide(snap).kind == "merge"


def test_failed_test_still_blocks_after_gate_jobs_are_excluded():
    snap = snapshot()
    snap["check_runs"] = [
        {"name": "select", "conclusion": "failure"},
        {"name": "gate (42)", "conclusion": None},
        {"name": "test", "conclusion": "failure"},
    ]
    action = decide(snap)
    assert action.kind == "none"
    assert "미통과 check: test" in action.reasons
    snap["check_runs"] = snap["check_runs"][:2]
    assert "현재 head의 check run이 없습니다." in decide(snap).reasons


def test_write_permission_error_is_distinct_from_other_http_errors(monkeypatch):
    def denied(request, timeout):
        response = BytesIO(b'{"message":"Resource not accessible by integration"}')
        raise HTTPError(request.full_url, 403, "Forbidden", {}, response)

    monkeypatch.setattr(gate_module, "urlopen", denied)
    api = gate_module.GitHub("team/repo", "dummy")
    with pytest.raises(gate_module.GitHubPermissionError):
        api.request("POST", "/issues", {"title": "follow-up"})
    with pytest.raises(RuntimeError) as caught:
        api.request("GET", "/issues")
    assert not isinstance(caught.value, gate_module.GitHubPermissionError)


def test_cli_warns_and_succeeds_on_apply_permission_error(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    monkeypatch.setattr(gate_module.sys, "argv", ["pr_gate.py", "--repo", "team/repo", "--pr", "42"])
    monkeypatch.setattr(gate_module, "snapshot_for", lambda api, number: snapshot())

    def denied(api, snap, action):
        raise gate_module.GitHubPermissionError("write permission denied")

    monkeypatch.setattr(gate_module, "apply", denied)
    assert gate_module.main() == 0
    assert "warning: write permission denied" in capsys.readouterr().err


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
    old["original_commit_id"] = "1234567890123456789012345678901234567890"
    snap["review_comments"] = [old]
    assert decide(snap).kind == "merge"


def test_real_pr11_p2_badge_merges_and_creates_followup():
    findings = real_sample("real_bot_comments_pr11.json")
    snap = snapshot()
    snap["pr"]["number"] = 11
    snap["pr"]["head"]["sha"] = findings[0]["original_commit_id"]
    snap["issue_comments"] = real_sample("real_bot_summary_pr11.json")
    snap["review_comments"] = findings
    action = decide(snap)
    assert action.kind == "merge"
    assert len(action.followups) == 1
    assert action.followups[0]["title"].startswith("PR #11 follow-up: Honor transi")
    assert "followup pr=11 comment=4116885580" in action.followups[0]["body"]


def test_real_pr10_old_p1_is_excluded_but_two_current_p1_remain():
    findings = real_sample("real_bot_comments_pr10.json")
    snap = snapshot()
    snap["pr"]["number"] = 10
    snap["pr"]["head"]["sha"] = findings[1]["original_commit_id"]
    snap["issue_comments"][0]["body"] = snap["issue_comments"][0]["body"].replace("abcdef0", "a7e4350")
    snap["review_comments"] = findings
    action = decide(snap)
    assert action.kind == "none"
    reasons = " ".join(action.reasons)
    assert "상한 판정을 stale review 조기 반환보다 먼저 수행하세요" not in reasons
    assert "Codex의 실제 P2 badge 형식을 인식하세요" in reasons
    assert "Review가 없더라도 상한에서 PI를 호출하세요" in reasons


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
