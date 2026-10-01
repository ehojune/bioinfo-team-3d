"""Semantics pilot (#127): score model B and baseline A against the fixed expected answers.

python scripts/semantics_pilot.py            # score and measure, print a Markdown report
python scripts/semantics_pilot.py --json     # the same as JSON

Opt-in experiment only. Reads the synthetic fixture under tests/fixtures/semantics, copies it into a
temporary directory (the SQLite state is built there), and never touches a real state_dir.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "semantics"
RECORDS = FIXTURE / "records"
STATES = ("base", "change1", "change2")


# ---------------------------------------------------------------- fixture

def inventory_sha256(root: Path) -> str:
    """sha256 over 'relative posix path LF file sha256 LF' for every file under root, in path order."""
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        digest.update(f"{path.relative_to(root).as_posix()}\n{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
                      .encode())
    return digest.hexdigest()


def load_expected(path: Path = FIXTURE / "expected.yaml") -> dict:
    from labhq.research.semantics import load_yaml_unique
    return load_yaml_unique(path.read_text(encoding="utf-8"), path.name)


def write_state_db(path: Path, rows: list[dict]) -> None:
    """Build a gateway-shaped state DB (same table as labhq.store.StateStore) from fixture rows."""
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE state (kind TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, "
                   "PRIMARY KEY(kind, key))")
        db.executemany("INSERT INTO state VALUES (?, ?, ?)",
                       [(r["kind"], r["key"], json.dumps(r["body"], ensure_ascii=False)) for r in rows])
        db.commit()
    finally:
        db.close()


@dataclass(frozen=True)
class FixturePaths:
    root: Path
    state_db: Path
    observed: Path
    registry: Path
    overlay: Path

    def read(self, *, overlay: bool) -> Any:
        from labhq.research.semantics import read_records
        return read_records(self.root, state_db=self.state_db, observed=self.observed, registry=self.registry,
                            overlay=self.overlay if overlay else None)


def materialize(dst: Path, *, transform: Callable[[str], str] | None = None) -> FixturePaths:
    """Copy the fixture records into ``dst`` with a SQLite state DB. ``transform`` rewrites every text file."""
    def text(src: Path) -> str:
        raw = src.read_text(encoding="utf-8")
        return transform(raw) if transform else raw

    for src in RECORDS.rglob("*"):
        if src.is_file() and src.name != "state_rows.json":
            out = dst / src.relative_to(RECORDS)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text(src), encoding="utf-8")
    rows = json.loads(text(RECORDS / "state_rows.json"))["rows"]
    write_state_db(dst / "state.db", rows)
    return FixturePaths(root=dst, state_db=dst / "state.db", observed=dst / "observed.json",
                        registry=dst / "agents", overlay=dst / "change2_overlay.json")


@contextmanager
def fixture_copy(transform: Callable[[str], str] | None = None) -> Iterator[FixturePaths]:
    with tempfile.TemporaryDirectory(prefix="semantics-pilot-") as tmp:
        yield materialize(Path(tmp), transform=transform)


# ---------------------------------------------------------------- implementations

@dataclass(frozen=True)
class Impl:
    name: str
    project: Callable[[Any], Any]
    find_reusable: Callable[..., Any]
    audit_lineage: Callable[..., Any]
    state: str   # which expected state this implementation answers now


def impl_b() -> Impl:
    semantics = importlib.import_module("labhq.research.semantics")
    return Impl("B", lambda records: semantics.project(semantics.load_model(), records),
                semantics.find_reusable, semantics.audit_lineage, state=semantics.PILOT_STATE)


def impl_a() -> Impl:
    baseline = importlib.import_module("tests.semantics_baseline")
    return Impl("A", baseline.project, baseline.find_reusable, baseline.audit_lineage, state=baseline.PILOT_STATE)


IMPLS: dict[str, Callable[[], Impl]] = {"B": impl_b, "A": impl_a}


def result_of(answer: Any) -> dict:
    return answer.result if hasattr(answer, "result") else answer


# ---------------------------------------------------------------- expected states

def expected_state(expected: dict, state: str) -> dict:
    """nodes, edges and query answers after applying the change diffs up to ``state``."""
    nodes = copy.deepcopy(expected["nodes"])
    edges = copy.deepcopy(expected["edges"])
    answers = {q: copy.deepcopy(v["answer"]) for q, v in expected["queries"].items()}
    for name in STATES[1:STATES.index(state) + 1]:
        change = expected["changes"][name]
        for diff in change.get("nodes", {}).get("uses", []):
            use = next(u for u in nodes["uses"] if u["edge"] == diff["edge"])
            use.update({k: v for k, v in diff.items() if k != "edge"})
        for diff in change.get("nodes", {}).get("artifacts", []):
            row = next(a for a in nodes["artifacts"] if a["id"] == diff["id"])
            row["data_type"] = diff["data_type"]
            reasons = dict(row.get("unknown") or {})
            reasons.pop("data_type", None)
            reasons.update(diff.get("unknown") or {})
            row["unknown"] = reasons
        edges.update(change.get("edges_added") or {})
        for q, answer in (change.get("queries") or {}).items():
            answers[q].update(copy.deepcopy(answer))
    return {"nodes": nodes, "edges": edges, "answers": answers}


def canon(value: Any) -> Any:
    """Comparison form: lists sorted, empty unknown/candidates dropped, provenance-only keys dropped."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("rows", "edge"):
                continue
            if key in ("unknown", "candidates") and not item:
                continue
            out[key] = canon(item)
        return out
    if isinstance(value, (list, tuple)):
        items = [canon(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return value


def edge_key(edge: dict) -> tuple:
    return (edge["src"], edge["rel"], edge["dst"], edge.get("value"))


def edge_ids(edges: list[dict], table: dict[str, dict]) -> list[str]:
    """Map output edges onto expected edge ids; a wrong basis/unknown or an unknown edge maps to '?…'."""
    index = {edge_key(e): (eid, e) for eid, e in table.items()}
    out = []
    for edge in edges:
        hit = index.get(edge_key(edge))
        if hit and canon({k: v for k, v in hit[1].items() if k not in ("rows",)}) == canon(edge):
            out.append(hit[0])
        else:
            out.append("?" + "|".join(str(x) for x in edge_key(edge)))
    return sorted(out)


def candidate_expected(state: dict, art: str, answer: dict) -> dict:
    row = next(a for a in state["nodes"]["artifacts"] if a["id"] == art)
    uses = [u for u in state["nodes"]["uses"] if u["artifact"] == art]
    return canon({**row, "uses": uses, **answer})


def query_expected(expected: dict, state: dict, qid: str) -> dict:
    """The full expected output of one query in comparison form (edge lists as ids)."""
    call = expected["queries"][qid]["call"]
    answer = copy.deepcopy(state["answers"][qid])
    if call["consumer"] == "find_reusable":
        answer["candidates"] = {art: candidate_expected(state, art, c) for art, c in answer["candidates"].items()}
        return canon(answer)
    kind = next(k for k in ("claim", "evidence", "artifact", "run", "step") if k in call)
    answer["root"] = call[kind]
    if kind == "artifact":
        answer["node"] = next(a for a in state["nodes"]["artifacts"] if a["id"] == call[kind])
    if kind == "run":
        answer["node"] = next(r for r in state["nodes"]["runs"] if r["id"] == call[kind])
        answer["uses"] = [u for u in state["nodes"]["uses"] if u["run"] == call[kind]]
    return canon(answer)


def query_actual(result: dict, expected_answer: dict, state: dict) -> dict:
    """Project an output onto the expected answer's fields; edge dicts become expected edge ids."""
    out = {}
    for key in expected_answer:
        value = result.get(key, "<missing>")
        if key in ("edges", "cites", "resumes", "wake_of") and isinstance(value, list):
            value = edge_ids(value, state["edges"])
        out[key] = value
    return canon(out)


def call_args(call: dict) -> dict:
    return {k: v for k, v in call.items() if k != "consumer"}


def run_queries(impl: Impl, projection: Any, expected: dict) -> dict[str, dict]:
    outputs = {}
    for qid, query in expected["queries"].items():
        consumer = impl.find_reusable if query["call"]["consumer"] == "find_reusable" else impl.audit_lineage
        outputs[qid] = result_of(consumer(projection, **call_args(query["call"])))
    return outputs


def score_queries(expected: dict, state_name: str, outputs: dict[str, dict]) -> dict[str, dict]:
    state = expected_state(expected, state_name)
    report = {}
    for qid in expected["queries"]:
        want = query_expected(expected, state, qid)
        got = query_actual(outputs[qid], want, state)
        diff = {k: {"expected": want[k], "actual": got.get(k)} for k in want if got.get(k) != want[k]}
        report[qid] = {"ok": not diff, "diff": diff}
    return report


def concept(node: str) -> str:
    return {"claim": "claim", "ev": "evidence", "art": "artifact", "run": "run", "ext": "external_source",
            "agent": "agent"}.get(str(node).split(":", 1)[0], "")


def _audit(impl: Impl, p: Any, **root: str) -> dict | None:
    try:
        return result_of(impl.audit_lineage(p, **root))
    except KeyError:
        return None


# ---------------------------------------------------------------- forbidden merges, agreement, defects

def forbidden_violations(impl: Impl, p: Any, expected: dict) -> list[str]:
    """Ids of forbidden_merges the implementation commits (wrong identifications)."""
    arts = result_of(impl.find_reusable(p))["candidates"]
    runs = {r["id"]: r for r in expected["nodes"]["runs"]}
    bad: list[str] = []
    for item in expected["forbidden_merges"]:
        kind = item["kind"]
        hit = False
        if kind == "same_entity":
            nodes = item["nodes"]
            c = concept(nodes[0])
            if c == "artifact":
                hit = any(n not in arts for n in nodes)
            elif c == "run":
                hit = any(((_audit(impl, p, run=n) or {}).get("node") or {}).get("id") != n for n in nodes)
            elif c == "external_source":
                picks = [tuple(result_of(impl.find_reusable(p, derived_from=n))["selected"]) for n in nodes]
                hit = len(set(picks)) < len(picks) or not all(picks)
            elif c in ("claim", "evidence"):
                outs = [_audit(impl, p, **{c: n}) for n in nodes]
                if any(o is None or o.get("root") != n for o, n in zip(outs, nodes)):
                    hit = True
                elif c == "claim":
                    sets = [set(sum((o["bears_on"][k] for k in ("supports", "contradicts", "context")), [])) for o in outs]
                    hit = any(sets[i] & sets[j] for i in range(len(sets)) for j in range(i + 1, len(sets)))
                else:
                    keys = [frozenset(map(edge_key, o["edges"])) for o in outs]
                    hit = len(set(keys)) < len(keys)
        elif kind == "edge_absent":
            src, rel, dst = item["edge"]
            out = _audit(impl, p, run=src) or {}
            hit = any((e["src"], e["rel"], e["dst"]) == (src, rel, dst) for e in out.get("edges", []))
            if rel == "used":
                hit = hit or any(u["run"] == src for u in (arts.get(dst) or {}).get("uses", []))
        elif kind == "value_not":
            node = item["node"]
            if concept(node) == "artifact":
                row = arts.get(node) or {}
            elif concept(node) == "run":
                row = (_audit(impl, p, run=node) or {}).get("node") or {}
            else:   # agent: read it through one run it performed
                run = next(r for r, v in runs.items() if v["agent"] == node)
                row = (_audit(impl, p, run=run) or {}).get("agent_verification") or {}
            hit = row.get(item["field"], "<missing>") in item["values"]
        elif kind == "support_not":
            out = _audit(impl, p, claim=item["claim"]) or {}
            hit = item["evidence"] in (out.get("bears_on") or {}).get("supports", [])
        elif kind == "lineage_not":
            out = _audit(impl, p, **{concept(item["root"]): item["root"]}) or {}
            touched = {n for e in out.get("edges", []) for n in (e["src"], e["dst"])}
            hit = bool(touched & set(item["nodes"]))
        elif kind == "same_content_not":
            a, b = item["nodes"]
            hit = b in (arts.get(a) or {}).get("same_content_as", []) or a in (arts.get(b) or {}).get("same_content_as", [])
        elif kind == "match_not":
            call = call_args(expected["queries"][item["query"]]["call"])
            cand = result_of(impl.find_reusable(p, **call))["candidates"].get(item["candidate"]) or {}
            hit = cand.get("match", {}).get(item["field"]) in item["values"]
        else:
            raise ValueError(f"unknown forbidden kind {kind}")
        if hit:
            bad.append(item["id"])
    return bad


def disagreements(impl: Impl, p: Any, expected: dict) -> list[str]:
    """Facts on which find_reusable and audit_lineage disagree (interface.agreement)."""
    arts = result_of(impl.find_reusable(p))["candidates"]
    out: list[str] = []
    for row in expected["nodes"]["artifacts"]:
        aid = row["id"]
        a, b = arts.get(aid) or {}, (_audit(impl, p, artifact=aid) or {}).get("node") or {}
        for name in ("generated_by", "sha256"):
            if (a.get(name), (a.get("unknown") or {}).get(name)) != (b.get(name), (b.get("unknown") or {}).get(name)):
                out.append(f"{aid}.{name}")
    by_artifact = {(u["run"], u["artifact"]): u["reuse_kind"] for c in arts.values() for u in c.get("uses", [])}
    by_run = {}
    for run in expected["nodes"]["runs"]:
        for u in (_audit(impl, p, run=run["id"]) or {}).get("uses", []):
            by_run[(u["run"], u["artifact"])] = u["reuse_kind"]
    for key in sorted(set(by_artifact) | set(by_run)):
        if by_artifact.get(key) != by_run.get(key):
            out.append(f"use {key[0]} -> {key[1]}")
    return out


def _field_diffs(want: dict, got: dict) -> set[str]:
    names = set()
    for name in set(want) | set(got):
        if name in ("unknown", "candidates"):
            for sub in set(want.get(name) or {}) | set(got.get(name) or {}):
                if canon((want.get(name) or {}).get(sub)) != canon((got.get(name) or {}).get(sub)):
                    names.add(sub)
        elif canon(want.get(name)) != canon(got.get(name)):
            names.add(name)
    return names


def defect_causes(impl: Impl, p: Any, expected: dict, state_name: str, scored: dict[str, dict]) -> dict[str, list[str]]:
    """Independent defect types (measurement.defects): cause -> what differs. One cause counts once."""
    rules = expected["measurement"]["defects"]
    state = expected_state(expected, state_name)
    causes: dict[str, list[str]] = {}

    def add(cause: str, what: str) -> None:
        causes.setdefault(cause, []).append(what)

    arts = result_of(impl.find_reusable(p))["candidates"]
    for row in state["nodes"]["artifacts"]:
        got = arts.get(row["id"])
        if got is None:
            add("scope", f"{row['id']} missing")
            continue
        want = canon({**row, "uses": [u for u in state["nodes"]["uses"] if u["artifact"] == row["id"]]})
        got = canon({k: v for k, v in got.items() if k not in ("match", "recommend")})
        for name in _field_diffs(want, got):
            add(rules["node_field_cause"].get(name, "reuse" if name == "uses" else "traverse"), f"{row['id']}.{name}")
    for row in state["nodes"]["runs"]:
        out = _audit(impl, p, run=row["id"]) or {}
        for name in _field_diffs(canon(row), canon(out.get("node") or {})):
            add(rules["node_field_cause"].get(name, "traverse"), f"{row['id']}.{name}")
    edge_cause = rules["edge_rel_cause"]
    for qid, verdict in scored.items():
        for key, diff in verdict["diff"].items():
            if key in ("edges", "cites", "resumes", "wake_of"):
                for eid in set(diff["expected"] or []) ^ set(diff["actual"] or []):
                    rel = (state["edges"][eid]["rel"] if eid in state["edges"] else str(eid).split("|")[1]
                           if "|" in str(eid) else "")
                    add(edge_cause.get(rel, "traverse"), f"{qid}.{key}:{eid}")
    if not causes:   # answers wrong with every node and edge fact right: output or traversal defects
        for qid, verdict in scored.items():
            for key in verdict["diff"]:
                add(rules["answer_field_cause"].get(key, "traverse"), f"{qid}.{key}")
    return causes


# ---------------------------------------------------------------- isolation guard

@contextmanager
def no_network_or_subprocess() -> Iterator[None]:
    """Fail any socket, subprocess or shell use inside the block (LLM-call zero, measured)."""
    import asyncio
    import os
    import socket
    import subprocess

    def refuse(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("semantics pilot: network and subprocess use is blocked")

    saved = [(socket, "socket"), (socket, "create_connection"), (socket, "getaddrinfo"), (subprocess, "Popen"),
             (os, "system"), (asyncio, "create_subprocess_exec"), (asyncio, "create_subprocess_shell")]
    originals = [(mod, name, getattr(mod, name)) for mod, name in saved]
    try:
        for mod, name, _ in originals:
            setattr(mod, name, refuse)
        yield
    finally:
        for mod, name, original in originals:
            setattr(mod, name, original)


# ---------------------------------------------------------------- synthetic shapes for traversal limits

def chain_records(length: int, *, cycle: bool = False) -> Any:
    """In-memory records: run i uses step s{i-1}'s output; with cycle, s0 also uses the last step's output."""
    from labhq.research.semantics import Records
    steps, tasks, manifests = [], {}, {}
    for i in range(length):
        refs = [f"step:s{i - 1}/outputs/o.tsv"] if i else ([f"step:s{length - 1}/outputs/o.tsv"] if cycle
                                                         else ["synth:DS-9000@r1"])
        steps.append({"id": f"s{i}", "input_refs": refs, "outputs": ["outputs/o.tsv"]})
        workdir = f"workspaces/c/t{i}_ws"
        tasks[f"t{i}"] = {"request_id": "req_c", "step_id": f"s{i}", "kind": "step", "attempt": 1, "revision": 0,
                          "parent_task": None, "payload": {"agent_id": "ag_c", "resume_session_id": None, "meta": {}},
                          "result": {"workdir": workdir, "workdir_id": f"t{i}_ws", "outputs": ["outputs/o.tsv"],
                                     "session_id": f"sess_t{i}", "pending_jobs": [], "pending_asks": [],
                                     "provenance": {"runs": {f"t{i}": {"started_at": float(i)}}}}}
        manifests[workdir] = None
    request = {"plan": {"steps": steps, "protocol": {"packs": []}}, "research_contract": {"plan_sha256": "0" * 64}}
    return Records(requests={"req_c": request}, tasks=tasks, plans={"req_c": request["plan"]}, results={},
                   manifests=manifests, observed={}, contracts={})


def traversal_check(impl: Impl, length: int, cycle: bool) -> dict:
    p = impl.project(chain_records(length, cycle=cycle))
    root = f"art:req_c/t{length - 1}_ws/outputs/o.tsv"
    out = result_of(impl.audit_lineage(p, artifact=root))
    return {"terminated": True, "cautions": sorted({c["code"] for c in out["cautions"]}), "edges": len(out["edges"])}


def diamond_records(layers: int) -> Any:
    """In-memory records: run i reports x and y and uses both outputs of run i-1, so the lineage of the
    last x has 2^(layers-1) paths through layers*3 nodes (a DAG, no cycle)."""
    from labhq.research.semantics import Records
    steps, tasks, manifests = [], {}, {}
    outs = ["outputs/x.tsv", "outputs/y.tsv"]
    for i in range(layers):
        refs = [f"step:d{i - 1}/{o}" for o in outs] if i else ["synth:DS-9000@r1"]
        steps.append({"id": f"d{i}", "input_refs": refs, "outputs": outs})
        workdir = f"workspaces/d/t{i}_ws"
        tasks[f"t{i}"] = {"request_id": "req_d", "step_id": f"d{i}", "kind": "step", "attempt": 1, "revision": 0,
                          "parent_task": None, "payload": {"agent_id": "ag_d", "resume_session_id": None, "meta": {}},
                          "result": {"workdir": workdir, "workdir_id": f"t{i}_ws", "outputs": list(outs),
                                     "session_id": f"sess_t{i}", "pending_jobs": [], "pending_asks": [],
                                     "provenance": {"runs": {f"t{i}": {"started_at": float(i)}}}}}
        manifests[workdir] = None
    request = {"plan": {"steps": steps, "protocol": {"packs": []}}, "research_contract": {"plan_sha256": "0" * 64}}
    return Records(requests={"req_d": request}, tasks=tasks, plans={"req_d": request["plan"]}, results={},
                   manifests=manifests, observed={}, contracts={})


def diamond_check(impl: Impl, layers: int) -> dict:
    """Walk the last x of ``diamond_records``; seconds cover audit_lineage only."""
    import time
    p = impl.project(diamond_records(layers))
    start = time.perf_counter()
    out = result_of(impl.audit_lineage(p, artifact=f"art:req_d/t{layers - 1}_ws/outputs/x.tsv"))
    return {"seconds": time.perf_counter() - start, "edges": len(out["edges"]),
            "cautions": sorted({c["code"] for c in out["cautions"]}), "result": out}


# ---------------------------------------------------------------- scale and timing

def scaled_transform(k: int) -> Callable[[str], str]:
    import re
    pattern = re.compile(r"\b(req_q\d+|task_q[0-9a-z]+|sess_q[0-9a-z]+|job_q[0-9a-z_]+)")
    if k == 0:   # copy 0 keeps the original ids so the 17 queries still name real nodes
        return lambda text: text
    return lambda text: pattern.sub(lambda m: f"{m.group(1)}_x{k:02d}", text)


def materialize_scaled(dst: Path, copies: int) -> FixturePaths:
    """``copies`` disjoint replicas of the fixture in one record set (ids suffixed, plan hashes recomputed)."""
    from labhq.research.contract import plan_sha256
    rows: list[dict] = []
    observed: dict[str, dict] = {}
    overlay: dict[str, dict] = {}
    source_rows = (RECORDS / "state_rows.json").read_text(encoding="utf-8")
    for k in range(copies):
        fix = scaled_transform(k)
        text = fix(source_rows)
        replica = json.loads(text)["rows"]
        for row in replica:
            if row["kind"] == "request" and row["body"].get("research_contract"):
                old = row["body"]["research_contract"]["plan_sha256"]
                text = text.replace(old, plan_sha256(row["body"]["plan"]))
        rows += json.loads(text)["rows"]
        observed.update(json.loads(fix((RECORDS / "observed.json").read_text(encoding="utf-8")))["workspaces"])
        overlay.update(json.loads(fix((RECORDS / "change2_overlay.json").read_text(encoding="utf-8")))["task_meta"])
        for src in (RECORDS / "workspaces").rglob("manifest.json"):
            out = dst / fix(src.relative_to(RECORDS).as_posix())
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(fix(src.read_text(encoding="utf-8")), encoding="utf-8")
    for src in (RECORDS / "agents").rglob("*.yaml"):
        out = dst / src.relative_to(RECORDS)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (dst / "observed.json").write_text(json.dumps({"workspaces": observed}), encoding="utf-8")
    (dst / "change2_overlay.json").write_text(json.dumps({"task_meta": overlay}), encoding="utf-8")
    write_state_db(dst / "state.db", rows)
    return FixturePaths(root=dst, state_db=dst / "state.db", observed=dst / "observed.json",
                        registry=dst / "agents", overlay=dst / "change2_overlay.json")


def p95(samples: list[int]) -> int:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, max(0, int(round(0.95 * len(ordered))) - 1))]


def measure(impls: dict[str, Impl], paths: FixturePaths, expected: dict, *, warmup: int, runs: int, batches: int,
            cold_runs: int) -> dict:
    """cold = project (B: model load too) + read + all 17 queries; warm = one query on a built projection."""
    import time
    import tracemalloc
    calls = {q: (v["call"]["consumer"], call_args(v["call"])) for q, v in expected["queries"].items()}

    def cold(impl: Impl) -> None:
        p = impl.project(paths.read(overlay=impl.state == "change2"))
        for consumer, args in calls.values():
            (impl.find_reusable if consumer == "find_reusable" else impl.audit_lineage)(p, **args)

    out: dict[str, Any] = {name: {"cold_ns": [], "warm_batches": {q: [] for q in calls}} for name in impls}
    for _ in range(cold_runs):
        for name, impl in impls.items():
            start = time.perf_counter_ns()
            cold(impl)
            out[name]["cold_ns"].append(time.perf_counter_ns() - start)
    projections = {name: impl.project(paths.read(overlay=impl.state == "change2")) for name, impl in impls.items()}
    for _ in range(batches):
        for name, impl in impls.items():   # alternate A and B batch by batch
            p = projections[name]
            for q, (consumer, args) in calls.items():
                fn = impl.find_reusable if consumer == "find_reusable" else impl.audit_lineage
                for _ in range(warmup):
                    fn(p, **args)
                samples = []
                for _ in range(runs):
                    start = time.perf_counter_ns()
                    fn(p, **args)
                    samples.append(time.perf_counter_ns() - start)
                out[name]["warm_batches"][q].append(p95(samples))
    for name, impl in impls.items():
        tracemalloc.start()
        cold(impl)
        out[name]["tracemalloc_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        out[name]["cold_p95_ns"] = p95(out[name]["cold_ns"])
        warm = {}
        for q, batch in out[name]["warm_batches"].items():
            ordered = sorted(batch)
            warm[q] = {"p95_ns": ordered[len(ordered) // 2], "batch_min_ns": ordered[0], "batch_max_ns": ordered[-1]}
        out[name]["warm"] = warm
        del out[name]["warm_batches"], out[name]["cold_ns"]
    return out


# ---------------------------------------------------------------- report

def score_impl(impl: Impl, paths: FixturePaths, expected: dict) -> dict:
    p = impl.project(paths.read(overlay=impl.state == "change2"))
    scored = score_queries(expected, impl.state, run_queries(impl, p, expected))
    return {"state": impl.state, "correct": sum(v["ok"] for v in scored.values()),
            "wrong": sorted(q for q, v in scored.items() if not v["ok"]),
            "forbidden": forbidden_violations(impl, p, expected),
            "disagreements": disagreements(impl, p, expected),
            "defect_causes": sorted(defect_causes(impl, p, expected, impl.state, scored))}


def adapter_lines() -> dict[str, dict[str, int]]:
    """Lines of semantic code versus output-shaping code (measurement.adapter), by function name."""
    import ast
    files = {"B": ROOT / "labhq" / "research" / "semantics.py", "A": ROOT / "tests" / "semantics_baseline.py"}
    adapters = {"B": {"_public", "_advisory", "_sorted_edges", "_gap", "SemanticsAdvisory"},
                "A": {"run_row", "artifact_row", "edge_dict", "edges_of", "agent_row", "uses_of", "gap"}}
    shared = {"B": {"read_records", "_open_state_readonly", "_parse_json", "_unique_pairs", "load_yaml_unique",
                    "_construct_unique_mapping", "_relative_inside", "Records", "RecordsError", "_UniqueKeyLoader"},
              "A": set()}
    out = {}
    for name, path in files.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        counts = {"adapter": 0, "shared_reader": 0, "semantic": 0}
        for node in tree.body:
            if not hasattr(node, "end_lineno"):
                continue
            span = node.end_lineno - node.lineno + 1
            label = getattr(node, "name", None)
            counts["adapter" if label in adapters[name] else "shared_reader" if label in shared[name]
                   else "semantic"] += span
        out[name] = counts
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quick", action="store_true", help="fewer timing runs (smoke test)")
    args = parser.parse_args(argv)
    from labhq.research.semantics import MODEL_PATH, load_model
    expected = load_expected()
    impls = {name: factory() for name, factory in IMPLS.items()}
    report: dict[str, Any] = {
        "model": load_model(MODEL_PATH).name, "model_sha256": load_model(MODEL_PATH).sha256,
        "expected_sha256": hashlib.sha256((FIXTURE / "expected.yaml").read_bytes()).hexdigest(),
        "fixture_sha256": inventory_sha256(RECORDS), "states": {n: i.state for n, i in impls.items()}}
    with no_network_or_subprocess():
        with fixture_copy() as paths:
            report["scores"] = {name: score_impl(impl, paths, expected) for name, impl in impls.items()}
            report["traversal"] = {name: {"cycle": traversal_check(impl, 3, True),
                                          "depth_70": traversal_check(impl, 70, False)}
                                   for name, impl in impls.items()}
            report["perf_1x"] = measure(impls, paths, expected, warmup=2 if args.quick else 20,
                                        runs=5 if args.quick else 200, batches=2 if args.quick else 5,
                                        cold_runs=3 if args.quick else 20)
        with tempfile.TemporaryDirectory(prefix="semantics-pilot-50x-") as tmp:
            scaled = materialize_scaled(Path(tmp), 2 if args.quick else 50)
            report["perf_50x"] = measure(impls, scaled, expected, warmup=1 if args.quick else 3,
                                         runs=2 if args.quick else 20, batches=2 if args.quick else 5,
                                         cold_runs=1 if args.quick else 3)
    report["adapter_lines"] = adapter_lines()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(report))
    return 0


def render(report: dict) -> str:
    lines = [f"model {report['model']} `{report['model_sha256'][:12]}` · expected `{report['expected_sha256'][:12]}`"
             f" · fixture `{report['fixture_sha256'][:12]}`", "",
             "| | state | correct | forbidden | disagree | defect causes |", "|---|---|---|---|---|---|"]
    for name, s in report["scores"].items():
        lines.append(f"| {name} | {s['state']} | {s['correct']}/17 | {len(s['forbidden'])} | {len(s['disagreements'])} "
                     f"| {', '.join(s['defect_causes']) or 0} |")
    for size in ("perf_1x", "perf_50x"):
        perf = report[size]
        a, b = perf["A"], perf["B"]
        warm_a = sum(v["p95_ns"] for v in a["warm"].values())
        warm_b = sum(v["p95_ns"] for v in b["warm"].values())
        worst = max(b["warm"][q]["p95_ns"] / max(1, a["warm"][q]["p95_ns"]) for q in a["warm"])
        lines += ["", f"{size}: cold p95 A {a['cold_p95_ns'] / 1e6:.2f} ms, B {b['cold_p95_ns'] / 1e6:.2f} ms "
                  f"(B/A {b['cold_p95_ns'] / a['cold_p95_ns']:.2f}); warm sum of p95 A {warm_a / 1e3:.0f} us, "
                  f"B {warm_b / 1e3:.0f} us (B/A {warm_b / warm_a:.2f}, worst query {worst:.2f}); "
                  f"tracemalloc peak A {a['tracemalloc_peak_bytes'] / 1e6:.1f} MB, B {b['tracemalloc_peak_bytes'] / 1e6:.1f} MB"]
    for name, t in report["traversal"].items():
        lines.append(f"{name} traversal: cycle {t['cycle']['cautions']}, depth 70 {t['depth_70']['cautions']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
