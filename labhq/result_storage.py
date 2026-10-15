"""Request-local storage choices and a reviewed, text-only repository edition (#558)."""
from __future__ import annotations

import asyncio
import base64
import copy
import csv
import json
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote

from .integrations.github import GitHubClient, GitHubHTTPError, publish_clean, sanitize, root_zone_restricted
from .models import ApprovalRequest, Task
from .policy import touches, restricted_paths
from . import edition_sources
from .intake import reference_dirs
from .recruit.provenance import touches_restricted
from .report_abstract import abstract_first, build_graphical_abstract, with_abstract_link
from .report_figures import (ABSTRACT_FILE, classify, figure_owners, has_abstract_link, image_links, is_abstract_target,
                             link_workdir_figures, output_exists)
from .report_pdf import NO_BROWSER, ReportRefused, build_report_file, pdf_cap_bytes, size_notes
from .request_bundle import _POSIX_ABS, _WINDOWS_ABS

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_PRIVATE_PATH = re.compile(r"(?<![\w:])(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/)[^\s<>\"'`\])]+")
_ASSIGNMENT = re.compile(r"(?im)\b((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+")
_TEXT_OUTPUTS = {".md", ".txt", ".svg", ".csv", ".tsv", ".py", ".r", ".sh", ".json"}
_IMAGE_OUTPUTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_DOCUMENT_LINKS = (("report.md", "전체 결과·그림·표"), ("PROGRESS.md", "진행 기록"), ("REPRODUCE.md", "재현 방법"),
                   ("SOURCES.md", "출처"))
README_EXCERPT_CHARS = 1800  # the deterministic README quotes this much of the report; report.md holds all of it
EDITOR_REPORT_CHARS = 100_000  # what the editor reads of the report whole; a longer one is sent as outline + sections
EDITOR_OTHER_CHARS = 30_000  # the same bound for each of PROGRESS.md, REPRODUCE.md and SOURCES.md
_PDF_NAME = "report.pdf"
_GENERATED = ("README.md", "report.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md")  # edition documents that may link a figure
PDF_UPLOAD_MAX_MB = 75  # base64 of this is 100 MB, the contents API's file limit (#608); settings caps the PDF size there


def plain_path(path):
    from .adapters.owned import is_link
    return not any((p.exists() or p.is_symlink()) and is_link(p) for p in [path, *path.parents])


def controlled_request(req, settings):
    """Whether the request touches controlled data: a restricted zone named by its text, folders, references or
    results, or `/` or a drive root as a restricted zone. The repository edition and the PDF report both stop here."""
    return bool(root_zone_restricted(settings.policy) or touches_restricted(
        [str(req.get("text") or "")],
        [*(req.get("project_dirs") or []), *reference_dirs(req.get("references"))], settings) or touches(
        [req.get("text"), req.get("project_dirs"), req.get("references"), req.get("results")],
        restricted_paths(settings.policy)))


def edition_candidates(req, settings) -> dict[str, Path]:
    """The bundle files the edition may carry, by bundle name: reported outputs of a step (the manifest says ``copied``,
    not an unreported file), of a type the edition keeps, whose name needs no masking. Their size is not looked at here:
    `edition_assets` limits what it copies, the PDF printed from the bundle does not (#608). One rule decides both, so the
    published PDF never shows a figure the reviewed edition leaves out (#611 review)."""
    bundle = settings.path(settings.runner.workspace_root) / "requests" / req["id"]
    manifest = bundle / "MANIFEST.tsv"
    if not plain_path(manifest) or not manifest.is_file():
        return {}
    found: dict[str, Path] = {}
    with manifest.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            name = row.get("relative_path", "")
            rel = PurePosixPath(name)
            if (row.get("status") != "copied" or rel.is_absolute() or ".." in rel.parts or "\\" in name
                    or len(rel.parts) < 4 or rel.parts[0] != "steps" or rel.parts[2] != "outputs"
                    or clean_document(name, settings, req) != name):
                continue
            suffix = rel.suffix.lower()
            if suffix not in _TEXT_OUTPUTS and suffix not in _IMAGE_OUTPUTS:
                continue
            path = bundle / name
            if not plain_path(path) or not path.is_file():
                continue
            found[name] = path
    return found


def edition_assets(req, settings):
    """Copy only reported outputs from the already checked bundle, never raw inputs or unreported files."""
    assets, images = {}, {}
    total = 0
    for name, path in edition_candidates(req, settings).items():
        suffix = PurePosixPath(name).suffix.lower()
        size = path.stat().st_size
        if size > 1_000_000 or total + size > 5_000_000:
            continue
        total += size
        payload = path.read_bytes()
        if suffix in _IMAGE_OUTPUTS:
            images[name] = payload
        else:
            try:
                assets[name] = clean_document(payload.decode("utf-8"), settings, req)
            except UnicodeDecodeError:
                continue
    return assets, images


