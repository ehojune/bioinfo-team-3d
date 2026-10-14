"""Request-local storage choices and a reviewed, text-only repository edition (#558)."""
from __future__ import annotations

import asyncio
import base64
import csv
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote

from .integrations.github import GitHubClient, GitHubHTTPError, publish_clean, sanitize, root_zone_restricted
from .models import ApprovalRequest, Task
from .policy import touches, restricted_paths
from .intake import reference_dirs
from .recruit.provenance import touches_restricted
from .request_bundle import _POSIX_ABS, _WINDOWS_ABS

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_PRIVATE_PATH = re.compile(r"(?<![\w:])(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/)[^\s<>\"'`\])]+")
_ASSIGNMENT = re.compile(r"(?im)\b((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+")
_TEXT_OUTPUTS = {".md", ".txt", ".svg", ".csv", ".tsv", ".py", ".r", ".sh", ".json"}
_IMAGE_OUTPUTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_DOCUMENT_LINKS = "[전체 결과·그림·표](report.md) · [진행 기록](PROGRESS.md) · [재현 방법](REPRODUCE.md) · [참고 자료](SOURCES.md)"


def plain_path(path):
    from .adapters.owned import is_link
    return not any((p.exists() or p.is_symlink()) and is_link(p) for p in [path, *path.parents])


def edition_assets(req, settings):
    """Copy only reported outputs from the already checked bundle, never raw inputs or unreported files."""
    bundle = settings.path(settings.runner.workspace_root) / "requests" / req["id"]
    manifest = bundle / "MANIFEST.tsv"
    if not plain_path(manifest) or not manifest.is_file():
        return {}, {}
    assets, images = {}, {}
    total = 0
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


def clean_document(text: str, settings, req: dict) -> str:
    text = sanitize(text, settings.policy, [os.environ.get(settings.github.token_env, "")], limit=None)
    text = publish_clean(text, settings, [req], limit=None)
    text = _WINDOWS_ABS.sub("<local-path>", text)
    text = _POSIX_ABS.sub("<local-path>", text)
    return _ASSIGNMENT.sub(r"\1<redacted-secret>", _PRIVATE_PATH.sub("<local-path>", text))


def draft_documents(req: dict, settings) -> dict[str, str]:
    """Only derived prose enters this edition. Raw bundle data and binary files stay local."""
    report = str(req.get("report") or "결과 보고서가 없습니다.")
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
    docs = {
        "README.md": "# 연구 결과\n\n## 무엇을 왜 했나요\n\n" + str(req.get("text") or "") +
            "\n\n## 결과\n\n" + report[:1800] + "\n\n[전체 결과·그림·표](report.md) · "
            "[진행 기록](PROGRESS.md) · [재현 방법](REPRODUCE.md) · [출처](SOURCES.md)\n",
        "report.md": report,
        "PROGRESS.md": "\n".join(progress),
        "REPRODUCE.md": "# 재현 방법\n\n원본 입력·실행 파일은 로컬 요청 묶음에 있습니다. "
            "묶음의 README.md와 MANIFEST.tsv에서 환경·실행 명령·누락 파일을 확인하세요.\n\n"
            "이 정리본은 결과 설명용입니다. 원본 데이터나 실행 환경을 포함하지 않아 이것만으로 재실행되지는 않습니다.\n\n"
            + "\n".join(f"- {s.get('id')}: {s.get('instruction', '')}" for s in steps)
            + "\n\n## 입력 출처\n\n" + ("\n".join(sources) or "공개 입력 출처가 기록되지 않았습니다."),
        "SOURCES.md": "# 출처·인용\n\n" + ("\n".join(f"- {url}" for url in dict.fromkeys(sources)) or
                                                  "기록된 URL이 없습니다. 보고서의 인용을 확인하세요.")
            + ("\n\n## 보고서 문헌\n\n" + citations if citations else ""),
    }
    return {name: clean_document(body, settings, req) for name, body in docs.items()}


