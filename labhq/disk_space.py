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


@dataclass(frozen=True)
class ShellCheck:
    action: str  # "ask" | "warn"
    reason: str


def _segments(command: str) -> list[list[str]]:
    """The simple commands of a shell line as word lists, split quote-aware at `;` `&&` `||` `|` `&` and newlines.
    A line whose quotes do not close is not read at all."""
    operators = "();<>|&" + chr(10)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=operators)
    lexer.whitespace, lexer.whitespace_split = " " + chr(9) + chr(13), True
    out: list[list[str]] = [[]]
    try:
        for token in lexer:
            if set(token) <= set(operators):
                out.append([])
            else:
                out[-1].append(token)
    except ValueError:
        return []
    return [words for words in out if words]


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
        if short and word == short and i + 1 < len(words):
            return words[i + 1]
        if short and word.startswith(short) and len(word) > len(short) and not word.startswith("--"):
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


def _tar_options(words: list[str]) -> tuple[bool, str | None, str | None]:
    """(extracts?, archive, folder) from every spelling: `xzf a.tgz`, `-C out -xf a`, `--extract --file=a`, `--get`."""
    extract, archive, folder = False, None, None
    rest = words[1:]
    i = 0
    while i < len(rest):
        word = rest[i]
        nxt = rest[i + 1] if i + 1 < len(rest) else None
        if word.startswith("--"):
            flag, equal, attached = word.partition("=")
            extract = extract or flag in {"--extract", "--get"}
            if flag == "--file":
                archive, i = (attached, i) if equal else (nxt, i + 1)
            elif flag == "--directory":
                folder, i = (attached, i) if equal else (nxt, i + 1)
        elif (word.startswith("-") and len(word) > 1) or (i == 0 and re.match(r"^[A-Za-z]+$", word)):
            letters = word.lstrip("-")
            for j, letter in enumerate(letters):
                if letter == "x":
                    extract = True
                elif letter in "fC":
                    attached = letters[j + 1:]
                    if attached:  # `-C/out`, `-xf/archive.tar`
                        value = attached
                    elif i + 1 < len(rest):
                        value, i = rest[i + 1], i + 1
                    else:
                        value = None
                    if letter == "f":
                        archive = value
                    else:
                        folder = value
                    if attached:
                        break
        i += 1
    return extract, archive, folder


def _clone_folder(words: list[str], workdir: str) -> Path:
    """Where `git clone [options] <repo> [<dir>]` writes: the named folder, else a new one inside the work folder.

    Every option takes a value unless it is one of the switches below (`--opt=value` carries its own), so an option
    this does not know never turns its value into the repository."""
    switches = {"--bare", "--mirror", "-n", "--no-checkout", "-q", "--quiet", "-v", "--verbose", "--progress",
                "--recursive", "--recurse-submodules", "--shallow-submodules", "--no-shallow-submodules",
                "--single-branch", "--no-single-branch", "--no-tags", "--tags", "-l", "--local", "-s", "--shared",
                "--no-hardlinks", "--dissociate", "--sparse", "--also-filter-submodules", "--reject-shallow",
                "--no-reject-shallow", "--checkout", "--remote-submodules", "--no-remote-submodules", "-4", "--ipv4", "-6",
                "--ipv6", "--no-local", "--no-shared", "--hardlinks"}
    short_values = {"-b", "-o", "-c", "-j", "-u"}
    positional, i = [], words.index("clone") + 1
    while i < len(words):
        word = words[i]
        if word == "--":  # everything after is an operand
            positional.extend(words[i + 1:])
            break
        if word.startswith("--"):
            i += 1 if "=" in word or word in switches else 2
            continue
        if word.startswith("-") and len(word) > 1:
            i += 2 if word in short_values else 1
            continue
        positional.append(word)
        i += 1
    return _resolve(positional[1], workdir) if len(positional) > 1 else Path(workdir)


