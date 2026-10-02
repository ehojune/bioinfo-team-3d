"""Provenance model in shadow (#150 B1): live rows, read tolerantly, never more confident than the rows allow."""
from __future__ import annotations

from pathlib import Path

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
    for req in requests.values():
        req["text"] = "Create outputs/counts.tsv."
        req["references"] = [{"kind": "doi", "value": "10.0000/reuse-input"}]
        req["plan"]["steps"][0].update({
            "outputs": ["outputs/counts.tsv"],
            "output_types": [{"name": "outputs/counts.tsv", "data_type": "raw_counts",
                              "format": "tsv", "vocab": V.sha256}],
        })
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


# ---------------------------------------------------------------- reuse selector precision (#263)

def _target_request(rid, input_name, output_name="wanted.tsv", data_type="table", *, created_at=9.0):
    req = request_row(rid, [("make", "analyst")], created_at=created_at,
                      text=f"Read inputs/{input_name} and save outputs/{output_name}.")
    req["references"] = [{"kind": "doi", "value": f"10.0000/{input_name}"}]
    req["plan"]["steps"][0].update({
        "outputs": [f"outputs/{output_name}"],
        "output_types": [{"name": f"outputs/{output_name}", "data_type": data_type,
                          "format": "tsv", "vocab": V.sha256}],
    })
    return req


def _history_task(tmp_path, rid, tid, output_name, data_type):
    wd, _ = workspace(tmp_path, tid, "analyst", {f"outputs/{output_name}": b"x\n"})
    return typed_row(rid, tid, "make", "analyst", wd, [f"outputs/{output_name}"], {
        f"outputs/{output_name}": {"data_type": data_type, "format": "tsv"},
    })


