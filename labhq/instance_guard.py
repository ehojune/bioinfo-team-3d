"""Warnings for a labhq config that differs from the one already running on this PC (#613).

The 10-13 trial ran an old clone's config next to the PI's tested one. They shared one state folder and said nothing
about it. Every running gateway and runner now records an *identity*: the config file it was started from, plus a
fingerprint of the settings that decide what it owns (gateway.state_dir, runner.state_dir, runner.id, the port). Both
are hashes of non-secret values; a gateway also returns them from `/api/health` (no path, no token).

`check()` compares the identity of whatever owns this config's state folder, its runner state folder or its port with
this config's identity, read-only and without a network call. `check_health()` does the same for the gateway that
answered `up`. Different identity: the owner is another config (or this config as it was before an edit), so `up`
stops instead of adopting it, and `doctor` says whose it is and what to run. Not an identity: `last` (the config that
last started here) and `primary` (the PC's first non-instance config) are warnings only.

A starting gateway or runner writes its owner record (`claim`), `up` writes the last/primary records after it has
confirmed the gateway is its own. A config with an `instance:` name is a deliberately separate lab: it never becomes
primary and is never compared with it.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .settings import Settings
from .test_guard import guard_test_path
from .util import atomic_write_text

LAST_FILE = "labhq-last-config.json"
PRIMARY_FILE = "labhq-primary-config.json"
OWNER_FILE = {"gateway": "labhq-gateway-owner.json", "runner": "labhq-runner-owner.json"}
BROKER_FILE = "labhq-broker-port-{port}.json"  # one per runner.broker_port, in the default state folder (#618)


@dataclass(frozen=True)
class Notice:
    key: str        # state_alive | runner_state | broker_port | port | port_unverified | state_last | primary
    text: str       # what differs, in plain Korean
    fix: str        # what to run or change
    blocking: bool  # `up` stops: starting would claim a config that is not the one running

    def render(self) -> str:
        return f"{'중단' if self.blocking else '경고'}: {self.text}\n  고치기: {self.fix}"


@dataclass(frozen=True)
class Identity:
    """Who a process or a state folder belongs to. `fingerprint` is None for records older than identities."""

    config_id: str
    fingerprint: str | None = None
    config: str | None = None  # the path, where a record has it; never part of what a gateway answers


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _norm(path: str | Path) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(str(path))


def same_file(a: str | Path, b: str | Path) -> bool:
    return _norm(a) == _norm(b)


def identity_of(settings: Settings) -> Identity:
    """This config's identity: its path, and the settings that decide which files and port it uses."""
    owned = {"gateway_state": _norm(settings.path(settings.gateway.state_dir)),
             "runner_state": _norm(settings.path(settings.runner.state_dir)),
             "runner_id": settings.runner.id, "port": settings.gateway.port}
    config = settings.config_path
    return Identity(_digest(_norm(config)) if config else "", _digest(json.dumps(owned, sort_keys=True)), config)


def public_identity(settings: Settings) -> dict[str, str] | None:
    """What `/api/health` returns: hashes only. None for settings that were not loaded from a file."""
    if not settings.config_path:
        return None
    identity = identity_of(settings)
    return {"config_id": identity.config_id, "fingerprint": identity.fingerprint or ""}


def from_public(value: object) -> Identity | None:
    if not isinstance(value, dict):
        return None
    config_id, fingerprint = value.get("config_id"), value.get("fingerprint")
    if not (isinstance(config_id, str) and config_id and isinstance(fingerprint, str) and fingerprint):
        return None
    return Identity(config_id, fingerprint)


def same_identity(a: Identity, b: Identity) -> bool:
    """The same config file, and, where both sides know it, the same state folders, runner id and port."""
    return a.config_id == b.config_id and (a.fingerprint is None or b.fingerprint is None
                                           or a.fingerprint == b.fingerprint)


def default_state_dir() -> Path:
    """The PC's default state folder, where the primary record lives so every config on this PC sees it."""
    path = Path(os.path.expandvars(os.path.expanduser(os.environ.get("LABHQ_STATE_DIR", "~/.labhq/state"))))
    guard_test_path(path)
    return path


def _state_dir(settings: Settings) -> Path:
    return settings.path(settings.gateway.state_dir)


