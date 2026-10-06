"""Environment failure kind and the error signature table (#35 stage 2). Fakes only: no real CLI, no network."""
import json
import sys
from pathlib import Path

import pytest
import yaml

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext, RunState
from labhq.facilities import signatures as env
from labhq.gateway.server import create_app
from labhq.cli import main
from labhq.integrations.rounds import build_record
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator, environment_problem, failure_kind, general_report_warnings
from labhq.settings import Settings

from tests.test_cso import FakeHub, result

EXAMPLES = yaml.safe_load((Path(__file__).parent / "fixtures" / "environment_signatures.yaml")
                          .read_text(encoding="utf-8"))


def _failed(error=None, **kwargs):
    return TaskResult(task_id="t", agent_id="worker", ok=False, error=error, **kwargs)


# ---------------- the table ----------------

def test_every_signature_has_a_real_example_and_the_fields_the_lane_asked_for():
    table = env.signatures()
    assert {sig.id for sig in table} == {example["id"] for example in EXAMPLES["match"]}
    assert {"python_module_missing", "command_not_found", "r_package_missing", "disk_full",
            "network_name_resolution", "docker_daemon_down", "codex_sandbox_setup"} <= {sig.id for sig in table}
    raw = yaml.safe_load(env.SIGNATURE_FILE.read_text(encoding="utf-8"))
    assert all({"id", "engine", "pattern", "cause", "hint", "fix"} <= set(entry) for entry in raw)
    fixes = {entry["id"]: entry["fix"] for entry in raw if entry["fix"]}
    assert fixes == {"python_module_missing": "install_python_package", "r_package_missing": "install_r_package",
                     "disk_full": "clear_workspace_cache"}


@pytest.mark.parametrize("example", EXAMPLES["match"], ids=lambda e: e["id"])
def test_real_wording_matches_its_signature(example):
    found = env.match(example.get("engine", ""), example["text"], error_kind=example.get("error_kind"))
    assert found is not None and found.id == example["id"], example["text"]


@pytest.mark.parametrize("example", EXAMPLES["no_match"], ids=lambda e: e["text"][:40] or "error_kind")
def test_quoted_phrases_and_ordinary_failures_are_not_environment(example):
    assert env.match(example.get("engine", ""), example["text"], error_kind=example.get("error_kind")) is None


def test_codex_signature_is_the_codex_setup_error_text():
    from labhq.adapters import codex as codex_mod
    found = env.match("codex", f"{codex_mod.ELEVATED_SETUP_ERROR} Codex: sandbox setup required")
    assert found and found.id == "codex_sandbox_setup"
    assert env.match("claude_code", codex_mod.ELEVATED_SETUP_ERROR) is None


@pytest.mark.parametrize("entry,message", [
    ({"id": "x", "engine": "any", "pattern": "a", "cause": "c", "hint": "h"}, "missing"),
    ({"id": "x", "engine": "any", "pattern": "a", "cause": "c", "hint": "h", "fix": None, "extra": 1}, "unknown"),
    ({"id": "x", "engine": "nope", "pattern": "a", "cause": "c", "hint": "h", "fix": None}, "engine"),
    ({"id": "x", "engine": "any", "pattern": "(", "cause": "c", "hint": "h", "fix": None}, "compile"),
    ({"id": "X y", "engine": "any", "pattern": "a", "cause": "c", "hint": "h", "fix": None}, "snake_case"),
])
def test_a_malformed_table_is_refused(tmp_path, entry, message):
    path = tmp_path / "signatures.yaml"
    path.write_text(yaml.safe_dump([entry]), encoding="utf-8")
    with pytest.raises(env.SignatureError, match=message):
        env.load(path)


def test_duplicate_ids_are_refused(tmp_path):
    entry = {"id": "x", "engine": "any", "pattern": "a", "cause": "c", "hint": "h", "fix": None}
    path = tmp_path / "signatures.yaml"
    path.write_text(yaml.safe_dump([entry, entry]), encoding="utf-8")
    with pytest.raises(env.SignatureError, match="unique"):
        env.load(path)