def _accepted_reference(task, reference):
    manifest_path = Path(task["result"]["workdir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runs"] = {task["result"]["task_id"]: {"reference_dirs": [str(reference)]}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_reuse_requires_same_input_identity_and_target_type_before_ranking(tmp_path):
    requests = {
        "req_same": _target_request("req_same", "same.tsv", "same.tsv", created_at=1.0),
        "req_input": _target_request("req_input", "other.tsv", "other.tsv", created_at=2.0),
        "req_type": _target_request("req_type", "same.tsv", "check.md", "qc_report", created_at=3.0),
        "req_now": _target_request("req_now", "same.tsv", created_at=4.0),
    }
    tasks = {
        "task_same": _history_task(tmp_path, "req_same", "task_same", "same.tsv", "table"),
        "task_input": _history_task(tmp_path, "req_input", "task_input", "other.tsv", "table"),
        "task_type": _history_task(tmp_path, "req_type", "task_type", "check.md", "qc_report"),
    }
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    for rid in ("req_same", "req_input", "req_type"):
        line_for(hub, rid, observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["history_artifacts"] == 3 and prov["candidates"] == 1, (
        prov["excluded"]["input_unknown"], prov["excluded"]["input_mismatch"],
        prov["excluded"]["target_type_unknown"], prov["excluded"]["target_type_mismatch"])
    assert prov["excluded"]["input_mismatch"] == 1
    assert prov["excluded"]["target_type_mismatch"] == 1
    assert prov["excluded"]["input_unknown"] == prov["excluded"]["target_type_unknown"] == 0
    assert len(prov["candidate_refs"]) == 1


@pytest.mark.parametrize("missing, reason", [("input", "input_unknown"), ("target", "target_type_unknown")])
def test_reuse_needs_declared_input_identity_and_target_type(tmp_path, missing, reason):
    requests = {
        "req_old": _target_request("req_old", "same.tsv", "same.tsv", created_at=1.0),
        "req_now": _target_request("req_now", "same.tsv", created_at=2.0),
    }
    if missing == "input":
        requests["req_now"]["references"] = []
    else:
        requests["req_now"]["plan"]["steps"][0]["output_types"] = []
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "same.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    line = line_for(hub, "req_now", observed)
    assert line["provenance"]["candidates"] == 0
    assert line["provenance"]["excluded"][reason] == 1
    assert "same.tsv" not in json.dumps(line)
    assert shadow.boundary_problems(line, []) == []


def test_a_gateway_path_reference_is_unknown_even_when_the_file_exists(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "Sample.TSV").write_text("same\n", encoding="utf-8")
    requests = {
        "req_old": _target_request("req_old", "same.tsv", "same.tsv", created_at=1.0),
        "req_now": _target_request("req_now", "same.tsv", created_at=2.0),
    }
    requests["req_now"]["references"] = [{"kind": "path", "value": str(inputs)}]
    requests["req_now"]["text"] = "Read Sample.TSV and save outputs/wanted.tsv."
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "same.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["candidates"] == 0 and prov["excluded"]["input_unknown"] == 1


def test_a_same_host_runner_accepted_reference_is_an_opaque_input_link(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "same.tsv").write_text("same\n", encoding="utf-8")
    requests = {
        "req_old": _target_request("req_old", "same.tsv", "old.tsv", created_at=1.0),
        "req_now": _target_request("req_now", "same.tsv", created_at=2.0),
    }
    for req in requests.values():
        req["references"] = [{"kind": "path", "value": str(inputs)}]
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "old.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    for task in tasks.values():
        _accepted_reference(task, inputs)
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["candidates"] == 1 and prov["excluded"]["input_unknown"] == 0


def test_a_history_input_keeps_its_observed_digest_when_the_reference_file_changes(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "same.tsv"
    source.write_text("before\n", encoding="utf-8")
    requests = {
        "req_old": _target_request("req_old", "same.tsv", "old.tsv", created_at=1.0),
        "req_now": _target_request("req_now", "same.tsv", created_at=2.0),
    }
    for req in requests.values():
        req["references"] = [{"kind": "path", "value": str(inputs)}]
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "old.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    for task in tasks.values():
        _accepted_reference(task, inputs)
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    source.write_text("after\n", encoding="utf-8")
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["candidates"] == 0 and prov["excluded"]["input_mismatch"] == 1


def test_an_accepted_reference_keeps_the_filename_case_used_on_disk(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "Sample.TSV").write_text("same\n", encoding="utf-8")
    requests = {
        "req_old": _target_request("req_old", "Sample.TSV", "old.tsv", created_at=1.0),
        "req_now": _target_request("req_now", "Sample.TSV", created_at=2.0),
    }
    for req in requests.values():
        req["references"] = [{"kind": "path", "value": str(inputs)}]
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "old.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    for task in tasks.values():
        _accepted_reference(task, inputs)
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    assert line_for(hub, "req_now", observed)["provenance"]["candidates"] == 1


def test_github_url_identity_keeps_case_sensitive_ref_path(tmp_path):
    requests = {
        "req_old": _target_request("req_old", "same.tsv", "same.tsv", created_at=1.0),
        "req_now": _target_request("req_now", "same.tsv", created_at=2.0),
    }
    requests["req_old"]["references"] = [{"kind": "github", "value": "https://github.com/org/repo/tree/Data-v1"}]
    requests["req_now"]["references"] = [{"kind": "github", "value": "https://github.com/org/repo/tree/data-v1"}]
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "same.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    hub = fake_hub(tmp_path, requests, tasks)
    observed = {}
    line_for(hub, "req_old", observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["candidates"] == 0 and prov["excluded"]["input_mismatch"] == 1


def test_an_explicit_artifact_input_is_reduced_to_the_same_root_input(tmp_path):
    requests = {
        "req_raw": _target_request("req_raw", "same.tsv", "raw.tsv", created_at=1.0),
        "req_mid": _target_request("req_mid", "same.tsv", "mid.tsv", created_at=2.0),
        "req_now": _target_request("req_now", "same.tsv", "wanted.tsv", created_at=3.0),
    }
    tasks = {
        "task_raw": _history_task(tmp_path, "req_raw", "task_raw", "raw.tsv", "table"),
        "task_mid": _history_task(tmp_path, "req_mid", "task_mid", "mid.tsv", "table"),
    }
    mid_ws = tasks["task_mid"]["result"]["workdir_id"]
    requests["req_now"]["plan"]["steps"][0]["input_refs"] = [f"art:req_mid/{mid_ws}/outputs/mid.tsv"]
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_raw", observed)
    line_for(hub, "req_mid", observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["candidates"] == 2 and len(prov["candidate_refs"]) == 2
    assert prov["excluded"]["input_unknown"] == prov["excluded"]["input_mismatch"] == 0


@pytest.mark.parametrize("made_at", [1.0, 3.0], ids=["earlier_output", "later_output"])
def test_equal_digest_without_an_explicit_artifact_edge_is_not_ancestry(tmp_path, made_at):
    """#269: raw input bytes that match another request's output, made before or after, do not inherit that
    output's producer roots."""
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "raw.tsv").write_bytes(b"x\n")  # the same bytes as req_old's made.tsv
    requests = {
        "req_old": _target_request("req_old", "source.tsv", "made.tsv", created_at=made_at),
        "req_now": _target_request("req_now", "raw.tsv", "wanted.tsv", created_at=2.0),
    }
    requests["req_now"]["references"] = [{"kind": "path", "value": str(inputs)}]
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "made.tsv", "table")}
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    _accepted_reference(tasks["task_now"], inputs)
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["history_artifacts"] == 1 and prov["candidates"] == 0
    assert prov["excluded"]["input_mismatch"] == 1 and prov["excluded"]["input_unknown"] == 0


@pytest.mark.parametrize(("made_at", "candidates"), [(1.0, 1), (3.0, 0)], ids=["earlier", "later"])
def test_an_accepted_reference_to_the_earlier_output_itself_is_ancestry(tmp_path, made_at, candidates):
    """#269: the runner opened the earlier request's output at its own path, as in the 2nd-run replay: that file is
    the output, so it reduces to the producer's root input like an explicit plan edge. A producer that is not
    earlier is no ancestry."""
    requests = {
        "req_old": _target_request("req_old", "source.tsv", "made.tsv", created_at=made_at),
        "req_now": _target_request("req_now", "made.tsv", "wanted.tsv", created_at=2.0),
    }
    tasks = {"task_old": _history_task(tmp_path, "req_old", "task_old", "made.tsv", "table")}
    old_outputs = Path(tasks["task_old"]["result"]["workdir"]) / "outputs"
    requests["req_now"]["references"] = [{"kind": "path", "value": str(old_outputs)}]
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    _accepted_reference(tasks["task_now"], old_outputs)
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_old", observed)
    prov = line_for(hub, "req_now", observed)["provenance"]
    assert prov["history_artifacts"] == 1 and prov["candidates"] == candidates
    assert prov["excluded"]["input_mismatch"] == 1 - candidates and prov["excluded"]["input_unknown"] == 0


def test_a_general_snapshot_keeps_input_refs_for_explicit_artifact_ancestry(tmp_path):
    """#268: the light general plan keeps the declared edge, so it reduces to the producer's root input; the ref
    stays in memory, is a sensitive value and never reaches the line."""
    requests = {
        "req_raw": _target_request("req_raw", "same.tsv", "raw.tsv", created_at=1.0),
        "req_mid": _target_request("req_mid", "same.tsv", "mid.tsv", created_at=2.0),
        "req_now": _target_request("req_now", "unused.tsv", "wanted.tsv", created_at=3.0),
    }
    tasks = {
        "task_raw": _history_task(tmp_path, "req_raw", "task_raw", "raw.tsv", "table"),
        "task_mid": _history_task(tmp_path, "req_mid", "task_mid", "mid.tsv", "table"),
    }
    ref = f"art:req_mid/{tasks['task_mid']['result']['workdir_id']}/outputs/mid.tsv"
    requests["req_now"]["references"] = []  # no shared public link: only the edge can match the inputs
    requests["req_now"]["plan"]["steps"][0]["input_refs"] = [ref, 7]
    wd, _ = workspace(tmp_path, "task_now", "analyst", {})
    tasks["task_now"] = task_row("req_now", "task_now", "make", "analyst", wd, [])
    hub = fake_hub(tmp_path, requests, tasks, zones=[DataZone(path=str(tmp_path), level="internal")])
    observed = {}
    line_for(hub, "req_raw", observed)
    line_for(hub, "req_mid", observed)
    snap = shadow.take_snapshot(hub, "req_now", shadow.ShadowConfig())
    assert snap["requests"]["req_now"]["plan"]["steps"][0]["input_refs"] == [ref]
    assert shadow.sensitive_values(snap)[ref] == "path"
    line = shadow.compute_line(snap, observed, lambda: None, epoch=1)
    prov = line["provenance"]
    assert prov["candidates"] == 2 and prov["excluded"]["input_unknown"] == prov["excluded"]["input_mismatch"] == 0
    assert ref not in json.dumps(line) and "mid.tsv" not in json.dumps(line)
    allowed = {"vocab_sha256": output_vocab.current().sha256}
    assert shadow.boundary_problems(line, shadow.sensitive_values(snap), allowed_fields=allowed) == []
