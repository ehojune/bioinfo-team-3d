"""Required recruiter skill: temporary homes, cached source and fake engines only."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

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
    return next(r for r in doctor.collect(s, dry_run=True, **kwargs)["checks"] if r["name"] == "paper2agent")


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
