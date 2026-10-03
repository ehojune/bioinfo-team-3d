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
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import unquote, urlsplit

from .settings import SCHEDULER_JOB_COMMANDS, PolicySettings

READ_LIKE = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
WRITE_LIKE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Lexical prompts for common PowerShell hazards; this is not a command sandbox.
POWERSHELL_ASK_PATTERNS = (
    # Remove-Item and all its aliases; PowerShell accepts any prefix of -Recurse (-r, -re, …) and unix-style -rf.
    r"\b(?:Remove-Item|rm|ri|del|erase|rd|rmdir)\b[^;|\n]*\s-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|rf|fr)\b",
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


def _data_command(name: str, args: list[str], powershell: bool) -> str | None:
    """'data' if `name` reads its quoted arguments as text, 'inline' for an interpreter's inline code, else None."""
    base = re.split(r"[\\/]", name)[-1].casefold()
    base = base[:-4] if base.endswith(".exe") else base
    if _INTERPRETER.fullmatch(base):
        for arg in args:
            if arg in _INLINE_CODE_FLAGS:
                break
            if arg == "-m" or not arg.startswith("-"):
                return None  # a module or script file may do anything with its arguments
        return "inline"
    if name.casefold() not in (base, base + ".exe"):
        return None  # './cat' or 'C:\x\echo.exe' may be any program
    if base == "git":
        return "data" if args and args[0] in _GIT_DATA_SUBCOMMANDS else None  # not 'git -c alias.x=!..'
    if base == "printf" and not powershell and any(arg.startswith("-v") for arg in args):
        return None  # 'printf -v' assigns a variable
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
        kind = _data_command(word.group(), [w.group() for w in words[k + 1:]], powershell)
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


def _named_write_targets(words: list[str]) -> Iterator[str]:
    if not words:
        return
    name = words[0].casefold()
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
        segments = [[m.group().strip("\"'") for m in _SHELL_WORD.finditer(segment)]
                    for segment in re.split(r"[|;&\n]", unredirected(command))]
    else:
        words_text = unredirected(text)
        segments = [[command[m.start():m.end()].strip("\"'")
                     for m in _BARE_WORD.finditer(words_text, seg.start(), seg.end())]
                    for seg in re.finditer(r"[^|;&\n]+", words_text)]
    for words in segments:
        yield from _named_write_targets(words)


@dataclass
class Decision:
    action: str  # allow | deny | ask
    reason: str = ""
    # The tool input the decision was made on, when it is not the input Claude sent (#219). The gate must answer
    # with it (updatedInput), so the file Claude writes is the one that was judged.
    updated_input: dict | None = None


def _norm(p: str, *, expand_vars: bool = True) -> str:
    """Lexically normalize both POSIX and Windows paths, regardless of the host OS."""
    p = os.path.expanduser(p)
    if expand_vars:
        p = os.path.expandvars(p)
    if re.match(r"^[A-Za-z]:[/\\]", p) or p.startswith(("\\\\", "//")):
        return ntpath.normpath(p).replace("\\", "/").casefold()
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
        chunk_end = chunk_start
        while chunk_end < length and token[chunk_end] not in _CHUNK_BREAKS:
            chunk_end += 1
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
            part_start = colon + 1
            while part_start < right and token[part_start] == ":":
                part_start += 1
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


def _real(p: str) -> str | None:
    try:
        return _norm(os.path.realpath(os.path.expandvars(os.path.expanduser(p))))
    except (OSError, ValueError):
        return None


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
    for s, path_field in strings:
        for token in (s,) if path_field else _scan_path_text(s).candidates:
            if not token or _drive_relative(token):
                continue
            if not _absolute(token):
                if not workdir:
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
    for p in restricted_paths(policy):
        for tool in ("Read", "Edit", "Write"):
            deny.append(f"{tool}(/{claude_rule_path(p)}/**)")
    return {"permissions": {"deny": deny}} if deny else {}


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


def claude_deny_private(settings: dict, paths: Iterable[str], home: str | None = None,
                        open_reads: Iterable[str] = ()) -> dict:
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
    holders = [p for p in ruled if holds_open_read(p, open_reads)]
    merged = claude_deny_links(settings, [p for p in ruled if p not in holders])
    if holders:
        rules = [f"{tool}(/{claude_rule_path(p)}{tail})" for p in holders
                 for tool in ("Edit", "Write") for tail in ("", "/**")]
        rules += [f"Read(/{claude_rule_path(entry)}{tail})" for p in holders
                  for entry in closed_entries(p, open_reads) for tail in ("", "/**")]
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
        for resolve in (os.path.abspath, os.path.realpath):
            try:
                rules.append(f"{tool}(/{claude_rule_path(resolve(root))}/**)")
            except (OSError, ValueError):
                continue
    return rules


def claude_allowed_tools(tools: Iterable[str], write_roots: Iterable[str | os.PathLike],
                         read_roots: Iterable[str | os.PathLike] | None = None) -> list[str]:
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
    """
    tools = list(tools)
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
                value = os.path.realpath(value)
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
) -> Decision:
    allowed_roots = list(allowed_roots)
    judged = claude_write_input(tool_name, tool_input, workdir, allowed_roots, windows=windows, environ=environ)
    # Every check reads a Git Bash command with its drive paths spelled the Windows way, zones and private paths
    # included: converting only the write targets let `cp /c/<zone>/raw /c/<root>/out` through (PR #364 review).
    checked = git_bash_command(tool_name, judged, allowed_roots, windows)
    decision = _evaluate_tool(tool_name, checked, policy, allowed_roots, workdir)
    # On with no active path (PR #327) still runs the registry check; a non-empty list alone also means on.
    private_paths = list(private_paths)
    private = (_private_decision(tool_name, checked, private_paths, workdir, home, environ, list(private_open_reads))
               if private_enabled or private_paths else None)
    # Only ever stricter: a deny stays a deny, and an ask is not turned into an allow.
    if private and decision.action != "deny" and (private.action == "deny" or decision.action == "allow"):
        decision = private
    if judged is not tool_input:
        decision.updated_input = judged
    return decision


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
                      open_reads: list[str] | None = None) -> Decision | None:
    """PI personal paths (policy.private_paths): file tools are denied, a shell command naming one asks the PI.

    While private paths are active Claude pre-approves no shell command and no read outside the task's folders
    (`claude_allowed_tools`), so this is where those calls are judged: the text is canonicalized (case-folded where
    the volume is, `~`, variables, admin shares, `\\\\?\\`) and paths are resolved (links, junctions, 8.3 names). A
    file tool's free text (content, a Grep pattern) is not a path being opened and is not checked; a Glob
    pattern's folder part is. Called while private paths are on, even with no active path: then only the
    registry check has anything to match.

    `open_reads` (#298 ⑤): a read tool may open a path inside one of these folders although a private path holds
    it, when every spelling of it (as written, `..` resolved, and its real path) is inside one. Shell and write
    tools are judged as before.
    """
    from .private_paths import holds_open_read, mentioned_private_path, open_read_allowed, path_field_text, \
        registry_access

    if tool_name in READ_LIKE | WRITE_LIKE:
        open_reads = (open_reads or []) if tool_name in READ_LIKE else []
        holders = [p for p in private_paths if holds_open_read(p, open_reads)]
        closed = [p for p in private_paths if p not in holders]

        def private(spelled: str) -> bool:
            if mentioned_private_path(spelled, closed, home, environ):
                return True
            return bool(holders and mentioned_private_path(spelled, holders, home, environ)
                        and not open_read_allowed(spelled, open_reads))

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
                    spellings.add(os.path.realpath(spelled))
                except (OSError, ValueError):
                    pass
            if any(private(s) for s in spellings):
                return Decision("deny", f"{tool_name} path is a PI personal path (policy.private_paths); it is "
                                        "outside every staff task. Do not open it; ask the CSO if the task needs it.")
        return None
    if tool_name in SHELL_TOOLS:
        cmd = str(tool_input.get("command", ""))
        if private_paths and mentioned_private_path(cmd, private_paths, home, environ):
            return Decision("ask", f"{tool_name} names a PI personal path (policy.private_paths): `{cmd[:200]}`")
        # A link in the workspace (`alias -> ~/.ssh`) names no private path; its real path does.
        hit = private_paths and touches_resolved({"command": cmd}, private_paths, workdir=workdir)
        if hit:
            return Decision("ask", f"{tool_name} reaches a PI personal path (policy.private_paths) through a link, or "
                                   f"its paths were not all resolved: `{cmd[:200]}`")
        if private_paths and _cd_reaches_private(cmd, private_paths, workdir, home, environ):
            return Decision("ask", f"{tool_name} changes into a folder from which it names a PI personal path "
                                   f"(policy.private_paths), or changes folder too often to judge: `{cmd[:200]}`")
        # The PI's GITHUB_TOKEN is stripped from staff env but stays readable in the user registry (#325).
        spelled = registry_access(cmd)
        if spelled:
            return Decision("ask", f"{tool_name} touches the registry (any registry access asks while private paths "
                                   f"are on; HKCU\\Environment holds the PI's tokens such as GITHUB_TOKEN; 레지스트리 "
                                   f"접근은 PI 승인 필요) via `{spelled[:80]}`: `{cmd[:200]}`")
    return None


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
    if len(tokens) > MAX_RESOLVED_CANDIDATES:
        return True
    for base in bases:
        spelled = [base, *(path_field_text(t, base) for t in tokens)]
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


def _evaluate_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: PolicySettings,
    allowed_roots: Iterable[str] = (),
    workdir: str | None = None,
) -> Decision:
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

    if tool_name in {"Bash", "PowerShell"}:
        cmd = str(tool_input.get("command", ""))
        if hit:
            return Decision("ask", f"{tool_name} touches restricted zone {hit}: `{cmd[:200]}`")
        patterns = (policy.approvals.bash_ask_patterns if tool_name == "Bash"
                    else POWERSHELL_ASK_PATTERNS)
        for pat in patterns:
            if re.search(pat, cmd, re.IGNORECASE):
                return Decision("ask", f"risky command (/{pat}/): `{cmd[:200]}`")
        roots = [_norm(r) for r in allowed_roots if r]
        # Claude's Bash on Windows is Git Bash: /c/Users/... is C:/Users/..., the folder the roots name (12th mock
        # trial: a write into the staff member's own .tmp asked the PI). A path it cannot map stays as written.
        git_bash = tool_name == "Bash" and any(re.match(r"^[a-z]:/", root) for root in roots)
        for target in _shell_write_targets(cmd, powershell=tool_name == "PowerShell"):
            if git_bash and _absolute(target) and not _drive_relative(target):
                target = _git_bash_path(target, os.environ) or target
            if _drive_relative(target):
                return Decision("ask", f"drive-relative shell write destination: {target}")
            if _absolute(target) and not any(_inside(_norm(target), root) for root in roots):
                return Decision("ask", f"shell write outside allowed roots: {target}")
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

    return Decision("allow")


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
