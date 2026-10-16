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
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote

from .integrations.github import SECRET_PATTERNS, publish_clean, redact_url_credentials, sanitize
from .korean_qc import check_document, check_preservation
from .policy import touches
from .report_abstract import build_graphical_abstract
from .request_bundle import _POSIX_ABS, _WINDOWS_ABS


_PRIVATE_PATH = re.compile(
    r"(?<![\w:])(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/)[^\s<>\"'`\])]+"
)
_ASSIGNMENT = re.compile(
    r"(?im)\b((?:api[_-]?key|token|password|secret)\s*[:=]\s*)"
    r"(?!<redacted-secret>|&lt;redacted-secret&gt;|\[REDACTED\])[^\s,;<]+"
)
_LOCAL_SCAN = re.compile(
    r"(?i)(?:[A-Za-z]:[/\\](?:Users|Documents and Settings|AppData|ProgramData)[/\\]|"
    r"/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/|\.labhq[/\\]|C--Users)"
)
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
_PORTFOLIO_FOLDERS = ("report/", "run/", "code/", "figures/", "tables/")


def declared_aggregate_tables(req: Mapping[str, Any]) -> set[str]:
    """Return tables explicitly declared with the aggregate ``table`` output type."""
    declared: set[str] = set()

    def add(step_id: str, name: Any, data_type: Any) -> None:
        if isinstance(data_type, Mapping):
            data_type = data_type.get("value") if data_type.get("basis") == "declared" else None
        if not isinstance(name, str) or data_type != "table":
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


def _paper_candidate(req: dict, assets: Mapping[str, str]) -> str:
    report = str(req.get("report") or "").strip()
    candidates: list[tuple[int, str]] = []
    for name, body in assets.items():
        if not name.lower().endswith(".md") or not _paper_like(body):
            continue
        lowered = name.lower()
        score = 10
        if "final_report" in lowered or "science_report" in lowered or "report_ko" in lowered:
            score += 20
        if "/report/" in lowered:
            score += 10
        candidates.append((score, body.strip()))
    review_ended = (req.get("outcome") == "research_review_revise"
                    or str((req.get("review") or {}).get("verdict") or "").casefold() == "revise")
    if candidates and (review_ended or not _paper_like(report)):
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


def portfolio_report(req: dict, settings, assets: Mapping[str, str]) -> str:
    """Return the canonical scientific manuscript used by the portfolio edition."""
    return _plain_korean(_clean(_corrections(_paper_candidate(req, assets), req), settings, req))


def _corrections(report: str, req: dict) -> str:
    review = req.get("review") or (req.get("review_progress") or {}).get("review") or {}
    if str(review.get("verdict") or "").casefold() != "revise" or not review.get("issues"):
        return report
    if re.search(r"(?m)^## 정정\s*$", report):
        return report
    lines = ["", "## 정정", "", "다음 리뷰 지적은 다음 차수 없이 종료되어 본문을 읽을 때 함께 적용한다.", ""]
    for issue in review["issues"]:
        lines.append(f"- {issue.get('problem') or '지적'} → {issue.get('request') or '수정 요청'}")
    return report.rstrip() + "\n" + "\n".join(lines) + "\n"


def _first_answer(report: str) -> str:
    in_abstract = False
    for block in report.split("\n\n"):
        value = block.strip()
        if value.startswith("## "):
            in_abstract = "요약" in value or "abstract" in value.casefold()
            continue
        if in_abstract and value and not value.startswith(("#", "![", "|")):
            return " ".join(value.split())
    return "결과와 한계는 과학 보고서에 정리했다."


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
        text = str(result.get("text") or "")
        if not text and "report" in sid.casefold():
            text = str(req.get("report") or "")
        rows.append((sid, data, _result_sentence(text)))
    return rows or [("종합", "요청에 기록된 자료", _result_sentence(str(req.get("report") or "")))]


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


def request_uses_internal_data(req: dict, settings) -> bool:
    paths = [zone.path for zone in settings.policy.data_zones if zone.level in {"internal", "restricted"}]
    return bool(paths and touches(
        [req.get("text"), req.get("project_dirs"), req.get("references"), req.get("results")], paths))


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


