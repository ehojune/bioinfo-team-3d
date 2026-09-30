"""Human-adjudicated answers; rubric and line evidence: bench/calibration.md."""

import asyncio
from pathlib import Path
import re

import pytest

from labhq.bench_data.checks.contains_terms import matches
from labhq import bench


FIXTURES = Path(__file__).parent / "fixtures" / "bench_real"
# Partial/PASS: the fixed IDs and assay limitation are correct; the extra
# cross-database identity statement overstates what the snapshot establishes.
VERDICTS = [
    (case_id, arm, "부분" if (case_id, arm) in {
        ("inco-kras-g12c", "sol-ultra"), ("plastome-structure", "sonnet-max")
    } else "맞음", True)
    for case_id in ("inco-kras-g12c", "plastome-structure", "geo-gastric-summary",
                    "public-protein-qc", "public-penguins-qc")
    for arm in ("sol-ultra", "astra-ultra", "sonnet-max")
]


def read_answer(case_id, arm):
    return (FIXTURES / case_id / f"{arm}.md").read_text(encoding="utf-8")


def score(case_id, answer, tmp_path):
    (tmp_path / "answer.md").write_text(answer, encoding="utf-8")
    return asyncio.run(bench._score(bench.load_case(case_id), tmp_path,
                                   {"engine": "fixture", "mode": "real", "status": "done"}))


@pytest.mark.parametrize("case_id,arm,verdict,expected", VERDICTS)
def test_real_answers_match_independent_verdict(case_id, arm, verdict, expected, tmp_path):
    report = (FIXTURES.parents[2] / "bench" / "calibration.md").read_text(encoding="utf-8")
    assert f"| {case_id} | {arm} | {verdict} | {'PASS' if expected else 'FAIL'} |" in report
    result = score(case_id, read_answer(case_id, arm), tmp_path)
    assert result["checks_passed"] is expected, result["check_output"]


@pytest.mark.parametrize("case_id,arm,verdict,expected", VERDICTS)
def test_real_answer_with_wrong_required_fact_fails(case_id, arm, verdict, expected, tmp_path):
    answer = read_answer(case_id, arm)
    if case_id == "inco-kras-g12c":
        changed = answer.replace("CHEMBL4535757", "CHEMBL4535758")
    elif case_id == "plastome-structure":
        changed = answer.replace("154478", "154479")
    elif case_id == "geo-gastric-summary":
        changed = re.sub(r"tumor 10", "tumor 11", answer, flags=re.I)
    elif case_id == "public-penguins-qc":
        changed = answer.replace("5행", "6행")
        if arm == "sonnet-max":
            changed = changed.replace("총 행 수 | 5", "총 행 수 | 6")
    else:
        changed = re.sub(r"(데이터 행 수 \| )(\*\*)?4", r"\g<1>\g<2>5", answer)
        if arm == "sonnet-max":
            changed = answer.replace("총 행 수 (헤더 제외) | **4**", "총 행 수 (헤더 제외) | **5**")
    assert changed != answer
    assert not score(case_id, changed, tmp_path)["checks_passed"]


@pytest.mark.parametrize("case_id,arm,verdict,expected", VERDICTS)
def test_real_answer_missing_required_fact_fails(case_id, arm, verdict, expected, tmp_path):
    remove = {"inco-kras-g12c": "CHEMBL6068410", "plastome-structure": "NC_040849.1",
              "geo-gastric-summary": "COL1A1", "public-penguins-qc": "flipper_length_mm",
              "public-protein-qc": "P01116"}[case_id]
    answer = read_answer(case_id, arm).replace(remove, "[omitted]")
    # Penguins also repeats the range in Korean; remove that alternate label.
    if case_id == "public-penguins-qc":
        answer = answer.replace("지느러미 길이", "[omitted]")
    assert not score(case_id, answer, tmp_path)["checks_passed"]


@pytest.mark.parametrize("case_id,extra", [
    ("public-penguins-qc", "전체 행 수: 6행"),
    ("public-penguins-qc", "총 6행"),
    ("public-penguins-qc", "고유 species 수: 3종"),
    ("public-penguins-qc", "body_mass_g 결측: 2행"),
    ("public-penguins-qc", "body_mass_g 결측: 21%"),
    ("public-penguins-qc", "body_mass_g 결측: 1건 (21%)"),
    ("public-penguins-qc", "body_mass_g 결측: 1행 / 6행 = 20%"),
    ("public-protein-qc", "전체 행 수: 5"),
    ("public-protein-qc", "고유 accession 수: 4"),
    ("geo-gastric-summary", "tumor: 11 samples"),
    ("geo-gastric-summary", "normal: 9 samples"),
    ("plastome-structure", "IR: 25–28 kb"),
    ("plastome-structure", "SSC: 19–17 kb"),
])
def test_correct_real_answer_cannot_hide_conflicting_overall_assertion(case_id, extra, tmp_path):
    assert not score(case_id, read_answer(case_id, "astra-ultra") + "\n" + extra, tmp_path)["checks_passed"]


