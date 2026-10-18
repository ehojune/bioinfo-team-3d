import asyncio
import json
import platform
import threading
from pathlib import Path

import pytest

from labhq.gateway.server import Hub
from labhq.portfolio import build_portfolio_files
from labhq.request_bundle import build_request_bundle
from labhq.research import semantics_shadow
from labhq.research.semantic_device_report import build_report, render_report
from labhq.settings import Settings
from labhq.vocab.topic_checklists import ChecklistItem
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
        "errors": [{"code": "configured_off", "device": "semantics", "stage": "setting",
                    "at": "2026-10-18T13:00:00+09:00",
                    "message": "semantics 평가가 설정으로 꺼져 있습니다."}],
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


def test_report_redacts_authorization_bearer_and_basic_credentials(tmp_path):
    settings, request = fixed_request(tmp_path)
    request["review"]["checklist"][0]["evidence"] = (
        "Authorization: Bearer bearer-value Bearer standalone-value")
    request["review"]["checklist"][1]["evidence"] = "Authorization: Basic basic-value"

    payload = json.dumps(build_report(request, settings), ensure_ascii=False)

    assert "bearer-value" not in payload
    assert "standalone-value" not in payload
    assert "basic-value" not in payload


def test_report_redacts_arbitrary_posix_absolute_paths(tmp_path):
    settings, request = fixed_request(tmp_path)
    paths = ["/workspace/team/private.tsv", "/scratch/run/secret.txt",
             "/project/cohort/sample.csv", "/root/.config/credential"]
    request["review"]["checklist"][0]["evidence"] = " ".join(paths)

    payload = json.dumps(build_report(request, settings), ensure_ascii=False)

    assert all(path not in payload for path in paths)
    assert "<local-path>" in payload


def test_other_request_broken_shadow_line_does_not_mark_current_request_error(tmp_path):
    settings, request = fixed_request(tmp_path)
    request.pop("semantics_observation")
    settings.semantics = {"mode": "shadow"}
    root = Path(settings.gateway.state_dir) / "semantics"
    root.mkdir(parents=True)
    valid = {
        "type": "request", "request_id": request["id"], "ts": 1792299500, "epoch": 4,
        "provenance": {"status": "ok", "candidates": 0, "excluded": {}},
        "objects": {"status": "ok", "relations": 1},
        "input_fit": {"fit": 1, "mismatch": 0, "unknown": 0}, "referenced": False,
    }
    (root / "shadow.jsonl").write_text(
        '{"type":"request","request_id":"another-request"\n' + json.dumps(valid) + "\n",
        encoding="utf-8")

    report = build_report(request, settings)
    semantics = next(row for row in report["devices"] if row["id"] == "semantics")

    assert semantics["status"] == "작동"
    assert "error" not in semantics


def test_current_request_latest_malformed_observation_is_an_error(tmp_path):
    settings, request = fixed_request(tmp_path)
    request.pop("semantics_observation")
    settings.semantics = {"mode": "shadow"}
    root = Path(settings.gateway.state_dir) / "semantics"
    root.mkdir(parents=True)
    valid = {
        "type": "request", "request_id": request["id"], "ts": 1792299400, "epoch": 4,
        "provenance": {"status": "ok", "candidates": 0, "excluded": {}},
        "objects": {"status": "ok", "relations": 1},
        "input_fit": {"fit": 1, "mismatch": 0, "unknown": 0}, "referenced": False,
    }
    malformed = {**valid, "ts": 1792299500,
                 "provenance": {"status": "ok", "candidates": -1, "excluded": {}}}
    (root / "shadow.jsonl").write_text(
        json.dumps(valid) + "\n" + json.dumps(malformed) + "\n", encoding="utf-8")

    report = build_report(request, settings)
    semantics = next(row for row in report["devices"] if row["id"] == "semantics")

    assert semantics["status"] == "오류"
    assert semantics["error"] == {
        "code": "semantics_observed_state_failed", "device": "semantics",
        "stage": "advisory_projection", "at": report["generated_at"],
        "message": "현재 요청의 의미 관측을 읽지 못해 요청별 평가를 완료하지 못했습니다.",
    }
    assert report["errors"][-1] == semantics["error"]
    rendered = render_report(report)
    assert "| 오류 코드 | 장치 | 단계 | 시각 | 설명 |" in rendered
    assert "RuntimeError" not in rendered and "Traceback" not in rendered


