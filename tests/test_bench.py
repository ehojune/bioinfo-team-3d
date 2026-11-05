import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import bench
from labhq.cli import main


def _mock_tier_control(limit=10, **kwargs):
    return bench.MockTierCostControl(limit, valid_until=100, clock=lambda: 0,
                                     verified=True, **kwargs)


def test_tier_cost_missing_control_rejects_before_dispatch(tmp_path, monkeypatch):
    from labhq.settings import Settings

    async def forbidden(*args, **kwargs):
        pytest.fail("rejected admission must not dispatch")

    monkeypatch.setattr(bench, "_run_labhq", forbidden)
    with pytest.raises(ValueError, match="cost control"):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "a", "balanced": "b"}))
    assert not (tmp_path / "pair").exists()


@pytest.mark.parametrize("bounds", [None, {}, {"reasoning": 5},
                                    {"reasoning": 5, "balanced": None},
                                    {"reasoning": 5, "balanced": 5.0},
                                    {"reasoning": 5, "balanced": True},
                                    {"reasoning": 5, "balanced": -1},
                                    {"reasoning": 5, "balanced": 6}])
def test_tier_cost_bad_bounds_reject_without_dispatch(tmp_path, monkeypatch, bounds):
    from labhq.settings import Settings

    async def forbidden(*args, **kwargs):
        pytest.fail("rejected admission must not dispatch")

    control = _mock_tier_control()
    monkeypatch.setattr(bench, "_run_labhq", forbidden)
    with pytest.raises(ValueError):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "a", "balanced": "b"},
                                             cost_control=control, worst_case_units=bounds))
    assert control.snapshot()["outstanding_units"] == 0
    assert not (tmp_path / "pair").exists()


@pytest.mark.parametrize("verified,expires", [(False, 100), (None, 100), (True, 0),
                                              (True, float("nan"))])
def test_tier_cost_unverified_or_stale_control_rejects(verified, expires):
    with pytest.raises(ValueError):
        control = bench.MockTierCostControl(10, valid_until=expires, clock=lambda: 0,
                                            verified=verified)
        control.reserve_pair({"reasoning": 5, "balanced": 5})


def test_tier_cost_exact_limit_and_competing_admission():
    from concurrent.futures import ThreadPoolExecutor

    control = _mock_tier_control()

    def reserve():
        try:
            return control.reserve_pair({"reasoning": 4, "balanced": 6})
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert control.snapshot()["outstanding_units"] == 10
    assert control.snapshot()["settled_units"] == 0


@pytest.mark.parametrize("kind", ["missing", "foreign", "unverified", "stale", "bad-clock"])
def test_tier_cost_invalid_control_never_dispatches(tmp_path, monkeypatch, kind):
    from labhq.settings import Settings

    control = _mock_tier_control()
    if kind == "missing":
        control = None
    elif kind == "foreign":
        control = SimpleNamespace(reserve_pair=lambda _: {})
    elif kind == "unverified":
        control = bench.MockTierCostControl(10, valid_until=100, clock=lambda: 0)
    elif kind == "stale":
        control = bench.MockTierCostControl(10, valid_until=0, clock=lambda: 0, verified=True)
    elif kind == "bad-clock":
        def bad_clock():
            raise RuntimeError("unverifiable")
        control = bench.MockTierCostControl(10, valid_until=100, clock=bad_clock, verified=True)

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid control must not dispatch")

    monkeypatch.setattr(bench, "_run_labhq", forbidden)
    with pytest.raises(ValueError, match="cost control"):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "a", "balanced": "b"},
                                             cost_control=control,
                                             worst_case_units={"reasoning": 4, "balanced": 6}))
    assert not (tmp_path / "pair").exists()


def test_tier_cost_dispatch_settlement_and_unknown_usage(tmp_path, monkeypatch):
    from labhq.settings import Settings

    control = _mock_tier_control()
    seen = []

    async def run(*args, **kwargs):
        seen.append(control.snapshot())
        if len(seen) == 1:
            return {"mock_cost_receipt": {"units": 2, "verified": True, "final": True}}
        return {"status": "done", "cost_usd": 0, "cost_known": True}

    async def score(*args):
        return {}

    monkeypatch.setattr(bench, "_run_labhq", run)
    monkeypatch.setattr(bench, "_score", score)
    result = asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                                  {"reasoning": "a", "balanced": "b"},
                                                  cost_control=control,
                                                  worst_case_units={"reasoning": 4, "balanced": 6}))
    assert [(s["settled_units"], s["outstanding_units"]) for s in seen] == [(0, 10), (2, 6)]
    assert result["cost_control"]["outstanding_units"] == 6  # dollars are not a final receipt
    ids = json.loads((tmp_path / "pair" / "cost-reservations.json").read_text())
    control.settle(ids["balanced"], 3, verified=True, final=True)
    control.settle(ids["balanced"], 3, verified=True, final=True)
    assert control.snapshot()["settled_units"] == 5
    assert control.snapshot()["outstanding_units"] == 0
    with pytest.raises(ValueError):
        control.reserve_pair({"reasoning": 3, "balanced": 3})


def test_tier_cost_receipt_cannot_settle_another_control_or_unstarted_arm():
    control = _mock_tier_control()
    ids = control.reserve_pair({"reasoning": 4, "balanced": 6})
    other = _mock_tier_control().reserve_pair({"reasoning": 4, "balanced": 6})
    for reservation_id in (ids["reasoning"], other["reasoning"]):
        with pytest.raises(ValueError):
            control.settle(reservation_id, 0, verified=True, final=True)
    assert control.snapshot()["outstanding_units"] == 10


def test_tier_cost_live_stays_blocked_with_verified_mock_control(tmp_path, monkeypatch):
    from labhq.settings import Settings

    control = _mock_tier_control(0)

    async def forbidden(*args, **kwargs):
        pytest.fail("mock verification must never unlock live dispatch")

    monkeypatch.setattr(bench, "_run_labhq", forbidden)
    with pytest.raises(ValueError, match="live tier pair disabled"):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "a", "balanced": "b"}, "real",
                                             cost_control=control,
                                             worst_case_units={"reasoning": 0, "balanced": 0}))
    assert not (tmp_path / "pair").exists()


def test_tier_cost_late_duplicate_receipt_and_retry():
    control = _mock_tier_control()
    ids = control.reserve_pair({"reasoning": 4, "balanced": 6})
    control.start(ids["reasoning"])
    with pytest.raises(ValueError, match="already"):
        control.start(ids["reasoning"])
    with pytest.raises(ValueError):
        control.reserve_pair({"reasoning": 1, "balanced": 1})
    # Timeout/cancel do not free anything. A late final receipt does, exactly once.
    control.settle(ids["reasoning"], 2, verified=True, final=True)
    assert control.snapshot()["settled_units"] == 2
    assert control.snapshot()["outstanding_units"] == 6
    control.settle(ids["reasoning"], 2, verified=True, final=True)
    retry = control.reserve_pair({"reasoning": 1, "balanced": 1})
    assert set(retry.values()).isdisjoint(ids.values())
    assert control.snapshot()["settled_units"] + control.snapshot()["outstanding_units"] == 10


