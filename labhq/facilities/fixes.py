"""Small, deterministic environment repairs approved by the PI (#35 stage 3).

The allowlist is data, but execution kinds are code. Package names are extracted from the failure, validated, and
passed as one subprocess argument. No employee-authored command is evaluated by a shell.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from ..util import short
from ..yaml_unique import load_yaml_unique

FIX_FILE = Path(__file__).with_name("fixes.yaml")
EXECUTIONS = frozenset({"python_package", "r_package", "workspace_cache"})
REQUIRED = ("id", "signatures", "action", "execution")
_PYPI_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")
_CRAN_NAME = re.compile(r"[A-Za-z](?:[A-Za-z0-9.]*[A-Za-z0-9])?")
_PYTHON_MISSING = re.compile(r"No module named\s+['\"]?([^'\"\s]+)", re.IGNORECASE)
_R_MISSING = re.compile(r"there is no package called\s+['\"‘’]?([^'\"‘’\s]+)", re.IGNORECASE)
_PYTHON_PATH = re.compile(
    r"(?i)([A-Za-z]:[\\/][^\r\n\"']*?[\\/]python(?:\d+(?:\.\d+)*)?(?:\.exe)?|"
    r"/[^\r\n\"']*?/python(?:\d+(?:\.\d+)*)?)(?=\s*(?::|$|--version))")
CACHE_DIRS = (".cache", ".tmp", "cache", "tmp")
RunCommand = Callable[[list[str], Path], Awaitable[tuple[int, str, str]]]


class FixError(ValueError):
    """An allowlist entry or proposed repair is unsafe or incomplete."""


@dataclass(frozen=True)
class Fix:
    id: str
    signatures: tuple[str, ...]
    action: str
    execution: str


def _one_line(value: Any, field: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
        raise FixError(f"{where}: {field} must be one non-empty line")
    return value.strip()


def load(path: Path | None = None) -> tuple[Fix, ...]:
    source = path or FIX_FILE
    raw = load_yaml_unique(source.read_text(encoding="utf-8"), source.name)
    if not isinstance(raw, list) or not raw:
        raise FixError(f"{source.name}: expected a non-empty list")
    seen: set[str] = set()
    out = []
    for index, entry in enumerate(raw, 1):
        where = f"{source.name} entry {index}"
        if not isinstance(entry, dict) or set(entry) != set(REQUIRED):
            raise FixError(f"{where}: expected exactly {list(REQUIRED)}")
        fix_id = _one_line(entry["id"], "id", where)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", fix_id) or fix_id in seen:
            raise FixError(f"{where}: id must be unique snake_case")
        seen.add(fix_id)
        signatures = entry["signatures"]
        if (not isinstance(signatures, list) or not signatures or
                any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", item)
                    for item in signatures) or len(set(signatures)) != len(signatures)):
            raise FixError(f"{where}: signatures must be unique signature ids")
        execution = _one_line(entry["execution"], "execution", where)
        if execution not in EXECUTIONS:
            raise FixError(f"{where}: unknown execution {execution}")
        out.append(Fix(fix_id, tuple(signatures), _one_line(entry["action"], "action", where), execution))
    return tuple(out)


@lru_cache(maxsize=1)
def fixes() -> tuple[Fix, ...]:
    table = load()
    from . import signatures as signature_table
    linked = {sig.id: sig.fix for sig in signature_table.signatures() if sig.fix}
    expected = {signature: fix.id for fix in table for signature in fix.signatures}
    if linked != expected:
        raise FixError("fixes.yaml and signatures.yaml links do not match")
    return table


def by_id(fix_id: str) -> Fix | None:
    return next((fix for fix in fixes() if fix.id == fix_id), None)


def validate_python_package(name: str) -> str:
    """Accept a bare PyPI distribution spelling; reject versions, URLs, paths and options."""
    value = str(name).strip()
    if not _PYPI_NAME.fullmatch(value):
        raise FixError("Python package must be one bare PyPI name")
    return value


def validate_r_package(name: str) -> str:
    """Accept a bare CRAN package spelling; reject versions, URLs, paths and options."""
    value = str(name).strip()
    if not _CRAN_NAME.fullmatch(value) or ".." in value:
        raise FixError("R package must be one bare CRAN name")
    return value


def _first(pattern: re.Pattern[str], texts: Iterable[str]) -> str | None:
    for text in texts:
        match = pattern.search(str(text))
        if match:
            return match.group(1)
    return None


def proposal(environment: dict[str, str] | None, error: str | None,
             tool_errors: Iterable[str] = ()) -> dict[str, Any] | None:
    """Build the bounded object shown on the approval card, or None if no safe argument can be derived."""
    if not environment or not environment.get("fix"):
        return None
    fix = by_id(environment["fix"])
    signature_id = str(environment.get("id") or "")
    if fix is None or signature_id not in fix.signatures:
        return None
    texts = [str(error or ""), *(str(value) for value in tool_errors)]
    out: dict[str, Any] = {"fix_id": fix.id, "signature_id": signature_id,
                           "action": fix.action, "execution": fix.execution,
                           "reason": str(environment.get("cause") or signature_id)[:300]}
    try:
        if fix.execution == "python_package":
            out["package"] = validate_python_package(environment.get("package") or
                                                       _first(_PYTHON_MISSING, texts) or "")
            out["command"] = f"<환경 단계 python> -m pip install {out['package']}"
        elif fix.execution == "r_package":
            out["package"] = validate_r_package(environment.get("package") or _first(_R_MISSING, texts) or "")
            out["command"] = f'Rscript --vanilla -e install.packages("{out["package"]}")'
        else:
            out["command"] = "작업 폴더 안 .cache/.tmp/cache/tmp 비우기"
    except FixError:
        return None
    return out


def clean_proposal(value: Any) -> dict[str, Any]:
    """Validate the gateway-supplied object again on the runner."""
    if not isinstance(value, dict):
        raise FixError("fix proposal must be an object")
    fix_id, signature_id = value.get("fix_id"), value.get("signature_id")
    fix = by_id(str(fix_id or ""))
    if fix is None or signature_id not in fix.signatures or value.get("execution") != fix.execution:
        raise FixError("fix proposal does not match the allowlist")
    out = {"fix_id": fix.id, "signature_id": str(signature_id), "action": fix.action,
           "execution": fix.execution}
    if fix.execution == "python_package":
        out["package"] = validate_python_package(value.get("package", ""))
    elif fix.execution == "r_package":
        out["package"] = validate_r_package(value.get("package", ""))
    return out


def _inside(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(child), os.path.abspath(parent)]) == os.path.abspath(parent)
    except (OSError, ValueError):
        return False


def _python_interpreter(workdir: Path, upstream_steps: dict[str, Any], workspace_root: Path) -> Path:
    """Read only #344's environment lock and accept an executable path lexically inside that upstream workspace."""
    candidates: list[tuple[Path, Path]] = []
    tail = workdir / ".labhq" / "stderr_tail.txt"
    if tail.is_file() and not tail.is_symlink():
        match = _PYTHON_PATH.search(tail.read_text(encoding="utf-8", errors="replace")[:20_000])
        if match:
            candidates.append((Path(match.group(1)), workdir))
    for directory in upstream_steps.values():
        base = Path(str(directory))
        if not base.is_absolute() or not _inside(base, workspace_root):
            continue
        lock = base / "outputs" / "env" / "requirements.lock.txt"
        if not lock.is_file() or lock.is_symlink():
            continue
        for match in _PYTHON_PATH.finditer(lock.read_text(encoding="utf-8", errors="replace")[:20_000]):
            candidates.append((Path(match.group(1)), base))
    for executable, owner in candidates:
        if executable.is_absolute() and _inside(executable, owner) and executable.is_file():
            return executable
    raise FixError("환경 단계 requirements.lock.txt에서 안전한 Python interpreter를 찾지 못했습니다")


