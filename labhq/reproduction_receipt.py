"""Evidence for published source, never implicit permission to rerun an analysis.

Only source/lock text supplied by the caller is staged. Syntax checks do not evaluate
the source. ``rerun=True`` is for explicitly authorized inputs/resources; the temporary
directory and minimal environment are isolation from user state, not an OS sandbox.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

LANGUAGES = {".py": "python", ".r": "r", ".sh": "bash"}
LOCK_NAMES = frozenset({"requirements.lock.txt", "renv.lock", "uv.lock", "poetry.lock", "pipfile.lock",
                        "environment.lock.yml", "environment.lock.yaml"})
RECEIPT_PATH = "run/reproduction_receipt.json"
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PY_COMPILE = "import pathlib,sys; compile(pathlib.Path(sys.argv[1]).read_bytes(), sys.argv[1], 'exec')"
_R_PARSE = "parse(file=commandArgs(TRUE)[1], keep.source=FALSE)"
_SYNTAX = {"pass", "fail", "unavailable", "timeout", "blocked_path", "not_applicable"}
_EXECUTION = {"pass", "fail", "not_rerun", "blocked_input", "blocked_syntax", "missing_output", "timeout", "unavailable",
              "not_applicable"}


def is_lock_file(name: str) -> bool:
    base = PurePosixPath(name).name.casefold()
    return any(base == lock or base.endswith("-" + lock) for lock in LOCK_NAMES)


def _path(value: str) -> str | None:
    # Logical publication-relative paths only. Never expand an operator's path or open an input.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./ -]+", value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"..", ".labhq"} for part in path.parts):
        return None
    return path.as_posix() if path.parts and path.as_posix() != "." else None


def _hash(body: str | bytes) -> str:
    return hashlib.sha256(body.encode("utf-8") if isinstance(body, str) else body).hexdigest()


def input_variables(source: str, language: str) -> list[str]:
    names = set(re.findall(r"\bLABHQ_INPUT_[A-Z0-9_]+\b", source))
    if language == "python":
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            tree = ast.Module(body=[], type_ignores=[])
        for node in ast.walk(tree):
            key = None
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) in {"os.environ", "environ"}:
                key = node.slice
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in {
                    "os.getenv", "getenv", "os.environ.get", "environ.get"} and node.args:
                key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    elif language == "r":
        names.update(re.findall(r"Sys\.getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", source))
    else:
        names.update(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", source))
        assigned = set(re.findall(r"(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", source))
        names -= assigned - {name for name in names if name.startswith("LABHQ_INPUT_")}
    return sorted(name for name in names if _VARIABLE.fullmatch(name))


def _location(name: str) -> tuple[str, str, str]:
    parts = PurePosixPath(name).parts
    for index, part in enumerate(parts[:-1]):
        if part in {"steps", "code"} and index + 2 < len(parts):
            return "/".join(parts[:index + 2]), "/".join(parts[index + 2:]), parts[index + 1]
    return str(PurePosixPath(name).parent), PurePosixPath(name).name, ""


def expected_outputs(request: Mapping[str, Any]) -> dict[str, list[str]]:
    outputs = {}
    for step in (request.get("plan") or {}).get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        values = [*(step.get("outputs") or []), *[row.get("name") for row in step.get("output_types") or []
                                           if isinstance(row, Mapping)]]
        outputs[str(step.get("id") or "")] = sorted({path for raw in values if isinstance(raw, str)
            and (path := _path(raw)) and PurePosixPath(path).suffix.casefold() not in LANGUAGES
            and not is_lock_file(path)})
    return outputs


def _environment(root: Path, inputs: Mapping[str, str]) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({key: str(root) for key in ("HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR")})
    env.update(PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", R_DEFAULT_PACKAGES="NULL",
               R_ENVIRON_USER=str(root / "no-renviron"), R_PROFILE_USER=str(root / "no-rprofile"))
    env.update(inputs)
    return env


def _run(command: list[str], cwd: Path, env: dict[str, str], timeout: float) -> dict:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout)
        # Parser diagnostics quote source, absolute paths and input values. Never serialize them.
        return {"status": "pass" if result.returncode == 0 else "fail", "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "returncode": None}
    except OSError:
        return {"status": "unavailable", "returncode": None}


def _own_output(folder: Path, name: str) -> os.stat_result | None:
    """A declared output that is only this step folder's file: not a link, not under a linked folder, no other hard link.

    An alias names a file outside the receipt folder (an input or an operator file). It is never touched and never counted
    as an output (PR #781 review)."""
    path = folder / name
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            return None
        stat = path.stat()
    except OSError:
        return None
    return stat if stat.st_nlink == 1 else None


def _mark_outputs(folder: Path, expected: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Declared outputs an earlier script of the same step left in the shared folder (#771).

    Their modification time is reset first, so a rewrite by the next run shows even with the same bytes in the same clock
    tick. Only the folder's own files are reset (`_own_output`). The folder is shared on purpose: sibling sources and
    undeclared intermediates stay for a later script."""
    marks = {}
    for name in expected:
        if _own_output(folder, name) is None:
            continue
        try:
            os.utime(folder / name, ns=(0, 0))
        except OSError:
            pass
        stat = (folder / name).stat()
        marks[name] = (stat.st_mtime_ns, stat.st_size)
    return marks


def _wrote_outputs(folder: Path, expected: Sequence[str], marks: Mapping[str, tuple[int, int]]) -> bool:
    """Every declared output is the folder's own file this run created or rewrote; one only an earlier script wrote does not
    count."""
    def written(name: str) -> bool:
        stat = _own_output(folder, name)
        return stat is not None and marks.get(name) != (stat.st_mtime_ns, stat.st_size)
    return bool(expected) and all(written(name) for name in expected)


def _version(executable: str, root: Path, env: dict[str, str], timeout: float) -> str | None:
    try:
        result = subprocess.run([executable, "--version"], cwd=root, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
        found = re.search(rb"\b\d+\.\d+(?:\.\d+)?(?:\([0-9]+\))?", result.stdout + result.stderr)
        return found.group().decode("ascii") if found and result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def build_receipt(files: Mapping[str, str | bytes], *, outputs: Mapping[str, Sequence[str]] | None = None,
                  inputs: Mapping[str, str] | None = None, rerun: bool = False,
                  required_locks: Sequence[str] = (),
                  tools: Mapping[str, str | None] | None = None, timeout: float = 10,
                  temp_root: Path | None = None) -> dict[str, Any]:
    """Hash supplied publication text and check syntax; execution is separately opt-in.

    ``inputs`` is explicit, never inherited from the gateway environment. Only its
    variable names enter the receipt; availability does not prove that data exist.
    """
    inputs = dict(inputs or {})
    if any(not _VARIABLE.fullmatch(key) for key in inputs):
        raise ValueError("invalid input variable name")
    supplied = dict(tools) if tools is not None else {
        "python": sys.executable, "r": shutil.which("Rscript"), "bash": shutil.which("bash")}
    checked_at = datetime.now(timezone.utc).isoformat()
    locks = [{"path": _path(name) or "redacted-" + _hash(name)[:12], "sha256": _hash(body), "status": "present"}
             for name, body in sorted(files.items()) if is_lock_file(name)]
    locks += [{"path": _path(name) or "redacted-" + _hash(name)[:12], "sha256": None, "status": "missing"}
              for name in required_locks if name not in files]
    receipt = {"schema_version": 1, "checked_at": checked_at, "scope": "supplied_publication_files",
               "locks": locks, "lock_status": "present" if locks and all(row["status"] == "present" for row in locks)
               else "missing", "input_check": "explicit_variable_presence_only", "files": [], "omitted_files": []}
    with tempfile.TemporaryDirectory(prefix="labhq-receipt-", dir=temp_root) as temporary:
        root = Path(temporary)
        env = _environment(root, {})
        versions = {}
        # Never stage input data or follow source paths. Each mapping value is already in memory.
        for name, body in files.items():
            relative = _path(name)
            if relative and (PurePosixPath(relative).suffix.casefold() in LANGUAGES or is_lock_file(relative)):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
        for name, body in sorted(files.items()):
            language = LANGUAGES.get(PurePosixPath(name).suffix.casefold())
            if language is None:
                continue
            if not isinstance(body, str):
                receipt["omitted_files"].append({"path": _path(name) or "redacted", "reason": "not_text"})
                continue
            relative = _path(name)
            shown = relative or "redacted-" + _hash(name)[:12] + PurePosixPath(name).suffix.casefold()
            cwd, script, step = _location(shown)
            names = input_variables(body, language)
            missing = [key for key in names if not inputs.get(key)]
            expected = sorted({path for raw in (outputs or {}).get(step, []) if (path := _path(raw))})
            static_args = {"python": ["-I", "-S", "-B", "-c", _PY_COMPILE, script],
                           "r": ["--vanilla", "-e", _R_PARSE, script],
                           "bash": ["--noprofile", "--norc", "-n", script]}[language]
            runtime_args = {"python": ["-I", "-S", "-B", script], "r": ["--vanilla", script],
                            "bash": ["--noprofile", "--norc", script]}[language]
            tool_name = {"python": "python", "r": "Rscript", "bash": "bash"}[language]
            executable = supplied.get(language)
            if executable and language not in versions:
                versions[language] = _version(executable, root, env, timeout)
            syntax = {"status": "blocked_path" if relative is None else "unavailable", "returncode": None}
            if relative and executable:
                syntax = _run([executable, *static_args], root / cwd, env, timeout)
            execution = {"status": "blocked_input" if missing else "not_rerun", "returncode": None}
            if rerun and not missing:
                if syntax["status"] != "pass":
                    execution["status"] = "blocked_syntax"
                else:
                    marks = _mark_outputs(root / cwd, expected)
                    execution = _run([executable, *runtime_args], root / cwd,
                                     _environment(root, {key: inputs[key] for key in names}), timeout)
                    if execution["status"] == "pass" and not _wrote_outputs(root / cwd, expected, marks):
                        execution["status"] = "missing_output"
            receipt["files"].append({"path": shown, "sha256": _hash(body), "language": language,
                "cwd": cwd, "input_variables": names, "missing_inputs": missing,
                "command": [tool_name, *runtime_args], "expected_outputs": expected,
                "tool": {"name": tool_name, "version": versions.get(language)},
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "syntax": {**syntax, "command": [tool_name, *static_args]},
                "execution": {**execution, "output_check": "declared_files_written_by_this_run"}})
    receipt["checked_at"] = datetime.now(timezone.utc).isoformat()
    return receipt


def receipt_summary(receipt: Any, *, path: str = "") -> dict[str, Any]:
    """A bounded, value-free descriptor for report fronts and bench manifests."""
    missing = {"present": False, "syntax": "not_checked", "execution": "not_rerun", "lock_status": "not_recorded",
               "failed_files": [], "unavailable_files": [], "omitted_files": [], "missing_inputs": [], "path": _path(path) or ""}
    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != 1 or not isinstance(receipt.get("files"), list):
        return missing
    rows = receipt["files"]
    if any(not isinstance(row, Mapping) or not isinstance(row.get("syntax"), Mapping)
           or not isinstance(row.get("execution"), Mapping) or row["syntax"].get("status") not in _SYNTAX
           or row["execution"].get("status") not in _EXECUTION for row in rows):
        return missing
    static = {row["syntax"]["status"] for row in rows}
    executed = {row["execution"]["status"] for row in rows}
    omitted = [_path(row.get("path")) or "redacted" for row in receipt.get("omitted_files", []) if isinstance(row, Mapping)]
    syntax = next((s for s in ("fail", "timeout", "blocked_path", "unavailable") if s in static),
                  "not_checked" if omitted else "pass" if rows else "not_applicable")
    execution = next((s for s in ("fail", "timeout", "unavailable", "missing_output", "blocked_input", "blocked_syntax", "not_rerun")
                      if s in executed), "pass" if rows and not omitted else "not_rerun")
    return {**missing, "present": True, "syntax": syntax, "execution": execution,
            "lock_status": "present" if receipt.get("lock_status") == "present" else "missing",
            "omitted_files": omitted,
            "failed_files": [_path(row.get("path")) or "redacted" for row in rows
                             if row["syntax"]["status"] in {"fail", "timeout", "blocked_path"}],
            "unavailable_files": [_path(row.get("path")) or "redacted" for row in rows
                                  if row["syntax"]["status"] == "unavailable"],
            "missing_inputs": sorted({key for row in rows for key in row.get("missing_inputs", [])
                                      if isinstance(key, str) and _VARIABLE.fullmatch(key)})}


def receipt_note(summary: Mapping[str, Any]) -> str:
    return (f"문법 검사: {summary.get('syntax', 'not_checked')} · 실행: {summary.get('execution', 'not_rerun')} · "
            f"lock: {summary.get('lock_status', 'not_recorded')} · "
            f"문법 실패 {len(summary.get('failed_files') or [])} · 도구 없음 {len(summary.get('unavailable_files') or [])} · "
            f"미검사 {len(summary.get('omitted_files') or [])} · 누락 입력 {len(summary.get('missing_inputs') or [])}. "
            "문법 PASS는 실행 PASS가 아닙니다.")


def receipt_json(receipt: Mapping[str, Any]) -> str:
    return json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
