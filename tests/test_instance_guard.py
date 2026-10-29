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
                url_host: str = "127.0.0.1", runner_state: Path | None = None,
                runner_id: str | None = None, broker_port: int | None = None) -> Settings:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"gateway": {"client_token": token, "runner_token": "unit-runner", "port": port,
                        "url": f"ws://{url_host}:{port}", "state_dir": str(state)},
            "runner": {"state_dir": str(runner_state or state), "id": runner_id or f"runner-{path.stem}",
                       **({"broker_port": broker_port} if broker_port else {})}}
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


# --- identity: whatever owns the state, the runner state or the port is compared with this config ------------------


def answer(settings: Settings, claims: Settings | None = None) -> dict:
    """A gateway's /api/health answer with this config's runner connected, claiming the identity of `claims`."""
    health = {"service": "labhq gateway", "runners": [settings.runner.id]}
    if claims is not None:
        health["identity"] = instance_guard.public_identity(claims)
    return health


def test_the_fingerprint_follows_the_state_folders_runner_id_and_port_and_never_a_secret(tmp_path):
    base = make_config(tmp_path / "a.yaml", tmp_path / "s", runner_id="lab")
    same = make_config(tmp_path / "a.yaml", tmp_path / "s", runner_id="lab", token="another-token")
    assert instance_guard.identity_of(base) == instance_guard.identity_of(same)
    for change in ({"port": PORT + 1}, {"runner_id": "other"}):
        changed = make_config(tmp_path / "a.yaml", tmp_path / "s", **{"runner_id": "lab", **change})
        assert instance_guard.identity_of(changed).fingerprint != instance_guard.identity_of(base).fingerprint
        assert instance_guard.identity_of(changed).config_id == instance_guard.identity_of(base).config_id
    moved = make_config(tmp_path / "a.yaml", tmp_path / "t", runner_id="lab")
    assert instance_guard.identity_of(moved).fingerprint != instance_guard.identity_of(base).fingerprint
    elsewhere = make_config(tmp_path / "b.yaml", tmp_path / "s", runner_id="lab")
    assert instance_guard.identity_of(elsewhere).config_id != instance_guard.identity_of(base).config_id


def test_the_gateway_health_answer_carries_the_identity_and_no_path_or_token(tmp_path):
    from fastapi.testclient import TestClient

    from labhq.gateway.server import create_app

    settings = make_config(tmp_path / "labhq.yaml", tmp_path / "state", token="very-private-client-token")
    body = TestClient(create_app(settings)).get("/api/health")
    assert body.json()["identity"] == instance_guard.public_identity(settings)
    assert settings.config_path not in body.text and "very-private-client-token" not in body.text
    assert str(tmp_path) not in body.text
    bare = Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state2")}})
    assert "identity" not in TestClient(create_app(bare)).get("/api/health").json()


# P1: two runners must never share runner-<id>.sqlite3, the control folder or the input hash cache.
@pytest.mark.parametrize("same_id", [True, False], ids=["same runner id", "other runner id"])
def test_another_configs_live_runner_in_the_same_runner_state_folder_blocks_up_and_doctor(
        tmp_path, capsys, same_id):
    shared = tmp_path / "runner-shared"
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine", runner_state=shared, runner_id="lab")
    other = make_config(tmp_path / "old-clone" / "labhq.yaml", tmp_path / "state-old", port=PORT + 1,
                        runner_state=shared, runner_id="lab" if same_id else "old")
    instance_guard.write_owner(other, "runner")  # this process stands in for the other config's running runner
    notices = instance_guard.check(mine)
    assert [n.key for n in notices] == ["runner_state"] and notices[0].blocking
    assert "같은 runner state 폴더" in notices[0].text and "control 폴더" in notices[0].text
    assert f'labhq -c "{other.config_path}" down' in notices[0].fix
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2 and "같은 runner state 폴더" in capsys.readouterr().err
    row, = instance_rows(mine)
    assert row["name"] == "other config: runner" and row["status"] == "warn"


