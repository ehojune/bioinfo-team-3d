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


def _segments(command: str) -> list[tuple[list[str], list[str]]]:
    """The simple commands of a shell line as (words, redirect targets), read the way bash reads it.

    Commands end at `;` `&&` `||` `|` `&` `(` `)` and newlines outside quotes. A `#` starts a comment only at the start
    of a word (`url#frag` is one word). `>` `>>` `&>` `2>` name a file the command writes and `<` a file it reads: the
    redirection is not part of the words and splits nothing. A line whose quotes do not close is not read at all."""
    segments: list[tuple[list[str], list[str]]] = []
    words: list[str] = []
    redirects: list[str] = []
    current: list[str] | None = None  # the word being read; None between words
    pending: str | None = None  # "out" / "in": the next word is a redirection target
    i, n = 0, len(command)

    def finish_word() -> None:
        nonlocal current, pending
        if current is None:
            return
        word = "".join(current)
        current = None
        if pending == "out":
            if not (word.rstrip("-").isdigit() or word == "-"):  # `2>&1`, `1>&3-` duplicate or move a descriptor
                redirects.append(word)
        elif pending is None:
            words.append(word)
        pending = None

    def finish_segment() -> None:
        nonlocal words, redirects
        finish_word()
        if words:
            segments.append((words, redirects))
        words, redirects = [], []

    while i < n:
        c = command[i]
        if c in " \t\r":
            finish_word()
        elif c == "\n":
            finish_segment()
        elif c == "#" and current is None:
            while i < n and command[i] != "\n":
                i += 1
            continue
        elif c in "'\"":
            j = i + 1
            while j < n and command[j] != c:
                j += 2 if (c == '"' and command[j] == "\\" and j + 1 < n) else 1
            if j >= n:
                return []
            current = (current or []) + list(command[i + 1:j].replace('\\"', '"') if c == '"' else command[i + 1:j])
            i = j
        elif c == "\\" and command[i + 1:i + 2] == chr(10):
            i += 1  # a backslash-newline continues the line: bash drops both
        elif c == "\\" and i + 1 < n:
            current = (current or []) + [command[i + 1]]
            i += 1
        elif c in ";(|)" or (c == "&" and command[i + 1:i + 2] != ">"):
            finish_segment()
            if command[i:i + 2] in {"&&", "||"}:
                i += 1
        elif c in "<>" or c == "&":
            if current is not None and "".join(current).isdigit():
                current = None  # `2>file`: the 2 is a file descriptor
            else:
                finish_word()
            out = c in ">&"  # `&>` is an output redirect too
            i += 1
            if c == "&":
                i += 1
                if command[i:i + 1] == ">":
                    i += 1
            elif command[i:i + 1] in (">", "&", "|") if c == ">" else command[i:i + 1] in ("<", ">", "&"):
                i += 1
            pending = "out" if out else "in"
            continue
        else:
            current = (current or []) + [c]
        i += 1
    finish_segment()
    return segments


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
        letter = short[1:]
        if len(letter) == 1 and word.startswith("-") and not word.startswith("--") and len(word) > 2:
            cluster = word[1:]
            at = cluster.find(letter)
            if at >= 0:  # `-Lo file`: the letter ends a cluster and takes the next word; `-sfoFILE` takes the rest
                rest = cluster[at + 1:]
                if rest:
                    return rest
                if i + 1 < len(words):
                    return words[i + 1]
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
        if word in {"-x", "-i"}:  # file patterns follow up to the next option
            i += 1
            while i < len(words) and not words[i].startswith("-"):
                i += 1
            continue
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


def _assess(what: str, folder: Path, estimate: int | None, extra: list[Path], *, probe, fail_gb: float,
            warn_gb: float, warn_percent: float) -> ShellCheck | None:
    """The verdict for one write: where it goes (`folder` and every `extra` folder the same command also writes), and
    how big it is when that is known."""
    spaces = []
    for candidate in dict.fromkeys([folder, *extra]):
        try:
            spaces.append((candidate, probe(candidate)))
        except (OSError, ValueError):
            continue  # an unreadable drive is never a reason to ask
    spaces = [(f, sp) for f, sp in spaces if sp.total > 0]
    if not spaces:
        return None
    for candidate, space in spaces:
        if fail_gb and space.free < fail_gb * GIB:
            return ShellCheck("ask", f"{what} 대상 드라이브({candidate.as_posix()}) 여유가 "
                                     f"{space.free / GIB:.1f} GiB로 기준 {fail_gb:g} GiB보다 적습니다")
    main = spaces[0][1] if spaces[0][0] == folder else None
    if main is None:
        return None
    free = main.free / GIB
    if estimate is not None and fail_gb and main.free - estimate < fail_gb * GIB:
        return ShellCheck("ask", f"{what}에 약 {estimate / GIB:.1f} GiB가 필요한데 대상 드라이브"
                                 f"({folder.as_posix()}) 여유는 {free:.1f} GiB뿐입니다"
                                 f"(기준 {fail_gb:g} GiB 남겨야 함)")
    if estimate is None and low(main, warn_gb, warn_percent):
        return ShellCheck("warn", f"{what}: 대상 드라이브({folder.as_posix()}) 여유가 적습니다({free:.1f} GiB). "
                                  "크기를 알 수 없어 막지 않습니다")
    return None


