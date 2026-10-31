"""Read-only disk probes; never create the requested directory."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

GIB = 1024 ** 3


def usage(path: Path):
    """Probe the nearest existing ancestor, including for a future workspace/home."""
    path = path.expanduser().absolute()
    while not path.exists():
        if path.parent == path:
            raise OSError("disk unavailable")
        path = path.parent
    return shutil.disk_usage(path)


def low(space, free_gb: float, free_percent: float) -> bool:
    return (space.free < free_gb * GIB or space.total <= 0
            or space.free / space.total * 100 < free_percent)


def data_drives() -> list[Path]:
    """Windows fixed data volumes only: no removable or network drive probes."""
    if os.name != "nt":
        return []
    import ctypes

    kernel = ctypes.windll.kernel32
    system = os.environ.get("SystemDrive", "C:").casefold()
    mask = kernel.GetLogicalDrives()
    return [Path(f"{chr(65 + index)}:/") for index in range(26)
            if mask & (1 << index) and f"{chr(65 + index)}:".casefold() != system
            and kernel.GetDriveTypeW(f"{chr(65 + index)}:\\") == 3]


# ---- a shell command that is about to write a lot (#678) ----
ARCHIVE_RATIO = 4.0  # a tar/gz of unknown content is guessed at this many times its size; zip says its real size
_BULK_TOOLS = frozenset({"fasterq-dump", "fastq-dump", "prefetch", "aws", "gsutil", "rsync"})
_SEGMENT = re.compile(r"&&|\|\||[;|\n]")


@dataclass(frozen=True)
class ShellCheck:
    action: str  # "ask" | "warn"
    reason: str


def _words(segment: str) -> list[str] | None:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def _resolve(text: str, workdir: str) -> Path:
    if os.name == "nt":  # Git Bash spells C:\x as /c/x
        text = re.sub(r"^/([A-Za-z])(?=/)", lambda m: m.group(1).upper() + ":", text)
    path = Path(text).expanduser()
    return path if path.is_absolute() else Path(workdir) / path


def _option(words: list[str], short: str, long: str | None = None) -> str | None:
    """The value of `-X value`, `-Xvalue` or `--long value` / `--long=value`; None when absent."""
    for i, word in enumerate(words[1:], 1):
        if long and word == long and i + 1 < len(words):
            return words[i + 1]
        if long and word.startswith(long + "="):
            return word[len(long) + 1:]
        if word == short and i + 1 < len(words):
            return words[i + 1]
        if word.startswith(short) and len(word) > len(short) and not word.startswith("--"):
            return word[len(short):]
    return None


def _zip_size(path: Path) -> int | None:
    try:
        with zipfile.ZipFile(path) as archive:
            return sum(info.file_size for info in archive.infolist())
    except (OSError, zipfile.BadZipFile, ValueError):
        return None


def _packed_size(path: Path) -> int | None:
    try:
        return int(path.stat().st_size * ARCHIVE_RATIO)
    except OSError:
        return None


def _write(words: list[str], workdir: str) -> tuple[str, Path, int | None] | None:
    """(what, folder it writes into, estimated bytes or None) for one simple command, or None when it is no big write."""
    name = Path(words[0]).name.casefold().removesuffix(".exe")
    if name in {"curl", "wget"}:
        out = _option(words, "-o", "--output") if name == "curl" else (_option(words, "-O", "--output-document")
                                                                      or _option(words, "-P", "--directory-prefix"))
        target = _resolve(out, workdir) if out else Path(workdir)
        return "다운로드", (target.parent if target.suffix else target), None
    if name == "unzip":
        archive = next((w for w in words[1:] if not w.startswith("-")), None)
        folder = _option(words, "-d")
        return ("압축 해제", _resolve(folder, workdir) if folder else Path(workdir),
                _zip_size(_resolve(archive, workdir)) if archive else None)
    if name in {"tar", "bsdtar"}:
        flags = "".join(w.lstrip("-") for w in words[1:2] if re.match(r"-?[A-Za-z]+$", w))
        if "x" not in flags:
            return None
        archive = _option(words, "-f", "--file")
        if archive is None and "f" in flags:
            index = 2 if len(words) > 2 else None
            archive = words[index] if index is not None and not words[index].startswith("-") else None
        folder = _option(words, "-C", "--directory")
        return ("압축 해제", _resolve(folder, workdir) if folder else Path(workdir),
                _packed_size(_resolve(archive, workdir)) if archive else None)
    if name in {"7z", "7za", "7zr"} and len(words) > 2 and words[1] in {"x", "e"}:
        archive = next((w for w in words[2:] if not w.startswith("-")), None)
        folder = next((w[2:] for w in words[2:] if w.startswith("-o") and len(w) > 2), None)
        return ("압축 해제", _resolve(folder, workdir) if folder else Path(workdir),
                _packed_size(_resolve(archive, workdir)) if archive else None)
    if name in _BULK_TOOLS or (name == "git" and "clone" in words[1:3]):
        return "큰 데이터 내려받기", Path(workdir), None
    return None


def shell_check(command: str, workdir: str, *, fail_gb: float, warn_gb: float, warn_percent: float,
                probe=None) -> ShellCheck | None:
    """Whether a shell command that downloads or unpacks should ask the PI first because the drive it writes to is short.

    Only a certain shortfall asks: free space below `fail_gb`, or a known size (zip listing, or a packed archive's size
    times `ARCHIVE_RATIO`) that would leave less than `fail_gb`. An unknown size on a drive below the warning level only
    warns; pipes, variables and anything unparsed are left alone, as is an unreadable drive. Never denies."""
    probe = probe or usage
    worst: ShellCheck | None = None
    for segment in _SEGMENT.split(command):
        words = _words(segment)
        if not words:
            continue
        try:
            found = _write(words, workdir)
            if found is None:
                continue
            what, folder, estimate = found
            space = probe(folder)
        except (OSError, ValueError):
            continue
        if space.total <= 0:
            continue
        free = space.free / GIB
        if fail_gb and space.free < fail_gb * GIB:
            verdict = ShellCheck("ask", f"{what} 대상 드라이브({folder.as_posix()}) 여유가 {free:.1f} GiB로 "
                                        f"기준 {fail_gb:g} GiB보다 적습니다")
        elif estimate is not None and fail_gb and space.free - estimate < fail_gb * GIB:
            verdict = ShellCheck("ask", f"{what}에 약 {estimate / GIB:.1f} GiB가 필요한데 대상 드라이브"
                                        f"({folder.as_posix()}) 여유는 {free:.1f} GiB뿐입니다(기준 {fail_gb:g} GiB 남겨야 함)")
        elif estimate is None and low(space, warn_gb, warn_percent):
            verdict = ShellCheck("warn", f"{what}: 대상 드라이브({folder.as_posix()}) 여유가 적습니다({free:.1f} GiB). "
                                         "크기를 알 수 없어 막지 않습니다")
        else:
            continue
        if worst is None or (verdict.action == "ask" and worst.action != "ask"):
            worst = verdict
    return worst
