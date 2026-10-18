from copy import deepcopy
from pathlib import Path

from labhq.portfolio import broken_markdown_links, build_portfolio_files, internal_data_steps
from labhq.settings import DataZone, Settings

from test_portfolio_edition import PAPER, portfolio_fixture


def branched_fixture(tmp_path: Path):
    settings = Settings()
    internal = tmp_path / "mdis"
    internal.mkdir()
    settings.policy.data_zones = [DataZone(path=str(internal), level="internal")]
    plan = {
        "branches": [
            {"id": "A", "question": "문헌 근거가 있는가?", "evidence_type": "literature",
             "data_boundary": "public"},
            {"id": "B", "question": "공개 오믹스가 지지하는가?", "evidence_type": "omics",
             "data_boundary": "public"},
            {"id": "C", "question": "국내 자료가 지지하는가?", "evidence_type": "mdis",
             "data_boundary": "internal"},
            {"id": "D", "question": "해외 자료가 지지하는가?", "evidence_type": "ipums",
             "data_boundary": "licensed"},
        ],
        "steps": [
            {"id": "lit", "branch": "A", "instruction": "문헌을 검토한다.",
             "output_types": [{"name": "outputs/report/stage_report.md", "data_type": "report"}]},
            {"id": "omics", "branch": "B", "instruction": "오믹스를 분석한다.",
             "output_types": [{"name": "outputs/tables/summary.tsv", "data_type": "table"}]},
            {"id": "mdis", "branch": "C", "instruction": "내부 자료를 집계한다.",
             "output_types": [{"name": "outputs/tables/summary.tsv", "data_type": "table"}]},
            {"id": "ipums", "branch": "D", "instruction": "해외 자료를 분석한다."},
            {"id": "synth", "branch": "synthesis", "instruction": "갈래를 종합한다."},
        ],
    }
    report = PAPER.replace(
        "## 결과\n\n",
        "## 결과\n\n![오믹스 그림](work-omics/outputs/figures/result.svg)\n\n"
        "![국내 그림](work-mdis/outputs/figures/result.svg)\n\n",
    )
    request = {
        "id": "req_branches",
        "text": f"공개 자료와 {internal / 'births.tsv'}를 네 갈래로 비교한다.",
        "status": "done",
        "report": report,
        "project_dirs": [str(internal)],
        "plan": plan,
        "results": {
            "lit": {"ok": True, "text": "직접 근거를 찾지 못했다."},
            "omics": {"ok": True, "text": "유전자 16,242개 중 FDR 0.05 통과는 0개였다.",
                       "workdir": "work-omics", "outputs": ["outputs/figures/result.svg"]},
            "mdis": {"ok": True, "text": "주 검정은 통계적으로 유의하지 않았다.",
                      "workdir": "work-mdis", "outputs": ["outputs/figures/result.svg"]},
            "ipums": {"ok": False, "error": "자료 접근이 거절됨", "text": ""},
            "synth": {"ok": True, "text": "완료한 세 갈래를 종합했다."},
        },
    }
    assets = {
        "steps/synth/outputs/report/final_report.md": report,
        "steps/lit/outputs/report/stage_report.md": "# 문헌 단계 보고서\n\n직접 근거를 찾지 못했다.\n",
        "steps/omics/outputs/scripts/analyze.py": "print('omics')\n",
        "steps/mdis/outputs/scripts/analyze.py": "print('mdis')\n",
        "steps/omics/outputs/tables/summary.tsv": "tested\tpassed\n16242\t0\n",
        "steps/mdis/outputs/tables/summary.tsv": "group\tcount\n전체\t100\n",
        "steps/mdis/outputs/tables/people.tsv": "person_id\tvalue\np1\t42\n",
    }
    images = {
        "work-omics/outputs/figures/result.svg":
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>omics</text></svg>',
        "work-mdis/outputs/figures/result.svg":
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>mdis</text></svg>',
    }
    return settings, request, assets, images


def test_four_branches_get_study_folders_and_failed_row(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)

    files = build_portfolio_files(request, settings, assets, images)

    folders = ("A-literature", "B-omics", "C-mdis", "D-ipums")
    for folder in folders:
        assert f"studies/{folder}/README.md" in files
    assert "studies/A-literature/report/stage_report.md" in files
    assert "studies/B-omics/code/omics/outputs/scripts/analyze.py" in files
    assert "studies/C-mdis/code/mdis/outputs/scripts/analyze.py" in files
    index = files["studies/README.md"]
    assert "| 갈래 | 질문 | 자료 | 상태 | 주요 결과 | 폴더 |" in index
    assert "| D | 해외 자료가 지지하는가? | licensed · ipums | 실패 | 자료 접근이 거절됨 |" in index
    assert "D-ipums/README.md" in index
    assert "[열기](studies/A-literature/README.md)" in files["README.md"]
    assert "studies/               갈래별 코드·그림·표·단계 보고서" in files["README.md"]


