"""Shadow worker (#150 B1): after a request ends, one daemon thread computes and writes one local line.

The request itself, its prompts, plan, approvals, results, round record and web view are the same as with
semantics off, and off never imports the shadow code.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq.settings import DataZone, Settings
from tests.semantics_shadow_lab import lab_record, run_lab

ROOT = Path(__file__).resolve().parents[1]
TEXT = "CD276 세포유형 분석 [artifact] [revise] [question]"


async def test_shadow_writes_one_line_per_finished_request(tmp_path):
    lab = await run_lab(tmp_path, "shadow", [TEXT, "Partial study [max-turns]"], project_id="shadowproj")
    hub, (done, failed) = lab["hub"], lab["rids"]
    assert hub.requests[done]["status"] == "done" and hub.requests[failed]["status"] == "failed"
    lines = [line for line in lab["lines"] if line["type"] == "request"]
    assert [(line["request_id"], line["status"]) for line in lines] == [(done, "done"), (failed, "failed")]
    for line in lines:
        assert line["provenance"]["status"] == "ok" and line["objects"]["status"] == "ok"
        assert line["epoch"] == 1 and line["lane"] == "general" and line["project"] and line["ms"] >= 0
    workers = [t for t in threading.enumerate() if t.name == hub.semantics_shadow.thread_name]
    assert len(workers) <= 1 and all(t.daemon for t in workers)


@pytest.mark.parametrize("value", ["shadow", {"mode": "shadow", "timeout_s": 2}, {"mode": "advisory"}, "bogus"],
                         ids=["shadow", "shadow-mapping", "held-mode", "typo"])
async def test_the_lab_records_the_same_with_semantics_off_or_on(tmp_path, value):
    off = await run_lab(tmp_path / "off", None, [TEXT])
    on = await run_lab(tmp_path / "on", value, [TEXT])
    assert lab_record(on, tmp_path / "on") == lab_record(off, tmp_path / "off")
    assert not (tmp_path / "off" / "state" / "semantics").exists()
    written = [line for line in on["lines"] if line["type"] == "request"]
    assert len(written) == (1 if value in ("shadow", {"mode": "shadow", "timeout_s": 2}) else 0)


async def test_the_lab_records_the_same_across_a_restart_with_shadow(tmp_path):
    """A gateway restarted with shadow on reads the requests an off gateway finished, and vice versa."""
    from labhq.gateway.server import Hub
    off = await run_lab(tmp_path / "lab", None, [TEXT])
    before = lab_record(off, tmp_path / "lab")
    restarted = Hub(off["settings"].model_copy(update={"semantics": "shadow"}))
    assert restarted.semantics_shadow is not None
    assert lab_record({"hub": restarted, "rids": off["rids"]}, tmp_path / "lab") == before


OFF_PROCESS = """
import asyncio, json, sys, time
from labhq.settings import Settings
from labhq.gateway.server import Hub

async def main():
    s = Settings()
    s.gateway.state_dir = sys.argv[1]
    s.semantics = json.loads(sys.argv[2])
    hub = Hub(s)
    hub.requests["req_off1"] = {"id": "req_off1", "status": "done", "text": "x", "mode": "orchestrate",
                                "created_at": time.time()}
    hub.commit_terminal("req_off1", "request.completed", {"ok": True})
    await asyncio.sleep(0.05)
    print(json.dumps([hub.semantics_shadow is None, sorted(m for m in sys.modules if "semantics" in m)]))

