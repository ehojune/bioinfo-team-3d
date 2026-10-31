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


# PowerShell treats these typographic quotes as the ASCII ones (about_Quoting_Rules).
_PS_SMART_QUOTES = {0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x201C: '"', 0x201D: '"', 0x201E: '"'}


def _ps_commands(command: str) -> list[list[str]]:
    """The commands of a PowerShell line as word lists. A line whose quotes do not close is not read at all."""
    command = command.translate(_PS_SMART_QUOTES)
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
        elif c == "`" and command[i + 1:i + 2] in ("\n", "\r"):
            end_word()  # a backtick right before a line break continues the command on the next line
            i += 2 if command[i + 1:i + 3] == "\r\n" else 1
        elif c == "`" and i + 1 < n:
            current = [] if current is None else current
            current.append(command[i + 1])
            i += 1
        elif c in _PS_SEPARATORS:
            end_command()
        elif c == "," and current is None and words:
            current = [words.pop(), ","]  # `a ,b`: the comma belongs to the array that began with the word before it
        elif c.isspace():
            if current is not None and current[-1] == ",":
                pass  # `a, b`: whitespace after a comma does not end the array
            else:
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


# Which words are parameters and which are their values is decided from each cmdlet's own parameter table, never from
# a guess: a name (or unambiguous prefix) that is not in the table makes the command's positional words unreadable.
# value=True takes the next word; False is a switch. The common parameters are the same for every cmdlet.
_PS_COMMON = {"verbose": False, "debug": False, "whatif": False, "confirm": False, "erroraction": True,
              "warningaction": True, "informationaction": True, "progressaction": True, "errorvariable": True,
              "warningvariable": True, "informationvariable": True, "outvariable": True, "outbuffer": True,
              "pipelinevariable": True}
_PS_COMMON_ALIASES = {"vb": "verbose", "db": "debug", "wi": "whatif", "cf": "confirm", "ea": "erroraction",
                      "wa": "warningaction", "infa": "informationaction", "pa": "progressaction",
                      "ev": "errorvariable", "wv": "warningvariable", "iv": "informationvariable",
                      "ov": "outvariable", "ob": "outbuffer", "pv": "pipelinevariable"}
_PS_WEB = {"uri": True, "outfile": True, "method": True, "body": True, "contenttype": True, "headers": True,
           "useragent": True, "credential": True, "usedefaultcredentials": False, "usebasicparsing": False,
           "websession": True, "sessionvariable": True, "timeoutsec": True, "maximumredirection": True,
           "maximumretrycount": True, "retryintervalsec": True, "proxy": True, "proxycredential": True,
           "proxyusedefaultcredentials": False, "skipcertificatecheck": False, "skiphttperrorcheck": False,
           "skipheadervalidation": False, "allowunencryptedauthentication": False, "authentication": True,
           "token": True, "certificate": True, "certificatethumbprint": True, "infile": True, "passthru": False,
           "resume": False, "transferencoding": True, "form": True, "noproxy": False, "disablekeepalive": False,
           "httpversion": True, "sslprotocol": True, "custommethod": True, "preserveauthorizationonredirect": False,
           "preservehttpmethodonredirect": False}
_PS_ARCHIVE = {"path": True, "literalpath": True, "destinationpath": True, "force": False}
_PS_BITS: dict[str, bool | None] = {"source": True, "destination": True, "displayname": True, "description": True, "priority": True,
            "transfertype": True, "retryinterval": True, "retrytimeout": True, "credential": True,
            "authentication": True, "proxyusage": True, "proxylist": True, "proxybypass": True,
            "proxycredential": True, "proxyauthentication": True, "notifyflags": True, "notifycmdline": True,
            "customheaders": True, "securityflags": True, "httpmethod": True, "transferpolicy": True,
            "asynchronous": False, "suspended": False, "dynamictransfer": False, "usestoredcredential": True,
            "maxdownloadtime": True, "aclflags": True, "customheaderswriteonly": None}
_PS_LOCATION = {"path": True, "literalpath": True, "passthru": False, "stackname": True}
_PS_LOCATION_NAMES = frozenset({"cd", "chdir", "set-location", "sl", "push-location", "pushd"})
_PS_LOCATION_LOST = frozenset({"pop-location", "popd"})


def _ps_name(word: str) -> str:
    """A command name as PowerShell resolves it: lower case, with a module qualifier (`Module.Name\\Cmdlet`) dropped."""
    name = word.casefold()
    head, slash, tail = name.rpartition("\\")
    return tail if slash and re.fullmatch(r"[\w.]+", head) else name