def _data_licenses(req: dict) -> str:
    refs = req.get("references") or []
    rows = []
    for ref in refs:
        value = str(ref.get("value") or "").strip()
        if value:
            rows.append((str(ref.get("kind") or "자료"), value))
    lines = ["# 자료와 이용 조건", "", "원자료는 이 정리본에 넣지 않았다. 자료별 이용 조건과 필수 인용문은 게시 전 확인한다.", "",
             "| 자료 영역 | 출처 | 이용 조건 | 필수 인용 문구 |", "|---|---|---|---|"]
    lines += [f"| {_safe_cell(kind)} | {_safe_cell(value)} | 확인 필요 | 원 출처의 필수 문구를 넣으세요 |"
              for kind, value in rows]
    if not rows:
        lines.append("| 기록 없음 | 요청 기록 확인 | 확인 필요 | 필수 문구 자리 |")
    return "\n".join(lines) + "\n"


def _readme(req: dict, report: str, rows: list[tuple[str, str, str]], figure_names: list[str],
            omitted_tables: list[str], run_documents: Mapping[str, str]) -> str:
    title = report.splitlines()[0].lstrip("#").strip() or "연구 결과"
    abstract = "figures/graphical_abstract.svg" if "figures/graphical_abstract.svg" in figure_names else None
    key = next((name for name in figure_names if name != abstract), None)
    lines = [f"# {title}", "", f"**답: {_first_answer(report)}**", ""]
    if abstract:
        lines += [f"![그래픽 초록]({abstract})", ""]
    lines += ["| 단계 | 자료 | 결과 |", "|---|---|---|"]
    lines += [f"| {_safe_cell(step)} | {_safe_cell(data)} | {_safe_cell(result)} |" for step, data, result in rows]
    if key:
        lines += ["", f"![핵심 그림]({key})"]
    omitted_figures = list(dict.fromkeys(re.findall(r"\*\(그림 생략: ([^)\n]+)\)\*", report)))
    if omitted_figures:
        lines += ["", "## 생략한 그림", "", *[f"- *(그림 생략: {label})*" for label in omitted_figures]]
    lines += ["", "## 읽는 순서", "",
              "1. [과학 보고서](report/report.md) — 논문 형식 보고서와 정정 사항"]
    if "run/progress.md" in run_documents:
        lines.append("2. [진행 기록](run/progress.md) — 계획 변경과 과학 리뷰 기록")
    if "run/reproduce.md" in run_documents:
        lines.append("3. [재현 방법](run/reproduce.md) — 환경, 실행 명령과 입력 출처")
    lines += ["4. [실행 기록](run/execution_record.md) — 요청, 결정, 비용과 Nextflow 기록",
              "5. [출처](REFERENCES.md) — PMID, DOI, GEO와 URL",
              "6. `code/`, `figures/`, `tables/` — 게시 가능한 코드·SVG·집계표", "",
              "## 저장소 지도", "", "```text", "README.md", "report/", "run/", "code/", "figures/", "tables/",
              "REFERENCES.md", "DATA_AND_LICENSES.md", "```", "",
              "## Summary (English)", "",
              f"This repository is the publication edition of labhq request {req.get('id')}. "
              "It keeps the scientific report, aggregate results, code, figures, decisions, and execution record together.", "",
              "## 어떻게 만들었나", "",
              f"labhq 요청 `{req.get('id')}`의 결과를 [labhq#686](https://github.com/ehojune/bioinfo-team-3d/issues/686) 규칙으로 정리했다. "
              "정리 직원은 graphical-abstract와 humanizer 규칙을 적용하며 숫자·링크·인용·표 모양 보존 검사를 통과한 문장만 반영한다.", "",
              "## 원자료 안내", "",
              "원자료는 넣지 않았다. 출처, 이용 조건과 필수 인용은 [DATA_AND_LICENSES.md](DATA_AND_LICENSES.md)에 있다."]
    if omitted_tables:
        lines += ["", f"내부 자료 영역이라 표 {len(omitted_tables)}개는 요청 묶음에만 둠."]
    return "\n".join(lines) + "\n"


def scan_publish_files(files: Mapping[str, str | bytes], *, internal: bool = False) -> list[dict[str, str]]:
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
        suffix = PurePosixPath(name).suffix.lower()
        if internal and suffix in {".tsv", ".csv"} and small_cells(text, suffix):
            issues.append({"kind": "small_cell", "path": name})
    return issues