def test_a_result_field_from_another_runner_is_bounded():
    record = TaskResult(task_id="t", agent_id="a", ok=False, environment={
        "id": "disk_full", "cause": "c" * 1000, "hint": "h", "source": "elsewhere", "extra": "dropped"}).environment
    assert record["source"] == "command" and len(record["cause"]) == 300 and "extra" not in record
    assert TaskResult(task_id="t", agent_id="a", ok=False, environment={"cause": "no id"}).environment is None
    assert "environment" not in TaskResult(task_id="t", agent_id="a", ok=False).model_dump()


# ---------------- failure_kind ----------------

STDERR = env.by_id("disk_full").record("stderr")
COMMAND = env.by_id("python_module_missing").record("command")
DOCKER_STDERR = env.by_id("docker_daemon_down").record("stderr")


@pytest.mark.parametrize("outcome,kind", [
    (_failed("exit 127: bash: samtools: command not found"), "environment"),
    (_failed("exit 1: Error in library(DESeq2) : there is no package called ‘DESeq2’"), "environment"),
    (_failed("curl: (6) Could not resolve host: ftp.ncbi.nlm.nih.gov"), "environment"),
    (_failed("anything", error_kind="sandbox_setup_required"), "environment"),
    (_failed("stream disconnected", environment=STDERR), "environment"),
    (_failed("exit 1: crashed", environment=COMMAND), "environment"),
    (_failed("incomplete: missing outputs: de.tsv", environment=COMMAND), "environment"),
    # the step's own error decides first; a worked-around command does not override it
    (_failed("exit 1: HTTP 503 overloaded", environment=COMMAND), "transient"),
    (_failed("permission denied", environment=COMMAND), "terminal"),
    (_failed("cancelled", environment=COMMAND), "terminal"),
    (_failed("cancelled", environment=STDERR), "terminal"),
    # a stderr-only signature is older than an explicit transient signal in the error (PR #447 review)
    (_failed("timeout after 600s", environment=DOCKER_STDERR), "transient"),
    (_failed("exit 1: HTTP 429 too many requests", environment=DOCKER_STDERR), "transient"),
    (_failed("runner restarted", environment=DOCKER_STDERR), "transient"),
    # ... and than an explicit terminal cause in the error (PR #447 review)
    (_failed("permission denied", environment=STDERR), "terminal"),
    (_failed("exit 1: approval required for Bash", environment=STDERR), "terminal"),
    (_failed("budget exceeded: $2.00", environment=STDERR), "terminal"),
    # an engine limit is its own cause, whatever command the agent last tripped on
    (_failed("error_max_turns", error_kind="error_max_turns", environment=COMMAND), "terminal"),
    (_failed("error_max_turns", error_kind="error_max_turns", environment=STDERR), "terminal"),
    (TaskResult(task_id="t", agent_id="a", ok=True, text="done", environment=COMMAND), None),
])
def test_environment_kind(outcome, kind):
    assert failure_kind(outcome, "codex") == kind


def test_an_explicit_terminal_cause_hides_an_older_stderr_signature():
    """stderr held "No space left on device" earlier; the step then ended on a permission error (PR #447 review)."""
    outcome = _failed("permission denied", environment=STDERR)
    assert failure_kind(outcome, "claude_code") != "environment"
    assert environment_problem(outcome, "claude_code") is None


def test_login_and_quota_are_decided_before_environment():
    login = _failed("Not logged in · Please run /login", environment=STDERR)
    assert failure_kind(login, "claude_code") == "login"
    quota = _failed("exit 1: bash: x: command not found", quota_reset_at=1.0)
    assert failure_kind(quota, "codex") == "quota"