def _runner_dir(settings: Settings) -> Path:
    return settings.path(settings.runner.state_dir)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _read_config(path: Path) -> str | None:
    """The config path a record holds, only while that file still exists (a deleted config is no conflict)."""
    value = (_read_json(path) or {}).get("config")
    return value if isinstance(value, str) and value and Path(value).is_file() else None


def _write_json(path: Path, value: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(value, ensure_ascii=False) + "\n")
    except OSError:
        pass  # a note for the next warning, never a reason for `up` or a daemon to fail


def _write_config(path: Path, config: str) -> None:
    _write_json(path, {"config": config})


def last_config(settings: Settings) -> str | None:
    return _read_config(_state_dir(settings) / LAST_FILE)


def primary_config() -> str | None:
    return _read_config(default_state_dir() / PRIMARY_FILE)


def record_success(settings: Settings, *, make_primary: bool = False) -> None:
    """After `up` confirmed the gateway is this config's: remember which config this state folder runs, and the PC's
    primary config once."""
    if not settings.config_path:
        return
    _write_config(_state_dir(settings) / LAST_FILE, settings.config_path)
    if settings.instance:
        return
    if make_primary or primary_config() is None:
        _write_config(default_state_dir() / PRIMARY_FILE, settings.config_path)


def _alive(record: dict) -> bool:
    from . import cli

    try:
        return (isinstance(record.get("pid"), int) and isinstance(record.get("created"), str)
                and cli._process_created(record["pid"]) == record["created"])
    except (OSError, ValueError, TypeError, cli.ProcessIdentityError):
        return False


def _record_identity(record: dict) -> Identity | None:
    config = record.get("config")
    config_id = record.get("config_id")
    if not (isinstance(config_id, str) and config_id):
        if not (isinstance(config, str) and config):
            return None
        config_id = _digest(_norm(config))
    fingerprint = record.get("fingerprint")
    return Identity(config_id, fingerprint if isinstance(fingerprint, str) and fingerprint else None,
                    config if isinstance(config, str) and config else None)


def _owner_path(settings: Settings, role: str) -> Path:
    return (_state_dir(settings) if role == "gateway" else _runner_dir(settings)) / OWNER_FILE[role]


def write_owner(settings: Settings, role: str, *, required: bool = False) -> None:
    """The running gateway or runner records itself: process, config file and identity. Best effort."""
    from . import cli

    if not settings.config_path:
        return
    try:
        pid = os.getpid()
        created = cli._process_created(pid)
    except (OSError, cli.ProcessIdentityError):
        if required:
            raise
        return
    if required and created is None:
        raise RuntimeError("Cannot identify the claiming process")
    identity = identity_of(settings)
    record = {
        "pid": pid, "created": created, "role": role, "config": settings.config_path,
        "config_id": identity.config_id, "fingerprint": identity.fingerprint}
    if required:
        atomic_write_text(_owner_path(settings, role), json.dumps(record, ensure_ascii=False) + "\n")
    else:
        _write_json(_owner_path(settings, role), record)


def _broker_path(settings: Settings) -> Path:
    return default_state_dir() / BROKER_FILE.format(port=settings.runner.broker_port)


@contextmanager
def _runner_claim_lock(settings: Settings):
    """Serialize owner inspection/publication; the stable lock file is never removed.

    OS locks disappear on exit, so a crashed claimant cannot strand a lock.
    """
    with _file_lock(_owner_path(settings, "runner").with_suffix(".lock")):
        yield


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _live_owner(settings: Settings, role: str) -> Identity | None:
    """The live process that holds this role's state folder: from its owner record."""
    record = _read_json(_owner_path(settings, role))
    return _record_identity(record) if record and _alive(record) else None


def _live_pid_identity(settings: Settings, role: str) -> Identity | None:
    """The same from `up`'s process record (older starts have no owner record; both name the config)."""
    from . import cli

    record = _read_json(cli._pid_file(settings, role))
    return _record_identity(record) if record and _alive(record) else None


def _gateway_state_owners(settings: Settings) -> list[tuple[str, Identity]]:
    """(role, identity) of every live process that holds this config's gateway state folder."""
    found = []
    for role, identity in (("gateway", _live_owner(settings, "gateway")),
                           ("gateway", _live_pid_identity(settings, "gateway")),
                           ("runner", _live_pid_identity(settings, "runner"))):
        if identity is not None:
            found.append((role, identity))
    return found


def _open_command(config: str, command: str) -> str:
    launcher = os.environ.get("LABHQ_LAUNCHER") or "labhq"
    return f'{launcher} -c "{config}" {command}'


