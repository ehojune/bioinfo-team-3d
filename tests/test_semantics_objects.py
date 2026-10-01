"""Read-only object and link view (#150 B1): projected from rows, counts out, no actions."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from labhq.research.semantics_objects import LINK_TYPES, OBJECT_TYPES, build_view, summarize
from tests.semantics_shadow_lab import fake_hub, line_for, request_row, task_row, workspace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labhq" / "research" / "semantics_objects.py"


def _snapshot():
    req = request_row("req_o", [("s1", "analyst"), ("s2", "ghost")])
    req["plan"]["steps"][1]["depends_on"] = ["s1", "s9"]
    req["references"] = [{"kind": "doi", "value": "10.5555/x"}, {"kind": "path", "value": "/refs/notes"}]
    tasks = {
        "task_p": task_row("req_o", "task_p", None, "cso", None, [], kind="plan"),
        "task_1": task_row("req_o", "task_1", "s1", "analyst", "/w/task_1_analyst", ["outputs/a.tsv", "outputs/b.tsv"]),
        "task_1w": {**task_row("req_o", "task_1w", "s1", "analyst", "/w/task_1_analyst", []), "parent_task": "task_1"},
        "task_2": task_row("req_o", "task_2", "s2", "ghost", "/w/task_2_ghost", []),
        "task_x": {**task_row("req_o", "task_x", "s7", "analyst", None, []), "parent_task": "task_gone"},
    }
    tasks["task_1"]["result"]["pending_jobs"] = ["101"]
    return {"rid": "req_o", "requests": {"req_o": req}, "tasks": tasks,
            "agents": {"cso": {"engine": "mock"}, "analyst": {"engine": "mock"}},
            "jobs_done": {"task_1": {"jobs": [{"job_id": "101", "state": "completed"},
                                              {"job_id": "102", "state": "failed"}]}},
            "approvals": [{"id": "appr_1", "kind": "hpc_submit", "task_id": "task_1", "request_id": "req_o",
                           "state": "approved"},
                          {"id": "appr_2", "kind": "clarify", "task_id": None, "request_id": "req_o", "state": "pending"},
                          {"id": "appr_3", "kind": "tool_permission", "task_id": "task_elsewhere",
                           "request_id": "req_o", "state": "denied"}]}


def test_objects_and_links_are_projected_from_the_rows():
    summary = summarize(build_view(_snapshot()))
    assert summary["objects"] == {"Staff": 2, "Request": 1, "Step": 2, "Task": 5, "Job": 2, "DataAsset": 2,
                                  "Approval": 3, "Artifact": 2}
    links = summary["links"]
    assert links["request_step"] == 2 and links["step_staff"] == 1 and links["step_depends_on"] == 1
    assert links["task_step"] == 3 and links["task_request"] == 1 and links["task_staff"] == 4
    assert links["task_job"] == 2 and links["task_artifact"] == 2 and links["task_parent"] == 1
    assert links["approval_target"] == 2 and links["request_asset"] == 2
    assert summary["unresolved_by"] == {"approval_target": 1, "step_depends_on": 1, "step_staff": 1,
                                        "task_parent": 1, "task_staff": 1, "task_step": 1}
    assert summary["unresolved"] == 6 and summary["pending_approvals"] == 1
    assert set(summary["objects"]) == set(OBJECT_TYPES) and set(summary["links"]) == set(LINK_TYPES)


def test_object_ids_do_not_carry_paths_or_reference_values():
    view = build_view(_snapshot())
    ids = [oid for objects in view.objects.values() for oid in objects]
    assert not [oid for oid in ids if "outputs/" in oid or "10.5555" in oid or "/refs/" in oid]


def test_the_view_has_no_actions():
    """Read-only: no file, process or network access, and no function that approves, submits or writes."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"__future__", "hashlib", "re", "collections.abc", "dataclasses", "typing",
                        "evidence.claims",  # normalize_artifact_path: one spelling per path, as the model uses
                        "vocab.declare",  # the one declaration reader both models use; pure, no file read (#221)
                        "contract"}  # validate_research_result: staff declarations as the provenance model reads them
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"open", "exec", "eval", "__import__"}
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not [n for n in names if any(w in n for w in ("approve", "submit", "dispatch", "write", "save",
                                                          "cancel", "delete", "run"))]


def test_the_shadow_line_carries_the_view_counts(tmp_path):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", {"outputs/a.tsv": b"x"})
    hub = fake_hub(tmp_path, {"req_a": request_row("req_a", [("s1", "analyst")])},
                   {"task_a1": task_row("req_a", "task_a1", "s1", "analyst", wd, ["outputs/a.tsv"])},
                   decisions={"appr_9": {"approval": {"kind": "clarify", "request_id": "req_a"}, "approved": True}})
    objects = line_for(hub, "req_a")["objects"]
    assert objects["status"] == "ok" and objects["unresolved"] == 0
    assert objects["objects"]["Staff"] == 3 and objects["objects"]["Approval"] == 1
    assert objects["links"]["task_artifact"] == 1 and objects["links"]["approval_target"] == 1


