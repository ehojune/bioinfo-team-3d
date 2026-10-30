from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import clean_install_check
from tests.support.time_proxy import RuntimeProxy


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
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
                 "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_NO_INDEX", "PIP_FIND_LINKS", "PIP_CONSTRAINT",
                 "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, "operator-value")
    env = clean_install_check._isolated_env(tmp_path, tmp_path / "checkout")
    for name in ("LABHQ_CONFIG", "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                 "PIP_NO_INDEX", "PIP_FIND_LINKS", "PIP_CONSTRAINT", "GIT_DIR", "GIT_WORK_TREE",
                 "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
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


@pytest.fixture
def fake_install(monkeypatch):
    calls = []
    def run(stage, command, **kwargs):
        calls.append(stage)
        root = kwargs["cwd"].parent
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
            with zipfile.ZipFile(root / "wheel/labhq-test.whl", "w") as archive:
                for name in ("labhq/skills/.claude-plugin/plugin.json", "labhq/skills/humanizer/SKILL.md",
                             "labhq/skills/graphical-abstract/templates/graphical-abstract.svg"):
                    archive.writestr(name, "fixture")
        if stage == "공개 데이터 형태 mock 요청":
            bundle = root / "runs/requests/req_fixture123"
            bundle.mkdir(parents=True)
            for name in ("report.md", "report_appendix.md", "MANIFEST.tsv", "ro-crate-metadata.json"):
                (bundle / name).write_text("fixture", encoding="utf-8")
            return "request req_fixture123 [mock:analyst]"
        if stage == "labhq verify":
            return "문제 없음 (exit 0)"
        return ""
    monkeypatch.setattr(clean_install_check, "_run", run)
    monkeypatch.setattr(clean_install_check, "_patch_mock_config", lambda *args: None)
    monkeypatch.setattr(clean_install_check, "_venv_python_processes", lambda *args: [])
    return calls


def test_partial_start_is_stopped_and_original_failure_preserved(tmp_path, monkeypatch, fake_install):
    def start(*args):
        raise clean_install_check.CheckFailure("gateway와 runner 시작", "partial startup")
    monkeypatch.setattr(clean_install_check, "_start_services", start)
    with pytest.raises(clean_install_check.CheckFailure, match="partial startup"):
        clean_install_check.rehearse(tmp_path / "source", [], work_dir=tmp_path / "work")
    assert "실패 뒤 강제 종료" in fake_install
    receipt = json.loads((tmp_path / "work/receipt.json").read_text(encoding="utf-8"))
    assert receipt["failure_stage"] == "gateway와 runner 시작"
    assert any(row["stage"] == "실패 뒤 자식 종료" and row["status"] == "PASS" for row in receipt["steps"])


def test_inherited_git_routing_cannot_change_an_operator_repo(tmp_path, monkeypatch):
    import shutil
    git = shutil.which("git")
    assert git
    base_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    operator = tmp_path / "operator"
    root = tmp_path / "rehearsal"
    checkout = root / "checkout"
    root.mkdir()
    for folder in (operator, checkout):
        subprocess.run([git, "init", "-q", str(folder)], env=base_env, check=True)
    subprocess.run([git, "-C", str(operator), "config", "core.hooksPath", "keep-operator-hooks"],
                   env=base_env, check=True)
    monkeypatch.setenv("GIT_DIR", str(operator / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(operator))
    env = clean_install_check._isolated_env(root, checkout)
    clean_install_check._run("AGENTS hooks", [git, "config", "core.hooksPath", ".githooks"],
                             cwd=checkout, env=env)
    value = subprocess.run([git, "-C", str(operator), "config", "core.hooksPath"], env=base_env,
                           capture_output=True, text=True, check=True).stdout.strip()
    assert value == "keep-operator-hooks"
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        assert name not in env


def test_guard_snapshot_error_finishes_receipt_and_resets_context(tmp_path, monkeypatch):
    def denied(*args):
        raise PermissionError("guard fixture denied")
    monkeypatch.setattr(clean_install_check, "_snapshot", denied)
    receipt_path = tmp_path / "failed.json"
    previous = clean_install_check._RECEIPT.get()
    try:
        with pytest.raises((PermissionError, clean_install_check.CheckFailure)):
            clean_install_check.rehearse(tmp_path / "source", [tmp_path / "guard"],
                                         work_dir=tmp_path / "work", receipt_path=receipt_path)
        assert clean_install_check._RECEIPT.get() is previous
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["status"] == "FAIL"
        assert receipt["finished_at"]
    finally:
        clean_install_check._RECEIPT.set(previous)


def test_automatic_workdir_cleanup_error_cannot_leave_a_pass_receipt(tmp_path, monkeypatch, fake_install):
    root = tmp_path / "automatic"
    root.mkdir()
    receipt_path = tmp_path / "external.json"
    monkeypatch.setattr(clean_install_check.tempfile, "mkdtemp", lambda **kwargs: str(root))
    def locked(*args):
        raise PermissionError("locked fixture")
    monkeypatch.setattr(clean_install_check.shutil, "rmtree", locked)
    with pytest.raises((PermissionError, clean_install_check.CheckFailure)):
        clean_install_check.rehearse(tmp_path / "source", [], receipt_path=receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert receipt["finished_at"]
    assert "locked fixture" in receipt["detail"]
    assert clean_install_check._RECEIPT.get() is None


def _fail_final_guard(monkeypatch):
    calls = 0
    def snapshot(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {}
        raise PermissionError("guard fixture failure")
    monkeypatch.setattr(clean_install_check, "_snapshot", snapshot)


def test_original_failure_retained_when_final_guard_also_fails(tmp_path, monkeypatch, fake_install):
    _fail_final_guard(monkeypatch)
    def start(*args):
        raise clean_install_check.CheckFailure("startup", "primary fixture failure")
    monkeypatch.setattr(clean_install_check, "_start_services", start)
    with pytest.raises(clean_install_check.CheckFailure, match="primary fixture failure"):
        clean_install_check.rehearse(tmp_path / "source", [], work_dir=tmp_path / "work")
    receipt = json.loads((tmp_path / "work/receipt.json").read_text(encoding="utf-8"))
    assert receipt["failure_stage"] == "startup"
    assert receipt["detail"] == "primary fixture failure"
    assert "guard fixture failure" in receipt["guard_error"]
    assert clean_install_check._RECEIPT.get() is None


def test_guard_primary_failure_retained_when_cleanup_also_fails(tmp_path, monkeypatch, fake_install):
    _fail_final_guard(monkeypatch)
    root = tmp_path / "automatic"
    root.mkdir()
    receipt_path = tmp_path / "external.json"
    monkeypatch.setattr(clean_install_check.tempfile, "mkdtemp", lambda **kwargs: str(root))
    def locked(*args):
        raise PermissionError("secondary cleanup failure")
    monkeypatch.setattr(clean_install_check.shutil, "rmtree", locked)
    with pytest.raises(PermissionError, match="guard fixture failure"):
        clean_install_check.rehearse(tmp_path / "source", [], receipt_path=receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert receipt["failure_stage"] == "보호 경로"
    assert "guard fixture failure" in receipt["detail"]
    assert "secondary cleanup failure" in receipt["cleanup_error"]
    assert clean_install_check._RECEIPT.get() is None


@pytest.mark.parametrize("write_error", [OSError, KeyboardInterrupt, SystemExit])
def test_primary_failure_survives_final_receipt_write_error(tmp_path, monkeypatch, fake_install, write_error):
    def start(*args):
        raise clean_install_check.CheckFailure("startup", "primary fixture failure")
    original_write = clean_install_check.Receipt.write
    def write(receipt):
        if receipt.data.get("finished_at"):
            raise write_error("secondary receipt write failure")
        original_write(receipt)
    monkeypatch.setattr(clean_install_check, "_start_services", start)
    monkeypatch.setattr(clean_install_check.Receipt, "write", write)
    with pytest.raises(BaseException) as caught:
        clean_install_check.rehearse(tmp_path / "source", [], work_dir=tmp_path / "work")
    assert isinstance(caught.value, clean_install_check.CheckFailure)
    assert caught.value.detail == "primary fixture failure"
    assert clean_install_check._RECEIPT.get() is None


def test_interruption_cannot_leave_a_pass_receipt(tmp_path, monkeypatch, fake_install):
    def start(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(clean_install_check, "_start_services", start)
    with pytest.raises(KeyboardInterrupt):
        clean_install_check.rehearse(tmp_path / "source", [], work_dir=tmp_path / "work")
    receipt = json.loads((tmp_path / "work/receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert receipt["failure_stage"] == "KeyboardInterrupt"
    assert receipt["finished_at"]
    assert clean_install_check._RECEIPT.get() is None


def test_cleanup_primary_survives_failed_receipt_write_without_a_false_pass(tmp_path, monkeypatch, fake_install):
    root = tmp_path / "automatic"
    root.mkdir()
    receipt_path = tmp_path / "external.json"
    monkeypatch.setattr(clean_install_check.tempfile, "mkdtemp", lambda **kwargs: str(root))
    def locked(*args):
        raise PermissionError("primary cleanup failure")
    original_write = clean_install_check.Receipt.write
    def write(receipt):
        if receipt.data.get("finished_at") and receipt.data["status"] == "FAIL":
            raise OSError("secondary receipt write failure")
        original_write(receipt)
    monkeypatch.setattr(clean_install_check.shutil, "rmtree", locked)
    monkeypatch.setattr(clean_install_check.Receipt, "write", write)
    with pytest.raises(clean_install_check.CheckFailure, match="primary cleanup failure"):
        clean_install_check.rehearse(tmp_path / "source", [], receipt_path=receipt_path)
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] != "PASS"
    assert clean_install_check._RECEIPT.get() is None


@pytest.mark.parametrize("phase", ["guard", "cleanup"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_finalization_interruption_is_never_pass(tmp_path, monkeypatch, fake_install, phase, interruption):
    receipt_path = tmp_path / "external.json"
    def interrupted(*args):
        raise interruption("finalization fixture interrupted")
    if phase == "guard":
        calls = 0
        def snapshot(*args):
            nonlocal calls
            calls += 1
            return {} if calls == 1 else interrupted()
        monkeypatch.setattr(clean_install_check, "_snapshot", snapshot)
        options = {"work_dir": tmp_path / "work"}
    else:
        root = tmp_path / "automatic"
        root.mkdir()
        monkeypatch.setattr(clean_install_check.tempfile, "mkdtemp", lambda **kwargs: str(root))
        monkeypatch.setattr(clean_install_check.shutil, "rmtree", interrupted)
        options = {}
    with pytest.raises(interruption):
        clean_install_check.rehearse(tmp_path / "source", [], receipt_path=receipt_path, **options)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert "finalization fixture interrupted" in receipt["detail"]
    assert receipt["finished_at"]
    assert clean_install_check._RECEIPT.get() is None


@pytest.mark.parametrize("phase", ["down", "processes"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_service_cleanup_interruption_retains_primary_failure(tmp_path, monkeypatch, fake_install, phase, interruption):
    def start(*args):
        raise clean_install_check.CheckFailure("startup", "primary service failure")
    def interrupted(*args):
        raise interruption("secondary service cleanup interruption")
    original_run = clean_install_check._run
    def run(stage, command, **kwargs):
        if stage == "실패 뒤 강제 종료":
            interrupted()
        return original_run(stage, command, **kwargs)
    monkeypatch.setattr(clean_install_check, "_start_services", start)
    if phase == "down":
        monkeypatch.setattr(clean_install_check, "_run", run)
    else:
        monkeypatch.setattr(clean_install_check, "_venv_python_processes", interrupted)
    with pytest.raises(BaseException) as caught:
        clean_install_check.rehearse(tmp_path / "source", [], work_dir=tmp_path / "work")
    assert isinstance(caught.value, clean_install_check.CheckFailure)
    assert caught.value.detail == "primary service failure"
    receipt = json.loads((tmp_path / "work/receipt.json").read_text(encoding="utf-8"))
    assert receipt["failure_stage"] == "startup"
    assert receipt["detail"] == "primary service failure"
    assert "secondary service cleanup interruption" in receipt["service_cleanup_error"]
    assert clean_install_check._RECEIPT.get() is None


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_startup_diagnostic_interruption_cannot_replace_startup_failure(tmp_path, monkeypatch, interruption):
    original = clean_install_check.CheckFailure("startup", "primary startup failure")
    def fail(*args, **kwargs):
        raise original
    def interrupted(*args):
        raise interruption("diagnostic interruption")
    monkeypatch.setattr(clean_install_check, "_run", fail)
    monkeypatch.setattr(clean_install_check, "_startup_journal", interrupted)
    with pytest.raises(BaseException) as caught:
        clean_install_check._start_services(tmp_path, tmp_path / "checkout", {})
    assert caught.value is original


def _break_evidence(monkeypatch, receipt, fault):
    def broken(*args, **kwargs):
        error = {"receipt_keyboard": KeyboardInterrupt, "receipt_exit": SystemExit}.get(fault, OSError)
        raise error("evidence fixture failure")
    if fault == "log":
        original = Path.write_text
        def write(path, *args, **kwargs):
            if path.parent.name == "logs":
                broken()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", write)
    else:
        monkeypatch.setattr(receipt, "write", broken)


@pytest.mark.parametrize("primary,fault", [
    ("exit", "log"), ("exit", "receipt"), ("spawn", "log"), ("spawn", "receipt"),
    ("timeout", "log"), ("timeout", "receipt"), ("expect", "receipt"),
    ("exit", "receipt_keyboard"), ("exit", "receipt_exit"),
])
def test_evidence_error_never_replaces_an_existing_failure(tmp_path, monkeypatch, primary, fault):
    receipt = clean_install_check.Receipt(tmp_path / "receipt.json", tmp_path)
    _break_evidence(monkeypatch, receipt, fault)
    token = clean_install_check._RECEIPT.set(receipt)
    try:
        if primary in ("spawn", "timeout"):
            def fail(*args, **kwargs):
                if primary == "spawn":
                    raise OSError("primary spawn failure")
                raise subprocess.TimeoutExpired(args[0], 1, stderr="primary timeout failure")
            monkeypatch.setattr(clean_install_check.subprocess, "run", fail)
        with pytest.raises(BaseException) as caught:
            if primary == "expect":
                clean_install_check._expect("primary", False, "primary check failure")
            else:
                clean_install_check._run("primary", [sys.executable, "-c", "import sys; print('primary exit failure', file=sys.stderr); sys.exit(7)"],
                                         cwd=tmp_path, env=os.environ.copy())
        assert isinstance(caught.value, clean_install_check.CheckFailure)
        assert caught.value.stage == "primary"
        assert "primary" in caught.value.detail and "evidence fixture" not in caught.value.detail
        assert receipt.data["steps"][0]["stage"] == "primary"
        assert receipt.data["steps"][0]["status"] == "FAIL"
        assert "evidence fixture failure" in receipt.data["evidence_errors"][0]["detail"]
    finally:
        clean_install_check._RECEIPT.reset(token)


@pytest.mark.parametrize("fault", ["log", "receipt"])
def test_missing_success_evidence_fails_the_check(tmp_path, monkeypatch, fault):
    receipt = clean_install_check.Receipt(tmp_path / "receipt.json", tmp_path)
    _break_evidence(monkeypatch, receipt, fault)
    token = clean_install_check._RECEIPT.set(receipt)
    try:
        with pytest.raises(BaseException) as caught:
            clean_install_check._run("success evidence", [sys.executable, "-c", "print('success')"],
                                     cwd=tmp_path, env=os.environ.copy())
        assert isinstance(caught.value, clean_install_check.CheckFailure)
        assert caught.value.stage == "success evidence"
        assert "evidence fixture failure" in caught.value.detail
    finally:
        clean_install_check._RECEIPT.reset(token)


@pytest.mark.parametrize("phase", ["write", "replace"])
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt, SystemExit])
def test_atomic_receipt_keeps_previous_json_on_failure(tmp_path, monkeypatch, phase, failure):
    import io
    receipt = clean_install_check.Receipt(tmp_path / "receipt.json", tmp_path)
    previous = receipt.path.read_bytes()
    receipt.data["status"] = "FAIL"
    if phase == "write":
        original_open = io.open
        class PartialWriter:
            def __init__(self, stream):
                self.stream = stream
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def write(self, value):
                self.stream.write(value[:8])
                self.stream.flush()
                raise failure("partial receipt write")
        def opened(file, mode="r", *args, **kwargs):
            stream = original_open(file, mode, *args, **kwargs)
            return PartialWriter(stream) if "w" in mode else stream
        monkeypatch.setattr(io, "open", opened)
    else:
        def replace(*args):
            raise failure("receipt replace failed")
        monkeypatch.setattr(clean_install_check.os, "replace", replace)
    with pytest.raises(failure):
        receipt.write()
    assert receipt.path.read_bytes() == previous
    assert json.loads(receipt.path.read_text(encoding="utf-8"))["status"] == "RUNNING"
    assert not list(tmp_path.glob(".labhq-receipt-*.tmp"))


def test_receipt_waits_out_a_transient_windows_lock(tmp_path, monkeypatch):
    receipt = clean_install_check.Receipt(tmp_path / "receipt.json", tmp_path)
    original_replace = clean_install_check.os.replace
    calls, delays = [], []
    def locked(source, target):
        calls.append((source, target))
        if len(calls) < 3:
            raise PermissionError("scanner fixture")
        original_replace(source, target)
    monkeypatch.setattr(clean_install_check, "_RECEIPT_REPLACE_RETRY", True, raising=False)
    monkeypatch.setattr(clean_install_check.os, "replace", locked)
    monkeypatch.setattr(clean_install_check, "time", RuntimeProxy(clean_install_check.time, sleep=delays.append))
    receipt.data["status"] = "PASS"
    receipt.write()
    assert len(calls) == 3 and delays == [0.05, 0.05]
    assert json.loads(receipt.path.read_text(encoding="utf-8"))["status"] == "PASS"
    assert not list(tmp_path.glob(".labhq-receipt-*.tmp"))


@pytest.mark.parametrize("windows,failure,attempts", [
    (True, PermissionError, 20), (False, PermissionError, 1), (True, OSError, 1),
])
def test_receipt_replace_retry_is_bounded_and_specific(tmp_path, monkeypatch, windows, failure, attempts):
    receipt = clean_install_check.Receipt(tmp_path / "receipt.json", tmp_path)
    previous = receipt.path.read_bytes()
    calls, delays = [], []
    def denied(*args):
        calls.append(args)
        raise failure("persistent fixture")
    monkeypatch.setattr(clean_install_check, "_RECEIPT_REPLACE_RETRY", windows, raising=False)
    monkeypatch.setattr(clean_install_check.os, "replace", denied)
    monkeypatch.setattr(clean_install_check, "time", RuntimeProxy(clean_install_check.time, sleep=delays.append))
    with pytest.raises(failure):
        receipt.write()
    assert len(calls) == attempts and len(delays) == attempts - 1
    assert receipt.path.read_bytes() == previous
    assert not list(tmp_path.glob(".labhq-receipt-*.tmp"))
