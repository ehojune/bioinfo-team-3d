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

import argparse
import hashlib
import itertools
import json
import logging
import os
import platform
import queue
import re
import sqlite3
import stat
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
EXTERNAL_LOOK_S = 0.25           # how often a running job looks for a disabled.json written elsewhere
BUSY_LIMIT = 5                   # consecutive requests skipped because the worker was busy
CONSECUTIVE_FAILURES = 3
RECENT_WINDOW, RECENT_FAILURES = 20, 3
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
INTRODUCED = date(2026, 10, 1)   # B1 shadow PR; the PI's removal review falls due 90 days later
REVIEW_DAYS, MIDPOINT_DAYS = 90, 30
VERDICTS = ("ok", "wrong_identity", "wrong_other", "irrelevant")
REASONS = ("type_unknown", "hash_unknown", "zone_excluded", "version_changed", "not_generated", "incomplete")
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.:@#+-]{0,96}")
VOCABULARY = frozenset({"general", "research", "direct", "orchestrate", "plan_only", "done", "failed", "rejected",
                        "cancelled", "interrupted", "running", "ok", "error", "timeout", "request", "auto_off",
                        "enable", "mark", *VERDICTS, *REASONS})
SENSITIVE_MIN = 6
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
    def disabled(self) -> Path:
        return self.root / "disabled.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def observed(self) -> Path:
        return self.root / "observed.json"


def shadow_root(settings: Any) -> Path:
    return settings.path(settings.gateway.state_dir) / "semantics"


def inside_git_tree(path: Path) -> bool:
    """True when the path, or where a symlink or junction on it leads, is inside a git work tree.

    The shadow's local records must never land in a repository (this one is public). Unknown means yes.
    """
    try:
        places = {Path(os.path.abspath(path)), Path(os.path.realpath(path))}
    except (OSError, ValueError):
        return True
    return any((parent / ".git").exists() for place in places for parent in (place, *place.parents))


def _when(entry: Mapping[str, Any]) -> float:
    try:
        return float(entry.get("at") or 0)
    except (TypeError, ValueError):
        return 0.0


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


def read_state(paths: ShadowPaths) -> dict:
    """epoch and start time; created on first use."""
    if not paths.state.exists():
        state = {"epoch": 1, "since": time.time()}
        paths.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(paths.state, json.dumps(state))
        return state
    state = _read_json(paths.state)
    if not isinstance(state, dict) or not isinstance(state.get("epoch"), int) or state["epoch"] < 1:
        raise ValueError("state.json")
    return state


