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
import math
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

from .. import vocab as output_vocab
from ..policy import _inside, _norm
from ..util import atomic_write_text
from ..vocab import declare as output_types
from . import semantics as sem
from .semantics_objects import build_view, opaque, summarize, type_artifacts

log = logging.getLogger("labhq.semantics")

MODES = ("off", "shadow")
MODELS = ("provenance", "objects")  # the two models a line carries side by side
HELD_MODES = ("advisory", "ab")  # B2: CSO advisory and A/B, held by the PI (#149)
KEYS = ("mode", "timeout_s", "history_requests")
KEYS += ("actions",)  # semantics-hook: actions (#149 결정 13 A1, off by default)
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
HASH_SHARE = 0.5                 # of timeout_s: hashing stops there and the models get the rest (#159)
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
REASONS = ("type_unknown", "hash_unknown", "zone_excluded", "version_changed", "not_generated", "incomplete",
           "input_unknown", "input_mismatch", "target_type_unknown", "target_type_mismatch")
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.:@#+-]{0,96}")
VOCABULARY = frozenset({"general", "research", "direct", "orchestrate", "plan_only", "done", "failed", "rejected",
                        "cancelled", "interrupted", "running", "ok", "error", "timeout", "request", "auto_off",
                        "enable", "mark", *VERDICTS, *REASONS})
VOCABULARY |= {"followup", "refused", "refused_read_only", "shadow_only", "refused_p3"}  # semantics-hook: actions
SENSITIVE_MIN = 6
RUN_FIELDS = ("agent_spec_sha256", "kind", "attempt", "retry", "revision", "session_id", "method", "resumes",
              "wake_of")
ART_FIELDS = ("generated_by", "generator_inputs", "method", "packs", "sha256", "data_type")
TYPE_BUCKETS = ("local", "unknown", "withheld")  # besides the EDAM ids of the loaded subset (#221)
TYPE_BASIS = ("declared", "inferred", "unknown")
DECLARATION_COUNTS = ("outputs", "data_declared", "format_declared")
SHA_HEX = re.compile(r"[0-9a-f]{64}")
DOI_VALUE = re.compile(r"(?:doi:)?10\.\d{4,9}/\S+", re.IGNORECASE)
FILENAME_VALUE = re.compile(r"[^\\/\s]+\.[A-Za-z0-9]{1,12}")
BOUNDARY_FIELDS = frozenset({"unknown", "v", "type", "ts", "epoch", "request_id", "project", "lane",
                             "mode", "status", "rows", "busy_skipped", "snapshot_ms", "rows_ms",
                             "vocab_sha256", "objects", "provenance", "hash", "actions", "ms", "phase",
                             "key", "error_kind", "reason", "counts", "provenance.types",
                             "provenance.declarations", "objects.artifact_types"})
BOUNDARY_CLASSES = frozenset({"path", "filename", "url", "doi", "employee_id", "free_text", "identifier",
                              "unsafe_token", "non_string_key", "non_json_value", "schema", "type_version",
                              "type_fields", "type_bucket", "type_declarations", "type_objects"})


# ---------------------------------------------------------------- settings

@dataclass(frozen=True)
class ShadowConfig:
    timeout_s: float = DEFAULT_TIMEOUT_S
    history_requests: int = 200
    actions: bool = False  # semantics-hook: actions


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
        cfg = ShadowConfig(timeout_s=float(timeout), history_requests=history)
        cfg = _actions_config(cfg, options.get("actions"), raw)  # semantics-hook: actions
        return cfg
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

    @property
    def breaker(self) -> Path:
        return self.root / "breaker.json"


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


