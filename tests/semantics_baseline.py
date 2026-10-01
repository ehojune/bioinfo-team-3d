"""Baseline A of the semantics pilot (#127): no model file.

The #90 contract rows (ResearchResult claims, evidence, links) go into an in-memory SQLite registry
with an artifact table, a run table and one relation table ``relation(src, rel, dst, basis, value)``.
Judgments are SQL views and recursive CTEs plus a few shared Python helpers. It reads the same
``read_records`` output and answers with the same signatures and output shape as model B.
"""
from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from labhq.evidence import independent_groups
from labhq.evidence.claims import normalize_artifact_path, normalize_id, normalize_uri
from labhq.research.semantics import Records

PILOT_STATE = "change2"   # which expected.yaml state this baseline answers (#127 pilot changes move it)
DEPTH_LIMIT = 64
UNKNOWN, NONE = "unknown", "none"

SCHEMA = """
CREATE TABLE run (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, request TEXT, step TEXT, workspace TEXT, workdir TEXT,
                  agent TEXT, agent_spec_sha256 TEXT, kind TEXT, attempt INTEGER, revision INTEGER,
                  session_id TEXT, session_known INTEGER, resume_of TEXT, resume_known INTEGER, started_at REAL,
                  parent_task TEXT, pending INTEGER, method TEXT, packs TEXT, output_types TEXT);
CREATE TABLE artifact (id TEXT PRIMARY KEY, request TEXT, workspace TEXT, path TEXT);
CREATE TABLE observed (workspace TEXT, path TEXT, sha256 TEXT);
CREATE TABLE relation (src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, basis TEXT NOT NULL,
                       value TEXT NOT NULL DEFAULT '',
                       UNIQUE (src, rel, dst, value));
CREATE TABLE evidence (id TEXT PRIMARY KEY, run TEXT, target TEXT);
CREATE TABLE claim (id TEXT PRIMARY KEY, request TEXT, run TEXT, claim_id TEXT);
-- every run whose result reported a claim revision; claim.run keeps the first one only
CREATE TABLE claim_report (claim TEXT NOT NULL, run TEXT NOT NULL, PRIMARY KEY (claim, run));
CREATE TABLE agent (id TEXT PRIMARY KEY, agent_id TEXT, contract_status TEXT, probes INTEGER);
CREATE INDEX relation_src ON relation (src, rel);
CREATE INDEX relation_dst ON relation (dst, rel);
CREATE INDEX observed_key ON observed (workspace, path);

-- one row per artifact: how many runs reported it, the one reporter when there is exactly one
CREATE VIEW generator AS
  SELECT a.id AS artifact, COUNT(r.src) AS n, MIN(r.src) AS run
  FROM artifact a LEFT JOIN relation r ON r.dst = a.id AND r.rel = 'reported_output' GROUP BY a.id;
CREATE VIEW content AS
  SELECT a.id AS artifact, COUNT(DISTINCT o.sha256) AS n, MIN(o.sha256) AS sha256
  FROM artifact a LEFT JOIN observed o ON o.workspace = a.workspace AND o.path = a.path GROUP BY a.id;
-- every declared use of an artifact with its reuse kind (reporting request versus using request)
CREATE VIEW use_kind AS
  SELECT u.src AS run, u.dst AS artifact,
    CASE WHEN g.n = 0 THEN 'unknown'
         WHEN ur.request IS NOT NULL AND NOT EXISTS (SELECT 1 FROM relation r JOIN run rr ON rr.id = r.src
                          WHERE r.rel = 'reported_output' AND r.dst = u.dst
                            AND (rr.request IS NULL OR rr.request = ur.request)) THEN 'reused_cross_request'
         WHEN NOT EXISTS (SELECT 1 FROM relation r WHERE r.rel = 'reported_output' AND r.dst = u.dst
                          AND r.src = u.src) THEN 'reused_same_request'
         ELSE 'same_request' END AS reuse_kind
  FROM relation u JOIN run ur ON ur.id = u.src JOIN generator g ON g.artifact = u.dst
  WHERE u.rel = 'used';
-- lineage steps: claim->evidence, evidence->target, run->input, artifact->its one reporting run
CREATE VIEW lineage_step AS
  SELECT src AS a, dst AS b, rowid AS edge FROM relation WHERE rel IN ('bears_on', 'refers_to', 'used') AND dst != 'unknown'
  UNION ALL
  SELECT r.dst, r.src, r.rowid FROM relation r JOIN generator g ON g.artifact = r.dst AND g.n = 1 AND g.run = r.src
  WHERE r.rel = 'reported_output';
"""