class ResultStorage:
    def __init__(self, hub, transport=None):
        self.hub, self.s, self.transport = hub, hub.s, transport
        self.tasks: dict[str, asyncio.Task] = {}
        self._client = None
        self._card_lock = asyncio.Lock()

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
            if req.get("status") not in {"done", "completed", "failed", "cancelled", "review_pending"} or req.get("cancelling"):
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

    async def finish(self, rid):
        req = self.hub.requests[rid]
        saved = req.get("result_storage") or {}
        if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
            return
        if not saved or req.get("status") not in {"done", "completed", "failed", "cancelled"}:
            return
        try:
            # Controlled inputs never enter the editor prompt or the repository edition.
            if root_zone_restricted(self.s.policy) or touches_restricted(
                    [str(req.get("text") or "")],
                    [*(req.get("project_dirs") or []), *reference_dirs(req.get("references"))], self.s) or touches(
                    [req.get("text"), req.get("project_dirs"), req.get("references"), req.get("results")],
                    restricted_paths(self.s.policy)):
                saved.update(status="local", warning="통제 데이터가 포함된 요청은 로컬 묶음에만 남깁니다.")
                return
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
                        if path.suffix.lower() in _IMAGE_OUTPUTS:
                            images[name] = path.read_bytes()
                        else:
                            docs[name] = path.read_bytes().decode("utf-8")
                if "README.md" not in docs:
                    raise ValueError("missing editor README")
            else:
                docs = draft_documents(req, self.s)
                assets, images = edition_assets(req, self.s)
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
                    result = await self.hub.orchestrator.run_step(Task(
                        agent_id="editor", request_id=rid,
                        prompt="다음 기록만 써서 README를 정리하세요.\n" + json.dumps(docs, ensure_ascii=False),
                        output_schema={"type": "object", "properties": {"readme": {"type": "string"}},
                                       "required": ["readme"], "additionalProperties": False},
                        meta={"kind": "result_editor", "title": "결과를 저장소용으로 정리",
                              "agent_overrides": {"sandbox": "read-only"}}))
                    saved = req["result_storage"]  # PI review may replace the persisted request fields while we wait.
                    readme = (result.structured or {}).get("readme") if result.ok else None
                    if isinstance(readme, str) and readme.strip():
                        docs["README.md"] = clean_document(readme, self.s, req)
                        if links:
                            docs["README.md"] += "\n## 그림·표·실행 파일\n\n" + links + "\n"
                if any(f"]({name})" not in docs["README.md"] for name in
                       ("report.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md")):
                    docs["README.md"] += "\n\n" + _DOCUMENT_LINKS + "\n"
                docs.update(assets)
                # The audit bundle's MANIFEST and RO-Crate are already sealed.
                target = self.s.path(self.s.runner.workspace_root) / "requests" / f"{rid}-editor"
                if not plain_path(target):
                    raise ValueError("linked editor directory")
                target.mkdir(parents=True, exist_ok=True)
                for name, body in {**docs, **images}.items():
                    path = target / name
                    if not plain_path(path):
                        raise ValueError("linked editor file")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
                saved.update(draft_path=str(target), status="draft")
                self.hub.save_request(rid)
            if saved.get("mode") == "local":
                saved["status"] = "local"
                return
            if self.s.github.auto_publish_results and not req.get("pi_reviewed_at"):
                return  # review_done schedules this saved draft again.
            if not self.s.github.auto_publish_results:
                decision = await self.hub.request_approval(kind="result_publish", request_id=rid,
                    summary=f"편집한 결과를 {saved.get('repo')}에 올릴까요?",
                    detail={"documents": docs, "repo": saved.get("repo"),
                            "images": {name: "data:" + _IMAGE_OUTPUTS[Path(name).suffix.lower()] + ";base64," +
                                       base64.b64encode(body).decode("ascii") for name, body in images.items()},
                            "visibility": saved.get("visibility"), "local_draft": str(target)})
                saved = req["result_storage"]
                if not decision.get("approved"):
                    saved["status"] = "declined"
                    return
            await self.upload(rid, docs, images)
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

    async def upload(self, rid, docs, images=None):
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

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self._client:
            await self._client.http.aclose()