async def _run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    return int(proc.returncode or 0), stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


def _reparse(path: Path) -> bool:
    info = path.lstat()
    return bool(stat.S_ISLNK(info.st_mode) or
                (getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _clear_cache(workdir: Path) -> list[str]:
    removed = []
    for name in CACHE_DIRS:
        target = workdir / name
        if not os.path.lexists(target):
            continue
        if _reparse(target):
            raise FixError(f"캐시 경로 {name}가 링크라서 비우지 않았습니다")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(name)
    return removed


async def execute(value: Any, workdir: Path, upstream_steps: dict[str, Any], workspace_root: Path,
                  run: RunCommand | None = None) -> dict[str, Any]:
    """Run one allowlisted repair and return the bounded execution record."""
    started = time.time()
    try:
        fix = clean_proposal(value)
        if fix["execution"] == "python_package":
            executable = _python_interpreter(workdir, upstream_steps, workspace_root)
            argv = [str(executable), "-m", "pip", "install", fix["package"]]
            shown = f"{executable} -m pip install {fix['package']}"
            code, stdout, stderr = await (run or _run)(argv, workdir)
        elif fix["execution"] == "r_package":
            expression = f'install.packages("{fix["package"]}", repos="https://cloud.r-project.org")'
            argv = ["Rscript", "--vanilla", "-e", expression]
            shown = f'Rscript --vanilla -e install.packages("{fix["package"]}")'
            code, stdout, stderr = await (run or _run)(argv, workdir)
        else:
            removed = await asyncio.to_thread(_clear_cache, workdir)
            shown, code, stdout, stderr = "작업 폴더 캐시 비우기", 0, ", ".join(removed) or "비울 캐시 없음", ""
        return {**fix, "ok": code == 0, "status": "succeeded" if code == 0 else "failed",
                "command": shown, "exit_code": code, "stdout": short(stdout.strip(), 1000),
                "stderr": short(stderr.strip(), 1000), "started_at": started, "ended_at": time.time(),
                **({"error": short(stderr.strip() or stdout.strip() or f"exit {code}", 500)} if code else {})}
    except (FixError, OSError) as exc:
        return {"ok": False, "status": "failed", "error": short(str(exc), 500),
                "started_at": started, "ended_at": time.time()}
