"""PI personal paths that staff must not open while they run under the PI's own account (PI decision 2026-10-03).

Three layers, none of them a sandbox: Claude file tools get deny rules, a Claude shell command that names one of
these paths goes to the PI, and every staff member's instructions list them as `~` labels. Codex reads through its
own sandbox and is held only by the instructions.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .settings import Settings

# Relative to the runner account's home. A path that does not exist on this host is skipped quietly.
DEFAULT_HOME_ENTRIES = (
    ".ssh", ".aws", ".azure", ".gnupg", ".docker", ".kube", ".config/gh", ".config/gcloud",
    ".git-credentials", ".netrc", ".claude", ".claude.json", ".codex",
    "AppData/Local/Google/Chrome/User Data", "AppData/Local/Microsoft/Edge/User Data",
    "AppData/Roaming/Mozilla/Firefox",
    ".config/google-chrome", ".config/microsoft-edge", ".mozilla",
)
CONFIG_LABEL = "labhq 설정 파일"
STATE_LABEL = "labhq gateway 상태 폴더"
OUTSIDE_HOME_LABEL = "홈 밖 개인 경로"
ENV_VAR = "LABHQ_PRIVATE_PATHS"  # the runner hands the approval gate this task's list (os.pathsep-joined)

# Variables a shell can spell a home path with; each is replaced by its value before matching.
_PATH_VARS = ("USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME")
_HOME_MARK = "\x00home"
_NAME_CHARS = frozenset("._-")


def host_home() -> str:
    """The runner account's home on this host (tests point it at an empty folder)."""
    return os.path.expanduser("~")


@dataclass(frozen=True)
class PrivatePaths:
    paths: tuple[str, ...] = ()    # active paths plus their real path when a link/junction differs
    labels: tuple[str, ...] = ()   # one `~` or role label per active entry; never an absolute path
    skipped: tuple[str, ...] = ()  # labels of entries that contain or equal a work folder
    enabled: bool = True           # False when policy.private_paths is an explicit empty list


def _expand(raw: str, home: str) -> str:
    if raw == "~" or raw.startswith(("~/", "~\\")):
        raw = home + raw[1:]
    raw = os.path.expandvars(raw)
    return os.path.normpath(raw) if os.path.isabs(raw) else raw


def _fold(p: str) -> str:
    """Case-folded, forward slashes, no repeated or trailing separator: lexical only."""
    s = re.sub(r"/{2,}", "/", p.replace("\\", "/")).casefold()
    return s.rstrip("/") or s


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _label(path: str, home: str) -> str:
    folded, home_folded = _fold(path), _fold(home)
    if _under(folded, home_folded) and folded != home_folded:
        tail = re.sub(r"/{2,}", "/", path.replace("\\", "/")).rstrip("/")[len(home_folded):]
        return "~" + tail
    return OUTSIDE_HOME_LABEL


def _labhq_entries(settings: Settings, home: str) -> list[tuple[str, str]]:
    """labhq's own files staff must not read: the loaded config (tokens) and the gateway state (approvals)."""
    entries = [(settings.config_path, CONFIG_LABEL)] if settings.config_path else []
    state = _expand(settings.gateway.state_dir, home)
    entries.append((state if os.path.isabs(state) else str(settings.path(state)), STATE_LABEL))
    return entries


def configured_private_paths(settings: Settings, home: str | None = None) -> list[tuple[str, str]]:
    """(absolute path, label) for every configured entry, before existence and work-folder checks."""
    home = home or host_home()
    raw = settings.policy.private_paths
    if raw is None:
        entries = [(os.path.join(home, *entry.split("/")), "~/" + entry) for entry in DEFAULT_HOME_ENTRIES]
        return entries + _labhq_entries(settings, home)
    out = []
    for item in raw:
        if not item or not item.strip():
            continue
        path = _expand(item.strip(), home)
        if not os.path.isabs(path):
            path = str(settings.path(path))
        out.append((path, _label(path, home)))
    return out


def _forms(path: str) -> set[str]:
    forms = {_fold(os.path.abspath(path))}
    try:
        forms.add(_fold(os.path.realpath(path)))
    except (OSError, ValueError):
        pass
    return forms


def resolve_private_paths(settings: Settings, keep: Iterable[str | os.PathLike | None] = (),
                          home: str | None = None) -> PrivatePaths:
    """The entries that exist and contain no folder in `keep` (task workdir, workspace root, reference and
    project folders, plugin folders, the staff CODEX_HOME). An entry that contains one is skipped, not split."""
    if settings.policy.private_paths == []:
        return PrivatePaths(enabled=False)
    home = home or host_home()
    kept: set[str] = set()
    for folder in keep:
        if folder:
            kept |= _forms(str(folder))
    paths: list[str] = []
    labels: list[str] = []
    skipped: list[str] = []
    for path, label in configured_private_paths(settings, home):
        if not os.path.lexists(path):
            continue
        forms = _forms(path)
        if any(_under(k, f) for k in kept for f in forms):
            skipped.append(label)
            continue
        labels.append(label)
        paths.append(path)
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):
            real = path
        if _fold(real) != _fold(path):
            paths.append(real)
    return PrivatePaths(tuple(dict.fromkeys(paths)), tuple(dict.fromkeys(labels)), tuple(dict.fromkeys(skipped)))


