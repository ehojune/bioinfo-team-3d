#!/usr/bin/env python3
"""Kill owned gateway/runner processes during a disposable fake request (#924)."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import uuid


def write_json(path, value):
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    staging.replace(path)


def source_checkout(source):
    source = source.resolve()
    if source != Path(__file__).resolve().parents[1]:
        raise ValueError("--source must match the checkout containing this script")
    # A script launched by filename has scripts/, rather than the checkout, on sys.path.
    sys.path.insert(0, str(source))
    import labhq
    if Path(labhq.__file__).resolve() != source / "labhq/__init__.py":
        raise ValueError("imported labhq must come from the selected checkout")
    fixture = source / "scripts/repeat_fake_fixture.py"
    spec = importlib.util.find_spec("scripts.repeat_fake_fixture")
    if (fixture.exists() != (spec is not None)
            or (spec is not None and Path(spec.origin).resolve() != fixture)):
        raise ValueError("repeat fixture must come from the selected checkout")
    return source


def rows(path, kind=None):
    if not path.exists():
        return {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        table = "events" if kind is None else "state"
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return [] if kind is None else {}
        if kind is None:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM events ORDER BY seq")]
        return {key: json.loads(raw) for key, raw in db.execute(
            "SELECT key, body FROM state WHERE kind=?", (kind,))}


def free_port():
    # Avoid Windows' ephemeral/excluded ranges and the outbound sockets opened during startup.
    for _ in range(100):
        port = 15000 + uuid.uuid4().int % 15000
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        if port not in {8787, 8788, 55921}:
            return port
    raise RuntimeError("no disposable loopback port available")


def configure(root, source):
    from labhq.settings import Settings
    s = Settings()
    if importlib.util.find_spec("scripts.repeat_fake_fixture"):
        from scripts.repeat_fake_fixture import configure as repeat_configure
        s = repeat_configure(s, root)
    s.gateway.port = free_port()
    s.gateway.url = f"ws://127.0.0.1:{s.gateway.port}"
    s.gateway.state_dir = str(root / "gateway")
    s.gateway.client_token = uuid.uuid4().hex
    s.gateway.runner_token = uuid.uuid4().hex
    s.gateway.resume_wait_s = 15
    s.runner.broker_port = free_port()
    while s.runner.broker_port == s.gateway.port:
        s.runner.broker_port = free_port()
    s.runner.state_dir = str(root / "runner")
    s.runner.workspace_root = str(root / "runs")
    s.runner.agents_dir = str(root / "agents")
    s.runner.talent_dir = str(root / "talent")
    s.runner.force_engine = "mock"
    s.runner.mock_delay_s = 0
    s.runner.disk_fail_free_gb = 0
    s.runner.task_timeout_s = 30
    s.hpc.scheduler = "none"
    s.recruit.enabled = False
    s.research.enabled = False
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.precedent_agent = None
    s.orchestrator.reviewer_agent = None
    s.orchestrator.max_parallel_steps = 1
    s.orchestrator.max_failure_replans = 0
    s.policy.approvals.pi_decision_timeout_s = 30
    s.policy.budget.per_task_usd = 0.1
    s.policy.budget.per_request_usd = 1
    s.policy.budget.reserve_before_dispatch = True
    s.annot.request_lookup_limit = 9
    s.dev_log.enabled = False
    s.pi_profile.references = []
    s.pi_language.enabled = False
    core = root / "agents/core"
    core.mkdir(parents=True)
    for aid in ("cso", "biologist", "analyst"):
        write_json(core / f"{aid}.yaml", {"id": aid, "name": aid, "role": "fake recovery fixture",
                   "engine": "mock", "builtin_mcp": []})
    return s


async def child(root, role, nonce):
    import httpx
    import uvicorn
    from labhq.settings import Settings
    s = Settings.model_validate_json((root / "settings.json").read_text(encoding="utf-8"))
    if s.runner.force_engine != "mock" or s.hpc.scheduler != "none":
        raise ValueError("recovery children require mock and scheduler=none")
    # Installed only inside owned children: every attempted command is refused before creation.
    boundary = {"external_attempts": 0, "blocked_commands": 0}
    def offline(event, args):
        if event in {"socket.connect", "socket.sendto"}:
            address = args[1] if event == "socket.connect" else args[-1]
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                boundary["external_attempts"] += 1
                write_json(root / f"boundary-{role}-{nonce}.json", boundary)
                raise OSError("recovery fixture forbids external network")
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork"}:
            boundary["blocked_commands"] += 1
            write_json(root / f"boundary-{role}-{nonce}.json", boundary)
            raise OSError("recovery fixture forbids child commands")
    write_json(root / f"boundary-{role}-{nonce}.json", boundary)
    sys.addaudithook(offline)
    write_json(root / f"{role}-{nonce}.json", {"pid": os.getpid(), "nonce": nonce, "role": role})
    if role == "gateway":
        from labhq.gateway.server import create_app
        app = create_app(s)

        @app.get("/fake-public")
        async def public():
            return {"fixture": True}

        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=s.gateway.port,
                                           log_level="warning")).serve()
        return
    from labhq.adapters.mock import MockAdapter
    from labhq.models import TaskResult
    from labhq.runner import daemon

    class RecoveryMock(MockAdapter):
        async def run(self, ctx):
            if ctx.before_spawn:
                refused = ctx.before_spawn()  # MockAdapter has no real spawn boundary for the runner's file baseline.
                if refused:
                    raise RuntimeError(refused)
            task = ctx.task
            kind = task.meta.get("kind", "step")
            sid = task.meta.get("step_id")
            record = {"task_id": task.id, "kind": kind, "step_id": sid, "pid": os.getpid()}
            with (root / "invocations.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
            if kind == "plan":
                plan = {"scope": {"verdict": "in", "reason": "local fake"},
                        "clarifying_questions": [], "recruit": [], "notes": "recovery fixture",
                        "steps": [{"id": "s1", "agent_id": "biologist", "depends_on": [],
                                   "instruction": "first fake step", "outputs": []},
                                  {"id": "s2", "agent_id": "analyst", "depends_on": ["s1"],
                                   "instruction": "second fake step", "outputs": ["artifact.txt"]}]}
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                                  text="fake plan", structured=plan, cost_usd=0.01)
            if kind == "step":
                identity = {"task_id": task.id, "agent_id": task.agent_id}
                admitted = await self._broker(ctx, "/lookup/admit", {**identity, "tool": "recovery_fake", "host": "localhost"})
                if not admitted.get("admitted"):
                    raise RuntimeError("fake lookup refused")
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{s.gateway.port}/fake-public")
                    response.raise_for_status()
                await self._broker(ctx, "/lookup/finish", {**identity, "sends": [[admitted["seq"], 200]], "outcome": "ok"})
                if sid == "s2" and not (root / "release").exists():
                    write_json(root / "barrier.json", record)
                    while not (root / "release").exists():
                        await asyncio.sleep(0.05)
                if sid == "s2":
                    (ctx.workdir / "outputs/artifact.txt").write_text("fake recovery complete\n", encoding="utf-8")
            result = await super().run(ctx)
            return result.model_copy(update={"cost_usd": 0.01, "cost_known": True})

    daemon.get_adapter = lambda engine, settings: RecoveryMock(settings)
    runner = daemon.Runner(s)
    runner.engine_versions = {"mock": "fixture"}
    runner.local_software = {}
    await runner.run_forever()


class OwnedProcesses:
    def __init__(self, root, source):
        self.root, self.source = root, source
        self.items = []
        self.files = []
        self.errors = []

    def start(self, role):
        nonce = uuid.uuid4().hex
        # A minimal environment supplies no operator credentials, overrides, or proxy.
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "USERNAME", "LABHQ_TEST_REAL_HOME"}}
        home = self.root / "home"
        temp = self.root / "tmp"
        home.mkdir(exist_ok=True)
        temp.mkdir(exist_ok=True)
        env.update({name: str(home) for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                                                "XDG_CONFIG_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")})
        env.update({name: str(temp) for name in ("TEMP", "TMP", "TMPDIR")})
        package_dirs = [path for path in sys.path if "site-packages" in Path(path).parts]
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join([str(self.source), *package_dirs]))
        logs = [open(self.root / f"{role}-{nonce}.{suffix}.log", "wb") for suffix in ("stdout", "stderr")]
        self.files.extend(logs)
        # Windows venv python.exe is a redirector that starts another PID. Hold the real interpreter handle.
        executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
        process = subprocess.Popen([executable, str(self.source / "scripts/recovery_fake_check.py"),
                                    "--child", role, "--output", str(self.root), "--nonce", nonce],
                                   cwd=self.source, env=env, stdin=subprocess.DEVNULL,
                                   stdout=logs[0], stderr=logs[1],
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        item = {"process": process, "pid": process.pid, "nonce": nonce, "role": role}
        self.items.append(item)
        return item

    def kill(self, item):
        process = item["process"]
        identity = json.loads((self.root / f"{item['role']}-{item['nonce']}.json").read_text(encoding="utf-8"))
        if identity != {key: item[key] for key in ("pid", "nonce", "role")} or process.poll() is not None:
            raise RuntimeError("owned process identity/liveness mismatch")
        process.kill()  # held Popen process handle, never a discovered PID or process-name match
        process.wait(timeout=10)
        return {**identity, "method": "TerminateProcess" if os.name == "nt" else "SIGKILL",
                "returncode": process.returncode, "exit_observed": process.poll() is not None}

    def check(self):
        for item in self.items:
            if item.get("killed"):
                continue
            if item["process"].poll() is not None:
                raise RuntimeError(f"{item['role']} exited unexpectedly ({item['process'].returncode}); see stderr log")

    def close(self):
        for item in self.items:
            process = item["process"]
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.errors.append({"pid": item["pid"], "error": type(exc).__name__})
        for stream in self.files:
            stream.close()
        return [{key: item[key] for key in ("pid", "nonce", "role")} for item in self.items
                if item["process"].poll() is None]


async def wait_for(check, deadline):
    while time.time() < deadline:
        result = check()
        if result:
            return result
        await asyncio.sleep(0.05)
    raise TimeoutError("fixed recovery fixture deadline reached")


def validate(receipt):
    errors = []
    if receipt["before_contract"] != receipt["after_contract"]:
        errors.append("contract changed")
    if receipt["finished_at"] >= receipt["before_contract"]["deadline_at"]:
        errors.append("deadline exceeded")
    if receipt["resume_approvals"] != 1 or receipt["pending_approvals"]:
        errors.append("resume card count")
    counts = Counter(row["task_id"] for row in receipt["invocations"])
    if any(count != 1 for count in counts.values()):
        errors.append("same task executed twice")
    accepted = Counter(event["task_id"] for event in receipt["events"] if event.get("type") == "task.accepted")
    if (accepted != counts or any(count != 1 for count in accepted.values())
            or set(receipt["accepted_tasks"]) != set(counts)
            or any(entry.get("state") != "finished" for entry in receipt["accepted_tasks"].values())):
        errors.append("task acceptance missing or duplicated")
    if sum(row["kind"] == "step" and row["step_id"] == "s1" for row in receipt["invocations"]) != 1:
        errors.append("completed step repeated")
    completed = [row for row in receipt["before_invocations"] if row["task_id"] in receipt["before_completed_tasks"]]
    if (len(completed) != 2 or {(row["kind"], row["step_id"]) for row in completed}
            != {("plan", None), ("step", "s1")}):
        errors.append("completed phase baseline missing")
    for prior in completed:
        phase = [row for row in receipt["invocations"]
                 if (row["kind"], row["step_id"]) == (prior["kind"], prior["step_id"])]
        if phase != [prior]:
            errors.append("completed phase repeated or replaced")
    if receipt["status"] != "done" or set(receipt["results"]) != {"s1", "s2"}:
        errors.append("request did not finish")
    first = receipt["before"].get("results", {}).get("s1", {})
    saved = receipt["after"].get("results", {}).get("s1", {})
    if not first or any(saved.get(key) != value for key, value in first.items()):
        errors.append("completed result changed")
    ledger = receipt["after"].get("cost_by_task", {})
    numeric = all(type(cost) in (int, float) and math.isfinite(cost) and cost >= 0 for cost in ledger.values())
    if (set(ledger) != set(counts) or receipt.get("cost_known") is not True
            or receipt["after"].get("cost_known") is not True
            or not numeric):
        errors.append("fixture cost ledger missing or unknown")
    if (abs(receipt["cost_usd"] - receipt["ledger_cost_usd"]) > 1e-8
            or abs(receipt["cost_usd"] - receipt["after"]["cost_usd"]) > 1e-8
            or (numeric and abs(receipt["cost_usd"] - sum(ledger.values())) > 1e-8)):
        errors.append("cost counted twice or missing")
    if any(task_id not in ledger or ledger[task_id] != cost
           for task_id, cost in receipt["before"].get("cost_by_task", {}).items()):
        errors.append("prior cost history changed")
    if (receipt["cost_usd"] > receipt["before_contract"]["budget_usd"]
            or any(cost is not None and cost > receipt["before_contract"]["per_task_usd"]
                   for cost in ledger.values())):
        errors.append("cost cap exceeded")
    if receipt["lookup_count"] != sum(row["kind"] == "step" for row in receipt["invocations"]):
        errors.append("lookup counted twice or missing")
    if receipt["lookup_count"] > receipt["before_contract"]["lookup_limit"]:
        errors.append("lookup cap exceeded")
    if any(value["external_attempts"] for value in receipt["boundaries"].values()):
        errors.append("external network attempted")
    if not receipt["kill"]["exit_observed"] or receipt["kill"]["pid"] == receipt["restart"]["pid"]:
        errors.append("kill/restart not observed")
    if (len(receipt["artifact_sha256"]) != 1 or receipt["artifact_sha256"] != receipt["runner_artifact_sha256"]
            or receipt["remaining_processes"] or receipt.get("cleanup_errors")):
        errors.append("artifact/cleanup missing")
    return errors


POST_TIMEOUT_S = 10  # read limit of the two POSTs; the client's 2 s stays for the health polling (#1076)


async def timed_post(client, path, label=None, **kwargs):
    """POST with POST_TIMEOUT_S, naming the call and its elapsed time when it times out.

    A restarted gateway can be busy for a while after it answers its first health check (about 0.5 s on a fast
    machine); a bare ReadTimeout said only that one of two POSTs timed out on Windows CI (3 runs)."""
    import httpx
    started = time.monotonic()
    try:
        return await client.post(path, timeout=POST_TIMEOUT_S, **kwargs)
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"POST {label or path} timed out after {time.monotonic() - started:.1f}s "
                           f"({type(exc).__name__}, limit {POST_TIMEOUT_S}s)") from exc


async def run_case(source, root, target, timeout_s=90):
    source = source_checkout(source)
    import httpx
    from labhq.runner.lookup_admission import LookupAdmission, FILE
    root.mkdir(parents=True, exist_ok=False)
    settings = configure(root, source)
    write_json(root / "settings.json", settings.model_dump(mode="json"))
    deadline = time.time() + timeout_s
    contract = {"deadline_at": deadline, "deadline_owner": "disposable_test_driver",
                "task_timeout_s": settings.runner.task_timeout_s, "request_budget_usd": 1,
                "per_task_usd": settings.policy.budget.per_task_usd, "lookup_limit": settings.annot.request_lookup_limit,
                "reserve_before_dispatch": settings.policy.budget.reserve_before_dispatch}
    write_json(root / "contract.json", contract)
    owned = OwnedProcesses(root, source)
    async def wait(check):
        def monitored():
            owned.check()
            return check()
        return await wait_for(monitored, deadline)
    receipt = {"case": target, "passed": False, "real_model_used": False, "hpc_used": False,
               "source_commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                                        text=True).strip(),
               "source_dirty": bool(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"])),
               "fixture_sha256": hashlib.sha256((source / "scripts/recovery_fake_check.py").read_bytes()).hexdigest(),
               "python_version": sys.version.split()[0],
               "deadline_scope": "fixture driver only; no product request-wide timer is asserted"}
    gateway_db = root / "gateway/gateway.sqlite3"
    try:
        processes = {role: owned.start(role) for role in ("gateway", "runner")}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{settings.gateway.port}", trust_env=False,
                                     headers={"Authorization": f"Bearer {settings.gateway.client_token}"}, timeout=2) as client:
            await wait(lambda: len(rows(gateway_db, "runner_roster")) == 1)
            if importlib.util.find_spec("scripts.repeat_fake_fixture"):
                from scripts.repeat_fake_fixture import REQUEST_BODY
                receipt["repeat_fixture_integrated"] = True
            else:
                REQUEST_BODY = {"text": "Count the three synthetic rows and save the mock artifact [artifact]",
                                "mode": "orchestrate", "route": "team", "work_kind": "simple",
                                "default_references": False, "budget_usd": 1.0}
                receipt["repeat_fixture_integrated"] = False
            receipt["request_body"] = REQUEST_BODY
            response = await timed_post(client, "/api/requests", json=REQUEST_BODY)
            response.raise_for_status()
            rid = response.json()["request_id"]
            await wait(lambda: (root / "barrier.json").exists())
            before = rows(gateway_db, "request")[rid]
            receipt["before"] = before
            receipt["before_invocations"] = [json.loads(line) for line in
                                             (root / "invocations.jsonl").read_text(encoding="utf-8").splitlines()]
            receipt["before_completed_tasks"] = {tid: entry for tid, entry in rows(gateway_db, "task").items()
                                                 if entry.get("completed") and (entry.get("result") or {}).get("ok")}
            receipt["before_contract"] = {**contract, "created_at": before["created_at"],
                                          "budget_usd": before["budget_usd"]}
            receipt["kill"] = owned.kill(processes[target])
            processes[target]["killed"] = True
            replacement = owned.start(target)
            await wait(lambda: (root / f"{target}-{replacement['nonce']}.json").exists())
            receipt["restart"] = json.loads((root / f"{target}-{replacement['nonce']}.json").read_text(encoding="utf-8"))
            cards = await wait(lambda: [entry["approval"] for entry in rows(gateway_db, "approval").values()
                                       if entry["approval"]["kind"] == "resume"])
            if len(cards) != 1:
                raise RuntimeError("expected one resume card")
            while time.time() < deadline:
                owned.check()
                try:
                    response = await client.get("/api/health")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise TimeoutError("restarted gateway did not become ready")
            # Interrupted work may retry with a new task id; a completed step may never run again.
            (root / "release").touch()
            response = await timed_post(client, f"/api/approvals/{cards[0]['id']}", "/api/approvals/<id>",
                                        json={"approved": True})
            response.raise_for_status()
            def finished():
                req = rows(gateway_db, "request").get(rid, {})
                if any(entry["approval"]["kind"] == "resume"
                       for entry in rows(gateway_db, "approval").values()):
                    raise RuntimeError("second resume card after the single approved recovery")
                return req if req.get("status") in {"done", "failed", "cancelled"} and not req.get("bundle_pending") else None
            final = await wait(finished)
            receipt["after"] = final
            receipt["after_contract"] = {**json.loads((root / "contract.json").read_text(encoding="utf-8")),
                                         "created_at": final["created_at"], "budget_usd": final["budget_usd"]}
            receipt["finished_at"] = time.time()
            receipt["status"], receipt["results"] = final["status"], final.get("results", {})
            receipt["invocations"] = [json.loads(line) for line in (root / "invocations.jsonl").read_text(encoding="utf-8").splitlines()]
            decisions = rows(gateway_db, "approval_decision")
            receipt["resume_approvals"] = sum(row["approval"]["kind"] == "resume" and row.get("approved") is True
                                              for row in decisions.values())
            receipt["pending_approvals"] = len(rows(gateway_db, "approval"))
            receipt["cost_usd"] = final.get("cost_usd", 0)
            receipt["ledger_cost_usd"] = sum(float(value or 0) for value in final.get("cost_by_task", {}).values())
            receipt["cost_known"] = final.get("cost_known")
            receipt["lookups"] = LookupAdmission(root / "runner" / FILE, contract["lookup_limit"]).sends(rid)
            receipt["lookup_count"] = len(receipt["lookups"])
            outputs = list((root / "runs").rglob("outputs/artifact.txt"))
            outputs = [path for path in outputs if "/requests/" not in path.as_posix()]
            receipt["artifact_sha256"] = [hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs]
            receipt["runner_artifact_sha256"] = [final["results"]["s2"].get("output_sha256", {}).get("outputs/artifact.txt")]
            receipt["events"] = rows(gateway_db)
    except Exception as exc:
        receipt["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        receipt["remaining_processes"] = owned.close()
        receipt["cleanup_errors"] = owned.errors
        # Preserve failure evidence too; never replace the product result with a happy-path summary.
        receipt["events"] = rows(gateway_db)
        receipt["stored_requests"] = rows(gateway_db, "request")
        receipt["stored_approvals"] = rows(gateway_db, "approval")
        receipt["stored_decisions"] = rows(gateway_db, "approval_decision")
        receipt["accepted_tasks"] = rows(root / "runner/runner-local.sqlite3", "accepted_task")
        receipt["boundaries"] = {path.name: json.loads(path.read_text(encoding="utf-8"))
                                 for path in root.glob("boundary-*.json")}
        invocation_file = root / "invocations.jsonl"
        receipt["invocations"] = [json.loads(line) for line in invocation_file.read_text(encoding="utf-8").splitlines()] if invocation_file.exists() else []
    if "failure" not in receipt:
        receipt["errors"] = validate(receipt)
        receipt["passed"] = not receipt["errors"]
    write_json(root / "receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("gateway", "runner", "all"), default="all")
    parser.add_argument("--child", choices=("gateway", "runner"), help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        asyncio.run(child(args.output.resolve(), args.child, args.nonce))
        return 0
    try:
        source = source_checkout(args.source)
    except ValueError as exc:
        parser.error(str(exc))
    output = args.output.resolve()
    if output.exists():
        parser.error("--output must be a new disposable folder")
    output.mkdir(parents=True)
    results = []
    for case in (("gateway", "runner") if args.case == "all" else (args.case,)):
        receipt = asyncio.run(run_case(source, output / case, case))
        results.append({"case": case, "passed": receipt["passed"], "failure": receipt.get("failure"),
                        "errors": receipt.get("errors", [])})
        print(json.dumps(results[-1]), flush=True)
    write_json(output / "summary.json", results)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