def test_environment_problem_reads_stored_dicts():
    stored = _failed("exit 127: bash: samtools: command not found").model_dump(mode="json")
    found = environment_problem(stored)
    assert found["id"] == "command_not_found" and found["source"] == "error"
    assert env.problem_text(found) == f"환경 문제: {found['cause']} — {found['hint']}"
    assert environment_problem({"ok": False, "error": "no ids"}) is None
    assert environment_problem(_failed("exit 1: crashed")) is None


# ---------------- orchestrator ----------------

@pytest.mark.asyncio
async def test_environment_failure_is_not_retried_and_carries_the_signature():
    async def dispatch(task):
        return result(task, ok=False, error="exit 1: ModuleNotFoundError: No module named 'scanpy'")

    hub = FakeHub(dispatch)
    res = await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert len(hub.calls) == 1  # transient would have retried step_max_attempts times
    assert res.environment["id"] == "python_module_missing"
    assert not [e for e in hub.events if e["type"] == "request.step_retry"]


@pytest.mark.asyncio
async def test_step_done_and_reports_show_the_environment_problem():
    async def dispatch(task):
        if task.meta.get("step_id") == "A":
            return result(task, ok=False, error="exit 1: docker: Cannot connect to the Docker daemon at "
                                                 "unix:///var/run/docker.sock. Is the docker daemon running?")
        # gave up after a failed command, exited 0 without its declared output
        return result(task, text="could not finish", environment=COMMAND)

    hub = FakeHub(dispatch)
    orch = Orchestrator(hub)
    steps = [{"id": "A", "agent_id": "worker", "instruction": "first", "depends_on": []},
             {"id": "B", "agent_id": "worker", "instruction": "second", "depends_on": [], "outputs": ["de.tsv"]}]
    results = {}
    await orch.run_dag("r", "question", steps, results)
    done = {e["data"]["step_id"]: e["data"] for e in hub.events if e["type"] == "request.step_done"}
    assert done["A"]["environment"]["id"] == "docker_daemon_down"
    assert done["B"]["environment"]["id"] == "python_module_missing"
    report = Orchestrator.report_results(steps, results, 2000)
    assert "Next: 환경 문제: Docker 데몬에 연결하지 못했습니다 — " in report
    # a failed command's output is weaker than the missing output itself: Next asks for the output (PR #447 review)
    assert results["B"].missing_outputs == ["de.tsv"]
    assert "Next: 환경 문제: Python 모듈" not in report and "Next: produce the missing outputs" in report
    warnings = general_report_warnings(steps, results)
    assert "- A: 환경 문제: Docker 데몬" in warnings and "- B: 환경 문제: Python 모듈" in warnings


def test_skipped_dependents_are_not_environment_problems(tmp_path):
    upstream = _failed("exit 1: OSError: [Errno 28] No space left on device")
    skipped = _failed(f"skipped: upstream s1: {upstream.error}")
    assert environment_problem(upstream)["id"] == "disk_full" and environment_problem(skipped) is None
    steps = [{"id": "s1", "agent_id": "worker", "instruction": "x", "depends_on": []},
             {"id": "s2", "agent_id": "worker", "instruction": "y", "depends_on": ["s1"]}]
    warnings = general_report_warnings(steps, {"s1": upstream, "s2": skipped})
    assert "- s1: 환경 문제" in warnings and "- s2: 환경 문제" not in warnings
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["r1"] = {"id": "r1", "text": "분석", "mode": "orchestrate", "status": "failed", "created_at": 1.0,
                          "plan": {"steps": steps},
                          "results": {"s1": upstream.model_dump(mode="json"), "s2": skipped.model_dump(mode="json")}}
    assert list(hub.request_summary(hub.requests["r1"])["step_environment"]) == ["s1"]
    details = hub.request_step_details("r1", hub.requests["r1"])
    assert "environment" in details["s1"] and "environment" not in details["s2"]


