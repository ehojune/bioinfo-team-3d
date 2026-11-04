"""#1045: a CLI that ends with no result leaves its exit code, stderr end, run time and MCP status as evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.runner.memory_limit import JOB_ACTIVE
from labhq.settings import Settings

INIT = {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-opus",
        "mcp_servers": [{"name": "labhq_annot", "status": "failed"}, {"name": "labhq_ask", "status": "connected"}]}
RESULT = {"type": "result", "subtype": "success", "result": "끝", "session_id": "s1", "total_cost_usd": 0.1}


async def run_fake(tmp: Path, stdout_events: list[dict], stderr_lines: list[str], exit_code: int,
                   env: dict[str, str] | None = None, mcp_env: dict[str, str] | None = None):
    script = tmp / "fake_cli.py"
    script.write_text(
        "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nsys.stderr.reconfigure(encoding='utf-8')\n"
        + "".join(f"sys.stderr.write({line + chr(10)!r})\n" for line in stderr_lines)
        + "".join(f"sys.stdout.write({json.dumps(event, ensure_ascii=False) + chr(10)!r})\n" for event in stdout_events)
        + f"sys.exit({exit_code})\n", encoding="utf-8")
    settings = Settings()
    settings.engines.claude_code.bin = sys.executable
    settings.engines.claude_code.prefix_args = [str(script)]
    agent = AgentSpec(id="lit_scout", name="L", role="r", engine=Engine.claude_code, model="m", tools=["Read"],
                      system_prompt="ROLE")
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    workdir = tmp / "wd"
    workdir.mkdir()
    env = {"LABHQ_BROKER_URL": "http://127.0.0.1:9", "LABHQ_BROKER_TOKEN": "tok", "LABHQ_TASK_ID": "t1", **(env or {})}
    mcp = [McpServerSpec(name="labhq_ask", command="python", args=["-m", "labhq.tools.ask_mcp"], env={**env, **(mcp_env or {})})]
    ctx = RunContext(task=Task(agent_id="lit_scout", prompt="do it"), agent=agent, workdir=workdir, settings=settings,
                     mcp_servers=mcp, env=dict(env), emit=emit, prompt="do it")
    return await get_adapter(Engine.claude_code, settings).run(ctx), events, workdir


async def test_a_silent_early_exit_records_code_stderr_time_and_mcp(tmp_path):
    secret = "sk-ant-api03-" + "a" * 40
    res, events, workdir = await run_fake(
        tmp_path, [INIT], [f"{JOB_ACTIVE} pid=1 kill_on_close=1", "boom: could not start", f"token {secret}"], 0)
    assert not res.ok and res.exit_code == 0
    assert "empty CLI stream" in res.error and "exit 0" in res.error and "labhq_annot=failed" in res.error
    assert "boom: could not start" in res.error and JOB_ACTIVE not in res.error and secret not in res.error
    evidence = json.loads((workdir / ".labhq" / "exit_evidence.json").read_text(encoding="utf-8"))
    assert evidence["exit_code"] == 0 and evidence["elapsed_s"] >= 0
    assert evidence["mcp"] == [{"name": "labhq_annot", "status": "failed"},
                               {"name": "labhq_ask", "status": "connected"}]
    assert JOB_ACTIVE not in json.dumps(evidence) and secret not in json.dumps(evidence)
    assert any(kind == "agent.log" and "CLI 종료 증거" in str(data.get("text")) for kind, data in events)


async def test_a_nonzero_exit_without_output_keeps_its_code(tmp_path):
    res, _, workdir = await run_fake(tmp_path, [], ["fatal: bad flag"], 3)
    assert not res.ok and res.exit_code == 3 and "exit 3" in res.error and "fatal: bad flag" in res.error
    assert json.loads((workdir / ".labhq" / "exit_evidence.json").read_text(encoding="utf-8"))["exit_code"] == 3


async def test_a_normal_run_writes_no_evidence(tmp_path):
    res, events, workdir = await run_fake(tmp_path, [INIT, RESULT], ["noise"], 0)
    assert res.ok and "CLI 종료 증거" not in (res.error or "")
    assert not (workdir / ".labhq" / "exit_evidence.json").exists()
    assert not any("CLI 종료 증거" in str(data.get("text")) for kind, data in events if kind == "agent.log")


@pytest.mark.parametrize("stderr", [[], ["only a note"]])
async def test_no_stderr_is_stated_not_invented(tmp_path, stderr):
    res, _, _ = await run_fake(tmp_path, [], stderr, 0)
    assert not res.ok and ("(출력 없음)" in res.error) == (not stderr)


async def test_a_secret_the_run_really_received_is_masked_even_without_a_known_pattern(tmp_path):
    odd = "zz-not-a-known-pattern-9f8e7d6c"
    res, events, workdir = await run_fake(
        tmp_path, [], [f"LABHQ_BROKER_TOKEN=tok-value-123456789 dump {odd}"], 0,
        env={"MY_SERVICE_TOKEN": odd, "LABHQ_BROKER_TOKEN": "tok-value-123456789"})
    text = res.error + (workdir / ".labhq" / "exit_evidence.json").read_text(encoding="utf-8") + str(events)
    assert odd not in text and "tok-value-123456789" not in text


async def test_a_multi_line_secret_is_masked_across_lines(tmp_path):
    pem = chr(10).join(["-----BEGIN KEY-----", "bodybodybody0123456789", "morebodymorebody987654", "-----END KEY-----"])
    dump = chr(10).join(["start", *pem.splitlines(), "end"])
    res, events, workdir = await run_fake(tmp_path, [], dump.splitlines(), 0, env={"MY_PRIVATE_KEY": pem})
    text = res.error + (workdir / ".labhq" / "exit_evidence.json").read_text(encoding="utf-8") + str(events)
    assert "bodybodybody0123456789" not in text and "morebodymorebody987654" not in text


async def test_a_placeholder_mcp_secret_is_masked_in_its_expanded_form(tmp_path):
    real = "real-mcp-value-abcdef123456"
    res, events, workdir = await run_fake(
        tmp_path, [], [f"mcp dump {real}"], 0, env={"MCP_VALUE": real}, mcp_env={"SERVICE_LOGIN": "${MCP_VALUE}"})
    text = res.error + (workdir / ".labhq" / "exit_evidence.json").read_text(encoding="utf-8") + str(events)
    assert real not in text


async def test_a_short_secret_value_is_masked_too(tmp_path):
    res, events, workdir = await run_fake(tmp_path, [], ["login failed for pw9x7"], 0, env={"DB_PASSWORD": "pw9x7"})
    text = res.error + (workdir / ".labhq" / "exit_evidence.json").read_text(encoding="utf-8") + str(events)
    assert "pw9x7" not in text and "login failed for" in text
