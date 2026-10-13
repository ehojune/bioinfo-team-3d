"""Required recruiter skill: temporary homes, cached source and fake engines only."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from labhq import cli, doctor
from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.recruit import paper2agent as p2a
from labhq.settings import Settings


@pytest.fixture
def case(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "recruiter.yaml").write_text("id: recruiter\nname: R\nrole: recruit\nengine: claude_code\n", encoding="utf-8")
    s = Settings.model_validate({
        "runner": {"agents_dir": str(agents.parent), "state_dir": str(tmp_path / "state"),
                   "workspace_root": str(tmp_path / "runs")},
        "gateway": {"state_dir": str(tmp_path / "gateway")},
        "engines": {"claude_code": {"bin": "claude", "env": {"CLAUDE_CONFIG_DIR": str(home / "staff-claude")}},
                    "codex": {"bin": "codex", "env": {"CODEX_HOME": str(home / "staff-codex")}}},
    })
    repo = home / ".labhq/cache/Paper2Agent"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git/HEAD").write_text("a" * 40, encoding="utf-8")
    (repo / ".git/config").write_text(f'[remote "origin"]\nurl = {s.recruit.skill_source}\n', encoding="utf-8")
    source = repo / "skills/paper2agent"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("test skill", encoding="utf-8")
    monkeypatch.setattr(p2a.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, b"", b"")))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    return s, home


def row(s, **kwargs):
    return next(r for r in doctor.collect(s, dry_run=not kwargs.get("fix", False), **kwargs)["checks"]
                if r["name"] == "paper2agent")


def test_isolated_install_is_visible_only_in_claude_recruiter_command(case, tmp_path):
    s, home = case
    p2a.setup_paper2agent(s)
    path = p2a.skill_path("claude_code", s)
    assert path.is_relative_to(home / "staff-claude")
    assert row(s)["status"] == "ok"
    for name in ("recruiter", "worker"):
        agent = AgentSpec(id=name, name=name, role="test", engine=Engine.claude_code)
        ctx = RunContext(task=Task(agent_id=name, prompt="x"), agent=agent, settings=s,
                         workdir=tmp_path, mcp_servers=[], env={}, emit=lambda *a: None, prompt="x")
        adapter = get_adapter(agent.engine, s)
        assert adapter.preflight_error(ctx, adapter.staff_env(ctx)) is None
        cmd = adapter.build_command(ctx)
        assert ("--plugin-dir" in cmd) == (name == "recruiter")
        assert ("--disable-slash-commands" in cmd) == (name != "recruiter")
        assert cmd[cmd.index("--setting-sources") + 1] == ("local" if name == "recruiter" else "project,local")
        settings = json.loads(cmd[cmd.index("--settings") + 1])
        assert settings["autoMemoryEnabled"] is False
    assert s.engines.claude_code.isolate_user_config


def test_missing_skill_is_failure(case):
    s, _ = case
    assert row(s)["status"] == "fail"
    assert row(s)["hint"] == "labhq doctor --fix"


def test_init_defers_install_but_standalone_doctor_still_requires_it(case, monkeypatch):
    s, _ = case
    setup = Mock(side_effect=AssertionError("init must not install"))
    monkeypatch.setattr(p2a, "setup_paper2agent", setup)
    result = doctor.collect(s, dry_run=True, require_paper2agent=False)
    assert next(r for r in result["checks"] if r["name"] == "paper2agent")["status"] == "skip"
    assert result["summary"]["fail"] == 0
    assert row(s)["status"] == "fail"
    setup.assert_not_called()


def test_installed_but_hidden_skill_is_failure(case):
    s, _ = case
    p2a.setup_paper2agent(s)
    (p2a.skill_path("claude_code", s).parent.parent / ".claude-plugin/plugin.json").unlink()
    assert row(s)["status"] == "fail"


def test_doctor_cli_fix_calls_setup_and_rechecks(case, monkeypatch, capsys):
    s, _ = case
    setup = Mock(wraps=p2a.setup_paper2agent)
    monkeypatch.setattr(p2a, "setup_paper2agent", setup)
    monkeypatch.setattr(Settings, "load", lambda *_: s)
    monkeypatch.setattr(sys, "argv", ["labhq", "doctor", "--fix"])
    cli.main()
    setup.assert_called_once_with(s)
    assert row(s)["status"] == "ok"
    assert "installed and configured visible" in capsys.readouterr().out


@pytest.mark.parametrize("forced", [True, False])
def test_mock_demo_skips_install(case, monkeypatch, forced):
    s, _ = case
    if forced:
        s.runner.force_engine = "mock"
    else:
        f = Path(s.runner.agents_dir) / "core/recruiter.yaml"
        f.write_text("id: recruiter\nname: R\nrole: recruit\nengine: mock\n", encoding="utf-8")
    setup = Mock(side_effect=AssertionError("must not install"))
    monkeypatch.setattr(p2a, "setup_paper2agent", setup)
    result = doctor.collect(s, fix=True)
    assert next(r for r in result["checks"] if r["name"] == "paper2agent")["status"] == "skip"
    setup.assert_not_called()


def test_fix_reports_network_download_failure(case, monkeypatch):
    s, _ = case
    monkeypatch.setattr(p2a, "setup_paper2agent", Mock(side_effect=subprocess.CalledProcessError(128, "git")))
    result = doctor.collect(s, fix=True)
    item = next(r for r in result["checks"] if r["name"] == "paper2agent")
    assert item["status"] == "fail" and "network" in item["detail"]


@pytest.mark.parametrize("owned", [False, True])
def test_setup_cleans_only_labhq_owned_legacy_copies(case, owned):
    s, home = case
    old = [home / name / "skills/paper2agent" for name in (".claude", ".agents")]
    for path in old:
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("original", encoding="utf-8")
        if owned:
            (path / p2a.SOURCE_MARKER).write_text(json.dumps({"source": s.recruit.skill_source, "commit": "b" * 40}), encoding="utf-8")
    p2a.setup_paper2agent(s)
    assert all(path.exists() != owned for path in old)
    if not owned:
        assert all((path / "SKILL.md").read_text(encoding="utf-8") == "original" for path in old)


def test_codex_uses_recruiter_engine_and_staff_home(case):
    s, home = case
    s.runner.force_engine = "codex"
    # contract_engine describes the future hire, not the recruiter doing conversion.
    assert s.recruit.contract_engine == "claude_code"
    assert p2a.setup_paper2agent(s) == [home / "staff-codex/skills/paper2agent"]
    assert row(s)["status"] == "ok"


def test_personal_config_is_rejected(case):
    s, home = case
    s.engines.claude_code.env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    with pytest.raises(ValueError, match="separate staff"):
        p2a.setup_paper2agent(s)


@pytest.mark.parametrize("engine", ["claude_code", "codex"])
def test_doctor_reinstalls_changed_pin(case, monkeypatch, engine):
    s, home = case
    s.runner.force_engine = engine
    p2a.setup_paper2agent(s)
    assert p2a.installed_commit(engine, s) == "a" * 40
    s.recruit.skill_commit = "b" * 40
    assert row(s)["status"] == "fail"
    # Fake git checkout already contains B; setup must replace the installed A.
    (home / ".labhq/cache/Paper2Agent/.git/HEAD").write_text("b" * 40, encoding="utf-8")
    setup = Mock(wraps=p2a.setup_paper2agent)
    monkeypatch.setattr(p2a, "setup_paper2agent", setup)
    result = row(s, fix=True)
    assert result["status"] == "ok", result
    setup.assert_called_once_with(s)
    assert p2a.installed_commit(engine, s) == "b" * 40


@pytest.mark.parametrize("marker", [None, "{", '{}', '{"commit": "invalid"}'])
def test_doctor_requires_pin_marker_only_when_pinned(case, marker):
    s, _ = case
    p2a.setup_paper2agent(s)
    path = p2a.skill_path("claude_code", s) / p2a.SOURCE_MARKER
    if marker is None:
        path.unlink()
    else:
        path.write_text(marker, encoding="utf-8")
    assert row(s)["status"] == "ok"
    s.recruit.skill_commit = "a" * 40
    assert row(s)["status"] == "fail"


def test_matching_pin_does_not_reinstall(case, monkeypatch):
    s, _ = case
    p2a.setup_paper2agent(s)
    s.recruit.skill_commit = "a" * 40
    setup = Mock(side_effect=AssertionError("matching pin must not reinstall"))
    monkeypatch.setattr(p2a, "setup_paper2agent", setup)
    assert row(s, fix=True)["status"] == "ok"
    setup.assert_not_called()


@pytest.mark.parametrize("engine", ["claude_code", "codex"])
def test_shipped_recruiter_supports_actual_engine(case, tmp_path, engine):
    s, home = case
    source = Path(__file__).resolve().parents[1] / "agents/core/recruiter.yaml"
    spec = yaml.safe_load(source.read_text(encoding="utf-8"))
    spec["engine"] = engine
    target = Path(s.runner.agents_dir) / "core/recruiter.yaml"
    target.write_text(yaml.safe_dump(spec), encoding="utf-8")
    assert s.runner.force_engine is None
    agent = next(a for a in doctor._roster(s) if a.id == "recruiter")
    assert agent.engine.value == engine
    installed = p2a.setup_paper2agent(s)
    assert installed == [p2a.skill_path(engine, s)]
    assert installed[0].is_relative_to(home)
    assert row(s)["status"] == "ok"
    if engine == "claude_code":
        ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, settings=s,
                         workdir=tmp_path, mcp_servers=[], env={}, emit=lambda *a: None, prompt="x")
        adapter = get_adapter(agent.engine, s)
        assert adapter.preflight_error(ctx, adapter.staff_env(ctx)) is None
        cmd = adapter.build_command(ctx)
        assert "--plugin-dir" in cmd
        assert "--disable-slash-commands" not in cmd