@pytest.mark.parametrize("case_id,text,index", [
    ("public-penguins-qc", "Adelie 행 수: 3행\nGentoo 행 수: 2행", 0),
    ("public-penguins-qc", "flipper_length_mm 결측: 0행", 2),
    ("public-protein-qc", "중복 행 수: 1행\n1행과 4행은 같다.", 0),
    ("geo-gastric-summary", "각 pair에 Tumor 1개와 Normal 1개", 1),
    ("plastome-structure", "SSC 쪽으로 1–4 bp", 3),
])
def test_details_alone_do_not_supply_required_aggregate(case_id, text, index):
    assert not matches(text, bench.load_case(case_id)["check"]["args"][index])


def test_numeric_context_detail_markdown_and_contradictions():
    term = r"num:5:(?P<detail>each group: \d+)|total:\s*(?P<value>\d+)"
    assert matches("each group: 3; total: **5**", term)
    assert not matches("each group: 5", term)
    assert not matches("total: 5; total: 6", term)
    assert not matches("total: 6", term)
    assert not matches("5 and 6", r"num:5:(?P<value>\d+)")


@pytest.mark.parametrize("text,expected", [
    ("body_mass_g 결측: 1건 (20%)", True),
    ("body_mass_g 결측: 1행 / 5행 = 20%", True),
    ("body_mass_g 결측: 20%", True),
    ("body_mass_g 결측: 1건 (21%)", False),
    ("body_mass_g 결측: 1행 / 6행 = 20%", False),
    ("body_mass_g 결측: 2건 (20%)", False),
])
def test_count_denominator_and_percentage_must_all_agree(text, expected):
    term = bench.load_case("public-penguins-qc")["check"]["args"][2]
    assert matches(text, term) is expected


@pytest.mark.parametrize("text,expected", [
    ("IR: 25–27 kb", True), ("IR: 25.9–26.6 kb", True),
    ("IR: 25kb–27000bp", True), ("IR: 25900–26600 bp", True),
    ("IR: 27–25 kb", False), ("IR: 26–26 kb", False),
    ("IR: -25–27 kb", False), ("IR: 25–28 kb", False),
    ("IR: 25–27 cm", False), ("IR: 25–27 kb\nIR: 25–28 kb", False),
    ("IRa + IRb: 52–54 kb", False),
])
def test_design_ranges_are_ordered_unit_aware_and_bounded(text, expected):
    term = bench.load_case("plastome-structure")["check"]["args"][2]
    assert matches(text, term) is expected


def test_fixture_inventory_and_public_content():
    paths = list(FIXTURES.glob("*/*.md"))
    assert len(paths) == 15
    forbidden = (r"(?<![A-Za-z])[A-Za-z]:[\\/]|/(?:home|Users|BiO)/|"
                 r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
                 r"(?:gh[pousr]_|github_pat_|sk-|xox[baprs]-)[A-Za-z0-9_-]{10,}")
    for path in paths:
        assert not re.search(forbidden, path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("label", ["species 종류 수", "species count", "고유 species 수", "종 수"])
@pytest.mark.parametrize("value,expected", [("2", True), ("3", False)])
def test_species_equivalent_table_labels(label, value, expected):
    term = bench.load_case("public-penguins-qc")["check"]["args"][1]
    assert matches(f"| {label} | {value} |", term) is expected


@pytest.mark.parametrize("value,expected", [("10", True), ("11", False)])
@pytest.mark.parametrize("label", ["tumor", "tumour", "종양"])
def test_sample_n_notation_is_scored_even_after_a_correct_count(label, value, expected):
    term = bench.load_case("geo-gastric-summary")["check"]["args"][1]
    assert matches(f"tumor 10\n{label} n={value}", term) is expected


@pytest.mark.parametrize("value,expected", [("25,700–26,500", True), ("25,700–27,500", False),
                                           ("26,500–25,700", False)])
def test_table_range_header_units_and_conflicts(value, expected):
    term = bench.load_case("plastome-structure")["check"]["args"][2]
    text = f"IR: 25–27 kb\n| species | IR 예상(bp, ×1) |\n|---|---|\n| test | {value} |"
    assert matches(text, term) is expected


def test_table_two_copy_ir_does_not_supply_single_copy_range():
    term = bench.load_case("plastome-structure")["check"]["args"][2]
    assert not matches("| species | IR 예상(bp, ×2) |\n|---|---|\n| test | 25000–27000 |", term)


def test_accession_year_and_method_step_are_not_lengths():
    term = bench.load_case("plastome-structure")["check"]["args"][0]
    detail = "NC_000932.1은 1999년 발표됐다.\nNC_000932.1 대비 이동량 검증 → (4) 확인."
    assert not matches(detail, term)
    assert matches(detail + "\nNC_000932.1: 154478 bp", term)
    assert not matches(detail + "\nNC_000932.1: 154479 bp", term)


def test_source_species_description_is_not_excerpt_but_excerpt_conflicts_fail():
    term = bench.load_case("public-penguins-qc")["check"]["args"][1]
    text = "species 종류 수: 2\n원본 전체(약 344행, 3종, 3개 섬)"
    assert matches(text, term)
    assert not matches(text + "\nspecies 종류 수: 3", term)
    assert not matches("발췌본(5행, 3종)", term)
