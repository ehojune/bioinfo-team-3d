from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_failure_is_one_utf8_line_when_windows_starts_with_cp1252(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "clean_install_check.py"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    done = subprocess.run(
        [sys.executable, str(script), "--source", str(tmp_path / "missing")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    error = done.stderr.decode("utf-8")
    assert done.returncode == 1
    assert error.startswith("FAIL 새 clone:")
    assert len(error.rstrip().splitlines()) == 1
