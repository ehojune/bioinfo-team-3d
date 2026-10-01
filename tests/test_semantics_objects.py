"""Read-only object and link view (#150 B1): projected from rows, counts out, no actions."""
from __future__ import annotations

import ast
from pathlib import Path

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
    assert imported <= {"__future__", "hashlib", "re", "collections.abc", "dataclasses", "typing"}
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
