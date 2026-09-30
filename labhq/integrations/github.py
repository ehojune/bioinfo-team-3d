"""Project updates on GitHub — so the PI can follow every project where they already work.

Per request tied to a project: one issue ("[labhq] …") that gets the plan, reviewer verdicts and the final
report as comments, and the final report committed to <reports_dir>/<date>-<request>.md.
Runs inside the gateway as an ordered event subscriber; failures never block the lab.

Publish guard: restricted-zone paths and secret-looking strings are redacted, and public repos stay
silent unless the project sets allow_public_reports: true.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Callable

import httpx

from ..policy import mentions_zone, restricted_paths
from ..settings import PolicySettings, ProjectSettings, Settings
from ..util import clip, short

if TYPE_CHECKING:
    from ..gateway.server import Hub

log = logging.getLogger("labhq.github")
MAX_BODY = 60_000  # GitHub caps comment bodies at 65,536 characters

SECRET_PATTERNS = [
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"sk-[A-Za-z0-9_\-]{20,}",
    r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{30,}", r"xox[abpr]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]


NETWORK_URL_PREFIX = re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]*)://\S*$")


def _outside_network_url(m: re.Match) -> str:
    """Redact a zone match unless it is the path part of a network URL (https://host/data/…)."""
    line_start = max(m.string.rfind(c, 0, m.start()) for c in " \t\r\n") + 1
    url = NETWORK_URL_PREFIX.search(m.string[line_start:m.start()])
    return m.group(0) if url and url.group(1).casefold() != "file" else "<restricted-zone>"


def root_zone_restricted(policy: PolicySettings) -> bool:
    """`/` or a drive root (`E:/`) is a restricted zone: nothing about the lab can be published safely."""
    return any(re.fullmatch(r"/?|[a-z]:/?", z.rstrip("/"), flags=re.IGNORECASE) for z in restricted_paths(policy))


def sanitize(text: str, policy: PolicySettings, extra_secrets: list[str] | tuple[str, ...] = ()) -> str:
    out = text or ""
    # Zones are normalized exactly as the access policy does (`.`/`..`, separators, case on Windows).
    normalized = restricted_paths(policy)
    if root_zone_restricted(policy):
        # A filesystem root (`/`, `E:/`) is restricted: every path on that root is controlled, and no text
        # boundary reliably separates one from prose or URLs. Publish nothing (fail closed).
        return "<restricted-zone>" if out else ""
    # 1) A line the access policy would treat as touching a zone is withheld whole. This reuses the policy's
    #    candidate extraction and lexical normalization (`/data/tmp/../cohort`, `file:///data/./cohort`,
    #    quoted or spaced names), and like the policy it ignores network URLs.
    if normalized:
        out = "\n".join("<restricted-zone>" if mentions_zone(line, normalized) else line for line in out.split("\n"))
    # 2) Literal zone text glued to other words (e.g. Korean "경로/data/…") is not a path candidate for the
    #    policy, so it is matched here. Names below a zone may contain spaces or quotes, so where the path ends
    #    is unknowable: fail closed to the end of the line.
    tail = r"[^\r\n]*"
    for zone in sorted({p.rstrip("/") for p in normalized}, key=len, reverse=True):
        pattern = r"[/\\]+".join(re.escape(part) for part in zone.split("/"))
        # The lookahead keeps the directory boundary: /data/cohort2 is not inside /data/cohort.
        out = re.sub(pattern + r"(?![\w.-])" + tail, _outside_network_url, out, flags=re.IGNORECASE)
    for pat in SECRET_PATTERNS:
        out = re.sub(pat, "<redacted-secret>", out)
    for secret in extra_secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "<redacted-secret>")
    return clip(out, MAX_BODY)


def codex_comment(body: str, mention: str = "@codex") -> str:
    """The one top-level comment that asks Codex to review; each @codex comment starts its own Codex session."""
    return body if mention in body else f"{mention} {body}"