def _unzip_options(words: list[str]) -> tuple[str | None, str | None, bool]:
    """(archive, folder, extracts?) of an unzip call: `-P pw` and `-d dir` take a value, `-l -t -p -z -v` only read."""
    archive, folder, extracts, i = None, None, True, 1
    while i < len(words):
        word = words[i]
        if word in {"-d", "-P"} and i + 1 < len(words):
            if word == "-d":
                folder = words[i + 1]
            i += 2
            continue
        if word in {"-x", "-i"}:  # the rest are file patterns
            break
        if word.startswith("-d") and len(word) > 2:
            folder = word[2:]
        elif word.startswith("-") and len(word) > 1:
            if set(word[1:]) & set("ltpzv") and not word.startswith(("-P", "-d")):
                extracts = False
        elif archive is None:
            archive = word
        i += 1
    return archive, folder, extracts


_PATHLIKE = re.compile(r"^(?:[A-Za-z]:[\\/]|[/\\~]|\.{1,2}[/\\])|[/\\]")


def _operand_folders(words: list[str], cwd: str) -> list[Path]:
    """Every path-looking operand of a command, whatever option it follows (`-o a -o /full/b`, `-C/out`, `--dir=/x`).

    The drive of any of them may be the one filled, so the floor check reads them all; an operand that is only read
    costs a harmless question when that drive really is below the floor. URLs are not paths."""
    found: list[Path] = []
    for word in words[1:]:
        text = word
        if text.startswith("-"):
            if "=" in text:
                text = text.partition("=")[2]
            elif len(text) > 2 and not text.startswith("--"):
                text = text[2:]
            else:
                continue
        if "://" in text or not _PATHLIKE.search(text):
            continue
        found.append(_resolve(text, cwd))
    return found


def _write(words: list[str], workdir: str) -> tuple[str, Path, int | None] | None:
    """(what, folder it writes into, estimated bytes or None) for one simple command, or None when it is no big write."""
    name = Path(words[0]).name.casefold().removesuffix(".exe")
    if name in {"curl", "wget"}:
        out = _option(words, "-o", "--output") if name == "curl" else (_option(words, "-O", "--output-document")
                                                                      or _option(words, "-P", "--directory-prefix"))
        outdir = _option(words, "", "--output-dir") if name == "curl" else None
        base = _resolve(outdir, workdir) if outdir else Path(workdir)
        target = (_resolve(out, str(base)) if out else base)
        return "다운로드", (target.parent if out and target.suffix else target), None
    if name == "unzip":
        archive, folder, extracts = _unzip_options(words)
        if not extracts:
            return None
        return ("압축 해제", _resolve(folder, workdir) if folder else Path(workdir),
                _zip_size(_resolve(archive, workdir)) if archive else None)
    if name in {"tar", "bsdtar"}:
        extract, archive, folder = _tar_options(words)
        if not extract:
            return None
        return ("압축 해제", _resolve(folder, workdir) if folder else Path(workdir),
                _packed_size(_resolve(archive, workdir)) if archive else None)
    if name in {"7z", "7za", "7zr"} and len(words) > 2 and words[1] in {"x", "e"}:
        archive = next((w for w in words[2:] if not w.startswith("-")), None)
        folder = next((w[2:] for w in words[2:] if w.startswith("-o") and len(w) > 2), None)
        return ("압축 해제", _resolve(folder, workdir) if folder else Path(workdir),
                _packed_size(_resolve(archive, workdir)) if archive else None)
    if name == "git" and "clone" in words[1:3]:
        return "큰 데이터 내려받기", _clone_folder(words, workdir), None
    if name in _BULK_TOOLS:
        out = _option(words, "-O", "--outdir") if name in {"fasterq-dump", "fastq-dump", "prefetch"} else None
        if out is None and name in {"aws", "gsutil", "rsync"}:
            last = words[-1] if len(words) > 2 and not words[-1].startswith("-") else ""
            if last and "://" not in last and not re.match(r"^[^/\:]{2,}:", last):  # a local folder, not s3://, gs://, host:
                out = last
        return "큰 데이터 내려받기", _resolve(out, workdir) if out else Path(workdir), None
    return None