@pytest.mark.parametrize("amount,verified,final", [(None, True, True), (1.0, True, True),
                                                   (True, True, True), (-1, True, True),
                                                   (1, False, True), (1, True, False)])
def test_tier_cost_unknown_receipts_preserve_reservation(amount, verified, final):
    control = _mock_tier_control()
    ids = control.reserve_pair({"reasoning": 4, "balanced": 6})
    control.start(ids["reasoning"])
    with pytest.raises(ValueError):
        control.settle(ids["reasoning"], amount, verified=verified, final=final)
    assert control.snapshot()["outstanding_units"] == 10


def test_tier_cost_conflicting_or_excess_receipt_poison_control():
    for first, second in [(2, 3), (None, 5)]:
        control = _mock_tier_control()
        ids = control.reserve_pair({"reasoning": 4, "balanced": 6})
        control.start(ids["reasoning"])
        if first is not None:
            control.settle(ids["reasoning"], first, verified=True, final=True)
        with pytest.raises(ValueError):
            control.settle(ids["reasoning"], second, verified=True, final=True)
        assert control.snapshot()["blocked"]
        with pytest.raises(ValueError):
            control.start(ids["balanced"])


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
def test_tier_cost_interrupted_pair_retains_all_reservations(tmp_path, monkeypatch, failure):
    from labhq.settings import Settings

    seen = []
    control = _mock_tier_control()

    async def interrupted(*args, **kwargs):
        seen.append(True)
        raise failure()

    monkeypatch.setattr(bench, "_run_labhq", interrupted)
    with pytest.raises(failure):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "a", "balanced": "b"},
                                             cost_control=control,
                                             worst_case_units={"reasoning": 4, "balanced": 6}))
    assert seen == [True]
    assert control.snapshot()["outstanding_units"] == 10
    ids = json.loads((tmp_path / "pair" / "cost-reservations.json").read_text())
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "retry", Settings(),
                                             {"reasoning": "a", "balanced": "b"},
                                             cost_control=control,
                                             worst_case_units={"reasoning": 1, "balanced": 1}))
    assert seen == [True]
    control.settle(ids["reasoning"], 2, verified=True, final=True)
    assert control.snapshot()["settled_units"] == 2
    assert control.snapshot()["outstanding_units"] == 6


def test_tier_cost_expiry_between_arms_blocks_second(tmp_path, monkeypatch):
    from labhq.settings import Settings

    now = [0]
    control = bench.MockTierCostControl(10, valid_until=1, clock=lambda: now[0], verified=True)
    seen = []

    async def run(*args, **kwargs):
        seen.append(True)
        now[0] = 1
        return {"status": "done"}

    async def score(*args):
        return {}

    monkeypatch.setattr(bench, "_run_labhq", run)
    monkeypatch.setattr(bench, "_score", score)
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "a", "balanced": "b"},
                                             cost_control=control,
                                             worst_case_units={"reasoning": 4, "balanced": 6}))
    assert seen == [True]
    assert control.snapshot()["outstanding_units"] == 10


def test_fixed_plan_is_resumed_without_fresh_planning():
    plan = {"steps": [{"id": "s1", "agent_id": "analyst", "instruction": "QC"}]}
    seen = []

    class Orchestrator:
        async def run_request(self, rid, resume=False, continuation=False):
            seen.append((rid, resume, continuation))

    class Hub:
        def __init__(self):
            self.orchestrator = Orchestrator()

        def create_request(self, body, extra=None):
            assert extra["plan"] == plan
            asyncio.create_task(self.orchestrator.run_request("r"))
            return "r"

    async def run():
        hub = Hub()
        bench._create_fixed_plan_request(hub, object(), plan)
        await asyncio.sleep(0)
        assert seen == [("r", True, False)]

    asyncio.run(run())


def test_tier_pair_uses_separate_states_and_same_rubric(tmp_path, monkeypatch):
    from labhq.settings import Settings

    seen = []

    async def run(case, arm_dir, engines, settings, approve_budget_up_to=None, fixed_plan=None):
        seen.append((case, arm_dir, settings, fixed_plan))
        return {"status": "done"}

    async def score(case, arm_dir, run):
        return {"case": case["id"], "check": case["check"]}

    monkeypatch.setattr(bench, "_run_labhq", run)
    monkeypatch.setattr(bench, "_score", score)
    result = asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                                  {"reasoning": "model-a", "balanced": "model-b"},
                                                  cost_control=_mock_tier_control(),
                                                  worst_case_units={"reasoning": 4, "balanced": 6}))
    assert [row["tier"] for row in result["rows"]] == ["reasoning", "balanced"]
    assert seen[0][1] != seen[1][1]
    assert seen[0][0] == seen[1][0] and result["rows"][0]["score"] == result["rows"][1]["score"]
    steps = [entry[3]["steps"][0] for entry in seen]
    assert {k: v for k, v in steps[0].items() if k != "model_tier"} == {
        k: v for k, v in steps[1].items() if k != "model_tier"}
    assert [s["model_tier"] for s in steps] == ["reasoning", "balanced"]
    assert all(entry[2].orchestrator.step_model_tiers for entry in seen)
    assert seen[1][2].model_tiers.model_for("balanced", "claude_code") == "model-b"


def test_tier_pair_blocks_live_before_any_dispatch(tmp_path, monkeypatch):
    from labhq.settings import Settings

    async def forbidden(*args, **kwargs):
        pytest.fail("no live dispatch without an aggregate provider cap")

    monkeypatch.setattr(bench, "_run_labhq", forbidden)
    with pytest.raises(ValueError, match="aggregate provider spending control"):
        asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", Settings(),
                                             {"reasoning": "model-a", "balanced": "model-b"}, "real"))
    assert not (tmp_path / "pair").exists()


def test_fixed_tier_pair_dispatches_saved_plan_without_planning(tmp_path):
    import sqlite3
    import yaml
    from labhq.models import AgentSpec, Task
    from labhq.runner.daemon import Runner
    from labhq.settings import Settings

    settings = Settings()
    settings.runner.agents_dir = str(bench.REPO / "agents")
    result = asyncio.run(bench.run_fixed_tier_pair(tmp_path / "pair", settings,
                                                  {"reasoning": "model-a", "balanced": "model-b"},
                                                  cost_control=_mock_tier_control(),
                                                  worst_case_units={"reasoning": 4, "balanced": 6}))
    for row in result["rows"]:
        tier = row["tier"]
        assert row["run"]["status"] == "done"
        state = tmp_path / "pair" / tier / "state"
        spec = yaml.safe_load((state / "agents/core/analyst.yaml").read_text(encoding="utf-8"))
        assert spec["model"] == "model-a"
        with sqlite3.connect(state / "gateway/gateway.sqlite3") as db:
            tasks = [json.loads(body) for (body,) in db.execute("select body from state where kind='task'")]
        assert not any(task["kind"] in {"plan", "planning", "briefing"} for task in tasks)
        steps = [task for task in tasks if task["kind"] == "step"]
        assert len(steps) == 1 and steps[0]["payload"]["agent_id"] == "analyst"
        # The declared reasoning tier is represented by no override; balanced is an explicit override.
        assert steps[0]["payload"]["meta"].get("model_tier") == (None if tier == "reasoning" else "balanced")
        # Resolve the actual captured task with the real engine configured, without launching a provider.
        runner = Runner.__new__(Runner)
        runner.s = settings.model_copy(deep=True)
        runner.s.model_tiers.reasoning = {"claude_code": "model-a"}
        runner.s.model_tiers.balanced = {"claude_code": "model-b"}
        runner.registry = SimpleNamespace(get=lambda _id: AgentSpec.model_validate(spec))
        resolved = runner._resolve_agent(Task.model_validate(steps[0]["payload"]))
        assert resolved.model == ("model-a" if tier == "reasoning" else "model-b")