# Nodes with their least depth from the root. Rows are (node, depth) pairs, not paths, so the work grows
# with nodes times the depth limit instead of with the number of paths (#140); cycles are found apart.
WALK = """
WITH RECURSIVE walk(node, depth) AS (
  SELECT :root, 0
  UNION
  SELECT s.b, w.depth + 1 FROM walk w JOIN lineage_step s ON s.a = w.node
  WHERE w.depth <= :limit
)
SELECT node, MIN(depth) FROM walk GROUP BY node
"""

UPSTREAM = """
WITH RECURSIVE up(node, depth) AS (
  SELECT :art, 0
  UNION
  SELECT u.dst, up.depth + 1 FROM up
  JOIN generator g ON g.artifact = up.node AND g.n = 1
  JOIN relation u ON u.src = g.run AND u.rel = 'used'
  WHERE up.depth < :limit
)
SELECT EXISTS (SELECT 1 FROM up WHERE node = :target AND depth > 0),
       EXISTS (SELECT 1 FROM up JOIN generator g ON g.artifact = up.node WHERE g.n != 1)
       OR EXISTS (SELECT 1 FROM up WHERE node = 'unknown')
       OR EXISTS (SELECT 1 FROM up WHERE depth >= :limit)
"""


@dataclass
class Registry:
    db: sqlite3.Connection
    records: Records
    results: dict[str, list[Any]]   # claim id -> every ResearchResult that reported it


# ---------------------------------------------------------------- shared helpers

def ext_id(scheme: str, value: str, version: str | None = None) -> str:
    return f"ext:{scheme}:{normalize_id(scheme, value)}" + (f"@{version}" if version else "")


def input_external(ref: str) -> str | None:
    match = re.fullmatch(r"([a-z][a-z0-9_]*):([^@/]+)@([^@/]+)", ref)
    if not match or match.group(1) in ("step", "art"):
        return None
    return ext_id(*match.groups())


def source_external(source: Any) -> str | None:
    suffix = f"@{source.version}" if source.version else ""
    if source.id_scheme and source.id_value:
        return ext_id(source.id_scheme, source.id_value, source.version)
    if source.uri:
        uri = normalize_uri(source.uri)
        parts = urlsplit(uri)
        if parts.netloc in ("doi.org", "dx.doi.org") and parts.path.strip("/"):
            return f"ext:doi:{unquote(parts.path.strip('/')).casefold()}{suffix}"
        return f"ext:uri:{uri}{suffix}"
    return None


def manifest_value(entry: dict | None, key: str, fallback: dict, fallback_key: str) -> tuple[Any, bool]:
    """Manifest run entry first, then the SQLite row; (value, known)."""
    if entry is not None and key in entry:
        return entry[key], True
    if fallback_key in fallback:
        return fallback[fallback_key], True
    return None, False


def one(db: sqlite3.Connection, sql: str, *args: Any) -> Any:
    row = db.execute(sql, args).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- build the registry