def _ps_match(name: str, table: dict[str, bool]) -> str | None:
    """The parameter a `-name` means: exact, a common alias, or the one parameter it is an unambiguous prefix of."""
    name = name.casefold()
    if name in table:
        return name
    if name in _PS_COMMON_ALIASES:
        return _PS_COMMON_ALIASES[name]
    found = [key for key in table if name and key.startswith(name)]
    return found[0] if len(found) == 1 else None


_PS_MAX_DOUBT = 4  # unknown parameters read both ways; past this many the positional words are not read at all


def _ps_flag(word: str) -> bool:
    return word.startswith("-") and len(word) > 1 and not word[1].isdigit()


def _ps_bind(words: list[str], spec: dict[str, bool | None], order: tuple[str, ...]) -> list[dict[str, str | bool]]:
    """Every way one command's words can bind to its parameters: named values first, then the positional words in
    `order`. A parameter that is not in the table (or whose kind is marked None) may be a switch or may take the next
    word, so both readings are returned and the caller looks at all of them; a word is never silently dropped. `partial`
    marks a reading whose positional words were not assigned (too many doubts, or a value-taking parameter without its
    value)."""
    table = {**_PS_COMMON, **spec}
    readings: list[dict[str, str | bool]] = []

    def finish(named: dict, positional: list[str], partial: bool) -> None:
        bound = dict(named)
        if partial:
            bound["partial"] = True
        else:
            left = [key for key in order if key not in named and not (key == "path" and "literalpath" in named)]
            bound.update(zip(left, positional))
        if bound not in readings:
            readings.append(bound)

    def walk(index: int, named: dict, positional: list[str], doubt: int) -> None:
        while index < len(words):
            word = words[index]
            index += 1
            if word == "--":
                positional = positional + words[index:]
                break
            if not _ps_flag(word):
                positional = positional + [word]
                continue
            name, colon, value = word[1:].partition(":")
            key = _ps_match(name, table)
            kind = table[key] if key is not None else None
            if kind is False:
                named = {**named, key: True}  # a switch (`-Force:$false` still names it; the value is not a path)
                continue
            if colon and value:  # the value is attached: nothing to guess
                named = {**named, key: value} if key is not None else named
                continue
            following = words[index] if index < len(words) and not _ps_flag(words[index]) else None
            if kind is True:
                if following is None:
                    finish(named, positional, True)  # a value-taking parameter without a value: not understood
                    return
                named = {**named, key: following}
                index += 1
                continue
            if doubt >= _PS_MAX_DOUBT:
                finish(named, positional, True)
                return
            walk(index, named, positional, doubt + 1)  # unknown or uncertain: a switch, ...
            if following is not None:  # ... or a parameter that takes the next word
                walk(index + 1, {**named, key: following} if key is not None else named, positional, doubt + 1)
            return
        finish(named, positional, False)

    walk(1, {}, [], 0)
    return readings


def _unwritten(path: str) -> bool:
    """A path that a variable or subexpression makes: not written out, so not read."""
    return "$" in path or "`" in path


def _unknown_folders(text: str, workdir: str) -> list[Path]:
    """A target that cannot be read for sure (a variable, a subexpression) is unknown: the current folder is the guess,
    and a drive the text itself names is another candidate."""
    folders = [Path(workdir)]
    match = re.match(r"^([A-Za-z]):", text.strip())
    if match:
        folders.append(Path(f"{match.group(1).upper()}:/"))
    return folders


def _ps_folders(destination: object, workdir: str, *, file_like: bool) -> list[Path] | None:
    """The local folders a `-Destination`-style value writes into: a comma list names one target per element, and an
    element that is not written out is unknown (`_unknown_folders`). A remote element writes no local folder."""
    if not isinstance(destination, str):
        return None
    folders: list[Path] = []
    for part in destination.split(","):
        part = part.strip()
        if not part or "://" in part:
            continue
        if _unwritten(part):
            folders.extend(_unknown_folders(part, workdir))
            continue
        target = _resolve(part, workdir)
        folders.append(target.parent if file_like and target.suffix else target)
    return list(dict.fromkeys(folders)) or None


