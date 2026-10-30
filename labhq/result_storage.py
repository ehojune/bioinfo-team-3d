"""Request-local storage choices and a reviewed, text-only repository edition (#558)."""
from __future__ import annotations
from .request_persistence import persist_request, update_request

import asyncio
import base64
import copy
import csv
import datetime
import json
import os
import posixpath
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import re
from typing import Any
from urllib.parse import quote

import httpx

from .integrations.github import (GitHubClient, GitHubHTTPError, SECRET_PATTERNS, publish_clean,
                                  redact_url_credentials, sanitize, root_zone_restricted)
from .interruption import ShutdownInterrupted
from .models import ApprovalRequest, Task
from .policy import touches, restricted_paths
from . import edition_sources, github_token
from .portfolio import (PRIVATE_PATH_PATTERN, EditionCheckError, broken_markdown_links, build_portfolio_files,
                        check_failure, clean_publication_text, declared_aggregate_tables, legacy_aggregate_tables,
                        portfolio_root_file,
                        internal_data_steps, portfolio_report, publish_file_allowed, request_uses_internal_data,
                        scan_publish_files, source_uses_internal_data, table_allowed)
from .intake import reference_dirs
from .recruit.provenance import touches_restricted
from .report_abstract import abstract_first, build_graphical_abstract, with_abstract_link
from .run_report import nextflow_kind
from .report_figures import (ABSTRACT_FILE, classify, figure_owners, has_abstract_link, image_links, is_abstract_target,
                             link_workdir_figures, output_exists)
from .report_pdf import NO_BROWSER, ReportRefused, build_report_file, pdf_cap_bytes, size_notes
from .report_contract import reader_markdown
from .request_bundle import _POSIX_ABS, _WINDOWS_ABS
from .reproduction_receipt import (LANGUAGES, RECEIPT_PATH, build_receipt, expected_outputs, is_lock_file, receipt_json,
                                   receipt_note, receipt_summary)

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
STORAGE_QUESTION = "결과를 GitHub 저장소로 정리할까요?"  # PI wording 2026-10-16 (#688): the card offers local, new, existing


def check_choice(choice: dict, *, end: bool = False) -> None:
    """Raise ValueError for a storage answer that no request state could accept. The review-done API checks it
    before it saves the review, so a mistyped ``--storage`` leaves nothing half done (PR #711 review)."""
    mode = choice.get("mode", "local")
    if (mode not in ({"keep", "local", "new", "existing"} if end else {"local", "new", "existing"})
            or choice.get("visibility", "private") not in {"private", "public"}):
        raise ValueError("저장 위치와 공개 범위를 다시 골라 주세요.")
    repo = str(choice.get("repo") or "").strip()
    if end and mode == "new" and "/" in repo:
        repo = repo.rsplit("/", 1)[-1]
    if mode == "new" and not _NAME.fullmatch(repo):
        raise ValueError("새 저장소 이름은 영문·숫자·점·밑줄·하이픈으로 적어 주세요.")
    if mode == "existing" and (not _REPO.fullmatch(repo) or any(p in {".", ".."} for p in repo.split("/"))):
        raise ValueError("기존 저장소는 소유자/저장소 이름으로 적어 주세요.")
_PRIVATE_PATH = PRIVATE_PATH_PATTERN  # one rule with the portfolio scan (PR #717 review)
_ASSIGNMENT = re.compile(
    r"(?im)\b((?:api[_-]?key|token|password|secret)\s*[:=]\s*)"
    r"(?!<redacted-secret>|&lt;redacted-secret&gt;|\[REDACTED\])[^\s,;<]+")
_ASSIGNMENT_VALUE = re.compile(
    r"(?im)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*(?P<value>[^\s,;<]+)")
_TEXT_OUTPUTS = {".md", ".txt", ".svg", ".csv", ".tsv", ".py", ".r", ".sh", ".json", ".html"}
_IMAGE_OUTPUTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                  ".svg": "image/svg+xml"}
_DOCUMENT_LINKS = (("report.md", "전체 결과·그림·표"), ("PROGRESS.md", "진행 기록"), ("REPRODUCE.md", "재현 방법"),
                   ("SOURCES.md", "출처"))
README_EXCERPT_CHARS = 1800  # the deterministic README quotes this much of the report; report.md holds all of it
EDITOR_REPORT_CHARS = 100_000  # what the editor reads of the report whole; a longer one is sent as outline + sections
EDITOR_OTHER_CHARS = 30_000  # the same bound for each of PROGRESS.md, REPRODUCE.md and SOURCES.md
_PDF_NAME = "report.pdf"
_PORTFOLIO_PDF_NAME = "report/report.pdf"
_GENERATED = ("README.md", "report.md", "report/report.md", "PROGRESS.md", "REPRODUCE.md", "TASK_RECORD.md",
              "SOURCES.md")  # edition documents that may link a figure
PUBLISH_PREVIEW_FILE_MAX_BYTES = 1_000_000
PUBLISH_PREVIEW_TOTAL_MAX_BYTES = 5_000_000
_PROGRESS_OUTPUTS = {".md", ".svg", ".py", ".r", ".sh"}


class PublishAccessError(RuntimeError):
    """GitHub refused the token something the upload needs. The text is plain Korean, says what to change, and holds no token (#607)."""


def _denial(exc: Exception) -> GitHubHTTPError | None:
    """The 403 or 404 of a GitHub call that is not rate limiting: the token cannot reach the repository or create one.
    ``put_file`` checks an existing file with httpx's own error, so that one is read as a GitHub error here."""
    if isinstance(exc, httpx.HTTPStatusError):
        exc = GitHubHTTPError(exc.request.method, exc.request.url.path, exc.response)
    if isinstance(exc, GitHubHTTPError) and exc.status_code in {403, 404} and not exc.rate_limited:
        return exc
    return None


def _retry_command(rid: str) -> str:
    return f"`labhq storage {rid} --republish`"


def create_denied_text(repo: str, rid: str, visibility: str | None) -> str:
    flag = "--public" if visibility == "public" else "--private"
    return (f"토큰으로 새 저장소를 만들 수 없습니다. 선택한 저장소만 허용하는 fine-grained 토큰일 가능성이 큽니다. "
            f"저장된 다른 gh 자격 증명으로 저장소를 만드세요. PowerShell: `$env:GITHUB_TOKEN=$null; gh repo create {repo} {flag} --add-readme`. "
            f"bash: `env -u GITHUB_TOKEN -u GH_TOKEN gh repo create {repo} {flag} --add-readme`. "
            f"또는 GitHub 웹에서 {repo}를 만들며 README 추가를 체크하세요. 그다음 GitHub 토큰 설정의 저장소 접근에 그 저장소를 추가하고 "
            f"Contents: Read and write 권한을 주세요. 끝나면 {_retry_command(rid)}를 실행하세요.")


def write_denied_text(repo: str, rid: str) -> str:
    return (f"토큰으로 {repo}에 파일을 쓸 수 없습니다. 저장소 이름이 맞는지 보고, 선택한 저장소만 허용하는 fine-grained 토큰이면 "
            f"GitHub 토큰 설정의 저장소 접근에 {repo}를 추가하고 Contents: Read and write 권한을 주세요. "
            f"끝나면 {_retry_command(rid)}를 실행하세요.")


def upload_failed_text(rid: str) -> str:
    return ("GitHub에 올리지 못했습니다. 로컬 정리본은 남겨 두었습니다. 토큰과 저장소 권한을 확인하세요. "
            f"고친 뒤 {_retry_command(rid)}로 다시 올릴 수 있습니다.")


def plain_path(path):
    from .adapters.owned import is_link
    return not any((p.exists() or p.is_symlink()) and is_link(p) for p in [path, *path.parents])


def edition_input_readmes(req: Mapping[str, Any]) -> dict[str, str]:
    """Read only small, top-level README files of legacy input roots when structured references are absent."""
    if req.get("references"):
        return {}
    found: dict[str, str] = {}
    for raw in req.get("project_dirs") or []:
        if len(found) >= 8:
            break
        if not isinstance(raw, str) or not raw.strip():
            continue
        root = Path(raw)
        if not plain_path(root):
            continue
        if root.is_file():
            root = root.parent
        for filename in ("README.md", "README", "README.txt"):
            candidate = root / filename
            try:
                if (not plain_path(candidate) or not candidate.is_file()
                        or candidate.stat().st_size > 256_000):
                    continue
                found[str(candidate)] = candidate.read_text(encoding="utf-8", errors="replace")
                break
            except OSError:
                continue
    return found


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
                    or (len(rel.parts) > 3 and rel.parts[3].casefold() == "literature")
                    or clean_document(name, settings, req) != name):
                continue
            suffix = rel.suffix.lower()
            if suffix not in _TEXT_OUTPUTS and suffix not in _IMAGE_OUTPUTS and not is_lock_file(name):
                continue
            path = bundle / name
            if not plain_path(path) or not path.is_file():
                continue
            found[name] = path
    return found


NEXTFLOW_RECORD_MAX_BYTES = 20_000_000


def edition_assets(req, settings):
    """Copy only reported outputs from the already checked bundle, never raw inputs or unreported files."""
    assets, images = {}, {}
    total = 0
    for name, path in edition_candidates(req, settings).items():
        suffix = PurePosixPath(name).suffix.lower()
        size = path.stat().st_size
        unlimited = suffix in _IMAGE_OUTPUTS
        if nextflow_kind(name):  # an nf-core execution report is often 2-4 MB; it has its own cap, not the text total
            if size > NEXTFLOW_RECORD_MAX_BYTES:
                continue
        elif not unlimited:
            if size > 1_000_000 or total + size > 5_000_000:
                continue
            total += size
        payload = path.read_bytes()
        if suffix == ".svg":
            images[name] = clean_svg_for_publish(payload, settings, req)
        elif suffix in _IMAGE_OUTPUTS:
            images[name] = payload
        else:
            try:
                assets[name] = clean_publication_text(name, payload.decode("utf-8"), settings, req)
            except UnicodeDecodeError:
                continue
    return assets, images


def collect_reproduction_receipt(req, settings) -> dict:
    """Inspect only reported source/lock text in the checked bundle; never open data files."""
    sources = {}
    omitted = []
    total = 0
    for name, path in edition_candidates(req, settings).items():
        if PurePosixPath(name).suffix.casefold() not in LANGUAGES and not is_lock_file(name):
            continue
        size = path.stat().st_size
        if size > 1_000_000 or total + size > 5_000_000:
            omitted.append({"path": name, "reason": "size_limit"})
            continue
        total += size
        try:
            sources[name] = clean_publication_text(name, path.read_text(encoding="utf-8"), settings, req)
        except UnicodeDecodeError:
            omitted.append({"path": name, "reason": "not_utf8"})
            continue
    receipt = build_receipt(sources, outputs=expected_outputs(req))
    receipt["omitted_files"].extend(omitted)
    return receipt


