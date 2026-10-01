"""Semantics pilot (#127): fixed expected answers, model B, baseline A and their isolation."""

from __future__ import annotations

import hashlib
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "semantics"
RECORDS = FIXTURE / "records"

# Fixed after the one independent review and correction 1. Changing either is a pilot correction
# (a separate commit with its reason) or an experiment failure, never a routine update.
EXPECTED_SHA256 = "e8f094595f3e4c19056df37ac2ee54425c8d5d20499e312e6f59671e05671c02"
FIXTURE_SHA256 = "24dce1ed83a06b78d000606111a785e6eb388e0c39c262ecad4442b1700cafeb"


def inventory_sha256(root: Path) -> str:
    """sha256 over 'relative posix path LF file sha256 LF' for every file under root, in path order."""
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        digest.update(f"{path.relative_to(root).as_posix()}\n{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
                      .encode())
    return digest.hexdigest()


def test_expected_answers_are_the_pinned_version():
    assert hashlib.sha256((FIXTURE / "expected.yaml").read_bytes()).hexdigest() == EXPECTED_SHA256


def test_fixture_records_are_the_pinned_version():
    assert inventory_sha256(RECORDS) == FIXTURE_SHA256
