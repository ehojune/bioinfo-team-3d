"""Provenance and reuse semantics pilot (#127): model B. Advisory only.

Nothing on the CLI, gateway, runner or orchestrator path imports this module. It reads records it is
handed (read-only), projects them in memory onto the concepts and relations of ``semantics_v1.yaml``
and answers two consumers, ``find_reusable`` and ``audit_lineage``, through the same ``judge_*``
functions. Answers are ``SemanticsAdvisory`` values; nothing is written back anywhere.

``read_records`` is shared with the baseline (A) of the pilot: it parses and normalizes only, and
leaves generation, reuse, identity and ancestry judgments to each model.
"""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote, unquote, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from labhq.evidence.claims import independent_groups, normalize_artifact_path, normalize_id, normalize_uri
from labhq.research.contract import ResearchPlan, ResearchResult, validate_research_result

MODEL_PATH = Path(__file__).with_name("semantics_v1.yaml")
UNKNOWN = "unknown"
NONE = "none"
PILOT_STATE = "change2"  # which expected.yaml state this model answers (#127 pilot changes move it)


# ---------------------------------------------------------------- shared reader (A and B)

class RecordsError(ValueError):
    """A record could not be read or parsed. Messages name record-relative paths only."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise RecordsError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _parse_json(text: str, where: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs)
    except RecordsError as exc:
        raise RecordsError(f"{where}: {exc}") from None
    except ValueError as exc:
        raise RecordsError(f"{where}: invalid JSON ({getattr(exc, 'msg', exc)})") from None


class _UniqueKeyLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # type: ignore[misc]
    """SafeLoader that rejects a mapping key given twice instead of keeping the last one."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def load_yaml_unique(text: str, where: str) -> Any:
    """yaml.safe_load that refuses duplicate keys. Errors carry ``where`` and a line, never a path."""
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" line {mark.line + 1}" if mark is not None else ""
        raise RecordsError(f"{where}:{line} {getattr(exc, 'problem', None) or exc}") from None


@dataclass(frozen=True)
class Records:
    """Parsed records. Paths inside are record-relative strings exactly as stored."""

    requests: Mapping[str, dict]                         # request id -> state body
    tasks: Mapping[str, dict]                            # task id -> state body (overlay merged)
    plans: Mapping[str, dict]                            # request id -> validated research plan (as dict)
    results: Mapping[str, ResearchResult]                # task id -> validated research result
    manifests: Mapping[str, dict | None]                 # result.workdir -> manifest, None when missing
    observed: Mapping[str, Mapping[str, tuple[str, ...]]]  # workdir_id -> normalized path -> distinct sha256
    contracts: Mapping[str, dict]                        # agent id -> agent spec (contract staff only)


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _relative_inside(rel: str, where: str) -> PurePosixPath:
    path = PurePosixPath(rel.replace("\\", "/"))
    if path.is_absolute() or re.match(r"^[A-Za-z]:", rel) or ".." in path.parts or not path.parts:
        raise RecordsError(f"{where}: path {rel!r} is not inside the records root")
    return path


def _open_state_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing gateway state DB without writing to it or to its directory.

    ``mode=ro`` still creates -wal/-shm files beside a WAL database that has none, so a database
    without a live WAL is opened ``immutable``; with a WAL present (a writer may be active) plain
    ``mode=ro`` shares the existing files. ``query_only`` refuses writes either way.
    """
    if not path.is_file():
        raise RecordsError("state database: not found")
    wal = path.with_name(path.name + "-wal")
    uri = f"file:{quote(path.resolve().as_posix())}?mode=ro" + ("" if wal.exists() else "&immutable=1")
    try:
        db = sqlite3.connect(uri, uri=True)
        db.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise RecordsError(f"state database: cannot open read-only ({exc})") from None
    return db


def read_records(root: Path, *, state_db: Path, observed: Path | None = None, registry: Path | None = None,
                 overlay: Path | None = None) -> Records:
    """Read the records the caller names. Opens only these paths and manifests under ``root``.

    - ``state_db``: gateway SQLite state (request and task rows), read-only.
    - ``root``: directory that task ``result.workdir`` paths are relative to.
    - ``observed``: JSON ``{"workspaces": {workdir_id: {path: sha256}}}`` (verify_sources input).
    - ``registry``: directory holding ``contract/<agent_id>.yaml`` for contract staff.
    - ``overlay``: JSON ``{"task_meta": {task_id: {...}}}`` merged into ``payload.meta``.
    """
    db = _open_state_readonly(state_db)
    try:
        rows = db.execute("SELECT kind, key, body FROM state WHERE kind IN ('request', 'task') "
                          "ORDER BY kind, key").fetchall()
    except sqlite3.Error as exc:
        raise RecordsError(f"state database: cannot read state rows ({exc})") from None
    finally:
        db.close()
    requests: dict[str, dict] = {}
    tasks: dict[str, dict] = {}
    for kind, key, body in rows:
        parsed = _parse_json(body, f"state {kind} {key}")
        if not isinstance(parsed, dict):
            raise RecordsError(f"state {kind} {key}: body is not an object")
        (requests if kind == "request" else tasks)[key] = parsed

    if overlay is not None:
        extra = _parse_json(overlay.read_text(encoding="utf-8"), "overlay")
        for task_id, meta in (extra.get("task_meta") or {}).items():
            if task_id not in tasks:
                raise RecordsError(f"overlay: unknown task {task_id}")
            body = copy.deepcopy(tasks[task_id])
            body.setdefault("payload", {}).setdefault("meta", {}).update(copy.deepcopy(meta))
            tasks[task_id] = body

    plans: dict[str, dict] = {}
    for rid, body in requests.items():
        if body.get("research_contract") and isinstance(body.get("plan"), dict):
            try:
                ResearchPlan.model_validate(body["plan"])
            except ValidationError as exc:
                raise RecordsError(f"state request {rid}: research plan invalid ({exc.error_count()} errors)") from None
            plans[rid] = body["plan"]

    results: dict[str, ResearchResult] = {}
    manifests: dict[str, dict | None] = {}
    for tid, body in tasks.items():
        result = body.get("result") or {}
        structured = result.get("structured")
        rid = body.get("request_id")
        if structured and rid in plans:
            try:
                results[tid] = validate_research_result(structured, plan=plans[rid])
            except (ValidationError, ValueError) as exc:
                detail = f"{exc.error_count()} errors" if isinstance(exc, ValidationError) else str(exc)
                raise RecordsError(f"state task {tid}: research result invalid ({detail})") from None
        workdir = result.get("workdir")
        if workdir and workdir not in manifests:
            rel = _relative_inside(workdir, f"state task {tid} result.workdir")
            path = root.joinpath(*rel.parts, "manifest.json")
            manifests[workdir] = (_parse_json(path.read_text(encoding="utf-8"), f"{rel.as_posix()}/manifest.json")
                                  if path.is_file() else None)

    seen: dict[str, dict[str, set[str]]] = {}
    if observed is not None:
        data = _parse_json(observed.read_text(encoding="utf-8"), "observed")
        for ws, paths in (data.get("workspaces") or {}).items():
            for spelling, digest in paths.items():
                seen.setdefault(ws, {}).setdefault(normalize_artifact_path(spelling), set()).add(digest)

    contracts: dict[str, dict] = {}
    if registry is not None:
        for agent_id in sorted({(b.get("payload") or {}).get("agent_id") for b in tasks.values()} - {None}):
            if not _SAFE_NAME.match(agent_id):
                raise RecordsError(f"agent id {agent_id!r} is not a plain name")
            path = registry / "contract" / f"{agent_id}.yaml"
            if path.is_file():
                contracts[agent_id] = load_yaml_unique(path.read_text(encoding="utf-8"), f"contract/{agent_id}.yaml")

    return Records(requests=requests, tasks=tasks, plans=plans, results=results, manifests=manifests,
                   observed={ws: {p: tuple(sorted(h)) for p, h in paths.items()} for ws, paths in seen.items()},
                   contracts=contracts)


# ---------------------------------------------------------------- model

class ModelError(ValueError):
    """The semantics model file is malformed or does not match the judge table in code."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


