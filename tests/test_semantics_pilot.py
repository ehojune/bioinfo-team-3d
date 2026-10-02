"""Semantics pilot (#127): fixed expected answers, model B, baseline A and their isolation."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import semantics_pilot as pilot

FIXTURE = Path(__file__).parent / "fixtures" / "semantics"
RECORDS = FIXTURE / "records"

# Fixed after the one independent review and correction 1. Changing either is a pilot correction
# (a separate commit with its reason) or an experiment failure, never a routine update.
EXPECTED_SHA256 = "e8f094595f3e4c19056df37ac2ee54425c8d5d20499e312e6f59671e05671c02"
FIXTURE_SHA256 = "a2596ef7f86b27f78925f27af27b0e726b99804e187df3dcb95ff06c3daa0316"


def inventory_sha256(root: Path) -> str:
    """sha256 over 'relative posix path LF file sha256 LF' for every file under root, in path order."""
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        digest.update(f"{path.relative_to(root).as_posix()}\n{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
                      .encode())
    return digest.hexdigest()


def test_expected_answers_are_the_pinned_version():
    assert hashlib.sha256((FIXTURE / "expected.yaml").read_bytes()).hexdigest() == EXPECTED_SHA256


def test_fixture_records_are_the_pinned_version():
    assert inventory_sha256(RECORDS) == FIXTURE_SHA256


# ---------------------------------------------------------------- model B


# Model revision for #155 (reason start_unknown). The pilot measurement in docs/reference/semantics_pilot.md
# ran on the previous hash 8fd9860f…8159.
MODEL_SHA256 = "fc4171808c1941b8d1bb457f6fcf7b4761c35c874e7ba162c4e39899993a8eef"  # #221: typed declarations, five reasons


@pytest.fixture(scope="module")
def expected():
    return pilot.load_expected()


@pytest.fixture(scope="module")
def fixture_paths():
    with pilot.fixture_copy() as paths:
        yield paths


def outputs_of(name, fixture_paths, expected):
    impl = pilot.IMPLS[name]()
    records = fixture_paths.read(overlay=impl.state == "change2")
    return impl, pilot.run_queries(impl, impl.project(records), expected)


def test_model_file_is_the_pinned_version():
    from labhq.research.semantics import load_model
    model = load_model()
    assert (model.name, model.sha256) == ("labhq.provenance@1", MODEL_SHA256)


@pytest.mark.parametrize("edit, message", [
    (lambda s: s.replace("model: labhq.provenance\n", "model: labhq.provenance\nmodel: again\n"), "duplicate key"),
    (lambda s: s.replace("  probe_not_validation:", "  probe_not_validated:"), "without a judge"),
    (lambda s: s.replace("judge: judge_hash,", "judge: judge_hashes,"), "names judge_hashes"),
    (lambda s: s.replace("from: run, to: [method]", "from: run, to: [procedure]"), "undeclared concept procedure"),
    (lambda s: s.replace("  inherit_broader: false", "  inherit_broader: true"), "inherit_broader"),
])
def test_model_load_rejects_bad_models(tmp_path, edit, message):
    from labhq.research.semantics import MODEL_PATH, ModelError, load_model
    text = MODEL_PATH.read_text(encoding="utf-8")
    changed = edit(text)
    assert changed != text
    path = tmp_path / "semantics_v1.yaml"
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(ModelError, match=message):
        load_model(path)


def test_model_rejects_an_edge_the_model_does_not_declare(fixture_paths):
    from labhq.research.semantics import ModelError, load_model, project
    p = project(load_model(), fixture_paths.read(overlay=False))
    with pytest.raises(ModelError, match="does not connect"):
        p.add_edge("ev:ws/t/E1", "used", "art:r/ws/x.tsv", "declared")
    with pytest.raises(ModelError, match="basis"):
        p.add_edge("run:ws/t", "used", "art:r/ws/x.tsv", "reported")


@pytest.mark.parametrize("qid", [f"q{n:02d}" for n in range(1, 18)])
@pytest.mark.parametrize("name", ["B", "A"])
def test_answers(name, qid, fixture_paths, expected):
    impl, outputs = outputs_of(name, fixture_paths, expected)
    scored = pilot.score_queries(expected, impl.state, outputs)
    assert scored[qid]["ok"], scored[qid]["diff"]


def test_model_b_answers_are_advisory(fixture_paths):
    from labhq.research.semantics import audit_lineage, find_reusable, load_model, project
    model = load_model()
    p = project(model, fixture_paths.read(overlay=False))
    for answer in (find_reusable(p, key="outputs/scan_extra.tsv"),
                   audit_lineage(p, evidence="ev:task_q3scan_ag_seqtool/task_q3scan/E4")):
        assert (answer.status, answer.model, answer.model_sha256) == ("advisory", "labhq.provenance@1", MODEL_SHA256)
    a8 = find_reusable(p, key="outputs/scan_extra.tsv").result["candidates"]
    assert [c["recommend"] for c in a8.values()] == [False]


def test_changes_move_only_the_declared_answers(expected):
    """Before/after outputs are fixed: change 1 moves q02 and five uses, change 2 moves q06 and data types."""
    base, one, two = (pilot.expected_state(expected, s) for s in pilot.STATES)
    moved = lambda a, b: sorted(q for q in a["answers"] if a["answers"][q] != b["answers"][q])  # noqa: E731
    assert moved(base, one) == ["q02"] and moved(one, two) == ["q06"]
    kinds = lambda s: {u["edge"]: u["reuse_kind"] for u in s["nodes"]["uses"]}  # noqa: E731
    assert sorted(e for e in kinds(base) if kinds(base)[e] != kinds(one)[e]) == ["e07", "e10", "e28", "e36", "e68"]
    types = {a["id"]: a["data_type"] for a in two["nodes"]["artifacts"]}
    assert sum(t != "unknown" for t in types.values()) == 6 and len(two["edges"]) == len(one["edges"]) + 6
    assert {impl().state for impl in pilot.IMPLS.values()} == {"change2"}


# ---------------------------------------------------------------- measurement protocol

@pytest.mark.parametrize("name", ["B", "A"])
def test_no_wrong_identification_and_consumers_agree(name, fixture_paths, expected):
    impl = pilot.IMPLS[name]()
    p = impl.project(fixture_paths.read(overlay=impl.state == "change2"))
    assert pilot.forbidden_violations(impl, p, expected) == []
    assert pilot.disagreements(impl, p, expected) == []


def test_defects_are_counted_by_cause_not_by_query(fixture_paths, expected):
    """One wrong fact (A8.generated_by) breaks five queries but is one defect type."""
    impl = pilot.IMPLS["B"]()
    p = impl.project(fixture_paths.read(overlay=impl.state == "change2"))
    a8 = "art:req_q3/task_q3scan_ag_seqtool/outputs/scan_extra.tsv"
    p.artifacts[a8]["generated_by"] = "run:task_q3scan_ag_seqtool/task_q3scan"
    p.artifacts[a8]["unknown"].pop("generated_by")
    scored = pilot.score_queries(expected, impl.state, pilot.run_queries(impl, p, expected))
    assert sorted(q for q, v in scored.items() if not v["ok"]) == ["q04", "q05", "q10", "q11", "q17"]
    assert sorted(pilot.defect_causes(impl, p, expected, impl.state, scored)) == ["gen"]
    assert pilot.forbidden_violations(impl, p, expected) == ["F11"]


# ---------------------------------------------------------------- traversal limits and edge cases

@pytest.mark.parametrize("name", ["B", "A"])
def test_cycle_ends_with_a_caution(name):
    check = pilot.traversal_check(pilot.IMPLS[name](), 3, cycle=True)
    assert check["terminated"] and check["cautions"] == ["cycle"]


@pytest.mark.parametrize("name", ["B", "A"])
def test_depth_limit_ends_with_a_caution(name):
    check = pilot.traversal_check(pilot.IMPLS[name](), 70, cycle=False)
    assert check["cautions"] == ["depth_limit"]
    assert check["edges"] < 70 * 4


@pytest.mark.parametrize("length, cycle", [(3, True), (70, False)])
def test_models_agree_on_cycle_and_depth_limit(length, cycle):
    outs = []
    for name in ("B", "A"):
        impl = pilot.IMPLS[name]()
        p = impl.project(pilot.chain_records(length, cycle=cycle))
        outs.append(pilot.canon(pilot.result_of(impl.audit_lineage(p, artifact=f"art:req_c/t{length - 1}_ws/outputs/o.tsv"))))
    assert outs[0] == outs[1]


@pytest.mark.parametrize("hops, expected_match", [(64, "yes"), (65, "unknown"), (66, "unknown")])
def test_derived_from_depth_limit_counts_artifact_hops(hops, expected_match):
    """#212: A and B accept at most 64 artifact-to-artifact lineage hops."""
    root = f"art:req_c/t{hops}_ws/outputs/o.tsv"
    target = "art:req_c/t0_ws/outputs/o.tsv"
    matches = {}
    for name in ("B", "A"):
        impl = pilot.IMPLS[name]()
        p = impl.project(pilot.chain_records(hops + 1))
        out = pilot.result_of(impl.find_reusable(p, derived_from=target))
        matches[name] = out["candidates"][root]["match"]["derived_from"]
    assert matches == {"B": expected_match, "A": expected_match}