def edition_figure_links(text: str, req: dict, settings, included) -> str:
    """Make every local figure link of an edition document resolve to a file the edition holds (#584).

    The report links ``<workdir_id>/outputs/<file>``; `edition_assets` copies a reported output to its bundle path
    ``steps/<step_id>/outputs/<file>``, so the link is rewritten to that. A link to a file the edition does not hold
    (unreported, too large, a type left out) or to a path outside it becomes a plain-text note. ``included`` holds the
    edition's file names; http(s) and data: links are not paths into the bundle and stay."""
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    bundle = settings.path(settings.runner.workspace_root) / "requests" / req["id"]
    text = link_workdir_figures(text, figure_owners(results),
                                output_exists(results, bundle if plain_path(bundle) and bundle.is_dir() else None))
    pieces, last = [], 0
    for link in image_links(text):
        kind, value = classify(link.target, lambda name: name in included)
        if kind == "remote" or (kind == "local" and value in included):
            continue
        # a graphical abstract nobody made is not an omitted figure: its link goes without a note (#608)
        note = "" if kind == "local" and is_abstract_target(value) else f"*(그림 생략: {link.alt.strip() or '그림'})*"
        pieces += [text[last:link.start], note]
        last = link.end
    return "".join(pieces) + text[last:] if pieces else text


def edition_abstract(req: dict, settings, included) -> str | None:
    """SVG text of the graphical abstract labhq draws when the edition's report has no abstract figure the edition holds
    (#608); None when it has one or there is no report. The labels are masked like the edition's documents."""
    if not str(req.get("report") or "").strip():
        return None
    report = edition_figure_links(str(req["report"]), req, settings, included)
    if has_abstract_link(report, lambda name: name in included):
        return None
    return build_graphical_abstract(req, report, scrub=lambda text: clean_document(text, settings, req))


def pdf_report_text(req: dict, settings) -> str:
    """The report text the edition's PDF is printed from: cleaned like the edition's report.md, but its figure links point
    at the originals in the request bundle (no per-figure size limit, #608), not at the size-limited edition copies.

    Only the figures the edition may carry (`edition_candidates`) stay linked; an unreported bundle file, a type the
    edition leaves out or a name it would mask becomes a "그림 생략" note, as in the edition (#611 review)."""
    text = edition_figure_links(str(req.get("report") or "결과 보고서가 없습니다."), req, settings,
                                edition_candidates(req, settings).keys())
    return clean_document(text, settings, req)


def pdf_skip_line(reason: str) -> str:
    return f"PDF는 포함하지 않았어요: {reason}"


def clean_document(text: str, settings, req: dict) -> str:
    text = sanitize(text, settings.policy, [os.environ.get(settings.github.token_env, "")], limit=None)
    text = publish_clean(text, settings, [req], limit=None)
    text = _WINDOWS_ABS.sub("<local-path>", text)
    text = _POSIX_ABS.sub("<local-path>", text)
    return _ASSIGNMENT.sub(r"\1<redacted-secret>", _PRIVATE_PATH.sub("<local-path>", text))


