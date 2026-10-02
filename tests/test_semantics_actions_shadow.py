"""Action layer A1 (#149 결정 13) wired into the B1 shadow: off is as before, on only adds counts, and no action
is ever taken through it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from labhq.settings import DataZone, Settings
from tests.semantics_shadow_lab import lab_record, run_lab, task_row

ROOT = Path(__file__).resolve().parents[1]
ON = {"mode": "shadow", "actions": "shadow"}
TEXT = "CD276 세포유형 분석 [artifact] [recruit] [question]"  # one approval: two would race in the round order


def _settings(tmp_path, semantics=ON):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.policy.data_zones = [DataZone(path=str(tmp_path / "runs"), level="internal")]
    s.semantics = semantics
    return s


def _hub(tmp_path, semantics=ON):
    from labhq.gateway.server import Hub
    return Hub(_settings(tmp_path, semantics))


def _lines(tmp_path):
    path = tmp_path / "state" / "semantics" / "shadow.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _finish(hub, rid="req_unit1", **extra):
    hub.requests[rid] = {"id": rid, "status": "done", "text": "unit request", "mode": "orchestrate",
                         "created_at": 1790000000.0, "finished_at": 1790000100.0, "results": {},
                         "plan": {"steps": []}, **extra}
    hub.commit_terminal(rid, "request.completed", {"ok": True})


# ---------------------------------------------------------------- off is as before

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
                                "created_at": time.time(), "followups": [{"id": "fu_1", "status": "done"}]}
    hub.commit_terminal("req_off1", "request.completed", {"ok": True})
    hub.semantics_shadow.after_followup("req_off1", "fu_1", "ended", "done")
    assert hub.semantics_shadow.drain(20)
    lines = [json.loads(x) for x in open(sys.argv[1] + "/semantics/shadow.jsonl", encoding="utf-8")]
    print(json.dumps([sorted(m for m in sys.modules if "semantics_actions" in m), [sorted(l) for l in lines]]))

asyncio.run(main())
"""


@pytest.mark.parametrize("value", [{"mode": "shadow"}, {"mode": "shadow", "actions": "off"},
                                   {"mode": "shadow", "actions": False}, {"mode": "shadow", "actions": "confirm"},
                                   {"mode": "shadow", "actions": "bogus"}],
                         ids=["missing", "off", "false", "held", "typo"])
