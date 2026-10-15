"""`labhq init` turns on what the PI tested, finds the bioinfo-agent plugin, and asks for the dev log repo (#613)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from labhq import bioinfo_link, cli, doctor, init_defaults, init_wizard as wizard
from labhq.research.packs import check_configured_packs
from labhq.settings import Settings
from labhq.tools.scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]
REAL_COMMON_PATHS = bioinfo_link.common_paths  # the suite-wide stub (conftest) replaces it per test
TEMPLATES = (ROOT / "config" / "labhq.example.yaml", ROOT / "labhq" / "config" / "labhq.example.yaml")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(folder))
    for key in ("LABHQ_CONFIG", "BIOINFO_AGENT_DIR", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LABHQ_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    monkeypatch.setattr(wizard.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "_probe", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected CLI")))
    monkeypatch.setattr(wizard, "_hpc_query", lambda argv: None)
    monkeypatch.setattr(wizard, "_hpc_backend", lambda hpc: Scheduler(hpc))
    monkeypatch.chdir(tmp_path)
    return folder


def plugin_folder(root: Path) -> Path:
    """The two files the claude_code adapter requires of the bioinfo plugin."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "bioinfo"}', encoding="utf-8")
    (root / "skills" / "bioinfo-analyze").mkdir(parents=True)
    (root / "skills" / "bioinfo-analyze" / "SKILL.md").write_text("skill", encoding="utf-8")
    return root


def written(path: Path = Path("config/labhq.yaml")) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def no_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("unexpected prompt"))


# --- the reference behaviour set and the init template do not drift -----------------------------------------------


def test_reference_list_is_small_explicit_and_has_no_personal_value():
    paths = [path for path, _ in init_defaults.REFERENCE_BEHAVIOUR]
    assert paths == ["research.enabled", "research.evidence_checkpoint", "research.active_packs",
                     "semantics.mode", "semantics.actions", "plan.declare_output_types"]
    text = repr(init_defaults.REFERENCE_BEHAVIOUR)
    assert not any(word in text for word in ("token", "key", "/", "\\", "@codex"))


@pytest.mark.parametrize("template", TEMPLATES, ids=["repository", "packaged"])
def test_example_config_matches_the_reference_behaviour(template):
    assert init_defaults.drift(yaml.safe_load(template.read_text(encoding="utf-8"))) == []


def test_drift_names_every_setting_a_config_stops_carrying():
    data: dict = {}
    for dotted, value in init_defaults.REFERENCE_BEHAVIOUR:  # a config that matches the reference exactly
        *parents, leaf = dotted.split(".")
        node = data
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    assert init_defaults.drift(data) == []
    data["research"]["enabled"] = False
    data["semantics"] = "off"
    del data["plan"]
    problems = init_defaults.drift(data)
    assert [line.split(":")[0] for line in problems] == ["research.enabled", "semantics.mode",
                                                         "semantics.actions", "plan.declare_output_types"]


def test_the_two_example_copies_stay_identical():
    assert TEMPLATES[0].read_bytes() == TEMPLATES[1].read_bytes()


def test_init_output_carries_the_reference_behaviour_and_loads(monkeypatch):
    no_input(monkeypatch)
    wizard.run(yes=True)
    data = written()
    assert init_defaults.drift(data) == []
    settings = Settings.load("config/labhq.yaml")
    assert settings.research.enabled and settings.research.evidence_checkpoint
    assert settings.research.active_packs == ["bulk_tumor_normal@3", "single_cell_de@3"]
    assert settings.plan.declare_output_types is True
    assert settings.semantics == {"mode": "ab", "actions": "confirm"}
    check_configured_packs(settings)  # the gateway refuses to start on a pack it cannot load


@pytest.mark.parametrize("windows", [True, False])
def test_the_run_folder_is_an_internal_zone_so_semantics_can_hash_outputs(monkeypatch, windows):
    monkeypatch.setattr(wizard, "_is_windows", lambda: windows)
    no_input(monkeypatch)
    wizard.run(yes=True)
    zones = written()["policy"]["data_zones"]
    internal = [zone for zone in zones if zone["level"] == "internal"]
    assert len(internal) == 1 and internal[0]["path"] == written()["runner"]["workspace_root"]
    assert any(zone["level"] == "restricted" for zone in zones) is (not windows)