def test_a_dead_runner_owner_or_the_same_config_is_no_runner_conflict(tmp_path, monkeypatch):
    shared = tmp_path / "runner-shared"
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine", runner_state=shared)
    other = make_config(tmp_path / "old.yaml", tmp_path / "state-old", port=PORT + 1, runner_state=shared)
    instance_guard.write_owner(mine, "runner")
    assert instance_guard.check(mine) == []  # my own runner
    instance_guard.write_owner(other, "runner")
    assert [n.key for n in instance_guard.check(mine)] == ["runner_state"]
    monkeypatch.setattr(cli, "_process_created", lambda pid: "a-later-process")  # the owner has exited
    assert instance_guard.check(mine) == []


def test_a_starting_runner_or_gateway_is_refused_while_another_config_holds_its_state(tmp_path, capsys):
    shared = tmp_path / "runner-shared"
    first = make_config(tmp_path / "first.yaml", tmp_path / "state-1", runner_state=shared)
    second = make_config(tmp_path / "second.yaml", tmp_path / "state-2", port=PORT + 1, runner_state=shared)
    assert instance_guard.claim(first, "runner") is None  # records itself as the owner
    assert instance_guard.claim(first, "runner") is None  # the same config again is not a conflict
    refused = instance_guard.claim(second, "runner")
    assert refused is not None and refused.blocking and refused.key == "runner_state"
    with pytest.raises(SystemExit) as stopped:
        cli._claim_or_exit(second, "runner")
    assert stopped.value.code == 2 and "같은 runner state 폴더" in capsys.readouterr().err
    # a gateway claims its own state folder the same way
    gateway_one = make_config(tmp_path / "g1.yaml", tmp_path / "gstate")
    gateway_two = make_config(tmp_path / "g2.yaml", tmp_path / "gstate", port=PORT + 2)
    assert instance_guard.claim(gateway_one, "gateway") is None
    assert instance_guard.claim(gateway_two, "gateway").key == "state_alive"


def test_runner_claim_race_has_one_winner_and_loser_preserves_owner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    shared = tmp_path / "shared"
    first = make_config(tmp_path / "one.yaml", tmp_path / "s1", runner_state=shared)
    second = make_config(tmp_path / "two.yaml", tmp_path / "s2", runner_state=shared, port=PORT + 1)
    inspected, publish = Event(), Event()
    original = instance_guard._live_owner

    def paused(settings, role):
        owner = original(settings, role)
        if settings is first:
            inspected.set()
            assert publish.wait(5)
        return owner

    monkeypatch.setattr(instance_guard, "_live_owner", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(instance_guard.claim, first, "runner")
        try:
            assert inspected.wait(5)
            refused = pool.submit(instance_guard.claim, second, "runner").result(timeout=5)
            assert refused is not None and refused.blocking
        finally:
            publish.set()
        assert winner.result(timeout=5) is None
    path = instance_guard._owner_path(first, "runner")
    before = path.read_bytes()
    assert instance_guard.claim(second, "runner").blocking
    assert path.read_bytes() == before
    assert json.loads(before)["config"] == first.config_path


def test_runner_claim_recovers_stale_owner_and_released_lock(tmp_path, monkeypatch):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    path = instance_guard._owner_path(mine, "runner")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 999, "created": "old", "config": "old.yaml"}), encoding="utf-8")
    with pytest.raises(ValueError):
        with instance_guard._runner_claim_lock(mine):
            raise ValueError("interrupted claimant")
    assert instance_guard.claim(mine, "runner") is None
    assert json.loads(path.read_text(encoding="utf-8"))["config"] == mine.config_path


def test_runner_claim_cannot_succeed_without_owner_publication(tmp_path, monkeypatch):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")

    def fail(*args, **kwargs):
        raise OSError("disk error")

    monkeypatch.setattr(instance_guard, "atomic_write_text", fail)
    assert instance_guard.claim(mine, "runner").blocking
    assert not instance_guard._owner_path(mine, "runner").exists()


