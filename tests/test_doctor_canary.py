import json
import sys
from pathlib import Path

import pytest

from labhq import cli, doctor
from labhq.models import Engine
from labhq.settings import Settings


def _settings(tmp_path: Path, engine: str = "claude_code", model: str = "sonnet") -> Settings:
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "worker.yaml").write_text(
        f"id: worker\nname: Worker\nrole: test\nengine: {engine}\nmodel: {model}\n",
        encoding="utf-8",
    )
    return Settings.model_validate({
        "runner": {"state_dir": str(tmp_path / "state"),
                   "workspace_root": str(tmp_path / "runs"),
                   "agents_dir": str(agents.parent)},
        "gateway": {"state_dir": str(tmp_path / "gateway")},
        "hpc": {"scheduler": "none"},
    })


def _doctor_without_host_tools(settings: Settings, monkeypatch, version: str) -> dict:
    executable = Path(settings.runner.state_dir).parent / "engine.exe"
    executable.touch()
    monkeypatch.setattr(doctor, "_resolve_command", lambda command, env, engine: [str(executable)])
    monkeypatch.setattr(doctor, "_probe", lambda argv, env: (0, version))
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: None)
    return doctor.collect(settings)


def test_doctor_warns_for_invalid_claude_model_id(tmp_path, monkeypatch):
    settings = _settings(tmp_path, model="not a model")
    result = _doctor_without_host_tools(settings, monkeypatch, "2.1.289")

    row = next(row for row in result["checks"] if row["name"] == "worker model")
    assert row["status"] == "warn"
    assert "not a model" in row["detail"]


@pytest.mark.parametrize("model,status", [("gpt-6-sol", "ok"), ("gpt-missing", "warn")])
def test_doctor_checks_codex_model_cache_when_present(tmp_path, monkeypatch, model, status):
    settings = _settings(tmp_path, engine="codex", model=model)
    home = tmp_path / "codex-staff"
    home.mkdir()
    (home / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-6-sol"}, {"slug": "gpt-6-astra"}]}),
        encoding="utf-8",
    )
    settings.engines.codex.env = {"CODEX_HOME": str(home)}

    result = _doctor_without_host_tools(settings, monkeypatch, "codex-cli 0.160.1")

    row = next(row for row in result["checks"] if row["name"] == "worker model")
    assert row["status"] == status
    assert "models_cache.json" in row["detail"]


def test_doctor_skips_codex_model_list_when_cache_is_absent(tmp_path, monkeypatch):
    settings = _settings(tmp_path, engine="codex", model="gpt-any")
    home = tmp_path / "codex-staff"
    home.mkdir()
    settings.engines.codex.env = {"CODEX_HOME": str(home)}

    result = _doctor_without_host_tools(settings, monkeypatch, "codex-cli 0.160.1")

    row = next(row for row in result["checks"] if row["name"] == "worker model")
    assert row["status"] == "skip"