def _worse(worst: ShellCheck | None, verdict: ShellCheck | None) -> ShellCheck | None:
    if verdict is None:
        return worst
    if worst is None or (verdict.action == "ask" and worst.action != "ask"):
        return verdict
    return worst


def shell_check(command: str, workdir: str, *, fail_gb: float, warn_gb: float, warn_percent: float,
                probe=None) -> ShellCheck | None:
    """Whether a shell command that downloads or unpacks should ask the PI first because the drive it writes to is short.

    Only a certain shortfall asks: free space below `fail_gb`, or a known size (zip listing, or a packed archive's size
    times `ARCHIVE_RATIO`) that would leave less than `fail_gb`. An unknown size on a drive below the warning level only
    warns; pipes, variables and anything unparsed are left alone, as is an unreadable drive. Never denies."""
    probe = probe or usage
    worst: ShellCheck | None = None
    cwd = workdir
    for words, redirects in _segments(command):
        if Path(words[0]).name in {"cd", "pushd"} and len(words) == 2 and "$" not in words[1] and "`" not in words[1]:
            cwd = str(_resolve(words[1], cwd))  # the commands that follow run there
            continue
        try:
            found = _write(words, cwd)
            if found is None:
                continue
            what, folder, estimate = found
            extra = [*_operand_folders(words, cwd), *(_resolve(target, cwd) for target in redirects)]
        except (OSError, ValueError):
            continue
        worst = _worse(worst, _assess(what, folder, estimate, extra, probe=probe, fail_gb=fail_gb, warn_gb=warn_gb,
                                      warn_percent=warn_percent))
    return worst


# ---- the same for the PowerShell tool (#1167) ----
# PowerShell is read as PowerShell reads it, not as bash: `\` is a path separator, not an escape; `'…'` is literal and
# `"…"` takes a backtick escape; `;` `|` `&&` `||` and newlines end a command; `#` starts a comment at a word start.
_PS_DOWNLOADS = frozenset({"invoke-webrequest", "iwr", "invoke-restmethod", "irm", "curl", "wget"})
_PS_UNPACK = frozenset({"expand-archive"})
_PS_TRANSFER = frozenset({"start-bitstransfer"})
_PS_SEPARATORS = ";|&\n\r"


def _ps_commands(command: str) -> list[list[str]]:
    """The commands of a PowerShell line as word lists. A line whose quotes do not close is not read at all."""
    commands: list[list[str]] = []
    words: list[str] = []
    current: list[str] | None = None
    quote: str | None = None
    i, n = 0, len(command)

    def end_word() -> None:
        nonlocal current
        if current is not None:
            words.append("".join(current))
            current = None

    def end_command() -> None:
        nonlocal words
        end_word()
        if words:
            commands.append(words)
        words = []

    while i < n:
        c = command[i]
        if quote == "'":
            if c == "'":
                if command[i + 1:i + 2] == "'":  # '' is one quote inside a literal string
                    current.append("'")
                    i += 1
                else:
                    quote = None
            else:
                current.append(c)
        elif quote == '"':
            if c == "`" and i + 1 < n:
                current.append(command[i + 1])
                i += 1
            elif c == '"':
                if command[i + 1:i + 2] == '"':
                    current.append('"')
                    i += 1
                else:
                    quote = None
            else:
                current.append(c)
        elif c in "'\"":
            quote = c
            current = [] if current is None else current
        elif c == "`" and i + 1 < n:
            current = [] if current is None else current
            current.append(command[i + 1])
            i += 1
        elif c in _PS_SEPARATORS:
            end_command()
        elif c.isspace():
            end_word()
        elif c == "#" and current is None:
            while i < n and command[i] not in "\r\n":
                i += 1
            continue
        else:
            current = [] if current is None else current
            current.append(c)
        i += 1
    if quote is not None:
        return []
    end_command()
    return commands


def _ps_param(words: list[str], full: str, minimum: int) -> str | None:
    """The value of a named parameter: `-OutFile x`, `-OutFile:x`, or any unambiguous prefix of its name of at least
    `minimum` letters (PowerShell accepts `-OutF`). The last occurrence wins."""
    found = None
    for index, word in enumerate(words[1:], start=1):
        if not word.startswith("-") or len(word) < 2:
            continue
        name, colon, value = word[1:].partition(":")
        if len(name) < minimum or not full.startswith(name.casefold()):
            continue
        if colon:
            found = value
        elif index + 1 < len(words) and not words[index + 1].startswith("-"):
            found = words[index + 1]
    return found