@pytest.mark.asyncio
async def test_ordinary_failures_keep_their_events_unchanged():
    async def dispatch(task):
        return result(task, ok=False, error="exit 1: crashed")

    hub = FakeHub(dispatch)
    results = {}
    await Orchestrator(hub).run_dag("r", "q", [{"id": "A", "agent_id": "worker", "instruction": "x",
                                                "depends_on": []}], results)
    done = [e["data"] for e in hub.events if e["type"] == "request.step_done"]
    assert done and "environment" not in done[0]
    assert "environment" not in results["A"].model_dump()


# ---------------- gateway summary, snapshot, round record, CLI status ----------------

def test_status_summary_snapshot_and_round_record(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    failed = _failed("exit 1: OSError: [Errno 28] No space left on device").model_dump(mode="json")
    hub.requests["r1"] = {"id": "r1", "text": "분석", "mode": "orchestrate", "status": "failed",
                          "created_at": 1.0, "plan": {"steps": [{"id": "s1", "agent_id": "worker"},
                                                                 {"id": "s2", "agent_id": "worker"}]},
                          "results": {"s1": failed, "s2": _failed("exit 1: crashed").model_dump(mode="json")}}
    summary = hub.request_summary(hub.requests["r1"])
    assert list(summary["step_environment"]) == ["s1"]
    assert summary["step_environment"]["s1"]["id"] == "disk_full"
    details = hub.request_step_details("r1", hub.requests["r1"])
    assert details["s1"]["environment"]["id"] == "disk_full" and "environment" not in details["s2"]
    record = build_record(hub, "r1")
    steps = {step["id"]: step for step in record["steps"]}
    assert steps["s1"]["failure_kind"] == "environment" and steps["s1"]["environment"]["id"] == "disk_full"
    assert "environment" not in steps["s2"]
    hub.requests["r2"] = {"id": "r2", "text": "ok", "status": "done", "created_at": 2.0, "results": {}}
    assert "step_environment" not in hub.request_summary(hub.requests["r2"])


def test_cli_status_shows_environment_problems(monkeypatch, capsys):
    problem = env.by_id("disk_full").record("error")

    def api(_settings, _method, path):
        if path == "/api/health":
            return {"runners": []}
        if path.startswith("/api/requests?status=running"):
            return [{"id": "r1", "text": "분석", "step_progress": {"done": 1, "total": 2,
                     "steps": {"s1": "failed", "s2": "running"}}, "step_environment": {"s1": problem}}]
        if path.startswith("/api/requests?status=all"):
            return [{"id": "r1", "status": "running", "step_environment": {"s1": problem}},
                    {"id": "r0", "status": "failed", "step_environment": {"a": problem}}]
        return []

    monkeypatch.setattr("labhq.cli._api", api)
    main(["status"])
    output = capsys.readouterr().out
    line = env.problem_text(problem)
    assert f"s1: failed — {line}" in output
    assert "환경 문제로 멈춘 단계: 1" in output and f"r0 a: {line}" in output
    assert output.count(line) == 2  # the running request is not listed twice


# ---------------- runner side ----------------

def _ctx(tmp_path, settings, events, engine=Engine.codex):
    async def emit(kind, data):
        events.append((kind, data))

    agent = AgentSpec(id="engineer", name="Engineer", role="test", engine=engine, builtin_mcp=[])
    workdir = tmp_path / "workdir"
    workdir.mkdir(exist_ok=True)
    return RunContext(task=Task(agent_id=agent.id, prompt="x", request_id="req1"), agent=agent, workdir=workdir,
                      settings=settings, mcp_servers=[], env={}, emit=emit, prompt="x")


def _command(output, exit_code):
    return {"type": "item.completed", "item": {"type": "command_execution", "command": "python de.py",
                                               "exit_code": exit_code, "aggregated_output": output}}


async def _run_codex(tmp_path, events, exit_code=1):
    stream = tmp_path / "stream.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in [{"type": "thread.started", "thread_id": "t1"}, *events])
                      + "\n", encoding="utf-8")
    script = tmp_path / "fake_codex.py"
    script.write_text("import sys\n"
                      f"sys.stdout.write(open({str(stream)!r}, encoding='utf-8').read()); sys.stdout.flush()\n"
                      f"sys.exit({exit_code})\n", encoding="utf-8")
    settings = Settings()
    settings.engines.codex.bin = sys.executable
    settings.engines.codex.prefix_args = [str(script)]
    (tmp_path / "codex-home").mkdir(exist_ok=True)
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    return await get_adapter(Engine.codex, settings).run(_ctx(tmp_path, settings, []))