Basis = Literal["reported", "declared", "cited", "none"]


class Concept(_Strict):
    means: str = Field(min_length=1)
    id: str = Field(pattern=r"^[a-z]+:")

    @property
    def prefix(self) -> str:
        return self.id.split(":", 1)[0] + ":"


class Lineage(_Strict):
    role: Literal["follow", "record", "cites", "none"]
    direction: Literal["down", "up"] | None = None
    at: Literal["src", "dst"] | None = None

    @model_validator(mode="after")
    def shape(self) -> "Lineage":
        if (self.role == "follow") != (self.direction is not None):
            raise ValueError("lineage direction is given exactly for role follow")
        if (self.role == "record") != (self.at is not None):
            raise ValueError("lineage at is given exactly for role record")
        return self


class Relation(_Strict):
    from_: str = Field(alias="from")
    to: list[str] = Field(min_length=1)
    basis: list[Basis] = Field(min_length=1)
    values: list[str] = []
    lineage: Lineage


class ReuseRule(_Strict):
    scope: Literal["request", "run"]
    kinds: list[str]
    reuse_kinds: list[str]


class DataTypeRule(_Strict):
    source: Literal["none", "declared"]
    vocabulary: dict[str, dict[str, str]] = {}
    inherit_broader: Literal[False]


class VerificationRule(_Strict):
    probe_scope: str
    method_validation: str


class TraversalRule(_Strict):
    depth_limit: int = Field(ge=1, le=4096)


class Reasons(_Strict):
    unknown: list[str]
    cautions: list[str]


class Constraint(_Strict):
    judge: str = Field(pattern=r"^judge_[a-z_]+$")
    rule: str | list[str]
    says: str = Field(min_length=1)


class ModelSpec(_Strict):
    model: str
    version: Literal[1]
    status: Literal["pilot"]
    concepts: dict[str, Concept]
    relations: dict[str, Relation]
    field_basis: dict[str, Basis]
    reuse: ReuseRule
    data_type: DataTypeRule
    verification: VerificationRule
    traversal: TraversalRule
    reasons: Reasons
    constraints: dict[str, Constraint]

    @model_validator(mode="after")
    def consistent(self) -> "ModelSpec":
        problems = []
        for name, rel in self.relations.items():
            for end in (rel.from_, *rel.to):
                if end not in self.concepts:
                    problems.append(f"relation {name} names undeclared concept {end}")
        prefixes = [c.prefix for c in self.concepts.values()]
        if len(set(prefixes)) != len(prefixes):
            problems.append("concept id prefixes must be distinct")
        if UNKNOWN not in self.reuse.kinds or not set(self.reuse.reuse_kinds) <= set(self.reuse.kinds):
            problems.append("reuse kinds must include unknown and every reuse kind")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@dataclass(frozen=True)
class SemanticModel:
    spec: ModelSpec
    sha256: str
    judges: Mapping[str, Callable[..., Any]]

    @property
    def name(self) -> str:
        return f"{self.spec.model}@{self.spec.version}"

    def concept_of(self, node: str) -> str | None:
        for name, concept in self.spec.concepts.items():
            if node.startswith(concept.prefix):
                return name
        return None