def _records(rid, steps, tasks):
    """In-memory records of one request: steps (id, input_refs, outputs) and their tasks (task id, step id)."""
    from labhq.research.semantics import Records
    plan = {"steps": [{"id": sid, "input_refs": refs, "outputs": outs} for sid, refs, outs in steps],
            "protocol": {"packs": []}}
    outputs = {sid: outs for sid, _refs, outs in steps}
    rows = {tid: {"request_id": rid, "step_id": sid, "kind": "step", "attempt": 1, "revision": 0, "parent_task": None,
                  "payload": {"agent_id": "ag_x", "resume_session_id": None, "meta": {}},
                  "result": {"workdir": f"workspaces/{rid}/{tid}_ws", "workdir_id": f"{tid}_ws",
                             "outputs": list(outputs[sid]), "session_id": f"sess_{tid}", "pending_jobs": [],
                             "pending_asks": [], "provenance": {"runs": {tid: {"started_at": float(i)}}}}}
            for i, (tid, sid) in enumerate(tasks)}
    request = {"plan": plan, "research_contract": {"plan_sha256": "0" * 64}}
    return Records(requests={rid: request}, tasks=rows, plans={rid: plan}, results={},
                   manifests={row["result"]["workdir"]: None for row in rows.values()}, observed={}, contracts={})