class GitHubHTTPError(RuntimeError):
    def __init__(self, method: str, path: str, response: httpx.Response):
        super().__init__(f"GitHub {method} {path} → {response.status_code}: {response.text[:300]}")
        self.status_code = response.status_code
        self.rate_limited = (response.status_code == 403 and
                             (response.headers.get("x-ratelimit-remaining") == "0" or
                              "retry-after" in response.headers))


class GitHubClient:
    def __init__(self, token: str, api_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 clean: Callable[[str], str] | None = None):
        self.clean = clean or (lambda value: value)
        self.http = httpx.AsyncClient(base_url=api_url.rstrip("/"), transport=transport, timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "labhq",
        })

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            if "json" in kw:
                if method == "PUT" and "/contents/" in path:
                    payload = dict(kw["json"])
                    content = base64.b64decode(payload["content"]).decode("utf-8")
                    guarded = base64.b64encode(self.clean(content).encode("utf-8")).decode("ascii")
                    kw["json"] = self._clean_value({k: v for k, v in payload.items() if k != "content"})
                    kw["json"]["content"] = guarded
                else:
                    kw["json"] = self._clean_value(kw["json"])
        r = await self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise GitHubHTTPError(method, path, r)
        return r.json() if r.content else {}

    CONTROL_KEYS = frozenset({"branch", "sha", "ref"})  # GitHub control values, not published text

    def _clean_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, dict):
            return {self.clean(k) if isinstance(k, str) else k:
                    v if k in self.CONTROL_KEYS and isinstance(v, str) else self._clean_value(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean_value(v) for v in value]
        return value

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        return await self._req("POST", f"/repos/{repo}/issues", json={"title": title, "body": body, "labels": labels})

    async def find_marked_issue(self, repo: str, marker: str) -> dict | None:
        page = 1
        while True:
            issues = await self._req("GET", f"/repos/{repo}/issues",
                                     params={"state": "all", "per_page": 100, "page": page})
            match = next((issue for issue in issues if not issue.get("pull_request") and
                          marker in (issue.get("body") or "")), None)
            if match or len(issues) < 100:
                return match
            page += 1

    async def edit_issue(self, repo: str, number: int, body: str) -> dict:
        return await self._req("PATCH", f"/repos/{repo}/issues/{number}", json={"body": body})

    async def find_request_issue(self, repo: str, rid: str) -> dict | None:
        marker = f"<!-- labhq request {rid} -->"
        legacy = f"request id: `{rid}`"
        page = 1
        while True:
            issues = await self._req("GET", f"/repos/{repo}/issues",
                                     params={"state": "all", "per_page": 100, "page": page})
            match = next((issue for issue in issues if not issue.get("pull_request") and
                          (marker in (issue.get("body") or "") or legacy in (issue.get("body") or ""))), None)
            if match or len(issues) < 100:
                return match
            page += 1

    async def comment(self, repo: str, number: int, body: str) -> dict:
        return await self._req("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    async def find_issue_comment(self, repo: str, number: int, marker: str) -> dict | None:
        page = 1
        while True:
            comments = await self._req("GET", f"/repos/{repo}/issues/{number}/comments",
                                       params={"per_page": 100, "page": page})
            match = next((c for c in comments if marker in (c.get("body") or "")), None)
            if match or len(comments) < 100:
                return match
            page += 1

    async def close_issue(self, repo: str, number: int) -> dict:
        return await self._req("PATCH", f"/repos/{repo}/issues/{number}",
                               json={"state": "closed", "state_reason": "completed"})

    async def issue_is_closed(self, repo: str, number: int) -> bool:
        issue = await self._req("GET", f"/repos/{repo}/issues/{number}")
        return issue.get("state") == "closed"

    async def put_file(self, repo: str, path: str, content: str, message: str, branch: str) -> dict:
        path, content = self.clean(path), self.clean(content)  # branch is a ref, not published text
        sha = None
        r = await self.http.get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
        if r.status_code == 200:
            existing = r.json()
            sha = existing.get("sha")
            if existing.get("encoding") == "base64" and base64.b64decode(existing.get("content") or "").decode() == content:
                return {"content": {"html_url": existing.get("html_url")}, "unchanged": True}
        elif r.status_code != 404:
            r.raise_for_status()
        body = {"message": message, "branch": branch, "content": base64.b64encode(content.encode()).decode()}
        if sha:
            body["sha"] = sha
        return await self._req("PUT", f"/repos/{repo}/contents/{path}", json=body)

    async def request_codex_review(self, repo: str, pr: int, note: str = "", mention: str = "@codex") -> dict:
        return await self.comment(repo, pr, codex_comment("review" + (f"\n\n{note}" if note else ""), mention))


class ProjectReporter:
    HANDLED = {"request.created", "request.plan", "request.review", "recruit.done",
               "request.completed", "request.failed"}

    def __init__(self, hub: "Hub", settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.hub, self.s, self.transport = hub, settings, transport
        self.queue: asyncio.Queue | None = None
        self.worker: asyncio.Task | None = None
        self.issues: dict[str, int] = {rid: int(body["number"])
                                       for rid, body in hub.store.all("github_issue").items()}
        self._client: GitHubClient | None = None
        self._warned: set[str] = set()

    def enabled(self) -> bool:
        if root_zone_restricted(self.s.policy):
            # Publishing is off entirely: redacting every string would also corrupt protocol values
            # (issue state, refs) and leave the reporter retrying forever.
            if "root-zone" not in self._warned:
                self._warned.add("root-zone")
                log.warning("GitHub updates are off: a filesystem root is a restricted data zone")
            return False
        return any(p.repo for p in self.s.projects)

    def client(self) -> GitHubClient | None:
        if self._client is None:
            token = os.environ.get(self.s.github.token_env, "")
            if not token and self.transport is None:
                if "token" not in self._warned:
                    self._warned.add("token")
                    log.warning("GitHub updates are off: set %s on the gateway host", self.s.github.token_env)
                return None
            self._client = GitHubClient(token or "test-token", self.s.github.api_url, self.transport, self._clean)
        return self._client

    def submit(self, ev: dict) -> None:
        if ev.get("type") not in self.HANDLED or not ev.get("request_id"):
            return
        if self.queue is None:
            self.queue = asyncio.Queue()
            self.worker = asyncio.get_running_loop().create_task(self._run())
        self.queue.put_nowait(ev)

    async def drain(self) -> None:
        if self.queue is not None:
            await self.queue.join()

    async def _run(self) -> None:
        assert self.queue is not None
        while True:
            ev = await self.queue.get()
            try:
                delivered = await self.handle(ev)
                if delivered is not False:
                    self.hub.ack_reporter_delivery(ev)
            except Exception as e:
                log.warning("GitHub update failed (%s): %s", ev.get("type"), e)
                await self.hub.publish({"type": "github.failed", "ts": time.time(), "request_id": ev.get("request_id"),
                                        "data": {"event": ev.get("type"), "error": short(str(e), 300)}})
            finally:
                self.queue.task_done()

    def project_of(self, rid: str) -> ProjectSettings | None:
        return self.s.project((self.hub.requests.get(rid) or {}).get("project_id"))

    def _clean(self, text: str) -> str:
        return sanitize(text, self.s.policy, [self.s.gateway.client_token, self.s.gateway.runner_token])

    @staticmethod
    def _action_key(ev: dict, action: str) -> str:
        return f"{ev['request_id']}:{ev.get('seq') or ev['type']}:{action}"

    def _action(self, ev: dict, action: str) -> dict | None:
        return self.hub.store.get("github_action", self._action_key(ev, action))

    def _save_action(self, ev: dict, action: str, body: dict) -> None:
        self.hub.store.put("github_action", self._action_key(ev, action), body)

    async def _comment_once(self, ev: dict, gh: GitHubClient, repo: str, number: int,
                            body: str, kind: str) -> None:
        rid = ev["request_id"]
        marker = f"<!-- labhq {ev['type']} {rid} {ev.get('seq') or ev['type']} -->"
        saved = self._action(ev, "comment") or {}
        if saved.get("done"):
            return
        if not saved:
            self._save_action(ev, "comment", {"done": False, "marker": marker})
        find_comment = getattr(gh, "find_issue_comment", None)
        comment = await find_comment(repo, number, marker) if saved and find_comment else None
        if comment is None:
            comment = await gh.comment(repo, number,
                                       f"{clip(self._clean(body), MAX_BODY - len(marker) - 2)}\n\n{marker}")
        self._save_action(ev, "comment", {"done": True, "marker": marker,
                                          "url": comment.get("html_url")})
        await self._posted(rid, kind, comment.get("html_url"), number)

    async def _posted(self, rid: str, kind: str, url: str | None, number: int | None = None) -> None:
        await self.hub.publish({"type": "github.posted", "ts": time.time(), "request_id": rid,
                                "data": {"kind": kind, "url": url, "number": number}})

    async def handle(self, ev: dict) -> bool | None:
        rid = ev["request_id"]
        proj = self.project_of(rid)
        if not proj or not proj.repo or root_zone_restricted(self.s.policy):
            return
        if proj.visibility == "public" and not proj.allow_public_reports:
            if proj.id not in self._warned:
                self._warned.add(proj.id)
                log.warning("project %s is public; set allow_public_reports: true to post updates", proj.id)
            return
        gh = self.client()
        if gh is None:
            return False  # keep a terminal delivery until credentials are available
        typ, d = ev["type"], ev.get("data") or {}
        req = self.hub.requests.get(rid) or {}

        if typ == "request.created":
            if not proj.issues:
                return
            if rid in self.issues:
                return
            find_issue = getattr(gh, "find_request_issue", None)
            issue = await find_issue(proj.repo, rid) if find_issue else None
            if issue is None:
                issue = await gh.create_issue(proj.repo, f"[labhq] {short(self._clean(req.get('text', '')), 70)}",
                                              self._clean(self._issue_body(rid, req)), proj.labels)
            self.issues[rid] = issue["number"]
            self.hub.store.put("github_issue", rid, {"number": issue["number"]})
            await self._posted(rid, "issue", issue.get("html_url"), issue["number"])
            return

        num = self.issues.get(rid)
        if typ in ("request.plan", "request.review", "recruit.done", "request.completed", "request.failed") and proj.issues and num is None:
            return False  # keep delivery until the request issue can be opened
        if typ == "request.plan" and num:
            await self._comment_once(ev, gh, proj.repo, num, self._plan_md(d), "plan")
        elif typ == "request.review" and num:
            await self._comment_once(ev, gh, proj.repo, num, self._review_md(d), "review")
        elif typ == "recruit.done" and num:
            a = d.get("agent") or {}
            await self._comment_once(ev, gh, proj.repo, num,
                                     f"🐥 파견직 합류: **{a.get('name')}** (`{a.get('id')}`) — "
                                     f"수습 통과={d.get('passed_probation')}", "recruit")
        elif typ in ("request.completed", "request.failed"):
            report = d.get("report") or req.get("report") or d.get("error") or ""
            report_url = None
            if typ == "request.completed" and proj.commit_reports:
                saved = self._action(ev, "report") or {}
                path = saved.get("path") or (f"{proj.reports_dir.strip('/')}/"
                                             f"{time.strftime('%Y-%m-%d', time.localtime(req.get('finished_at') or time.time()))}-{rid}.md")
                if not saved:
                    self._save_action(ev, "report", {"path": path, "done": False})
                if saved.get("done"):
                    report_url = saved.get("url")
                else:
                    res = await gh.put_file(proj.repo, path, self._clean(self._report_md(rid, req, report)),
                                            f"labhq: report for {rid}", proj.branch)
                    report_url = (res.get("content") or {}).get("html_url")
                    self._save_action(ev, "report", {"path": path, "done": True, "url": report_url})
                    await self._posted(rid, "report", report_url, None)
            if num:
                ok = typ == "request.completed" and d.get("ok", True)
                head = "🏁 완료" if ok else "💥 실패"
                link = f"\n\n보고서: {report_url}" if report_url else ""
                # Failure events may carry no accounting; the stored request keeps what was actually spent.
                known = float(d.get("cost_usd", req.get("cost_usd")) or 0)
                cost = (f" · 비용 ${known}" if d.get("cost_known", req.get("cost_known", True)) else
                        f" · 비용 {f'${known} + ' if known else ''}비용 미집계")
                marker = f"<!-- labhq terminal {rid} {ev.get('seq') or typ} -->"
                saved = self._action(ev, "comment") or {}
                if not saved:
                    self._save_action(ev, "comment", {"done": False, "marker": marker})
                if not saved.get("done"):
                    find_comment = getattr(gh, "find_issue_comment", None)
                    c = await find_comment(proj.repo, num, marker) if saved and find_comment else None
                    if c is None:
                        c = await gh.comment(proj.repo, num, self._clean(
                            f"{head}{cost}{link}\n\n{clip(report, 20000)}\n\n{marker}"))
                    self._save_action(ev, "comment", {"done": True, "marker": marker,
                                                      "url": c.get("html_url")})
                    await self._posted(rid, "final", c.get("html_url"), num)
                if ok:
                    saved = self._action(ev, "close") or {}
                    if not saved:
                        self._save_action(ev, "close", {"done": False})
                    if not saved.get("done"):
                        is_closed = getattr(gh, "issue_is_closed", None)
                        if not (saved and is_closed and await is_closed(proj.repo, num)):
                            await gh.close_issue(proj.repo, num)
                        self._save_action(ev, "close", {"done": True})

    # ---------- markdown ----------
    def _issue_body(self, rid: str, req: dict) -> str:
        dash = f"\n\n실시간 사무실: {self.s.github.dashboard_url}" if self.s.github.dashboard_url else ""
        return (f"**요청** ({req.get('mode', 'orchestrate')})\n\n> {req.get('text', '')}\n\n"
                f"request id: `{rid}` · 이 이슈에 계획, 리뷰, 최종 보고가 차례로 올라옵니다.{dash}"
                f"\n\n<!-- labhq request {rid} -->")

    @staticmethod
    def _plan_md(plan: dict) -> str:
        rows = "\n".join(f"| {s['id']} | `{s['agent_id']}` | {', '.join(s.get('depends_on') or []) or '—'} | "
                         f"{short(s.get('instruction'), 160)} |" for s in plan.get("steps", []))
        warn = "".join(f"\n> ⚠ {w}" for w in plan.get("warnings") or [])
        recruit = "".join(f"\n- 채용 제안: {r.get('repo') or r.get('paper')} — {r.get('reason')}"
                          for r in plan.get("recruit") or [])
        return f"📋 **CSO 계획**\n\n| step | 담당 | 선행 | 지시 |\n|---|---|---|---|\n{rows}{warn}{recruit}"

    @staticmethod
    def _review_md(review: dict) -> str:
        if review.get("status") == "review_unparsed":
            return (f"🐢 **과학 리뷰 #{review.get('revision', 0)}: 리뷰 판정 실패** — "
                    f"{short(review.get('reason') or '판정을 읽지 못했습니다', 160)}. PI 확인 필요")
        sc = review.get("scores") or {}
        issues = "".join(f"\n- `{i.get('step_id')}` {i.get('problem')} → {i.get('request')}"
                         for i in review.get("issues") or [])
        return (f"🐢 **과학 리뷰 #{review.get('revision', 0)}: {review.get('verdict')}** — 질문 부합 "
                f"{sc.get('addresses_question')}/5 · 근거 {sc.get('evidence')}/5 · 철저성 {sc.get('thoroughness')}/5{issues}")

    def _report_md(self, rid: str, req: dict, report: str) -> str:
        known = float(req.get("cost_usd") or 0)
        cost = (f"${known}" if req.get("cost_known", True) else
                f"{f'${known} + ' if known else ''}비용 미집계")
        return (f"# {short(self._clean(req.get('text', '')), 120)}\n\n- request: `{rid}`\n- 생성: labhq CSO\n"
                f"- 비용: {cost}\n\n{report}\n")