def test_actions_off_never_loads_the_module_and_writes_the_b1_line_only(tmp_path, value):
    done = subprocess.run([sys.executable, "-c", OFF_PROCESS, (tmp_path / "state").as_posix(), json.dumps(value)],
                          capture_output=True, text=True, cwd=ROOT, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"})
    assert done.returncode == 0, done.stderr[-2000:]
    modules, lines = json.loads(done.stdout.strip().splitlines()[-1])
    assert modules == []
    assert len(lines) == 1 and "actions" not in lines[0] and "objects" in lines[0]


def _fixed_clock(monkeypatch):
    from labhq.research import semantics_shadow as shadow
    monkeypatch.setattr(shadow.time, "time", lambda: 1790000500.0)
    monkeypatch.setattr(shadow.time, "perf_counter", lambda: 42.0)


def _line(hub, cfg):
    from labhq.research import semantics_shadow as shadow
    snap = shadow.take_snapshot(hub, "req_unit1", cfg)
    return snap, shadow.compute_line(snap, {}, lambda: None, epoch=1)


async def test_actions_on_adds_one_field_and_changes_no_b1_field(tmp_path, monkeypatch):
    """The same rows under a fixed clock: the B1 line with actions on, minus its actions field, is byte for byte
    the line with actions off, and so is the snapshot minus its actions inputs."""
    from labhq.research import semantics_shadow as shadow
    hub = _hub(tmp_path, {"mode": "shadow"})
    hub.requests["req_unit1"] = {"id": "req_unit1", "status": "done", "text": "unit request", "created_at": 2.0,
                                 "finished_at": 3.0, "plan": {"steps": [], "recruit": [{"paper": "doi:10/x"}]}}
    for tid in ("task_own1", "task_own2"):
        hub.store.put("task", tid, {**task_row("req_unit1", tid, "s1", "analyst", None, []), "dispatched_at": 2.5})
    hub.store.put("approval_decision", "appr_own", {"approval": {"kind": "clarify", "request_id": "req_unit1",
                                                                 "created_at": 2.1, "timeout_s": 60},
                                                     "approved": True, "decided_at": 2.2})
    _fixed_clock(monkeypatch)
    off_snap, off = _line(hub, shadow.ShadowConfig())
    on_snap, on = _line(hub, shadow.ShadowConfig(actions=True))
    assert on["actions"]["status"] == "ok" and on["actions"]["past"]["approval.decide"]["taken"] == 1
    assert json.dumps({k: v for k, v in on.items() if k != "actions"}, sort_keys=True) == json.dumps(off, sort_keys=True)
    assert {k: v for k, v in on_snap.items() if k != "actions"} == off_snap
    assert shadow.boundary_problems(on, shadow.sensitive_strings(on_snap)) == []


@pytest.mark.parametrize("value", [ON, {"mode": "shadow", "actions": "confirm"}], ids=["shadow", "held"])
async def test_the_lab_records_the_same_with_actions_on(tmp_path, value):
    """Prompts, plans, approvals, results, rounds and the web view are the same as with semantics off; the CSO's
    recruit suggestion and the approvals stay where they were, and no recruit or contract message is sent."""
    off = await run_lab(tmp_path / "off", None, [TEXT])
    sent = []

    def spy(hub):
        real = hub.send_runner

        async def send_runner(runner_id, msg):
            sent.append(msg.get("type"))
            return await real(runner_id, msg)
        hub.send_runner = send_runner

    on = await run_lab(tmp_path / "on", value, [TEXT], before=spy)
    assert lab_record(on, tmp_path / "on") == lab_record(off, tmp_path / "off")
    assert not {"recruit.start", "contract.update", "task.cancel"} & set(sent)
    lines = [line for line in on["lines"] if line["type"] == "request"]
    assert len(lines) == 1 and not [line for line in on["lines"] if line["type"] == "auto_off"]
    if value == ON:
        section = lines[0]["actions"]
        assert section["status"] == "ok" and section["exec"]["hpc.submit"] == "refused_p3"
        assert section["past"]["recruit.start"]["windows"] == 1
        assert section["past"]["approval.decide"]["windows"] >= 1
    else:
        assert "actions" not in lines[0]


# ---------------------------------------------------------------- follow-up observations through the REST path

def _client(tmp_path, semantics=ON, engine="claude_code"):
    from labhq.gateway.server import create_app
    s = _settings(tmp_path, semantics)
    app = create_app(s)
    hub = app.state.hub
    hub.agents = {"cso": {"id": "cso", "engine": engine}}
    hub.requests["req_f1"] = {"id": "req_f1", "text": "t", "mode": "orchestrate", "status": "done",
                              "finished_at": 1.0, "followups": []}
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    return TestClient(app), hub, auth


def _wait(pred, timeout=10.0):
    end = time.time() + timeout
    while not pred():
        assert time.time() < end, "timed out"
        time.sleep(0.02)


def test_an_asked_and_ended_followup_is_observed_once_each_and_changes_nothing(tmp_path):
    def run(base, semantics):
        client, hub, auth = _client(base, semantics)
        with client:
            response = client.post("/api/requests/req_f1/followup", json={"text": "zz-fu-marker-1 왜?"}, headers=auth)
            assert response.status_code == 200
            _wait(lambda: hub.requests["req_f1"]["followups"][0]["status"] != "running")
            if hub.semantics_shadow is not None:
                assert hub.semantics_shadow.drain(10)
        entry = dict(hub.requests["req_f1"]["followups"][0])
        return {k: v for k, v in entry.items() if k not in ("id", "asked_at", "answered_at", "task_id")}, hub

    off_entry, _ = run(tmp_path / "off", None)
    on_entry, hub = run(tmp_path / "on", ON)
    assert on_entry == off_entry and on_entry["status"] == "failed"  # no runner hosts the CSO
    lines = [line for line in _lines(tmp_path / "on") if line["type"] == "followup"]
    assert [(line["phase"], line.get("outcome")) for line in lines] == [("asked", None), ("ended", "failed")]
    assert lines[0]["key"] == lines[1]["key"] and lines[0]["mismatch"] == {"taken_while_blocked": False}
    text = (tmp_path / "on" / "state" / "semantics" / "shadow.jsonl").read_text(encoding="utf-8")
    assert "zz-fu-marker-1" not in text and hub.requests["req_f1"]["followups"][0]["id"] not in text


def test_a_refused_followup_is_observed_and_still_refused(tmp_path):
    client, hub, auth = _client(tmp_path, engine="cli")
    with client:
        response = client.post("/api/requests/req_f1/followup", json={"text": "q"}, headers=auth)
        assert response.status_code == 409 and not hub.requests["req_f1"]["followups"]
        assert hub.semantics_shadow.drain(10)
    (line,) = [line for line in _lines(tmp_path) if line["type"] == "followup"]
    assert line["phase"] == "refused" and line["mismatch"] == {"refused_while_open": False}
    assert line["conditions"]["responder_read_only"] is False and line["key"] is None


async def test_a_read_only_refusal_after_the_followup_was_taken_names_its_reason(tmp_path):
    from labhq.orchestrator.cso import Orchestrator
    hub = _hub(tmp_path)
    hub.agents = {"cso": {"id": "cso", "engine": "cli"}}  # the engine changed after the server took it
    hub.requests["req_f1"] = {"id": "req_f1", "text": "t", "mode": "orchestrate", "status": "done",
                              "followups": [{"id": "fu_1", "text": "q", "agent_id": "cso", "status": "running",
                                             "asked_at": 1.0}]}
    await Orchestrator(hub).run_followup("req_f1", "fu_1")
    assert hub.semantics_shadow.drain(10)
    (line,) = [line for line in _lines(tmp_path) if line["type"] == "followup"]
    assert (line["phase"], line["outcome"], line["has_task"]) == ("ended", "refused_read_only", False)
    assert line["mismatch"] == {"refused_while_open": False}


async def test_observing_a_followup_with_actions_off_reads_nothing(tmp_path, monkeypatch):
    from labhq.research import semantics_shadow as shadow
    hub = _hub(tmp_path, {"mode": "shadow"})
    monkeypatch.setattr(hub.semantics_shadow, "queue", SimpleNamespace(put_nowait=None, empty=lambda: True))
    monkeypatch.setattr(shadow.ShadowService, "refresh", lambda self: (_ for _ in ()).throw(AssertionError))
    hub.semantics_shadow.after_followup("req_none", "fu_1", "ended", "done")  # no request read, nothing queued
    service = hub.semantics_shadow
    assert (service.pending, service.counts["failures"], len(service.action_backlog)) == (0, 0, 0)


# ---------------------------------------------------------------- failures stay inside the shadow

async def test_a_failing_action_model_leaves_the_request_and_the_b1_models_as_they_were(tmp_path, monkeypatch, caplog):
    from labhq.research import semantics_actions as acts
    from tests.test_semantics_shadow_worker import _as_recorded

    def boom(*args, **kwargs):
        raise RuntimeError("injected /data/cohort zz-secret")

    off, hub = _hub(tmp_path / "off", None), _hub(tmp_path / "on")
    monkeypatch.setattr(acts, "evaluate", boom)
    _finish(off)
    _finish(hub)
    assert hub.semantics_shadow.drain(10)
    assert _as_recorded(hub, "req_unit1", tmp_path / "on") == _as_recorded(off, "req_unit1", tmp_path / "off")
    (line,) = [line for line in _lines(tmp_path / "on") if line["type"] == "request"]
    assert line["actions"]["status"] == "error" and line["actions"]["error_kind"] == "RuntimeError"
    assert line["objects"]["status"] == "ok" and line["provenance"]["status"] == "ok"
    assert hub.semantics_shadow.counts["failures"] == 1  # counted in the B1 breaker window
    assert all("zz-secret" not in r.getMessage() for r in caplog.records)


async def test_three_failing_action_models_turn_the_shadow_off(tmp_path, monkeypatch):
    from labhq.research import semantics_actions as acts
    monkeypatch.setattr(acts, "evaluate", lambda *a, **k: 1 / 0)
    hub = _hub(tmp_path)
    for i in range(3):
        _finish(hub, rid=f"req_fail{i}")
        assert hub.semantics_shadow.drain(10)
    assert hub.semantics_shadow.latched == "consecutive_failures"


def test_a_failing_followup_observation_never_reaches_the_followup(tmp_path, monkeypatch):
    from labhq.research import semantics_actions as acts
    monkeypatch.setattr(acts, "followup_inputs", lambda *a, **k: 1 / 0)
    client, hub, auth = _client(tmp_path)
    with client:
        response = client.post("/api/requests/req_f1/followup", json={"text": "q"}, headers=auth)
        assert response.status_code == 200 and hub.requests["req_f1"]["followups"][0]["status"] in ("running",
                                                                                                    "failed")
    assert hub.semantics_shadow.counts["failures"] >= 1


async def test_a_full_queue_keeps_followup_observations_behind_it_without_the_b1_busy_count(tmp_path, monkeypatch):
    import queue
    from labhq.research import semantics_shadow as shadow
    hub = _hub(tmp_path)
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done", "followups": [{"id": "fu_1", "status": "done"}]}
    service = hub.semantics_shadow

    def full(job):
        raise queue.Full

    real = service.queue
    monkeypatch.setattr(service, "queue", SimpleNamespace(put_nowait=full, empty=lambda: True))
    for _ in range(shadow.ACTION_BACKLOG + 5):
        service.after_followup("req_f1", "fu_1", "ended", "done")
    assert len(service.action_backlog) == shadow.ACTION_BACKLOG and service.action_skipped == 5
    assert service.busy == service.busy_skipped == 0 and service.latched is None
    monkeypatch.setattr(service, "queue", real)
    service.work_backlog(real)  # what the worker does after the job that held the queue
    service.pending = 0
    lines = [line for line in _lines(tmp_path) if line["type"] == "followup"]
    assert len(lines) == shadow.ACTION_BACKLOG and len({line["key"] for line in lines}) == 1
    assert sum(line["busy_skipped"] for line in lines) == 5  # the drops are written, with no later follow-up
    assert acts_report(lines)["followup"]["ended"]["done"] == 1  # one follow-up, however often it was seen


async def test_a_waiting_followup_observation_never_takes_a_request_jobs_place(tmp_path, monkeypatch):
    """A follow-up observation waiting in the queue of one while the worker is busy: the request that ends next is
    still queued, not counted busy, and both lines are written."""
    from labhq.research import semantics_shadow as shadow
    hub = _hub(tmp_path)
    service = hub.semantics_shadow
    monkeypatch.setattr(service, "ensure_thread", lambda: None)  # the worker is busy elsewhere: nothing takes a job
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done", "followups": [{"id": "fu_1", "status": "done"}]}
    service.after_followup("req_f1", "fu_1", "ended", "done")
    assert service.queue.full() and not service.action_backlog
    _finish(hub, "req_c1")
    assert service.busy == service.busy_skipped == 0 and service.counts["busy"] == 0
    assert service.queue.queue[0][2]["rid"] == "req_c1" and len(service.action_backlog) == 1
    _finish(hub, "req_c2")  # a request job is waiting: busy exactly as B1 without actions
    assert service.busy == service.busy_skipped == 1 and len(service.action_backlog) == 1
    shadow.ShadowService.ensure_thread(service)
    assert service.drain(20)
    lines = _lines(tmp_path)
    assert [l["request_id"] for l in lines if l["type"] == "request"] == ["req_c1"]
    assert [l["phase"] for l in lines if l["type"] == "followup"] == ["ended"]


async def test_the_backlog_never_overtakes_a_queued_request_job(tmp_path):
    hub = _hub(tmp_path)
    service = hub.semantics_shadow
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done", "followups": [{"id": "fu_1", "status": "done"}]}
    with service.lock:
        service.queue.put_nowait((service.gen, service.queue, {"job": "placeholder"}))  # a request job waiting
    service.after_followup("req_f1", "fu_1", "ended", "done")
    service.after_followup("req_f1", "fu_1", "ended", "done")
    assert len(service.action_backlog) == 2
    service.work_backlog(service.queue)  # the queue is not empty: the request job goes first
    assert len(service.action_backlog) == 2 and not _lines(tmp_path)
    service.queue.get_nowait()
    service.work_backlog(service.queue)
    assert not service.action_backlog and len(_lines(tmp_path)) == 2


async def test_a_new_epoch_discards_the_old_followup_backlog_and_starts_the_new_one(tmp_path, monkeypatch):
    """#258: work owned by the closed epoch must not keep pending nonzero or stop a new worker."""
    from labhq.research import semantics_shadow as shadow

    hub = _hub(tmp_path)
    service = hub.semantics_shadow
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done",
                              "followups": [{"id": "fu_1", "status": "done"}]}
    monkeypatch.setattr(service, "ensure_thread", lambda: None)
    service.queue.put_nowait((service.gen, service.queue, {"job": "placeholder"}))
    service.after_followup("req_f1", "fu_1", "ended", "done")
    service.action_skipped = 2
    service.current = (service.gen, time.monotonic())  # the job holding the queue looks stuck
    assert (len(service.action_backlog), service.pending) == (1, 1)

    service.new_epoch(2)
    assert (len(service.action_backlog), service.action_skipped, service.pending) == (0, 0, 0)
    assert service.counts["discarded"] == 1
    service.after_followup("req_f1", "fu_1", "ended", "done")
    shadow.ShadowService.ensure_thread(service)
    assert service.drain(10)
    assert [(line["phase"], line["epoch"], line["busy_skipped"]) for line in _lines(tmp_path)] == [("ended", 2, 0)]