def test_case_catalog_has_five_bounded_cases():
    cases = bench.load_cases()
    assert [case["id"] for case in cases] == [
        "inco-kras-g12c",
        "plastome-structure",
        "geo-gastric-summary",
        "public-protein-qc",
        "public-penguins-qc",
    ]
    for case in cases:
        assert case["request"].strip()
        assert case["references"]
        assert case["scripted_pi_answers"]
        assert case["check"]["script"].endswith(".py")
        assert 0 < case["budget_usd"] <= 10


def test_bench_list_and_dry_run_print_four_arms_without_running(tmp_path, capsys, monkeypatch):
    from labhq.settings import Settings

    monkeypatch.setattr("labhq.cli.Settings.load", lambda *_: (_ for _ in ()).throw(AssertionError("no config")))
    main(["bench", "list"])
    listed = capsys.readouterr().out
    assert "inco-kras-g12c" in listed and "public-penguins-qc" in listed
    monkeypatch.setattr(Settings, "load", lambda *_: Settings())

    called = False

    async def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("dry-run must not execute")

    monkeypatch.setattr(bench, "run_case", forbidden)
    main(["bench", "run", "inco-kras-g12c", "--dry-run", "--output", str(tmp_path)])
    output = capsys.readouterr().out
    assert "labhq" in output
    assert "claude -p" in output and "--model sonnet --effort max" in output
    assert "codex exec" in output and "gpt-6-astra" in output and "gpt-5.6-sol" in output
    assert 'model_reasoning_effort=\\"ultra\\"' in output
    assert "--staff-model opus=sonnet" in output
    assert all(f"[{arm}]" in output for arm in bench.ARMS)
    assert "claude-opus" not in output
    assert "--ephemeral" in output and "--ignore-user-config" in output
    assert "--disallowedTools" in output and "SendMessage" in output
    assert not called and not any(tmp_path.iterdir())


def test_mock_case_runs_all_arms_scores_and_records_case_id(tmp_path):
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock"))
    assert result["case_id"] == "public-protein-qc"
    assert [row["engine"] for row in result["rows"]] == list(bench.ARMS)
    assert all(row["artifact_exists"] and row["checks_passed"] for row in result["rows"])
    assert all(row["cost_usd"] == 0 and row["token_total"] == 0 for row in result["rows"])
    run_dir = tmp_path / "public-protein-qc" / result["run_id"]
    assert (run_dir / "comparison.md").is_file()
    saved = json.loads((run_dir / "comparison.json").read_text(encoding="utf-8"))
    assert saved == result

    labhq_run = json.loads((run_dir / "labhq" / "run.json").read_text(encoding="utf-8"))
    round_record = json.loads(Path(labhq_run["round_json"]).read_text(encoding="utf-8"))
    assert round_record["request"]["meta"]["case_id"] == "public-protein-qc"
    assert "PI가 질문을 받으면" not in round_record["request"]["text"]
    commands = bench._real_commands(bench.load_case("public-protein-qc"), tmp_path, run_dir)
    assert commands["sonnet-max"][commands["sonnet-max"].index("-p") + 1] == round_record["request"]["text"]
    assert commands["astra-ultra"][-1] == round_record["request"]["text"]
    assert commands["sol-ultra"][-1] == round_record["request"]["text"]
    for row in result["rows"][1:]:
        assert row["pi_questions_observable"] is False
        assert row["pi_interventions"] == 0
    assert "감지 불가·답변 미제공" in (run_dir / "comparison.md").read_text(encoding="utf-8")


def test_scripted_pi_selects_the_matching_answer():
    case = bench.load_case("public-protein-qc")
    assert bench._scripted_answer(case, "중복 accession을 제거할까요?", "clarify") == (
        True, "중복은 제거하지 말고 원본 4행과 unique 3개를 함께 보고한다.")


@pytest.mark.parametrize("case", bench.load_cases(), ids=lambda case: case["id"])
def test_initial_prompt_never_discloses_scripted_pi_answers(case, tmp_path):
    prompt = bench._prompt(case)
    commands = bench._real_commands(case, tmp_path)
    assert commands["sonnet-max"][commands["sonnet-max"].index("-p") + 1] == prompt
    assert commands["astra-ultra"][-1] == prompt
    assert commands["sol-ultra"][-1] == prompt
    assert "PI가 질문을 받으면" not in prompt
    assert all(item["answer"] not in prompt for item in case["scripted_pi_answers"])


def _bench_process_exited(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    return stat.is_file() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"


def _bench_process_state(pids, processes):
    return {
        "pids": {pid: "exited" if _bench_process_exited(pid) else "running" for pid in pids},
        "spawned_returncodes": [proc.returncode for proc in processes],
    }


async def _wait_for_bench_process_tree(pids_file, processes, timeout=30):
    deadline = time.monotonic() + timeout
    raw, pids = "<missing>", []
    while True:
        if pids_file.is_file():
            raw = pids_file.read_text(encoding="utf-8")
            try:
                pids = list(map(int, raw.split()))
            except ValueError:
                pids = []
        if len(pids) == 3 and all(_bench_process_exited(pid) is False for pid in pids):
            return pids
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"fake CLI tree did not start: pids_file={raw!r}; "
                f"state={_bench_process_state(pids, processes)}"
            )
        await asyncio.sleep(0.05)


async def _wait_for_bench_processes(pids, processes=(), timeout=30):
    deadline = time.monotonic() + timeout
    while remaining := [pid for pid in pids if not _bench_process_exited(pid)]:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"baseline left live pids: {remaining}; "
                f"state={_bench_process_state(pids, processes)}"
            )
        await asyncio.sleep(0.05)


async def _ready_bench_spawn(spawn, args, kwargs, pids_file, processes, started, kill):
    """The fixture owns startup; product deadlines start only after a verified live tree."""
    proc = await spawn(*args, **kwargs)
    processes.append(proc)  # Retain ownership even if readiness fails or is cancelled.
    try:
        pids = await _wait_for_bench_process_tree(pids_file, processes)
    except BaseException as startup_error:
        try:
            await kill(proc)
        except BaseException as cleanup_error:
            if not started.done():
                started.set_exception(cleanup_error)
            raise cleanup_error from startup_error
        if not started.done():
            started.set_exception(startup_error)
        raise
    communicate = proc.communicate

    async def communicate_started():
        if (pids[0] != proc.pid or proc.returncode is not None
                or any(_bench_process_exited(pid) is not False for pid in pids)):
            raise AssertionError("verified CLI tree exited before the baseline drain started")
        started.set_result(pids)  # Product has its process/drain and its cancellation finally block now.
        return await communicate()

    proc.communicate = communicate_started
    return proc