def write_disabled(paths: ShadowPaths, reason: str, epoch: int, counts: Mapping[str, int] | None = None, *,
                   boundary: Iterable[Mapping[str, str]] | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    value = {"reason": reason, "ts": time.time(), "epoch": epoch, "counts": dict(counts or {})}
    detail = _clean_boundary_details(boundary)
    if detail:
        value["boundary"] = detail
    atomic_write_text(paths.disabled, json.dumps(value, sort_keys=True))


def write_breaker(paths: ShadowPaths, epoch: int, recent: Iterable[bool], consecutive: int) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(paths.breaker, json.dumps({"epoch": epoch, "recent": list(recent), "consecutive": consecutive}))


def read_breaker(paths: ShadowPaths, epoch: int) -> tuple[list[bool], int] | None:
    """The failure window an earlier process saved for ``epoch`` (#173): (recent, consecutive), or None when the
    file is missing or belongs to another epoch. A file of the wrong shape raises, like the other breaker files."""
    if not paths.breaker.exists():
        return None
    value = _read_json(paths.breaker)
    whole = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
    recent = value.get("recent") if isinstance(value, dict) else None
    if (not isinstance(recent, list) or len(recent) > RECENT_WINDOW or not all(isinstance(x, bool) for x in recent)
            or not whole(value.get("epoch")) or not whole(value.get("consecutive"))):
        raise ValueError("breaker.json")
    return (recent, value["consecutive"]) if value["epoch"] == epoch else None


def read_disabled(paths: ShadowPaths) -> dict | None:
    if not paths.disabled.exists():
        return None
    value = _read_json(paths.disabled)
    if not isinstance(value, dict) or not isinstance(value.get("reason"), str):
        raise ValueError("disabled.json")
    if "boundary" in value:
        value["boundary"] = _clean_boundary_details(value.get("boundary"))
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
        plan_copy = {"steps": [{k: s.get(k) for k in ("id", "agent_id", "depends_on", "outputs", "instruction",
                                                      "output_types") if k in s or k != "output_types"}
                               for s in plan.get("steps") or [] if isinstance(s, dict)]}
    results = {sid: {k: r.get(k) for k in ("task_id", "agent_id", "ok", "status", "outputs", "workdir_id", "workdir")}
               for sid, r in (req.get("results") or {}).items() if isinstance(r, dict)}
    contract = req.get("research_contract") if isinstance(req.get("research_contract"), dict) else {}
    return {"id": req.get("id"), "project_id": req.get("project_id"), "mode": req.get("mode"),
            "status": req.get("status"), "created_at": req.get("created_at"), "lane": _lane(req),
            "research_contract": {"plan_sha256": contract.get("plan_sha256")} if research else None,
            "plan": plan_copy, "results": results, "text": req.get("text"),
            "output_types_stats": req.get("output_types_stats") if isinstance(req.get("output_types_stats"), dict) else None,
            "references": [{"kind": r.get("kind"), "value": r.get("value")} for r in req.get("references") or []
                           if isinstance(r, dict)]}


def _light_task(task: Mapping[str, Any], research: set) -> dict:
    light = {k: task.get(k) for k in ("request_id", "step_id", "kind", "attempt", "revision", "parent_task",
                                      "accepted", "completed", "runner_id") if k in task}
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    light["payload"] = {k: payload.get(k) for k in ("id", "agent_id", "resume_session_id") if k in payload}
    light["payload"]["meta"] = {k: meta.get(k) for k in ("output_types", "output_types_vocab", "outputs") if k in meta}
    result = task.get("result")
    if isinstance(result, dict):
        kept = {k: result.get(k) for k in ("task_id", "agent_id", "ok", "session_id", "workdir", "workdir_id",
                                           "outputs", "missing_outputs", "pending_jobs", "pending_asks", "error_kind",
                                           "output_types")
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
JSON1_PROBE = ("SELECT json('{}'), json_remove('{}', '$.a'), json_extract('{}', '$.a'), "
               "(SELECT count(*) FROM json_each('[]'))")


def sqlite_json1() -> bool:
    """True when this Python's SQLite has the JSON1 functions the row queries use (#160)."""
    try:
        db = sqlite3.connect(":memory:")
        try:
            db.execute(JSON1_PROBE).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return False
    return True


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
    if cfg.actions:  # semantics-hook: actions
        snap["actions"] = _actions_inputs(hub, rid)  # semantics-hook: actions
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
    if "actions" in snap:  # semantics-hook: actions
        _actions_rows(snap, own, decisions)  # semantics-hook: actions
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


def _value_class(value: str) -> str:
    text = value.strip()
    if re.match(r"https?://", text, re.IGNORECASE):
        return "url"
    if DOI_VALUE.fullmatch(text):
        return "doi"
    if re.match(r"[A-Za-z]:[\\/]", text) or text.startswith(("/", "\\")) or "/" in text or "\\" in text:
        return "path"
    if FILENAME_VALUE.fullmatch(text):
        return "filename"
    if any(ch.isspace() for ch in text):
        return "free_text"
    return "identifier"


def sensitive_values(snap: Mapping[str, Any]) -> dict[str, str]:
    """Forbidden source values mapped to a fixed category; values are never written to the breaker record."""
    found: dict[str, str] = {}

    def add(value: Any, category: str | None = None) -> None:
        if isinstance(value, str) and len(value.strip()) >= SENSITIVE_MIN:
            found.setdefault(value.strip(), category or _value_class(value))

    for req in (snap.get("requests") or {}).values():
        add(req.get("text"), "free_text")
        add(req.get("project_id"), "identifier")
        for reference in req.get("references") or []:
            add(reference.get("value"))
        plan = req.get("plan") or {}
        if req.get("research_contract"):
            for value in _strings(plan):
                if len(value) >= 12:
                    add(value)
        else:
            for step in plan.get("steps") or []:
                add(step.get("instruction"), "free_text")
        for result in (req.get("results") or {}).values():
            add(result.get("workdir"), "path")
            add(result.get("workdir_id"), "identifier")
            for value in _strings(result.get("outputs")):
                add(value)
    for task in (snap.get("tasks") or {}).values():
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        outputs = list(_strings(result.get("outputs")))  # any shape: a malformed row must not end the check
        add(result.get("workdir"), "path")
        add(result.get("workdir_id"), "identifier")
        for output in outputs:
            add(output)
            add(output.replace("\\", "/").rsplit("/", 1)[-1], "filename")
    for zone in snap.get("zones") or []:
        add(zone[0], "path")
    project = snap.get("project") or {}
    add(project.get("id"), "identifier")
    add(project.get("local_dir"), "path")
    add(project.get("name"), "free_text")
    add(snap.get("workspace_root"), "path")
    for value in (snap.get("actions") or {}).get("sensitive") or []: add(value)  # semantics-hook: actions
    return found


def sensitive_strings(snap: Mapping[str, Any]) -> set[str]:
    """Values a line must never contain: texts, instructions, paths, reference values, zones, project names."""
    return set(sensitive_values(snap))


def _boundary_field(path: tuple[str, ...]) -> str:
    return path[0] if path and path[0] in BOUNDARY_FIELDS else "unknown"


def _boundary_rows(line: Mapping[str, Any], sensitive: Iterable[str] | Mapping[str, str],
                   allowed_fields: Mapping[str, str] | None = None) -> list[tuple[str, str, str]]:
    secrets = (sensitive.items() if isinstance(sensitive, Mapping)
               else ((value, _value_class(value)) for value in sensitive if isinstance(value, str)))
    secrets = [(value, category if category in BOUNDARY_CLASSES else _value_class(value))
               for value, category in secrets if value]
    allowed_fields = allowed_fields or {}
    findings: set[tuple[str, str, str]] = set()

    def add(problem: str, path: tuple[str, ...], category: str) -> None:
        if category not in BOUNDARY_CLASSES:
            category = "schema"
        findings.add((problem, _boundary_field(path), category))

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(key, str):
                    child_path = (*path, key)
                    if not SAFE_TOKEN.fullmatch(key):
                        category = _value_class(key)
                        add("not_a_token", child_path, "unsafe_token" if category == "identifier" else category)
                else:
                    child_path = path
                    add("non_string_key", path, "non_string_key")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child, path)
        elif isinstance(value, str):
            token = bool(SAFE_TOKEN.fullmatch(value))
            value_category = _value_class(value)
            if not token:
                category = value_category
                add("not_a_token", path, "unsafe_token" if category == "identifier" else category)
            allowed = len(path) == 1 and allowed_fields.get(path[0]) == value
            if len(value) >= SENSITIVE_MIN and value not in VOCABULARY and not allowed:
                for secret, category in secrets:
                    if secret in value:
                        add("sensitive_value", path, value_category if not token else category)
        elif not (value is None or isinstance(value, (bool, int, float))):
            add("non_json_value", path, "non_json_value")

    walk(line)
    return sorted(findings)


def boundary_findings(line: Mapping[str, Any], sensitive: Iterable[str] | Mapping[str, str], *,
                      allowed_fields: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """Off-record diagnostics: only a fixed field name and category, never the rejected value."""
    return [{"field": field, "class": category}
            for field, category in sorted({(field, category) for _, field, category in
                                           _boundary_rows(line, sensitive, allowed_fields)})]


def boundary_problems(line: Mapping[str, Any], sensitive: Iterable[str] | Mapping[str, str], *,
                      allowed_fields: Mapping[str, str] | None = None) -> list[str]:
    """Why a line may not be written: a string that is not a plain token, or a value carrying a sensitive one.

    Keys and the fixed words of ``VOCABULARY`` are code's own; every other string value is checked against
    the snapshot's texts, paths and names, so a request titled like a status word does not trip the check.
    """
    return [problem for problem, _, _ in _boundary_rows(line, sensitive, allowed_fields)]


def _clean_boundary_details(value: Any) -> list[dict[str, str]]:
    """Keep only code-owned labels before a diagnostic reaches disabled.json or a report."""
    if not isinstance(value, (list, tuple)):
        return []
    clean = {(item.get("field"), item.get("class")) for item in value if isinstance(item, Mapping)
             and item.get("field") in BOUNDARY_FIELDS and item.get("class") in BOUNDARY_CLASSES}
    return [{"field": field, "class": category} for field, category in sorted(clean)]


# ---------------------------------------------------------------- file reads: zones, same disk, hash

class ShadowStop(Exception):
    """The job ran past its time cap or was abandoned."""


class ShadowTimeout(ShadowStop):
    pass


# strictest last; a level the settings do not define ranks above restricted, so it is never allowed
ZONE_STRICTNESS = {"public": 0, "internal": 1, "restricted": 2}


def _zone_level(path: str, zones: list[tuple[str, str]]) -> str | None:
    """Restricted anywhere wins; otherwise the longest zone, and among zones of that length the strictest level.

    Two spellings of one folder (a public alias of an internal folder) tie on length; the tie must not fall to
    whichever level name sorts last."""
    inside = [(len(z), level) for z, level in zones if _inside(path, z)]
    if any(level == "restricted" for _, level in inside):
        return "restricted"
    if not inside:
        return None
    longest = max(n for n, _ in inside)
    return max((level for n, level in inside if n == longest),
               key=lambda level: ZONE_STRICTNESS.get(level, len(ZONE_STRICTNESS)))


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


_KERNEL32: list[Any] = []


def _share_delete_opener(path: str, flags: int) -> int:
    """Windows: open for reading with FILE_SHARE_DELETE as well, so a runner can still delete the file or rename
    it away while the shadow reads it (#161). A plain open() there would make that runner fail."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    if not _KERNEL32:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        _KERNEL32.append(kernel)
    kernel = _KERNEL32[0]
    # GENERIC_READ; share read, write and delete; OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL
    handle = kernel.CreateFileW(path, 0x80000000, 0x7, None, 3, 0x80, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(handle)
        raise


READ_OPENER = _share_delete_opener if os.name == "nt" else None  # POSIX: an open file never blocks a rename


@dataclass
class HashBudget:
    files: int = 0
    bytes: int = 0
    exhausted: bool = False


class Reader:
    """Read-only access to workspace files on this disk, inside allowed zones, within the budget."""

    def __init__(self, snap: Mapping[str, Any], check: Callable[[], None],
                 hash_over: Callable[[], bool] | None = None):
        self.zones = [tuple(z) for z in snap.get("zones") or []]
        self.zone_forms = zone_forms(self.zones)  # None: a zone did not resolve, so nothing is read
        self.visibility = (snap.get("project") or {}).get("visibility")
        self.root = snap.get("workspace_root") or ""
        self.host = snap.get("host")
        self.check = check
        self.hash_over = hash_over or (lambda: False)  # True once hashing has used its share of the time cap
        self.budget = HashBudget()
        self.manifests: dict[str, dict | None] = {}
        self.workspace_state: dict[str, str] = {}
        self.reference_cache: dict[str, tuple[str, dict | None]] = {}

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
            with open(path, "rb", opener=READ_OPENER) as handle:
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
        return self._hash_file(path, st)

    def accepted_reference_file(self, root: str, name: str) -> tuple[str, dict | None]:
        """Hash a named file only below a same-host reference directory recorded by the runner."""
        if (not os.path.isabs(root) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", name)
                or _norm(os.path.realpath(root)) != _norm(root)):
            return "not_regular", None
        path = os.path.join(root, name)
        if path in self.reference_cache:
            status, seen = self.reference_cache[path]
            return ("cached" if status == "hashed" else status), seen
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            answer = ("missing", None)
        else:
            answer = self._hash_file(path, st)
        self.reference_cache[path] = answer
        return answer

    def _hash_file(self, path: str, st: os.stat_result) -> tuple[str, dict | None]:
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
        if self.hash_over():
            self.budget.exhausted = True
            return "hash_time", None
        self.budget.files += 1
        self.budget.bytes += st.st_size
        digest = hashlib.sha256()
        with open(path, "rb", buffering=0, opener=READ_OPENER) as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_ino, opened.st_dev) != (st.st_ino, st.st_dev):
                return "not_regular", None  # replaced between the check and the open
            while True:
                self.check()
                if self.hash_over():  # past the hash share: the request is incomplete, the models still run
                    self.budget.exhausted = True
                    return "hash_time", None
                chunk = handle.read(HASH_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        try:
            after = os.stat(path)
        except OSError:  # deleted or moved away while hashed (#161)
            return "unstable", None
        if (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns) != (st.st_ino, st.st_dev, st.st_size,
                                                                             st.st_mtime_ns):
            return "unstable", None  # changed, or another file now has this name
        return "hashed", {"sha256": digest.hexdigest(), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


# ---------------------------------------------------------------- the two models

_MODEL: list[Any] = []


class _NoVocab:
    sha256 = None
    edam_ids: frozenset = frozenset()


_NO_VOCAB = _NoVocab()
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
        summary = summarize(type_artifacts(build_view(snap), snap, output_vocab.current()))
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


_INPUT_NAME = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_-]{1,120}(?:\.[A-Za-z0-9_-]{1,120})*"
                         r"\.[A-Za-z0-9]{1,12})(?![A-Za-z0-9_-])")


def _mentioned_names(req: Mapping[str, Any]) -> set[str]:
    text = req.get("text")
    if not isinstance(text, str):
        return set()
    return {m.group(1) for m in _INPUT_NAME.finditer(text[:100_000])}


def _output_names(req: Mapping[str, Any]) -> set[str]:
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    found: set[str] = set()
    for step in plan.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        values = list(step.get("outputs") or [])
        values += [entry.get("name") for entry in step.get("output_types") or [] if isinstance(entry, Mapping)]
        found.update(Path(value.replace("\\", "/")).name.casefold()
                     for value in values if isinstance(value, str))
    return found


def _target_types(req: Mapping[str, Any], vocab: Any) -> set[str]:
    """Declared types of outputs the PI named, not incidental plan-added reports or QC files."""
    if vocab is None:
        return set()
    named = {name.casefold() for name in _mentioned_names(req)}
    found: set[str] = set()
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    for step in plan.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        for entry in step.get("output_types") or []:
            if not isinstance(entry, Mapping) or entry.get("vocab") != vocab.sha256:
                continue
            name, key = entry.get("name"), entry.get("data_type")
            if (isinstance(name, str) and Path(name.replace("\\", "/")).name.casefold() in named
                    and vocab.is_key("data", key)):
                found.add(key)
    return found


def _input_identity(kind: str, value: str) -> str | None:
    """Opaque identity for an already recorded public link or accession; URI paths keep their case."""
    folded = kind.casefold()
    try:
        normalized = (sem.normalize_uri(value) if folded in ("url", "uri") or "://" in value
                      else sem.normalize_id(folded, value))
    except (TypeError, ValueError):
        return None
    return "link:" + hashlib.sha256(f"{folded}:{normalized}".encode("utf-8")).hexdigest()


def _request_input_hashes(req: Mapping[str, Any]) -> set[str]:
    """Opaque identities from structured public links only; path references provide no runner evidence."""
    found: set[str] = set()
    for ref in req.get("references") or []:
        if not isinstance(ref, Mapping):
            continue
        kind, value = ref.get("kind"), ref.get("value")
        if not isinstance(kind, str) or not isinstance(value, str) or not value:
            continue
        identity = None if kind.casefold() == "path" else _input_identity(kind, value)
        if identity is not None:
            found.add(identity)
    return found


def _accepted_file_hashes(records: Any, requests: Mapping[str, Any], reader: Reader, observed: dict[str, dict],
                          current: str, hashes: dict) -> dict[str, set[str]]:
    """Persist each request's input digest at its own shadow observation; never re-hash history."""
    found: dict[str, set[str]] = {}
    for task_id, task in records.tasks.items():
        request = task.get("request_id")
        result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
        manifest = records.manifests.get(result.get("workdir") or "")
        run = ((manifest or {}).get("runs") or {}).get(task_id)
        if not isinstance(request, str) or not isinstance(run, Mapping):
            continue
        req = requests.get(request) or {}
        outputs = _output_names(req)
        names = {name for name in _mentioned_names(req) if name.casefold() not in outputs}
        for root in run.get("reference_dirs") or []:
            if not isinstance(root, str) or not root:
                continue
            for name in sorted(names, key=str.casefold):
                link = _norm(os.path.join(root, name))
                key = opaque("input", request, link)
                seen = observed.get(key)
                if seen is None and request == current:
                    status, seen = reader.accepted_reference_file(root, name)
                    if status in ("hashed", "cached") and seen is not None:
                        observed[key] = {"sha256": seen["sha256"], "size": seen["size"], "at": time.time()}
                        hashes["observed_new"] += 1
                        if status == "hashed":
                            hashes["hashed"] += 1
                            hashes["bytes"] += seen["size"]
                    else:
                        seen = None
                if isinstance(seen, Mapping) and isinstance(seen.get("sha256"), str):
                    found.setdefault(request, set()).add("file:" + seen["sha256"])
    return found


def _declared_artifact_inputs(p: Any) -> dict[str, set[str]]:
    """Existing #249 plan edges become input hashes only when the referenced artifact was already hashed."""
    found: dict[str, set[str]] = {}
    for row in p.runs.values():
        request = row.get("request")
        if not isinstance(request, str):
            continue
        for artifact in row.get("_used") or []:
            art = p.artifacts.get(artifact)
            if art is not None and art.get("sha256") != sem.UNKNOWN:
                found.setdefault(request, set()).add("file:" + art["sha256"])
    return found


def _root_inputs(request: str, direct: Mapping[str, set[str]], artifacts: Mapping[str, set[str]],
                 cache: dict[str, set[str] | None], stack: frozenset[str] = frozenset()) -> set[str] | None:
    """Replace an explicitly linked earlier artifact with that artifact's root request inputs."""
    if request in cache:
        return cache[request]
    if request in stack or not direct.get(request):
        cache[request] = None
        return None
    roots: set[str] = set()
    for identity in direct[request]:
        producers = artifacts.get(identity)
        if not producers:
            roots.add(identity)
            continue
        earlier = sorted(rid for rid in producers if rid != request)
        if not earlier:
            roots.add(identity)
            continue
        resolved = [_root_inputs(rid, direct, artifacts, cache, stack | {request}) for rid in earlier]
        known = [value for value in resolved if value]
        if not known or any(value != known[0] for value in known[1:]):
            cache[request] = None
            return None
        roots.update(known[0])
    cache[request] = roots or None
    return cache[request]


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
        direct_inputs = {request: _request_input_hashes(req)
                         for request, req in snap["requests"].items()}
        for evidence in (_accepted_file_hashes(records, snap["requests"], reader, observed, rid, hashes),
                         _declared_artifact_inputs(p)):
            for request, identities in evidence.items():
                direct_inputs.setdefault(request, set()).update(identities)
        artifact_inputs: dict[str, set[str]] = {}
        for row in p.artifacts.values():
            if row["sha256"] != sem.UNKNOWN and isinstance(row.get("request"), str):
                artifact_inputs.setdefault("file:" + row["sha256"], set()).add(row["request"])
        input_cache: dict[str, set[str] | None] = {}
        current_inputs = _root_inputs(rid, direct_inputs, artifact_inputs, input_cache)
        vocab = output_vocab.current()
        targets = _target_types(snap["requests"].get(rid) or {}, vocab)
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
            if not targets:
                reasons.append("target_type_unknown")
            elif row["data_type"] != sem.UNKNOWN and row["data_type"] not in targets:
                reasons.append("target_type_mismatch")
            candidate_inputs = _root_inputs(row["request"], direct_inputs, artifact_inputs, input_cache)
            if current_inputs is None or candidate_inputs is None:
                reasons.append("input_unknown")
            elif current_inputs != candidate_inputs:
                reasons.append("input_mismatch")
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
        types = type_counts(p, rid, lambda row: reader.workspace(locations.get((row["workspace"], row["path"]))))
        check()
        candidates.sort(reverse=True)
        summary = {
            "status": "ok", "ms": _ms(started), "model_sha256": model.sha256, "rows_invalid": invalid,
            "runs": len(own_runs), "artifacts": len(own_arts), "edges": len(p.edges),
            "unknown_ratio": _unknown_ratio(own_runs + own_arts), "incomplete": incomplete,
            "history_artifacts": population, "candidates": len(candidates),
            "candidate_refs": ["sem:" + opaque("art", art)[:8] for _, art in candidates[:MAX_CANDIDATE_REFS]],
            "excluded": excluded, "lineage": lineage, "types": types,
            "declarations": declaration_counts((snap["requests"].get(rid) or {}).get("output_types_stats")),
        }
        return summary, hashes
    except ShadowTimeout as exc:
        return _failed("timeout", exc, started), hashes
    except ShadowStop:
        raise
    except Exception as exc:  # noqa: BLE001 - fail open: a metric, never the request
        return _failed("error", exc, started), hashes


def type_counts(p: Any, rid: str, workspace_state: Callable[[Mapping[str, Any]], str]) -> dict:
    """This request's artifacts by EDAM id (or local / unknown) and basis, per field (#221).

    The zone gate comes first: an artifact whose workspace is not a readable same-host folder in an allowed zone
    is counted only as ``withheld``, so restricted or unchecked outputs never add to a per-type count. Data types
    are the provenance model's judged value; formats come from the same reader (declarations, then extensions)."""
    vocab = output_vocab.current()
    types: dict[str, dict[str, dict[str, int]]] = {name: {} for name in output_types.FIELDS}
    for _, row in sorted(p.artifacts.items()):
        if row["request"] != rid:
            continue
        visible = workspace_state(row) == "ok"
        fields = row.get("_types") or {}
        for name in output_types.FIELDS:
            if name == "data_type":
                key = row["data_type"] if row["data_type"] != sem.UNKNOWN else None
                basis = "declared" if key else "unknown"
            else:
                field = fields.get(name)
                key = field.value if field is not None and field.basis != "unknown" else None
                basis = field.basis if key else "unknown"
            bucket = ("withheld" if not visible else
                      ((vocab.edam_id(key) if vocab else None) or "local") if key else "unknown")
            cell = types[name].setdefault(bucket, {})
            cell[basis] = cell.get(basis, 0) + 1
    return {name: dict(sorted(cells.items())) for name, cells in types.items()}


def declaration_counts(stats: Any) -> dict | None:
    """The plan's declaration counts, rebuilt from fixed fields only (outputs is the denominator)."""
    if not isinstance(stats, Mapping):
        return None
    out = {k: int(stats.get(k) or 0) for k in DECLARATION_COUNTS if isinstance(stats.get(k) or 0, int)}
    issues = stats.get("issues") if isinstance(stats.get("issues"), Mapping) else {}
    out["issues"] = {k: int(issues[k]) for k in output_types.ISSUES if isinstance(issues.get(k), int)}
    return out


def type_problems(line: Mapping[str, Any], allowed_ids: Iterable[str]) -> list[str]:
    """Why a line's type fields may not be written: a finite allow-list, keys and values both (#221).

    Bucket keys are the EDAM ids of the loaded subset or local / unknown / withheld; never a key, label, name,
    path or a string merely shaped like an id."""
    problems: list[str] = []
    allowed = set(allowed_ids) | set(TYPE_BUCKETS)
    version = line.get("vocab_sha256")
    if version is not None and not (isinstance(version, str) and SHA_HEX.fullmatch(version)):
        problems.append("type_version")

    def counts(cells: Any, keys: Iterable[str]) -> bool:
        return isinstance(cells, Mapping) and all(k in set(keys) and isinstance(v, int) and not isinstance(v, bool)
                                                  and v >= 0 for k, v in cells.items())

    prov = line.get("provenance") if isinstance(line.get("provenance"), Mapping) else {}
    types = prov.get("types")
    if types is not None:
        if not isinstance(types, Mapping) or set(types) - set(output_types.FIELDS):
            problems.append("type_fields")
        else:
            for cells in types.values():
                if not isinstance(cells, Mapping) or not all(k in allowed and counts(v, TYPE_BASIS)
                                                             for k, v in cells.items()):
                    problems.append("type_bucket")
    decl = prov.get("declarations")
    if decl is not None and not (isinstance(decl, Mapping) and set(decl) <= {*DECLARATION_COUNTS, "issues"}
                                 and counts({k: v for k, v in decl.items() if k != "issues"}, DECLARATION_COUNTS)
                                 and counts(decl.get("issues", {}), output_types.ISSUES)):
        problems.append("type_declarations")
    objects = line.get("objects") if isinstance(line.get("objects"), Mapping) else {}
    seen = objects.get("artifact_types")
    if seen is not None and not (isinstance(seen, Mapping) and set(seen) <= set(output_types.FIELDS)
                                 and all(counts(v, TYPE_BASIS) for v in seen.values())):
        problems.append("type_objects")
    return problems


def failed_line(snap: Mapping[str, Any], status: str, exc: BaseException, *, epoch: int, ms: float) -> dict:
    """A request whose job stopped before the models ran: ids and the failure only, so the report counts it."""
    if snap.get("job") == "followup":  # semantics-hook: actions
        return _followup_failed(snap, status, exc, epoch=epoch, ms=ms)  # semantics-hook: actions
    rid = snap["rid"]
    req = (snap.get("requests") or {}).get(rid) or {}
    project = req.get("project_id")
    model = {"status": status, "error_kind": type(exc).__name__, "ms": 0.0}
    return {"v": 1, "type": "request", "ts": round(time.time(), 3), "epoch": epoch, "request_id": rid,
            "project": opaque("project", project)[:12] if project else None, "lane": req.get("lane"),
            "mode": req.get("mode"), "status": req.get("status"), "provenance": dict(model), "objects": dict(model),
            "busy_skipped": int(snap.get("busy_skipped") or 0), "snapshot_ms": snap.get("snapshot_ms"), "ms": ms}


def compute_line(snap: Mapping[str, Any], observed: dict[str, dict], check: Callable[[], None], *,
                 epoch: int, hash_over: Callable[[], bool] | None = None) -> dict:
    """One request line: both models side by side, ids, kinds, hashes and counts only.

    ``hash_over`` says when output hashing has used its share of the time cap (#159)."""
    if snap.get("job") == "followup":  # semantics-hook: actions
        return _followup_compute(snap, check, epoch=epoch)  # semantics-hook: actions
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
        "rows_ms": snap.get("rows_ms"), "vocab_sha256": (output_vocab.current() or _NO_VOCAB).sha256,
    }
    line["objects"] = compute_objects(snap, check)
    reader = Reader(snap, check, hash_over)
    line["provenance"], hashes = compute_provenance(snap, reader, observed, check)
    workspaces: dict[str, int] = {}
    for state in reader.workspace_state.values():
        workspaces[state] = workspaces.get(state, 0) + 1
    line["hash"] = {**hashes, "workspaces": dict(sorted(workspaces.items()))}
    if "actions" in snap:  # semantics-hook: actions
        line["actions"] = compute_actions(snap, check)  # semantics-hook: actions
    line["ms"] = _ms(started)
    return line


# semantics-actions: begin (#149 결정 13 A1; scripts/semantics_shadow_remove.py --only actions deletes this block)
# The action layer's shadow (semantics_actions.py): the hook lines marked for actions call
# these. Off by default; nothing here executes an action, and with actions off none of it runs or is imported.

ACTIONS_HELD = ("confirm",)  # A2 (CLI execution of request.followup) is held for its own PR
ACTION_BACKLOG = 20  # follow-up observations kept behind a full queue; more are dropped and counted


def _actions() -> Any:
    from . import semantics_actions
    return semantics_actions


def _actions_config(cfg: ShadowConfig, value: Any, raw: Any) -> ShadowConfig:
    """``actions: shadow`` adds the A1 observations to the shadow. Any other value keeps actions off with one
    warning and leaves the shadow as it was; no value turns execution on."""
    from dataclasses import replace
    mode = _mode(value)
    if mode == "shadow":
        return replace(cfg, actions=True)
    if mode != "off":
        key = "actions:" + repr(raw)[:500]
        if key not in _warned:
            _warned.add(key)
            log.warning("semantics actions setting ignored (%s); actions stay off and the shadow goes on",
                        "confirm is held (A2); this version records only" if mode in ACTIONS_HELD
                        else "actions must be off or shadow")
    return cfg


def actions_setting(settings: Any) -> str:
    """What the setting asks of the action layer: shadow, off, held (confirm) or invalid."""
    if configured(settings) != "shadow":
        return "off"
    raw = getattr(settings, "semantics", None)
    mode = _mode(raw.get("actions") if isinstance(raw, Mapping) else None)
    return mode if mode in ("off", "shadow") else "held" if mode in ACTIONS_HELD else "invalid"


def _actions_inputs(hub: Any, rid: str) -> dict:
    """Event loop side: states, times and booleans from memory, like take_snapshot."""
    from ..adapters import enforces_read_only
    return _actions().request_inputs(hub.requests[rid], hub.approvals, hub.agents, getattr(hub, "agent_runner", {}),
                                     cso_agent=hub.s.orchestrator.cso_agent, recruiter=hub.s.recruit.agent_id,
                                     read_only=enforces_read_only, now=time.time())


def _actions_rows(snap: dict, own: Mapping[str, Any], decisions: Mapping[str, Any]) -> None:
    rows = _actions().row_inputs(own, decisions)
    acts = snap["actions"]
    acts["tasks"], acts["decided"] = rows["tasks"], rows["decided"]
    acts["sensitive"] = [*(acts.get("sensitive") or []), *rows["sensitive"]]


def compute_actions(snap: Mapping[str, Any], check: Callable[[], None]) -> dict:
    started = time.perf_counter()
    try:
        out = _actions().evaluate(snap["actions"], check)
        check()
        return {"status": "ok", "ms": _ms(started), **out}
    except ShadowTimeout as exc:
        return _failed("timeout", exc, started)
    except ShadowStop:
        raise
    except Exception as exc:  # noqa: BLE001 - fail open: a metric, never the request
        return _failed("error", exc, started)


def _followup_compute(snap: Mapping[str, Any], check: Callable[[], None], *, epoch: int) -> dict:
    started = time.perf_counter()
    check()
    line = _actions().followup_line(snap["actions"], rid=snap["rid"], epoch=epoch, ts=round(time.time(), 3),
                                    busy_skipped=int(snap.get("busy_skipped") or 0))
    line["ms"] = _ms(started)
    return line


def _followup_failed(snap: Mapping[str, Any], status: str, exc: BaseException, *, epoch: int, ms: float) -> dict:
    acts = snap.get("actions") or {}
    return {"v": 1, "type": "followup", "ts": round(time.time(), 3), "epoch": epoch, "request_id": snap.get("rid"),
            "phase": acts.get("phase"), "key": acts.get("key"), "status": status, "error_kind": type(exc).__name__,
            "ms": ms}


def _actions_shape(line: Mapping[str, Any]) -> list[str]:
    if "actions" not in line and line.get("type") != "followup":
        return []
    return _actions().shape_problems(line)


def _actions_boundary(line: Mapping[str, Any], boundary: list[dict[str, str]]) -> list[str]:
    failures = _actions_shape(line)
    if failures:
        boundary.append({"field": "actions", "class": "schema"})
    return failures


def _actions_failed(line: Mapping[str, Any]) -> bool:
    if "actions" in line:
        return not isinstance(line["actions"], Mapping) or line["actions"].get("status") != "ok"
    return line.get("type") == "followup" and line.get("status") != "ok"


def _actions_report(rep: dict, paths: ShadowPaths, settings: Any) -> None:
    """Add the A1 section to the report when actions are set or recorded; otherwise the report stays as it was."""
    setting = actions_setting(settings)
    lines, _ = read_lines(paths)
    if setting == "off" and not any(line.get("type") == "followup" or "actions" in line for line in lines):
        return
    rep["actions"] = _actions().report(lines, setting=setting, on=setting == "shadow" and bool(rep["state"]["on"]))


def _actions_render(rep: Mapping[str, Any]) -> list[str]:
    return _actions().render(rep) if "actions" in rep else []
# semantics-actions: end


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
        self.window_lock = threading.Lock()  # orders breaker.json writes without holding self.lock
        self.window_seq = self.window_saved = 0
        self.state_mtime: float | None = None
        self.counts = {"lines": 0, "failures": 0, "busy": 0, "discarded": 0}
        self.thread_name = f"labhq-semantics-shadow-{next(_SERVICES)}"
        self.pending = 0  # queued or running jobs
        self.action_backlog: deque = deque()  # semantics-hook: actions (follow-up observations behind the queue)
        self.action_skipped = 0  # semantics-hook: actions (dropped: queue and backlog full)

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
            if not service.latched and not sqlite_json1():  # every job would fail on the row query
                service.trip("sqlite_json1_missing")
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
            window = read_breaker(self.paths, self.epoch)
        except (OSError, ValueError, TypeError) as exc:
            self.trip("breaker_storage", kind=type(exc).__name__)
            return
        if disabled is not None:
            self.latched = disabled["reason"]
        elif window is not None:  # this epoch's failures before a restart still count (#173)
            self.recent.extend(window[0])
            self.consecutive = window[1]
            self.check_window()

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
            self.drop_backlog()  # semantics-hook: actions (#258)
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
        boundary = _clean_boundary_details(detail.get("boundary")) if reason == "info_boundary" else []
        try:
            write_disabled(self.paths, reason, epoch, counts, boundary=boundary)
        except OSError as exc:
            log.warning("semantics disabled.json not written (%s); off for this process", type(exc).__name__)
        try:
            line = {"v": 1, "type": "auto_off", "ts": round(time.time(), 3), "epoch": epoch,
                    "reason": reason, "counts": counts}
            if boundary:
                line["boundary"] = boundary
            append_line(self.paths, line)
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
                self.yield_followup()  # semantics-hook: actions (a waiting follow-up never takes this slot)
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
            self.outcome(failed=True, on_loop=True)

    # semantics-actions: begin (#149 결정 13 A1; scripts/semantics_shadow_remove.py --only actions deletes this)
    def after_followup(self, rid: str, fid: str | None, phase: str, outcome: str | None = None) -> None:
        """A follow-up was asked, refused or ended. Queue its observation: never raises, never waits, never counts
        toward the B1 busy limit. Behind a busy queue it waits in a backlog of ACTION_BACKLOG, in order and after
        the queued job; past that it is dropped and counted. With actions off it returns before reading anything."""
        if not self.cfg.actions:
            return
        try:
            self.refresh()
            self.check_stuck()
            if self.latched:
                return
            from ..adapters import enforces_read_only
            inputs = _actions().followup_inputs(self.hub.requests[rid], fid, phase, outcome, self.hub.agents,
                                                cso_agent=self.hub.s.orchestrator.cso_agent,
                                                read_only=enforces_read_only, now=time.time())
            snap = json.loads(json.dumps({"job": "followup", "rid": rid, "actions": inputs}, default=str))
            with self.lock:
                queued = False
                if not self.action_backlog:  # an earlier observation still waiting goes first
                    try:
                        self.queue.put_nowait((self.gen, self.queue, snap))
                        queued = True
                    except queue.Full:
                        pass
                if queued:
                    self.ensure_thread()
                elif len(self.action_backlog) >= ACTION_BACKLOG:
                    self.action_skipped += 1  # written with the next backlog line, which a drop guarantees
                    return
                else:
                    self.action_backlog.append((self.gen, snap))
                self.pending += 1
        except Exception as exc:  # noqa: BLE001 - the follow-up itself must never see this
            log.warning("semantics actions skipped a follow-up observation (%s)", type(exc).__name__)
            self.outcome(failed=True, on_loop=True)

    def drop_backlog(self) -> None:
        """Under self.lock, when a new epoch starts (#258): the closed epoch's waiting follow-up observations, their
        drop count and their share of pending go, so neither drain() nor the next follow-up waits on work that would
        only be discarded."""
        dropped = len(self.action_backlog)
        self.action_backlog.clear()
        self.action_skipped = 0
        self.counts["discarded"] += dropped
        self.pending = max(0, self.pending - dropped)

    def yield_followup(self) -> None:
        """Event loop side, under self.lock, just before a request job is queued: a follow-up observation still
        waiting in the queue of one steps back to the front of the backlog, so a request job finds the queue as
        it would without actions and B1's busy count never sees a follow-up. With actions off it does nothing."""
        if not self.cfg.actions or not self.queue.full():
            return
        try:
            job = self.queue.get_nowait()
        except queue.Empty:  # the worker took it first
            return
        if isinstance(job[2], Mapping) and job[2].get("job") == "followup":
            self.action_backlog.appendleft((job[0], job[2]))  # still pending: work_backlog runs it next
        else:
            self.queue.put_nowait(job)  # a request job: only putters hold self.lock, so its slot is still free

    def work_backlog(self, jobs: queue.Queue) -> None:
        """Worker side, after each job: the follow-up observations that found the queue busy, oldest first, and
        only while the queue is empty, so a request job queued before them is never overtaken."""
        while jobs is self.queue:
            with self.lock:
                if not self.action_backlog or not jobs.empty():
                    return
                gen, snap = self.action_backlog.popleft()
                snap = {**snap, "busy_skipped": self.action_skipped}  # drops so far ride on this line
                self.action_skipped = 0
            try:
                if gen != self.gen or self.latched or self.external_off():
                    self.counts["discarded"] += 1
                    continue
                self.work(gen, snap)
            except Exception as exc:  # noqa: BLE001
                log.warning("semantics actions observation failed (%s)", type(exc).__name__)
                self.outcome(failed=True)
            finally:
                with self.lock:
                    if jobs is self.queue:  # a replaced queue's count was cleared with it (#258)
                        self.pending = max(0, self.pending - 1)
    # semantics-actions: end

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
                    if jobs is self.queue:  # a new epoch's replaced queue took this job's count with it (#258)
                        self.pending = max(0, self.pending - 1)
                self.work_backlog(jobs)  # semantics-hook: actions

    def work(self, gen: int, snap: dict) -> None:
        started = time.monotonic()
        deadline = started + self.cfg.timeout_s
        with self.lock:
            self.current = (gen, started)
        watchdog = threading.Timer(self.stuck_s, self.watch, args=(gen, started))
        watchdog.daemon = True
        watchdog.start()

        next_look = [started + EXTERNAL_LOOK_S]
        hash_until = started + self.cfg.timeout_s * HASH_SHARE

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
            line = compute_line(snap, observed, check, epoch=self.epoch,
                                hash_over=lambda: time.monotonic() > hash_until)
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
            vocabulary = output_vocab.current() or _NO_VOCAB
            allowed = {"vocab_sha256": vocabulary.sha256} if vocabulary.sha256 else {}
            boundary = boundary_findings(line, sensitive_values(snap), allowed_fields=allowed)
            problems = boundary_problems(line, sensitive_values(snap), allowed_fields=allowed)
            type_failures = type_problems(line, vocabulary.edam_ids)
            type_fields = {"type_version": "vocab_sha256", "type_fields": "provenance.types",
                           "type_bucket": "provenance.types", "type_declarations": "provenance.declarations",
                           "type_objects": "objects.artifact_types"}
            boundary += [{"field": type_fields.get(problem, "unknown"), "class": problem}
                         for problem in type_failures]
            problems += type_failures
            problems += _actions_boundary(line, boundary)  # semantics-hook: actions
        except Exception as exc:  # noqa: BLE001 - an unchecked line is never written
            log.warning("semantics shadow boundary check failed (%s); record not written", type(exc).__name__)
            self.outcome(failed=True)
            return
        if problems:
            self.trip("info_boundary", boundary=boundary)
            return
        failed = False
        for model in [m for m in MODELS if m in line]:
            if line[model].get("status") != "ok":
                failed = True
                log.warning("semantics shadow %s %s (%s)", model, line[model].get("status"),
                            line[model].get("error_kind"))
        failed = _actions_failed(line) or failed  # semantics-hook: actions
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
        self.outcome(failed=failed, followup=line.get("type") == "followup")

    def outcome(self, *, failed: bool, on_loop: bool = False, followup: bool = False) -> None:
        """Count one job. ``on_loop``: called on the gateway event loop, so breaker.json is written by a short
        daemon thread instead of there (#173). ``followup``: a follow-up observation that succeeded neither resets
        nor dilutes the request window; one that failed still counts (#260)."""
        with self.lock:
            if failed:
                self.counts["failures"] += 1
            elif followup:
                return
            self.recent.append(failed)
            self.consecutive = self.consecutive + 1 if failed else 0
            self.window_seq += 1
            window = (self.window_seq, self.epoch, list(self.recent), self.consecutive)
        self.check_window()
        if on_loop:
            threading.Thread(target=self.keep_window, args=window, name=f"{self.thread_name}-breaker",
                             daemon=True).start()
        else:
            self.keep_window(*window)

    def keep_window(self, *window: Any) -> None:
        try:
            self.save_window(*window)
        except OSError as exc:  # a window that cannot be kept would forget failures at the next restart
            self.trip("breaker_storage", kind=type(exc).__name__)

    def check_window(self) -> None:
        with self.lock:
            consecutive, recent = self.consecutive, sum(self.recent)
        if consecutive >= CONSECUTIVE_FAILURES:
            self.trip("consecutive_failures")
        elif recent >= RECENT_FAILURES:
            self.trip("recent_failures")

    def save_window(self, seq: int, epoch: int, recent: list[bool], consecutive: int) -> None:
        """Write the failure window to breaker.json (#173). Outside self.lock, so the event loop never waits on the
        disk; a later outcome that already wrote wins over an earlier one still on its way."""
        with self.window_lock:
            if seq > self.window_saved:
                write_breaker(self.paths, epoch, recent, consecutive)
                self.window_saved = seq

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


HASH_COUNTS = ("hashed", "observed_new", "verified", "changed")
WORKSPACE_STATES = ("ok", "remote", "zone_excluded", "not_regular", "missing")


def _number(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and math.isfinite(value))


def _counts(value: Any, keys: Iterable[str] | None = None) -> bool:
    """None, or an object whose values (only `keys`, when given) are finite numbers."""
    if value is None:
        return True
    return isinstance(value, Mapping) and all(_number(v) for k, v in value.items() if keys is None or k in keys)


def readable_request(line: Mapping[str, Any]) -> bool:
    """Every field the report adds up, in the shape the worker writes it. A row left by a torn write or an older
    schema is a broken line, so one bad row does not end the report."""
    models = [line.get("provenance"), line.get("objects"), line.get("hash")]
    if not all(m is None or isinstance(m, Mapping) for m in models):
        return False
    prov, objs, hashes = (m or {} for m in models)
    return (all(_number(line.get(k)) for k in ("ms", "snapshot_ms", "busy_skipped"))
            and all(_number(m.get("ms")) for m in (prov, objs))
            and all(_number(prov.get(k)) for k in ("candidates", "unknown_ratio"))
            and _counts(prov.get("excluded")) and _counts(prov.get("lineage"), ("gaps",))
            and _counts(objs.get("objects"))
            and all(_number(objs.get(k)) for k in ("link_total", "unresolved", "pending_jobs"))
            and _counts(hashes, HASH_COUNTS) and _counts(hashes.get("workspaces"), WORKSPACE_STATES))


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
    requests = [l for l in lines if l.get("type") == "request" and readable_request(l)]
    broken += sum(1 for l in lines if l.get("type") == "request") - len(requests)
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
                       with_unresolved=sum(1 for r in ok if (r.get("unresolved") or 0) > 0),
                       pending_jobs=sum(int(r.get("pending_jobs") or 0) for r in ok))
        return out

    versions: dict[str, int] = {}
    for r in requests:  # model file and output type vocabulary, so a vocabulary change is not hidden (#221)
        model = (r.get("provenance") or {}).get("model_sha256") if isinstance(r.get("provenance"), Mapping) else None
        key = f"{str(model or '-')[:12]}/{str(r.get('vocab_sha256') or '-')[:12]}"
        versions[key] = versions.get(key, 0) + 1
    types: dict[str, dict[str, dict[str, int]]] = {name: {} for name in output_types.FIELDS}
    declarations = {**{k: 0 for k in DECLARATION_COUNTS}, "issues": {}}
    for r in requests:
        prov = r.get("provenance") if isinstance(r.get("provenance"), Mapping) else {}
        if prov.get("status") != "ok":
            continue
        for name, cells in (prov.get("types") or {}).items() if isinstance(prov.get("types"), Mapping) else ():
            for bucket, basis in (cells or {}).items() if isinstance(cells, Mapping) else ():
                into = types.setdefault(str(name), {}).setdefault(str(bucket), {})
                for k, n in (basis or {}).items() if isinstance(basis, Mapping) else ():
                    into[str(k)] = into.get(str(k), 0) + int(n or 0)
        decl = prov.get("declarations") if isinstance(prov.get("declarations"), Mapping) else {}
        for k in DECLARATION_COUNTS:
            declarations[k] += int(decl.get(k) or 0)
        for code, n in (decl.get("issues") or {}).items() if isinstance(decl.get("issues"), Mapping) else ():
            declarations["issues"][str(code)] = declarations["issues"].get(str(code), 0) + int(n or 0)
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
                  "reason": (disabled or {}).get("reason"), "epoch": state.get("epoch"),
                  "boundary": _clean_boundary_details((disabled or {}).get("boundary"))},
        "requests": len(requests), "broken_lines": broken,
        "period": [_day(requests[0].get("ts")), _day(requests[-1].get("ts"))] if requests else None,
        "busy_skipped": sum(int(r.get("busy_skipped") or 0) for r in requests),
        "total_p95_ms": _pct(total_ms, 0.95),
        "snapshot_p95_ms": _pct([float(r.get("snapshot_ms") or 0) for r in requests], 0.95),
        "provenance": model_stats("provenance"), "objects": model_stats("objects"),
        "hash": {k: sum(int((r.get("hash") or {}).get(k) or 0) for r in requests)
                 for k in HASH_COUNTS},
        "workspaces": {k: sum(int(((r.get("hash") or {}).get("workspaces") or {}).get(k) or 0) for r in requests)
                       for k in WORKSPACE_STATES},
        "research_with_candidates": research_with_candidates,
        "versions": dict(sorted(versions.items())),
        "types": {k: dict(sorted(v.items())) for k, v in types.items()}, "declarations": declarations,
        "marks": {"reviewed": len(marks), "wrong": wrong},
        "auto_off": [{"day": _day(l.get("ts")), "epoch": l.get("epoch"), "reason": l.get("reason"),
                      "boundary": _clean_boundary_details(l.get("boundary"))}
                      for l in lines if l.get("type") == "auto_off"],
        "deadline": deadline.isoformat(), "midpoint": (INTRODUCED + timedelta(days=MIDPOINT_DAYS)).isoformat(),
        "propose_removal": proposals,
    }