def two_entry_cycle(inputs):
    """#154: run r uses s and a; run d reports s and a and uses s again (lineage_step r→s, r→a, a→d, d→s, s→d)."""
    refs = {"s": "step:d/outputs/s.tsv", "a": "step:d/outputs/a.tsv"}
    return _records("req_y", [("d", [refs["s"]], ["outputs/s.tsv", "outputs/a.tsv"]),
                              ("r", [refs[n] for n in inputs], ["outputs/x.tsv"])], [("td", "d"), ("tr", "r")])


@pytest.mark.parametrize("inputs", [("s", "a"), ("a", "s")], ids=["s_first", "a_first"])
def test_a_cycle_with_two_entries_is_one_caution_naming_its_component(inputs):
    """#154: one cycle caution per strongly connected component, whatever order the walk enters it from."""
    outs = {}
    for name in ("B", "A"):
        impl = pilot.IMPLS[name]()
        p = impl.project(two_entry_cycle(inputs))
        outs[name] = pilot.result_of(impl.audit_lineage(p, artifact="art:req_y/tr_ws/outputs/x.tsv"))
    for out in outs.values():
        assert [c for c in out["cautions"] if c["code"] == "cycle"] == [
            {"code": "cycle", "nodes": ["art:req_y/td_ws/outputs/s.tsv", "run:td_ws/td"]}]
    assert pilot.canon(outs["B"]) == pilot.canon(outs["A"])


DETOUR, UPSTREAM = 20, 45


def detour_merge(direct_first):
    """#157: x uses m directly and through a detour of DETOUR steps; m sits on a chain of UPSTREAM steps.

    A walk that keeps the depth of its first visit reaches m deep through the detour and cuts the chain above
    it, though the direct path leaves the whole chain inside the depth limit."""
    steps = [(f"u{j}", [f"step:u{j - 1}/outputs/o.tsv"] if j else ["synth:DS-9000@r1"], ["outputs/o.tsv"])
             for j in range(UPSTREAM + 1)]
    steps.append(("m", [f"step:u{UPSTREAM}/outputs/o.tsv"], ["outputs/o.tsv"]))
    steps += [(f"l{k}", [f"step:{'m' if k == 0 else f'l{k - 1}'}/outputs/o.tsv"], ["outputs/o.tsv"])
              for k in range(DETOUR)]
    refs = ["step:m/outputs/o.tsv", f"step:l{DETOUR - 1}/outputs/o.tsv"]
    steps.append(("x", refs if direct_first else refs[::-1], ["outputs/x.tsv"]))
    return _records("req_m", steps, [(f"t{sid}", sid) for sid, _refs, _outs in steps])


@pytest.mark.parametrize("direct_first", [True, False], ids=["direct_first", "detour_first"])
def test_a_shorter_path_into_a_merge_is_walked_at_its_own_depth(direct_first):
    """#157: derived_from and audit_lineage use each node's least depth, not the depth of the first visit."""
    root, target = "art:req_m/tx_ws/outputs/x.tsv", "art:req_m/tu0_ws/outputs/o.tsv"
    outs = {}
    for name in ("B", "A"):
        impl = pilot.IMPLS[name]()
        p = impl.project(detour_merge(direct_first))
        found = pilot.result_of(impl.find_reusable(p, derived_from=target))
        assert found["candidates"][root]["match"]["derived_from"] == "yes", name
        outs[name] = pilot.result_of(impl.audit_lineage(p, artifact=root))
    assert pilot.canon(outs["B"]) == pilot.canon(outs["A"])
    walked = {(e["src"], e["dst"]) for e in outs["B"]["edges"] if e["rel"] == "used"}
    assert ("run:tu28_ws/tu28", "art:req_m/tu27_ws/outputs/o.tsv") in walked   # depth 39 by the direct path, 79 by the detour


def test_diamond_lineage_is_walked_by_node_not_by_path():
    """#140: 20 layers that each split into two outputs and merge in the next run (2^19 paths, 60 nodes)."""
    checks = {name: pilot.diamond_check(pilot.IMPLS[name](), 20) for name in ("B", "A")}
    for check in checks.values():
        assert check["seconds"] < 1.0
        # per run: performs, uses_method, two used (one at the first layer); two reported_output per layer below
        assert (check["edges"], check["cautions"]) == (6 * 20 - 2, [])
    assert pilot.canon(checks["B"]["result"]) == pilot.canon(checks["A"]["result"])


def edge_case_records():
    """A resume of a session no earlier run used, and one output reported under two spellings."""
    records = pilot.chain_records(2)
    tasks = dict(records.tasks)
    tasks["t1"] = {**tasks["t1"], "payload": {**tasks["t1"]["payload"], "resume_session_id": "sess_gone"}}
    tasks["t0"] = {**tasks["t0"], "result": {**tasks["t0"]["result"], "outputs": ["outputs/o.tsv", "./outputs/o.tsv"]}}
    return type(records)(**{**records.__dict__, "tasks": tasks})


