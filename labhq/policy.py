"""Guardrails shared by the permission-prompt MCP tool, the HPC tool and the adapters.

Honest scope note: these rules stop *accidental* leaks and costly mistakes by well-behaved
agents. They are not a sandbox. The real guarantee for controlled-access data is procedural:
raw records are only touched inside HPC jobs whose outputs are aggregates.
"""

from __future__ import annotations

import os
import ntpath
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import unquote, urlsplit

from .settings import BASH_RECURSIVE_DELETE_PATTERN, SCHEDULER_JOB_COMMANDS, PolicySettings

READ_LIKE = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
WRITE_LIKE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Claude Code native delegation starts workers outside labhq's task, approval, event and cost accounting. These names
# come from Claude Code's Agent/Task compatibility pair and team/workflow tools; they are never made safe by adding
# them to policy.approvals.auto_allow_tools. The recruiter pre-approves Agent in its CLI command instead (#421).
NATIVE_DELEGATION_TOOLS = {"Task", "Agent", "Workflow", "TeamCreate", "TeamDelete"}

# Lexical prompts for common PowerShell hazards; this is not a command sandbox.
POWERSHELL_RECURSIVE_DELETE_PATTERN = (
    r"\b(?:Remove-Item|rm|ri|del|erase|rd|rmdir)\b[^;|\n]*\s-"
    r"(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|rf|fr)\b"
)
POWERSHELL_ASK_PATTERNS = (
    # Remove-Item and all its aliases; PowerShell accepts any prefix of -Recurse (-r, -re, …) and unix-style -rf.
    POWERSHELL_RECURSIVE_DELETE_PATTERN,
    r"\b(?:Invoke-Expression|iex)\b[^;\n]*(?:Invoke-WebRequest|iwr|DownloadString|https?://)",
    r"\b(?:Invoke-WebRequest|iwr)\b[^|\n]*\|\s*(?:Invoke-Expression|iex)\b",
    r"\bSet-ExecutionPolicy\b", r"\bStart-Process\b[^;\n]*\s-Verb\s+RunAs\b",
    r"\bFormat-Volume\b", SCHEDULER_JOB_COMMANDS, r"\bsudo\b",
    r"\bgit\s+push\b[^;\n]*--force\b",
)

_SHELL_WORD = re.compile(r'''"[^"]*"|'[^']*'|[^\s|;&<>]+''')
_REDIRECT = re.compile(r'''>{1,2}\s*("[^"]*"|'[^']*'|[^\s|;&<>]+)''')


NULL_DEVICES = frozenset({"/dev/null", "nul", "nul:", "$null", "\\\\.\\nul", "//./nul"})


_REDIRECT_OP = re.compile(r">{1,2}")
_BARE_WORD = re.compile(r"[^\s|;&<>]+")
# A whole redirection with its stream number and target (`2>/dev/null`, `2>&1`, `*> log`, `< in.txt`). Its target is
# judged by the redirect pass; left in a command's words, `cp a b 2>/dev/null` read /dev/null as the copy
# destination (7th mock trial, 2026-10-03).
# A stream number counts only as its own token: in `cp a /work2>/dev/null` the 2 belongs to the path (PR #354 review).
# Bash process substitutions starting with `<(` or `>(` are command arguments, not redirection spans.
_REDIRECTION_SPAN = re.compile(r'''(?![<>]\()(?:(?<![^\s|;&()])(?:\d+|&|\*))?(?:>{1,2}&?|<)\s*(?:"[^"]*"|'[^']*'|[^\s|;&<>]+)''')
_PS_SINGLE = "'\u2018\u2019\u201a\u201b"  # PowerShell also quotes with typographic marks
_PS_DOUBLE = '"\u201c\u201d\u201e'
_PS_SINGLE_AT = re.compile(f"[{_PS_SINGLE}]")
_HERE_STRING_HEADER = re.compile(r"[ \t\r]*\n")
_HERE_STRING_AFTER = " \t\r\n=(,;|{+"  # a PowerShell here-string opens only at a token start

# Quoted text is read as data only when every command on the line is known to treat it so. Any other program
# (a shell, eval, source, sed, at, an alias, a script) may run it as code, and then the raw text is scanned.
_DATA_COMMANDS = frozenset({"cd", "pwd", "ls", "cat", "head", "tail", "wc", "echo", "printf", "grep", "egrep",
                            "fgrep", "mkdir", "touch", "cp", "mv", "true", "false"})  # not test: -v 'a[$(cmd)]' runs
_PS_DATA_COMMANDS = frozenset({
    "write-output", "write", "echo", "write-host", "get-content", "gc", "cat", "type", "set-content", "add-content",
    "out-file", "select-string", "sls", "get-childitem", "gci", "ls", "dir", "get-item", "gi", "test-path",
    "join-path", "split-path", "resolve-path", "get-location", "pwd", "set-location", "cd", "sl", "copy-item", "copy",
    "cp", "cpi", "move-item", "move", "mv", "mi", "get-date", "select-object", "select", "sort-object",
    "measure-object", "format-table", "format-list", "out-string", "out-null", "convertto-json", "convertfrom-json",
    "new-object", "get-filehash", "import-csv", "export-csv"})  # 8th mock trial: Get-FileHash next to a quoted row
# Writers whose first positional argument is the destination (with the PowerShell 5.1 aliases sc, ac, ni, epcsv).
_PS_WRITERS = frozenset({"set-content", "sc", "add-content", "ac", "out-file", "new-item", "ni", "export-csv",
                         "epcsv", "export-clixml", "tee-object"})
_PS_PATH_FLAGS = frozenset({"-path", "-literalpath", "-filepath", "-pspath", "-lp"})
_PS_SWITCHES = frozenset({"-force", "-nonewline", "-append", "-noclobber", "-passthru", "-whatif", "-confirm",
                          "-asbytestream", "-notypeinformation", "-includetypeinformation", "-useculture",
                          "-noenumerate"})
# Parameters whose value is text, never a path, so a quoted row such as "...<GSM>/suppl/..." is not a destination.
_PS_TEXT_FLAGS = frozenset({"-value", "-inputobject", "-encoding", "-width", "-delimiter", "-itemtype", "-stream",
                            "-depth"})
# String and file methods. InvokeScript, Create, Start, Invoke and the like may run their argument.
_PS_DATA_METHODS = frozenset({
    "replace", "split", "join", "trim", "trimstart", "trimend", "substring", "contains", "startswith", "endswith",
    "indexof", "lastindexof", "tolower", "toupper", "tolowerinvariant", "toupperinvariant", "padleft", "padright",
    "insert", "remove", "equals", "format", "concat", "isnullorempty", "isnullorwhitespace", "tostring", "escape",
    "unescape", "match", "matches", "ismatch", "readalltext", "readalllines", "writealltext", "writealllines",
    "appendalltext", "exists", "combine", "getfilename", "getdirectoryname", "getextension", "getfullpath", "new"})
_GIT_DATA_SUBCOMMANDS = frozenset({"add", "commit", "status", "diff", "log", "show", "restore", "switch", "checkout",
                                   "branch", "tag", "stash", "rm", "mv", "rev-parse", "ls-files", "blame"})
_BASH_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "time",
                            "!", "{", "}"})
_BASH_NOT_A_COMMAND = frozenset({"for", "select", "case", "function", "[["})  # the rest of the segment is not one
_PS_KEYWORDS = frozenset({"if", "elseif", "else", "switch", "foreach", "for", "while", "do", "until", "try", "catch",
                          "finally", "return", "throw", "break", "continue", "exit", "param", "begin", "process",
                          "end", "trap"})
# An interpreter is data-safe only for inline code (-c, -e, '-' or stdin) that calls no process API.
_INTERPRETER = re.compile(r"python(?:\d+(?:\.\d+)*)?|pythonw|py|rscript|node", re.I)
_INLINE_CODE_FLAGS = frozenset({"-c", "-e", "-p", "--eval", "--print", "-"})
_PROCESS_CALL = re.compile(r"\b(?:system\w*|popen\w*|subprocess|create_subprocess\w*|spawn\w*|posix_spawn\w*|exec\w*|"
                           r"shell\w*|startfile|createprocess\w*|pipe|eval|getoutput|getstatusoutput|check_output|"
                           r"check_call|child_process|processx|__import__|import_module)\b", re.I)
# These commands can execute an argument, a sourced file, or a wrapped command. They are kept out of the data-safe
# set deliberately. Unknown commands are treated the same way: uncertainty keeps the raw scan rather than hiding it.
_EXECUTES_ARGUMENTS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "cmd", "eval", "source", ".", "exec",
    "xargs", "find", "parallel", "ssh", "su", "sudo", "env", "nohup", "timeout", "nice", "time", "watch",
    "at", "crontab", "trap", "awk", "perl", "iex", "invoke-expression",
})
_BASH_WHOLE_WORD = re.compile(r"\$(?:\{[^{}()\[\]]*\}|\(\([^()]*\)\)|\[[^\[\]]*\])")  # ${..} $((..)) $[..]
_BASH_REDIRECTION = re.compile(r"\d*(?:&>>?|[<>]&|<<<|<<-?|>>?|<>|[<>])\s*[^\s|;&<>()]*")
_BASH_SEPARATOR = re.compile(r"[|;&\n()]|(?<!\S)[{}](?!\S)")
_BASH_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\+?=")
_PS_SEPARATOR = re.compile(r"\|\||&&|[|;\n(){}]")
# After a value only an assignment or foreach's 'in' starts a pipeline; in a command, 'a=b' is text.
_PS_PIPELINE_AFTER_VALUE = re.compile(r"(?<![=!<>])=(?!=)|(?<!\S)in(?!\S)", re.I)
_PS_ENV_ASSIGNMENT = re.compile(r"\$\{?env:[^\s=;|]*\s*[-+*/%?]?=(?!=)", re.I)
# The call operator '&', dot-sourcing '. ' and a [scriptblock] cast: each can run quoted text as code.
_PS_INVOKES_TEXT = re.compile(
    r"(?<!&)&(?!&)|(?:^|(?<=[\s;|({]))\.\s|\[\s*(?:system\.management\.automation\.)?scriptblock\s*\]", re.I)
_PS_PROVIDER = re.compile(r"(?<![\w:])(?:\$\{?)?(?:alias|function):|(?<![\w$:{])(?:env|variable):", re.I)
_PS_MEMBER_CALL = re.compile(r"(?:\.|::)\s*([^\s.:(){}\[\],;|=+\-*/%!<>]*)(?:\[[^\]]*\])?\(")  # also .M[T](


def _braces_closed(text: str) -> bool:
    depth = 0
    for k, ch in enumerate(text):
        if ch == "{" and (depth or text[k - 1:k] == "$"):
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
    return depth == 0


def _heredoc_word(command: str, j: int) -> tuple[str, bool, int] | None:
    """The delimiter after `<<`/`<<-` at j: (text without quotes, quoted?, end index), or None if unreadable."""
    while j < len(command) and command[j] in " \t":
        j += 1
    start, word, quoted = j, "", False
    while j < len(command) and command[j] not in " \t\r\n|&;()<>":
        ch = command[j]
        if ch == "'":
            end = command.find("'", j + 1)
            if end < 0:
                return None
            word, quoted, j = word + command[j + 1:end], True, end + 1
        elif ch == '"':
            end = command.find('"', j + 1)
            if end < 0 or "\\" in command[j:end]:
                return None
            word, quoted, j = word + command[j + 1:end], True, end + 1
        elif ch == "\\":
            word, quoted, j = word + command[j + 1:j + 2], True, j + 2
        else:
            word, j = word + ch, j + 1
    # Bash drops the '$' of $'..' and $"..", and an extglob '@(' continues the word: read neither here.
    if not word or "$" in command[start:j] or "`" in command[start:j] or command.startswith("(", j):
        return None
    return word, quoted, j


def _blank_non_syntax(command: str, powershell: bool) -> str | None:
    """`command` with strings, comments and here-document bodies blanked at the same length.

    Strings and escaped characters become '_' (a string stays one word); comments and here-document bodies
    become spaces. Returns None, and the caller scans the raw text as before, wherever the shell might read
    the text differently: an unterminated quote or here-document, a substitution that can nest quotes, a '#'
    that may not start a comment, a '<<' that may not start a here-document, or a command that may run the
    quoted text as code (see _quoted_text_is_data).
    """
    single, double = (_PS_SINGLE, _PS_DOUBLE) if powershell else ("'", '"')
    escape = "`" if powershell else "\\"
    out, n = list(command), len(command)
    heredocs: list[tuple[str, bool, bool]] = []
    # bash: open brackets as (closer, inside ${..}, ((..)), $[..] or a subscript/[[..]]). '<<' inside any of them
    # and '#' or a quote inside the second kind are not read here.
    stack: list[tuple[str, bool]] = []
    prev = "\n"  # the last character read as syntax; the start of the command counts as a line start
    i = 0

    def blank(start: int, end: int, ch: str) -> None:
        out[start:end] = ch * (len(out[start:end]))

    while i < n:
        c = command[i]
        if c == escape:
            if command.startswith("\n", i + 1):  # a line continuation: the shell joins the two lines
                blank(i, i + 2, " ")
            else:
                blank(i, i + 2, "_")
                prev = "_"
            i += 2
            continue
        if powershell:
            if command.startswith(("<#", "--%"), i):  # block comment, or stop-parsing: not read here
                return None
            if command.startswith("${", i):  # a braced variable name is literal up to '}'
                end = command.find("}", i)
                if end < 0 or escape in command[i:end]:
                    return None
                i, prev = end + 1, "}"
                continue
            header = _HERE_STRING_HEADER.match(command, i + 2) if c == "@" and command[i + 1:i + 2] else None
            if header and command[i + 1] in single + double:
                if i and command[i - 1] not in _HERE_STRING_AFTER:
                    return None  # 'x@' + quote is part of a generic token, not a here-string
                closers = single if command[i + 1] in single else double
                k = header.end()  # the closing quote and '@' must start a line
                while not (k < n and command[k] in closers and command.startswith("@", k + 1)):
                    k = command.find("\n", k) + 1
                    if k == 0:
                        return None
                if closers == double and "$(" in command[header.end():k]:
                    return None
                blank(i, k + 2, "_")
                i, prev = k + 2, "_"
                continue
        else:
            if c == "`":
                return None
            if c == "\n" and heredocs:
                if stack:
                    return None
                i += 1
                for delimiter, strip_tabs, quoted in heredocs:
                    start = i
                    while True:
                        end = command.find("\n", i)
                        end = n if end < 0 else end
                        line = command[i:end].lstrip("\t") if strip_tabs else command[i:end]
                        if line == delimiter:
                            break
                        if end == n:
                            return None
                        i = end + 1
                    if not quoted and ("$(" in command[start:i] or "`" in command[start:i]):
                        return None
                    blank(start, end, " ")
                    i = end + 1
                heredocs.clear()
                prev = "\n"
                continue
            if command.startswith("<<", i):
                if command.startswith("<<<", i):
                    i, prev = i + 3, "<"
                    continue
                # Inside brackets '<<' may be a shift ($[1<<2], a[1<<2]=) or an extglob character; Git Bash ends
                # a here-document at 'EOF\r' and Linux bash does not.
                if stack or "\r" in command:
                    return None
                strip_tabs = command.startswith("<<-", i)
                word = _heredoc_word(command, i + 2 + strip_tabs)
                if word is None:
                    return None
                heredocs.append((word[0], strip_tabs, word[1]))
                blank(i + 2, word[2], "_")
                i, prev = word[2], "_"
                continue
            if command.startswith(("${", "(("), i):
                stack += [("}" if c == "$" else ")", True)] * (1 if c == "$" else 2)
                i, prev = i + 2, command[i + 1]
                continue
            unit = any(inner for _, inner in stack)
            if c in "'\"" and unit:
                return None  # $(( '$(cmd)' )) and [[ 'a[$(cmd)]' -eq 0 ]] run cmd: quotes there do not quote
            if c in "([{":
                test_word = c == "[" and prev in " \t\n;|&(!" and command[i + 1:i + 2] in (" ", "\t")  # '[ -f x ]'
                stack.append((")]}"["([{".index(c)], unit or (c == "[" and not test_word)))
            elif c in ")]}" and stack:  # with nothing open, ')' ends a case pattern
                if stack.pop()[0] != c:
                    return None
        if c == "#":
            if prev in (" \t\r\n;|" if powershell else " \t\n;|&"):  # bash: a carriage return is a word character
                if any(unit for _, unit in stack):
                    return None
                end = command.find("\n", i)
                end = n if end < 0 else end
                blank(i, end, " ")
                i = end
                continue
            if powershell or prev in "()<>":
                return None  # PowerShell ends a token before '#' after '$x', '{', ',' ...; bash after ')' is unsure
        if c in single:
            if powershell:
                j = i
                while True:
                    found = _PS_SINGLE_AT.search(command, j + 1)
                    if not found:
                        return None
                    j = found.start()
                    if not (j + 1 < n and command[j + 1] in single):
                        break
                    j += 1  # a doubled quote is a literal quote
            else:
                j = command.find("'", i + 1)
                if j < 0 or (prev == "$" and "\\" in command[i:j]):  # $'...' takes backslash escapes
                    return None
            blank(i, j + 1, "_")
            i, prev = j + 1, "_"
            continue
        if c in double:
            j = i + 1
            while j < n:
                if command[j] == escape:
                    j += 2
                elif command[j] not in double:
                    j += 1
                elif powershell and j + 1 < n and command[j + 1] in double:
                    j += 2  # a doubled quote is a literal quote
                else:
                    break
            if j >= n:
                return None
            body = command[i + 1:j]
            if "$(" in body or (not powershell and "`" in body) or not _braces_closed(body):
                return None
            blank(i, j + 1, "_")
            i, prev = j + 1, "_"
            continue
        prev = c
        i += 1
    skeleton = "".join(out)
    if skeleton != command and not _quoted_text_is_data(command, skeleton, powershell):
        return None
    return skeleton


