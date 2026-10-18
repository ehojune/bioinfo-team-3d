"""Build and check the publication-oriented repository edition (#686)."""
from __future__ import annotations

import ast
import collections
import csv
import hashlib
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
    r"(?<![\w:.-])(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/)[^\s<>\"'`\]})]+"
)
# String fragments in f-strings may end at the private root (for example ``f"/home/{user}"``).
_PRIVATE_LITERAL_PATH = re.compile(
    r"(?<![\w.-])(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|srv|data|tmp|var|opt)/|~/)[^\s<>\"'`\]})]*"
)
PRIVATE_PATH_PATTERN = _PRIVATE_PATH
_MANUAL_PATH_REVIEW = "# LABHQ 수동 확인 필요: 개인 경로를 안전하게 자동 변환하지 못했습니다."
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
_PORTFOLIO_FOLDERS = ("report/", "run/", "code/", "figures/", "tables/", "studies/")


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


def _step_execution_path(source: str, target: str) -> tuple[str, str, str] | None:
    source_path = PurePosixPath(source)
    if (len(source_path.parts) < 5 or source_path.parts[0] != "steps"
            or source_path.parts[2] != "outputs" or source_path.suffix.lower() not in _CODE_SUFFIXES):
        return None
    relative = source_path.parts[2:]
    target_path = PurePosixPath(target)
    if len(target_path.parts) <= len(relative) or target_path.parts[-len(relative):] != relative:
        return None
    cwd = "/".join(target_path.parts[:-len(relative)])
    if "/code/" not in f"/{cwd}/":
        return None
    return source_path.parts[1], cwd, "/".join(relative)


def _rewrite_reproduce_commands(text: str, source_targets: Mapping[str, str],
                                files: Mapping[str, str | bytes]) -> str:
    """Keep each recorded command in the published equivalent of its original step cwd."""
    folders: dict[str, str] = {}
    for source, target in sorted(source_targets.items()):
        execution = _step_execution_path(source, target)
        if execution is None:
            continue
        step, cwd, within_step = execution
        folders.setdefault(step, cwd)
        path = PurePosixPath(source)
        command = {".py": "python", ".r": "Rscript", ".sh": "bash"}[path.suffix.lower()]
        executables = ("python", "python3", "py") if command == "python" else (command,)
        for executable in executables:
            published = f"cd {cwd} && {executable} {within_step}"
            text = text.replace(f"cd steps/{step} && {executable} {within_step}", published)
            text = text.replace(f"{executable} {source}", published)

    text = text.replace(
        "정리본 루트에서 아래 순서대로 실행합니다.",
        "정리본 루트에서 시작하되, 각 명령은 아래 단계별 실행 폴더로 이동해 실행합니다.",
    )
    if folders and "## 단계별 실행 폴더" not in text:
        section = ["## 단계별 실행 폴더", "",
                   *[f"- `{step}`: `{folder}`" for step, folder in sorted(folders.items())], ""]
        marker = "## 예상 출력"
        text = text.replace(marker, "\n".join(section) + "\n" + marker) if marker in text else (
            text.rstrip() + "\n\n" + "\n".join(section))

    keys = sorted(set(re.findall(
        r"\bLABHQ_INPUT_[A-Z0-9_]+\b",
        "\n".join(body for body in files.values() if isinstance(body, str)),
    )))
    manual = sorted(name for name, body in files.items()
                    if isinstance(body, str) and _MANUAL_PATH_REVIEW in body)
    input_section = ["## 입력 설정", "",
                     "개인 경로는 코드에 넣지 않습니다. 접근 권한이 있는 로컬 경로나 경로 앞부분을 아래 환경 변수에 지정하세요.", "",
                     *([f"- `{key}`" for key in keys] or ["- 별도 로컬 입력 경로 설정 없음"])]
    if manual:
        input_section += ["", "### 수동 확인 필요", "",
                          *[f"- `{name}`: 주석이 붙은 줄의 입력 경로를 확인하세요." for name in manual]]
    replacement = "\n".join(input_section) + "\n\n"
    text, replaced = re.subn(r"(?ms)^## 입력 설정\n.*?(?=^## )", replacement, text)
    if not replaced and (keys or manual):
        text = text.rstrip() + "\n\n" + replacement
    return text


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


