"""PI personal paths that staff must not open while they run under the PI's own account (PI decision 2026-10-03).

Three layers for Claude staff, none of them a sandbox: Claude file tools get deny rules, and while any path is
active no shell command and no read outside the task's own folders is pre-approved, so they reach the approval
gate, which judges the canonical, real path (a shell command naming one goes to the PI); the instructions list
them as `~` labels. Codex, Gemini, Antigravity and cli staff get only the instructions: nothing intercepts their
file or shell reads (the Codex sandbox limits writes and network, not reads).

The gate is lexical, so a path built at run time passes it: `python -c` joining `'..', '.ssh'`, or a script the
staff member wrote into its own workdir (PR #324 live probe, Claude 2.1.282). Staff with shell and workdir writes
can therefore read any private path in two steps; only a separate account (docs/runner-account.md) stops that.

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
from typing import Callable, Iterable, Mapping

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
# ... and whether private paths are on at all ("1"/"0"): on with an empty list when no entry is active (PR #327).
ENABLED_ENV_VAR = "LABHQ_PRIVATE_PATHS_ENABLED"
# ... and the folders inside them this task may read (`staff_claude_project_dirs`), os.pathsep-joined.
OPEN_READS_ENV_VAR = "LABHQ_PRIVATE_OPEN_READS"
# What a Claude config folder keeps beside projects/ that matters most (login, account state, prompt history):
# ruled by name even before it exists, since the Claude deny rules for an open folder are listed per entry.
CLAUDE_CONFIG_SECRETS = (".credentials.json", ".claude.json", "history.jsonl")

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
    open_reads: tuple[str, ...] = ()  # folders this task may read even inside a path above (never write)


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
    """labhq's own files staff must not read: the loaded config (tokens), the gateway state (approvals) and the
    Claude staff config folder (its login; a Claude task reads back only its own project folder there)."""
    entries = [(settings.config_path, CONFIG_LABEL)] if settings.config_path else []
    state = _expand(settings.gateway.state_dir, home)
    entries.append((state if os.path.isabs(state) else str(settings.path(state)), STATE_LABEL))
    staff = staff_claude_config_dir(settings, home)
    if staff:
        entries.append((staff, _label(staff, home)))
    return entries


def inside_any(path: str, roots: Iterable[str]) -> bool:
    """Whether `path` is one of `roots` or below one, lexically, case-folded where the volume is."""
    for root in roots:
        fold = _case_insensitive(root)
        if _under(_fold(path, fold), _fold(root, fold)):
            return True
    return False


def claude_project_slug(cwd: str) -> str:
    """The folder name Claude keeps a working folder's sessions and saved tool output under (<config>/projects/).

    Every UTF-16 unit but ASCII letters and digits becomes `-`; past 200 characters it is the first 200, `-` and
    base36 |djb2| of the path. Probed on Claude 2.1.282 / Windows 11 with an empty CLAUDE_CONFIG_DIR
    (tests/fixtures/real/claude_code/claude_project_slug.json): the folder is written before the login check."""
    raw = cwd.encode("utf-16-le", "surrogatepass")
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    slug = "".join(chr(u) if chr(u).isascii() and chr(u).isalnum() else "-" for u in units)
    if len(slug) <= 200:
        return slug
    digest = 0
    for unit in units:
        digest = ((digest << 5) - digest + unit) & 0xFFFFFFFF
    digest = abs(digest - (1 << 32) if digest >= 1 << 31 else digest)
    digits = ""
    while True:
        digest, rest = divmod(digest, 36)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"[rest] + digits
        if not digest:
            break
    return f"{slug[:200]}-{digits}"


def configured_claude_config_dir(settings: Settings, home: str | None = None) -> str | None:
    """engines.claude_code.env.CLAUDE_CONFIG_DIR with `${VAR}` and `~` expanded; None when unset."""
    from .adapters.base import expand_env

    raw = (expand_env(settings.engines.claude_code.env).get("CLAUDE_CONFIG_DIR") or "").strip()
    return _expand(raw, home or host_home()) if raw else None


def in_pi_claude(path: str, home: str | None = None) -> bool:
    """Whether `path` is the PI's ~/.claude or inside it, as written or through a link."""
    pi = _forms(os.path.join(home or host_home(), ".claude"))
    return any(inside_any(form, pi) for form in _forms(path))


def staff_claude_config_dir(settings: Settings, home: str | None = None) -> str | None:
    """engines.claude_code.env.CLAUDE_CONFIG_DIR as an absolute path: the staff's own Claude login (#298 ⑤).

    None when unset or relative, and when it is the PI's ~/.claude or inside it (doctor fails that): nothing in
    the PI's folder is ever opened."""
    path = configured_claude_config_dir(settings, home)
    if not path or not os.path.isabs(path) or in_pi_claude(path, home):
        return None
    return path


