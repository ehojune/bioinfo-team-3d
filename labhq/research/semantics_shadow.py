"""Shadow mode of the semantics model (#150 B1): computed after a request ends, written locally only.

Off by default. With ``semantics: shadow`` the gateway hands a copy of a finished request's rows to one
daemon worker thread, which computes two read-only models within a time cap:

1. the provenance model of ``semantics.py`` (#136): reuse candidates and lineage audit counts;
2. the object and link view of ``semantics_objects.py``.

Both results go side by side on one JSON line under ``<gateway.state_dir>/semantics/``, outside the git work
tree. A line carries ids, kinds, hashes and counts only: no prompt, answer, file content, path, data zone
or reference value. Nothing is read back into a prompt, plan, approval, receipt, round record or the web
office, and every failure here ends in a warning and a metric, never in the request.

The gateway imports this module only when the setting is not off; every line that wires it in elsewhere
ends with ``# semantics-hook`` so ``scripts/semantics_shadow_remove.py`` can take it out again.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import platform
import queue
import re
import stat
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..policy import _inside, _norm
from ..util import atomic_write_text
from . import semantics as sem
from .semantics_objects import build_view, opaque, summarize

log = logging.getLogger("labhq.semantics")

MODES = ("off", "shadow")
MODELS = ("provenance", "objects")  # the two models a line carries side by side
HELD_MODES = ("advisory", "ab")  # B2: CSO advisory and A/B, held by the PI (#149)
KEYS = ("mode", "timeout_s", "history_requests")
DEFAULT_TIMEOUT_S = 5.0
STUCK_S = 10.0                   # a job running longer than this turns the shadow off
IDLE_EXIT_S = 60.0               # an idle worker thread ends; the next request starts a new one
HASH_MAX_FILES = 200
HASH_MAX_TOTAL = 2 * 1024 ** 3
HASH_MAX_FILE = 512 * 1024 ** 2
HASH_CHUNK = 8 * 1024 ** 2
MANIFEST_MAX = 1024 ** 2
MAX_TASK_ROWS = 20_000
MAX_EDGES = 50_000
MAX_LINEAGE_ROOTS = 200
MAX_CANDIDATE_REFS = 5
OBSERVED_MAX = 20_000
RETENTION_DAYS = 90
LOG_PART_BYTES = 23 * 1024 ** 2  # two parts plus observed.json (OBSERVED_MAX entries) stay under 50 MiB
REASONS = ("type_unknown", "hash_unknown", "zone_excluded", "version_changed", "not_generated", "incomplete")
RUN_FIELDS = ("agent_spec_sha256", "kind", "attempt", "retry", "revision", "session_id", "method", "resumes",
              "wake_of")
ART_FIELDS = ("generated_by", "generator_inputs", "method", "packs", "sha256", "data_type")


# ---------------------------------------------------------------- settings

@dataclass(frozen=True)
class ShadowConfig:
    timeout_s: float = DEFAULT_TIMEOUT_S
    history_requests: int = 200


_warned: set[str] = set()


def _mode(value: Any) -> str | None:
    if value is None or value is False:  # YAML reads a bare `off` as false
        return "off"
    return value if isinstance(value, str) else None


def _ignored(raw: Any, why: str) -> None:
    key = repr(raw)[:500]
    if key not in _warned:
        _warned.add(key)
        log.warning("semantics setting ignored (%s); semantics stays off", why)
    return None


def resolve(raw: Any) -> ShadowConfig | None:
    """The shadow config, or None for off. A bad value turns semantics off with one warning, never raises."""
    try:
        options: Mapping[str, Any] = {}
        if isinstance(raw, Mapping):
            unknown = sorted(str(k) for k in raw if k not in KEYS)
            if unknown:
                return _ignored(raw, f"unknown keys {unknown}")
            options = raw
            mode = _mode(raw.get("mode", "off"))
        else:
            mode = _mode(raw)
        if mode == "off":
            return None
        if mode in HELD_MODES:
            return _ignored(raw, f"mode {mode} is held (B2); this version supports off and shadow")
        if mode not in MODES:
            return _ignored(raw, "mode must be off or shadow")
        timeout = options.get("timeout_s", DEFAULT_TIMEOUT_S)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= STUCK_S:
            return _ignored(raw, f"timeout_s must be a number from 0.1 to {STUCK_S:g}")
        history = options.get("history_requests", 200)
        if isinstance(history, bool) or not isinstance(history, int) or not 10 <= history <= 1000:
            return _ignored(raw, "history_requests must be an integer from 10 to 1000")
        return ShadowConfig(timeout_s=float(timeout), history_requests=history)
    except Exception as exc:  # noqa: BLE001 - a setting must never break the gateway
        return _ignored(raw, type(exc).__name__)


# ---------------------------------------------------------------- local files

@dataclass(frozen=True)
class ShadowPaths:
    root: Path

    @property
    def log(self) -> Path:
        return self.root / "shadow.jsonl"

    @property
    def log_old(self) -> Path:
        return self.root / "shadow.1.jsonl"


    @property
    def observed(self) -> Path:
        return self.root / "observed.json"


def shadow_root(settings: Any) -> Path:
    return settings.path(settings.gateway.state_dir) / "semantics"


def inside_git_tree(path: Path) -> bool:
    resolved = Path(os.path.abspath(path))
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_ts(path: Path) -> float | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return float(json.loads(handle.readline()).get("ts"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def prune(paths: ShadowPaths, now: float) -> None:
    """Keep about RETENTION_DAYS: a part is rotated after half of it and deleted after the other half."""
    half = RETENTION_DAYS * 86400 / 2
    for part, age in ((paths.log_old, half), (paths.log, 2 * half)):
        try:
            if part.stat().st_mtime < now - age:
                part.unlink()
        except FileNotFoundError:
            pass


def append_line(paths: ShadowPaths, line: Mapping[str, Any]) -> None:
    """Append one JSON line. Two parts of at most LOG_PART_BYTES each, together about RETENTION_DAYS."""
    paths.root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        first = _first_ts(paths.log)
        if paths.log.stat().st_size > LOG_PART_BYTES or (first is not None and
                                                          first < now - RETENTION_DAYS * 86400 / 2):
            os.replace(paths.log, paths.log_old)
    except FileNotFoundError:
        pass
    prune(paths, now)
    with open(paths.log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def read_lines(paths: ShadowPaths) -> tuple[list[dict], int]:
    lines: list[dict] = []
    broken = 0
    for part in (paths.log_old, paths.log):
        try:
            text = part.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        for raw in text.splitlines():
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                broken += 1
                continue
            if isinstance(value, dict):
                lines.append(value)
            else:
                broken += 1
    return lines, broken


# ---------------------------------------------------------------- snapshot (event loop side, no file reads)

def _lane(req: Mapping[str, Any]) -> str:
    if req.get("mode") == "direct":
        return "direct"
    return "research" if (req.get("intake") or {}).get("work_kind") == "research" else "general"


def _light_request(req: Mapping[str, Any]) -> dict:
    plan = req.get("plan") if isinstance(req.get("plan"), dict) else {}
    research = bool(req.get("research_contract"))
    if research:
        plan_copy: Any = plan  # validated as a ResearchPlan by the provenance model
    else:
        plan_copy = {"steps": [{k: s.get(k) for k in ("id", "agent_id", "depends_on", "outputs", "instruction")}
                               for s in plan.get("steps") or [] if isinstance(s, dict)]}
    results = {sid: {k: r.get(k) for k in ("task_id", "agent_id", "ok", "status", "outputs", "workdir_id", "workdir")}
               for sid, r in (req.get("results") or {}).items() if isinstance(r, dict)}
    contract = req.get("research_contract") if isinstance(req.get("research_contract"), dict) else {}
    return {"id": req.get("id"), "project_id": req.get("project_id"), "mode": req.get("mode"),
            "status": req.get("status"), "created_at": req.get("created_at"), "lane": _lane(req),
            "research_contract": {"plan_sha256": contract.get("plan_sha256")} if research else None,
            "plan": plan_copy, "results": results, "text": req.get("text"),
            "references": [{"kind": r.get("kind"), "value": r.get("value")} for r in req.get("references") or []
                           if isinstance(r, dict)]}


def _light_task(task: Mapping[str, Any], research: set) -> dict:
    light = {k: task.get(k) for k in ("request_id", "step_id", "kind", "attempt", "revision", "parent_task",
                                      "accepted", "completed", "runner_id") if k in task}
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    light["payload"] = {k: payload.get(k) for k in ("id", "agent_id", "resume_session_id") if k in payload}
    light["payload"]["meta"] = {k: meta.get(k) for k in ("output_types", "outputs") if k in meta}
    result = task.get("result")
    if isinstance(result, dict):
        kept = {k: result.get(k) for k in ("task_id", "agent_id", "ok", "session_id", "workdir", "workdir_id",
                                           "outputs", "missing_outputs", "pending_jobs", "pending_asks", "error_kind")
                if k in result}
        runs = ((result.get("provenance") or {}).get("runs") or {}) if isinstance(result.get("provenance"), dict) else {}
        kept["provenance"] = {"runs": {tid: {k: run.get(k) for k in ("started_at", "ended_at") if k in run}
                                       for tid, run in runs.items() if isinstance(run, dict)}}
        if task.get("request_id") in research and task.get("step_id") and result.get("structured") is not None:
            kept["structured"] = result["structured"]
        light["result"] = kept
    elif result is not None:
        light["result"] = result  # left for the model to count as an invalid row
    return light


def take_snapshot(hub: Any, rid: str, cfg: ShadowConfig) -> dict:
    """A copy of what the two models need, taken on the event loop. Shares nothing with live state."""
    started = time.perf_counter()
    req = hub.requests[rid]
    project_id = req.get("project_id")
    others = sorted((r for r in hub.requests.values() if r.get("project_id") == project_id and r.get("id") != rid),
                    key=lambda r: r.get("created_at") or 0, reverse=True)
    chosen = [req, *others[:cfg.history_requests - 1]]
    rids = {r.get("id") for r in chosen}
    research = {r.get("id") for r in chosen if r.get("research_contract")}
    own: dict[str, dict] = {}
    history: dict[str, dict] = {}
    for tid, task in hub.store.all("task").items():
        if task.get("request_id") == rid:
            own[tid] = task
        elif task.get("request_id") in rids:
            history[tid] = task
    tasks_truncated = len(own) + len(history) > MAX_TASK_ROWS
    kept = {**own, **dict(sorted(history.items())[:max(0, MAX_TASK_ROWS - len(own))])}
    tasks = {tid: _light_task(task, research) for tid, task in sorted(kept.items())}
    approvals = []
    for aid, decision in hub.store.all("approval_decision").items():
        approval = decision.get("approval") or {}
        if approval.get("request_id") == rid:
            state = decision.get("state") or ("approved" if decision.get("approved") else "denied")
            approvals.append({"id": aid, "kind": approval.get("kind"), "task_id": approval.get("task_id"),
                              "request_id": rid, "state": state})
    for aid, entry in hub.approvals.items():
        approval = entry.get("approval") or {}
        if approval.get("request_id") == rid:
            approvals.append({"id": aid, "kind": approval.get("kind"), "task_id": approval.get("task_id"),
                              "request_id": rid, "state": "pending"})
    jobs_done = {tid: {"jobs": [{"job_id": j.get("job_id"), "state": j.get("state")}
                                for j in (hub.jobs_done.get(tid) or {}).get("jobs") or [] if isinstance(j, dict)]}
                 for tid in own if tid in hub.jobs_done}
    project = hub.s.project(project_id)
    snap = {
        "rid": rid, "ts": time.time(),
        "requests": {r.get("id"): _light_request(r) for r in chosen},
        "history_truncated": len(others) > len(chosen) - 1, "tasks_truncated": tasks_truncated,
        "tasks": tasks, "approvals": approvals, "jobs_done": jobs_done,
        "agents": {aid: {"engine": a.get("engine"), "employment": a.get("employment")}
                   for aid, a in hub.agents.items()},
        "project": {"id": project_id, "visibility": project.visibility if project else None,
                    "local_dir": project.local_dir if project else None, "name": project.name if project else None},
        "zones": [[z.path, z.level] for z in hub.s.policy.data_zones],
        "workspace_root": str(hub.s.path(hub.s.runner.workspace_root)),
        "host": platform.node(),
    }
    snap = json.loads(json.dumps(snap, default=str))
    snap["snapshot_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return snap


# ---------------------------------------------------------------- file reads: zones, same disk, hash

class ShadowStop(Exception):
    """The job ran past its time cap or was abandoned."""


class ShadowTimeout(ShadowStop):
    pass


def _zone_level(path: str, zones: list[tuple[str, str]]) -> str | None:
    inside = [(len(z), level) for z, level in zones if _inside(path, z)]
    if any(level == "restricted" for _, level in inside):
        return "restricted"
    return max(inside)[1] if inside else None


def zone_allows(path: str, zones: Iterable[Iterable[str]], visibility: str | None) -> bool:
    """Only `public` zones for a public project, `public` and `internal` otherwise. Outside every zone: no."""
    allowed = {"public"} if visibility == "public" else {"public", "internal"}
    normalized = [(_norm(z), level) for z, level in zones]
    try:
        real = _norm(os.path.realpath(path))
    except (OSError, ValueError):
        return False
    return all(_zone_level(p, normalized) in allowed for p in {_norm(path), real})


def _is_link(st: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & reparse)


def _unc(path: str) -> bool:
    return path.startswith(("\\\\", "//"))


@dataclass
class HashBudget:
    files: int = 0
    bytes: int = 0
    exhausted: bool = False


class Reader:
    """Read-only access to workspace files on this disk, inside allowed zones, within the budget."""

    def __init__(self, snap: Mapping[str, Any], check: Callable[[], None]):
        self.zones = [tuple(z) for z in snap.get("zones") or []]
        self.visibility = (snap.get("project") or {}).get("visibility")
        self.root = snap.get("workspace_root") or ""
        self.host = snap.get("host")
        self.check = check
        self.budget = HashBudget()
        self.manifests: dict[str, dict | None] = {}
        self.workspace_state: dict[str, str] = {}

    def workspace(self, workdir: Any) -> str:
        """`ok`, or why this workspace is not read: remote, zone_excluded, not_regular, missing."""
        if not isinstance(workdir, str) or not workdir:
            return "missing"
        if workdir in self.workspace_state:
            return self.workspace_state[workdir]
        state = self._workspace(workdir)
        self.workspace_state[workdir] = state
        return state

    def _workspace(self, workdir: str) -> str:
        if _unc(workdir) or not os.path.isabs(workdir) or not self.root:
            return "remote"
        if not _inside(_norm(workdir), _norm(self.root)):
            return "remote"
        if not zone_allows(workdir, self.zones, self.visibility):
            return "zone_excluded"
        try:
            if _norm(os.path.realpath(workdir)) != _norm(workdir):
                return "not_regular"  # the workspace or a parent is a link or junction
            st = os.lstat(workdir)
        except FileNotFoundError:
            return "missing"
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            return "not_regular"
        manifest = self._small_json(os.path.join(workdir, "manifest.json"))
        self.manifests[workdir] = manifest
        if not isinstance(manifest, dict) or manifest.get("host") != self.host:
            return "remote"  # a manifest written on another host: the runner's disk is not this one
        return "ok"

    def _small_json(self, path: str) -> Any:
        try:
            st = os.lstat(path)
            if _is_link(st) or not stat.S_ISREG(st.st_mode) or st.st_size > MANIFEST_MAX:
                return None
            with open(path, "rb") as handle:
                return json.loads(handle.read(MANIFEST_MAX + 1).decode("utf-8"))
        except (OSError, ValueError):
            return None

    def file(self, workdir: str, rel: str) -> tuple[str, dict | None]:
        """(`hashed`, {sha256, size, mtime_ns}) or (reason, None)."""
        state = self.workspace(workdir)
        if state != "ok":
            return state, None
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or ".." in parts or re.match(r"^[A-Za-z]:", rel) or rel.startswith(("/", "\\")):
            return "not_regular", None
        current = workdir
        try:
            for part in parts[:-1]:
                current = os.path.join(current, part)
                st = os.lstat(current)
                if _is_link(st) or not stat.S_ISDIR(st.st_mode):
                    return "not_regular", None
            path = os.path.join(current, parts[-1])
            st = os.lstat(path)
        except FileNotFoundError:
            return "missing", None
        if _is_link(st) or not stat.S_ISREG(st.st_mode):
            return "not_regular", None
        if not zone_allows(path, self.zones, self.visibility):
            return "zone_excluded", None
        if st.st_size > HASH_MAX_FILE:
            self.budget.exhausted = True
            return "too_large", None
        if self.budget.files >= HASH_MAX_FILES or self.budget.bytes + st.st_size > HASH_MAX_TOTAL:
            self.budget.exhausted = True
            return "budget", None
        self.budget.files += 1
        self.budget.bytes += st.st_size
        digest = hashlib.sha256()
        with open(path, "rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_ino, opened.st_dev) != (st.st_ino, st.st_dev):
                return "not_regular", None  # replaced between the check and the open
            while True:
                self.check()
                chunk = handle.read(HASH_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        after = os.stat(path)
        if (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
            return "unstable", None
        return "hashed", {"sha256": digest.hexdigest(), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


# ---------------------------------------------------------------- the two models

_MODEL: list[Any] = []
_SERVICES = itertools.count(1)


def _model() -> Any:
    if not _MODEL:
        _MODEL.append(sem.load_model())
    return _MODEL[0]


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _failed(status: str, exc: BaseException, started: float) -> dict:
    return {"status": status, "error_kind": type(exc).__name__, "ms": _ms(started)}


def compute_objects(snap: Mapping[str, Any], check: Callable[[], None]) -> dict:
    started = time.perf_counter()
    try:
        summary = summarize(build_view(snap))
        check()
        return {"status": "ok", "ms": _ms(started), **summary}
    except ShadowTimeout as exc:
        return _failed("timeout", exc, started)
    except ShadowStop:
        raise
    except Exception as exc:  # noqa: BLE001 - fail open: a metric, never the request
        return _failed("error", exc, started)


def _artifact_locations(snap: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    """(workdir_id, normalized path) -> workdir, from every task row that reported it."""
    found: dict[tuple[str, str], str] = {}
    for task in (snap.get("tasks") or {}).values():
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        ws, workdir = result.get("workdir_id"), result.get("workdir")
        if not isinstance(ws, str) or not isinstance(workdir, str):
            continue
        for out in result.get("outputs") or []:
            if isinstance(out, str):
                found.setdefault((ws, sem.normalize_artifact_path(out)), workdir)
    return found


def _unknown_ratio(rows: Iterable[tuple[Mapping[str, Any], tuple[str, ...]]]) -> float | None:
    total = unknown = 0
    for row, fields in rows:
        total += sum(1 for f in fields if f in row)
        unknown += sum(1 for f in fields if f in (row.get("unknown") or {}))
    return round(unknown / total, 4) if total else None


def compute_provenance(snap: Mapping[str, Any], reader: Reader, observed: dict[str, dict],
                       check: Callable[[], None]) -> tuple[dict, dict]:
    """(provenance summary, hash summary). ``observed`` (opaque artifact key -> first observation) is updated."""
    started = time.perf_counter()
    hashes = {"hashed": 0, "bytes": 0, "observed_new": 0, "verified": 0, "changed": 0, "skipped": {}}
    try:
        rid = snap["rid"]
        model = _model()
        records, invalid = sem.records_from_rows(snap["requests"], snap["tasks"])
        check()
        first = sem.project(model, records)
        check()
        locations = _artifact_locations(snap)
        task_ok = {tid: bool((t.get("result") or {}).get("ok")) for tid, t in records.tasks.items()
                   if isinstance(t.get("result"), dict)}

        def generator_ok(row: Mapping[str, Any], p: Any) -> bool:
            run = p.runs.get(row.get("generated_by")) if row.get("generated_by") != sem.UNKNOWN else None
            return bool(run) and task_ok.get(run["task_id"], False)

        def skip(reason: str) -> None:
            hashes["skipped"][reason] = hashes["skipped"].get(reason, 0) + 1

        # hash this request's outputs (first observation) and the eligible earlier ones (re-check)
        hash_state: dict[str, str] = {}
        seen_now: dict[str, dict[str, str]] = {}
        order = sorted(first.artifacts.items(), key=lambda kv: kv[1]["request"] != rid)
        for art, row in order:
            own = row["request"] == rid
            key = opaque("art", art)
            if not own and not (generator_ok(row, first) and row["data_type"] != sem.UNKNOWN):
                hash_state[art] = "not_checked"  # fails another rule first: no file read needed
                continue
            workdir = locations.get((row["workspace"], row["path"]))
            where = reader.workspace(workdir)  # zone and host first: no read outside them
            if where != "ok":
                hash_state[art] = where
                skip(where)
                continue
            if not own and key not in observed:
                hash_state[art] = "not_observed"
                continue
            status, seen = reader.file(workdir, row["path"])
            check()
            if seen is None:
                hash_state[art] = status
                skip(status)
                continue
            hashes["hashed"] += 1
            hashes["bytes"] += seen["size"]
            if own:
                if key not in observed:
                    observed[key] = {"sha256": seen["sha256"], "size": seen["size"], "at": time.time()}
                    hashes["observed_new"] += 1
                hash_state[art] = "observed" if observed[key]["sha256"] == seen["sha256"] else "changed"
            else:
                same = (observed[key]["sha256"], observed[key]["size"]) == (seen["sha256"], seen["size"])
                hash_state[art] = "verified" if same else "changed"
                hashes["verified" if same else "changed"] += 1
            if hash_state[art] in ("observed", "verified"):
                seen_now.setdefault(row["workspace"], {})[row["path"]] = seen["sha256"]
        check()
        records, _ = sem.records_from_rows(snap["requests"], snap["tasks"], observed=seen_now)
        p = sem.project(model, records)
        check()
        incomplete = bool(invalid or snap.get("history_truncated") or snap.get("tasks_truncated")
                          or len(p.edges) > MAX_EDGES or reader.budget.exhausted)
        advisory = sem.find_reusable(p)
        excluded = {reason: 0 for reason in REASONS}
        candidates: list[tuple[float, str]] = []
        population = 0
        created = {r: (req.get("created_at") or 0) for r, req in snap["requests"].items()}
        for art, row in advisory.result["candidates"].items():
            if row["request"] == rid:
                continue
            population += 1
            reasons = []
            if incomplete:
                reasons.append("incomplete")
            if row["generated_by"] == sem.UNKNOWN or not generator_ok(row, p):
                reasons.append("not_generated")
            if row["data_type"] == sem.UNKNOWN:
                reasons.append("type_unknown")
            state = hash_state.get(art)
            if state == "zone_excluded":
                reasons.append("zone_excluded")
            elif state == "changed":
                reasons.append("version_changed")
            elif state not in ("verified", "not_checked"):
                reasons.append("hash_unknown")
            for reason in reasons:
                excluded[reason] += 1
            if not reasons and row["recommend"]:
                candidates.append((created.get(row["request"], 0), art))
        check()
        lineage = {"roots": 0, "edges": 0, "cites": 0, "gaps": 0, "cautions": 0}
        roots = [("artifact", a) for a, row in sorted(p.artifacts.items()) if row["request"] == rid]
        roots += [("claim", c) for c, row in sorted(p.claims.items()) if row["request"] == rid]
        for kind, root in roots[:MAX_LINEAGE_ROOTS]:
            result = sem.audit_lineage(p, **{kind: root}).result
            lineage["roots"] += 1
            for key in ("edges", "cites", "gaps", "cautions"):
                lineage[key] += len(result.get(key) or [])
            check()
        own_runs = [(row, RUN_FIELDS) for row in p.runs.values() if row["request"] == rid]
        own_arts = [(row, ART_FIELDS) for row in p.artifacts.values() if row["request"] == rid]
        candidates.sort(reverse=True)
        summary = {
            "status": "ok", "ms": _ms(started), "model_sha256": model.sha256, "rows_invalid": invalid,
            "runs": len(own_runs), "artifacts": len(own_arts), "edges": len(p.edges),
            "unknown_ratio": _unknown_ratio(own_runs + own_arts), "incomplete": incomplete,
            "history_artifacts": population, "candidates": len(candidates),
            "candidate_refs": ["sem:" + opaque("art", art)[:8] for _, art in candidates[:MAX_CANDIDATE_REFS]],
            "excluded": excluded, "lineage": lineage,
        }
        return summary, hashes
    except ShadowTimeout as exc:
        return _failed("timeout", exc, started), hashes
    except ShadowStop:
        raise
    except Exception as exc:  # noqa: BLE001 - fail open: a metric, never the request
        return _failed("error", exc, started), hashes


def compute_line(snap: Mapping[str, Any], observed: dict[str, dict], check: Callable[[], None], *,
                 epoch: int) -> dict:
    """One request line: both models side by side, ids, kinds, hashes and counts only."""
    started = time.perf_counter()
    rid = snap["rid"]
    req = snap["requests"][rid]
    project = req.get("project_id")
    line: dict[str, Any] = {
        "v": 1, "type": "request", "ts": round(time.time(), 3), "epoch": epoch, "request_id": rid,
        "project": opaque("project", project)[:12] if project else None, "lane": req.get("lane"),
        "mode": req.get("mode"), "status": req.get("status"),
        "rows": {"requests": len(snap["requests"]), "tasks": len(snap["tasks"]),
                 "history_truncated": bool(snap.get("history_truncated")),
                 "tasks_truncated": bool(snap.get("tasks_truncated"))},
        "busy_skipped": int(snap.get("busy_skipped") or 0), "snapshot_ms": snap.get("snapshot_ms"),
    }
    line["objects"] = compute_objects(snap, check)
    reader = Reader(snap, check)
    line["provenance"], hashes = compute_provenance(snap, reader, observed, check)
    workspaces: dict[str, int] = {}
    for state in reader.workspace_state.values():
        workspaces[state] = workspaces.get(state, 0) + 1
    line["hash"] = {**hashes, "workspaces": dict(sorted(workspaces.items()))}
    line["ms"] = _ms(started)
    return line


# ---------------------------------------------------------------- worker

class ShadowService:
    """One daemon worker thread and a queue of one. A busy worker skips the request and counts it."""

    def __init__(self, hub: Any, cfg: ShadowConfig, paths: ShadowPaths):
        self.hub, self.cfg, self.paths = hub, cfg, paths
        self.lock = threading.RLock()
        self.queue: queue.Queue = queue.Queue(maxsize=1)
        self.thread: threading.Thread | None = None
        self.gen = 0
        self.epoch = 1
        self.busy_skipped = 0
        self.observed: dict[str, dict] | None = None
        self.observed_dirty = False
        self.counts = {"lines": 0, "failures": 0, "busy": 0, "discarded": 0}
        self.thread_name = f"labhq-semantics-shadow-{next(_SERVICES)}"
        self.pending = 0  # queued or running jobs

    @classmethod
    def start(cls, hub: Any) -> "ShadowService | None":
        try:
            cfg = resolve(hub.s.semantics)
            if cfg is None:
                return None
            root = shadow_root(hub.s)
            if inside_git_tree(root):
                log.warning("semantics shadow refused: gateway.state_dir is inside a git work tree; semantics stays off")
                return None
            service = cls(hub, cfg, ShadowPaths(root))
            prune(service.paths, time.time())
            return service
        except Exception as exc:  # noqa: BLE001
            log.warning("semantics shadow could not start (%s); semantics stays off", type(exc).__name__)
            return None

    # -- event loop side
    def after_request(self, rid: str) -> None:
        """Queue the finished request for the worker. Never raises, never waits, reads no workspace file."""
        try:
            snap = take_snapshot(self.hub, rid, self.cfg)
            snap["busy_skipped"] = self.busy_skipped
            with self.lock:
                job = (self.gen, self.queue, snap)
                try:
                    self.queue.put_nowait(job)
                    self.pending += 1
                except queue.Full:
                    self.busy_skipped += 1
                    self.counts["busy"] += 1
                else:
                    self.busy_skipped = 0
                    self.ensure_thread()
        except Exception as exc:  # noqa: BLE001 - the request already finished; this must not touch it
            log.warning("semantics shadow skipped a request (%s)", type(exc).__name__)
            self.outcome(failed=True)

    def ensure_thread(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, args=(self.queue,), name=self.thread_name, daemon=True)
            self.thread.start()

    # -- worker thread
    def run(self, jobs: queue.Queue) -> None:
        while True:
            try:
                gen, _, snap = jobs.get(timeout=IDLE_EXIT_S)
            except queue.Empty:
                with self.lock:  # after_request puts and starts a thread under the same lock
                    if jobs.empty():
                        if self.thread is threading.current_thread():
                            self.thread = None
                        return
                continue
            try:
                if jobs is not self.queue:
                    return
                if gen != self.gen:
                    self.counts["discarded"] += 1
                    continue
                self.work(gen, snap)
            finally:
                with self.lock:
                    self.pending = max(0, self.pending - 1)

    def work(self, gen: int, snap: dict) -> None:
        started = time.monotonic()
        deadline = started + self.cfg.timeout_s

        def check() -> None:
            if self.gen != gen:
                raise ShadowStop("abandoned")
            if time.monotonic() > deadline:
                raise ShadowTimeout("time cap")

        line = None
        try:
            observed = self.load_observed()
            known = len(observed)
            line = compute_line(snap, observed, check, epoch=self.epoch)
            self.observed_dirty = self.observed_dirty or len(observed) != known
        except ShadowStop:
            line = None
        except Exception as exc:  # noqa: BLE001
            log.warning("semantics shadow job failed (%s)", type(exc).__name__)
            line = None
            if self.gen == gen:
                self.outcome(failed=True)
                return
        if line is None or self.gen != gen:
            self.counts["discarded"] += 1  # late or abandoned: never written
            return
        self.finish(gen, snap, line)

    def load_observed(self) -> dict[str, dict]:
        if self.observed is None:
            try:
                value = _read_json(self.paths.observed) if self.paths.observed.exists() else {}
            except (OSError, ValueError):
                value = {}
            self.observed = value if isinstance(value, dict) else {}
            horizon = time.time() - RETENTION_DAYS * 86400
            self.observed = {k: v for k, v in self.observed.items()
                             if isinstance(v, dict) and float(v.get("at") or 0) >= horizon}
        return self.observed

    def save_observed(self) -> None:
        if self.observed is None or not self.observed_dirty:
            return
        if len(self.observed) > OBSERVED_MAX:
            newest = sorted(self.observed.items(), key=lambda kv: kv[1].get("at") or 0)[-OBSERVED_MAX:]
            self.observed = dict(newest)
        atomic_write_text(self.paths.observed, json.dumps(self.observed, sort_keys=True))
        self.observed_dirty = False

    def finish(self, gen: int, snap: Mapping[str, Any], line: dict) -> None:
        failed = False
        for model in [m for m in MODELS if m in line]:
            if line[model].get("status") != "ok":
                failed = True
                log.warning("semantics shadow %s %s (%s)", model, line[model].get("status"),
                            line[model].get("error_kind"))
        try:
            if self.gen != gen:
                return
            append_line(self.paths, line)
            self.save_observed()
            self.counts["lines"] += 1
        except OSError as exc:
            log.warning("semantics shadow record not written (%s)", type(exc).__name__)
            failed = True
        self.outcome(failed=failed)

    def outcome(self, *, failed: bool) -> None:
        with self.lock:
            if failed:
                self.counts["failures"] += 1

    def drain(self, timeout: float = 5.0) -> bool:
        """Tests and tools: wait until the queue is empty and no job runs."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                idle = self.pending == 0
            if idle:
                return True
            time.sleep(0.01)
        return False