def _input_setting(path: str) -> str:
    """A stable, path-free environment key for one local input literal."""
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("//"):
        normalized = normalized.casefold()
    name = normalized.rstrip("/").rsplit("/", 1)[-1]
    key = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() or "PATH"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8].upper()
    return f"LABHQ_INPUT_{key}_{digest}"


def _input_expression(value: str) -> tuple[str, bool]:
    matches = list(_PRIVATE_LITERAL_PATH.finditer(value))
    if not matches:
        return repr(value), False
    # A literal beginning with one local path can contain spaces. It denotes that one input.
    spans = ([(0, len(value), value)] if len(matches) == 1 and matches[0].start() == 0
             else [(match.start(), match.end(), match.group(0)) for match in matches])
    parts: list[str] = []
    last = 0
    for start, end, path in spans:
        if start > last:
            parts.append(repr(value[last:start]))
        parts.append(f'__import__("os").environ["{_input_setting(path)}"]')
        last = end
    if last < len(value):
        parts.append(repr(value[last:]))
    return " + ".join(parts), True


def _joined_input_expression(node: ast.JoinedStr) -> tuple[str | None, bool]:
    parts: list[str] = []
    changed = False
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            expression, converted = _input_expression(value.value)
            parts.append(expression)
            changed = changed or converted
        elif isinstance(value, ast.FormattedValue):
            expression = ast.unparse(ast.JoinedStr(values=[value]))
            if _PRIVATE_LITERAL_PATH.search(expression):
                return None, False
            parts.append(expression)
        else:
            return None, False
    return (" + ".join(parts) if changed else None), changed


def _source_offset(lines: list[str], starts: list[int], lineno: int, byte_column: int) -> int:
    line = lines[lineno - 1]
    column = len(line.encode("utf-8")[:byte_column].decode("utf-8"))
    return starts[lineno - 1] + column


def _manual_path_comments(text: str) -> str:
    lines = text.splitlines(keepends=True)
    rewritten: list[str] = []
    for line in lines:
        if _PRIVATE_LITERAL_PATH.search(line) is None:
            rewritten.append(line)
            continue
        indent = line[:len(line) - len(line.lstrip())]
        ending = "\n" if line.endswith("\n") else ""
        rewritten.append(f"{indent}{_MANUAL_PATH_REVIEW}{ending}")
        rewritten.append(_PRIVATE_LITERAL_PATH.sub("<local-path>", line))
    return "".join(rewritten)