def _spans(text: str, separator: re.Pattern) -> Iterator[tuple[int, int]]:
    start = 0
    for match in separator.finditer(text):
        yield start, match.start()
        start = match.end()
    yield start, len(text)


def _word_value(word: str) -> str:
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def _shell_word_prefix(word: str, powershell: bool) -> tuple[str, bool]:
    """The literal text a shell word starts with, and whether that text is the whole word.

    The prefix stops where expansion (a variable, substitution or glob) or an unterminated quote begins.
    """
    value: list[str] = []
    i = 0
    while i < len(word):
        quote = word[i]
        if quote == "'":
            i += 1
            while True:
                end = word.find("'", i)
                if end < 0:
                    return "".join(value), False
                value.append(word[i:end])
                if powershell and end + 1 < len(word) and word[end + 1] == "'":
                    value.append("'")
                    i = end + 2
                    continue
                i = end + 1
                break
            continue
        if quote == '"':
            i += 1
            while i < len(word):
                ch = word[i]
                if ch == '"':
                    if powershell and i + 1 < len(word) and word[i + 1] == '"':
                        value.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                if ch in "$`":
                    return "".join(value), False
                if not powershell and ch == "\\" and i + 1 < len(word) and word[i + 1] in '$`"\\':
                    value.append(word[i + 1])
                    i += 2
                    continue
                value.append(ch)
                i += 1
            else:
                return "".join(value), False
            continue
        if quote in "$`*?[]{}":
            return "".join(value), False
        if not powershell and quote == "\\":
            if i + 1 >= len(word):
                return "".join(value), False
            value.append(word[i + 1])
            i += 2
            continue
        value.append(quote)
        i += 1
    return "".join(value), True


def _literal_shell_word(word: str, powershell: bool) -> str | None:
    """Return a shell word's literal value, or None when expansion can change it."""
    value, complete = _shell_word_prefix(word, powershell)
    return value if complete else None


def _sed_delimited_end(script: str, start: int, delimiter: str) -> int | None:
    i = start
    while i < len(script):
        if script[i] == "\\":
            i += 2
        elif script[i] == delimiter:
            return i + 1
        else:
            i += 1
    return None


def _sed_skip_address(script: str, start: int) -> int:
    """Skip the common sed address forms; return start when no address begins there."""
    i = start
    while i < len(script) and script[i] in " \t":
        i += 1

    def one(pos: int) -> int | None:
        if pos >= len(script):
            return None
        if script[pos].isdigit():
            while pos < len(script) and script[pos].isdigit():
                pos += 1
            return pos
        if script[pos] == "$":
            return pos + 1
        if script[pos] == "/":
            return _sed_delimited_end(script, pos + 1, "/")
        if script[pos] == "\\" and pos + 1 < len(script):
            return _sed_delimited_end(script, pos + 2, script[pos + 1])
        if script[pos] in "+~" and pos + 1 < len(script) and script[pos + 1].isdigit():
            pos += 2
            while pos < len(script) and script[pos].isdigit():
                pos += 1
            return pos
        return None

    end = one(i)
    if end is None:
        return start
    i = end
    while i < len(script) and script[i] in " \t":
        i += 1
    if i < len(script) and script[i] in ",~":
        i += 1
        while i < len(script) and script[i] in " \t":
            i += 1
        end = one(i)
        if end is None:
            return start
        i = end
    while i < len(script) and script[i] in " \t":
        i += 1
    if i < len(script) and script[i] == "!":
        i += 1
        while i < len(script) and script[i] in " \t":
            i += 1
    return i


def _sed_script_writes(script: str) -> list[str] | None:
    """Return literal sed write targets, or None when the script executes code or cannot be proved safe."""
    targets: list[str] = []
    i = 0
    simple = frozenset("acdDgGhHilLnNpPqQrRbBtTxvVyYzZF=:")
    while i < len(script):
        while i < len(script) and script[i] in " \t\r\n;{}":
            i += 1
        if i >= len(script):
            break
        if script[i] == "#":
            end = script.find("\n", i)
            i = len(script) if end < 0 else end + 1
            continue
        addressed = _sed_skip_address(script, i)
        i = addressed if addressed != i else i
        if i >= len(script):
            return None
        command = script[i]
        i += 1
        if command == "e":
            return None
        if command in "wW":
            end = len(script)
            for separator in (";", "\n"):
                found = script.find(separator, i)
                if found >= 0:
                    end = min(end, found)
            target = script[i:end].strip()
            if not target:
                return None
            targets.append(target)
            i = end + (end < len(script))
            continue
        if command == "s":
            if i >= len(script) or script[i] in "\\\r\n":
                return None
            delimiter = script[i]
            regex_end = _sed_delimited_end(script, i + 1, delimiter)
            replacement_end = (_sed_delimited_end(script, regex_end, delimiter)
                               if regex_end is not None else None)
            if replacement_end is None:
                return None
            end = replacement_end
            while end < len(script) and script[end] not in ";\n":
                end += 1
            flags = script[replacement_end:end]
            k = 0
            while k < len(flags):
                if flags[k].isspace() or flags[k].isdigit() or flags[k] in "gIpMm":
                    k += 1
                elif flags[k] == "e":
                    return None
                elif flags[k] == "w":
                    target = flags[k + 1:].strip()
                    if not target:
                        return None
                    targets.append(target)
                    k = len(flags)
                else:
                    return None
            i = end + (end < len(script))
            continue
        if command not in simple:
            return None
        end = i
        while end < len(script) and script[end] not in ";\n":
            end += 1
        i = end + (end < len(script))
    return targets


@dataclass(frozen=True)
class _SedCommand:
    writes: tuple[str, ...]
    in_place_inputs: tuple[str, ...]
    text_is_data: bool


def _sed_command(args: list[str], powershell: bool) -> _SedCommand | None:
    """Parse options first, then distinguish scripts from input files independent of option order."""
    scripts: list[str | None] = []
    positionals: list[str | None] = []
    in_place = False
    script_option = False
    text_is_data = True
    options = True
    i = 0
    while i < len(args):
        raw = args[i]
        value = _literal_shell_word(raw, powershell)
        if value is None:
            if options and raw.startswith("-"):
                return None
            positionals.append(None)
            text_is_data = False
            i += 1
            continue
        if options and value == "--":
            options = False
            i += 1
            continue
        if options and value.startswith("--"):
            flag, equal, attached = value.partition("=")
            if flag in {"--quiet", "--silent", "--debug", "--regexp-extended", "--separate", "--unbuffered",
                        "--null-data", "--sandbox", "--posix", "--follow-symlinks", "--help", "--version"}:
                if equal:
                    return None
                i += 1
                continue
            if flag == "--in-place":
                in_place = True
                i += 1
                continue
            if flag == "--line-length":
                if not equal:
                    i += 1
                    if i >= len(args):
                        return None
                    attached = _literal_shell_word(args[i], powershell)
                if attached is None or not attached.isdigit():
                    return None
                i += 1
                continue
            if flag == "--expression":
                script_option = True
                if not equal:
                    i += 1
                    if i >= len(args):
                        return None
                    attached = _literal_shell_word(args[i], powershell)
                if attached is None:
                    text_is_data = False
                scripts.append(attached)
                i += 1
                continue
            if flag == "--file":
                script_option = True
                text_is_data = False
                if not equal:
                    i += 1
                    if i >= len(args):
                        return None
                i += 1
                continue
            return None
        if options and value.startswith("-") and value != "-":
            j = 1
            while j < len(value):
                flag = value[j]
                if flag in "nErsuz":
                    j += 1
                elif flag == "i":
                    in_place = True
                    j = len(value)  # the rest is the backup suffix
                elif flag == "e":
                    script_option = True
                    attached = value[j + 1:]
                    if not attached:
                        i += 1
                        if i >= len(args):
                            return None
                        attached = _literal_shell_word(args[i], powershell)
                    if attached is None:
                        text_is_data = False
                    scripts.append(attached)
                    j = len(value)
                elif flag == "l":
                    attached = value[j + 1:]
                    if not attached:
                        i += 1
                        if i >= len(args):
                            return None
                        attached = _literal_shell_word(args[i], powershell)
                    if attached is None or not attached.isdigit():
                        return None
                    j = len(value)
                elif flag == "f":
                    script_option = True
                    text_is_data = False
                    if j + 1 == len(value):
                        i += 1
                        if i >= len(args):
                            return None
                    j = len(value)
                else:
                    return None
            i += 1
            continue
        positionals.append(value)
        i += 1
    inputs = positionals
    if not script_option:
        if not positionals:
            return _SedCommand((), (), False)
        scripts.append(positionals[0])
        inputs = positionals[1:]
    if not scripts:
        text_is_data = False
    writes: list[str] = []
    for script in scripts:
        found = _sed_script_writes(script) if script is not None else None
        if found is None:
            text_is_data = False
        else:
            writes.extend(found)
    in_place_inputs = tuple(value for value in inputs if in_place and value not in {None, "-"})
    return _SedCommand(tuple(writes), in_place_inputs, text_is_data)


_CURL_LONG_SWITCHES = frozenset({
    "--compressed", "--fail", "--fail-with-body", "--globoff", "--head", "--include", "--insecure",
    "--location", "--no-progress-meter", "--remote-header-name", "--remote-name", "--show-error", "--silent",
})
_CURL_LONG_VALUES = frozenset({
    "--cacert", "--cert", "--connect-timeout", "--cookie", "--cookie-jar", "--data", "--data-binary",
    "--data-raw", "--form", "--header", "--key", "--max-time", "--output", "--output-dir", "--proxy",
    "--range", "--referer", "--request", "--resolve", "--retry", "--upload-file", "--url", "--user",
    "--user-agent", "--write-out",
})
_WGET_LONG_SWITCHES = frozenset({
    "--content-disposition", "--continue", "--no-check-certificate", "--quiet", "--server-response", "--spider",
    "--timestamping", "--trust-server-names",
})
_WGET_LONG_VALUES = frozenset({
    "--accept", "--directory-prefix", "--header", "--input-file", "--limit-rate", "--output-document",
    "--output-file", "--post-data", "--post-file", "--reject", "--timeout", "--tries", "--user-agent", "--wait",
})
# Options whose value names a file or folder the download command writes: the outside-roots check reads every one.
_CURL_OUTPUT_LONGS = frozenset({
    "--output", "--output-dir", "--dump-header", "--cookie-jar", "--trace", "--trace-ascii", "--stderr",
    "--libcurl", "--etag-save", "--hsts", "--alt-svc",
})
_WGET_OUTPUT_LONGS = frozenset({
    "--output-document", "--directory-prefix", "--output-file", "--append-output", "--save-cookies",
})


@dataclass(frozen=True)
class _DownloadOptions:
    short_switches: frozenset[str]
    short_values: frozenset[str]
    output_shorts: frozenset[str]
    long_switches: frozenset[str]
    long_values: frozenset[str]
    output_longs: frozenset[str]


_DOWNLOAD_OPTIONS = {
    "curl": _DownloadOptions(frozenset("sSLfIOkqgNi"), frozenset("oAbcdDeEFHmrTuUwxXyYz"), frozenset("ocD"),
                             _CURL_LONG_SWITCHES, _CURL_LONG_VALUES, _CURL_OUTPUT_LONGS),
    "wget": _DownloadOptions(frozenset("qcNS"), frozenset("OPoaiUTtwQAR"), frozenset("OPoa"),
                             _WGET_LONG_SWITCHES, _WGET_LONG_VALUES, _WGET_OUTPUT_LONGS),
}


def _download_options_known(name: str, args: list[str], powershell: bool) -> bool:
    """Prove every option form understood; operands with a literal non-option prefix may still contain expansion."""
    values: list[str | None] = [_literal_shell_word(arg, powershell) for arg in args]
    spec = _DOWNLOAD_OPTIONS[name]
    long_switches, long_values = spec.long_switches, spec.long_values
    short_switches, short_values = spec.short_switches, spec.short_values
    output_longs, output_shorts = spec.output_longs, spec.output_shorts
    i = 0
    options = True
    while i < len(args):
        raw, value = args[i], values[i]
        if options and value == "--":
            options = False
            i += 1
            continue
        could_be_option = raw.startswith("-") or value is not None and value.startswith("-")
        if options and value is None:
            if could_be_option or not raw or raw[0] in "'$`":
                return False
            i += 1
            continue
        if not options or value is None or not value.startswith("-") or value == "-":
            i += 1
            continue
        if value.startswith("--"):
            flag, equal, _attached = value.partition("=")
            if flag == "--config":
                return False
            if flag in long_switches:
                if equal:
                    return False
                i += 1
                continue
            if flag in long_values:
                if not equal:
                    i += 1
                    if i >= len(args):
                        return False
                    if flag in output_longs and values[i] is None:
                        return False
                i += 1
                continue
            return False
        j = 1
        while j < len(value):
            flag = value[j]
            if name == "curl" and flag == "K":
                return False
            if flag in short_switches:
                j += 1
                continue
            if flag in short_values:
                if j + 1 == len(value):
                    i += 1
                    if i >= len(args):
                        return False
                    if flag in output_shorts and values[i] is None:
                        return False
                j = len(value)
                continue
            return False
        i += 1
    return True


def _data_command(name: str, args: list[str], powershell: bool) -> str | None:
    """'data' if `name` reads its quoted arguments as text, 'inline' for an interpreter's inline code, else None."""
    name = _word_value(name)
    values = [_word_value(arg) for arg in args]
    base = re.split(r"[\\/]", name)[-1].casefold()
    base = base[:-4] if base.endswith(".exe") else base
    if base in _EXECUTES_ARGUMENTS:
        return None
    if _INTERPRETER.fullmatch(base):
        for arg in values:
            if arg in _INLINE_CODE_FLAGS:
                break
            if arg == "-m" or not arg.startswith("-"):
                return None  # a module or script file may do anything with its arguments
        return "inline"
    if name.casefold() not in (base, base + ".exe"):
        return None  # './cat' or 'C:\x\echo.exe' may be any program
    if base == "git":
        return "data" if values and values[0] in _GIT_DATA_SUBCOMMANDS else None  # not 'git -c alias.x=!..'
    if base == "printf" and not powershell and any(arg.startswith("-v") for arg in values):
        return None  # 'printf -v' assigns a variable
    if base == "sed":
        parsed = _sed_command(args, powershell)
        return "data" if parsed is not None and parsed.text_is_data else None
    if powershell and base == 'new-item':
        # Only the literal directory form seen in the trial. Do not trust provider/value creation.
        return ('data' if len(values) == 4 and [v.casefold() for v in values[:3]] ==
                ['-itemtype', 'directory', '-force'] and not values[3].startswith('-') else None)
    if base in {"curl", "wget"}:
        return "data" if _download_options_known(base, args, powershell) else None
    return "data" if base in (_PS_DATA_COMMANDS if powershell else _DATA_COMMANDS) else None


