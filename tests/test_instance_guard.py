"""`labhq doctor` and `labhq up` name another config that shares this one's state folder or port, or that is the PC's
primary config instead (#613). Temp folders and fake process records only: no process is started, no port opened."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from labhq import cli, doctor, instance_guard
from labhq.settings import Settings

PORT = 18911


@pytest.fixture(autouse=True)
def fake_processes(monkeypatch):
    monkeypatch.delenv("LABHQ_LAUNCHER", raising=False)
    # A record whose `created` is "gen-<pid>" is a live process; any other value is a dead one.
    monkeypatch.setattr(cli, "_process_created", lambda pid: f"gen-{pid}")
    monkeypatch.setattr(cli, "_start_background", lambda *a, **k: pytest.fail("must not start a process"))


def make_config(path: Path, state: Path, *, port: int = PORT, token: str = "unit-client", instance: str | None = None,
                url_host: str = "127.0.0.1") -> Settings:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"gateway": {"client_token": token, "runner_token": "unit-runner", "port": port,
                        "url": f"ws://{url_host}:{port}", "state_dir": str(state)},
            "runner": {"state_dir": str(state), "id": f"runner-{path.stem}"}}
    if instance:
        data["instance"] = instance
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Settings.load(str(path))


def record(settings: Settings, role: str, pid: int, *, alive: bool = True, config: str | None = None) -> Path:
    path = cli._pid_file(settings, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "role": role, "created": f"gen-{pid}" if alive else "old",
                                "config": config or settings.config_path}), encoding="utf-8")
    return path


def allow_start(monkeypatch):
    """`up` may start its two processes; they are stubbed and report healthy."""
    monkeypatch.setattr(cli, "_start_background", lambda *a, **k: 1)
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: {
        "service": "labhq gateway", "runners": [s.runner.id] if runner else []})


def last_used_by(other: Settings, state: Path) -> None:
    """The record `up` leaves in a state folder, without making `other` the PC's primary config."""
    instance_guard._write_config(state / instance_guard.LAST_FILE, other.config_path)


def fix_commands(text: str) -> str:
    return text.split("고치기:", 1)[1]


# --- (a) another config uses this config's state folder -----------------------------------------------------------