def project(records: Records) -> Registry:
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    reg = Registry(db=db, records=records, results={})

    def rel(src: str, kind: str, dst: str, basis: str, value: str = "") -> None:
        db.execute("INSERT OR IGNORE INTO relation VALUES (?, ?, ?, ?, ?)", (src, kind, dst, basis, value))

    def art(rid: str | None, ws: str | None, path: str) -> str | None:
        if not rid or not ws or not path:
            return None
        aid = f"art:{rid}/{ws}/{path}"
        db.execute("INSERT OR IGNORE INTO artifact VALUES (?, ?, ?, ?)", (aid, rid, ws, path))
        return aid

    for tid, task in sorted(records.tasks.items()):
        result = task.get("result") or {}
        payload = task.get("payload") or {}
        ws, rid = result.get("workdir_id"), task.get("request_id")
        manifest = records.manifests.get(result.get("workdir") or "")
        entry = ((manifest or {}).get("runs") or {}).get(tid)
        session, session_known = manifest_value(entry, "session_id", result, "session_id")
        resume_of, resume_known = manifest_value(entry, "resume_of", payload, "resume_session_id")
        started, _ = manifest_value(entry, "started_at",
                                    ((result.get("provenance") or {}).get("runs") or {}).get(tid) or {}, "started_at")
        if not isinstance(started, (int, float)) or isinstance(started, bool):
            started = None   # an unknown start never compares (SQL NULL), like B
        plan_hash = ((records.requests.get(rid) or {}).get("research_contract") or {}).get("plan_sha256")
        packs = sorted(f"{p.get('id')}@{p.get('version')}"
                       for p in ((records.plans.get(rid) or {}).get("protocol") or {}).get("packs") or [])
        db.execute("INSERT INTO run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            f"run:{ws}/{tid}" if ws else f"run:?{tid}/{tid}", tid, rid, task.get("step_id"), ws, result.get("workdir"),
            f"agent:{payload['agent_id']}" if payload.get("agent_id") else None,
            (manifest or {}).get("agent_spec_sha256"), task.get("kind"), task.get("attempt"), task.get("revision"),
            session, int(session_known), resume_of, int(resume_known), started, task.get("parent_task"),
            int(bool(result.get("pending_jobs") or result.get("pending_asks"))),
            f"method:{plan_hash}#{task.get('step_id')}" if plan_hash and task.get("step_id") else None,
            json.dumps(packs), json.dumps((payload.get("meta") or {}).get("output_types"))))
        if payload.get("agent_id"):
            db.execute("INSERT OR IGNORE INTO agent VALUES (?, ?, NULL, NULL)",
                       (f"agent:{payload['agent_id']}", payload["agent_id"]))

    runs = db.execute("SELECT id, task_id, request, step, workspace, workdir, agent, method FROM run").fetchall()
    for run, tid, rid, _step, ws, _workdir, agent, method in runs:
        if agent:
            rel(agent, "performs", run, "reported")
        if method:
            rel(run, "uses_method", method, "declared")
        for out in (records.tasks[tid].get("result") or {}).get("outputs") or []:
            aid = art(rid, ws, normalize_artifact_path(out))
            if aid:
                rel(run, "reported_output", aid, "reported")

    # declared inputs
    for run, tid, rid, step, ws, _workdir, _agent, _method in runs:
        plan_step = next((s for s in (records.plans.get(rid) or {}).get("steps") or [] if s.get("id") == step), {})
        for ref in plan_step.get("input_refs") or []:
            dst = input_external(ref)
            if dst is None and ref.startswith("step:"):
                ref_step, _, path = ref[5:].partition("/")
                spaces = [r[0] for r in db.execute("SELECT DISTINCT workspace FROM run WHERE request = ? AND step = ?",
                                                   (rid, ref_step))]
                dst = art(rid, spaces[0], normalize_artifact_path(path)) if len(spaces) == 1 else None
            elif dst is None and ref.startswith("art:"):
                ref_rid, _, rest = ref[4:].partition("/")
                ref_ws, _, path = rest.partition("/")
                if one(db, "SELECT 1 FROM run WHERE request = ? AND workspace = ?", ref_rid, ref_ws):
                    dst = art(ref_rid, ref_ws, normalize_artifact_path(path))
            rel(run, "used", dst or UNKNOWN, "declared")

    # resumes: the one earlier run in the same workspace on the resumed session
    for run, resume_of, known in db.execute("SELECT id, resume_of, resume_known FROM run").fetchall():
        if not known or resume_of is None:
            continue
        parents = [r[0] for r in db.execute(
            "SELECT o.id FROM run o JOIN run r ON r.id = ? WHERE o.workspace = r.workspace AND o.id != r.id "
            "AND o.session_id = r.resume_of AND o.started_at < r.started_at ORDER BY o.id", (run,))]
        rel(run, "resumes", parents[0] if len(parents) == 1 else UNKNOWN, "reported")
    # wake: parent task whose result left pending jobs or asks
    for run, parent in db.execute("SELECT r.id, p.id FROM run r JOIN run p ON p.task_id = r.parent_task "
                                  "WHERE p.pending = 1").fetchall():
        rel(run, "wake_of", parent, "reported")

    # observed hashes
    for ws, paths in records.observed.items():
        for path, hashes in paths.items():
            db.executemany("INSERT INTO observed VALUES (?, ?, ?)", [(ws, path, h) for h in hashes])

    # ledger rows: evidence -> target, claims, links
    workdirs = db.execute("SELECT DISTINCT workdir, workspace FROM run WHERE workdir IS NOT NULL").fetchall()
    for run, tid, rid, _step, ws, workdir, _agent, _method in runs:
        result = records.results.get(tid)
        if result is None:
            continue
        paths = {ref.artifact_id: ref.path for ref in result.artifact_refs}
        for ev in result.evidence:
            ev_id = f"ev:{ws}/{tid}/{ev.id}"
            target = source_external(ev.source) if ev.source else None
            if target is None and ev.source and ev.source.artifact_id in paths and workdir:
                joined = posixpath.normpath(posixpath.join(workdir, paths[ev.source.artifact_id].replace("\\", "/")))
                for known, known_ws in workdirs:
                    if joined.startswith(known.rstrip("/") + "/"):
                        owners = [r[0] for r in db.execute("SELECT DISTINCT request FROM run WHERE workspace = ?",
                                                           (known_ws,))]
                        if len(owners) == 1:
                            target = art(owners[0], known_ws, normalize_artifact_path(joined[len(known.rstrip("/")) + 1:]))
                        break
            db.execute("INSERT INTO evidence VALUES (?, ?, ?)", (ev_id, run, target))
            if target:
                rel(ev_id, "refers_to", target, "cited")
        for claim in result.claims:
            cid = f"claim:{rid}/{claim.id}@{claim.revision}"
            db.execute("INSERT OR IGNORE INTO claim VALUES (?, ?, ?, ?)", (cid, rid, run, claim.id))
            db.execute("INSERT OR IGNORE INTO claim_report VALUES (?, ?)", (cid, run))
            reg.results.setdefault(cid, []).append(result)
        for link in result.links:
            value = link.relation if link.relation in ("supports", "contradicts") else "context"
            rel(f"claim:{rid}/{link.claim_id}@{link.claim_revision}", "bears_on", f"ev:{ws}/{tid}/{link.evidence_id}",
                "reported", value)
    # citations: evidence of a run about something that run did not report
    db.execute("INSERT OR IGNORE INTO relation SELECT DISTINCT e.run, 'cites', e.target, 'cited', '' FROM evidence e "
               "WHERE e.target IS NOT NULL AND NOT EXISTS (SELECT 1 FROM relation o WHERE o.src = e.run "
               "AND o.rel = 'reported_output' AND o.dst = e.target)")

    for agent, agent_id in db.execute("SELECT id, agent_id FROM agent").fetchall():
        contract = (records.contracts.get(agent_id) or {}).get("contract")
        probes = bool(((contract or {}).get("verification") or {}).get("probe_tools"))
        db.execute("UPDATE agent SET contract_status = ?, probes = ? WHERE id = ?",
                   ((contract or {}).get("status") or UNKNOWN if contract else None, int(probes), agent))
    for (aid,) in db.execute("SELECT id FROM artifact").fetchall():
        meaning = artifact_row(reg, aid)["data_type"]
        if meaning != UNKNOWN:
            rel(aid, "means", f"type:{meaning}", "declared")
    db.commit()
    return reg