async def test_the_closed_epochs_worker_never_takes_the_new_epochs_pending_count(tmp_path, monkeypatch):
    """#258: the old thread returns after a new epoch replaced its queue; drain still waits for the new work."""
    from labhq.research import semantics_shadow as shadow

    hub = _hub(tmp_path)
    service = hub.semantics_shadow
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done",
                              "followups": [{"id": "fu_1", "status": "done"}]}
    old = service.queue
    old.put_nowait((service.gen, old, {"job": "placeholder"}))
    service.pending = 1
    service.current = (service.gen, time.monotonic())
    service.new_epoch(2)
    assert service.queue is not old and service.pending == 0
    monkeypatch.setattr(service, "ensure_thread", lambda: None)  # the new epoch's worker has not started yet
    service.after_followup("req_f1", "fu_1", "ended", "done")
    assert service.pending == 1
    service.run(old)  # the old thread: its closed-epoch job is dropped and it ends
    assert service.pending == 1 and not service.drain(0.1)
    shadow.ShadowService.ensure_thread(service)
    assert service.drain(10)
    assert [(line["phase"], line["epoch"]) for line in _lines(tmp_path)] == [("ended", 2)]


def acts_report(lines):
    from labhq.research import semantics_actions as acts
    return acts.report(lines, setting="shadow", on=True)