def render_report(rep: Mapping[str, Any]) -> str:
    st = rep["state"]
    p, o = rep["provenance"], rep["objects"]

    def v(x: Any) -> str:
        return "-" if x is None else str(x)

    def boundary(value: Any) -> str:
        detail = _clean_boundary_details(value)
        return " · 경계 " + ", ".join(f"{item['field']}:{item['class']}" for item in detail) if detail else ""

    out = [
        "semantics shadow report (로컬 기록, 네트워크 없음)",
        f"상태: {'on' if st['on'] else 'off'} · 설정 {st['setting']} · 자동 off {v(st['reason'])} · "
        f"epoch {v(st['epoch'])}{boundary(st.get('boundary'))}",
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
        f"({o['with_unresolved']}건) · 대기 job {o['pending_jobs']} |",
        "",
        "후보 제외 이유: " + ", ".join(f"{k} {n}" for k, n in p["excluded"].items()),
        "hash: " + ", ".join(f"{k} {n}" for k, n in rep["hash"].items()) + " · 작업 폴더: "
        + ", ".join(f"{k} {n}" for k, n in rep["workspaces"].items()),
        f"후보 있는 연구 요청 {rep['research_with_candidates']} · 검토 표시 {rep['marks']['reviewed']}"
        f"(wrong {rep['marks']['wrong']})",
        "판본(model/vocab): " + (", ".join(f"{k} {n}" for k, n in rep["versions"].items()) or "-"),
        "산출 종류 선언: 계획 산출 {outputs} · data 선언 {data_declared} · format 선언 {format_declared}".format(
            **rep["declarations"]) + " · 버림 " + (", ".join(f"{k} {n}" for k, n in rep["declarations"]["issues"].items())
                                                 or "0"),
        *[f"{name}: " + (", ".join(f"{bucket} " + "/".join(f"{b} {n}" for b, n in sorted(cells.items()))
                                   for bucket, cells in rep["types"][name].items()) or "-")
          for name in rep["types"]],
        "declared는 CSO 선언, inferred는 확장자로 본 이름일 뿐이다. 어느 쪽도 내용 검증이 아니다. withheld는 zone·host를 "
        "확인하지 못해 종류별로 세지 않은 산출이다.",
        "",
        "자동 off 이력:" + ("" if rep["auto_off"] else " 없음"),
    ]
    out += [f"- {a['day']} epoch {v(a['epoch'])}: {a['reason']}{boundary(a.get('boundary'))}"
            for a in rep["auto_off"]]
    out += ["", f"중간 점검 {rep['midpoint']} · 판정 기한 {rep['deadline']} (기준은 전부 미측정 제안치)"]
    if rep["propose_removal"]:
        out += ["PROPOSE_REMOVAL — 제거 제안(결정은 PI):"] + [f"- {x}" for x in rep["propose_removal"]]
        out.append("제거: semantics: off → scripts/semantics_shadow_remove.py → state_dir/semantics 삭제(선택)")
    else:
        out.append("제거 제안: 없음")
    out += _actions_render(rep)  # semantics-hook: actions
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
    for stale in (paths.disabled, paths.breaker):  # the new epoch starts with no failures
        if stale.exists():
            stale.unlink()
    append_line(paths, {"v": 1, "type": "enable", "ts": round(time.time(), 3), "epoch": epoch,
                        "previous_reason": (disabled or {}).get("reason")})
    head = f"꺼진 이유: {disabled['reason']}. " if disabled else "꺼져 있지 않았습니다. "
    return head + f"새 epoch {epoch}을 엽니다. 설정 mode가 shadow인 gateway는 다음 요청부터 기록합니다."