def load_model(path: Path = MODEL_PATH) -> SemanticModel:
    """Load and check the model file. Fails on duplicate keys, bad shape or a judge table mismatch."""
    raw = path.read_bytes()
    try:
        data = load_yaml_unique(raw.decode("utf-8"), path.name)
    except RecordsError as exc:
        raise ModelError(str(exc)) from None
    try:
        spec = ModelSpec.model_validate(data)
    except ValidationError as exc:
        raise ModelError(f"{path.name}: {exc.error_count()} problems: "
                         + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])) from None
    declared = set(spec.constraints)
    unpaired = sorted(declared ^ set(JUDGES))
    if unpaired:
        raise ModelError(f"{path.name}: constraints without a judge or judges without a constraint: {unpaired}")
    for cid, constraint in spec.constraints.items():
        if JUDGES[cid].__name__ != constraint.judge:
            raise ModelError(f"{path.name}: constraint {cid} names {constraint.judge}, code has {JUDGES[cid].__name__}")
    # LF-normalized so a CRLF checkout (Windows autocrlf) pins the same model hash.
    return SemanticModel(spec=spec, sha256=hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
                         judges=dict(JUDGES))


# ---------------------------------------------------------------- judges (pure functions)

@dataclass(frozen=True)
class Judged:
    """A judged value; ``reason`` and ``candidates`` explain an unknown."""

    value: Any
    reason: str | None = None
    candidates: tuple[str, ...] = ()


def judge_identity(kind: str, *parts: str | None) -> str | None:
    """Scoped id, or None when a scoping part is missing. Two missing parts never make one id."""
    if any(part is None or part == "" for part in parts):
        return None
    templates = {"run": "run:{}/{}", "artifact": "art:{}/{}/{}", "claim": "claim:{}/{}@{}", "evidence": "ev:{}/{}/{}",
                 "method": "method:{}#{}", "agent": "agent:{}", "type": "type:{}"}
    return templates[kind].format(*parts)


def judge_generation(reporters: Iterable[str]) -> Judged:
    found = tuple(sorted(set(reporters)))
    if len(found) == 1:
        return Judged(found[0])
    return Judged(UNKNOWN, "multiple_reporters", found) if found else Judged(UNKNOWN, "no_reported_output")


def judge_reuse(scope: str, user_run: str, user_request: str | None, reporters: Mapping[str, str | None]) -> Judged:
    """reporters: run id -> request id of each run that reported the artifact."""
    if not reporters:
        return Judged(UNKNOWN, "no_reported_output")
    requests = set(reporters.values())
    if user_request is not None and None not in requests and user_request not in requests:
        return Judged("reused_cross_request")
    if scope == "run" and user_run not in reporters:
        return Judged("reused_same_request")
    return Judged("same_request")


def judge_resume(resume_of: str | None, started_at: float | None, peers: Iterable[tuple[str, str | None, float | None]]
                 ) -> Judged:
    """peers: (run id, session_id, started_at) of the other runs in the same workspace."""
    if resume_of is None:
        return Judged(NONE)
    earlier = tuple(sorted(run for run, session, start in peers
                           if session == resume_of and start is not None and started_at is not None
                           and start < started_at))
    if len(earlier) == 1:
        return Judged(earlier[0])
    return Judged(UNKNOWN, "shared_session", earlier) if earlier else Judged(UNKNOWN, "no_matching_session")


def judge_retry(attempt: Any) -> Judged:
    return Judged(attempt > 1) if isinstance(attempt, int) else Judged(UNKNOWN, "manifest_missing")


def judge_wake(parent_task: str | None, parent_result: Mapping[str, Any] | None, parent_run: str | None) -> Judged:
    if not parent_task:
        return Judged(NONE)
    if parent_result is None or parent_run is None:
        return Judged(UNKNOWN, "parent_missing")
    if parent_result.get("pending_jobs") or parent_result.get("pending_asks"):
        return Judged(parent_run)
    return Judged(NONE)  # e.g. a wrap-up turn: it continues the parent but is not a wake


def judge_run_meta(task: Mapping[str, Any], manifest: Mapping[str, Any] | None, task_id: str) -> dict[str, Judged]:
    """Run fields from the SQLite task row; the manifest first for session and resume, alone for the agent hash."""
    result = task.get("result") or {}
    payload = task.get("payload") or {}
    run_entry = ((manifest or {}).get("runs") or {}).get(task_id)
    run_entry = run_entry if isinstance(run_entry, dict) else None

    def pick(manifest_key: str, row_value: Any, row_has: bool) -> Judged:
        if run_entry is not None and manifest_key in run_entry:
            return Judged(run_entry[manifest_key])
        return Judged(row_value) if row_has else Judged(UNKNOWN, "manifest_missing")

    started = pick("started_at", ((result.get("provenance") or {}).get("runs") or {}).get(task_id, {}).get("started_at"),
                   task_id in ((result.get("provenance") or {}).get("runs") or {}))
    return {
        "attempt": Judged(task["attempt"]) if "attempt" in task else Judged(UNKNOWN, "manifest_missing"),
        "kind": Judged(task["kind"]) if "kind" in task else Judged(UNKNOWN, "manifest_missing"),
        "revision": Judged(task["revision"]) if "revision" in task else Judged(UNKNOWN, "manifest_missing"),
        "agent_spec_sha256": (Judged(manifest["agent_spec_sha256"]) if manifest and manifest.get("agent_spec_sha256")
                              else Judged(UNKNOWN, "manifest_missing")),
        "session_id": pick("session_id", result.get("session_id"), "session_id" in result),
        "resume_of": pick("resume_of", payload.get("resume_session_id"), "resume_session_id" in payload),
        "started_at": started,
    }


def judge_citation(target: str | None, run_reported: Iterable[str]) -> bool:
    """True when evidence of a run points at something that run did not report: a citation, not lineage."""
    return target is not None and target not in set(run_reported)