def _ps_write(words: list[str], workdir: str) -> list[tuple[str, list[Path], int | None]]:
    """The writes one PowerShell command may make, one per way its words can bind: (what, folders it writes into,
    estimated bytes or None). Empty when it writes nothing readable."""
    name = _ps_name(words[0])
    found: list[tuple[str, list[Path], int | None]] = []
    if name in _PS_DOWNLOADS:
        for bound in _ps_bind(words, _PS_WEB, ("uri",)):
            out = bound.get("outfile")
            if isinstance(out, str):  # no -OutFile: the result goes to the pipeline
                found.append(("다운로드", _ps_folders(out, workdir, file_like=True) or [Path(workdir)], None))
    elif name in _PS_UNPACK:
        for bound in _ps_bind(words, _PS_ARCHIVE, ("path", "destinationpath")):
            destination = bound.get("destinationpath")
            if destination is None and bound.get("partial"):
                continue  # a destination that may sit behind a word that was not understood is not guessed
            archive = bound.get("path") or bound.get("literalpath")
            # No destination named: it unpacks into the current folder, which is still a drive that can be short.
            folders = (_ps_folders(destination, workdir, file_like=False) if isinstance(destination, str)
                       else None) or [Path(workdir)]
            known = (_zip_size(_resolve(archive, workdir)) if isinstance(archive, str) and not _unwritten(archive)
                     else None)
            found.append(("압축 해제", folders, known))
    elif name in _PS_TRANSFER:
        for bound in _ps_bind(words, _PS_BITS, ("source", "destination")):
            if str(bound.get("transfertype") or "").casefold().startswith("upload"):
                continue  # an upload writes no local folder
            folders = _ps_folders(bound.get("destination"), workdir, file_like=True)
            if folders:
                found.append(("다운로드", folders, None))
    return found


def _ps_location(words: list[str], cwds: list[str], workdir: str) -> list[str]:
    """Where the commands after a `Set-Location`/`Push-Location` run. A target that cannot be read leaves the folder
    unknown: the folders known so far (and the one the line started in) stay candidates, plus a drive the text names."""
    targets = set()
    unknown = False
    for bound in _ps_bind(words, _PS_LOCATION, ("path",)):
        target = bound.get("literalpath") or bound.get("path")
        if not isinstance(target, str) or _unwritten(target) or target == "-":
            unknown = True
            if isinstance(target, str):
                targets.update(str(f) for f in _unknown_folders(target, workdir)[1:])
        else:
            targets.add(target)
    if unknown or len(targets) != 1:
        return list(dict.fromkeys([*cwds, workdir, *targets]))
    (target,) = targets
    return list(dict.fromkeys(str(_resolve(target, cwd)) for cwd in cwds))


_PS_DRIVE_FUNCTION = re.compile(r"^[A-Za-z]:$")


def powershell_check(command: str, workdir: str, *, fail_gb: float, warn_gb: float, warn_percent: float,
                     probe=None) -> ShellCheck | None:
    """`shell_check` for the PowerShell tool: `Invoke-WebRequest -OutFile`, `Invoke-RestMethod -OutFile`,
    `Expand-Archive` and `Start-BitsTransfer`. Same rule: only a certain shortfall asks and an unknown size never
    blocks. What cannot be read for sure is unknown, never skipped: a target that is a variable, or a folder no
    `Set-Location` can name (`Pop-Location`, `D:`), is checked against the current folder and any drive the command
    names. A word the parameter tables do not explain is read both ways, and the worst reading counts."""
    probe = probe or usage
    worst: ShellCheck | None = None
    cwds = [workdir]
    for words in _ps_commands(command):
        name = _ps_name(words[0])
        if name in _PS_LOCATION_NAMES:
            cwds = _ps_location(words, cwds, workdir)
            continue
        if name in _PS_LOCATION_LOST:
            cwds = list(dict.fromkeys([*cwds, workdir]))
            continue
        if len(words) == 1 and _PS_DRIVE_FUNCTION.match(words[0]):
            cwds = list(dict.fromkeys([f"{words[0].upper()}/", *cwds]))  # `D:` switches to that drive's folder
            continue
        seen = set()
        for cwd in cwds:
            try:
                writes = _ps_write(words, cwd)
            except (OSError, ValueError):
                continue
            for what, folders, estimate in writes:
                key = (what, tuple(folders), estimate)
                if key in seen:
                    continue
                seen.add(key)
                worst = _worse(worst, _assess(what, folders[0], estimate, folders[1:], probe=probe, fail_gb=fail_gb,
                                              warn_gb=warn_gb, warn_percent=warn_percent))
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
        return _ps_name(words[0]) in _PS_DOWNLOADS | _PS_UNPACK | _PS_TRANSFER
    name = Path(words[0]).name.casefold().removesuffix(".exe")
    if name == "git":
        return words[1:2] == ["clone"]
    return name in _BULK_TOOLS or name in {"curl", "wget", "unzip", "tar", "bsdtar", "7z", "7za", "7zr"}
