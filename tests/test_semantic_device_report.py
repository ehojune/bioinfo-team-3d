import json
import platform
from pathlib import Path

from labhq.portfolio import build_portfolio_files
from labhq.request_bundle import build_request_bundle
from labhq.research.semantic_device_report import build_report, render_report
from labhq.settings import Settings
from tests.test_portfolio_edition import portfolio_fixture


def fixed_request(tmp_path: Path) -> tuple[Settings, dict]:
    settings = Settings()
    settings.semantics = {"mode": "ab"}
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    workdir = tmp_path / "runs" / "2026-10-18" / "task_fixed"
    output = workdir / "outputs" / "result.tsv"
    output.parent.mkdir(parents=True)
    output.write_text("gene\tvalue\nTP53\t1\n", encoding="utf-8")
    (workdir / "manifest.json").write_text(json.dumps({"host": platform.node()}), encoding="utf-8")
    request = {
        "id": "req_semantic_report", "mode": "orchestrate", "status": "done", "text": "고정 연구 요청",
        "created_at": 1792296000, "finished_at": 1792299600,
        "report": "# 결과\n\n고정 결과", "report_appendix": "", "execution_record": "# 실행 기록\n\n완료",
        "plan": {
            "topics": ["dna_methylation"],
            "checklist": {
                "conversion_rate": "step:qc",
                "coverage_snp": "assumption: 공개 집계에 coverage가 없음",
                "differential_model": "not_applicable: 차등 분석 요청이 아님",
            },
            "pack_values": {"single_cell_de@3": {"not_applicable": "bulk 자료"}},
            "pack_applicability": {
                "bulk_tumor_normal@3": {"applied": True, "matched_topics": ["dna_methylation"],
                                         "reason": "topic_match"},
                "single_cell_de@3": {"applied": False, "matched_topics": [], "reason": "no_topic_match"},
            },
            "steps": [
                {"id": "qc", "depends_on": [], "outputs": ["outputs/result.tsv"],
                 "output_types": [{"name": "outputs/result.tsv", "data_type": "table", "format": "tsv"}]},
            ],
        },
        "research_contract": {
            "plan_sha256": "a" * 64,
            "pack_applicability": {
                "bulk_tumor_normal@3": {"applied": True, "matched_topics": ["dna_methylation"],
                                         "reason": "topic_match"},
                "single_cell_de@3": {"applied": False, "matched_topics": [], "reason": "no_topic_match"},
            },
            "review": {"plan_sha256": "a" * 64, "verdict": "accept", "checklist": [
                {"id": "conversion_rate", "status": "done", "evidence": "qc/result.tsv"},
                {"id": "coverage_snp", "status": "partly",
                 "evidence": "C:/Users/alice/private.tsv api_key=should-not-leak"},
                {"id": "differential_model", "status": "done", "evidence": "비적용 확인"},
            ]},
        },
        "semantics_ab": {"arm": "advisory", "offered": []},
        "semantics_observation": {
            "type": "request", "request_id": "req_semantic_report", "ts": 1792299500, "epoch": 4,
            "provenance": {"status": "ok", "candidates": 0, "excluded": {"input_mismatch": 2}},
            "objects": {"status": "ok", "unresolved": 1, "relations": 3},
            "input_fit": {"fit": 1, "mismatch": 1, "unknown": 0},
            "arm": "advisory", "offered": 0, "referenced": False,
        },
        "results": {"qc": {
            "task_id": "task-fixed", "agent_id": "analyst", "ok": True, "status": "done",
            "workdir": str(workdir), "workdir_id": "task_fixed", "outputs": ["outputs/result.tsv"],
        }},
        "review": {"verdict": "accept", "checklist": [
            {"id": "conversion_rate", "status": "done", "evidence": "qc/result.tsv"},
            {"id": "coverage_snp", "status": "partly",
             "evidence": "C:/Users/alice/private.tsv api_key=should-not-leak"},
            {"id": "differential_model", "status": "done", "evidence": "비적용 확인"},
        ]},
    }
    return settings, request