def publish_image_previews(images: Mapping[str, bytes], target: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Bound approval-card payloads without changing the full-size files kept in the edition."""
    previews: dict[str, str] = {}
    omitted: dict[str, dict[str, Any]] = {}
    total = 0
    for name, body in images.items():
        size = len(body)
        if size > PUBLISH_PREVIEW_FILE_MAX_BYTES or total + size > PUBLISH_PREVIEW_TOTAL_MAX_BYTES:
            omitted[name] = {"size_bytes": size, "path": str(target / name)}
            continue
        previews[name] = ("data:" + _IMAGE_OUTPUTS[Path(name).suffix.lower()] + ";base64," +
                          base64.b64encode(body).decode("ascii"))
        total += size
    return previews, omitted


def edition_figure_links(text: str, req: dict, settings, included, document: str = "README.md") -> str:
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
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(document), value)) if kind == "local" else value
        if kind == "remote" or (kind == "local" and resolved in included):
            continue
        if kind == "local" and value in included:
            relative = quote(posixpath.relpath(value, posixpath.dirname(document) or "."), safe="/")
            original = text[link.start:link.end]
            pieces += [text[last:link.start], original.replace(link.target, relative, 1)]
            last = link.end
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


def pdf_report_text(req: dict, settings, report_text: str | None = None) -> str:
    """The report text the edition's PDF is printed from: cleaned like the edition's report.md, but its figure links point
    at the originals in the request bundle (no per-figure size limit, #608), not at the size-limited edition copies.

    Only the figures the edition may carry (`edition_candidates`) stay linked; an unreported bundle file, a type the
    edition leaves out or a name it would mask becomes a "그림 생략" note, as in the edition (#611 review)."""
    source = report_text if report_text is not None else str(req.get("report") or "결과 보고서가 없습니다.")
    text = edition_figure_links(source, req, settings,
                                edition_candidates(req, settings).keys())
    return clean_document(text, settings, req)


def nested_report_links(text: str) -> str:
    """Point legacy README links at the migrated canonical report path."""
    return re.sub(r"(\]\(\s*)(?:\./)?report\.md(?=(?:#[^)]*)?\))", r"\1report/report.md", text)


def pdf_skip_line(reason: str) -> str:
    return f"PDF는 포함하지 않았습니다: {reason}"


def place_pdf_in_readme(readme: str, *, link: str | None = None, reason: str = "") -> str:
    """Keep the PDF status in the README's fixed report/results/reproduction block."""
    pattern = re.compile(r"(?m)^- 보고서: \[Markdown\]\(report/report\.md\).*$")
    if link:
        replacement = f"- 보고서: [Markdown](report/report.md) · [PDF]({link})"
    else:
        replacement = f"- 보고서: [Markdown](report/report.md) · {pdf_skip_line(reason)}"
    if pattern.search(readme):
        return pattern.sub(replacement, readme, count=1)
    fallback = f"[과학 보고서 PDF]({link})" if link else pdf_skip_line(reason)
    return readme.rstrip() + "\n\n" + fallback + "\n"


def editor_edition() -> dict:
    """The editor version a rebuilt edition names (#754): this labhq version and the commit the gateway loaded."""
    from . import __version__
    from .integrations.rounds import _git_commit
    commit = _git_commit()
    return {"version": __version__, "commit": commit[:7] if commit else None}


def rebuild_note(rebuild: Mapping) -> str:
    """The line a rebuilt edition's README opens with: the editor that made it and the edition it replaces (#754)."""
    editor = rebuild.get("editor") or {}
    made = f"labhq {editor.get('version') or '버전 기록 없음'}"
    if editor.get("commit"):
        made += f"(commit {editor['commit']})"
    previous = str(rebuild.get("from_repo") or "")
    if previous and previous != rebuild.get("repo"):
        source = f"원본 저장소: [{previous}](https://github.com/{previous})"
    else:
        source = "이전 판은 이 저장소의 커밋 기록에 있습니다"
    when = f"{rebuild['date']}에 " if rebuild.get("date") else ""
    return f"> 이 판은 {made} 편집기로 {when}다시 만든 판입니다. {source}."


def place_rebuild_note(readme: str, note: str) -> str:
    """Put the rebuild line under the README's title, once."""
    if note in readme:
        return readme
    title, _, rest = readme.partition("\n")
    if not title.startswith("# "):
        return f"{note}\n\n{readme}"
    rest = rest.lstrip("\n")
    return f"{title}\n\n{note}\n\n{rest}" if rest else f"{title}\n\n{note}\n"


def clean_document(text: str, settings, req: dict) -> str:
    text = sanitize(text, settings.policy, [os.environ.get(settings.github.token_env, "")], limit=None)
    text = publish_clean(text, settings, [req], limit=None)
    text = _WINDOWS_ABS.sub("<local-path>", text)
    text = _POSIX_ABS.sub("<local-path>", text)
    return _ASSIGNMENT.sub(r"\1<redacted-secret>", _PRIVATE_PATH.sub("<local-path>", text))


def _unsafe_publish_text(text: str, settings, req: dict) -> bool:
    """Whether a cleaned text still matches a publish-time secret or local-path rule."""
    if any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
        return True
    for match in _ASSIGNMENT_VALUE.finditer(text):
        if not match.group("value").startswith(("<redacted-secret>", "&lt;redacted-secret&gt;", "[REDACTED]")):
            return True
    secrets = (os.environ.get(settings.github.token_env, ""), settings.gateway.client_token,
               settings.gateway.runner_token)
    if any(secret and len(secret) >= 8 and secret in text for secret in secrets):
        return True
    if (_PRIVATE_PATH.search(text) or _WINDOWS_ABS.search(text) or _POSIX_ABS.search(text)
            or redact_url_credentials(text) != text):
        return True
    return clean_document(text, settings, req) != text


def clean_svg_for_publish(payload: bytes, settings, req: dict) -> bytes:
    """Clean the repository/card SVG copy while leaving the request-bundle original untouched."""
    try:
        cleaned = clean_document(payload.decode("utf-8"), settings, req)
    except UnicodeDecodeError as exc:
        raise ValueError("SVG 게시 사본을 UTF-8 텍스트로 읽을 수 없습니다.") from exc
    if _unsafe_publish_text(cleaned, settings, req):
        raise ValueError("SVG 게시 사본에 가리지 못한 비밀값 또는 로컬 경로가 있습니다.")
    for marker in ("redacted-secret", "restricted-zone", "reference-path", "private-reference", "local-path"):
        cleaned = cleaned.replace(f"<{marker}>", f"&lt;{marker}&gt;")
    return cleaned.encode("utf-8")


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


def _bounded_titles(titles: list[str], budget: int) -> list[str]:
    """Keep a title list inside ``budget``, retaining both ends and counting the omitted middle."""
    if not titles or budget <= 0:
        return []
    if len("; ".join(titles)) <= budget:
        return titles
    for side in range(min(8, len(titles) // 2), -1, -1):
        middle = len(titles) - side * 2
        summary = titles[:side] + [f"… 중간 {middle}개 절"] + (titles[-side:] if side else [])
        if len("; ".join(summary)) <= budget:
            return summary
    return ["…"[:budget]]


def fit_record(text: str, budget: int) -> tuple[str, list[str]]:
    """``text`` whole when it fits ``budget`` characters. Otherwise an outline of its headings, then its sections in
    order, whole until the budget is used. The outline and a bounded summary of omitted section titles share that same
    budget; the section that crosses it keeps as much of its opening as fits."""
    if len(text) <= budget:
        return text, []
    pieces = [piece for piece in re.split(r"(?m)^(?=#{1,6}\s)", text) if piece]
    titles = [piece.splitlines()[0].lstrip("#").strip()[:120] if piece.startswith("#") else "(제목 없는 앞부분)"
              for piece in pieces]
    title_budget = min(2048, max(1, budget // 5)) if budget > 0 else 0
    body_budget = max(0, budget - title_budget)
    outline_budget = min(8000, max(0, body_budget // 5))
    outline = _bounded_titles(titles, outline_budget)
    header = "[개요]\n" + "\n".join(f"- {title}" for title in outline) + "\n\n[본문]\n"
    if len(header) >= body_budget:
        return header[:body_budget], _bounded_titles(titles, title_budget)

    shown, left, omitted_at = [], body_budget - len(header), len(pieces)
    for at, piece in enumerate(pieces):
        if len(piece) <= left:
            shown.append(piece)
            left -= len(piece)
            continue
        omitted_at = at
        if left > 0:
            rest = len(pieces) - at - 1
            marker = f"\n…(이 절과 나머지 {rest}개 절의 본문은 싣지 않았습니다)\n"
            opening = piece[:max(0, left - len(marker))].rstrip()
            shown.append((opening + marker)[:left])
        break
    body = header + "".join(shown)
    omitted = _bounded_titles(titles[omitted_at:], title_budget)
    return body, omitted


def editor_prompt(docs: dict[str, str], request_text: str) -> str:
    """What the editor reads: the request, deterministic README and the complete source records (#607, #686).

    A document over its size bound is sent as an outline plus whole sections, and the prompt names exactly which
    sections are not whole, says the published document has them, and tells the editor not to call the record
    incomplete. The README is a rewrite target whose numbers, links, citations, headings and table shape stay fixed."""
    parts, omitted = [], []
    for name, label, budget in (("README.md", "README 초안", EDITOR_OTHER_CHARS),
                                ("report/report.md", "논문 형식 보고서 report/report.md", EDITOR_REPORT_CHARS),
                                ("report.md", "호환용 보고서 report.md", EDITOR_REPORT_CHARS),
                                ("PROGRESS.md", "진행 기록 PROGRESS.md", EDITOR_OTHER_CHARS),
                                ("REPRODUCE.md", "재현 방법 REPRODUCE.md", EDITOR_OTHER_CHARS),
                                ("SOURCES.md", "출처 SOURCES.md", EDITOR_OTHER_CHARS)):
        if name not in docs:
            continue
        body, cut = fit_record(docs.get(name, ""), budget)
        omitted += [f"{name}의 {title}" for title in cut]
        parts.append(f"===== {label}{' (개요와 일부 절)' if cut else ' (전문)'} =====\n{body.strip()}\n")
    if omitted:
        note = ("입력 크기 때문에 다음 절은 본문 대신 앞부분만 담았습니다(목록이 길면 가운데 절은 개수로 요약): " + "; ".join(omitted) +
                ". 이 절들의 전문은 게시되는 문서에 그대로 있습니다. README에 기록이 불완전하다고 쓰지 말고 "
                "자세한 내용은 해당 문서를 보라고 안내하세요.")
    else:
        note = "아래 기록은 전체를 그대로 담았습니다. README에 기록의 길이나 완결성을 문제 삼는 말을 쓰지 마세요."
    return ("다음 기록만 써서 README 초안을 윤문하세요. 단계마다 실제 분석과 결과를 표에 남기고, "
            "통계적으로 유의하지 않은 결과도 검정 수·기준·통과 수와 함께 유지하세요. "
            "그래픽 초록 링크, 숫자, 링크, 인용, 링크된 제목과 표 모양은 바꾸지 마세요. "
            "'노출'처럼 뜻이 여러 개인 전문어는 처음에 짧게 정의하세요.\n" + note
            + "\n\n===== 요청 =====\n" + request_text.strip()
            + "\n\n" + "\n".join(parts))


def _bundle_section(text: str, title: str) -> list[str]:
    match = re.search(rf"(?m)^## {re.escape(title)}\s*$", text or "")
    if match is None:
        return []
    end = re.search(r"(?m)^## ", text[match.end():])
    body = text[match.end():match.end() + end.start()] if end else text[match.end():]
    return [line.strip() for line in body.splitlines() if line.strip()]


def _task_record(req: dict) -> str:
    lines = ["# 작업 지시 기록", "", "재현 안내에서 뺀 원래 단계별 작업 지시입니다.", ""]
    steps = [step for step in ((req.get("plan") or {}).get("steps") or []) if isinstance(step, Mapping)]
    for step in steps:
        step_id = str(step.get("id") or "단계")
        deps = ", ".join(map(str, step.get("depends_on") or [])) or "없음"
        lines += [f"## {step_id}", "", f"- 의존 단계: {deps}", "", str(step.get("instruction") or "기록 없음"), ""]
    if not steps:
        lines.append("단계별 지시가 기록되지 않았습니다.")
    return "\n".join(lines).rstrip() + "\n"


def _reproduce_document(req: dict, settings, assets: Mapping[str, str],
                        environment_records: Mapping[str, Any], bundle_readme: str) -> str:
    steps = [step for step in ((req.get("plan") or {}).get("steps") or []) if isinstance(step, Mapping)]
    step_ids = [str(step.get("id")) for step in steps if step.get("id")]
    locks = sorted(name for name in assets if PurePosixPath(name).name.casefold() == "requirements.lock.txt")
    input_keys = sorted(set(re.findall(
        r"\bLABHQ_INPUT_[A-Z0-9_]+\b", "\n".join(body for body in assets.values() if isinstance(body, str)))))
    order = _bundle_section(bundle_readme, "단계 순서")
    raw_commands = _bundle_section(bundle_readme, "다시 실행")
    commands = [line if line.startswith("-") else f"- `{line}`" for line in raw_commands
                if line.startswith("-") or re.match(r"^(?:cd\s+|python(?:3)?\s+|Rscript\s+|bash\s+)", line)]
    if not order:
        order = [f"- `{step_id}`" for step_id in step_ids] or ["- 선언된 단계 없음"]
    if not commands:
        commands = ["- 기록된 실행 명령 없음"]

    lines = ["# 재현 방법", "", "정리본 루트에서 아래 순서대로 실행합니다. 원래 작업 지시는 [작업 지시 기록](task_record.md)에 따로 두었습니다.", "",
             "## 고정 환경", ""]
    if locks:
        lines += [f"- [{PurePosixPath(name).parts[1]} lock]({name})" for name in locks]
    else:
        lines.append("- lock 파일 없음: 요청 묶음의 환경 기록을 확인하세요.")
    for step_id, record in sorted(environment_records.items()):
        if not isinstance(record, Mapping):
            continue
        summary = {key: record[key] for key in ("python", "python_version", "version", "status") if key in record}
        lines.append(f"- `{step_id}` 버전 기록: `{json.dumps(summary or dict(record), ensure_ascii=False, sort_keys=True)[:240]}`")
    lines += ["", "## 입력 설정", "",
              "개인 경로는 코드에 넣지 않습니다. 접근 권한이 있는 로컬 파일 경로를 아래 환경 변수에 지정하세요.", ""]
    lines += [f"- `{key}`" for key in input_keys] or ["- 별도 로컬 입력 경로 설정 없음"]
    public_sources = [f"{row.get('kind')}: {row.get('value')}" for row in req.get("references") or []
                      if isinstance(row, Mapping) and row.get("kind") in {"url", "github", "doi", "pmid"}
                      and row.get("value")]
    lines += ["", "### 공개 입력 출처", "", *(f"- {source}" for source in public_sources)]
    if not public_sources:
        lines.append("- 기록 없음")
    lines += ["", "## 실행 순서와 명령", "", *order, "", *commands,
              "", "## 예상 출력", "", "| 단계 | 출력 |", "|---|---|"]
    for step in steps:
        outputs = [str(value) for value in step.get("outputs") or [] if isinstance(value, str)]
        outputs += [str(row.get("name")) for row in step.get("output_types") or []
                    if isinstance(row, Mapping) and row.get("name")]
        lines.append(f"| {step.get('id') or '-'} | {'<br>'.join(dict.fromkeys(outputs)) or '선언 없음'} |")
    if not steps:
        lines.append("| - | 선언 없음 |")

    internal = internal_data_steps(req, settings)
    request_internal = request_uses_internal_data(req, settings)
    if internal is None and request_internal:
        public_steps, restricted_steps = [], step_ids
    else:
        restricted = set(internal or ())
        public_steps = [step_id for step_id in step_ids if step_id not in restricted]
        restricted_steps = [step_id for step_id in step_ids if step_id in restricted]
    lines += ["", "## 재현 가능 범위", "", "### 공개 자료로 다시 만들 수 있는 것", "",
              "- " + (", ".join(public_steps) if public_steps else "없음"), "",
              "### 접근 제한 자료가 필요한 것", "",
              "- " + (", ".join(restricted_steps) if restricted_steps else "없음"), "",
              "원자료와 접근 자격은 정리본에 포함하지 않습니다."]
    return "\n".join(lines).rstrip() + "\n"


def draft_documents(req: dict, settings, included=None, assets=None, *, abstract: bool = False,
                    environment_records: Mapping[str, Any] | None = None,
                    bundle_readme: str = "") -> dict[str, str]:
    """Only derived prose enters this edition. Raw bundle data and binary files stay local.

    ``included``: the edition's file names; the report's figure links are made to resolve to them (#584).
    ``assets``: the edition's text outputs by name; its ``resource_versions`` files feed SOURCES.md (#607).
    ``abstract``: the edition holds the drawn graphical abstract (`edition_abstract`); it is the report's first figure (#608)."""
    report = reader_markdown(str(req.get("report") or "결과 보고서가 없습니다."))
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
    bibliography = re.search(r"(?im)^#{1,6}\s+(?:references|bibliography|참고(?:\s*문헌|\s*자료)?|출처)[^\n]*\n", report)
    citations = re.split(r"(?m)^#{1,6}\s+", report[bibliography.end():], maxsplit=1)[0] if bibliography else ""
    found = edition_sources.collect(
        [report, str(req.get("report_appendix") or "")], req.get("references") or [],
        [text for name, text in (assets or {}).items() if edition_sources.is_versions_file(name)])
    assets = assets or {}
    receipt = build_receipt({name: clean_publication_text(name, body, settings, req)
                             for name, body in assets.items()}, outputs=expected_outputs(req))
    docs = {
        "README.md": "# 연구 결과\n\n## 무엇을 왜 했나요\n\n" + str(req.get("text") or "") +
            "\n\n## 결과\n\n" + readme_excerpt(report) + "\n\n" + document_links("") + "\n",
        "report.md": report,
        "PROGRESS.md": "\n".join(progress),
        "REPRODUCE.md": _reproduce_document(
            req, settings, assets, environment_records or {}, bundle_readme),
        "TASK_RECORD.md": _task_record(req),
        "SOURCES.md": edition_sources.render(found, citations),
        "REPRODUCTION_RECEIPT.json": receipt_json(receipt),
    }
    docs["REPRODUCE.md"] += ("\n\n## 재현 검증\n\n" + receipt_note(receipt_summary(receipt))
                             + "\n\n[검사 receipt](REPRODUCTION_RECEIPT.json)\n")
    docs["report.md"] += ("\n\n## 재현 검증\n\n" + receipt_note(receipt_summary(receipt))
                         + "\n\n[검사 receipt](REPRODUCTION_RECEIPT.json)\n")
    return {name: clean_document(body, settings, req) for name, body in docs.items()}


class ResultStorage:
    def __init__(self, hub, transport=None):
        self.hub, self.s, self.transport = hub, hub.s, transport
        self.tasks: dict[str, asyncio.Task] = {}
        self.end_tasks: dict[str, asyncio.Task] = {}
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

    async def _new_github_choice(self, rid: str, name: str, visibility: str) -> tuple[dict, str]:
        """Validate #702's local-to-GitHub choice with the same non-creating permission probe."""
        if not _NAME.fullmatch(name):
            raise ValueError("새 저장소 이름은 영문·숫자·점·밑줄·하이픈으로 적어 주세요.")
        if visibility not in {"private", "public"}:
            raise ValueError("공개 범위는 private 또는 public입니다.")
        gh = self.client()
        if gh is None:
            raise ValueError(f"GitHub 토큰이 없습니다. `labhq init`으로 넣거나 {self.s.github.token_env} 환경 변수를 설정하세요"
                             f"({github_token.NEEDED_TEXT}).")
        try:
            login = (await gh._req("GET", "/user"))["login"]
        except GitHubHTTPError as exc:
            raise ValueError(f"GitHub가 토큰을 받지 않았습니다(HTTP {exc.status_code}). 새 토큰을 `labhq init`으로 넣으세요.") from None
        try:
            await gh._req("POST", "/user/repos", json={})
        except GitHubHTTPError as exc:
            if exc.status_code != 422:
                raise ValueError(f"이 토큰은 저장소를 만들 수 없습니다. 빠진 권한: Repository creation(또는 Administration 쓰기)"
                                 f"(HTTP {exc.status_code}). "
                                 f"권한 있는 토큰을 `labhq init`으로 넣으세요: {github_token.TOKEN_SETTINGS_URL}") from None
        note = ("fine-grained 토큰이면 Repository access가 All repositories여야 새 저장소에 올릴 수 있습니다."
                if github_token.read_token(self.s).startswith(github_token.FINE_GRAINED_PREFIX) else "")
        return {"mode": "new", "repo": f"{login}/{name}", "visibility": visibility,
                "allow_public": visibility == "public", "status": "selected"}, note

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
            self._mark_end_ask(req, req["result_storage"])
            self.hub.save_request(rid)
            return True
        project = self.s.project(req.get("project_id"))
        if project:
            can_publish = project.repo and (project.visibility != "public" or project.allow_public_reports)
            req["result_storage"] = {"mode": "existing" if can_publish else "local", "repo": project.repo,
                "visibility": project.visibility, "allow_public": project.allow_public_reports,
                "branch": project.branch, "status": "selected"}
            self._mark_end_ask(req, req["result_storage"])
            self.hub.save_request(rid)
            return True
        return False

    async def ainherited(self, rid):
        req = self.hub.requests[rid]
        if req.get("result_storage"):
            return True
        parent = (req.get("meta") or {}).get("revised_from")
        previous = (self.hub.requests.get(parent) or {}).get("result_storage") or {}
        if previous:
            req["result_storage"] = {key: value for key, value in previous.items()
                                     if key in {"mode", "repo", "visibility", "allow_public", "branch"}}
            req["result_storage"]["status"] = "selected"
            self._mark_end_ask(req, req["result_storage"])
            await persist_request(self.hub, rid)
            return True
        project = self.s.project(req.get("project_id"))
        if project:
            can_publish = project.repo and (project.visibility != "public" or project.allow_public_reports)
            req["result_storage"] = {"mode": "existing" if can_publish else "local", "repo": project.repo,
                "visibility": project.visibility, "allow_public": project.allow_public_reports,
                "branch": project.branch, "status": "selected"}
            self._mark_end_ask(req, req["result_storage"])
            await persist_request(self.hub, rid)
            return True
        return False



    @staticmethod
    def _choice_snapshot(saved: dict) -> dict:
        return {key: saved[key] for key in (
            "mode", "repo", "visibility", "allow_public", "branch", "progress_push", "progress_branch"
        ) if key in saved}

    def _mark_end_ask(self, req: dict, saved: dict) -> None:
        """Only choices made by a request running on this version get the new end question.

        Persisted requests from before #688 have no marker, so a gateway upgrade never reopens an already reviewed run.
        """
        if (req.get("status") and req.get("status") not in {"done", "completed", "failed", "cancelled"}
                and not req.get("pi_reviewed_at")):
            req["storage_end_ask"] = {"state": "pending", "initial": self._choice_snapshot(saved)}

    @staticmethod
    def _choice_label(saved: dict) -> str:
        mode = saved.get("mode")
        if mode == "local":
            return "로컬"
        if mode == "new":
            return f"새 GitHub 저장소 {saved.get('repo') or ''}".strip()
        if mode == "existing":
            return f"기존 GitHub 저장소 {saved.get('repo') or ''}".strip()
        return "선택 없음"

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
                summary=STORAGE_QUESTION, detail={"storage": storage, "late_storage": True}
                ).model_dump(mode="json", exclude={"timeout_s"})
            self.hub.approvals[approval["id"]] = {"approval": approval, "origin": None}
            self.hub.save_approval(approval["id"])
            await self.hub.publish({"type": "approval.requested", "request_id": rid, "data": approval})
            return approval

    async def open_end_card(self, rid):
        """Ask once more after a newly marked, successful request has been reviewed."""
        async with self._card_lock:
            req = self.hub.requests[rid]
            marker = req.get("storage_end_ask") or {}
            saved = req.get("result_storage") or {}
            if marker.get("state") not in {"pending", "asked"}:
                return None
            if (req.get("status") not in {"done", "completed"} or not req.get("pi_reviewed_at")
                    or saved.get("status") == "published"):
                return None
            for entry in self.hub.approvals.values():
                approval = entry["approval"]
                if (approval.get("request_id") == rid
                        and (approval.get("detail") or {}).get("storage_end")):
                    marker["state"] = "asked"
                    return approval
            storage = await self.card(rid, end=True)
            initial = marker.get("initial") or self._choice_snapshot(saved)
            storage.update(default="keep", allow_keep=True, initial_label=self._choice_label(initial))
            approval = ApprovalRequest(kind="result_storage", request_id=rid,
                summary=STORAGE_QUESTION, detail={"storage": storage, "storage_end": True}
                ).model_dump(mode="json", exclude={"timeout_s"})
            marker["state"] = "asked"
            self.hub.approvals[approval["id"]] = {"approval": approval, "origin": None}
            await persist_request(self.hub, rid)
            self.hub.save_approval(approval["id"])
            await self.hub.publish({"type": "approval.requested", "request_id": rid, "data": approval})
            return approval

    def check_end_choice(self, rid: str, *, decision: str = "accepted") -> None:
        """Refuse an inline end-storage answer before review persistence when no end card can open (#714)."""
        req = self.hub.requests[rid]
        marker = req.get("storage_end_ask") or {}
        saved = req.get("result_storage") or {}
        status = req.get("status")
        if status == "review_pending" and decision == "accepted":
            status = "done"
        if (marker.get("state") in {"pending", "asked"} and status in {"done", "completed"}
                and saved.get("status") != "published"):
            return
        if saved.get("mode") == "local" and saved.get("status") == "local":
            command = f"`labhq storage {rid} --to github`"
        elif saved.get("status") in {"draft", "declined", "failed"}:
            command = f"`labhq storage {rid} --redraft`"
        else:
            command = f"`labhq review-done {rid}`에서 --storage를 빼고"
        raise ValueError(f"이 요청에는 끝 저장 위치 질문이 없습니다. 검토 완료는 저장하지 않았습니다. {command} 실행해 주세요.")

    async def card(self, rid, *, late=False, end=False):
        req = self.hub.requests[rid]
        if not end and (req.get("result_storage") or (not late and await self.ainherited(rid))):
            return None
        research = (req.get("intake") or {}).get("work_kind") == "research" or req.get("research_contract")
        if not late and not end and not research and not self.s.github.ask_storage:
            return None
        warning = ""
        available = False
        gh = self.client()
        later = f"나중에 토큰을 넣고 `labhq storage {rid} --to github`로 올릴 수 있습니다."
        if gh is None:
            warning = (f"GitHub 토큰이 없어 로컬에 남깁니다. `labhq init`으로 넣거나 {self.s.github.token_env} 환경 변수를 "
                       f"설정하세요({github_token.NEEDED_TEXT}). {later}")
        else:
            try:
                await gh._req("GET", "/user")
                available = True
            except Exception:
                warning = ("GitHub 권한을 확인하지 못해 로컬에 남깁니다. 토큰이 만료됐거나 권한이 모자랄 수 있습니다"
                           f"({github_token.NEEDED_TEXT}). {later}")
        card = {"default": "local", "suggested_name": "labhq-" + rid.removeprefix("req_"),
                "github_available": available, "warning": warning}
        if not end and not late:
            card.update(progress_question="진행 과정도 단계마다 올릴까요?", progress_default=False,
                        progress_available=available and not controlled_request(req, self.s))
        return card

    async def select(self, rid, choice, *, late=False, end=False, approval_id=None):
        async with self._card_lock:
            return await self._select(rid, choice, late=late, end=end, approval_id=approval_id)

    async def _select(self, rid, choice, *, late=False, end=False, approval_id=None):
        req = self.hub.requests[rid]
        requested = copy.deepcopy(choice)
        receipt = req.get("storage_selection_receipt") or {}
        if approval_id and receipt.get("approval_id") == approval_id:
            if (receipt.get("choice") != requested or receipt.get("late") != late
                    or receipt.get("end") != end):
                raise ValueError("이미 저장한 카드 선택과 다릅니다. 같은 선택으로 다시 답해 주세요.")
            # A commit can precede cancellation or a crash before card resolution.
            # Replaying that exact card completes its scheduling without a second choice/API call.
            if late or end:
                await self.aschedule(rid)
            return req["result_storage"]
        if late and req.get("result_storage"):
            raise ValueError("이미 저장 위치를 골랐거나 게시한 요청입니다.")
        check_choice(choice, end=end)
        mode = choice.get("mode", "local")
        marker = req.get("storage_end_ask") or {}
        if end and marker.get("state") not in {"pending", "asked"}:
            raise ValueError("끝 저장 위치 질문이 없는 요청입니다.")
        keep_initial = end and mode == "keep"
        if keep_initial:
            choice = dict(marker.get("initial") or {})
            mode = choice.get("mode", "local")
        saved = {**choice, "mode": mode, "status": "selected"} if keep_initial else {
            "mode": "local", "status": "selected"}
        if not keep_initial and mode in {"new", "existing"}:
            repo = str(choice.get("repo") or "").strip()
            if end and mode == "new" and "/" in repo:
                repo = repo.rsplit("/", 1)[-1]
            if mode == "new" and not _NAME.fullmatch(repo):
                raise ValueError("새 저장소 이름은 영문·숫자·점·밑줄·하이픈으로 적어 주세요.")
            if mode == "existing" and (not _REPO.fullmatch(repo) or any(p in {".", ".."} for p in repo.split("/"))):
                raise ValueError("기존 저장소는 소유자/저장소 이름으로 적어 주세요.")
            gh = self.client()
            try:
                if end and mode == "new":
                    saved, _ = await self._new_github_choice(rid, repo, choice.get("visibility", "private"))
                elif gh is None:
                    raise ValueError("no token")
                elif mode == "new":
                    user = await gh._req("GET", "/user")
                    repo = f"{user['login']}/{repo}"
                else:
                    info = await gh.repo_info(repo)
                    if not info.get("permissions", {}).get("push"):
                        raise ValueError("no write permission")
                if not (end and mode == "new"):
                    visibility = choice.get("visibility", "private")
                    if mode == "existing" and not info.get("private", False) and visibility != "public":
                        raise ValueError("public repository requires public choice")
                    saved = {"mode": mode, "repo": repo, "visibility": visibility,
                             "allow_public": visibility == "public", "status": "selected"}
            except Exception as exc:
                if end:
                    if gh is None:
                        saved["warning"] = (f"GitHub 토큰이 없어 로컬에 남깁니다. `labhq init`으로 넣으세요"
                                            f"({github_token.NEEDED_TEXT}).")
                    else:
                        missing = ("Repository creation(또는 Administration 쓰기)" if mode == "new"
                                   else "Contents 쓰기")
                        saved["warning"] = (f"빠진 권한: {missing}. `labhq init`으로 권한 있는 토큰을 넣어 로컬 정리본을 "
                                            "나중에 GitHub로 옮길 수 있습니다.")
                else:
                    saved["warning"] = "GitHub에 쓸 권한이 없어 로컬에 남깁니다. 토큰의 저장소 읽기·쓰기 권한을 확인하세요."
        if late:
            saved["after_review"] = True
        if end:
            saved["after_review"] = True
            marker["state"] = "answered"
            marker["final"] = self._choice_snapshot(saved)
        progress_requested = bool(choice.get("progress_push")) and not late and not end
        if progress_requested and controlled_request(req, self.s):
            saved.update(progress_push=False,
                         progress_warning="통제 데이터 요청은 단계별 GitHub 진행 게시를 켤 수 없습니다.")
        elif progress_requested and mode in {"new", "existing"} and saved.get("mode") in {"new", "existing"}:
            saved.update(progress_push=True, progress_branch=self.s.github.progress_branch,
                         progress_status="selected")
        elif not end:
            saved["progress_push"] = False
        req["result_storage"] = saved
        if approval_id:
            req["storage_selection_receipt"] = {"approval_id": approval_id, "choice": requested,
                                                 "late": late, "end": end}
        if not late and not end:
            self._mark_end_ask(req, saved)
        await persist_request(self.hub, rid)
        if late or end:
            await self.aschedule(rid)
        return saved

    @staticmethod
    def _progress_result(req: dict, step_id: str, result: Any | None) -> dict:
        if result is not None:
            if hasattr(result, "model_dump"):
                return result.model_dump(mode="json")
            if isinstance(result, Mapping):
                return dict(result)
        stored = req.get("results") if isinstance(req.get("results"), Mapping) else {}
        value = stored.get(step_id)
        return dict(value) if isinstance(value, Mapping) else {}

    def _progress_payload(self, req: dict, step_id: str, result: Any | None = None
                          ) -> tuple[dict[str, str | bytes], dict[str, list[str]], set[str], dict[str, list[str]]]:
        """Build one conservative progress update from reported bundle outputs only.

        Progress never carries tables or raster images. An internal step carries no output at all, and a file that
        trips the publication scan is omitted rather than scrubbed into a misleading partial artifact.
        """
        current = self._progress_result(req, step_id, result)
        shown = dict(req)
        results = dict(req.get("results") or {})
        if current:
            results[step_id] = current
        shown["results"] = results
        scope = internal_data_steps(shown, self.s)
        request_internal = request_uses_internal_data(shown, self.s)
        internal_steps = ({str(step.get("id")) for step in (shown.get("plan") or {}).get("steps") or []}
                          if request_internal and scope is None else set(scope or ()))
        saved = req.get("result_storage") or {}
        omitted = {str(key): list(value) for key, value in (saved.get("progress_omitted") or {}).items()}
        published = {str(key): list(value) for key, value in (saved.get("progress_files") or {}).items()}
        completed_steps = {str(key) for key, value in results.items() if isinstance(value, Mapping)}
        for completed in completed_steps:
            omitted.pop(completed, None)
            published.pop(completed, None)
        payload: dict[str, str | bytes] = {}

        for name, path in edition_candidates(shown, self.s).items():
            parts = PurePosixPath(name).parts
            source_step = parts[1]
            if (source_step not in completed_steps or source_step in internal_steps
                    or PurePosixPath(name).suffix.lower() not in _PROGRESS_OUTPUTS):
                continue
            suffix = PurePosixPath(name).suffix.lower()
            raw = path.read_bytes()
            raw_body: str | bytes = raw if suffix == ".svg" else raw.decode("utf-8", errors="replace")
            if scan_publish_files({name: raw_body}, internal=False, settings=self.s):
                omitted.setdefault(source_step, []).append(PurePosixPath(name).name)
                continue
            try:
                body = (clean_svg_for_publish(raw, self.s, shown) if suffix == ".svg"
                        else clean_document(raw.decode("utf-8"), self.s, shown))
            except (UnicodeDecodeError, ValueError):
                omitted.setdefault(source_step, []).append(PurePosixPath(name).name)
                continue
            if scan_publish_files({name: body}, internal=False, settings=self.s):
                omitted.setdefault(source_step, []).append(PurePosixPath(name).name)
                continue
            relative = PurePosixPath(*parts[3:]).as_posix()
            target = f"progress/steps/{source_step}/{relative}"
            payload[target] = body
            published.setdefault(source_step, []).append(target)

        rows = ["# 요청 진행 상황", "", f"- 요청: `{req.get('id')}`",
                f"- 진행 branch: `{(req.get('result_storage') or {}).get('progress_branch')}`", "",
                "| 단계 | 상태 | 공개 산출 | 요약 |", "|---|---|---|---|"]
        plan_steps = (shown.get("plan") or {}).get("steps") or []
        for step in plan_steps:
            sid = str(step.get("id") or "단계")
            value = results.get(sid) if isinstance(results.get(sid), Mapping) else {}
            if sid in internal_steps:
                state = "내부 단계 · 올리지 않음"
                summary = "내부 단계 요약은 로컬 기록에만 둠"
            elif not value:
                state = "대기"
                summary = "-"
            elif value.get("ok") is False:
                state = "실패"
                summary = "실패 이유는 로컬 기록에서 확인"
            else:
                state = "완료"
                raw_summary = str(value.get("text") or "").strip()
                if raw_summary and scan_publish_files({f"progress/{sid}-summary.md": raw_summary}, internal=False, settings=self.s):
                    if "단계 요약" not in omitted.setdefault(sid, []):
                        omitted[sid].append("단계 요약")
                    summary = "검사로 뺌"
                else:
                    cleaned_summary = clean_document(raw_summary, self.s, shown) if raw_summary else "요약 없음"
                    summary = " ".join(cleaned_summary.split())[:240]
            files = [PurePosixPath(name).name for name in published.get(sid, [])]
            detail = ", ".join(files) or "없음"
            if omitted.get(sid):
                detail += (" · " if detail != "없음" else "") + "검사로 뺌: " + ", ".join(omitted[sid])
            safe_sid = " ".join(sid.replace("|", "\\|").split())
            safe_detail = " ".join(detail.replace("|", "\\|").split())
            safe_summary = " ".join(summary.replace("|", "\\|").split())
            rows.append(f"| {safe_sid} | {state} | {safe_detail} | {safe_summary} |")
        readme = "\n".join(rows) + "\n"
        if issues := scan_publish_files({"progress/README.md": readme}, internal=False, settings=self.s):
            raise check_failure(issues)
        payload["progress/README.md"] = readme
        return payload, omitted, internal_steps, published

    async def _progress_branch(self, rid: str, saved: dict, gh: GitHubClient) -> str:
        repo = saved["repo"]
        try:
            info = await gh.repo_info(repo)
        except GitHubHTTPError as exc:
            if exc.status_code != 404 or saved.get("mode") != "new":
                raise
            try:
                info = await gh._req("POST", "/user/repos", json={
                    "name": repo.split("/", 1)[1], "private": saved.get("visibility") != "public",
                    "auto_init": True,
                })
            except GitHubHTTPError as collision:
                if collision.status_code != 422:
                    raise
                info = await gh.repo_info(repo)
        if not info.get("private", False) and not saved.get("allow_public"):
            raise PublishAccessError("공개 저장소에 진행 상황을 올리도록 승인하지 않았습니다.")
        if info.get("permissions") is not None and not info.get("permissions", {}).get("push"):
            raise PublishAccessError("진행 branch에 쓸 Contents 권한이 없습니다.")
        default = info.get("default_branch") or "main"
        branch = saved.get("progress_branch") or self.s.github.progress_branch
        try:
            await gh.branch_sha(repo, branch)
        except GitHubHTTPError as exc:
            if exc.status_code != 404:
                raise
            base = await gh.branch_sha(repo, default)
            if not base:
                raise PublishAccessError("진행 branch를 만들 기준 commit이 없습니다.")
            await gh.create_branch(repo, branch, base)
        return branch

    async def _progress_warning(self, rid: str, saved: dict, text: str, *, disable: bool) -> None:
        saved.update(progress_warning=text, progress_status="disabled" if disable else "warning")
        if disable:
            saved["progress_push"] = False
        await persist_request(self.hub, rid)
        await self.hub.publish({"type": "request.progress_push", "request_id": rid,
                                "data": {"ok": False, "warning": text, "disabled": disable}})

    async def progress_step(self, rid: str, step_id: str, result: Any | None = None) -> None:
        """Best-effort per-step progress upload. No failure escapes into orchestration."""
        req = self.hub.requests[rid]
        saved = req.get("result_storage") or {}
        if not saved.get("progress_push") or saved.get("mode") not in {"new", "existing"}:
            return
        if controlled_request(req, self.s):
            await self._progress_warning(
                rid, saved, "통제 데이터 요청은 단계별 GitHub 진행 게시를 켤 수 없어 껐습니다.", disable=True)
            return
        gh = self.client()
        if gh is None:
            await self._progress_warning(
                rid, saved, "GitHub 토큰이 없어 단계별 진행 게시를 껐습니다. 토큰을 넣은 뒤 다음 요청에서 켜 주세요.",
                disable=True)
            return
        try:
            branch = await self._progress_branch(rid, saved, gh)
            payload, omitted, internal_steps, published = self._progress_payload(req, step_id, result)
            for name, body in payload.items():
                if isinstance(body, bytes):
                    await gh.put_binary(saved["repo"], name, body, branch, f"labhq 진행: {step_id}")
                else:
                    await gh.put_file(saved["repo"], name, body, f"labhq 진행: {step_id}", branch)
            saved.update(progress_status="pushed", progress_last_step=step_id,
                         progress_files=published, progress_omitted=omitted)
            saved.pop("progress_warning", None)
            await persist_request(self.hub, rid)
            await self.hub.publish({"type": "request.progress_push", "request_id": rid, "data": {
                "ok": True, "step_id": step_id, "branch": branch,
                "omitted": omitted.get(step_id, []), "internal": step_id in internal_steps,
            }})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            denied = _denial(exc)
            http_status = (exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else
                           exc.status_code if isinstance(exc, GitHubHTTPError) else None)
            permission = bool(denied or isinstance(exc, PublishAccessError)
                              or http_status in {401, 403, 404})
            text = ("GitHub 권한이 없어 단계별 진행 게시를 껐습니다. Contents 쓰기 권한을 확인하세요."
                    if permission else "단계별 GitHub 진행 게시에 실패했습니다. 요청은 계속 진행하며 다음 단계에서 다시 시도합니다.")
            await self._progress_warning(rid, saved, text, disable=permission)

    def schedule(self, rid):
        req = self.hub.requests[rid]
        action = self._schedule_action(req)
        if action == "skip":
            self._skip_end_marker(req)
            self.hub.save_request(rid)
            action = self._schedule_action(req)
        self._schedule_ready(rid, action)

    async def aschedule(self, rid):
        req = self.hub.requests[rid]
        action = self._schedule_action(req)
        if action == "skip":
            await update_request(self.hub, rid, self._skip_end_marker)
            action = self._schedule_action(self.hub.requests[rid])
        self._schedule_ready(rid, action)

    def _skip_end_marker(self, req):
        marker = req.get("storage_end_ask") or {}
        marker["state"] = "skipped"
        marker["final"] = self._choice_snapshot(req.get("result_storage") or {})

    def _schedule_action(self, req):
        saved = req.get("result_storage") or {}
        marker = req.get("storage_end_ask") or {}
        if marker.get("state") in {"pending", "asked"}:
            if req.get("status") in {"failed", "cancelled"} or saved.get("status") == "published":
                return "skip"
            else:
                if (req.get("status") in {"done", "completed"} and req.get("pi_reviewed_at")
                        and not req.get("bundle_pending")):
                    return "end"
                return
        if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
            return
        if not saved or saved.get("status") in {"published", "local", "failed", "declined"}:
            return
        if saved.get("status") == "interrupted" and self._editor_connection() is None:
            return  # A gateway restart must also wait for the editor, not publish a fallback draft.
        return "finish"

    def _schedule_ready(self, rid, action):
        if action == "end":
            task = self.end_tasks.get(rid)
            if task is None or task.done():
                self.end_tasks[rid] = asyncio.create_task(self.open_end_card(rid))
        elif action == "finish" and (rid not in self.tasks or self.tasks[rid].done()):
            self.tasks[rid] = asyncio.create_task(self.finish(rid))

    def _editor_connection(self):
        runner = getattr(self.hub, "agent_runner", {}).get("editor")
        return getattr(self.hub, "runners", {}).get(runner)

    def resume_interrupted(self):
        if self._editor_connection() is None:
            return
        for rid, req in self.hub.requests.items():
            if (req.get("result_storage") or {}).get("status") != "interrupted":
                continue
            task = self.tasks.get(rid)
            if task is not None and not task.done():
                # The new runner can connect during finish's final state publication.
                task.add_done_callback(lambda _: self.resume_interrupted())
            else:
                self.schedule(rid)

    async def redraft(self, rid) -> dict:
        """Run the editor again for an edition that is drafted but not published, and replace its publish card (#607).

        Allowed while the publish card is pending (or its answer window passed), after the PI declined it, and after an
        upload failed (`republish` reuses the local draft instead). A published edition is refused: its files are already
        in the repository. The old card is closed and the new draft opens a new one; the folder is rebuilt, so nothing of
        the old draft stays."""
        async with self.card_lock:
            req = self.hub.requests[rid]
            saved = req.get("result_storage") or {}
            status = saved.get("status")
            if status == "published":
                raise ValueError("이미 게시한 정리본은 다시 만들 수 없습니다. 저장소에 올라간 파일을 이 명령이 바꾸지 않습니다. "
                                 f"지금 편집기로 새 판을 만들려면 `labhq storage {rid} --rebuild`를 쓰세요.")
            if status not in {"draft", "declined", "failed"}:
                raise ValueError("다시 만들 정리본이 없습니다. 정리본을 만든 뒤 게시 승인을 기다리거나, 거절했거나, 게시에 실패한 요청만 "
                                 f"다시 만들 수 있습니다(저장 상태: {status or '선택 없음'}).")
            if req.get("status") not in {"done", "completed", "failed", "cancelled"} or req.get("cancelling"):
                raise ValueError("끝난 요청만 정리본을 다시 만들 수 있습니다.")
            if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
                raise ValueError("보고서 검토 완료와 묶음 갱신 뒤에 다시 만들 수 있습니다.")
            if controlled_request(req, self.s):
                raise ValueError("통제 데이터가 포함된 요청은 편집자에게 보내지 않습니다.")
            cards = self.publish_cards(rid)
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
            await persist_request(self.hub, rid)
            self.tasks[rid] = asyncio.create_task(self.finish(rid))
            return {"request_id": rid, "status": "redrafting", "closed_cards": cards}

    async def to_github(self, rid, name: str | None = None, visibility: str = "private") -> dict:
        """Publish a request that ended local to a new GitHub repository (#688). On 10-16 there was no way: the late card
        refused a request that had a storage choice, so the lead copied the edition and pushed it by hand.

        The token is checked first: GET /user names the owner, and an empty POST /user/repos answers 422 when the token may
        create repositories (403 without; nothing is made, #687). Then the choice becomes a new repository and the editor
        runs again, since a local edition is not laid out for a repository root. The publish card and the report-review
        condition stay as they are: nothing is uploaded before the PI answers the card."""
        async with self.card_lock:
            req = self.hub.requests[rid]
            saved = req.get("result_storage") or {}
            if not saved:
                raise ValueError(f"아직 저장 위치를 고르지 않은 요청입니다. `labhq storage {rid}`로 저장 위치 카드를 여세요.")
            if saved.get("mode") != "local" or saved.get("status") != "local":
                raise ValueError(f"로컬에 남긴 정리본만 GitHub로 옮깁니다(저장 위치 {saved.get('mode')}, 상태 {saved.get('status')}). "
                                 f"게시에 실패했으면 `labhq storage {rid} --republish`, 정리본을 다시 만들려면 `--redraft`, "
                                 "게시한 정리본을 지금 편집기로 다시 만들려면 `--rebuild`를 쓰세요.")
            if req.get("status") == "review_pending":
                # CP4: the final draft waits in the report review. Say what to run, so a retry is not spent on this.
                raise ValueError(f"보고서 검토 대기(review_pending) 중인 요청은 아직 옮길 수 없습니다. 먼저 보고서 카드에 적힌 "
                                 f"번호와 hash로 `labhq review-done {rid} --revision <번호> --sha256 <hash>`를 실행해 검토를 마치거나, "
                                 "같은 명령에 `--storage github`를 붙여 검토와 저장 선택을 한 번에 하세요.")
            if req.get("status") not in {"done", "completed", "failed", "cancelled"} or req.get("cancelling"):
                raise ValueError("끝난 요청만 GitHub로 옮길 수 있습니다.")
            if controlled_request(req, self.s):
                raise ValueError("통제 데이터가 포함된 요청은 게시하지 않습니다.")
            running = self.tasks.get(rid)
            if rid in self.uploading or (running is not None and not running.done()):
                raise ValueError("정리 작업이 진행 중입니다. 끝난 뒤 다시 실행하세요.")
            name = (name or "labhq-" + rid.removeprefix("req_")).strip()
            choice, note = await self._new_github_choice(rid, name, visibility)
            # A branch inherited from a project names a branch of that repository, not of the new one (PR #702 review):
            # the new repository's default branch is used instead.
            for key in ("warning", "error_type", "draft_path", "branch"):
                saved.pop(key, None)
            saved.update(choice, after_review=True, converted_from="local")
            await persist_request(self.hub, rid)
            self.schedule(rid)
            return {"request_id": rid, "status": "converting", "repo": saved["repo"],
                    "waiting_review": bool(not req.get("pi_reviewed_at") or req.get("bundle_pending")), "note": note}

    async def rebuild(self, rid, name: str | None = None, visibility: str = "private") -> dict:
        """Make a published edition again with the editor as it is now (#754). On 10-19 the PI asked for a request
        published before #736's editor fixes; `redraft` refuses a published edition, so the lead rebuilt it by hand.

        Without ``name`` the new edition goes to the same repository as a new commit; with ``name`` to a new repository,
        checked first like `to_github`. The old repository is never changed in the second case. Either way the publish card
        asks before anything is uploaded, even with ``auto_publish_results``, and the README names the editor version and
        the edition it replaces. Files only the old edition had stay in the repository: this command does not delete."""
        async with self.card_lock:
            req = self.hub.requests[rid]
            saved = req.get("result_storage") or {}
            if saved.get("status") != "published":
                raise ValueError(f"게시한 정리본만 다시 만듭니다(저장 상태: {saved.get('status') or '선택 없음'}). "
                                 f"아직 게시하지 않았으면 `labhq storage {rid} --redraft`, 로컬에 남겼으면 `--to github`를 쓰세요.")
            if req.get("status") not in {"done", "completed", "failed", "cancelled"} or req.get("cancelling"):
                raise ValueError("끝난 요청만 정리본을 다시 만들 수 있습니다.")
            if not req.get("pi_reviewed_at") or req.get("bundle_pending"):
                raise ValueError("보고서 검토 완료와 묶음 갱신 뒤에 다시 만들 수 있습니다.")
            if req.get("revised_to"):
                raise ValueError(f"수정 요청 {req['revised_to']}로 이어진 요청입니다. 그 요청의 정리본을 다시 만드세요.")
            if (req.get("storage_end_ask") or {}).get("state") in {"pending", "asked"}:
                raise ValueError("끝 저장 위치 질문에 먼저 답해 주세요.")
            if controlled_request(req, self.s):
                raise ValueError("통제 데이터가 포함된 요청은 편집자에게 보내지 않습니다.")
            running = self.tasks.get(rid)
            if rid in self.uploading or (running is not None and not running.done()):
                raise ValueError("정리 작업이 진행 중입니다. 끝난 뒤 다시 실행하세요.")
            previous = {key: saved[key] for key in ("mode", "repo", "branch", "visibility", "report_url") if key in saved}
            note = ""
            if name is not None:
                choice, note = await self._new_github_choice(rid, name.strip(), visibility)
                if await self._same_repository(choice["repo"], previous.get("repo")):
                    raise ValueError("새 저장소 이름이 지금 저장소와 같습니다. 같은 저장소에 새 판으로 올리려면 --name 없이 실행하세요.")
            else:
                choice = {}
            for key in ("draft_path", "pdf", "warning", "error_type", "report_url", "round_warning",
                        *(("branch",) if choice else ())):  # a new repository uses its own default branch
                saved.pop(key, None)
            saved.update(choice, status="selected", after_review=True)
            saved.setdefault("editions", []).append(previous)
            saved["rebuild"] = {"from_repo": previous.get("repo"), "from_url": previous.get("report_url"),
                                "repo": saved.get("repo"), "editor": editor_edition(), "date": datetime.date.today().isoformat()}
            await persist_request(self.hub, rid)
            self.tasks[rid] = asyncio.create_task(self.finish(rid))
            return {"request_id": rid, "status": "rebuilding", "repo": saved.get("repo"),
                    "new_repository": bool(choice), "note": note}

    async def _same_repository(self, new: str, current: str | None) -> bool:
        """Whether a `--rebuild --name` target is the current repository (PR #777 review). A new edition goes to the
        repository root, so an alias would overwrite the current repository's root files. GitHub names are
        case-insensitive and a renamed repository still answers its old name, so the canonical `full_name` decides."""
        if not current:
            return False
        if new.casefold() == current.casefold():
            return True
        gh = self.client()
        if gh is None:
            return False  # nothing can be uploaded without a token; the upload asks GitHub again
        try:
            found = (await gh.repo_info(new)).get("full_name") or new
            if found.casefold() == current.casefold():
                return True
            existing = (await gh.repo_info(current)).get("full_name") or current
        except GitHubHTTPError as exc:
            if exc.status_code == 404:
                return False  # no repository answers the new name (or the current one is gone): it is not the current one
            raise ValueError(f"새 저장소가 지금 저장소와 다른지 GitHub에서 확인하지 못했습니다(HTTP {exc.status_code}). "
                             "잠시 뒤 다시 실행하세요.") from None
        except httpx.HTTPError:
            raise ValueError("새 저장소가 지금 저장소와 다른지 GitHub에서 확인하지 못했습니다(연결 실패). "
                             "잠시 뒤 다시 실행하세요.") from None
        return found.casefold() == existing.casefold()

    def publish_cards(self, rid) -> list[str]:
        return [aid for aid, entry in self.hub.approvals.items()
                if entry["approval"].get("request_id") == rid and entry["approval"].get("kind") == "result_publish"]

    async def republish(self, rid) -> dict:
        """Open the publish card again for an edition whose upload failed, from the local draft the PI reviewed (#607).

        The editor does not run again (`redraft` does that): the folder is read as it is, so the card shows the same files.
        Refused: a published edition (its files are in the repository), an edition that did not fail (its card is pending or
        declined, so `redraft`), an unreviewed or controlled-data request, an upload still going, and a failed edition whose
        local draft is gone. Held under the lock of `redraft` and of the answer to a publish card."""
        async with self.card_lock:
            req = self.hub.requests[rid]
            saved = req.get("result_storage") or {}
            status = saved.get("status")
            if status == "published":
                raise ValueError("이미 게시한 정리본은 다시 올릴 수 없습니다. 저장소에 올라간 파일을 이 명령이 바꾸지 않습니다. "
                                 f"지금 편집기로 새 판을 만들려면 `labhq storage {rid} --rebuild`를 쓰세요.")
            if status != "failed":
                raise ValueError(f"게시에 실패한 정리본만 다시 올릴 수 있습니다(저장 상태: {status or '선택 없음'}). "
                                 f"게시 카드가 떠 있거나 거절한 정리본은 `labhq storage {rid} --redraft`로 다시 만드세요.")
            if req.get("status") not in {"done", "completed", "failed", "cancelled"} or req.get("cancelling"):
                raise ValueError("끝난 요청만 정리본을 다시 올릴 수 있습니다.")
            if not req.get("pi_reviewed_at") or req.get("bundle_pending"):
                raise ValueError("보고서 검토 완료와 묶음 갱신 뒤에 다시 올릴 수 있습니다.")
            if controlled_request(req, self.s):
                raise ValueError("통제 데이터가 포함된 요청은 게시하지 않습니다.")
            running = self.tasks.get(rid)
            if rid in self.uploading or (running is not None and not running.done()):
                raise ValueError("게시가 진행 중입니다. 끝난 뒤 결과를 확인해 주세요.")
            draft = saved.get("draft_path")
            if not draft or not plain_path(Path(draft)) or not Path(draft, "README.md").is_file():
                raise ValueError(f"다시 올릴 로컬 정리본이 없습니다. `labhq storage {rid} --redraft`로 정리본을 다시 만드세요.")
            cards = self.publish_cards(rid)
            for aid in cards:
                await self.hub.close_approval(aid, "게시에 실패한 정리본을 다시 올려 이 게시 카드를 닫았습니다")
            for key in ("warning", "error_type"):
                saved.pop(key, None)
            saved["status"] = "draft"  # finish reads the folder back and opens the card, as after a restart
            await persist_request(self.hub, rid)
            self.tasks[rid] = asyncio.create_task(self.finish(rid))
            return {"request_id": rid, "status": "republishing", "closed_cards": cards}

    async def finish(self, rid):
        req = self.hub.requests[rid]
        saved = req.get("result_storage") or {}
        if (req.get("storage_end_ask") or {}).get("state") in {"pending", "asked"}:
            return
        if saved.get("after_review") and (not req.get("pi_reviewed_at") or req.get("bundle_pending")):
            return
        if not saved or req.get("status") not in {"done", "completed", "failed", "cancelled"}:
            return
        if saved.get("status") == "interrupted":
            if self._editor_connection() is None:
                return
            saved["status"] = "selected"
            for key in ("warning", "shutdown_evidence"):
                saved.pop(key, None)
        editor_connection = self._editor_connection()
        request_internal = request_uses_internal_data(req, self.s)
        step_scope = internal_data_steps(req, self.s)
        scope_mode = saved.get("internal_publication_scope")
        publication_internal = set(saved.get("internal_files") or [])
        internal_allowed = set(saved.get("internal_allowed_files") or [])
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
                        if name in {_PDF_NAME, _PORTFOLIO_PDF_NAME}:
                            payload = path.read_bytes()
                            if not payload.startswith(b"%PDF-"):
                                raise ValueError("invalid editor PDF")
                            pdf = payload
                        elif path.suffix.lower() in _IMAGE_OUTPUTS:
                            payload = path.read_bytes()
                            images[name] = (clean_svg_for_publish(payload, self.s, req)
                                            if path.suffix.lower() == ".svg" else payload)
                        else:
                            docs[name] = path.read_bytes().decode("utf-8")
                if "README.md" not in docs:
                    raise ValueError("missing editor README")
                report_name = "report/report.md" if "report/report.md" in docs else "report.md"
                if report_name not in docs:
                    raise ValueError("missing editor report")
                if report_name == "report.md":
                    legacy = target / "report.md"
                    migrated = target / "report" / "report.md"
                    migrated.parent.mkdir(parents=True, exist_ok=True)
                    legacy.replace(migrated)
                    docs["report/report.md"] = docs.pop("report.md")
                    docs["README.md"] = nested_report_links(docs["README.md"])
                    (target / "README.md").write_text(docs["README.md"], encoding="utf-8")
                    report_name = "report/report.md"
                from .korean_qc import check_document
                saved["korean_qc"] = {
                    "README.md": check_document(docs["README.md"]),
                    report_name: check_document(docs[report_name]),
                }
            else:
                raw_assets, images = edition_assets(req, self.s)
                included = {*raw_assets, *images}
                drawn = edition_abstract(req, self.s, included)  # #608: the edition opens with a graphical abstract
                if drawn:
                    images[ABSTRACT_FILE] = clean_svg_for_publish(drawn.encode("utf-8"), self.s, req)
                    included.add(ABSTRACT_FILE)
                portfolio_images = ({name: body for name, body in images.items() if name != ABSTRACT_FILE}
                                    if drawn else images)
                bundle = self.s.path(self.s.runner.workspace_root) / "requests" / rid
                store = getattr(self.hub, "store", None)
                decisions = list(store.all("approval_decision").values()) if store else []
                tasks = list(store.all("task").values()) if store else []
                portfolio_included = {
                    *raw_assets,
                    *(name for name in images if PurePosixPath(name).suffix.lower() == ".svg"),
                }
                portfolio_req = dict(req)
                portfolio_req["report"] = edition_figure_links(
                    str(req.get("report") or ""), req, self.s, portfolio_included, "report.md")
                portfolio_assets = {
                    name: (edition_figure_links(body, req, self.s, portfolio_included, "report.md")
                           if PurePosixPath(name).suffix.lower() == ".md" else body)
                    for name, body in raw_assets.items()
                }
                canonical_pdf_report = pdf_report_text(
                    req, self.s, report_text=portfolio_report(req, self.s, raw_assets))
                aggregate_tables = declared_aggregate_tables(req)
                aggregate_tables.update(legacy_aggregate_tables(req, raw_assets))
                assets = {}
                for name, body in raw_assets.items():
                    if not table_allowed(
                            name, body,
                            internal=source_uses_internal_data(name, req, self.s, step_scope),
                            declared=aggregate_tables):
                        continue
                    assets[name] = body
                publication_internal = {
                    name for name in [*assets, *images]
                    if source_uses_internal_data(name, req, self.s, step_scope)
                }
                internal_allowed = set()
                included = {*assets, *images}
                bundle_readme = bundle / "README.md"
                manifest = bundle_readme.with_name("MANIFEST.tsv")
                environment_records: dict[str, Any] = {}
                if plain_path(manifest) and manifest.is_file():
                    with manifest.open(encoding="utf-8", newline="") as handle:
                        for row in csv.DictReader(handle, delimiter="\t"):
                            if row.get("status") == "summary" and row.get("environment_records"):
                                try:
                                    parsed = json.loads(row["environment_records"])
                                    if isinstance(parsed, dict):
                                        environment_records = parsed
                                except (json.JSONDecodeError, TypeError):
                                    pass
                                break
                bundle_text = ""
                if plain_path(bundle_readme) and bundle_readme.is_file():
                    bundle_text = bundle_readme.read_text(encoding="utf-8")
                input_readmes = edition_input_readmes(req)
                docs = draft_documents(
                    req, self.s, included, assets, abstract=bool(drawn),
                    environment_records=environment_records, bundle_readme=bundle_text)
                portfolio = build_portfolio_files(
                    portfolio_req, self.s, portfolio_assets, portfolio_images,
                    decisions=decisions, tasks=tasks, bundle=bundle,
                    run_documents={"run/progress.md": docs["PROGRESS.md"],
                                   "run/reproduce.md": docs["REPRODUCE.md"],
                                   "run/task_record.md": docs["TASK_RECORD.md"]},
                    internal_files=internal_allowed, input_readmes=input_readmes)
                # Keep the final publication paths/hashes, rather than the earlier bundle layout.
                req["reproduction_receipt"] = json.loads(portfolio[RECEIPT_PATH])
                saved["reproduction_receipt"] = receipt_summary(req["reproduction_receipt"], path=RECEIPT_PATH)
                if isinstance(req.get("report_package"), dict):
                    req["report_package"]["reproduction_receipt"] = saved["reproduction_receipt"]
                publication_internal.update(internal_allowed)
                for name, body in portfolio.items():
                    if isinstance(body, bytes):
                        images[name] = body
                        included.add(name)
                    else:
                        docs[name] = body
                if saved.get("mode") != "new" and (missing := document_links(docs["README.md"])):
                    docs["README.md"] += "\n\n" + missing + "\n"
                if len(str(req.get("report") or "")) > README_EXCERPT_CHARS:
                    report_path = "report/report.md" if saved.get("mode") == "new" else "report.md"
                    docs["README.md"] += f"\n이어지는 내용은 {report_path}에 있습니다.\n"
                links = "\n".join(f"- [{name}]({quote(name, safe='/')})" for name in [*assets, *images])
                if links:
                    if saved.get("mode") != "new":
                        docs["README.md"] += "\n## 그림·표·실행 파일\n\n" + links + "\n"
                    docs["REPRODUCE.md"] += "\n\n## 포함한 파일\n\n" + links + "\n"
                if "editor" in self.hub.agents:
                    try:
                        result = await self.hub.orchestrator.run_step(Task(
                            agent_id="editor", request_id=rid,
                            prompt=editor_prompt(docs, clean_document(str(req.get("text") or ""), self.s, req)),
                            output_schema={"type": "object", "properties": {"readme": {"type": "string"}},
                                           "required": ["readme"], "additionalProperties": False},
                            meta={"kind": "result_editor", "title": "결과를 저장소용으로 정리",
                                  "agent_overrides": {"sandbox": "read-only"}}))
                    except ShutdownInterrupted:
                        raise
                    except Exception:
                        result = None  # Budget/dispatch failures still leave the deterministic edition.
                    saved = req["result_storage"]  # PI review may replace the persisted request fields while we wait.
                    readme = (result.structured or {}).get("readme") if result and result.ok else None
                    if isinstance(readme, str) and readme.strip():
                        from .korean_qc import check_preservation
                        candidate = clean_document(readme, self.s, req)
                        preservation = check_preservation(docs["README.md"], candidate)
                        saved["humanizer_preservation"] = preservation
                        if preservation["accepted"]:
                            docs["README.md"] = candidate
                        elif (candidate.count("\n#") == 0 and not re.search(r"\]\([^)]*\)|\d", candidate)):
                            # Before #686 the editor returned only a short lead. Keep that lead without replacing
                            # any protected portfolio fact; new editors receive and preserve the complete draft.
                            docs["README.md"] = candidate.rstrip() + "\n\n" + docs["README.md"]
                        if saved.get("mode") != "new" and (missing := document_links(docs["README.md"])):
                            docs["README.md"] += "\n\n" + missing + "\n"
                        if links and saved.get("mode") != "new":
                            docs["README.md"] += "\n## 그림·표·실행 파일\n\n" + links + "\n"
                if saved.get("mode") != "new" and (missing := document_links(docs["README.md"])):
                    docs["README.md"] += "\n\n" + missing + "\n"
                if (links and saved.get("mode") != "new"
                        and "## 그림·표·실행 파일" not in docs["README.md"]):
                    docs["README.md"] += "\n## 그림·표·실행 파일\n\n" + links + "\n"
                if saved.get("mode") != "new":
                    lead_figure = next((name for name in [*images, *assets]
                                        if PurePosixPath(name).suffix.lower() in _IMAGE_OUTPUTS
                                        and not is_abstract_target(name)), None)
                    if lead_figure:
                        docs["README.md"] += f"\n![대표 그림]({quote(lead_figure, safe='/')})\n"
                if saved.get("rebuild"):  # after the editor, so its rewrite never drops the line (#754)
                    docs["README.md"] = place_rebuild_note(docs["README.md"], rebuild_note(saved["rebuild"]))
                for name in _GENERATED:  # the editor's README and the bundle's own text are linked here as well
                    docs[name] = edition_figure_links(docs[name], req, self.s, included, name)
                docs.update(assets)
                if saved.get("mode") == "new":
                    portfolio_payload = {name: body for name, body in {**docs, **images}.items()
                                         if portfolio_root_file(name)}
                    if broken := broken_markdown_links(portfolio_payload):
                        detail = ", ".join(f"{row['document']} -> {row['target']}" for row in broken)
                        raise EditionCheckError(f"정리본 Markdown 링크가 깨졌습니다: {detail}")
                scope_mode = "request" if request_internal and step_scope is None else "step"
                saved["internal_publication_scope"] = scope_mode
                saved["internal_files"] = sorted(publication_internal)
                saved["internal_allowed_files"] = sorted(internal_allowed)
                issues = scan_publish_files(
                    {**docs, **images},
                    internal=publication_internal if scope_mode == "step" else request_internal,
                    settings=self.s,
                )
                if issues:
                    raise check_failure(issues)
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
                pdf, reason = await self.edition_pdf(rid, req, report_text=canonical_pdf_report)
                saved = req["result_storage"]  # PI review may replace the persisted request fields while we print.
                if pdf:
                    pdf_link = _PORTFOLIO_PDF_NAME if saved.get("mode") == "new" else _PDF_NAME
                    docs["README.md"] = place_pdf_in_readme(docs["README.md"], link=pdf_link)
                    if reason:
                        docs["README.md"] += f"\n{reason}\n"
                    write(_PDF_NAME, pdf)
                    write(_PORTFOLIO_PDF_NAME, pdf)
                else:
                    docs["README.md"] = place_pdf_in_readme(docs["README.md"], reason=reason)
                    for name in (_PDF_NAME, _PORTFOLIO_PDF_NAME):
                        if plain_path(target / name):
                            (target / name).unlink(missing_ok=True)  # a PDF left by an earlier attempt is not this edition's
                from .korean_qc import check_document
                report_name = "report/report.md" if "report/report.md" in docs else "report.md"
                saved["korean_qc"] = {
                    "README.md": check_document(docs["README.md"]),
                    report_name: check_document(docs[report_name]),
                }
                write("README.md", docs["README.md"])
                saved["pdf"] = {"included": bool(pdf), "bytes": len(pdf or b""), "note": "" if pdf else pdf_skip_line(reason),
                                "size_note": reason if pdf else ""}
                saved.update(draft_path=str(target), status="draft")
                await persist_request(self.hub, rid)
            if saved.get("mode") == "local":
                saved["status"] = "local"
                return
            review_required = self.s.github.auto_publish_results or getattr(
                self.hub, "result_review_required", False)
            if req.get("revised_to") or (review_required and not req.get("pi_reviewed_at")):
                return  # review_done schedules this saved draft again.
            if not self.s.github.auto_publish_results or saved.get("rebuild"):  # a rebuilt edition always asks (#754)
                previews, omitted = publish_image_previews(images, target)
                detail = {"documents": docs, "repo": saved.get("repo"),
                          "korean_qc": saved.get("korean_qc"),
                          "images": previews,
                          "visibility": saved.get("visibility"), "local_draft": str(target)}
                if omitted:
                    detail["image_preview_omitted"] = omitted
                pdf_line = (f"포함: {_PDF_NAME} ({len(pdf) / 1024 / 1024:.1f} MB)"
                            + (f" · {(saved.get('pdf') or {}).get('size_note')}" if (saved.get("pdf") or {}).get("size_note") else "")
                            if pdf else str((saved.get("pdf") or {}).get("note") or ""))
                if pdf_line:
                    detail["pdf"] = pdf_line
                decision = await self.hub.request_approval(kind="result_publish", request_id=rid,
                    summary=f"편집한 결과를 {saved.get('repo')}에 올릴까요?", detail=detail)
                saved = req["result_storage"]
                if not decision.get("approved"):
                    saved["status"] = "draft" if decision.get("state") in {"timed_out", "expired"} else "declined"
                    return
            self.uploading.add(rid)  # from here a redraft is refused: some files may already be in the repository
            try:
                await self.upload(rid, docs, images, pdf)
            finally:
                self.uploading.discard(rid)
            saved["status"] = "published"
            report_path = ("report/report.md" if saved.get("mode") == "new"
                           else f"labhq/results/{rid}/report/report.md")
            saved["report_url"] = (f"https://github.com/{saved['repo']}/blob/{saved.get('branch') or 'main'}/"
                                   f"{report_path}")
            if hasattr(self.hub, "rounds"):
                try:
                    await self.hub.rounds.awrite(rid)
                    await self.hub.rounds.asubmit(rid)
                except Exception as exc:
                    saved["round_warning"] = (
                        f"게시를 마쳤지만 운전 기록을 갱신하지 못했습니다({type(exc).__name__}).")
        except ShutdownInterrupted as exc:
            saved = req["result_storage"]
            saved.update(status="interrupted", shutdown_evidence=exc.evidence,
                         warning="runner 종료로 편집이 중단됐습니다. 편집자 runner가 연결되면 다시 준비합니다.")
            # A replacement may already be connected when its predecessor's result arrives.
            if self._editor_connection() is not editor_connection:
                self.resume_interrupted()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if receipt := getattr(exc, "reproduction_receipt", None):
                req["reproduction_receipt"] = receipt
                saved["reproduction_receipt"] = receipt_summary(receipt)
            saved["error_type"] = type(exc).__name__  # the warning never quotes the exception, only the texts above
            # A check that stopped the edition says which check and which files; before #713 it read as a token problem.
            saved.update(status="failed", warning=str(exc) if isinstance(exc, (PublishAccessError, EditionCheckError))
                         else upload_failed_text(rid))
        finally:
            await persist_request(self.hub, rid)
            await self.hub.publish({"type": "request.storage", "request_id": rid,
                                    "data": {"result_storage": saved}})

    async def edition_pdf(self, rid, req, *, report_text: str | None = None,
                          source: Path | None = None) -> tuple[bytes | None, str]:
        """``(report.pdf bytes, size note)``, or ``(None, plain Korean reason)``.

        The report is printed from the request bundle's original figures, whatever their size, under the edition's cleaned
        text (#608, #690). Figure copies and the PDF are not size-limited or re-encoded. A configured
        ``github.edition_pdf_max_mb`` value produces a warning only."""
        bundle = source or (self.s.path(self.s.runner.workspace_root) / "requests" / rid)
        text = clean_document(report_text, self.s, req) if report_text is not None else pdf_report_text(req, self.s)
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
        return built.data, " ".join(size_notes(built.data, pdf_cap_bytes(self.s)))

    async def upload(self, rid, docs, images=None, pdf=None):
        if not self.hub.requests[rid].get("pi_reviewed_at"):
            raise ValueError("보고서 검토 완료 뒤 게시해 주세요.")
        saved = self.hub.requests[rid]["result_storage"]
        gh = self.client()
        if gh is None:
            raise ValueError("GitHub unavailable")
        repo = saved["repo"]

        def denied(exc, text):
            """A permission refusal of the token becomes the plain-Korean reason (#607); any other error is left to the caller."""
            if _denial(exc):
                raise PublishAccessError(text) from exc

        try:
            info = await gh.repo_info(repo)
        except GitHubHTTPError as exc:
            if exc.status_code != 404 or saved["mode"] != "new":
                denied(exc, write_denied_text(repo, rid))
                raise
            try:
                info = await gh._req("POST", "/user/repos", json={"name": repo.split("/")[1],
                           "private": saved.get("visibility") != "public", "auto_init": True})
            except GitHubHTTPError as collision:
                if collision.status_code == 403:
                    denied(collision, create_denied_text(repo, rid, saved.get("visibility")))
                if collision.status_code != 422:
                    raise
                try:
                    info = await gh.repo_info(repo)
                except GitHubHTTPError as again:
                    denied(again, write_denied_text(repo, rid))
                    raise
        if not info.get("private", False) and not saved.get("allow_public"):
            raise ValueError("public publishing not approved")
        default_branch = info.get("default_branch") or "main"
        branch = saved.get("branch") or default_branch
        try:
            empty = await gh.repository_is_empty(repo, default_branch)
            if empty:
                branch = default_branch
            saved["branch"] = branch
            write_branch = None if empty else branch
            request = self.hub.requests[rid]
            internal = request_uses_internal_data(request, self.s)
            scope_mode = saved.get("internal_publication_scope")
            internal_files = set(saved.get("internal_files") or [])

            def file_internal(name: str) -> bool:
                if scope_mode == "step":
                    return name in internal_files
                return internal

            aggregate_tables = declared_aggregate_tables(self.hub.requests[rid])
            aggregate_tables.update(saved.get("internal_allowed_files") or [])
            aggregate_bodies = {
                body for name, body in docs.items()
                if name in aggregate_tables
                and table_allowed(name, body, internal=file_internal(name), declared=aggregate_tables)
            }
            aggregate_tables.update(
                name for name, body in docs.items()
                if name.startswith("tables/") and body in aggregate_bodies
            )
            compatibility_docs = {name: body for name, body in docs.items()
                                  if publish_file_allowed(name, body, internal=file_internal(name),
                                                          declared=aggregate_tables)}
            compatibility_images = {name: body for name, body in (images or {}).items()
                                    if publish_file_allowed(name, body, internal=file_internal(name),
                                                            declared=aggregate_tables)}
            payloads = []
            if saved.get("mode") == "new":
                payloads.append(("",
                                 {name: body for name, body in compatibility_docs.items() if portfolio_root_file(name)},
                                 {name: body for name, body in compatibility_images.items() if portfolio_root_file(name)},
                                 (_PORTFOLIO_PDF_NAME,) if pdf else ()))
            else:
                payloads.append((f"labhq/results/{rid}/", compatibility_docs, compatibility_images,
                                 (_PDF_NAME, _PORTFOLIO_PDF_NAME) if pdf else ()))
            for base, payload_docs, payload_images, pdf_names in payloads:
                for name, body in payload_docs.items():
                    await gh.put_file(repo, base + name, body, "연구 결과 정리", write_branch)
                    write_branch = branch
                for name, body in payload_images.items():
                    payload = (clean_svg_for_publish(body, self.s, self.hub.requests[rid])
                               if Path(name).suffix.lower() == ".svg" else body)
                    await gh.put_image(repo, base + name, payload, write_branch)
                    write_branch = branch
                if pdf:
                    for name in pdf_names:
                        await gh.put_binary(repo, base + name, pdf, write_branch, "연구 결과 PDF")
                        write_branch = branch
        except (GitHubHTTPError, httpx.HTTPStatusError) as exc:
            denied(exc, write_denied_text(repo, rid))
            raise

    async def close(self):
        for task in [*self.tasks.values(), *self.end_tasks.values()]:
            task.cancel()
        pending = [*self.tasks.values(), *self.end_tasks.values()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._client:
            await self._client.http.aclose()