def _bench_startup_finished(task, started):
    """An early task failure/return must not leave the readiness waiter hanging."""
    if started.done():
        return
    if task.cancelled():
        started.cancel()
    elif task.exception() is not None:
        started.set_exception(task.exception())
    else:
        started.set_exception(AssertionError("baseline exited before verified CLI tree readiness"))


def test_wait_for_bench_processes_reports_remaining_pids(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_bench_process_exited", lambda _pid: False)
    with pytest.raises(TimeoutError, match=r"live pids: \[11, 12\]"):
        asyncio.run(_wait_for_bench_processes([11, 12], timeout=0))


def test_wait_for_bench_process_tree_reports_partial_pid_state(tmp_path, monkeypatch):
    pids_file = tmp_path / "pids.txt"
    pids_file.write_text("11 12", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_bench_process_exited", lambda _pid: False)
    with pytest.raises(TimeoutError, match=r"pids_file='11 12'.*11.*running.*12.*running"):
        asyncio.run(_wait_for_bench_process_tree(pids_file, [], timeout=0))


@pytest.mark.parametrize("arm", ["sonnet-max", "astra-ultra"])
@pytest.mark.parametrize("stop", ["case-timeout", "runner-timeout", "cancel"])
def test_baseline_timeout_and_cancel_kill_cli_tree(tmp_path, monkeypatch, arm, stop):
    from labhq.settings import Settings
    from labhq.adapters.base import AgentAdapter

    pids_file = tmp_path / "pids.txt"
    script = tmp_path / "stalled_cli.py"
    script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "pids = pathlib.Path(sys.argv[1])\n"
        "def publish(path, text):\n"
        "    pending = path.with_suffix(path.suffix + '.tmp')\n"
        "    pending.write_text(text, encoding='utf-8')\n"
        "    pending.replace(path)\n"
        "if len(sys.argv) > 2:\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "    publish(pids.with_suffix('.child'), f'{os.getpid()} {child.pid}')\n"
        "else:\n"
        "    child = subprocess.Popen([sys.executable, __file__, str(pids), 'child'])\n"
        "    while not pids.with_suffix('.child').is_file(): time.sleep(0.01)\n"
        "    publish(pids, f'{os.getpid()} ' + pids.with_suffix('.child').read_text(encoding='utf-8'))\n"
        "time.sleep(60)\n", encoding="utf-8")
    case = bench.load_case("public-protein-qc")
    settings = Settings()
    settings.runner.task_timeout_s = 30 if stop != "runner-timeout" else 1
    if stop == "case-timeout":
        case["timeout_s"] = 1
    monkeypatch.setattr("labhq.adapters.base.child_config_dirs", lambda *args: [tmp_path])
    processes = []
    started = None
    kill = AgentAdapter._kill
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        if (os.name == "nt" and args[0] == "taskkill"
                and stop == "cancel" and arm == "sonnet-max"):
            raise MemoryError
        if args[0] == sys.executable:
            return await _ready_bench_spawn(original_spawn, args, kwargs, pids_file, processes, started, kill)
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def exercise():
        nonlocal started
        started = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(bench._run_baseline(
            case, arm, tmp_path, "real", [sys.executable, str(script), str(pids_file)], settings))
        task.add_done_callback(lambda done: _bench_startup_finished(done, started))
        pids = []
        try:
            pids = await asyncio.wait_for(asyncio.shield(started), 30)
            if stop == "cancel":
                task.cancel()  # asyncio.run translates Ctrl+C into cancellation of the main task.
                try:
                    await asyncio.wait_for(asyncio.shield(task), 15)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    pytest.fail(
                        "cancelled baseline did not finish; "
                        f"state={_bench_process_state(pids, processes)}"
                    )
                else:
                    pytest.fail("cancelled baseline returned instead of raising CancelledError")
            else:
                try:
                    run = await asyncio.wait_for(asyncio.shield(task), 15)
                except asyncio.TimeoutError:
                    pytest.fail(
                        "timed-out baseline did not finish; "
                        f"state={_bench_process_state(pids, processes)}"
                    )
                assert run["status"] == "failed"
                assert run["error"] == "timeout after 1s"
                assert json.loads((tmp_path / "run.json").read_text())["error"] == run["error"]
            await _wait_for_bench_processes(pids, processes)
        finally:
            # Keep a failed assertion from leaking its fake CLI.
            if not started.done():
                started.cancel()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not pids and pids_file.is_file():
                pids = list(map(int, pids_file.read_text().split()))
            for pid in reversed(pids):
                if not _bench_process_exited(pid):
                    try:
                        os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for proc in processes:
                if proc.returncode is None:
                    proc.kill()
                await asyncio.wait_for(proc.wait(), 10)

    asyncio.run(exercise())


def _bench_readiness_fake_helpers():
    """Extract only fixture code; every process/event/clock capability is fake."""
    import ast

    source = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {"_wait_for_bench_process_tree", "_ready_bench_spawn", "_bench_startup_finished"}
    nodes = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    fixture = next(node for node in source.body if getattr(node, "name", "") ==
                   "test_baseline_timeout_and_cancel_kill_cli_tree")
    spawn = next(node for node in ast.walk(fixture) if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "spawn")
    nodes.append(spawn)
    events, processes = [], []

    class Ready:
        value = error = None
        finished = False

        def done(self):
            return self.finished

        def set_result(self, value):
            assert not self.finished
            self.finished, self.value = True, value
            events.append("ready")

        def set_exception(self, error):
            assert not self.finished
            self.finished, self.error = True, error
            events.append("error")

        def cancel(self):
            self.finished = True
            events.append("cancel")

    async def communicate():
        events.append("communicate")
        return b"", b""

    proc = SimpleNamespace(pid=11, returncode=None, communicate=communicate)

    async def original_spawn(*args, **kwargs):
        events.append("spawn")
        return proc

    async def kill(process):
        assert process is proc
        events.append("kill")

    async def ready_tree(*args, **kwargs):
        events.append("tree")
        return [11, 12, 13]

    namespace = {"original_spawn": original_spawn, "processes": processes, "started": Ready(),
                 "pids_file": object(), "kill": kill, "stop": "runner-timeout", "arm": "sonnet-max",
                 "os": SimpleNamespace(name="posix"), "sys": SimpleNamespace(executable="fake-python")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), __file__, "exec"), namespace)
    namespace["_wait_for_bench_process_tree"] = ready_tree
    return SimpleNamespace(namespace=namespace, events=events, processes=processes, proc=proc,
                           started=namespace["started"])


def _bench_finish_fake(coroutine):
    try:
        try:
            coroutine.send(None)
        except StopIteration as ended:
            return ended.value
        raise AssertionError("unexpected suspension in immediate fake")
    finally:
        coroutine.close()


