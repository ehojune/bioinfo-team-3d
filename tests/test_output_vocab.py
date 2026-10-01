"""Output data type vocabulary (#221 todo 2, #151): local keys, optional EDAM subset, versions, packaging."""

import ast
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from labhq import vocab
from labhq.yaml_unique import UniqueKeyError, load_yaml_unique

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "labhq"
# An EDAM id written out (data_0006, format_1930, ...). Only the generated subset may hold one.
EDAM_ID_TEXT = re.compile(r"(?<![A-Za-z0-9])(?:data|format|operation|topic)_[0-9]{4}(?![0-9])")


@pytest.fixture
def vdir(tmp_path):
    target = tmp_path / "vocab"
    target.mkdir()
    shutil.copy(vocab.VOCAB_DIR / vocab.LOCAL_FILE, target / vocab.LOCAL_FILE)
    return target


def subset_for(local_dir, overrides=None):
    terms = yaml.safe_load((local_dir / vocab.LOCAL_FILE).read_text(encoding="utf-8"))["terms"]
    body = {k: {"branch": t["branch"], "id": "unknown", "reason": "no_candidate"} for k, t in terms.items()}
    body.update(overrides or {})
    return {"source": {"url": "https://example.invalid/EDAM.owl", "release": "test", "sha256": "0" * 64,
                       "license": "https://creativecommons.org/licenses/by-sa/4.0"}, "terms": body}


def test_packaged_vocabulary_loads_with_counts_under_the_cap():
    v = vocab.load()
    counts = v.counts()
    assert counts["keys"] <= vocab.MAX_KEYS and counts["edam_terms"] <= vocab.MAX_KEYS
    assert (counts["data"], counts["format"], counts["operation"]) == (18, 14, 6)
    assert v.is_key("data", "raw_counts") and not v.is_key("format", "raw_counts")
    assert re.fullmatch(r"[0-9a-f]{64}", v.sha256)


def test_version_ignores_line_endings_and_comments_but_not_meaning(vdir):
    base = vocab.load(vdir).sha256
    text = (vdir / vocab.LOCAL_FILE).read_text(encoding="utf-8")
    (vdir / vocab.LOCAL_FILE).write_bytes(("# another comment\n" + text).replace("\n", "\r\n").encode("utf-8"))
    assert vocab.load(vdir).sha256 == base
    (vdir / vocab.LOCAL_FILE).write_text(text.replace("tab-separated values", "tab separated text"), encoding="utf-8")
    assert vocab.load(vdir).sha256 != base


def test_review_marks_do_not_change_the_version(vdir):
    base = vocab.load(vdir).sha256
    text = (vdir / vocab.LOCAL_FILE).read_text(encoding="utf-8")
    (vdir / vocab.LOCAL_FILE).write_text(text.replace("review: pending", "review: pi_2026-10-02", 1), encoding="utf-8")
    assert vocab.load(vdir).sha256 == base


@pytest.mark.parametrize("mutate, match", [
    (lambda t: t + "\n  raw_counts:\n    branch: data\n    definition: twice\n", "duplicate key"),
    (lambda t: t.replace("branch: format\n    definition: \"tab-separated values\"",
                         "branch: table\n    definition: \"tab-separated values\""), "needs a branch"),
    (lambda t: t.replace('extensions: [".csv"]', 'extensions: [".tsv"]'), "belongs to both"),
    (lambda t: t.replace("version: 1", "version: 2"), "version: 1"),
])
def test_bad_local_file_is_refused(vdir, mutate, match):
    path = vdir / vocab.LOCAL_FILE
    path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(vocab.VocabError, match=match):
        vocab.load(vdir)


def test_more_than_forty_keys_is_refused(vdir):
    path = vdir / vocab.LOCAL_FILE
    extra = "".join(f"  extra_{i}:\n    branch: data\n    definition: x\n" for i in range(3))
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    with pytest.raises(vocab.VocabError, match="1 to 40"):
        vocab.load(vdir)