def test_a_live_process_of_another_config_in_the_same_state_folder_stops_up_and_says_how_to_open_it(
        tmp_path, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    other = make_config(tmp_path / "old-clone" / "labhq.yaml", tmp_path / "state")
    path = record(mine, "gateway", 4101, config=other.config_path)
    before = path.read_bytes()
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "같은 state 폴더를 쓰는 다른 설정 파일이 실행 중입니다" in err
    assert f'labhq -c "{other.config_path}" open' in fix_commands(err)
    assert f'labhq -c "{other.config_path}" down' in fix_commands(err)
    assert path.read_bytes() == before
    assert not (tmp_path / "state" / instance_guard.LAST_FILE).exists()


def test_a_dead_process_is_no_conflict_but_the_config_that_last_used_the_state_folder_is_named(
        tmp_path, monkeypatch, capsys):
    other = make_config(tmp_path / "old-clone" / "labhq.yaml", tmp_path / "state")
    last_used_by(other, tmp_path / "state")
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    record(mine, "gateway", 4101, alive=False, config=other.config_path)
    notices = instance_guard.check(mine)
    assert [n.key for n in notices] == ["state_last"] and not notices[0].blocking
    assert "다른 파일입니다" in notices[0].text and "--instance" in notices[0].fix
    allow_start(monkeypatch)
    cli._up_unlocked(mine)
    err = capsys.readouterr().err
    assert "경고:" in err and "마지막으로 쓴 설정 파일" in err
    assert instance_guard.last_config(mine) == mine.config_path  # the next `up` of this config is quiet


def test_a_deleted_other_config_is_no_conflict(tmp_path):
    other = make_config(tmp_path / "old-clone" / "labhq.yaml", tmp_path / "state")
    last_used_by(other, tmp_path / "state")
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    Path(other.config_path).unlink()
    assert instance_guard.check(mine) == []


def test_the_same_config_twice_is_quiet(tmp_path, monkeypatch, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    allow_start(monkeypatch)
    cli._up_unlocked(mine)
    cli._up_unlocked(mine)
    assert capsys.readouterr().err == ""
    assert instance_guard.check(mine) == []


# --- (b) a gateway on this config's port belongs to another config or another token --------------------------------


def named_instance(home: Path, name: str, state: Path, **kwargs) -> Settings:
    return make_config(home / ".labhq" / name / "labhq.yaml", state, **kwargs)


@pytest.mark.parametrize("token", ["theirs", "unit-client"], ids=["another token", "same token"])
def test_another_config_running_on_this_port_stops_up_and_names_it(tmp_path, monkeypatch, capsys, token):
    home = Path.home()
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine", token="unit-client")
    other = named_instance(home, "trial", tmp_path / "state-trial", token=token)
    record(other, "gateway", 4201)
    # The gateway on the port answers (the same token) or refuses the token; either way it is the other config's.
    monkeypatch.setattr(httpx, "request", lambda method, url, **kw: httpx.Response(
        200 if token == "unit-client" else 401, json=[], request=httpx.Request(method, url)))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert f"포트 {PORT}에는 이미 다른 설정 파일" in err
    assert f'labhq -c "{other.config_path}" open' in fix_commands(err)
    assert "gateway.port" in fix_commands(err)


def test_a_wrong_token_with_no_record_of_whose_gateway_it_is_still_gets_a_fix(tmp_path, monkeypatch, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    monkeypatch.setattr(httpx, "request", lambda method, url, **kw: httpx.Response(
        401, json={"detail": "bad client token"}, request=httpx.Request(method, url)))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "client_token이 이 gateway와 다릅니다." in err
    assert f"포트 {PORT}에 이미 labhq gateway가 떠 있는데 이 설정의 client_token과 다릅니다" in err
    assert "labhq -c <그 설정 파일> open" in fix_commands(err)


def test_a_config_on_another_port_or_not_running_is_no_conflict(tmp_path):
    home = Path.home()
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine")
    record(named_instance(home, "elsewhere", tmp_path / "state-b", port=PORT + 1), "gateway", 4301)
    record(named_instance(home, "stopped", tmp_path / "state-c"), "gateway", 4302, alive=False)
    assert instance_guard.check(mine) == []


def test_a_runner_only_pc_ignores_a_local_gateway_that_happens_to_use_the_same_port_number(tmp_path, monkeypatch):
    home = Path.home()
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine")
    record(named_instance(home, "trial", tmp_path / "state-trial"), "gateway", 4201)
    assert [n.key for n in instance_guard.check(mine)] == ["port"]
    monkeypatch.setattr(cli, "_gateway_is_local", lambda s: False)
    assert instance_guard.check(mine) == []


# --- (c) this config is not the PC's primary config ---------------------------------------------------------------


def test_the_first_successful_up_records_the_primary_config_and_a_different_config_is_warned(
        tmp_path, monkeypatch, capsys):
    allow_start(monkeypatch)
    first = make_config(tmp_path / "pi.yaml", tmp_path / "state-pi", port=PORT)
    assert instance_guard.primary_config() is None
    cli._up_unlocked(first)
    assert instance_guard.primary_config() == first.config_path
    assert capsys.readouterr().err == ""

    second = make_config(tmp_path / "old-clone" / "labhq.yaml", tmp_path / "state-old", port=PORT + 1)
    cli._up_unlocked(second)
    err = capsys.readouterr().err
    assert "이 PC의 주 설정과 다릅니다" in err and "주 설정은" in err
    assert f'labhq -c "{first.config_path}" up' in fix_commands(err)
    assert f'labhq -c "{first.config_path}" open' in fix_commands(err)
    assert f'labhq -c "{second.config_path}" up --make-primary' in fix_commands(err)
    assert instance_guard.primary_config() == first.config_path  # a later config never takes the place by itself


def test_make_primary_replaces_the_record_and_the_old_primary_is_then_the_odd_one(tmp_path, monkeypatch):
    allow_start(monkeypatch)
    first = make_config(tmp_path / "pi.yaml", tmp_path / "state-pi")
    second = make_config(tmp_path / "other.yaml", tmp_path / "state-two", port=PORT + 1)
    cli._up_unlocked(first)
    cli._up_unlocked(second, make_primary=True)
    assert instance_guard.primary_config() == second.config_path
    assert [n.key for n in instance_guard.check(first)] == ["primary"]
    assert instance_guard.check(second) == []


def test_the_up_flag_reaches_up(tmp_path, monkeypatch):
    config = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    seen = []
    monkeypatch.setattr(cli, "_up", lambda s, **kwargs: seen.append(kwargs))
    cli.main(["-c", config.config_path, "up", "--make-primary"])
    cli.main(["-c", config.config_path, "up"])
    assert seen == [{"make_primary": True}, {"make_primary": False}]


def test_a_named_instance_never_becomes_primary_and_is_never_compared_with_it(tmp_path, monkeypatch, capsys):
    allow_start(monkeypatch)
    lab = named_instance(Path.home(), "lab2", tmp_path / "state-lab2", port=PORT + 2, instance="lab2")
    cli._up_unlocked(lab)
    assert instance_guard.primary_config() is None
    primary = make_config(tmp_path / "pi.yaml", tmp_path / "state-pi")
    cli._up_unlocked(primary)
    assert instance_guard.primary_config() == primary.config_path
    assert instance_guard.check(lab) == []
    assert "주 설정" not in capsys.readouterr().err


def test_a_primary_record_whose_config_file_is_gone_is_ignored_and_replaced(tmp_path, monkeypatch):
    allow_start(monkeypatch)
    gone = make_config(tmp_path / "gone.yaml", tmp_path / "state-gone")
    cli._up_unlocked(gone)
    Path(gone.config_path).unlink()
    assert instance_guard.primary_config() is None
    new = make_config(tmp_path / "new.yaml", tmp_path / "state-new", port=PORT + 1)
    assert instance_guard.check(new) == []
    cli._up_unlocked(new)
    assert instance_guard.primary_config() == new.config_path


def test_an_up_that_fails_records_nothing(tmp_path, monkeypatch):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    allow_start(monkeypatch)
    monkeypatch.setattr(cli, "_wait_for_health", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        cli._up_unlocked(mine)
    assert instance_guard.primary_config() is None and instance_guard.last_config(mine) is None


def test_a_config_loaded_without_a_file_has_nothing_to_compare(tmp_path):
    assert instance_guard.check(Settings()) == []
    instance_guard.record_success(Settings())
    assert not (Path.home() / ".labhq").exists()


# --- doctor -------------------------------------------------------------------------------------------------------


def instance_rows(settings: Settings) -> list[dict]:
    return [row for row in doctor.collect(settings, dry_run=True)["checks"] if row["group"] == "instance"]


def test_doctor_has_no_instance_row_when_nothing_overlaps(tmp_path):
    assert instance_rows(make_config(tmp_path / "mine.yaml", tmp_path / "state")) == []


def test_doctor_warns_about_a_shared_state_folder_with_the_fix_command(tmp_path):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    other = make_config(tmp_path / "old-clone" / "labhq.yaml", tmp_path / "state")
    record(mine, "runner", 4102, config=other.config_path)
    row, = instance_rows(mine)
    assert row["name"] == "other config: state" and row["status"] == "warn"
    assert "같은 state 폴더를 쓰는 다른 설정 파일" in row["detail"]
    assert f'labhq -c "{other.config_path}" open' in row["hint"]


def test_doctor_warns_about_another_gateway_on_the_port_and_about_a_different_primary(tmp_path, monkeypatch):
    allow_start(monkeypatch)
    primary = make_config(tmp_path / "pi.yaml", tmp_path / "state-pi", port=PORT + 5)
    cli._up_unlocked(primary)
    other = named_instance(Path.home(), "trial", tmp_path / "state-trial")
    record(other, "gateway", 4201)
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine")
    rows = {row["name"]: row for row in instance_rows(mine)}
    assert set(rows) == {"other config: port", "primary config"}
    assert all(row["status"] == "warn" for row in rows.values())
    assert f'labhq -c "{other.config_path}" open' in rows["other config: port"]["hint"]
    assert f'labhq -c "{primary.config_path}" open' in rows["primary config"]["hint"]
    assert "up --make-primary" in rows["primary config"]["hint"]


def test_doctor_masks_the_home_folder_in_what_it_shows_but_not_in_the_command(tmp_path):
    home = Path.home()
    mine = make_config(home / "work" / "mine.yaml", tmp_path / "state")
    other = make_config(home / "old" / "labhq.yaml", tmp_path / "state")
    record(mine, "gateway", 4101, config=other.config_path)
    row, = instance_rows(mine)
    assert str(home) not in row["detail"] and "~/old/labhq.yaml" in row["detail"]
    assert other.config_path in row["hint"]
