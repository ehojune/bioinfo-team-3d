from __future__ import annotations

import shutil
from pathlib import Path

from labhq.research import semantics_input_fit as fit
from labhq.research import semantics_shadow as shadow
from labhq.research.contract import plan_sha256
from labhq.research.semantics_shadow import ShadowPaths, build_report, render_report
from labhq.vocab import current, load
from tests.semantics_shadow_lab import fake_hub, line_for, request_row
from tests.test_research_protocol import valid_plan


PACK = {"id": "single_cell_de", "version": "2", "sha256": "a" * 64}


def plan(data_type: str | None, *, version: str | None = None, agent: str = "analyst") -> dict:
    declared = [] if data_type is None else [{"name": "outputs/source.tsv", "data_type": data_type,
                                               "vocab": version or current().sha256}]
    return {
        "protocol": {"packs": [PACK]},
        "steps": [
            {"id": "source", "agent_id": "analyst", "input_refs": [], "outputs": ["outputs/source.tsv"],
             "output_types": declared},
            {"id": "analysis", "agent_id": agent, "input_refs": ["step:source/outputs/source.tsv"],
             "outputs": ["outputs/result.tsv"]},
        ],
    }


def test_fit_mismatch_and_missing_declaration_are_counted_per_step():
    assert fit.evaluate(plan("raw_counts")) == {"fit": 1, "mismatch": 0, "unknown": 1}
    assert fit.evaluate(plan("sequence_reads")) == {"fit": 0, "mismatch": 1, "unknown": 1}
    assert fit.evaluate(plan(None)) == {"fit": 0, "mismatch": 0, "unknown": 2}


def test_a_declaration_under_another_vocabulary_version_is_unknown():
    assert fit.evaluate(plan("sequence_reads", version="b" * 64)) == {"fit": 0, "mismatch": 0, "unknown": 2}
    assert fit.evaluate(plan("raw_counts", version="b" * 64)) == {"fit": 0, "mismatch": 0, "unknown": 2}


def test_the_steps_own_agent_operation_comes_before_the_pack():
    # data_steward maps to data_retrieval, which has no rule: unknown, not the pack's de_analysis mismatch
    assert fit.evaluate(plan("sequence_reads", agent="data_steward")) == {"fit": 0, "mismatch": 0, "unknown": 2}
    assert fit.evaluate(plan("sequence_reads", agent="analyst")) == {"fit": 0, "mismatch": 1, "unknown": 1}


def test_local_key_judgement_does_not_need_the_optional_edam_subset(tmp_path):
    vocab_dir = tmp_path / "vocab"
    vocab_dir.mkdir()
    source = Path(fit.__file__).parents[1] / "vocab"
    shutil.copy2(source / "output_types.yaml", vocab_dir / "output_types.yaml")
    vocab = load(vocab_dir)

    assert vocab.edam_sha256 is None
    assert fit.evaluate(plan("raw_counts", version=vocab.sha256), vocab=vocab)["fit"] == 1
    assert fit.table_sha256(vocab) is not None


def test_the_table_digest_follows_its_meaning_not_its_comments(tmp_path, monkeypatch):
    vocab = current()
    original = fit.RELATION_FILE.read_text(encoding="utf-8")
    copy = tmp_path / "semantics_input_fit.yaml"
    monkeypatch.setattr(fit, "RELATION_FILE", copy)
    try:
        copy.write_text("# another comment\n" + original, encoding="utf-8")
        fit.reset_cache()
        commented = fit.table_sha256(vocab)
        assert "fit: [de_table]" in original
        copy.write_text(original.replace("fit: [de_table]", "fit: [de_table, table]"), encoding="utf-8")
        fit.reset_cache()
        changed = fit.table_sha256(vocab)
    finally:
        monkeypatch.undo()
        fit.reset_cache()
    assert commented == fit.table_sha256(vocab)
    assert changed is not None and changed != commented


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
    assert line["input_fit_sha256"] == fit.table_sha256(current())
    assert shadow.SHA_HEX.fullmatch(line["input_fit_sha256"])
    assert shadow.readable_request(line)
    # the declaration now carries the real vocabulary version, which the worker allows on vocab_sha256 only
    allowed = {"vocab_sha256": current().sha256}
    assert shadow.boundary_problems(line, shadow.sensitive_values(shadow.take_snapshot(
        hub, "req_fit", shadow.ShadowConfig())), allowed_fields=allowed) == []


def test_report_sums_only_fit_verdict_counts_per_table_version(tmp_path):
    paths = ShadowPaths(tmp_path)
    paths.root.mkdir(parents=True, exist_ok=True)
    row = ('{"v":1,"type":"request","ts":1,"rows":{},"snapshot_ms":1,"busy_skipped":0,"ms":2,'
           '"provenance":{"status":"ok","ms":1},"objects":{"status":"ok","ms":1,"objects":{},'
           '"link_total":0,"unresolved":0,"pending_jobs":0},"hash":{},'
           '"input_fit":{"fit":%d,"mismatch":1,"unknown":3}%s}\n')
    version = ',"input_fit_sha256":"%s"'
    paths.log.write_text(row % (2, "") + row % (1, version % ("a" * 64)) + row % (4, version % ("a" * 64))
                         + row % (5, version % ("c" * 64)) + row % (9, version % "not-a-digest"),
                         encoding="utf-8")

    report = build_report(paths, setting="shadow")
    assert report["input_fit"] == {"-": {"fit": 2, "mismatch": 1, "unknown": 3},
                                   "a" * 12: {"fit": 5, "mismatch": 2, "unknown": 6},
                                   "c" * 12: {"fit": 5, "mismatch": 1, "unknown": 3}}
    rendered = render_report(report)
    assert f"{'a' * 12} fit 5 · mismatch 2 · unknown 6" in rendered and "- fit 2 · mismatch 1" in rendered
