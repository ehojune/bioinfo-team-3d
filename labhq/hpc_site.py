"""Site profile: how this user reaches their HPC (#1300, part of #1295).

How a cluster is reached differs by institution: direct ssh, a gateway tunnel, a VPN, a file-transfer window. The agent
asks the user and keeps the answers as a local profile at ``~/.labhq/sites/<name>.yaml``, outside any repository. This
module holds the schema, the checker, the question flow and the storage path. It knows no institution: every value is
the user's, and a value the user does not know stays empty and is asked again, never guessed.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MARKER = "labhq_site_profile"  # first key of every profile; scripts/check_public.sh looks for it in tracked files
VERSION = 1
NAME_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"
_NAME = re.compile(NAME_PATTERN)
_SECRET = re.compile(r"(?i)(pass(?:word|wd)?|secret|token|otp)\s*=|--(?:password|passwd|token)\b|sshpass")
# What the profile still has to learn before a session can use it; None means "not known yet".
DECISIONS = ("access_method", "remote_exec", "file_transfer", "idle_lock_minutes", "agent_inside_cluster", "scheduler")


class SiteProfileError(ValueError):
    pass


class SiteProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # The key in the file is MARKER; the attribute has another name so no source line looks like a profile to the public check.
    format_version: int = Field(default=VERSION, alias=MARKER)
    name: str
    # How the login is reached. `connect_argv` is the command that opens it (no secret in it); a gateway whose local port
    # must be looked up says how in `port_discovery`.
    access_method: Literal["ssh", "gateway_tunnel", "vpn", "other"] | None = None
    connect_argv: list[str] = Field(default_factory=list)
    port_discovery: str | None = None
    # Texts the connect command prints when a person has to open the tunnel first (a site with a manual tunnel). Only
    # these make the session manager wait for a person; any other connect failure is retried (#1295 C).
    tunnel_texts: list[str] = Field(default_factory=list)
    remote_exec: bool | None = None  # can commands be run on the login node from here
    file_transfer: Literal["scp", "sftp", "rsync", "gateway_window", "none", "other"] | None = None
    file_transfer_note: str | None = None
    idle_lock_minutes: int | None = Field(default=None, ge=1)  # the site locks a session after this long without a command
    agent_inside_cluster: bool | None = None  # may an agent run on the cluster
    scheduler: Literal["sge", "pbs", "slurm", "none"] | None = None
    queue: str | None = None
    remote_workdir: str | None = None
    # The user's own limits; `hpc.login_limit_*` in the labhq config is what is enforced (#1298).
    login_limit_per_request: int | None = Field(default=None, ge=1)
    login_limit_per_day: int | None = Field(default=None, ge=1)
    notes: str | None = None

    @field_validator("format_version")
    @classmethod
    def known_version(cls, value: int) -> int:
        if value != VERSION:
            raise ValueError(f"unsupported profile version {value} (this labhq reads {VERSION})")
        return value

    @field_validator("name")
    @classmethod
    def slug(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError(f"name must match {NAME_PATTERN}")
        return value

    @field_validator("connect_argv")
    @classmethod
    def argv_without_secrets(cls, value: list[str]) -> list[str]:
        for word in value:
            if not isinstance(word, str) or not word.strip() or "\n" in word or "\x00" in word:
                raise ValueError("connect_argv holds single-line, non-empty words")
            if _SECRET.search(word):
                raise ValueError("connect_argv must not carry a password, token or key text: use an agent or key file")
        return value

    @field_validator("remote_workdir")
    @classmethod
    def absolute_remote_path(cls, value: str | None) -> str | None:
        if value is not None and (not value.startswith("/") or ".." in value.split("/") or "\n" in value):
            raise ValueError("remote_workdir is an absolute POSIX path without '..'")
        return value


def unknown_fields(profile: SiteProfile) -> list[str]:
    """The decisions not answered yet, plus the connect command when an access method is set without one."""
    unknown = [key for key in DECISIONS if getattr(profile, key) is None]
    if profile.access_method and not profile.connect_argv and profile.access_method != "vpn":
        unknown.append("connect_argv")
    return unknown


def problems(profile: SiteProfile) -> list[str]:
    """Contradictions between answers. Empty answers are not problems: `unknown_fields` lists them."""
    found = []
    if profile.remote_exec is False and profile.scheduler not in (None, "none"):
        found.append("remote_exec is false but a scheduler is set: a scheduler is driven by remote commands")
    if profile.remote_exec is False and profile.connect_argv and profile.access_method == "ssh":
        found.append("access_method is ssh but remote_exec is false")
    if profile.file_transfer == "none" and profile.remote_workdir:
        found.append("remote_workdir is set but file_transfer is none")
    return found


# ----- where profiles live -----

def sites_dir(environ: dict[str, str] | None = None) -> Path:
    """`~/.labhq/sites`, or `LABHQ_SITES_DIR`: always outside the repository (see `outside_repo`)."""
    env = os.environ if environ is None else environ
    override = env.get("LABHQ_SITES_DIR")
    return Path(override).expanduser() if override else Path.home() / ".labhq" / "sites"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def outside_repo(path: Path) -> bool:
    """True when `path` is neither inside this checkout nor under any folder holding a `.git` entry."""
    target = path.expanduser().resolve()
    root = repo_root()
    if target == root or root in target.parents:
        return False
    return not any((folder / ".git").exists() for folder in (target, *target.parents))


def profile_path(name: str, directory: Path | None = None) -> Path:
    if not _NAME.fullmatch(name):
        raise SiteProfileError(f"profile name must match {NAME_PATTERN}")
    return (directory if directory is not None else sites_dir()) / f"{name}.yaml"


def dump(profile: SiteProfile) -> str:
    data = profile.model_dump(mode="json", by_alias=True)
    ordered = {MARKER: data.pop(MARKER), **data}
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False)


def save_profile(profile: SiteProfile, directory: Path | None = None) -> Path:
    """Write the profile, refusing a folder that sits inside a repository. The file is private to the user."""
    path = profile_path(profile.name, directory)
    if not outside_repo(path.parent):
        raise SiteProfileError("a site profile is saved outside any repository (it names this user's hosts and paths)")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{profile.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(dump(profile))
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def parse_profile(text: str) -> SiteProfile:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SiteProfileError("not valid YAML") from error
    if not isinstance(data, dict):
        raise SiteProfileError("a profile is a YAML mapping")
    if MARKER not in data:  # the public check looks for this key, so a profile without it would not be seen there
        raise SiteProfileError(f"the first key of a profile is {MARKER}: {VERSION}")
    try:
        return SiteProfile.model_validate(data)
    except ValidationError as error:
        raise SiteProfileError("; ".join(f"{'.'.join(map(str, e['loc'])) or 'profile'}: {e['msg']}" for e in error.errors())) from None


def load_profile(name: str, directory: Path | None = None) -> SiteProfile:
    path = profile_path(name, directory)
    try:
        profile = parse_profile(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SiteProfileError(f"profile {name} cannot be read") from error
    if profile.name != name:
        raise SiteProfileError(f"profile file {name}.yaml names itself {profile.name}")
    return profile


def list_profiles(directory: Path | None = None) -> list[str]:
    folder = directory if directory is not None else sites_dir()
    try:
        return sorted(p.stem for p in folder.glob("*.yaml") if _NAME.fullmatch(p.stem))
    except OSError:
        return []


def check_file(path: Path) -> list[str]:
    """Everything wrong with one profile file: parse and schema errors, then contradictions."""
    try:
        profile = parse_profile(path.read_text(encoding="utf-8"))
    except OSError:
        return ["cannot read the file"]
    except SiteProfileError as error:
        return [str(error)]
    if profile.name != path.stem:
        return [f"the file {path.name} names itself {profile.name}"]
    return problems(profile)


def example_text() -> str:
    """An empty profile to copy: every answer unset."""
    return dump(SiteProfile(name="my-cluster"))


# ----- the questions -----

_CHOICES = {
    "access_method": ("접속 방식", ("ssh", "gateway_tunnel", "vpn", "other")),
    "file_transfer": ("파일 전송 방법", ("scp", "sftp", "rsync", "gateway_window", "none", "other")),
    "scheduler": ("scheduler", ("sge", "pbs", "slurm", "none")),
}
_YES_NO = {
    "remote_exec": "원격 명령을 이 PC에서 실행할 수 있나요? (y/n)",
    "agent_inside_cluster": "클러스터 안에서 agent를 써도 되나요? (y/n)",
}
_NUMBERS = {
    "idle_lock_minutes": "유휴 잠김 시간(분): 이 시간 동안 명령이 없으면 세션이 잠깁니다",
    "login_limit_per_request": "로그인 상한: 요청당 (비우면 제한 없음)",
    "login_limit_per_day": "로그인 상한: 하루 (비우면 제한 없음)",
}
_TEXTS = {
    "queue": "큐 또는 partition 이름",
    "remote_workdir": "클러스터의 작업 경로 (절대 경로)",
    "port_discovery": "게이트웨이의 로컬 포트를 찾는 방법 (해당 없으면 비움)",
    "file_transfer_note": "파일 전송에 대한 메모 (해당 없으면 비움)",
}


def _ask_one(key: str, current, ask: Callable[[str, str], str]):
    """One answer. Empty keeps what is there (unknown stays unknown); anything unreadable is asked again."""
    shown = " ".join(current) if isinstance(current, list) else "" if current is None else str(current)
    while True:
        raw = ask(_label(key), shown).strip()
        if raw == "":
            return current
        try:
            if key in _CHOICES:
                if raw not in _CHOICES[key][1]:
                    raise ValueError("다음 중 하나여야 합니다: " + ", ".join(_CHOICES[key][1]))
                return raw
            if key in _YES_NO:
                if raw.lower() not in ("y", "yes", "n", "no"):
                    raise ValueError("y 또는 n으로 답하세요")
                return raw.lower() in ("y", "yes")
            if key in _NUMBERS:
                if not raw.isdigit() or int(raw) < 1:
                    raise ValueError("1 이상의 정수여야 합니다")
                return int(raw)
            if key == "connect_argv":
                return SiteProfile.argv_without_secrets(raw.split())
            return raw
        except ValueError as error:
            print(error)


def _label(key: str) -> str:
    if key in _CHOICES:
        return f"{_CHOICES[key][0]} ({'/'.join(_CHOICES[key][1])})"
    for table in (_YES_NO, _NUMBERS, _TEXTS):
        if key in table:
            return table[key]
    return "접속 명령 (공백으로 나눈 단어, 비밀번호·토큰은 쓰지 마세요)"


ORDER = ("access_method", "connect_argv", "port_discovery", "remote_exec", "file_transfer", "file_transfer_note",
         "idle_lock_minutes", "agent_inside_cluster", "scheduler", "queue", "remote_workdir",
         "login_limit_per_request", "login_limit_per_day")


def ask_profile(name: str, ask: Callable[[str, str], str], existing: SiteProfile | None = None,
                preset: dict | None = None) -> SiteProfile:
    """Ask each question once. A blank answer means "I do not know": it is kept as unknown, never guessed, and
    `unknown_fields` says what to ask next time. `preset` carries answers already given elsewhere (the scheduler and
    login limits from `labhq init`), shown as defaults."""
    values = (existing.model_dump(mode="python") if existing else {"name": name, "connect_argv": []})
    for key, value in (preset or {}).items():
        if values.get(key) in (None, []):
            values[key] = value
    for key in ORDER:
        values[key] = _ask_one(key, values.get(key), ask)
    try:
        return SiteProfile.model_validate(values)
    except ValidationError as error:
        raise SiteProfileError("; ".join(e["msg"] for e in error.errors())) from None


# ----- from a profile to the session manager (#1295 C) -----

def session_profile(site_profile: str | None, directory: Path | None = None) -> SiteProfile | None:
    """The profile a session is opened from: None when no profile is named or it gives no connect command (the old
    ``hpc.ssh_host`` path, or a runner on the login node, applies). A named profile that cannot be read raises: the
    session path fails closed instead of quietly falling back to a different way of logging in."""
    if not site_profile:
        return None
    profile = load_profile(site_profile, directory)
    return profile if profile.connect_argv else None


def session_config(profile: SiteProfile, command_timeout_s: float):
    """The ``hpc_session.SessionConfig`` for a profile. The site's idle lock and failure texts come from the profile."""
    from .hpc_session import SessionConfig

    return SessionConfig(argv=list(profile.connect_argv),
                         idle_lock_s=profile.idle_lock_minutes * 60.0 if profile.idle_lock_minutes else None,
                         command_timeout_s=float(command_timeout_s), tunnel_texts=tuple(profile.tunnel_texts))
