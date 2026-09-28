"""Text files and console output keep working under the Windows locale encoding."""

import ast
import io
import sys
from pathlib import Path

from labhq import cli


ROOT = Path(__file__).resolve().parents[1]


def test_package_and_scripts_text_io_declares_encoding():
    missing = []
    for directory in (ROOT / "labhq", ROOT / "scripts"):
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name not in {"read_text", "write_text", "open"}:
                    continue
                if name == "open" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os":
                    continue
                if any(keyword.arg == "encoding" for keyword in node.keywords):
                    continue
                if name == "open":
                    mode = node.args[1] if len(node.args) > 1 else next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "mode"), None)
                    if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value:
                        continue
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not missing, "text I/O without explicit encoding: " + ", ".join(missing)


def test_cli_preserves_cp949_and_replaces_emoji(monkeypatch):
    output = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
    error = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", error)
    monkeypatch.setattr(cli.Settings, "load", lambda _: object())
    monkeypatch.setattr(cli, "_api", lambda *_args: [{"id": "cso", "name": "한국어", "engine": "mock"}])
    cli.main(["agents"])
    output.flush()
    assert output.encoding == "cp949"
    assert output.errors == "replace" and error.errors == "replace"
    rendered = output.buffer.getvalue().decode("cp949")
    assert "한국어" in rendered and "?" in rendered
