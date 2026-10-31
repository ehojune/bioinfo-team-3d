"""Per-test time limit (#1198): one stuck test fails alone, with its name, and the job limits stay as they were."""
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_suite as suite
from scripts.ci_selection import ROOT


def fake_item(limit):
    return SimpleNamespace(nodeid="tests/test_x.py::test_y", config=SimpleNamespace(getini=lambda name: limit))


@pytest.mark.parametrize("value,expected", [("300", 300.0), (300, 300.0), ("0", 0.0), ("", 0.0), (None, 0.0), ("x", 0.0), (-5, 0.0)])
def test_the_limit_is_the_timeout_ini_or_zero(value, expected):
    assert suite.test_limit(fake_item(value).config) == expected


def test_the_test_is_named_before_the_limit_fires(monkeypatch):
    named = []
    monkeypatch.setattr(suite, "announce_limit", named.append)
    monkeypatch.setattr(suite, "TEST_LIMIT_NOTICE_S", 0)
    with suite.limit_notice(fake_item(0.2)):
        deadline = suite.time.monotonic() + 5
        while not named and suite.time.monotonic() < deadline:
            suite.time.sleep(0.05)
    assert named == ["tests/test_x.py::test_y"]


def test_a_test_that_ends_first_is_never_named(monkeypatch):
    named = []
    monkeypatch.setattr(suite, "announce_limit", named.append)
    monkeypatch.setattr(suite, "TEST_LIMIT_NOTICE_S", 0)
    with suite.limit_notice(fake_item(0.3)):
        pass
    suite.time.sleep(0.6)
    assert named == []


def test_no_limit_starts_no_timer(monkeypatch):
    before = suite.threading.active_count()
    with suite.limit_notice(fake_item(0)):
        assert suite.threading.active_count() == before


@pytest.mark.skipif(sys.platform == "win32", reason="the signal method needs SIGALRM; Windows uses the thread method")
def test_a_stuck_test_fails_alone_under_the_signal_method(tmp_path):
    (tmp_path / "test_stuck.py").write_text(textwrap.dedent("""
        import threading
        def test_stuck():
            threading.Event().wait(60)
        def test_after():
            pass
    """), encoding="utf-8")
    run = subprocess.run([sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "timeout=1",
                          "-o", "timeout_method=signal", str(tmp_path)], cwd=tmp_path, capture_output=True, text=True,
                         timeout=60)
    assert "1 failed, 1 passed" in run.stdout, run.stdout
    assert "Timeout >1.0s" in run.stdout and "test_stuck" in run.stdout


def test_ci_pins_the_limit_per_platform_and_keeps_the_job_limits():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pytest-timeout==2.4.0"' in project and "timeout = 300" in project
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]
    linux = [s for s in jobs["pytest"]["steps"] if "pytest" in (s.get("run") or "")]
    assert len(linux) == 2
    assert all("timeout_method=signal" in s["run"] for s in linux)
    windows = [s["run"] for s in jobs["pytest-windows"]["steps"] if "pytest -q" in (s.get("run") or "")]
    assert len(windows) == 2 and all("timeout_method=thread" in r for r in windows)
    assert any(s.get("timeout-minutes") == 25 for s in jobs["pytest"]["steps"])