asyncio.run(main())
"""


@pytest.mark.parametrize("value", [None, False, "off", {"mode": False}, {"mode": "off"},
                                   {"mode": "off", "timeout_s": 2}])
def test_off_never_loads_the_shadow_or_the_model(tmp_path, value):
    done = subprocess.run([sys.executable, "-c", OFF_PROCESS, str(tmp_path / "state"), json.dumps(value)],
                          capture_output=True, text=True, cwd=ROOT, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"})
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [True, []]
    assert not (tmp_path / "state" / "semantics").exists()


# ---------------------------------------------------------------- fail-open, unit level

def _hub(tmp_path, semantics="shadow"):
    from labhq.gateway.server import Hub
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.policy.data_zones = [DataZone(path=str(tmp_path / "runs"), level="internal")]
    s.semantics = semantics
    return Hub(s)


def _finish(hub, rid="req_unit1", status="done", results=None):
    hub.requests[rid] = {"id": rid, "status": status, "text": "unit request", "mode": "orchestrate",
                         "created_at": 1790000000.0, "results": results or {}, "plan": {"steps": []}}
    hub.commit_terminal(rid, "request.completed" if status == "done" else "request.failed", {"ok": status == "done"})


def _as_recorded(hub, rid, root):
    from labhq.integrations.rounds import build_record
    from tests.semantics_shadow_lab import _normalize
    roots = [str(root), str(root.resolve())]
    events = [{"type": e["type"], "data": e["data"]} for e in hub.store.events_since(0) if e.get("request_id") == rid]
    return _normalize({"request": hub.requests[rid], "round": build_record(hub, rid), "events": events,
                       "stored": hub.store.get("request", rid)}, roots)


@pytest.mark.parametrize("stage", ["snapshot", "objects", "provenance", "write", "queue"])
async def test_a_failure_in_any_stage_leaves_the_request_as_off(tmp_path, monkeypatch, caplog, stage):
    from labhq.research import semantics_shadow as shadow
    off, hub = _hub(tmp_path / "off", None), _hub(tmp_path / "on")

    def boom(*args, **kwargs):
        raise (OSError if stage == "write" else RuntimeError)("injected secret-path /data/cohort")

    target = {"snapshot": lambda: (shadow, "take_snapshot"), "objects": lambda: (shadow, "build_view"),
              "provenance": lambda: (shadow.sem, "records_from_rows"), "write": lambda: (shadow, "append_line")}
    if stage == "queue":
        monkeypatch.setattr(hub.semantics_shadow, "queue", SimpleNamespace(put_nowait=boom, empty=lambda: True))
    else:
        monkeypatch.setattr(*target[stage](), boom)
    _finish(off)
    _finish(hub)
    assert hub.semantics_shadow.drain(10)
    assert _as_recorded(hub, "req_unit1", tmp_path / "on") == _as_recorded(off, "req_unit1", tmp_path / "off")
    assert all("/data/cohort" not in r.getMessage() for r in caplog.records)  # class names only
    log_file = tmp_path / "on" / "state" / "semantics" / "shadow.jsonl"
    lines = [json.loads(x) for x in log_file.read_text(encoding="utf-8").splitlines()] if log_file.exists() else []
    if stage in ("objects", "provenance"):
        model, other = ("objects", "provenance") if stage == "objects" else ("provenance", "objects")
        assert lines[-1][model] == {"status": "error", "error_kind": "RuntimeError", "ms": lines[-1][model]["ms"]}
        assert lines[-1][other]["status"] == "ok"
    else:
        assert not [line for line in lines if line.get("type") == "request"]
    assert hub.semantics_shadow.counts["failures"] == 1


async def test_a_cancelled_step_still_gets_a_line(tmp_path):
    hub = _hub(tmp_path)
    _finish(hub, status="failed", results={"s1": {"task_id": "task_c1", "agent_id": "analyst", "ok": False,
                                                 "error": "cancelled", "status": "failed"}})
    assert hub.semantics_shadow.drain(10)
    line = json.loads((tmp_path / "state" / "semantics" / "shadow.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert (line["request_id"], line["status"]) == ("req_unit1", "failed")


async def test_the_event_loop_never_waits_for_the_worker(tmp_path, monkeypatch):
    from labhq.research import semantics_shadow as shadow
    hub = _hub(tmp_path)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    real = shadow.compute_line

    def slow(*args, **kwargs):
        started.set()
        try:
            release.wait(10)
            return real(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(shadow, "compute_line", slow)
    _finish(hub, rid="req_burst0")
    assert started.wait(5), "shadow worker did not start"
    safety_release = threading.Timer(10, release.set)
    safety_release.daemon = True
    safety_release.start()
    try:
        _finish(hub, rid="req_burst1")
        _finish(hub, rid="req_burst2")
        assert not release.is_set(), "event-loop calls waited for the blocked worker"
        assert not finished.is_set(), "worker finished before the event-loop calls returned"
    finally:
        safety_release.cancel()
        release.set()
    assert hub.semantics_shadow.drain(10)
    assert hub.semantics_shadow.counts["busy"] == 1  # one running, one queued, the third skipped
    assert len([t for t in threading.enumerate() if t.name == hub.semantics_shadow.thread_name]) == 1


async def test_lines_carry_ids_kinds_hashes_and_counts_only(tmp_path):
    marker = "zz-secret-marker-2741"
    lab = await run_lab(tmp_path, "shadow", [f"{marker} 분석 [artifact]"], project_id="shadowproj",
                        references=[{"kind": "url", "value": f"https://example.org/{marker}"}])
    assert lab["lines"]
    for path in (tmp_path / "state" / "semantics").iterdir():
        text = path.read_text(encoding="utf-8")
        assert marker not in text and "shadowproj" not in text and "outputs/" not in text, path.name
        assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text


def _rows_hub(tmp_path):
    from tests.semantics_shadow_lab import task_row
    hub = _hub(tmp_path)
    for rid, project in (("req_hist1", None), ("req_other", "elsewhere")):
        hub.requests[rid] = {"id": rid, "status": "done", "project_id": project, "created_at": 1.0}
    rows = {"task_own1": "req_unit1", "task_own2": "req_unit1", "task_h1": "req_hist1", "task_h2": "req_hist1",
            "task_x1": "req_other", "task_x2": "req_other", "task_x3": "req_other"}
    for tid, rid in rows.items():
        hub.store.put("task", tid, task_row(rid, tid, "s1", "analyst", None, []))
    hub.store.put("approval_decision", "appr_own", {"approval": {"kind": "clarify", "request_id": "req_unit1"},
                                                     "approved": True})
    hub.store.put("jobs_done", "task_own1", {"jobs": [{"job_id": "7", "state": "completed"}]})
    hub.requests["req_unit1"] = {"id": "req_unit1", "status": "done", "text": "unit request", "created_at": 2.0}
    return hub


class _NoTable:
    def __getattr__(self, name):
        raise AssertionError(f"the event loop touched the database ({name})")


async def test_the_event_loop_reads_no_table_and_the_worker_reads_only_what_it_needs(tmp_path, monkeypatch):
    """The loop copies requests from memory only. The worker reads this request's tasks and its project's
    history by request id, through its own read-only connection, without prompts or answer text."""
    from labhq.research import semantics_shadow as shadow
    hub = _rows_hub(tmp_path)
    real_db = hub.store.db
    monkeypatch.setattr(hub.store, "db", _NoTable())
    monkeypatch.setattr(hub.store, "all", lambda kind: _NoTable().all)
    snap = shadow.take_snapshot(hub, "req_unit1", shadow.ShadowConfig())
    monkeypatch.undo()
    assert hub.store.db is real_db and "tasks" not in snap
    shadow.read_rows(snap, lambda: None)
    assert sorted(snap["tasks"]) == ["task_h1", "task_h2", "task_own1", "task_own2"]
    assert [a["id"] for a in snap["approvals"]] == ["appr_own"]
    assert snap["jobs_done"] == {"task_own1": {"jobs": [{"job_id": "7", "state": "completed"}]}}
    assert "secret prompt text" not in json.dumps(snap) and "answer body" not in json.dumps(snap)
    monkeypatch.setattr(shadow, "MAX_TASK_ROWS", 3)
    capped = shadow.take_snapshot(hub, "req_unit1", shadow.ShadowConfig())
    shadow.read_rows(capped, lambda: None)
    assert capped["tasks_truncated"] is True and {"task_own1", "task_own2"} <= set(capped["tasks"])
    assert len(capped["tasks"]) == 3


async def test_a_slow_row_query_is_interrupted_at_the_time_cap(tmp_path):
    from labhq.research import semantics_shadow as shadow
    from tests.semantics_shadow_lab import task_row
    hub = _rows_hub(tmp_path)
    with hub.store.db:
        hub.store.db.executemany("INSERT OR REPLACE INTO state VALUES ('task', ?, ?)",
                                 [(f"task_bulk{i}", json.dumps(task_row("req_other", f"task_bulk{i}", "s1", "x",
                                                                        None, [])))
                                  for i in range(3000)])
    snap = shadow.take_snapshot(hub, "req_unit1", shadow.ShadowConfig())

    def past_the_cap():
        raise shadow.ShadowTimeout("time cap")

    with pytest.raises(shadow.ShadowTimeout):
        shadow.read_rows(snap, past_the_cap)