def test_bench_spawn_defers_deadline_until_live_tree_and_communicate():
    fake = _bench_readiness_fake_helpers()

    class TreeReady:
        def __await__(self):
            yield "waiting-for-tree"
            return [11, 12, 13]

    async def wait(*args):
        fake.events.append("wait-tree")
        return await TreeReady()

    fake.namespace["_wait_for_bench_process_tree"] = wait
    fake.namespace["_bench_process_exited"] = lambda pid: False
    spawn = fake.namespace["spawn"]("fake-python")
    try:
        assert spawn.send(None) == "waiting-for-tree"
        assert fake.processes == [fake.proc] and not fake.started.done()
        with pytest.raises(StopIteration) as done:
            spawn.send(None)
        assert done.value.value is fake.proc
        assert not fake.started.done()  # Product must first receive the retained process and start its drain.
        assert _bench_finish_fake(fake.proc.communicate()) == (b"", b"")
        assert fake.started.value == [11, 12, 13]
        assert fake.events == ["spawn", "wait-tree", "ready", "communicate"]
    finally:
        spawn.close()


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_bench_startup_failure_cleans_owned_process_before_publishing_error(cleanup_fails, cancelled):
    fake = _bench_readiness_fake_helpers()
    startup_error = asyncio.CancelledError() if cancelled else TimeoutError("tree not ready")
    cleanup_error = RuntimeError("cleanup failed")

    async def wait(*args):
        raise startup_error

    async def kill(process):
        assert process is fake.proc
        fake.events.append("kill")
        if cleanup_fails:
            raise cleanup_error

    fake.namespace.update(_wait_for_bench_process_tree=wait, kill=kill)
    expected = cleanup_error if cleanup_fails else startup_error
    with pytest.raises(type(expected)) as failed:
        _bench_finish_fake(fake.namespace["spawn"]("fake-python"))
    assert failed.value is expected and fake.started.error is expected
    assert fake.events == ["spawn", "kill", "error"]
    if cleanup_fails:
        assert failed.value.__cause__ is startup_error


@pytest.mark.parametrize("state", [None, True])
def test_bench_tree_lost_before_drain_cannot_signal_readiness(state):
    fake = _bench_readiness_fake_helpers()
    fake.namespace["_bench_process_exited"] = lambda pid: state
    assert _bench_finish_fake(fake.namespace["spawn"]("fake-python")) is fake.proc
    with pytest.raises(AssertionError, match="exited before"):
        _bench_finish_fake(fake.proc.communicate())
    assert not fake.started.done()
    error = RuntimeError("product task failed")
    fake.namespace["_bench_startup_finished"](
        SimpleNamespace(cancelled=lambda: False, exception=lambda: error), fake.started)
    assert fake.started.error is error and fake.events == ["spawn", "tree", "error"]


@pytest.mark.parametrize("outcome", ["error", "cancelled", "returned", "already-ready"])
def test_bench_early_task_exit_cannot_leave_readiness_pending_or_replace_success(outcome):
    fake = _bench_readiness_fake_helpers()
    error = RuntimeError("launch failed")
    task = SimpleNamespace(cancelled=lambda: outcome == "cancelled", exception=lambda: (
        error if outcome == "error" else None))
    if outcome == "already-ready":
        fake.started.set_result([11, 12, 13])
    fake.namespace["_bench_startup_finished"](task, fake.started)
    assert fake.started.done()
    if outcome == "error":
        assert fake.started.error is error
    elif outcome == "returned":
        assert isinstance(fake.started.error, AssertionError)
    elif outcome == "already-ready":
        assert fake.started.value == [11, 12, 13] and fake.events == ["ready"]
    else:
        assert fake.events == ["cancel"]


@pytest.mark.parametrize("state", [None, True, False])
def test_bench_readiness_requires_three_known_live_pids_with_bounded_fake_clock(state):
    import ast

    fake = _bench_readiness_fake_helpers()
    # Extract the original readiness helper too; do not execute any native probe.
    node = next(n for n in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
                if getattr(n, "name", "") == "_wait_for_bench_process_tree")
    clock = [0]

    async def sleep(delay):
        clock[0] += delay

    fake.namespace.update(time=SimpleNamespace(monotonic=lambda: clock[0]),
                          asyncio=SimpleNamespace(sleep=sleep),
                          _bench_process_exited=lambda pid: state,
                          _bench_process_state=lambda *args: {"fake": True})
    exec(compile(ast.Module(body=[node], type_ignores=[]), __file__, "exec"), fake.namespace)
    path = SimpleNamespace(is_file=lambda: True, read_text=lambda **kwargs: "11 12 13")
    pending = fake.namespace["_wait_for_bench_process_tree"](path, [], timeout=0.1)
    if state is False:
        assert _bench_finish_fake(pending) == [11, 12, 13] and clock[0] == 0
    else:
        with pytest.raises(TimeoutError):
            _bench_finish_fake(pending)
        assert clock[0] == 0.1


@pytest.mark.parametrize("raw", ["11182 ", "11 12", "not-pids"])
def test_bench_partial_readiness_failure_keeps_original_diagnostics(raw):
    import ast

    fake = _bench_readiness_fake_helpers()
    node = next(n for n in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
                if getattr(n, "name", "") == "_wait_for_bench_process_tree")
    fake.namespace.update(time=SimpleNamespace(monotonic=lambda: 0),
                          _bench_process_exited=lambda pid: False,
                          _bench_process_state=lambda *args: {
                              "pids": {11182: "exited"}, "spawned_returncodes": [-15]})
    exec(compile(ast.Module(body=[node], type_ignores=[]), __file__, "exec"), fake.namespace)
    path = SimpleNamespace(is_file=lambda: True, read_text=lambda **kwargs: raw)
    with pytest.raises(TimeoutError) as failure:
        _bench_finish_fake(fake.namespace["_wait_for_bench_process_tree"](path, [], timeout=0))
    assert f"pids_file={raw!r}" in str(failure.value)
    assert "'spawned_returncodes': [-15]" in str(failure.value)


def test_bench_pid_publication_is_complete_before_it_becomes_visible():
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    fixture = next(n for n in tree.body if getattr(n, "name", "") ==
                   "test_baseline_timeout_and_cancel_kill_cli_tree")
    write = next(n for n in ast.walk(fixture) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "write_text")
    script = ast.parse(ast.literal_eval(write.args[0]))
    publish = next(n for n in script.body if getattr(n, "name", "") == "publish")
    records, observations = {}, []

    class File:
        suffix = ".child"

        def __init__(self, name):
            self.name = name

        def with_suffix(self, suffix):
            return File("pids" + suffix)

        def write_text(self, text, *, encoding):
            assert encoding == "utf-8"
            records[self.name] = ""  # An observer can see a newly opened file before its content.
            observations.append(records.get("pids.child"))
            records[self.name] = text

        def replace(self, target):
            records[target.name] = records.pop(self.name)

    namespace = {}
    exec(compile(ast.Module(body=[publish], type_ignores=[]), __file__, "exec"), namespace)
    namespace["publish"](File("pids.child"), "12 13")
    assert observations == [None] and records == {"pids.child": "12 13"}


def test_scripted_pi_requires_kind_and_keeps_explicit_denial():
    case = {"scripted_pi_answers": [
        {"question_contains": "unrelated", "answer": "first"},
        {"question_contains": "accession", "answer": "declined", "approved": False},
    ]}
    assert bench._scripted_answer(case, "ACCESSION?") is None
    assert bench._scripted_answer(case, "ACCESSION?", "clarify") == (False, "declined")


