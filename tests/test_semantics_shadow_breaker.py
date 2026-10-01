"""Automatic off (#150 B1): the shadow latches itself off in the process, without a redeploy, and stays off
across a restart until `labhq semantics enable` opens a new epoch."""
from __future__ import annotations

import json
import logging
import threading
import time

import pytest

from labhq.research import semantics_shadow as shadow
from tests.semantics_shadow_lab import fake_hub, request_row, task_row, workspace


def _service(tmp_path, n=8):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", {"outputs/counts.tsv": b"x"})
    requests = {f"req_{i:03d}": request_row(f"req_{i:03d}", [("s1", "analyst")], created_at=float(i)) for i in range(n)}
    tasks = {"task_a1": task_row("req_000", "task_a1", "s1", "analyst", wd, ["outputs/counts.tsv"])}
    hub = fake_hub(tmp_path, requests, tasks)
    service = shadow.ShadowService.start(hub)
    assert service is not None
    return service


def _lines(tmp_path, kind=None):
    path = tmp_path / "state" / "semantics" / "shadow.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [line for line in lines if kind is None or line.get("type") == kind]


def _disabled(tmp_path):
    path = tmp_path / "state" / "semantics" / "disabled.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _wait_for_shadow_state(service, tmp_path, predicate, description, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out waiting for {description}; "
                f"state={{'latched': {service.latched!r}, 'disabled': {_disabled(tmp_path)!r}, "
                f"'pending': {service.pending!r}, 'current': {service.current!r}, "
                f"'counts': {service.counts!r}, 'events': {[line.get('type') for line in _lines(tmp_path)]!r}}}"
            )
        time.sleep(0.02)


def _run(service, rids):
    for rid in rids:
        service.after_request(rid)
        assert service.drain(10)


