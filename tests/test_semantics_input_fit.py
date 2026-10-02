from __future__ import annotations

import shutil
from pathlib import Path

from labhq.research import semantics_input_fit as fit
from labhq.research import semantics_shadow as shadow
from labhq.research.contract import plan_sha256
from labhq.research.semantics_shadow import ShadowPaths, build_report, render_report
from labhq.vocab import load
from tests.semantics_shadow_lab import fake_hub, line_for, request_row
from tests.test_research_protocol import valid_plan


PACK = {"id": "single_cell_de", "version": "2", "sha256": "a" * 64}


def plan(data_type: str | None) -> dict:
    declared = [] if data_type is None else [{"name": "outputs/source.tsv", "data_type": data_type,
                                               "vocab": "b" * 64}]
    return {
        "protocol": {"packs": [PACK]},
        "steps": [
            {"id": "source", "agent_id": "analyst", "input_refs": [], "outputs": ["outputs/source.tsv"],
             "output_types": declared},
            {"id": "analysis", "agent_id": "analyst", "input_refs": ["step:source/outputs/source.tsv"],
             "outputs": ["outputs/result.tsv"]},
        ],
    }


def test_fit_mismatch_and_missing_declaration_are_counted_per_step():
    assert fit.evaluate(plan("raw_counts")) == {"fit": 1, "mismatch": 0, "unknown": 1}
    assert fit.evaluate(plan("sequence_reads")) == {"fit": 0, "mismatch": 1, "unknown": 1}
    assert fit.evaluate(plan(None)) == {"fit": 0, "mismatch": 0, "unknown": 2}


def test_local_key_judgement_does_not_need_the_optional_edam_subset(tmp_path):
    vocab_dir = tmp_path / "vocab"
    vocab_dir.mkdir()
    source = Path(fit.__file__).parents[1] / "vocab"
    shutil.copy2(source / "output_types.yaml", vocab_dir / "output_types.yaml")
    vocab = load(vocab_dir)

    assert vocab.edam_sha256 is None
    assert fit.evaluate(plan("raw_counts"), vocab=vocab)["fit"] == 1


def test_shadow_line_contains_counts_only(tmp_path):
    research_plan = valid_plan(packs=[PACK], steps=2)
    research_plan["steps"][0].update(agent_id="analyst", input_refs=[], outputs=["outputs/source.tsv"],
                                      output_types=plan("raw_counts")["steps"][0]["output_types"])
    research_plan["steps"][1].update(agent_id="analyst", input_refs=["step:s1/outputs/source.tsv"])
    request = request_row("req_fit", [])
    request["plan"] = research_plan
    request["research_contract"] = {"plan_sha256": plan_sha256(research_plan)}
    request["intake"] = {"work_kind": "research"}
    hub = fake_hub(tmp_path, {"req_fit": request}, {})

    line = line_for(hub, "req_fit")

    assert line["input_fit"] == {"fit": 1, "mismatch": 0, "unknown": 1}
    assert shadow.boundary_problems(line, shadow.sensitive_values(shadow.take_snapshot(
        hub, "req_fit", shadow.ShadowConfig()))) == []


def test_report_sums_only_fit_verdict_counts(tmp_path):
    paths = ShadowPaths(tmp_path)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.log.write_text(
        '{"v":1,"type":"request","ts":1,"rows":{},"snapshot_ms":1,"busy_skipped":0,"ms":2,'
        '"provenance":{"status":"ok","ms":1},"objects":{"status":"ok","ms":1,"objects":{},'
        '"link_total":0,"unresolved":0,"pending_jobs":0},"hash":{},'
        '"input_fit":{"fit":2,"mismatch":1,"unknown":3}}\n', encoding="utf-8"
    )

    report = build_report(paths, setting="shadow")
    assert report["input_fit"] == {"fit": 2, "mismatch": 1, "unknown": 3}
    assert "fit 2" in render_report(report)