# ---------------------------------------------------------------- judged rows

def run_row(reg: Registry, run: str) -> dict:
    db = reg.db
    (rid, step, tid, ws, agent, agent_hash, kind, attempt, revision, session, session_known, resume_of, resume_known,
     method) = db.execute("SELECT request, step, task_id, workspace, agent, agent_spec_sha256, kind, attempt, revision, "
                          "session_id, session_known, resume_of, resume_known, method FROM run WHERE id = ?",
                          (run,)).fetchone()
    unknown: dict[str, str] = {}
    candidates: dict[str, list[str]] = {}
    if agent_hash is None:
        agent_hash, unknown["agent_spec_sha256"] = UNKNOWN, "manifest_missing"
    if not session_known:
        session, unknown["session_id"] = UNKNOWN, "manifest_missing"
    resumes = one(db, "SELECT dst FROM relation WHERE src = ? AND rel = 'resumes'", run)
    if not resume_known:
        resumes, unknown["resumes"] = UNKNOWN, "manifest_missing"
    elif resume_of is None:
        resumes = NONE
    elif resumes is None or resumes == UNKNOWN:
        found = resume_candidates(reg, run)
        resumes, unknown["resumes"] = UNKNOWN, "shared_session" if found else "no_matching_session"
        if found:
            candidates["resumes"] = found
    wake = one(db, "SELECT dst FROM relation WHERE src = ? AND rel = 'wake_of'", run)
    parent = one(db, "SELECT parent_task FROM run WHERE id = ?", run)
    if wake is None and parent and not one(db, "SELECT 1 FROM run WHERE task_id = ?", parent):
        wake, unknown["wake_of"] = UNKNOWN, "parent_missing"
    row = {"id": run, "request": rid, "step": step, "task_id": tid, "workspace": ws, "agent": agent or UNKNOWN,
           "agent_spec_sha256": agent_hash, "kind": kind, "attempt": attempt,
           "retry": attempt > 1 if isinstance(attempt, int) else UNKNOWN, "revision": revision,
           "session_id": session, "resumes": resumes, "wake_of": wake or NONE,
           "method": method or UNKNOWN}
    if method is None:
        unknown["method"] = "no_plan_hash"
    if unknown:
        row["unknown"] = unknown
    if candidates:
        row["candidates"] = candidates
    return row