def test_root_branch_table_shows_status(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)

    readme = build_portfolio_files(request, settings, assets, images)["README.md"]

    assert "| 갈래 | 자료 | 상태 | 결과 | 폴더 |" in readme
    assert "| D | licensed · ipums | 실패 | 자료 접근이 거절됨 |" in readme


def test_branch_with_one_failed_terminal_is_partial_and_keeps_success(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    request["plan"]["steps"].insert(1, {
        "id": "lit_failed", "branch": "A", "instruction": "원문을 추가 확인한다.",
    })
    request["results"]["lit_failed"] = {
        "ok": False, "error": "원문 확보 실패", "text": "",
    }

    files = build_portfolio_files(request, settings, assets, images)

    index = files["studies/README.md"]
    assert "| A | 문헌 근거가 있는가? | public · literature | 부분 |" in index
    assert "직접 근거를 찾지 못했다." in index
    assert "lit_failed: 원문 확보 실패" in index
    assert "studies/A-literature/report/stage_report.md" in files


def test_same_named_branch_figures_and_report_links_stay_separate(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)

    files = build_portfolio_files(request, settings, assets, images)

    omics = "studies/B-omics/figures/result.svg"
    mdis = "studies/C-mdis/figures/result.svg"
    assert files[omics] != files[mdis]
    report = files["report/report.md"]
    assert f"![오믹스 그림](../{omics})" in report
    assert f"![국내 그림](../{mdis})" in report
    assert broken_markdown_links(files) == []


def test_accepted_request_manuscript_wins_over_stage_synthesis(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    request["review"] = {"verdict": "accept"}
    request["report"] = PAPER.replace("# 공개 오믹스 대체 분석", "# 검토 완료 요청 원고")
    request["plan"]["steps"][-1] = {
        "id": "S1", "branch": "synthesis", "instruction": "최종 종합을 쓴다.",
        "depends_on": ["lit", "omics", "mdis", "ipums"],
    }
    request["results"].pop("synth")
    request["results"]["S1"] = {"ok": True, "text": "최종 종합"}
    assets = {"steps/S1/outputs/report/final_report.md": PAPER.replace(
        "# 공개 오믹스 대체 분석", "# 단계 종합 원고")}

    files = build_portfolio_files(request, settings, assets, images)

    assert files["report/report.md"].startswith("# 검토 완료 요청 원고\n")


def test_revise_uses_latest_final_synthesis_manuscript(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    request["review"] = {"verdict": "revise"}
    request["plan"]["steps"][-1] = {
        "id": "S1", "branch": "synthesis", "instruction": "중간 종합을 쓴다.",
        "depends_on": ["lit", "omics", "mdis", "ipums"],
    }
    request["plan"]["steps"].append({
        "id": "S2", "branch": "synthesis", "instruction": "최종 종합을 쓴다.",
        "depends_on": ["S1"],
    })
    request["results"].pop("synth")
    request["results"].update({
        "S1": {"ok": True, "text": "중간 종합"},
        "S2": {"ok": True, "text": "최종 종합"},
    })
    intermediate = PAPER.replace("# 공개 오믹스 대체 분석", "# 중간 종합 원고")
    final = PAPER.replace("# 공개 오믹스 대체 분석", "# 최종 종합 원고")
    assets = {
        "steps/S1/outputs/report/final_report.md": intermediate,
        "steps/S2/outputs/report/final_report.md": final,
    }

    files = build_portfolio_files(request, settings, assets, images)

    assert files["report/report.md"].startswith("# 최종 종합 원고\n")


def test_declared_branch_report_is_kept_without_report_in_filename(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    request["plan"]["steps"][0]["output_types"] = [
        {"name": "outputs/answer.md", "data_type": "report"},
    ]
    assets["steps/lit/outputs/answer.md"] = "# 문헌 답변\n\n직접 근거를 찾지 못했다.\n"

    files = build_portfolio_files(request, settings, assets, images)

    assert files["studies/A-literature/report/answer.md"] == assets["steps/lit/outputs/answer.md"]


def test_internal_branch_uses_existing_publication_allowlist(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)

    files = build_portfolio_files(request, settings, assets, images)

    assert files["studies/C-mdis/tables/summary.tsv"] == "group\tcount\n전체\t100\n"
    assert not any(name.endswith("people.tsv") for name in files)
    assert "내부 자료 영역이라 표 1개는 요청 묶음에만 둠" in files["README.md"]


def test_internal_rules_follow_the_source_step_and_descendants(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    internal_file = Path(request["project_dirs"][0]) / "births.tsv"
    request["results"]["mdis"]["text"] += f" 입력은 {internal_file}이다."
    request["plan"]["steps"][-1].update(
        depends_on=["omics", "mdis"],
        output_types=[{"name": "outputs/tables/combined.tsv", "data_type": "table"}],
    )
    request["results"]["synth"]["outputs"] = ["outputs/tables/combined.tsv"]
    assets.update({
        "steps/omics/outputs/tables/summary.tsv": "gene\trank\nG1\t5\n",
        "steps/mdis/outputs/tables/summary.tsv": "group\tcount\nA\t5\n",
        "steps/synth/outputs/tables/combined.tsv": "group\tcount\nA\t5\n",
    })

    files = build_portfolio_files(request, settings, assets, images)

    assert files["studies/B-omics/tables/summary.tsv"] == "gene\trank\nG1\t5\n"
    assert not any(name.endswith("C-mdis/tables/summary.tsv") for name in files)
    assert not any(name.endswith("combined.tsv") for name in files)


def test_runner_workspace_inside_internal_zone_is_not_step_input_provenance(tmp_path):
    settings, request, _assets, _images = branched_fixture(tmp_path)
    request = deepcopy(request)
    internal = Path(request["project_dirs"][0])
    settings.runner.workspace_root = str(internal)
    request["results"]["mdis"]["text"] += f" 입력은 {internal / 'births.tsv'}이다."
    for step_id, result in request["results"].items():
        workdir = internal / "runs" / step_id
        result.update(
            workdir=str(workdir),
            source_workdir=str(workdir),
            cwd=str(workdir),
            workspace_root=str(internal),
        )

    assert internal_data_steps(request, settings) == {"mdis"}


def test_missing_step_path_provenance_keeps_request_wide_internal_fallback(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    request["plan"]["steps"][1].pop("output_types")
    assets["steps/omics/outputs/tables/summary.tsv"] = "gene\trank\nG1\t5\n"

    files = build_portfolio_files(request, settings, assets, images)

    assert not any(name.endswith("B-omics/tables/summary.tsv") for name in files)


def test_two_internal_files_need_concrete_step_attribution(tmp_path):
    settings, request, assets, images = branched_fixture(tmp_path)
    request = deepcopy(request)
    internal = Path(request["project_dirs"][0])
    births = internal / "births.tsv"
    deaths = internal / "deaths.tsv"
    request["text"] = f"{births}와 {deaths}를 서로 다른 단계에서 읽는다."
    request["plan"]["steps"][1]["input_refs"] = [str(internal)]
    request["results"]["mdis"]["text"] += f" 입력은 {births}이다."
    assets["steps/omics/outputs/tables/summary.tsv"] = "gene\trank\nG1\t5\n"

    files = build_portfolio_files(request, settings, assets, images)

    assert not any(name.endswith("B-omics/tables/summary.tsv") for name in files)


def test_posix_internal_paths_keep_case_for_step_attribution():
    settings = Settings()
    settings.policy.data_zones = [DataZone(path="/srv/internal", level="internal")]
    request = {
        "text": "Compare /srv/internal/A.tsv with /srv/internal/a.tsv.",
        "plan": {"steps": [
            {"id": "upper", "instruction": "Read the uppercase input."},
            {"id": "lower", "instruction": "Read the lowercase input."},
        ]},
        "results": {
            "upper": {"text": "Read /srv/internal/A.tsv."},
            "lower": {"text": "Finished the public comparison."},
        },
    }

    assert internal_data_steps(request, settings) is None


def test_zone_basename_in_plain_prose_is_not_internal_provenance():
    settings = Settings()
    settings.policy.data_zones = [DataZone(path="/home/researcher/.labhq/runs", level="internal")]
    request = {
        "text": "Compare public data from three sequencing runs.",
        "plan": {"steps": [{"id": "public", "instruction": "Analyze public reads."}]},
        "results": {"public": {"text": "Three sequencing runs passed QC."}},
    }

    assert internal_data_steps(request, settings) == set()


def test_unbranched_request_keeps_the_existing_file_list(tmp_path):
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    request = deepcopy(request)

    files = build_portfolio_files(
        request, settings, assets, images, decisions=decisions, tasks=tasks, bundle=bundle,
        run_documents={"run/progress.md": "# 진행 기록\n\n완료\n",
                       "run/reproduce.md": "# 재현 방법\n\npython analysis.py\n"},
    )

    assert set(files) == {
        "README.md", "report/report.md", "run/requests.md", "run/decisions.md",
        "run/execution_record.md", "run/costs.md", "run/progress.md", "run/reproduce.md",
        "code/analysis/outputs/scripts/analyze.py", "figures/graphical_abstract.svg", "figures/volcano.svg",
        "tables/result_summary.tsv", "REFERENCES.md", "DATA_AND_LICENSES.md",
    }