def test_missing_vocabulary_turns_declarations_off_without_raising(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(vocab, "VOCAB_DIR", tmp_path)
    vocab.reset_cache()
    try:
        with caplog.at_level(logging.WARNING, logger="labhq.vocab"):
            assert vocab.current() is None
        assert "declarations stay off" in caplog.text
    finally:
        vocab.reset_cache()


def test_without_a_subset_every_key_is_local(vdir):
    v = vocab.load(vdir)
    assert v.edam_sha256 is None and v.edam_problem is None and v.edam_ids == frozenset()
    assert all(t.edam_id is None for t in v.terms.values())


def test_a_valid_subset_maps_keys_and_moves_the_version(vdir):
    local = vocab.load(vdir)
    (vdir / vocab.SUBSET_FILE).write_text(yaml.safe_dump(subset_for(vdir, {
        "tsv": {"branch": "format", "id": "format_" + "9999"}})), encoding="utf-8")
    v = vocab.load(vdir)
    assert v.edam_id("tsv") == "format_" + "9999" and v.edam_id("csv") is None
    assert v.terms["csv"].edam_reason == "no_candidate"
    assert v.local_sha256 == local.local_sha256 and v.sha256 != local.sha256


@pytest.mark.parametrize("change, problem", [
    (lambda s: s["terms"].pop("tsv"), "subset_keys_mismatch"),
    (lambda s: s["terms"].update(tsv={"branch": "format", "id": "data_" + "9999"}), "subset_id"),
    (lambda s: s["terms"].update(tsv={"branch": "data", "id": "unknown", "reason": "no_match"}),
     "subset_branch_mismatch"),
    (lambda s: s["terms"].update(tsv={"branch": "format", "id": "unknown", "reason": "guess"}), "subset_reason"),
    (lambda s: s["source"].update(sha256="short"), "subset_source"),
])
def test_a_damaged_subset_is_ignored_and_keys_stay_local(vdir, change, problem, caplog):
    subset = subset_for(vdir)
    change(subset)
    (vdir / vocab.SUBSET_FILE).write_text(yaml.safe_dump(subset), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="labhq.vocab"):
        v = vocab.load(vdir)
    assert v.edam_problem == problem and v.edam_sha256 is None and v.edam_ids == frozenset()
    assert v.is_key("format", "tsv")
    assert problem in caplog.text


def test_unparsable_subset_is_ignored(vdir):
    (vdir / vocab.SUBSET_FILE).write_text("terms: [\n", encoding="utf-8")
    assert vocab.load(vdir).edam_problem == "subset_unreadable"


@pytest.mark.parametrize("name, key", [
    ("outputs/reads_R1.fastq.gz", "fastq"), ("outputs/x.FQ", "fastq"), ("outputs/genome.fa", "fasta"),
    ("outputs/t.TSV", "tsv"), ("outputs/a.gff3", "gff3"), ("outputs/a.gff", None), ("outputs/a.txt", None),
    ("outputs/run.log", None), ("outputs/x.py", None), ("outputs/x.tar.gz", None), ("outputs/noext", None),
    ("outputs\\win\\answer.md", "markdown"),
])
def test_format_from_extension(name, key):
    assert vocab.load().format_of(name) == key


def test_shared_yaml_loader_refuses_duplicate_keys_without_a_path():
    with pytest.raises(UniqueKeyError, match=r"where\.yaml: line 2 .*duplicate key 'a'"):
        load_yaml_unique("a: 1\na: 2\n", "where.yaml")


def test_vocabulary_import_loads_no_semantics_module():
    code = ("import sys, labhq.vocab, labhq.vocab.declare; "
            "print(sorted(m for m in sys.modules if 'semantics' in m))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "[]"


def test_package_data_ships_the_vocabulary():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    patterns = ast.literal_eval(re.search(r'labhq = (\[[\s\S]*?\])', config)[1])
    included = {p for pattern in patterns for p in PACKAGE.glob(pattern) if p.is_file()}
    needed = {p for p in (PACKAGE / "vocab").iterdir() if p.suffix in {".yaml", ".md"}}
    # Windows may import this copy through its 8.3 TEMP alias (RUNNER~1) while ROOT uses the long path.
    local = vocab.VOCAB_DIR / vocab.LOCAL_FILE
    assert any(path.samefile(local) for path in needed) and needed <= included


def _scanned():
    for folder in (PACKAGE, ROOT / "agents", ROOT / "config"):
        for path in folder.rglob("*"):
            if path.suffix in {".py", ".yaml", ".yml", ".json"} and "__pycache__" not in path.parts:
                yield path


def test_no_edam_id_is_written_outside_the_generated_subset():
    subset = PACKAGE / "vocab" / vocab.SUBSET_FILE
    hits = [f"{p.relative_to(ROOT).as_posix()}: {m.group(0)}" for p in _scanned() if p != subset
            for m in EDAM_ID_TEXT.finditer(p.read_text(encoding="utf-8", errors="replace"))]
    assert hits == []