def test_test_agent_runs_cases_in_order_and_writes_summary(tmp_path, monkeypatch):
    seen = []

    async def fake(case_id, output, engines="real", **_kwargs):
        seen.append(case_id)
        return {"case_id": case_id, "run_id": f"run-{len(seen)}", "rows": [{"engine": "labhq", "status": "done",
                                                 "checks_passed": True, "artifact_exists": True,
                                                 "within_budget": True}]}

    monkeypatch.setattr(bench, "run_case", fake)
    summary = asyncio.run(bench.run_test_agent(tmp_path, engines="mock"))
    assert seen == [case["id"] for case in bench.load_cases()]
    assert summary["passed"] == 5 and summary["failed"] == 0
    assert (tmp_path / "test-agent-summary.md").is_file()
    assert json.loads((tmp_path / "test-agent-summary.json").read_text(encoding="utf-8")) == summary


def test_test_agent_does_not_pass_an_unaccounted_budget(tmp_path, monkeypatch):
    async def fake(case_id, output, engines="real", **_kwargs):
        return {"case_id": case_id, "run_id": "run-unknown", "rows": [{
            "engine": "labhq", "status": "done", "checks_passed": True,
            "artifact_exists": True, "within_budget": None, "unscripted_approvals": 0,
        }]}

    monkeypatch.setattr(bench, "run_case", fake)
    summary = asyncio.run(bench.run_test_agent(tmp_path, engines="mock"))
    assert summary["passed"] == 0 and summary["failed"] == 5


def test_partly_unaccounted_cost_is_neither_within_budget_nor_hidden():
    from labhq.costs import aggregate_costs, classify_cost

    items = {"t1": classify_cost(engine="claude_code", model="opus", usage={}, usage_known=True,
                                 cost_usd=0.3, cost_known=True, task_id="t1"),
             "t2": classify_cost(engine="codex", model=None, usage={}, usage_known=True,
                                 cost_usd=None, cost_known=False, task_id="t2")}
    partial = {"cost_usd": 0.3, "cost_known": False, "cost_summary": aggregate_costs(items)}

    assert bench._within_budget(partial, 1.0) is None  # the unknown task may have spent anything
    assert bench._within_budget(partial, 0.2) is False  # the counted part alone is over the cap
    assert bench._within_budget({"cost_usd": 0.3, "cost_known": False}, 1.0) is None  # older run.json
    assert bench._within_budget({"cost_usd": 0.3, "cost_known": True}, 1.0) is True
    row = {"engine": "labhq", "model": None, "status": "done", "artifact_exists": True, "checks_passed": True,
           "pi_interventions": 0, "cost_usd": 0.3, "cost_budget_ratio": 0.3, "within_budget": None,
           "token_total": 0, "duration_s": 1.0, "cost_summary": partial["cost_summary"]}
    table = bench._comparison_markdown({"case_id": "c", "mode": "real", "budget_usd": 1.0, "rows": [row]})
    assert "| 확인 0.3000 + 비용 모름 1건 | ≥0.300 | 비용 모름 |" in table
    assert "## 비용 내역" in table and "claude_code 확인 $0.3000 · codex 비용 모름 1건" in table
    legacy = {**row, "cost_summary": None, "cost_known": False}
    assert "| 0.3000 + 비용 모름 | ≥0.300 |" in bench._comparison_markdown(
        {"case_id": "c", "mode": "real", "budget_usd": 1.0, "rows": [legacy]})


@pytest.mark.parametrize("model,status", [("gpt-6-astra", "estimated"), ("unpriced-model", "unknown")])
def test_codex_arm_cost_is_estimated_only_with_a_price_row(tmp_path, monkeypatch, model, status):
    from labhq.settings import Settings

    settings = Settings.model_validate({"bench": {"arms": {
        "custom": {"engine": "codex", "model": model, "effort": "max"}}}})
    raw = (b'{"type":"turn.completed","usage":{"input_tokens":100000,"cached_input_tokens":0,'
           b'"cache_write_input_tokens":0,"output_tokens":10000,"reasoning_output_tokens":0}}\n')

    class Process:
        returncode = 0

        async def communicate(self):
            return raw, b""

    async def spawn(*args, **kwargs):
        (tmp_path / "answer.md").write_text("codex answer", encoding="utf-8")
        return Process()

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *args: command)
    monkeypatch.setattr("labhq.adapters.base.child_config_dirs", lambda *args: [tmp_path])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    run = asyncio.run(bench._run_baseline(bench.load_case("public-protein-qc"), "custom", tmp_path,
                                         "real", ["fake-cli"], settings))
    item = run["cost_summary"]
    if status == "estimated":  # 100k x $10 + 10k x $50 per 1M
        assert run["cost_usd"] == pytest.approx(1.5) and run["cost_known"] is True
        assert item["estimated_usd"] == pytest.approx(1.5) and item["prices"][0]["model"] == "gpt-6-astra"
    else:
        assert run["cost_usd"] is None and run["cost_known"] is False and item["unknown_count"] == 1


def test_one_failed_arm_is_scored_and_does_not_stop_the_next(tmp_path, monkeypatch):
    async def fake(case, arm, arm_dir, engines, command, settings):
        if arm == "sonnet-max":
            raise OSError("missing CLI")
        (arm_dir / "answer.md").write_text(case["mock_answer"], encoding="utf-8")
        return {"engine": arm, "status": "done", "pi_interventions": 0, "cost_usd": None,
                "cost_known": False, "usage": {}, "duration_s": 0.01}

    monkeypatch.setattr(bench, "_run_baseline", fake)
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="real",
                                        arms=("sonnet-max", "astra-ultra")))
    assert [row["status"] for row in result["rows"]] == ["failed", "done"]
    assert result["rows"][0]["artifact_exists"] is False
    assert result["rows"][1]["checks_passed"] is True


