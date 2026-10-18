"""Build and check the publication-oriented repository edition (#686)."""
from __future__ import annotations

import collections
import csv
import io
import json
import os
import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import fields
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote

from .integrations.github import SECRET_PATTERNS, publish_clean, redact_url_credentials, sanitize
from .korean_qc import check_document, check_preservation
from .models import RunnerPathFields
from .policy import touches
from .research.contract import branch_plan_topology
from .report_abstract import build_graphical_abstract
from .request_bundle import _POSIX_ABS, _WINDOWS_ABS


# The one private-path rule of the edition (result_storage uses it too). `./data/x` and `../data/x` are relative paths:
# masking them broke the published scripts (PR #717 review).
_PRIVATE_PATH = re.compile(
    r"(?<![\w:.-])(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/)[^\s<>\"'`\])]+"
)
PRIVATE_PATH_PATTERN = _PRIVATE_PATH
_ASSIGNMENT = re.compile(
    r"(?im)\b((?:api[_-]?key|token|password|secret)\s*[:=]\s*)"
    r"(?!<redacted-secret>|&lt;redacted-secret&gt;|\[REDACTED\])[^\s,;<]+"
)
# A root folder counts only where a path starts: `inputs/om_fetch/data/x.tsv` is a relative path, and catching its
# `/data/` stopped the whole 10-17 edition over three analysis scripts (#713).
_LOCAL_SCAN = re.compile(
    r"(?i)(?:[A-Za-z]:[/\\](?:Users|Documents and Settings|AppData|ProgramData)[/\\]|"
    r"(?<![\w.-])/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/|\.labhq[/\\]|C--Users)"
)


class EditionCheckError(ValueError):
    """A check stopped the edition before anything was written: a broken link or a publication scan. The message names
    the check and the edition files, never a local path or a value, so it can be shown as the storage warning."""


def check_failure(issues: list[dict[str, str]]) -> EditionCheckError:
    kinds = ", ".join(sorted({issue["kind"] for issue in issues}))
    names = sorted({issue["path"] for issue in issues})
    shown = ", ".join(names[:5]) + (f" 외 {len(names) - 5}개" if len(names) > 5 else "")
    return EditionCheckError(f"게시 전 검사에 실패했습니다: {kinds} — {shown}")
_SIGNED_URL = re.compile(r"[?&](?:X-Amz-[^=&#\s]+|X-Goog-[^=&#\s]+|Signature|token|sig)=[^&#\s]+", re.I)
_DOI = re.compile(r"(?i)\b10\.\d{4,9}/[-._;()/:A-Z0-9]+")
_PMID = re.compile(r"(?i)\bPMID\s*[:#]?\s*(\d{5,9})\b")
_GEO = re.compile(r"\b(?:GSE|GSM|GPL)\d+\b", re.I)
_URL = re.compile(r"https?://[^\s<>\"'\])]+")
_PAPER_SECTIONS = ("요약", "서론", "방법", "결과", "논의", "결론")
_LITERATURE_OUTPUT = re.compile(r"(?:^|/)outputs/literature(?:/|$)", re.I)
_TABLE_SUFFIXES = {".tsv", ".csv", ".xlsx", ".xls"}
_AGGREGATE_TEXT_SUFFIXES = {".tsv", ".csv", ".json", ".txt", ".html"}
_INTERNAL_DEFAULT_SUFFIXES = {".md", ".svg", ".py", ".r", ".sh"}
_CODE_SUFFIXES = {".py", ".r", ".sh"}
_NEXTFLOW_NAMES = {"report.html", "timeline.html", "trace.txt"}
_RUNNER_EXECUTION_LOCATION_FIELDS = frozenset(
    {field.name for field in fields(RunnerPathFields)}
    & {
        "workdir", "source_workdir", "cwd", "workspace_root", "project_dirs", "plugin_dirs",
        "reference_roots", "local_dir", "skill_dir", "talent_dir", "mcp_path", "mcp_entry_path",
        "read_only_roots",
    }
)
_RESULT_INPUT_PROVENANCE_FIELDS = frozenset({
    "reference_dirs", "upstream_dirs", "upstream_steps", "references", "input_refs", "input_files",
    "input_records",
})
_RESULT_INPUT_PROVENANCE_CONTAINERS = frozenset({"meta", "provenance", "runs"})


def _balanced_link_target(depth: int = 4) -> str:
    inner = r"[^()\s]*"
    for _ in range(depth - 1):
        inner = rf"(?:[^()\s]|\({inner}\))*"
    return rf"(?:[^()\s]|\({inner}\))+"


