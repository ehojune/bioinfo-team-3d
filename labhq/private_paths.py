"""PI personal paths that staff must not open while they run under the PI's own account (PI decision 2026-10-03).

Three layers for Claude staff, none of them a sandbox: Claude file tools get deny rules, and while any path is
active no shell command and no read outside the task's own folders is pre-approved, so they reach the approval
gate, which judges the canonical, real path (a shell command naming one goes to the PI); the instructions list
them as `~` labels. Codex, Gemini, Antigravity and cli staff get only the instructions: nothing intercepts their
file or shell reads (the Codex sandbox limits writes and network, not reads).

Paths compare case-insensitively only where the file system does: a Windows spelling, a Windows host, or a POSIX
volume the #315 helper judges case-insensitive (PR #324 review). On a case-sensitive POSIX volume `Secret` and
`secret` are different folders.
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


def _host_windows() -> bool:
    return os.name == "nt"


def _case_insensitive(path: str) -> bool:
    """Whether `path` names the same entry in any case: a Windows spelling or host, or a case-insensitive POSIX
    volume (macOS default). A POSIX path that does not exist keeps its case."""
    if _host_windows() or re.match(r"^[A-Za-z]:(?:[/\\]|$)|^[/\\]{2}", path):
        return True
    from .adapters.owned import case_sensitive_directory

    try:
        return os.path.lexists(path) and not case_sensitive_directory(Path(path))
    except (OSError, ValueError):
        return False


def _fold(p: str, fold: bool = True) -> str:
    """Forward slashes, no repeated or trailing separator, case-folded when `fold`: lexical only."""
    s = re.sub(r"/{2,}", "/", p.replace("\\", "/"))
    if fold:
        s = s.casefold()
    return s.rstrip("/") or s


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _label(path: str, home: str) -> str:
    fold = _case_insensitive(path)
    folded, home_folded = _fold(path, fold), _fold(home, fold)
    if _under(folded, home_folded) and folded != home_folded:
        tail = re.sub(r"/{2,}", "/", path.replace("\\", "/")).rstrip("/")[len(home_folded):]
        return "~" + tail
    # Only the last component, so staff know which folder to avoid without the PI's layout going in a report.
    name = re.sub(r"/{2,}", "/", path.replace("\\", "/")).rstrip("/").rsplit("/", 1)[-1]
    return f"{OUTSIDE_HOME_LABEL} …/{name}" if name and not name.endswith(":") else OUTSIDE_HOME_LABEL


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
    """The path as written (absolute) and its real path, with case kept; callers fold where the volume does."""
    forms = {_fold(os.path.abspath(path), False)}
    try:
        forms.add(_fold(os.path.realpath(path), False))
    except (OSError, ValueError):
        pass
    return forms


def _short_name(path: str) -> str | None:
    """Windows 8.3 spelling of an existing path (`C:\\Users\\pi\\SSH~1`), or None off Windows or without one."""
    if os.name != "nt":
        return None
    import ctypes

    try:
        size = ctypes.windll.kernel32.GetShortPathNameW(path, None, 0)
        if not size:
            return None
        buf = ctypes.create_unicode_buffer(size)
        if not ctypes.windll.kernel32.GetShortPathNameW(path, buf, size):
            return None
    except (OSError, AttributeError, ValueError):
        return None
    return buf.value if _fold(buf.value) != _fold(path) else None


def short_spellings(path: str) -> list[str]:
    """8.3 spellings Claude's deny rules compare literally: the whole path short, and the long parent with a
    short last component (`~/SSH~1`). Mixed spellings deeper in the path are not listed."""
    short = _short_name(path)
    if not short:
        return []
    out = [short]
    parent, name = os.path.split(path)
    short_last = os.path.basename(short)
    if short_last.casefold() != name.casefold():
        out.append(os.path.join(parent, short_last))
    return list(dict.fromkeys(out))


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
        fold = _case_insensitive(path)
        forms = {_fold(f, fold) for f in _forms(path)}
        if any(_under(_fold(k, fold), f) for k in kept for f in forms):
            skipped.append(label)
            continue
        labels.append(label)
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):
            real = path
        for spelled in (path, real):
            paths.append(spelled)
            paths.extend(short_spellings(spelled))
    paths = [p for i, p in enumerate(paths)
             if _fold(p, _case_insensitive(p)) not in {_fold(q, _case_insensitive(p)) for q in paths[:i]}]
    return PrivatePaths(tuple(dict.fromkeys(paths)), tuple(dict.fromkeys(labels)), tuple(dict.fromkeys(skipped)))


def plugin_keep_dirs(settings: Settings, plugin_dirs: Iterable[str], cwd: str | os.PathLike | None = None,
                     task_env: Mapping[str, str] | None = None) -> list[str]:
    """Plugin folders as the Claude adapter expands them (`_plugin_dirs`): `${VAR}` from the runner env, then
    engines.claude_code.env, then the task env, so a variable set only for staff still finds the folder."""
    from .adapters.base import expand_env

    source = {**os.environ, **expand_env(settings.engines.claude_code.env), **(task_env or {})}
    out = []
    for raw in plugin_dirs:
        directory = os.path.expanduser(expand_env({"dir": raw}, source)["dir"])
        if cwd is not None and not os.path.isabs(directory):
            directory = os.path.join(str(cwd), directory)
        out.append(directory)
    return out


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


def shell_needles(paths: Iterable[str], home: str | None = None) -> list[str]:
    """Substrings for Claude `ask` rules (`Bash(*<needle>*)`): a path below home by its tail (`.ssh`,
    `.config/gh`) so every home spelling matches, others by their absolute and Git Bash forms; both separators.
    A last component with a space (`User Data`) also stands alone, since shells quote it apart."""
    home = home or host_home()
    out: list[str] = []
    for path in paths:
        if not path:
            continue
        fold = _case_insensitive(path)
        home_folded = _fold(home, fold)
        slashed = re.sub(r"/{2,}", "/", path.replace("\\", "/")).rstrip("/")
        folded = _fold(path, fold)
        if _under(folded, home_folded) and folded != home_folded:
            forms = [slashed[len(home_folded):].lstrip("/")]
        else:
            forms = [slashed]
            drive = re.match(r"^([A-Za-z]):/(.+)$", slashed)
            if drive:
                forms.append(f"/{drive.group(1).lower()}/{drive.group(2)}")  # also inside /mnt/c/…
        for form in forms:
            out += [form, form.replace("/", "\\")]
        name = slashed.rsplit("/", 1)[-1]
        if " " in name:
            out.append(name)
    return [n for n in dict.fromkeys(out) if n and "(" not in n and ")" not in n]


def local_drive_text(text: str, environ: Mapping[str, str] | None = None) -> str:
    """`\\\\?\\C:\\x`, `\\\\?\\UNC\\localhost\\C$\\x` and `\\\\localhost\\C$\\x` (also 127.0.0.1, ::1 and this
    computer's name) spelled as `C:\\x`: the same file under another name."""
    environ = os.environ if environ is None else environ
    hosts = ["localhost", r"127\.0\.0\.1", r"\[?::1\]?", r"0:0:0:0:0:0:0:1"]
    computer = {k.casefold(): v for k, v in environ.items()}.get("computername")
    if computer:
        hosts.append(re.escape(computer))
    sep = r"[\\/]"
    t = re.sub(rf"{sep}{{2}}[?.]{sep}unc{sep}", r"\\\\", text, flags=re.I)
    t = re.sub(rf"{sep}{{2}}[?.]{sep}(?=[a-z]:)", "", t, flags=re.I)
    return re.sub(rf"{sep}{{2}}(?:{'|'.join(hosts)}){sep}([a-z])\$(?={sep}|$|[\s'\"])", r"\1:", t, flags=re.I)


def _canonical_text(text: str, home: str, environ: Mapping[str, str], fold: bool = True) -> str:
    """Shell text with every home spelling replaced by one marker: variables, `~`, Git Bash and WSL drives.

    Quotes are dropped (`"$HOME"/.ssh`, `Chrome/"User Data"`), `/./` and `seg/../` are resolved lexically, and
    the local admin share (`\\\\localhost\\C$\\`) and `\\\\?\\` prefix are read as the drive path. Without `fold`
    (a case-sensitive POSIX path) the text and the variable names keep their case."""
    t = re.sub(r"\\(?=[ \t])", "", text)  # `User\ Data` → `User Data`
    t = local_drive_text(t, environ)
    t = re.sub(r"/{2,}", "/", t.replace("\\", "/"))
    if fold:
        t = t.casefold()
    key = str.casefold if fold else (lambda name: name)
    home_folded = _fold(home, fold)
    lookup = {key(k): v for k, v in environ.items()}
    values = {key("USERPROFILE"): home_folded, key("HOME"): home_folded}
    for name in _PATH_VARS:
        value = lookup.get(key(name))
        if value:
            values[key(name)] = _fold(value, fold)
    values_seq = sorted(values.items(), key=lambda kv: len(kv[0]), reverse=True)
    for name, value in values_seq:
        n = re.escape(name)
        t = re.sub(rf"%{n}%|\$\{{(?:env:)?{n}\}}|\$(?:env:)?{n}(?![\w])", lambda _m, v=value: v, t)
    t = re.sub(r"%homedrive%%homepath%|\$\{?env:homedrive\}?\$\{?env:homepath\}?", lambda _m: home_folded, t,
               flags=re.I)
    t = t.replace('"', "").replace("'", "")
    while (resolved := re.sub(r"/\.(?=/|$|\s)", "", t)) != t:
        t = resolved
    while (resolved := re.sub(r"(?<![^/\s=:,;|&<>(])(?!\.\.?/|~/)[^/\s]+/\.\.(?:/|(?=\s|$))", "", t)) != t:
        t = resolved
    spellings = {home_folded}
    drive = re.match(r"^([a-z]):/(.*)$", home_folded, re.I)
    if drive:
        letter, rest = drive.group(1).lower(), drive.group(2)
        spellings |= {f"/{letter}/{rest}", f"/mnt/{letter}/{rest}", f"/cygdrive/{letter}/{rest}"}
    for spelling in sorted(spellings, key=len, reverse=True):
        t = re.sub(re.escape(spelling) + r"(?=/|$|[^\w.\-])", _HOME_MARK, t)
    return re.sub(r"(?:^|(?<=[\s'\"=(:,;|&<>`]))~(?=/|$|[\s'\"`;|&)])", _HOME_MARK, t)


def _targets(path: str, home: str, fold: bool = True) -> list[str]:
    folded, home_folded = _fold(path, fold), _fold(home, fold)
    if _under(folded, home_folded) and folded != home_folded:
        return [_HOME_MARK + folded[len(home_folded):]]
    drive = re.match(r"^([a-z]):/(.*)$", folded, re.I)
    if drive:
        letter, rest = drive.group(1).lower(), drive.group(2)
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
    """The first private path `text` names in any common spelling, case-insensitively where the volume is
    (`_case_insensitive`); None otherwise.

    Lexical, like the zone guard: `cd ~ && cat .ssh/x`, globs and paths built at run time are not seen."""
    paths = [p for p in paths if p]
    if not paths or not text:
        return None
    home = home or host_home()
    environ = os.environ if environ is None else environ
    canonical: dict[bool, str] = {}
    for path in paths:
        fold = _case_insensitive(path)
        if fold not in canonical:
            canonical[fold] = _canonical_text(text, home, environ, fold)
        if any(_found(canonical[fold], target) for target in _targets(path, home, fold)):
            return path
    return None


def path_field_text(value: str, workdir: str | None) -> str:
    """A file tool's path argument as an absolute, `..`-free spelling for `mentioned_private_path`."""
    value = local_drive_text(value)
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