@pytest.mark.parametrize("kind,question", [
    ("budget", "중복 accession을 제거할까요?"),
    ("tool_permission", "중복 accession을 제거할까요?"),
    ("clarify", "예상하지 않은 질문"),
    (None, "중복 accession을 제거할까요?"),
])
def test_scripted_pi_rejects_unscripted_approvals_and_reports_them(tmp_path, monkeypatch, kind, question):
    case = bench.load_case("public-protein-qc")
    decisions = []

    class Hub:
        def __init__(self):
            self.agents = {}
            self.requests = {}
            self.rounds = SimpleNamespace(directory=tmp_path,
                                          write=lambda rid: {"result": {"status": "done"}})

        async def publish(self, event):
            pass

        def create_request(self, request):
            self.requests["request"] = {"status": "running", "report": case["mock_answer"]}

            async def ask():
                await self.publish({"type": "approval.requested", "data": {
                    "id": "approval", "kind": kind, "summary": question}})

            asyncio.create_task(ask())
            return "request"

        async def resolve_approval(self, aid, approved, note):
            decisions.append((aid, approved, note))
            self.requests["request"]["status"] = "done"

    hub = Hub()

    class Server:
        started = True
        should_exit = False

        def __init__(self, config):
            pass

        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0.01)

    class Runner:
        def __init__(self, settings):
            pass

        async def run_forever(self):
            hub.agents["cso"] = {}
            await asyncio.Future()

        def stop(self):
            pass

    monkeypatch.setattr("labhq.gateway.server.create_app", lambda settings: SimpleNamespace(
        state=SimpleNamespace(hub=hub)))
    monkeypatch.setattr("uvicorn.Config", lambda *args, **kwargs: None)
    monkeypatch.setattr("uvicorn.Server", Server)
    monkeypatch.setattr("labhq.runner.daemon.Runner", Runner)
    result = asyncio.run(bench.run_case(case["id"], tmp_path, engines="real", arms=("labhq",)))
    assert len(decisions) == 1
    assert decisions[0][1] is False
    assert "미스크립트 승인" in decisions[0][2]
    run_dir = tmp_path / case["id"] / result["run_id"]
    run = json.loads((run_dir / "labhq" / "run.json").read_text(encoding="utf-8"))
    assert run["unscripted_approvals"] == 1
    assert result["rows"][0]["unscripted_approvals"] == 1
    assert "미스크립트 승인" in (run_dir / "comparison.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("arm", ["sonnet-max", "astra-ultra"])
def test_baseline_strips_parent_claude_env_after_engine_overrides(tmp_path, monkeypatch, arm):
    from labhq.settings import Settings

    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_HOST_MARKER"):
        monkeypatch.setenv(name, "host-session")
    monkeypatch.setenv("BENCH_KEEP", "keep")
    settings = Settings()
    engine = settings.engines.claude_code if arm == "sonnet-max" else settings.engines.codex
    engine.env = {"CLAUDE_CODE_SESSION_ID": "engine-session", "CLAUDECODE_OVERRIDE": "engine-session",
                  "CLAUDE_CONFIG_DIR": str(tmp_path / "config"), "BENCH_KEEP": "override"}
    child_env = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def spawn(*args, **kwargs):
        child_env.update(kwargs["env"])
        return Process()

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *args: command)
    monkeypatch.setattr("labhq.adapters.base.child_config_dirs", lambda *args: [tmp_path])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(bench._run_baseline(bench.load_case("public-protein-qc"), arm, tmp_path,
                                   "real", ["fake-cli"], settings))
    assert not any(name in child_env for name in (
        "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_HOST_MARKER", "CLAUDECODE_OVERRIDE"))
    assert child_env["CLAUDE_CONFIG_DIR"] == engine.env["CLAUDE_CONFIG_DIR"]
    assert child_env["BENCH_KEEP"] == "override"
    assert os.environ["CLAUDECODE"] == "host-session"


@pytest.mark.parametrize("failure", ["status", "artifact_exists", "checks_passed", "within_budget",
                                     "unscripted_approvals"])
def test_test_agent_cli_exits_one_for_any_failed_case(tmp_path, monkeypatch, capsys, failure):
    from labhq.settings import Settings

    async def fake(case_id, *args, **kwargs):
        row = {"status": "done", "artifact_exists": True, "checks_passed": True,
               "within_budget": True, "unscripted_approvals": 0}
        row[failure] = "failed" if failure == "status" else 1 if failure == "unscripted_approvals" else False
        return {"case_id": case_id, "run_id": "fake-run", "rows": [row]}

    monkeypatch.setattr(bench, "run_case", fake)
    monkeypatch.setattr(Settings, "load", lambda *args: Settings())
    with pytest.raises(SystemExit) as exited:
        main(["bench", "test-agent", "--engines", "mock", "--output", str(tmp_path)])
    assert exited.value.code == 1
    assert "PASS 0 / FAIL 5" in capsys.readouterr().out
    assert json.loads((tmp_path / "test-agent-summary.json").read_text(encoding="utf-8"))["failed"] == 5


def test_test_agent_cli_returns_normally_when_all_cases_pass(tmp_path, monkeypatch, capsys):
    from labhq.settings import Settings

    async def fake(*args, **kwargs):
        return {"passed": 5, "failed": 0}

    monkeypatch.setattr(bench, "run_test_agent", fake)
    monkeypatch.setattr(Settings, "load", lambda *args: Settings())
    main(["bench", "test-agent", "--engines", "mock", "--output", str(tmp_path)])
    assert "PASS 5 / FAIL 0" in capsys.readouterr().out


def test_configured_baselines_use_their_model_effort_and_engine(tmp_path):
    from labhq.settings import Settings

    config = tmp_path / "bench.yaml"
    config.write_text(
        "bench:\n  arms:\n"
        "    old-opus: {engine: claude_code, model: claude-opus-5-5, effort: max}\n"
        "    custom-codex: {engine: codex, model: gpt-5.6-sol, effort: high}\n"
        "  staff_model: {}\n", encoding="utf-8")
    settings = Settings.load(str(config))
    commands = bench._real_commands(bench.load_case("public-protein-qc"), tmp_path, settings=settings)
    assert list(commands) == ["labhq", "old-opus", "custom-codex"]
    assert "claude-opus-5-5" in commands["old-opus"]
    assert commands["old-opus"][commands["old-opus"].index("--effort") + 1] == "max"
    assert commands["custom-codex"][commands["custom-codex"].index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="high"' in commands["custom-codex"]
    assert commands["labhq"][1:3] == ["--config", str(config.resolve())]
    assert "--staff-model" not in commands["labhq"]


@pytest.mark.parametrize("engine", ["claude_code", "codex"])
def test_custom_arm_parses_the_correct_cli_output(tmp_path, monkeypatch, engine):
    from labhq.settings import Settings

    settings = Settings.model_validate({"bench": {"arms": {
        "custom": {"engine": engine, "model": "configured-model", "effort": "max"}}}})
    raw = (b'{"type":"result","result":"claude answer","total_cost_usd":0.12, '
           b'"usage":{"input_tokens":3}}\n' if engine == "claude_code" else
           b'{"type":"turn.completed","usage":{"output_tokens":7}}\n')

    class Process:
        returncode = 0

        async def communicate(self):
            return raw, b""

    async def spawn(*args, **kwargs):
        if engine == "codex":
            (tmp_path / "answer.md").write_text("codex answer", encoding="utf-8")
        return Process()

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *args: command)
    monkeypatch.setattr("labhq.adapters.base.child_config_dirs", lambda *args: [tmp_path])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    run = asyncio.run(bench._run_baseline(bench.load_case("public-protein-qc"), "custom", tmp_path,
                                         "real", ["fake-cli"], settings))
    assert run["engine"] == "custom" and run["model"] == "configured-model" and run["effort"] == "max"
    assert run["usage"] == ({"input_tokens": 3} if engine == "claude_code" else {"output_tokens": 7})
    assert run["cost_usd"] == (0.12 if engine == "claude_code" else None)
    assert (tmp_path / "answer.md").read_text().strip() == (
        "claude answer" if engine == "claude_code" else "codex answer")