def test_an_instance_gets_its_own_run_folder_zone(home, monkeypatch):
    no_input(monkeypatch)
    wizard.run(config=str(home / ".labhq" / "lab2" / "labhq.yaml"), yes=True, instance="lab2")
    data = written(home / ".labhq" / "lab2" / "labhq.yaml")
    internal = [zone for zone in data["policy"]["data_zones"] if zone["level"] == "internal"]
    assert [zone["path"] for zone in internal] == ["~/.labhq/lab2/runs"] == [data["runner"]["workspace_root"]]
    assert init_defaults.drift(data) == []


def test_init_prints_what_it_turned_on(monkeypatch, capsys):
    no_input(monkeypatch)
    wizard.run(yes=True)
    out = capsys.readouterr().out
    assert "켜 둔 기본 설정" in out and "bulk_tumor_normal@3" in out and "research.enabled" in out


def test_init_does_not_touch_the_pipeline_consent_or_dev_log_defaults(monkeypatch):
    no_input(monkeypatch)
    wizard.run(yes=True)
    data = written()
    assert data["policy"]["bioinfo_agent"]["pipeline_pr"] is False
    assert data["dev_log"]["repo"] is None


# --- bioinfo-agent: find, ask, or say it is off -------------------------------------------------------------------


def test_detection_order_is_env_then_common_places_then_wsl(tmp_path, monkeypatch):
    common = plugin_folder(tmp_path / "common-checkout")
    wsl = plugin_folder(tmp_path / "wsl-checkout")
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [("common", str(tmp_path / "none")),
                                                               ("common", str(common)), ("wsl", str(wsl))])
    assert bioinfo_link.detect({}) == (str(common), "common")
    assert bioinfo_link.detect({"BIOINFO_AGENT_DIR": str(wsl)}) == (str(wsl), "env")
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [("common", str(tmp_path / "none")),
                                                               ("wsl", str(wsl))])
    assert bioinfo_link.detect({}) == (str(wsl), "wsl")
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [("common", str(tmp_path / "none"))])
    assert bioinfo_link.detect({}) is None


def test_common_places_include_the_documented_ones(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/user")))
    paths = [path.replace("\\", "/") for _, path in REAL_COMMON_PATHS()]
    assert "/home/user/bioinfo-agent" in paths and "/mnt/d/bioinfo-agent" in paths
    if bioinfo_link.os.name == "nt":
        assert "D:/bioinfo-agent" in paths


def test_a_folder_in_a_common_place_is_connected_without_asking(tmp_path, monkeypatch, capsys):
    checkout = plugin_folder(tmp_path / "checkout")
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [("common", str(checkout))])
    no_input(monkeypatch)
    wizard.run(yes=True)
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(checkout)
    out = capsys.readouterr().out
    assert "흔한 위치" in out and str(checkout) not in out and "연결 안 됨" not in out


def test_a_wsl_path_is_found_when_nothing_else_is(tmp_path, monkeypatch, capsys):
    checkout = plugin_folder(tmp_path / "mnt-d" / "bioinfo-agent")
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [("wsl", str(checkout))])
    no_input(monkeypatch)
    wizard.run(yes=True)
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(checkout)
    assert "WSL 경로" in capsys.readouterr().out


def test_interactive_init_confirms_a_found_folder_and_lets_the_person_decline(tmp_path, monkeypatch, capsys):
    checkout = plugin_folder(tmp_path / "checkout")
    monkeypatch.setattr(bioinfo_link, "common_paths", lambda: [("common", str(checkout))])
    answers = {"scheduler": "none", "연결할까요": "n"}
    monkeypatch.setattr("builtins.input", lambda prompt: next((v for k, v in answers.items() if k in prompt), ""))
    wizard.run()
    env = written()["engines"]["claude_code"].get("env") or {}
    assert "BIOINFO_AGENT_DIR" not in env
    assert "bioinfo-agent 연결 안 됨 — " in capsys.readouterr().out