def resume_candidates(reg: Registry, run: str) -> list[str]:
    return [r[0] for r in reg.db.execute(
        "SELECT o.id FROM run o JOIN run r ON r.id = ? WHERE o.workspace = r.workspace AND o.id != r.id "
        "AND o.session_id = r.resume_of AND o.started_at < r.started_at ORDER BY o.id", (run,))]


def artifact_row(reg: Registry, aid: str) -> dict:
    db = reg.db
    rid, ws, path = db.execute("SELECT request, workspace, path FROM artifact WHERE id = ?", (aid,)).fetchone()
    reporters = [r[0] for r in db.execute("SELECT src FROM relation WHERE rel = 'reported_output' AND dst = ? "
                                          "ORDER BY src", (aid,))]
    n_hash, sha = db.execute("SELECT n, sha256 FROM content WHERE artifact = ?", (aid,)).fetchone()
    unknown: dict[str, str] = {}
    candidates: dict[str, list[str]] = {}
    row: dict[str, Any] = {"id": aid, "request": rid, "workspace": ws, "path": path, "reported_by": reporters}
    if len(reporters) == 1:
        gen = reporters[0]
        gen_method, packs, types = db.execute("SELECT method, packs, output_types FROM run WHERE id = ?",
                                              (gen,)).fetchone()
        row.update(generated_by=gen,
                   generator_inputs=sorted(r[0] for r in db.execute(
                       "SELECT dst FROM relation WHERE src = ? AND rel = 'used'", (gen,))),
                   method=gen_method or UNKNOWN, packs=json.loads(packs))
        if gen_method is None:
            unknown["method"] = "no_plan_hash"
    else:
        row.update(generated_by=UNKNOWN, generator_inputs=UNKNOWN, method=UNKNOWN, packs=UNKNOWN)
        unknown["generated_by"] = "multiple_reporters" if reporters else "no_reported_output"
        unknown.update(generator_inputs="generator_unknown", method="generator_unknown", packs="generator_unknown")
        if reporters:
            candidates["generated_by"] = reporters
        types = None
    if n_hash == 1:
        row["sha256"] = sha
        row["same_content_as"] = [r[0] for r in db.execute(
            "SELECT artifact FROM content WHERE n = 1 AND sha256 = ? AND artifact != ? ORDER BY artifact", (sha, aid))]
    else:
        row["sha256"], row["same_content_as"] = UNKNOWN, []
        unknown["sha256"] = "multiple_hashes" if n_hash else "not_observed"
        if n_hash:
            candidates["sha256"] = [r[0] for r in db.execute(
                "SELECT DISTINCT sha256 FROM observed WHERE workspace = ? AND path = ? ORDER BY sha256", (ws, path))]
    row["data_type"] = data_type(row["generated_by"], types, path, unknown)
    row["unknown"], row["candidates"] = unknown, candidates
    return row