def recorded_candidate(paths: ShadowPaths, rid: str, ref: str) -> bool:
    """True when a recorded request line of ``rid`` listed ``ref`` among its candidate refs (#175)."""
    lines, _ = read_lines(paths)
    for line in lines:
        prov = line.get("provenance")
        refs = prov.get("candidate_refs") if isinstance(prov, Mapping) else None
        if line.get("type") == "request" and line.get("request_id") == rid and isinstance(refs, list) and ref in refs:
            return True
    return False


def mark(paths: ShadowPaths, rid: str, ref: str, verdict: str) -> str:
    if not re.fullmatch(r"req_[A-Za-z0-9]{1,64}", rid):
        raise ValueError("request id must look like req_<id>")
    if not re.fullmatch(r"sem:[0-9a-f]{8}", ref):
        raise ValueError("ref must be sem:<8 hex>")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not recorded_candidate(paths, rid, ref):  # a typo must not turn the shadow off or skew the wrong ratio
        raise ValueError(f"{rid} has no recorded candidate {ref}; nothing written")
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
            _actions_report(rep, paths, settings)  # semantics-hook: actions
            print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_report(rep))
        elif args.semantics_cmd == "enable":
            print(enable(paths))
        else:
            print(mark(paths, args.request_id, args.ref, args.verdict))
    except (OSError, ValueError) as exc:
        print(f"semantics {args.semantics_cmd}: {exc if isinstance(exc, ValueError) else type(exc).__name__}")
        return 1
    except Exception as exc:  # a record shape the checks above missed: one line, not a traceback
        print(f"semantics {args.semantics_cmd}: unexpected {type(exc).__name__} while reading the shadow records")
        return 1
    return 0