def test_models_agree_on_edge_cases_outside_the_17_queries():
    outs, states = {}, set()
    for name in ("B", "A"):
        impl = pilot.IMPLS[name]()
        states.add(impl.state)
        p = impl.project(edge_case_records())
        outs[name] = {"run": pilot.result_of(impl.audit_lineage(p, run="run:t1_ws/t1")),
                      "art": pilot.result_of(impl.audit_lineage(p, artifact="art:req_c/t0_ws/outputs/o.tsv"))}
    for key in ("run", "art"):
        if len(states) > 1:   # mid-change: drop the fields the two changes move, compare the rest
            for out in (outs["B"][key], outs["A"][key]):
                out.pop("uses", None)
                out["node"].pop("data_type", None)
                (out["node"].get("unknown") or {}).pop("data_type", None)
        assert pilot.canon(outs["B"][key]) == pilot.canon(outs["A"][key])
    node = outs["B"]["run"]["node"]
    assert (node["resumes"], node["unknown"]["resumes"]) == ("unknown", "no_matching_session")
    assert outs["B"]["art"]["node"]["reported_by"] == ["run:t0_ws/t0"]


def resume_in_one_workspace(peer_started: bool, own_started: bool = True):
    """t1 resumes t0's session in t0's workspace; without peer_started t0 left no started_at anywhere (#137),
    without own_started t1 did not."""
    records = pilot.chain_records(2)
    t0, t1 = copy.deepcopy(records.tasks["t0"]), copy.deepcopy(records.tasks["t1"])
    if not peer_started:
        t0["result"]["provenance"] = {"runs": {}}
    if not own_started:
        t1["result"]["provenance"] = {"runs": {}}
    t1["payload"]["resume_session_id"] = "sess_t0"
    t1["result"].update(workdir=t0["result"]["workdir"], workdir_id="t0_ws", outputs=["outputs/o1.tsv"])
    return type(records)(**{**records.__dict__, "tasks": {"t0": t0, "t1": t1}})


@pytest.mark.parametrize("peer_started, own_started, resumes, reason", [
    (True, True, "run:t0_ws/t0", None),
    (False, True, "unknown", "start_unknown"),
    (True, False, "unknown", "start_unknown"),
])
def test_resume_with_an_unknown_peer_start_is_unknown_not_an_error(peer_started, own_started, resumes, reason):
    """#137: a run whose started_at is unknown drops out of the comparison instead of raising TypeError.
    #155: a matching session whose order cannot be judged says so, never "no matching session"."""
    outs = {}
    for name in ("B", "A"):
        impl = pilot.IMPLS[name]()
        p = impl.project(resume_in_one_workspace(peer_started, own_started))
        outs[name] = pilot.result_of(impl.audit_lineage(p, run="run:t0_ws/t1"))
    node = outs["B"]["node"]
    assert (node["resumes"], (node.get("unknown") or {}).get("resumes")) == (resumes, reason)
    if reason:
        assert node["candidates"]["resumes"] == ["run:t0_ws/t0"]
        gap = next(g for g in outs["B"]["gaps"] if g["field"] == "resumes")
        assert (gap["reason"], gap["candidates"]) == ("start_unknown", ["run:t0_ws/t0"])
    assert pilot.canon(outs["B"]) == pilot.canon(outs["A"])


K2 = "claim:req_q2/K2@1"
Q2DE_RUNS = ("run:task_q2de_ag_scde/task_q2de", "run:task_q2de_ag_scde/task_q2de2")


def _add_q2de_retry(paths):
    """task_q2de's result reported again by an attempt 2 task of the same request, as task_q4qc2 retries
    task_q4qc: same workspace, resumed session. The retry's E2 carries its own independence group."""
    import sqlite3
    db = sqlite3.connect(paths.state_db)
    body = json.loads(db.execute("SELECT body FROM state WHERE kind = 'task' AND key = 'task_q2de'").fetchone()[0])
    body["attempt"] = body["payload"]["meta"]["attempt"] = 2
    body["payload"].update(id="task_q2de2", resume_session_id="sess_q2de")
    result = body["result"]
    result["task_id"] = "task_q2de2"
    result["provenance"]["runs"] = {"task_q2de2": {**result["provenance"]["runs"]["task_q2de"],
                                                   "started_at": 1790036000.0, "ended_at": 1790036600.0}}
    for ev in result["structured"]["evidence"]:
        if ev["id"] == "E2":
            ev["independence_group"] = "g_q2_de_retry"
    db.execute("INSERT INTO state VALUES ('task', 'task_q2de2', ?)", (json.dumps(body),))
    db.commit()
    db.close()


def claim_reported_twice(name):
    impl = pilot.IMPLS[name]()
    with pilot.fixture_copy() as paths:
        _add_q2de_retry(paths)
        records = paths.read(overlay=impl.state == "change2")
    return pilot.result_of(impl.audit_lineage(impl.project(records), claim=K2))


