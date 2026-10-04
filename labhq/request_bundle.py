"""Portable, per-request copies of final reports and step outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
from contextlib import ExitStack
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.held_dir import HeldDir, NotPlainFolder
from .evidence.audit import locate_workdir


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


def _copy_candidates(folder: HeldDir, roots: set[PurePosixPath]):
    """Yield selected regular files opened through held parent descriptors.

    Each descriptor remains open while its metadata, hash and bytes are consumed. Subfolders are opened relative
    to the held parent and checked against the identity seen in its directory listing, matching the runner walker.
    """
    def walk(current: HeldDir, relative: PurePosixPath):
        entries = sorted(current.entries(1_000_000), key=lambda entry: entry.name)
        folders = []
        for entry in entries:
            rel = relative / entry.name
            if entry.kind == "dir":
                folders.append(entry)
            elif entry.kind == "file" and _selected(rel, roots):
                fd = current.open_read_file(entry.name)
                try:
                    info = os.fstat(fd)
                    if entry.ident is not None and (info.st_dev, info.st_ino) != entry.ident:
                        raise OSError(f"{rel.as_posix()} was replaced after it was listed")
                    with os.fdopen(fd, "rb") as stream:
                        fd = -1
                        yield stream, info, rel
                finally:
                    if fd >= 0:
                        os.close(fd)
        for entry in folders:
            try:
                child = current.child(entry.name, expect=entry.ident)
            except (NotPlainFolder, OSError):
                continue
            with child:
                yield from walk(child, relative / entry.name)

    yield from walk(folder, PurePosixPath())


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


def _readme(req: Mapping[str, Any], steps: list[Mapping[str, Any]], scripts: list[str]) -> str:
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
    script_commands: list[str] = []
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    plan_steps = [step for step in ((req.get("plan") or {}).get("steps") or []) if isinstance(step, Mapping)]
    ordered_ids = [str(step.get("id")) for step in plan_steps if step.get("id") in results]
    ordered_ids += [str(sid) for sid in results if str(sid) not in ordered_ids]
    max_bytes = int(float(settings.runner.bundle_max_file_mb) * 1024 * 1024)
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
                for stream, info, output_rel in _copy_candidates(outputs, _candidate_roots(result)):
                    rel = Path("steps") / step_id / Path(*output_rel.parts)
                    source = workdir / "outputs" / Path(*output_rel.parts)
                    row = {"relative_path": rel.as_posix(), "size": info.st_size, "sha256": "",
                           "step_id": step_id, "original_path": str(source), "status": "copied",
                           "rewritten": 0, "remaining_absolute_paths": "", "rewritten_files": ""}
                    if info.st_size > max_bytes:
                        row["status"] = "not copied: size"
                    else:
                        destination = temp / rel
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        row["sha256"] = _copy_held(stream, info, destination)
                        command = SCRIPT_COMMANDS.get(destination.suffix.casefold())
                        if command and "scripts" in output_rel.parts:
                            script_commands.append(f'{command} "{rel.as_posix()}"')
                    rows.append(row)
        if not found_workdirs:
            raise OSError("단계 작업 폴더를 하나도 찾지 못했습니다")

        report = str(req.get("report") or "")
        appendix = str(req.get("report_appendix") or "")
        replaced = _superseded(req, tasks or {})
        if replaced:
            appendix += ("\n\n## 요청 묶음: 대체됨\n\n" +
                         "\n".join(f"- {item}" for item in replaced))
        (temp / "report.md").write_text(report, encoding="utf-8", newline="\n")
        (temp / "report_appendix.md").write_text(appendix, encoding="utf-8", newline="\n")
        (temp / "README.md").write_text(_readme(req, plan_steps, sorted(set(script_commands))),
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
