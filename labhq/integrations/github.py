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
from typing import TYPE_CHECKING, Any

import httpx

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


def sanitize(text: str, policy: PolicySettings, extra_secrets: list[str] | tuple[str, ...] = ()) -> str:
    out = text or ""
    for z in policy.data_zones:
        if z.level == "restricted":
            out = out.replace(os.path.normpath(os.path.expanduser(z.path)), "<restricted-zone>")
    for pat in SECRET_PATTERNS:
        out = re.sub(pat, "<redacted-secret>", out)
    for secret in extra_secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "<redacted-secret>")
    return clip(out, MAX_BODY)


def codex_comment(body: str, mention: str = "@codex") -> str:
    """A PR comment addressed to Codex always carries the mention — also when replying under its comment."""
    return body if mention in body else f"{mention} {body}"


class GitHubClient:
    def __init__(self, token: str, api_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=api_url.rstrip("/"), transport=transport, timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "labhq",
        })

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = await self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        return await self._req("POST", f"/repos/{repo}/issues", json={"title": title, "body": body, "labels": labels})

    async def comment(self, repo: str, number: int, body: str) -> dict:
        return await self._req("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    async def close_issue(self, repo: str, number: int) -> dict:
        return await self._req("PATCH", f"/repos/{repo}/issues/{number}",
                               json={"state": "closed", "state_reason": "completed"})

    async def put_file(self, repo: str, path: str, content: str, message: str, branch: str) -> dict:
        sha = None
        r = await self.http.get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
        if r.status_code == 200:
            sha = r.json().get("sha")
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
        self.issues: dict[str, int] = {}
        self._client: GitHubClient | None = None
        self._warned: set[str] = set()

    def enabled(self) -> bool:
        return any(p.repo for p in self.s.projects)

    def client(self) -> GitHubClient | None:
        if self._client is None:
            token = os.environ.get(self.s.github.token_env, "")
            if not token and self.transport is None:
                if "token" not in self._warned:
                    self._warned.add("token")
                    log.warning("GitHub updates are off: set %s on the gateway host", self.s.github.token_env)
                return None
            self._client = GitHubClient(token or "test-token", self.s.github.api_url, self.transport)
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
                await self.handle(ev)
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

    async def _posted(self, rid: str, kind: str, url: str | None, number: int | None = None) -> None:
        await self.hub.publish({"type": "github.posted", "ts": time.time(), "request_id": rid,
                                "data": {"kind": kind, "url": url, "number": number}})

    async def handle(self, ev: dict) -> None:
        rid = ev["request_id"]
        proj = self.project_of(rid)
        if not proj or not proj.repo:
            return
        if proj.visibility == "public" and not proj.allow_public_reports:
            if proj.id not in self._warned:
                self._warned.add(proj.id)
                log.warning("project %s is public; set allow_public_reports: true to post updates", proj.id)
            return
        gh = self.client()
        if gh is None:
            return
        typ, d = ev["type"], ev.get("data") or {}
        req = self.hub.requests.get(rid) or {}

        if typ == "request.created":
            if not proj.issues:
                return
            issue = await gh.create_issue(proj.repo, f"[labhq] {short(req.get('text', ''), 70)}",
                                          self._clean(self._issue_body(rid, req)), proj.labels)
            self.issues[rid] = issue["number"]
            await self._posted(rid, "issue", issue.get("html_url"), issue["number"])
            return

        num = self.issues.get(rid)
        if typ == "request.plan" and num:
            c = await gh.comment(proj.repo, num, self._clean(self._plan_md(d)))
            await self._posted(rid, "plan", c.get("html_url"), num)
        elif typ == "request.review" and num:
            c = await gh.comment(proj.repo, num, self._clean(self._review_md(d)))
            await self._posted(rid, "review", c.get("html_url"), num)
        elif typ == "recruit.done" and num:
            a = d.get("agent") or {}
            c = await gh.comment(proj.repo, num, self._clean(
                f"🐥 파견직 합류: **{a.get('name')}** (`{a.get('id')}`) — 수습 통과={d.get('passed_probation')}"))
            await self._posted(rid, "recruit", c.get("html_url"), num)
        elif typ in ("request.completed", "request.failed"):
            report = d.get("report") or req.get("report") or d.get("error") or ""
            report_url = None
            if typ == "request.completed" and proj.commit_reports:
                path = f"{proj.reports_dir.strip('/')}/{time.strftime('%Y-%m-%d')}-{rid}.md"
                res = await gh.put_file(proj.repo, path, self._clean(self._report_md(rid, req, report)),
                                        f"labhq: report for {rid}", proj.branch)
                report_url = (res.get("content") or {}).get("html_url")
                await self._posted(rid, "report", report_url, None)
            if num:
                ok = typ == "request.completed" and d.get("ok", True)
                head = "🏁 완료" if ok else "💥 실패"
                link = f"\n\n보고서: {report_url}" if report_url else ""
                cost = f" · 비용 ${d.get('cost_usd')}" if d.get("cost_usd") is not None else ""
                c = await gh.comment(proj.repo, num, self._clean(f"{head}{cost}{link}\n\n{clip(report, 20000)}"))
                await self._posted(rid, "final", c.get("html_url"), num)
                if ok:
                    await gh.close_issue(proj.repo, num)

    # ---------- markdown ----------
    def _issue_body(self, rid: str, req: dict) -> str:
        dash = f"\n\n실시간 사무실: {self.s.github.dashboard_url}" if self.s.github.dashboard_url else ""
        return (f"**요청** ({req.get('mode', 'orchestrate')})\n\n> {req.get('text', '')}\n\n"
                f"request id: `{rid}` · 이 이슈에 계획, 리뷰, 최종 보고가 차례로 올라옵니다.{dash}")

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
        sc = review.get("scores") or {}
        issues = "".join(f"\n- `{i.get('step_id')}` {i.get('problem')} → {i.get('request')}"
                         for i in review.get("issues") or [])
        return (f"🐢 **과학 리뷰 #{review.get('revision', 0)}: {review.get('verdict')}** — 질문 부합 "
                f"{sc.get('addresses_question')}/5 · 근거 {sc.get('evidence')}/5 · 철저성 {sc.get('thoroughness')}/5{issues}")

    @staticmethod
    def _report_md(rid: str, req: dict, report: str) -> str:
        return (f"# {short(req.get('text', ''), 120)}\n\n- request: `{rid}`\n- 생성: labhq CSO\n"
                f"- 비용: ${req.get('cost_usd', 0)}\n\n{report}\n")