def test_one_file_under_two_spellings_is_one_artifact():
    snap = _snapshot()
    snap["tasks"]["task_1"]["result"]["outputs"] = ["outputs\\a.tsv", "outputs/a.tsv", "./outputs/a.tsv"]
    snap["requests"]["req_o"]["plan"]["steps"][1]["input_refs"] = ["step:s1/outputs\\a.tsv"]
    snap["tasks"].pop("task_1w")  # one workspace reports s1, so the step reference resolves
    summary = summarize(build_view(snap))
    assert summary["objects"]["Artifact"] == 1 and summary["links"]["task_artifact"] == 1
    assert summary["links"]["step_input"] == 1 and "step_input" not in summary["unresolved_by"]


def _hpc_wait(child_kind: str | None = "step"):
    """task_w left HPC job 301 pending; task_w2 is its wake-up. Saving the step result cleared jobs_done."""
    tasks = {"task_w": task_row("req_h", "task_w", "s1", "analyst", "/w/task_w_analyst", [])}
    tasks["task_w"]["result"]["pending_jobs"] = ["301"]
    if child_kind:
        tasks["task_w2"] = {**task_row("req_h", "task_w2", "s1", "analyst", "/w/task_w_analyst", ["outputs/a.tsv"],
                                       kind=child_kind), "parent_task": "task_w"}
    return {"rid": "req_h", "requests": {"req_h": request_row("req_h", [("s1", "analyst")])}, "tasks": tasks,
            "agents": {"analyst": {"engine": "mock"}}, "jobs_done": {}, "approvals": []}


@pytest.mark.parametrize("child, job_state, task_state, pending", [
    ("step", "finished", "done", 0),       # woken: the CSO waited for the jobs before it dispatched the wake
    (None, "pending", "waiting", 1),       # never woken: still pending
    ("wrap_up", "pending", "waiting", 1),  # a wrap-up turn continues a task without waiting for its jobs
])
def test_jobs_of_a_woken_task_are_not_pending_after_the_checkpoint_is_cleared(child, job_state, task_state, pending):
    """#163 #174: the wake-up task is the durable record that the jobs ended."""
    view = build_view(_hpc_wait(child))
    assert view.objects["Job"]["job:task_w/301"]["state"] == job_state
    assert view.objects["Task"]["task:task_w"]["state"] == task_state
    assert summarize(view)["pending_jobs"] == pending


def test_a_recorded_final_job_state_is_kept():
    snap = _hpc_wait("step")
    snap["jobs_done"] = {"task_w": {"jobs": [{"job_id": "301", "state": "failed"}]}}
    assert build_view(snap).objects["Job"]["job:task_w/301"]["state"] == "failed"


def test_the_line_and_report_count_no_pending_job_after_a_wake(tmp_path):
    from labhq.research import semantics_shadow as shadow
    snap = _hpc_wait("step")
    hub = fake_hub(tmp_path, snap["requests"], snap["tasks"])   # the store keeps no jobs_done, as after a wake
    line = line_for(hub, "req_h")
    assert line["objects"]["status"] == "ok" and line["objects"]["pending_jobs"] == 0
    paths = shadow.ShadowPaths(tmp_path / "report")
    shadow.append_line(paths, {**line, "objects": {**line["objects"], "pending_jobs": 2}})
    shadow.append_line(paths, line)
    rep = shadow.build_report(paths)
    assert rep["broken_lines"] == 0 and rep["objects"]["pending_jobs"] == 2
    assert "대기 job 2" in shadow.render_report(rep)


# ---------------------------------------------------------------- typed output declarations (#221 todo 2)

import json  # noqa: E402

from labhq import vocab as output_vocab  # noqa: E402
from labhq.research import semantics as sem  # noqa: E402
from labhq.research.semantics_objects import type_artifacts  # noqa: E402
from labhq.research.semantics_shadow import ShadowConfig, read_rows, take_snapshot  # noqa: E402
from labhq.vocab import declare  # noqa: E402

V = output_vocab.current()


def _typed_snapshot(tmp_path, decl, *, outputs=("outputs/a.tsv", "outputs/b.md")):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", {o: b"x" for o in outputs})
    row = task_row("req_a", "task_a1", "s1", "analyst", wd, list(outputs))
    meta = {"output_types_vocab": V.sha256, "output_types": decl}
    row["payload"]["meta"].update(meta)
    row["result"]["output_types"] = declare.runner_records(list(outputs), meta, V)
    hub = fake_hub(tmp_path, {"req_a": request_row("req_a", [("s1", "analyst")])}, {"task_a1": row})
    snap = take_snapshot(hub, "req_a", ShadowConfig())
    read_rows(snap, lambda: None)
    return hub, snap