def test_doctor_fails_cli_below_the_supported_minimum(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    result = _doctor_without_host_tools(settings, monkeypatch, "Claude Code 2.0.0")

    row = next(row for row in result["checks"] if row["name"] == "claude_code")
    assert row["status"] == "fail"
    assert "2.1.282" in row["detail"]
    assert "stream-json" in row["hint"]


def test_doctor_warns_when_cli_version_cannot_be_parsed(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    result = _doctor_without_host_tools(settings, monkeypatch, "Claude Code development build")

    row = next(row for row in result["checks"] if row["name"] == "claude_code")
    assert row["status"] == "warn"


def _fake_canary_cli(tmp_path: Path, engine: Engine) -> Path:
    script = tmp_path / f"fake_{engine.value}.py"
    script.write_text(
        """import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
calls = root / 'calls.jsonl'
with calls.open('a', encoding='utf-8') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
engine = sys.argv[1]
args = sys.argv[2:]
if engine == 'claude_code':
    prompt = args[args.index('-p') + 1]
    session = 'claude-session'
    print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session,
                      'model': 'claude-sonnet-4-5'}), flush=True)
    if 'CANCEL' in prompt:
        time.sleep(60)
    else:
        print(json.dumps({'type': 'result', 'subtype': 'success', 'result': 'LABHQ_CANARY_OK',
                          'session_id': session, 'total_cost_usd': 0.01,
                          'usage': {'input_tokens': 4, 'output_tokens': 2},
                          'modelUsage': {'claude-sonnet-4-5': {'inputTokens': 4, 'outputTokens': 2}}}), flush=True)
else:
    prompt = args[-1]
    session = 'codex-session'
    print(json.dumps({'type': 'thread.started', 'thread_id': session, 'model_id': 'gpt-6-sol'}), flush=True)
    if 'CANCEL' in prompt:
        time.sleep(60)
    else:
        print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
                                                              'text': 'LABHQ_CANARY_OK'}}), flush=True)
        print(json.dumps({'type': 'turn.completed',
                          'usage': {'input_tokens': 4, 'output_tokens': 2}}), flush=True)
""",
        encoding="utf-8",
    )
    return script


@pytest.mark.parametrize("engine,model", [(Engine.claude_code, "sonnet"), (Engine.codex, "gpt-6-sol")])
def test_canary_fake_cli_reports_each_check_and_uses_resume(tmp_path, engine, model):
    settings = _settings(tmp_path, engine=engine.value, model=model)
    fake = _fake_canary_cli(tmp_path, engine)
    configured = getattr(settings.engines, engine.value)
    configured.bin = sys.executable
    configured.prefix_args = [str(fake), engine.value]
    if engine == Engine.codex:
        home = tmp_path / "codex-home"
        home.mkdir()
        settings.engines.codex.env = {"CODEX_HOME": str(home)}
        settings.engines.codex.windows_sandbox = ""

    rows = doctor.run_canaries(settings, "worker")

    assert [row["name"].split(": ", 1)[1] for row in rows] == [
        "result", "cost/tokens", "session", "resume", "cancel"]
    assert {row["status"] for row in rows} == {"ok"}
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 3
    if engine == Engine.claude_code:
        assert "--resume" in calls[1] and calls[1][calls[1].index("--resume") + 1] == "claude-session"
    else:
        assert "resume" in calls[1] and calls[1][calls[1].index("resume") + 1] == "codex-session"


def test_canary_fake_cli_marks_every_missing_item_failed(tmp_path):
    settings = _settings(tmp_path)
    fake = tmp_path / "empty.py"
    fake.write_text("# no event stream\n", encoding="utf-8")
    settings.engines.claude_code.bin = sys.executable
    settings.engines.claude_code.prefix_args = [str(fake)]

    rows = doctor.run_canaries(settings, "worker")

    assert len(rows) == 5
    assert {row["status"] for row in rows} == {"fail"}
    assert all(row["detail"].startswith("fail;") for row in rows)


def test_doctor_without_canary_never_enters_the_paid_call_path(tmp_path, monkeypatch):
    config = tmp_path / "labhq.yaml"
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "mock.yaml").write_text("id: mock\nname: Mock\nrole: test\nengine: mock\n", encoding="utf-8")
    config.write_text(
        "runner:\n"
        f"  agents_dir: {agents.parent.as_posix()}\n"
        f"  state_dir: {(tmp_path / 'state').as_posix()}\n"
        f"  workspace_root: {(tmp_path / 'runs').as_posix()}\n"
        "gateway:\n"
        f"  state_dir: {(tmp_path / 'gateway').as_posix()}\n"
        "hpc: {scheduler: none}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor, "run_canaries", lambda *args: (_ for _ in ()).throw(AssertionError("paid path")))
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: None)

    cli.main(["-c", str(config), "doctor"])


def test_doctor_cli_adds_explicit_canary_rows_to_json(tmp_path, monkeypatch):
    config = tmp_path / "labhq.yaml"
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "mock.yaml").write_text("id: mock\nname: Mock\nrole: test\nengine: mock\n", encoding="utf-8")
    state = tmp_path / "state"
    config.write_text(
        "runner:\n"
        f"  agents_dir: {agents.parent.as_posix()}\n"
        f"  state_dir: {state.as_posix()}\n"
        f"  workspace_root: {(tmp_path / 'runs').as_posix()}\n"
        "gateway:\n"
        f"  state_dir: {(tmp_path / 'gateway').as_posix()}\n"
        "hpc: {scheduler: none}\n",
        encoding="utf-8",
    )
    called = []
    row = {"group": "canary", "name": "mock: result", "status": "ok", "detail": "pass; fake", "hint": ""}
    monkeypatch.setattr(doctor, "run_canaries", lambda settings, selector: called.append(selector) or [row])
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: None)

    cli.main(["-c", str(config), "doctor", "--canary", "mock", "--json"])

    manifest = json.loads((state / "capabilities.json").read_text(encoding="utf-8"))
    assert called == ["mock"]
    assert row in manifest["checks"]
    assert manifest["summary"]["ok"] >= 1