def _candidate_configs(settings: Settings) -> list[str]:
    """Other config files this PC is known to use: the records and the named instances."""
    found: list[str] = []

    def add(path: str | Path | None) -> None:
        if (path and Path(path).is_file() and not same_file(path, settings.config_path)
                and not any(same_file(path, other) for other in found)):
            found.append(str(path))

    add(last_config(settings))
    add(primary_config())
    add(_read_config(default_state_dir() / LAST_FILE))
    root = Path.home() / ".labhq"
    guard_test_path(root)
    for path in sorted(root.glob("*/labhq.yaml")):
        add(path)
    return found


def _known_config(settings: Settings, config_id: str) -> str | None:
    """The config file behind a config id from a health answer, when this PC knows it."""
    return next((path for path in _candidate_configs(settings) if _digest(_norm(path)) == config_id), None)


def _running_on_port(settings: Settings) -> list[str]:
    """Other configs whose gateway runs now on this config's port. A process record counts only for the config it
    names: a candidate whose state folder another config holds is not the one running there."""
    from . import cli

    if not cli._gateway_is_local(settings):
        return []  # a runner-only PC: the gateway port belongs to another machine
    running = []
    for path in _candidate_configs(settings):
        try:
            other = Settings.load(path)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if other.gateway.port != settings.gateway.port:
            continue
        own = [identity for _role, identity in _gateway_state_owners(other)
               if _role == "gateway" and identity.config and same_file(identity.config, other.config_path)]
        if own:
            running.append(path)
    return running


def _shown(owner: Identity, show: Callable[[str], str]) -> str:
    return show(owner.config) if owner.config else "어느 설정인지 기록 없음"


def _state_notice(settings: Settings, role: str, owner: Identity, mine: Identity,
                  show: Callable[[str], str]) -> Notice:
    from . import cli

    if owner.config and owner.config_id == mine.config_id:
        return Notice(
            "state_alive",
            f"이 설정 파일로 시작한 {role}가 시작한 뒤 바뀐 설정(state 폴더·포트·runner id)으로 아직 돌고 있습니다. "
            "지금 설정과 달라서 같은 파일을 두 설정이 나눠 쓰게 됩니다.",
            f"`{cli._config_command(settings, 'down')}`으로 끈 뒤 `{cli._config_command(settings, 'up')}`을 다시 실행하세요.",
            True)
    other = owner.config
    commands = (f"그 사무실은 `{_open_command(other, 'open')}`으로 엽니다. 이 설정으로 바꾸려면 "
                f"`{_open_command(other, 'down')}`으로 끄고 `{cli._config_command(settings, 'up')}`을 다시 실행하세요. "
                if other else "")
    return Notice(
        "state_alive",
        f"같은 state 폴더를 쓰는 다른 설정 파일이 실행 중입니다. 다른 설정 파일({_shown(owner, show)})로 시작한 {role}가 "
        "이 설정과 같은 요청 기록·승인을 나눠 쓰고, 이 설정의 값은 적용되지 않습니다.",
        commands + "두 설정을 함께 쓰려면 한쪽의 gateway.state_dir·runner.state_dir을 다르게 하세요.",
        True)


def _runner_notice(settings: Settings, owner: Identity, mine: Identity, show: Callable[[str], str]) -> Notice:
    from . import cli

    shared = ("이 설정의 runner.state_dir(runner-<id>.sqlite3, control 폴더, 입력 hash cache)을 쓰고 있어 두 runner가 같은 "
              "파일을 쓰게 됩니다.")
    restart = (f"`{cli._config_command(settings, 'down')}`으로 끈 뒤 `{cli._config_command(settings, 'up')}`을 "
               "다시 실행하세요.")
    if owner.config_id == mine.config_id:
        return Notice("runner_state",
                      "이 설정 파일로 시작한 runner가 시작한 뒤 바뀐 설정(runner.state_dir·runner.id)으로 아직 돌고 있습니다. "
                      + shared, restart, True)
    return Notice(
        "runner_state",
        f"같은 runner state 폴더를 쓰는 다른 설정 파일이 실행 중입니다. 다른 설정 파일({_shown(owner, show)})의 runner가 " + shared,
        (f"`{_open_command(owner.config, 'down')}`으로 그 runner를 끄거나, " if owner.config else "")
        + "한쪽의 runner.state_dir을 다르게 하세요.",
        True)