def test_selected_arms_accumulate_and_report_latest_without_rerunning(tmp_path, monkeypatch, capsys):
    from labhq.settings import Settings

    first = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock", arms=("sol-ultra",)))
    first_run = tmp_path / "public-protein-qc" / first["run_id"]
    assert sorted(path.name for path in first_run.iterdir() if path.is_dir()) == ["sol-ultra"]
    second = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock",
                                       arms=("sonnet-max", "astra-ultra")))
    combined = bench.report_case("public-protein-qc", tmp_path, "mock")
    assert [row["engine"] for row in combined["rows"]] == ["sonnet-max", "sol-ultra", "astra-ultra"]
    assert [row["run_id"] for row in combined["rows"]] == [second["run_id"], first["run_id"], second["run_id"]]
    settings = Settings()
    settings.bench.arms["sol-ultra"].model = "changed-model"
    settings.bench.arms["sol-ultra"].effort = "high"
    third = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock",
                                      arms=("sol-ultra",), settings=settings))
    monkeypatch.setattr(bench, "run_case", lambda *args, **kwargs: pytest.fail("report must not run arms"))
    main(["bench", "report", "public-protein-qc", "--engines", "mock", "--output", str(tmp_path)])
    printed = capsys.readouterr().out
    assert "changed-model | high" in printed and "sonnet | max" in printed
    saved = json.loads((tmp_path / "public-protein-qc" / "comparison.json").read_text(encoding="utf-8"))
    assert saved["rows"][1]["run_id"] == third["run_id"]
    assert saved["rows"][0]["run_id"] == second["run_id"]
    original = json.loads((first_run / "sol-ultra" / "score.json").read_text(encoding="utf-8"))
    assert original["model"] == "gpt-5.6-sol" and original["effort"] == "ultra"
    with pytest.raises(ValueError, match="no real"):
        bench.report_case("public-protein-qc", tmp_path, "real")


@pytest.mark.parametrize("override,target", [(None, "sonnet"), (["opus=haiku"], "haiku")])
def test_labhq_staff_model_replacement_is_claude_only_and_preserves_source(tmp_path, override, target):
    import shutil
    import yaml
    from labhq.settings import Settings

    source = tmp_path / "source-agents"
    shutil.copytree(bench.REPO / "agents", source)
    codex_file = source / "core" / "engineer.yaml"
    spec = yaml.safe_load(codex_file.read_text(encoding="utf-8"))
    assert spec["engine"] == "codex"
    spec["model"] = "opus"  # Even a matching Codex model must not be replaced.
    codex_file.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*.yaml")}
    settings = Settings()
    settings.runner.agents_dir = str(source)
    settings.bench.staff_model = bench._staff_mapping(settings, override)
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path / "out", engines="mock",
                                       arms=("labhq",), settings=settings))
    copied = tmp_path / "out" / "public-protein-qc" / result["run_id"] / "labhq" / "state" / "agents"
    for relative, original in before.items():
        assert (source / relative).read_bytes() == original
        old = yaml.safe_load(original)
        new = yaml.safe_load((copied / relative).read_text(encoding="utf-8"))
        if old.get("engine") == "claude_code" and old.get("model") == "opus":
            assert new == {**old, "model": target}
        else:
            assert (copied / relative).read_bytes() == original
    row = result["rows"][0]
    assert row["staff_model"] == {"opus": target}
    assert next(staff for staff in row["staff_models"] if staff["id"] == "cso")["model"] == target
    assert next(staff for staff in row["staff_models"] if staff["id"] == "engineer")["model"] == "opus"
    assert settings.recruit.contract_model == "sonnet"


@pytest.mark.parametrize("selection", ["unknown", "../escape", "sol-ultra,sol-ultra", "sol-ultra,", ""])
def test_invalid_arm_selection_fails_before_writes(tmp_path, selection):
    with pytest.raises(SystemExit) as exc:
        main(["bench", "run", "public-protein-qc", "--arms", selection, "--output", str(tmp_path)])
    assert exc.value.code == 2 and not any(tmp_path.iterdir())


def test_selected_cli_dry_run_and_test_agent_forward_configuration(tmp_path, monkeypatch, capsys):
    main(["bench", "run", "public-protein-qc", "--dry-run", "--arms", "sol-ultra", "--output", str(tmp_path)])
    output = capsys.readouterr().out
    assert "[sol-ultra]" in output and "[labhq]" not in output and "[sonnet-max]" not in output
    seen = []

    async def fake(case_id, output, engines, arms, settings):
        seen.append((arms, settings.bench.staff_model))
        return {"case_id": case_id, "run_id": "selected", "rows": [{"status": "done",
                "artifact_exists": True, "checks_passed": True, "within_budget": True}]}

    monkeypatch.setattr(bench, "run_case", fake)
    main(["bench", "test-agent", "--arms", "labhq,sol-ultra", "--staff-model", "opus=haiku",
          "--engines", "mock", "--output", str(tmp_path)])
    assert seen == [(("labhq", "sol-ultra"), {"opus": "haiku"})] * 5


def test_dry_run_loads_custom_arm_settings_and_staff_overrides(tmp_path, capsys):
    config = tmp_path / "bench.yaml"
    config.write_text("bench:\n  arms:\n"
                      "    opus-max: {engine: claude_code, model: opus, effort: max}\n"
                      "  staff_model: {opus: sonnet}\n", encoding="utf-8")
    main(["--config", str(config), "bench", "run", "public-protein-qc", "--dry-run",
          "--staff-model", "opus=haiku", "--output", str(tmp_path / "results")])
    output = capsys.readouterr().out
    assert "[opus-max]" in output and "--model opus --effort max" in output
    assert "--staff-model opus=haiku" in output
    assert "[sonnet-max]" not in output and not (tmp_path / "results").exists()


def test_real_and_mock_reports_do_not_mix_same_arm_results(tmp_path, monkeypatch):
    mock = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock", arms=("sol-ultra",)))

    async def fake(case, arm, arm_dir, engines, command, settings):
        (arm_dir / "answer.md").write_text(case["mock_answer"], encoding="utf-8")
        return {"engine": arm, "status": "done", "usage": {}, "cost_usd": None,
                "cost_known": False, "duration_s": 0.1}

    monkeypatch.setattr(bench, "_run_baseline", fake)
    real = asyncio.run(bench.run_case("public-protein-qc", tmp_path, arms=("sol-ultra",)))
    assert bench.report_case("public-protein-qc", tmp_path, "mock")["rows"][0]["run_id"] == mock["run_id"]
    assert bench.report_case("public-protein-qc", tmp_path, "real")["rows"][0]["run_id"] == real["run_id"]


@pytest.mark.parametrize("mapping", ["opus", "opus=", "=sonnet", "opus=sonnet=bad"])
def test_invalid_staff_mapping_dry_run_is_rejected(tmp_path, mapping):
    with pytest.raises(SystemExit) as exc:
        main(["bench", "run", "public-protein-qc", "--dry-run", "--staff-model", mapping,
              "--output", str(tmp_path)])
    assert exc.value.code == 2 and not any(tmp_path.iterdir())




def test_baseline_commands_expand_configured_env_paths(tmp_path, monkeypatch):
    # Local configs write engine bins as ${LOCALAPPDATA}/... the way staff adapters accept; unexpanded it was WinError 2.
    from labhq.settings import Settings
    monkeypatch.setenv("LABHQ_TEST_BIN_DIR", str(tmp_path))
    settings = Settings()
    settings.engines.codex.bin = "${LABHQ_TEST_BIN_DIR}/codex.exe"
    settings.engines.claude_code.bin = "${LABHQ_TEST_BIN_DIR}/claude.exe"
    commands = bench._real_commands(bench.load_cases()[0], tmp_path, settings=settings)
    baselines = [c for arm, c in commands.items() if arm != "labhq"]
    assert baselines and all(c[0].startswith(str(tmp_path)) for c in baselines)
    assert all("${LABHQ_TEST_BIN_DIR}" not in c[0] for c in baselines)
