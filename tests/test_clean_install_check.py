from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts import clean_install_check


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


def test_remaining_venv_process_names_its_recorded_role(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    venv_python = checkout / ".venv/Scripts/python.exe"
    monkeypatch.setattr(clean_install_check, "_process_images", lambda: [
        (4101, venv_python),
        (4102, tmp_path / "other/python.exe"),
    ])

    assert clean_install_check._venv_python_processes(checkout, {4101: "gateway-supervisor"}) == [
        "gateway-supervisor (python.exe PID 4101)"
    ]