def test_interactive_init_asks_when_nothing_is_found(tmp_path, monkeypatch):
    checkout = plugin_folder(tmp_path / "typed")
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or (
        f'"{checkout}"' if "찾지 못했습니다" in prompt else ("none" if "scheduler" in prompt else "")))
    wizard.run()
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(checkout)
    assert sum("찾지 못했습니다" in prompt for prompt in prompts) == 1


def test_not_found_non_interactively_writes_the_config_off_and_says_so(monkeypatch, capsys):
    no_input(monkeypatch)
    wizard.run(yes=True)
    assert "BIOINFO_AGENT_DIR" not in (written()["engines"]["claude_code"].get("env") or {})
    out = capsys.readouterr().out
    assert "bioinfo-agent 연결 안 됨 — " in out and "--bioinfo-agent" in out


def test_the_flag_connects_a_folder_when_nothing_would_be_found(tmp_path, monkeypatch, capsys):
    checkout = plugin_folder(tmp_path / "given")
    no_input(monkeypatch)
    cli.main(["init", "--yes", "--bioinfo-agent", str(checkout)])
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(checkout)
    assert str(checkout) not in capsys.readouterr().out


def test_the_flag_wins_over_the_environment(tmp_path, monkeypatch):
    given = plugin_folder(tmp_path / "given")
    monkeypatch.setenv("BIOINFO_AGENT_DIR", str(plugin_folder(tmp_path / "from-env")))
    no_input(monkeypatch)
    wizard.run(yes=True, bioinfo_agent=str(given))
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(given)


def test_a_flag_folder_without_the_plugin_is_refused_and_writes_nothing(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit) as stopped:
        cli.main(["init", "--yes", "--bioinfo-agent", str(tmp_path / "empty")])
    assert stopped.value.code == 1
    assert not Path("config").exists()
    assert "plugin" in capsys.readouterr().err


def test_an_existing_environment_variable_is_connected_even_before_doctor_checks_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BIOINFO_AGENT_DIR", str(tmp_path / "set-by-user"))
    no_input(monkeypatch)
    wizard.run(yes=True)
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(tmp_path / "set-by-user")
    assert "주의" in capsys.readouterr().out


# --- doctor: bioinfo-agent connection and the dev log hint ---------------------------------------------------------


def rows(settings: Settings, name: str) -> list[dict]:
    return [row for row in doctor.collect(settings, dry_run=True)["checks"]
            if row["name"] == name and row["group"] == "config"]


def test_doctor_warns_in_korean_when_bioinfo_agent_is_not_connected():
    row, = rows(Settings(), "bioinfo-agent")
    assert row["group"] == "config" and row["status"] == "warn"
    assert row["detail"].startswith("bioinfo-agent 연결 안 됨 — ")
    assert "BIOINFO_AGENT_DIR" in row["hint"] and "labhq init --bioinfo-agent" in row["hint"]


def test_doctor_warns_when_the_folder_has_no_plugin_and_is_quiet_when_connected(tmp_path):
    settings = Settings.model_validate({"engines": {"claude_code": {"bin": "claude", "env": {"BIOINFO_AGENT_DIR": str(tmp_path)}}}})
    row, = rows(settings, "bioinfo-agent")
    assert row["status"] == "warn" and "plugin" in row["detail"]
    checkout = plugin_folder(tmp_path / "checkout")
    settings = Settings.model_validate({"engines": {"claude_code": {"bin": "claude", "env": {"BIOINFO_AGENT_DIR": str(checkout)}}}})
    row, = rows(settings, "bioinfo-agent")
    assert row["status"] == "ok" and row["detail"].startswith("연결됨")


def test_doctor_explains_how_to_set_the_dev_log_repo_when_it_is_empty():
    row, = rows(Settings(), "dev log")
    assert row["status"] == "warn" and "owner/name" in row["hint"] and "비워 두면 이 PC에만" in row["hint"]


# --- the dev log repo: asked, optional, never defaulted -----------------------------------------------------------


def dev_log_answers(monkeypatch, repo: str):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or (
        repo if "dev log" in prompt else "none" if "scheduler" in prompt else ""))
    return prompts