def test_baseline_reads_a_claim_revision_reported_twice():
    """#138: the claim row is stored once and every reporting run is kept, instead of an IntegrityError."""
    out = claim_reported_twice("A")
    assert out["bears_on"]["supports"] == sorted(f"ev:task_q2de_ag_scde/{run.rsplit('/', 1)[1]}/{ev}"
                                                 for run in Q2DE_RUNS for ev in ("E1r", "E2"))
    assert out["independent_groups"] == ["g_q1_de", "g_q2_de", "g_q2_de_retry"]


def test_independent_groups_cover_every_result_that_reported_the_claim():
    """#139: bears_on and independent_groups name the same reporting results; B used to judge the last one only."""
    outs = {name: claim_reported_twice(name) for name in ("B", "A")}
    supports = outs["B"]["bears_on"]["supports"]
    assert sorted({ev.split("/")[1] for ev in supports}) == ["task_q2de", "task_q2de2"]
    assert outs["B"]["independent_groups"] == ["g_q1_de", "g_q2_de", "g_q2_de_retry"]
    assert pilot.canon(outs["B"]) == pilot.canon(outs["A"])


# ---------------------------------------------------------------- shared reader

def test_reader_refuses_a_missing_database_without_creating_it(tmp_path):
    from labhq.research.semantics import RecordsError, read_records
    with pytest.raises(RecordsError, match="not found"):
        read_records(tmp_path, state_db=tmp_path / "state.db")
    assert list(tmp_path.iterdir()) == []


def _rewrite_task(paths, task_id, change):
    import sqlite3
    db = sqlite3.connect(paths.state_db)
    body = json.loads(db.execute("SELECT body FROM state WHERE kind = 'task' AND key = ?", (task_id,)).fetchone()[0])
    db.execute("UPDATE state SET body = ? WHERE kind = 'task' AND key = ?", (change(body), task_id))
    db.commit()
    db.close()


def _duplicate_key(body):
    return json.dumps(body)[:-1] + ', "attempt": 2}'


def _escaping_workdir(body):
    body["result"]["workdir"] = "../outside"
    return json.dumps(body)


def _foreign_plan_hash(body):
    body["result"]["structured"]["plan_sha256"] = "0" * 64
    return json.dumps(body)


@pytest.mark.parametrize("breakage, message", [
    ("duplicate_key", "duplicate key"),
    ("escaping_workdir", "not inside the records root"),
    ("broken_manifest", "invalid JSON"),
    ("foreign_plan_hash", "research result invalid"),
    ("overlay_unknown_task", "unknown task"),
])
def test_reader_rejects_broken_records(breakage, message):
    from labhq.research.semantics import RecordsError
    with pilot.fixture_copy() as paths:
        if breakage == "duplicate_key":
            _rewrite_task(paths, "task_q1fetch", _duplicate_key)
        elif breakage == "escaping_workdir":
            _rewrite_task(paths, "task_q1fetch", _escaping_workdir)
        elif breakage == "broken_manifest":
            next(paths.root.rglob("manifest.json")).write_text("{not json", encoding="utf-8")
        elif breakage == "foreign_plan_hash":
            _rewrite_task(paths, "task_q2de", _foreign_plan_hash)
        else:
            paths.overlay.write_text(json.dumps({"task_meta": {"task_nope": {}}}), encoding="utf-8")
        with pytest.raises(RecordsError, match=message) as caught:
            paths.read(overlay=True)
        assert str(paths.root) not in str(caught.value) and paths.root.as_posix() not in str(caught.value)


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_reading_and_answering_leaves_every_file_as_it_was(expected):
    fixture_before = _snapshot(FIXTURE)
    with pilot.fixture_copy() as paths:
        copy_before = _snapshot(paths.root)
        for name in ("B", "A"):
            impl = pilot.IMPLS[name]()
            pilot.run_queries(impl, impl.project(paths.read(overlay=True)), expected)
        assert _snapshot(paths.root) == copy_before   # no -wal/-shm or other side files
    assert _snapshot(FIXTURE) == fixture_before


def test_reader_shares_a_live_wal_and_cannot_write(tmp_path):
    import sqlite3
    from labhq.research.semantics import _open_state_readonly
    path = tmp_path / "state.db"
    pilot.write_state_db(path, [{"kind": "task", "key": "t", "body": {}}])
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO state VALUES ('task', 'u', '{}')")
    writer.commit()
    try:
        reader = _open_state_readonly(path)
        assert [r[0] for r in reader.execute("SELECT key FROM state ORDER BY key")] == ["t", "u"]
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("INSERT INTO state VALUES ('task', 'v', '{}')")
        reader.close()
    finally:
        writer.close()