def judge_support(relation: str) -> str:
    return relation if relation in ("supports", "contradicts") else "context"


def judge_hash(hashes: Iterable[str]) -> Judged:
    found = tuple(sorted(set(hashes)))
    if len(found) == 1:
        return Judged(found[0])
    return Judged(UNKNOWN, "multiple_hashes", found) if found else Judged(UNKNOWN, "not_observed")


def judge_method(plan_sha256: str | None, step_id: str | None, packs: Iterable[Mapping[str, Any]]) -> tuple[Judged, list[str]]:
    method = judge_identity("method", plan_sha256, step_id)
    pack_keys = sorted(f"{p.get('id')}@{p.get('version')}" for p in packs)
    return (Judged(method) if method else Judged(UNKNOWN, "no_plan_hash")), pack_keys


def judge_data_type(rule: DataTypeRule, generator: str, declared: Mapping[str, Any] | None, path: str) -> Judged:
    if rule.source == "none":
        return Judged(UNKNOWN, "no_data_type_source")
    if generator == UNKNOWN:
        return Judged(UNKNOWN, "generator_unknown")
    types = {normalize_artifact_path(k): v for k, v in (declared or {}).items()}
    if path in types and types[path] in rule.vocabulary:
        return Judged(types[path])
    return Judged(UNKNOWN, "not_declared")


def judge_verification(rule: VerificationRule, spec: Mapping[str, Any] | None) -> dict[str, Judged]:
    contract = (spec or {}).get("contract") if spec else None
    if not contract:
        return {"contract_status": Judged(NONE), "verification_scope": Judged(UNKNOWN, "no_contract_record"),
                "method_validation": Judged(rule.method_validation)}
    probes = (contract.get("verification") or {}).get("probe_tools") or []
    scope = Judged(rule.probe_scope) if probes else Judged(UNKNOWN, "no_contract_record")
    return {"contract_status": Judged(contract.get("status") or UNKNOWN), "verification_scope": scope,
            "method_validation": Judged(rule.method_validation)}


def judge_independence(claim_id: str, result: ResearchResult) -> list[str]:
    return independent_groups(claim_id, result.evidence, result.links)


JUDGES: dict[str, Callable[..., Any]] = {
    "generation_from_reports": judge_generation,
    "reuse_from_declared_use": judge_reuse,
    "resume_by_session": judge_resume,
    "retry_by_attempt": judge_retry,
    "wake_by_parent": judge_wake,
    "run_meta_from_rows": judge_run_meta,
    "citation_not_lineage": judge_citation,
    "support_from_links": judge_support,
    "hash_not_identity": judge_hash,
    "scoped_identity": judge_identity,
    "method_from_plan": judge_method,
    "no_type_inheritance": judge_data_type,
    "probe_not_validation": judge_verification,
    "independence_by_ledger": judge_independence,
}


# ---------------------------------------------------------------- projection

@dataclass
class Projection:
    """In-memory view of the records on the model's concepts. Built per call; never stored."""

    model: SemanticModel
    runs: dict[str, dict] = field(default_factory=dict)
    artifacts: dict[str, dict] = field(default_factory=dict)
    externals: dict[str, dict] = field(default_factory=dict)
    evidence: dict[str, dict] = field(default_factory=dict)
    claims: dict[str, dict] = field(default_factory=dict)
    agents: dict[str, dict] = field(default_factory=dict)
    methods: dict[str, dict] = field(default_factory=dict)
    uses: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    out_edges: dict[str, list[dict]] = field(default_factory=dict)
    in_edges: dict[str, list[dict]] = field(default_factory=dict)
    run_cautions: dict[str, list[dict]] = field(default_factory=dict)
    results: dict[str, ResearchResult] = field(default_factory=dict)  # claim id -> result that records it

    def add_edge(self, src: str, rel: str, dst: str, basis: str, **extra: Any) -> dict:
        spec = self.model.spec.relations.get(rel)
        if spec is None:
            raise ModelError(f"relation {rel} is not in the model")
        if basis not in spec.basis:
            raise ModelError(f"relation {rel} does not allow basis {basis}")
        if spec.values and extra.get("value") not in spec.values:
            raise ModelError(f"relation {rel} value {extra.get('value')} is not declared")
        if self.model.concept_of(src) != spec.from_ or (dst != UNKNOWN and self.model.concept_of(dst) not in spec.to):
            raise ModelError(f"relation {rel} does not connect {self.model.concept_of(src)} to {self.model.concept_of(dst)}")
        edge = {"src": src, "rel": rel, "dst": dst, "basis": basis, **{k: v for k, v in extra.items() if v}}
        self.edges.append(edge)
        self.out_edges.setdefault(src, []).append(edge)
        self.in_edges.setdefault(dst, []).append(edge)
        return edge

    def check_reason(self, reason: str | None) -> None:
        if reason is not None and reason not in self.model.spec.reasons.unknown:
            raise ModelError(f"unknown reason {reason} is not declared in the model")


def _external_id(scheme: str, value: str, version: str | None = None) -> str:
    return f"ext:{scheme}:{normalize_id(scheme, value)}" + (f"@{version}" if version else "")


def _input_external(ref: str) -> str | None:
    match = re.fullmatch(r"([a-z][a-z0-9_]*):([^@/]+)@([^@/]+)", ref)
    return _external_id(match.group(1), match.group(2), match.group(3)) if match and match.group(1) not in (
        "step", "art") else None