def readme_excerpt(report: str, limit: int = README_EXCERPT_CHARS) -> str:
    """The opening of the report for the README, cut at a paragraph, else a line, else a sentence; report.md has the rest."""
    if len(report) <= limit:
        return report
    head = report[:limit]
    sentence = max((m.start() for m in re.finditer(r"(?<=[.!?。])\s", head)), default=-1)
    cut = next((at for at in (head.rfind("\n\n"), head.rfind("\n"), sentence) if at > limit // 3), limit)
    return head[:cut].rstrip() + "\n\n…(이어지는 내용은 report.md에 있습니다.)"


def document_links(readme: str) -> str:
    """A link line for the edition documents the README does not link yet; empty when it already links all of them."""
    return " · ".join(f"[{label}]({name})" for name, label in _DOCUMENT_LINKS
                      if not re.search(rf"\]\((?:\./)?{re.escape(name)}(?:#[^)]*)?\)", readme))


def fit_record(text: str, budget: int) -> tuple[str, list[str]]:
    """``text`` whole when it fits ``budget`` characters. Otherwise an outline of its headings, then its sections in
    order, whole until the budget is used (the section that crosses it keeps its opening) and only a title and first
    line after that. Also the titles of the sections that were not sent whole."""
    if len(text) <= budget:
        return text, []
    pieces = [piece for piece in re.split(r"(?m)^(?=#{1,6}\s)", text) if piece]
    titles = [piece.splitlines()[0].lstrip("#").strip()[:120] if piece.startswith("#") else "(제목 없는 앞부분)"
              for piece in pieces]
    shown, left, omitted = [], budget, []
    for piece, title in zip(pieces, titles):
        if len(piece) <= left:
            shown.append(piece)
            left -= len(piece)
            continue
        omitted.append(title)
        if left > 0:
            part = piece[:left]
            shown.append((part[:part.rfind("\n")] if "\n" in part else part) + "\n…(이 절은 여기까지만 실었습니다)\n")
            left = 0
        else:
            first = next((line.strip() for line in piece.splitlines()[1:] if line.strip()), "")
            shown.append(f"{title}\n{first[:200]}\n…(본문은 싣지 않았습니다)\n")
    return "[개요]\n" + "\n".join(f"- {title}" for title in titles) + "\n\n[본문]\n" + "".join(shown), omitted


def editor_prompt(docs: dict[str, str], request_text: str) -> str:
    """What the editor reads: the request and the whole report, PROGRESS, REPRODUCE and SOURCES (#607).

    A document over its size bound is sent as an outline plus whole sections, and the prompt names exactly which
    sections are not whole, says the published document has them, and tells the editor not to call the record
    incomplete. The README draft is left out: it quotes only the report's opening."""
    parts, omitted = [], []
    for name, label, budget in (("report.md", "보고서 report.md", EDITOR_REPORT_CHARS),
                                ("PROGRESS.md", "진행 기록 PROGRESS.md", EDITOR_OTHER_CHARS),
                                ("REPRODUCE.md", "재현 방법 REPRODUCE.md", EDITOR_OTHER_CHARS),
                                ("SOURCES.md", "출처 SOURCES.md", EDITOR_OTHER_CHARS)):
        body, cut = fit_record(docs.get(name, ""), budget)
        omitted += [f"{name}의 {title}" for title in cut]
        parts.append(f"===== {label}{' (개요와 일부 절)' if cut else ' (전문)'} =====\n{body.strip()}\n")
    if omitted:
        note = ("입력 크기 때문에 다음 절은 본문 대신 제목과 첫 줄만(또는 앞부분만) 담았습니다: " + "; ".join(omitted) +
                ". 이 절들의 전문은 게시되는 문서에 그대로 있습니다. README에 기록이 불완전하다고 쓰지 말고 "
                "자세한 내용은 해당 문서를 보라고 안내하세요.")
    else:
        note = "아래 기록은 전체를 그대로 담았습니다. README에 기록의 길이나 완결성을 문제 삼는 말을 쓰지 마세요."
    return ("다음 기록만 써서 README를 정리하세요.\n" + note + "\n\n===== 요청 =====\n" + request_text.strip()
            + "\n\n" + "\n".join(parts))


def draft_documents(req: dict, settings, included=None, assets=None, *, abstract: bool = False) -> dict[str, str]:
    """Only derived prose enters this edition. Raw bundle data and binary files stay local.

    ``included``: the edition's file names; the report's figure links are made to resolve to them (#584).
    ``assets``: the edition's text outputs by name; its ``resource_versions`` files feed SOURCES.md (#607).
    ``abstract``: the edition holds the drawn graphical abstract (`edition_abstract`); it is the report's first figure (#608)."""
    report =str(req.get("report") or "결과 보고서가 없습니다.")
    if included is not None:
        report = edition_figure_links(report, req, settings, included)  # before cleaning, which would cut a path's ")"
        report = with_abstract_link(report) if abstract else abstract_first(report, lambda name: name in included)
    steps = (req.get("plan") or {}).get("steps") or []
    progress = ["# 진행 기록", "", *[f"- {s.get('id')}: {s.get('instruction', '')}" for s in steps],
                "", "## 결정·카드", json.dumps(req.get("clarifications") or [], ensure_ascii=False, indent=2),
                "CP1: " + str((req.get("research_contract") or {}).get("approval", {}).get("state", "기록 없음")),
                "", "## 수정 회차", json.dumps({"replans": [
                    {k: v for k, v in entry.items() if k != "prior_results"}
                    for entry in req.get("replan_history") or []],
                    "review_progress": req.get("review_progress") or {}}, ensure_ascii=False, indent=2),
                "", "## 검토", json.dumps(req.get("review") or {}, ensure_ascii=False, indent=2)]
    sources = [f"{r.get('kind')}: {r.get('value', '')}" for r in req.get("references") or []
               if r.get("kind") in {"url", "github", "doi", "pmid"}]
    sources += re.findall(r"https?://[^\s<>\"'\])]+", report)
    bibliography = re.search(r"(?im)^#{1,6}\s+(?:references|bibliography|참고\s*문헌|참고\s*자료|출처)[^\n]*\n", report)
    citations = re.split(r"(?m)^#{1,6}\s+", report[bibliography.end():], maxsplit=1)[0] if bibliography else ""
    found = edition_sources.collect(
        [report, str(req.get("report_appendix") or "")], req.get("references") or [],
        [text for name, text in (assets or {}).items() if edition_sources.is_versions_file(name)])
    docs = {
        "README.md": "# 연구 결과\n\n## 무엇을 왜 했나요\n\n" + str(req.get("text") or "") +
            "\n\n## 결과\n\n" + readme_excerpt(report) + "\n\n" + document_links("") + "\n",
        "report.md": report,
        "PROGRESS.md": "\n".join(progress),
        "REPRODUCE.md": "# 재현 방법\n\n원본 입력·실행 파일은 로컬 요청 묶음에 있습니다. "
            "묶음의 README.md와 MANIFEST.tsv에서 환경·실행 명령·누락 파일을 확인하세요.\n\n"
            "이 정리본은 결과 설명용입니다. 원본 데이터나 실행 환경을 포함하지 않아 이것만으로 재실행되지는 않습니다.\n\n"
            + "\n".join(f"- {s.get('id')}: {s.get('instruction', '')}" for s in steps)
            + "\n\n## 입력 출처\n\n" + ("\n".join(sources) or "공개 입력 출처가 기록되지 않았습니다."),
        "SOURCES.md": edition_sources.render(found, citations),
    }
    return {name: clean_document(body, settings, req) for name, body in docs.items()}


class ResultStorage:
    def __init__(self, hub, transport=None):
        self.hub, self.s, self.transport = hub, hub.s, transport
        self.tasks: dict[str, asyncio.Task] = {}
        self._client = None
        self._card_lock = asyncio.Lock()
        self.uploading: set[str] = set()  # requests whose files are going to the repository right now

    @property
    def card_lock(self) -> asyncio.Lock:
        """Held by a redraft and by the answer to a publish card, so the two never interleave (#607)."""
        return self._card_lock

    def client(self):
        token = os.environ.get(self.s.github.token_env, "")
        if not token or root_zone_restricted(self.s.policy):
            return None
        if self._client is None:
            self._client = GitHubClient(token, self.s.github.api_url, self.transport)
        return self._client

    def inherited(self, rid):
        req = self.hub.requests[rid]
        if req.get("result_storage"):
            return True
        parent = (req.get("meta") or {}).get("revised_from")
        previous = (self.hub.requests.get(parent) or {}).get("result_storage") or {}
        if previous:
            req["result_storage"] = {key: value for key, value in previous.items()
                                     if key in {"mode", "repo", "visibility", "allow_public", "branch"}}
            req["result_storage"]["status"] = "selected"
            self.hub.save_request(rid)
            return True
        project = self.s.project(req.get("project_id"))
        if project:
            can_publish = project.repo and (project.visibility != "public" or project.allow_public_reports)
            req["result_storage"] = {"mode": "existing" if can_publish else "local", "repo": project.repo,
                "visibility": project.visibility, "allow_public": project.allow_public_reports,
                "branch": project.branch, "status": "selected"}
            self.hub.save_request(rid)
            return True
        return False

    async def open_card(self, rid):
        """Open one durable late-choice card without parking or restarting the request."""
        async with self._card_lock:
            req = self.hub.requests[rid]
            if req.get("result_storage"):
                raise ValueError("이미 저장 위치를 골랐거나 게시한 요청입니다.")
            if req.get("status") not in {"done", "failed", "cancelled", "review_pending"} or req.get("cancelling"):
                raise ValueError("끝난 요청이나 보고서 검토 대기 요청에서 저장 위치를 정해 주세요.")
            if req.get("status") == "cancelled" and not str(req.get("report") or "").strip():
                raise ValueError("저장할 부분 결과 보고서가 없습니다.")
            for entry in self.hub.approvals.values():
                approval = entry["approval"]
                if approval.get("request_id") == rid and (approval.get("detail") or {}).get("storage"):
                    return approval
            storage = await self.card(rid, late=True)
            approval = ApprovalRequest(kind="result_storage", request_id=rid,
                summary="이 결과를 어디에 남길까요?", detail={"storage": storage, "late_storage": True}
                ).model_dump(mode="json", exclude={"timeout_s"})
            self.hub.approvals[approval["id"]] = {"approval": approval, "origin": None}
            self.hub.save_approval(approval["id"])
            await self.hub.publish({"type": "approval.requested", "request_id": rid, "data": approval})
            return approval

    async def card(self, rid, *, late=False):
        req = self.hub.requests[rid]
        if req.get("result_storage") or (not late and self.inherited(rid)):
            return None
        research = (req.get("intake") or {}).get("work_kind") == "research" or req.get("research_contract")
        if not late and not research and not self.s.github.ask_storage:
            return None
        warning = ""
        available = False
        gh = self.client()
        if gh is None:
            warning = f"GitHub에 연결하지 못했습니다. {self.s.github.token_env} 환경 변수에 토큰을 설정하세요. 지금은 로컬에 남깁니다."
        else:
            try:
                await gh._req("GET", "/user")
                available = True
            except Exception:
                warning = "GitHub 권한을 확인하지 못했습니다. 토큰의 저장소 읽기·쓰기 권한을 확인하세요. 지금은 로컬에 남깁니다."
        return {"default": "local", "suggested_name": "labhq-" + rid.removeprefix("req_"),
                "github_available": available, "warning": warning}

    async def select(self, rid, choice, *, late=False):
        async with self._card_lock:
            return await self._select(rid, choice, late=late)

    async def _select(self, rid, choice, *, late=False):
        req = self.hub.requests[rid]
        if late and req.get("result_storage"):
            raise ValueError("이미 저장 위치를 골랐거나 게시한 요청입니다.")
        mode = choice.get("mode", "local")
        if mode not in {"local", "new", "existing"} or choice.get("visibility", "private") not in {"private", "public"}:
            raise ValueError("저장 위치와 공개 범위를 다시 골라 주세요.")
        saved = {"mode": "local", "status": "selected"}
        if mode in {"new", "existing"}:
            repo = str(choice.get("repo") or "").strip()
            if mode == "new" and not _NAME.fullmatch(repo):
                raise ValueError("새 저장소 이름은 영문·숫자·점·밑줄·하이픈으로 적어 주세요.")
            if mode == "existing" and (not _REPO.fullmatch(repo) or any(p in {".", ".."} for p in repo.split("/"))):
                raise ValueError("기존 저장소는 소유자/저장소 이름으로 적어 주세요.")
            gh = self.client()
            try:
                if gh is None:
                    raise ValueError("no token")
                if mode == "new":
                    user = await gh._req("GET", "/user")
                    repo = f"{user['login']}/{repo}"
                else:
                    info = await gh.repo_info(repo)
                    if not info.get("permissions", {}).get("push"):
                        raise ValueError("no write permission")
                visibility = choice.get("visibility", "private")
                if mode == "existing" and not info.get("private", False) and visibility != "public":
                    raise ValueError("public repository requires public choice")
                saved = {"mode": mode, "repo": repo, "visibility": visibility,
                         "allow_public": visibility == "public", "status": "selected"}
            except Exception:
                saved["warning"] = "GitHub에 쓸 권한이 없어 로컬에 남깁니다. 토큰의 저장소 읽기·쓰기 권한을 확인하세요."
        if late:
            saved["after_review"] = True
        req["result_storage"] = saved
        self.hub.save_request(rid)
        if late:
            self.schedule(rid)
        return saved

    def schedule(self, rid):
        req = self.hub.requests[rid]
        saved = req.get("result_storage") or {}
        if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
            return
        if not saved or saved.get("status") in {"published", "local", "failed", "declined"}:
            return
        if rid not in self.tasks or self.tasks[rid].done():
            self.tasks[rid] = asyncio.create_task(self.finish(rid))

    async def redraft(self, rid) -> dict:
        """Run the editor again for an edition that is drafted but not published, and replace its publish card (#607).

        Allowed while the publish card is pending (or its answer window passed) and after the PI declined it. A published
        edition is refused: its files are already in the repository. The old card is closed and the new draft opens a
        new one; the folder is rebuilt, so nothing of the old draft stays."""
        async with self.card_lock:
            req = self.hub.requests[rid]
            saved = req.get("result_storage") or {}
            status = saved.get("status")
            if status == "published":
                raise ValueError("이미 게시한 정리본은 다시 만들 수 없습니다. 저장소에 올라간 파일을 이 명령이 바꾸지 않습니다.")
            if status not in {"draft", "declined"}:
                raise ValueError("다시 만들 정리본이 없습니다. 정리본을 만든 뒤 게시 승인을 기다리거나 거절한 요청만 다시 만들 수 있습니다"
                                 f"(저장 상태: {status or '선택 없음'}).")
            if req.get("status") not in {"done", "completed", "failed", "cancelled"} or req.get("cancelling"):
                raise ValueError("끝난 요청만 정리본을 다시 만들 수 있습니다.")
            if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
                raise ValueError("보고서 검토 완료와 묶음 갱신 뒤에 다시 만들 수 있습니다.")
            if controlled_request(req, self.s):
                raise ValueError("통제 데이터가 포함된 요청은 편집자에게 보내지 않습니다.")
            cards = [aid for aid, entry in self.hub.approvals.items()
                     if entry["approval"].get("request_id") == rid and entry["approval"].get("kind") == "result_publish"]
            running = self.tasks.get(rid)
            if rid in self.uploading or (running is not None and not running.done() and not cards):
                # approved (its card is gone) or already uploading: cancelling now could leave a half-published edition
                raise ValueError("게시가 시작됐습니다. 올라가는 도중에는 다시 만들 수 없으니 끝난 뒤 결과를 확인해 주세요.")
            if running is not None and not running.done():
                running.cancel()  # it only waits for the old card's answer
                await asyncio.gather(running, return_exceptions=True)
            for aid in cards:
                await self.hub.close_approval(aid, "정리본을 다시 만들어 이 게시 카드를 닫았습니다")
            for key in ("draft_path", "pdf", "warning", "error_type"):
                saved.pop(key, None)
            saved["status"] = "selected"  # a restart before the new draft is saved builds it from scratch
            self.hub.save_request(rid)
            self.tasks[rid] = asyncio.create_task(self.finish(rid))
            return {"request_id": rid, "status": "redrafting", "closed_cards": cards}

    async def finish(self, rid):
        req = self.hub.requests[rid]
        saved = req.get("result_storage") or {}
        if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
            return
        if not saved or req.get("status") not in {"done", "completed", "failed", "cancelled"}:
            return
        try:
            # Controlled inputs never enter the editor prompt or the repository edition.
            if controlled_request(req, self.s):
                saved.update(status="local", warning="통제 데이터가 포함된 요청은 로컬 묶음에만 남깁니다.")
                return
            pdf = None
            if saved.get("status") == "draft":
                # Resume the exact on-disk edition the PI was reviewing, without another paid task.
                target = Path(saved["draft_path"])
                if not plain_path(target) or not target.is_dir():
                    raise ValueError("missing or linked editor directory")
                docs, images = {}, {}
                for path in target.rglob("*"):
                    if not plain_path(path):
                        raise ValueError("linked editor file")
                    if path.is_file():
                        name = path.relative_to(target).as_posix()
                        if name == _PDF_NAME:
                            pdf = path.read_bytes()
                        elif path.suffix.lower() in _IMAGE_OUTPUTS:
                            images[name] = path.read_bytes()
                        else:
                            docs[name] = path.read_bytes().decode("utf-8")
                if "README.md" not in docs:
                    raise ValueError("missing editor README")
                if pdf is not None and (not pdf.startswith(b"%PDF-") or len(pdf) > PDF_UPLOAD_MAX_MB * 1024 * 1024):
                    raise ValueError("invalid editor PDF")
            else:
                assets, images = edition_assets(req, self.s)
                included = {*assets, *images}
                drawn = edition_abstract(req, self.s, included)  # #608: the edition opens with a graphical abstract
                if drawn:
                    assets[ABSTRACT_FILE] = drawn
                    included.add(ABSTRACT_FILE)
                docs = draft_documents(req, self.s, included, assets, abstract=bool(drawn))
                links = "\n".join(f"- [{name}]({quote(name, safe='/')})" for name in [*assets, *images])
                if links:
                    docs["README.md"] += "\n## 그림·표·실행 파일\n\n" + links + "\n"
                    docs["REPRODUCE.md"] += "\n\n## 포함한 파일\n\n" + links + "\n"
                bundle_readme = self.s.path(self.s.runner.workspace_root) / "requests" / rid / "README.md"
                manifest = bundle_readme.with_name("MANIFEST.tsv")
                environments = "환경 기록이 없습니다. 실행 환경은 로컬 묶음에서 확인하세요."
                if plain_path(manifest) and manifest.is_file():
                    with manifest.open(encoding="utf-8", newline="") as handle:
                        for row in csv.DictReader(handle, delimiter="\t"):
                            if row.get("status") == "summary" and row.get("environment_records"):
                                environments = clean_document(row["environment_records"], self.s, req)
                                break
                docs["REPRODUCE.md"] += "\n\n## 환경\n\n" + environments
                if plain_path(bundle_readme) and bundle_readme.is_file():
                    docs["REPRODUCE.md"] += "\n\n## 실행 명령·묶음 안내\n\n" + clean_document(bundle_readme.read_text(encoding="utf-8"), self.s, req)
                else:
                    docs["REPRODUCE.md"] += "\n\n## 실행 명령\n\n요청 묶음에 기록된 실행 명령이 없습니다."
                if "editor" in self.hub.agents:
                    try:
                        result = await self.hub.orchestrator.run_step(Task(
                            agent_id="editor", request_id=rid,
                            prompt=editor_prompt(docs, clean_document(str(req.get("text") or ""), self.s, req)),
                            output_schema={"type": "object", "properties": {"readme": {"type": "string"}},
                                           "required": ["readme"], "additionalProperties": False},
                            meta={"kind": "result_editor", "title": "결과를 저장소용으로 정리",
                                  "agent_overrides": {"sandbox": "read-only"}}))
                    except Exception:
                        result = None  # Budget/dispatch failures still leave the deterministic edition.
                    saved = req["result_storage"]  # PI review may replace the persisted request fields while we wait.
                    readme = (result.structured or {}).get("readme") if result and result.ok else None
                    if isinstance(readme, str) and readme.strip():
                        docs["README.md"] = clean_document(readme, self.s, req)
                        if missing := document_links(docs["README.md"]):  # one link line: the editor's own, or ours
                            docs["README.md"] += "\n\n" + missing + "\n"
                        if links:
                            docs["README.md"] += "\n## 그림·표·실행 파일\n\n" + links + "\n"
                for name in _GENERATED:  # the editor's README and the bundle's own text are linked here as well
                    docs[name] = edition_figure_links(docs[name], req, self.s, included)
                docs.update(assets)
                # The audit bundle's MANIFEST and RO-Crate are already sealed.
                target = self.s.path(self.s.runner.workspace_root) / "requests" / f"{rid}-editor"
                if not plain_path(target):
                    raise ValueError("linked editor directory")
                target.mkdir(parents=True, exist_ok=True)
                for stale in sorted(target.rglob("*"), reverse=True):  # only this edition's files stay (a redraft, a retry)
                    if not plain_path(stale):
                        raise ValueError("linked editor file")
                    if stale.is_file():
                        stale.unlink()
                    else:
                        stale.rmdir()

                def write(name, body):
                    path = target / name
                    if not plain_path(path):
                        raise ValueError("linked editor file")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))

                for name, body in {**docs, **images}.items():
                    write(name, body)
                pdf, reason = await self.edition_pdf(rid, req)  # reason: why there is no PDF, else a size note
                saved = req["result_storage"]  # PI review may replace the persisted request fields while we print.
                if pdf:
                    docs["README.md"] += f"\n[보고서 PDF]({_PDF_NAME})\n" + (f"\n{reason}\n" if reason else "")
                    write(_PDF_NAME, pdf)
                else:
                    docs["README.md"] += f"\n{pdf_skip_line(reason)}\n"
                    if plain_path(target / _PDF_NAME):
                        (target / _PDF_NAME).unlink(missing_ok=True)  # a PDF left by an earlier attempt is not this edition's
                write("README.md", docs["README.md"])
                saved["pdf"] = {"included": bool(pdf), "bytes": len(pdf or b""), "note": "" if pdf else pdf_skip_line(reason),
                                "size_note": reason if pdf else ""}
                saved.update(draft_path=str(target), status="draft")
                self.hub.save_request(rid)
            if saved.get("mode") == "local":
                saved["status"] = "local"
                return
            if self.s.github.auto_publish_results and not req.get("pi_reviewed_at"):
                return  # review_done schedules this saved draft again.
            if not self.s.github.auto_publish_results:
                detail = {"documents": docs, "repo": saved.get("repo"),
                          "images": {name: "data:" + _IMAGE_OUTPUTS[Path(name).suffix.lower()] + ";base64," +
                                     base64.b64encode(body).decode("ascii") for name, body in images.items()},
                          "visibility": saved.get("visibility"), "local_draft": str(target)}
                pdf_line = (f"포함: {_PDF_NAME} ({len(pdf) / 1024 / 1024:.1f} MB)"
                            + (f" · {(saved.get('pdf') or {}).get('size_note')}" if (saved.get("pdf") or {}).get("size_note") else "")
                            if pdf else str((saved.get("pdf") or {}).get("note") or ""))
                if pdf_line:
                    detail["pdf"] = pdf_line
                decision = await self.hub.request_approval(kind="result_publish", request_id=rid,
                    summary=f"편집한 결과를 {saved.get('repo')}에 올릴까요?", detail=detail)
                saved = req["result_storage"]
                if not decision.get("approved"):
                    saved["status"] = "draft" if decision.get("state") == "timed_out" else "declined"
                    return
            self.uploading.add(rid)  # from here a redraft is refused: some files may already be in the repository
            try:
                await self.upload(rid, docs, images, pdf)
            finally:
                self.uploading.discard(rid)
            saved["status"] = "published"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            saved["error_type"] = type(exc).__name__
            saved.update(status="failed", warning="GitHub에 올리지 못했습니다. 로컬 정리본은 남겨 두었습니다. 토큰과 저장소 권한을 확인하세요.")
        finally:
            self.hub.save_request(rid)
            await self.hub.publish({"type": "request.storage", "request_id": rid,
                                    "data": {"result_storage": saved}})

    async def edition_pdf(self, rid, req) -> tuple[bytes | None, str]:
        """``(report.pdf bytes, size note)``, or ``(None, plain Korean reason)``.

        The report is printed from the request bundle's original figures, whatever their size, under the edition's cleaned
        text (#608); the edition's own copies keep their 1 MB and 5 MB limits. The PDF is at most ``github.edition_pdf_max_mb``
        (50 MB by default): figures are shrunk to fit, never left out, and a PDF still over the cap is kept with a note. Only a
        PDF past what the GitHub contents API takes is left out, with its size as the reason."""
        bundle = self.s.path(self.s.runner.workspace_root) / "requests" / rid
        text = pdf_report_text(req, self.s)
        shown = copy.deepcopy(req)
        shown.update(text=clean_document(str(req.get("text") or ""), self.s, req), report=text, report_appendix="")
        store = getattr(self.hub, "store", None)
        tasks = {key: copy.deepcopy(task) for key, task in store.all("task").items()
                 if isinstance(task, dict) and task.get("request_id") == rid} if store else {}
        try:
            built = await asyncio.to_thread(
                build_report_file, shown, self.s, tasks, source=bundle, report_text=text,
                scrub=lambda label: clean_document(label, self.s, req))
        except ReportRefused:
            return None, "이 요청은 PDF 보고서를 만들 수 없는 상태예요."
        except Exception:
            return None, "PDF를 만드는 중 오류가 나서 넣지 못했어요."
        if built.kind != "pdf" or not built.data.startswith(b"%PDF-"):
            return None, ("이 PC에서 Edge나 Chrome을 찾지 못했어요." if built.reason == NO_BROWSER
                          else "브라우저가 PDF를 만들지 못했어요.")
        if len(built.data) > PDF_UPLOAD_MAX_MB * 1024 * 1024:
            return None, (f"PDF가 {len(built.data) / 1024 / 1024:.1f} MB라 GitHub에 올릴 수 있는 한도 "
                          f"{PDF_UPLOAD_MAX_MB} MB를 넘어요.")
        return built.data, " ".join(size_notes(built.data, built.shrunk, built.over_cap, pdf_cap_bytes(self.s)))

    async def upload(self, rid, docs, images=None, pdf=None):
        if not self.hub.requests[rid].get("pi_reviewed_at"):
            raise ValueError("보고서 검토 완료 뒤 게시해 주세요.")
        saved = self.hub.requests[rid]["result_storage"]
        gh = self.client()
        if gh is None:
            raise ValueError("GitHub unavailable")
        repo = saved["repo"]
        try:
            info = await gh.repo_info(repo)
        except GitHubHTTPError as exc:
            if exc.status_code != 404 or saved["mode"] != "new":
                raise
            try:
                info = await gh._req("POST", "/user/repos", json={"name": repo.split("/")[1],
                           "private": saved.get("visibility") != "public", "auto_init": True})
            except GitHubHTTPError as collision:
                if collision.status_code != 422:
                    raise
                info = await gh.repo_info(repo)
        if not info.get("private", False) and not saved.get("allow_public"):
            raise ValueError("public publishing not approved")
        branch = saved.get("branch") or info.get("default_branch") or "main"
        for name, body in docs.items():
            await gh.put_file(repo, f"labhq/results/{rid}/{name}", body, "연구 결과 정리", branch)
        for name, body in (images or {}).items():
            await gh.put_image(repo, f"labhq/results/{rid}/{name}", body, branch)
        if pdf:
            await gh.put_binary(repo, f"labhq/results/{rid}/{_PDF_NAME}", pdf, branch, "연구 결과 PDF")

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self._client:
            await self._client.http.aclose()
