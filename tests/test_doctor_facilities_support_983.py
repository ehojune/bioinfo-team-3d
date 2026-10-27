"""Doctor profile intent gates use synthetic evidence only; no CLI, daemon or network starts."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from labhq import cli, doctor, runtime_readiness
from labhq.models import AgentSpec
from labhq.settings import Settings

ROSTER = doctor._roster


@pytest.fixture
def environment(tmp_path, monkeypatch):
    settings = Settings.model_validate({
        "runner": {"state_dir": str(tmp_path / "state"), "workspace_root": str(tmp_path / "runs"),
                   "agents_dir": str(tmp_path / "agents")},
        "gateway": {"state_dir": str(tmp_path / "gateway")},
    })
    executable = tmp_path / "synthetic-cli"
    executable.touch()
    evidence = {"agents": [AgentSpec(id="facilities", name="Facilities", role="synthetic", engine="claude_code")],
                "version": (0, "synthetic 1.2.3"), "login": (0, "signed in"), "preflight": {}, "found": True}
    monkeypatch.setattr(doctor, "_roster", lambda s: evidence["agents"])
    monkeypatch.setattr(doctor, "_resolve_command", lambda *a: [str(executable if evidence["found"] else
                                                                               tmp_path / "missing")])
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "_probe", lambda args, env: evidence["version"] if args[-1] == "--version"
                                                       else evidence["login"])
    monkeypatch.setattr(doctor, "_adapter_check", lambda s, agent: evidence["preflight"].get(agent.id))
    monkeypatch.setattr(doctor.subprocess, "Popen", lambda *a, **kw: pytest.fail("subprocess forbidden"))
    monkeypatch.setattr(doctor, "_network_check", lambda *a: pytest.fail("network forbidden"))
    for name in ("_memory_limit_row", "_research_row", "_semantics_row", "_bioinfo_row", "_github_token_row",
                 "_private_paths_row", "_alphagenome_row", "_paper2agent_row"):
        monkeypatch.setattr(doctor, name, lambda *a, **kw: doctor._row("fixture", "synthetic", "ok", "", ""))
    for name in ("_semantic_device_rows", "_instance_rows", "_disk_rows", "_sandbox_homes_row", "_sandbox_version_rows"):
        monkeypatch.setattr(doctor, name, lambda *a, **kw: [])
    monkeypatch.setattr(doctor, "_claude_staff_row", lambda *a: None)
    monkeypatch.setattr(doctor, "_python3_alias_row", lambda: None)
    monkeypatch.setattr(cli, "_local_process_diagnostics", lambda s: [])
    monkeypatch.setattr(runtime_readiness, "inspect", lambda s: {"ready": None, "reasons": [], "status": "down"})
    from labhq.knowledge import recall
    monkeypatch.setattr(recall, "health", lambda s: {"available": True, "count": 0, "last_success": None,
                                                   "fallback": None})
    return settings, evidence


def support(report):
    return next(r for r in report["checks"] if r["group"] == "support" and r["name"] == "facilities")


@pytest.mark.parametrize("policy", ["required", "optional", "unspecified"])
@pytest.mark.parametrize("case,state", [("absent", "absent"), ("preflight", "blocked"),
                                     ("signed_out", "blocked"), ("unknown", "unverified"),
                                     ("dry_run", "unverified"), ("mock", "mock_only"), ("ready", "ready")])
def test_profile_matrix(environment, policy, case, state):
    settings, evidence = environment
    settings.runner.facilities_support = policy
    if case == "absent":
        evidence["agents"] = [AgentSpec(id="other", name="Other", role="synthetic", engine="mock")]
    elif case == "preflight":
        evidence["preflight"]["facilities"] = "synthetic preflight refusal"
    elif case == "signed_out":
        evidence["login"] = (1, "Not logged in")
    elif case == "unknown":
        evidence["login"] = (None, "")
    elif case == "mock":
        settings.runner.force_engine = "mock"
    report = doctor.collect(settings, dry_run=case == "dry_run")
    row = support(report)
    expected = "fail" if policy == "required" and state != "ready" else (
        "ok" if policy != "unspecified" and state == "ready" else "warn")
    assert row["status"] == expected
    assert f"state={state}" in row["detail"]
    assert f"policy={policy}" in doctor.render(report)
    assert report["configuration_readiness"]["ready"] == (expected != "fail" and case != "preflight")
    if case == "absent":
        assert "CSO" in row["detail"]


@pytest.mark.parametrize("case,state", [("missing", "blocked"), ("version_failure", "blocked"),
                                     ("version_timeout", "unverified"), ("version_unknown", "unverified"),
                                     ("login_unsupported", "unverified"), ("unknown_login_error", "unverified"),
                                     ("invalid_force", "blocked"), ("json_signed_out", "blocked")])
def test_additional_evidence(environment, case, state):
    settings, evidence = environment
    settings.runner.facilities_support = "required"
    if case == "missing":
        evidence["found"] = False
    elif case.startswith("version_"):
        evidence["version"] = {"version_failure": (1, "synthetic error"), "version_timeout": (None, ""),
                               "version_unknown": (0, "unknown")}[case]
    elif case == "login_unsupported":
        evidence["agents"][0] = evidence["agents"][0].model_copy(update={"engine": doctor.Engine.cli})
    elif case == "unknown_login_error":
        evidence["login"] = (2, "status command unsupported")
    elif case == "invalid_force":
        settings.runner.force_engine = "typo"
    else:
        evidence["login"] = (0, '{"loggedIn":false,"secret":"never publish"}')
    report = doctor.collect(settings)
    assert support(report)["status"] == "fail"
    assert f"state={state}" in support(report)["detail"]
    assert "never publish" not in str(report)


def test_blocked_evidence_wins_over_unknown(environment):
    settings, evidence = environment
    settings.runner.facilities_support = "required"
    evidence["preflight"]["facilities"] = "synthetic refusal"
    evidence["login"] = (None, "")
    assert "state=blocked" in support(doctor.collect(settings, dry_run=True))["detail"]


@pytest.mark.parametrize("engine", ["codex", "claude_code"])
def test_force_engine_replaces_mock_evidence(environment, engine):
    settings, evidence = environment
    settings.runner.facilities_support = "required"
    evidence["agents"][0] = evidence["agents"][0].model_copy(update={"engine": doctor.Engine.mock})
    settings.runner.force_engine = engine
    assert support(doctor.collect(settings))["status"] == "ok"


@pytest.mark.parametrize("facilities_ready", [False, True])
def test_other_engine_login_does_not_prove_facilities_ready(environment, monkeypatch, facilities_ready):
    settings, evidence = environment
    settings.runner.facilities_support = "required"
    evidence["agents"].append(AgentSpec(id="other", name="Other", role="test", engine="codex"))
    settings.engines.claude_code.env["FIXTURE_ENGINE"] = "claude_code"
    settings.engines.codex.env["FIXTURE_ENGINE"] = "codex"

    def probe(args, env):
        if args[-1] == "--version":
            return 0, "synthetic 1.2.3"
        ready = (env["FIXTURE_ENGINE"] == "claude_code") == facilities_ready
        return (0, "signed in") if ready else (None, "")

    monkeypatch.setattr(doctor, "_probe", probe)
    assert support(doctor.collect(settings))["status"] == ("ok" if facilities_ready else "fail")


@pytest.mark.parametrize("contract_status,expires,state", [("active", 9999999999, "mock_only"),
                                                         ("active", 1, "absent"),
                                                         ("archived", 9999999999, "absent")])
def test_support_uses_effective_custom_contract_readonly(environment, tmp_path, monkeypatch,
                                                       contract_status, expires, state):
    from tests.test_doctor import _write_roster_agent

    settings, evidence = environment
    settings.runner.facilities_support = "required"
    settings.runner.contract_dir = str(tmp_path / "custom")
    _write_roster_agent(tmp_path / "agents/core", "other.yaml", "other", employment="core")
    _write_roster_agent(tmp_path / "agents/contract", "ignored.yaml", "facilities", expires_at=9999999999)
    _write_roster_agent(tmp_path / "custom", "facility.yaml", "facilities", status=contract_status, expires_at=expires)
    monkeypatch.setattr(doctor, "_roster", ROSTER)
    before = {p.relative_to(tmp_path): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
              for p in tmp_path.rglob("*")}
    assert f"state={state}" in support(doctor.collect(settings, dry_run=True))["detail"]
    assert {p.relative_to(tmp_path): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in tmp_path.rglob("*")} == before


@pytest.mark.parametrize("policy", ["required", "optional", "unspecified"])
def test_other_staff_failure_is_preserved(environment, policy):
    settings, evidence = environment
    settings.runner.facilities_support = policy
    evidence["agents"].append(AgentSpec(id="other", name="Other", role="test", engine="claude_code"))
    # Claude plugin errors are WARN, but that facilities preflight still blocks required support.
    evidence["preflight"]["other"] = "other staff failure"
    report = doctor.collect(settings)
    assert "state=ready" in support(report)["detail"]
    assert report["summary"]["fail"] == 1
    assert report["configuration_readiness"]["ready"] is False
    evidence["preflight"] = {"facilities": "facilities preflight failure"}
    evidence["agents"][0].plugin_dirs = ["synthetic-plugin"]
    report = doctor.collect(settings)
    assert "state=blocked" in support(report)["detail"]
    assert support(report)["status"] == ("fail" if policy == "required" else "warn")


def test_dry_run_does_not_probe_or_write(environment, tmp_path, monkeypatch):
    settings, evidence = environment
    settings.runner.facilities_support = "required"
    monkeypatch.setattr(doctor, "_probe", lambda *a: pytest.fail("dry-run probe forbidden"))
    before = {p.relative_to(tmp_path): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
              for p in tmp_path.rglob("*")}
    assert support(doctor.collect(settings, dry_run=True))["status"] == "fail"
    assert {p.relative_to(tmp_path): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in tmp_path.rglob("*")} == before


@pytest.mark.parametrize("command", ["doctor", "init"])
@pytest.mark.parametrize("policy,exit_code", [("required", 1), ("optional", 0), ("unspecified", 0)])
def test_cli_exit_uses_support_failure(environment, monkeypatch, capsys, command, policy, exit_code):
    settings, evidence = environment
    settings.runner.facilities_support = policy
    evidence["agents"] = []
    monkeypatch.setattr(Settings, "load", lambda *a, **kw: settings)
    if command == "init":
        from labhq import init_wizard
        monkeypatch.setattr(init_wizard, "run", lambda *a, **kw: doctor.collect(settings, dry_run=True))
    monkeypatch.setattr("sys.argv", ["labhq", command] + (["--dry-run", "--yes"] if command == "init" else []))
    if exit_code:
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == exit_code
    else:
        cli.main()
    if command == "doctor":
        assert f"policy={policy}" in capsys.readouterr().out


@pytest.mark.parametrize("invalid", [None, "typo", True, 1])
def test_invalid_declaration_rejected(invalid):
    with pytest.raises(ValidationError):
        Settings.model_validate({"runner": {"facilities_support": invalid}})


@pytest.mark.parametrize("code,raw,state", [(0, '{"loggedIn":true}', "ready"),
                                         (0, "Logged in using ChatGPT", "ready"),
                                         (0, "Logged in using an API key - synthetic", "ready"),
                                         (0, "", "unverified"), (0, "usage: login status", "unverified"),
                                         (None, "Not logged in", "unverified"),
                                         (1, "Not logged in", "blocked"),
                                         (0, '{"loggedIn":false}', "blocked")])
def test_login_evidence_requires_recognized_status(code, raw, state):
    assert doctor._login_evidence(code, raw) == state


def test_examples_match_and_default_is_unspecified():
    root = Path(__file__).resolve().parents[1]
    assert (root / "config/labhq.example.yaml").read_bytes() == (root / "labhq/config/labhq.example.yaml").read_bytes()
    assert Settings().runner.facilities_support == "unspecified"
