import asyncio
import copy
import json

import pytest

from labhq.bench_data.checks.contains_terms import matches
from labhq import bench
from labhq.cli import main


VARIANTS = {
    "inco-kras-g12c": [
        "6OIM CHEMBL4535757 CHEMBL4594350 CHEMBL6068410. IC50를 직접 비교 하지 않는다.",
        "6oim chembl4535757 chembl4594350 chembl6068410. Different assays are not directly comparable.",
    ],
    "plastome-structure": [
        "NC_000932.1: 154,478 bp; NC_040849.1: 153,483bp. IR: 25 - 27 kb; SSC: 17~18kb.",
        "NC_000932.1 = 154.478 kb; NC_040849.1 = 153.483kb. IR 25kb to 27kb; SSC 17–18 kb.",
    ],
    "geo-gastric-summary": [
        "GSE79973 종양 10건, 정상 10 개. GKN1 COL1A1 HALLMARK_EMT STRING 9606 . ENSP.",
        "gse79973 tumour: 10 samples; normal: 10 samples. gkn1 col1a1 hallmark_emt 9606.ENSP001.",
    ],
    "public-penguins-qc": [
        "발췌본 5 행, 2종. body_mass_g 결측치 1건. flipper_length_mm: 181–230 mm.",
        "Excerpt: 5 rows, 2개 species. Missing values: 20 %. Flipper length: 181 mm to 230mm.",
    ],
    "public-protein-qc": [
        "4 행, 고유 accession 3개. P01116 중복. 모든 길이는 양수. BRCA1_HUMAN: 1,863 aa.",
        "Rows: 4; unique accessions: 3. Duplicate accession: P01116. All lengths are positive. Longest length: 1863aa.",
    ],
}
WRONG_NUMBER = {
    "inco-kras-g12c": ("4535757", "4535758"),
    "plastome-structure": ("154,478", "154,479"),
    "geo-gastric-summary": ("종양 10", "종양 11"),
    "public-penguins-qc": ("2종", "3종"),
    "public-protein-qc": ("4 행", "5 행"),
}
MISSING_ITEM = {
    "inco-kras-g12c": "CHEMBL6068410",
    "plastome-structure": "SSC: 17~18kb",
    "geo-gastric-summary": "COL1A1",
    "public-penguins-qc": "body_mass_g 결측치 1건",
    "public-protein-qc": "P01116 중복",
}


def saved_run():
    return {"engine": "sol-ultra", "mode": "real", "status": "done",
            "model": "original-model", "effort": "ultra", "usage": {"total_tokens": 42},
            "cost_usd": None, "cost_known": False, "duration_s": 1.25}


@pytest.mark.parametrize("case", bench.load_cases(), ids=lambda case: case["id"])
@pytest.mark.parametrize("variant", [0, 1, "mock", "wrong-number", "missing-item"])
def test_case_checks_accept_variants_and_reject_wrong_answers(case, variant, tmp_path):
    case_id = case["id"]
    if variant == "mock":
        answer = case["mock_answer"]
    elif variant == "wrong-number":
        answer = VARIANTS[case_id][0].replace(*WRONG_NUMBER[case_id])
    elif variant == "missing-item":
        answer = VARIANTS[case_id][0].replace(MISSING_ITEM[case_id], "")
    else:
        answer = VARIANTS[case_id][variant]
    (tmp_path / "answer.md").write_text(answer, encoding="utf-8")
    row = asyncio.run(bench._score(case, tmp_path, saved_run()))
    assert row["checks_passed"] == (variant not in {"wrong-number", "missing-item"}), row["check_output"]


@pytest.mark.parametrize("case_id,answer", [
    ("public-penguins-qc", "발췌본 15행, 12 species. 결측 11건. flipper_length_mm: 1181–1230 mm."),
    ("public-penguins-qc", "발췌본 5행, 2종. 결측치 21%. flipper_length_mm: 181–230 mm."),
    ("public-penguins-qc", "발췌본 5행, 2종. 결측치 -20%. flipper_length_mm: 181–230 mm."),
    ("public-penguins-qc", "발췌본 5행, 2종. 결측치 -1건. flipper_length_mm: 181–230 mm."),
    ("public-penguins-qc", "발췌본 5행, 2종. 결측치 1건. flipper_length_mm: 181–230 cm."),
    ("public-penguins-qc", "발췌본 5행, 2종. 결측치 1건. flipper_length_mm: 181–231 mm. 참고: 230."),
    ("public-penguins-qc", "발췌본 5행, 2종. 결측치 1건. flipper_length_mm: 181–230 mm. 결측치 2건."),
    ("public-protein-qc", "4행, unique 3 accession. P01116 중복. 모든 길이는 양수. BRCA1_HUMAN: 18630aa."),
    ("inco-kras-g12c", "6OIM CHEMBL4535757 CHEMBL4594350 CHEMBL6068410. IC50를 직접 비교한다."),
    ("plastome-structure", "NC_000932.1: 154478bp; NC_040849.1: 153483bp. IR 25–28kb; SSC 17–18kb."),
    ("plastome-structure", "NC_000932X1: 154478bp; NC_040849.1: 153483bp. IR 25–27kb; SSC 17–18kb."),
])
def test_checks_do_not_pass_substrings_contradictions_or_unrelated_numbers(case_id, answer, tmp_path):
    (tmp_path / "answer.md").write_text(answer, encoding="utf-8")
    row = asyncio.run(bench._score(bench.load_case(case_id), tmp_path, saved_run()))
    assert not row["checks_passed"]