def write_disabled(paths: ShadowPaths, reason: str, epoch: int, counts: Mapping[str, int] | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(paths.disabled, json.dumps({"reason": reason, "ts": time.time(), "epoch": epoch,
                                                  "counts": dict(counts or {})}, sort_keys=True))


def read_disabled(paths: ShadowPaths) -> dict | None:
    if not paths.disabled.exists():
        return None
    value = _read_json(paths.disabled)
    if not isinstance(value, dict) or not isinstance(value.get("reason"), str):
        raise ValueError("disabled.json")
    return value


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


_TASK_SQL = ("SELECT key, json_remove(body, '$.payload.prompt', '$.payload.context', '$.result.text') FROM state "
             "WHERE kind = 'task' AND json_extract(body, '$.request_id') {} ORDER BY key LIMIT ?")
_DECISION_SQL = ("SELECT key, body FROM state WHERE kind = 'approval_decision' "
                 "AND json_extract(body, '$.approval.request_id') = ?")
_JOBS_SQL = "SELECT key, body FROM state WHERE kind = 'jobs_done' AND key IN (SELECT value FROM json_each(?))"


def _rows_from_sql(execute: Callable[..., Any], rid: str, history: set, limit: int) -> list[dict]:
    own = {k: json.loads(b) for k, b in execute(_TASK_SQL.format("= ?"), (rid, limit + 1))}
    rest = {k: json.loads(b) for k, b in execute(_TASK_SQL.format("IN (SELECT value FROM json_each(?))"),
                                                 (json.dumps(sorted(history)), limit + 1))}
    decisions = {k: json.loads(b) for k, b in execute(_DECISION_SQL, (rid,))}
    jobs = {k: json.loads(b) for k, b in execute(_JOBS_SQL, (json.dumps(sorted(own)),))}
    return [own, rest, decisions, jobs]


def _rows_from_store(store: Any, rid: str, history: set) -> list[dict]:
    """A store without SQL (a test double): filter full reads."""
    tasks = store.all("task")
    own = {k: v for k, v in tasks.items() if v.get("request_id") == rid}
    rest = {k: v for k, v in tasks.items() if v.get("request_id") in history}
    decisions = {k: v for k, v in store.all("approval_decision").items()
                 if (v.get("approval") or {}).get("request_id") == rid}
    jobs = {k: v for k, v in store.all("jobs_done").items() if k in own}
    return [own, rest, decisions, jobs]


def take_snapshot(hub: Any, rid: str, cfg: ShadowConfig) -> dict:
    """What the two models need from memory, taken on the event loop: no file and no database read.

    Request rows are light copies; task, decision and job rows are read later by the worker (``read_rows``).
    The copy shares nothing with live state.
    """
    started = time.perf_counter()
    req = hub.requests[rid]
    project_id = req.get("project_id")
    others = sorted((r for r in hub.requests.values() if r.get("project_id") == project_id and r.get("id") != rid),
                    key=lambda r: r.get("created_at") or 0, reverse=True)
    chosen = [req, *others[:cfg.history_requests - 1]]
    pending = [{"id": aid, "kind": (entry.get("approval") or {}).get("kind"),
                "task_id": (entry.get("approval") or {}).get("task_id"), "request_id": rid, "state": "pending"}
               for aid, entry in sorted(hub.approvals.items()) if (entry.get("approval") or {}).get("request_id") == rid]
    project = hub.s.project(project_id)
    snap: dict[str, Any] = {
        "rid": rid, "ts": time.time(),
        "requests": {r.get("id"): _light_request(r) for r in chosen},
        "history_truncated": len(others) > len(chosen) - 1, "pending_approvals": pending,
        "agents": {aid: {"engine": a.get("engine"), "employment": a.get("employment")}
                   for aid, a in hub.agents.items()},
        "project": {"id": project_id, "visibility": project.visibility if project else None,
                    "local_dir": project.local_dir if project else None, "name": project.name if project else None},
        "zones": [[z.path, z.level] for z in hub.s.policy.data_zones],
        "workspace_root": str(hub.s.path(hub.s.runner.workspace_root)),
        "host": platform.node(),
    }
    if hasattr(hub.store, "db"):
        snap["state_db"] = str(hub.s.path(hub.s.gateway.state_dir) / "gateway.sqlite3")
    else:
        snap["rows"] = _rows_from_store(hub.store, rid, {r.get("id") for r in others[:cfg.history_requests - 1]})
    snap = json.loads(json.dumps(snap, default=str))
    snap["snapshot_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return snap


def read_rows(snap: dict, check: Callable[[], None]) -> None:
    """Fill the snapshot's task, decision and job rows in the worker, off the event loop.

    The gateway DB is opened read-only through its own connection (the #141 reader) and queried by request
    id. Prompt and answer text are dropped inside SQLite, rows past MAX_TASK_ROWS are not decoded, and a
    query still running past the time cap is interrupted.
    """
    if "tasks" in snap:
        return
    started = time.perf_counter()
    rid = snap["rid"]
    history = set(snap["requests"]) - {rid}
    if snap.get("rows") is not None:
        own, rest, decisions, jobs = snap.pop("rows")
    else:
        db = sem._open_state_readonly(Path(snap["state_db"]), immutable=False)

        def interrupt() -> int:
            try:
                check()
            except ShadowStop:
                return 1
            return 0

        db.set_progress_handler(interrupt, 20_000)
        try:
            own, rest, decisions, jobs = _rows_from_sql(db.execute, rid, history, MAX_TASK_ROWS)
        except sqlite3.OperationalError:
            check()  # an interrupted query raises the timeout or stop that interrupted it
            raise
        finally:
            db.close()
    snap["tasks_truncated"] = len(own) + len(rest) > MAX_TASK_ROWS
    own = dict(sorted(own.items())[:MAX_TASK_ROWS])
    rest = dict(sorted(rest.items())[:max(0, MAX_TASK_ROWS - len(own))])
    research = {r for r, req in snap["requests"].items() if req.get("research_contract")}
    snap["tasks"] = {tid: _light_task(task, research) for tid, task in sorted({**own, **rest}.items())}
    approvals = []
    for aid, decision in sorted(decisions.items()):
        approval = decision.get("approval") or {}
        state = decision.get("state") or ("approved" if decision.get("approved") else "denied")
        approvals.append({"id": aid, "kind": approval.get("kind"), "task_id": approval.get("task_id"),
                          "request_id": rid, "state": state})
    snap["approvals"] = approvals + list(snap.get("pending_approvals") or [])
    snap["jobs_done"] = {tid: {"jobs": [{"job_id": j.get("job_id"), "state": j.get("state")}
                                        for j in (body or {}).get("jobs") or [] if isinstance(j, dict)]}
                         for tid, body in sorted(jobs.items())}
    snap["rows_ms"] = _ms(started)


# ---------------------------------------------------------------- information boundary

def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def sensitive_strings(snap: Mapping[str, Any]) -> set[str]:
    """Values a line must never contain: texts, instructions, paths, reference values, zones, project names."""
    found: list[Any] = []
    for req in (snap.get("requests") or {}).values():
        found += [req.get("text"), req.get("project_id")]
        found += [r.get("value") for r in req.get("references") or []]
        plan = req.get("plan") or {}
        found += [s for s in _strings(plan) if len(s) >= 12] if req.get("research_contract") else [
            step.get("instruction") for step in plan.get("steps") or []]
        for result in (req.get("results") or {}).values():
            found += [result.get("workdir"), result.get("workdir_id"), *_strings(result.get("outputs"))]
    for task in (snap.get("tasks") or {}).values():
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        outputs = list(_strings(result.get("outputs")))  # any shape: a malformed row must not end the check
        found += [result.get("workdir"), result.get("workdir_id"), *outputs]
        found += [o.replace("\\", "/").rsplit("/", 1)[-1] for o in outputs]
    found += [zone[0] for zone in snap.get("zones") or []]
    project = snap.get("project") or {}
    found += [project.get("id"), project.get("local_dir"), project.get("name"), snap.get("workspace_root")]
    return {s.strip() for s in found if isinstance(s, str) and len(s.strip()) >= SENSITIVE_MIN}


def boundary_problems(line: Mapping[str, Any], sensitive: Iterable[str]) -> list[str]:
    """Why a line may not be written: a string that is not a plain token, or a value carrying a sensitive one.

    Keys and the fixed words of ``VOCABULARY`` are code's own; every other string value is checked against
    the snapshot's texts, paths and names, so a request titled like a status word does not trip the check.
    """
    problems: list[str] = []
    keys: list[str] = []
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                if isinstance(k, str):
                    keys.append(k)
                else:
                    problems.append("non_string_key")
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            values.append(value)
        elif not (value is None or isinstance(value, (bool, int, float))):
            problems.append("non_json_value")

    walk(line)
    problems += ["not_a_token" for s in keys + values if not SAFE_TOKEN.fullmatch(s)]
    secrets = [s for s in sensitive if s]
    problems += ["sensitive_value" for leaf in values if len(leaf) >= SENSITIVE_MIN and leaf not in VOCABULARY
                 for s in secrets if s in leaf]
    return problems


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


def zone_forms(zones: Iterable[Iterable[str]]) -> list[tuple[str, str]] | None:
    """Each zone under its written and its real spelling, as the runner's gate compares them: a restricted
    zone given as a link or junction also covers the folder it points to. None when one cannot be resolved."""
    forms: list[tuple[str, str]] = []
    for z, level in zones:
        try:
            real = _norm(os.path.realpath(os.path.expandvars(os.path.expanduser(z))))
        except (OSError, ValueError):
            return None
        forms += [(spelling, level) for spelling in {_norm(z), real}]
    return forms


def zone_allows(path: str, zones: Iterable[Iterable[str]], visibility: str | None, *,
                forms: list[tuple[str, str]] | None = None) -> bool:
    """Only `public` zones for a public project, `public` and `internal` otherwise. Outside every zone: no."""
    allowed = {"public"} if visibility == "public" else {"public", "internal"}
    normalized = forms if forms is not None else zone_forms(zones)
    if normalized is None:
        return False
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
        self.zone_forms = zone_forms(self.zones)  # None: a zone did not resolve, so nothing is read
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
        if not zone_allows(workdir, self.zones, self.visibility, forms=self.zone_forms or []):
            return "zone_excluded"
        try:
            if _norm(os.path.realpath(workdir)) != _norm(workdir):
                return "not_regular"  # the workspace or a parent is a link or junction
            st = os.lstat(workdir)
        except FileNotFoundError:
            return "missing"
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            return "not_regular"
        manifest_path = os.path.join(workdir, "manifest.json")
        if not zone_allows(manifest_path, self.zones, self.visibility, forms=self.zone_forms or []):
            return "zone_excluded"  # a narrower zone on the manifest itself wins over the folder's
        manifest = self._small_json(manifest_path)
        self.manifests[workdir] = manifest
        if not isinstance(manifest, dict) or manifest.get("host") != self.host:
            return "remote"  # a manifest written on another host: the runner's disk is not this one
        return "ok"

    def trusted_manifests(self) -> dict[str, dict]:
        """Manifests of same-host workspaces in allowed zones: run fields the gateway rows do not carry."""
        return {w: m for w, m in self.manifests.items() if self.workspace_state.get(w) == "ok" and isinstance(m, dict)}

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
        if not zone_allows(path, self.zones, self.visibility, forms=self.zone_forms or []):
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
        for task in snap["tasks"].values():  # this request's workspaces: their manifests fill the run fields
            result = task.get("result")
            if task.get("request_id") == rid and isinstance(result, dict):
                reader.workspace(result.get("workdir"))
        records, invalid = sem.records_from_rows(snap["requests"], snap["tasks"], manifests=reader.trusted_manifests())
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
        records, _ = sem.records_from_rows(snap["requests"], snap["tasks"], manifests=reader.trusted_manifests(),
                                           observed=seen_now)
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


def failed_line(snap: Mapping[str, Any], status: str, exc: BaseException, *, epoch: int, ms: float) -> dict:
    """A request whose job stopped before the models ran: ids and the failure only, so the report counts it."""
    rid = snap["rid"]
    req = (snap.get("requests") or {}).get(rid) or {}
    project = req.get("project_id")
    model = {"status": status, "error_kind": type(exc).__name__, "ms": 0.0}
    return {"v": 1, "type": "request", "ts": round(time.time(), 3), "epoch": epoch, "request_id": rid,
            "project": opaque("project", project)[:12] if project else None, "lane": req.get("lane"),
            "mode": req.get("mode"), "status": req.get("status"), "provenance": dict(model), "objects": dict(model),
            "busy_skipped": int(snap.get("busy_skipped") or 0), "snapshot_ms": snap.get("snapshot_ms"), "ms": ms}


def compute_line(snap: Mapping[str, Any], observed: dict[str, dict], check: Callable[[], None], *,
                 epoch: int) -> dict:
    """One request line: both models side by side, ids, kinds, hashes and counts only."""
    started = time.perf_counter()
    read_rows(snap, check)
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
        "rows_ms": snap.get("rows_ms"),
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


# ---------------------------------------------------------------- worker and breaker

class ShadowService:
    """One daemon worker thread, a queue of one, and a breaker that latches off without a redeploy."""

    def __init__(self, hub: Any, cfg: ShadowConfig, paths: ShadowPaths, *, stuck_s: float = STUCK_S):
        self.hub, self.cfg, self.paths, self.stuck_s = hub, cfg, paths, stuck_s
        self.lock = threading.RLock()
        self.queue: queue.Queue = queue.Queue(maxsize=1)
        self.thread: threading.Thread | None = None
        self.gen = 0
        self.epoch = 1
        self.latched: str | None = None
        self.recent: deque[bool] = deque(maxlen=RECENT_WINDOW)
        self.consecutive = 0
        self.busy = 0
        self.busy_skipped = 0
        self.current: tuple[int, float] | None = None
        self.observed: dict[str, dict] | None = None
        self.observed_dirty = False
        self.state_mtime: float | None = None
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
            service.load()
            return service
        except Exception as exc:  # noqa: BLE001
            log.warning("semantics shadow could not start (%s); semantics stays off", type(exc).__name__)
            return None

    # -- breaker state on disk
    def load(self) -> None:
        try:
            self.epoch = int(read_state(self.paths)["epoch"])
            self.state_mtime = self.paths.state.stat().st_mtime
            disabled = read_disabled(self.paths)
        except (OSError, ValueError, TypeError) as exc:
            self.trip("breaker_storage", kind=type(exc).__name__)
            return
        if disabled is not None:
            self.latched = disabled["reason"]

    def external_off(self) -> bool:
        """A disabled.json written elsewhere (`labhq semantics mark`, another process) latches this one too."""
        try:
            if not self.paths.disabled.exists():
                return False
            if not self.latched:
                disabled = read_disabled(self.paths) or {}
                with self.lock:
                    if not self.latched:
                        self.latched, self.gen = str(disabled.get("reason") or "disabled"), self.gen + 1
            return True
        except (OSError, ValueError, TypeError) as exc:
            self.trip("breaker_storage", kind=type(exc).__name__)
            return True

    def refresh(self) -> None:
        """Pick up `labhq semantics enable` or a disabled.json written by `labhq semantics mark`."""
        try:
            if self.external_off():
                return
            mtime = self.paths.state.stat().st_mtime if self.paths.state.exists() else None
            if mtime != self.state_mtime:
                epoch = int(read_state(self.paths)["epoch"])
                self.state_mtime = mtime
                if epoch > self.epoch:
                    self.new_epoch(epoch)
        except (OSError, ValueError, TypeError) as exc:
            self.trip("breaker_storage", kind=type(exc).__name__)

    def new_epoch(self, epoch: int) -> None:
        with self.lock:
            stuck = self.current is not None
            self.epoch, self.latched, self.gen = epoch, None, self.gen + 1
            self.recent.clear()
            self.consecutive = self.busy = self.busy_skipped = 0
            if stuck:  # the old thread may never return; give the new epoch its own worker
                self.queue, self.thread, self.pending = queue.Queue(maxsize=1), None, 0
            self.current = None  # an older job belongs to the closed epoch: no stuck check, no watchdog trip

    def trip(self, reason: str, **detail: Any) -> None:
        with self.lock:
            if self.latched:
                return
            self.latched, self.gen = reason, self.gen + 1
            counts = {**self.counts, "recent_failures": sum(self.recent), "consecutive": self.consecutive}
            epoch = self.epoch
        log.warning("semantics shadow turned itself off: %s%s", reason,
                    f" ({detail['kind']})" if detail.get("kind") else "")
        try:
            write_disabled(self.paths, reason, epoch, counts)
        except OSError as exc:
            log.warning("semantics disabled.json not written (%s); off for this process", type(exc).__name__)
        try:
            append_line(self.paths, {"v": 1, "type": "auto_off", "ts": round(time.time(), 3), "epoch": epoch,
                                     "reason": reason, "counts": counts})
        except OSError:
            pass

    # -- event loop side
    def after_request(self, rid: str) -> None:
        """Queue the finished request for the worker. Never raises, never waits, reads no workspace file."""
        try:
            self.refresh()
            self.check_stuck()
            if self.latched:
                return
            snap = take_snapshot(self.hub, rid, self.cfg)
            snap["busy_skipped"] = self.busy_skipped
            with self.lock:
                job = (self.gen, self.queue, snap)
                try:
                    self.queue.put_nowait(job)
                    self.pending += 1
                except queue.Full:
                    self.busy_skipped += 1
                    self.busy += 1
                    self.counts["busy"] += 1
                    busy = self.busy
                else:
                    self.busy = self.busy_skipped = 0
                    busy = 0
                    self.ensure_thread()
            if busy >= BUSY_LIMIT:
                self.trip("worker_busy")
        except Exception as exc:  # noqa: BLE001 - the request already finished; this must not touch it
            log.warning("semantics shadow skipped a request (%s)", type(exc).__name__)
            self.outcome(failed=True)

    def ensure_thread(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, args=(self.queue,), name=self.thread_name, daemon=True)
            self.thread.start()

    def check_stuck(self) -> None:
        with self.lock:
            current = self.current if self.current and self.current[0] == self.gen else None
        if current and time.monotonic() - current[1] > self.stuck_s:
            self.trip("worker_stuck")

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
                if gen != self.gen or self.latched or self.external_off():
                    self.counts["discarded"] += 1
                    continue
                self.work(gen, snap)
            except Exception as exc:  # noqa: BLE001 - a dead worker would count nothing and never turn off
                log.warning("semantics shadow job failed (%s)", type(exc).__name__)
                self.outcome(failed=True)
            finally:
                with self.lock:
                    self.pending = max(0, self.pending - 1)

    def work(self, gen: int, snap: dict) -> None:
        started = time.monotonic()
        deadline = started + self.cfg.timeout_s
        with self.lock:
            self.current = (gen, started)
        watchdog = threading.Timer(self.stuck_s, self.watch, args=(gen, started))
        watchdog.daemon = True
        watchdog.start()

        next_look = [started + EXTERNAL_LOOK_S]

        def check() -> None:
            if self.gen != gen:
                raise ShadowStop("abandoned")
            now = time.monotonic()
            if now > deadline:
                raise ShadowTimeout("time cap")
            if now >= next_look[0]:
                next_look[0] = now + EXTERNAL_LOOK_S
                if self.external_off():
                    raise ShadowStop("turned off elsewhere")

        line = None
        try:
            observed = self.load_observed()
            known = len(observed)
            line = compute_line(snap, observed, check, epoch=self.epoch)
            self.observed_dirty = self.observed_dirty or len(observed) != known
        except ShadowTimeout as exc:  # stopped before the models ran: still one line, so the report counts it
            line = failed_line(snap, "timeout", exc, epoch=self.epoch, ms=round((time.monotonic() - started) * 1000, 2))
        except ShadowStop:
            line = None
        except Exception as exc:  # noqa: BLE001
            line = failed_line(snap, "error", exc, epoch=self.epoch, ms=round((time.monotonic() - started) * 1000, 2))
        finally:
            watchdog.cancel()
            with self.lock:
                if self.current == (gen, started):
                    self.current = None
        if line is None or self.gen != gen:
            self.counts["discarded"] += 1  # late or abandoned: never written
            return
        self.finish(gen, snap, line)

    def watch(self, gen: int, started: float) -> None:
        with self.lock:
            stuck = self.current == (gen, started) and gen == self.gen
        if stuck:
            self.trip("worker_stuck")

    def load_observed(self) -> dict[str, dict]:
        """First observations, read once; entries past RETENTION_DAYS drop out on every job and are saved."""
        if self.observed is None:
            try:
                value = _read_json(self.paths.observed) if self.paths.observed.exists() else {}
            except (OSError, ValueError):
                value = {}
            self.observed = value if isinstance(value, dict) else {}
        horizon = time.time() - RETENTION_DAYS * 86400
        kept = {k: v for k, v in self.observed.items() if isinstance(v, dict) and _when(v) >= horizon}
        if len(kept) != len(self.observed):
            self.observed, self.observed_dirty = kept, True
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
        try:
            problems = boundary_problems(line, sensitive_strings(snap))
        except Exception as exc:  # noqa: BLE001 - an unchecked line is never written
            log.warning("semantics shadow boundary check failed (%s); record not written", type(exc).__name__)
            self.outcome(failed=True)
            return
        if problems:
            self.trip("info_boundary")
            return
        failed = False
        for model in [m for m in MODELS if m in line]:
            if line[model].get("status") != "ok":
                failed = True
                log.warning("semantics shadow %s %s (%s)", model, line[model].get("status"),
                            line[model].get("error_kind"))
        try:
            if self.gen != gen or self.external_off():
                self.counts["discarded"] += 1
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
            self.recent.append(failed)
            self.consecutive = self.consecutive + 1 if failed else 0
            if failed:
                self.counts["failures"] += 1
            consecutive, recent = self.consecutive, sum(self.recent)
        if consecutive >= CONSECUTIVE_FAILURES:
            self.trip("consecutive_failures")
        elif recent >= RECENT_FAILURES:
            self.trip("recent_failures")

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


# ---------------------------------------------------------------- report, enable, mark (local, no network)

def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))], 2)


