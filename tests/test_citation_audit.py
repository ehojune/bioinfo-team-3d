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
    # Registry markup is formatting, not a different title (#910 11th run: Europe PMC "<i>KRAS</i>").
    ("Sotorasib for Lung Cancers with KRAS p.G12C Mutation.",
     "Sotorasib for Lung Cancers with <i>KRAS</i> p.G12C Mutation.", "match"),
    ("Outcomes in KRAS(G12C) & STK11 tumors", "Outcomes in <sup>KRAS</sup>(G12C) &amp; STK11 tumors", "match"),
    ("treated with KRAS(G12C) mutation", "treated with KRAS<sup>G12C</sup> mutation", "match"),
    ("treated with KRAS(G12C) inhibitors", "treated with KRAS&lt;sup&gt;G12C&lt;/sup&gt; inhibitors", "match"),
    ("CO2 effects on stomata", "CO<sub>2</sub> effects on stomata", "match"),  # a tag inside a word joins it
    ("Ca2+ signalling in neurons", "Ca<sup>2+</sup> signalling in neurons", "match"),
    ("KRAS(G12C) and CO2", "KRAS<sup>G12C</sup> and CO<sub>2</sub>", "match"),  # one tag breaks, one joins
    ("Deep learning for proteins", "<i>Soil</i> microbiome diversity", "mismatch"),
])
def test_title_match(cited, registered, verdict):
    assert A.title_match(cited, registered) == verdict


@pytest.mark.parametrize("pointer", ["# 포인터: CodeBreaK 100 (10.1056/NEJMoa2103695)\n\n- 초록 파일: inputs/x.md\n",
                                     "\n# Pointer: trial (10.5555/x)\n- text file: .tmp/literature/x.md\n"])
def test_a_pointer_file_is_no_source_text_not_a_missing_number(tmp_path, pointer):
    """#910 11th run: the bundle keeps only a pointer to a third-party text, whose numbers are therefore unchecked."""
    (tmp_path / "texts").mkdir()
    (tmp_path / "texts" / "p.md").write_text(pointer + "ORR 37.1%\n", encoding="utf-8")
    row = A.audit_one({"id": "p", "doi": "10.5555/x", "source": "texts/p.md", "numbers": ["37.1", "6.8"],
                       "quotes": ["not in the pointer"]}, {"doi:10.5555/x": {"found": True, "title": "T"}}, tmp_path)
    assert row["grade"] == "minor"
    assert [f["check"] for f in row["findings"]] == ["source"] and "only points at" in row["findings"][0]["detail"]


@pytest.mark.parametrize("heading", ["# Methods\nThe pointer heuristic is not used here.",
                                     "# Pointer analysis of the cohort", "# 포인터 기법의 한계"])
def test_a_text_that_merely_mentions_a_pointer_is_still_a_source(tmp_path, heading):
    """Only the marker with its colon ("# Pointer:") makes a pointer; a real heading that starts with the word does not."""
    (tmp_path / "s.md").write_text(heading + "\nORR 37.1%\n", encoding="utf-8")
    row = A.audit_one({"id": "s", "doi": "10.5555/x", "source": "s.md", "numbers": ["37.1", "6.8"]},
                      {"doi:10.5555/x": {"found": True, "title": "T"}}, tmp_path)
    assert row["grade"] == "major" and [f["check"] for f in row["findings"]] == ["number"]


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


def files(folder):
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("bundle, missing", [("seeded", {"sensitivity.csv"}), ("seeded_contrast", set())])
def test_each_seeded_bundle_differs_from_clean_only_where_its_answer_key_says(bundle, missing):
    key = load(CAL / bundle / "ERRORS.json")["errors"]
    clean, seeded = files(CAL / "clean"), files(CAL / bundle)
    assert seeded - clean == {"ERRORS.json"}
    assert clean - seeded == missing  # the missing_run error removes the sensitivity output
    changed = {name for name in clean & seeded
               if (CAL / "clean" / name).read_bytes() != (CAL / bundle / name).read_bytes()}
    assert changed == {e["file"] for e in key}


def test_the_answer_keys_cover_ten_error_types_four_changing_the_conclusion():
    key = load(CAL / "seeded" / "ERRORS.json")["errors"] + load(CAL / "seeded_contrast" / "ERRORS.json")["errors"]
    assert len(key) == 10 and {e["type"] for e in key} == ERROR_TYPES
    assert len({e["id"] for e in key}) == 10
    assert sum(e["conclusion_changing"] for e in key) == 4
    # a reversed contrast flips every sign, so it never shares a bundle with a direction error
    main = {e["type"] for e in load(CAL / "seeded" / "ERRORS.json")["errors"]}
    assert "contrast_reversed" not in main and "direction" in main


