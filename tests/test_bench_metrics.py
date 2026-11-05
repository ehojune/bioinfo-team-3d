"""Synthetic ledgers and fake spawn boundaries only: no benchmark, process, port, thread or research run."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from labhq import bench, bench_metrics as bm
from labhq.adapters import base
from labhq.models import AgentSpec, Task
from labhq.settings import Settings
from tests.test_ci807_research_regression import mock_native_boundaries  # noqa: F401
from tests.test_paper_skill_attach import deterministic_paper_io  # noqa: F401
from tests.test_bench_review3 import harness  # noqa: F401


def task(kind="step", cost=1.5, **updates):
    return {"request_id": "r", "kind": kind, "attempt": 1, "parent_task": None, "dispatched_at": 10,
            "payload": {"prompt": "SECRET /private/input", "context": "한글"},
            "result": {"cost_known": cost is not None, "cost_usd": cost}, **updates}


def spawn(recorder, tid="t", sid="spawn", state="started", rid="r", **counts):
    args = dict(task_id=tid, request_id=rid, spawn_id=sid, prompt_bytes=3, argv_bytes=7, stdin_bytes=2, **counts)
    recorder.spawn(**args, state="attempt")
    recorder.spawn(**args, state=state)
    recorder.spawn(**args, state=state)  # replay


@pytest.mark.parametrize("attempt", [None, True])
def test_missing_observations_are_not_zero_or_dispatch_proxies(attempt):
    receipt = bm.collect(bm.Recorder("labhq", "r"), tasks={"t": task(payload={}, attempt=attempt)})
    assert receipt["recorded_tasks"]["value"] == 1
    for key in ("retry_tasks", "task_prompt_utf8_bytes_proxy", "handoff_context_utf8_bytes_proxy"):
        assert receipt[key]["status"] == "not_collected" and receipt[key]["value"] is None
    assert receipt["cli"]["spawn_successes"]["status"] == "not_collected"
    assert receipt["stages"]["analysis"]["elapsed_task_s"]["value"] is None
    assert receipt["approval_wait_s"]["value"] is None


def test_deduplication_unknown_cost_and_request_isolation():
    rec = bm.Recorder("labhq", "r")
    spawn(rec)
    spawn(rec, tid="foreign", rid="private-request")
    for ts in (20, 25, 20):
        rec.event({"type": "task.result", "task_id": "t", "request_id": "r", "ts": ts})
    rows = {"t": task(), "u": task("consult", None, attempt=2, parent_task="t",
                                   result={"cost_known": False, "cost_usd": 99}),
            "private": task(request_id="private-request", cost=999)}
    partial = bm.collect(rec, tasks=rows)
    assert partial["cli"]["spawn_successes"] == {"status": "not_collected", "value": None,
        "reason": "incomplete_spawn_coverage", "observed": 1}
    spawn(rec, tid="u", state="failed")
    receipt = bm.collect(rec, tasks=rows, approvals={})
    assert receipt["cli"]["spawn_successes"]["value"] == 1
    assert receipt["cli"]["spawn_failures"]["value"] == 1
    assert receipt["cli"]["argv_utf8_bytes"]["value"] == 7
    assert receipt["recorded_tasks"]["value"] == 2
    assert receipt["retry_tasks"]["value"] == receipt["continuation_turns"]["value"] == 1
    assert receipt["stages"]["analysis"]["elapsed_task_s"]["value"] == 10
    assert receipt["stages"]["analysis"]["cost_usd"]["value"] == 1.5
    assert receipt["stages"]["consult"]["unknown_cost_tasks"] == 1
    assert receipt["stages"]["consult"]["known_cost_usd"] == 0
    assert receipt["stages"]["consult"]["cost_usd"]["value"] is None
    assert receipt["handoff_context_utf8_bytes_proxy"]["value"] == len("한글".encode()) * 2
    encoded = json.dumps(receipt)
    assert "SECRET" not in encoded and "/private" not in encoded and "private-request" not in encoded
    assert receipt == bm.collect(rec, tasks=copy.deepcopy(rows), approvals={})
    assert receipt == bm.collect(rec, tasks=dict(reversed(list(rows.items()))), approvals={})


def test_incomplete_spawn_and_independent_arm_state():
    a, b = bm.Recorder("labhq", "r"), bm.Recorder("labhq", "r")
    a.spawn(task_id="t", request_id="r", spawn_id="x", state="attempt", argv_bytes=8)
    assert bm.collect(a, tasks={"t": task()})["cli"]["spawn_successes"]["value"] is None
    assert not b.spawns and not b.ends
    spawn(b)
    assert bm.collect(b, tasks={"t": task()})["cli"]["spawn_successes"]["value"] == 1
    spawn(b, sid="second-real-launch")
    assert bm.collect(b, tasks={"t": task()})["cli"]["spawn_successes"]["value"] == 2
    assert bm.collect(b, tasks={"t": task()})["recorded_tasks"]["value"] == 1
    no_ledger = bm.collect(b)["cli"]["spawn_successes"]
    assert no_ledger["value"] is None and no_ledger["observed"] == 2


def test_partial_clocks_preserve_only_observed_lower_bounds():
    rec = bm.Recorder("labhq", "r")
    rec.event({"type": "task.result", "request_id": "r", "task_id": "t", "ts": 20})
    cards = {"done": {"approval": {"id": "a", "request_id": "r", "created_at": 10}, "decided_at": 12},
             "pending": {"approval": {"id": "b", "request_id": "r", "created_at": 11}}}
    receipt = bm.collect(rec, tasks={"t": task(), "u": task()}, approvals=cards)
    duration = receipt["stages"]["analysis"]["elapsed_task_s"]
    assert duration["value"] is None and duration["observed"] == 10
    assert receipt["approval_wait_s"]["value"] is None and receipt["approval_wait_s"]["observed"] == 2


def test_result_storage_editor_is_report_work():
    # Existing result_storage.finish emits this task kind, independently of CSO synthesis.
    receipt = bm.collect(bm.Recorder("labhq", "r"), tasks={"t": task("result_editor")})
    assert receipt["stages"]["report"]["recorded_tasks"]["value"] == 1
    assert receipt["stages"]["analysis"]["recorded_tasks"]["value"] == 0


@pytest.mark.parametrize("state,count", [("started", 1), ("failed", 0)])
def test_baseline_has_actual_starts_and_inapplicable_labhq_stages(state, count):
    rec = bm.Recorder("sol-ultra")
    spawn(rec, rid=None, state=state)
    result = bm.collect(rec)
    assert result["cli"]["spawn_successes"]["value"] == count
    assert result["recorded_tasks"]["status"] == "not_applicable"
    assert all(row["recorded_tasks"]["status"] == "not_applicable" for row in result["stages"].values())
    assert bm.collect(rec, mode="mock")["cli"]["spawn_successes"]["status"] == "not_applicable"


def approval(steps, *, at=10, **detail):
    canonical = json.dumps({"steps": steps}, sort_keys=True)
    return {"approved": True, "decided_at": at, "approval": {"id": str(at), "request_id": "r", "created_at": at-2,
            "detail": {"plan_canonical": canonical, "target_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                       **detail}}}


def test_approved_initial_replan_continuation_and_waits_require_proof():
    steps = [{"id": "s1", "agent_id": "analyst", "outputs": ["x"], "depends_on": []},
             {"id": "s2", "agent_id": "qc_reviewer", "depends_on": ["s1"]}]
    decisions = {"a": approval(steps), "duplicate": approval(steps, at=11),
                 "replan": approval(steps[:1], at=12),
                 "continuation": approval(steps, at=13, continuation={"round": 2, "rerun": ["s2"]}),
                 "bad_hash": approval(steps, at=14, target_sha256="wrong"),
                 "declined": {**approval(steps, at=15), "approved": False}}
    decisions["replayed_decision"] = copy.deepcopy(decisions["a"])
    receipt = bm.collect(bm.Recorder("labhq", "r"), tasks={}, approvals=decisions)
    plans = receipt["approved_plans"]["items"]
    assert [p["phase"] for p in plans] == ["initial", "replan", "continuation"]
    assert [p["expected_dispatches"]["value"] for p in plans] == [2, None, 1]
    assert plans[0]["shape"]["boundary_handoffs"] == 1
    assert receipt["approval_wait_s"]["value"] == 12
    assert "not_collected" in bm.render(receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_adapter_observes_success_only_after_fake_spawn(tmp_path, monkeypatch, fail):
    class Adapter(base.AgentAdapter):
        engine = "fixture"
        def build_command(self, ctx): return ["fixture", ctx.prompt]
        async def handle_line(self, *args): pass
        def staff_env(self, ctx): return {}
        def prepare(self, ctx): pass
        def preflight_error(self, *args): return None

    rec = bm.Recorder("labhq", "r")
    ctx = base.RunContext(task=Task(id="t", agent_id="analyst", request_id="r", prompt="SECRET"),
        agent=AgentSpec(id="analyst", name="fixture", role="fixture"), workdir=tmp_path,
        settings=Settings(), mcp_servers=[], env={}, emit=AsyncMock(), prompt="SECRET",
        bench_observer=rec.spawn, on_started=Mock(side_effect=asyncio.CancelledError))
    ctx.awrite_meta = AsyncMock()
    monkeypatch.setattr(base, "_resolve_command", lambda cmd, *args: cmd)
    monkeypatch.setattr(base.memory_limit, "configured_limit_bytes", lambda *args: 0)
    monkeypatch.setattr(base.memory_limit, "step_memory_guard", lambda *args: SimpleNamespace(
        spawn_kwargs=lambda: {}, wrap_command=lambda cmd: cmd, close=Mock()))

    async def fake_spawn(*args, **kwargs):
        assert rec.cli({"t"})["spawn_successes"]["value"] is None  # an attempt is not a start
        if fail: raise FileNotFoundError("synthetic")
        return SimpleNamespace()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    adapter = Adapter(ctx.settings)
    adapter._kill = AsyncMock()
    if fail:
        assert not (await adapter.run(ctx)).ok
    else:
        with pytest.raises(asyncio.CancelledError):
            await adapter.run(ctx)
    assert rec.cli({"t"})["spawn_successes"]["value"] == (0 if fail else 1)
    assert "SECRET" not in json.dumps(bm.collect(rec, tasks={"t": task()}))


def test_default_hook_does_not_even_measure_or_call_a_recorder(tmp_path):
    ctx = base.RunContext(task=Task(agent_id="x", prompt="fixture"),
        agent=AgentSpec(id="x", name="fixture", role="fixture"), workdir=tmp_path,
        settings=Settings(), mcp_servers=[], env={}, emit=AsyncMock(), prompt="fixture")
    class Forbidden:
        def encode(self, *args): raise AssertionError("off must not inspect arguments")
    ctx.bench_spawn(object(), "attempt", [Forbidden()], Forbidden())
    assert ctx.bench_observer is None
    ctx.bench_observer = Mock(side_effect=RuntimeError("optional recorder failed"))
    ctx.bench_spawn(object(), "attempt", ["synthetic"], b"x")
    ctx.bench_observer.assert_called_once()
    assert set(ctx.bench_observer.call_args.kwargs) == {
        "task_id", "request_id", "spawn_id", "state", "prompt_bytes", "argv_bytes", "stdin_bytes"}


def test_metric_files_cannot_alias_another_arm(tmp_path, monkeypatch):
    from pathlib import Path
    arm = tmp_path / "arm-a"
    arm.mkdir()
    original = Path.resolve
    def resolved(path, *args, **kwargs):
        if path == arm / "bench_metrics.md":
            return tmp_path / "arm-b" / "bench_metrics.md"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolved)
    with pytest.raises(ValueError, match="escapes its arm"):
        bm.save(arm, bm.collect(bm.Recorder("sol-ultra")))
    assert not (arm / "bench_metrics.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_baseline_writes_isolated_receipt_at_the_fake_spawn_boundary(tmp_path, monkeypatch, fail):
    monkeypatch.setattr(base, "_resolve_command", lambda cmd, *args: cmd)
    async def fake_spawn(*args, **kwargs):
        if fail: raise OSError("PRIVATE must never appear in receipt")
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
            b'{"type":"result","result":"fixture","total_cost_usd":0.1}', b'')))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    result = await bench._run_baseline({"timeout_s": 1}, "sonnet-max", tmp_path, "real",
                                       ["fixture", "PRIVATE"], Settings())
    receipt = json.loads((tmp_path / "bench_metrics.json").read_text(encoding="utf-8"))
    assert result["bench_metrics"] == receipt
    assert receipt["cli"]["spawn_successes"]["value"] == (0 if fail else 1)
    if not fail:
        assert receipt["cli"]["prepared_prompt_utf8_bytes_proxy"]["value"] is None
        assert receipt["cli"]["argv_utf8_bytes"]["value"] == len("fixture\0PRIVATE\0".encode())
    assert "PRIVATE" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_labhq_bench_uses_only_its_own_request_and_runner(tmp_path, monkeypatch, harness):
    from labhq.runner import daemon
    case, _, _, _ = harness
    hub = case.pop("_harness_hub")
    monkeypatch.setattr(bench, "free_ports", lambda n: [43333 + i for i in range(n)])  # no socket even with fake server
    original_runner, instances = daemon.Runner, []
    def make_runner(settings):
        obj = original_runner(settings)
        instances.append(obj)
        return obj
    monkeypatch.setattr(daemon, "Runner", make_runner)
    original_create = hub.create_request
    def create(request):
        rid = original_create(request)
        hub.requests[rid]["reproduction_receipt"] = {"files": []}
        async def events():
            observer = instances[0]._bench_observer
            for state in ("attempt", "started", "started"):
                observer(task_id="t", request_id=rid, spawn_id="s", state=state,
                         prompt_bytes=5, argv_bytes=10, stdin_bytes=0)
            await hub.publish({"type": "task.result", "request_id": rid, "task_id": "t", "ts": 20})
        asyncio.create_task(events())
        return rid
    hub.create_request = create
    hub.store = SimpleNamespace(all=lambda kind: {"t": task(request_id="request")} if kind == "task" else {})
    result = await bench._run_labhq(case, tmp_path, "real", Settings())
    assert result["bench_metrics"]["cli"]["spawn_successes"]["value"] == 1
    assert result["bench_metrics"]["stages"]["analysis"]["elapsed_task_s"]["value"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("old_has_metrics", [False, True])
async def test_rescore_preserves_execution_receipt_without_reinstrumenting(tmp_path, monkeypatch, old_has_metrics):
    case = bench.load_case("public-penguins-qc")
    arm = tmp_path / case["id"] / "run-001" / "sol-ultra"
    arm.mkdir(parents=True)
    monkeypatch.setattr(bench.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    metrics = bm.collect(bm.Recorder("sol-ultra"))
    run = {"engine": "sol-ultra", "mode": "real", "status": "done", "cost_usd": 1.25,
           "cost_known": False, "duration_s": 3.0, "bench_metrics": metrics}
    (arm / "answer.md").write_text(case["mock_answer"], encoding="utf-8")
    (arm / "run.json").write_text(json.dumps(run), encoding="utf-8")
    bm.save(arm, metrics)
    row = await bench._score(case, arm, run)
    assert row["bench_metrics"] == metrics
    if not old_has_metrics: row.pop("bench_metrics")
    (arm / "score.json").write_text(json.dumps(row), encoding="utf-8")
    before = {name: (arm / name).read_bytes() for name in ("run.json", "bench_metrics.json", "bench_metrics.md")}
    monkeypatch.setattr(bm, "collect", Mock(side_effect=AssertionError("rescore cannot rerun collection")))
    await bench.rescore_case(case["id"], tmp_path)
    assert before == {name: (arm / name).read_bytes() for name in before}
    saved = json.loads((arm / "score.json").read_text(encoding="utf-8"))
    assert saved["bench_metrics"] == metrics and saved["cost_usd"] == 1.25 and saved["cost_known"] is False
    assert json.loads((arm.parent / "comparison.json").read_text(encoding="utf-8"))["rows"][0]["bench_metrics"] == metrics
