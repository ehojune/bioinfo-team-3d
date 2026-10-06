"""docs/vocabulary.md is generated from labhq/vocab and must match it (PI request 2026-10-07)."""

import subprocess
import sys
from pathlib import Path

from scripts import vocab_tables

ROOT = Path(__file__).resolve().parents[1]


def test_committed_vocabulary_doc_matches_the_vocab_files():
    committed = (ROOT / "docs" / "vocabulary.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == vocab_tables.render(), "run: python scripts/vocab_tables.py --write"


def test_doc_lists_every_topic_and_each_checklist_item():
    text = vocab_tables.render()
    assert "| `bulk_rna_seq` |" in text and "| `proteomics` |" in text
    assert "### single_cell_rna_seq" in text and "| `batch` |" in text
    assert "## 데이터 종류(data)" in text and "## 파일 형식(format)" in text and "## 작업(operation)" in text
    assert "Ensembl VEP" in text


def test_check_mode_reports_up_to_date():
    done = subprocess.run([sys.executable, "scripts/vocab_tables.py", "--check"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
