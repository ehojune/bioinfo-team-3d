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


def test_bench_list_and_dry_run_print_three_arms_without_running(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("labhq.cli.Settings.load", lambda *_: (_ for _ in ()).throw(AssertionError("no config")))
    main(["bench", "list"])
    listed = capsys.readouterr().out
    assert "inco-kras-g12c" in listed and "public-penguins-qc" in listed

    called = False

    async def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("dry-run must not execute")

    monkeypatch.setattr(bench, "run_case", forbidden)
    main(["bench", "run", "inco-kras-g12c", "--dry-run", "--output", str(tmp_path)])
    output = capsys.readouterr().out
    assert "labhq" in output
    assert "claude -p" in output and "claude-opus-5-5" in output
    assert "codex exec" in output and "gpt-6-astra" in output
    assert "--ephemeral" in output and "--ignore-user-config" in output
    assert "--disallowedTools" in output and "SendMessage" in output
    assert not called and not any(tmp_path.iterdir())


def test_mock_case_runs_all_arms_scores_and_records_case_id(tmp_path):
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="mock"))
    assert result["case_id"] == "public-protein-qc"
    assert [row["engine"] for row in result["rows"]] == ["labhq", "opus-5.5", "gpt-6-astra"]
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
    assert commands["opus-5.5"][commands["opus-5.5"].index("-p") + 1] == round_record["request"]["text"]
    assert commands["gpt-6-astra"][-1] == round_record["request"]["text"]
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
    assert commands["opus-5.5"][commands["opus-5.5"].index("-p") + 1] == prompt
    assert commands["gpt-6-astra"][-1] == prompt
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


@pytest.mark.parametrize("arm", ["opus-5.5", "gpt-6-astra"])
@pytest.mark.parametrize("stop", ["case-timeout", "runner-timeout", "cancel"])
def test_baseline_timeout_and_cancel_kill_cli_tree(tmp_path, monkeypatch, arm, stop):
    from labhq.settings import Settings

    pids_file = tmp_path / "pids.txt"
    script = tmp_path / "stalled_cli.py"
    script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "pids = pathlib.Path(sys.argv[1])\n"
        "if len(sys.argv) > 2:\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "    pids.with_suffix('.child').write_text(f'{os.getpid()} {child.pid}')\n"
        "else:\n"
        "    child = subprocess.Popen([sys.executable, __file__, str(pids), 'child'])\n"
        "    while not pids.with_suffix('.child').is_file(): time.sleep(0.01)\n"
        "    pids.write_text(f'{os.getpid()} ' + pids.with_suffix('.child').read_text())\n"
        "time.sleep(60)\n", encoding="utf-8")
    case = bench.load_case("public-protein-qc")
    settings = Settings()
    settings.runner.task_timeout_s = 30 if stop != "runner-timeout" else 1
    if stop == "case-timeout":
        case["timeout_s"] = 1
    monkeypatch.setattr("labhq.adapters.base.child_config_dirs", lambda *args: [tmp_path])
    processes = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        proc = await original_spawn(*args, **kwargs)
        if args[0] == sys.executable:
            processes.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def exercise():
        task = asyncio.create_task(bench._run_baseline(
            case, arm, tmp_path, "real", [sys.executable, str(script), str(pids_file)], settings))
        pids = []
        started = time.monotonic()
        try:
            await bench._until(pids_file.is_file, 5, "fake CLI did not start")
            pids = list(map(int, pids_file.read_text().split()))
            assert len(pids) == 3 and all(not _bench_process_exited(pid) for pid in pids)
            if stop == "cancel":
                task.cancel()  # asyncio.run translates Ctrl+C into cancellation of the main task.
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), 4)
            else:
                run = await asyncio.wait_for(asyncio.shield(task), 4)
                assert run["status"] == "failed"
                assert run["error"] == "timeout after 1s"
                assert json.loads((tmp_path / "run.json").read_text())["error"] == run["error"]
            assert time.monotonic() - started < 5
            await bench._until(lambda: all(_bench_process_exited(pid) for pid in pids),
                               2, "baseline left a live descendant")
        finally:
            # Keep the deliberately failing pre-fix regression from leaking its fake CLI.
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
                await asyncio.wait_for(proc.wait(), 3)

    asyncio.run(exercise())


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


def test_one_failed_arm_is_scored_and_does_not_stop_the_next(tmp_path, monkeypatch):
    async def fake(case, arm, arm_dir, engines, command, settings):
        if arm == "opus-5.5":
            raise OSError("missing CLI")
        (arm_dir / "answer.md").write_text(case["mock_answer"], encoding="utf-8")
        return {"engine": arm, "status": "done", "pi_interventions": 0, "cost_usd": None,
                "cost_known": False, "usage": {}, "duration_s": 0.01}

    monkeypatch.setattr(bench, "_run_baseline", fake)
    result = asyncio.run(bench.run_case("public-protein-qc", tmp_path, engines="real",
                                        arms=("opus-5.5", "gpt-6-astra")))
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


@pytest.mark.parametrize("arm", ["opus-5.5", "gpt-6-astra"])
def test_baseline_strips_parent_claude_env_after_engine_overrides(tmp_path, monkeypatch, arm):
    from labhq.settings import Settings

    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_HOST_MARKER"):
        monkeypatch.setenv(name, "host-session")
    monkeypatch.setenv("BENCH_KEEP", "keep")
    settings = Settings()
    engine = settings.engines.claude_code if arm == "opus-5.5" else settings.engines.codex
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