def _broker_notice(settings: Settings, owner: Identity, show: Callable[[str], str]) -> Notice:
    port = settings.runner.broker_port
    return Notice(
        "broker_port",
        f"runner.broker_port {port}를 이미 다른 설정 파일({_shown(owner, show)})의 runner가 쓰고 있습니다. "
        "두 번째 runner는 이 포트를 열지 못해 시작에 실패합니다.",
        (f"`{_open_command(owner.config, 'down')}`으로 그 runner를 끄거나, " if owner.config else "")
        + "한쪽의 runner.broker_port를 다르게 하세요.",
        True)


def _port_notice(settings: Settings, port: int, other: str | None, show: Callable[[str], str], *,
                 token_mismatch: bool = False) -> Notice:
    if other is None:
        return Notice(
            "port",
            f"포트 {port}에 이미 labhq gateway가 떠 있는데 "
            + ("이 설정의 client_token과 다릅니다. " if token_mismatch else "이 설정의 gateway가 아닙니다. ")
            + "다른 설정 파일로 시작한 gateway인데, 어느 설정인지 이 PC의 기록에서 찾지 못했습니다.",
            "그 gateway를 시작한 설정 파일을 찾아 `labhq -c <그 설정 파일> open`으로 여세요. 이 설정으로 쓰려면 "
            "그 gateway를 끄거나 이 설정의 gateway.port·runner.broker_port를 바꾸세요.",
            True)
    return Notice(
        "port",
        f"포트 {port}에는 이미 다른 설정 파일({show(other)})로 시작한 gateway가 떠 있습니다. "
        "이 설정의 gateway가 아니라서 이 설정의 값은 적용되지 않습니다.",
        f"그 사무실은 `{_open_command(other, 'open')}`으로 엽니다. 이 설정으로 쓰려면 먼저 "
        f"`{_open_command(other, 'down')}`으로 끄거나, 이 설정의 gateway.port·runner.broker_port를 바꾸세요.",
        True)


def _claim_broker(settings: Settings, mine: Identity) -> Notice | None:
    """The runner's broker port belongs to one config on this PC (#618). A live runner of another config there stops
    this start. The same config's live runner keeps its record (this process would fail to bind anyway). An error
    opening, locking or writing the record is the caller's "could not acquire" notice, as for the runner state."""
    from . import cli

    path = _broker_path(settings)
    with _file_lock(path.with_suffix(".lock")):
        held = _read_json(path)
        if held and _alive(held):
            owner = _record_identity(held)
            if owner is not None and not same_identity(owner, mine):
                return _broker_notice(settings, owner, str)
            if held.get("pid") != os.getpid():
                return None
        atomic_write_text(path, json.dumps({
            "pid": os.getpid(), "created": cli._process_created(os.getpid()), "role": "runner",
            "config": settings.config_path, "config_id": mine.config_id, "fingerprint": mine.fingerprint,
            "port": settings.runner.broker_port}, ensure_ascii=False) + chr(10))
    return None


def check(settings: Settings, *, show: Callable[[str], str] = str, token_mismatch: bool = False) -> list[Notice]:
    """Every difference worth a warning, read-only. `show` formats a config path inside the sentence (doctor masks
    home folders); the fix command always carries the real path. `token_mismatch`: a gateway on this config's port
    already refused its client token."""
    if not settings.config_path:
        return []
    from . import cli

    mine = identity_of(settings)
    notices: list[Notice] = []
    named: set[str] = set()

    for role, owner in _gateway_state_owners(settings):
        if not same_identity(owner, mine):
            notices.append(_state_notice(settings, role, owner, mine, show))
            named.add(owner.config_id)
            break

    runner_owner = _live_owner(settings, "runner")
    if runner_owner is not None and not same_identity(runner_owner, mine) and runner_owner.config_id not in named:
        notices.append(_runner_notice(settings, runner_owner, mine, show))
        named.add(runner_owner.config_id)

    port = settings.gateway.port
    for other in _running_on_port(settings):
        if _digest(_norm(other)) in named:
            continue
        notices.append(_port_notice(settings, port, other, show))
        named.add(_digest(_norm(other)))
    if token_mismatch and not any(n.key in ("state_alive", "runner_state", "port") for n in notices):
        notices.append(_port_notice(settings, port, None, show, token_mismatch=True))

    if not any(n.key == "state_alive" for n in notices):
        last = last_config(settings)
        if last and not same_file(last, settings.config_path):
            notices.append(Notice(
                "state_last",
                f"이 설정의 state 폴더를 마지막으로 쓴 설정 파일은 다른 파일입니다({show(last)}). "
                "두 설정이 같은 요청 기록·승인 대기를 나눠 쓰게 됩니다.",
                "따로 쓰려면 한쪽의 gateway.state_dir·runner.state_dir을 다르게 하거나 `labhq --instance <이름> init`으로 "
                "별도 인스턴스를 만드세요. 하나만 쓸 거면 쓰지 않는 설정 파일을 치우세요.",
                False))

    primary = None if settings.instance else primary_config()
    if primary and not same_file(primary, settings.config_path):
        notices.append(Notice(
            "primary",
            f"이 설정 파일은 이 PC의 주 설정과 다릅니다. 주 설정은 {show(primary)}입니다. "
            "지금 보는 사무실이 주 설정으로 시험하던 곳이 아닐 수 있습니다.",
            f"주 설정을 쓰려면 `{_open_command(primary, 'up')}`(열기: `{_open_command(primary, 'open')}`). "
            f"지금 설정을 주 설정으로 바꾸려면 `{cli._config_command(settings, 'up --make-primary')}`.",
            False))
    return notices