def _source_external(source: Any) -> str | None:
    if source.id_scheme and source.id_value:
        return _external_id(source.id_scheme, source.id_value, source.version)
    if source.uri:
        parts = urlsplit(normalize_uri(source.uri))
        if parts.netloc in ("doi.org", "dx.doi.org") and parts.path.strip("/"):
            return f"ext:doi:{unquote(parts.path.strip('/')).casefold()}" + (f"@{source.version}" if source.version else "")
        return f"ext:uri:{normalize_uri(source.uri)}" + (f"@{source.version}" if source.version else "")
    return None


def _set_field(row: dict, name: str, judged: Judged, p: Projection) -> None:
    row[name] = judged.value
    if judged.value == UNKNOWN:
        p.check_reason(judged.reason)
        row.setdefault("unknown", {})[name] = judged.reason
        if judged.candidates:
            row.setdefault("candidates", {})[name] = list(judged.candidates)


def project(model: SemanticModel, records: Records) -> Projection:
    """Project records onto the model. Pure: reads ``records``, returns a new Projection."""
    p = Projection(model=model)
    spec = model.spec
    workdir_ids: dict[str, str] = {}      # result.workdir -> workdir_id
    ws_request: dict[str, set[str]] = {}  # workdir_id -> requests of the runs that wrote it
    run_ws: dict[str, str] = {}
    run_reported: dict[str, list[str]] = {}
    task_run: dict[str, str] = {}

    # runs and their row fields
    for tid, task in sorted(records.tasks.items()):
        result = task.get("result") or {}
        ws = result.get("workdir_id")
        rid = task.get("request_id")
        run = judge_identity("run", ws, tid) or f"run:?{tid}/{tid}"
        task_run[tid] = run
        if ws:
            run_ws[run] = ws
            ws_request.setdefault(ws, set()).add(rid)
            if result.get("workdir"):
                workdir_ids[result["workdir"]] = ws
        manifest = records.manifests.get(result.get("workdir") or "")
        meta = judge_run_meta(task, manifest, tid)
        plan = records.plans.get(rid) or {}
        method, packs = judge_method(((records.requests.get(rid) or {}).get("research_contract") or {}).get("plan_sha256"),
                                     task.get("step_id"), (plan.get("protocol") or {}).get("packs") or [])
        agent_id = (task.get("payload") or {}).get("agent_id")
        row: dict[str, Any] = {"id": run, "request": rid, "step": task.get("step_id"), "task_id": tid, "workspace": ws,
                               "agent": judge_identity("agent", agent_id) or UNKNOWN}
        _set_field(row, "agent_spec_sha256", meta["agent_spec_sha256"], p)
        for name in ("kind", "attempt"):
            _set_field(row, name, meta[name], p)
        _set_field(row, "retry", judge_retry(meta["attempt"].value), p)
        _set_field(row, "revision", meta["revision"], p)
        _set_field(row, "session_id", meta["session_id"], p)
        _set_field(row, "method", method, p)
        row["_packs"] = packs
        row["_meta"] = meta
        row["_output_types"] = ((task.get("payload") or {}).get("meta") or {}).get("output_types")
        p.runs[run] = row
        if method.value != UNKNOWN:
            p.methods.setdefault(method.value, {"id": method.value, "request": rid, "step": task.get("step_id"),
                                                "packs": packs})
        if agent_id:
            p.agents.setdefault(row["agent"], {"id": row["agent"], "_agent_id": agent_id})

    # resumes and wakes need every run in place
    for tid, task in sorted(records.tasks.items()):
        run = task_run[tid]
        row = p.runs[run]
        ws = run_ws.get(run)
        peers = [(other, o["_meta"]["session_id"].value, o["_meta"]["started_at"].value)
                 for other, o in p.runs.items() if other != run and ws is not None and run_ws.get(other) == ws]
        meta = row["_meta"]
        resume_of = meta["resume_of"].value
        resumed = (Judged(UNKNOWN, meta["resume_of"].reason) if resume_of == UNKNOWN else
                   judge_resume(resume_of, meta["started_at"].value if meta["started_at"].value != UNKNOWN else None,
                                peers))
        _set_field(row, "resumes", resumed, p)
        if resumed.reason == "shared_session":
            p.run_cautions.setdefault(run, []).append({"code": "resume_parent_ambiguous", "nodes": [run],
                                                       "candidates": list(resumed.candidates)})
        parent = task.get("parent_task")
        parent_task = records.tasks.get(parent) if parent else None
        woke = judge_wake(parent, (parent_task or {}).get("result") if parent_task else None,
                          task_run.get(parent) if parent else None)
        _set_field(row, "wake_of", woke, p)

    # artifacts: reported outputs first, then any declared or cited path inside a known workspace
    def artifact(rid: str | None, ws: str | None, path: str) -> str | None:
        art = judge_identity("artifact", rid, ws, path)
        if art and art not in p.artifacts:
            p.artifacts[art] = {"id": art, "request": rid, "workspace": ws, "path": path, "reported_by": []}
        return art

    for run, row in p.runs.items():
        task = records.tasks[row["task_id"]]
        reported = []
        for out in (task.get("result") or {}).get("outputs") or []:
            art = artifact(row["request"], row["workspace"], normalize_artifact_path(out))
            if art and art not in reported:   # one file under two spellings is one report
                p.artifacts[art]["reported_by"].append(run)
                reported.append(art)
        run_reported[run] = reported

    def workspace_request(ws: str) -> str | None:
        found = ws_request.get(ws) or set()
        return next(iter(found)) if len(found) == 1 else None

    def resolve_input(ref: str, rid: str | None) -> str | None:
        external = _input_external(ref)
        if external:
            return external
        if ref.startswith("step:"):
            step, _, path = ref[len("step:"):].partition("/")
            spaces = {row["workspace"] for row in p.runs.values() if row["request"] == rid and row["step"] == step}
            return artifact(rid, next(iter(spaces)), normalize_artifact_path(path)) if len(spaces) == 1 else None
        if ref.startswith("art:"):
            ref_rid, _, rest = ref[len("art:"):].partition("/")
            ws, _, path = rest.partition("/")
            return artifact(ref_rid, ws, normalize_artifact_path(path)) if ref_rid in (ws_request.get(ws) or set()) else None
        return None

    def resolve_cited(run: str, ref_path: str) -> str | None:
        workdir = records.tasks[p.runs[run]["task_id"]].get("result", {}).get("workdir")
        if not workdir:
            return None
        joined = posixpath.normpath(posixpath.join(workdir, ref_path.replace("\\", "/")))
        for known, ws in workdir_ids.items():
            if joined.startswith(known.rstrip("/") + "/"):
                rid = workspace_request(ws)
                return artifact(rid, ws, normalize_artifact_path(joined[len(known.rstrip("/")) + 1:])) if rid else None
        return None

    # performs, uses_method, used, reported_output, resumes, wake_of
    for run, row in sorted(p.runs.items()):
        if row["agent"] != UNKNOWN:
            p.add_edge(row["agent"], "performs", run, "reported")
        if row["method"] != UNKNOWN:
            p.add_edge(run, "uses_method", row["method"], "declared")
        plan = records.plans.get(row["request"]) or {}
        step = next((s for s in plan.get("steps") or [] if s.get("id") == row["step"]), None)
        used: list[str] = []
        for ref in (step or {}).get("input_refs") or []:
            dst = resolve_input(ref, row["request"])
            if dst is not None and dst in used:
                continue
            if dst is None:
                p.check_reason("unresolved_input_ref")
                p.add_edge(run, "used", UNKNOWN, "declared", unknown={"dst": "unresolved_input_ref"})
                used.append(UNKNOWN)
                continue
            if dst.startswith("ext:"):
                p.externals.setdefault(dst, {"id": dst, "used_by": [], "referred_by": [], "cited_by": []})["used_by"].append(run)
            p.add_edge(run, "used", dst, "declared")
            used.append(dst)
        row["_used"] = used
        for art in run_reported[run]:
            p.add_edge(run, "reported_output", art, "reported")
        if row["resumes"] not in (NONE, UNKNOWN):
            p.add_edge(run, "resumes", row["resumes"], "reported")
        elif row["resumes"] == UNKNOWN:
            p.add_edge(run, "resumes", UNKNOWN, "reported", unknown={"dst": row["unknown"]["resumes"]},
                       candidates={"dst": row["candidates"]["resumes"]} if row.get("candidates", {}).get("resumes")
                       else None)
        if row["wake_of"] not in (NONE, UNKNOWN):
            p.add_edge(run, "wake_of", row["wake_of"], "reported")

    # evidence, claims, refers_to, cites, bears_on
    for run, row in sorted(p.runs.items()):
        result = records.results.get(row["task_id"])
        if result is None:
            continue
        paths = {ref.artifact_id: ref.path for ref in result.artifact_refs}
        cited: list[str] = []
        for ev in result.evidence:
            ev_id = judge_identity("evidence", row["workspace"], row["task_id"], ev.id)
            target = None
            if ev.source is not None:
                target = _source_external(ev.source)
                if target is None and ev.source.artifact_id and ev.source.artifact_id in paths:
                    target = resolve_cited(run, paths[ev.source.artifact_id])
            p.evidence[ev_id] = {"id": ev_id, "run": run, "kind": ev.kind, "counts": ev.counts,
                                 "independence_group": ev.independence_group, "refers_to": target or UNKNOWN}
            if target:
                p.add_edge(ev_id, "refers_to", target, "cited")
                if target.startswith("ext:"):
                    p.externals.setdefault(target, {"id": target, "used_by": [], "referred_by": [], "cited_by": []}
                                           )["referred_by"].append(ev_id)
                if judge_citation(target, run_reported[run]) and target not in cited:
                    cited.append(target)
        for target in cited:
            p.add_edge(run, "cites", target, "cited")
            if target.startswith("ext:"):
                p.externals[target]["cited_by"].append(run)
        for claim in result.claims:
            cid = judge_identity("claim", row["request"], claim.id, str(claim.revision))
            p.claims[cid] = {"id": cid, "request": row["request"], "recorded_by": run, "status": claim.status,
                             "_claim_id": claim.id}
            p.results[cid] = result
        for link in result.links:
            cid = judge_identity("claim", row["request"], link.claim_id, str(link.claim_revision))
            ev_id = judge_identity("evidence", row["workspace"], row["task_id"], link.evidence_id)
            p.add_edge(cid, "bears_on", ev_id, "reported", value=judge_support(link.relation))

    # artifact row fields
    for art, row in sorted(p.artifacts.items()):
        row["reported_by"] = sorted(set(row["reported_by"]))
        gen = judge_generation(row["reported_by"])
        _set_field(row, "generated_by", gen, p)
        gen_row = p.runs.get(gen.value) if gen.value != UNKNOWN else None
        if gen_row is None:
            for name in ("generator_inputs", "method", "packs"):
                _set_field(row, name, Judged(UNKNOWN, "generator_unknown"), p)
        else:
            row["generator_inputs"] = sorted(gen_row["_used"])
            _set_field(row, "method", Judged(gen_row["method"], (gen_row.get("unknown") or {}).get("method")), p)
            row["packs"] = list(gen_row["_packs"])
        _set_field(row, "sha256", judge_hash(records.observed.get(row["workspace"] or "", {}).get(row["path"], ())), p)
        _set_field(row, "data_type", judge_data_type(spec.data_type, gen.value, gen_row["_output_types"] if gen_row else None,
                                                     row["path"]), p)
        if row["data_type"] != UNKNOWN:
            p.add_edge(art, "means", judge_identity("type", row["data_type"]), "declared")
    by_hash: dict[str, list[str]] = {}
    for art, row in p.artifacts.items():
        if row["sha256"] != UNKNOWN:
            by_hash.setdefault(row["sha256"], []).append(art)
    for art, row in p.artifacts.items():
        row["same_content_as"] = sorted(a for a in by_hash.get(row["sha256"], []) if a != art) if row["sha256"] != UNKNOWN else []

    # uses: artifact inputs only
    for edge in p.edges:
        if edge["rel"] != "used" or not edge["dst"].startswith("art:"):
            continue
        user = p.runs[edge["src"]]
        reporters = {r: p.runs[r]["request"] for r in p.artifacts[edge["dst"]]["reported_by"]}
        kind = judge_reuse(spec.reuse.scope, edge["src"], user["request"], reporters)
        use = {"run": edge["src"], "artifact": edge["dst"], "reuse_kind": kind.value}
        if kind.value == UNKNOWN:
            p.check_reason(kind.reason)
            use["unknown"] = {"reuse_kind": kind.reason}
        p.uses.append(use)

    # agents
    for agent, row in p.agents.items():
        for name, judged in judge_verification(spec.verification, records.contracts.get(row["_agent_id"])).items():
            _set_field(row, name, judged, p)
    return p