def staff_claude_project_dirs(settings: Settings, workdir: str | os.PathLike) -> list[str]:
    """<staff CLAUDE_CONFIG_DIR>/projects/<slug of the task folder>: where Claude saves this task's long tool
    output. Each spelling of the config folder and of the task folder (as written and real), so Claude's own
    choice of spelling still lands inside. Empty without a staff config folder."""
    config = staff_claude_config_dir(settings)
    if not config:
        return []
    written = os.path.abspath(str(workdir))
    cwds, configs = {written}, {config}
    try:
        cwds.add(os.path.realpath(written))
        configs.add(os.path.realpath(config))
    except (OSError, ValueError):
        pass
    return list(dict.fromkeys(os.path.join(c, "projects", claude_project_slug(w))
                              for c in sorted(configs) for w in sorted(cwds)))


def closed_entries(root: str, open_reads: Iterable[str]) -> list[str]:
    """What to deny inside `root` so the `open_reads` below it stay readable: every top-level entry not on the way
    to an open folder, plus CLAUDE_CONFIG_SECRETS before they exist.

    Only the top level, a fixed set of names in a Claude config folder. Deeper entries get no rule: projects/
    gains a folder per Claude task, and naming each one grew the --settings argument until no Claude task could
    start. Those entries, like any made after the task starts, are not pre-approved (the task's read roots), so
    reads of them reach the gate, which refuses them."""
    fold = _case_insensitive(root)
    key = str.casefold if fold else (lambda name: name)
    top = re.sub(r"/{2,}", "/", root.replace("\\", "/")).rstrip("/")
    ways: set[str] = set()
    for folder in open_reads:
        spelled = re.sub(r"/{2,}", "/", folder.replace("\\", "/")).rstrip("/")
        if _under(_fold(spelled, fold), _fold(top, fold)) and len(spelled) > len(top):
            first = next((part for part in spelled[len(top):].split("/") if part), None)
            if first:
                ways.add(key(first))
    if not ways:
        return [root]
    try:
        present = os.listdir(root)
    except OSError:
        present = []
    return [os.path.join(root, name) for name in dict.fromkeys([*present, *CLAUDE_CONFIG_SECRETS])
            if key(name) not in ways]


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
                          home: str | None = None, open_reads: Iterable[str] = ()) -> PrivatePaths:
    """The entries that exist and contain no folder in `keep` (task workdir, workspace root, reference and
    project folders, plugin folders, the staff CODEX_HOME). An entry that contains one is skipped, not split.
    `open_reads` (a Claude task's own project folder in the staff config folder) is carried through: an entry
    holding one stays active, and only that folder opens, for reading."""
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
    return PrivatePaths(tuple(dict.fromkeys(paths)), tuple(dict.fromkeys(labels)), tuple(dict.fromkeys(skipped)),
                        open_reads=tuple(dict.fromkeys(str(p) for p in open_reads if p)))


def open_read_allowed(path: str, open_reads: Iterable[str]) -> bool:
    """Whether a file tool's absolute, `..`-free `path` is inside a folder this task may read."""
    return inside_any(path, [p for p in open_reads if p])


def holds_open_read(private: str, open_reads: Iterable[str]) -> bool:
    """Whether the private path `private` contains a folder this task may read."""
    return any(inside_any(folder, [private]) for folder in open_reads if folder)