@pytest.mark.parametrize("failure", ["error", "timeout", "write"])
def test_three_failures_in_a_row_turn_it_off(tmp_path, monkeypatch, failure):
    service = _service(tmp_path)
    if failure == "error":
        monkeypatch.setattr(shadow, "build_view", lambda snap: 1 / 0)
    elif failure == "timeout":
        service.cfg = shadow.ShadowConfig(timeout_s=0.1)
        real = shadow.compute_objects

        def slow(snap, check):
            time.sleep(0.15)
            return real(snap, check)
        monkeypatch.setattr(shadow, "compute_objects", slow)
    else:
        def full(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(shadow, "append_line", full)
    _run(service, ["req_001", "req_002", "req_003", "req_004"])
    assert service.latched == "consecutive_failures"
    assert _disabled(tmp_path)["reason"] == "consecutive_failures"
    if failure != "write":
        assert len(_lines(tmp_path, "request")) == 3
        assert _lines(tmp_path, "auto_off")[-1]["reason"] == "consecutive_failures"


def test_three_failures_in_the_last_twenty_turn_it_off(tmp_path, monkeypatch):
    service = _service(tmp_path, n=12)
    real = shadow.build_view

    def sometimes(snap):
        if snap["rid"] in ("req_002", "req_005", "req_008"):
            raise RuntimeError("x")
        return real(snap)

    monkeypatch.setattr(shadow, "build_view", sometimes)
    _run(service, [f"req_{i:03d}" for i in range(1, 12)])
    assert service.latched == "recent_failures"
    assert len(_lines(tmp_path, "request")) == 8  # nothing after req_008


def test_a_stuck_worker_turns_it_off_and_its_late_result_is_dropped(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.stuck_s = 0.2
    release = threading.Event()
    real = shadow.compute_line

    def stuck(*args, **kwargs):
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(shadow, "compute_line", stuck)
    service.after_request("req_001")
    _wait_for_shadow_state(
        service,
        tmp_path,
        lambda: service.latched == "worker_stuck" and (_disabled(tmp_path) or {}).get("reason") == "worker_stuck",
        "worker_stuck latch and durable disabled state",
    )
    release.set()
    assert service.drain(10)
    assert _lines(tmp_path, "request") == [] and _disabled(tmp_path)["reason"] == "worker_stuck"


def test_five_busy_skips_in_a_row_turn_it_off(tmp_path, monkeypatch):
    service = _service(tmp_path)
    release = threading.Event()
    real = shadow.compute_line
    monkeypatch.setattr(shadow, "compute_line", lambda *a, **k: (release.wait(10), real(*a, **k))[1])
    for i in range(1, 8):  # one runs, one waits, five are skipped
        service.after_request(f"req_{i:03d}")
    assert service.latched == "worker_busy" and service.counts["busy"] == 5
    release.set()
    assert service.drain(10)


def test_one_boundary_violation_turns_it_off_and_writes_nothing(tmp_path, monkeypatch):
    service = _service(tmp_path)
    real = shadow.compute_line

    def leaky(snap, *args, **kwargs):
        line = real(snap, *args, **kwargs)
        line["objects"]["leak"] = "outputs/counts.tsv"
        return line

    monkeypatch.setattr(shadow, "compute_line", leaky)
    _run(service, ["req_001"])
    assert service.latched == "info_boundary"
    assert _lines(tmp_path, "request") == []
    for path in (tmp_path / "state" / "semantics").iterdir():
        assert "counts.tsv" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["outputs/counts.tsv", "C" + ":" + "\\data", "/" + "home" + "/u", "two words",
                                   "counts.tsv", "request text"])
def test_the_boundary_check_refuses_paths_free_text_and_known_values(value):
    sensitive = shadow.sensitive_strings({"requests": {"r": {"text": "request text", "references": [],
                                                             "plan": {"steps": []}, "results": {}}},
                                          "tasks": {"t": {"result": {"outputs": ["outputs/counts.tsv"]}}}})
    assert shadow.boundary_problems({"x": value}, sensitive)
    assert shadow.boundary_problems({"x": "sem:0a1b2c3d", "n": 3, "ok": True, "r": None}, sensitive) == []


def test_wrong_identity_marked_from_the_cli_turns_the_running_shadow_off(tmp_path):
    service = _service(tmp_path)
    _run(service, ["req_001"])
    print(shadow.mark(service.paths, "req_001", "sem:0a1b2c3d", "wrong_identity"))
    _run(service, ["req_002"])
    assert service.latched == "wrong_identity" and len(_lines(tmp_path, "request")) == 1
    assert _disabled(tmp_path)["reason"] == "wrong_identity"


def test_off_survives_a_restart_and_enable_opens_a_new_epoch(tmp_path, monkeypatch, capsys):
    service = _service(tmp_path)
    service.trip("info_boundary")
    again = shadow.ShadowService.start(service.hub)
    assert again.latched == "info_boundary"
    _run(again, ["req_001"])
    assert _lines(tmp_path, "request") == []
    message = shadow.enable(again.paths)
    assert "info_boundary" in message and "epoch 2" in message
    _run(again, ["req_002"])
    assert again.latched is None and again.epoch == 2
    assert [line["epoch"] for line in _lines(tmp_path, "request")] == [2]
    assert [line["type"] for line in _lines(tmp_path)] == ["auto_off", "enable", "request"]


@pytest.mark.parametrize("broken, text", [
    ("disabled.json", "{not json"), ("state.json", "{not json"), ("breaker.json", "{not json"),
    ("breaker.json", '{"epoch": 1, "recent": "TF", "consecutive": 0}'),
    ("breaker.json", '{"epoch": 1, "recent": [true], "consecutive": -1}'),
], ids=["disabled", "state", "breaker", "breaker_recent_shape", "breaker_negative"])
def test_an_unreadable_breaker_file_means_off(tmp_path, broken, text):
    service = _service(tmp_path)
    (service.paths.root / broken).write_text(text, encoding="utf-8")
    again = shadow.ShadowService.start(service.hub)
    assert again.latched == "breaker_storage"
    _run(again, ["req_001"])
    assert _lines(tmp_path, "request") == []


def _fail_on(monkeypatch, rids):
    real = shadow.build_view

    def sometimes(snap):
        if snap["rid"] in rids:
            raise RuntimeError("x")
        return real(snap)

    monkeypatch.setattr(shadow, "build_view", sometimes)


def test_recent_failures_survive_a_restart(tmp_path, monkeypatch):
    """#173: two failures, a gateway restart, then one more of the last twenty turns it off."""
    service = _service(tmp_path, n=12)
    _fail_on(monkeypatch, {"req_002", "req_004", "req_007"})
    _run(service, ["req_001", "req_002", "req_003", "req_004", "req_005"])
    assert service.latched is None
    again = shadow.ShadowService.start(service.hub)
    assert list(again.recent) == [False, True, False, True, False] and again.latched is None
    _run(again, ["req_006", "req_007"])
    assert again.latched == "recent_failures" and _disabled(tmp_path)["reason"] == "recent_failures"


def test_consecutive_failures_survive_a_restart(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _fail_on(monkeypatch, {"req_001", "req_002", "req_003"})
    _run(service, ["req_001", "req_002"])
    assert service.latched is None
    again = shadow.ShadowService.start(service.hub)
    assert again.consecutive == 2
    _run(again, ["req_003"])
    assert again.latched == "consecutive_failures"


def test_a_new_epoch_does_not_count_the_failures_of_the_last_one(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _fail_on(monkeypatch, {"req_001", "req_002", "req_003"})
    _run(service, ["req_001", "req_002"])
    shadow.enable(service.paths)
    again = shadow.ShadowService.start(service.hub)
    assert again.epoch == 2 and list(again.recent) == [] and again.consecutive == 0
    _run(again, ["req_003"])
    assert again.latched is None and again.consecutive == 1
    third = shadow.ShadowService.start(service.hub)
    assert (third.epoch, list(third.recent), third.consecutive) == (2, [True], 1)


def test_a_saved_window_past_a_limit_turns_it_off_at_start(tmp_path):
    """A process that stopped after saving its third failure but before writing disabled.json."""
    service = _service(tmp_path)
    service.paths.breaker.write_text(json.dumps({"epoch": 1, "recent": [True] * 3, "consecutive": 3}),
                                     encoding="utf-8")
    again = shadow.ShadowService.start(service.hub)
    assert again.latched == "consecutive_failures"


def test_a_window_that_cannot_be_saved_turns_it_off(tmp_path, monkeypatch):
    service = _service(tmp_path)

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shadow, "write_breaker", full, raising=False)
    monkeypatch.setattr(shadow, "atomic_write_text", full)
    _run(service, ["req_001"])
    assert service.latched == "breaker_storage"


def test_a_state_dir_inside_a_git_work_tree_is_refused(tmp_path, caplog):
    (tmp_path / ".git").mkdir()
    hub = fake_hub(tmp_path, {}, {})
    with caplog.at_level(logging.WARNING, logger="labhq.semantics"):
        assert shadow.ShadowService.start(hub) is None
    assert "git work tree" in caplog.text and not (tmp_path / "state" / "semantics").exists()


def test_old_parts_are_pruned_and_the_log_is_capped(tmp_path, monkeypatch):
    import os
    service = _service(tmp_path)
    old = time.time() - 50 * 86400
    service.paths.log.write_text(json.dumps({"v": 1, "type": "request", "ts": old}) + "\n", encoding="utf-8")
    os.utime(service.paths.log, (old, old))
    _run(service, ["req_001"])  # the 50-day-old part rotates out and, unchanged for 45 days, is deleted
    assert not service.paths.log_old.exists()
    assert [line["request_id"] for line in _lines(tmp_path, "request")] == ["req_001"]
    monkeypatch.setattr(shadow, "LOG_PART_BYTES", 10)
    _run(service, ["req_002", "req_003"])
    assert service.paths.log_old.exists()
    lines, _ = shadow.read_lines(service.paths)
    assert [line["request_id"] for line in lines if line.get("type") == "request"] == ["req_002", "req_003"]
    stale = time.time() - 91 * 86400
    os.utime(service.paths.log, (stale, stale))
    os.utime(service.paths.log_old, (stale, stale))
    shadow.prune(service.paths, time.time())
    assert not service.paths.log.exists() and not service.paths.log_old.exists()


def test_expired_observations_are_dropped_while_running(tmp_path):
    service = _service(tmp_path)
    fresh, stale = time.time(), time.time() - 91 * 86400
    service.paths.root.mkdir(parents=True, exist_ok=True)
    service.paths.observed.write_text(json.dumps({"k_fresh": {"sha256": "a", "size": 1, "at": fresh},
                                                  "k_stale": {"sha256": "b", "size": 1, "at": stale}}),
                                      encoding="utf-8")
    _run(service, ["req_001"])  # no output of its own: only the expiry changes the file
    assert set(json.loads(service.paths.observed.read_text(encoding="utf-8"))) == {"k_fresh"}
    service.observed["k_late"] = {"sha256": "c", "size": 1, "at": time.time() - 91 * 86400}
    _run(service, ["req_002"])
    assert "k_late" not in json.loads(service.paths.observed.read_text(encoding="utf-8"))


def test_enable_after_a_stuck_worker_starts_a_fresh_one(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.stuck_s = 0.2
    release = threading.Event()
    real = shadow.compute_line

    def stuck_once(snap, *args, **kwargs):
        if snap["rid"] == "req_001":
            release.wait(10)
        return real(snap, *args, **kwargs)

    monkeypatch.setattr(shadow, "compute_line", stuck_once)
    service.after_request("req_001")
    deadline = time.monotonic() + 5
    while service.latched is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.latched == "worker_stuck"
    shadow.enable(service.paths)
    service.after_request("req_002")  # the old job is still stuck; the new epoch must not trip on it
    assert service.drain(10)
    time.sleep(0.4)  # past the old watchdog's deadline
    assert service.latched is None and service.epoch == 2
    assert [(line["request_id"], line["epoch"]) for line in _lines(tmp_path, "request")] == [("req_002", 2)]
    release.set()
    time.sleep(0.2)
    assert [line["request_id"] for line in _lines(tmp_path, "request")] == ["req_002"]


def test_a_cli_mark_stops_the_running_job_without_another_request(tmp_path, monkeypatch):
    service = _service(tmp_path)
    started, release = threading.Event(), threading.Event()
    real = shadow.compute_objects

    def held(snap, check):
        started.set()
        release.wait(10)
        time.sleep(shadow.EXTERNAL_LOOK_S + 0.05)
        return real(snap, check)

    monkeypatch.setattr(shadow, "compute_objects", held)
    service.after_request("req_001")
    assert started.wait(5)
    shadow.mark(service.paths, "req_000", "sem:0a1b2c3d", "wrong_identity")
    release.set()
    assert service.drain(10)
    assert service.latched == "wrong_identity" and _lines(tmp_path, "request") == []


def test_a_state_dir_linked_into_a_git_work_tree_is_refused(tmp_path):
    import os
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "inside").mkdir()
    link = tmp_path / "outside"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(repo / "inside"), str(link))
        else:
            os.symlink(repo / "inside", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a directory link here")
    hub = fake_hub(tmp_path, {}, {})
    hub.s.gateway.state_dir = str(link / "state")
    assert shadow.ShadowService.start(hub) is None
    assert not (repo / "inside" / "state").exists()


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_a_job_stopped_before_the_models_still_leaves_a_line(tmp_path, monkeypatch, failure):
    service = _service(tmp_path)

    def stopped(snap, check):
        raise shadow.ShadowTimeout("time cap") if failure == "timeout" else KeyError("x")

    monkeypatch.setattr(shadow, "read_rows", stopped)
    _run(service, ["req_001"])
    (line,) = _lines(tmp_path, "request")
    assert line["request_id"] == "req_001" and line["status"] == "done"
    assert line["provenance"]["status"] == line["objects"]["status"] == failure
    assert service.counts["failures"] == 1
    rep = shadow.build_report(service.paths)
    assert rep["requests"] == 1 and rep["provenance"][failure] == 1


def test_a_malformed_row_is_a_failure_and_never_ends_the_worker(tmp_path):
    """`result.outputs: 42` in a stored row: the line is still checked and written, the failure counts, and
    three in a row turn the shadow off. The worker thread must not die before it counts."""
    service = _service(tmp_path)
    for i in (1, 2, 3):
        rid, tid = f"req_{i:03d}", f"task_bad{i}"
        row = task_row(rid, tid, "s1", "analyst", None, [])
        row["result"]["outputs"] = 42
        service.hub.store.rows["task"][tid] = row
    _run(service, ["req_001", "req_002", "req_003"])
    assert service.counts["failures"] == 3 and service.latched == "consecutive_failures"
    assert [line["objects"]["status"] for line in _lines(tmp_path, "request")] == ["error"] * 3


def test_a_boundary_check_that_raises_writes_nothing_and_counts(tmp_path, monkeypatch, caplog):
    service = _service(tmp_path)

    def broken(snap):
        raise TypeError("injected /data/cohort")

    monkeypatch.setattr(shadow, "sensitive_strings", broken)
    caplog.set_level(logging.WARNING, logger="labhq.semantics")
    _run(service, ["req_001", "req_002", "req_003"])
    assert service.counts["failures"] == 3 and service.latched == "consecutive_failures"
    assert _lines(tmp_path, "request") == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("boundary check" in m for m in messages) and all("/data/cohort" not in m for m in messages)


def test_a_sqlite_without_json1_turns_it_off_at_start_with_its_reason(tmp_path, monkeypatch):
    """#160: the task query needs JSON1; without it the shadow is off with a reason the report shows, instead of
    failing every job and ending as consecutive_failures."""
    monkeypatch.setattr(shadow, "JSON1_PROBE", "SELECT no_such_json1_function('{}')", raising=False)
    service = _service(tmp_path)
    assert service.latched == "sqlite_json1_missing" and _disabled(tmp_path)["reason"] == "sqlite_json1_missing"
    _run(service, ["req_001"])
    assert _lines(tmp_path, "request") == [] and service.counts["failures"] == 0
    rep = shadow.build_report(service.paths, setting="shadow")
    assert rep["state"]["on"] is False and rep["state"]["reason"] == "sqlite_json1_missing"
    assert [a["reason"] for a in rep["auto_off"]] == ["sqlite_json1_missing"]
    assert "sqlite_json1_missing" in shadow.render_report(rep)


def test_the_json1_probe_runs_the_functions_the_task_query_uses():
    assert shadow.sqlite_json1() is True
    for name in ("json_remove", "json_extract", "json_each"):
        assert name in shadow._TASK_SQL + shadow._JOBS_SQL and name in shadow.JSON1_PROBE


def test_a_failure_counted_on_the_event_loop_saves_its_window_off_the_loop(tmp_path, monkeypatch):
    """#173: after_request runs on the gateway event loop; its failure is counted at once, the disk write is not
    done there."""
    service = _service(tmp_path)
    writers, real = [], shadow.write_breaker

    def recording(*args, **kwargs):
        writers.append(threading.current_thread().name)
        return real(*args, **kwargs)

    def broken(*args, **kwargs):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(shadow, "write_breaker", recording)
    monkeypatch.setattr(shadow, "take_snapshot", broken)
    service.after_request("req_001")
    assert service.consecutive == 1
    _wait_for_shadow_state(service, tmp_path, lambda: service.paths.breaker.exists(), "breaker.json written")
    assert writers and threading.main_thread().name not in writers
    assert json.loads(service.paths.breaker.read_text(encoding="utf-8"))["consecutive"] == 1
