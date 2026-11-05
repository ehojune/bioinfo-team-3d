"""replace_file waits out a Windows scanner holding a path open, and nothing else (found by a 1-in-13 local
PermissionError in test_round_records on Windows; POSIX is untouched)."""

import os

import pytest

from labhq import util
from tests.support.time_proxy import RuntimeProxy


def _fail_then_replace(monkeypatch, failures):
    real, calls = os.replace, []

    def flaky(source, target):
        calls.append((source, target))
        if len(calls) <= failures:
            raise PermissionError(5, "Access is denied")
        real(source, target)

    monkeypatch.setattr(util.os, "replace", flaky)
    monkeypatch.setattr(util, "time", RuntimeProxy(util.time, sleep=lambda _seconds: None))
    return calls


def test_a_brief_windows_hold_is_waited_out(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "REPLACE_RETRY", True)
    calls = _fail_then_replace(monkeypatch, 3)
    (tmp_path / "a.tmp").write_text("new", encoding="utf-8")
    (tmp_path / "a").write_text("old", encoding="utf-8")
    util.replace_file(tmp_path / "a.tmp", tmp_path / "a")
    assert (tmp_path / "a").read_text(encoding="utf-8") == "new" and len(calls) == 4


def test_a_hold_that_never_ends_still_raises_after_the_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "REPLACE_RETRY", True)
    calls = _fail_then_replace(monkeypatch, 99)
    (tmp_path / "a.tmp").write_text("new", encoding="utf-8")
    with pytest.raises(PermissionError):
        util.replace_file(tmp_path / "a.tmp", tmp_path / "a", attempts=5)
    assert len(calls) == 5


def test_posix_and_other_errors_raise_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "REPLACE_RETRY", False)
    calls = _fail_then_replace(monkeypatch, 99)
    (tmp_path / "a.tmp").write_text("new", encoding="utf-8")
    with pytest.raises(PermissionError):
        util.replace_file(tmp_path / "a.tmp", tmp_path / "a")
    assert len(calls) == 1
    monkeypatch.setattr(util.os, "replace", lambda *_a: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(util, "REPLACE_RETRY", True)
    with pytest.raises(FileNotFoundError):
        util.replace_file(tmp_path / "missing", tmp_path / "a")
