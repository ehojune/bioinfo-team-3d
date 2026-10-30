"""Shared, offline team-request fixture for repeat/recovery checks (#923)."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import time

REQUEST_TEXT = "Count the three synthetic rows and save the mock artifact [artifact]"
REQUEST_BODY = {"text": REQUEST_TEXT, "mode": "orchestrate", "route": "team",
                "work_kind": "simple", "default_references": False, "budget_usd": 1.0}
ARTIFACT = ("mock artifact" + os.linesep).encode("utf-8")
TIMEOUT_S = 120
REQUIRED_FILES = {"report.md", "report_appendix.md", "execution_record.md", "MANIFEST.tsv",
                  "ro-crate-metadata.json"}
LIMITS = {"request_cap_usd": 1.0, "task_cap_usd": 0.05, "task_timeout_s": TIMEOUT_S,
          "lookup_cap": 0, "reserve_before_dispatch": True}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


RESERVED_PORTS = frozenset({8787, 55921})  # the PI's own gateway and broker: a test never binds them
PORT_DRAWS = 8


def configure(settings, root: Path):
    """Configure real services under an empty per-run directory; no PI ports/state."""
    def port(avoid=frozenset()):
        # The OS picks the port; a reserved or already used draw is closed and drawn again (#1076, as #1072). Only
        # PORT_DRAWS bad draws in a row are returned and refused below.
        for _ in range(PORT_DRAWS):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                drawn = sock.getsockname()[1]
            if drawn not in RESERVED_PORTS and drawn not in avoid:
                break
        return drawn

    settings.gateway.host = "127.0.0.1"
    settings.gateway.port = port()
    settings.gateway.url = f"ws://127.0.0.1:{settings.gateway.port}"
    settings.gateway.state_dir = str(root / "state/gateway")
    settings.runner.state_dir = str(root / "state/runner")
    settings.runner.workspace_root = str(root / "runs")
    settings.runner.talent_dir = str(root / "talent")
    settings.runner.agents_dir = str(Path(__file__).resolve().parents[1] / "agents")
    settings.runner.broker_port = port({settings.gateway.port})
    require(settings.runner.broker_port != settings.gateway.port, "port collision")
    require(not {settings.runner.broker_port, settings.gateway.port} & {8787, 55921}, "reserved port")
    settings.runner.force_engine = "mock"
    settings.runner.mock_delay_s = 0.01
    settings.runner.task_timeout_s = TIMEOUT_S
    settings.hpc.scheduler = "none"
    settings.policy.data_zones = []
    settings.projects = []
    settings.dev_log.enabled = False
    settings.pi_language.enabled = False
    settings.recruit.enabled = False
    settings.github.ask_storage = settings.github.auto_publish_results = False
    settings.policy.budget.per_task_usd = 0.05
    settings.policy.budget.per_request_usd = 1.0
    settings.policy.budget.reserve_before_dispatch = True
    settings.annot.request_lookup_limit = 0
    settings.semantics = {"mode": "off"}
    return settings


class OfflineAudit:
    """Reject non-loopback sockets and ALL subprocess launches during the fake flow."""
    def __init__(self):
        self.external_attempts = 0
        self.subprocess_attempts = 0
        self.events = []

    def __call__(self, event, args):
        if event in {"socket.connect", "socket.sendto"}:
            address = args[1] if event == "socket.connect" else args[-1]
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                self.external_attempts += 1
                self.events.append(event)
                raise OSError("repeat fixture forbids external network")
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork"}:
            self.subprocess_attempts += 1
            self.events.append(event)
            raise OSError("repeat fixture forbids child commands")


async def run_request(base_url: str, token: str, timeout_s: float = TIMEOUT_S) -> dict:
    """Submit via real HTTP and poll the durable terminal/bundle record, with a fixed deadline."""
    import httpx

    require(base_url.startswith("http://127.0.0.1:"), "fixture requires loopback")
    start = time.monotonic()
    async with httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {token}"},
                                 trust_env=False, timeout=min(10, timeout_s)) as client:
        response = await client.post("/api/requests", json=REQUEST_BODY)
        response.raise_for_status()
        rid = response.json()["request_id"]
        while time.monotonic() - start < timeout_s:
            response = await client.get(f"/api/requests/{rid}")
            response.raise_for_status()
            record = response.json()
            if record["status"] in {"done", "failed", "cancelled", "interrupted"} and not record.get("bundle_pending"):
                return {"request": record, "elapsed_s": time.monotonic() - start}
            await asyncio.sleep(0.05)
    raise TimeoutError("fake request exceeded deadline")


def collect_receipt(observed: dict, root: Path, tasks: dict, audit: OfflineAudit) -> dict:
    """Check actual state, outputs and provenance before projecting the comparable contract."""
    req, elapsed = observed["request"], observed["elapsed_s"]
    rid = req["id"]
    require(req["status"] == "done", "request did not finish done")
    require(req["bundle_status"] == "complete", "bundle incomplete")
    require(req["text"] == REQUEST_TEXT, "request input changed")
    require(req.get("cost_known") is True, "unknown cost")
    cost = req["cost_usd"]
    require(type(cost) in (int, float) and math.isfinite(cost) and cost == 0, "mock cost must be known zero")
    require(math.isfinite(elapsed) and 0 < elapsed <= TIMEOUT_S, "elapsed bound")
    require(req["budget_usd"] == req["requested_budget_usd"] == 1.0, "request cap changed")
    require(observed["limits"] == LIMITS, "configured limits changed")
    artifacts = [(step, result) for step, result in req["results"].items() if result.get("outputs")]
    require(len(artifacts) == 1, "expected exactly one artifact-producing step")
    step, result = artifacts[0]
    require(result["ok"] is True and "[mock:data_steward]" in result["text"], "missing mock result marker")
    require(result["cost_known"] is True and result["cost_usd"] == 0, "task cost missing/changed")
    require(result["outputs"] == ["outputs/artifact.txt"], "artifact declaration changed")
    require(result["provenance"]["engine"] == "mock", "non-mock task provenance")
    workdir = Path(result["workdir"]).resolve()
    require(workdir.is_relative_to((root / "runs").resolve()), "workdir escaped run")
    artifact = workdir / "outputs/artifact.txt"
    require(artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_nlink == 1,
            "artifact is missing or reused through a link")
    require(artifact.stat().st_mtime >= req["created_at"], "artifact predates this request")
    require(artifact.read_bytes() == ARTIFACT, "artifact content changed")
    rows = {tid: task for tid, task in tasks.items() if task.get("request_id") == rid}
    require(len(rows) >= len(req["results"]) + 2, "plan/review/task execution missing")
    require(result["task_id"] in rows, "artifact task not recorded")
    require(rows[result["task_id"]]["result"]["workdir"] == result["workdir"], "task/result provenance disconnected")
    for task in rows.values():
        run_result = task["result"]
        require(run_result["ok"] is True and run_result["cost_known"] is True and run_result["cost_usd"] == 0,
                "task success/cost missing")
        require(run_result["provenance"]["engine"] == "mock", "task engine is not mock")
        require(task["dispatched_at"] >= req["created_at"], "previous task reused")
        require(task["payload"]["budget_usd"] == LIMITS["task_cap_usd"], "dispatched task cap changed")
    runs = result["provenance"]["runs"]
    require(bool(runs), "missing run provenance")
    for run in runs.values():
        require(run["started_at"] <= run["ended_at"], "invalid run times")
        require(run["started_at"] >= req["created_at"], "prior run reused")
    bundle = Path(req["bundle_path"]).resolve()
    require(bundle == (root / "runs/requests" / rid).resolve(), "bundle disconnected from request")
    files = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    require(REQUIRED_FILES <= files, "bundle structure incomplete")
    copied = bundle / f"steps/{step}/outputs/artifact.txt"
    require(copied.read_bytes() == ARTIFACT, "bundle artifact missing/changed")
    execution = (bundle / "execution_record.md").read_text(encoding="utf-8")
    report = (bundle / "report.md").read_text(encoding="utf-8")
    require("outputs/artifact.txt" in execution and "[mock:" in report, "evidence/mock marking missing")
    digest = hashlib.sha256(ARTIFACT).hexdigest()
    require(result["output_sha256"]["outputs/artifact.txt"] == digest, "runner artifact hash disconnected")
    with (bundle / "MANIFEST.tsv").open(encoding="utf-8", newline="") as stream:
        manifest = list(csv.DictReader(stream, delimiter="\t"))
    require(any(row["relative_path"] == f"steps/{step}/outputs/artifact.txt"
                and row["sha256"] == digest and row["status"] == "copied" for row in manifest),
            "manifest evidence hash missing")
    require(audit.external_attempts == audit.subprocess_attempts == 0, "offline boundary was attempted")
    return {"schema_version": 1, "request_id": rid, "task_ids": sorted(rows),
            "created_at": req["created_at"], "elapsed_s": elapsed,
            "workdir": workdir.relative_to(root).as_posix(), "bundle": bundle.relative_to(root).as_posix(),
            "artifact_identity": [artifact.stat().st_dev, artifact.stat().st_ino],
            "contract": {"status": req["status"], "cost_usd": cost, "cost_known": req["cost_known"],
                         "task_cost_usd": result["cost_usd"], "task_cost_known": result["cost_known"],
                         "budget_usd": req["budget_usd"], "timeout_s": TIMEOUT_S,
                         "task_count": len(rows), "limits": observed["limits"],
                         "external_connections": audit.external_attempts, "child_commands": audit.subprocess_attempts,
                         "artifact_sha256": digest, "outputs": result["outputs"], "bundle_files": sorted(files),
                         "mock_marked": True, "evidence_linked": True},
            "interpretation": "fake cost/zero external connections observed; provider billing and hard-stop enforcement unverified"}


async def exercise(root: Path, config: Path) -> dict:
    """Run real gateway + runner in one owned process (no CLI/model child allowed)."""
    import sys
    import uvicorn
    from labhq.gateway.server import create_app
    from labhq.adapters.mock import MockAdapter
    from labhq.integrations.rounds import _git_commit
    from labhq.runner.daemon import Runner
    from labhq.settings import Settings

    require(not (root / "state").exists() and not (root / "runs").exists(), "run directory not fresh")
    settings = configure(Settings.load(config), root)
    # Cache OS metadata before the audited request. On Windows platform may run `ver`.
    platform.platform()
    platform.processor()
    _git_commit()
    # MockAdapter has no process-spawn boundary. Enter the real runner's pre-spawn
    # baseline here so its post-run scanner (rather than this checker) records output hashes.
    mock_run = MockAdapter.run

    async def run_with_baseline(adapter, ctx):
        if ctx.before_spawn:
            require(not ctx.before_spawn(), "runner baseline refused")
        return await mock_run(adapter, ctx)

    MockAdapter.run = run_with_baseline
    audit = OfflineAudit()
    sys.addaudithook(audit)
    app = create_app(settings)
    hub = app.state.hub
    require(not hub.requests, "request state not empty")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.gateway.port, log_level="warning"))
    runner = Runner(settings)
    runner.local_software = {}  # fake does not need informational host package/import probes
    server_task = asyncio.create_task(server.serve())
    runner_task = None
    try:
        async def ready(predicate):
            start = time.monotonic()
            while not predicate():
                for task in (server_task, runner_task):
                    if task and task.done():
                        task.result()
                        raise RuntimeError("fixture service exited before ready")
                if time.monotonic() - start > 30:
                    raise TimeoutError("fixture services failed to become ready")
                await asyncio.sleep(0.05)
        await ready(lambda: server.started)
        runner_task = asyncio.create_task(runner.run_forever())
        await ready(lambda: "analyst" in hub.agents)
        require(all(a["engine"] == "mock" for a in hub.agents.values()), "non-mock roster")
        observed = await asyncio.wait_for(run_request(f"http://127.0.0.1:{settings.gateway.port}",
                                                     settings.gateway.client_token), TIMEOUT_S + 1)
        observed["limits"] = {"request_cap_usd": settings.policy.budget.per_request_usd,
                              "task_cap_usd": settings.policy.budget.per_task_usd,
                              "task_timeout_s": settings.runner.task_timeout_s,
                              "lookup_cap": settings.annot.request_lookup_limit,
                              "reserve_before_dispatch": settings.policy.budget.reserve_before_dispatch}
        write_json(root / "request.json", observed["request"])
        write_json(root / "tasks.json", hub.store.all("task"))
        require(len(hub.requests) == 1, "unexpected request count")
        stored = hub.store.all("request")[observed["request"]["id"]]
        require(stored["status"] == "done" and stored["results"] == observed["request"]["results"],
                "durable state differs from API")
        return collect_receipt(observed, root, hub.store.all("task"), audit)
    finally:
        await runner.shutdown()
        runner.stop()
        server.should_exit = True
        pending = [t for t in (server_task, runner_task) if t]
        _, left = await asyncio.wait(pending, timeout=10)
        for task in left:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        write_json(root / "boundary.json", {"external_attempts": audit.external_attempts,
                   "subprocess_attempts": audit.subprocess_attempts, "events": audit.events})
        MockAdapter.run = mock_run
        require(audit.external_attempts == audit.subprocess_attempts == 0,
                "offline boundary was attempted before process exit")