def staff_codex_homes(settings: Settings, cwd: Path, codex_task: bool) -> list[Path]:
    """The CODEX_HOME a staff Codex uses. For other engines only an explicitly configured one counts, so the
    PI's ~/.codex stays closed to Claude staff when Codex staff have their own login."""
    from .adapters.base import child_config_dirs, expand_env

    configured = expand_env(settings.engines.codex.env)
    if codex_task:
        return child_config_dirs({**os.environ, **configured}, cwd, "CODEX_HOME", ".codex")
    raw = configured.get("CODEX_HOME")
    if not raw:
        return []
    p = Path(os.path.expanduser(raw))
    return [p if p.is_absolute() else cwd / p]


def _canonical_text(text: str, home: str, environ: Mapping[str, str]) -> str:
    """Shell text with every home spelling replaced by one marker: variables, `~`, Git Bash and WSL drives."""
    t = re.sub(r"\\(?=[ \t])", "", text)  # `User\ Data` → `User Data`
    t = re.sub(r"/{2,}", "/", t.replace("\\", "/")).casefold()
    home_folded = _fold(home)
    lookup = {k.casefold(): v for k, v in environ.items()}
    values = {"userprofile": home_folded, "home": home_folded}
    for name in _PATH_VARS:
        value = lookup.get(name.casefold())
        if value:
            values[name.casefold()] = _fold(value)
    values_seq = sorted(values.items(), key=lambda kv: len(kv[0]), reverse=True)
    for name, value in values_seq:
        n = re.escape(name)
        t = re.sub(rf"%{n}%|\$\{{(?:env:)?{n}\}}|\$(?:env:)?{n}(?![\w])", lambda _m, v=value: v, t)
    t = t.replace("%homedrive%%homepath%", home_folded)
    spellings = {home_folded}
    drive = re.match(r"^([a-z]):/(.*)$", home_folded)
    if drive:
        letter, rest = drive.groups()
        spellings |= {f"/{letter}/{rest}", f"/mnt/{letter}/{rest}", f"/cygdrive/{letter}/{rest}"}
    for spelling in sorted(spellings, key=len, reverse=True):
        t = re.sub(re.escape(spelling) + r"(?=/|$|[^\w.\-])", _HOME_MARK, t)
    return re.sub(r"(?:^|(?<=[\s'\"=(:,;|&<>`]))~(?=/|$|[\s'\"`;|&)])", _HOME_MARK, t)


def _targets(path: str, home: str) -> list[str]:
    folded, home_folded = _fold(path), _fold(home)
    if _under(folded, home_folded) and folded != home_folded:
        return [_HOME_MARK + folded[len(home_folded):]]
    drive = re.match(r"^([a-z]):/(.*)$", folded)
    if drive:
        letter, rest = drive.groups()
        return [folded, f"/{letter}/{rest}", f"/mnt/{letter}/{rest}", f"/cygdrive/{letter}/{rest}"]
    return [folded]


def _found(text: str, target: str) -> bool:
    start = 0
    while (start := text.find(target, start)) != -1:
        end = start + len(target)
        before_ok = target.startswith(_HOME_MARK) or start == 0 or not (
            text[start - 1].isalnum() or text[start - 1] in _NAME_CHARS)
        after_ok = end == len(text) or not (text[end].isalnum() or text[end] in _NAME_CHARS)
        if before_ok and after_ok:
            return True
        start += 1
    return False


def mentioned_private_path(text: str, paths: Iterable[str], home: str | None = None,
                           environ: Mapping[str, str] | None = None) -> str | None:
    """The first private path `text` names in any common spelling, case-insensitively; None otherwise.

    Lexical, like the zone guard: `cd ~ && cat .ssh/x`, globs and paths built at run time are not seen."""
    paths = [p for p in paths if p]
    if not paths or not text:
        return None
    home = home or host_home()
    canonical = _canonical_text(text, home, os.environ if environ is None else environ)
    for path in paths:
        if any(_found(canonical, target) for target in _targets(path, home)):
            return path
    return None


def path_field_text(value: str, workdir: str | None) -> str:
    """A file tool's path argument as an absolute, `..`-free spelling for `mentioned_private_path`."""
    if value.startswith(("~", "$", "%")):
        return value
    if re.match(r"^[A-Za-z]:[/\\]", value) or value.startswith(("\\\\", "//")):
        return ntpath.normpath(value)
    if value.startswith(("/", "\\")):
        return posixpath.normpath(value.replace("\\", "/"))
    if workdir:
        joined = workdir.replace("\\", "/").rstrip("/") + "/" + value.replace("\\", "/")
        return ntpath.normpath(joined) if re.match(r"^[A-Za-z]:/", joined) else posixpath.normpath(joined)
    return value
