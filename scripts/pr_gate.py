"""Codex PR review gate. Only the standard library is required."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


BOT = "chatgpt-codex-connector[bot]"
GATE_BOT = "github-actions[bot]"
GATE_MARKER = "<!-- labhq-pr-gate -->"
SUMMARY_MARKER = "codex-pull-request-review-summary"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
MENTION = re.compile(r"(?<![\w])@codex\s+review\b", re.I)
BADGE = re.compile(
    r"!\[P(?P<image>[12]) Badge\]\(https://img\.shields\.io/badge/P(?P=image)-[^)\s]+\)"
    r"|\[P(?P<plain>[12])\]", re.I,
)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)
FOLLOWUP_MARKER = re.compile(r"<!-- labhq-pr-gate followup pr=\d+ comment=(\d+) -->")
GATE_CHECK = re.compile(r"(?:select|gate(?: \(\d+\))?)\Z")


class GitHubPermissionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Action:
    kind: str
    reasons: list[str] = field(default_factory=list)
    followups: list[dict[str, str]] = field(default_factory=list)


def _login(item: dict) -> str:
    return (item.get("user") or {}).get("login", "")


def _trusted(item: dict) -> bool:
    return _login(item) == BOT or item.get("author_association") in TRUSTED


def _latest_review(comments: list[dict]) -> tuple[str, str] | None:
    summaries = [c for c in comments if _login(c) == BOT and SUMMARY_MARKER in (c.get("body") or "")]
    if not summaries:
        return None
    body = max(summaries, key=lambda c: (c.get("updated_at") or c.get("created_at") or "", c.get("id", 0))).get("body") or ""
    # The bot updates a Markdown table, newest review first. Ignore the table header.
    for line in body.splitlines():
        if "|" not in line or not re.search(r"\b(Completed|Running)\b", line, re.I):
            continue
        match = SHA.search(line)
        if re.search(r"\bRunning\b", line, re.I):
            return "running", match.group().lower() if match else ""
        if match:
            return "completed", match.group().lower()
    return None


def _finding(comment: dict, number: int) -> tuple[str, str, dict[str, str]]:
    body = (comment.get("body") or "").strip()
    badge = BADGE.search(body)
    severity = (badge.group("image") or badge.group("plain")) if badge else "1"  # Unknown severity is unsafe to waive.
    title = next((line.strip(" #*` ") for line in body.splitlines() if line.strip()), "Codex finding")
    title = re.sub(r"</?sub>", "", BADGE.sub("", title), flags=re.I).strip(" #*` :")[:100] or "Codex finding"
    issue_body = f"원본 지적:\n\n{body}\n\n링크: {comment.get('html_url') or ''}"
    if severity == "2":
        issue_body += f"\n\n<!-- labhq-pr-gate followup pr={number} comment={comment['id']} -->"
    return severity, title, {
        "title": f"PR #{number} follow-up: {title}",
        "body": issue_body,
    }


def decide(snapshot: dict, *, cap: int = 10, warn_at: int = 8) -> Action:
    """Return a decision without API calls or mutations."""
    pr = snapshot["pr"]
    head = pr["head"]["sha"]
    comments = snapshot.get("issue_comments", [])
    rounds = 1 + sum(
        bool(MENTION.search(c.get("body") or ""))
        for c in comments
        if _trusted(c) and _login(c) != BOT and GATE_MARKER not in (c.get("body") or "")
    )
    review = _latest_review(comments)
    if review and review[0] == "running":
        return Action("none", ["봇 리뷰가 아직 완료되지 않았습니다."])
    gate_comments = [
        c.get("body") or "" for c in comments
        if (_login(c) == GATE_BOT or _trusted(c)) and GATE_MARKER in (c.get("body") or "")
    ]
    pi_called_for_head = any(f"needs_pi head={head}" in body for body in gate_comments)
    def wait_for_review(reason: str) -> Action:
        if rounds >= warn_at and not any("warn cap=" in body for body in gate_comments):
            return Action("warn", [f"봇 리뷰 {rounds}/{cap}회. 남은 횟수를 확인하세요.", reason])
        return Action("none", [reason])

    if not review:
        if rounds >= cap:
            if pi_called_for_head:
                return Action("none", ["현재 head의 PI 호출을 이미 남겼습니다."])
            return Action("needs_pi", ["상한 도달 뒤 봇 리뷰 요약이 없음"])
        return wait_for_review("봇 리뷰가 아직 완료되지 않았습니다.")
    reviewed_head = head.lower().startswith(review[1])
    if not reviewed_head:
        if rounds >= cap:
            if pi_called_for_head:
                return Action("none", ["현재 head의 PI 호출을 이미 남겼습니다."])
            return Action("needs_pi", ["상한 도달 뒤 새 커밋을 봇이 보지 않았음"])
        return wait_for_review("최신 봇 리뷰가 현재 head를 보지 않았습니다.")

    findings = [
        _finding(c, pr["number"])
        for c in snapshot.get("review_comments", [])
        if _login(c) == BOT and c.get("original_commit_id") == head and not c.get("in_reply_to_id")
    ]
    p1 = [f[1] for f in findings if f[0] == "1"]
    p2 = [f[2] for f in findings if f[0] == "2"]
    checks = [c for c in snapshot.get("check_runs", []) if not GATE_CHECK.fullmatch(c.get("name") or "")]
    bad_checks = [c.get("name") or "unnamed" for c in checks if c.get("conclusion") not in {"success", "neutral", "skipped"}]
    reasons = []
    if p1:
        reasons.append("남은 P1: " + ", ".join(p1))
    if bad_checks:
        reasons.append("미통과 check: " + ", ".join(bad_checks))
    if not checks:
        reasons.append("현재 head의 check run이 없습니다.")
    if pr.get("mergeable") is not True:
        reasons.append("PR이 mergeable 상태가 아닙니다.")
    if pr.get("draft"):
        reasons.append("draft PR입니다.")
    head_repo = ((pr["head"].get("repo") or {}).get("full_name"))
    base_repo = ((pr["base"].get("repo") or {}).get("full_name"))
    if not head_repo or head_repo != base_repo:
        reasons.append("포크 PR 또는 저장소 확인 불가입니다.")
    if not reasons:
        return Action("merge", [], p2)

    if rounds >= cap:
        if pi_called_for_head:
            return Action("none", ["현재 head의 PI 호출을 이미 남겼습니다."])
        return Action("needs_pi", reasons)
    if rounds >= warn_at and not any("warn cap=" in body for body in gate_comments):
        return Action("warn", [f"봇 리뷰 {rounds}/{cap}회. 남은 횟수를 확인하세요."] + reasons)
    return Action("none", reasons)


def select_pr_numbers(event_name: str, event: dict, *, open_pr_numbers=()) -> list[int]:
    """Select PRs from a trusted Actions event or the scheduled open-PR list."""
    if event_name == "issue_comment":
        issue = event.get("issue") or {}
        numbers = [issue["number"]] if "pull_request" in issue else []
    elif event_name == "workflow_run":
        numbers = [pr["number"] for pr in (event.get("workflow_run") or {}).get("pull_requests", [])]
    elif event_name in {"pull_request_review_comment", "pull_request_review"}:
        pr = event.get("pull_request") or {}
        numbers = [pr["number"]] if pr else []
    elif event_name == "workflow_dispatch":
        numbers = [(event.get("inputs") or {})["pr"]]
    elif event_name == "schedule":
        numbers = open_pr_numbers
    else:
        numbers = []
    return sorted({int(number) for number in numbers if int(number) > 0})


class GitHub:
    def __init__(self, repo: str, token: str):
        self.root = f"https://api.github.com/repos/{repo}"
        self.token = token

    def request(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(self.root + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        })
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error).get("message", "")
            except (ValueError, AttributeError):
                message = ""
            if method != "GET" and error.code == 403 and "Resource not accessible by integration" in message:
                raise GitHubPermissionError(f"GitHub API write permission denied: {method} {path}") from error
            raise RuntimeError(f"GitHub API {method} {path}: HTTP {error.code}") from error

    def pages(self, path: str) -> list[dict]:
        result = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if isinstance(batch, dict):
                batch = batch["check_runs"]
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1


def snapshot_for(api: GitHub, number: int) -> dict:
    pr = api.request("GET", f"/pulls/{number}")
    if pr.get("state") != "open":
        raise ValueError("Only open PRs can be evaluated")
    sha = pr["head"]["sha"]
    same_repo = ((pr["head"].get("repo") or {}).get("full_name") ==
                 (pr["base"].get("repo") or {}).get("full_name"))
    return {
        "pr": pr,
        "issue_comments": api.pages(f"/issues/{number}/comments"),
        "review_comments": api.pages(f"/pulls/{number}/comments"),
        "reactions": api.pages(f"/issues/{number}/reactions"),
        "check_runs": api.pages(f"/commits/{quote(sha)}/check-runs") if same_repo else [],
    }


def apply(api: GitHub, snapshot: dict, action: Action) -> None:
    pr = snapshot["pr"]
    number = pr["number"]
    head = pr["head"]["sha"]
    # A finding title can contain a mention; gate comments must never summon Codex.
    safe_reasons = [reason.replace("@", "@\u200b") for reason in action.reasons]
    if action.kind == "warn":
        body = f"{GATE_MARKER}\nwarn cap=10\n" + "\n".join(safe_reasons)
        api.request("POST", f"/issues/{number}/comments", {"body": body})
    elif action.kind == "needs_pi":
        if "needs-pi" not in {label["name"] for label in pr.get("labels", [])}:
            try:
                api.request("GET", "/labels/needs-pi")
            except RuntimeError as error:
                if "HTTP 404" not in str(error):
                    raise
                api.request("POST", "/labels", {
                    "name": "needs-pi", "color": "b60205",
                    "description": "Codex review cap reached; PI decision required",
                })
            api.request("POST", f"/issues/{number}/labels", {"labels": ["needs-pi"]})
        body = f"{GATE_MARKER}\nneeds_pi head={head}\nPI 확인 필요: " + "\n".join(safe_reasons)
        api.request("POST", f"/issues/{number}/comments", {"body": body})
    elif action.kind == "merge":
        if action.followups:
            existing = api.pages("/issues?state=all")
            bodies = [
                item.get("body") or "" for item in existing
                if "pull_request" not in item and (_trusted(item) or _login(item) == GATE_BOT)
            ]
            for issue in action.followups:
                marker = FOLLOWUP_MARKER.search(issue["body"])
                if marker is None:
                    raise ValueError("P2 follow-up is missing its review comment marker")
                fragment = re.compile(rf"#discussion_r{marker.group(1)}(?!\d)")
                if any(marker.group() in body or fragment.search(body) for body in bodies):
                    continue
                api.request("POST", "/issues", issue)
                bodies.append(issue["body"])
        merged = api.request("PUT", f"/pulls/{number}/merge", {
            "commit_title": f"{pr['title']} (#{number})", "sha": head, "merge_method": "squash",
        })
        if not merged.get("merged"):
            raise RuntimeError("GitHub did not confirm the squash merge")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) or args.pr < 1:
        parser.error("--repo must be OWNER/REPO and --pr must be positive")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN is required")
    api = GitHub(args.repo, token)
    try:
        snap = snapshot_for(api, args.pr)
    except ValueError as error:
        print(json.dumps({"action": "none", "reasons": [str(error)]}, ensure_ascii=False))
        return 0
    action = decide(snap)
    print(json.dumps({"action": action.kind, "reasons": action.reasons, "followups": action.followups}, ensure_ascii=False))
    if not args.dry_run:
        try:
            apply(api, snap, action)
        except GitHubPermissionError as error:
            print(f"warning: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
