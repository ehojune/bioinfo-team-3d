"""The AlphaGenome API key file (#435, PI decision 2026-10-06).

`labhq init` asks for the key once and writes it here; the labhq_annot MCP server reads it at start. The key never
goes into the repository, the config (only the file's location, `annot.alphagenome_key_file`), logs, prompts, events,
doctor output or the MCP command line. A file and not an environment variable: staff processes inherit the runner's
environment, so a key there would be one `echo` away from the agent and its transcript.

The file is readable by the account that ran `labhq init` only: POSIX mode 0600 in a 0700 folder; on Windows the folder
and the file get a new protected DACL with one entry, full control for the current account (no SYSTEM, Administrators or
Users entry; explicit entries already there are dropped too), checked with `icacls` after writing. With a
separate runner account (docs/runner-account.md) that is the runner, so `labhq init` runs once in the runner's window.
Owner-only access goes on the key's own folder, so that folder must hold nothing else and must not contain labhq's
state, workspaces or config: `write_key` refuses otherwise rather than cut other accounts out of them.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from collections.abc import Iterable
from pathlib import Path

from ..settings import Settings

_KEY = re.compile(r"^[A-Za-z0-9_\-.]{10,200}$")
_ACE = re.compile(r"^(?P<account>\S.*?):(?P<rights>(?:\([A-Z,]+\))+)\s*$")


class KeyFileError(OSError):
    """The key file could not be written with owner-only access (nothing is left behind)."""


def key_path(settings: Settings) -> Path:
    return settings.path(settings.annot.alphagenome_key_file)


def read_key(settings: Settings) -> str | None:
    """The key, or None when the file is missing, unreadable or empty."""
    try:
        text = key_path(settings).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return text or None


def has_key(settings: Settings) -> bool:
    return read_key(settings) is not None


def valid_key(key: str) -> bool:
    """A pasted key: one token of letters, digits, `_`, `-` or `.` (Google API keys look like `AIza...`)."""
    return bool(_KEY.match(key))


def current_account() -> tuple[str, str]:
    """(DOMAIN\\user, SID) of the process token from `whoami`, not the spoofable USERNAME variable (#304)."""
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30,
                         check=True).stdout
    match = re.search(r"\"([^\"]+)\",\"(S-1-[0-9-]+)\"", out)
    if not match:
        raise KeyFileError("현재 계정을 읽지 못했습니다")
    return match.group(1), match.group(2)


def acl_entries(path: Path) -> list[tuple[str, str]]:
    """(account, rights) for every ACE `icacls` lists on `path`, e.g. ("PC\\me", "(F)") or (.., "(I)(F)")."""
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=60).stdout
    entries = []
    for number, line in enumerate(out.splitlines()):
        text = line[len(str(path)):] if number == 0 and line.startswith(str(path)) else line
        match = _ACE.match(text.strip())
        if match:
            entries.append((match.group("account").strip(), match.group("rights")))
    return entries


def _set_owner_only_dacl(path: Path, sid: str, folder: bool) -> None:
    """Replace the whole DACL of `path` with one protected entry: full control for `sid`.

    The DACL is written as a unit, so explicit entries already on the folder (a GitHub runner's temp folder carries
    SYSTEM, Administrators and OWNER RIGHTS that `icacls /inheritance:r` left in place, PR #450) go too. SYSTEM gets no
    entry: services running as SYSTEM keep their backup and take-ownership privileges whatever the DACL says, so an entry
    would give them nothing they lack, and "this account alone" stays one rule the check below can verify.
    """
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    to_sd = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    to_sd.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    to_sd.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
                         ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    set_info = advapi32.SetNamedSecurityInfoW
    set_info.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p]
    set_info.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    sddl = f"D:P(A;{'OICI' if folder else ''};FA;;;{sid})"  # P: protected, nothing inherited from above
    descriptor = ctypes.c_void_p()
    if not to_sd(sddl, 1, ctypes.byref(descriptor), None):
        raise KeyFileError(f"접근 권한을 만들지 못했습니다(Windows error {ctypes.get_last_error()})")
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise KeyFileError(f"접근 권한을 만들지 못했습니다(Windows error {ctypes.get_last_error()})")
        se_file_object, dacl_info, protected_dacl_info = 1, 0x4, 0x80000000
        error = set_info(str(path), se_file_object, dacl_info | protected_dacl_info, None, None, dacl, None)
        if error:
            raise KeyFileError(f"접근 권한을 바꾸지 못했습니다(Windows error {error})")
    finally:
        kernel32.LocalFree(descriptor)


def _windows_owner_only(path: Path, account: str, sid: str, folder: bool) -> None:
    """Give `path` a DACL with the account alone, then check with `icacls` that nothing else is left."""
    _set_owner_only_dacl(path, sid, folder)
    entries = acl_entries(path)
    others = [name for name, _rights in entries if name.lower() != account.lower() and name != f"*{sid}"]
    if not entries or others or any("(I)" in rights for _name, rights in entries):
        raise KeyFileError(f"키 {'폴더' if folder else '파일'}에 다른 계정의 접근이 남아 있습니다({len(others)}개)")


def _within(inner: Path, outer: Path) -> bool:
    try:
        inner.relative_to(outer)
        return True
    except ValueError:
        return False


def check_key_folder(path: Path, keep_out: Iterable[Path] = ()) -> None:
    """Refuse a key folder that is not the key's own: one holding other files, the home folder itself, or one that
    contains (or is) a folder in `keep_out` (labhq state, workspaces, config). Owner-only access on it would spread
    to everything below and cut sandbox and service accounts out."""
    folder = Path(os.path.abspath(path.parent))
    if folder == Path(os.path.abspath(Path.home())) or folder.parent == folder:
        raise KeyFileError("키 파일을 홈 폴더나 드라이브 바로 아래에 둘 수 없습니다. 키 전용 하위 폴더를 지정하세요")
    for other in keep_out:
        if _within(Path(os.path.abspath(other)), folder):
            raise KeyFileError("키 파일 폴더 안에 labhq의 state·작업 폴더·설정이 있습니다. 키 전용 하위 폴더를 지정하세요")
    if folder.is_dir():
        others = [p.name for p in folder.iterdir()
                  if p.name != path.name and not (p.name.startswith(f".{path.name}.") and p.name.endswith(".tmp"))]
        if others:
            raise KeyFileError(f"키 파일 폴더에 다른 항목이 {len(others)}개 있습니다. 키만 두는 폴더를 "
                               "annot.alphagenome_key_file에 지정하세요")


def write_key(path: Path, key: str, keep_out: Iterable[Path] = ()) -> None:
    """Write `key` so only the current account can read it. On any failure nothing new is left at `path`."""
    folder = path.parent
    check_key_folder(path, keep_out)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        if os.name == "nt":
            account, sid = current_account()
            # The folder first: a file created in it afterwards never has wider access, even for a moment.
            _windows_owner_only(folder, account, sid, folder=True)
            temporary.write_text(key + "\n", encoding="utf-8")
            _windows_owner_only(temporary, account, sid, folder=False)
        else:
            os.chmod(folder, 0o700)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(key + "\n")
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except (OSError, subprocess.SubprocessError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, KeyFileError):
            raise
        raise KeyFileError(f"키 파일을 이 계정 전용으로 쓰지 못했습니다: {type(exc).__name__}") from exc