async def test_a_latched_shadow_observes_no_followup(tmp_path):
    hub = _hub(tmp_path)
    hub.semantics_shadow.trip("wrong_identity")
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done", "followups": [{"id": "fu_1", "status": "done"}]}
    hub.semantics_shadow.after_followup("req_f1", "fu_1", "ended", "done")
    assert hub.semantics_shadow.drain(10)
    assert not [line for line in _lines(tmp_path) if line["type"] == "followup"]


async def test_a_free_string_in_the_actions_section_turns_the_shadow_off(tmp_path, monkeypatch):
    from labhq.research import semantics_actions as acts
    real = acts.evaluate
    monkeypatch.setattr(acts, "evaluate", lambda *a, **k: {**real(*a, **k), "note": "plain_token"})
    hub = _hub(tmp_path)
    _finish(hub)
    assert hub.semantics_shadow.drain(10)
    assert hub.semantics_shadow.latched == "info_boundary"
    assert not [line for line in _lines(tmp_path) if line["type"] == "request"]


# ---------------------------------------------------------------- information boundary

async def test_texts_of_followups_approvals_and_proposals_never_reach_a_line_log_or_report(tmp_path, caplog, capsys):
    from labhq.research import semantics_shadow as shadow
    marker = "zz-canary-6620"
    hub = _hub(tmp_path)
    hub.agents = {"cso": {"id": "cso", "engine": "claude_code"}, "recruiter": {"id": "recruiter", "engine": "mock"}}
    hub.store.put("approval_decision", "appr_c1", {"approval": {"kind": "budget", "request_id": "req_unit1",
                                                                "summary": f"{marker} budget", "created_at": 1.0,
                                                                "timeout_s": 60, "detail": {"why": marker}},
                                                   "approved": True, "note": f"{marker} note", "decided_at": 2.0})
    _finish(hub, text=f"{marker} request", followups=[{"id": "fu_c1", "text": f"{marker} question",
                                                       "answer": f"{marker} answer", "status": "done",
                                                       "agent_id": "cso", "task_id": "task_c1"}],
            plan={"steps": [], "recruit": [{"paper": f"https://doi.org/{marker}", "focus": marker}]})
    hub.semantics_shadow.after_followup("req_unit1", "fu_c1", "ended", "done")
    assert hub.semantics_shadow.drain(10)
    lines = _lines(tmp_path)
    assert {line["type"] for line in lines} == {"request", "followup"} and lines[0]["actions"]["status"] == "ok"
    assert shadow.run_cli(SimpleNamespace(semantics_cmd="report", today=None, json=True), hub.s) == 0
    assert shadow.run_cli(SimpleNamespace(semantics_cmd="report", today=None, json=False), hub.s) == 0
    out = capsys.readouterr().out
    assert "액션 층 그림자 A1" in out and '"actions"' in out
    for path in (tmp_path / "state" / "semantics").iterdir():
        assert marker not in path.read_text(encoding="utf-8"), path.name
    assert marker not in out and all(marker not in r.getMessage() for r in caplog.records)


