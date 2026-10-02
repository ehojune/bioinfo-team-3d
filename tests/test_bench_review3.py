import asyncio
import json
import zipfile
from types import SimpleNamespace

import pytest

from labhq import bench
from labhq.cli import main
from labhq.settings import Settings
from scripts.check_bench_wheel import check_wheel


def test_wheel_checker_rejects_the_old_wheel_without_bench_data(tmp_path):
    wheel = tmp_path / "old.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("labhq/bench.py", "")
    with pytest.raises(ValueError, match="wheel lacks benchmark data"):
        check_wheel(wheel)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    case = bench.load_case("public-penguins-qc")
    events = []
    decisions = []
    waits = []

    class Hub:
        agents = {}
        requests = {}
        rounds = SimpleNamespace(directory=tmp_path,
                                 write=lambda rid: {"result": {"status": "done"}})

        async def publish(self, event):
            pass

        def create_request(self, request):
            self.requests["request"] = {"status": "running", "report": case["mock_answer"],
                                        "cost_usd": 0.55, "cost_known": True}

            async def ask():
                for index, data in enumerate(events):
                    await self.publish({"type": "approval.requested", "data": {
                        "id": str(index), **data}})
                    while len(decisions) <= index:
                        await asyncio.sleep(0.001)
                self.requests["request"]["status"] = "done"

            asyncio.create_task(ask())
            return "request"

        async def resolve_approval(self, aid, approved, note):
            decisions.append((approved, note))

    hub = Hub()
    case["_harness_hub"] = hub

    class Server:
        started = True
        should_exit = False

        def __init__(self, config):
            pass

        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0.001)

    class Runner:
        def __init__(self, settings):
            pass

        async def run_forever(self):
            hub.agents["cso"] = {}
            await asyncio.Future()

        def stop(self):
            pass

    original_until = bench._until

    async def until(predicate, timeout, message):
        waits.append((timeout, message))
        await original_until(predicate, timeout, message)

    monkeypatch.setattr(bench, "_until", until)
    monkeypatch.setattr("labhq.gateway.server.create_app", lambda settings: SimpleNamespace(
        state=SimpleNamespace(hub=hub)))
    monkeypatch.setattr("uvicorn.Config", lambda *args, **kwargs: None)
    monkeypatch.setattr("uvicorn.Server", Server)
    monkeypatch.setattr("labhq.runner.daemon.Runner", Runner)
    monkeypatch.setattr(bench, "load_case", lambda _: case)
    return case, events, decisions, waits


@pytest.mark.parametrize("case_timeout", [None, 1800])
def test_labhq_uses_case_timeout_or_runner_default(tmp_path, harness, case_timeout):
    case, _, _, waits = harness
    settings = Settings()
    settings.runner.task_timeout_s = 21600
    if case_timeout is not None:
        case["timeout_s"] = case_timeout
    asyncio.run(bench._run_labhq(case, tmp_path, "real", settings))
    timeout, message = next(pair for pair in waits if "request timed out" in pair[1])
    assert timeout == (case_timeout or 21600)
    assert str(case_timeout or 21600) in message


def test_labhq_benchmark_waits_through_quota_hold(tmp_path, harness):
    case, _, _, _ = harness
    hub = case.pop("_harness_hub")

    def create_request(_request):
        hub.requests["request"] = {"status": "waiting_quota", "report": case["mock_answer"],
                                   "cost_usd": 0.55, "cost_known": True}

        async def finish_after_reset():
            await asyncio.sleep(0.02)
            hub.requests["request"]["status"] = "done"

        asyncio.create_task(finish_after_reset())
        return "request"

    hub.create_request = create_request
    run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings()))
    assert run["status"] == "done"


@pytest.mark.parametrize("case_timeout", [True, False])
def test_labhq_timeout_actually_interrupts_a_stalled_request(tmp_path, harness, monkeypatch, case_timeout):
    case, _, _, _ = harness
    settings = Settings()
    settings.runner.task_timeout_s = 0.02 if not case_timeout else 100
    if case_timeout:
        case["timeout_s"] = 0.02
    original_until = bench._until

    async def until(predicate, timeout, message):
        if "request timed out" in message:
            predicate = lambda: False
        await original_until(predicate, timeout, message)

    monkeypatch.setattr(bench, "_until", until)
    with pytest.raises(TimeoutError, match="timed out after 0.02s"):
        asyncio.run(bench._run_labhq(case, tmp_path, "real", settings))


@pytest.mark.parametrize("multiplier,expected,unscripted", [(None, False, 1), (3, True, 0)])
def test_budget_policy_default_and_opt_in_record_cost_overhead(tmp_path, harness, multiplier, expected, unscripted):
    case, events, decisions, _ = harness
    events.append({"kind": "budget", "detail": {"requested_budget_usd": 1.0}})
    result = asyncio.run(bench.run_case(case["id"], tmp_path, arms=("labhq",),
                                       approve_budget_up_to=multiplier))
    assert decisions[0][0] is expected
    row = result["rows"][0]
    assert row["budget_approvals"] == int(expected)
    assert row["unscripted_approvals"] == unscripted
    assert row["cost_budget_ratio"] == pytest.approx(1.1)
    assert row["within_budget"] is False
    run_dir = tmp_path / case["id"] / result["run_id"] / "labhq"
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["budget_approvals"] == int(expected)
    assert run["approve_budget_up_to"] == multiplier
    report = bench._comparison_markdown(bench.report_case(case["id"], tmp_path))
    assert "예산 승인(회)" in report and "최종 비용/예산(배)" in report
    assert "0.5500 | 1.100 | FAIL" in report