def test_init_asks_for_the_dev_log_repo_once_and_stores_a_valid_answer(monkeypatch):
    prompts = dev_log_answers(monkeypatch, "my-lab/labhq-rounds")
    wizard.run()
    assert written()["dev_log"]["repo"] == "my-lab/labhq-rounds"
    assert sum("dev log" in prompt for prompt in prompts) == 1


@pytest.mark.parametrize("answer", ["", "not a repo", "https://github.com/a/b", "a/b/c"])
def test_an_empty_or_malformed_dev_log_answer_leaves_it_empty(monkeypatch, capsys, answer):
    dev_log_answers(monkeypatch, answer)
    wizard.run()
    assert written()["dev_log"]["repo"] is None
    assert "dev_log.repo" in capsys.readouterr().out


def test_yes_never_asks_for_the_dev_log_repo_and_explains_the_empty_value(monkeypatch, capsys):
    no_input(monkeypatch)
    wizard.run(yes=True)
    assert written()["dev_log"]["repo"] is None
    assert "비공개 저장소 owner/name을 적으세요" in capsys.readouterr().out


def test_force_keeps_an_existing_dev_log_repo_without_asking(monkeypatch):
    wizard.run(yes=True)
    data = written()
    data["dev_log"]["repo"] = "my-lab/labhq-rounds"
    Path("config/labhq.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    prompts = dev_log_answers(monkeypatch, "other/repo")
    wizard.run(force=True)
    assert written()["dev_log"]["repo"] == "my-lab/labhq-rounds"
    assert not any("dev log" in prompt for prompt in prompts)


def test_an_existing_config_is_left_alone_and_nothing_is_asked(monkeypatch):
    wizard.run(yes=True)
    before = Path("config/labhq.yaml").read_bytes()
    no_input(monkeypatch)
    wizard.run()
    assert Path("config/labhq.yaml").read_bytes() == before  # no migration of an existing config


# --- the saved folder is absolute: a task reads a relative plugin folder from its own work folder -------------------


def test_a_relative_flag_folder_is_saved_as_the_absolute_path_it_was_checked_at(tmp_path, monkeypatch):
    plugin_folder(tmp_path / "work" / "bioinfo-agent")
    (tmp_path / "work" / "checkout").mkdir()
    monkeypatch.chdir(tmp_path / "work" / "checkout")
    no_input(monkeypatch)
    wizard.run(config=str(tmp_path / "out" / "labhq.yaml"), yes=True, bioinfo_agent="../bioinfo-agent")
    saved = written(tmp_path / "out" / "labhq.yaml")["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"]
    assert Path(saved).is_absolute() and ".." not in Path(saved).parts
    assert Path(saved) == tmp_path / "work" / "bioinfo-agent"


def test_a_relative_folder_from_the_environment_or_the_prompt_is_saved_absolute_too(tmp_path, monkeypatch):
    plugin_folder(tmp_path / "rel-env")
    monkeypatch.setenv("BIOINFO_AGENT_DIR", "rel-env")
    no_input(monkeypatch)
    wizard.run(yes=True)
    assert written()["engines"]["claude_code"]["env"]["BIOINFO_AGENT_DIR"] == str(tmp_path / "rel-env")
    monkeypatch.delenv("BIOINFO_AGENT_DIR")
    plugin_folder(tmp_path / "rel-typed")
    monkeypatch.setattr("builtins.input", lambda prompt: "rel-typed" if "찾지 못했습니다" in prompt else (
        "none" if "scheduler" in prompt else ""))
    wizard.run(config=str(tmp_path / "typed" / "labhq.yaml"))
    assert written(tmp_path / "typed" / "labhq.yaml")["engines"]["claude_code"]["env"][
        "BIOINFO_AGENT_DIR"] == str(tmp_path / "rel-typed")


def test_doctor_warns_when_the_configured_folder_is_relative_even_if_it_exists_here(tmp_path):
    plugin_folder(tmp_path / "rel-config")  # init runs in tmp_path, so the relative name would resolve here
    settings = Settings.model_validate({"engines": {"claude_code": {"bin": "claude",
                                                                    "env": {"BIOINFO_AGENT_DIR": "rel-config"}}}})
    row, = rows(settings, "bioinfo-agent")
    assert row["status"] == "warn" and "상대 경로" in row["detail"] and "절대 경로" in row["hint"]
