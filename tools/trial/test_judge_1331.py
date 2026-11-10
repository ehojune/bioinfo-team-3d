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


def write_de(path, rows, sep=","):
    path.write_text("\n".join(sep.join(map(str, r)) for r in rows), encoding="utf-8")
    return path


def test_genes_pass_by_ensembl_with_version(tmp_path):
    rows = [["", "baseMean", "log2FoldChange", "lfcSE", "pvalue", "padj"]]
    for i, ensg in enumerate(J.GENES.values()):
        rows.append([f"{ensg}.{i}", 100, 2.0 if i < 5 else -0.1, 0.1, 1e-9, 1e-6 if i < 5 else 0.9])
    short = write_de(tmp_path / "short" / "de.csv", rows) if (tmp_path / "short").mkdir() is None else None
    assert J.check_genes([short])["status"] == "FAIL"  # six rows are not a genome-wide table
    rows += [[f"ENSG9{i:010d}", 10, 0.0, 0.1, 0.5, 0.9] for i in range(J.MIN_ROWS)]
    table = write_de(tmp_path / "de.csv", rows)
    assert table in J.find_de_tables(tmp_path)
    out = J.check_genes([table])
    assert out["status"] == "PASS" and len(out["up"]) == 5


def test_genes_fail_on_reversed_contrast_with_hint(tmp_path):
    rows = [["gene", "logFC", "FDR"]] + [[s, -3.0, 1e-8] for s in J.GENES]
    out = J.check_genes([write_de(tmp_path / "de.tsv", rows, "\t")])
    assert out["status"] == "FAIL" and "반대" in out["detail"]


def test_genes_na_skipped(tmp_path):
    rows = [["symbol", "log2FoldChange", "padj"]] + [[s, "NA", "NA"] for s in J.GENES]
    out = J.check_genes([write_de(tmp_path / "de.csv", rows)])
    assert out["status"] == "FAIL" and len(out["missing"]) == 6


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
    assert J.check_session(tasks, [(90.0, REQ), (350.0, REQ)])["status"] == "PASS"  # before both, after the overlap
    assert J.check_session(tasks, [(120.0, REQ), (200.0, REQ)])["status"] == "FAIL"
    assert J.check_session(tasks, [(102.0, REQ), (112.0, REQ)])["status"] == "FAIL"  # two sessions, one per step


def test_genes_prefers_the_genome_wide_table(tmp_path):
    summary = write_de(tmp_path / "a_summary.csv", [["gene", "log2FoldChange", "padj"]] + [[s, 3.0, 1e-9] for s in J.GENES])
    rows = [["gene", "log2FoldChange", "padj"]] + [[s, 2.0, 1e-6] for s in list(J.GENES)[:5]]
    rows += [[f"ENSG9{i:010d}", 0.0, 0.9] for i in range(J.MIN_ROWS)]
    full = write_de(tmp_path / "b_full.csv", rows)
    out = J.check_genes([summary, full])
    assert out["status"] == "PASS" and out["table"] == str(full)


def test_one_employee_with_two_steps_is_not_a_pair(tmp_path):
    db = events_db(tmp_path, [
        {**ev("task.dispatched", "t1", 1.0, step="fetch"), "agent_id": "same"},
        {**ev("task.dispatched", "t2", 2.0, step="index"), "agent_id": "same"},
        ev("agent.log", "t1", text="hpc_submit"), ev("agent.log", "t2", text="hpc_submit"),
        ev("task.result", "t1", 3.0), ev("task.result", "t2", 4.0),
    ])
    assert J.check_overlap(J.step_intervals(db, REQ, set()))["status"] == "FAIL"


def test_public_short_terms_match_whole_words(tmp_path):
    (tmp_path / "report.md").write_text("ran as zq on n07; the zqx tool and an7 are not site words", encoding="utf-8")
    out = J.check_public([tmp_path], ["zq", "n07"], ["n07"])
    assert out["status"] == "FAIL" and list(out["files"].values()) == [2]
    assert J.check_public([tmp_path], ["zz"], [])["status"] == "FAIL"  # no pinned node known: refuse to pass
    assert J.check_public([tmp_path], ["zz"], ["n99"])["status"] == "PASS"