def _python_input_settings(text: str) -> str:
    """Replace private path literals, including raw, joined and f-string fragments."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, TypeError):
        return _manual_path_comments(text)

    replacements: list[tuple[int, int, str]] = []
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)

    def visit(node: ast.AST) -> None:
        replacement: str | None = None
        if isinstance(node, ast.JoinedStr):
            replacement, _changed = _joined_input_expression(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            replacement, changed = _input_expression(node.value)
            if not changed:
                replacement = None
        else:
            for child in ast.iter_child_nodes(node):
                visit(child)
            return
        if replacement is None:
            return
        start = _source_offset(lines, starts, node.lineno, node.col_offset)
        end = _source_offset(lines, starts, node.end_lineno, node.end_col_offset)
        replacements.append((start, end, replacement))

    visit(tree)
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return _manual_path_comments(text)


def _quoted_input_settings(text: str, suffix: str) -> str:
    """Handle full quoted path literals in R and shell scripts."""
    pattern = re.compile(r"(?P<quote>['\"])(?P<value>[^'\"\n]*)(?P=quote)")

    def replace(match: re.Match[str]) -> str:
        value = match.group("value")
        found = _PRIVATE_PATH.match(value)
        if found is None or found.start() != 0:
            return match.group(0)
        key = _input_setting(value)
        return f'Sys.getenv("{key}")' if suffix == ".r" else f'"${{{key}:?set {key}}}"'

    return pattern.sub(replace, text)


def clean_publication_text(name: str, text: str, settings, req: dict) -> str:
    """Clean prose normally; keep code executable by replacing only actual private path literals."""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _CODE_SUFFIXES:
        return _clean(text, settings, req)
    rewritten = (_python_input_settings(text) if suffix == ".py"
                 else _quoted_input_settings(text, suffix))
    rewritten = sanitize(rewritten, settings.policy, [os.environ.get(settings.github.token_env, "")], limit=None)
    rewritten = publish_clean(rewritten, settings, [req], limit=None)
    return _ASSIGNMENT.sub(r"\1<redacted-secret>", rewritten)


def code_syntax_issues(files: Mapping[str, str | bytes]) -> list[dict[str, str]]:
    """Compile every published Python source before the edition can be written."""
    issues = []
    for name, body in files.items():
        if not name.lower().endswith(".py") or not isinstance(body, str):
            continue
        try:
            compile(body, name, "exec")
        except (SyntaxError, ValueError, TypeError):
            issues.append({"kind": "python_syntax", "path": name})
    return issues


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


_VERDICT = re.compile(r"지지(?:되지|하지)\s*않|지지(?:됐|했|된다|한다|합니다|됩니다)|기각")
_ANCHOR = re.compile(r"\s*\[\[\s*claim\s*:[^\[\]\n]*\]\]", re.I)
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


def _study_documents(rows: list[dict[str, str]]) -> dict[str, str]:
    index = ["# 갈래별 정리본", "", "| 갈래 | 질문 | 자료 | 상태 | 주요 결과 | 폴더 |",
             "|---|---|---|---|---|---|"]
    files: dict[str, str] = {}
    for row in rows:
        folder = row["folder"]
        target = f"{folder}/README.md"
        index.append("| {id} | {question} | {data} | {status} | {result} | [열기]({target}) |".format(
            **{key: _safe_cell(value) for key, value in row.items()}, target=target))
        files[f"studies/{folder}/README.md"] = (
            f"# {row['id']} · {row['question']}\n\n"
            f"- 자료: {row['data']}\n"
            f"- 상태: {row['status']}\n\n"
            f"## 주요 결과\n\n{row['result']}\n"
        )
    files["studies/README.md"] = "\n".join(index) + "\n"
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


def _code_target(folder: str, source: str, used: set[str]) -> str:
    """Keep the step's ``outputs/...`` layout below its published execution folder."""
    path = PurePosixPath(posixpath.normpath(source.replace("\\", "/")))
    if len(path.parts) >= 5 and path.parts[0] == "steps" and path.parts[2] == "outputs":
        target = f"{folder}/{path.parts[1]}/{'/'.join(path.parts[2:])}"
        if target not in used:
            used.add(target)
            return target
    return _unique_target(folder, source, used)


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
            omitted_tables: list[str], run_documents: Mapping[str, str],
            branch_rows: list[dict[str, str]] | None = None) -> str:
    title = report.splitlines()[0].lstrip("#").strip() or "연구 결과"
    abstract = "figures/graphical_abstract.svg" if "figures/graphical_abstract.svg" in figure_names else None
    key = next((name for name in figure_names if name != abstract), None)
    lines = [f"# {title}", "", f"**답: {_first_answer(report, req)}**", ""]
    if abstract:
        lines += [f"![그래픽 초록]({abstract})", ""]
    if branch_rows:
        lines += ["| 갈래 | 자료 | 상태 | 결과 | 폴더 |", "|---|---|---|---|---|"]
        lines += [f"| {_safe_cell(row['id'])} | {_safe_cell(row['data'])} | {_safe_cell(row['status'])} | "
                  f"{_safe_cell(row['result'])} | "
                  f"[열기](studies/{row['folder']}/README.md) |" for row in branch_rows]
    else:
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
    if "run/task_record.md" in run_documents:
        lines.append("4. [작업 지시 기록](run/task_record.md) — 단계별 원래 작업 지시")
    lines += ["5. [실행 기록](run/execution_record.md) — 요청, 결정, 비용과 Nextflow 기록",
              "6. [출처](REFERENCES.md) — PMID, DOI, GEO와 URL",
              "7. `code/`, `figures/`, `tables/` — 게시 가능한 코드·SVG·집계표", "",
              "## 저장소 지도", "", "```text", "README.md", "report/", "run/", "code/", "figures/", "tables/",
              *(["studies/               갈래별 코드·그림·표·단계 보고서"] if branch_rows else []),
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
    assets = {name: clean_publication_text(name, body, settings, req) for name, body in assets.items()
              if not _LITERATURE_OUTPUT.search(name.replace("\\", "/"))}
    report = portfolio_report(req, settings, assets)
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
    omitted_tables: list[str] = []
    figure_names: list[str] = []

    for name, body in assets.items():
        path = PurePosixPath(name)
        suffix = path.suffix.lower()
        branch_folder = step_folders.get(_source_step(name, req) or "")
        prefix = f"studies/{branch_folder}/" if branch_folder else ""
        if suffix in _CODE_SUFFIXES and "scripts" in {part.casefold() for part in path.parts}:
            target = _code_target(prefix + "code", name, used)
            files[target] = body
            source_targets[name] = target
            track(name, target)
        elif path.name.casefold() == "requirements.lock.txt":
            target = _unique_target("run/environment", name, used)
            files[target] = body
            source_targets[name] = target
            track(name, target)
        elif branch_folder and suffix == ".md" and path.as_posix() in declared_reports:
            target = _unique_target(prefix + "report", name, used)
            files[target] = body
            source_targets[name] = target
            track(name, target)
        elif path.name in _NEXTFLOW_NAMES and "pipeline_info" in {part.casefold() for part in path.parts}:
            if publish_file_allowed(name, body, internal=source_internal(name), declared=aggregate_tables):
                target = _unique_target(prefix + "run/nextflow", name, used)
                files[target] = body
                source_targets[name] = target
                track(name, target)
        elif suffix in _TABLE_SUFFIXES:
            if not publish_file_allowed(name, body, internal=source_internal(name), declared=aggregate_tables):
                omitted_tables.append(name)
            else:
                target = _unique_target(prefix + "tables", name, used)
                files[target] = body
                source_targets[name] = target
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
        if not branch_folder:
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
    for name in ("run/progress.md", "run/reproduce.md", "run/task_record.md"):
        if name in run_documents:
            files[name] = _clean(run_documents[name], settings, req)
    if branch_rows:
        files.update({name: _clean(body, settings, req) for name, body in _study_documents(branch_rows).items()})
    readme = _plain_korean(_readme(
        req, report, _step_rows(req, assets), figure_names, omitted_tables, run_documents, branch_rows))
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
    if isinstance(files.get("run/reproduce.md"), str):
        files["run/reproduce.md"] = _rewrite_reproduce_commands(
            files["run/reproduce.md"], source_targets, files)
    files = _rewrite_markdown_links(files, source_targets)
    broken = broken_markdown_links(files, source_targets)
    if broken:
        detail = ", ".join(f"{row['document']} -> {row['target']}" for row in broken)
        raise EditionCheckError(f"정리본 Markdown 링크가 깨졌습니다: {detail}")

    issues = code_syntax_issues(files) + scan_publish_files(
        files, internal=tracked_internal if step_scope is not None else internal)
    blocking = [issue for issue in issues if issue["kind"] != "small_cell"]
    if blocking:
        raise check_failure(blocking)
    if internal_files is not None:
        internal_files.update(tracked_internal)
    return files
