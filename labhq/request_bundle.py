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
from collections import Counter
from contextlib import ExitStack, contextmanager
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .adapters.held_dir import HeldDir, NotPlainFolder
from .evidence.audit import locate_workdir
from .ro_crate import FORMAT_MARKER, METADATA_FILE, write_ro_crate


TEXT_SUFFIXES = frozenset({
    ".py", ".r", ".sh", ".md", ".tsv", ".csv", ".json", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".js", ".cjs", ".mjs", ".sql",
})
SCRIPT_COMMANDS = {".r": "Rscript", ".sh": "bash"}
SCRIPT_SUFFIXES = frozenset({".py", ".r", ".sh"})
# Outputs that are prose, not data a script must have produced (the grade does not ask a script for them).
TEXT_ONLY = frozenset({".md"})  # .txt is a common data format (counts, variant lists): PR #431 review
DOCUMENT_SUFFIXES = frozenset({".md"})
MANIFEST_FIELDS = (
    "relative_path", "size", "sha256", "step_id", "original_path", "status",
    "rewritten", "remaining_absolute_paths", "rewritten_files",
)
INPUT_FIELDS = ("step_id", "path", "size", "mtime_ns", "sha256", "skipped", "cached")
MANIFEST_MAX_BYTES = 16 * 1024 * 1024
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_SCRIPT_COMPONENT = re.compile(r"[A-Za-z0-9._-]+\Z")
_WINDOWS_ABS = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/])[^\s<>\"'|]+")
_POSIX_ABS = re.compile(r"(?:^|(?<=[\s=(\[{:>\"'`]))(?P<path>/(?!/|\.\.?/)[^\s<>\"'`|]+)", re.MULTILINE)
REMOTE_RUNNER_NOTE = "runner가 다른 PC라 묶음을 만들지 않음"
LINK_SCRIPT = "link_inputs.py"
# Written into the bundle: scripts read upstream files as inputs/<step id>/..., which the runner linked (#423).
LINK_SCRIPT_BODY = '''"""Link steps/<step>/inputs/<upstream> to steps/<upstream>/outputs (labhq request bundle)."""
import os
import shutil
from pathlib import Path

LINKS = __LINKS__
root = Path(__file__).resolve().parent
for step, upstream_ids in LINKS.items():
    for upstream in upstream_ids:
        target, link = root / "steps" / upstream / "outputs", root / "steps" / step / "inputs" / upstream
        if not target.is_dir() or os.path.lexists(link):
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(os.path.relpath(target, link.parent), link, target_is_directory=True)
        except OSError:
            try:
                import _winapi  # Windows without the symlink privilege: a junction

                _winapi.CreateJunction(str(target), str(link))
            except (ImportError, OSError):
                shutil.copytree(target, link)
        print(f"steps/{step}/inputs/{upstream} -> steps/{upstream}/outputs")
'''
# Status suffix for a file the step wrote without reporting it: copied without a run-time hash, and a miss does not
# make the bundle incomplete ("not copied (unreported): size" does not start with "not copied:").
UNREPORTED_TAG = " (unreported)"


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


def _recorded_outputs(result: Mapping[str, Any]) -> list[tuple[str, PurePosixPath | None, str]]:
    """Runner outputs that have a hash in the same terminal result, preserving output order."""
    hashes = result.get("output_sha256") if isinstance(result.get("output_sha256"), Mapping) else {}
    found: list[tuple[str, PurePosixPath | None, str]] = []
    seen: set[str] = set()
    for raw in result.get("outputs") or []:
        if not isinstance(raw, str) or raw in seen or raw not in hashes:
            continue
        seen.add(raw)
        found.append((raw, _safe_output_path(raw), str(hashes[raw])))
    return found


@contextmanager
def _open_recorded_output(outputs: HeldDir, path: PurePosixPath) -> Iterator[tuple[Any, os.stat_result]]:
    """Open one recorded output by held parents, without listing or following links."""
    with ExitStack() as stack:
        folder = outputs
        for part in path.parts[1:-1]:
            folder = stack.enter_context(folder.child(part))
        fd = folder.open_read_file(path.name)
        try:
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                yield stream, os.fstat(stream.fileno())
        finally:
            if fd >= 0:
                os.close(fd)