def test_failed_shadow_models_do_not_publish_zero_counts_or_runtime_as_relations(tmp_path):
    settings, request = fixed_request(tmp_path)
    request["semantics_observation"]["provenance"] = {
        "status": "error", "error_kind": "RuntimeError", "ms": 12.5,
    }
    request["semantics_observation"]["objects"] = {
        "status": "timeout", "error_kind": "TimeoutError", "ms": 987.6,
    }

    report = build_report(request, settings)
    semantics = next(row for row in report["devices"] if row["id"] == "semantics")
    ontology = next(row for row in report["devices"] if row["id"] == "operational_ontology")

    assert semantics["status"] == ontology["status"] == "오류"
    assert all("reuse_candidates" not in decision for decision in semantics["decisions"]
               if isinstance(decision, dict))
    assert semantics["impact"] == "입력 적합도와 재사용 후보를 계산하지 못함"
    assert ontology["decisions"] == ["객체·관계 집계 계산 못 함"]
    assert ontology["impact"] == "객체·관계와 미해결 항목을 계산하지 못함"
    assert "object_relation_counts" not in json.dumps(ontology["decisions"], ensure_ascii=False)
    assert "ms" not in json.dumps(ontology["decisions"], ensure_ascii=False)

    assert semantics["error"] in report["errors"]
    assert ontology["error"] in report["errors"]
    assert semantics["error"] == {
        "code": "semantics_shadow_provenance_failed", "device": "semantics",
        "stage": "shadow_provenance", "at": "2026-10-18T13:58:20+09:00",
        "message": "재사용 후보 provenance 관측이 error로 끝나 계산하지 못했습니다.",
    }
    assert ontology["error"] == {
        "code": "semantics_shadow_objects_failed", "device": "운영 ontology",
        "stage": "shadow_objects", "at": "2026-10-18T13:58:20+09:00",
        "message": "객체·관계 관측이 timeout으로 끝나 계산하지 못했습니다.",
    }


def test_observation_exception_text_is_not_published(tmp_path):
    settings, request = fixed_request(tmp_path)
    request.pop("semantics_observation")
    request["semantics_observation_error"] = "RuntimeError: private cohort path"

    report = build_report(request, settings)
    payload = json.dumps(report, ensure_ascii=False) + render_report(report)

    assert report["errors"][-1]["code"] == "semantics_observed_state_failed"
    assert "RuntimeError" not in payload and "private cohort path" not in payload


def test_report_uses_the_checklist_snapshot_frozen_with_the_plan(tmp_path, monkeypatch):
    settings, request = fixed_request(tmp_path)
    request["plan"]["checklist"] = {"frozen_check": "step:qc"}
    request["review"]["checklist"] = [
        {"id": "frozen_check", "status": "done", "evidence": "qc/result.tsv"}]
    request["research_contract"]["checklist_snapshot"] = [
        {"id": "frozen_check", "check": "실행 당시 점검", "why": "실행 당시 이유",
         "topic": "dna_methylation"}]
    monkeypatch.setattr(
        "labhq.research.semantic_device_report.topic_checklists.load",
        lambda: {"dna_methylation": [
            ChecklistItem("new_check", "업데이트 뒤 점검", "새 이유", "dna_methylation")]})

    checklist = next(row for row in build_report(request, settings)["devices"]
                     if row["id"] == "checklist")

    assert [item["id"] for item in checklist["items"]] == ["frozen_check"]


def test_general_request_bundle_has_no_research_semantic_report(tmp_path):
    settings, request = fixed_request(tmp_path)
    request.pop("research_contract")

    bundle = Path(build_request_bundle(request, settings)["path"])

    assert not (bundle / "semantic_report.md").exists()
    assert not (bundle / "semantic_report.json").exists()


@pytest.mark.asyncio
async def test_terminal_bundle_waits_for_shadow_and_freezes_its_observation(tmp_path, monkeypatch):
    settings, request = fixed_request(tmp_path)
    settings.semantics = {"mode": "shadow", "timeout_s": 2}
    request.pop("semantics_observation")
    hub = Hub(settings)
    hub.requests[request["id"]] = request
    started, release, bundle_called = threading.Event(), threading.Event(), threading.Event()
    captured = {}

    def delayed_work(_gen, _snap):
        started.set()
        assert release.wait(5)
        semantics_shadow.append_line(hub.semantics_shadow.paths, {
            "type": "request", "request_id": request["id"], "ts": 1792299500, "epoch": 1,
            "provenance": {"status": "ok", "candidates": 0, "excluded": {}},
            "objects": {"status": "ok", "relations": 1},
            "input_fit": {"fit": 1, "mismatch": 0, "unknown": 0}, "referenced": False,
        })

    def capture_bundle(_rid, frozen, _tasks, _caps):
        captured["request"] = frozen
        bundle_called.set()
        return {"bundle": {"path": str(tmp_path / "bundle"), "status": "complete"}}

    monkeypatch.setattr(hub.semantics_shadow, "work", delayed_work)
    monkeypatch.setattr(hub, "_bundle_outcome", capture_bundle)
    task = hub.schedule_terminal(request["id"], "request.completed", {"ok": True})
    assert task is not None and await asyncio.to_thread(started.wait, 2)
    await asyncio.to_thread(bundle_called.wait, 0.3)
    release.set()
    await asyncio.wait_for(task, 5)

    assert captured["request"]["semantics_observation"]["request_id"] == request["id"]
    hub.store.close()
