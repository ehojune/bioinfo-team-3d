"""Provenance model in shadow (#150 B1): live rows, read tolerantly, never more confident than the rows allow."""
from __future__ import annotations

from labhq.research.semantics import records_from_rows
from tests.semantics_shadow_lab import fake_hub, line_for, request_row, task_row, workspace


def _two_requests(tmp_path, *, first_tasks=None, output_types=None):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", {"outputs/counts.tsv": b"gene\tn\nX\t1\n"})
    requests = {"req_a": request_row("req_a", [("s1", "analyst")], created_at=1.0),
                "req_b": request_row("req_b", [("s1", "analyst")], created_at=2.0)}
    tasks = first_tasks or {"task_a1": task_row("req_a", "task_a1", "s1", "analyst", wd, ["outputs/counts.tsv"],
                                                output_types=output_types)}
    wd_b, _ = workspace(tmp_path, "task_b1", "analyst", {})
    tasks["task_b1"] = task_row("req_b", "task_b1", "s1", "analyst", wd_b, [])
    return requests, tasks, wd


def test_rows_that_do_not_parse_are_skipped_and_counted():
    requests = {"req_ok": {"id": "req_ok", "project_id": "p1"},
                "req_bad": {"id": "req_bad", "research_contract": {"plan_sha256": "x"}, "plan": {"steps": "no"}},
                "req_odd": ["not", "a", "row"]}
    tasks = {"task_ok": {"request_id": "req_ok", "result": {"outputs": ["outputs/a"]}, "payload": {}},
             "task_list": {"request_id": "req_ok", "result": {"outputs": "outputs/a"}, "payload": {}},
             "task_orphan": {"request_id": "req_bad", "result": {}, "payload": {}},
             "task_meta": {"request_id": "req_ok", "result": {}, "payload": {"meta": "x"}}}
    records, invalid = records_from_rows(requests, tasks)
    assert invalid == 5
    assert set(records.requests) == {"req_ok"} and set(records.tasks) == {"task_ok"}


def test_live_rows_have_no_declared_type_so_there_is_no_candidate(tmp_path):
    requests, tasks, _ = _two_requests(tmp_path)
    line = line_for(fake_hub(tmp_path, requests, tasks), "req_b")
    prov = line["provenance"]
    assert prov["status"] == "ok" and prov["history_artifacts"] == 1 and prov["candidates"] == 0
    assert prov["excluded"]["type_unknown"] == 1 and prov["excluded"]["incomplete"] == 0
    assert prov["incomplete"] is False and prov["candidate_refs"] == []
    assert 0 <= prov["unknown_ratio"] <= 1 and prov["runs"] == 1


def test_one_broken_row_makes_the_whole_request_incomplete(tmp_path):
    requests, tasks, wd = _two_requests(tmp_path, output_types={"outputs/counts.tsv": "raw_counts"})
    tasks["task_a2"] = {**task_row("req_a", "task_a2", "s1", "analyst", wd, []), "result": {"outputs": "broken"}}
    prov = line_for(fake_hub(tmp_path, requests, tasks), "req_b")["provenance"]
    assert prov["rows_invalid"] == 1 and prov["incomplete"] is True and prov["candidates"] == 0
    assert prov["excluded"]["incomplete"] == prov["history_artifacts"] == 1


def test_a_cut_history_is_incomplete(tmp_path):
    requests = {f"req_h{i:02d}": request_row(f"req_h{i:02d}", [("s1", "analyst")], created_at=float(i))
                for i in range(12)}
    line = line_for(fake_hub(tmp_path, requests, {}), "req_h11", history=10)
    assert line["rows"]["history_truncated"] is True and line["provenance"]["incomplete"] is True
    assert line["rows"]["requests"] == 10


def test_two_reporters_or_a_failed_generator_is_not_generated(tmp_path):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", {"outputs/counts.tsv": b"x"})
    types = {"outputs/counts.tsv": "raw_counts"}
    both = {"task_a1": task_row("req_a", "task_a1", "s1", "analyst", wd, ["outputs/counts.tsv"], output_types=types),
            "task_a2": task_row("req_a", "task_a2", "s1", "analyst", wd, ["outputs/counts.tsv"], output_types=types,
                                attempt=2)}
    requests, tasks, _ = _two_requests(tmp_path, first_tasks=both)
    assert line_for(fake_hub(tmp_path, requests, tasks), "req_b")["provenance"]["excluded"]["not_generated"] == 1
    failed = {"task_a1": task_row("req_a", "task_a1", "s1", "analyst", wd, ["outputs/counts.tsv"], ok=False,
                                  output_types=types)}
    requests, tasks, _ = _two_requests(tmp_path, first_tasks=failed)
    prov = line_for(fake_hub(tmp_path, requests, tasks), "req_b")["provenance"]
    assert prov["excluded"]["not_generated"] == 1 and prov["candidates"] == 0


def test_the_request_s_own_outputs_get_a_lineage_audit_not_a_candidate(tmp_path):
    requests, tasks, _ = _two_requests(tmp_path)
    prov = line_for(fake_hub(tmp_path, requests, tasks), "req_a")["provenance"]
    assert prov["artifacts"] == 1 and prov["history_artifacts"] == 0 and prov["candidates"] == 0
    assert prov["lineage"]["roots"] == 1 and prov["lineage"]["edges"] >= 1
    assert len(prov["model_sha256"]) == 64


def test_another_project_is_never_read(tmp_path):
    requests, tasks, _ = _two_requests(tmp_path)
    requests["req_a"]["project_id"] = "other"
    line = line_for(fake_hub(tmp_path, requests, tasks), "req_b")
    assert line["rows"]["requests"] == 1 and line["provenance"]["history_artifacts"] == 0