def _quoted_text_is_data(command: str, skeleton: str, powershell: bool) -> bool:
    """Whether every command in `command` reads its strings, comments and here-documents only as text.

    The commands come from `skeleton`, the command with that text blanked. Anything not known to be safe
    makes the answer False: an unknown program, a quoted or variable command name, an environment
    assignment, a PowerShell call operator or member call outside a short list of string methods, or inline
    interpreter code that calls a process API.
    """
    def blanked(start: int, end: int) -> bool:
        return skeleton[start:end] != command[start:end]

    inline = False
    if powershell:
        if _PS_PROVIDER.search(command) or _PS_ENV_ASSIGNMENT.search(skeleton):
            return False  # 'Set-Content env:X', '$env:X = ..', 'alias:ls' change what later commands run
        if _PS_INVOKES_TEXT.search(skeleton):
            # '& (..)', '. (..)' and a [scriptblock] cast run a string as code wherever they stand, inside
            # parentheses too, where segment splitting would lose them (PR #340 review).
            return False
        for call in _PS_MEMBER_CALL.finditer(skeleton):
            if blanked(*call.span(1)) or call.group(1).casefold() not in _PS_DATA_METHODS:
                return False
        text, separator = skeleton, _PS_SEPARATOR
    else:
        text, before = skeleton, None
        while text != before:
            before, text = text, _BASH_WHOLE_WORD.sub(lambda m: "$" * len(m.group()), text)
        text, separator = _BASH_REDIRECTION.sub(lambda m: " " * len(m.group()), text), _BASH_SEPARATOR
    pending = list(_spans(text, separator))
    while pending:
        start, end = pending.pop()
        words = list(_BARE_WORD.finditer(text, start, end))
        keywords, fold = (_PS_KEYWORDS, str.casefold) if powershell else (_BASH_KEYWORDS, str)
        k = 0
        while k < len(words) and fold(words[k].group()) in keywords and not blanked(*words[k].span()):
            k += 1
        if k == len(words):
            continue
        word = words[k]
        if powershell:
            if text[words[k - 1].end() if k else start:word.start()].strip():
                return False  # the call operator '&', or a redirection before the command
            first = command[word.start()]
            if first == "`":
                return False  # '`iex' is the command iex
            member = first == "." and text[word.start() - 1:word.start()] in (")", "]", "}")  # (..).Replace
            if (blanked(word.start(), word.start() + 1) or first in "$[@0123456789" or member
                    or (first in "-+!," and not k)):
                # A value, never a command; its member calls were checked above. '$a, [int]$b = iex x' and
                # 'foreach ($x in iex x)' start a pipeline after it.
                after = _PS_PIPELINE_AFTER_VALUE.search(text, word.start(), end)
                if after:
                    pending.append((after.end(), end))
                continue
        elif word.group() in _BASH_NOT_A_COMMAND:
            continue
        elif _BASH_ASSIGNMENT.match(word.group()):
            return False  # 'GIT_EDITOR=..' reaches later commands even without a prefix
        if blanked(*word.span()) or "$" in word.group():
            return False
        kind = _data_command(command[word.start():word.end()],
                             [command[w.start():w.end()] for w in words[k + 1:]], powershell)
        if kind is None:
            return False
        inline = inline or kind == "inline"
    return not (inline and _PROCESS_CALL.search(command))


def _ps_writer_targets(args: list[str]) -> Iterator[str]:
    """A PowerShell writer's destination: a flag's value, else its first positional argument.

    `Set-Content -Encoding utf8 C:/x/out.txt` names its path after a flag (8th mock trial review). PowerShell takes
    any prefix of a parameter name (-Pa, -Enc), so the next word after any flag that is not a switch or a known text
    parameter also counts as a destination: unsure words are reported, never skipped.
    """
    i, named = 0, False
    while i < len(args):
        word = args[i]
        if not (word.startswith("-") and len(word) > 1):
            if not named:  # with the path named, the first positional argument binds to -Value (PR #356 review)
                yield word
            return
        flag, colon, value = word.casefold().partition(":")
        named = named or flag in _PS_PATH_FLAGS
        if flag in _PS_SWITCHES:
            i += 1
        elif colon:  # -Path:C:/x
            if flag not in _PS_TEXT_FLAGS:
                yield word[len(flag) + 1:]
            i += 1
        else:
            if flag not in _PS_TEXT_FLAGS and i + 1 < len(args):
                yield args[i + 1]
            i += 2


def _download_output_long(flag: str, spec: _DownloadOptions) -> bool:
    """Whether a long option names an output file, abbreviations included (getopt takes unique prefixes)."""
    if flag in spec.output_longs:
        return True
    if flag in spec.long_switches or flag in spec.long_values or len(flag) < 3:
        return False
    return any(name.startswith(flag) for name in spec.output_longs)


def _download_write_targets(name: str, args: list[str], powershell: bool) -> Iterator[str]:
    """Every path a curl/wget call writes, as the literal text it starts with.

    An operand with expansion (`-o "C:/x/$f"`, `-O /abs/${s}.bat`) keeps its literal prefix, so an absolute
    destination still meets the outside-roots check, as cp and tee do. Short options cluster (`-fsSLo /x`,
    `-qO /x`) and take attached values; a cluster with an unknown letter still reports a later output letter.
    """
    spec = _DOWNLOAD_OPTIONS[name]
    words = [_shell_word_prefix(arg, powershell) for arg in args]
    i = 0
    while i < len(words):
        text, complete = words[i]
        following = words[i + 1][0] if i + 1 < len(words) else None
        if not text.startswith("-") or text == "-":
            i += 1
            continue
        if text.startswith("--"):
            flag, equal, attached = text.partition("=")
            if name == "curl" and flag == "--remote-name":
                yield "."
            elif _download_output_long(flag, spec):
                if equal:
                    yield attached
                elif complete and following is not None:
                    yield following
                    i += 1
            elif flag in spec.long_values and not equal and complete:
                i += 1  # the option's own value, not a path it writes
            i += 1
            continue
        j, takes_next = 1, False
        while j < len(text):
            letter, rest = text[j], text[j + 1:]
            if name == "curl" and letter == "O":
                yield "."
            if letter in spec.short_switches:
                j += 1
                continue
            if letter in spec.short_values:
                if letter in spec.output_shorts and rest:
                    yield rest
                takes_next = not rest and complete
                if takes_next and letter in spec.output_shorts and following is not None:
                    yield following
                break
            # An unknown letter may take the rest as its value; an output letter after it may still be one.
            later = [k for k in range(j + 1, len(text)) if text[k] in spec.output_shorts]
            if later:
                attached = text[later[0] + 1:]
                if attached:
                    yield attached
                elif following is not None:
                    yield following
            break
        else:
            if not complete and following is not None:
                yield following  # the cluster continues in an expansion that may end in an output letter
        i += 2 if takes_next else 1


# Windows PowerShell's `curl` and `wget` are aliases of Invoke-WebRequest, so they are read both ways.
_PS_DOWNLOADERS = frozenset({"invoke-webrequest", "iwr", "invoke-restmethod", "irm", "curl", "wget"})


def _ps_download_targets(args: list[str]) -> Iterator[str]:
    """Invoke-WebRequest/RestMethod `-OutFile` values, any unique-or-not prefix of the name, as literal prefixes."""
    for i, raw in enumerate(args):
        text, _complete = _shell_word_prefix(raw, True)
        flag, colon, attached = text.partition(":")
        if len(flag) < 2 or not "-outfile".startswith(flag.casefold()):
            continue
        if colon:
            yield attached
        elif i + 1 < len(args):
            yield _shell_word_prefix(args[i + 1], True)[0]


def _named_write_targets(raw_words: list[str], powershell: bool = False) -> Iterator[str]:
    if not raw_words:
        return
    words = [_word_value(word) for word in raw_words]
    name = words[0].casefold()
    plain_name = name[:-4] if name.endswith(".exe") else name
    if name not in {plain_name, plain_name + ".exe"}:
        return
    if plain_name == "sed":
        parsed = _sed_command(raw_words[1:], powershell)
        if parsed is not None:
            yield from parsed.writes
            yield from parsed.in_place_inputs
        return
    if powershell and name in _PS_DOWNLOADERS:
        yield from _ps_download_targets(raw_words[1:])
    if plain_name in {"curl", "wget"}:
        yield from _download_write_targets(plain_name, raw_words[1:], powershell)
        return
    if name in _PS_DOWNLOADERS:
        return
    if name == "tee":  # bash tee writes every file argument; in PowerShell tee is Tee-Object
        yield from (word for word in words[1:] if not word.startswith("-"))
        return
    if name in _PS_WRITERS:
        yield from _ps_writer_targets(words[1:])
        return
    if name not in {"copy-item", "move-item", "cp", "mv"}:
        return
    for i, word in enumerate(words[:-1]):
        if word.casefold() == "-destination":
            yield words[i + 1]
    yield words[-1]


def _shell_write_targets(command: str, powershell: bool = False) -> Iterator[str]:
    """Find obvious literal write destinations; expansions and aliases are not parsed.

    Only shell syntax is read: a '>' or a command name inside a string, comment or here-document is text.
    When that split is unsure the raw text is scanned, which over-reports but never hides a write.
    Null devices (`2>/dev/null`, `> $null`, `> NUL`) discard output and are not writes.
    """
    text = _blank_non_syntax(command, powershell)
    # Positions come from the blanked text, values from the command. Every '>' is tried, so a quoted target
    # never swallows a later redirect, as `> "$(cmd > /x)"` did when the raw text was matched in one pass.
    for op in _REDIRECT_OP.finditer(command if text is None else text):
        match = _REDIRECT.match(command, op.start())
        target = match.group(1).strip("\"'") if match else ""
        if target and target.casefold() not in NULL_DEVICES:
            yield target
    def unredirected(value: str) -> str:  # same length, so positions in the blanked text still hold
        return _REDIRECTION_SPAN.sub(lambda m: " " * len(m.group()), value)

    if text is None:
        segments = [[m.group() for m in _SHELL_WORD.finditer(segment)]
                    for segment in re.split(r"[|;&\n]", unredirected(command))]
    else:
        words_text = unredirected(text)
        segments = [[command[m.start():m.end()]
                      for m in _BARE_WORD.finditer(words_text, seg.start(), seg.end())]
                    for seg in re.finditer(r"[^|;&\n]+", words_text)]
    for words in segments:
        # A command's own output argument may be a null device too (`curl -o /dev/null`, `tee NUL`; trial 2026-10-08).
        yield from (target for target in _named_write_targets(words, powershell)
                    if target.strip("\"'").casefold() not in NULL_DEVICES)


def _plain_delete_path(target: str, powershell: bool, *, relative: bool) -> bool:
    """Whether a delete path is one literal filesystem path with a known base."""
    if not target or target.startswith("~") or _drive_relative(target):
        return False
    if target.startswith(("\\\\", "//")) or (relative and _absolute(target)):
        return False
    if any(char in target for char in "*?[{"):
        return False
    if any(part == ".." for part in target.replace("\\", "/").split("/")):
        return False
    if "$" in target or "`" in target or re.search(r"%[^%]+%|![^!]+!", target):
        return False
    if powershell:
        # PowerShell providers/PSDrives and String[]/array syntax can name non-filesystem or extra targets.
        drive_absolute = bool(re.match(r"^[A-Za-z]:[/\\]", target))
        if (":" in target and not drive_absolute) or any(char in target for char in ",()") or target.startswith("@"):
            return False
    return True


def _recursive_delete_inside_workdir(command: str, powershell: bool, workdir: str | None) -> bool:
    """Allow one simple recursive delete of literal targets below the step's disposable `.tmp`."""
    if not workdir:
        return False
    if any(char in command for char in ";|&()<>`\r\n"):
        return False
    text = _blank_non_syntax(command, powershell)
    if text is None:
        return False
    raw_words = [command[word.start():word.end()] for word in _BARE_WORD.finditer(text)]
    literals = [_literal_shell_word(word, powershell) for word in raw_words]
    if not literals or any(value is None for value in literals):
        return False
    values = [str(value) for value in literals]
    if values[0].casefold() != ("remove-item" if powershell else "rm"):
        return False

    targets: list[str] = []
    recursive = False
    after_options = False
    for value in values[1:]:
        folded = value.casefold()
        if not powershell and value == "--" and not after_options:
            after_options = True
        elif not after_options and value.startswith("-"):
            if powershell:
                if folded not in {"-recurse", "-force"}:
                    return False
                recursive = recursive or folded == "-recurse"
            elif folded in {"--recursive", "--force", "--verbose"}:
                recursive = recursive or folded == "--recursive"
            else:
                flags = folded[1:]
                if not flags or not set(flags) <= {"r", "f", "v"}:
                    return False
                recursive = recursive or "r" in flags
        else:
            if value.startswith("-"):
                return False
            targets.append(value)
    if not recursive or not targets:
        return False
    return _targets_below_step_tmp(targets, powershell, workdir)


def _targets_below_step_tmp(targets: Iterable[str], powershell: bool, workdir: str) -> bool:
    """Whether every delete target is a literal relative path at or below the step's `.tmp`, through no link."""
    from .adapters.owned import is_link

    root = _norm(workdir, expand_vars=False)
    tmp_root = _norm(posixpath.join(root, ".tmp"), expand_vars=False)
    physical_root = Path(workdir)
    for target in targets:
        if not _plain_delete_path(target, powershell, relative=True):
            return False
        relative = posixpath.normpath(target.replace("\\", "/"))
        resolved = _norm(posixpath.join(root, relative), expand_vars=False)
        if resolved != tmp_root and not _inside(resolved, tmp_root):
            return False
        parts = [part for part in relative.split("/") if part not in {"", "."}]
        # The target itself counts too: `rm -rf .tmp/link/` follows a directory link (#501 review).
        for index in range(1, len(parts) + 1):
            component = physical_root.joinpath(*parts[:index])
            try:
                if os.path.lexists(component) and is_link(component):
                    return False
            except OSError:
                return False
    return True


# A bash command word, wherever it stands: not `--rm`, `x-rm` or `perm`, but `/bin/rm`, `\rm`, `'rm'`, `$(rm`.
# Any case: Git Bash finds `RM.exe` on a case-insensitive drive.
_BASH_RM_WORD = re.compile(r"""(?<![\w.-])rm(?:\.exe)?(?=[\s'"]|$)""", re.IGNORECASE)
_BASH_FIND_WORD = re.compile(r"""(?<![\w.-])find(?:\.exe)?(?=[\s'"]|$)""", re.IGNORECASE)
_BASH_DELETE_WORD = re.compile(r"(?<![\w.-])(?:rm|unlink)(?:\.exe)?(?![\w.-])", re.IGNORECASE)
# Where one simple command ends; find's own `\;` and `';'` do not end it. Parentheses do not either, so a word like
# `$(pwd)` stays one word and the words after it are still read.
_BASH_COMMAND_END = re.compile(r"""(?<![\\'"]);(?!['"])|[|&\n]""")
_FIND_TEXT = re.compile(r"\\[;()]|'[;()]'|\"[;()]\"")  # find's `\;`, `\(`, `';'` ... are its own words
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
# cmd's `rd /s` and `del /s` (also `//s` from Git Bash) are recursive deletes the step folder rule never allows.
_CMD_RECURSIVE_DELETE = re.compile(r"""(?<![\w.-])(?:rd|rmdir|del|erase)(?:\.exe)?(?=[\s'"])[^|;&\n]*?\s/{1,2}s\b""",
                                   re.IGNORECASE)