# ---------------------------------------------------------------- consumers

class SemanticsAdvisory(BaseModel):
    """What a consumer returns. Advisory: never a plan, receipt, claim status, ask, event or prompt input."""

    model_config = ConfigDict(frozen=True)

    status: Literal["advisory"] = "advisory"
    model: str
    model_sha256: str
    consumer: Literal["find_reusable", "audit_lineage"]
    query: dict[str, Any]
    result: dict[str, Any]


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in row.items() if not k.startswith("_")}


def _advisory(p: Projection, consumer: str, query: dict[str, Any], result: dict[str, Any]) -> SemanticsAdvisory:
    return SemanticsAdvisory(model=p.model.name, model_sha256=p.model.sha256, consumer=consumer,  # type: ignore[arg-type]
                             query={k: v for k, v in query.items() if v is not None}, result=result)


def _derived_from(p: Projection, art: str, target: str) -> str:
    """yes when generated_by -> used reaches target; unknown when the walk hits an unknown first."""
    seen: set[str] = set()
    stack = [(art, 0)]
    unknown = False
    while stack:
        node, depth = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if depth > p.model.spec.traversal.depth_limit:
            unknown = True
            continue
        row = p.artifacts.get(node)
        if row is None:
            continue
        gen = row["generated_by"]
        if gen == UNKNOWN:
            unknown = True
            continue
        for dst in p.runs[gen]["_used"]:
            if dst == target:
                return "yes"
            if dst == UNKNOWN:
                unknown = True
            elif dst.startswith("art:"):
                stack.append((dst, depth + 1))
    return UNKNOWN if unknown else "no"


