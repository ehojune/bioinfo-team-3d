"""Use the real repository lint; no binary/handle blanket exemptions or native calls."""
import io
from pathlib import Path

import pytest

from labhq import cli, runtime_readiness
from labhq.settings import Settings
from tests import test_text_encoding as lint


def check_source(tmp_path, monkeypatch, sources):
    package = tmp_path / "labhq"
    package.mkdir()
    (tmp_path / "scripts").mkdir()
    for name, source in sources.items():
        (package / name).write_text(source, encoding="utf-8")
    monkeypatch.setattr(lint, "ROOT", tmp_path)
    lint.test_package_and_scripts_text_io_declares_encoding()


def test_real_classifier_accepts_assigned_modules_without_exemptions(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    check_source(tmp_path, monkeypatch, {name: (root / "labhq" / name).read_text(encoding="utf-8")
                                       for name in ("runtime_readiness.py", "windows_broker.py")})


@pytest.mark.parametrize("source", [
    'open("file.txt")', 'open("file.txt", "r")', 'open("file.txt", mode="w")',
    'journal.open(mode="r")', 'journal.open("w")', 'native.open("file.txt")',
    'path.read_text()', 'path.write_text("hello")', 'path.read_text(errors="replace")',
])
def test_real_classifier_still_rejects_text_io_without_encoding(tmp_path, monkeypatch, source):
    with pytest.raises(AssertionError, match="text I/O without explicit encoding"):
        check_source(tmp_path, monkeypatch, {"counterexample.py": source})


@pytest.mark.parametrize("source", [
    'open("data.bin", "rb")', 'journal.open(mode="rb")',
    'native.open_process(pid)', 'native.open_process(pid, terminate=False)',
    'journal.open(mode="r", encoding="utf-8")', 'path.read_text(encoding="utf-8")',
])
def test_real_classifier_accepts_explicit_binary_handle_or_encoded_text(tmp_path, monkeypatch, source):
    check_source(tmp_path, monkeypatch, {"explicit.py": source})


def test_binary_journal_tail_reads_stay_bounded(tmp_path, monkeypatch):
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path)},
                                        "runner": {"state_dir": str(tmp_path)}})
    journal = cli._launch_state(settings) / "logs/detached-launch.jsonl"
    data = b"x" * (3 * 65536) + b'\n{"role":"runner-supervisor","status":"recorded"}\n'
    opened = []
    class Tail(io.BytesIO):
        def read(self, size=-1):
            assert len(data) - self.tell() <= 65536
            return super().read(size)
        def readline(self, size=-1):
            assert len(data) - self.tell() <= 65536
            return super().readline(size)
    original = Path.open
    def binary_open(path, *args, **kwargs):
        if path == journal:
            assert not args and kwargs == {"mode": "rb"}
            opened.append(path)
            return Tail(data)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", binary_open)
    monkeypatch.setattr(cli, "_process_record", lambda *a, **kw: ("missing", None, None))
    result = runtime_readiness.inspect(settings)
    assert len(opened) == 2
    assert result["ready"] is False and result["roles"]["runner"]["state"] == "missing"
    assert result["roles"]["gateway"]["state"] == "unmanaged"
