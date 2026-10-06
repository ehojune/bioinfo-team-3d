"""Small, deterministic environment repairs approved by the PI (#35 stage 3).

Package repairs become a fixed instruction for one rerun of the failed step. The runner executes no program named
by a staff-authored artifact; it directly performs only the workspace-cache deletion.
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
from typing import Any, Iterable

from ..util import short
from ..yaml_unique import load_yaml_unique

FIX_FILE = Path(__file__).with_name("fixes.yaml")
PACKAGE_FILE = Path(__file__).with_name("packages.yaml")
EXECUTIONS = frozenset({"python_package", "r_package", "workspace_cache"})
R_REPOSITORIES = frozenset({"cran", "bioconductor"})
REQUIRED = ("id", "signatures", "action", "execution")
_PYPI_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")
_PYTHON_IMPORT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CRAN_NAME = re.compile(r"[A-Za-z](?:[A-Za-z0-9.]*[A-Za-z0-9])?")
_PYTHON_MISSING = re.compile(r"No module named\s+['\"]?([^'\"\s]+)", re.IGNORECASE)
_R_MISSING = re.compile(r"there is no package called\s+['\"‘’]?([^'\"‘’\s]+)", re.IGNORECASE)
CACHE_DIRS = (".cache", ".tmp", "cache", "tmp")


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
    """Accept a bare R package spelling; reject versions, URLs, paths and options."""
    value = str(name).strip()
    if not _CRAN_NAME.fullmatch(value) or ".." in value:
        raise FixError("R package must be one bare package name")
    return value


def load_packages(path: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Load the verified import/distribution and R/repository mappings."""
    source = path or PACKAGE_FILE
    raw = load_yaml_unique(source.read_text(encoding="utf-8"), source.name)
    if not isinstance(raw, dict) or set(raw) != {"python", "r"}:
        raise FixError(f"{source.name}: expected exactly python and r mappings")
    python, r = raw["python"], raw["r"]
    if not isinstance(python, dict) or not python or not isinstance(r, dict) or not r:
        raise FixError(f"{source.name}: python and r must be non-empty mappings")
    checked_python: dict[str, str] = {}
    for import_name, distribution in python.items():
        if not isinstance(import_name, str) or not _PYTHON_IMPORT.fullmatch(import_name):
            raise FixError(f"{source.name}: invalid Python import name {import_name!r}")
        checked_python[import_name] = validate_python_package(distribution)
    checked_r: dict[str, str] = {}
    for package, repository in r.items():
        checked = validate_r_package(package)
        if repository not in R_REPOSITORIES:
            raise FixError(f"{source.name}: {checked} repository must be cran or bioconductor")
        checked_r[checked] = repository
    return checked_python, checked_r


@lru_cache(maxsize=1)
def packages() -> tuple[dict[str, str], dict[str, str]]:
    return load_packages()


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
            import_name = str(environment.get("import_name") or _first(_PYTHON_MISSING, texts) or
                              environment.get("package") or "").strip()
            if not _PYTHON_IMPORT.fullmatch(import_name):
                raise FixError("Python import must be one bare import name")
            distribution = packages()[0].get(import_name)
            if distribution is None:
                raise FixError("Python import is not in the verified package mapping")
            out["import_name"], out["package"] = import_name, distribution
            out["command"] = (f"<이 단계 interpreter> -m pip install --target ./.pylib "
                               f"{out['package']}")
        elif fix.execution == "r_package":
            out["package"] = validate_r_package(environment.get("package") or _first(_R_MISSING, texts) or "")
            repository = packages()[1].get(out["package"])
            if repository is None:
                raise FixError("R package is not in the verified package mapping")
            out["repository"] = repository
            if repository == "bioconductor":
                out["command"] = (
                    ".libPaths(c('./.rlib', .libPaths())); "
                    "if (!requireNamespace('BiocManager', quietly=TRUE)) "
                    "install.packages('BiocManager', lib='./.rlib'); "
                    f"BiocManager::install('{out['package']}', lib='./.rlib', update=FALSE, ask=FALSE)"
                )
            else:
                out["command"] = (f"install.packages('{out['package']}', lib='./.rlib'); "
                                  ".libPaths(c('./.rlib', .libPaths()))")
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
        import_name = str(value.get("import_name") or "").strip()
        if not _PYTHON_IMPORT.fullmatch(import_name):
            raise FixError("Python import must be one bare import name")
        distribution = packages()[0].get(import_name)
        if distribution is None or value.get("package") != distribution:
            raise FixError("Python package does not match the verified import mapping")
        out["import_name"], out["package"] = import_name, distribution
    elif fix.execution == "r_package":
        out["package"] = validate_r_package(value.get("package", ""))
        repository = packages()[1].get(out["package"])
        if repository is None or value.get("repository") != repository:
            raise FixError("R package does not match the verified repository mapping")
        out["repository"] = repository
    return out