async def test_a_line_carrying_a_followup_text_is_refused(tmp_path, monkeypatch):
    """The boundary check knows the follow-up texts, so a model that leaked one would turn the shadow off."""
    from labhq.research import semantics_actions as acts
    marker = "zzleaked0042"
    real = acts.followup_line
    monkeypatch.setattr(acts, "followup_line", lambda *a, **k: {**real(*a, **k), "request_id": marker})
    hub = _hub(tmp_path)
    hub.agents = {"cso": {"id": "cso", "engine": "claude_code"}}
    hub.requests["req_f1"] = {"id": "req_f1", "status": "done", "followups": [{"id": "fu_1", "text": marker,
                                                                             "status": "done"}]}
    hub.semantics_shadow.after_followup("req_f1", "fu_1", "ended", "done")
    assert hub.semantics_shadow.drain(10)
    assert hub.semantics_shadow.latched == "info_boundary"
    assert marker not in (tmp_path / "state" / "semantics" / "shadow.jsonl").read_text(encoding="utf-8")


# ---------------------------------------------------------------- report and removal

def test_the_report_is_unchanged_with_actions_off_and_no_action_records(tmp_path, capsys):
    from labhq.research import semantics_shadow as shadow
    s = _settings(tmp_path, {"mode": "shadow"})
    paths = shadow.ShadowPaths(shadow.shadow_root(s))
    shadow.append_line(paths, {"v": 1, "type": "request", "ts": 1.0, "request_id": "req_r1"})
    rep = shadow.build_report(paths, None, shadow.configured(s))
    assert shadow.run_cli(SimpleNamespace(semantics_cmd="report", today=None, json=True), s) == 0
    printed = json.loads(capsys.readouterr().out)
    assert "actions" not in printed and printed.keys() == rep.keys()
    assert shadow.render_report(rep) == shadow.render_report(json.loads(json.dumps(rep)))


async def test_removing_the_action_layer_only_leaves_the_b1_shadow_working(tmp_path):
    from scripts import semantics_shadow_remove as removal
    lab = await run_lab(tmp_path / "lab", ON, ["CD276 세포유형 분석 [artifact]"])
    assert lab["lines"] and "actions" in lab["lines"][-1]
    hub = lab["hub"]
    hub.requests["req_inflight1"] = {"id": "req_inflight1", "status": "running", "text": "x", "mode": "orchestrate",
                                     "created_at": 1.0}
    hub.save_request("req_inflight1")
    summary = removal.check(tmp_path / "lab" / "state", only="actions")
    assert summary["files"] == removal.ACTIONS_OWNED
    assert summary["hook_lines"] == 8 + 24 and summary["blocks"] == 2  # server 4, cso 4, semantics_shadow 24
    assert summary["state"] == {"done": 1, "interrupted": 1, "resume_approvals": 1, "b1_line": "ok",
                                "actions_field": False}
    assert " passed" in summary["pytest"] and "failed" not in summary["pytest"]
