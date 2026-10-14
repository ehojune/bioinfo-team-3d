"""Durable per-request records and optional private GitHub issue publication."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .. import __version__
from ..models import TaskResult
from ..orchestrator.cso import environment_problem, failure_kind
from ..util import short
from .github import GitHubClient, GitHubHTTPError, MAX_BODY, publish_clean, root_zone_restricted

if TYPE_CHECKING:
    from ..gateway.server import Hub

log = logging.getLogger("labhq.rounds")
REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ORIGINAL_REPORT = object()


class RoundPublicationDeferred(RuntimeError):
    """A restart-time configuration change can make this delivery publishable."""


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    """HEAD when this process first asks: the code it loaded, even if the checkout moves later."""
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return None
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       stderr=subprocess.DEVNULL, timeout=2, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _manifest(path: str | None) -> dict:
    if not path:
        return {}
    try:
        return json.loads((Path(path) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _clean_tree(value: Any, clean) -> Any:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return {k: _clean_tree(v, clean) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_tree(v, clean) for v in value]
    return value


def environment_snapshot(hub: "Hub") -> dict:
    """What ran the request. Taken once when it starts, so rebuilding a record later never restamps it."""
    return {"labhq_version": __version__, "git_commit": _git_commit(),
            "source_repo": hub.s.dev_log.source_repo,
            "engine_cli_versions": {k: v.get("engine_cli_versions") for k, v in hub.runner_capabilities.items()
                                    if v.get("engine_cli_versions")},
            "instance": Path(hub.s.gateway.state_dir).name,
            "state_dir_name": Path(hub.s.gateway.state_dir).name}


def record_sources(hub: "Hub") -> dict[str, dict[str, list[dict]]]:
    """Tasks, events and approval decisions grouped by request, read from the state DB once.

    Rebuilding many records (recovery at gateway start) must not decode the whole task ledger once per request:
    on the 10-08 trial state (40 requests, 18 MB of task rows) that held the start for 7 s.
    """
    grouped: dict[str, dict[str, list[dict]]] = {"tasks": {}, "events": {}, "decisions": {}}
    for task in hub.store.all("task").values():
        grouped["tasks"].setdefault(str(task.get("request_id")), []).append(task)
    for event in hub.store.events_since(0):
        grouped["events"].setdefault(str(event.get("request_id")), []).append(event)
    for decision in hub.store.all("approval_decision").values():
        grouped["decisions"].setdefault(str((decision.get("approval") or {}).get("request_id")), []).append(decision)
    return grouped


def step_status(result: dict, dispatched: bool = False) -> str:
    """Shared record/recall status without opening runner artifacts."""
    if result.get("missing_outputs"):
        return "INCOMPLETE"
    return result.get("status") or ("done" if result.get("ok") else
                                    "failed" if result else "running" if dispatched else "not_run")


def metadata_failure_kind(result: dict) -> str | None:
    """Classify saved error metadata only; never inspect result text or CLI logs."""
    if result.get("failure_kind"):
        return result["failure_kind"]
    if not result or result.get("ok") or result.get("status") == "done":
        return None
    if not any(result.get(key) for key in ("error", "error_kind", "environment", "quota_reset_at")):
        return "unknown"
    fields = {key: result[key] for key in ("error", "error_kind", "environment", "quota_reset_at", "pending_jobs")
              if key in result}
    return failure_kind(TaskResult(task_id="recall", agent_id="recall", ok=False, **fields))


def build_record(hub: "Hub", rid: str, sources: dict[str, dict[str, list[dict]]] | None = None) -> dict:
    req = hub.requests[rid]
    if sources is None:
        tasks = [v for v in hub.store.all("task").values() if v.get("request_id") == rid]
        events = [v for v in hub.store.events_since(0) if v.get("request_id") == rid]
        decisions = [v for v in hub.store.all("approval_decision").values()
                     if (v.get("approval") or {}).get("request_id") == rid]
    else:
        tasks = list(sources["tasks"].get(rid, []))
        events = list(sources["events"].get(rid, []))
        decisions = list(sources["decisions"].get(rid, []))
    decisions += [{"approval": v["approval"], "approved": None, "note": None,
                   "state": "pending"} for v in hub.approvals.values()
                  if v["approval"].get("request_id") == rid]
    # Execution choice and scope clarification are distinct cards. The state DB iterates random approval IDs,
    # not the order PI saw them; kind is only a stable tie-break for old records without decision timestamps.
    decisions.sort(key=lambda d: (d.get("decided_at") or (d.get("approval") or {}).get("created_at") or 0,
                                  (d.get("approval") or {}).get("kind") or ""))
    plan = req.get("plan") or {}
    results = req.get("results") or {}
    steps = []
    plugins = []
    models = set()
    planned = list(plan.get("steps") or [])
    for sid, result in results.items():
        if sid not in {step.get("id") for step in planned}:
            planned.append({"id": sid, "agent_id": result.get("agent_id"),
                            "depends_on": [], "outputs": []})
    for step in planned:
        sid = step.get("id")
        result = results.get(sid) or {}
        matching = [t for t in tasks if t.get("step_id") == sid]
        if sid == "direct":
            matching = [t for t in tasks if t.get("kind") == "direct"]
        # A remote runner's workdir is not on this disk: prefer what the runner sent with the result.
        manifest = result.get("provenance") or _manifest(result.get("workdir"))
        runs = manifest.get("runs") or {}
        if manifest:
            for run in runs.values():
                for plugin in run.get("plugins") or []:
                    plugins.append({"step_id": sid, **plugin})
                for key in ("model", "model_id"):
                    if run.get(key):
                        models.add(run[key])
            if manifest.get("model"):
                models.add(manifest["model"])
        duration = sum(max(0, run["ended_at"] - run["started_at"])
                       for run in runs.values() if isinstance(run, dict) and
                       isinstance(run.get("started_at"), (int, float)) and
                       isinstance(run.get("ended_at"), (int, float)))
        classified = None
        if result.get("error") and result.get("task_id") and result.get("agent_id"):
            classified = failure_kind(TaskResult.model_validate(result))
        status = step_status(result, bool(matching))
        steps.append({"id": sid, "agent_id": step.get("agent_id"), "depends_on": step.get("depends_on") or [],
                      "outputs_expected": step.get("outputs") or [], "status": status,
                      "attempts": result.get("attempts") or max((int(t.get("attempt") or 1) for t in matching), default=0),
                      "failure_kind": result.get("failure_kind") or classified,
                      "error_kind": result.get("error_kind"),
                      "error": result.get("error"),
                      "turns": result.get("turns") or next((run.get("turns") for run in runs.values()
                                                               if isinstance(run, dict) and run.get("turns") is not None), None),
                      "usage": result.get("usage") or {}, "cost_usd": result.get("cost_usd"),
                      "cost_known": result.get("cost_known"), "duration_s": result.get("duration_s", duration or None),
                      "output_paths": [str(Path(result.get("workdir") or result.get("workdir_id") or "") / p)
                                       for p in result.get("outputs") or []],
                      "missing_outputs": result.get("missing_outputs") or [],
                      "revision_failed": result.get("revision_failed"),
                      **({"environment": environment} if (environment := (
                          None if result.get("ok") or not result.get("task_id") or not result.get("agent_id")
                          else environment_problem(result))) else {}),
                      **({"facilities_fix": result["facilities_fix"]} if result.get("facilities_fix") else {}),
                      "engine_cli_version": next((run.get("engine_cli_version") for run in runs.values()
                                                  if isinstance(run, dict) and run.get("engine_cli_version")), None)})
    for ev in events:
        data = ev.get("data") or {}
        for key in ("model", "model_id"):
            if isinstance(data.get(key), str) and data[key]:
                models.add(data[key])
    for t in tasks:
        manifest = (t.get("result") or {}).get("provenance") or _manifest((t.get("result") or {}).get("workdir"))
        if manifest.get("model"):
            models.add(manifest["model"])
        for run in (manifest.get("runs") or {}).values():
            for key in ("model", "model_id"):
                if run.get(key):
                    models.add(run[key])
            for plugin in run.get("plugins") or []:
                entry = {"task_id": (t.get("payload") or {}).get("id"), **plugin}
                if entry not in plugins:
                    plugins.append(entry)
        payload = t.get("payload") or {}
        model = (payload.get("meta") or {}).get("model")
        if model:
            models.add(model)
    anomalies = []
    if any(s["attempts"] > 1 for s in steps):
        anomalies.append("retries")
    if any((d.get("approval") or {}).get("kind") == "budget" for d in decisions):
        anomalies.append("budget alarm")
    if any("cli" in str((t.get("result") or {}).get("error") or "").lower() and
           failure_kind(TaskResult.model_validate(t["result"])) == "transient"
           for t in tasks if t.get("result")):
        anomalies.append("transient CLI error")
    pending = list(req.get("pending_questions") or [])
    pending += [(d.get("approval") or {}).get("kind") for d in decisions if d.get("state") == "pending"]
    return {
        "schema_version": 1, "request_id": rid,
        "request": {"text": req.get("text"), "mode": req.get("mode"), "project_id": req.get("project_id"),
                    "meta": req.get("meta") or {},
                    "clarifications": req.get("clarifications") or [],
                    "pending_questions": req.get("pending_questions") or [],
                    "step_decisions": req.get("step_decisions") or []},
        "environment": {**(req.get("environment") or environment_snapshot(hub)),
                        "model_ids": sorted(models), "plugin_provenance": plugins},
        "plan": {"steps": planned, "warnings": plan.get("warnings") or []},
        "steps": steps,
        "review": {"verdict": (req.get("review") or {}).get("verdict"),
                   "scores": (req.get("review") or {}).get("scores") or {},
                   "revision_rounds": (req.get("review_progress") or {}).get("last_completed_revision", 0),
                   "revision_failures": [s["revision_failed"] for s in steps if s["revision_failed"]],
                   "detail": req.get("review") or {}},
        "pi_decisions": [{"kind": (d.get("approval") or {}).get("kind"), "approved": d.get("approved"),
                          "note": d.get("note"), "state": d.get("state") or "resolved"} for d in decisions],
        "result": {"status": req.get("status"), "root_cause_summary": req.get("report") or req.get("error"),
                   "report": req.get("report"), "pending_decisions": pending,
                   "anomalies": anomalies, "usage": req.get("usage") or {},
                   "cost_usd": req.get("cost_usd"), "cost_known": req.get("cost_known"),
                   "cost_summary": req.get("cost_summary"),
                   "created_at": req.get("created_at"), "finished_at": req.get("finished_at")},
    }


def render_record(record: dict, *, include_json: bool = True,
                  report_override: object = _ORIGINAL_REPORT,
                  report_notice: str | None = None) -> str:
    data = json.dumps(record, ensure_ascii=False, indent=2, default=str)
    req, result = record["request"], record["result"]
    commit = record["environment"].get("git_commit")
    source_repo = record["environment"].get("source_repo")
    shown_commit = commit or "미확인"
    if commit and isinstance(source_repo, str) and REPO_NAME.fullmatch(source_repo):
        shown_commit = f"[{commit}](https://github.com/{source_repo}/commit/{commit})"
    lines = [f"# Round {record['request_id']}", "", "## 요청", str(req.get("text") or ""),
             f"- 모드: {req.get('mode') or '—'}", f"- 프로젝트: {req.get('project_id') or '—'}",
             f"- meta: {json.dumps(req.get('meta') or {}, ensure_ascii=False)}",
             f"- 확인 질문·답변: {json.dumps(req.get('clarifications') or [], ensure_ascii=False)}",
             f"- 단계 결정: {json.dumps(req.get('step_decisions') or {}, ensure_ascii=False)}",
             "", "## 환경", f"- labhq: {record['environment']['labhq_version']}",
             f"- Git: {shown_commit}",
             f"- 모델: {', '.join(record['environment']['model_ids']) or '보고 없음'}",
             f"- 인스턴스: {record['environment']['instance']}", "", "## 계획"]
    lines += [f"- {s.get('id')}: {s.get('agent_id')} (선행: {', '.join(s.get('depends_on') or []) or '없음'})"
              for s in record["plan"]["steps"]]
    lines += [f"- 경고: {warning}" for warning in record["plan"]["warnings"]]
    lines += ["", "## 단계"]
    lines += [f"- {s['id']}: {s['status']}, 시도 {s['attempts']}, 산출물 {', '.join(s['output_paths']) or '없음'}"
              for s in record["steps"]]
    lines += ["", "## 리뷰", f"- 판정: {record['review']['verdict'] or '없음'}",
              f"- 점수: {json.dumps(record['review']['scores'], ensure_ascii=False)}",
              f"- 수정 회차: {record['review']['revision_rounds']}", "", "## PI 결정"]
    lines += [f"- {d['kind']}: {d['approved']} {d['note'] or ''}" for d in record["pi_decisions"]]
    if report_override is _ORIGINAL_REPORT:
        report = result.get("report") or result.get("root_cause_summary") or ""
    else:
        report = str(report_override)
    lines += ["", "## 결과", f"- 상태: {result['status']}",
              f"- 미결정: {json.dumps(result['pending_decisions'], ensure_ascii=False)}",
              f"- 이상 징후: {', '.join(result['anomalies']) or '없음'}",
               report]
    if report_notice:
        lines += ["", report_notice]
    if include_json:
        lines += ["", "<details><summary>JSON schema v1</summary>", "", "```json", data, "```", "", "</details>", ""]
    else:
        lines += ["", f"> JSON 원본은 로컬 rounds/{record['request_id']}.json에 있음", ""]
    return "\n".join(lines)


def _line_prefix(text: str, limit: int) -> str:
    """Fit whole Markdown lines, closing a fenced block when the cut lands inside one."""
    if limit <= 0:
        return ""
    kept: list[str] = []
    used = 0
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        next_fence = fence
        if match:
            token = match.group(1)
            if fence is None:
                next_fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1]:
                next_fence = None
        closing = f"\n{next_fence[0] * next_fence[1]}" if next_fence else ""
        if used + len(line) + len(closing) > limit:
            break
        kept.append(line)
        used += len(line)
        fence = next_fence
    prefix = "".join(kept).rstrip("\r\n")
    if fence:
        prefix += f"\n{fence[0] * fence[1]}"
    return prefix


def _fit_variable_sections(prefix: str, sections: list[tuple[str, str]], suffix: str, limit: int) -> str:
    """Share the remaining body budget across every variable section, keeping whole lines."""
    def assemble(contents: list[str]) -> str:
        parts = [prefix]
        for (heading, _), content in zip(sections, contents):
            parts += ["", heading]
            if content:
                parts.append(content)
        parts += ["", suffix]
        return "\n".join(parts)

    empty = assemble([""] * len(sections))
    available = max(0, limit - len(empty))
    texts = [text for _, text in sections]
    allocations = [0] * len(texts)
    active = set(range(len(texts)))
    while active and available:
        share = available // len(active)
        if share == 0:
            break
        completed = {i for i in active if len(texts[i]) - allocations[i] <= share}
        if completed:
            for i in completed:
                added = len(texts[i]) - allocations[i]
                allocations[i] += added
                available -= added
            active -= completed
            continue
        for i in sorted(active):
            added = min(share, available)
            allocations[i] += added
            available -= added
        break

    body = assemble([_line_prefix(text, allocations[i]) for i, text in enumerate(texts)])
    return body if len(body) <= limit else _line_prefix(body, limit)


def _compact_record_body(record: dict, marker: str, limit: int) -> str:
    """Render every variable record section within one shared Markdown budget."""
    req, result = record["request"], record["result"]
    commit = record["environment"].get("git_commit")
    source_repo = record["environment"].get("source_repo")
    shown_commit = commit or "미확인"
    if commit and isinstance(source_repo, str) and REPO_NAME.fullmatch(source_repo):
        shown_commit = f"[{commit}](https://github.com/{source_repo}/commit/{commit})"

    plan_lines = [
        f"- {step.get('id')}: {step.get('agent_id')} (선행: {', '.join(step.get('depends_on') or []) or '없음'})"
        for step in record["plan"]["steps"]
    ] + [f"- 경고: {warning}" for warning in record["plan"]["warnings"]]
    step_lines: list[str] = []
    for step in record["steps"]:
        step_lines.append(f"- {step['id']}: {step['status']}, 시도 {step['attempts']}")
        outputs = step["output_paths"]
        step_lines += [f"  - 산출물: {path}" for path in outputs] if outputs else ["  - 산출물: 없음"]
    report = str(result.get("report") or result.get("root_cause_summary") or "")
    sections = [
        ("## 요청", "\n".join([
            str(req.get("text") or ""), f"- 모드: {req.get('mode') or '—'}",
            f"- 프로젝트: {req.get('project_id') or '—'}",
            f"- meta: {json.dumps(req.get('meta') or {}, ensure_ascii=False)}",
            f"- 확인 질문·답변: {json.dumps(req.get('clarifications') or [], ensure_ascii=False)}",
            f"- 단계 결정: {json.dumps(req.get('step_decisions') or {}, ensure_ascii=False)}",
        ])),
        ("## 환경", "\n".join([
            f"- labhq: {record['environment']['labhq_version']}", f"- Git: {shown_commit}",
            f"- 모델: {', '.join(record['environment']['model_ids']) or '보고 없음'}",
            f"- 인스턴스: {record['environment']['instance']}",
        ])),
        ("## 계획", "\n".join(plan_lines)),
        ("## 단계", "\n".join(step_lines)),
        ("## 리뷰", "\n".join([
            f"- 판정: {record['review']['verdict'] or '없음'}",
            f"- 점수: {json.dumps(record['review']['scores'], ensure_ascii=False)}",
            f"- 수정 회차: {record['review']['revision_rounds']}",
        ])),
        ("## PI 결정", "\n".join(
            f"- {decision['kind']}: {decision['approved']} {decision['note'] or ''}"
            for decision in record["pi_decisions"]
        )),
        ("## 결과", "\n".join([
            f"- 상태: {result['status']}",
            f"- 미결정: {json.dumps(result['pending_decisions'], ensure_ascii=False)}",
            f"- 이상 징후: {', '.join(result['anomalies']) or '없음'}", report,
        ])),
    ]
    suffix = "\n".join([
        f"> 결과 보고서 전문은 로컬 기록 rounds/{record['request_id']}.md에 있음",
        f"> JSON 원본은 로컬 rounds/{record['request_id']}.json에 있음",
    ])
    return _fit_variable_sections(marker, sections, suffix, limit)


def _marked_body(record: dict, *, limit: int, compact: bool = True) -> str:
    marker = f"<!-- labhq round {record['request_id']} -->"
    full = marker + "\n\n" + render_record(record)
    if len(full) <= limit or not compact:
        return full
    without_json = marker + "\n\n" + render_record(record, include_json=False)
    if len(without_json) <= limit:
        return without_json
    return _compact_record_body(record, marker, limit)


class RoundRecorder:
    MAX_ATTEMPTS = 5
    RETRY_BASE_S = 1.0
    RETRY_MAX_S = 30.0

    def __init__(self, hub: "Hub", transport: httpx.AsyncBaseTransport | None = None):
        self.hub, self.s, self.transport = hub, hub.s, transport
        self.directory = self.s.path(self.s.gateway.state_dir) / "rounds"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self.retry_tasks: set[asyncio.Task] = set()
        self.generation = 0
        self.worker: asyncio.Task | None = None
        self.client: GitHubClient | None = None
        self._sleep = asyncio.sleep
        if not self.s.dev_log.repo:
            log.warning("dev_log.repo is unset; rounds are recorded locally only")

    def write(self, rid: str, sources: dict[str, dict[str, list[dict]]] | None = None) -> dict:
        req = self.hub.requests[rid]
        if not req.get("environment"):
            # Requests from before snapshots existed: keep what their first record stored, else stamp once now.
            req["environment"] = self._stored_environment(rid) or environment_snapshot(self.hub)
            self.hub.save_request(rid)
        record = build_record(self.hub, rid, sources)
        for suffix, body in (("json", json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n"),
                             ("md", render_record(record))):
            target = self.directory / f"{rid}.{suffix}"
            temporary = target.with_suffix(f".{suffix}.tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, target)
        return record

    def _stored_environment(self, rid: str) -> dict | None:
        try:
            env = json.loads((self.directory / f"{rid}.json").read_text(encoding="utf-8")).get("environment")
        except (OSError, ValueError):
            return None
        if not isinstance(env, dict):
            return None
        return {k: v for k, v in env.items() if k not in {"model_ids", "plugin_provenance"}} or None

    def submit(self, rid: str, *, attempts: int = 0) -> None:
        if not self.s.dev_log.enabled or not self.s.dev_log.repo or root_zone_restricted(self.s.policy):
            return
        self.generation += 1
        self.hub.store.put("round_delivery", rid, {"request_id": rid, "generation": self.generation,
                                                   "attempts": attempts, "state": "pending"})
        if self.worker is None or self.worker.done():
            self.worker = asyncio.get_running_loop().create_task(self._run())
        self.queue.put_nowait((rid, self.generation))

    def recover(self) -> None:
        """Rebuild every terminal/interrupted record from the DB and re-queue delivery.

        A crash can land between the DB commit, the JSON, the Markdown and the queue, so existing files prove
        nothing. publish() skips the API when the stored body digest already matches.
        """
        pending = set(self.hub.store.all("round_delivery"))
        sources = None
        for rid, req in self.hub.requests.items():
            if req.get("status") in {"interrupted", "done", "failed", "cancelled"}:
                sources = sources or record_sources(self.hub)
                self.write(rid, sources)
                pending.add(rid)
        for rid in pending:
            delivery = self.hub.store.get("round_delivery", rid) or {}
            stored_digest = (self.hub.store.get("round_issue", rid) or {}).get("sha256")
            failed_oversize = (delivery.get("state") == "failed" and
                               stored_digest != self._current_body_digest(rid) and
                               self._legacy_body_exceeded_limit(rid))
            if delivery.get("state") != "failed" or failed_oversize:
                self.submit(rid, attempts=delivery.get("attempts", 0))

    def _current_body_digest(self, rid: str) -> str | None:
        try:
            record = json.loads((self.directory / f"{rid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        def clean(value: str) -> str:
            return publish_clean(value, self.s, self.hub.requests.values())

        body = _marked_body(_clean_tree(record, clean), limit=MAX_BODY)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _legacy_body_exceeded_limit(self, rid: str) -> bool:
        """Recognize deliveries the pre-compaction publisher left permanently failed."""
        try:
            record = json.loads((self.directory / f"{rid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        def clean(value: str) -> str:
            return publish_clean(value, self.s, self.hub.requests.values())

        return len(_marked_body(_clean_tree(record, clean), limit=MAX_BODY, compact=False)) > MAX_BODY

    async def drain(self) -> None:
        await self.queue.join()

    async def _run(self) -> None:
        try:
            while True:
                rid, generation = await self.queue.get()
                deferred = False
                try:
                    delivery = self.hub.store.get("round_delivery", rid) or {}
                    if delivery.get("generation") != generation:
                        continue  # A newer record was submitted while this one waited.
                    attempts = delivery.get("attempts", 0) + 1
                    transient = False
                    rate_limited = False
                    retry_after = 0.0
                    restart_only = False
                    try:
                        published = await self.publish(rid)
                    except RoundPublicationDeferred as exc:
                        published = False
                        restart_only = True
                        log.warning("round issue publication deferred: %s", exc)
                    except Exception as exc:
                        published = False
                        transient = (isinstance(exc, httpx.TransportError) or
                                     isinstance(exc, GitHubHTTPError) and
                                     (exc.status_code in {408, 429} or exc.status_code >= 500 or exc.rate_limited))
                        if isinstance(exc, GitHubHTTPError):
                            rate_limited = exc.rate_limited
                            retry_after = exc.retry_delay_s()
                        log.warning("round issue publication failed: %s", exc)
                    # publish() awaits network I/O; do not clear or overwrite a newer submission.
                    current = self.hub.store.get("round_delivery", rid) or {}
                    if current.get("generation") != generation:
                        continue
                    if published:
                        self.hub.store.delete("round_delivery", rid)
                    elif restart_only:
                        self.hub.store.put("round_delivery", rid, {**delivery, "state": "pending"})
                    else:
                        retry = rate_limited or transient and attempts < self.MAX_ATTEMPTS
                        saved_attempts = delivery.get("attempts", 0) if rate_limited else attempts
                        self.hub.store.put("round_delivery", rid, {**delivery, "attempts": saved_attempts,
                                                                    "state": "pending" if retry else "failed"})
                        if retry:
                            delay = min(self.RETRY_MAX_S, self.RETRY_BASE_S * 2 ** (attempts - 1))
                            delay = max(delay, retry_after)
                            task = asyncio.create_task(self._retry(rid, generation, delay))
                            self.retry_tasks.add(task)
                            task.add_done_callback(self.retry_tasks.discard)
                            task.add_done_callback(lambda _: self.queue.task_done())
                            deferred = True
                finally:
                    if not deferred:
                        self.queue.task_done()
        finally:
            retries = list(self.retry_tasks)
            for task in retries:
                task.cancel()
            await asyncio.gather(*retries, return_exceptions=True)

    async def _retry(self, rid: str, generation: int, delay: float) -> None:
        # The done callback releases the old queue item after the replacement is queued,
        # including cancellation before this coroutine starts. drain() waits through backoff.
        await self._sleep(delay)
        self.queue.put_nowait((rid, generation))

    async def _may_post(self, gh: GitHubClient) -> bool:
        """Recheck before every write; the startup diagnostic never authorizes publication."""
        meta = await gh._req("GET", f"/repos/{self.s.dev_log.repo}")
        private = bool(meta.get("private") is True or meta.get("visibility") in {"private", "internal"})
        if not private and not self.s.dev_log.allow_public:
            log.warning("round issue publication refused: configured repository is public")
            return False
        return True

    async def publish(self, rid: str) -> bool:
        cfg = self.s.dev_log
        if not cfg.repo or root_zone_restricted(self.s.policy):
            return False
        if self.client is None:
            token = os.environ.get(self.s.github.token_env, "")
            if not token and self.transport is None:
                raise RoundPublicationDeferred(f"{self.s.github.token_env} is not set")
            # Same cleaner as project reports: reference paths are masked here too (#130).
            self.client = GitHubClient(token or "test-token", self.s.github.api_url, self.transport,
                                       lambda value: publish_clean(value, self.s, self.hub.requests.values()))
        gh = self.client
        record = json.loads((self.directory / f"{rid}.json").read_text(encoding="utf-8"))
        clean = gh.clean
        record = _clean_tree(record, clean)
        marker = f"<!-- labhq round {rid} -->"
        body = _marked_body(record, limit=MAX_BODY)
        if len(body) > MAX_BODY:
            log.warning("round record exceeds GitHub issue size after report compaction; local record retained")
            return False
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if (self.hub.store.get("round_issue", rid) or {}).get("sha256") == digest:
            return True  # exactly this record is already published
        existing = await gh.find_marked_issue(cfg.repo, marker)  # reads only; may page through many issues
        if existing and existing.get("body") == body:
            number = existing["number"]
        elif not await self._may_post(gh):  # checked after paging, immediately before the one write
            return False
        elif existing:
            await gh.edit_issue(cfg.repo, existing["number"], body)
            number = existing["number"]
        else:
            created = await gh.create_issue(cfg.repo, f"[labhq round] {short(clean(record['request'].get('text') or rid), 70)}",
                                            body, cfg.labels)
            number = created["number"]
        self.hub.store.put("round_issue", rid, {"number": number, "sha256": digest})
        return True