# Declared data meanings (pilot change 2). broader is kept for reference only: never inherited.
VOCABULARY = {"raw_counts": {"broader": "expression_counts"}, "normalized_counts": {"broader": "expression_counts"},
              "de_table": {}}


def data_type(generator: str, output_types: str | None, path: str, unknown: dict) -> str:
    """Only the generating run's declared task meta output_types; pack values and broader types never fill it."""
    if generator == UNKNOWN:
        unknown["data_type"] = "generator_unknown"
        return UNKNOWN
    declared = {normalize_artifact_path(k): v for k, v in (json.loads(output_types or "null") or {}).items()}
    if declared.get(path) in VOCABULARY:
        return declared[path]
    unknown["data_type"] = "not_declared"
    return UNKNOWN


def uses_of(reg: Registry, *, artifact: str | None = None, run: str | None = None) -> list[dict]:
    sql = "SELECT run, artifact, reuse_kind FROM use_kind WHERE " + ("artifact = ?" if artifact else "run = ?")
    out = []
    for user, aid, kind in reg.db.execute(sql + " ORDER BY run, artifact", (artifact or run,)):
        use = {"run": user, "artifact": aid, "reuse_kind": kind}
        if kind == UNKNOWN:
            use["unknown"] = {"reuse_kind": "no_reported_output"}
        out.append(use)
    return out


REUSE_KINDS = ("reused_cross_request", "reused_same_request")


def agent_row(reg: Registry, agent: str) -> dict:
    status, probes = reg.db.execute("SELECT contract_status, probes FROM agent WHERE id = ?", (agent,)).fetchone()
    row = {"agent": agent, "contract_status": status or NONE, "method_validation": "not_established"}
    if status and probes:
        row["verification_scope"] = "connection_only"
    else:
        row["verification_scope"], row["unknown"] = UNKNOWN, {"verification_scope": "no_contract_record"}
    return row


def edge_dict(reg: Registry, rowid: int) -> dict:
    src, kind, dst, basis, value = reg.db.execute("SELECT src, rel, dst, basis, value FROM relation WHERE rowid = ?",
                                                  (rowid,)).fetchone()
    edge: dict[str, Any] = {"src": src, "rel": kind, "dst": dst, "basis": basis}
    if value:
        edge["value"] = value
    if dst == UNKNOWN and kind == "resumes":
        found = resume_candidates(reg, src)
        edge["unknown"] = {"dst": "shared_session" if found else "no_matching_session"}
        if found:
            edge["candidates"] = {"dst": found}
    elif dst == UNKNOWN:
        edge["unknown"] = {"dst": "unresolved_input_ref"}
    return edge


def edges_of(reg: Registry, rowids: set[int]) -> list[dict]:
    return sorted((edge_dict(reg, r) for r in rowids), key=lambda e: (e["src"], e["rel"], e["dst"], e.get("value", "")))


# ---------------------------------------------------------------- consumers

def derived(reg: Registry, aid: str, target: str) -> str:
    found, unknown = reg.db.execute(UPSTREAM, {"art": aid, "target": target, "limit": DEPTH_LIMIT}).fetchone()
    return "yes" if found else (UNKNOWN if unknown else "no")


def used_by(reg: Registry, aid: str, rid: str) -> str:
    kinds = [r[0] for r in reg.db.execute("SELECT u.reuse_kind FROM use_kind u JOIN run r ON r.id = u.run "
                                          "WHERE u.artifact = ? AND r.request = ?", (aid, rid))]
    return "yes" if any(k in REUSE_KINDS for k in kinds) else (UNKNOWN if UNKNOWN in kinds else "no")