# A PowerShell delete after the first pipe, in a script block too (`| ForEach-Object { Remove-Item $_ }`), deletes what
# the listing before the pipe found (#525 review).
_PS_DELETE_CMDLETS = frozenset({"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"})
# .NET deletes: `$_.Delete()`, `[IO.Directory]::Delete(p, $true)`, `[IO.File]::Delete(p)`, `...DeleteDirectory(p)`.
_PS_METHOD_DELETE = re.compile(r"(?:\.|::)\s*Delete\w*\s*\(", re.IGNORECASE)
_PS_DELETE_WORD = re.compile(r"(?<![\w-])(?:Remove-Item|ri|rm|del|erase|rd|rmdir)(?![\w-])|(?:\.|::)\s*Delete\w*\s*\(",
                             re.IGNORECASE)
_PS_RUNS_TEXT = re.compile(r"(?<![\w-])(?:Invoke-Expression|iex)(?![\w-])|&|\.Invoke\s*\(|\[\s*scriptblock\s*\]",
                           re.IGNORECASE)
# The pipeline object a delete may name: the item the listing found, as itself or its path.
_PS_PIPELINE_ITEM = frozenset({"$_", "$psitem", "$_.fullname", "$psitem.fullname", "$_.pspath", "$psitem.pspath"})
_PS_ITEM_METHOD_DELETE = re.compile(r"\$(?:_|psitem)\s*\.\s*Delete\s*\(\s*(?:\$true|\$false)?\s*\)", re.IGNORECASE)
# Remove-Item parameters: the ones naming what is deleted, the ones taking another value, and switches.
_RI_PATH_PARAMETERS = ("-path", "-literalpath")
_RI_VALUE_PARAMETERS = ("-filter", "-include", "-exclude", "-erroraction", "-warningaction", "-informationaction")
_RI_SWITCHES = ("-recurse", "-force", "-whatif", "-confirm", "-verbose", "-debug")
_RI_ALIASES = {"-lp": "-literalpath", "-pspath": "-literalpath", "-ea": "-erroraction", "-wa": "-warningaction",
               "-infa": "-informationaction", "-vb": "-verbose", "-db": "-debug", "-wi": "-whatif", "-cf": "-confirm"}
# Stages that pass the listed items on unchanged. Select-Object only with these: -Property and -ExpandProperty make
# new objects, and a calculated property can name any path.
_PS_FILTERS = frozenset({"where-object", "where", "?"})
_PS_SORTS = frozenset({"sort-object", "sort"})
_PS_SELECTS = frozenset({"select-object", "select"})
_PS_SELECT_COUNTS = frozenset({"-first", "-last", "-skip", "-skiplast", "-index"})
_PS_FOREACH = frozenset({"foreach-object", "%", "foreach"})
_PS_LISTING_WORD = re.compile(r"(?<![\w-])(?:Get-ChildItem|gci|ls|dir|Get-Item|gi)(?![\w-])", re.IGNORECASE)
_PS_LISTING_START = re.compile(r"\s*(?:Get-ChildItem|gci|ls|dir)(?![\w-])", re.IGNORECASE)
# Get-ChildItem parameters: the ones naming the listed folder, the ones taking another value, and switches.
_GCI_PATH_PARAMETERS = ("-path", "-literalpath")
_GCI_VALUE_PARAMETERS = ("-filter", "-include", "-exclude", "-depth", "-attributes", "-erroraction", "-errorvariable",
                         "-warningaction", "-warningvariable", "-informationaction", "-informationvariable",
                         "-outvariable", "-outbuffer", "-pipelinevariable")
_GCI_SWITCHES = ("-recurse", "-force", "-name", "-directory", "-file", "-hidden", "-readonly", "-system", "-verbose",
                 "-debug", "-followsymlink")
_GCI_ALIASES = {"-r": "-recurse", "-lp": "-literalpath", "-pspath": "-literalpath", "-ad": "-directory", "-af": "-file",
                "-ah": "-hidden", "-ar": "-readonly", "-as": "-system", "-ea": "-erroraction", "-ev": "-errorvariable",
                "-wa": "-warningaction", "-wv": "-warningvariable", "-infa": "-informationaction",
                "-iv": "-informationvariable", "-ov": "-outvariable", "-ob": "-outbuffer", "-pv": "-pipelinevariable",
                "-vb": "-verbose", "-db": "-debug"}


def _gci_parameter(flag: str) -> str | None:
    """A Get-ChildItem parameter's full name from any unique prefix or alias; None when unknown or ambiguous."""
    flag = flag.casefold()
    if flag in _GCI_ALIASES:
        return _GCI_ALIASES[flag]
    names = [name for name in (*_GCI_PATH_PARAMETERS, *_GCI_VALUE_PARAMETERS, *_GCI_SWITCHES) if name.startswith(flag)]
    return names[0] if len(names) == 1 or flag in names else None


def _ps_piped_delete(command: str) -> bool:
    """Whether a PowerShell delete stands after the command's first pipe.

    A quoted string is text there unless something runs text as code (`iex`, `&`, `.Invoke(`), which counts as a
    delete too since the gate cannot read it.
    """
    _head, pipe, tail = command.partition("|")
    if not pipe:
        return False
    if _PS_RUNS_TEXT.search(tail):
        return True
    return bool(_PS_DELETE_WORD.search(re.sub(r"'[^']*'|\"[^\"]*\"", "''", tail)))


def _ps_method_delete(command: str) -> bool:
    """Whether a PowerShell command calls a .NET delete. Text a data command only prints or writes (`Write-Output
    '$_.Delete()'`) is blanked first, as the write check does; where that split is unsure the raw text counts (#535)."""
    text = _blank_non_syntax(command, True)
    return bool(_PS_METHOD_DELETE.search(command if text is None else text))


def _ps_stages(text: str) -> list[str] | None:
    """`text` split at its top-level pipes (not inside quotes, braces or parentheses); None when one is left open."""
    stages: list[str] = []
    depth = start = i = 0
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote and text[i + 1:i + 2] == quote:
                i += 1  # a doubled quote is a literal quote
            elif ch == quote:
                quote = ""
            elif ch == "`" and quote == '"':
                i += 1
        elif ch in "'\"":
            quote = ch
        elif ch == "`":
            i += 1
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
            if depth < 0:
                return None
        elif ch == "|" and not depth:
            stages.append(text[start:i].strip())
            start = i + 1
        i += 1
    if quote or depth:
        return None
    return [*stages, text[start:].strip()]


def _ps_parameter(flag: str, aliases: Mapping[str, str], *groups: tuple[str, ...]) -> str | None:
    """A cmdlet parameter's full name from any unique prefix or alias; None when unknown or ambiguous."""
    flag = flag.casefold()
    if flag in aliases:
        return aliases[flag]
    names = [name for group in groups for name in group if name.startswith(flag)]
    return names[0] if len(names) == 1 or flag in names else None


def _ps_item_delete(text: str, *, piped: bool) -> bool:
    """Whether `text` deletes only the pipeline object: `$_.Delete()`/`$_.Delete($true)`, or one Remove-Item (any
    alias) whose every path is the pipeline object. With no path it deletes its pipeline input, which counts only
    where it is a pipeline stage (`piped`)."""
    if _PS_ITEM_METHOD_DELETE.fullmatch(text.strip()):
        return True
    if any(char in text for char in "(){}[]@,;&`\"'") or _PS_METHOD_DELETE.search(text):
        return False  # only the plain forms are read; `$(...)`, arrays and quoted paths are not
    words = text.split()
    name = words[0].casefold().rsplit("\\", 1)[-1] if words else ""
    if name not in _PS_DELETE_CMDLETS or (words[0].count("\\") and
                                          words[0].casefold().rsplit("\\", 1)[0] != "microsoft.powershell.management"):
        return False
    paths: list[str] = []
    i = 1
    while i < len(words):
        word = words[i]
        if not word.startswith("-") or word == "-":
            paths.append(word)
            i += 1
            continue
        flag, colon, attached = word.partition(":")
        parameter = _ps_parameter(flag, _RI_ALIASES, _RI_PATH_PARAMETERS, _RI_VALUE_PARAMETERS, _RI_SWITCHES)
        if parameter is None:
            return False
        if parameter in _RI_SWITCHES:
            if colon and attached.casefold() not in {"$true", "$false"}:
                return False
            i += 1
            continue
        value = attached if colon else (words[i + 1] if i + 1 < len(words) else None)
        if not value:
            return False
        if parameter in _RI_PATH_PARAMETERS:
            paths.append(value)
        elif "$" in value:
            return False
        i += 1 if colon else 2
    if not paths:
        return piped
    return all(path.casefold() in _PS_PIPELINE_ITEM for path in paths)


def _ps_passes_items_on(stage: str) -> bool:
    """Whether a stage between the listing and the delete passes the listed items on unchanged: Where-Object (a
    script block, or a property filter) with no delete inside, Sort-Object by property, and Select-Object counts."""
    words = stage.split()
    name = words[0].casefold() if words else ""
    rest = stage[len(words[0]):].strip() if words else ""
    blanked = re.sub(r"'[^']*'|\"[^\"]*\"", "''", rest)
    if _PS_DELETE_WORD.search(blanked) or _PS_RUNS_TEXT.search(rest):
        return False
    if name in _PS_FILTERS:
        return bool(rest) and (re.fullmatch(r"\{.*\}", rest, re.DOTALL) is not None
                               or not any(char in blanked for char in "(){}$@"))
    if name in _PS_SORTS:
        return not any(char in blanked for char in "(){}$@")
    if name in _PS_SELECTS:
        args = words[1:]
        return (len(args) % 2 == 0 and all(_ps_parameter(flag, {}, tuple(_PS_SELECT_COUNTS)) is not None
                                           and count.isdigit() for flag, count in zip(args[::2], args[1::2])))
    return False


def _ps_pipeline_deletes_only_items(tail: str) -> bool:
    """Whether what follows a listing's first pipe deletes the listed items and nothing else (#533).

    The stages before the last must pass the items on unchanged (`_ps_passes_items_on`), and the last must delete the
    pipeline object: Remove-Item with no path or with `$_`/`$PSItem` (`.FullName`, `.PSPath`), or ForEach-Object whose
    script block is exactly one such delete or `$_.Delete(...)`. A delete of any other path, a stage that makes new
    objects (`% { 'C:/x' }`, a calculated property) and text run as code are not read, so they ask.
    """
    stages = _ps_stages(tail)
    if not stages or not all(stages):
        return False
    *middle, last = stages
    if not all(_ps_passes_items_on(stage) for stage in middle):
        return False
    words = last.split()
    if words and words[0].casefold() in _PS_FOREACH:
        block = last[len(words[0]):].strip()
        if not (block.startswith("{") and block.endswith("}") and _ps_stages(block[1:-1]) == [block[1:-1].strip()]):
            return False
        return _ps_item_delete(block[1:-1], piped=False)
    return _ps_item_delete(last, piped=True)


def _ps_piped_delete_inside_step_tmp(command: str, workdir: str | None) -> bool:
    """A listing piped into a delete meets the `rm -r` rule: one pipeline from one Get-ChildItem whose every folder is
    literal and below `.tmp`, and whose later stages delete only the items it lists. No folder means the step folder
    itself; `-FollowSymlink` walks out through links."""
    if not workdir or re.search(r"[;\n\r]|&&|\|\|", command):
        return False
    head, _pipe, tail = command.partition("|")
    start = _PS_LISTING_START.match(head)
    if not start or _PS_LISTING_WORD.search(tail) or not _ps_pipeline_deletes_only_items(tail):
        return False  # the items come from elsewhere, a later stage lists another folder, or deletes another path
    words = head[start.end():].split()
    paths: list[str] = []
    i = 0
    while i < len(words):
        if not words[i].startswith("-") or words[i] == "-":
            value = _literal_shell_word(words[i], True)
            if value is None:
                return False
            paths.append(value)
            i += 1
            continue
        flag, colon, raw_value = words[i].partition(":")
        name = _gci_parameter(flag)
        if name is None or name == "-followsymlink":
            return False
        if name in _GCI_SWITCHES:
            i += 1
            continue
        attached = _literal_shell_word(raw_value, True)
        if not colon:
            i += 1
            if i >= len(words):
                return False
            attached = _literal_shell_word(words[i], True)
        if name in _GCI_PATH_PARAMETERS:
            if attached is None:
                return False  # only the listed folder must be literal; `-Filter *.part` may be a pattern
            paths.append(attached)
        i += 1
    return bool(paths) and _targets_below_step_tmp(paths, True, workdir)


def _bare(word: str) -> str:
    """A word with its quotes and backslashes dropped, for reading option letters lexically."""
    return re.sub(r"""\\(.)|["']""", lambda m: m.group(1) or "", word)


def _simple_command_words(command: str, start: int) -> tuple[list[str], re.Match | None]:
    """The whitespace-split words from `start` to the end of that simple command, and the match that ends it."""
    end = _BASH_COMMAND_END.search(command, start)
    return command[start:end.start() if end else len(command)].split(), end


_BRACE_EXPANSION = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")


def _rm_word(word: str) -> tuple[str, bool]:
    """A word read lexically after `rm`: its leading literal text, and whether an expansion follows that text.

    `$'-rf'`, `"$OPT"`, `` `cmd` `` and a brace list (`{-r,x}`) are expansions; a glob is not (it expands to names). A
    quote left open is the lexical scan's own edge (`sh -c 'rm x'`), not more of the word (#533).
    """
    value: list[str] = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch == "'":
            end = word.find("'", i + 1)
            if end < 0:
                break
            value.append(word[i + 1:end])
            i = end + 1
        elif ch == '"':
            j = i + 1
            while j < len(word) and word[j] != '"':
                if word[j] in "$`":
                    return "".join(value), True
                if word[j] == "\\" and j + 1 < len(word):
                    j += 1
                value.append(word[j])
                j += 1
            if j >= len(word):
                break
            i = j + 1
        elif ch in "$`" or (ch == "{" and _BRACE_EXPANSION.match(word, i)):
            return "".join(value), True
        elif ch == "\\" and i + 1 < len(word):
            value.append(word[i + 1])
            i += 2
        else:
            value.append(ch)
            i += 1
    return "".join(value), False


# Programs that run the words after them as a command line, so an `rm` there is a real rm (#535 follow-up).
_RUNS_COMMAND_LINE = _EXECUTES_ARGUMENTS | frozenset({
    "command", "builtin", "stdbuf", "setsid", "doas", "busybox", "ionice", "taskset", "chrt", "flock", "srun",
    "mpirun", "mpiexec", "unshare", "nsenter", "chroot", "runuser", "strace", "ltrace", "valgrind", "unbuffer"})
# Where a simple command can start, read lexically. An opening quote right before the word counts too (`_rm_runs`).
_COMMAND_START = frozenset(";|&\n(`{'\"")
_REDIRECTION_WORD = re.compile(r"(?:\d+|&)?(?:>>?|<|>&|<&|&>>?)(.*)")
_GIT_VALUE_OPTIONS = frozenset({"-c", "--namespace", "--super-prefix", "--config-env"})


def _git_rm_reaches_another_tree(options: list[str], assignments: list[str]) -> bool | None:
    """`git <options> rm`: True when the options or the environment may point git at another tree (`-C`, `--git-dir`,
    `--work-tree`, `core.worktree`, GIT_DIR/GIT_WORK_TREE) or a computed word may, False when it removes files in the
    current tree. None when `rm` is not the subcommand but another one's argument (`git log --grep rm`)."""
    if any(re.match(r"GIT_(?:DIR|WORK_TREE)=", word) for word in assignments) or \
            any("core.worktree" in _bare(word).casefold() for word in options):
        return True
    k = 0
    while k < len(options):
        word, value = options[k], _bare(options[k])
        if "$" in word or "`" in word:
            return True
        if not value.startswith("-"):
            return None
        name = value.split("=", 1)[0]
        if name in {"-C", "--git-dir", "--work-tree"} or value.startswith("-C"):
            return True
        k += 2 if name in _GIT_VALUE_OPTIONS and "=" not in value else 1
    return False


