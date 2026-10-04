"""Portable, per-request copies of final reports and step outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
from contextlib import ExitStack
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.held_dir import HeldDir, NotPlainFolder
from .evidence.audit import locate_workdir
from .runner.workspace import restricted_zones, walk_output_files


TEXT_SUFFIXES = frozenset({
    ".py", ".r", ".sh", ".md", ".tsv", ".csv", ".json", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".js", ".cjs", ".mjs", ".sql",
})
SCRIPT_COMMANDS = {".py": "python", ".r": "Rscript", ".sh": "bash"}
DOCUMENT_SUFFIXES = frozenset({".md"})
MANIFEST_FIELDS = (
    "relative_path", "size", "sha256", "step_id", "original_path", "status",
    "rewritten", "remaining_absolute_paths", "rewritten_files",
)
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_SCRIPT_COMPONENT = re.compile(r"[A-Za-z0-9._-]+\Z")
_WINDOWS_ABS = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/])[^\s<>\"'|]+")
_POSIX_ABS = re.compile(r"(?:^|(?<=[\s=(\[{:>\"'`]))(?P<path>/(?!/|\.\.?/)[^\s<>\"'`|]+)", re.MULTILINE)
REMOTE_RUNNER_NOTE = "runner가 다른 PC라 묶음을 만들지 않음"


class RemoteRunnerBundle(OSError):
    """The terminal result names a runner workspace that is not on the gateway host."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _absolute_paths(text: str) -> list[str]:
    return sorted(set(_WINDOWS_ABS.findall(text)) |
                  {match.group("path") for match in _POSIX_ABS.finditer(text)})


def _safe_output_path(raw: str) -> PurePosixPath | None:
    value = str(raw).replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "outputs":
        return None
    if any(part in ("", ".", "..") for part in path.parts):
        return None
    return path


def _clear_owned_dir(path: Path, parent: Path) -> None:
    """Remove only the fixed plain directory directly below the resolved request-bundle parent."""
    from .adapters.owned import is_link

    if not path.exists() and not path.is_symlink():
        return
    if is_link(path) or not path.is_dir() or path.resolve().parent != parent.resolve():
        raise OSError(f"request bundle target is not a plain child directory: {path}")
    shutil.rmtree(path)


def _candidate_roots(result: Mapping[str, Any]) -> set[PurePosixPath]:
    """Declared paths below outputs, plus the reproducibility scripts folder."""
    roots: set[PurePosixPath] = set()
    for raw in result.get("outputs") or []:
        if isinstance(raw, str) and (safe := _safe_output_path(raw)) is not None:
            roots.add(PurePosixPath(*safe.parts[1:]))
    roots.add(PurePosixPath("scripts"))
    return roots


