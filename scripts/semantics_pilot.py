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


IMPLS: dict[str, Callable[[], Impl]] = {"B": impl_b}


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    expected = load_expected()
    report: dict[str, Any] = {}
    with fixture_copy() as paths:
        for name, factory in IMPLS.items():
            impl = factory()
            records = paths.read(overlay=impl.state == "change2")
            outputs = run_queries(impl, impl.project(records), expected)
            scored = score_queries(expected, impl.state, outputs)
            report[name] = {"state": impl.state, "correct": sum(v["ok"] for v in scored.values()),
                            "wrong": {q: v["diff"] for q, v in scored.items() if not v["ok"]}}
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str) if args.json else
          "\n".join(f"{n}: {r['correct']}/17 ({r['state']}) wrong={sorted(r['wrong'])}" for n, r in report.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