def test_runner_claim_lock_excludes_another_process(tmp_path):
    import subprocess
    import sys

    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    command = [sys.executable, "-c", (
        "import sys\n"
        "from labhq import cli, instance_guard\n"
        "from labhq.settings import Settings\n"
        "cli._process_created = lambda pid: 'fixture-generation'\n"
        "notice = instance_guard.claim(Settings.load(sys.argv[1]), 'runner')\n"
        "print('blocked' if notice else 'claimed')\n"
    ), mine.config_path]
    with instance_guard._runner_claim_lock(mine):
        blocked = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    assert blocked.stdout.strip() == "blocked"
    assert not instance_guard._owner_path(mine, "runner").exists()
    recovered = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    assert recovered.stdout.strip() == "claimed"


# P1: a gateway on the port that answers with the same token is not adopted unless it is this config's.
def test_a_gateway_with_the_same_token_from_an_unknown_config_is_not_adopted_or_recorded(
        tmp_path, monkeypatch, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine", runner_id="lab")
    unknown = make_config(tmp_path / "checkout-elsewhere" / "config" / "labhq.yaml", tmp_path / "state-x",
                          runner_id="lab")  # same token, same port, same runner id, listed nowhere
    monkeypatch.setattr(cli, "_health", lambda s: answer(mine, unknown))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "다른 설정 파일로 시작한 gateway" in err and "찾지 못했습니다" in err
    assert instance_guard.last_config(mine) is None and instance_guard.primary_config() is None


def test_a_gateway_of_a_known_config_is_named_with_the_command_that_opens_it(tmp_path, monkeypatch, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine")
    trial = named_instance(Path.home(), "trial", tmp_path / "state-trial")  # known from ~/.labhq, no record of running
    monkeypatch.setattr(cli, "_health", lambda s: answer(mine, trial))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert f"포트 {PORT}에는 이미 다른 설정 파일" in err
    assert f'labhq -c "{trial.config_path}" open' in fix_commands(err)
    assert instance_guard.primary_config() is None


def test_a_gateway_started_from_this_config_before_an_edit_is_not_adopted_either(tmp_path, monkeypatch, capsys):
    before = make_config(tmp_path / "labhq.yaml", tmp_path / "state-old")
    after = make_config(tmp_path / "labhq.yaml", tmp_path / "state-new")  # the same file, edited since the start
    monkeypatch.setattr(cli, "_health", lambda s: answer(after, before))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(after)
    assert stopped.value.code == 2
    assert "시작한 뒤 바뀐 설정" in capsys.readouterr().err and instance_guard.last_config(after) is None


def test_a_gateway_that_answers_with_this_configs_identity_is_adopted_and_recorded(tmp_path, monkeypatch):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    monkeypatch.setattr(cli, "_health", lambda s: answer(mine, mine))
    cli._up_unlocked(mine)  # nothing to start
    assert instance_guard.last_config(mine) == mine.config_path
    assert instance_guard.primary_config() == mine.config_path


def test_a_gateway_that_could_not_take_the_port_does_not_pass_for_the_one_just_started(
        tmp_path, monkeypatch, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state-mine", runner_id="lab")
    other = make_config(tmp_path / "elsewhere" / "labhq.yaml", tmp_path / "state-x", runner_id="lab")
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_start_background", lambda *a, **k: 1)  # the new gateway lost the race for the port
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, *, runner, timeout=20: answer(mine, other))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(mine)
    assert stopped.value.code == 2
    assert "다른 설정 파일로 시작한 gateway" in capsys.readouterr().err
    assert instance_guard.last_config(mine) is None


def test_a_gateway_without_an_identity_is_trusted_only_with_this_configs_own_live_record(
        tmp_path, monkeypatch, capsys):
    mine = make_config(tmp_path / "mine.yaml", tmp_path / "state")
    monkeypatch.setattr(cli, "_health", lambda s: answer(mine))  # an older gateway: no identity in its answer
    cli._up_unlocked(mine)
    err = capsys.readouterr().err
    assert "알 수 없습니다" in err and "기록은 남기지 않습니다" in err
    assert instance_guard.last_config(mine) is None and instance_guard.primary_config() is None
    record(mine, "gateway", 4101)  # now this config's own live process record vouches for it
    cli._up_unlocked(mine)
    assert capsys.readouterr().err == "" and instance_guard.last_config(mine) == mine.config_path


# P2: a process record counts for a candidate only when it names that candidate.
def test_a_candidate_whose_state_folder_another_config_holds_is_not_the_one_running_on_the_port(tmp_path):
    home = Path.home()
    shared = tmp_path / "state-shared"
    candidate = named_instance(home, "a", shared, port=PORT)  # A would use this port
    running = named_instance(home, "b", shared, port=PORT + 1)  # B is what runs, in A's state folder
    record(candidate, "gateway", 4401, config=running.config_path)
    new = make_config(tmp_path / "c.yaml", tmp_path / "state-c", port=PORT)  # C takes A's port, which is free
    assert instance_guard.check(new) == []
    record(candidate, "gateway", 4402)  # now A's own process runs there
    assert [n.key for n in instance_guard.check(new)] == ["port"]


def test_a_second_runner_on_the_same_broker_port_is_refused_before_it_starts(tmp_path, capsys):
    # #618 P2: everything else differs (config, state folders, runner id, gateway port), only the broker port is shared.
    first = make_config(tmp_path / "first.yaml", tmp_path / "s1", runner_state=tmp_path / "r1", broker_port=PORT + 50)
    second = make_config(tmp_path / "second.yaml", tmp_path / "s2", port=PORT + 1, runner_state=tmp_path / "r2",
                         broker_port=PORT + 50)
    assert instance_guard.claim(first, "runner") is None
    assert instance_guard.claim(first, "runner") is None  # the same config again is not a conflict
    refused = instance_guard.claim(second, "runner")
    assert refused is not None and refused.blocking and refused.key == "broker_port"
    assert str(PORT + 50) in refused.text and f'labhq -c "{first.config_path}" down' in refused.fix
    assert not instance_guard._owner_path(second, "runner").exists(), "the refused runner claims nothing"
    with pytest.raises(SystemExit) as stopped:
        cli._claim_or_exit(second, "runner")
    assert stopped.value.code == 2 and "runner.broker_port" in capsys.readouterr().err


def test_runners_on_different_broker_ports_do_not_conflict(tmp_path):
    first = make_config(tmp_path / "first.yaml", tmp_path / "s1", runner_state=tmp_path / "r1", broker_port=PORT + 50)
    second = make_config(tmp_path / "second.yaml", tmp_path / "s2", port=PORT + 1, runner_state=tmp_path / "r2",
                         broker_port=PORT + 51)
    assert instance_guard.claim(first, "runner") is None
    assert instance_guard.claim(second, "runner") is None


def test_a_dead_broker_port_owner_is_recovered_and_a_refused_claim_leaves_the_record_alone(tmp_path, monkeypatch):
    first = make_config(tmp_path / "first.yaml", tmp_path / "s1", runner_state=tmp_path / "r1", broker_port=PORT + 50)
    second = make_config(tmp_path / "second.yaml", tmp_path / "s2", port=PORT + 1, runner_state=tmp_path / "r2",
                         broker_port=PORT + 50)
    assert instance_guard.claim(first, "runner") is None
    path = instance_guard._broker_path(first)
    before = path.read_bytes()
    assert instance_guard.claim(second, "runner").key == "broker_port"
    assert path.read_bytes() == before and json.loads(before)["config"] == first.config_path
    monkeypatch.setattr(cli, "_process_created", lambda pid: "a-later-process")  # the first runner has exited
    assert instance_guard.claim(second, "runner") is None
    assert json.loads(path.read_text(encoding="utf-8"))["config"] == second.config_path


def test_the_same_configs_other_live_runner_keeps_its_broker_record(tmp_path):
    # #618 review: a second launch of the same config must not take over the first runner's record.
    config = make_config(tmp_path / "a.yaml", tmp_path / "s", runner_state=tmp_path / "r", broker_port=PORT + 60)
    assert instance_guard.claim(config, "runner") is None
    path = instance_guard._broker_path(config)
    held = json.loads(path.read_text(encoding="utf-8"))
    held["pid"] += 1  # another live process of this same config
    held["created"] = f"gen-{held['pid']}"
    path.write_text(json.dumps(held), encoding="utf-8")
    before = path.read_bytes()
    assert instance_guard.claim(config, "runner") is None
    assert path.read_bytes() == before
