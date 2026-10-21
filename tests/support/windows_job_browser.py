"""Test-only Windows launcher for the demo browser (#842): the product's KILL_ON_CLOSE Job with the standard handles.

`labhq/runner/memory_limit.py --windows-job` starts its child with `subprocess.run(command)`. With no standard handle
given, CPython on Windows sets no STARTF_USESTDHANDLES and, with the default close_fds=True, the child inherits no
handle. Chrome's stderr would then never reach the Node driver, and the driver's stdio close would only mean that
this wrapper ended. This adapter reuses the product's `_WindowsJob` unchanged (the same Job, memory limit,
KILL_ON_CLOSE and non-inheritable Job handle) and passes exactly this process's stdin/stdout/stderr to the child;
CPython then lists only those three handles for inheritance. Argument checks, error lines and exit codes are the
product entry's. Standard library only; the product module is loaded from its file, not from an installed package.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

PRODUCT_MODULE = Path(__file__).resolve().parents[2] / "labhq" / "runner" / "memory_limit.py"


def product_job_class(path: Path = PRODUCT_MODULE):
    """The product's `_WindowsJob`, executed from its own source file."""
    name = "labhq_runner_memory_limit_for_demo_browser"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise OSError(f"product Windows Job module is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclass resolves annotations through sys.modules
    spec.loader.exec_module(module)
    return module._WindowsJob


def run(argv: list[str], *, job_factory=None, start=subprocess.run, stdin=None, stdout=None, stderr=None) -> int:
    """`_run_windows_job` of the product, except that the child receives this process's standard handles."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if len(argv) < 4 or argv[0] != "--windows-job" or argv[2] != "--":
        print("LABHQ_MEMORY_LIMIT_SETUP: invalid Windows wrapper arguments", file=stderr)
        return 126
    # As in the product: the KILL_ON_CLOSE handle stays open until this process exits, so ending this wrapper (the
    # driver's only owned child) closes the Job and the kernel ends Chrome and every descendant.
    job = None
    try:
        limit = int(argv[1])
        command = argv[3:]
        if limit <= 0 or not command:
            raise ValueError("missing positive limit or command")
        job = (job_factory or product_job_class())(os.getpid(), limit)
        completed = start(command, check=False, stdin=stdin, stdout=stdout, stderr=stderr)
        if completed.returncode and job.peak_bytes() >= int(limit * 0.98):
            print("out of memory: Windows Job object limit reached", file=stderr)
        return int(completed.returncode)
    except FileNotFoundError as exc:
        print(f"LABHQ_EXEC_NOT_FOUND: {exc}", file=stderr)
        return 127
    except (OSError, ValueError) as exc:
        print(f"LABHQ_MEMORY_LIMIT_SETUP: {type(exc).__name__}: {exc}", file=stderr)
        return 126


if __name__ == "__main__":  # pragma: no cover - started by tests/support/demo_driver.cjs on Windows CI
    raise SystemExit(run(sys.argv[1:]))
