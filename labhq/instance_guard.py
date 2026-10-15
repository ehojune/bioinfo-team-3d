"""Warnings for a labhq config that differs from the one already running on this PC (#613).

The 10-13 trial ran an old clone's config next to the PI's tested one. They shared one state folder and said nothing
about it. `labhq up` and `labhq doctor` now check, read-only and without a network call:

* state: another config file uses this config's state folder (a live process record names it, or the last
  successful `up` there used it);
* port: another config's gateway is running on this config's port, or a gateway answers there with another token;
* primary: this config is not the PC's primary config, the first non-instance config whose `up` succeeded.

`up` writes two small records after it succeeds: the config it started, in the config's own state folder, and the
primary config, in the PC's default state folder (once, or again with `up --make-primary`). A config with an
`instance:` name is a deliberately separate lab: it never becomes primary and is never compared with it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .settings import Settings
from .test_guard import guard_test_path
from .util import atomic_write_text

LAST_FILE = "labhq-last-config.json"
PRIMARY_FILE = "labhq-primary-config.json"


@dataclass(frozen=True)
class Notice:
    key: str        # state_alive | state_last | port | primary
    text: str       # what differs, in plain Korean
    fix: str        # what to run or change
    blocking: bool  # `up` stops: starting would claim a config that is not the one running

    def render(self) -> str:
        return f"{'중단' if self.blocking else '경고'}: {self.text}\n  고치기: {self.fix}"


def same_file(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return str(a) == str(b)


def default_state_dir() -> Path:
    """The PC's default state folder, where the primary record lives so every config on this PC sees it."""
    path = Path(os.path.expandvars(os.path.expanduser(os.environ.get("LABHQ_STATE_DIR", "~/.labhq/state"))))
    guard_test_path(path)
    return path


def _state_dir(settings: Settings) -> Path:
    return settings.path(settings.gateway.state_dir)


def _read_config(path: Path) -> str | None:
    """The config path a record holds, only while that file still exists (a deleted config is no conflict)."""
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("config")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value and Path(value).is_file() else None


def _write_config(path: Path, config: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"config": config}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # a note for the next warning, never a reason for `up` to fail


def last_config(settings: Settings) -> str | None:
    return _read_config(_state_dir(settings) / LAST_FILE)


def primary_config() -> str | None:
    return _read_config(default_state_dir() / PRIMARY_FILE)


def record_success(settings: Settings, *, make_primary: bool = False) -> None:
    """After `up` succeeded: remember which config this state folder runs, and the PC's primary config once."""
    if not settings.config_path:
        return
    _write_config(_state_dir(settings) / LAST_FILE, settings.config_path)
    if settings.instance:
        return
    if make_primary or primary_config() is None:
        _write_config(default_state_dir() / PRIMARY_FILE, settings.config_path)


def _running_record(settings: Settings, role: str) -> dict | None:
    """The role's process record while its process still exists; None otherwise. Reads only, removes nothing."""
    from . import cli

    try:
        record = json.loads(cli._pid_file(settings, role).read_text(encoding="utf-8"))
        if not (isinstance(record, dict) and isinstance(record.get("pid"), int)
                and isinstance(record.get("created"), str)):
            return None
        return record if cli._process_created(record["pid"]) == record["created"] else None
    except (OSError, ValueError, TypeError, cli.ProcessIdentityError):
        return None


def _open_command(settings: Settings, config: str, command: str) -> str:
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


def _running_on_port(settings: Settings) -> list[str]:
    """Other configs whose gateway runs now on this config's port while this config's gateway is not theirs."""
    from . import cli

    if not cli._gateway_is_local(settings):
        return []  # a runner-only PC: the gateway port belongs to another machine
    running = []
    for path in _candidate_configs(settings):
        try:
            other = Settings.load(path)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if other.gateway.port == settings.gateway.port and _running_record(other, "gateway"):
            running.append(path)
    return running


def check(settings: Settings, *, show: Callable[[str], str] = str, token_mismatch: bool = False) -> list[Notice]:
    """Every difference worth a warning, read-only. `show` formats a config path inside the sentence (doctor masks
    home folders); the fix command always carries the real path. `token_mismatch`: a gateway on this config's port
    already refused its client token."""
    if not settings.config_path:
        return []
    from . import cli

    notices: list[Notice] = []
    named: set[str] = set()

    for role in ("gateway", "runner"):
        record = _running_record(settings, role)
        other = record.get("config") if record else None
        if isinstance(other, str) and other and not same_file(other, settings.config_path):
            notices.append(Notice(
                "state_alive",
                f"같은 state 폴더를 쓰는 다른 설정 파일이 실행 중입니다. 다른 설정 파일({show(other)})로 시작한 {role}가 "
                "이 설정과 같은 요청 기록·승인을 나눠 쓰고, 이 설정의 값은 적용되지 않습니다.",
                f"그 사무실은 `{_open_command(settings, other, 'open')}`으로 엽니다. 이 설정으로 바꾸려면 "
                f"`{_open_command(settings, other, 'down')}`으로 끄고 `{cli._config_command(settings, 'up')}`을 "
                "다시 실행하세요. 두 설정을 함께 쓰려면 한쪽의 gateway.state_dir·runner.state_dir을 다르게 하세요.",
                True))
            named.add(os.path.normcase(os.path.realpath(other)))
            break

    port = settings.gateway.port
    for other in _running_on_port(settings):
        if os.path.normcase(os.path.realpath(other)) in named:
            continue
        notices.append(Notice(
            "port",
            f"포트 {port}에는 이미 다른 설정 파일({show(other)})로 시작한 gateway가 떠 있습니다. "
            "이 설정의 gateway가 아니라서 이 설정의 값은 적용되지 않습니다.",
            f"그 사무실은 `{_open_command(settings, other, 'open')}`으로 엽니다. 이 설정으로 쓰려면 먼저 "
            f"`{_open_command(settings, other, 'down')}`으로 끄거나, 이 설정의 gateway.port·runner.broker_port를 바꾸세요.",
            True))
        named.add(os.path.normcase(os.path.realpath(other)))
    if token_mismatch and not any(n.key in ("state_alive", "port") for n in notices):
        notices.append(Notice(
            "port",
            f"포트 {port}에 이미 labhq gateway가 떠 있는데 이 설정의 client_token과 다릅니다. "
            "다른 설정 파일로 시작한 gateway인데, 어느 설정인지 이 PC의 기록에서 찾지 못했습니다.",
            "그 gateway를 시작한 설정 파일을 찾아 `labhq -c <그 설정 파일> open`으로 여세요. 이 설정으로 쓰려면 "
            "그 gateway를 끄거나 이 설정의 gateway.port·runner.broker_port를 바꾸세요.",
            True))

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
            f"주 설정을 쓰려면 `{_open_command(settings, primary, 'up')}`(열기: `{_open_command(settings, primary, 'open')}`). "
            f"지금 설정을 주 설정으로 바꾸려면 `{cli._config_command(settings, 'up --make-primary')}`.",
            False))
    return notices


DOCTOR_NAME = {"state_alive": "other config: state", "state_last": "other config: state",
               "port": "other config: port", "primary": "primary config"}