def check_health(settings: Settings, health: dict, *, started_here: bool,
                 show: Callable[[str], str] = str) -> tuple[list[Notice], bool]:
    """Whose gateway answered `up`: (notices, confirmed). Confirmed means it is this config's own gateway, so `up`
    may record it. A gateway of another identity is never adopted, whatever its token; one that predates identities
    (no `identity` in its answer) is trusted only when this config's own live record or this very `up` started it."""
    mine = identity_of(settings)
    theirs = from_public(health.get("identity")) if isinstance(health, dict) else None
    if theirs is not None:
        if same_identity(theirs, mine):
            return [], True
        if theirs.config_id == mine.config_id:
            return [Notice(
                "port",
                f"포트 {settings.gateway.port}의 gateway는 이 설정 파일로 시작했지만 시작한 뒤 바뀐 설정(state 폴더·포트·"
                "runner id)으로 돌고 있습니다.",
                "그 gateway를 끈 뒤 다시 시작하세요: `labhq down` 다음에 `labhq up`.", True)], False
        return [_port_notice(settings, settings.gateway.port, _known_config(settings, theirs.config_id), show)], False
    if started_here or any(same_identity(owner, mine) for _role, owner in _gateway_state_owners(settings)
                           if _role == "gateway"):
        return [], True
    return [Notice(
        "port_unverified",
        f"포트 {settings.gateway.port}의 gateway가 어느 설정 파일로 시작했는지 알 수 없습니다(설정 정보를 알려 주지 않는 이전 판). "
        "이 설정의 gateway라고 확인하지 못해 기록은 남기지 않습니다.",
        "의심스러우면 그 gateway를 끄고 이 설정으로 `labhq up`을 다시 실행하세요.", False)], False


def claim(settings: Settings, role: str) -> Notice | None:
    """A gateway or runner that is starting records itself as the owner of its state folder. Another identity that
    already holds it, alive, stops the start: the notice is returned and nothing is written."""
    if not settings.config_path:
        return None
    mine = identity_of(settings)
    if role == "gateway":
        owners = [(r, owner) for r, owner in _gateway_state_owners(settings) if r == "gateway"]
        for _role, owner in owners:
            if not same_identity(owner, mine):
                return _state_notice(settings, role, owner, mine, str)
    else:
        try:
            with _runner_claim_lock(settings):
                owner = _live_owner(settings, "runner")
                if owner is not None and not same_identity(owner, mine):
                    return _runner_notice(settings, owner, mine, str)
                if (notice := _claim_broker(settings, mine)) is not None:
                    return notice
                write_owner(settings, role, required=True)
        except (OSError, RuntimeError) as exc:
            return Notice("runner_state", "runner state 소유권을 획득하지 못했습니다.",
                          f"다른 runner의 시작이 끝난 뒤 다시 시도하세요 ({type(exc).__name__}).", True)
        return None
    write_owner(settings, role)
    return None


DOCTOR_NAME = {"state_alive": "other config: state", "state_last": "other config: state",
               "runner_state": "other config: runner", "port": "other config: port",
               "broker_port": "other config: broker port",
               "port_unverified": "other config: port", "primary": "primary config"}