def _rm_runs(command: str, match: re.Match) -> str:
    """How a lexical `rm` word runs: "rm" for a filesystem rm, "elsewhere" for a `git rm` that may reach another tree,
    "" for another program's argument (`docker rm "$id"`, `git rm -r x` in the current tree).

    The rm is a filesystem rm when it starts a simple command (after assignments, keywords and redirections), or
    follows a computed program or one that runs a command line (`sudo`, `xargs`, `find`, `sh`, ...).
    """
    start = match.start()
    while start and not command[start - 1].isspace() and command[start - 1] not in _COMMAND_START:
        start -= 1
    name = re.split(r"[\\/]", _bare(command[start:match.end()]))[-1].lstrip("!").casefold()
    if name not in {"rm", "rm.exe"}:
        return ""  # `X=rm`: an assignment's value
    if start and command[start - 1] in "'\"":
        return "rm"  # it opens quoted text, which may be run (`sh -c 'rm ..'`)
    head = start
    while head and command[head - 1] not in _COMMAND_START - {"'", '"'}:
        head -= 1
    words: list[str] = []
    assignments: list[str] = []
    before = command[head:start].split()
    k = 0
    while k < len(before):
        word = before[k]
        redirection = _REDIRECTION_WORD.fullmatch(word)
        if redirection:
            k += 1 if redirection.group(1) else 2
        elif not words and _BASH_ASSIGNMENT.match(word):
            assignments.append(word)
            k += 1
        elif not words and word in _BASH_KEYWORDS:
            k += 1
        else:
            words.append(word)
            k += 1
    if not words:
        return "rm"
    program = re.split(r"[\\/]", _bare(words[0]))[-1].casefold()
    program = program[:-4] if program.endswith(".exe") else program
    if "$" in words[0] or "`" in words[0] or program in _RUNS_COMMAND_LINE:
        return "rm"
    if program == "git" and _git_rm_reaches_another_tree(words[1:], assignments):
        return "elsewhere"
    return ""


def _recursive_rm(command: str) -> bool:
    """Whether a bash `rm` asks for recursion in any of its option words (#17), or may (#533).

    GNU rm reads options anywhere before `--`, so `rm -f -r`, `rm -v -rf`, `rm --recursive` and `rm x -r` are all
    recursive; the pattern in settings sees only the first cluster. The scan is lexical, as that pattern is: a
    quoted `sh -c 'rm -f -r x'` counts too, but `docker rm` and `git rm` are not filesystem rms (`_rm_runs`). A word
    the gate cannot read as literal text may be an option, `-r` among them: one that starts with an expansion
    (`$'-rf'`, `"$OPT"`) or an option followed by one (`-f"$X"`) counts as recursive, except one double-quoted word
    that is the only operand (`rm -f "$f"`): one word is one option cluster with nothing to delete, or one file.
    After `--` every word is a target. A `git rm` that may reach another tree counts too.
    """
    for match in _BASH_RM_WORD.finditer(command):
        runs = _rm_runs(command, match)
        if runs == "elsewhere":
            return True
        if runs != "rm":
            continue
        computed: list[str] = []
        operands = 0
        for word in _simple_command_words(command, match.end())[0]:
            value, expands = _rm_word(word)
            if expands and (not value or value.startswith("-")):
                computed.append(word)
            elif value == "--":
                break
            elif value.startswith("--"):
                flag = value.split("=", 1)[0]
                if len(flag) > 2 and "--recursive".startswith(flag):
                    return True
            elif value.startswith("-") and len(value) > 1:
                if any(letter in "rR" for letter in value[1:]):
                    return True
            elif value:
                operands += 1
        if computed and not (len(computed) == 1 and not operands and re.fullmatch(r'"[^"]*"', computed[0])):
            return True
    return False


@dataclass(frozen=True)
class _FindDelete:
    starts: tuple[str, ...]  # raw words; none means `.`
    follows_links: bool
    targets: tuple[str, ...] = ()  # literal paths an `-exec rm` payload names besides the found item `{}`
    unreadable: bool = False  # a payload that may delete what the gate cannot read (#533)


_FOUND = "\x00found"  # find's `{}` while a payload is read: the found path, never an expansion


def _find_payload(action: str, words: list[str]) -> tuple[str, list[str]]:
    """What one `-exec`/`-execdir`/`-ok`/`-okdir` payload deletes: ("none" | "delete" | "unreadable", other targets).

    The payload is read as one literal simple command. A computed program (`$CMD`, `$'rm'`) may be rm, and a program
    that runs other code (`sh -c '...'`, `xargs rm`, `env rm`) with a delete word in it hides what it deletes: both
    are unreadable. A literal rm/unlink deletes `{}` and its other targets, which must be literal too; `-execdir`
    runs in each found item's folder, so there only `{}` is read (#533).
    """
    literals = [_literal_shell_word(word.replace("{}", _FOUND), False) for word in words]
    if not literals:
        return "none", []
    if literals[0] is None:
        return "unreadable", []
    program = re.split(r"[\\/]", literals[0])[-1].casefold()
    program = program[:-4] if program.endswith(".exe") else program
    if program not in {"rm", "unlink"}:
        return ("unreadable" if any(_BASH_DELETE_WORD.search(word) for word in words) else "none"), []
    if any(value is None for value in literals):
        return "unreadable", []
    targets: list[str] = []
    after_options = False
    for value in literals[1:]:
        if not after_options and value == "--":
            after_options = True
        elif not after_options and value.startswith("-") and value != "-":
            continue
        elif value != _FOUND:
            if _FOUND in str(value) or action in {"-execdir", "-okdir"}:
                return "unreadable", []
            targets.append(str(value))
    return "delete", targets


def _find_deletes(command: str) -> list[_FindDelete]:
    """Each bash find that deletes what it finds (#17).

    A find deletes with `-delete`, with `-exec`/`-execdir`/`-ok`/`-okdir` running rm or unlink, or when its output
    is piped to `xargs ... rm`. A payload the gate cannot read may delete anything, so it counts too (#533).
    """
    found: list[_FindDelete] = []
    for match in _BASH_FIND_WORD.finditer(command):
        words, end = _simple_command_words(command, match.end())
        deletes = follows = unreadable = False
        targets: list[str] = []
        for index, word in enumerate(words):
            value = _bare(word).rstrip(")")  # `$(find . -delete)`
            if value == "-delete":
                deletes = True
            elif value in {"-L", "-follow"}:
                follows = True
            elif value in _FIND_EXEC:
                tail = words[index + 1:]
                close = next((k for k, w in enumerate(tail) if _bare(w) in {";", "+"}), len(tail))
                kind, named = _find_payload(value, tail[:close])
                deletes = deletes or kind != "none"
                unreadable = unreadable or kind == "unreadable"
                targets += named
        if not deletes and end and end.group() == "|":
            piped = re.split(r"[;&\n]|\|\|", command[end.end():], maxsplit=1)[0]
            deletes = bool(re.search(r"(?<![\w.-])xargs\b", piped) and _BASH_DELETE_WORD.search(piped))
        if not deletes:
            continue
        k = 0
        while k < len(words) and re.fullmatch(r"-[HP]|-O\d*", _bare(words[k])):
            k += 1
        if k < len(words) and _bare(words[k]) == "-D":
            k += 2
        starts = []
        while k < len(words) and not _bare(words[k]).startswith(("-", "(", "!", ")", ",")):
            starts.append(words[k])
            k += 1
        found.append(_FindDelete(tuple(starts), follows, tuple(targets), unreadable))
    return found


def _find_delete_inside_step_tmp(command: str, find: _FindDelete, workdir: str | None) -> bool:
    """A deleting find meets the rule `rm -r` meets: one simple command, every start literal and below `.tmp`.

    No start means `.`, the step folder itself. A pipe (`| xargs rm`), a redirection, `cd` or a substitution makes it
    more than one simple command, as it does for rm; `-L` walks through links to wherever they lead. An `-exec rm`
    payload's own targets meet the same rule, and a payload the gate cannot read never passes (#533).
    """
    if not workdir or find.follows_links or find.unreadable:
        return False
    plain = _FIND_TEXT.sub("", command)
    if any(char in plain for char in ";|&()<>`\r\n") or not _BASH_FIND_WORD.match(plain.lstrip()):
        return False
    starts = [_literal_shell_word(raw, False) for raw in find.starts] or ["."]
    if any(start is None for start in starts):
        return False
    return _targets_below_step_tmp([*(str(start) for start in starts), *find.targets], False, workdir)


@dataclass
class Decision:
    action: str  # allow | deny | ask
    reason: str = ""
    # The tool input the decision was made on, when it is not the input Claude sent (#219). The gate must answer
    # with it (updatedInput), so the file Claude writes is the one that was judged.
    updated_input: dict | None = None


def _windows_normpath(path: str) -> str:
    """`ntpath.normpath` in linear time: the running stdlib's own drive/root split, then one pass over components.

    CPython's pure-Python `ntpath.normpath` (Linux and other non-Windows hosts; Windows has a native one) deletes each
    empty, `.` and `x\\..` component from a list in place, so a long separator or dot run costs quadratic time
    (#868: a 131,072-character separator candidate). The prefix still comes from the stdlib, so UNC, device and drive
    spellings normalize as before; the component rules are the stdlib's, applied with a stack.
    """
    splitroot = getattr(ntpath, "splitroot", None)
    if splitroot is not None:  # Python 3.12+
        path = path.replace("/", "\\")
        drive, root, tail = splitroot(path)
    else:  # Python 3.10/3.11: device and literal paths come back unchanged; leading separators make one root
        if path.startswith(("\\\\.\\", "\\\\?\\")):
            return ntpath.normpath(path)
        path = path.replace("/", "\\")
        drive, tail = ntpath.splitdrive(path)
        root = "\\" if tail.startswith("\\") else ""
        tail = tail.lstrip("\\")
    comps: list[str] = []
    for comp in tail.split("\\"):
        if not comp or comp == ".":
            continue
        if comp == "..":
            if comps and comps[-1] != "..":
                comps.pop()
                continue
            if not comps and root:
                continue
        comps.append(comp)
    prefix = drive + root
    return prefix + "\\".join(comps) if prefix or comps else "."


def _norm(p: str, *, expand_vars: bool = True) -> str:
    """Lexically normalize both POSIX and Windows paths, regardless of the host OS."""
    p = os.path.expanduser(p)
    if expand_vars:
        p = os.path.expandvars(p)
    if re.match(r"^[A-Za-z]:[/\\]", p) or p.startswith(("\\\\", "//")):
        return _windows_normpath(p).replace("\\", "/").casefold()
    normalized = posixpath.normpath(p.replace("\\", "/"))
    return normalized.casefold() if os.name == "nt" else normalized


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _absolute(p: str) -> bool:
    return p.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", p))


def _drive_relative(p: str) -> bool:
    """A drive-qualified path without a slash uses that drive's unknown current directory."""
    return bool(re.match(r"^[A-Za-z]:(?![/\\])", p))


@dataclass(frozen=True)
class _PathTextScan:
    candidates: tuple[str, ...]
    separator_starts: tuple[int, ...]
    drive_starts: tuple[int, ...]
    network_ranges: tuple[tuple[int, int], ...]


_URI_PREFIX = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
_TOKEN_BREAKS = frozenset(" \t\r\n'\"`|;&<>")
_CHUNK_BREAKS = frozenset("=<>(),")
_CHUNK_END = re.compile(r"[=<>(),]")
_COLON_RUN_END = re.compile(r"[^:]")
_SCHEME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+.-")


def _file_uri_path(uri: str) -> str | None:
    """Return the local/UNC path named by a file URI."""
    parts = urlsplit(uri)
    host, path = parts.netloc, unquote(parts.path)
    if re.fullmatch(r"[A-Za-z]:", host):  # file://C:/x (non-standard but seen)
        host, path = "", host + path
    elif host and host.casefold() != "localhost":
        path = "//" + host + path  # a remote host is a UNC path
    if re.match(r"^/[A-Za-z]:[/\\]", path):  # file:///C:/x → C:/x
        path = path[1:]
    return path or None


def _token_candidates(token: str) -> Iterator[str]:
    """Split one lexical token without retrying at every delimiter in a run."""
    chunk_start = 0
    length = len(token)
    while chunk_start < length:
        while chunk_start < length and token[chunk_start] in _CHUNK_BREAKS:
            chunk_start += 1
        if chunk_start >= length:
            break
        # Fixed character classes scan long runs in C, within the same bounds.
        boundary = _CHUNK_END.search(token, chunk_start, length)
        chunk_end = boundary.start() if boundary else length
        left, right = chunk_start, chunk_end
        while left < right and token[left] in "[]{}":
            left += 1
        while right > left and token[right - 1] in "[]{}":
            right -= 1
        part_start = left
        while part_start < right:
            while part_start < right and token[part_start] in "[]{}":
                part_start += 1
            if part_start >= right:
                break
            uri = _URI_PREFIX.match(token, part_start, right)
            if uri and len(uri.group(1)) > 1:
                if uri.group(1).casefold() == "file":
                    path = _file_uri_path(token[part_start:right])
                    if path:
                        yield path
                break
            colon = part_start
            while colon < right:
                if token[colon] == ":" and not (
                        colon == part_start + 1 and token[part_start].isalpha()):
                    break
                colon += 1
            if colon == right:
                yield token[part_start:right]
                break
            if colon > part_start:
                yield token[part_start:colon]
            boundary = _COLON_RUN_END.search(token, colon + 1, right)
            part_start = boundary.start() if boundary else right
        chunk_start = chunk_end + 1