def test_checker_literals_regex_and_numeric_comparison():
    assert matches("Alpha", "alpha")
    assert matches("2개 species", r"re:2\s*(종|개?\s*species)")
    assert matches("154.478 kb", r"num:154478:(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>kb)")
    assert matches("20%", r"num:1/5:(?P<percent>\d+)%")
    assert not matches("21%", r"num:1/5:(?P<percent>\d+)%")
    assert not matches("10 and 11", r"num:10:(?P<value>\d+)")


def store_run(root, run_id, answer=None, mode="real", status="done", arm="sol-ultra"):
    case = bench.load_case("public-penguins-qc")
    directory = root / case["id"] / run_id / arm
    directory.mkdir(parents=True)
    run = {**saved_run(), "engine": arm, "mode": mode, "status": status}
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    if answer is not None:
        (directory / "answer.md").write_text(answer, encoding="utf-8")
    score = asyncio.run(bench._score(case, directory, run))
    # Simulate the literal-only historical checker rejecting a correct answer.
    score.update(checks_passed=False, check_output="missing: 2 species, 결측 1", run_id=run_id)
    (directory / "score.json").write_text(json.dumps(score), encoding="utf-8")
    return directory, score


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_rescore_saved_artifacts_preserves_history_execution_and_updates_comparisons(tmp_path, monkeypatch):
    case_id = "public-penguins-qc"
    directory, original = store_run(tmp_path, "run-001", VARIANTS[case_id][0])
    custom, _ = store_run(tmp_path, "run-001", VARIANTS[case_id][1], arm="retired-arm")
    unchanged = {path: path.read_bytes() for path in
                 (directory / "answer.md", directory / "run.json", custom / "run.json")}

    def forbidden(*args, **kwargs):
        raise AssertionError("rescore must not load config or execute arms")

    monkeypatch.setattr(bench, "_run_labhq", forbidden)
    monkeypatch.setattr(bench, "_run_baseline", forbidden)
    monkeypatch.setattr("labhq.settings.Settings.load", forbidden)
    monkeypatch.setenv("LABHQ_BENCH_DIR", str(tmp_path))
    main(["bench", "rescore", case_id, "--run-id", "run-001"])
    score = read_json(directory / "score.json")
    assert score["checks_passed"]
    assert score["score_history"][0]["score"] == original
    assert all(score[key] == original[key] for key in original
               if key not in {"artifact_exists", "checks_passed", "check_output", "check"})
    assert all(path.read_bytes() == body for path, body in unchanged.items())
    assert read_json(directory.parent / "comparison.json")["rows"][1] == score
    assert all(row["checks_passed"] for row in read_json(directory.parent.parent / "comparison.json")["rows"])
    assert "PASS" in (directory.parent.parent / "comparison.md").read_text(encoding="utf-8")
    main(["bench", "rescore", case_id, "--output", str(tmp_path)])
    again = read_json(directory / "score.json")
    assert len(again["score_history"]) == 2
    assert again["score_history"][0]["score"] == original
    assert again["score_history"][1]["score"] == {k: v for k, v in score.items() if k != "score_history"}


def test_rescore_latest_all_missing_answers_failed_runs_and_modes(tmp_path):
    case_id = "public-penguins-qc"
    first, _ = store_run(tmp_path, "run-001", VARIANTS[case_id][0], mode="mock")
    second, _ = store_run(tmp_path, "run-002", status="failed")
    bench.report_case(case_id, tmp_path, "mock")
    before = (first / "score.json").read_bytes()
    main(["bench", "rescore", case_id, "--output", str(tmp_path)])
    assert (first / "score.json").read_bytes() == before
    missing = read_json(second / "score.json")
    assert not missing["artifact_exists"] and not missing["checks_passed"]
    assert missing["status"] == "failed"
    main(["bench", "rescore", case_id, "--all", "--output", str(tmp_path)])
    assert read_json(first / "score.json")["checks_passed"]
    assert len(read_json(second / "score.json")["score_history"]) == 2
    assert read_json(first.parent.parent / "comparison.json")["mode"] == "real"
    assert read_json(first.parent / "comparison.json")["mode"] == "mock"


@pytest.mark.parametrize("flags", [["--run-id", "../escape"], ["--run-id", "missing"],
                                   ["--all"], ["--run-id", "id", "--all"]])
def test_rescore_invalid_or_missing_selection_fails_without_writes(tmp_path, flags):
    with pytest.raises(SystemExit) as exc:
        main(["bench", "rescore", "public-penguins-qc", "--output", str(tmp_path), *flags])
    assert exc.value.code == 2
    assert not any(tmp_path.iterdir())


def test_rescore_rechecks_current_case_and_cannot_turn_failed_execution_into_success(tmp_path, monkeypatch):
    case_id = "public-penguins-qc"
    directory, _ = store_run(tmp_path, "run-001", VARIANTS[case_id][0], status="failed")
    case = copy.deepcopy(bench.load_case(case_id))
    case["check"]["args"].append("unavailable required item")
    monkeypatch.setattr(bench, "load_case", lambda _: case)
    asyncio.run(bench.rescore_case(case_id, tmp_path))
    score = read_json(directory / "score.json")
    assert score["status"] == "failed" and not score["checks_passed"]
    assert "unavailable required item" in score["check_output"]