def _held_workdir(stack: ExitStack, root: Path,
                  result: Mapping[str, Any]) -> tuple[HeldDir, Path, dict[str, Any]] | tuple[None, str, dict]:
    """Hold one same-host workdir inside the configured root, or return its local lookup failure."""
    workdir, reason = locate_workdir(root, result)
    if workdir is None:
        return None, str(reason), {}
    try:
        held = stack.enter_context(HeldDir.hold(workdir))
        real_root, real_workdir = root.resolve(), workdir.resolve()
        if (real_workdir == real_root or not real_workdir.is_relative_to(real_root)
                or not held.same_as(real_workdir)):
            return None, "runner.workspace_root 밖이거나 바뀐 작업 폴더", {}
        fd = held.open_read_file("manifest.json")
        with os.fdopen(fd, "rb") as source:
            manifest = json.loads(source.read(MANIFEST_MAX_BYTES + 1).decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("host") != platform.node():
            raise RemoteRunnerBundle(REMOTE_RUNNER_NOTE)
        return held, real_workdir, manifest
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


def _path_prefix_pattern(workdir: Any) -> re.Pattern[str]:
    raw = str(workdir)
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", raw) or re.match(r"^[\\/]{2}[^\\/]", raw))
    separator = r"[\\/]+" if windows else "/"
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    prefix = separator.join(re.escape(part) for part in parts)
    if windows and raw.startswith(("//", "\\\\")):
        prefix = r"[\\/]{2}" + prefix
    elif not windows and raw.startswith(("/", "\\")):
        prefix = "/" + prefix
    # Match the rest of a path until a common text delimiter so its separators can be made portable too.
    boundary = r"(?:^|(?<=[\s\"'=\(\[,:]))"
    flags = re.IGNORECASE if windows else 0
    return re.compile(boundary + prefix + separator + r"outputs" + separator +
                      r"(?P<tail>[^\s<>\"'`|]*)", flags)


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
    # Commands run in their step folder. Markdown links remain relative to the document that contains them.
    relative_path = path.relative_to(bundle_root)
    if path.suffix.casefold() in DOCUMENT_SUFFIXES:
        destination = path.parent
    elif len(relative_path.parts) >= 3 and relative_path.parts[0] == "steps":
        destination = bundle_root / "steps" / relative_path.parts[1]
    else:
        destination = bundle_root
    for step_id, workdir in workdirs:
        target = bundle_root / "steps" / step_id / "outputs"
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


def _runner_python_command(result: Mapping[str, Any], tasks: Mapping[str, Any],
                           runner_capabilities: Mapping[str, Any]) -> str | None:
    task = tasks.get(str(result.get("task_id") or ""))
    runner_id = task.get("runner_id") if isinstance(task, Mapping) else None
    capabilities = runner_capabilities.get(str(runner_id)) if runner_id else None
    local = capabilities.get("local_software") if isinstance(capabilities, Mapping) else None
    python = local.get("python") if isinstance(local, Mapping) else None
    command = python.get("command") if isinstance(python, Mapping) else None
    return command if command in {"python3", "python", "py"} else None


def _grade(rows: list[dict[str, Any]], steps: list[Mapping[str, Any]],
           rewrite_by_file: Mapping[str, tuple[int, list[str]]], not_copied: int) -> tuple[str, list[str]]:
    """`replayable` or `documented`, with the reasons for the lower grade (#423, docs/research_protocol.md).

    replayable: every recorded output was copied; a step with data outputs ships a script; a step with scripts has an
    environment record (outputs/env/) of its own or from a step it depends on; no copied script keeps an absolute path.
    `rerun_verified` needs a recorded rerun elsewhere, which nothing writes yet, so it is never given here."""
    reasons = [f"기록 산출 {not_copied}개 미복사"] if not_copied else []
    files: dict[str, list[PurePosixPath]] = {}
    for row in rows:
        if row["status"] in ("copied", f"copied{UNREPORTED_TAG}"):
            parts = PurePosixPath(str(row["relative_path"])).parts  # steps/<step>/outputs/...
            files.setdefault(parts[1], []).append(PurePosixPath(*parts[3:]))
    deps = {str(step.get("id")): [str(dep) for dep in step.get("depends_on") or []] for step in steps}

    def lineage(step_id: str) -> set[str]:
        found, pending = {step_id}, [step_id]
        while pending:
            for dep in deps.get(pending.pop(), []):
                if dep not in found:
                    found.add(dep)
                    pending.append(dep)
        return found

    has_env = {step_id for step_id, paths in files.items() if any(path.parts[0] == "env" for path in paths)}
    no_script, no_env = [], []
    for step_id, paths in sorted(files.items()):
        scripts = [p for p in paths if p.parts[0] == "scripts" and p.suffix.casefold() in SCRIPT_SUFFIXES]
        data = [p for p in paths if p.parts[0] not in ("scripts", "env") and p.suffix.casefold() not in TEXT_ONLY]
        if data and not scripts:
            no_script.append(step_id)
        if scripts and not lineage(step_id) & has_env:
            no_env.append(step_id)
    absolute = sorted(path for path, (_count, left) in rewrite_by_file.items() if left and "/outputs/scripts/" in path)
    if no_script:
        reasons.append("스크립트 없이 데이터 산출만 있는 단계: " + ", ".join(no_script))
    if no_env:
        reasons.append("환경 기록(outputs/env/)이 없는 단계: " + ", ".join(no_env))
    if absolute:
        reasons.append("절대경로가 남은 스크립트: " + ", ".join(absolute))
    return ("documented" if reasons else "replayable"), reasons


def _readme(req: Mapping[str, Any], steps: list[Mapping[str, Any]], scripts: list[str],
            unsafe_scripts: list[str], python_unknown: bool, linked: bool = False) -> str:
    lines = [
        "# 요청 묶음", "", f"- 요청: `{req.get('id')}`", "- `report.md`: PI용 본문",
        "- `report_appendix.md`: 실행 기록과 묶음 변환 기록", "- `steps/<step_id>/`: 단계 workdir 사본",
        "- `MANIFEST.tsv`: 사본의 크기·sha256과 원래 위치",
        "- `INPUTS.tsv`: 단계가 읽을 수 있던 외부 입력의 크기·mtime·sha256 또는 생략 이유", "",
        FORMAT_MARKER, "", "## RO-Crate", "",
        "`ro-crate-metadata.json`은 RO-Crate 1.1·Process Run Crate 0.5 metadata입니다. "
        "INPUTS.tsv는 실제 사용 입력이 아니라 실행 전 입력 영역 scan으로 기록합니다.", "",
        "## 단계 순서", "",
    ]
    if steps:
        for step in steps:
            deps = ", ".join(map(str, step.get("depends_on") or [])) or "없음"
            lines.append(f"- `{step.get('id')}` (의존: {deps})")
    else:
        lines.append("- 선언된 단계 없음")
    lines += ["", "## 다시 실행", "", "아래 명령은 묶음 루트에서 시작해 각 단계 폴더에서 실행합니다.", ""]
    if linked:
        lines.append(f"- 먼저 `python {LINK_SCRIPT}`: 단계 폴더의 `inputs/<앞 단계>`를 `steps/<앞 단계>/outputs`로 잇습니다")
    lines += [f"- `{command}`" for command in scripts] if scripts else ["- 실행 스크립트 없음"]
    lines += [f"- 이름이 안전하지 않아 명령을 만들지 않음: `{path}`" for path in unsafe_scripts]
    if python_unknown:
        lines.append("- runner가 쓴 python 명령을 확인하지 못함: `python`을 사용")
    lines += ["", "묶음은 원본의 사본입니다. `labhq verify`와 claim anchor 검사는 원래 작업 폴더를 기준으로 합니다.", ""]
    return "\n".join(lines)


def build_request_bundle(req: Mapping[str, Any], settings: Any,
                         tasks: Mapping[str, Any] | None = None,
                         runner_capabilities: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
    input_rows: list[dict[str, Any]] = []
    run_records: dict[str, dict[str, Any]] = {}
    workdirs: list[tuple[str, Path]] = []
    script_commands: dict[str, dict[str, str]] = {}
    unsafe_scripts: dict[str, set[str]] = {}
    python_unknown = False
    tasks = tasks or {}
    runner_capabilities = runner_capabilities or {}
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    plan_steps = [step for step in ((req.get("plan") or {}).get("steps") or []) if isinstance(step, Mapping)]
    ordered_ids, ordered_steps = _topological_steps(plan_steps, results)
    max_file_bytes = int(float(settings.runner.bundle_max_file_mb) * 1024 * 1024)
    max_total_bytes = int(float(settings.runner.bundle_max_total_mb) * 1024 * 1024)
    max_total_files = int(settings.runner.bundle_max_files)
    copied_bytes = 0
    copied_files = 0
    total_limit_hit = False
    unreported: list[tuple[str, HeldDir, Path, PurePosixPath, str | None]] = []

    def copy_output(step_id: str, outputs: HeldDir, workdir: Path, path: PurePosixPath,
                    expected_sha256: str | None, python_command: str | None) -> dict[str, Any]:
        """Copy one output. ``expected_sha256`` None: an unreported file, copied as it is now (no run hash)."""
        nonlocal copied_bytes, copied_files, total_limit_hit, python_unknown
        tag = "" if expected_sha256 is not None else UNREPORTED_TAG
        output_rel = PurePosixPath(*path.parts[1:])
        rel = PurePosixPath("steps", step_id, *path.parts)
        source = workdir / Path(*path.parts)
        row = {"relative_path": rel.as_posix(), "size": "", "sha256": "",
               "step_id": step_id, "original_path": str(source), "status": f"copied{tag}",
               "rewritten": 0, "remaining_absolute_paths": "", "rewritten_files": ""}
        destination = temp / Path(*rel.parts)
        try:
            with _open_recorded_output(outputs, path) as (stream, info):
                row["size"] = info.st_size
                if info.st_size > max_file_bytes:
                    row["status"] = f"not copied{tag}: size"
                elif copied_files >= max_total_files or copied_bytes + info.st_size > max_total_bytes:
                    row["status"] = f"not copied{tag}: total limit"
                    total_limit_hit = total_limit_hit or not tag  # its appendix note counts recorded outputs
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        source_sha256 = _copy_held(stream, info, destination)
                    except OSError:
                        destination.unlink(missing_ok=True)
                        row["status"] = f"not copied{tag}: changed since run"
                        return row
                    if expected_sha256 is not None and source_sha256.casefold() != expected_sha256.casefold():
                        destination.unlink(missing_ok=True)
                        row["status"] = "not copied: changed since run"
                        return row
                    copied_files += 1
                    copied_bytes += info.st_size
                    suffix = destination.suffix.casefold()
                    command = python_command if suffix == ".py" else SCRIPT_COMMANDS.get(suffix)
                    if suffix == ".py" and command is None:
                        command = "python"
                        python_unknown = True
                    if command and "scripts" in output_rel.parts:
                        if _safe_script_path(rel):
                            script_path = PurePosixPath("outputs", *output_rel.parts)
                            script_commands.setdefault(step_id, {})[rel.as_posix()] = shlex.join(
                                ("cd", f"steps/{step_id}")) + " && " + shlex.join((command, script_path.as_posix()))
                        else:
                            unsafe_scripts.setdefault(step_id, set()).add(rel.as_posix())
        except FileNotFoundError:
            row["status"] = f"not copied{tag}: missing"
        except OSError:
            row["status"] = f"not copied{tag}: changed since run"
        return row

    try:
        found_workdirs = 0
        with ExitStack() as stack:
            for step_id in ordered_ids:
                if not _SAFE_ID.fullmatch(step_id):
                    raise ValueError(f"unsafe step id for bundle: {step_id!r}")
                result = results.get(step_id)
                if not isinstance(result, Mapping):
                    continue
                recorded = _recorded_outputs(result)
                held, workdir_or_reason, manifest = _held_workdir(stack, root, result)
                if held is None:
                    for index, (raw, safe, _expected) in enumerate(recorded, 1):
                        relative = (PurePosixPath("steps", step_id, *safe.parts).as_posix() if safe is not None
                                    else f"steps/{step_id}/<invalid-output-{index}>")
                        rows.append({"relative_path": relative, "size": "", "sha256": "",
                                     "step_id": step_id, "original_path": str(result.get("workdir") or raw),
                                     "status": "not copied: missing", "rewritten": 0,
                                     "remaining_absolute_paths": "", "rewritten_files": ""})
                    continue
                workdir = Path(workdir_or_reason)
                found_workdirs += 1
                workdirs.append((step_id, workdir))
                task_id = str(result.get("task_id") or "")
                run = (manifest.get("runs") or {}).get(task_id) if task_id else None
                run = run if isinstance(run, Mapping) else {}
                run_records[step_id] = {
                    key: value for key, value in {
                        "agent_id": result.get("agent_id"),
                        "engine": manifest.get("engine"),
                        "model": manifest.get("model"),
                        "engine_cli_version": run.get("engine_cli_version"),
                        "started_at": run.get("started_at"),
                        "ended_at": run.get("ended_at"),
                    }.items() if value not in (None, "")
                }
                records = run.get("input_files") if isinstance(run, Mapping) else None
                for record in records or []:
                    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                        continue
                    input_path = record["path"]
                    if input_path == "inputs" or input_path.startswith("inputs/"):
                        input_path = PurePosixPath("steps", step_id, input_path).as_posix()
                    input_rows.append({
                        "step_id": step_id, "path": input_path,
                        "size": "" if record.get("size") is None else record.get("size"),
                        "mtime_ns": "" if record.get("mtime_ns") is None else record.get("mtime_ns"),
                        "sha256": record.get("sha256") or "", "skipped": record.get("skipped") or "",
                        "cached": "yes" if record.get("cached") is True else "",
                    })
                try:
                    outputs = stack.enter_context(held.child("outputs"))
                except (NotPlainFolder, OSError):
                    for index, (raw, safe, _expected) in enumerate(recorded, 1):
                        relative = (PurePosixPath("steps", step_id, *safe.parts).as_posix() if safe is not None
                                    else f"steps/{step_id}/<invalid-output-{index}>")
                        source = workdir / Path(*safe.parts) if safe is not None else workdir / raw
                        rows.append({"relative_path": relative, "size": "", "sha256": "",
                                     "step_id": step_id, "original_path": str(source),
                                     "status": "not copied: missing", "rewritten": 0,
                                     "remaining_absolute_paths": "", "rewritten_files": ""})
                    continue
                python_command = _runner_python_command(result, tasks, runner_capabilities)
                for index, (raw, path, expected_sha256) in enumerate(recorded, 1):
                    if path is None:
                        rows.append({"relative_path": f"steps/{step_id}/<invalid-output-{index}>",
                                     "size": "", "sha256": "", "step_id": step_id,
                                     "original_path": raw, "status": "not copied: unsafe path", "rewritten": 0,
                                     "remaining_absolute_paths": "", "rewritten_files": ""})
                        continue
                    rows.append(copy_output(step_id, outputs, workdir, path, expected_sha256, python_command))
                listed = {raw for raw, _path, _sha in recorded}
                unreported += [(step_id, outputs, workdir, path, python_command)
                               for raw in result.get("unreported_outputs") or []
                               if isinstance(raw, str) and raw not in listed
                               and (path := _safe_output_path(raw)) is not None]
            # Files a step wrote without reporting them (a gene-set copy, a figure) can be what its scripts read
            # (#423). They are copied after every recorded output, so they never push one past the total limit.
            for step_id, outputs, workdir, path, python_command in unreported:
                rows.append(copy_output(step_id, outputs, workdir, path, None, python_command))
        if not found_workdirs:
            raise OSError("단계 작업 폴더를 하나도 찾지 못했습니다")

        with (temp / "INPUTS.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(input_rows)
        input_hashed = sum(bool(row["sha256"]) for row in input_rows)
        input_skipped = sum(bool(row["skipped"]) for row in input_rows)
        skipped_by_reason = Counter(str(row["skipped"]) for row in input_rows if row["skipped"])
        input_summary = f"입력 {input_hashed}개 hash, {input_skipped}개 생략"
        if skipped_by_reason:
            input_summary += " (" + ", ".join(
                f"{reason} {count}" for reason, count in sorted(skipped_by_reason.items())) + ")"

        report = str(req.get("report") or "")
        appendix = str(req.get("report_appendix") or "")
        replaced = _superseded(req, tasks)
        if replaced:
            appendix += ("\n\n## 요청 묶음: 대체됨\n\n" +
                         "\n".join(f"- {item}" for item in replaced))
        if total_limit_hit:
            appendix += ("\n\n- 누적 상한으로 기록 산출 일부를 복사하지 않음 "
                         f"(복사 {copied_files}개, {copied_bytes} bytes; "
                         f"상한 {max_total_files}개, {max_total_bytes} bytes)")
        not_copied = sum(str(row["status"]).startswith("not copied:") for row in rows)
        bundle_status = "incomplete" if not_copied else "complete"
        unreported_missed = sum(str(row["status"]).startswith(f"not copied{UNREPORTED_TAG}:") for row in rows)
        if unreported_missed:
            appendix += f"\n\n- 보고하지 않은 산출 {unreported_missed}개 미복사(크기·누적 상한 등; MANIFEST.tsv 확인)"
        if not_copied:
            appendix += f"\n\n- 요청 묶음 상태: incomplete (기록 산출 {not_copied}개 미복사; MANIFEST.tsv 확인)"
        (temp / "report.md").write_text(report, encoding="utf-8", newline="\n")
        (temp / "report_appendix.md").write_text(appendix, encoding="utf-8", newline="\n")
        commands = [command for step_id in ordered_ids
                    for _path, command in sorted(script_commands.get(step_id, {}).items())]
        unsafe = [path for step_id in ordered_ids for path in sorted(unsafe_scripts.get(step_id, set()))]
        bundled = {step_id for step_id, _workdir in workdirs}
        links = {str(step.get("id")): deps for step in ordered_steps
                 if str(step.get("id")) in bundled
                 and (deps := [str(dep) for dep in step.get("depends_on") or [] if str(dep) in bundled])}
        if links:
            (temp / LINK_SCRIPT).write_text(LINK_SCRIPT_BODY.replace("__LINKS__", json.dumps(links, sort_keys=True)),
                                            encoding="utf-8", newline="\n")
        (temp / "README.md").write_text(_readme(req, ordered_steps, commands, unsafe, python_unknown, bool(links)),
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

        grade, grade_reasons = _grade(rows, ordered_steps, rewrite_by_file, not_copied)
        with (temp / "README.md").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(["## 재현 등급", "", f"- `{grade}`", *(f"- {reason}" for reason in grade_reasons),
                                    f"- {input_summary}",
                                    "- `replayable`: 기록 산출이 모두 있고, 데이터를 낸 단계마다 스크립트, 스크립트를 쓴 "
                                    "단계마다 환경 기록이 있으며 스크립트에 절대경로가 없음",
                                    "- `rerun_verified`는 다른 곳에서 다시 돌린 기록이 있을 때만 줍니다. 아직 그런 기록은 "
                                    "없습니다.", ""]))
        bundle_note = ["", "## 요청 묶음 변환", "", f"- 절대경로를 바꾼 파일: {rewritten_files}개",
                       f"- 재현 등급: {grade}" + (f" ({'; '.join(grade_reasons)})" if grade_reasons else "")]
        bundle_note.append(f"- {input_summary}")
        bundle_note.append("- 남은 절대경로: " + (", ".join(sorted(remaining)) if remaining else "없음"))
        if replaced:
            bundle_note.append(f"- 대체되어 제외한 판: {len(replaced)}개")
        appendix_path = temp / "report_appendix.md"
        with appendix_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(bundle_note) + "\n")

        generated = [temp / "README.md", temp / "report.md", appendix_path, temp / "INPUTS.tsv",
                     *([temp / LINK_SCRIPT] if (temp / LINK_SCRIPT).is_file() else [])]
        for path in generated:
            relative = path.relative_to(temp).as_posix()
            count, paths = rewrite_by_file.get(relative, (0, []))
            rows.append({"relative_path": relative, "size": path.stat().st_size, "sha256": _sha256(path),
                         "step_id": "", "original_path": "", "status": "generated", "rewritten": count,
                         "remaining_absolute_paths": " | ".join(paths), "rewritten_files": ""})
        for row in rows:
            if row["status"] in ("copied", f"copied{UNREPORTED_TAG}"):
                copied = temp / str(row["relative_path"])
                count, paths = rewrite_by_file.get(str(row["relative_path"]), (0, []))
                row.update(size=copied.stat().st_size, sha256=_sha256(copied), rewritten=count,
                           remaining_absolute_paths=" | ".join(paths))
        crate_warning = None
        try:
            crate_path = temp / METADATA_FILE
            write_ro_crate(crate_path, req, rows, input_rows, run_records)
            rows.append({"relative_path": METADATA_FILE, "size": crate_path.stat().st_size,
                         "sha256": _sha256(crate_path), "step_id": "", "original_path": "",
                         "status": "generated", "rewritten": 0, "remaining_absolute_paths": "",
                         "rewritten_files": ""})
        except Exception as exc:
            crate_warning = f"RO-Crate metadata를 만들지 못했습니다: {exc}"
            for path in (temp / "README.md", appendix_path):
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(f"\n- {crate_warning}\n")
                relative = path.relative_to(temp).as_posix()
                row = next(item for item in rows if item["relative_path"] == relative)
                row.update(size=path.stat().st_size, sha256=_sha256(path))
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
        return {"path": str(target), "status": bundle_status, "grade": grade, "grade_reasons": grade_reasons,
                "not_copied": not_copied,
                "rewritten_files": rewritten_files,
                "remaining_absolute_paths": sorted(remaining), "superseded": replaced,
                **({"crate_warning": crate_warning} if crate_warning else {})}
    except Exception:
        if temp.exists():
            try:
                _clear_owned_dir(temp, requests_root)
            except OSError:
                pass
        raise