def _wal_db_without_wal(path):
    """A WAL-mode state DB whose last connection closed, so no -wal or -shm file is beside it."""
    import sqlite3
    pilot.write_state_db(path, [{"kind": "task", "key": "t", "body": {}}])
    setup = sqlite3.connect(path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.close()
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_reader_leaves_no_side_files_beside_a_wal_db_without_a_writer(tmp_path):
    from labhq.research.semantics import read_records
    path = tmp_path / "state.db"
    _wal_db_without_wal(path)
    before = path.read_bytes()
    assert sorted(read_records(tmp_path, state_db=path).tasks) == ["t"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.db"] and path.read_bytes() == before


@pytest.mark.parametrize("when", ["before_open", "after_open"])
def test_reader_does_not_miss_a_writer_that_starts_around_the_open(tmp_path, monkeypatch, when):
    """#141: with no -wal the reader opens immutable (no locks); a writer that commits just before or just
    after that open keeps its commit in -wal, which the immutable read cannot see. The read is redone."""
    import sqlite3
    from labhq.research.semantics import read_records
    path = tmp_path / "state.db"
    _wal_db_without_wal(path)
    real_connect, writers = sqlite3.connect, []

    def start_writer():
        if not writers:
            writer = real_connect(path)
            writer.execute("INSERT INTO state VALUES ('task', 'u', '{}')")
            writer.commit()
            writers.append(writer)   # kept open: the commit stays in -wal, the main file is unchanged

    def connect(*args, **kwargs):
        if when == "before_open":
            start_writer()
        db = real_connect(*args, **kwargs)
        if when == "after_open":
            start_writer()
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        assert sorted(read_records(tmp_path, state_db=path).tasks) == ["t", "u"]
    finally:
        for writer in writers:
            writer.close()


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _RacedRead:
    """A read-only connection whose query is followed, before _read_state checks again, by ``after()``."""

    def __init__(self, db, after):
        self.db, self.after = db, after

    def execute(self, sql):
        rows = self.db.execute(sql).fetchall()
        self.after()
        return _Rows(rows)

    def close(self):
        self.db.close()


def _wrap_reader(monkeypatch, wrap):
    """Wrap the first ``times`` opens of _open_state_readonly; record each open's immutable flag."""
    from labhq.research import semantics
    real, opened = semantics._open_state_readonly, []

    def opener(path, *, immutable=None):
        db = real(path, immutable=immutable)
        opened.append(immutable)
        return wrap(db, len(opened))

    monkeypatch.setattr(semantics, "_open_state_readonly", opener)
    return opened


def _commit_and_close(path, key):
    """A writer that commits and closes: its -wal is checkpointed and removed, the main file grows."""
    import sqlite3
    writer = sqlite3.connect(path)
    writer.execute("INSERT INTO state VALUES ('task', ?, ?)", (key, json.dumps({"pad": "x" * 20000})))
    writer.commit()
    writer.close()


def test_reader_reads_again_when_a_writer_commits_and_closes_after_the_immutable_read(tmp_path, monkeypatch):
    """#156: no -wal is left to see, only the changed main file; the stamp check sends the read round again."""
    from labhq.research.semantics import read_records
    path = tmp_path / "state.db"
    _wal_db_without_wal(path)
    opened = _wrap_reader(monkeypatch, lambda db, n: _RacedRead(db, lambda: _commit_and_close(path, "u"))
                          if n == 1 else db)
    assert sorted(read_records(tmp_path, state_db=path).tasks) == ["t", "u"]
    assert opened == [True, True] and sorted(p.name for p in tmp_path.iterdir()) == ["state.db"]


def test_reader_gives_up_when_a_writer_keeps_changing_the_file(tmp_path, monkeypatch):
    from labhq.research.semantics import RecordsError, read_records
    path = tmp_path / "state.db"
    _wal_db_without_wal(path)
    opened = _wrap_reader(monkeypatch, lambda db, n: _RacedRead(db, lambda: _commit_and_close(path, f"u{n}")))
    with pytest.raises(RecordsError, match="kept changing"):
        read_records(tmp_path, state_db=path)
    assert opened == [True, True, True]


class _TornRead(_RacedRead):
    def execute(self, sql):
        import sqlite3
        raise sqlite3.DatabaseError("database disk image is malformed")


def test_reader_retries_an_immutable_read_that_fails_and_reports_a_shared_one(tmp_path, monkeypatch):
    """#156: an sqlite error in an immutable read may be a page torn by a writer that started meanwhile, so
    the read is redone; the same error with a writer's -wal shared is reported at once."""
    from labhq.research.semantics import RecordsError, read_records
    path = tmp_path / "state.db"
    _wal_db_without_wal(path)
    opened = _wrap_reader(monkeypatch, lambda db, n: _TornRead(db, None) if n == 1 else db)
    assert sorted(read_records(tmp_path, state_db=path).tasks) == ["t"]
    assert opened == [True, True]
    import sqlite3
    writer = sqlite3.connect(path)
    writer.execute("INSERT INTO state VALUES ('task', 'w', '{}')")
    writer.commit()   # kept open: its -wal is beside the file, so the read shares it (not immutable)
    try:
        opened = _wrap_reader(monkeypatch, lambda db, n: _TornRead(db, None))
        with pytest.raises(RecordsError, match="cannot read state rows"):
            read_records(tmp_path, state_db=path)
        assert opened == [False]
    finally:
        writer.close()


# ---------------------------------------------------------------- isolation from the execution path

ROOT = FIXTURE.parents[2]
SEMANTIC_FILES = [ROOT / "labhq" / "research" / "semantics.py", ROOT / "tests" / "semantics_baseline.py"]
IMPORT_ALLOW = {"__future__", "copy", "hashlib", "json", "posixpath", "re", "sqlite3", "collections.abc",
                "dataclasses", "pathlib", "typing", "urllib.parse", "yaml", "pydantic", "labhq.evidence",
                "labhq.evidence.claims", "labhq.research.contract", "labhq.research.semantics",
                "labhq.yaml_unique",  # the duplicate-key YAML loader, shared with core vocab code (#221)
                "labhq.vocab"}  # the output type vocabulary and its one reader (#221)


def _imported_names(path):
    import ast
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            names |= {base} | {f"{base}.{alias.name}" for alias in node.names}
    return names


SHADOW_MODULES = {"labhq/research/semantics.py", "labhq/research/semantics_shadow.py",
                  "labhq/research/semantics_objects.py"}  # the model and its #150 shadow worker


def test_existing_modules_reach_the_model_only_through_a_lazy_marked_hook():
    """No module imports the model at load time. The gateway and CLI may import the #150 shadow, but only
    inside a function, on a `# semantics-hook` line that the removal script deletes."""
    import ast
    offenders = []
    for path in (ROOT / "labhq").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in SHADOW_MODULES:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source)
        lazy = {id(n) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                for n in ast.walk(f)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = ["." * node.level + (node.module or "")] + [alias.name for alias in node.names]
            else:
                continue
            if any("semantics" in name for name in names) and not (
                    id(node) in lazy and "# semantics-hook" in lines[node.lineno - 1]
                    and all("semantics_shadow" in name or "semantics" not in name for name in names)):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


@pytest.mark.parametrize("path", SEMANTIC_FILES, ids=lambda p: p.name)
def test_pilot_imports_only_the_allowlist(path):
    import ast
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert modules <= IMPORT_ALLOW, modules - IMPORT_ALLOW


SCRIPT = ROOT / "scripts" / "semantics_pilot.py"
SCRIPT_ALLOW = IMPORT_ALLOW | {"argparse", "ast", "contextlib", "importlib", "sys", "tempfile", "time", "tracemalloc"}
GUARD_ONLY = {"asyncio", "os", "socket", "subprocess"}   # the script imports these only to block them
DYNAMIC_ALLOW = {"labhq.research.semantics", "tests.semantics_baseline"}


def _dotted(node):
    import ast
    if isinstance(node, ast.Name):
        return node.id
    return f"{_dotted(node.value)}.{node.attr}" if isinstance(node, ast.Attribute) else ""


def script_import_problems(source):
    """Imports of the measurement script outside its allowlist (#142). Process and network modules may be
    imported only inside no_network_or_subprocess; a dynamic import must name an allowed module literally."""
    import ast
    tree = ast.parse(source)
    guard = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "no_network_or_subprocess")
    guarded = {id(n) for n in ast.walk(guard)}
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in ("importlib.import_module", "__import__"):
            arg = node.args[0] if node.args else None
            if not (isinstance(arg, ast.Constant) and arg.value in DYNAMIC_ALLOW):
                problems.append(f"dynamic import line {node.lineno}")
            continue
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = ["." * node.level + (node.module or "")]
        else:
            continue
        for name in names:
            if name in GUARD_ONLY and id(node) not in guarded:
                problems.append(f"{name} outside the guard, line {node.lineno}")
            elif name not in GUARD_ONLY and name not in SCRIPT_ALLOW:
                problems.append(f"{name} line {node.lineno}")
    return problems


def test_script_imports_only_the_allowlist():
    assert script_import_problems(SCRIPT.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("edit", [
    lambda s: s.replace("import argparse\n", "import argparse\nimport urllib.request\n", 1),
    lambda s: s.replace("import argparse\n", "import argparse\nimport subprocess\n", 1),
    lambda s: s.replace("def main(argv", "def _llm():\n    from labhq.adapters import claude_code\n\n\ndef main(argv", 1),
    lambda s: s.replace('importlib.import_module("tests.semantics_baseline")',
                        'importlib.import_module("labhq." + "adapters")', 1),
], ids=["network_module", "subprocess_outside_guard", "adapter", "computed_dynamic_import"])
def test_script_import_check_catches_a_variant(edit):
    source = SCRIPT.read_text(encoding="utf-8")
    assert edit(source) != source
    assert script_import_problems(edit(source))


def _starter_args(name, bad):
    """Arguments naming a program that does not exist, so an unblocked starter fails instead of running."""
    import os
    if name.startswith("posix_spawn"):
        return (bad, [bad], {})
    family = "spawn" if name.startswith("spawn") else "exec"
    suffix = name[len(family):]
    head = (os.P_WAIT,) if family == "spawn" else ()
    return (*head, bad, *((bad,) if suffix.startswith("l") else ([bad],)), *(({},) if suffix.endswith("e") else ()))


def _process_starters():
    import os
    return sorted(n for n in dir(os) if n.startswith(("spawn", "exec", "posix_spawn")) and callable(getattr(os, n)))


@pytest.mark.parametrize("name", _process_starters())
def test_guard_blocks_every_os_process_starter(tmp_path, name):
    """#142: os.spawn*, os.exec* and os.posix_spawn* start a program without subprocess; the guard refuses them."""
    import os
    original, bad = getattr(os, name), str(tmp_path / "no-such-program")
    with pilot.no_network_or_subprocess():
        with pytest.raises(RuntimeError, match="blocked"):
            getattr(os, name)(*_starter_args(name, bad))
    assert getattr(os, name) is original


def test_guard_blocks_fork_and_shell_helpers():
    import os
    names = [n for n in ("system", "popen", "fork", "forkpty", "startfile") if hasattr(os, n)]
    originals = {n: getattr(os, n) for n in names}
    with pilot.no_network_or_subprocess():
        assert all(getattr(os, n) is not originals[n] for n in names)   # replaced, not called: fork is real
        with pytest.raises(RuntimeError, match="blocked"):
            os.popen("exit 0")
    assert all(getattr(os, n) is originals[n] for n in names)


def _fresh(code):
    import os
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"})
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip().splitlines()[-1]


EXECUTION_PATH_IMPORT = """
import sys
import labhq.cli, labhq.gateway.server, labhq.runner.daemon, labhq.orchestrator.cso, labhq.research
print(sorted(m for m in sys.modules if 'semantics' in m))
"""

CSO_SNAPSHOT_AROUND_THE_PILOT = """
import hashlib, json, sys
import labhq.orchestrator.cso as cso
from labhq.research import RESEARCH_PLAN_SCHEMA, RESEARCH_RESULT_SCHEMA


def snap():
    texts = {k: v for k, v in vars(cso).items() if k.isupper() and isinstance(v, str)}
    blob = json.dumps([texts, RESEARCH_PLAN_SCHEMA, RESEARCH_RESULT_SCHEMA], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


before = snap()
adapters = sorted(m for m in sys.modules if m.startswith('labhq.adapters'))
from scripts import semantics_pilot as pilot
expected = pilot.load_expected()
with pilot.no_network_or_subprocess(), pilot.fixture_copy() as paths:
    for name in ('B', 'A'):
        impl = pilot.IMPLS[name]()
        pilot.run_queries(impl, impl.project(paths.read(overlay=True)), expected)
print(json.dumps([before == snap(), sorted(m for m in sys.modules if m.startswith('labhq.adapters')) == adapters]))
"""


def test_execution_path_imports_do_not_load_the_pilot():
    assert _fresh(EXECUTION_PATH_IMPORT) == "[]"


def test_running_the_pilot_changes_no_cso_prompt_and_loads_no_adapter():
    assert json.loads(_fresh(CSO_SNAPSHOT_AROUND_THE_PILOT)) == [True, True]


def test_pilot_runs_with_network_and_subprocess_blocked(fixture_paths, expected):
    import socket
    with pilot.no_network_or_subprocess():
        with pytest.raises(RuntimeError, match="blocked"):
            socket.create_connection(("127.0.0.1", 9))
        for name in ("B", "A"):
            impl = pilot.IMPLS[name]()
            records = fixture_paths.read(overlay=impl.state == "change2")
            assert len(pilot.run_queries(impl, impl.project(records), expected)) == 17


def test_settings_have_no_semantics_key_but_the_marked_shadow_switch():
    """The model adds no setting. #150 B1 adds one untyped top-level switch, default off, on a hook line."""
    from labhq.settings import ResearchSettings, Settings
    assert "semantics" not in ResearchSettings.model_fields
    field = Settings.model_fields.get("semantics")
    if field is not None:  # gone again after scripts/semantics_shadow_remove.py
        assert field.default is None
        source = (ROOT / "labhq" / "settings.py").read_text(encoding="utf-8").splitlines()
        assert [line for line in source if "semantics" in line and "# semantics-hook" not in line] == []


def test_script_hash_helpers_match_the_pinned_constants():
    assert pilot.inventory_sha256(RECORDS) == inventory_sha256(RECORDS) == FIXTURE_SHA256


def test_reader_accepts_runner_absolute_workdir_under_root():
    # The runner stores result.workdir as str(ws.dir) — an absolute path under workspace_root.
    with pilot.fixture_copy() as paths:
        relative = paths.read(overlay=True)
        original = relative.tasks["task_q1fetch"]["result"]["workdir"]

        def absolute(body):
            body["result"]["workdir"] = str(paths.root.joinpath(*original.replace("\\", "/").split("/")))
            return json.dumps(body)

        _rewrite_task(paths, "task_q1fetch", absolute)
        records = paths.read(overlay=True)
        stored = records.tasks["task_q1fetch"]["result"]["workdir"]
        assert records.manifests[stored] == relative.manifests[original] is not None


def test_reader_rejects_absolute_workdir_outside_root():
    from labhq.research.semantics import RecordsError

    with pilot.fixture_copy() as paths:
        def outside(body):
            body["result"]["workdir"] = str(paths.root.parent / "elsewhere" / "run")
            return json.dumps(body)

        _rewrite_task(paths, "task_q1fetch", outside)
        with pytest.raises(RecordsError, match="not inside the records root"):
            paths.read(overlay=True)
