"""#1300: the site profile schema, checker, question flow, storage outside the repository and the public check."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from labhq import hpc_site as site
from labhq import init_wizard
from labhq.settings import HpcSettings
from tests.test_private_paths import BASH, needs_bash  # Git's bash on Windows: System32's bash.exe would start WSL

ROOT = Path(__file__).resolve().parents[1]


def answers(*values):
    """A fake terminal: each question takes the next answer; an empty string is a blank Enter."""
    queue = list(values)
    asked = []

    def ask(label, default):
        asked.append((label, default))
        return queue.pop(0) if queue else ""

    ask.asked = asked
    return ask


def test_the_empty_example_is_a_valid_profile_with_everything_unknown():
    profile = site.parse_profile(site.example_text())
    assert profile.name == "my-cluster"
    assert site.unknown_fields(profile) == list(site.DECISIONS)
    assert site.problems(profile) == []
    assert site.example_text().startswith(site.MARKER + ": 1\n")


@pytest.mark.parametrize("change,message", [
    ({"surprise": 1}, "surprise"),
    ({"name": "Has Space"}, "name"),
    ({"name": "../x"}, "name"),
    ({"labhq_site_profile": 2}, "version"),
    ({"idle_lock_minutes": 0}, "idle_lock_minutes"),
    ({"scheduler": "lsf"}, "scheduler"),
    ({"remote_workdir": "relative/path"}, "remote_workdir"),
    ({"remote_workdir": "/a/../b"}, "remote_workdir"),
    ({"connect_argv": ["ssh", "-o", "password=hunter2"]}, "password"),
    ({"connect_argv": ["sshpass", "-p", "x", "ssh"]}, "password"),
    ({"connect_argv": ["ssh", ""]}, "connect_argv"),
])
def test_schema_rejects(change, message):
    data = {site.MARKER: 1, "name": "site", **change}
    with pytest.raises(site.SiteProfileError) as error:
        site.parse_profile(yaml.safe_dump(data))
    assert message in str(error.value)


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "a: [", "42\n", "name: site\n"])  # the last has no marker key
def test_a_file_that_is_not_a_profile_mapping_is_an_error(text):
    with pytest.raises(site.SiteProfileError):
        site.parse_profile(text)


def test_unknown_fields_name_what_is_still_to_ask():
    profile = site.SiteProfile(name="s", access_method="ssh", remote_exec=True, scheduler="slurm")
    assert site.unknown_fields(profile) == ["file_transfer", "idle_lock_minutes", "agent_inside_cluster", "connect_argv"]
    assert "connect_argv" not in site.unknown_fields(site.SiteProfile(name="s", access_method="vpn"))


def test_contradictory_answers_are_reported_not_rejected():
    profile = site.SiteProfile(name="s", access_method="ssh", connect_argv=["ssh", "login"], remote_exec=False,
                               scheduler="slurm", file_transfer="none", remote_workdir="/scratch/me")
    found = site.problems(profile)
    assert len(found) == 3 and any("scheduler" in item for item in found)


# ----- storage -----

def test_a_saved_profile_round_trips_and_is_listed(tmp_path):
    profile = site.SiteProfile(name="lab", access_method="gateway_tunnel", connect_argv=["ssh", "-p", "2222", "localhost"],
                               port_discovery="the gateway client shows the local port", remote_exec=True,
                               file_transfer="gateway_window", idle_lock_minutes=180, agent_inside_cluster=False,
                               scheduler="sge", queue="all.q", remote_workdir="/scratch/me", login_limit_per_day=30)
    path = site.save_profile(profile, tmp_path)
    assert path == tmp_path / "lab.yaml" and path.read_text(encoding="utf-8").startswith(site.MARKER)
    assert site.load_profile("lab", tmp_path) == profile
    assert site.list_profiles(tmp_path) == ["lab"]
    assert site.check_file(path) == []
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_saving_again_replaces_the_file(tmp_path):
    site.save_profile(site.SiteProfile(name="lab", queue="a"), tmp_path)
    site.save_profile(site.SiteProfile(name="lab", queue="b"), tmp_path)
    assert site.load_profile("lab", tmp_path).queue == "b"


@pytest.mark.parametrize("name", ["../x", "a/b", "A", "", "x" * 65, "-x"])
def test_profile_names_cannot_leave_the_folder(name, tmp_path):
    with pytest.raises(site.SiteProfileError):
        site.profile_path(name, tmp_path)


def test_a_profile_is_refused_inside_this_checkout_or_any_git_folder(tmp_path):
    profile = site.SiteProfile(name="lab")
    with pytest.raises(site.SiteProfileError, match="outside any repository"):
        site.save_profile(profile, ROOT / "config" / "sites")
    other = tmp_path / "clone"
    (other / ".git").mkdir(parents=True)
    with pytest.raises(site.SiteProfileError, match="outside any repository"):
        site.save_profile(profile, other / ".labhq" / "sites")
    assert not (ROOT / "config" / "sites").exists() and not (other / ".labhq").exists()


def test_the_default_folder_is_under_the_home_and_outside_the_repository():
    assert site.sites_dir({}) == Path.home() / ".labhq" / "sites"
    assert site.sites_dir({"LABHQ_SITES_DIR": "/somewhere/else"}) == Path("/somewhere/else")
    assert site.outside_repo(Path.home() / ".labhq" / "sites") or (Path.home() / ".git").exists()
    assert not site.outside_repo(ROOT / "docs")


def test_a_profile_file_that_names_another_profile_is_refused(tmp_path):
    (tmp_path / "one.yaml").write_text(site.dump(site.SiteProfile(name="two")), encoding="utf-8")
    assert "names itself two" in site.check_file(tmp_path / "one.yaml")[0]  # the checker agrees with the loader
    with pytest.raises(site.SiteProfileError, match="names itself two"):
        site.load_profile("one", tmp_path)
    with pytest.raises(site.SiteProfileError):
        site.load_profile("missing", tmp_path)


# ----- the questions -----

def test_blank_answers_stay_unknown_and_are_never_guessed():
    profile = site.ask_profile("lab", answers())
    assert site.unknown_fields(profile) == list(site.DECISIONS)
    assert profile.connect_argv == [] and profile.queue is None


def test_answers_are_typed_and_a_bad_one_is_asked_again(capsys):
    ask = answers("telnet", "ssh", "ssh -p 2222 login", "", "y", "scp", "", "180", "n", "slurm", "short", "/scratch/me", "", "")
    profile = site.ask_profile("lab", ask)
    assert (profile.access_method, profile.connect_argv, profile.remote_exec) == ("ssh", ["ssh", "-p", "2222", "login"], True)
    assert (profile.file_transfer, profile.idle_lock_minutes, profile.agent_inside_cluster) == ("scp", 180, False)
    assert (profile.scheduler, profile.queue, profile.remote_workdir) == ("slurm", "short", "/scratch/me")
    assert "다음 중 하나" in capsys.readouterr().out
    assert site.unknown_fields(profile) == []


def test_an_argv_with_a_secret_is_asked_again(capsys):
    profile = site.ask_profile("lab", answers("ssh", "ssh -o password=x login", "ssh login"))
    assert profile.connect_argv == ["ssh", "login"] and "password" in capsys.readouterr().out


def test_numbers_and_yes_no_are_checked(capsys):
    ask = answers("", "", "", "maybe", "n", "", "", "0", "soon", "90")
    profile = site.ask_profile("lab", ask)
    assert profile.remote_exec is False and profile.idle_lock_minutes == 90
    out = capsys.readouterr().out
    assert "y 또는 n" in out and "1 이상" in out


def test_presets_are_the_defaults_and_existing_answers_win_when_blank():
    ask = answers()
    profile = site.ask_profile("lab", ask, preset={"scheduler": "slurm", "login_limit_per_day": 25})
    assert profile.scheduler == "slurm" and profile.login_limit_per_day == 25
    again = site.ask_profile("lab", answers("ssh"), existing=profile)
    assert again.access_method == "ssh" and again.scheduler == "slurm"


# ----- labhq init -----

def _init(monkeypatch, tmp_path, *values, interactive=True, hpc=None, scheduler="slurm", yes=False):
    monkeypatch.setattr(init_wizard, "_interactive", lambda: interactive)
    ask = answers(*values)
    monkeypatch.setattr(init_wizard, "_ask", lambda label, default, _yes: ask(label, default))
    hpc = {} if hpc is None else hpc
    init_wizard._ask_hpc_site_profile(hpc, scheduler, yes, tmp_path)
    return hpc, ask


def test_init_writes_the_profile_outside_the_config_and_keeps_only_its_name(monkeypatch, tmp_path, capsys):
    hpc, _ = _init(monkeypatch, tmp_path, "y", "lab", "ssh", "ssh login", "", "y", "scp", "", "180", "n", "", "short",
                   "/scratch/me", "", "", hpc={"default_queue": "short", "login_limit_per_day": 30})
    assert hpc["site_profile"] == "lab"
    profile = site.load_profile("lab", tmp_path)
    assert profile.scheduler == "slurm" and profile.queue == "short" and profile.login_limit_per_day == 30
    out = capsys.readouterr().out
    assert "~/.labhq/sites/lab.yaml" in out and str(tmp_path) not in out


@pytest.mark.parametrize("kwargs", [{"yes": True}, {"interactive": False}, {"scheduler": "none"}, {"scheduler": "mock"},
                                    {"hpc": {"site_profile": "already"}}])
def test_init_does_not_ask_without_a_person_or_a_scheduler_or_when_a_profile_is_named(monkeypatch, tmp_path, kwargs):
    hpc, ask = _init(monkeypatch, tmp_path, "y", "lab", **kwargs)
    assert ask.asked == [] and list(tmp_path.iterdir()) == []
    assert hpc.get("site_profile") in (None, "already")


def test_init_never_replaces_an_existing_profile_and_offers_its_values(monkeypatch, tmp_path, capsys):
    site.save_profile(site.SiteProfile(name="lab", access_method="ssh", connect_argv=["ssh", "login"], remote_exec=True,
                                       scheduler="slurm", queue="long"), tmp_path)
    hpc, ask = _init(monkeypatch, tmp_path, "y", "lab", *[""] * 13)  # Enter on every question keeps the old answers
    kept = site.load_profile("lab", tmp_path)
    assert (kept.access_method, kept.connect_argv, kept.remote_exec, kept.queue) == ("ssh", ["ssh", "login"], True, "long")
    defaults = {label: default for label, default in ask.asked}
    assert defaults["접속 명령 (공백으로 나눈 단어, 비밀번호·토큰은 쓰지 마세요)"] == "ssh login"
    assert "이미 있는 프로필" in capsys.readouterr().out and hpc["site_profile"] == "lab"


def test_init_cannot_replace_an_unreadable_existing_profile(monkeypatch, tmp_path, capsys):
    (tmp_path / "lab.yaml").write_text("not: a profile\n", encoding="utf-8")
    hpc, _ = _init(monkeypatch, tmp_path, "y", "lab")
    assert "site_profile" not in hpc and (tmp_path / "lab.yaml").read_text(encoding="utf-8") == "not: a profile\n"
    assert "저장하지 못했습니다" in capsys.readouterr().out


def test_init_dry_run_asks_nothing_and_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    monkeypatch.setattr(init_wizard, "_ask", lambda *a: pytest.fail("dry-run must not prompt"))
    hpc = {}
    init_wizard._ask_hpc_site_profile(hpc, "slurm", False, tmp_path, dry_run=True)
    assert hpc == {} and list(tmp_path.iterdir()) == [] and "dry-run" in capsys.readouterr().out


def test_init_declining_writes_nothing(monkeypatch, tmp_path):
    hpc, ask = _init(monkeypatch, tmp_path, "n")
    assert len(ask.asked) == 1 and "site_profile" not in hpc and list(tmp_path.iterdir()) == []


def test_init_refuses_a_repository_folder_and_says_so(monkeypatch, tmp_path, capsys):
    (tmp_path / ".git").mkdir()
    hpc, _ = _init(monkeypatch, tmp_path / "sites", "y", "lab")
    assert "site_profile" not in hpc and "저장하지 못했습니다" in capsys.readouterr().out


def test_init_rejects_a_bad_profile_name_without_writing(monkeypatch, tmp_path, capsys):
    hpc, _ = _init(monkeypatch, tmp_path, "y", "../evil")
    assert "site_profile" not in hpc and list(tmp_path.iterdir()) == []


# ----- settings -----

def test_the_hpc_setting_keeps_only_a_profile_name():
    assert HpcSettings(site_profile="lab").site_profile == "lab"
    assert HpcSettings().site_profile is None
    for bad in ("../x", "C:/x", "a b", "A", "x" * 65):
        with pytest.raises(ValueError):
            HpcSettings(site_profile=bad)


# ----- the public check -----

@needs_bash
@pytest.mark.parametrize("path,text,fails", [
    ("profile.yaml", site.example_text(), True),
    ("deep/dir/mine.yml", site.MARKER + ": 1\nname: x\n", True),
    (".labhq/sites/anything.txt", "x\n", True),
    ("copy/quoted.yaml", "'" + site.MARKER + "': 1\nname: x\n", True),
    ("copy/double.yaml", '"' + site.MARKER + '": 1\nname: x\n', True),
    ("copy/indented.yaml", "  " + site.MARKER + ": 1\n  name: x\n", True),
    ("docs/note.md", "the key " + site.MARKER + ": is described here\n", False),
    ("tests/sample.py", 'TEXT = "' + site.MARKER + ': 1"\n', False),
])
def test_the_public_check_stops_a_tracked_site_profile(tmp_path, path, text, fails):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    script = (ROOT / "scripts" / "check_public.sh").read_bytes().replace(b"\r\n", b"\n")  # a Windows checkout has CRLF
    (repo / "scripts" / "check_public.sh").write_bytes(script)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    done = subprocess.run([BASH, "scripts/check_public.sh"], cwd=repo, capture_output=True, env=env)
    out = (done.stdout + done.stderr).decode("utf-8", "replace")
    assert (done.returncode != 0) is fails, out
    if fails:
        assert "사이트 프로필" in out


def test_this_repository_tracks_no_site_profile():
    done = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    for name in done.stdout.decode("utf-8").split("\0"):
        path = ROOT / name
        if not name or not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            assert not re.match(r"^\s*.?" + site.MARKER + r".?\s*:", line), name
        assert ".labhq/sites/" not in name


def test_init_names_the_override_folder_when_one_is_set(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path))
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    ask = answers("y", "lab")
    monkeypatch.setattr(init_wizard, "_ask", lambda label, default, _yes: ask(label, default))
    hpc = {}
    init_wizard._ask_hpc_site_profile(hpc, "slurm", False)
    out = capsys.readouterr().out
    assert hpc["site_profile"] == "lab" and "$LABHQ_SITES_DIR/lab.yaml" in out and str(tmp_path) not in out


def test_enter_on_every_question_keeps_every_existing_answer_including_the_yes_no_ones():
    """The terminal hands the shown default back on Enter, so a boolean has to be shown the way it is answered."""
    existing = site.SiteProfile(name="lab", access_method="ssh", connect_argv=["ssh", "login"], remote_exec=True,
                                file_transfer="scp", idle_lock_minutes=180, agent_inside_cluster=False, scheduler="slurm",
                                queue="long", remote_workdir="/scratch/me", login_limit_per_day=30)
    shown = {}

    def enter(label, default):
        shown[label] = default
        return default

    kept = site.ask_profile("lab", enter, existing=existing)
    assert kept == existing
    assert shown[site._label("remote_exec")] == "y" and shown[site._label("agent_inside_cluster")] == "n"


@pytest.mark.parametrize("answer,expected", [("true", True), ("TRUE", True), ("false", False), ("yes", True), ("no", False),
                                              ("y", True), ("n", False)])
def test_yes_no_questions_accept_true_and_false_too(answer, expected):
    profile = site.ask_profile("lab", answers("", "", "", answer))
    assert profile.remote_exec is expected


def test_init_keeps_an_existing_profiles_yes_no_answers_on_enter(monkeypatch, tmp_path):
    """With the real prompt (`_ask` returns the default for an empty line), not a fake one."""
    site.save_profile(site.SiteProfile(name="lab", remote_exec=False, agent_inside_cluster=True, scheduler="slurm"), tmp_path)
    lines = iter(["y", "lab"] + [""] * 13)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    hpc = {}
    init_wizard._ask_hpc_site_profile(hpc, "slurm", False, tmp_path)
    kept = site.load_profile("lab", tmp_path)
    assert (kept.remote_exec, kept.agent_inside_cluster) == (False, True) and hpc["site_profile"] == "lab"