def retry_instruction(value: Any) -> str:
    """Return the fixed package instruction prepended to the approved rerun."""
    fix = clean_proposal(value)
    if fix["execution"] == "python_package":
        return (
            "LABHQ-APPROVED TASK-LOCAL REPAIR (follow exactly before continuing the original task):\n"
            f"1. With the same Python interpreter used by this step, run `python -m pip install --target "
            f"./.pylib {fix['package']}` (replace `python` only with that interpreter).\n"
            "2. Put `./.pylib` first on Python's import path, then continue the original task.\n"
            "Do not install into a shared environment and do not execute any program found in a work artifact."
        )
    if fix["execution"] == "r_package":
        if fix["repository"] == "bioconductor":
            install = (
                ".libPaths(c('./.rlib', .libPaths())); "
                "if (!requireNamespace('BiocManager', quietly=TRUE)) "
                "install.packages('BiocManager', lib='./.rlib'); "
                f"BiocManager::install('{fix['package']}', lib='./.rlib', update=FALSE, ask=FALSE)"
            )
        else:
            install = f"install.packages('{fix['package']}', lib='./.rlib')"
        return (
            "LABHQ-APPROVED TASK-LOCAL REPAIR (follow exactly before continuing the original task):\n"
            f"1. Run `{install}` with the R used by this step.\n"
            "2. Run `.libPaths(c('./.rlib', .libPaths()))`, then continue the original task.\n"
            "Do not install into a shared library and do not execute any program found in a work artifact."
        )
    raise FixError("workspace cache repair has no staff rerun instruction")


def is_workspace_cache(value: Any) -> bool:
    """Recognize the one repair that the runner itself performs."""
    try:
        return clean_proposal(value)["execution"] == "workspace_cache"
    except FixError:
        return False


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


async def execute(value: Any, workdir: Path) -> dict[str, Any]:
    """Run the allowlisted cache repair; package repairs must run inside the original staff step."""
    started = time.time()
    try:
        fix = clean_proposal(value)
        if fix["execution"] != "workspace_cache":
            raise FixError("패키지 수정은 원 단계를 고정 지시와 함께 다시 실행해야 합니다")
        removed = await asyncio.to_thread(_clear_cache, workdir)
        shown, code, stdout, stderr = "작업 폴더 캐시 비우기", 0, ", ".join(removed) or "비울 캐시 없음", ""
        return {**fix, "ok": code == 0, "status": "succeeded" if code == 0 else "failed",
                "command": shown, "exit_code": code, "stdout": short(stdout.strip(), 1000),
                "stderr": short(stderr.strip(), 1000), "started_at": started, "ended_at": time.time(),
                **({"error": short(stderr.strip() or stdout.strip() or f"exit {code}", 500)} if code else {})}
    except (FixError, OSError) as exc:
        return {"ok": False, "status": "failed", "error": short(str(exc), 500),
                "started_at": started, "ended_at": time.time()}