def find_reusable(reg: Registry, *, key: str | None = None, method: str | None = None, derived_from: str | None = None,
                  used_by_request: str | None = None, data_type: str | None = None) -> dict:
    selected, excluded_unknown, candidates = [], [], {}
    for (aid,) in reg.db.execute("SELECT id FROM artifact ORDER BY id").fetchall():
        row = artifact_row(reg, aid)
        match: dict[str, str] = {}
        if key is not None:
            match["key"] = "yes" if row["path"] == normalize_artifact_path(key) else "no"
        if method is not None:
            match["method"] = UNKNOWN if row["packs"] == UNKNOWN else ("yes" if method in row["packs"] else "no")
        if derived_from is not None:
            match["derived_from"] = derived(reg, aid, derived_from)
        if used_by_request is not None:
            match["used_by_request"] = used_by(reg, aid, used_by_request)
        filters = list(match.values())
        if data_type is not None:
            match["data_type"] = UNKNOWN if row["data_type"] == UNKNOWN else ("yes" if row["data_type"] == data_type
                                                                               else "no")
        if "no" in filters:
            continue
        (selected if all(v == "yes" for v in filters) else excluded_unknown).append(aid)
        candidates[aid] = {**row, "uses": uses_of(reg, artifact=aid), "match": match,
                           "recommend": (row["generated_by"] != UNKNOWN and row["sha256"] != UNKNOWN
                                         and all(v == "yes" for v in filters) and match.get("data_type", "yes") == "yes")}
    return {"selected": selected, "excluded_unknown": excluded_unknown, "candidates": candidates}


def concept(node: str) -> str:
    return {"claim": "claim", "ev": "evidence", "art": "artifact", "run": "run", "ext": "external_source"}.get(
        node.split(":", 1)[0], "")


def closes_cycle(steps: dict[str, list[tuple[str, int]]], depth: dict[str, int], src: str, dst: str) -> bool:
    """A walked step src -> dst closes a cycle when dst is no deeper than src and reaches src again."""
    if depth[dst] > depth[src]:
        return False
    seen, todo = {dst}, [dst]
    while todo:
        for nxt, _edge in steps.get(todo.pop(), []):
            if nxt == src:
                return True
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return False


def walk(reg: Registry, root: str) -> dict[str, Any]:
    db = reg.db
    depth = dict(db.execute(WALK, {"root": root, "limit": DEPTH_LIMIT}).fetchall())
    expanded = sorted(node for node, d in depth.items() if d <= DEPTH_LIMIT)
    reached = set(depth)
    steps = {node: db.execute("SELECT b, edge FROM lineage_step WHERE a = ? ORDER BY b", (node,)).fetchall()
             for node in expanded}
    edges = {edge for out in steps.values() for _nxt, edge in out}
    cautions = [{"code": "depth_limit", "nodes": [node]} for node in sorted(reached) if depth[node] > DEPTH_LIMIT]
    for node in sorted({nxt for src, out in steps.items() for nxt, _edge in out if closes_cycle(steps, depth, src, nxt)}):
        cautions.append({"code": "cycle", "nodes": [node]})
    runs = sorted(n for n in expanded if concept(n) == "run")
    artifacts = sorted(n for n in expanded if concept(n) == "artifact")
    marks = ",".join("?" * len(runs))
    if runs:
        edges |= {r[0] for r in db.execute(
            f"SELECT rowid FROM relation WHERE (rel = 'performs' AND dst IN ({marks})) OR "
            f"(rel IN ('uses_method', 'resumes', 'wake_of') AND src IN ({marks}))", runs + runs)}
    for aid in artifacts:
        if one(db, "SELECT n FROM generator WHERE artifact = ?", aid) > 1:
            edges |= {r[0] for r in db.execute("SELECT rowid FROM relation WHERE rel = 'reported_output' AND dst = ?",
                                               (aid,))}
    cites = {r[0] for r in db.execute(f"SELECT rowid FROM relation WHERE rel = 'cites' AND src IN ({marks})", runs)} \
        if runs else set()
    gaps = []
    for aid in artifacts:
        row = artifact_row(reg, aid)
        gaps += [gap(row, f) for f in ("generated_by", "sha256") if row[f] == UNKNOWN]
    for run in runs:
        row = run_row(reg, run)
        gaps += [gap(row, f) for f in ("agent_spec_sha256", "resumes") if row[f] == UNKNOWN]
        if (row.get("unknown") or {}).get("resumes") == "shared_session":
            cautions.append({"code": "resume_parent_ambiguous", "nodes": [run], "candidates": row["candidates"]["resumes"]})
    return {"edges": edges, "cites": cites, "gaps": gaps, "cautions": cautions,
            "nodes": {n for n in reached if concept(n) in ("artifact", "external_source")}}


