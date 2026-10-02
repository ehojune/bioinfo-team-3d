"""The #276 real-CLI probe, driven here by the fake CLI so its wiring is checked without a model."""

import json
import sys
from pathlib import Path

import pytest

import scripts.probe_inbound_mcp as probe
from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings

FAKE_CLI = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_client.py"


async def _emit(_kind, _data):
    pass


def _fake_settings(tmp_path: Path, engine: str) -> Settings:
    settings = Settings()
    binary = getattr(settings.engines, engine)
    binary.bin = sys.executable
    binary.prefix_args = [str(FAKE_CLI), engine, "labhq_ask"]
    if engine == "codex":  # hermetic: the host's ~/.codex/AGENTS.md would refuse the staff session
        (tmp_path / "codex-home").mkdir()
        settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    return settings


@pytest.mark.parametrize("argv,message", [
    (["claude_code", "--output-dir", str(probe.ROOT / "out")], "outside the public repository"),
    (["claude_code", "--output-dir", "{tmp}", "--delay", "60"], "longer than --approval-timeout"),
])
def test_probe_refuses_repo_output_and_a_delay_within_the_approval_wait(tmp_path, monkeypatch, capsys, argv, message):
    monkeypatch.setattr("sys.argv", ["probe_inbound_mcp.py", *[a.replace("{tmp}", str(tmp_path)) for a in argv]])
    with pytest.raises(SystemExit):
        probe.main()
    assert message in capsys.readouterr().err
    assert not (probe.ROOT / "out").exists()


@pytest.mark.parametrize("engine", ["claude_code", "codex"])
def test_probe_runs_one_delayed_call_through_the_adapter(tmp_path, monkeypatch, capsys, engine):
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, *_: _fake_settings(tmp_path, engine)))
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["probe_inbound_mcp.py", engine, "--output-dir", str(out), "--delay", "2",
                                     "--approval-timeout", "1", "--no-sender"])
    probe.main()

    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["ok"] and summary["text_is_marker"] and summary["error"] is None
    assert summary["server_calls"] == 1 and summary["slow_answer_calls"] == 1 and summary["tool_errors"] == 0
    assert summary["labhq_mcp_timeout_s"] == 1200 + 120  # labhq_ask's longest wait, not the 1 s approval timeout
    assert 2 <= summary["elapsed_s"] < 60
    assert "inbound" not in summary and "sender" not in summary
    assert (out / "staff.raw.jsonl").read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("inbound,expected", [("accept", "accept"), ("unset", None)])
def test_probe_controls_change_only_the_inbound_value(tmp_path, inbound, expected):
    settings = Settings()
    agent = AgentSpec(id="probe", name="Probe", role="test", engine=Engine.claude_code, builtin_mcp=[])
    ctx = RunContext(task=Task(agent_id="probe", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[], env={}, emit=_emit, prompt="x")
    adapter = get_adapter(Engine.claude_code, settings)
    staff = adapter.build_command(ctx)
    probe._control(adapter, inbound)
    control = adapter.build_command(ctx)

    generated = json.loads(staff[staff.index("--settings") + 1])
    changed = json.loads(control[control.index("--settings") + 1])
    assert generated.pop("crossSessionInbound") == "refuse"
    assert changed.pop("crossSessionInbound", None) == expected
    assert changed == generated
    i = staff.index("--settings")
    assert control[:i + 1] == staff[:i + 1] and control[i + 2:] == staff[i + 2:]


def _line(event: dict) -> str:
    return json.dumps(event)


def test_probe_reads_the_senders_refused_notice_and_tool_results():
    """Event shapes from Claude 2.1.282 stream-json: the receipt for a refused message is a system notice, separate
    from the SendMessage tool result (which says queued either way)."""
    queued = '{"success":true,"message":"probe -> labhq-inbound-probe-x (queued there)"}'
    tool_result = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": queued}]}]}}
    notice = {"type": "system", "subtype": "informational", "level": "warning",
              "content": "Cross-session message refused (recipient: uds:pipe-x). It was not delivered."}
    other = {"type": "system", "subtype": "informational", "content": "Something else"}
    stream = "\n".join([_line({"type": "system", "subtype": "init"}), "not json", _line(["a", "list"]),
                        _line({"type": "user", "message": {"role": "user", "content": "plain text"}}),
                        _line(tool_result), _line(other), _line(notice)])

    refused = probe._sender_result(stream)
    accepted = probe._sender_result("\n".join([_line(tool_result), _line(other)]))

    assert refused == {"tool_results": [json.dumps([{"type": "text", "text": queued}])], "refused_notice": True}
    assert accepted["refused_notice"] is False and accepted["tool_results"] == refused["tool_results"]
