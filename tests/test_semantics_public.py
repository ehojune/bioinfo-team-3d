"""The semantics pilot (#127) files carry no local absolute path or e-mail address.

This is a public repository and a commit stays in history, so the pilot's files are scanned on disk,
untracked ones included, before they can be committed. Set LABHQ_PRIVATE_TERMS_FILE to a file kept
outside the repository (one term per line) to also refuse private words locally.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ["labhq/research/semantics*", "tests/*semantics*", "scripts/semantics*", "docs/reference/semantics*",
            "tests/fixtures/semantics/**/*"]

# Built from pieces so this file does not match itself.
DRIVE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![\\/])")
HOME = re.compile("|".join(re.escape(sep.join(["", name, ""])) for name in ("home", "Users") for sep in ("/", "\\")))
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def pilot_files() -> list[Path]:
    found = set()
    for pattern in PATTERNS:
        found |= {p for p in ROOT.glob(pattern) if p.is_file() and "__pycache__" not in p.parts}
    return sorted(found)


def test_the_scan_sees_the_pilot_files():
    names = {p.relative_to(ROOT).as_posix() for p in pilot_files()}
    assert {"labhq/research/semantics.py", "labhq/research/semantics_v1.yaml", "tests/semantics_baseline.py",
            "tests/fixtures/semantics/expected.yaml", "scripts/semantics_pilot.py"} <= names


@pytest.mark.parametrize("path", pilot_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_absolute_path_or_email(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = [m.group(0) for regex in (DRIVE, HOME, EMAIL) for m in regex.finditer(text)]
    assert hits == []


def test_no_private_terms_when_a_local_list_is_given():
    source = os.environ.get("LABHQ_PRIVATE_TERMS_FILE")
    if not source:
        pytest.skip("LABHQ_PRIVATE_TERMS_FILE not set (local pre-commit check only)")
    terms = [t.strip() for t in Path(source).read_text(encoding="utf-8").splitlines() if t.strip()]
    hits = [(p.relative_to(ROOT).as_posix(), t) for p in pilot_files()
            for t in terms if t.casefold() in p.read_text(encoding="utf-8", errors="replace").casefold()]
    assert hits == []


def test_the_patterns_catch_what_they_refuse():
    drive, home = "D" + ":" + "/x", "/" + "home" + "/u"
    assert DRIVE.search(drive) and DRIVE.search("C" + ":" + "\\x") and HOME.search(home)
    assert EMAIL.search("a.b" + "@" + "example.org")
    assert not DRIVE.search("https://doi.org/10.5555/x") and not EMAIL.search("pack single_cell_de@2")
