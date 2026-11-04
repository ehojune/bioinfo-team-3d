"""Fail before pytest touches the operator's .labhq; inert in normal runs."""
from __future__ import annotations

import os
from pathlib import Path
import sys


# conftest imports this before replacing HOME. Children inherit the original home.
_ORIGINAL_HOME = Path(os.environ.get("LABHQ_TEST_REAL_HOME", str(Path.home())))


def guard_test_path(path: Path) -> None:
    if ("pytest" not in sys.modules and not os.environ.get("PYTEST_CURRENT_TEST")
            and not os.environ.get("LABHQ_TEST_REAL_HOME")):
        return
    homes = {_ORIGINAL_HOME, Path(os.environ.get("LABHQ_TEST_REAL_HOME", str(_ORIGINAL_HOME)))}
    resolved = path.resolve()
    if any(resolved.is_relative_to((home / ".labhq").resolve()) for home in homes):
        raise RuntimeError("pytest refused operator .labhq path; use an isolated temporary home/state")