def gate_private_paths(environ: Mapping[str, str], resolve: Callable[[], PrivatePaths]) -> PrivatePaths:
    """What the approval gate checks: the runner's list and switch from `environ`, else `resolve()`.

    On with an empty list still runs the registry check (PR #327). Without the switch (an older runner) a
    non-empty list means on; a non-empty list is never turned off."""
    if ENV_VAR not in environ:
        return resolve()
    paths = tuple(p for p in environ[ENV_VAR].split(os.pathsep) if p)
    switch = environ.get(ENABLED_ENV_VAR, "").strip()
    open_reads = tuple(p for p in environ.get(OPEN_READS_ENV_VAR, "").split(os.pathsep) if p)
    return PrivatePaths(paths=paths, enabled=bool(paths) or switch not in ("", "0"), open_reads=open_reads)


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

    Lexical, like the zone guard: globs and paths built at run time are not seen. The gate adds `cd` targets
    (`shell_cd_bases`) for `cd ~ && cat .ssh/x`."""
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


# `cd`, `pushd`, `chdir` and PowerShell `Set-Location`/`sl`/`Push-Location` as a word anywhere (`cmd /c cd /d x`):
# a false match only adds a folder to read relative paths from.
_CD = re.compile(r"(?:^|(?<=[\s;&|({\"']))(?:builtin\s+)?(?:cd|chdir|pushd|set-location|sl|push-location)(?=[\s;&|)]|$)"
                 r"([^;&|\n)]*)", re.I)
MAX_CD_BASES = 32


def _expand_dir(target: str, home: str, environ: Mapping[str, str]) -> str | None:
    """A `cd` target with `~` and variables replaced, Git Bash and WSL drives as `C:/`; None if a part is unknown."""
    if target == "~" or target.startswith(("~/", "~\\")):
        target = home + target[1:]
    target = local_drive_text(target, environ)  # before the `$` check: `\\localhost\C$\x` is `C:\x`
    lookup = {k.casefold(): v for k, v in environ.items()}
    lookup.update({"home": home, "userprofile": home})
    unknown = False

    def value(match: re.Match) -> str:
        nonlocal unknown
        name = next(g for g in match.groups() if g)
        if name.casefold() not in lookup:
            unknown = True
            return ""
        return lookup[name.casefold()]

    target = re.sub(r"%(\w+)%|\$\{(?:env:)?(\w+)\}|\$(?:env:)?(\w+)", value, target, flags=re.I)
    if unknown or any(mark in target for mark in ("$", "`", "%")):
        return None
    if re.match(r"^[A-Za-z]:[/\\]", home):
        drive = re.match(r"^(?:/mnt|/cygdrive)?/([A-Za-z])(?:/(.*))?$", target.replace("\\", "/"))
        if drive:
            target = f"{drive.group(1).upper()}:/{drive.group(2) or ''}"
    return target


def shell_cd_bases(command: str, workdir: str | None, home: str | None = None,
                   environ: Mapping[str, str] | None = None) -> list[str] | None:
    """The folders a shell command changes into, each relative to the one before (PR #324 probe: `cd ~ && cat
    .ssh/x` named no private path). A target with an unknown variable or a substitution ends the chain, since the
    folder is only known at run time. None past MAX_CD_BASES: the caller asks rather than resolving them all."""
    home = home or host_home()
    environ = os.environ if environ is None else environ
    base, out = workdir, []
    for match in _CD.finditer(command):
        args = [a.strip("\"'") for a in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', match.group(1))]
        args = [a for a in args if not (a.startswith("-") and len(a) > 1) and a.casefold() != "/d"]
        target = args[0] if args else "~"
        if target == "-":
            continue
        spelled = _expand_dir(target, home, environ)
        if spelled is None:
            base = None
            continue
        absolute = re.match(r"^[A-Za-z]:[/\\]", spelled) or spelled.startswith(("/", "\\"))
        if not absolute and not base:
            continue
        base = path_field_text(spelled, None if absolute else base)
        out.append(base)
        if len(out) > MAX_CD_BASES:
            return None
    return out


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


# ---------------- registry access (#325) ----------------
# The PI's GITHUB_TOKEN is a user environment variable, kept in the registry at HKCU\Environment (also
# HKEY_USERS\<SID>\Environment). labhq strips it from staff process env (#301), but a shell under the same account
# can read the registry directly. While private paths are on, the gate sends ANY registry access to the PI, not
# only spellings of the Environment key: three review rounds on PR #327 each found a narrower spelling of that key
# (`..` detours, a custom PSDrive, a line continuation inside the hive name), so the rule names the registry itself.
#
# Matched case-insensitively after line continuations are joined (PowerShell backtick, cmd `^` and sh `\` each
# followed by LF or CRLF, removed as a pair), then `"`, `'`, backtick and `^` escapes removed (`"HK"CU`, ``H`KCU``,
# `HK^CU`), a Python string prefix dropped with its quote (`r'HKCU'`), and `\` read as `/`:
#   - a hive by name: `HKCU`/`HKLM`/`HKU`/`HKCR`/`HKCC` as a word (`HKCU:`, `reg query HKCU`, `echo HKCU`; not a
#     path segment such as `outputs/hkcu`) and `HKEY_CURRENT_USER`, `HKEY_USERS`, `HKEY_LOCAL_MACHINE`, ... anywhere;
#   - the PowerShell Registry provider: `Registry::` paths, `-PSProvider Registry`, `New-PSDrive`/`ndr`/`mount` with
#     the word Registry (a drive such as `U:` created in the same command is covered by its creation); Git Bash
#     `/proc/registry`;
#   - `reg`/`reg.exe` with any subcommand, `regedit`, `regini`;
#   - registry APIs: Python `winreg`/`_winreg`, .NET `Microsoft.Win32.*`, `RegistryKey`/`RegistryHive`/
#     `RegistryView`, `[Registry]`, `Registry.CurrentUser`-style members; WMI `StdRegProv`;
#   - WMI's copy of the environment: `Win32_Environment`, `wmic environment`; `[EnvironmentVariableTarget]::User`.
# `[Environment]::GetEnvironmentVariable(s)` with a target other than Process, called directly or through
# `.Invoke(`, is judged by `registry_access`; the method group held without a call (`$f = [Environment]::
# GetEnvironmentVariable`) asks, since its later `.Invoke` cannot be followed.
# Reads of the stripped process env (`$env:X`, `%X%`, `set`, `printenv`, `os.environ`, `Env:`) do not match.
# Accepted false alarm: these words inside a commit message or a grep pattern ask; asking costs one PI answer, while
# telling a search argument from a real read by text would reopen the bypasses.
# Lexical like the private-path check: registry access built at run time (string building, a script written to the
# workdir) passes. The first defense is the token's scope (the PI token reaches only the repositories labhq needs).
_HIVE_LONG = r"hkey_(?:current_user|users|local_machine|classes_root|current_config|performance_data)"
REGISTRY_ACCESS = re.compile(
    rf"\b{_HIVE_LONG}\b|(?<![\w/.-])hk(?:cu|lm|u|cr|cc)(?![\w.-])"           # a hive by name
    r"|registry(?:32|64)?::|/proc/registry"                                   # provider path, Git Bash
    r"|-ps\w*\s*:?\s*registry\b|\b(?:new-psdrive|ndr|mount)\b[^;|&\n]*\bregistry\b"  # a Registry PSDrive
    r"|(?<![\w.-])reg(?:\.exe)?[\s,]+(?:query|add|delete|copy|save|restore|load|unload|compare|export|import"
    r"|flags)\b|(?<![\w.-])reg(?:edit|ini)(?:\.exe)?\b"                        # reg.exe, regedit, regini
    r"|\b_?winreg\b|\bmicrosoft\.win32\b|\bregistry(?:key|hive|view)\b|\[registry\]"  # registry APIs
    r"|\bregistry\s*(?:::|\.)\s*(?:currentuser|localmachine|users|classesroot|currentconfig|getvalue"
    r"|openbasekey|openremotebasekey)\b|\bstdregprov\b"
    r"|\bwin32_environment\b|\bwmic\b[^;|&\n]*\benvironment\b"                # WMI's copy
    r"|environmentvariabletarget\]?\s*(?:::|\.)?\s*user\b",                   # the User target, anywhere
    re.I,
)
_CONTINUATION = re.compile(r"[`^\\]\r?\n")
_GET_ENV_CALL = re.compile(r"getenvironmentvariable(s?)(?:\s*\.\s*invoke)?\s*\(", re.I)
_GET_ENV_GROUP = re.compile(r"(?:::|\.)\s*getenvironmentvariables?\b(?!\s*(?:\(|\.\s*invoke\s*\())", re.I)
_PROCESS_TARGET = re.compile(r"^(?:\[?(?:system\.)?environmentvariabletarget\]?\s*(?:::|\.)?\s*)?"
                             r"(?:process|0|\$null)$", re.I)
_STRING_PREFIX = re.compile(r"(?<!\w)(?:rb|br|fr|rf|[rubf])(?=[\"'])", re.I)


def _registry_text(command: str) -> str:
    """`command` spelled for the patterns above: continuations joined, escapes and quotes gone, `/` for `\\`."""
    text = _STRING_PREFIX.sub("", _CONTINUATION.sub("", command))
    return re.sub(r"[\"'`^]", "", text).replace("\\", "/")


def _call_args(text: str, start: int) -> list[str]:
    """Top-level comma-separated arguments of the call whose `(` ends at `start`; unclosed text runs to the end."""
    depth, args, current = 0, [], []
    for ch in text[start:]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            args.append("".join(current))
            current = []
            continue
        current.append(ch)
    args.append("".join(current))
    return [a.strip() for a in args]


def registry_access(command: str) -> str | None:
    """The spelling in `command` that touches the registry (the patterns above), or None."""
    text = _registry_text(command)
    match = REGISTRY_ACCESS.search(text) or _GET_ENV_GROUP.search(text)
    if match:
        return match.group(0)
    for call in _GET_ENV_CALL.finditer(text):
        args = _call_args(text, call.end())
        target = args[0] if call.group(1) else (args[1] if len(args) > 1 else "")
        if target and not _PROCESS_TARGET.match(target):
            return call.group(0) + target
    return None
