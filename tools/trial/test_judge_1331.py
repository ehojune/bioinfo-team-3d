"""Synthetic checks for judge_1331 (not in CI's testpaths): python -m pytest tools/trial/test_judge_1331.py"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import judge_1331 as J  # noqa: E402

REQ = "req_test"


def events_db(tmp_path, events):
    path = tmp_path / "gateway.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)")
    db.executemany("INSERT INTO events (body) VALUES (?)", [(json.dumps(e),) for e in events])
    db.commit()
    db.close()
    return path


def ev(typ, tid, ts=None, step=None, **data):
    body = {"type": typ, "task_id": tid, "request_id": REQ, "agent_id": f"a-{tid}"}
    if ts is not None:
        body["ts"] = ts
    if step:
        body["data"] = {"step_id": step}
    elif data:
        body["data"] = data
    return body


def two_overlapping(tmp_path):
    return events_db(tmp_path, [
        ev("task.dispatched", "t1", 100.0, step="fetch"),
        ev("task.dispatched", "t2", 110.0, step="index"),
        ev("agent.log", "t1", text="calling hpc_submit"),
        ev("agent.log", "t2", text="mcp__labhq-hpc__hpc_status"),
        ev("task.result", "t2"),
        {"type": "request.status", "request_id": REQ, "ts": 300.0},
        ev("task.result", "t1"),
        {"type": "request.status", "request_id": REQ, "ts": 400.0},
        ev("task.dispatched", "t3", 500.0, step="quant"),
        ev("task.result", "t3", 600.0),
    ])


def test_overlap_and_end_time_from_next_event(tmp_path):
    tasks = J.step_intervals(two_overlapping(tmp_path), REQ, set())
    by = {t["step_id"]: t for t in tasks}
    assert by["fetch"]["hpc"] and by["index"]["hpc"] and not by["quant"]["hpc"]
    assert by["index"]["end_ts"] == 300.0 and by["fetch"]["end_ts"] == 400.0
    assert J.check_overlap(tasks)["status"] == "PASS"


def test_sequential_hpc_steps_do_not_overlap(tmp_path):
    db = events_db(tmp_path, [
        ev("task.dispatched", "t1", 1.0, step="fetch"), ev("agent.log", "t1", text="hpc_submit"), ev("task.result", "t1", 2.0),
        ev("task.dispatched", "t2", 3.0, step="index"), ev("agent.log", "t2", text="hpc_submit"), ev("task.result", "t2", 4.0),
    ])
    tasks = J.step_intervals(db, REQ, set())
    assert J.check_overlap(tasks)["status"] == "FAIL"
    assert J.check_session(tasks, [])["status"] == "FAIL"


def test_retry_of_same_step_is_not_a_pair(tmp_path):
    db = events_db(tmp_path, [
        ev("task.dispatched", "t1", 1.0, step="fetch"), ev("task.dispatched", "t2", 2.0, step="fetch"),
        ev("task.result", "t1", 3.0), ev("task.result", "t2", 4.0),
    ])
    assert J.check_overlap(J.step_intervals(db, REQ, {"fetch"}))["status"] == "FAIL"


def test_session_counts_logins_inside_overlap(tmp_path):
    tasks = J.step_intervals(two_overlapping(tmp_path), REQ, set())
    assert J.check_session(tasks, [(50.0, None), (105.0, REQ)])["status"] == "PASS"
    assert J.check_session(tasks, [(115.0, REQ), (200.0, REQ)])["status"] == "FAIL"


def test_window_sliding():
    assert J.check_window([(0.0, None), (3599.0, None)], (0, 10))["status"] == "PASS"
    assert J.check_window([(0.0, None), (1000.0, None), (3599.0, None)], (0, 10))["status"] == "FAIL"
    assert J.check_window([(0.0, None), (1000.0, None), (3600.0, None)], (0, 10))["peak"] == 2


def full_run(tmp_path, hits_assoc=19, hits_none=0, pairs=J.MIN_PAIRS, as_group_files=True):
    """A synthetic run: the table's pairs plus filler pairs with p=0.9 (filler keeps BH at the paper's scale)."""
    table = J.load_table()
    assoc = [r for r in table if r["expect"] == "assoc"]
    none = [r for r in table if r["expect"] == "none"]
    rows = {}
    for i, r in enumerate(assoc):
        rows[(r["trait"], r["cell_type"].replace("_", " "))] = 1e-6 if i < hits_assoc else 0.5
    for i, r in enumerate(none):
        rows[(r["trait"], r["cell_type"])] = 1e-6 if i < hits_none else 0.8
    for i in range(pairs - len(rows)):
        rows[(f"TRAIT{i % 74}", f"filler cell {i}")] = 0.9
    out = tmp_path / "run"
    out.mkdir()
    if as_group_files:
        by_trait = {}
        for (trait, cell), p in rows.items():
            by_trait.setdefault(trait, []).append((cell, p))
        for trait, cells in by_trait.items():
            (out / f"{trait}.scdrs_group.cell_ontology_class").write_text(
                "group\tn_cell\tassoc_mcp\n" + "".join(f"{c}\t10\t{p}\n" for c, p in cells), encoding="utf-8")
    else:
        (out / "celltype_assoc.tsv").write_text(
            "trait\tcell_type\tassoc_mcp\n" + "".join(f"{t}\t{c}\t{p}\n" for (t, c), p in rows.items()),
            encoding="utf-8")
    return J.find_assoc_files(out), table


def test_table_is_fixed_from_paper():
    table = J.load_table()
    assert sum(r["expect"] == "assoc" for r in table) == 19 and sum(r["expect"] == "none" for r in table) == 5
    assert all(float(r["paper_assoc_fdr"]) < 0.05 for r in table if r["expect"] == "assoc")
    assert all(float(r["paper_assoc_fdr"]) >= 0.5 for r in table if r["expect"] == "none")


def test_repro_pass_from_group_files(tmp_path):
    files, table = full_run(tmp_path)
    out = J.check_repro(files, table)
    assert out["status"] == "PASS" and out["n_pairs"] == J.MIN_PAIRS


def test_repro_combined_table_and_thresholds(tmp_path):
    files, table = full_run(tmp_path, hits_assoc=15, hits_none=1, as_group_files=False)
    assert J.check_repro(files, table)["status"] == "PASS"
    (tmp_path / "run").rename(tmp_path / "old")
    files, table = full_run(tmp_path, hits_assoc=14)
    assert J.check_repro(files, table)["status"] == "FAIL"


def test_repro_fails_on_off_block_hits_or_small_run(tmp_path):
    files, table = full_run(tmp_path, hits_none=2)
    assert J.check_repro(files, table)["status"] == "FAIL"
    (tmp_path / "run").rename(tmp_path / "old")
    files, table = full_run(tmp_path, pairs=100)  # only the table's traits: BH is not the paper's scale
    assert J.check_repro(files, table)["status"] == "FAIL"
    assert J.check_repro([], table)["status"] == "FAIL"


def test_bh():
    q = J.bh({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    want = {"a": 0.04, "c": 0.04 * 4 / 3, "b": 0.04 * 4 / 3, "d": 0.5}  # c is lifted to b's q (monotone step-up)
    assert all(abs(q[k] - v) < 1e-12 for k, v in want.items())


def hire_req(agent="scdrs-hire", step_agent="scdrs-hire", status="active"):
    return {"plan": {"recruit_results": [{"paper": "https://doi.org/10.1038/s41588-022-01167-z", "status": status,
                                          "agent_id": agent, "step_ids": ["score"]}],
                     "steps": [{"id": "score", "agent_id": step_agent}, {"id": "fetch", "agent_id": "engineer"}]}}


def task(step, agent, ended=True):
    return {"task_id": f"t-{step}-{agent}", "step_id": step, "agent_id": agent, "end_seq": 5 if ended else None}


def test_hire():
    assert J.check_hire(hire_req(), [task("score", "scdrs-hire")])["status"] == "PASS"
    assert J.check_hire(hire_req(status="failed"), [task("score", "scdrs-hire")])["status"] == "FAIL"
    assert J.check_hire(hire_req(step_agent="analyst"), [task("score", "analyst")])["status"] == "FAIL"
    assert J.check_hire(hire_req(), [task("score", "scdrs-hire", ended=False)])["status"] == "FAIL"
    assert J.check_hire(hire_req(), [task("score", "scdrs-hire"), task("score", "analyst")])["status"] == "FAIL"
    assert J.check_hire({}, [])["status"] == "FAIL"


def test_request_state_reads_plan(tmp_path):
    db = tmp_path / "g.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE state (kind TEXT, key TEXT, body TEXT, PRIMARY KEY(kind, key))")
    con.execute("INSERT INTO state VALUES ('request', ?, ?)", (REQ, json.dumps(hire_req())))
    con.commit()
    con.close()
    assert J.request_state(db, REQ)["plan"]["recruit_results"][0]["agent_id"] == "scdrs-hire"
    assert J.request_state(db, "other") == {}


def test_public_scan_hides_terms(tmp_path):
    (tmp_path / "report.md").write_text("ran on node-x.example.org under /scratch/zz", encoding="utf-8")
    (tmp_path / "clean.md").write_text("nothing here", encoding="utf-8")
    out = J.check_public([tmp_path], ["node-x.example.org", "/scratch/zz"])
    assert out["status"] == "FAIL" and list(out["files"].values()) == [2]
    assert "example" not in json.dumps(out["detail"])
    assert J.check_public([tmp_path], [])["status"] == "SKIP"


def test_log_keeps_steps_after_events_pruned(tmp_path):
    db = two_overlapping(tmp_path)
    log = tmp_path / "events.jsonl"
    J.watch(db, REQ, log, every=0, until=0)  # until in the past: nothing recorded
    assert not log.exists() or not log.read_text()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO events (body) VALUES (?)", (json.dumps({"type": "request.completed", "request_id": REQ}),))
    con.commit()
    assert J.watch(db, REQ, log, every=0, until=9e18) == 0
    con.execute("DELETE FROM events")  # replay retention dropped everything
    con.commit()
    con.close()
    tasks = J.step_intervals(db, REQ, set(), log)
    by = {t["step_id"]: t for t in tasks}
    assert set(by) == {"fetch", "index", "quant"} and by["fetch"]["hpc"] and by["index"]["hpc"]
    assert by["index"]["end_ts"] is not None and by["quant"]["end_ts"] == 600.0
    assert J.check_overlap(tasks)["status"] == "PASS"
    J.watch(db, REQ, log, every=0, until=0)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 8  # restart appends nothing twice


def test_archive_marks_hpc(tmp_path):
    db = events_db(tmp_path, [ev("task.dispatched", "t1", 1.0, step="fetch"), ev("task.result", "t1", 2.0)])
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messenger_events (seq INTEGER PRIMARY KEY, request_id TEXT, body TEXT NOT NULL)")
    con.execute("INSERT INTO messenger_events VALUES (99, ?, ?)", (REQ, json.dumps(ev("agent.log", "t1", text="hpc_submit ok"))))
    con.commit()
    con.close()
    assert J.step_intervals(db, REQ, set())[0]["hpc"]


def test_argv_terms_all_ssh_forms():
    assert J.argv_terms(["ssh", "-l", "alice", "cluster"]) == {"alice", "cluster"}
    assert J.argv_terms(["ssh", "-p", "2222", "bob@login.example.org", "qstat"]) == {"bob", "login.example.org"}
    assert J.argv_terms(["ssh", "-lcarol", "-J", "dave@jump01", "-o", "HostName=node9", "-i", "k.pem", "gw"]) == \
        {"carol", "dave", "jump01", "node9", "gw"}


def test_session_counts_only_the_overlap(tmp_path):
    tasks = J.step_intervals(two_overlapping(tmp_path), REQ, set())  # overlap: 110 (index starts) .. 300 (index ends)
    assert J.check_session(tasks, [(100.0, REQ), (350.0, REQ)])["status"] == "PASS"  # before and after the overlap
    assert J.check_session(tasks, [(120.0, REQ), (200.0, REQ)])["status"] == "FAIL"