def test_budget_multiplier_is_always_relative_to_original_case_budget(tmp_path, harness):
    case, events, decisions, _ = harness
    events.extend({"kind": "budget", "detail": {"requested_budget_usd": amount}}
                  for amount in [1.0, 1.5, 2.0])
    run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings(), 3))
    assert [approved for approved, _ in decisions] == [True, True, False]
    assert run["budget_approvals"] == 2
    assert run["unscripted_approvals"] == 0


@pytest.mark.parametrize("data", [
    {"kind": "budget"},
    {"kind": "budget", "detail": {"requested_budget_usd": "1.0"}},
    *({"kind": "budget", "detail": {"requested_budget_usd": amount}}
      for amount in [True, -1, 0, float("nan"), float("inf"), 1.50001]),
    {"kind": "tool", "detail": {"requested_budget_usd": 0.1}},
])
def test_budget_policy_fails_closed_for_invalid_or_other_approvals(tmp_path, harness, data):
    case, events, decisions, _ = harness
    events.append({"summary": "中복 accession", **data})
    run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings(), 3))
    assert decisions[0][0] is False
    assert run["budget_approvals"] == 0


@pytest.mark.parametrize("value", ["0", "-1", "0.9", "nan", "inf"])
def test_invalid_budget_multiplier_rejected_before_writing(tmp_path, value):
    with pytest.raises(SystemExit) as exc:
        main(["bench", "run", "public-penguins-qc", "--output", str(tmp_path),
              "--approve-budget-up-to", value])
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["run", "test-agent"])
def test_cli_forwards_explicit_budget_policy(tmp_path, monkeypatch, command):
    seen = []

    async def fake(*args, **kwargs):
        seen.append(kwargs["approve_budget_up_to"])
        return {"passed": 5, "failed": 0}

    monkeypatch.setattr(bench, "run_case" if command == "run" else "run_test_agent", fake)
    monkeypatch.setattr(bench, "report_case", lambda *args: {"case_id": "fake", "rows": []})
    args = ["bench", command]
    if command == "run":
        args += ["public-penguins-qc"]
    main([*args, "--arms", "labhq", "--output", str(tmp_path), "--approve-budget-up-to", "3"])
    assert seen == [3]


def test_test_agent_forwards_budget_policy_to_each_case(tmp_path, monkeypatch):
    seen = []

    async def fake(case_id, *args, **kwargs):
        seen.append(kwargs["approve_budget_up_to"])
        return {"run_id": "fake", "rows": [{"status": "done", "artifact_exists": True,
                "checks_passed": True, "within_budget": False, "budget_approvals": 1}]}

    monkeypatch.setattr(bench, "run_case", fake)
    summary = asyncio.run(bench.run_test_agent(tmp_path, arms=("labhq",), approve_budget_up_to=3))
    assert seen == [3] * 5
    assert summary["failed"] == 5


@pytest.mark.parametrize("tool,path,approved", [
    ("Write", "{workdir}/outputs/answer.md", True),
    ("Edit", "outputs/answer.md", True),
    ("NotebookEdit", "{workdir}/outputs/nb.ipynb", True),
    ("Write", "{elsewhere}/answer.md", False),
    ("Write", "outputs/../../answer.md", False),
    ("Write", "{workdir}/.claude/settings.json", False),
    ("Bash", "{workdir}/outputs/answer.md", False),
    ("Write", None, False),
])
def test_scripted_pi_approves_only_writes_inside_the_task_workdir(tmp_path, harness, monkeypatch, tool, path, approved):
    """#219: a tool_permission ask for a Write/Edit inside that task's own workdir is a harness rule, not unscripted."""
    case, events, decisions, _ = harness
    workdir = tmp_path / "runs" / "task_w_analyst"
    (workdir / "outputs").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()

    from labhq.runner import daemon
    monkeypatch.setattr(daemon.Runner, "workspaces", {"task_w": SimpleNamespace(dir=workdir)}, raising=False)
    detail = {"tool_name": tool, "input": "{}"}
    if path is not None:
        detail["path"] = path.format(workdir=workdir.as_posix(), elsewhere=(tmp_path / "elsewhere").as_posix())
    events.append({"kind": "tool_permission", "task_id": "task_w", "summary": f"{tool}: write", "detail": detail})
    events.append({"kind": "tool_permission", "task_id": "task_other", "summary": "Write: other task",
                   "detail": {"tool_name": "Write", "path": (workdir / "outputs" / "x.md").as_posix()}})
    run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings()))
    assert [ok for ok, _ in decisions] == [approved, False]
    assert run["workdir_write_approvals"] == int(approved)
    assert run["unscripted_approvals"] == 2 - int(approved)