def _selected(path: PurePosixPath, roots: set[PurePosixPath]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _held_workdir(stack: ExitStack, root: Path, result: Mapping[str, Any]) -> tuple[HeldDir, Path] | tuple[None, str]:
    """Hold one same-host workdir inside the configured root, or return its local lookup failure."""
    workdir, reason = locate_workdir(root, result)
    if workdir is None:
        return None, str(reason)
    try:
        held = stack.enter_context(HeldDir.hold(workdir))
        real_root, real_workdir = root.resolve(), workdir.resolve()
        if (real_workdir == real_root or not real_workdir.is_relative_to(real_root)
                or not held.same_as(real_workdir)):
            return None, "runner.workspace_root 밖이거나 바뀐 작업 폴더"
        fd = held.open_read_file("manifest.json")
        with os.fdopen(fd, "rb") as source:
            manifest = json.loads(source.read(1024 * 1024 + 1).decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("host") != platform.node():
            raise RemoteRunnerBundle(REMOTE_RUNNER_NOTE)
        return held, real_workdir
    except RemoteRunnerBundle:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise RemoteRunnerBundle(REMOTE_RUNNER_NOTE) from exc


def _copy_held(stream, info: os.stat_result, destination: Path) -> str:
    """Copy and hash the already-open source, rejecting a file that changes during the read."""
    digest = hashlib.sha256()
    read = 0
    with open(destination, "wb") as target:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            target.write(block)
            read += len(block)
    after = os.fstat(stream.fileno())
    if read != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
        destination.unlink(missing_ok=True)
        raise OSError("source changed while request bundle copied it")
    return digest.hexdigest()


def _path_prefix_pattern(workdir: Path) -> re.Pattern[str]:
    parts = [part for part in re.split(r"[\\/]+", str(workdir)) if part]
    prefix = r"[\\/]+".join(re.escape(part) for part in parts)
    if str(workdir).startswith(("/", "\\")):
        prefix = r"[\\/]+" + prefix
    # Match the rest of a path until a common text delimiter so its separators can be made portable too.
    return re.compile(prefix + r"[\\/]+outputs[\\/]+(?P<tail>[^\s<>\"'`|]*)", re.IGNORECASE)


def _rewrite_text(path: Path, workdirs: list[tuple[str, Path]]) -> tuple[int, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return 0, []
    original = text
    replacements = 0
    bundle_root = next((parent for parent in path.parents if (parent / "steps").is_dir()), None)
    if bundle_root is None:
        return 0, _absolute_paths(text)
    # Commands run at the bundle root. Markdown links remain relative to the document that contains them.
    destination = path.parent if path.suffix.casefold() in DOCUMENT_SUFFIXES else bundle_root
    for step_id, workdir in workdirs:
        target = bundle_root / "steps" / step_id
        relative = os.path.relpath(target, destination).replace("\\", "/")

        def replace(match: re.Match[str]) -> str:
            nonlocal replacements
            replacements += 1
            tail = match.group("tail").replace("\\", "/")
            return f"{relative}/{tail}"

        text = _path_prefix_pattern(workdir).sub(replace, text)
    if text != original:
        path.write_text(text, encoding="utf-8", newline="\n")
    remaining = _absolute_paths(text)
    return replacements, remaining


def _superseded(req: Mapping[str, Any], tasks: Mapping[str, Any]) -> list[str]:
    rid = req.get("id")
    current = {str(sid): str((result or {}).get("task_id") or "")
               for sid, result in (req.get("results") or {}).items() if isinstance(result, Mapping)}
    rows: set[str] = set()
    for entry in tasks.values():
        if not isinstance(entry, Mapping) or entry.get("request_id") != rid or not entry.get("completed"):
            continue
        result = entry.get("result") if isinstance(entry.get("result"), Mapping) else {}
        sid = str(entry.get("step_id") or entry.get("kind") or "")
        if sid in current and str(result.get("task_id") or "") != current[sid] and result.get("outputs"):
            rows.add(f"{sid}: {result.get('workdir_id') or 'unknown-workdir'} ({', '.join(map(str, result['outputs']))})")
    for history in req.get("replan_history") or []:
        if not isinstance(history, Mapping):
            continue
        prior = history.get("prior_results") if isinstance(history.get("prior_results"), Mapping) else {}
        for sid, result in prior.items():
            if isinstance(result, Mapping):
                rows.add(f"{sid}: {result.get('workdir_id') or 'unknown-workdir'} "
                         f"({', '.join(map(str, result.get('outputs') or [])) or 'outputs 없음'})")
    return sorted(rows)


def _topological_steps(steps: list[Mapping[str, Any]], results: Mapping[str, Any]) -> tuple[list[str], list[Mapping[str, Any]]]:
    """Return result step ids by DAG depth then id, with undeclared/direct results last."""
    by_id = {str(step.get("id")): step for step in steps if step.get("id") is not None}
    remaining = {step_id for step_id in by_id if step_id in results}
    ordered: list[str] = []
    completed: set[str] = set()
    while remaining:
        layer = sorted(step_id for step_id in remaining
                       if {str(dep) for dep in by_id[step_id].get("depends_on") or []
                           if str(dep) in by_id and str(dep) in results}
                       <= completed)
        if not layer:  # Invalid/cyclic stored plans remain deterministic; validation belongs to plan intake.
            layer = sorted(remaining)
        ordered.extend(layer)
        completed.update(layer)
        remaining.difference_update(layer)
    ordered.extend(sorted(str(step_id) for step_id in results if str(step_id) not in ordered))
    return ordered, [by_id[step_id] for step_id in ordered if step_id in by_id]


def _safe_script_path(path: PurePosixPath) -> bool:
    return all(_SAFE_SCRIPT_COMPONENT.fullmatch(part) for part in path.parts)


def _readme(req: Mapping[str, Any], steps: list[Mapping[str, Any]], scripts: list[str],
            unsafe_scripts: list[str]) -> str:
    lines = [
        "# 요청 묶음", "", f"- 요청: `{req.get('id')}`", "- `report.md`: PI용 본문",
        "- `report_appendix.md`: 실행 기록과 묶음 변환 기록", "- `steps/<step_id>/`: 최종 단계 산출물과 스크립트",
        "- `MANIFEST.tsv`: 사본의 크기·sha256과 원래 위치", "",
        "## 단계 순서", "",
    ]
    if steps:
        for step in steps:
            deps = ", ".join(map(str, step.get("depends_on") or [])) or "없음"
            lines.append(f"- `{step.get('id')}` (의존: {deps})")
    else:
        lines.append("- 선언된 단계 없음")
    lines += ["", "## 다시 실행", "", "아래 명령은 이 묶음의 루트에서 실행합니다.", ""]
    lines += [f"- `{command}`" for command in scripts] if scripts else ["- 실행 스크립트 없음"]
    lines += [f"- 이름이 안전하지 않아 명령을 만들지 않음: `{path}`" for path in unsafe_scripts]
    lines += ["", "묶음은 원본의 사본입니다. `labhq verify`와 claim anchor 검사는 원래 작업 폴더를 기준으로 합니다.", ""]
    return "\n".join(lines)


def build_request_bundle(req: Mapping[str, Any], settings: Any,
                         tasks: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build ``<workspace_root>/requests/<request_id>`` from the request's final stored results."""
    rid = str(req.get("id") or "")
    if not _SAFE_ID.fullmatch(rid):
        raise ValueError("unsafe request id for bundle")
    root = settings.path(settings.runner.workspace_root).resolve()
    requests_root = root / "requests"
    requests_root.mkdir(parents=True, exist_ok=True)
    from .adapters.owned import is_link
    if is_link(requests_root) or requests_root.resolve().parent != root:
        raise OSError("request bundle parent is not a plain workspace directory")
    target = requests_root / rid
    temp = requests_root / f".{rid}.tmp"
    _clear_owned_dir(temp, requests_root)
    temp.mkdir()
    rows: list[dict[str, Any]] = []
    workdirs: list[tuple[str, Path]] = []
    script_commands: dict[str, dict[str, str]] = {}
    unsafe_scripts: dict[str, set[str]] = {}
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    plan_steps = [step for step in ((req.get("plan") or {}).get("steps") or []) if isinstance(step, Mapping)]
    ordered_ids, ordered_steps = _topological_steps(plan_steps, results)
    max_file_bytes = int(float(settings.runner.bundle_max_file_mb) * 1024 * 1024)
    max_total_bytes = int(float(settings.runner.bundle_max_total_mb) * 1024 * 1024)
    max_total_files = int(settings.runner.bundle_max_files)
    copied_bytes = 0
    copied_files = 0
    total_limit_hit = False
    zones = restricted_zones(settings)
    try:
        found_workdirs = 0
        with ExitStack() as stack:
            for step_id in ordered_ids:
                if not _SAFE_ID.fullmatch(step_id):
                    raise ValueError(f"unsafe step id for bundle: {step_id!r}")
                result = results.get(step_id)
                if not isinstance(result, Mapping):
                    continue
                held, workdir_or_reason = _held_workdir(stack, root, result)
                if held is None:
                    rows.append({"relative_path": f"steps/{step_id}", "size": "", "sha256": "",
                                 "step_id": step_id, "original_path": str(result.get("workdir") or ""),
                                 "status": f"not copied: {workdir_or_reason}", "rewritten": 0,
                                 "remaining_absolute_paths": "", "rewritten_files": ""})
                    continue
                workdir = Path(workdir_or_reason)
                found_workdirs += 1
                workdirs.append((step_id, workdir))
                try:
                    outputs = stack.enter_context(held.child("outputs"))
                except (NotPlainFolder, OSError):
                    rows.append({"relative_path": f"steps/{step_id}", "size": "", "sha256": "",
                                 "step_id": step_id, "original_path": str(workdir / "outputs"),
                                 "status": "not copied: outputs 폴더 없음", "rewritten": 0,
                                 "remaining_absolute_paths": "", "rewritten_files": ""})
                    continue
                roots = _candidate_roots(result)

                def selected(path: PurePosixPath, _entry: Any, _depth: int) -> bool:
                    output_rel = PurePosixPath(*path.parts[1:])
                    return _selected(output_rel, roots)

                def copy_file(path: PurePosixPath, _entry: Any, stream: Any,
                              info: os.stat_result | None) -> str | None:
                    nonlocal copied_bytes, copied_files, total_limit_hit
                    assert stream is not None and info is not None
                    output_rel = PurePosixPath(*path.parts[1:])
                    rel = PurePosixPath("steps", step_id, *output_rel.parts)
                    source = workdir / "outputs" / Path(*output_rel.parts)
                    row = {"relative_path": rel.as_posix(), "size": info.st_size, "sha256": "",
                           "step_id": step_id, "original_path": str(source), "status": "copied",
                           "rewritten": 0, "remaining_absolute_paths": "", "rewritten_files": ""}
                    if info.st_size > max_file_bytes:
                        row["status"] = "not copied: size"
                    elif copied_files >= max_total_files or copied_bytes + info.st_size > max_total_bytes:
                        row["status"] = "not copied: total limit"
                        total_limit_hit = True
                    else:
                        destination = temp / Path(*rel.parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        row["sha256"] = _copy_held(stream, info, destination)
                        copied_files += 1
                        copied_bytes += info.st_size
                        command = SCRIPT_COMMANDS.get(destination.suffix.casefold())
                        if command and "scripts" in output_rel.parts:
                            if _safe_script_path(rel):
                                script_commands.setdefault(step_id, {})[rel.as_posix()] = shlex.join(
                                    (command, rel.as_posix()))
                            else:
                                unsafe_scripts.setdefault(step_id, set()).add(rel.as_posix())
                    rows.append(row)
                    return "request bundle total limit" if total_limit_hit else None

                real_outputs = (workdir / "outputs").resolve()
                walk_output_files(
                    outputs, workdir / "outputs", real_outputs, zones,
                    settings.runner.reference_scan_max_entries, settings.runner.reference_scan_max_depth,
                    None, detailed=True, selected=selected, visit_file=copy_file, open_files=True)
                if total_limit_hit:
                    break
        if not found_workdirs:
            raise OSError("단계 작업 폴더를 하나도 찾지 못했습니다")

        report = str(req.get("report") or "")
        appendix = str(req.get("report_appendix") or "")
        replaced = _superseded(req, tasks or {})
        if replaced:
            appendix += ("\n\n## 요청 묶음: 대체됨\n\n" +
                         "\n".join(f"- {item}" for item in replaced))
        if total_limit_hit:
            appendix += ("\n\n- 누적 상한에 도달해 첫 제외 파일에서 순회를 멈춤 "
                         f"(복사 {copied_files}개, {copied_bytes} bytes; "
                         f"상한 {max_total_files}개, {max_total_bytes} bytes)")
        (temp / "report.md").write_text(report, encoding="utf-8", newline="\n")
        (temp / "report_appendix.md").write_text(appendix, encoding="utf-8", newline="\n")
        commands = [command for step_id in ordered_ids
                    for _path, command in sorted(script_commands.get(step_id, {}).items())]
        unsafe = [path for step_id in ordered_ids for path in sorted(unsafe_scripts.get(step_id, set()))]
        (temp / "README.md").write_text(_readme(req, ordered_steps, commands, unsafe),
                                          encoding="utf-8", newline="\n")

        rewritten_files = 0
        remaining: set[str] = set()
        rewrite_by_file: dict[str, tuple[int, list[str]]] = {}
        for path in sorted(p for p in temp.rglob("*") if p.is_file() and p.suffix.casefold() in TEXT_SUFFIXES):
            count, paths = _rewrite_text(path, workdirs)
            if count:
                rewritten_files += 1
            relative = path.relative_to(temp).as_posix()
            rewrite_by_file[relative] = (count, paths)
            remaining.update(paths)

        bundle_note = ["", "## 요청 묶음 변환", "", f"- 절대경로를 바꾼 파일: {rewritten_files}개"]
        bundle_note.append("- 남은 절대경로: " + (", ".join(sorted(remaining)) if remaining else "없음"))
        if replaced:
            bundle_note.append(f"- 대체되어 제외한 판: {len(replaced)}개")
        appendix_path = temp / "report_appendix.md"
        with appendix_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(bundle_note) + "\n")

        generated = [temp / "README.md", temp / "report.md", appendix_path]
        for path in generated:
            relative = path.relative_to(temp).as_posix()
            count, paths = rewrite_by_file.get(relative, (0, []))
            rows.append({"relative_path": relative, "size": path.stat().st_size, "sha256": _sha256(path),
                         "step_id": "", "original_path": "", "status": "generated", "rewritten": count,
                         "remaining_absolute_paths": " | ".join(paths), "rewritten_files": ""})
        for row in rows:
            if row["status"] == "copied":
                copied = temp / str(row["relative_path"])
                count, paths = rewrite_by_file.get(str(row["relative_path"]), (0, []))
                row.update(size=copied.stat().st_size, sha256=_sha256(copied), rewritten=count,
                           remaining_absolute_paths=" | ".join(paths))
        rows.append({"relative_path": ".", "size": "", "sha256": "", "step_id": "",
                     "original_path": "", "status": "summary", "rewritten": "",
                     "remaining_absolute_paths": " | ".join(sorted(remaining)),
                     "rewritten_files": rewritten_files})
        with (temp / "MANIFEST.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

        _clear_owned_dir(target, requests_root)
        temp.replace(target)
        return {"path": str(target), "rewritten_files": rewritten_files,
                "remaining_absolute_paths": sorted(remaining), "superseded": replaced}
    except Exception:
        if temp.exists():
            try:
                _clear_owned_dir(temp, requests_root)
            except OSError:
                pass
        raise