def shell_check(command: str, workdir: str, *, fail_gb: float, warn_gb: float, warn_percent: float,
                probe=None) -> ShellCheck | None:
    """Whether a shell command that downloads or unpacks should ask the PI first because the drive it writes to is short.

    Only a certain shortfall asks: free space below `fail_gb`, or a known size (zip listing, or a packed archive's size
    times `ARCHIVE_RATIO`) that would leave less than `fail_gb`. An unknown size on a drive below the warning level only
    warns; pipes, variables and anything unparsed are left alone, as is an unreadable drive. Never denies."""
    probe = probe or usage
    worst: ShellCheck | None = None
    cwd = workdir
    for words in _segments(command):
        if Path(words[0]).name in {"cd", "pushd"} and len(words) == 2 and "$" not in words[1] and "`" not in words[1]:
            cwd = str(_resolve(words[1], cwd))  # the commands that follow run there
            continue
        try:
            found = _write(words, cwd)
            if found is None:
                continue
            what, folder, estimate = found
            folders = [folder, *_operand_folders(words, cwd)]
            spaces = []
            for candidate in dict.fromkeys(folders):
                try:
                    spaces.append((candidate, probe(candidate)))
                except (OSError, ValueError):
                    continue  # an unreadable drive is never a reason to ask
        except (OSError, ValueError):
            continue
        spaces = [(f, sp) for f, sp in spaces if sp.total > 0]
        if not spaces:
            continue
        verdict = None
        for candidate, space in spaces:
            if fail_gb and space.free < fail_gb * GIB:
                verdict = ShellCheck("ask", f"{what} 대상 드라이브({candidate.as_posix()}) 여유가 "
                                            f"{space.free / GIB:.1f} GiB로 기준 {fail_gb:g} GiB보다 적습니다")
                break
        main = spaces[0][1] if spaces[0][0] == folder else None
        if verdict is None and main is not None:
            free = main.free / GIB
            if estimate is not None and fail_gb and main.free - estimate < fail_gb * GIB:
                verdict = ShellCheck("ask", f"{what}에 약 {estimate / GIB:.1f} GiB가 필요한데 대상 드라이브"
                                            f"({folder.as_posix()}) 여유는 {free:.1f} GiB뿐입니다"
                                            f"(기준 {fail_gb:g} GiB 남겨야 함)")
            elif estimate is None and low(main, warn_gb, warn_percent):
                verdict = ShellCheck("warn", f"{what}: 대상 드라이브({folder.as_posix()}) 여유가 적습니다({free:.1f} GiB). "
                                             "크기를 알 수 없어 막지 않습니다")
        if verdict is None:
            continue
        if worst is None or (verdict.action == "ask" and worst.action != "ask"):
            worst = verdict
    return worst


def preapproves_bulk_write(rule: str) -> bool:
    """Whether a Claude `--allowedTools` rule such as `Bash(unzip *)` or `Bash(git clone *)` pre-approves a command
    that `shell_check` reads. Claude runs a pre-approved call without asking the gate, so the rule is left out and
    the call reaches the gate (and the disk check) instead (#678)."""
    match = re.fullmatch(r"\s*Bash\((.*)\)\s*", rule, flags=re.DOTALL)
    if not match:
        return False
    words = [w for w in re.split(r"[\s:*]+", match.group(1)) if w]
    if not words:
        return False
    name = Path(words[0]).name.casefold().removesuffix(".exe")
    if name == "git":
        return words[1:2] == ["clone"]
    return name in _BULK_TOOLS or name in {"curl", "wget", "unzip", "tar", "bsdtar", "7z", "7za", "7zr"}