def test_citation_errors_are_detectable_from_the_bundle_alone():
    seeded = CAL / "seeded"
    rows = A.audit(load(seeded / "citations.json")["citations"], load(seeded / "records.json"), seeded)["citations"]
    assert {r["id"]: r["grade"] for r in rows}["himes"] == "major"  # E04: the DOI names another registered work
    clean = A.audit(load(CAL / "clean" / "citations.json")["citations"], load(CAL / "clean" / "records.json"),
                    CAL / "clean")
    assert clean["counts"]["major"] == 0


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


@pytest.mark.parametrize("number, held", [
    ("1e-5", True), ("1.0e-5", True), ("2.3e-8", True), ("1e4", True), ("0.00001", True),
    ("1e-4", False), ("1e-6", False), ("2.4e-8", False), ("0.3", False),
])
def test_scientific_notation_is_compared_at_its_significant_digits(number, held):
    source = A.Source("p was 1e-5, q 2.3 × 10^-8, r = 0.2 and s 1.0E+04")
    assert A.number_held(number, source) is held


def test_a_tiny_claim_is_not_held_by_a_value_that_rounds_to_zero():
    assert A.number_held("1e-5", A.Source("the effect was 0.2")) is False


def test_null_quote_or_number_lists_are_bad_input(tmp_path):
    cites = tmp_path / "c.json"
    cites.write_text(json.dumps({"citations": [{"id": "x", "doi": "10.5555/x", "quotes": None, "numbers": "1.5"}]}),
                     encoding="utf-8")
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "citation_audit.py"), str(cites),
                          "--records", str(AUDIT / "records.json")], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 2 and "numbers must be a list" in run.stderr
    assert A.audit_one({"id": "y", "doi": "10.5555/x", "quotes": None, "numbers": None},
                       {"doi:10.5555/x": {"found": True, "title": "T"}}, tmp_path)["grade"] == "ok"


@pytest.mark.parametrize("number", ["5", "1", "10", "8"])
def test_parts_of_a_scientific_number_are_not_plain_numbers(number):
    assert A.number_held(number, A.Source("p = 1e-5 and q = 2.3 × 10^-8")) is False


@pytest.mark.parametrize("entries", [{"quotes": [123]}, {"numbers": [None]}, {"numbers": [True]}])
def test_invalid_list_entries_are_bad_input(tmp_path, entries):
    cites = tmp_path / "c.json"
    cites.write_text(json.dumps({"citations": [{"id": "x", "doi": "10.5555/x", **entries}]}), encoding="utf-8")
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "citation_audit.py"), str(cites),
                          "--records", str(AUDIT / "records.json")], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 2 and "every entry of" in run.stderr


@pytest.mark.parametrize("entries", [{"numbers": [2.970]}, {"numbers": [1]}, {"quotes": [123]}, {"quotes": [""]}])
def test_malformed_entries_are_major_not_dropped(tmp_path, entries):
    row = A.audit_one({"id": "m", "doi": "10.5555/x", "source": "s.txt", **entries},
                      {"doi:10.5555/x": {"found": True, "title": "T"}}, tmp_path)
    assert row["grade"] == "major" and row["findings"][0]["check"] in ("quotes", "numbers")


def test_json_numbers_are_bad_input_on_the_command_line(tmp_path):
    cites = tmp_path / "c.json"
    cites.write_text('{"citations": [{"id": "x", "doi": "10.5555/x", "numbers": [2.970]}]}', encoding="utf-8")
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "citation_audit.py"), str(cites),
                          "--records", str(AUDIT / "records.json")], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 2 and "numbers must be str" in run.stderr


@pytest.mark.parametrize("number, source, held", [
    ("1e−5", "r = 0.2", False), ("1e−5", "p 1e-5", True), ("−2.97", "fold -2.97", True),
    ("0e0", "z 0e0", True), ("0e0", "r = 0.2", False),
])
def test_unicode_signs_and_exact_zero(number, source, held):
    assert A.number_held(number, A.Source(source)) is held


@pytest.mark.parametrize("source", ["p 1e–5", "p 1 × 10–5", "p 1e−5"])
def test_dash_variants_in_source_exponents(source):
    assert A.number_held("1e-5", A.Source(source)) is True
    assert A.number_held("5", A.Source(source)) is False


def test_a_range_written_with_an_en_dash_still_holds_both_ends():
    source = A.Source("mapping rate 93.9–95.7 percent")
    assert A.number_held("93.9", source) and A.number_held("95.7", source)
