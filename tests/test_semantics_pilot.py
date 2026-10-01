"""Semantics pilot (#127): fixed expected answers, model B, baseline A and their isolation."""

from __future__ import annotations

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
FIXTURE_SHA256 = "24dce1ed83a06b78d000606111a785e6eb388e0c39c262ecad4442b1700cafeb"


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


MODEL_SHA256 = "0308ed44c1bd80f2d7750d5ca4f97c5040547b5a1bc6ca0565463365926828ce"


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
    p = impl.project(fixture_paths.read(overlay=False))
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
        if len(states) > 1:   # mid-change the two answer different reuse scopes; compare the rest
            for out in (outs["B"][key], outs["A"][key]):
                out.pop("uses", None)
        assert pilot.canon(outs["B"][key]) == pilot.canon(outs["A"][key])
    node = outs["B"]["run"]["node"]
    assert (node["resumes"], node["unknown"]["resumes"]) == ("unknown", "no_matching_session")
    assert outs["B"]["art"]["node"]["reported_by"] == ["run:t0_ws/t0"]


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


# ---------------------------------------------------------------- isolation from the execution path

ROOT = FIXTURE.parents[2]
SEMANTIC_FILES = [ROOT / "labhq" / "research" / "semantics.py", ROOT / "tests" / "semantics_baseline.py"]
IMPORT_ALLOW = {"__future__", "copy", "hashlib", "json", "posixpath", "re", "sqlite3", "collections.abc",
                "dataclasses", "pathlib", "typing", "urllib.parse", "yaml", "pydantic", "labhq.evidence",
                "labhq.evidence.claims", "labhq.research.contract", "labhq.research.semantics"}


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


def test_existing_modules_never_import_the_pilot():
    offenders = [path.relative_to(ROOT).as_posix() for path in (ROOT / "labhq").rglob("*.py")
                 if not (path.name == "semantics.py" and path.parent.name == "research")
                 and any("semantics" in name for name in _imported_names(path))]
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


def test_settings_have_no_semantics_key():
    from labhq.settings import ResearchSettings, Settings
    assert "semantics" not in ResearchSettings.model_fields and "semantics" not in Settings.model_fields


def test_script_hash_helpers_match_the_pinned_constants():
    assert pilot.inventory_sha256(RECORDS) == inventory_sha256(RECORDS) == FIXTURE_SHA256