@pytest.mark.asyncio
async def test_runner_reads_failed_command_output(tmp_path):
    traceback = ("Traceback (most recent call last):\n  File \"de.py\", line 1, in <module>\n"
                 "ModuleNotFoundError: No module named 'pydeseq2'\n")
    res = await _run_codex(tmp_path, [_command(traceback, 1)])
    assert not res.ok and res.environment == {**env.by_id("python_module_missing").record("command"),
                                               "package": "pydeseq2"}
    assert failure_kind(res, "codex") == "environment"


@pytest.mark.asyncio
async def test_a_missing_engine_binary_is_command_not_found(tmp_path):
    settings = Settings()
    settings.engines.codex.bin = str(tmp_path / "no-such-codex")
    (tmp_path / "codex-home").mkdir()
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    res = await get_adapter(Engine.codex, settings).run(_ctx(tmp_path, settings, []))
    assert not res.ok and res.error.startswith("executable not found: ")
    assert failure_kind(res, "codex") == "environment"
    found = environment_problem(res, "codex")
    assert found["id"] == "command_not_found" and found["source"] == "error"


@pytest.mark.asyncio
async def test_successful_commands_that_print_the_phrase_are_not_evidence(tmp_path):
    grep = _command("logs/a.log:3:ModuleNotFoundError: No module named 'x'\nbash: x: command not found\n", 0)
    res = await _run_codex(tmp_path, [grep])
    assert not res.ok and res.environment is None
    assert failure_kind(res, "codex") == "terminal"


def test_claude_tool_result_blocks_are_kept_for_signatures(tmp_path):
    import asyncio
    settings = Settings()
    adapter = get_adapter(Engine.claude_code, settings)
    state = RunState()
    line = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                       "content": [{"type": "text", "text": "Exit code 127\n/usr/bin/bash: line 1: salmon: "
                                                            "command not found"}]}]}})
    asyncio.run(adapter.handle_line(line, state, _ctx(tmp_path, settings, [], Engine.claude_code)))
    found = env.scan_run("claude_code", [], state.failed_outputs)
    assert found and found["id"] == "command_not_found" and found["source"] == "command"


@pytest.mark.asyncio
async def test_a_failed_command_the_agent_got_past_is_not_evidence(tmp_path):
    traceback = "ModuleNotFoundError: No module named 'scanpy'\n"
    res = await _run_codex(tmp_path, [_command(traceback, 1), _command("Successfully installed scanpy\n", 0)])
    assert not res.ok and res.environment is None
    assert failure_kind(res, "codex") == "terminal"


@pytest.mark.asyncio
async def test_only_the_last_failed_command_counts(tmp_path):
    later = _command("KeyError: 'condition'\n", 1)
    res = await _run_codex(tmp_path, [_command("ModuleNotFoundError: No module named 'scanpy'\n", 1), later])
    assert res.environment is None


def _claude_lines(adapter, state, ctx, events):
    import asyncio
    for event in events:
        asyncio.run(adapter.handle_line(json.dumps(event), state, ctx))


def _claude_tool(tool_id, name):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name,
                                                          "input": {}}]}}


def _claude_result(tool_id, text, is_error):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                     "is_error": is_error, "content": text}]}}