# ---------------------------------------------------------------- typed output declarations (#221 todo 2)

import json  # noqa: E402

import pytest  # noqa: E402

from labhq import vocab as output_vocab  # noqa: E402
from labhq.research import semantics_shadow as shadow  # noqa: E402
from labhq.settings import DataZone  # noqa: E402
from labhq.vocab import declare  # noqa: E402

V = output_vocab.current()
CANARY = "CANARY-shadow-5d1e"


def bucket(key):
    return V.edam_id(key) or "local"


def typed_row(rid, tid, step, agent, wd, outputs, decl, *, version=None):
    row = task_row(rid, tid, step, agent, wd, outputs)
    meta = {"output_types_vocab": version or V.sha256, "output_types": decl}
    row["payload"]["meta"].update(meta)
    row["result"]["output_types"] = declare.runner_records(outputs, meta, V)
    return row


def _typed_pair(tmp_path, *, version=None, zones=None):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", {"outputs/counts.tsv": b"gene\tn\nX\t1\n"})
    wd_b, _ = workspace(tmp_path, "task_b1", "analyst", {})
    requests = {"req_a": request_row("req_a", [("s1", "analyst")], created_at=1.0),
                "req_b": request_row("req_b", [("s1", "analyst")], created_at=2.0)}
    tasks = {"task_a1": typed_row("req_a", "task_a1", "s1", "analyst", wd, ["outputs/counts.tsv"],
                                  {"outputs/counts.tsv": {"data_type": "raw_counts"}}, version=version),
             "task_b1": task_row("req_b", "task_b1", "s1", "analyst", wd_b, [])}
    return fake_hub(tmp_path, requests, tasks, zones=zones), requests


def test_a_runner_record_types_an_orchestrate_output_so_a_later_request_sees_a_candidate(tmp_path):
    hub, _ = _typed_pair(tmp_path)
    observed: dict = {}
    first = line_for(hub, "req_a", observed)
    assert first["vocab_sha256"] == V.sha256
    assert first["provenance"]["types"] == {"data_type": {bucket("raw_counts"): {"declared": 1}},
                                            "format": {bucket("tsv"): {"inferred": 1}}}
    second = line_for(hub, "req_b", observed)["provenance"]
    assert second["candidates"] == 1 and second["excluded"]["type_unknown"] == 0
    assert "raw_counts" not in json.dumps(second) and "counts" not in json.dumps(second)


def test_a_declaration_made_under_another_vocabulary_is_never_reinterpreted(tmp_path):
    hub, _ = _typed_pair(tmp_path, version="0" * 64)
    observed: dict = {}
    first = line_for(hub, "req_a", observed)["provenance"]
    assert first["types"] == {"data_type": {"unknown": {"unknown": 1}}, "format": {"unknown": {"unknown": 1}}}
    second = line_for(hub, "req_b", observed)["provenance"]
    assert second["candidates"] == 0 and second["excluded"]["type_unknown"] == 1


def test_outputs_outside_an_allowed_zone_count_only_as_withheld(tmp_path):
    hub, _ = _typed_pair(tmp_path, zones=[DataZone(path=str(tmp_path / "runs"), level="restricted")])
    types = line_for(hub, "req_a")["provenance"]["types"]
    assert types == {"data_type": {"withheld": {"declared": 1}}, "format": {"withheld": {"inferred": 1}}}


def test_the_line_carries_declaration_counts_rebuilt_from_fixed_fields(tmp_path):
    hub, requests = _typed_pair(tmp_path)
    requests["req_a"]["output_types_stats"] = {"outputs": 3, "data_declared": 1, "format_declared": 0,
                                               "issues": {"unknown_key": 2, CANARY: 1}, "vocab": V.sha256,
                                               "note": CANARY}
    line = line_for(hub, "req_a")
    assert line["provenance"]["declarations"] == {"outputs": 3, "data_declared": 1, "format_declared": 0,
                                                  "issues": {"unknown_key": 2}}
    assert CANARY not in json.dumps(line) and shadow.type_problems(line, V.edam_ids) == []


@pytest.mark.parametrize("change, problem", [
    (lambda l: l["provenance"]["types"]["data_type"].update({"data_" + "9999": {"declared": 1}}), "type_bucket"),
    (lambda l: l["provenance"]["types"]["data_type"].update({CANARY: {"declared": 1}}), "type_bucket"),
    (lambda l: l["provenance"]["types"]["format"].update({"local": {"guessed": 1}}), "type_bucket"),
    (lambda l: l["provenance"]["types"]["format"].update({"local": {"declared": "1"}}), "type_bucket"),
    (lambda l: l["provenance"]["types"].update({"method": {}}), "type_fields"),
    (lambda l: l["provenance"]["declarations"]["issues"].update({CANARY: 1}), "type_declarations"),
    (lambda l: l["objects"]["artifact_types"]["format"].update({"declared": CANARY}), "type_objects"),
    (lambda l: l.update(vocab_sha256=CANARY), "type_version"),
])
def test_type_fields_are_a_finite_allow_list_for_keys_and_values(tmp_path, change, problem):
    hub, _ = _typed_pair(tmp_path)
    line = line_for(hub, "req_a")
    line["provenance"]["declarations"] = {"outputs": 1, "data_declared": 1, "format_declared": 0, "issues": {}}
    assert shadow.type_problems(line, V.edam_ids) == []
    change(line)
    assert problem in shadow.type_problems(line, V.edam_ids)