def _used_by_request(p: Projection, art: str, rid: str) -> str:
    kinds = [use["reuse_kind"] for use in p.uses if use["artifact"] == art and p.runs[use["run"]]["request"] == rid]
    if any(k in p.model.spec.reuse.reuse_kinds for k in kinds):
        return "yes"
    return UNKNOWN if UNKNOWN in kinds else "no"


def find_reusable(p: Projection, *, key: str | None = None, method: str | None = None, derived_from: str | None = None,
                  used_by_request: str | None = None, data_type: str | None = None) -> SemanticsAdvisory:
    """Artifacts matching every given selector. ``data_type`` is checked, never used to filter."""
    selected: list[str] = []
    excluded_unknown: list[str] = []
    candidates: dict[str, dict] = {}
    for art, row in sorted(p.artifacts.items()):
        match: dict[str, str] = {}
        if key is not None:
            match["key"] = "yes" if row["path"] == normalize_artifact_path(key) else "no"
        if method is not None:
            match["method"] = UNKNOWN if row["packs"] == UNKNOWN else ("yes" if method in row["packs"] else "no")
        if derived_from is not None:
            match["derived_from"] = _derived_from(p, art, derived_from)
        if used_by_request is not None:
            match["used_by_request"] = _used_by_request(p, art, used_by_request)
        filters = list(match.values())
        if data_type is not None:
            match["data_type"] = (UNKNOWN if row["data_type"] == UNKNOWN else
                                  "yes" if row["data_type"] == data_type else "no")  # no broader inheritance
        if "no" in filters:
            continue
        (selected if all(v == "yes" for v in filters) else excluded_unknown).append(art)
        recommend = (row["generated_by"] != UNKNOWN and row["sha256"] != UNKNOWN and all(v == "yes" for v in filters)
                     and match.get("data_type", "yes") == "yes")
        candidates[art] = {**_public(row), "uses": [dict(u) for u in p.uses if u["artifact"] == art],
                           "match": match, "recommend": recommend}
    return _advisory(p, "find_reusable", {"key": key, "method": method, "derived_from": derived_from,
                                          "used_by_request": used_by_request, "data_type": data_type},
                     {"selected": selected, "excluded_unknown": excluded_unknown, "candidates": candidates})


@dataclass
class _Walk:
    edges: list[dict] = field(default_factory=list)
    cites: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    cautions: list[dict] = field(default_factory=list)
    nodes: set[str] = field(default_factory=set)       # artifacts and external sources reached


def _gap(p: Projection, row: Mapping[str, Any], name: str) -> dict:
    gap = {"node": row["id"], "field": name, "reason": row["unknown"][name],
           "basis": p.model.spec.field_basis[name]}
    if (row.get("candidates") or {}).get(name):
        gap["candidates"] = list(row["candidates"][name])
    return gap