def _day(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def configured(settings: Any) -> str:
    """What the setting asks for: shadow, off, invalid (off with a warning) or refused (state_dir in git)."""
    raw = getattr(settings, "semantics", None)
    if resolve(raw) is not None:
        return "refused" if inside_git_tree(shadow_root(settings)) else "shadow"
    mode = _mode(raw.get("mode", "off")) if isinstance(raw, Mapping) else _mode(raw)
    return "off" if mode == "off" else "invalid"


def build_report(paths: ShadowPaths, today: date | None = None, setting: str = "unknown") -> dict:
    today = today or date.today()
    lines, broken = read_lines(paths)
    requests = [l for l in lines if l.get("type") == "request"]
    try:
        disabled = read_disabled(paths)
    except (OSError, ValueError):
        disabled = {"reason": "breaker_storage"}
    try:
        state = read_state(paths) if paths.state.exists() else {"epoch": 1}
    except (OSError, ValueError):
        state = {"epoch": None}

    def model_stats(name: str) -> dict:
        rows = [r.get(name) or {} for r in requests]
        status = {s: sum(1 for r in rows if r.get("status") == s) for s in ("ok", "timeout", "error")}
        ok = [r for r in rows if r.get("status") == "ok"]
        out = {"n": len(rows), **status, "p50_ms": _pct([float(r.get("ms") or 0) for r in rows], 0.5),
               "p95_ms": _pct([float(r.get("ms") or 0) for r in rows], 0.95)}
        if name == "provenance":
            ratios = [float(r["unknown_ratio"]) for r in ok if isinstance(r.get("unknown_ratio"), (int, float))]
            excluded: dict[str, int] = {k: 0 for k in REASONS}
            for r in ok:
                for k, v in (r.get("excluded") or {}).items():
                    excluded[k] = excluded.get(k, 0) + int(v or 0)
            out.update(with_candidates=sum(1 for r in ok if (r.get("candidates") or 0) > 0),
                       candidates=sum(int(r.get("candidates") or 0) for r in ok),
                       unknown_ratio_median=round(statistics.median(ratios), 4) if ratios else None,
                       incomplete=sum(1 for r in ok if r.get("incomplete")), excluded=excluded,
                       lineage_gaps=sum(int((r.get("lineage") or {}).get("gaps") or 0) for r in ok))
        else:
            out.update(objects_mean=round(statistics.mean(sum((r.get("objects") or {}).values()) for r in ok), 2)
                       if ok else None,
                       links_mean=round(statistics.mean(int(r.get("link_total") or 0) for r in ok), 2) if ok else None,
                       unresolved=sum(int(r.get("unresolved") or 0) for r in ok),
                       with_unresolved=sum(1 for r in ok if (r.get("unresolved") or 0) > 0))
        return out

    research_with_candidates = sum(1 for r in requests if r.get("lane") == "research"
                                   and ((r.get("provenance") or {}).get("candidates") or 0) > 0)
    marks = [l for l in lines if l.get("type") == "mark"]
    wrong = sum(1 for m in marks if str(m.get("verdict", "")).startswith("wrong"))
    total_ms = [float(r.get("ms") or 0) for r in requests]
    unknown_median = model_stats("provenance")["unknown_ratio_median"]
    deadline = INTRODUCED + timedelta(days=REVIEW_DAYS)
    proposals = []
    if today >= deadline:
        proposals.append(f"판정 기한 도달(도입 {INTRODUCED.isoformat()} + {REVIEW_DAYS}일)")
        if research_with_candidates < 10:
            proposals.append(f"효용 미입증: 후보 있는 연구 요청 {research_with_candidates}건 < 10, 판정 불가 — 접기 제안")
    if len(requests) >= 15 and (_pct(total_ms, 0.95) or 0) > 1000:
        proposals.append("지연: 계산 p95 > 1초")
    if len(requests) >= 15 and unknown_median is not None and unknown_median >= 0.9:
        proposals.append("기록 공백: unknown 비율 중앙값 ≥ 0.9, 병목은 기록(#58·#115)")
    if len(marks) >= 5 and wrong / len(marks) >= 0.2:
        proposals.append(f"오답: 검토 {len(marks)}건 중 wrong {wrong}건(≥20%)")
    return {
        "state": {"on": setting == "shadow" and disabled is None, "setting": setting,
                  "reason": (disabled or {}).get("reason"), "epoch": state.get("epoch")},
        "requests": len(requests), "broken_lines": broken,
        "period": [_day(requests[0].get("ts")), _day(requests[-1].get("ts"))] if requests else None,
        "busy_skipped": sum(int(r.get("busy_skipped") or 0) for r in requests),
        "total_p95_ms": _pct(total_ms, 0.95),
        "snapshot_p95_ms": _pct([float(r.get("snapshot_ms") or 0) for r in requests], 0.95),
        "provenance": model_stats("provenance"), "objects": model_stats("objects"),
        "hash": {k: sum(int((r.get("hash") or {}).get(k) or 0) for r in requests)
                 for k in ("hashed", "observed_new", "verified", "changed")},
        "workspaces": {k: sum(int(((r.get("hash") or {}).get("workspaces") or {}).get(k) or 0) for r in requests)
                       for k in ("ok", "remote", "zone_excluded", "not_regular", "missing")},
        "research_with_candidates": research_with_candidates,
        "marks": {"reviewed": len(marks), "wrong": wrong},
        "auto_off": [{"day": _day(l.get("ts")), "epoch": l.get("epoch"), "reason": l.get("reason")}
                     for l in lines if l.get("type") == "auto_off"],
        "deadline": deadline.isoformat(), "midpoint": (INTRODUCED + timedelta(days=MIDPOINT_DAYS)).isoformat(),
        "propose_removal": proposals,
    }


def render_report(rep: Mapping[str, Any]) -> str:
    st = rep["state"]
    p, o = rep["provenance"], rep["objects"]

    def v(x: Any) -> str:
        return "-" if x is None else str(x)

    out = [
        "semantics shadow report (로컬 기록, 네트워크 없음)",
        f"상태: {'on' if st['on'] else 'off'} · 설정 {st['setting']} · 자동 off {v(st['reason'])} · "
        f"epoch {v(st['epoch'])}",
        f"요청 {rep['requests']}건 · 기간 {' ~ '.join(rep['period']) if rep['period'] else '-'} · "
        f"busy로 건너뜀 {rep['busy_skipped']} · 깨진 줄 {rep['broken_lines']}",
        f"계산 p95 {v(rep['total_p95_ms'])} ms · snapshot p95 {v(rep['snapshot_p95_ms'])} ms",
        "",
        "| 모델 | n | ok | timeout | error | p50 ms | p95 ms | 지표 |",
        "|---|---|---|---|---|---|---|---|",
        f"| 출처 의미 모델 | {p['n']} | {p['ok']} | {p['timeout']} | {p['error']} | {v(p['p50_ms'])} | {v(p['p95_ms'])} | "
        f"후보 있는 요청 {p['with_candidates']} · 후보 {p['candidates']} · unknown 중앙값 {v(p['unknown_ratio_median'])} · "
        f"incomplete {p['incomplete']} |",
        f"| 객체·링크 뷰 | {o['n']} | {o['ok']} | {o['timeout']} | {o['error']} | {v(o['p50_ms'])} | {v(o['p95_ms'])} | "
        f"객체 평균 {v(o['objects_mean'])} · 링크 평균 {v(o['links_mean'])} · unresolved {o['unresolved']} "
        f"({o['with_unresolved']}건) |",
        "",
        "후보 제외 이유: " + ", ".join(f"{k} {n}" for k, n in p["excluded"].items()),
        "hash: " + ", ".join(f"{k} {n}" for k, n in rep["hash"].items()) + " · 작업 폴더: "
        + ", ".join(f"{k} {n}" for k, n in rep["workspaces"].items()),
        f"후보 있는 연구 요청 {rep['research_with_candidates']} · 검토 표시 {rep['marks']['reviewed']}"
        f"(wrong {rep['marks']['wrong']})",
        "",
        "자동 off 이력:" + ("" if rep["auto_off"] else " 없음"),
    ]
    out += [f"- {a['day']} epoch {v(a['epoch'])}: {a['reason']}" for a in rep["auto_off"]]
    out += ["", f"중간 점검 {rep['midpoint']} · 판정 기한 {rep['deadline']} (기준은 전부 미측정 제안치)"]
    if rep["propose_removal"]:
        out += ["PROPOSE_REMOVAL — 제거 제안(결정은 PI):"] + [f"- {x}" for x in rep["propose_removal"]]
        out.append("제거: semantics: off → scripts/semantics_shadow_remove.py → state_dir/semantics 삭제(선택)")
    else:
        out.append("제거 제안: 없음")
    return "\n".join(out)


def enable(paths: ShadowPaths) -> str:
    try:
        disabled = read_disabled(paths)
    except ValueError:
        disabled = {"reason": "breaker_storage"}
    try:
        state = read_state(paths)
    except ValueError:  # a broken state.json: continue after the highest epoch the log has seen
        lines, _ = read_lines(paths)
        state = {"epoch": max([int(l.get("epoch") or 1) for l in lines if isinstance(l.get("epoch"), int)] or [1]),
                 "since": time.time()}
    epoch = int(state["epoch"]) + 1
    atomic_write_text(paths.state, json.dumps({**state, "epoch": epoch}))
    if paths.disabled.exists():
        paths.disabled.unlink()
    append_line(paths, {"v": 1, "type": "enable", "ts": round(time.time(), 3), "epoch": epoch,
                        "previous_reason": (disabled or {}).get("reason")})
    head = f"꺼진 이유: {disabled['reason']}. " if disabled else "꺼져 있지 않았습니다. "
    return head + f"새 epoch {epoch}을 엽니다. 설정 mode가 shadow인 gateway는 다음 요청부터 기록합니다."


def mark(paths: ShadowPaths, rid: str, ref: str, verdict: str) -> str:
    if not re.fullmatch(r"req_[A-Za-z0-9]{1,64}", rid):
        raise ValueError("request id must look like req_<id>")
    if not re.fullmatch(r"sem:[0-9a-f]{8}", ref):
        raise ValueError("ref must be sem:<8 hex>")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    state = read_state(paths)
    append_line(paths, {"v": 1, "type": "mark", "ts": round(time.time(), 3), "epoch": state["epoch"],
                        "request_id": rid, "ref": ref, "verdict": verdict})
    if verdict != "wrong_identity":
        return "기록했습니다."
    write_disabled(paths, "wrong_identity", int(state["epoch"]))
    append_line(paths, {"v": 1, "type": "auto_off", "ts": round(time.time(), 3), "epoch": state["epoch"],
                        "reason": "wrong_identity", "counts": {}})
    return ("자동 off: wrong_identity. 총괄이 원천 ID·판본을 확인한 뒤 `labhq semantics enable`로 다시 켤지 정합니다.")


def run_cli(args: argparse.Namespace, settings: Any) -> int:
    paths = ShadowPaths(shadow_root(settings))
    if args.semantics_cmd != "report" and inside_git_tree(paths.root):
        print(f"semantics {args.semantics_cmd}: gateway.state_dir is inside a git work tree; nothing written")
        return 1
    try:
        if args.semantics_cmd == "report":
            today = date.fromisoformat(args.today) if args.today else None
            rep = build_report(paths, today, configured(settings))
            print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_report(rep))
        elif args.semantics_cmd == "enable":
            print(enable(paths))
        else:
            print(mark(paths, args.request_id, args.ref, args.verdict))
    except (OSError, ValueError) as exc:
        print(f"semantics {args.semantics_cmd}: {exc if isinstance(exc, ValueError) else type(exc).__name__}")
        return 1
    return 0
