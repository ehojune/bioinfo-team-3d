"""A mock lab (gateway + runner + mock engine) for the semantics shadow tests (#150), and a normalizer that
reduces what the lab recorded to what must not change when the shadow is on."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import uvicorn

from labhq.gateway.server import RequestIn, create_app
from labhq.integrations.rounds import build_record
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, ProjectSettings, Settings
from labhq.util import free_port

REPO = Path(__file__).resolve().parents[1]
TIMESTAMP_KEYS = {"ts", "created_at", "updated_at", "finished_at", "dispatched_at", "started_at", "ended_at",
                  "decided_at", "asked_at", "duration_s", "expires_at", "elapsed_s", "seq", "git_commit"}


def lab_settings(tmp: Path, semantics: Any) -> Settings:
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp / "state")
    port = free_port()
    s.gateway.port, s.gateway.url = port, f"ws://127.0.0.1:{port}"
    s.runner.broker_port, s.runner.force_engine, s.runner.job_poll_s = free_port(), "mock", 0.05
    s.runner.workspace_root = str(tmp / "runs")
    s.runner.talent_dir = str(tmp / "talent")
    s.runner.agents_dir = str(tmp / "agents")
    s.hpc.scheduler = "mock"
    s.policy.data_zones = [DataZone(path=str(tmp / "runs"), level="internal")]
    s.projects = [ProjectSettings(id="shadowproj", name="shadow test project")]
    s.semantics = semantics
    return s


async def _until(pred, timeout: float = 60.0) -> None:
    started = time.time()
    while not pred():
        assert time.time() - started < timeout, "timed out"
        await asyncio.sleep(0.05)


async def run_lab(tmp: Path, semantics: Any, texts: list[str], *, project_id: str | None = None,
                  references: list[dict] | None = None, before=None, request: dict | None = None) -> dict:
    """Run each request to its end; return the hub and the shadow lines written."""
    shutil.copytree(REPO / "agents", tmp / "agents")
    s = lab_settings(tmp, semantics)
    app = create_app(s)
    hub = app.state.hub
    if before:
        before(hub)
    publish = hub.publish

    async def tap(ev, **kwargs):
        await publish(ev, **kwargs)
        if ev.get("type") == "approval.requested":
            aid = ev["data"]["id"]

            async def approve():
                await _until(lambda: aid in hub.approvals, 10)
                await hub.resolve_approval(aid, True, "ok")
            asyncio.get_running_loop().create_task(approve())

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=s.gateway.port, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    await _until(lambda: server.started, 10)
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    rids = []
    try:
        await _until(lambda: "cso" in hub.agents, 15)
        for text in texts:
            rid = hub.create_request(RequestIn(text=text, project_id=project_id, references=references or [],
                                               **(request or {})))
            await _until(lambda: hub.requests[rid]["status"] not in ("running", "waiting_for_runner"), 60)
            rids.append(rid)
        if hub.semantics_shadow is not None:
            assert hub.semantics_shadow.drain(20)
    finally:
        runner.stop()
        server.should_exit = True
        _, pending = await asyncio.wait(tasks, timeout=10)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {"hub": hub, "rids": rids, "settings": s, "lines": shadow_lines(tmp)}


def shadow_lines(tmp: Path) -> list[dict]:
    path = tmp / "state" / "semantics" / "shadow.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _normalize(value: Any, roots: list[str]) -> Any:
    """Random ids, temporary paths, dates and timestamps out; everything else kept."""
    if isinstance(value, dict):
        return {_normalize(k, roots): _normalize(v, roots) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
                if k not in TIMESTAMP_KEYS}
    if isinstance(value, list):
        return [_normalize(v, roots) for v in value]
    if isinstance(value, str):
        for root in roots:
            value = value.replace(root, "<tmp>").replace(root.replace("\\", "/"), "<tmp>")
        value = re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", value)
        value = re.sub(r"mock-session-[0-9a-f]{4}", "<session>", value)
        return re.sub(r"\b(req|task|appr|ask|fu|job)_[0-9a-f]{10}", r"<\1>", value)
    return value


def lab_record(result: dict, tmp: Path) -> dict:
    """What the lab recorded for its requests: prompts, schemas, plans, approvals, results, rounds, web."""
    hub = result["hub"]
    roots = sorted({str(tmp), str(tmp.resolve())}, key=len, reverse=True)
    out = []
    for rid in result["rids"]:
        req = hub.requests[rid]
        tasks = sorted((t for t in hub.store.all("task").values() if t.get("request_id") == rid),
                       key=lambda t: (str(t.get("kind")), str(t.get("step_id")), t.get("attempt") or 0,
                                      t.get("revision") or 0, t.get("dispatched_at") or 0))
        prompts = [{"agent": (t.get("payload") or {}).get("agent_id"), "kind": t.get("kind"),
                    "step": t.get("step_id"), "attempt": t.get("attempt"), "revision": t.get("revision"),
                    "prompt": (t.get("payload") or {}).get("prompt"),
                    "schema": (t.get("payload") or {}).get("output_schema"),
                    "resume": bool((t.get("payload") or {}).get("resume_session_id"))} for t in tasks]
        approvals = sorted(json.dumps(_normalize({**{k: (d.get("approval") or {}).get(k) for k in
                                                     ("kind", "summary", "detail")}, "approved": d.get("approved")},
                                                 roots), sort_keys=True, default=str)
                           for d in hub.store.all("approval_decision").values()
                           if (d.get("approval") or {}).get("request_id") == rid)
        events = sorted(json.dumps(_normalize({"type": e.get("type"), "data": e.get("data")}, roots),
                                   sort_keys=True, default=str)
                        for e in hub.store.events_since(0) if e.get("request_id") == rid)
        snapshot = next(r for r in hub.snapshot()["data"]["requests"] if r["id"] == rid)
        out.append({
            "status": req.get("status"), "plan": req.get("plan"), "results": req.get("results"),
            "review": req.get("review"), "report": req.get("report"), "outcome": req.get("outcome"),
            "research_contract": req.get("research_contract"), "prompts": prompts,
            "approvals": approvals, "events": events,
            "round": build_record(hub, rid), "web": snapshot,
        })
    return _normalize(out, roots)


# ---------------------------------------------------------------- synthetic rows for unit tests

class FakeStore:
    def __init__(self, tasks: dict | None = None, decisions: dict | None = None):
        self.rows = {"task": tasks or {}, "approval_decision": decisions or {}}

    def all(self, kind: str) -> dict:
        return json.loads(json.dumps(self.rows.get(kind, {})))


def fake_hub(tmp: Path, requests: dict, tasks: dict, *, zones: list | None = None, visibility: str = "private",
             project: str = "p1", decisions: dict | None = None, approvals: dict | None = None,
             jobs_done: dict | None = None, semantics: Any = "shadow"):
    from types import SimpleNamespace
    s = Settings()
    s.gateway.state_dir = str(tmp / "state")
    s.runner.workspace_root = str(tmp / "runs")
    s.policy.data_zones = ([DataZone(path=str(tmp / "runs"), level="internal")] if zones is None else zones)
    s.projects = [ProjectSettings(id=project, visibility=visibility)]
    s.semantics = semantics
    agents = {a: {"id": a, "engine": "mock", "employment": "core"} for a in ("cso", "analyst", "biologist")}
    return SimpleNamespace(s=s, requests=requests, store=FakeStore(tasks, decisions), agents=agents,
                           approvals=approvals or {}, jobs_done=jobs_done or {})


def workspace(tmp: Path, tid: str, agent: str, files: dict[str, bytes], *, host: str | None = None) -> tuple[str, str]:
    import platform
    ws = tmp / "runs" / "2026-10-01" / f"{tid}_{agent}"
    (ws / "outputs").mkdir(parents=True, exist_ok=True)
    (ws / "manifest.json").write_text(json.dumps({"host": host or platform.node(), "agent_id": agent,
                                                  "agent_spec_sha256": "a" * 64}), encoding="utf-8")
    for rel, data in files.items():
        target = ws.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return str(ws), ws.name


def task_row(rid: str, tid: str, step: str | None, agent: str, workdir: str | None, outputs: list[str], *,
             ok: bool = True, output_types: dict | None = None, kind: str = "step", attempt: int = 1) -> dict:
    meta = {"step_id": step, "kind": kind, "outputs": outputs}
    if output_types is not None:
        meta["output_types"] = output_types
    return {"request_id": rid, "step_id": step, "kind": kind, "attempt": attempt, "revision": 0, "parent_task": None,
            "accepted": True, "completed": True, "runner_id": "local",
            "payload": {"id": tid, "agent_id": agent, "prompt": "secret prompt text for " + tid, "meta": meta},
            "result": {"task_id": tid, "agent_id": agent, "ok": ok, "text": "answer body " + tid,
                       "session_id": f"sess-{tid}", "workdir": workdir,
                       "workdir_id": Path(workdir).name if workdir else None, "outputs": outputs,
                       "missing_outputs": [], "pending_jobs": [], "pending_asks": [],
                       "provenance": {"runs": {tid: {"started_at": 1.0, "ended_at": 2.0}}}}}


def request_row(rid: str, steps: list[tuple[str, str]], *, project: str | None = "p1", created_at: float = 1.0,
                status: str = "done", text: str = "request text") -> dict:
    return {"id": rid, "status": status, "mode": "orchestrate", "project_id": project, "created_at": created_at,
            "text": text, "references": [],
            "plan": {"steps": [{"id": sid, "agent_id": agent, "instruction": f"do {sid}", "depends_on": [],
                                "outputs": []} for sid, agent in steps]},
            "results": {}}


def line_for(hub, rid: str, observed: dict | None = None, *, history: int = 200) -> dict:
    from labhq.research.semantics_shadow import ShadowConfig, compute_line, take_snapshot
    snap = take_snapshot(hub, rid, ShadowConfig(history_requests=history))
    return compute_line(snap, {} if observed is None else observed, lambda: None, epoch=1)