def test_node_terms():
    assert J.node_terms(["-w", "n01", "--nodelist=n02,n03", "-l", "h=n04", "all.q@n05", "--mem=4G"]) == \
        {"n01", "n02", "n03", "n04", "n05"}


def test_site_terms_take_every_user_set_value(tmp_path):
    from types import SimpleNamespace
    from labhq.settings import HpcSettings, SlurmSettings
    sbatch = [*SlurmSettings().sbatch_args, "--account=private_lab", "--qos=pq7", "--nodelist=cn12"]
    hpc = HpcSettings(scheduler="slurm", ssh_host="bob@login.example.org", slurm=SlurmSettings(sbatch_args=sbatch))
    terms, nodes = J.site_terms(SimpleNamespace(hpc=hpc), None)
    assert {"bob", "login.example.org", "private_lab", "pq7", "cn12"} <= set(terms) and nodes == ["cn12"]
    assert not {"slurm", "NONE", "1", "smp", "h_vmem"} & set(terms)  # defaults and generic words stay out


def test_public_fails_when_nothing_was_read(tmp_path):
    assert J.check_public([tmp_path / "missing"], ["zz"], ["n99"])["status"] == "FAIL"


def test_site_terms_include_the_fallback_account(monkeypatch):
    from types import SimpleNamespace
    import getpass
    from labhq.settings import HpcSettings
    monkeypatch.setattr(getpass, "getuser", lambda: "hiddenacct")
    assert "hiddenacct" in J.site_terms(SimpleNamespace(hpc=HpcSettings(scheduler="sge")), None)[0]
    assert "hiddenacct" not in J.site_terms(SimpleNamespace(hpc=HpcSettings(scheduler="sge", user="u9x")), None)[0]


