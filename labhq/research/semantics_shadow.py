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
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("labhq.semantics")

MODES = ("off", "shadow")
MODELS = ("provenance", "objects")  # the two models a line carries side by side
HELD_MODES = ("advisory", "ab")  # B2: CSO advisory and A/B, held by the PI (#149)
KEYS = ("mode", "timeout_s", "history_requests")
DEFAULT_TIMEOUT_S = 5.0
STUCK_S = 10.0                   # a job running longer than this turns the shadow off
IDLE_EXIT_S = 60.0               # an idle worker thread ends; the next request starts a new one
MAX_TASK_ROWS = 20_000
RETENTION_DAYS = 90
LOG_PART_BYTES = 23 * 1024 ** 2  # two parts plus observed.json (OBSERVED_MAX entries) stay under 50 MiB


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


# ---------------------------------------------------------------- stop signals

class ShadowStop(Exception):
    """The job ran past its time cap or was abandoned."""


class ShadowTimeout(ShadowStop):
    pass


# ---------------------------------------------------------------- the request line

_SERVICES = itertools.count(1)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def opaque(*parts: Any) -> str:
    """A stable id that does not carry the value it stands for (paths, project names)."""
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def compute_line(snap: Mapping[str, Any], observed: dict[str, dict], check: Callable[[], None], *,
                 epoch: int) -> dict:
    """One request line: ids, kinds and counts only. The models are added to it in turn."""
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
            line = compute_line(snap, {}, check, epoch=self.epoch)
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
