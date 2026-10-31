"""Credential boundary: real processes/HTTP, fixed broker operation, ownership and cleanup."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from types import SimpleNamespace as NS
import uuid

import httpx
import pytest
import uvicorn
import yaml

from labhq.adapters.base import RunContext
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.runner.annot_broker import AnnotBroker, ENABLED_ENV, refuse_credential_copy
from labhq.runner.approvals import Broker
from labhq.settings import Settings, write_staff_config
from labhq.tools import annot_alphagenome_atlas as atlas


async def noop(*args):
    pass


def context(tmp_path, settings=None):
    settings = settings or Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    return RunContext(task=Task(id="task-atlas", agent_id="worker", request_id="request-atlas", prompt="go"),
        agent=AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=["annot"]),
        workdir=tmp_path, settings=settings, mcp_servers=[], env={}, emit=noop, prompt="go")


@pytest.fixture
def service(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    broker = Broker(0, noop, noop, noop)
    token = broker.issue_task_token(ctx.task.id, ctx.agent.id, ctx.task.request_id, workdir=str(tmp_path))
    service = AnnotBroker(broker, ctx.settings)
    key = uuid.uuid4().hex
    service.register(ctx, key)
    seen = []

    def setup(backend):
        def create(received, **kwargs):
            seen.append(received)
            return NS(scorer_metadata=lambda: {"RNA_SEQ": NS(is_signed=True)},
                      query_variant=lambda *a, **k: {})
        backend._atlas = NS(create=create)
        backend._genome = NS(Variant=lambda **kwargs: kwargs)
        return "fixture-sdk"

    monkeypatch.setattr(atlas.AtlasBackend, "setup", setup)
    return ctx, broker, service, token, key, seen


def client(broker):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=broker.app), base_url="http://broker")


async def test_lookup_receives_key_and_records_without_disclosing_it(service):
    ctx, broker, service, token, key, seen = service
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 200 and response.json()["n_ok"] == 1
    assert seen == [key]
    assert key not in response.text + repr(service.grants[ctx.task.id])
    files = list(ctx.workdir.rglob("*.json*"))
    assert files and all(key not in p.read_text(encoding="utf-8") for p in files)
    assert '"status": "ok"' in (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("body,status", [
    ({"task_id": "other"}, 403), ({"agent_id": "other"}, 403), ({"request_id": "other"}, 403),
    ({"url": "https://example.invalid"}, 400), ({"command": "anything"}, 400),
    ({"workdir": "elsewhere"}, 400), ({"method": "alphagenome_variant"}, 400),
])
async def test_owner_and_allowlist_cannot_be_overridden(service, body, status):
    _, broker, _, token, _, seen = service
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"], **body})
    assert response.status_code == status and not seen


async def test_token_admission_and_finished_task_refuse_before_sdk(service):
    ctx, broker, service, token, _, seen = service
    async with client(broker) as c:
        assert (await c.post("/annot/atlas", json={})).status_code == 401
        broker.lookup_admission = object()
        assert (await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                            json={"variants": ["chr1:100:A:G"]})).status_code == 403
        broker.lookup_admission = None
        await service.finish(ctx.task.id)
        assert (await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                            json={"variants": ["chr1:100:A:G"]})).status_code == 403
        broker.revoke_task_token(token)
        assert (await c.post("/annot/atlas", headers={"X-Labhq-Token": token}, json={})).status_code == 401
    assert not seen


@pytest.mark.parametrize("payload,status", [(b"x" * 16385, 413), (b"[1]", 400), (b"{", 400)])
async def test_bad_payload_is_bounded(service, payload, status):
    _, broker, _, token, _, seen = service
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token}, content=payload)
    assert response.status_code == status and not seen


async def test_finish_cancels_query_and_prevents_late_sdk_work(service, monkeypatch):
    ctx, broker, service, token, _, _ = service
    entered = asyncio.Event()
    backends = []

    async def blocked(annotator, **kwargs):
        backends.append(annotator.atlas_backend)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", blocked)
    async with client(broker) as c:
        pending = asyncio.create_task(c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                            json={"variants": ["chr1:100:A:G"]}))
        await asyncio.wait_for(entered.wait(), 5)
        busy = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                            json={"variants": ["chr1:100:A:G"]})
        assert busy.status_code == 409
        await service.finish(ctx.task.id)
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert backends[0].cancelled.is_set() and not service.grants
    assert '"status": "cancelled"' in (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")


async def test_all_errors_fail_status_without_raw_exception(service, monkeypatch):
    ctx, broker, _, token, key, _ = service
    async def failed(*a, **k):
        return {"n_ok": 0, "results": [{"error": key}]}
    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", failed)
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 502 and key not in response.text
    status = (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")
    assert '"status": "failed"' in status and '"status": "ok"' not in status and key not in status


async def test_deadline_stops_backend_and_records_timeout(service, monkeypatch):
    from labhq.tools.external_wait import run_lookup
    ctx, broker, _, token, _, _ = service
    backends = []
    async def blocked(annotator, **kwargs):
        backends.append(annotator.atlas_backend)
        await asyncio.Event().wait()
    async def short_deadline(*args, **kwargs):
        return await run_lookup(*args, **kwargs, timeout_s=0.05)
    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", blocked)
    monkeypatch.setattr("labhq.runner.annot_broker.run_lookup", short_deadline)
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 502 and backends[0].cancelled.is_set()
    assert '"status": "timeout"' in (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")


async def test_disconnected_mcp_cancels_backend(service, monkeypatch):
    from starlette.requests import Request
    _, broker, service, token, _, _ = service
    entered = asyncio.Event()
    backends = []
    async def blocked(annotator, **kwargs):
        backends.append(annotator.atlas_backend)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", blocked)
    sent = False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b'{"variants":["chr1:100:A:G"]}', "more_body": False}
        await entered.wait()
        return {"type": "http.disconnect"}
    request = Request({"type": "http", "method": "POST", "path": "/annot/atlas", "headers": []}, receive)
    endpoint = next(route.endpoint for route in broker.app.routes if route.path == "/annot/atlas")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(endpoint(request, token), 5)
    assert backends[0].cancelled.is_set()
    assert service.grants["task-atlas"].pending is None


@pytest.mark.parametrize("read_only,builtin", [(True, ["annot"]), (False, [])])
async def test_unavailable_staff_cannot_register(service, read_only, builtin):
    ctx, broker, service, token, key, seen = service
    await service.finish(ctx.task.id)
    ctx.read_only, ctx.agent.builtin_mcp = read_only, builtin
    service.register(ctx, key)
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 403 and not seen


@pytest.mark.parametrize("source", ["parent", "engine", "task", "alias", "case", "empty_override"])
def test_real_child_shell_has_no_key(tmp_path, monkeypatch, source):
    ctx = context(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.delenv(atlas.KEY_ENV, raising=False)
    if source in {"parent", "alias", "empty_override"}:
        monkeypatch.setenv(atlas.KEY_ENV, key)
    if source == "alias":
        monkeypatch.setenv("ATLAS_TEST_ALIAS", key)
    if source in {"engine", "case", "empty_override"}:
        ctx.settings.engines.codex.env[atlas.KEY_ENV.lower() if source == "case" else atlas.KEY_ENV] = (
            "" if source == "empty_override" else key)
    if source == "task":
        ctx.env[atlas.KEY_ENV] = key
    env = CodexAdapter(ctx.settings).staff_env(ctx)
    assert key not in json.dumps(env)
    if os.name == "nt":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   "if (Test-Path Env:ALPHAGENOME_API_KEY) {exit 7}; "
                   "if (Test-Path Env:ATLAS_TEST_ALIAS) {exit 8}; Write-Output 'ATLAS_SHELL_CLEAR'"]
    else:
        command = ["/bin/sh", "-c", 'test -z "${ALPHAGENOME_API_KEY+x}" && '
                   'test -z "${ATLAS_TEST_ALIAS+x}" && echo ATLAS_SHELL_CLEAR']
    result = subprocess.run(command, env=env, capture_output=True, timeout=15, check=True)
    assert key.encode() not in result.stdout + result.stderr
    assert atlas.KEY_ENV.lower().encode() not in result.stdout.lower()


@pytest.mark.parametrize("override", ["set", "mcp", "alias", "prefix"])
async def test_cli_overrides_refused_before_generated_files(tmp_path, monkeypatch, override):
    ctx = context(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.setenv(atlas.KEY_ENV, key)
    monkeypatch.setenv("ATLAS_TEST_ALIAS", key)
    engine = ctx.settings.engines.codex
    if override == "set":
        engine.extra_args = ["-c", f'shell_environment_policy.set.OTHER="{key}"']
    elif override == "mcp":
        engine.extra_args = ["-c", 'mcp_servers.other.env_vars=["ALPHAGENOME_API_KEY"]']
    elif override == "alias":
        engine.extra_args = ["-c", 'shell_environment_policy.set.OTHER="${ATLAS_TEST_ALIAS}"']
    else:
        engine.prefix_args = ["${ATLAS_TEST_ALIAS}"]
    result = await CodexAdapter(ctx.settings).run(ctx)
    assert not result.ok and "Atlas" in result.error and key not in result.error
    assert not list(tmp_path.rglob("*"))


def test_staff_config_refuses_literal_but_preserves_env_reference(tmp_path, monkeypatch):
    key = uuid.uuid4().hex
    config = tmp_path / "source.yaml"
    settings = Settings(config_path=str(config))
    config.write_text(yaml.safe_dump({"engines": {"codex": {"env": {atlas.KEY_ENV: key}}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="environment"):
        refuse_credential_copy(settings, {key})
    assert not (tmp_path / "staff").exists()
    config.write_text(yaml.safe_dump({"engines": {"codex": {"env": {atlas.KEY_ENV: "${ATLAS_TEST_ALIAS}"}}}}),
                      encoding="utf-8")
    refuse_credential_copy(settings, {key})
    name = write_staff_config(settings, tmp_path / "staff")
    assert key not in Path(name).read_text(encoding="utf-8")


async def test_real_mcp_process_calls_runner_without_key(service, monkeypatch):
    ctx, broker, service, token, key, seen = service
    monkeypatch.setenv(atlas.KEY_ENV, key)
    # Hold a bound socket until uvicorn owns it; no free-port probe race.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(broker.app, log_level="error", lifespan="off"))
    server.install_signal_handlers = lambda: None
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    proc = None
    try:
        for _ in range(500):
            if server.started:
                break
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
        assert server.started
        ctx.env = {"LABHQ_BROKER_URL": f"http://127.0.0.1:{port}", "LABHQ_BROKER_TOKEN": token,
                   "LABHQ_TASK_ID": ctx.task.id, "LABHQ_AGENT_ID": ctx.agent.id,
                   "LABHQ_WORKDIR": str(ctx.workdir), ENABLED_ENV: "1"}
        env = CodexAdapter(ctx.settings).staff_env(ctx)
        script = ("import asyncio,os; from labhq.tools import annot_mcp as m; "
                  "assert 'ALPHAGENOME_API_KEY' not in os.environ; "
                  "assert m.ATLAS_ENABLED; print(asyncio.run(m.alphagenome_atlas(['chr1:100:A:G'])))")
        proc = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8", "-c", script, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
        assert proc.returncode == 0, stderr.decode(errors="replace")
        assert json.loads(stdout)["n_ok"] == 1 and seen == [key]
        assert key.encode() not in stdout + stderr
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        await service.finish(ctx.task.id)
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()


@pytest.mark.skipif(os.name != "nt" or os.environ.get("LABHQ_ATLAS_CODEX_SANDBOX_TEST") != "1",
                    reason="explicit local installed-Codex sandbox probe; no model or service calls")
def test_installed_codex_sandbox_shell_has_no_key(tmp_path, monkeypatch):
    executable = os.environ.get("LABHQ_ATLAS_CODEX_BIN") or shutil.which("codex")
    assert executable
    ctx = context(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.setenv(atlas.KEY_ENV, key)
    monkeypatch.setenv("ATLAS_TEST_ALIAS", key)
    ctx.env["CODEX_HOME"] = str(tmp_path / "codex-home")
    Path(ctx.env["CODEX_HOME"]).mkdir()
    env = CodexAdapter(ctx.settings).staff_env(ctx)
    # Isolated HOME and unelevated sandbox: no login, account setup or model turn.
    command = [executable, "sandbox", "-C", str(tmp_path), "-P", ":workspace",
        "-c", 'windows.sandbox="unelevated"', "-c", 'shell_environment_policy.inherit="all"',
        "--", "cmd.exe", "/d", "/c",
        "if defined ALPHAGENOME_API_KEY (exit /b 7) & "
        "if defined ATLAS_TEST_ALIAS (exit /b 8) & echo ATLAS_SHELL_CLEAR"]
    proc = subprocess.Popen(command, env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=30)
        result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    finally:
        if proc.poll() is None:
            from labhq.proc_tree import kill_tree
            kill_tree(proc.pid)
            proc.communicate(timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"ATLAS_SHELL_CLEAR" in result.stdout
    assert key.encode() not in result.stdout + result.stderr
    assert key not in json.dumps(command)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes()


@pytest.mark.parametrize("outcome", ["ok", "error", "cancelled", "cleanup_cancelled"])
async def test_runner_wiring_and_grant_cleanup(service, tmp_path, monkeypatch, outcome):
    from labhq.models import TaskResult
    from tests.test_observed_outputs import _runner, _task
    _, _, _, _, key, seen = service
    monkeypatch.setenv(atlas.KEY_ENV, key)
    runner = _runner(tmp_path / "runner", monkeypatch, lambda _: None, engine=Engine.codex)
    task = _task()
    runner._resolve_agent(task).builtin_mcp = ["annot"]
    contexts = []

    class Staff:
        async def run(self, ctx):
            contexts.append(ctx)
            assert ctx.env[ENABLED_ENV] == "1"
            assert atlas.KEY_ENV not in CodexAdapter(ctx.settings).staff_env(ctx)
            assert key not in json.dumps([s.model_dump() for s in ctx.mcp_servers])
            assert runner.annot_broker.grants[ctx.task.id].key == key
            if outcome == "error":
                raise RuntimeError("fixture failure")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            async with client(runner.broker) as c:
                response = await c.post("/annot/atlas", headers={"X-Labhq-Token": ctx.env["LABHQ_BROKER_TOKEN"]},
                                        json={"variants": ["chr1:100:A:G"]})
            assert response.status_code == 200
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: Staff())
    if outcome == "cleanup_cancelled":
        finish = runner.annot_broker.finish
        async def interrupted_finish(task_id):
            await finish(task_id)
            raise asyncio.CancelledError
        monkeypatch.setattr(runner.annot_broker, "finish", interrupted_finish)
    if outcome == "ok":
        result = await runner.run_task(task)
        assert result.ok and seen == [key]
    else:
        with pytest.raises(RuntimeError if outcome == "error" else asyncio.CancelledError):
            await runner.run_task(task)
    assert contexts and not runner.annot_broker.grants and not runner.broker.identities
    assert not runner.tool_contexts and not runner.active_roots
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes()