def _ps_positional(words: list[str]) -> list[str]:
    """Words that are neither a parameter nor, as far as can be told, a parameter's value."""
    out = []
    skip = False
    for word in words[1:]:
        if skip:
            skip = False
            continue
        if word.startswith("-") and len(word) > 1:
            skip = ":" not in word and word[1:].casefold() in {
                "path", "literalpath", "destinationpath", "destination", "source", "outfile", "uri"}
            continue
        out.append(word)
    return out


def _unwritten(path: str) -> bool:
    """A path that a variable or subexpression makes: not written out, so not read."""
    return "$" in path or "`" in path


def _ps_write(words: list[str], workdir: str) -> tuple[str, Path, int | None] | None:
    """(what, folder it writes into, estimated bytes or None) for one PowerShell command, or None when it is none."""
    name = words[0].casefold()
    if name in _PS_DOWNLOADS:
        out = _ps_param(words, "outfile", 4)
        if not out or _unwritten(out):
            return None  # the result goes to the pipeline, or the path is not written out
        target = _resolve(out, workdir)
        return "다운로드", target.parent, None
    if name in _PS_UNPACK:
        destination = _ps_param(words, "destinationpath", 1)
        archive = _ps_param(words, "path", 1) or _ps_param(words, "literalpath", 1)
        positional = _ps_positional(words)
        if archive is None and positional:
            archive = positional[0]
        if destination is None and len(positional) >= 2:
            destination = positional[1]
        if destination is not None and _unwritten(destination):
            return None
        # No destination named: it unpacks into the current folder, which is still a drive that can be short.
        folder = _resolve(destination, workdir) if destination else Path(workdir)
        known = _zip_size(_resolve(archive, workdir)) if archive and not _unwritten(archive) else None
        return "압축 해제", folder, known
    if name in _PS_TRANSFER:
        destination = _ps_param(words, "destination", 4)
        positional = _ps_positional(words)
        if destination is None and len(positional) >= 2:
            destination = positional[1]
        transfer = (_ps_param(words, "transfertype", 4) or "").casefold()
        if not destination or _unwritten(destination) or transfer == "upload" or "://" in destination:
            return None  # an upload, or a remote destination, writes no local folder
        target = _resolve(destination, workdir)
        return "다운로드", (target.parent if target.suffix else target), None
    return None


def powershell_check(command: str, workdir: str, *, fail_gb: float, warn_gb: float, warn_percent: float,
                     probe=None) -> ShellCheck | None:
    """`shell_check` for the PowerShell tool: `Invoke-WebRequest -OutFile`, `Invoke-RestMethod -OutFile`,
    `Expand-Archive` and `Start-BitsTransfer`. Same rule: only a certain shortfall asks, an unknown size never blocks,
    a command that uses a variable or a subexpression for the path is left alone."""
    probe = probe or usage
    worst: ShellCheck | None = None
    cwd = workdir
    for words in _ps_commands(command):
        name = words[0].casefold()
        if name in {"cd", "set-location", "sl", "push-location", "pushd"} and len(words) == 2 and "$" not in words[1]:
            cwd = str(_resolve(words[1], cwd))
            continue
        try:
            found = _ps_write(words, cwd)
        except (OSError, ValueError):
            continue
        if found is None:
            continue
        what, folder, estimate = found
        worst = _worse(worst, _assess(what, folder, estimate, [], probe=probe, fail_gb=fail_gb, warn_gb=warn_gb,
                                      warn_percent=warn_percent))
    return worst


def preapproves_bulk_write(rule: str) -> bool:
    """Whether a Claude `--allowedTools` rule such as `Bash(unzip *)` or `Bash(git clone *)` pre-approves a command
    that `shell_check` reads. Claude runs a pre-approved call without asking the gate, so the rule is left out and
    the call reaches the gate (and the disk check) instead (#678)."""
    match = re.fullmatch(r"\s*(Bash|PowerShell)\((.*)\)\s*", rule, flags=re.DOTALL)
    if not match:
        return False
    words = [w for w in re.split(r"[\s:*]+", match.group(2)) if w]
    if not words:
        return False
    if match.group(1) == "PowerShell":
        return words[0].casefold() in _PS_DOWNLOADS | _PS_UNPACK | _PS_TRANSFER
    name = Path(words[0]).name.casefold().removesuffix(".exe")
    if name == "git":
        return words[1:2] == ["clone"]
    return name in _BULK_TOOLS or name in {"curl", "wget", "unzip", "tar", "bsdtar", "7z", "7za", "7zr"}