_MARKDOWN_LINK = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\]\n]*)\]\(\s*(?P<target><[^>\n]*>|" + _balanced_link_target()
    + r")(?P<title>\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
_MARKDOWN_REFERENCE_DEFINITION = re.compile(
    r"(?m)^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*(?P<target><[^>\n]*>|\S+)"
    r"(?P<title>[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$")
_MARKDOWN_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.DOTALL)
_REMOTE_LINK = re.compile(r"(?i)^(?:https?://|data:|mailto:)")
_PORTFOLIO_TOP = {"README.md", "REFERENCES.md", "DATA_AND_LICENSES.md"}
_PORTFOLIO_FOLDERS = ("report/", "run/", "code/", "docs/", "figures/", "tables/", "studies/")
_LICENSE_PLACEHOLDER = re.compile(
    r"<reference-path>|확인 필요|필수 문구 자리|원 출처의 필수 문구를 넣으세요", re.I
)


def _declared_outputs(req: Mapping[str, Any], expected_type: str) -> set[str]:
    declared: set[str] = set()

    def add(step_id: str, name: Any, data_type: Any) -> None:
        if isinstance(data_type, Mapping):
            data_type = data_type.get("value") if data_type.get("basis") == "declared" else None
        if not isinstance(name, str) or data_type != expected_type:
            return
        raw = name.replace("\\", "/")
        normalized = posixpath.normpath(raw[2:] if raw.startswith("./") else raw)
        if normalized in {"", ".", ".."} or normalized.startswith(("../", "/", "steps/")):
            return
        if not normalized.startswith("outputs/"):
            normalized = f"outputs/{normalized}"
        declared.add(f"steps/{step_id}/{normalized}")

    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    for step in plan.get("steps") or []:
        if not isinstance(step, Mapping) or not isinstance(step.get("id"), str):
            continue
        for entry in step.get("output_types") or []:
            if isinstance(entry, Mapping):
                add(step["id"], entry.get("name"), entry.get("data_type"))
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    for step_id, result in results.items():
        if not isinstance(step_id, str) or not isinstance(result, Mapping):
            continue
        output_types = result.get("output_types")
        if isinstance(output_types, Mapping):
            for name, entry in output_types.items():
                if isinstance(entry, Mapping):
                    add(step_id, name, entry.get("data_type"))
    return declared


def declared_aggregate_tables(req: Mapping[str, Any]) -> set[str]:
    """Return tables explicitly declared with the aggregate ``table`` output type."""
    return _declared_outputs(req, "table")


def publish_file_allowed(name: str, body: str | bytes, *, internal: bool,
                         declared: set[str] | None = None) -> bool:
    """Keep public outputs; internal outputs follow a small publication allowlist."""
    path = PurePosixPath(name)
    suffix = path.suffix.lower()
    if not internal or suffix in _INTERNAL_DEFAULT_SUFFIXES:
        return True
    if path.as_posix() not in (declared or set()) or suffix not in _AGGREGATE_TEXT_SUFFIXES:
        return False
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    return not small_cells(text, suffix)


def table_allowed(name: str, body: str | bytes, *, internal: bool,
                  declared: set[str] | None = None) -> bool:
    """Compatibility name for the shared publication allowlist."""
    return publish_file_allowed(name, body, internal=internal, declared=declared)


def portfolio_root_file(name: str) -> bool:
    """Whether a builder-selected layout path belongs at a new repository's root."""
    return name in _PORTFOLIO_TOP or name.startswith(_PORTFOLIO_FOLDERS)


def _markdown_targets(text: str):
    """Yield inline and reference-definition targets through one parser."""
    masked = _MARKDOWN_CODE.sub(lambda match: " " * len(match.group()), text)
    matches = [*_MARKDOWN_LINK.finditer(masked), *_MARKDOWN_REFERENCE_DEFINITION.finditer(masked)]
    return sorted(matches, key=lambda match: match.start("target"))


def _mapping_key(value: str) -> str | None:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized in {"", ".", ".."} or normalized.startswith(("../", "/")):
        return None
    return normalized


def _local_link(document: str, target: str,
                source_targets: Mapping[str, str]) -> tuple[str | None, str | None, str]:
    raw = target[1:-1].strip() if target.startswith("<") and target.endswith(">") else target
    if not raw or raw.startswith("#") or _REMOTE_LINK.match(raw):
        return None, None, ""
    cut = min((index for marker in ("?", "#") if (index := raw.find(marker)) >= 0), default=len(raw))
    suffix = raw[cut:]
    path = unquote(raw[:cut]).replace("\\", "/")
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        return path, None, suffix
    relative = _mapping_key(posixpath.join(posixpath.dirname(document), path))
    root = _mapping_key(path)
    for key in (relative, root):
        if key is not None and key in source_targets:
            return path, source_targets[key], suffix
    return path, None, suffix


def _rewrite_markdown_links(files: Mapping[str, str | bytes],
                            source_targets: Mapping[str, str]) -> dict[str, str | bytes]:
    rewritten = dict(files)
    for document, body in files.items():
        if not document.lower().endswith(".md") or not isinstance(body, str):
            continue
        pieces: list[str] = []
        last = 0
        for match in _markdown_targets(body):
            _path, resolved, suffix = _local_link(document, match.group("target"), source_targets)
            if resolved is None:
                continue
            relative = posixpath.relpath(resolved, posixpath.dirname(document) or ".")
            encoded = quote(relative, safe="/") + suffix
            start, end = match.span("target")
            if body[start:end] != encoded:
                pieces.extend((body[last:start], encoded))
                last = end
        if pieces:
            pieces.append(body[last:])
            rewritten[document] = "".join(pieces)
    return rewritten


def broken_markdown_links(files: Mapping[str, str | bytes],
                          source_targets: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """List unresolved relative links in every Markdown document in an edition."""
    names = set(files)
    mapping = dict(source_targets or {})
    mapping.update({name: name for name in names})
    broken: list[dict[str, str]] = []
    for document in sorted(names):
        body = files[document]
        if not document.lower().endswith(".md") or not isinstance(body, str):
            continue
        for match in _markdown_targets(body):
            path, resolved, _suffix = _local_link(document, match.group("target"), mapping)
            if path is not None and resolved is None:
                broken.append({"document": document, "target": path})
    return broken


def _clean(text: str, settings, req: dict) -> str:
    text = sanitize(text, settings.policy, [os.environ.get(settings.github.token_env, "")], limit=None)
    text = publish_clean(text, settings, [req], limit=None)
    text = _WINDOWS_ABS.sub("<local-path>", text)
    text = _POSIX_ABS.sub("<local-path>", text)
    return _ASSIGNMENT.sub(r"\1<redacted-secret>", _PRIVATE_PATH.sub("<local-path>", text))


def _outside_literals(text: str, transform) -> str:
    pattern = re.compile(r'`[^`\n]*`|"[^"\n]*"|“[^”\n]*”')
    out, last = [], 0
    for match in pattern.finditer(text):
        out.extend((transform(text[last:match.start()]), match.group(0)))
        last = match.end()
    out.append(transform(text[last:]))
    return "".join(out)


def _plain_korean(text: str) -> str:
    def replace(value: str) -> str:
        replacements = (
            (r"비유의였다", "통계적으로 유의하지 않았다"),
            (r"비유의(?:적)?", "통계적으로 유의하지 않음"),
            (r"유의차", "통계적으로 유의한 차이"),
            (r"(?:미|무|비)보정", "보정 전"),
            (r"에 있어서", "에서"),
            (r"되어진다", "된다"),
            (r"되어진", "된"),
            (r"되어지", "되"),
            (r"가지고 있다", "있다"),
            (r"가지고 있", "있"),
        )
        for pattern, replacement in replacements:
            value = re.sub(pattern, replacement, value)
        return value

    rewritten = _outside_literals(text, replace)
    guard = check_preservation(text, rewritten)
    return rewritten if guard["accepted"] else text


def _paper_like(text: str) -> bool:
    headings = [line.lstrip("#").strip().casefold() for line in text.splitlines() if line.startswith("## ")]
    return text.lstrip().startswith("# ") and all(any(section in heading for heading in headings)
                                                   for section in _PAPER_SECTIONS)


def _latest_final_synthesis_ids(plan: Mapping[str, Any]) -> tuple[list[str], set[str]]:
    branches = [branch for branch in plan.get("branches") or [] if isinstance(branch, Mapping)]
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    if not branches:
        return [], set()
    topology = branch_plan_topology(branches, steps)
    ordered_final_ids = [str(step_id) for step_id in topology["final_synthesis_ids"]]
    final_ids = set(ordered_final_ids)
    synthesis_ids = set(topology["synthesis_ids"])
    dependencies = {str(step.get("id")): [str(value) for value in step.get("depends_on") or []]
                    for step in steps if step.get("id")}

    def ancestors(step_id: str) -> set[str]:
        found: set[str] = set()
        pending = list(dependencies.get(step_id, []))
        while pending:
            dependency = pending.pop()
            if dependency in found:
                continue
            found.add(dependency)
            pending.extend(dependencies.get(dependency, []))
        return found

    superseded = {step_id for other_id in final_ids
                  for step_id in final_ids.intersection(ancestors(other_id))}
    return [step_id for step_id in ordered_final_ids if step_id not in superseded], synthesis_ids


def _paper_candidate(req: dict, assets: Mapping[str, str]) -> str:
    report = str(req.get("report") or "").strip()
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    final_synthesis_ids, synthesis_ids = _latest_final_synthesis_ids(plan)
    step_branches = {str(step.get("id")): step.get("branch") for step in plan.get("steps") or []
                     if isinstance(step, Mapping) and step.get("id")}
    candidates: list[tuple[int, str, str | None]] = []
    for name, body in assets.items():
        if not name.lower().endswith(".md") or not _paper_like(body):
            continue
        lowered = name.lower()
        source_step = _source_step(name, req)
        score = 10
        if "final_report" in lowered or "science_report" in lowered or "report_ko" in lowered:
            score += 20
        if "/report/" in lowered:
            score += 10
        if step_branches.get(source_step or "") == "synthesis":
            score += 100
        candidates.append((score, body.strip(), source_step))
    review = req.get("review") or (req.get("review_progress") or {}).get("review") or {}
    review_ended = (req.get("outcome") == "research_review_revise"
                    or str(review.get("verdict") or "").casefold() == "revise")
    if review_ended or not _paper_like(report):
        final_order = {step_id: index for index, step_id in enumerate(final_synthesis_ids)}
        final_candidates = [row for row in candidates if row[2] in final_order]
        if final_candidates:
            return max(final_candidates, key=lambda row: (final_order[row[2]], row[0]))[1]
        if synthesis_ids:
            candidates = [row for row in candidates if row[2] not in synthesis_ids]
        if candidates:
            return max(candidates, key=lambda row: row[0])[1]
    if _paper_like(report):
        return report
    title = str(req.get("text") or "연구 결과").strip().splitlines()[0][:120] or "연구 결과"
    answer = next((p.strip() for p in report.split("\n\n") if p.strip() and not p.startswith("#")),
                  "확인된 결과가 없습니다.")
    steps = "\n".join(f"- {step.get('id')}: {step.get('instruction', '')}"
                       for step in (req.get("plan") or {}).get("steps") or []) or "- 기록된 분석 단계가 없습니다."
    return (f"# {title}\n\n## 요약(Abstract)\n\n{answer}\n\n## 서론\n\n요청한 질문의 근거를 확인했다.\n\n"
            f"## 방법\n\n{steps}\n\n## 결과\n\n{report or '보고된 결과가 없습니다.'}\n\n"
            "## 논의\n\n해석은 기록된 결과와 한계의 범위를 넘지 않는다.\n\n"
            f"## 결론\n\n{answer}\n\n## 참고\n\nREFERENCES.md를 참조한다.\n\n"
            "## 부록\n\n실행 기록은 run/execution_record.md에 있다.\n")


_HISTORY_SECTION = re.compile(r"^(?:정정|수정 이력|이전 차수에서 고친 것|리뷰 참고)$", re.I)


def _split_revision_history(report: str) -> tuple[str, list[str]]:
    """Move publication-process history out of the scientific manuscript without changing the request record."""
    lines = report.splitlines(keepends=True)
    kept: list[str] = []
    moved: list[str] = []
    index = 0
    while index < len(lines):
        heading = re.match(r"^(#{2,6})\s+(.+?)\s*$", lines[index].rstrip("\r\n"))
        if not heading or not _HISTORY_SECTION.fullmatch(heading.group(2).strip()):
            kept.append(lines[index])
            index += 1
            continue
        level = len(heading.group(1))
        block = [lines[index]]
        index += 1
        while index < len(lines):
            following = re.match(r"^(#{1,6})\s+", lines[index])
            if following and len(following.group(1)) <= level:
                break
            block.append(lines[index])
            index += 1
        moved.append("".join(block).strip())
    return "".join(kept).strip() + "\n", moved


_ANCHOR = re.compile(r"\s*\[\[\s*claim\s*:([^\[\]\n]*)\]\]", re.I)


def _claim_note_id(step_id: str, claim_id: str, ordinal: int) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", f"{step_id}-{claim_id}").strip("-").casefold()
    return f"claim-{slug or ordinal}"


def _claim_notes(report: str) -> dict[str, tuple[str, str]]:
    """Assign stable, collision-free IDs to the unique claim anchors in a report."""
    notes: dict[str, tuple[str, str]] = {}
    claims: set[tuple[str, str]] = set()
    for raw in _ANCHOR.findall(report):
        step_id, separator, claim_id = (part.strip() for part in raw.strip().rpartition("/"))
        if not separator:
            step_id, claim_id = "미확인", raw.strip() or "미확인"
        claim = (step_id, claim_id)
        if claim in claims:
            continue
        claims.add(claim)
        base = _claim_note_id(step_id, claim_id, len(notes) + 1)
        note_id = base
        suffix = 2
        while note_id in notes:
            note_id = f"{base}-{suffix}"
            suffix += 1
        notes[note_id] = claim
    return notes


def _publication_claim_footnotes(report: str) -> str:
    """Replace validation anchors in the publication copy with readable evidence footnotes."""
    notes = _claim_notes(report)
    note_ids = {claim: note_id for note_id, claim in notes.items()}

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        step_id, separator, claim_id = (part.strip() for part in raw.rpartition("/"))
        if not separator:
            step_id, claim_id = "미확인", raw or "미확인"
        return f"[^{note_ids[(step_id, claim_id)]}]"

    published = _ANCHOR.sub(replace, report).rstrip()
    if not notes:
        return published + "\n"
    definitions = [
        f"[^{note_id}]: 단계 `{step_id}` · claim `{claim_id}` · "
        f"[근거 파일](../docs/reference/claim_evidence.md#{note_id})"
        for note_id, (step_id, claim_id) in notes.items()
    ]
    return published + "\n\n" + "\n".join(definitions) + "\n"


def _review_history(req: Mapping[str, Any]) -> str:
    review = req.get("review") or (req.get("review_progress") or {}).get("review") or {}
    if str(review.get("verdict") or "").casefold() != "revise" or not review.get("issues"):
        return ""
    lines = ["## 검토 종료 때 남은 정정", "", "다음 차수 없이 종료된 리뷰 지적입니다.", ""]
    for issue in review["issues"]:
        lines.append(f"- {issue.get('problem') or '지적'} → {issue.get('request') or '수정 요청'}")
    return "\n".join(lines).strip()


def _portfolio_report_parts(req: dict, assets: Mapping[str, str]) -> tuple[str, str]:
    report, moved = _split_revision_history(_paper_candidate(req, assets))
    review = _review_history(req)
    history = "\n\n".join([*moved, *([review] if review else [])]).strip()
    return _publication_claim_footnotes(report), history


def portfolio_report(req: dict, settings, assets: Mapping[str, str]) -> str:
    """Return the publication manuscript; the validated request report itself is never changed."""
    report, _history = _portfolio_report_parts(req, assets)
    return _plain_korean(_clean(report, settings, req))


def portfolio_revision_history(req: dict, settings, assets: Mapping[str, str]) -> str:
    """Return review and correction history for ``run/`` instead of the scientific manuscript."""
    _report, history = _portfolio_report_parts(req, assets)
    if not history:
        return ""
    return _plain_korean(_clean("# 수정 이력\n\n" + history + "\n", settings, req))


_VERDICT = re.compile(r"지지(?:되지|하지)\s*않|지지(?:됐|했|된다|한다|합니다|됩니다)|기각")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CONNECTIVE = re.compile(r"^(?:그래서|따라서|그러므로|결국|즉)\s*,?\s*")


def _claim_statements(result: Any) -> list[str]:
    """The claim statements of a research step's ledger. Its `text` is the ledger JSON itself, which the 10-17 README
    printed into the branch table (#718)."""
    if not isinstance(result, Mapping):
        return []
    ledger = result.get("structured")
    if not isinstance(ledger, Mapping):
        try:
            ledger = json.loads(str(result.get("text") or ""))
        except (json.JSONDecodeError, TypeError):
            return []
    claims = ledger.get("claims") if isinstance(ledger, Mapping) else None
    return [str(claim["statement"]) for claim in claims or []
            if isinstance(claim, Mapping) and str(claim.get("statement") or "").strip()]


def _claim_ledger(result: Any) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    ledger = result.get("structured")
    if isinstance(ledger, Mapping):
        return ledger
    try:
        parsed = json.loads(str(result.get("text") or ""))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _is_ledger(text: str) -> bool:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(value, Mapping) and ("claims" in value or "schema_version" in value)


def _result_text(result: Any) -> str:
    statements = _claim_statements(result)
    if statements:
        return statements[0]
    text = str(result.get("text") or "") if isinstance(result, Mapping) else ""
    # Only a research ledger is hidden: an ordinary step may answer with a JSON document (PR #719 review).
    return "" if _is_ledger(text) else text


def _first_answer(report: str, req: Mapping[str, Any] | None = None) -> str:
    """The README's one-line answer: the first verdict sentence ("…지지되지 않습니다") of the abstract or the synthesis
    claims, else the abstract's first paragraph. The 10-17 abstract opened with "세 근거로 살폈습니다", which is not an
    answer (#718)."""
    abstract: list[str] = []
    in_abstract = False
    for block in report.split("\n\n"):
        value = block.strip()
        if value.startswith("## "):
            in_abstract = "요약" in value or "abstract" in value.casefold()
            continue
        if in_abstract and value and not value.startswith(("#", "![", "|")):
            abstract.append(" ".join(_ANCHOR.sub("", value).split()))
    synthesis = []
    if isinstance(req, Mapping):
        results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
        for step in (req.get("plan") or {}).get("steps") or []:
            if isinstance(step, Mapping) and step.get("branch") == "synthesis":
                synthesis += [" ".join(_ANCHOR.sub("", text).split())
                              for text in _claim_statements(results.get(str(step.get("id"))))]
    for paragraph in [*abstract, *synthesis]:
        for sentence in _SENTENCE_END.split(paragraph):  # not after 다: "단태아보다 여아" is one sentence
            if _VERDICT.search(sentence):
                return _CONNECTIVE.sub("", sentence.strip())
    return abstract[0] if abstract else "결과와 한계는 과학 보고서에 정리했다."


def _claim_summary(result: Any) -> str:
    """A branch row's result: the first two sentences of its first claim, which is already one short finding."""
    statements = _claim_statements(result)
    if not statements:
        return _result_sentence(_result_text(result))
    return " ".join(_SENTENCE_END.split(" ".join(statements[0].split()))[:2])[:380]


def _result_sentence(text: str) -> str:
    compact = " ".join(str(text or "").split())
    sentences = re.split(r"(?<=[.!?다요])\s+", compact)
    chosen = [sentence for sentence in sentences if re.search(
        r"\d|유의하지|통계적으로 유의|FDR|p\s*[<=>]|신뢰구간|confidence interval", sentence, re.I)]
    result = " ".join(chosen[:2] or sentences[:1])[:380] or "결과 요약은 보고서를 참조한다."
    if (re.search(r"(?:통과|유의[^ ]* 항목)[^.!?]{0,30}\b0개", result)
            and "유의하지" not in result):
        result += " 통계적으로 유의하지 않은 결과다."
    return result


def _operational_result(text: str) -> bool:
    """Keep formatting and file-maintenance details out of the README's scientific result rows."""
    return bool(re.search(
        r"(?:탭 문자|실제 탭|머리줄|칸 수|열 수|파일명|파일을 (?:만들|고침)|형식(?:을|이) (?:교정|수정))",
        text, re.I,
    ))


def _step_rows(req: dict, assets: Mapping[str, str]) -> list[tuple[str, str, str]]:
    rows = []
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    for step in (req.get("plan") or {}).get("steps") or []:
        sid = str(step.get("id") or "단계")
        result = results.get(sid) if isinstance(results.get(sid), Mapping) else {}
        outputs = list(result.get("outputs") or [])
        if not outputs:
            prefix = f"steps/{sid}/outputs/"
            outputs = [name.removeprefix(prefix) for name in assets if name.startswith(prefix)][:4]
        data = ", ".join(PurePosixPath(str(name)).name for name in outputs[:4]) or "보고서 기록"
        text = _result_text(result)
        if not text and "report" in sid.casefold():
            text = str(req.get("report") or "")
        rows.append((sid, data, _result_sentence(text)))
    return rows or [("종합", "요청에 기록된 자료", _result_sentence(str(req.get("report") or "")))]


def _branch_slug(branch: Mapping[str, Any]) -> str:
    label = str(branch.get("evidence_type") or branch.get("question") or "study").strip().casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "-", label).strip("-")[:32] or "study"


def _branch_layout(req: dict) -> tuple[list[dict[str, Any]], dict[str, str]]:
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    branches = [branch for branch in plan.get("branches") or [] if isinstance(branch, Mapping)]
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    if not branches:
        return [], {}
    topology = branch_plan_topology(branches, steps)
    folders = {str(branch["id"]): f"{branch['id']}-{_branch_slug(branch)}" for branch in branches}
    specs = [{**branch, "folder": folders[str(branch["id"])],
              "terminals": topology["terminals"].get(branch["id"], [])}
             for branch in branches]
    step_folders = {str(step_id): folders[str(branch_id)]
                    for step_id, branch_id in topology["branch_by_step"].items()
                    if branch_id in folders}
    return specs, step_folders


def _source_step(name: str, req: dict) -> str | None:
    normalized = posixpath.normpath(name.replace("\\", "/"))
    parts = PurePosixPath(normalized).parts
    if len(parts) > 2 and parts[0] == "steps":
        return parts[1]
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    for step_id, result in results.items():
        if not isinstance(result, Mapping):
            continue
        workdir = result.get("workdir") or result.get("workdir_id")
        if not isinstance(workdir, str):
            continue
        prefix = posixpath.normpath(workdir.replace("\\", "/")).rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return str(step_id)
    return None


def _add_workdir_source_targets(req: Mapping[str, Any], source_targets: dict[str, str]) -> None:
    """Add workdir paths as aliases for their published step outputs."""
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    aliases: dict[str, str] = {}
    for step_id, result in results.items():
        if not isinstance(result, Mapping):
            continue
        workdir_value = result.get("workdir") or result.get("workdir_id")
        if not isinstance(workdir_value, str):
            continue
        prefix = f"steps/{step_id}/outputs/"
        workdir = posixpath.normpath(workdir_value.replace("\\", "/")).rstrip("/")
        for source, target in source_targets.items():
            normalized = posixpath.normpath(source.replace("\\", "/"))
            if normalized.startswith(prefix):
                aliases[posixpath.join(workdir, "outputs", normalized[len(prefix):])] = target
    for source, target in aliases.items():
        source_targets.setdefault(source, target)


def _branch_rows(req: dict, specs: list[dict[str, Any]],
                 step_folders: Mapping[str, str]) -> list[dict[str, str]]:
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    folder_steps: dict[str, list[str]] = collections.defaultdict(list)
    for step_id, folder in step_folders.items():
        folder_steps[folder].append(step_id)
    rows = []
    for branch in specs:
        folder = str(branch["folder"])
        step_ids = folder_steps[folder]
        recorded = [(step_id, results.get(step_id)) for step_id in step_ids
                    if isinstance(results.get(step_id), Mapping)]
        terminal_records = [(step_id, results.get(step_id)) for step_id in branch["terminals"]]
        completed = [(step_id, result) for step_id, result in terminal_records
                     if isinstance(result, Mapping) and result.get("ok") is not False]
        failures = [
            (step_id, str(result.get("error") or _result_text(result) or "실패 이유 없음"))
            if isinstance(result, Mapping) else (step_id, "terminal 결과 없음")
            for step_id, result in terminal_records
            if not isinstance(result, Mapping) or result.get("ok") is False
        ]
        if not terminal_records:
            completed = [(step_id, result) for step_id, result in recorded
                         if result.get("ok") is not False]
            if not completed:
                failures = [("terminal", "terminal 결과 없음")]
        summaries = [_claim_summary(result) for _step_id, result in completed]
        scientific = [summary for summary in summaries if not _operational_result(summary)]
        if scientific:
            summaries = scientific
        if completed and failures:
            status = "부분"
            failed_text = "; ".join(f"{step_id}: {reason}" for step_id, reason in failures)
            result_text = " / ".join(summaries + [failed_text])
        elif completed:
            status = "완료"
            result_text = " / ".join(summaries)
        else:
            status = "실패"
            result_text = (failures[0][1] if len(failures) == 1 else
                           "; ".join(f"{step_id}: {reason}" for step_id, reason in failures))
        rows.append({
            "id": str(branch.get("id") or "갈래"),
            "question": str(branch.get("question") or "질문 기록 없음"),
            "data": f"{branch.get('data_boundary') or '미기록'} · {branch.get('evidence_type') or '자료 미기록'}",
            "status": status,
            "result": result_text,
            "folder": folder,
        })
    return rows


def _step_flow(step: Mapping[str, Any], *, first: bool, terminal: bool) -> str:
    text = f"{step.get('id', '')} {step.get('instruction', '')}".casefold()
    if re.search(r"(?:^|[_\s-])qc(?:$|[_\s-])|품질|검토|review|check", text):
        return "QC"
    if terminal or re.search(r"보고|report|종합|결론|result", text):
        return "결과"
    if first or re.search(r"입력|자료|manifest|download|verify|source", text):
        return "입력"
    return "방법"


def _markdown_link_from(document: str, target: str, label: str) -> str:
    relative = posixpath.relpath(target, posixpath.dirname(document) or ".")
    return f"[{label}]({quote(relative, safe='/')})"


def _omitted_table_lines(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    lines = []
    for row in rows:
        columns = ", ".join(row.get("columns") or []) or "미확인"
        count = row.get("rows")
        count_text = f"{count}행" if isinstance(count, int) else "행 수 미확인"
        lines.append(
            f"- 뺀 표: {row.get('name') or '미확인'} · 사유: {row.get('reason') or '미확인'} · "
            f"대체 요약: {count_text} · 주요 열: {columns}"
        )
    return lines


def _study_documents(req: Mapping[str, Any], rows: list[dict[str, str]], specs: list[dict[str, Any]],
                     step_folders: Mapping[str, str], source_targets: Mapping[str, str],
                     reference_targets: Mapping[str, list[str]],
                     omitted_tables: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    index = ["# 갈래별 정리본", "", "| 갈래 | 질문 | 자료 | 상태 | 주요 결과 | 폴더 |",
             "|---|---|---|---|---|---|"]
    files: dict[str, str] = {}
    plans = (req.get("plan") or {}).get("steps") if isinstance(req.get("plan"), Mapping) else []
    steps = [step for step in plans or [] if isinstance(step, Mapping)]
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    folder_steps: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for step in steps:
        folder = step_folders.get(str(step.get("id") or ""))
        if folder:
            folder_steps[folder].append(step)
    terminals = {str(spec["folder"]): {str(value) for value in spec.get("terminals") or []} for spec in specs}
    omitted_by_folder: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for omitted in omitted_tables:
        if omitted.get("folder"):
            omitted_by_folder[str(omitted["folder"])].append(omitted)

    for row in rows:
        folder = row["folder"]
        target = f"{folder}/README.md"
        index.append("| {id} | {question} | {data} | {status} | {result} | [열기]({target}) |".format(
            **{key: _safe_cell(value) for key, value in row.items()}, target=target))
        document = f"studies/{folder}/README.md"
        connection = ["## 입력→방법→QC→결과 연결표", "", "| 구간 | 단계 | 직원 | 주요 산출 |",
                      "|---|---|---|---|"]
        for number, step in enumerate(folder_steps[folder]):
            step_id = str(step.get("id") or "미확인")
            result = results.get(step_id) if isinstance(results.get(step_id), Mapping) else {}
            staff = str(step.get("agent_id") or step.get("agent") or result.get("agent_id") or "미확인")
            outputs = []
            for source, output in source_targets.items():
                if _source_step(source, dict(req)) == step_id and output not in outputs:
                    outputs.append(output)
            links = ", ".join(_markdown_link_from(document, output, PurePosixPath(output).name)
                              for output in outputs[:4]) or "기록 없음"
            flow = _step_flow(step, first=number == 0, terminal=step_id in terminals.get(folder, set()))
            connection.append(f"| {flow} | {_safe_cell(step_id)} | {_safe_cell(staff)} | {links} |")
        reference_index = f"docs/reference/{folder}/README.md"
        reference_link = _markdown_link_from(document, reference_index, "상세 근거 문서")
        body = (
            f"# {row['id']} · {row['question']}\n\n"
            f"- 자료: {row['data']}\n- 상태: {row['status']}\n- 근거: {reference_link}\n\n"
            + "\n".join(connection)
            + f"\n\n## 주요 결과\n\n{row['result']}\n"
        )
        omitted = _omitted_table_lines(omitted_by_folder.get(folder, []))
        if omitted:
            body += "\n## 뺀 표\n\n" + "\n".join(omitted) + "\n"
        files[document] = body

        references = reference_targets.get(folder, [])
        reference_lines = [f"# {row['id']} 상세 근거", "", "이 폴더는 lookup-only입니다. 갈래 README에서 필요한 문서만 여세요.", ""]
        reference_lines += [f"- {_markdown_link_from(reference_index, item, PurePosixPath(item).name)}"
                            for item in references] or ["- 기록 없음"]
        files[reference_index] = "\n".join(reference_lines) + "\n"
    files["studies/README.md"] = "\n".join(index) + "\n"
    files["docs/reference/README.md"] = (
        "# 상세 근거 문서\n\n이 폴더는 lookup-only입니다. 단계 보고서와 검토문은 갈래 README에서 찾으세요.\n"
    )
    return files


def _safe_cell(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())


def _unique_target(folder: str, source: str, used: set[str]) -> str:
    base = PurePosixPath(source).name
    target = f"{folder}/{base}"
    if target not in used:
        used.add(target)
        return target
    step = PurePosixPath(source).parts[1] if len(PurePosixPath(source).parts) > 1 else "file"
    target = f"{folder}/{step}-{base}"
    serial = 2
    while target in used:
        target = f"{folder}/{step}-{serial}-{base}"
        serial += 1
    used.add(target)
    return target


def small_cells(text: str, suffix: str) -> list[str]:
    if suffix == ".json":
        try:
            pending = [json.loads(text)]
        except (json.JSONDecodeError, TypeError):
            return []
        found = []
        while pending:
            value = pending.pop()
            if isinstance(value, Mapping):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif re.fullmatch(r"[1-9]", str(value).strip()):
                found.append(str(value).strip())
        return found
    if suffix == ".html":
        return re.findall(r"(?is)<(?:td|th)\b[^>]*>\s*([1-9])\s*</(?:td|th)>", text)
    if suffix == ".txt":
        return [cell.strip() for line in text.splitlines() for cell in re.split(r"[\t,]", line)
                if re.fullmatch(r"[1-9]", cell.strip())]
    try:
        rows = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
        return [cell.strip() for row in rows for cell in row if re.fullmatch(r"[1-9]", cell.strip())]
    except csv.Error:
        return []


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _text_values(item)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _text_values(item)


def _result_input_evidence(result: Any) -> list[Any]:
    """Keep declared/read inputs, not the runner and output locations carried by a result."""
    if not isinstance(result, Mapping):
        return [result]

    evidence: list[Any] = [result.get("text")]

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in _RUNNER_EXECUTION_LOCATION_FIELDS:
                    continue
                if key in _RESULT_INPUT_PROVENANCE_FIELDS:
                    evidence.append(item)
                elif key in _RESULT_INPUT_PROVENANCE_CONTAINERS:
                    collect(item)
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                collect(item)

    collect(result)
    return evidence


def _path_key(value: str) -> str:
    value = value.rstrip(".,;:)]}")
    file_path = re.match(
        r"^(.*\.[A-Za-z0-9]{1,16})(?:에서|으로|입니다|이다|에게|의|와|과|을|를|은|는|이|가|로)$", value)
    if file_path:
        value = file_path.group(1)
    windows_path = ("\\" in value or bool(re.match(r"^[A-Za-z]:[/\\]", value))
                    or bool(re.match(r"^//[^/]+/[^/]+(?:/|$)", value)))
    normalized = posixpath.normpath(value.replace("\\", "/"))
    return normalized.casefold() if windows_path else normalized


def _concrete_internal_paths(value: Any, zone_paths: Iterable[str]) -> set[str]:
    zones = {_path_key(path) for path in zone_paths}
    found: set[str] = set()
    for text in _text_values(value):
        candidates = set(_WINDOWS_ABS.findall(text))
        candidates.update(match.group("path") for match in _POSIX_ABS.finditer(text))
        for candidate in candidates:
            key = _path_key(candidate)
            if any(key != zone and key.startswith(zone.rstrip("/") + "/") for zone in zones):
                found.add(key)
    return found


def _mentions_internal_zone(value: Any, zone_paths: Iterable[str]) -> bool:
    return bool(touches(value, zone_paths))


def request_uses_internal_data(req: dict, settings) -> bool:
    paths = [zone.path for zone in settings.policy.data_zones if zone.level in {"internal", "restricted"}]
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    plan_fields = [[step.get("instruction"), step.get("input_refs")]
                   for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    result_inputs = [_result_input_evidence(result) for result in results.values()]
    return bool(paths and _mentions_internal_zone(
        [req.get("text"), req.get("project_dirs"), req.get("references"), plan_fields, result_inputs], paths))


def internal_data_steps(req: dict, settings) -> set[str] | None:
    """Return proven internal steps and descendants; otherwise require request-wide rules.

    A step is public only when its plan and result avoid internal zones and every concrete internal input named by the
    request or plan is attributed to a step. A zone root alone is not concrete provenance (#715).
    """
    paths = [zone.path for zone in settings.policy.data_zones if zone.level in {"internal", "restricted"}]
    if not paths or not request_uses_internal_data(req, settings):
        return set()

    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    steps = [step for step in plan.get("steps") or [] if isinstance(step, Mapping) and step.get("id")]
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    request_inputs = _concrete_internal_paths(
        [req.get("text"), req.get("project_dirs"), req.get("references")], paths)
    plan_inputs = _concrete_internal_paths(
        [[step.get("instruction"), step.get("input_refs")] for step in steps], paths)
    named_inputs = request_inputs | plan_inputs
    if not named_inputs:
        return None

    direct: set[str] = set()
    attributed: set[str] = set()
    for step in steps:
        step_id = str(step["id"])
        evidence = [step.get("instruction"), step.get("input_refs"),
                    _result_input_evidence(results.get(step_id))]
        if _mentions_internal_zone(evidence, paths):
            direct.add(step_id)
        attributed.update(_concrete_internal_paths(evidence, paths) & named_inputs)
    if attributed != named_inputs:
        return None

    internal = set(direct)
    changed = True
    while changed:
        changed = False
        for step in steps:
            step_id = str(step["id"])
            dependencies = {str(value) for value in step.get("depends_on") or []}
            if step_id not in internal and dependencies & internal:
                internal.add(step_id)
                changed = True
    return internal


def source_uses_internal_data(name: str, req: dict, settings,
                              step_scope: set[str] | None = None) -> bool:
    """Classify one output by its source step, with request-wide fallback for missing provenance."""
    if not request_uses_internal_data(req, settings):
        return False
    step_scope = internal_data_steps(req, settings) if step_scope is None else step_scope
    if step_scope is None:
        return True
    source_step = _source_step(name, req)
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    known_steps = {str(step.get("id")) for step in plan.get("steps") or []
                   if isinstance(step, Mapping) and step.get("id")}
    if source_step is None or source_step not in known_steps:
        return True
    return source_step in step_scope


def _decisions_document(req: dict, decisions: Iterable[dict]) -> str:
    labels = {
        "clarify": "확인 질문 답", "research_plan": "CP1", "research_evidence": "CP2",
        "research_continue": "리뷰 뒤 차수", "question": "질문", "resume": "재개",
    }
    lines = ["# 결정 기록", "", "확인 질문, CP1·CP2, 리뷰 뒤 차수와 실행 중 메모의 원문이다. 로컬 경로만 가렸다.", ""]
    if req.get("clarifications"):
        lines += ["## 확인 질문", "", "```json", json.dumps(req["clarifications"], ensure_ascii=False, indent=2), "```", ""]
    shown = 0
    for decision in decisions:
        approval = decision.get("approval") or {}
        if approval.get("request_id") != req.get("id") or approval.get("kind") not in labels:
            continue
        shown += 1
        note = str(decision.get("note") or "").strip() or "메모 없음"
        state = "승인" if decision.get("approved") else "거절·수정"
        lines += [f"## {labels[approval['kind']]} {shown}", "", f"- 결과: {state}", "- 메모 원문:", "", note, ""]
    if req.get("pi_notes"):
        lines += ["## 실행 중 PI 메모", ""]
        for note in req["pi_notes"]:
            text = note.get("text") if isinstance(note, Mapping) else str(note)
            lines += [str(text or "").strip(), ""]
    review = req.get("review") or (req.get("review_progress") or {}).get("review")
    if review:
        lines += ["## 과학 리뷰", "", "```json", json.dumps(review, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def _costs_document(req: dict, tasks: Iterable[dict]) -> str:
    counts: collections.Counter[str] = collections.Counter()
    for task in tasks:
        if task.get("request_id") == req.get("id"):
            counts[str(task.get("agent_id") or (task.get("payload") or {}).get("agent_id") or "미확인")] += 1
    lines = ["# 비용·직원 작업", "", f"- 요청 비용: ${float(req.get('cost_usd') or 0):.2f}", "",
             "| 직원 | 작업 수 |", "|---|---:|"]
    lines += [f"| {name} | {count} |" for name, count in sorted(counts.items())]
    if not counts:
        lines.append("| 기록 없음 | 0 |")
    return "\n".join(lines) + "\n"


def _references(req: dict, report: str, assets: Mapping[str, str]) -> str:
    text = "\n".join([report, *assets.values(), *[str(ref.get("value") or "") for ref in req.get("references") or []]])
    pmids = {match.group(1) for match in _PMID.finditer(text)}
    for ref in req.get("references") or []:
        if ref.get("kind") == "pmid" and str(ref.get("value") or "").isdigit():
            pmids.add(str(ref["value"]))
    dois = {match.group(0).rstrip(".,;)") for match in _DOI.finditer(text)}
    geos = {match.group(0).upper() for match in _GEO.finditer(text)}
    urls = {redact_url_credentials(match.group(0)).rstrip(".,;") for match in _URL.finditer(text)}
    lines = ["# 참고문헌", ""]
    for title, values, template in (
        ("PMID", sorted(pmids), "https://pubmed.ncbi.nlm.nih.gov/{}/"),
        ("DOI", sorted(dois, key=str.casefold), "https://doi.org/{}"),
        ("GEO", sorted(geos), "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={}"),
    ):
        lines += [f"## {title}", ""]
        lines += [f"- [{value}]({template.format(value)})" for value in values] or ["- 기록 없음"]
        lines.append("")
    known = {f"https://doi.org/{doi}" for doi in dois}
    lines += ["## 그 밖의 URL", ""]
    lines += [f"- {url}" for url in sorted(urls - known)] or ["- 기록 없음"]
    return "\n".join(lines) + "\n"


def _artifact_target(step_id: str, path: str, result: Mapping[str, Any],
                     source_targets: Mapping[str, str]) -> str | None:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    candidates = [normalized]
    if normalized.startswith("outputs/"):
        candidates.append(f"steps/{step_id}/{normalized}")
        workdir = result.get("workdir") or result.get("workdir_id")
        if isinstance(workdir, str):
            candidates.append(posixpath.join(workdir.replace("\\", "/"), normalized))
    return next((source_targets[name] for name in candidates if name in source_targets), None)


def _claim_evidence_document(req: dict, assets: Mapping[str, str],
                             source_targets: Mapping[str, str]) -> str:
    report, _history = _split_revision_history(_paper_candidate(req, assets))
    anchors = _claim_notes(report)
    if not anchors:
        return ""

    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    lines = ["# Claim 근거", "", "정리본 보고서 각주가 가리키는 단계·claim·근거 파일 목록입니다.", ""]
    document = "docs/reference/claim_evidence.md"
    for note_id, (step_id, claim_id) in anchors.items():
        lines += [f'<a id="{note_id}"></a>', f"## {step_id}/{claim_id}", "",
                  f"- 단계: `{step_id}`", f"- claim: `{claim_id}`"]
        result = results.get(step_id) if isinstance(results.get(step_id), Mapping) else {}
        ledger = _claim_ledger(result)
        evidence_by_id = {str(row.get("id")): row for row in ledger.get("evidence") or []
                          if isinstance(row, Mapping)}
        artifact_paths = {str(row.get("artifact_id")): str(row.get("path") or "")
                          for row in ledger.get("artifact_refs") or [] if isinstance(row, Mapping)}
        evidence_ids = [str(link.get("evidence_id")) for link in ledger.get("links") or []
                        if isinstance(link, Mapping) and str(link.get("claim_id")) == claim_id]
        evidence_lines = []
        for evidence_id in dict.fromkeys(evidence_ids):
            evidence = evidence_by_id.get(evidence_id, {})
            source = evidence.get("source") if isinstance(evidence.get("source"), Mapping) else {}
            artifact_id = str(source.get("artifact_id") or "")
            target = _artifact_target(step_id, artifact_paths.get(artifact_id, ""), result, source_targets)
            if target:
                evidence_lines.append(
                    f"  - `{evidence_id}`: {_markdown_link_from(document, target, PurePosixPath(target).name)}"
                )
                continue
            uri = str(source.get("uri") or "")
            if uri.startswith(("http://", "https://")):
                evidence_lines.append(f"  - `{evidence_id}`: [외부 근거]({redact_url_credentials(uri)})")
                continue
            scheme, value = str(source.get("id_scheme") or ""), str(source.get("id_value") or "")
            evidence_lines.append(f"  - `{evidence_id}`: {scheme + ' ' + value if scheme and value else '정리본에 근거 파일 없음'}")
        lines.append("- 근거 파일:")
        lines += evidence_lines or ["  - 정리본에 근거 파일 없음"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _note_fields(note: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in str(note or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and value.strip():
            fields[key.strip().casefold()] = value.strip()
    return fields


def _zone_for_reference(value: str, settings) -> Any | None:
    for zone in getattr(settings.policy, "data_zones", []):
        if touches(value, [zone.path]):
            return zone
    return None


def _data_licenses(req: dict, settings) -> str:
    refs = req.get("references") or []
    rows: list[tuple[str, str, str, str, str]] = []
    path_number = 0
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        kind = str(ref.get("kind") or "자료")
        value = str(ref.get("value") or "").strip()
        if not value:
            continue
        note = _note_fields(ref.get("note"))
        source = value
        access = note.get("접근") or note.get("access") or "미확인"
        redistribution = note.get("재배포") or note.get("redistribution") or "미확인"
        citation = note.get("인용") or note.get("citation") or "미확인"
        if kind == "path":
            path_number += 1
            source = f"요청 경로 자료 {path_number}"
            zone = _zone_for_reference(value, settings)
            zone_note = _note_fields(getattr(zone, "note", "")) if zone else {}
            access = note.get("접근") or zone_note.get("접근") or (
                {"public": "공개 구역", "internal": "승인된 내부 환경", "restricted": "통제 접근 환경"}
                .get(getattr(zone, "level", ""), "미확인")
            )
            redistribution = note.get("재배포") or zone_note.get("재배포") or (
                "공개 가능한 집계표만" if getattr(zone, "level", "") in {"internal", "restricted"} else "미확인"
            )
            citation = note.get("인용") or zone_note.get("인용") or "미확인"
        elif kind == "doi":
            source = f"https://doi.org/{value}"
            access = note.get("접근") or "공개 DOI 메타데이터; 원문은 출처 조건을 따름"
            redistribution = note.get("재배포") or "서지정보만"
            citation = note.get("인용") or f"DOI {value}"
        elif kind == "pmid":
            source = f"https://pubmed.ncbi.nlm.nih.gov/{value}/"
            access = note.get("접근") or "PubMed 공개 메타데이터; 원문은 출처 조건을 따름"
            redistribution = note.get("재배포") or "서지정보만"
            citation = note.get("인용") or f"PMID {value}"
        elif kind == "github":
            source = value if re.match(r"https?://", value, re.I) else f"https://github.com/{value}"
        rows.append((kind, source, access, redistribution, citation))
    lines = ["# 자료와 이용 조건", "", "원자료는 이 정리본에 넣지 않습니다. 알려지지 않은 조건은 항목별로 미확인이라고 표시합니다.", "",
             "| 자료 | 출처 | 접근 조건 | 재배포 범위 | 필수 인용 |", "|---|---|---|---|---|"]
    lines += [f"| {_safe_cell(kind)} | {_safe_cell(source)} | {_safe_cell(access)} | "
              f"{_safe_cell(redistribution)} | {_safe_cell(citation)} |"
              for kind, source, access, redistribution, citation in rows]
    if not rows:
        lines.append("| 요청 기록에 자료 없음 | 미확인 | 미확인 | 미확인 | 미확인 |")
    return "\n".join(lines) + "\n"


def _first_limit(report: str) -> str:
    scope = ""
    candidates: dict[str, list[str]] = collections.defaultdict(list)
    for block in report.split("\n\n"):
        value = block.strip()
        if value.startswith("## "):
            heading = value.casefold()
            if "한계" in heading or "limitation" in heading:
                scope = "limit"
            elif "논의" in heading or "discussion" in heading:
                scope = "discussion"
            elif "요약" in heading or "abstract" in heading:
                scope = "summary"
            else:
                scope = ""
            continue
        if not scope or not value or value.startswith(("#", "![", "|")):
            continue
        plain = " ".join(_ANCHOR.sub("", value).split())
        candidates[scope].extend(_SENTENCE_END.split(plain))
    constraint = re.compile(
        r"한계|제한|제약|limitation|검정력|일반화|편향|교란|설계상|방법상|"
        r"표본\s*(?:수|크기)?\S{0,12}(?:작|적|부족)|(?:자료|데이터)\S{0,12}부족",
        re.I,
    )
    limited = next((sentence.strip() for name in ("limit", "discussion", "summary")
                    for sentence in candidates[name] if constraint.search(sentence)), "")
    return limited or "해석 범위와 남은 한계는 과학 보고서에 적었습니다."


def _readme(req: dict, report: str, rows: list[tuple[str, str, str]], figure_names: list[str],
            table_names: list[str], omitted_tables: list[Mapping[str, Any]],
            run_documents: Mapping[str, str], branch_rows: list[dict[str, str]] | None = None) -> str:
    title = report.splitlines()[0].lstrip("#").strip() or "연구 결과"
    abstract = "figures/graphical_abstract.svg" if "figures/graphical_abstract.svg" in figure_names else None
    key = next((name for name in figure_names if name != abstract), None)
    question = " ".join(str(req.get("text") or "연구 질문이 기록되지 않았습니다.").split())[:500]
    lines = [f"# {title}", "", f"**질문: {question}**", "",
             f"**답: {_first_answer(report, req)}**", "", f"**한계: {_first_limit(report)}**", ""]
    if abstract:
        lines += [f"![그래픽 초록]({abstract})", ""]
    if key:
        lines += [f"![대표 그림]({key})", ""]
    lines += ["## 보고서·결과표·재현", "", "- 보고서: [Markdown](report/report.md)"]
    if table_names:
        table_links = ", ".join(f"[{PurePosixPath(name).name}]({quote(name, safe='/')})"
                                for name in table_names)
        lines.append(f"- 결과표: {table_links}")
    else:
        lines.append("- 결과표: 공개 가능한 표가 없습니다.")
    if "run/reproduce.md" in run_documents:
        lines.append("- 재현: [안내](run/reproduce.md)")
    else:
        lines.append("- 재현: 요청 기록에 안내가 없습니다.")
    if "run/progress.md" in run_documents:
        lines.append("- 진행: [기록](run/progress.md)")
    if "run/semantic_report.md" in run_documents:
        lines.append("- 온톨로지·의미 장치: [요청별 보고서](run/semantic_report.md)")
    lines.append("- 참고문헌: [목록](REFERENCES.md)")
    lines.append("")
    if branch_rows:
        lines += ["| 갈래 | 상태 | 핵심 결과 한 줄 | 폴더 |", "|---|---|---|---|"]
        lines += [f"| {_safe_cell(row['id'])} | {_safe_cell(row['status'])} | {_safe_cell(row['result'])} | "
                  f"[열기](studies/{row['folder']}/README.md) |" for row in branch_rows]
    else:
        lines += ["| 단계 | 자료 | 결과 |", "|---|---|---|"]
        lines += [f"| {_safe_cell(step)} | {_safe_cell(data)} | {_safe_cell(result)} |" for step, data, result in rows]
    omitted_figures = list(dict.fromkeys(re.findall(r"\*\(그림 생략: ([^)\n]+)\)\*", report)))
    if omitted_figures:
        lines += ["", "## 생략한 그림", "", *[f"- *(그림 생략: {label})*" for label in omitted_figures]]
    lines += ["", "## 자료 안내", "",
              "원자료는 넣지 않습니다. 출처·접근 조건·재배포 범위·필수 인용은 "
              "[DATA_AND_LICENSES.md](DATA_AND_LICENSES.md)에 적었습니다.", "",
              "상세 단계 보고서와 검토문은 [docs/reference/](docs/reference/README.md)에 모았습니다."]
    if omitted_tables:
        lines += ["", "## 뺀 표", "", *_omitted_table_lines(omitted_tables)]
    return "\n".join(lines) + "\n"


def _table_metadata(name: str, body: str | bytes) -> tuple[int | None, list[str], bool]:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in {".tsv", ".csv"}:
        return None, [], True
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    delimiter = "\t" if suffix == ".tsv" else ","
    try:
        parsed = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(cell.strip() for cell in row)]
    except csv.Error:
        return None, [], False
    if not parsed:
        return 0, [], False
    columns = [cell.strip() for cell in parsed[0]]
    rectangular = bool(columns) and all(columns) and len(set(columns)) == len(columns)
    rectangular = rectangular and all(len(row) == len(columns) for row in parsed[1:])
    return len(parsed) - 1, columns, rectangular


def _omitted_table(name: str, body: str | bytes, *, internal: bool, declared: set[str],
                   folder: str | None) -> dict[str, Any]:
    rows, columns, _valid = _table_metadata(name, body)
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    suffix = PurePosixPath(name).suffix.lower()
    if internal and name not in declared:
        reason = "내부 자료이며 집계표로 선언되지 않았습니다."
    elif internal and suffix in _AGGREGATE_TEXT_SUFFIXES and small_cells(text, suffix):
        reason = "내부 집계에 1–9 작은 칸이 있습니다."
    else:
        reason = "게시 허용 목록에 들지 않습니다."
    return {"source": name, "name": PurePosixPath(name).name, "reason": reason,
            "rows": rows, "columns": columns, "folder": folder}


def _report_table_issues(files: Mapping[str, str | bytes]) -> list[dict[str, str]]:
    document = "report/report.md"
    report = files.get(document)
    if not isinstance(report, str):
        return []
    mapping = {name: name for name in files}
    issues: list[dict[str, str]] = []
    for match in _markdown_targets(report):
        path, resolved, _suffix = _local_link(document, match.group("target"), mapping)
        if path is None or resolved is None or PurePosixPath(resolved).suffix.lower() not in {".tsv", ".csv"}:
            continue
        _rows, _columns, valid = _table_metadata(resolved, files[resolved])
        if not valid:
            issues.append({"kind": "table_shape", "path": resolved})
    return issues


def scan_publish_files(files: Mapping[str, str | bytes], *,
                       internal: bool | set[str] = False) -> list[dict[str, str]]:
    """Find credentials, local paths, signed URLs and internal aggregate cells of 1–9."""
    issues: list[dict[str, str]] = []
    for name, body in files.items():
        if isinstance(body, bytes):
            if not name.lower().endswith(".svg"):
                continue
            text = body.decode("utf-8", errors="replace")
        else:
            text = body
        if any(re.search(pattern, text) for pattern in SECRET_PATTERNS) or re.search(
                r"(?im)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*(?!<redacted-secret>|\[REDACTED\])\S+", text):
            issues.append({"kind": "secret", "path": name})
        if _LOCAL_SCAN.search(text) or _PRIVATE_PATH.search(text):
            issues.append({"kind": "local_path", "path": name})
        if _SIGNED_URL.search(text) or redact_url_credentials(text) != text:
            issues.append({"kind": "signed_url", "path": name})
        if name == "DATA_AND_LICENSES.md" and _LICENSE_PLACEHOLDER.search(text):
            issues.append({"kind": "placeholder", "path": name})
        if name == "report/report.md" and _ANCHOR.search(text):
            issues.append({"kind": "claim_anchor", "path": name})
        suffix = PurePosixPath(name).suffix.lower()
        file_internal = name in internal if isinstance(internal, set) else internal
        if file_internal and suffix in {".tsv", ".csv"} and small_cells(text, suffix):
            issues.append({"kind": "small_cell", "path": name})
    return issues


def build_portfolio_files(req: dict, settings, assets: Mapping[str, str], images: Mapping[str, bytes], *,
                          decisions: Iterable[dict] = (), tasks: Iterable[dict] = (),
                          bundle: Path | None = None,
                          run_documents: Mapping[str, str] | None = None,
                          internal_files: set[str] | None = None) -> dict[str, str | bytes]:
    """Return the stable publication layout; raw data and third-party full text never enter it."""
    assets = {name: _clean(body, settings, req) for name, body in assets.items()
              if not _LITERATURE_OUTPUT.search(name.replace("\\", "/"))}
    report = portfolio_report(req, settings, assets)
    revision_history = portfolio_revision_history(req, settings, assets)
    if check_document(report)["status"] != "PASS":
        raise ValueError("과학 보고서에 반드시 0이어야 하는 한국어 표현이 남았습니다.")
    files: dict[str, str | bytes] = {"report/report.md": report}
    used = set(files)
    source_targets: dict[str, str] = {}
    branch_specs, step_folders = _branch_layout(req)
    branch_rows = _branch_rows(req, branch_specs, step_folders) if branch_specs else []
    internal = request_uses_internal_data(req, settings)
    step_scope = internal_data_steps(req, settings)
    tracked_internal: set[str] = set()

    def source_internal(name: str) -> bool:
        return source_uses_internal_data(name, req, settings, step_scope)

    def track(source: str, target: str) -> None:
        if source_internal(source):
            tracked_internal.add(target)

    aggregate_tables = declared_aggregate_tables(req)
    declared_reports = _declared_outputs(req, "report")
    omitted_tables: list[dict[str, Any]] = []
    table_names: list[str] = []
    figure_names: list[str] = []
    reference_targets: dict[str, list[str]] = collections.defaultdict(list)

    for name, body in assets.items():
        path = PurePosixPath(name)
        suffix = path.suffix.lower()
        branch_folder = step_folders.get(_source_step(name, req) or "")
        prefix = f"studies/{branch_folder}/" if branch_folder else ""
        if suffix in _CODE_SUFFIXES and "scripts" in {part.casefold() for part in path.parts}:
            target = _unique_target(prefix + "code", name, used)
            files[target] = body
            source_targets[name] = target
            track(name, target)
        elif branch_folder and suffix == ".md" and path.as_posix() in declared_reports:
            target = _unique_target(f"docs/reference/{branch_folder}", name, used)
            files[target] = body
            source_targets[name] = target
            reference_targets[branch_folder].append(target)
            track(name, target)
        elif path.name in _NEXTFLOW_NAMES and "pipeline_info" in {part.casefold() for part in path.parts}:
            if publish_file_allowed(name, body, internal=source_internal(name), declared=aggregate_tables):
                target = _unique_target(prefix + "run/nextflow", name, used)
                files[target] = body
                source_targets[name] = target
                track(name, target)
        elif suffix in _TABLE_SUFFIXES:
            if not publish_file_allowed(name, body, internal=source_internal(name), declared=aggregate_tables):
                omitted_tables.append(_omitted_table(
                    name, body, internal=source_internal(name), declared=aggregate_tables,
                    folder=branch_folder,
                ))
            else:
                target = _unique_target(prefix + "tables", name, used)
                files[target] = body
                source_targets[name] = target
                table_names.append(target)
                track(name, target)

    abstract_source = next((name for name in images
                            if "graphical_abstract" in name.casefold()
                            and not step_folders.get(_source_step(name, req) or "")
                            and PurePosixPath(name).suffix.lower() == ".svg"), None)
    prepared_images = dict(images)
    if abstract_source is None:
        shown = dict(req)
        shown["report"] = report
        prepared_images["graphical_abstract.svg"] = build_graphical_abstract(
            shown, report, scrub=lambda value: _clean(value, settings, req)).encode("utf-8")
    for name, body in prepared_images.items():
        if _LITERATURE_OUTPUT.search(name.replace("\\", "/")) or PurePosixPath(name).suffix.lower() != ".svg":
            continue
        branch_folder = step_folders.get(_source_step(name, req) or "")
        figure_folder = f"studies/{branch_folder}/figures" if branch_folder else "figures"
        target = ("figures/graphical_abstract.svg" if not branch_folder and
                  "graphical_abstract" in name.casefold() else _unique_target(figure_folder, name, used))
        if target in files:
            continue
        text = _clean(body.decode("utf-8", errors="strict"), settings, req)
        files[target] = text.encode("utf-8")
        source_targets[name] = target
        track(name, target)
        figure_names.append(target)

    _add_workdir_source_targets(req, source_targets)

    request_text = _clean(str(req.get("text") or ""), settings, req)
    execution = ""
    if bundle is not None and (bundle / "execution_record.md").is_file():
        execution = (bundle / "execution_record.md").read_text(encoding="utf-8", errors="replace")
    execution = _clean(execution or str(req.get("execution_record") or "# 실행 기록\n\n기록 없음\n"), settings, req)
    files.update({
        "run/requests.md": f"# 요청 원문\n\n```text\n{request_text.strip()}\n```\n",
        "run/decisions.md": _clean(_decisions_document(req, decisions), settings, req),
        "run/execution_record.md": execution,
        "run/costs.md": _costs_document(req, tasks),
        "REFERENCES.md": _clean(_references(req, report, assets), settings, req),
        "DATA_AND_LICENSES.md": _clean(_data_licenses(req, settings), settings, req),
    })
    if revision_history:
        files["run/revision_history.md"] = revision_history
    run_documents = dict(run_documents or {})
    if bundle is not None and (bundle / "semantic_report.md").is_file():
        run_documents["run/semantic_report.md"] = (bundle / "semantic_report.md").read_text(
            encoding="utf-8", errors="replace")
    for name in ("run/progress.md", "run/reproduce.md", "run/semantic_report.md"):
        if name in run_documents:
            files[name] = _clean(run_documents[name], settings, req)
    claim_evidence = _claim_evidence_document(req, assets, source_targets)
    if claim_evidence:
        files["docs/reference/claim_evidence.md"] = _clean(claim_evidence, settings, req)
    if branch_rows:
        files.update({name: _clean(body, settings, req) for name, body in _study_documents(
            req, branch_rows, branch_specs, step_folders, source_targets, reference_targets, omitted_tables,
        ).items()})
    else:
        files["docs/reference/README.md"] = (
            "# 상세 근거 문서\n\n이 폴더는 lookup-only입니다. 보고서 각주가 가리키는 근거만 확인하세요.\n"
        )
    readme_req = dict(req)
    readme_req["text"] = request_text.replace("<local-path>", "내부 자료").replace(
        "<reference-path>", "참고 자료")
    readme = _plain_korean(_readme(
        readme_req, report, _step_rows(req, assets), figure_names, table_names, omitted_tables,
        run_documents, branch_rows))
    if check_document(readme)["status"] != "PASS":
        raise ValueError("README에 반드시 0이어야 하는 한국어 표현이 남았습니다.")
    files["README.md"] = _clean(readme, settings, req)

    source_targets.update({name: name for name in files})
    files = _rewrite_markdown_links(files, source_targets)
    broken = broken_markdown_links(files, source_targets)
    if broken:
        detail = ", ".join(f"{row['document']} -> {row['target']}" for row in broken)
        raise EditionCheckError(f"정리본 Markdown 링크가 깨졌습니다: {detail}")
    if table_issues := _report_table_issues(files):
        raise check_failure(table_issues)

    issues = scan_publish_files(
        files, internal=tracked_internal if step_scope is not None else internal)
    blocking = [issue for issue in issues if issue["kind"] != "small_cell"]
    if blocking:
        raise check_failure(blocking)
    if internal_files is not None:
        internal_files.update(tracked_internal)
    return files