def test_artifacts_carry_the_key_and_apart_its_edam_id_with_basis(tmp_path):
    _, snap = _typed_snapshot(tmp_path, {"outputs/a.tsv": {"data_type": "raw_counts"}})
    arts = {a.get("data_type"): a for a in build_view(snap, vocab=V).objects["Artifact"].values()}
    assert arts["raw_counts"] == {"data_type": "raw_counts", "data_basis": "declared",
                                  "data_edam": V.edam_id("raw_counts") or "local", "format": "tsv",
                                  "format_basis": "inferred", "format_edam": V.edam_id("tsv") or "local"}
    assert arts["unknown"]["format"] == "markdown" and arts["unknown"]["data_edam"] == "unknown"
    assert summarize(build_view(snap, vocab=V))["artifact_types"] == {
        "data_type": {"declared": 1, "unknown": 1}, "format": {"inferred": 2}}
    assert summarize(build_view(snap))["artifact_types"] == {"data_type": {"unknown": 2}, "format": {"unknown": 2}}


def test_both_models_read_the_same_declarations(tmp_path):
    _, snap = _typed_snapshot(tmp_path, {"outputs/a.tsv": {"data_type": "raw_counts"},
                                         "outputs/b.md": {"data_type": "report"}})
    objects = {(a["data_type"], a["data_basis"]) for a in build_view(snap, vocab=V).objects["Artifact"].values()}
    records, _ = sem.records_from_rows(snap["requests"], snap["tasks"])
    p = sem.project(sem.load_model(), records, types_vocab=V)
    provenance = {(row["data_type"], "declared" if row["data_type"] != sem.UNKNOWN else "unknown")
                  for row in p.artifacts.values()}
    assert objects == provenance == {("raw_counts", "declared"), ("report", "declared")}


def test_the_line_s_object_type_counts_hold_no_key_or_id(tmp_path):
    hub, _ = _typed_snapshot(tmp_path, {"outputs/a.tsv": {"data_type": "raw_counts"}})
    objects = line_for(hub, "req_a")["objects"]
    assert objects["artifact_types"]["data_type"] == {"declared": 1, "unknown": 1}
    assert "raw_counts" not in json.dumps(objects) and "tsv" not in json.dumps(objects)


def _research_snapshot(tmp_path, refs):
    from labhq.research.contract import plan_sha256
    from tests.test_research_evidence import minimal_plan, result as research_result

    plan = minimal_plan()
    plan["steps"][0]["outputs"] = ["outputs/a.tsv", "outputs/b.tsv"]
    wd, _ = workspace(tmp_path, "task_r1", "analyst", {"outputs/a.tsv": b"x", "outputs/b.tsv": b"y"})
    row = task_row("req_r", "task_r1", "s1", "analyst", wd, ["outputs/a.tsv", "outputs/b.tsv"])
    row["payload"]["meta"].update({"output_types_vocab": V.sha256,
                                   "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}}})
    row["result"]["structured"] = research_result(plan_sha256=plan_sha256(plan), artifact_refs=refs)
    request = {**request_row("req_r", [("s1", "analyst")]), "plan": plan,
               "research_contract": {"plan_sha256": plan_sha256(plan)}, "intake": {"work_kind": "research"}}
    hub = fake_hub(tmp_path, {"req_r": request}, {"task_r1": row})
    snap = take_snapshot(hub, "req_r", ShadowConfig())
    read_rows(snap, lambda: None)
    return snap


def _both_models(snap):
    objects = {a["data_type"] for a in type_artifacts(build_view(snap), snap, V).objects["Artifact"].values()}
    records, invalid = sem.records_from_rows(snap["requests"], snap["tasks"])
    assert invalid == 0
    provenance = {row["data_type"] for row in sem.project(sem.load_model(), records, types_vocab=V).artifacts.values()}
    return objects, provenance


@pytest.mark.parametrize("reverse", [False, True])
def test_both_models_read_staff_declarations_and_their_conflicts_alike(tmp_path, reverse):
    refs = [{"artifact_id": "a1", "path": "outputs/a.tsv", "data_type": "normalized_counts"},
            {"artifact_id": "a2", "path": "outputs/b.tsv", "data_type": "table"},
            {"artifact_id": "a3", "path": "./outputs/b.tsv", "data_type": "de_table"}]
    objects, provenance = _both_models(_research_snapshot(tmp_path, refs[::-1] if reverse else refs))
    # a.tsv: the plan said raw_counts, the staff normalized_counts; b.tsv: two staff refs disagree
    assert objects == provenance == {sem.UNKNOWN}


def test_a_staff_only_declaration_counts_in_both_models(tmp_path):
    refs = [{"artifact_id": "a1", "path": "outputs/a.tsv"}, {"artifact_id": "a2", "path": "outputs/b.tsv",
                                                              "data_type": "table"}]
    objects, provenance = _both_models(_research_snapshot(tmp_path, refs))
    assert objects == provenance == {"raw_counts", "table"}