def _network_url_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Locate non-file URL tokens without retrying a greedy scheme at every character."""
    ranges: list[tuple[int, int]] = []
    search_from = 0
    while (slashes := text.find("://", search_from)) >= 0:
        start = slashes
        while start and text[start - 1] in _SCHEME_CHARS:
            start -= 1
        scheme = text[start:slashes]
        if (scheme and scheme[0].isalpha() and (start == 0 or text[start - 1] not in _SCHEME_CHARS)
                and scheme.casefold() != "file"):
            end = slashes + 3
            while end < len(text) and not text[end].isspace():
                end += 1
            ranges.append((start, end))
            search_from = end
        else:
            search_from = slashes + 3
    return tuple(ranges)


def _scan_path_text(s: str) -> _PathTextScan:
    """Scan path-like text in linear time; separator runs have one start only."""
    separator_starts: list[int] = []
    drive_starts: list[int] = []
    for i, char in enumerate(s):
        if char in "/\\" and (i == 0 or s[i - 1] not in "/\\"):
            separator_starts.append(i)
        if (char.isalpha() and i + 2 < len(s) and s[i + 1] == ":"
                and s[i + 2] in "/\\"):
            drive_starts.append(i)

    candidates: list[str] = []
    i = 0
    while i < len(s):
        char = s[i]
        if char in "'\"":
            end = s.find(char, i + 1)
            if end < 0:
                i += 1
                continue
            inner = s[i + 1:end]
            candidates.extend(_token_candidates(inner))
            if "'" in inner or '"' in inner:
                # A quote inside a quoted string (`python -c "open(r'C:\x')"`) opens a path of its own; scanning only
                # the outer string split `C:\x` at the colon and resolved `\x` against the current drive (#324 CI).
                candidates.extend(_scan_path_text(inner).candidates)
            i = end + 1
            continue
        if char in _TOKEN_BREAKS:
            i += 1
            continue
        start = i
        escaped: list[str] | None = None
        while i < len(s) and s[i] not in _TOKEN_BREAKS:
            if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in " \t":
                if escaped is None:
                    escaped = []
                escaped.append(s[start:i])
                escaped.append(s[i + 1])
                i += 2
                start = i
                continue
            i += 1
        if escaped is None:
            token = s[start:i]
        else:
            escaped.append(s[start:i])
            token = "".join(escaped)
        candidates.extend(_token_candidates(token))
    network_ranges = _network_url_ranges(s)
    return _PathTextScan(tuple(candidates), tuple(separator_starts), tuple(drive_starts), network_ranges)


def _candidate_paths(s: str) -> Iterator[str]:
    yield from _scan_path_text(s).candidates


def restricted_paths(policy: PolicySettings) -> list[str]:
    return [_norm(z.path) for z in policy.data_zones if z.level == "restricted"]


def non_public_paths(policy: PolicySettings) -> list[str]:
    """Normalized roots whose records must not be copied into a staff conversation."""
    return [_norm(z.path) for z in policy.data_zones if z.level in {"internal", "restricted"}]


def _zone_hit(tool_input: dict[str, Any], policy: PolicySettings, levels: set[str],
              workdir: str | None = None):
    zones = [zone.path for zone in policy.data_zones if zone.level in levels]
    roots = [_norm(path) for path in zones]
    return touches(tool_input, roots, workdir=workdir) or touches_resolved(
        tool_input, zones, workdir=workdir,
    )


def internal_row_output_hit(tool_name: str, tool_input: dict[str, Any], policy: PolicySettings,
                            workdir: str | None = None) -> bool:
    """Whether an approval card should warn about non-public source-row output."""
    return internal_output_classification(tool_name, tool_input, policy, workdir) == "rows"


def internal_output_classification(tool_name: str, tool_input: dict[str, Any], policy: PolicySettings,
                                   workdir: str | None = None) -> str | None:
    """Advisory card label for a non-public read; never decides allow/ask/deny."""
    if not _zone_hit(tool_input, policy, {"internal", "restricted"}, workdir):
        return None
    if tool_name == "Glob":
        return "summary"
    if tool_name in {"Read", "Grep"}:
        # A first line may be a JSONL record or a headerless table row. Ambiguity stays on the row side.
        return "rows"
    if tool_name in SHELL_TOOLS:
        from .tool_command import command_prints_rows
        return ("rows" if command_prints_rows(str(tool_input.get("command") or ""),
                                               powershell=tool_name == "PowerShell") else "summary")
    return None


def _strings(obj: Any, path_field: bool = False) -> Iterator[tuple[str, bool]]:
    if isinstance(obj, str):
        yield obj, path_field
    elif isinstance(obj, dict):
        for key, v in obj.items():
            is_path = isinstance(key, str) and (key in {"path", "paths", "directory", "cwd", "workdir"}
                                                or key.endswith("_path"))
            yield from _strings(v, path_field or is_path)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, path_field)


def _raw_spaced_zone(s: str, zone: str) -> bool:
    """Conservatively find a spaced absolute zone in shell text at explicit boundaries."""
    if " " not in zone:
        return False
    raw = re.sub(r"\\([ \t])", r"\1", s)
    if re.match(r"^[A-Za-z]:/|^//", zone):
        raw = raw.replace("\\", "/")
    if os.name == "nt" or re.match(r"^[A-Za-z]:/|^//", zone):
        raw = raw.casefold()
    start = 0
    while (start := raw.find(zone, start)) != -1:
        end = start + len(zone)
        before = start == 0 or raw[start - 1] in " \t\r\n'\"=:<>(),;|&" or raw[:start].endswith("file://")
        after = end == len(raw) or raw[end] in "/ \t\r\n'\"=:<>(),;|&"
        if before and after:
            return True
        start += 1
    return False


def _scan_mentions_zone(text: str, zones: Iterable[str], scan: _PathTextScan) -> bool:
    folded = [z.casefold().rstrip("/") or "/" for z in zones if z]
    for token in scan.candidates:
        if _drive_relative(token):
            if any(re.match(r"^[a-z]:/", z) and token[0].casefold() == z[0] for z in folded):
                return True
            continue
        if _absolute(token) and any(_inside(_norm(token).casefold(), z) for z in folded):
            return True
    return any(_raw_spaced_zone(text.casefold(), z) for z in folded)


def mentions_zone(text: str, zones: Iterable[str]) -> bool:
    """The publish guard's view of `touches`: same candidates and lexical normalization, case-folded.

    Over-matching is safe when deciding what not to publish, so case is ignored on every host.
    """
    return _scan_mentions_zone(text, zones, _scan_path_text(text))


def touches(obj: Any, paths: Iterable[str], workdir: str | None = None) -> str | None:
    """Find lexical path references, preserving structured, quoted and escaped spaces.

    Drive-relative paths on a restricted zone's drive are treated as touching it,
    since that drive's current directory is unknown. Unquoted shell text also
    gets a boundary check for zones with spaces. This is not a shell parser:
    paths concatenated without a recognized separator, or produced by variables,
    globs, substitutions, symlinks or other runtime expansion may be missed.
    """
    paths = [(p, _norm(p)) for p in paths if p]
    for s, path_field in _strings(obj):
        for token in (s,) if path_field else _scan_path_text(s).candidates:
            if _drive_relative(token):
                for original, zone in paths:
                    if re.match(r"^[A-Za-z]:/", zone) and token[0].casefold() == zone[0].casefold():
                        return original
                continue
            if not _absolute(token) and workdir:
                token = _norm(posixpath.join(_norm(workdir), token))
            else:
                token = _norm(token)
            for original, zone in paths:
                if _inside(token, zone):
                    return original
        if not path_field:
            for original, zone in paths:
                if _raw_spaced_zone(s, zone):
                    return original
    return None


MAX_RESOLVED_CANDIDATES = 256


class Unresolved(str):
    """`touches_resolved` gave up before every candidate was resolved: unknown, not clear (#131).

    `path_field` says whether the cap was reached among structured path fields (always an access) or only
    in free text, which for file tools is content or a search pattern rather than a path being opened.
    """

    path_field: bool = False


def _collapse_separators(p: str, *, nt: bool | None = None) -> str:
    """`p` with every run of `/` after its first one or two made a single `/`, on a POSIX host (#1158).

    CPython's POSIX `os.path.realpath` copies the rest of the path at every separator, so a run of 65,536 of them costs
    the square of its length (0.03 s, then 0.12, 0.44, 1.75 s for each doubling; 3.10 and 3.12 alike). A run means what
    one separator means to `realpath`. A leading pair is kept, the one place a pair differs from a single separator
    (`//host` is implementation-defined); three or more leading separators are one, as `normpath` reads them. A Windows
    path is returned as written: its realpath has no such cost, and a UNC prefix with a missing share reads differently
    when its separators are merged. `nt` is the host's own unless a test names one."""
    if os.name == "nt" if nt is None else nt:
        return p
    lead = len(p) - len(p.lstrip("/"))
    head = p[:lead] if lead == 2 else p[:1] if lead else ""
    return head + _SEPARATOR_RUN.sub("/", p[lead:])


_SEPARATOR_RUN = re.compile(r"/{2,}")


def _realpath(p: str | os.PathLike) -> str:
    """`os.path.realpath` of `p` with its separator runs merged first: the same path, in time linear in its length.

    Takes what `os.path.realpath` takes: a `str`, `bytes` or any path-like (a `Path` root reaches `_root_rules`)."""
    text = os.fspath(p)
    return os.path.realpath(text if isinstance(text, bytes) else _collapse_separators(text))


def _real(p: str) -> str | None:
    try:
        return _norm(_realpath(os.path.expandvars(os.path.expanduser(p))))
    except (OSError, ValueError):
        return None


def _absent_below(token: str, folder: str, listings: dict[str, set[str] | None]) -> bool:
    """Whether relative `token` surely names nothing in `folder`: no spelling of its first component is there.

    The first component is read every way a host may split it: at `/` (bash, POSIX) and at `/` or `\\` (Windows),
    each also before an NTFS stream (`name:stream`). The shell may still rewrite a bare word (glob, brace, escape)
    but not quoted text, which is what a word with whitespace is; expansions work in both, and an 8.3 short name
    (`~`) is the filesystem's own spelling. `.` and `..` name the folder or its parent. (PR #371 review)"""
    if any(part in (".", "..") for part in re.split(r"[/\\]", token)):
        return False  # Windows drops `missing/..` before it looks anything up (PR #371 review)
    slash = token.split("/", 1)[0]
    either = re.split(r"[/\\]", token, maxsplit=1)[0]
    if not slash or not either:
        return False
    shell = "$%`!" if any(char.isspace() for char in slash) else "*?[]{}^\\$%`!"
    if any(char in slash for char in shell) or "~" in slash:
        return False
    keys = {_entry_key(spelling) for part in (slash, either) for spelling in (part, part.split(":", 1)[0])}
    if "" in keys:
        return False
    if folder not in listings:
        try:
            listings[folder] = {_entry_key(name) for name in os.listdir(folder)}
        except (OSError, ValueError):
            listings[folder] = None
    names = listings[folder]
    return names is not None and not keys & names


def _entry_key(name: str) -> str:
    """A name as the loosest volume compares it, whatever this one does: case- and normalization-insensitive (APFS,
    NTFS), with trailing dots and spaces dropped (Windows). Folding too much only means resolving more (PR #371)."""
    return unicodedata.normalize("NFD", name.rstrip(". ")).casefold()


def touches_resolved(obj: Any, paths: Iterable[str], workdir: str | None = None) -> str | None:
    """`touches` on this host's real filesystem: symlinks and junctions inside a candidate are followed.

    `reference/link/raw.tsv` names no zone, yet reads one when `link` points there. This only means something
    where the files are (the runner's approval gate) and only for calls the gate sees. Structured path fields
    are checked first, then distinct other candidates. Past MAX_RESOLVED_CANDIDATES it returns `Unresolved`
    rather than None: 257 decoy paths must not make the 258th, a link into a zone, pass (#131).
    """
    zones = [(p, zone) for p in paths if p for zone in {_norm(p), _real(p)} if zone]
    if not zones:
        return None
    strings = sorted(_strings(obj), key=lambda item: not item[1])  # path fields first, e.g. Write before content
    seen: set[str] = set()
    listings: dict[str, set[str] | None] = {}
    for s, path_field in strings:
        for token in (s,) if path_field else _scan_path_text(s).candidates:
            if not token or _drive_relative(token):
                continue
            if not _absolute(token):
                if not workdir:
                    continue
                if not path_field and _absent_below(token, workdir, listings):
                    # Nothing by that name is in the workdir, so no link there can carry it into a zone: it is read
                    # as spelled and costs no resolution, so an inline script's hundreds of words do not hit the
                    # cap (13th mock trial).
                    spelled = _norm(os.path.join(_real(workdir) or workdir, token))
                    for original, zone in zones:
                        if _inside(spelled, zone):
                            return original
                    continue
                token = os.path.join(workdir, token)
            if token in seen:
                continue  # a repeated path costs nothing more and proves nothing new
            seen.add(token)
            if len(seen) > MAX_RESOLVED_CANDIDATES:
                unresolved = Unresolved(f"more than {MAX_RESOLVED_CANDIDATES} path candidates")
                unresolved.path_field = path_field
                return unresolved
            real = _real(token)
            for original, zone in zones:
                if real and _inside(real, zone):
                    return original
    return None


def claude_rule_path(p: str) -> str:
    """Path as Claude Code matches it in permission rules.

    Claude normalises Windows paths to POSIX form before matching (`C:\\Users\\x` → `/c/Users/x`).
    Verified on Claude 2.1.282 / Windows 11: only `Read(//c/Users/x/**)` blocked the read;
    `Read(/C:\\Users\\x/**)` and `Read(//C:/Users/x/**)` did not (tests/fixtures/real/claude_code).
    """
    if p.startswith(("\\\\", "//")):
        raise ValueError("Claude UNC deny syntax is unverified; runner must reject this zone")
    s = p.replace("\\", "/").rstrip("/")
    m = re.match(r"^([A-Za-z]):(?:/(.*))?$", s)
    if m:
        return f"/{m.group(1).lower()}" + (f"/{m.group(2)}" if m.group(2) else "")
    return s


def claude_settings(policy: PolicySettings) -> dict:
    """Claude Code settings: deny file tools on restricted zones.

    Absolute paths in permission rules take a leading `//` (e.g. `Read(//data/cohort/**)`).
    """
    deny: list[str] = []
    ask: list[str] = []
    for p in restricted_paths(policy):
        for tool in ("Read", "Edit", "Write"):
            deny.append(f"{tool}(/{claude_rule_path(p)}/**)")
    for zone in policy.data_zones:
        if zone.level == "internal":
            for tool in ("Read", "Grep"):
                ask.append(f"{tool}(/{claude_rule_path(zone.path)}/**)")
    permissions = {key: value for key, value in (("deny", deny), ("ask", ask)) if value}
    return {"permissions": permissions} if permissions else {}


def claude_deny_links(settings: dict, links: Iterable[str]) -> dict:
    """Read/Edit/Write deny rules for link paths inside an open folder that lead into a zone (#132).

    Claude compares rules with the path as written, so the link itself (a file) and anything below it (a
    directory) are both named; the zone rules only cover the zone's own spelling. PI personal paths
    (`claude_deny_private`) use the same pair, since an entry may be a file (`~/.netrc`) or a folder.
    """
    rules = [f"{tool}(/{claude_rule_path(link)}{tail})" for link in links
             for tool in ("Read", "Edit", "Write") for tail in ("", "/**")]
    if not rules:
        return settings
    permissions = dict(settings.get("permissions") or {})
    permissions["deny"] = list(dict.fromkeys([*(permissions.get("deny") or []), *rules]))
    return {**settings, "permissions": permissions}


def claude_ask_internal_links(settings: dict, links: Iterable[str]) -> dict:
    """Read/Grep ask rules for aliases inside an open folder that lead into an internal zone (#725)."""
    rules = [f"{tool}(/{claude_rule_path(link)}{tail})" for link in links
             for tool in ("Read", "Grep") for tail in ("", "/**")]
    if not rules:
        return settings
    permissions = dict(settings.get("permissions") or {})
    permissions["ask"] = list(dict.fromkeys([*(permissions.get("ask") or []), *rules]))
    return {**settings, "permissions": permissions}


def claude_deny_private(settings: dict, paths: Iterable[str], home: str | None = None,
                        open_reads: Iterable[str] = (), write_roots: Iterable[str] = ()) -> dict:
    """Read/Edit/Write deny rules for PI personal paths (policy.private_paths), merged into `settings`, plus
    Bash/PowerShell `ask` rules naming each path.

    A path with no rule form (UNC) gets no deny rule; the approval gate still sees shell commands that name it.
    These rules are the first layer only: Claude compares them literally, so a case-varied or aliased spelling
    passes them. The structural check is that no shell command and no outside read is pre-approved while these
    paths are active (`claude_allowed_tools` with `read_roots`), so such calls reach the gate. Probed on Claude
    2.1.282: `ask: ["Bash(*/.sec/*)"]` stopped a pre-approved `python -c` read.

    A path holding one of `open_reads` (the task's project folder in the staff Claude config folder, #298 ⑤)
    keeps Edit/Write on the whole path, but Read only on its top-level entries off the way to that folder
    (`closed_entries`): a deny rule outranks every allow rule, so a Read deny on the whole path would close it.
    Other tasks' project folders beside it get no rule; the gate refuses reads of them.

    A holder that also holds one of the task's `write_roots` (the workspace root, which the artifact contract marks
    private around the task's own folder) cannot take an Edit/Write deny on the whole path: a deny outranks the
    allow rule for the task's own `outputs/`, so a step could write nothing (#946, trial 2026-10-25 attempt 11).
    There the Edit/Write deny goes on the same top-level entries off the way to the task's folders as the Read deny.
    """
    from .private_paths import closed_entries, holds_open_read, shell_needles

    paths = [p for p in paths if p]
    open_reads = [p for p in open_reads if p]
    ruled = []
    for p in paths:
        try:
            claude_rule_path(p)
        except ValueError:
            continue
        ruled.append(p)
    # Both spellings of each writable folder: with a symlinked or junctioned workspace root the private path is the
    # real root while the task's folder keeps the alias spelling, and neither alone lies under the other.
    write_roots = list(dict.fromkeys(
        spelling for w in write_roots if w
        for spelling in (str(w), os.path.abspath(str(w)), _realpath(str(w)))))
    holders = [p for p in ruled if holds_open_read(p, [*open_reads, *write_roots])]
    merged = claude_deny_links(settings, [p for p in ruled if p not in holders])
    if holders:
        writable = [p for p in holders if holds_open_read(p, write_roots)]
        rules = [f"{tool}(/{claude_rule_path(p)}{tail})" for p in holders if p not in writable
                 for tool in ("Edit", "Write") for tail in ("", "/**")]
        rules += [f"{tool}(/{claude_rule_path(entry)}{tail})" for p in writable
                  for entry in closed_entries(p, [*open_reads, *write_roots])
                  for tool in ("Edit", "Write") for tail in ("", "/**")]
        rules += [f"Read(/{claude_rule_path(entry)}{tail})" for p in holders
                  for entry in closed_entries(p, [*open_reads, *(w for w in write_roots if p in writable)])
                  for tail in ("", "/**")]
        permissions = dict(merged.get("permissions") or {})
        permissions["deny"] = list(dict.fromkeys([*(permissions.get("deny") or []), *rules]))
        merged = {**merged, "permissions": permissions}
    needles = shell_needles(paths, home)
    if not needles:
        return merged
    permissions = dict(merged.get("permissions") or {})
    ask = [f"{tool}(*{needle}*)" for needle in needles for tool in ("Bash", "PowerShell")]
    permissions["ask"] = list(dict.fromkeys([*(permissions.get("ask") or []), *ask]))
    return {**merged, "permissions": permissions}


def claude_read_only(settings: dict, directories: Iterable[str]) -> dict:
    """Add Edit/Write deny rules for directories a task may only read (reference paths, #36)."""
    rules = [f"{tool}(/{claude_rule_path(d)}/**)" for d in directories for tool in ("Edit", "Write")]
    if not rules:
        return settings
    permissions = dict(settings.get("permissions") or {})
    permissions["deny"] = list(dict.fromkeys([*(permissions.get("deny") or []), *rules]))
    return {**settings, "permissions": permissions}


SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


def rule_tool(rule: str) -> str:
    """The tool a permission rule names: `Bash(python *)` → `Bash`."""
    return rule.split("(", 1)[0].strip()


def _root_rules(tool: str, roots: Iterable[str | os.PathLike]) -> list[str]:
    rules = []
    for root in roots:
        for resolve in (os.path.abspath, _realpath):
            try:
                rules.append(f"{tool}(/{claude_rule_path(resolve(root))}/**)")
            except (OSError, ValueError):
                continue
    return rules


def claude_allowed_tools(tools: Iterable[str], write_roots: Iterable[str | os.PathLike],
                         read_roots: Iterable[str | os.PathLike] | None = None,
                         shared_environment_gate: bool = False) -> list[str]:
    """`--allowedTools` with bare Write/Edit narrowed to `Edit(//root/**)` rules for the write roots (#219).

    A bare `Write` pre-approves every path, so the gate never saw Claude write `C:/tmp/...` on Windows
    (tests/fixtures/real/claude_code/claude_windows_write_paths.json). The Edit rule also covers Write, MultiEdit and
    NotebookEdit. Claude pre-approves a write only when the path as written and its resolved path both match, so a
    root spelled through a link or junction gets a rule for each spelling (probes junction_*). A spelling with no
    rule form (UNC) is left to the gate.

    `read_roots` is given while PI personal paths are on (policy.private_paths), even with no active path. Claude matches rule text
    literally, so a case-varied (`C:/USERS/PI/.SSH`) or aliased spelling slips past every deny and ask rule. Then no
    shell rule is pre-approved (`Bash(python *)`, bare `Bash`) and every Read/Grep/Glob rule becomes
    `Read(//root/**)` for the task's own folders: those calls reach the gate, which canonicalizes the text and
    resolves real paths. Probed on Claude 2.1.282 (tests/fixtures/real/claude_code/claude_read_scope_alias.json):
    bare `Read` read a junction from the workdir into another folder, `Read(//workdir/**)` sent it to the
    permission prompt, and the same rule still pre-approved Grep and Glob inside the workdir.

    Shared environments also leave every shell call to the gate: `Bash(ls *)` matches a following install
    joined with `&&`, so narrowing only installer prefixes still skips the installation check.
    """
    tools = list(tools)
    if shared_environment_gate:
        tools = [tool for tool in tools if rule_tool(tool) not in SHELL_TOOLS]
    read_rules: list[str] = []
    if read_roots is not None:
        if any(rule_tool(t) in READ_LIKE for t in tools):
            read_rules = _root_rules("Read", read_roots)
        tools = [t for t in tools if rule_tool(t) not in SHELL_TOOLS | READ_LIKE]
    kept = [t for t in tools if t not in WRITE_LIKE]
    if len(kept) != len(tools):
        kept += _root_rules("Edit", write_roots)
    return list(dict.fromkeys([*kept, *read_rules]))


_ROOTED_NO_DRIVE = re.compile(r"^[/\\](?![/\\])")  # `/tmp/x`, `\tmp\x`; not UNC, not `C:/x`
_GIT_BASH_DRIVE = re.compile(r"^[/\\]([A-Za-z])(?=[/\\]|$)")
_GIT_BASH_TMP = re.compile(r"^[/\\]tmp(?=[/\\]|$)", re.IGNORECASE)


def _git_bash_tmp(environ: Mapping[str, str]) -> str | None:
    """The folder Git for Windows mounts at `/tmp` (`usertemp`: the user's TMP/TEMP); None when unclear."""
    found = set()
    for key in ("TMP", "TEMP"):
        value = environ.get(key)
        if not value:
            continue
        if not re.match(r"^[A-Za-z]:[/\\]", value):
            return None
        if os.name == "nt":  # 8.3 names and junctions, as the workdir was resolved
            try:
                value = _realpath(value)
            except (OSError, ValueError):
                return None
        found.add(ntpath.normpath(value))
    if len({p.casefold() for p in found}) != 1:
        return None
    return found.pop()


def _git_bash_path(path: str, environ: Mapping[str, str]) -> str | None:
    """What Claude's Bash (Git Bash) means by a drive-less rooted path, for the mounts labhq knows."""
    if _GIT_BASH_TMP.match(path):
        tmp = _git_bash_tmp(environ)
        return ntpath.normpath(tmp + "\\" + path[4:]) if tmp else None
    drive = _GIT_BASH_DRIVE.match(path)
    if drive:
        return ntpath.normpath(f"{drive.group(1).upper()}:\\{path[2:]}")
    return None


def claude_write_input(tool_name: str, tool_input: dict[str, Any], workdir: str | None, roots: Iterable[str], *,
                       windows: bool | None = None, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The input with its write path spelled as the file Claude will write (#219).

    On Windows Claude writes a drive-less rooted path (`/tmp/x`, `\\tmp\\x`) on its cwd's drive (`C:/tmp/x`),
    while its Bash (Git Bash) prints TEMP as `/tmp`. When only the Git Bash meaning is inside the write roots, the
    model copied `pwd` and the path is respelled there; otherwise the drive-root path is kept so an ask shows it.
    The gate must answer with the returned input. Other paths and hosts come back unchanged.
    """
    windows = os.name == "nt" if windows is None else windows
    key = next((k for k in ("file_path", "notebook_path") if tool_input.get(k)), None)
    if tool_name not in WRITE_LIKE or not windows or key is None or not workdir:
        return tool_input
    raw = tool_input[key]
    if not isinstance(raw, str) or not _ROOTED_NO_DRIVE.match(raw):
        return tool_input
    drive = ntpath.splitdrive(workdir)[0]
    if not drive:
        return tool_input
    folded = [_norm(r, expand_vars=False) for r in roots if r]
    actual = ntpath.normpath(drive + raw)
    chosen = actual
    if not any(_inside(_norm(actual, expand_vars=False), r) for r in folded):
        meant = _git_bash_path(raw, os.environ if environ is None else environ)
        if meant and any(_inside(_norm(meant, expand_vars=False), r) for r in folded):
            chosen = meant
    return {**tool_input, key: chosen}


def evaluate_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: PolicySettings,
    allowed_roots: Iterable[str] = (),
    workdir: str | None = None,
    *,
    windows: bool | None = None,
    environ: Mapping[str, str] | None = None,
    private_paths: Iterable[str] = (),
    private_enabled: bool = False,
    home: str | None = None,
    private_open_reads: Iterable[str] = (),
    read_only: bool = False,
) -> Decision:
    allowed_roots = list(allowed_roots)
    if tool_name in SHELL_TOOLS:
        from .process_stop_guard import DENIAL, broad_process_stop
        if broad_process_stop(str(tool_input.get("command", ""))):
            return Decision("deny", DENIAL)
        from .detached_command import DENIAL as DETACHED_DENIAL, detached
        if detached(tool_name, tool_input.get("command")):  # #675: no card, and the answer names the job tool
            return Decision("deny", DETACHED_DENIAL)
    judged = claude_write_input(tool_name, tool_input, workdir, allowed_roots, windows=windows, environ=environ)
    # Every check reads a Git Bash command with its drive paths spelled the Windows way, zones and private paths
    # included: converting only the write targets let `cp /c/<zone>/raw /c/<root>/out` through (PR #364 review).
    checked = git_bash_command(tool_name, judged, allowed_roots, windows)
    # On with no active path (PR #327) still runs the registry check; a non-empty list alone also means on.
    private_paths = list(private_paths)
    private_open_reads = list(private_open_reads)
    closed_zones = [z.path for z in policy.data_zones if z.level == 'restricted']
    from .workspace_commands import workspace_command_reason
    grammar = None
    if tool_name in SHELL_TOOLS:
        from .private_paths import claude_project_slug
        reads = [p for p in private_open_reads if not (Path(p).parent.name == 'projects' and
                 Path(p).name == claude_project_slug(os.path.abspath(workdir or '.')))]
        grammar = workspace_command_reason(str(checked.get('command', '')), workdir, private_paths,
                    powershell=tool_name == 'PowerShell', read_roots=reads, forbidden=closed_zones,
                    environ=environ)
    scoped = bool(grammar and grammar.startswith('문법 안:'))
    decision = _evaluate_tool(tool_name, checked, policy, allowed_roots, workdir, environ=environ,
                              windows=windows, workspace_operation=bool(scoped))
    private = (_private_decision(tool_name, checked, private_paths, workdir, home, environ,
                                list(private_open_reads), closed_zones, workspace_operation=scoped)
               if private_enabled or private_paths else None)
    # Only ever stricter: a deny stays a deny, and an ask is not turned into an allow.
    if private and decision.action != "deny" and (private.action == "deny" or decision.action == "allow"):
        decision = private
    if grammar and (scoped or private_open_reads):
        # The closed grammar governs the managed task-read exception introduced here.
        # Legacy policy without a managed read grant retains its existing decisions.
        if not scoped and private_open_reads and (private_enabled or private_paths) and decision.action == 'allow':
            decision = Decision('ask', grammar)
        elif decision.action == 'allow':
            decision.reason = grammar
        else:
            decision.reason = grammar + ' · ' + decision.reason
    if read_only:
        if tool_name in WRITE_LIKE:
            path = judged.get("file_path") or judged.get("notebook_path")
            if not own_output_write(path, workdir):
                decision = Decision("deny", "Read-only task may write only its own outputs/ and .tmp/ (no links)")
        elif tool_name not in READ_LIKE:
            decision = Decision("deny", "Read-only task permits only reading and scoped file writes")
        if decision.action == "ask":
            decision = Decision("deny", decision.reason)
    if judged is not tool_input:
        decision.updated_input = judged
    return decision


def own_output_write(path: object, workdir: str | None) -> bool:
    """Both spelling and real target must stay in this task's plain output/temp directory."""
    if not isinstance(path, str) or not path or not workdir:
        return False
    from .adapters.owned import is_link

    try:
        root = Path(workdir).resolve()
        target = Path(os.path.abspath(os.path.join(str(root), path)))
        for name in ("outputs", ".tmp"):
            allowed = root / name
            if target == allowed or not target.is_relative_to(allowed):
                continue
            if not target.resolve().is_relative_to(allowed):
                return False
            for part in (target, *target.parents):
                if part == root:
                    break
                if os.path.lexists(part) and (is_link(part) or (part.is_file() and part.stat().st_nlink > 1)):
                    return False
            return True
    except (OSError, ValueError, RuntimeError):
        pass
    return False


# `/c/` where a path starts: after a space, quote, `=`, `>`, `(` ... and never inside a word, URL or other path.
_GIT_BASH_DRIVE_TEXT = re.compile(r"(?<![\w.\-/\\:~$])/([A-Za-z])(?=/)")


def git_bash_command(tool_name: str, tool_input: dict[str, Any], roots: Iterable[str],
                     windows: bool | None = None) -> dict[str, Any]:
    """A Bash input with Git Bash drive paths (`/c/Users/...`) spelled `C:/Users/...`, for the checks only.

    Claude's Bash on Windows is Git Bash. Windows is the `windows` flag, else this host or a drive-letter root."""
    command = tool_input.get("command")
    if tool_name != "Bash" or not isinstance(command, str):
        return tool_input
    if windows is None:
        windows = os.name == "nt" or any(re.match(r"^[A-Za-z]:[/\\]", str(root)) for root in roots)
    if not windows:
        return tool_input
    spelled = _GIT_BASH_DRIVE_TEXT.sub(lambda m: m.group(1).upper() + ":", command)
    return tool_input if spelled == command else {**tool_input, "command": spelled}


_PRIVATE_PATH_KEYS = ("file_path", "notebook_path", "path")


def _private_decision(tool_name: str, tool_input: dict[str, Any], private_paths: list[str], workdir: str | None,
                      home: str | None, environ: Mapping[str, str] | None,
                      open_reads: list[str] | None = None, closed_zones: Iterable[str] = (), *,
                      workspace_operation: bool = False) -> Decision | None:
    """PI personal paths (policy.private_paths): file tools are denied, a shell command naming one asks the PI.

    While private paths are active Claude pre-approves no shell command and no read outside the task's folders
    (`claude_allowed_tools`), so this is where those calls are judged: the text is canonicalized (case-folded where
    the volume is, `~`, variables, admin shares, `\\\\?\\`) and paths are resolved (links, junctions, 8.3 names). A
    file tool's free text (content, a Grep pattern) is not a path being opened and is not checked; a Glob
    pattern's folder part is. Called while private paths are on, even with no active path: then only the
    registry check has anything to match.

    `open_reads`: file reads and proven literal Bash reads may use managed folders below a private parent.
    Written and resolved destinations must stay open. The task's own file-write grant remains scoped to its
    workdir; other shell/write rules are unchanged.
    """
    from .private_paths import holds_open_read, inside_any, mentioned_private_path, open_read_allowed, path_field_text, \
        registry_access, credential_path, managed_shell_read, resolved_read_path

    input_hint = (" 선행 단계 산출물은 읽기 전용 inputs/<step_id>/ 경로를 사용하세요."
                  if workdir and (Path(workdir) / 'inputs').is_dir() else "")

    if tool_name in READ_LIKE | WRITE_LIKE:
        # Keep the existing file-write grant for this task, never for all managed folders.
        managed = open_reads or []
        open_reads = managed if tool_name in READ_LIKE else (
            [workdir] if workdir and open_read_allowed(workdir, managed) else [])
        holders = [p for p in private_paths if holds_open_read(p, open_reads)]
        closed = [p for p in private_paths if p not in holders]

        def private(spelled: str) -> bool:
            if credential_path(spelled) and any((
                    mentioned_private_path(spelled, private_paths, home, environ),
                    open_read_allowed(os.path.dirname(spelled), open_reads))):
                return True
            if mentioned_private_path(spelled, closed, home, environ):
                return True
            # A broad task read grant cannot reopen an explicitly protected child (e.g. inputs/).
            return any(mentioned_private_path(spelled, [holder], home, environ)
                       and not open_read_allowed(spelled, [r for r in open_reads if inside_any(r, [holder])])
                       for holder in holders)

        # (value, folder a relative value starts from): path fields from the workdir, a Glob pattern from its path.
        values = [(tool_input.get(key), workdir) for key in _PRIVATE_PATH_KEYS]
        if tool_name == "Glob" and isinstance(tool_input.get("pattern"), str):
            base = workdir
            if isinstance(tool_input.get("path"), str) and tool_input["path"]:
                base = path_field_text(tool_input["path"], workdir)
            values += [(tool_input["pattern"], base), (_glob_base(tool_input["pattern"]), base)]
        for value, base in values:
            if not isinstance(value, str) or not value:
                continue
            spelled = path_field_text(value, base)
            spellings = {spelled}
            if os.path.isabs(spelled):  # a link in the workspace can lead to a personal folder
                try:
                    spellings.add(_realpath(spelled))
                    raw = value if os.path.isabs(value) else os.path.join(base or '', value)
                    spellings.add(resolved_read_path(raw))
                except (OSError, ValueError):
                    pass
            if any(private(s) for s in spellings):
                return Decision("deny", f"{tool_name} path is a PI personal path (policy.private_paths); it is "
                                        "outside every staff task. Do not open it; ask the CSO if the task needs it."
                                        + input_hint)
        return None
    if tool_name in SHELL_TOOLS:
        cmd = str(tool_input.get("command", ""))
        if workspace_operation and not registry_access(cmd):
            return None
        if tool_name == 'Bash' and open_reads:
            managed = managed_shell_read(cmd, private_paths, open_reads, workdir)
            if managed is True:
                return None
            if managed is False:
                return Decision('ask', 'Bash reaches a closed or credential path (policy.private_paths)' + input_hint)
        if private_paths and mentioned_private_path(cmd, private_paths, home, environ):
            return Decision("ask", f"{tool_name} names a PI personal path (policy.private_paths): `{cmd[:200]}`"
                            + input_hint)
        # A link in the workspace (`alias -> ~/.ssh`) names no private path; its real path does.
        hit = private_paths and touches_resolved({"command": cmd}, private_paths, workdir=workdir)
        if hit:
            return Decision("ask", f"{tool_name} reaches a PI personal path (policy.private_paths) through a link, or "
                                   f"its paths were not all resolved: `{cmd[:200]}`" + input_hint)
        if private_paths and _cd_reaches_private(cmd, private_paths, workdir, home, environ):
            return Decision("ask", f"{tool_name} changes into a folder from which it names a PI personal path "
                                   f"(policy.private_paths), or changes folder too often to judge: `{cmd[:200]}`")
        # The PI's GITHUB_TOKEN is stripped from staff env but stays readable in the user registry (#325).
        spelled = registry_access(cmd)
        if spelled:
            return Decision("ask", f"{tool_name} touches the registry (any registry access asks while private paths "
                                   f"are on; HKCU\\Environment holds the PI's tokens such as GITHUB_TOKEN; 레지스트리 "
                                   f"접근은 PI 승인 필요) via `{spelled[:80]}`: `{cmd[:200]}`")
        if tool_name == 'Bash' and open_reads:
            return Decision('ask', 'Bash read scope is ambiguous (policy.private_paths); confirmation required')
    return None


def _holds_private(base: str, private_paths: Iterable[str]) -> bool:
    """Whether a private path is `base` or below it, as spelled or through a link in `base`."""
    roots = {root for root in (_norm(base), _real(base)) if root}
    return any(_inside(_norm(p), root) for p in private_paths if p for root in roots)


def _cd_reaches_private(cmd: str, private_paths: list[str], workdir: str | None, home: str | None,
                        environ: Mapping[str, str] | None) -> bool:
    """`cd <home> && cat .ssh/x` (PR #324 live probe): relative paths read from each `cd` target, lexically and
    through links. A folder computed at run time (`cd "$(…)"`, an unknown variable) is not followed."""
    from .private_paths import mentioned_private_path, path_field_text, shell_cd_bases

    bases = shell_cd_bases(cmd, workdir, home, environ)
    if bases is None:
        return True
    if not bases:
        return False
    # The scanner keeps a quoted string whole (`cmd /c "cd /d x && type .ssh\\k"`), so plain words count too.
    words = [*_scan_path_text(cmd).candidates, *re.split(r"[\s'\"`|;&<>(),=]+", cmd)]
    tokens = [t for t in dict.fromkeys(words)
              if t and not _absolute(t) and not _drive_relative(t) and not t.startswith(("~", "$", "%"))]
    # Words are read lexically from a cd target, so without `..` they name only what is below it. A target with no
    # private path below it (the task's own workdir) needs no word check: `cd <workdir> && python -c "…"` splits into
    # hundreds of script words and went to the PI (13th mock trial). Links are still followed below for every target.
    # A word with a `..` part (Windows also drops trailing dots and spaces, so `.. ` and `...` count) can leave the
    # target, so those words are always checked. One `../upstream` beside an inline script used to switch the check on
    # for every script word and hit the cap (bench A, 2026-10-04).
    climbing = [t for t in tokens if any(".." in part and not part.rstrip(". ") for part in re.split(r"[/\\]", t))]
    lexical = [b for b in bases if not os.path.isabs(b) or _holds_private(b, private_paths)]
    if len(tokens if lexical else climbing) > MAX_RESOLVED_CANDIDATES:
        return True
    for base in bases:
        spelled = [base, *(path_field_text(t, base) for t in (tokens if base in lexical else climbing))]
        if mentioned_private_path("\n".join(spelled), private_paths, home, environ):
            return True
        if os.path.isabs(base) and touches_resolved({"command": cmd}, private_paths, workdir=base):
            return True
        if os.path.isabs(base) and touches_resolved({"path": base}, private_paths):
            return True
    return False


def _glob_base(pattern: str) -> str:
    """The folder a Glob pattern starts from: everything before the first component with a wildcard."""
    out = []
    for part in re.split(r"([/\\])", pattern):
        if any(char in part for char in "*?[{"):
            break
        out.append(part)
    base = "".join(out).rstrip("/\\")
    return base or (pattern[:1] if pattern[:1] in "/\\" else "")


def _shell_homes(powershell: bool, environ: Mapping[str, str], windows: bool) -> list[str]:
    """Every folder a shell may mean by `~` in a write destination; empty when none is known.

    Bash reads HOME; Git Bash sets it from USERPROFILE (older builds from HOMEDRIVE+HOMEPATH) when it is unset.
    PowerShell's FileSystem home is $HOME: USERPROFILE on Windows, where Windows PowerShell 5.1 may use
    HOMEDRIVE+HOMEPATH instead, and HOME elsewhere. Where the shell may take either, both are judged.
    """
    lookup = {key.casefold(): value for key, value in environ.items()} if windows else dict(environ)

    def get(name: str) -> str:
        return lookup.get(name.casefold() if windows else name) or ""

    drive_home = get("HOMEDRIVE") + get("HOMEPATH") if get("HOMEDRIVE") and get("HOMEPATH") else ""
    if powershell:
        homes = [get("USERPROFILE"), drive_home] if windows else [get("HOME")]
    elif get("HOME"):
        homes = [get("HOME")]
    else:
        homes = [get("USERPROFILE"), drive_home] if windows else []
    found = []
    for home in homes:
        if home and windows and not powershell:
            home = _git_bash_path(home, environ) or home  # HOME may be spelled `/c/Users/x`
        if home:
            found.append(home)
    return list(dict.fromkeys(found))


def _tilde_targets(target: str, powershell: bool, environ: Mapping[str, str], windows: bool) -> list[str]:
    """A `~` write destination spelled from each home the shell may use; empty when it cannot be expanded.

    Only `~` alone or before a separator (`/`, and `\\` in PowerShell) is a home. `~user`, `~+`, `~-` and `~\\x` in
    bash name another user's home, the current or previous folder, or a quoted tilde: the gate does not read them.
    """
    rest = target[1:]
    if rest and not (rest[0] == "/" or powershell and rest[0] == "\\"):
        return []
    return [home.rstrip("/\\") + "/" + rest.lstrip("/\\") for home in _shell_homes(powershell, environ, windows)]


def _evaluate_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: PolicySettings,
    allowed_roots: Iterable[str] = (),
    workdir: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    windows: bool | None = None,
    workspace_operation: bool = False,
) -> Decision:
    environ = os.environ if environ is None else environ
    windows = os.name == "nt" if windows is None else windows
    if tool_name in NATIVE_DELEGATION_TOOLS:
        return Decision("deny", f"{tool_name} starts native delegation outside labhq accounting; use labhq orchestration")
    if tool_name in WRITE_LIKE:
        write_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if write_path and _drive_relative(write_path):
            return Decision("ask", f"drive-relative write path has no known base: {write_path}")
    rp = restricted_paths(policy)
    # Lexical first, then the real path: a link below an allowed folder can lead into a zone (#36).
    hit = touches(tool_input, rp, workdir=workdir) or touches_resolved(
        tool_input, [z.path for z in policy.data_zones if z.level == "restricted"], workdir=workdir)
    if isinstance(hit, Unresolved):
        # Free text opens paths only in a shell command, an MCP call or a Glob pattern; elsewhere it is file
        # content, a search pattern or a prompt, and the structured path fields were all resolved.
        if not (hit.path_field or tool_name in {"Bash", "PowerShell", "Glob"} or tool_name.startswith("mcp__")):
            hit = None
        else:
            shown = str(tool_input.get("command") or tool_input)[:200]
            return Decision("ask", f"{tool_name} names {hit}; links among them were not all resolved, so a "
                                   f"restricted zone may be reached: `{shown}`")

    if hit and tool_name in READ_LIKE | WRITE_LIKE:
        return Decision(
            "deny",
            f"'{hit}' is a restricted data zone. Do not read raw records into the conversation; "
            "submit an HPC job (hpc_submit) that writes aggregate/QC summaries and read those instead.",
        )

    # Internal-zone reads all use one approval flow. Output-shape classification only changes the card wording.
    internal_hit = _zone_hit(tool_input, policy, {"internal"}, workdir)
    if internal_hit and tool_name in READ_LIKE | SHELL_TOOLS:
        return Decision("ask", f"internal data zone read requires approval: {internal_hit}")

    if tool_name in {"Bash", "PowerShell"}:
        cmd = str(tool_input.get("command", ""))
        if hit:
            return Decision("ask", f"{tool_name} touches restricted zone {hit}: `{cmd[:200]}`")
        powershell = tool_name == "PowerShell"
        # Reuse the conservative #525/#535 syntax scanner: only proven data is blanked.
        # Executed strings, substitutions and unknown interpreters still use the raw command.
        ps_syntax = _blank_non_syntax(cmd, True) if powershell else None
        delete_text = ps_syntax if ps_syntax is not None else cmd
        patterns = policy.approvals.bash_ask_patterns if not powershell else POWERSHELL_ASK_PATTERNS
        delete_pattern = POWERSHELL_RECURSIVE_DELETE_PATTERN if powershell else BASH_RECURSIVE_DELETE_PATTERN
        roots = [_norm(r) for r in allowed_roots if r]
        # Claude's Bash on Windows is Git Bash: /c/Users/... is C:/Users/..., the folder the roots name (12th mock
        # trial: a write into the staff member's own .tmp asked the PI). A path it cannot map stays as written.
        git_bash = tool_name == "Bash" and any(re.match(r"^[a-z]:/", root) for root in roots)
        for pat in patterns:
            if pat == delete_pattern:
                # The pattern sees only rm's first option cluster; every option word of a real rm counts (#17), and
                # `docker rm` or `git rm` in its own tree is not one (#535 follow-up).
                recursive = _recursive_rm(cmd) if not powershell else re.search(pat, delete_text, re.IGNORECASE)
                if recursive and not workspace_operation and not _recursive_delete_inside_workdir(cmd, powershell, workdir):
                    return Decision("ask", f"재귀 삭제 확인 필요: `{cmd[:200]}`")
                # A deleting find meets the same `.tmp` rule (find -delete, -exec rm, | xargs rm).
                if not powershell and not all(_find_delete_inside_step_tmp(cmd, find, workdir)
                                              for find in _find_deletes(cmd)):
                    return Decision("ask", f"재귀 삭제 확인 필요: `{cmd[:200]}`")
                if _CMD_RECURSIVE_DELETE.search(delete_text):
                    return Decision("ask", f"재귀 삭제 확인 필요: `{cmd[:200]}`")
                # A listing piped into a delete, in a script block too, meets the same `.tmp` rule (#525 review). A
                # .NET delete (`.Delete($true)`, `[IO.Directory]::Delete(p, $true)`) names no literal path, so it
                # passes only as `$_.Delete()` in such a pipeline (#533).
                if (powershell and (_ps_piped_delete(delete_text) or _ps_method_delete(delete_text))
                        and not _ps_piped_delete_inside_step_tmp(cmd, workdir)):
                    return Decision("ask", f"재귀 삭제 확인 필요: `{cmd[:200]}`")
                continue
            if re.search(pat, cmd, re.IGNORECASE):
                return Decision("ask", f"위험 명령 확인 필요: `{cmd[:200]}`")
        for written in _shell_write_targets(cmd, powershell=tool_name == "PowerShell"):
            # A `~` destination is the shell's home (#521): judged there, or asked when it cannot be expanded.
            expanded = _tilde_targets(written, powershell, environ, windows) if written.startswith("~") else [written]
            if not expanded:
                return Decision("ask", f"shell write destination starts with a `~` the gate cannot expand: {written}")
            for target in expanded:
                if git_bash and _absolute(target) and not _drive_relative(target):
                    target = _git_bash_path(target, os.environ) or target
                if _drive_relative(target):
                    return Decision("ask", f"drive-relative shell write destination: {target}")
                if written.startswith("~") and not _absolute(target):
                    return Decision("ask", f"shell write destination starts with a `~` the gate cannot expand: "
                                           f"{written}")
                if _absolute(target) and not any(_inside(_norm(target), root) for root in roots):
                    shown = f"{written} (= {target})" if written.startswith("~") else target
                    return Decision("ask", f"shell write outside allowed roots: {shown}")
        return Decision("allow")

    if tool_name.startswith("mcp__"):
        if hit:
            return Decision("ask", f"MCP tool {tool_name} references restricted zone {hit}")
        return Decision("allow")

    roots = [_norm(r, expand_vars=False) for r in allowed_roots if r]
    if tool_name in WRITE_LIKE:
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if fp:
            if not _absolute(fp):
                if not workdir:
                    return Decision("ask", f"write path has no known workdir: {fp}")
                fp = posixpath.join(_norm(workdir, expand_vars=False), fp)
            if roots and not any(_inside(_norm(fp, expand_vars=False), r) for r in roots):
                return Decision("ask", f"write outside workspace/project dirs: {fp}")

    if tool_name in READ_LIKE | WRITE_LIKE or tool_name in policy.approvals.auto_allow_tools:
        return Decision("allow")
    # A tool with no rule here is not allowed by default (#421): Monitor runs a shell command, for one.
    return Decision("ask", f"{tool_name} is neither a shell, file nor MCP tool labhq checks, nor in "
                           "policy.approvals.auto_allow_tools (분류되지 않은 도구는 PI 승인 필요)")


def walltime_hours(walltime: str) -> float:
    """'HH:MM:SS', 'HH:MM', 'D-HH:MM:SS' or plain hours → hours."""
    w = walltime.strip()
    days = 0.0
    if "-" in w:
        d, w = w.split("-", 1)
        days = float(d)
    parts = [float(x) for x in w.split(":")]
    if len(parts) == 1:
        h = parts[0]
    elif len(parts) == 2:
        h = parts[0] + parts[1] / 60
    else:
        h = parts[0] + parts[1] / 60 + parts[2] / 3600
    return days * 24 + h


def core_hours(cores: int, walltime: str) -> float:
    return max(1, int(cores)) * walltime_hours(walltime)


def hpc_needs_approval(cores: int, walltime: str, policy: PolicySettings) -> bool:
    thr = policy.approvals.hpc_core_hours_threshold
    return thr <= 0 or core_hours(cores, walltime) > thr