def gap(row: dict, name: str) -> dict:
    out = {"node": row["id"], "field": name, "reason": row["unknown"][name], "basis": "reported"}
    if (row.get("candidates") or {}).get(name):
        out["candidates"] = row["candidates"][name]
    return out


def audit_lineage(reg: Registry, *, claim: str | None = None, evidence: str | None = None, artifact: str | None = None,
                  run: str | None = None, step: str | None = None) -> dict:
    given = {k: v for k, v in {"claim": claim, "evidence": evidence, "artifact": artifact, "run": run,
                               "step": step}.items() if v is not None}
    if len(given) != 1:
        raise ValueError("audit_lineage takes exactly one root")
    kind, root = next(iter(given.items()))
    db = reg.db
    if kind == "step":
        rid, _, step_id = root.partition("/")
        runs = [r[0] for r in db.execute("SELECT id FROM run WHERE request = ? AND step = ? ORDER BY id", (rid, step_id))]
        marks = ",".join("?" * len(runs))
        rel_rows = lambda name: {r[0] for r in db.execute(  # noqa: E731
            f"SELECT rowid FROM relation WHERE rel = ? AND src IN ({marks})", (name, *runs))} if runs else set()
        return {"root": root, "runs": runs, "run_count": len(runs),
                "retry_count": one(db, f"SELECT COUNT(*) FROM run WHERE attempt > 1 AND id IN ({marks})", *runs) or 0,
                "resumes": edges_of(reg, rel_rows("resumes")), "wake_of": edges_of(reg, rel_rows("wake_of"))}
    table = {"claim": "claim", "evidence": "evidence", "artifact": "artifact", "run": "run"}[kind]
    if not one(db, f"SELECT 1 FROM {table} WHERE id = ?", root):
        raise KeyError(f"{kind} {root} is not in the records")
    walked = walk(reg, root)
    result: dict[str, Any] = {"root": root, "edges": edges_of(reg, walked["edges"]), "cites": edges_of(reg, walked["cites"]),
                              "gaps": sorted(walked["gaps"], key=lambda g: (g["node"], g["field"])),
                              "cautions": walked["cautions"]}
    if kind == "claim":
        bears = {"supports": [], "contradicts": [], "context": []}
        for dst, value in db.execute("SELECT dst, value FROM relation WHERE src = ? AND rel = 'bears_on' ORDER BY dst",
                                     (root,)):
            bears[value].append(dst)
        result["bears_on"] = bears
        counted = sorted(bears["supports"] + bears["contradicts"])
        lineage = {ev: walk(reg, ev)["nodes"] for ev in counted}
        shared = [{"evidence": [a, b], "nodes": sorted(lineage[a] & lineage[b])}
                  for i, a in enumerate(counted) for b in counted[i + 1:] if lineage[a] & lineage[b]]
        result["shared_ancestors"] = shared
        claim_id = one(db, "SELECT claim_id FROM claim WHERE id = ?", root)
        # one ledger per reporting result: evidence ids are local to a result, so groups are joined, not rows
        result["independent_groups"] = sorted({g for res in reg.results[root]
                                               for g in independent_groups(claim_id, res.evidence, res.links)})
        result["cautions"] = walked["cautions"] + [{"code": "shared_ancestor_review", **s} for s in shared]
    if kind == "artifact":
        result["node"] = artifact_row(reg, root)
    if kind == "run":
        row = run_row(reg, root)
        result["node"] = row
        if row["agent"] != UNKNOWN:
            result["agent_verification"] = agent_row(reg, row["agent"])
        result["uses"] = uses_of(reg, run=root)
    return result
