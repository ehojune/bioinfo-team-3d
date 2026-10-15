"""Nothing in the package or the tests may collect a Windows process tree by parent PID alone (#589).

Windows reuses the PID of a parent that exited. A tree collected from that number (the tree switch of the Windows
kill-by-PID command) then holds whatever unrelated process owns it now, and the PI's gateway and runner when their
launcher's recorded parent is that number. #597 moved labhq's own kill paths to `labhq.proc_tree`, which checks
creation times; this keeps a new `/T` from creeping back into the package or into a test.

The scan reads every text file under `tests/` and `labhq/`, comments and docstrings included, so a spelling that
only appears in a comment is also reworded. The command's name is written in two parts below, so this file does not
match its own scan.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "task" + "kill"
# The command, then a `/T` or `-T` switch in the same statement: after a space, quote, comma or bracket, or glued to
# another switch (`/F/T`). `.{0,300}` reaches across the lines of an argument list.
TREE_SWITCH = re.compile(
    COMMAND + r".{0,300}?(?:(?<=[\s\"'`,(\[])|(?<=/[A-Za-z]))[/-]t(?![A-Za-z0-9_])",
    re.IGNORECASE | re.DOTALL,
)
SKIP_DIRS = {"__pycache__", "node_modules", ".pytest_cache"}


def _text_files(folder: str):
    for path in sorted((ROOT / folder).rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.parts):
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue  # a binary file (a compiled module, an image)
        yield path, data.decode("utf-8", errors="replace")


def _offenders(folders=("tests", "labhq")) -> list[str]:
    found = []
    for folder in folders:
        for path, text in _text_files(folder):
            for match in TREE_SWITCH.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    return found


def test_no_file_in_the_package_or_the_tests_spells_the_tree_switch():
    assert _offenders() == []


def test_the_scan_covers_both_folders():
    scanned = {path.relative_to(ROOT).parts[0] for folder in ("tests", "labhq") for path, _ in _text_files(folder)}
    assert scanned == {"tests", "labhq"}
    assert any(path.name == "cli.py" for path, _ in _text_files("labhq"))
    assert any(path.name == Path(__file__).name for path, _ in _text_files("tests"))


@pytest.mark.parametrize("text", [
    COMMAND + " /PID 12 /T /F",
    COMMAND + " /F /PID 12 /t",
    COMMAND + ".exe /pid 12 /T",
    COMMAND + " -t -pid 12",
    COMMAND + " /F/T /PID 12",
    f'["{COMMAND}", "/PID", str(pid), "/T", "/F"]',
    f"['{COMMAND}', '/F', '/t']",
    f'["{COMMAND}",\n    "/PID", str(pid),\n    "/F",\n    "/T"]',
    f'subprocess.run(f"{COMMAND} /pid {{pid}} /t /f")',
    f"`{COMMAND} /T`",
])
def test_the_scan_catches_every_spelling_of_the_tree_switch(text):
    assert TREE_SWITCH.search(text), text


@pytest.mark.parametrize("text", [
    COMMAND + " /PID 12 /F",
    COMMAND + " /F /IM labhq.exe",
    f'["{COMMAND}", "/PID", str(pid), "/F"]',
    "kill the tree with proc_tree.kill_tree",
    COMMAND + " /PID 12 /F and a /tmp folder",  # `/t` that starts a word is not the switch
])
def test_the_scan_leaves_per_pid_kills_and_ordinary_text_alone(text):
    assert not TREE_SWITCH.search(text), text
