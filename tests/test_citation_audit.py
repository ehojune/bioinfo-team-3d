"""Citation audit and the offline evaluation-suite fixtures (#1383). No network, no model calls."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import citation_audit as A  # noqa: E402

SUITE = ROOT / "tests" / "fixtures" / "eval_suite"
AUDIT = SUITE / "citation_audit"
CAL = SUITE / "review_calibration"
ERROR_TYPES = {"direction", "table_text_mismatch", "denominator", "citation", "shared_source", "missing_run",
               "contrast_reversed", "multiple_testing", "covariate_dropped", "outlier_unreported"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_recorded_fixture_gets_the_expected_grade_for_every_citation():
    data = load(AUDIT / "citations.json")
    result = A.audit(data["citations"], load(AUDIT / "records.json"), AUDIT)
    assert {row["id"]: row["grade"] for row in result["citations"]} == data["expected"]
    assert result["counts"] == {"ok": 1, "minor": 3, "major": 6}


@pytest.mark.parametrize("cited, registered, verdict", [
    ("Glucocorticoid response: a study", "Glucocorticoid Response - A Study", "match"),
    ("A synthetic cohort study of serum marker: worked example",
     "A synthetic cohort study of serum markers: a worked example", "near"),
    ("Deep learning for proteins", "Soil microbiome diversity", "mismatch"),
])
def test_title_match(cited, registered, verdict):
    assert A.title_match(cited, registered) == verdict


def test_numbers_are_checked_at_written_precision_and_sign():
    records = {"doi:10.5555/x": {"found": True, "title": "T"}}
    row = A.audit_one({"id": "n", "doi": "10.5555/X", "title": "T", "source": "sources/paper_a.txt",
                       "numbers": ["3.0", "2.97", "-2.97", "95.7"]}, records, AUDIT)
    held = [f["detail"] for f in row["findings"]]
    assert row["grade"] == "major"
    assert any("-2.97" in d for d in held) and not any(" 2.97 " in d for d in held)
    assert not any("3.0" in d for d in held) and not any("95.7" in d for d in held)


def test_a_source_outside_the_root_is_not_read(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("the exact quote", encoding="utf-8")
    root = tmp_path / "bundle"
    root.mkdir()
    row = A.audit_one({"id": "o", "doi": "10.5555/x", "source": "../outside.txt", "quotes": ["the exact quote"]},
                      {"doi:10.5555/x": {"found": True, "title": "T"}}, root)
    assert [f["check"] for f in row["findings"]] == ["source"] and row["grade"] == "minor"


def test_cli_exit_codes(tmp_path):
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "citation_audit.py"), *map(str, a)],
                                    capture_output=True, text=True, encoding="utf-8")
    out = tmp_path / "result.json"
    major = run(AUDIT / "citations.json", "--records", AUDIT / "records.json", "--json", out)
    assert major.returncode == 1 and load(out)["counts"]["major"] == 6
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"citations": [load(AUDIT / "citations.json")["citations"][0]]}), encoding="utf-8")
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "paper_a.txt").write_text((AUDIT / "sources" / "paper_a.txt").read_text(encoding="utf-8"),
                                                      encoding="utf-8")
    assert run(clean, "--records", AUDIT / "records.json").returncode == 0
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert run(bad, "--records", AUDIT / "records.json").returncode == 2


def test_seeded_bundle_differs_from_clean_only_where_the_answer_key_says():
    key = load(CAL / "seeded" / "ERRORS.json")["errors"]
    assert len(key) == 10 and {e["type"] for e in key} == ERROR_TYPES
    assert len({e["id"] for e in key}) == 10
    assert sum(e["conclusion_changing"] for e in key) == 4
    clean = {p.relative_to(CAL / "clean").as_posix() for p in (CAL / "clean").rglob("*") if p.is_file()}
    seeded = {p.relative_to(CAL / "seeded").as_posix() for p in (CAL / "seeded").rglob("*") if p.is_file()}
    assert seeded - clean == {"ERRORS.json"}
    assert clean - seeded == {"sensitivity.csv"}  # the missing_run error removes the output
    changed = {name for name in clean & seeded
               if (CAL / "clean" / name).read_bytes() != (CAL / "seeded" / name).read_bytes()}
    assert changed == {e["file"] for e in key}


def test_clean_bundle_is_internally_consistent():
    clean = CAL / "clean"
    report = (clean / "report.md").read_text(encoding="utf-8")
    rows = {line.split(",")[1]: line.split(",") for line in (clean / "de_results.csv").read_text().splitlines()[1:]}
    for gene in ("DUSP1", "KLF15", "PER1", "TSC22D3", "ZBTB16", "FKBP5"):
        lfc = rows[gene][3]
        assert float(lfc) > 0 and lfc in report
    rates = [float(line.split("\t")[3]) for line in (clean / "mapping_rates.tsv").read_text().splitlines()[1:]]
    assert f"{min(rates):.2f} to {max(rates):.2f}" in report
    log = (clean / "run_log.tsv").read_text(encoding="utf-8")
    assert "~ cell + dex; contrast dex trt vs untrt" in log and "sensitivity.csv" in log


def test_falsification_frames_stay_unanswered():
    frames = load(SUITE / "falsification" / "cases.json")
    assert len(frames["cases"]) == sum(frames["target_split"].values()) == 6
    assert set(frames["target_split"]) == set(frames["answer_values"])
    for case in frames["cases"]:
        assert case["answer"] is None and case["answer_rationale"] is None
        assert case["question"] and case["counterevidence"] and case["fixed_next_action"]
