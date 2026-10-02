"""#330: a CLI that sent its final turn event but never exits must not hold the step until task_timeout_s."""

import json
import sys
import time
from pathlib import Path

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.settings import Settings

EVENTS = {
    Engine.codex: [
        {"type": "thread.started", "thread_id": "thr-330"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "문헌 정리 끝"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ],
    Engine.claude_code: [
        {"type": "system", "subtype": "init", "session_id": "sess-330", "model": "claude-opus"},
        {"type": "result", "subtype": "success", "result": "문헌 정리 끝", "session_id": "sess-330",
         "total_cost_usd": 0.1},
    ],
}
TOKEN = "fixture-broker-330"


def _fake(tmp: Path, engine: Engine, sleep_s: float, exit_code: int = 0) -> Path:
    events = tmp / "events.jsonl"
    events.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in EVENTS[engine]) + "\n", encoding="utf-8")
    script = tmp / "fake_cli.py"
    script.write_text(
        "# coding: utf-8\nimport sys, json, os, pathlib, time\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        f"pathlib.Path({str(tmp / 'argv.json')!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"pathlib.Path({str(tmp / 'token.txt')!r}).write_text(os.environ.get('LABHQ_BROKER_TOKEN', ''))\n"
        f"sys.stdout.write(open({str(events)!r}, encoding='utf-8').read())\n"
        "sys.stdout.flush()\n"
        f"time.sleep({sleep_s})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return script


async def _run(tmp: Path, engine: Engine, *, sleep_s: float, grace_s: float, exit_code: int = 0):
    s = Settings()
    s.runner.exit_grace_s = grace_s
    s.runner.task_timeout_s = 120
    getattr(s.engines, engine.value).bin = sys.executable
    getattr(s.engines, engine.value).prefix_args = [str(_fake(tmp, engine, sleep_s, exit_code))]
    if engine == Engine.codex:  # hermetic: the host's ~/.codex/AGENTS.md would refuse the staff session
        (tmp / "codex-home").mkdir()
        s.engines.codex.env = {"CODEX_HOME": str(tmp / "codex-home")}
    agent = AgentSpec(id="lit_scout", name="L", role="r", engine=engine, model="m", tools=["Read"],
                      system_prompt="ROLE")
    events = []

    async def emit(t, d):
        events.append((t, d))

    wd = tmp / "wd"
    wd.mkdir()
    env = {"LABHQ_BROKER_URL": "http://127.0.0.1:9", "LABHQ_BROKER_TOKEN": TOKEN, "LABHQ_TASK_ID": "t1"}
    mcp = [McpServerSpec(name="labhq_ask", command="python", args=["-m", "labhq.tools.ask_mcp"],
                         env={**env, "PYTHONPATH": "/repo"})]
    ctx = RunContext(task=Task(agent_id="lit_scout", prompt="do it"), agent=agent, workdir=wd, settings=s,
                     mcp_servers=mcp, env=dict(env), emit=emit, prompt="do it")
    started = time.monotonic()
    res = await get_adapter(engine, s).run(ctx)
    return res, events, time.monotonic() - started


def _kill_logs(events):
    return [d["text"] for t, d in events if t == "agent.log" and "종료하지 않아" in str(d.get("text"))]


@pytest.mark.parametrize("engine", [Engine.codex, Engine.claude_code])
async def test_process_left_after_final_event_is_ended_and_result_kept(tmp_path, engine):
    res, events, elapsed = await _run(tmp_path, engine, sleep_s=60, grace_s=1)
    assert elapsed < 1 + 15, elapsed
    assert res.ok, res.error
    assert res.text == "문헌 정리 끝"
    assert res.session_id in ("thr-330", "sess-330")
    logs = _kill_logs(events)
    assert len(logs) == 1 and "1s" in logs[0]


async def test_a_killed_codex_keeps_its_last_message_not_every_message(tmp_path, monkeypatch):
    # #330 review: Codex writes -o only after its (hanging) shutdown, so the guard's result must be the turn's last
    # agent message, the way Codex itself picks its final answer, not the preamble joined to it.
    monkeypatch.setitem(EVENTS, Engine.codex, [
        {"type": "thread.started", "thread_id": "thr-330"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "PubMed부터 찾겠습니다."}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "최종 답"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ])
    res, events, _ = await _run(tmp_path, Engine.codex, sleep_s=60, grace_s=1)
    assert res.ok, res.error
    assert res.text == "최종 답"
    assert len(_kill_logs(events)) == 1


@pytest.mark.parametrize("engine", [Engine.codex, Engine.claude_code])
async def test_process_that_exits_within_grace_is_left_alone(tmp_path, engine):
    res, events, _ = await _run(tmp_path, engine, sleep_s=0.3, grace_s=30)
    assert res.ok and res.text == "문헌 정리 끝"
    assert _kill_logs(events) == []


async def test_normal_exit_code_still_counts(tmp_path):
    res, events, _ = await _run(tmp_path, Engine.codex, sleep_s=0, grace_s=30, exit_code=3)
    assert not res.ok and res.error.startswith("exit 3")
    assert _kill_logs(events) == []


async def test_codex_broker_token_is_not_on_the_command_line(tmp_path):
    res, _, _ = await _run(tmp_path, Engine.codex, sleep_s=0, grace_s=30)
    assert res.ok
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert not any(TOKEN in arg for arg in argv)
    assert (tmp_path / "token.txt").read_text() == TOKEN  # Codex holds it in its env and forwards it by name
    forwarded = [arg for arg in argv if arg.startswith("mcp_servers.labhq_ask.env_vars=")]
    assert forwarded and "LABHQ_BROKER_TOKEN" in forwarded[0]
    assert 'mcp_servers.labhq_ask.env={PYTHONPATH = "/repo"}' in argv  # a value Codex does not hold stays inline