def test_claude_shell_success_clears_the_failed_output_but_other_tools_do_not(tmp_path):
    settings = Settings()
    adapter = get_adapter(Engine.claude_code, settings)
    ctx = _ctx(tmp_path, settings, [], Engine.claude_code)
    failed = [_claude_tool("t1", "Bash"), _claude_result("t1", "ModuleNotFoundError: No module named 'x'", True)]
    state = RunState()
    _claude_lines(adapter, state, ctx, [*failed, _claude_tool("t2", "Write"), _claude_result("t2", "ok", False)])
    assert env.scan_run("claude_code", [], state.failed_outputs)["id"] == "python_module_missing"
    state = RunState()
    _claude_lines(adapter, state, ctx, [*failed, _claude_tool("t3", "Bash"), _claude_result("t3", "done", False)])
    assert env.scan_run("claude_code", [], state.failed_outputs) is None


def test_gemini_and_antigravity_shell_success_clears_the_failed_output(tmp_path):
    import asyncio
    settings = Settings()
    gemini = get_adapter(Engine.gemini, settings)
    ctx = _ctx(tmp_path, settings, [], Engine.gemini)
    state = RunState()
    for event in [{"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "a", "parameters": {}},
                  {"type": "tool_result", "tool_id": "a", "status": "error", "output": "bash: samtools: command not found"},
                  {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "b", "parameters": {}},
                  {"type": "tool_result", "tool_id": "b", "status": "success", "output": "ok"}]:
        asyncio.run(gemini.handle_line(json.dumps(event), state, ctx))
    assert env.scan_run("gemini", [], state.failed_outputs) is None
    agy = get_adapter(Engine.antigravity, settings)
    ctx = _ctx(tmp_path, settings, [], Engine.antigravity)
    state = RunState()

    def step(state_name, **info):
        return {"event": "step_update", "step_update": {"step_type": "tool", "state": state_name,
                                                         "tool_name": "run_command", "tool_info": info}}
    asyncio.run(agy.handle_line(json.dumps(step("ERROR", error="bash: samtools: command not found")), state, ctx))
    assert env.scan_run("antigravity", [], state.failed_outputs)["id"] == "command_not_found"
    asyncio.run(agy.handle_line(json.dumps(step("DONE", output="ok")), state, ctx))
    assert env.scan_run("antigravity", [], state.failed_outputs) is None


def test_antigravity_error_objects_keep_their_message(tmp_path):
    """Real agy sends tool_info.error as {"type", "message"}: the message is matched, not the dict (PR #447 review)."""
    import asyncio
    settings = Settings()
    agy = get_adapter(Engine.antigravity, settings)
    events = []
    ctx = _ctx(tmp_path, settings, events, Engine.antigravity)
    state = RunState()
    error = {"type": "TOOL_ERROR", "message": "bash: samtools: command not found"}
    line = {"event": "step_update", "step_update": {"step_type": "tool", "state": "ERROR", "tool_name": "run_command",
                                                     "tool_info": {"name": "run_command", "error": error}}}
    asyncio.run(agy.handle_line(json.dumps(line), state, ctx))
    assert list(state.failed_outputs) == ["bash: samtools: command not found"]
    assert env.scan_run("antigravity", [], state.failed_outputs)["id"] == "command_not_found"
    assert ("agent.tool_error", {"text": "bash: samtools: command not found"}) in events
    # the recorded real stream: the kept text is the message itself
    real = Path(__file__).parent / "fixtures" / "real" / "antigravity" / "agy_tool_allowed.jsonl"
    failed = next(json.loads(row) for row in real.read_text(encoding="utf-8").splitlines()
                  if '"state": "ERROR"' in row)
    state = RunState()
    asyncio.run(agy.handle_line(json.dumps(failed), state, ctx))
    assert list(state.failed_outputs) == [failed["step_update"]["tool_info"]["error"]["message"]]