def build_portfolio_files(req: dict, settings, assets: Mapping[str, str], images: Mapping[str, bytes], *,
                          decisions: Iterable[dict] = (), tasks: Iterable[dict] = (),
                          bundle: Path | None = None,
                          run_documents: Mapping[str, str] | None = None) -> dict[str, str | bytes]:
    """Return the stable publication layout; raw data and third-party full text never enter it."""
    assets = {name: _clean(body, settings, req) for name, body in assets.items()
              if not _LITERATURE_OUTPUT.search(name.replace("\\", "/"))}
    report = portfolio_report(req, settings, assets)
    if check_document(report)["status"] != "PASS":
        raise ValueError("과학 보고서에 반드시 0이어야 하는 한국어 표현이 남았습니다.")
    files: dict[str, str | bytes] = {"report/report.md": report}
    used = set(files)
    source_targets: dict[str, str] = {}
    internal = request_uses_internal_data(req, settings)
    aggregate_tables = declared_aggregate_tables(req)
    omitted_tables: list[str] = []
    figure_names: list[str] = []

    for name, body in assets.items():
        path = PurePosixPath(name)
        suffix = path.suffix.lower()
        if suffix in _CODE_SUFFIXES and "scripts" in {part.casefold() for part in path.parts}:
            target = _unique_target("code", name, used)
            files[target] = body
            source_targets[name] = target
        elif path.name in _NEXTFLOW_NAMES and "pipeline_info" in {part.casefold() for part in path.parts}:
            if publish_file_allowed(name, body, internal=internal, declared=aggregate_tables):
                target = _unique_target("run/nextflow", name, used)
                files[target] = body
                source_targets[name] = target
        elif suffix in _TABLE_SUFFIXES:
            if not publish_file_allowed(name, body, internal=internal, declared=aggregate_tables):
                omitted_tables.append(name)
            else:
                target = _unique_target("tables", name, used)
                files[target] = body
                source_targets[name] = target

    abstract_source = next((name for name in images
                            if "graphical_abstract" in name.casefold()
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
        target = ("figures/graphical_abstract.svg" if "graphical_abstract" in name.casefold()
                  else _unique_target("figures", name, used))
        if target in files:
            continue
        text = _clean(body.decode("utf-8", errors="strict"), settings, req)
        files[target] = text.encode("utf-8")
        source_targets[name] = target
        figure_names.append(target)

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
        "DATA_AND_LICENSES.md": _clean(_data_licenses(req), settings, req),
    })
    run_documents = dict(run_documents or {})
    for name in ("run/progress.md", "run/reproduce.md"):
        if name in run_documents:
            files[name] = _clean(run_documents[name], settings, req)
    readme = _plain_korean(_readme(
        req, report, _step_rows(req, assets), figure_names, omitted_tables, run_documents))
    if check_document(readme)["status"] != "PASS":
        raise ValueError("README에 반드시 0이어야 하는 한국어 표현이 남았습니다.")
    files["README.md"] = _clean(readme, settings, req)

    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    for step_id, result in results.items():
        if not isinstance(result, Mapping):
            continue
        workdir_value = result.get("workdir") or result.get("workdir_id")
        if not isinstance(workdir_value, str):
            continue
        prefix = f"steps/{step_id}/outputs/"
        workdir = workdir_value.replace("\\", "/")
        for source, target in list(source_targets.items()):
            if source.startswith(prefix):
                source_targets[f"{workdir}/outputs/{source[len(prefix):]}"] = target
    source_targets.update({name: name for name in files})
    files = _rewrite_markdown_links(files, source_targets)
    broken = broken_markdown_links(files, source_targets)
    if broken:
        detail = ", ".join(f"{row['document']} -> {row['target']}" for row in broken)
        raise ValueError(f"정리본 Markdown 링크가 깨졌습니다: {detail}")

    issues = scan_publish_files(files, internal=internal)
    blocking = [issue for issue in issues if issue["kind"] != "small_cell"]
    if blocking:
        kinds = ", ".join(sorted({issue["kind"] for issue in blocking}))
        raise ValueError(f"게시 전 검사에 실패했습니다: {kinds}")
    return files