def test_public_reads_logs_by_content(tmp_path):
    (tmp_path / "job.out").write_text("running on n07", encoding="utf-8")
    (tmp_path / "noext").write_text("user zq", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0\0 n07 zq")
    (tmp_path / "x.gz").write_bytes(b"n07")
    out = J.check_public([tmp_path], ["n07", "zq"], ["n07"])
    assert out["status"] == "FAIL" and sorted(Path(f).name for f in out["files"]) == ["job.out", "noext"]


def test_public_ignores_case_and_reads_large_logs(tmp_path):
    (tmp_path / "job.err").write_text("x" * 100 + "\n" * 10 + "landed on N07\n", encoding="utf-8")
    out = J.check_public([tmp_path], ["n07"], ["n07"])
    assert out["status"] == "FAIL" and list(out["files"].values()) == [1]


def test_skip_is_incomplete_not_pass(tmp_path, monkeypatch):
    from types import SimpleNamespace
    two_overlapping(tmp_path)  # writes tmp_path/gateway.sqlite3, where the fake settings point
    fake = SimpleNamespace(hpc=SimpleNamespace(_login_ledger=None), gateway=SimpleNamespace(state_dir="x"),
                           runner=SimpleNamespace(workspace_root=str(tmp_path)), path=lambda v: tmp_path)
    import labhq.settings
    monkeypatch.setattr(labhq.settings.Settings, "load", classmethod(lambda cls, p=None: fake))
    code = J.main(["--config", "c.yaml", "--request", REQ, "--skip", "genes", "--skip", "verify", "--skip", "public",
                   "--skip", "session", "--skip", "window", "--hpc-step", "fetch", "--hpc-step", "index"])
    assert code == 2

# ---- r6: ssh option values and Windows drive letters are not site terms ----
from types import SimpleNamespace  # noqa: E402

j = J

ARGV = ["ssh", "-tt", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
        "-o", "HostKeyAlias=gw", "-o", "UserKnownHostsFile=C:/Users/me/.ssh/known", "-p", "13000", "alice@127.0.0.1"]


def test_connect_argv_gives_the_login_name_only():
    assert j.argv_terms(ARGV) == {"alice"}
    assert j.argv_terms(["ssh", "-J", "jump.site", "bob@login.site"]) == {"jump.site", "bob", "login.site"}


def test_a_profile_connect_argv_is_not_split_into_option_words():
    class Profile(SimpleNamespace):
        model_fields = {"connect_argv": None, "queue": None, "remote_workdir": None}

    nodes = set()
    terms = j.model_terms(Profile(connect_argv=ARGV, queue="part1", remote_workdir="/scratch/lab/me"), nodes)
    assert terms == {"part1", "/scratch/lab/me"}
    assert not {"yes", "BatchMode", "ConnectTimeout", "13000"} & terms


def test_windows_paths_stay_whole_and_short_identifiers_stay():
    found = j.value_terms(["C:/Users/me/x", "--config=D:/lab/cfg", "q", "n1"])
    assert found == {"C:/Users/me/x", "D:/lab/cfg", "q", "n1"}


def test_space_form_ssh_options_still_give_user_and_host():
    assert j.argv_terms(["ssh", "-o", "HostName private.example", "-o", "User alice", "alias"]) >= {"private.example", "alice"}


def test_unknown_step_ids_are_not_one_step(tmp_path):
    # dispatch events without data.step_id (the gateway's form): two staff still make a pair
    db = events_db(tmp_path, [
        ev("task.dispatched", "t1", 1.0), ev("task.dispatched", "t2", 2.0),
        ev("agent.log", "t1", text="hpc_submit"), ev("agent.log", "t2", text="hpc_submit"),
        ev("task.result", "t1", 3.0), ev("task.result", "t2", 4.0),
    ])
    assert J.check_overlap(J.step_intervals(db, REQ, set()))["status"] == "PASS"


def step_file(steps, step, rows):
    folder = steps / step
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_file_intervals_find_overlap_from_step_events(tmp_path):
    steps = tmp_path / "steps"
    step_file(steps, "fetch", [
        {"type": "agent.status", "ts": 10.0, "task_id": "t1", "agent_id": "steward", "data": {"state": "queued"}},
        {"type": "agent.tool", "ts": 11.0, "task_id": "t1", "agent_id": "steward", "data": {"name": "mcp__labhq_hpc__hpc_submit"}},
        {"type": "task.result", "ts": 20.0, "task_id": "t1", "agent_id": "steward", "data": {}},
        {"type": "agent.status", "ts": 50.0, "task_id": "t3", "agent_id": "steward", "data": {"state": "queued"}},
        {"type": "task.result", "ts": 60.0, "task_id": "t3", "agent_id": "steward", "data": {}},
    ])
    step_file(steps, "index", [
        {"type": "agent.status", "ts": 12.0, "task_id": "t2", "agent_id": "engineer", "data": {"state": "queued"}},
        {"type": "agent.tool", "ts": 13.0, "task_id": "t2", "agent_id": "engineer", "data": {"name": "mcp__labhq_hpc__hpc_submit"}},
        {"type": "task.result", "ts": 30.0, "task_id": "t2", "agent_id": "engineer", "data": {}},
    ])
    tasks = J.file_intervals(steps, set())
    by = {t["task_id"]: t for t in tasks}
    assert by["t1"]["step_id"] == "fetch" and by["t1"]["end_ts"] == 20.0 and by["t1"]["hpc"]
    assert not by["t3"]["hpc"]
    check = J.check_overlap(tasks)
    assert check["status"] == "PASS" and len(check["pairs"]) == 1


def test_file_intervals_sequential_steps_fail(tmp_path):
    steps = tmp_path / "steps"
    step_file(steps, "fetch", [
        {"type": "agent.tool", "ts": 1.0, "task_id": "t1", "agent_id": "a", "data": {"name": "hpc_submit"}},
        {"type": "task.result", "ts": 2.0, "task_id": "t1", "agent_id": "a", "data": {}}])
    step_file(steps, "index", [
        {"type": "agent.tool", "ts": 3.0, "task_id": "t2", "agent_id": "b", "data": {"name": "hpc_submit"}},
        {"type": "task.result", "ts": 4.0, "task_id": "t2", "agent_id": "b", "data": {}}])
    assert J.check_overlap(J.file_intervals(steps, set()))["status"] == "FAIL"
