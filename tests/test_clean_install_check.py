from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_install_drops_operator_overrides_and_auth_before_first_subprocess(tmp_path, monkeypatch):
    for name in ("LABHQ_CONFIG", "LABHQ_HOME", "LABHQ_STATE_DIR", "GH_TOKEN", "GITHUB_TOKEN",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "operator-value")
    env = clean_install_check._isolated_env(tmp_path, tmp_path / "checkout")
    for name in ("LABHQ_CONFIG", "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        assert name not in env
    for name in ("HOME", "USERPROFILE", "LABHQ_HOME", "LABHQ_STATE_DIR", "CODEX_HOME",
                 "CLAUDE_CONFIG_DIR", "TEMP", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert Path(env[name]).is_relative_to(tmp_path)
    assert env["PYTHONNOUSERSITE"] == "1"
    # Preserve conftest's guard identity instead of bypassing the operator-home guard.
    assert env["LABHQ_TEST_REAL_HOME"] == os.environ["LABHQ_TEST_REAL_HOME"]


def test_failed_command_retains_separate_streams_and_receipt(tmp_path, monkeypatch):
    work = tmp_path / "new install"
    receipt_path = tmp_path / "receipt.json"

    original_run = clean_install_check._run
    def fail_install(stage, command, **kwargs):
        return original_run("install", [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"],
                            cwd=work, env=os.environ.copy())
    monkeypatch.setattr(clean_install_check, "_run", fail_install)
    with pytest.raises(clean_install_check.CheckFailure, match="exit 7: err"):
        clean_install_check.rehearse(tmp_path, [], work_dir=work, receipt_path=receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert receipt["failure_stage"] == "install"
    row = receipt["steps"][0]
    assert row["returncode"] == 7
    assert (work / row["stdout"]).read_text(encoding="utf-8").strip() == "out"
    assert (work / row["stderr"]).read_text(encoding="utf-8").strip() == "err"
    assert clean_install_check._RECEIPT.get() is None


def test_nonempty_work_dir_is_never_reused_or_modified(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("operator", encoding="utf-8")
    with pytest.raises(clean_install_check.CheckFailure, match="빈 폴더"):
        clean_install_check.rehearse(tmp_path, [], work_dir=tmp_path)
    assert marker.read_text(encoding="utf-8") == "operator"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd quoting boundary")
def test_launcher_preserves_spaced_install_path_and_argument(tmp_path):
    checkout = tmp_path / "install with spaces"
    checkout.mkdir()
    (checkout / "labhq.cmd").write_bytes(b'@echo off\r\necho argument=%1\r\n')
    done = subprocess.run(clean_install_check._labhq_command(checkout, "argument with spaces"),
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == 'argument="argument with spaces"'


def test_partial_start_is_stopped_and_original_failure_preserved(tmp_path, monkeypatch):
    calls = []
    def run(stage, command, **kwargs):
        calls.append(stage)
        if stage in ("source HEAD", "clone HEAD"):
            return "abc123"
        if stage == "labhq init":
            (kwargs["cwd"] / "config").mkdir()
            (kwargs["cwd"] / "config/labhq.yaml").write_text("", encoding="utf-8")
        if stage == "새 clone":
            checkout = Path(command[-1])
            checkout.mkdir()
        if stage == "labhq doctor":
            return "실행 준비: 예"
        if stage == "wheel build":
            import zipfile
            with zipfile.ZipFile(tmp_path / "work/wheel/labhq-test.whl", "w") as archive:
                for name in ("labhq/skills/.claude-plugin/plugin.json", "labhq/skills/humanizer/SKILL.md",
                             "labhq/skills/graphical-abstract/templates/graphical-abstract.svg"):
                    archive.writestr(name, "fixture")
        return ""
    def start(*args):
        raise clean_install_check.CheckFailure("gateway와 runner 시작", "partial startup")
    monkeypatch.setattr(clean_install_check, "_run", run)
    monkeypatch.setattr(clean_install_check, "_patch_mock_config", lambda *args: None)
    monkeypatch.setattr(clean_install_check, "_start_services", start)
    monkeypatch.setattr(clean_install_check, "_venv_python_processes", lambda *args: [])
    with pytest.raises(clean_install_check.CheckFailure, match="partial startup"):
        clean_install_check.rehearse(tmp_path / "source", [], work_dir=tmp_path / "work")
    assert "실패 뒤 강제 종료" in calls
    receipt = json.loads((tmp_path / "work/receipt.json").read_text(encoding="utf-8"))
    assert receipt["failure_stage"] == "gateway와 runner 시작"
    assert any(row["stage"] == "실패 뒤 자식 종료" and row["status"] == "PASS" for row in receipt["steps"])