def test_fixed_research_request_has_six_auditable_device_rows(tmp_path):
    settings, request = fixed_request(tmp_path)
    report = build_report(request, settings)

    assert len(report["devices"]) == 6
    assert {row["id"] for row in report["devices"]} == {
        "vocabulary_edam", "checklist", "analysis_pack", "semantics", "operational_ontology", "shadow_request",
    }
    for row in report["devices"]:
        assert row["status"] in {"작동", "켜졌으나 미사용", "꺼짐", "오류"}
        assert row["decisions"]
        assert row["code_locations"]
        assert row["record_locations"]

    checklist = next(row for row in report["devices"] if row["id"] == "checklist")
    assert [(item["id"], item["plan_included"], item["owner_steps"], item["review_status"])
            for item in checklist["items"]] == [
        ("conversion_rate", True, ["qc"], "done"),
        ("coverage_snp", True, [], "partly"),
        ("differential_model", True, [], "done"),
    ]
    shadow = next(row for row in report["devices"] if row["id"] == "shadow_request")
    assert shadow["evaluation"] == "평가 완료"
    assert shadow["offered_count"] == 0
    assert shadow["referenced"] is False

    rendered = render_report(report)
    assert "| 단어장·EDAM | 작동 |" in rendered
    assert "평가 완료 · 후보 0건" in rendered
    assert str(tmp_path) not in rendered
    assert "C:/Users" not in rendered and "should-not-leak" not in rendered


def test_semantics_off_records_reason_and_time(tmp_path):
    settings, request = fixed_request(tmp_path)
    request.pop("semantics_observation")
    request.pop("semantics_ab")
    request["semantic_devices"] = {
        "semantics": {"setting": "off", "stopped": False, "stopped_at": None},
        "errors": [{"device": "semantics", "path": "setting", "reason": "configured_off",
                    "at": "2026-10-18T13:00:00+09:00", "impact": "semantics 평가를 하지 않음",
                    "recovery": "semantics.mode를 shadow 또는 ab로 바꾸세요"}],
    }

    report = build_report(request, settings)
    semantics = next(row for row in report["devices"] if row["id"] == "semantics")
    shadow = next(row for row in report["devices"] if row["id"] == "shadow_request")
    assert semantics["status"] == "꺼짐"
    assert semantics["off"] == {"reason": "configured_off", "at": "2026-10-18T13:00:00+09:00"}
    assert shadow["evaluation"] == "평가 안 됨"
    assert "꺼짐: configured_off · 2026-10-18T13:00:00+09:00" in render_report(report)


def test_shadow_candidates_and_plan_reference_are_preserved(tmp_path):
    settings, request = fixed_request(tmp_path)
    request["semantics_ab"] = {"arm": "advisory", "offered": ["sem:0123abcd"]}
    request["plan"]["steps"][0]["input_refs"] = ["sem:0123abcd"]
    request["semantics_observation"].update(offered=1, referenced=True)

    shadow = next(row for row in build_report(request, settings)["devices"] if row["id"] == "shadow_request")
    assert shadow["offered"] == ["sem:0123abcd"]
    assert shadow["offered_count"] == 1 and shadow["referenced"] is True


def test_bundle_writes_markdown_json_and_portfolio_publishes_same_markdown(tmp_path):
    settings, request = fixed_request(tmp_path)
    bundle = Path(build_request_bundle(request, settings)["path"])
    markdown = (bundle / "semantic_report.md").read_text(encoding="utf-8")
    machine = json.loads((bundle / "semantic_report.json").read_text(encoding="utf-8"))
    assert len(machine["devices"]) == 6
    assert str(tmp_path) not in markdown
    assert str(tmp_path) not in json.dumps(machine, ensure_ascii=False)

    portfolio_root = tmp_path / "portfolio"
    portfolio_root.mkdir()
    p_settings, p_request, p_bundle, assets, images, decisions, tasks = portfolio_fixture(portfolio_root)
    (p_bundle / "semantic_report.md").write_text(markdown, encoding="utf-8")
    (p_bundle / "semantic_report.json").write_text(json.dumps(machine, ensure_ascii=False), encoding="utf-8")
    files = build_portfolio_files(p_request, p_settings, assets, images, decisions=decisions, tasks=tasks,
                                  bundle=p_bundle)
    assert files["run/semantic_report.md"] == markdown


def test_semantic_report_is_on_by_default_and_can_be_disabled(tmp_path):
    settings, request = fixed_request(tmp_path)
    assert settings.research.semantic_report is True
    settings.research.semantic_report = False
    bundle = Path(build_request_bundle(request, settings)["path"])
    assert not (bundle / "semantic_report.md").exists()
    assert not (bundle / "semantic_report.json").exists()
