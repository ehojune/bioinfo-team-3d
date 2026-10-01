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


async def _wait_for_bench_processes(pids, timeout=10):
    deadline = time.monotonic() + timeout
    while remaining := [pid for pid in pids if not _bench_process_exited(pid)]:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"baseline left live pids: {remaining}")
        await asyncio.sleep(0.05)


def test_wait_for_bench_processes_reports_remaining_pids(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_bench_process_exited", lambda _pid: False)
    with pytest.raises(TimeoutError, match=r"live pids: \[11, 12\]"):
        asyncio.run(_wait_for_bench_processes([11, 12], timeout=0))


@pytest.mark.parametrize("arm", ["sonnet-max", "astra-ultra"])
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
            await _wait_for_bench_processes(pids)
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