def _walk(p: Projection, root: str) -> _Walk:
    walk = _Walk()
    seen_edges: set[int] = set()
    done: set[str] = set()
    limit = p.model.spec.traversal.depth_limit
    relations = p.model.spec.relations

    def keep(edge: dict, into: list[dict]) -> None:
        if id(edge) not in seen_edges:
            seen_edges.add(id(edge))
            into.append(edge)

    def visit(node: str, depth: int, path: tuple[str, ...]) -> None:
        if node in path:
            walk.cautions.append({"code": "cycle", "nodes": [node]})
            return
        if node in done:
            return
        if depth > limit:
            walk.cautions.append({"code": "depth_limit", "nodes": [node]})
            return
        done.add(node)
        concept = p.model.concept_of(node)
        if concept in ("artifact", "external_source"):
            walk.nodes.add(node)
        if concept == "artifact":
            row = p.artifacts[node]
            walk.gaps.extend(_gap(p, row, f) for f in ("generated_by", "sha256") if row[f] == UNKNOWN)
            if row["generated_by"] == UNKNOWN:
                for edge in p.in_edges.get(node, []):   # several reporters: show them, walk none
                    if edge["rel"] == "reported_output":
                        keep(edge, walk.edges)
                return
        if concept == "run":
            row = p.runs[node]
            walk.gaps.extend(_gap(p, row, f) for f in ("agent_spec_sha256", "resumes") if row[f] == UNKNOWN)
            walk.cautions.extend(copy.deepcopy(p.run_cautions.get(node, [])))
        nexts: list[str] = []
        for edge in p.out_edges.get(node, []) + p.in_edges.get(node, []):
            lineage = relations[edge["rel"]].lineage
            at_src = edge["src"] == node
            if lineage.role == "record" and (lineage.at == "src") == at_src:
                keep(edge, walk.edges)
            elif lineage.role == "cites" and at_src:
                keep(edge, walk.cites)
            elif lineage.role == "follow" and (lineage.direction == "down") == at_src:
                if edge["rel"] == "reported_output" and p.artifacts[node]["generated_by"] != edge["src"]:
                    continue
                keep(edge, walk.edges)
                other = edge["dst"] if at_src else edge["src"]
                if other != UNKNOWN:
                    nexts.append(other)
        for other in nexts:
            visit(other, depth + 1, (*path, node))

    visit(root, 0, ())
    return walk


def _sorted_edges(edges: Iterable[dict]) -> list[dict]:
    return sorted((dict(e) for e in edges), key=lambda e: (e["src"], e["rel"], e["dst"], e.get("value", "")))


def audit_lineage(p: Projection, *, claim: str | None = None, evidence: str | None = None, artifact: str | None = None,
                  run: str | None = None, step: str | None = None) -> SemanticsAdvisory:
    """Lineage of one root: the edges walked, citations kept apart, gaps and cautions."""
    given = {k: v for k, v in {"claim": claim, "evidence": evidence, "artifact": artifact, "run": run,
                               "step": step}.items() if v is not None}
    if len(given) != 1:
        raise ValueError("audit_lineage takes exactly one root")
    kind, root = next(iter(given.items()))
    if kind == "step":
        rid, _, step_id = root.partition("/")
        runs = sorted(r for r, row in p.runs.items() if row["request"] == rid and row["step"] == step_id)
        rels = [e for r in runs for e in p.out_edges.get(r, [])]
        result = {"root": root, "runs": runs, "run_count": len(runs),
                  "retry_count": sum(1 for r in runs if p.runs[r]["retry"] is True),
                  "resumes": _sorted_edges(e for e in rels if e["rel"] == "resumes"),
                  "wake_of": _sorted_edges(e for e in rels if e["rel"] == "wake_of")}
        return _advisory(p, "audit_lineage", given, result)
    table = {"claim": p.claims, "evidence": p.evidence, "artifact": p.artifacts, "run": p.runs}[kind]
    if root not in table:
        raise KeyError(f"{kind} {root} is not in the records")
    walk = _walk(p, root)
    result: dict[str, Any] = {"root": root, "edges": _sorted_edges(walk.edges), "cites": _sorted_edges(walk.cites),
                              "gaps": sorted(walk.gaps, key=lambda g: (g["node"], g["field"])),
                              "cautions": walk.cautions}
    if kind == "claim":
        links = [e for e in p.out_edges.get(root, []) if e["rel"] == "bears_on"]
        bears = {"supports": [], "contradicts": [], "context": []}
        for edge in links:
            bears[edge["value"]].append(edge["dst"])
        result["bears_on"] = {k: sorted(v) for k, v in bears.items()}
        counted = sorted(bears["supports"] + bears["contradicts"])
        lineages = {ev: _walk(p, ev).nodes for ev in counted}
        shared = []
        for i, first in enumerate(counted):
            for second in counted[i + 1:]:
                common = sorted(lineages[first] & lineages[second])
                if common:
                    shared.append({"evidence": [first, second], "nodes": common})
        result["shared_ancestors"] = shared
        result["independent_groups"] = judge_independence(p.claims[root]["_claim_id"], p.results[root])
        result["cautions"] = walk.cautions + [{"code": "shared_ancestor_review", **s} for s in shared]
    if kind in ("artifact", "run"):
        result["node"] = _public(table[root])
    if kind == "run":
        row = p.runs[root]
        agent = p.agents.get(row["agent"])
        if agent is not None:
            result["agent_verification"] = {"agent": agent["id"], **{k: v for k, v in _public(agent).items() if k != "id"}}
        result["uses"] = [dict(u) for u in p.uses if u["run"] == root]
    return _advisory(p, "audit_lineage", given, result)
