"""Small literal shell grammar. Unknown syntax never qualifies for the managed-read exception."""
from __future__ import annotations

import re
import shlex


def commands(command: str, *, bash: bool = True) -> list[list[str]] | None:
    if not command or len(command) > 16000 or re.search(r'[$`{}()<>\n\r*?\[\]#~]', command):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
        lexer.whitespace_split = True
        lexer.commenters = ''
        # POSIX escaping joins escaped spaces and adjacent quoted/unquoted fragments.
        if not bash:
            lexer.escape = ''  # PowerShell's literal Windows separators are not Bash escapes.
        tokens = list(lexer)
    except ValueError:
        return None
    result, part = [], []
    for token in tokens:
        if token in (';', '&&', '|'):
            if not part:
                return None
            result.append(part)
            part = []
        elif token and set(token) <= set(';&|'):
            return None
        else:
            part.append(token)
    if part:
        result.append(part)
    return result if result and len(result) <= 32 else None


def read_commands(command: str) -> list[tuple[str, list[str]]] | None:
    """Return only path operands; flag values and program names are not paths."""
    parts = commands(command)
    if parts is None:
        return None
    out = []
    for words in parts:
        name, args = words[0], words[1:]
        paths = []
        if name == 'cd':
            if len(args) != 1 or args[0].startswith('-'):
                return None
            paths = args
        elif name in ('which', 'pwd'):
            if (name == 'pwd' and args) or (name == 'which' and
                    (not args or any(not re.fullmatch(r'[A-Za-z0-9_.+-]+', a) or a.startswith('-') for a in args))):
                return None
        elif name in ('ls', 'cat', 'head', 'tail'):
            for arg in args:
                if arg.startswith('-'):
                    valid = (name == 'ls' and re.fullmatch(r'-[ladlh1]+', arg) or
                             name in ('head', 'tail') and re.fullmatch(r'-\d+', arg))
                    if not valid:
                        return None
                else:
                    paths.append(arg)
            if name == 'ls' and not paths:
                paths = ['.']
        elif name == 'find':
            # No -L, -exec, -delete, -fprint, predicates, or shell-expanded paths.
            if len(args) not in (1, 3) or args[0].startswith('-'):
                return None
            if len(args) == 3 and (args[1] != '-maxdepth' or not args[2].isdigit()):
                return None
            paths = args[:1]
        else:
            return None
        out.append((name, paths))
    return out


def _head_count(args: list[str]) -> int | None:
    count = 10
    index = 0
    while index < len(args):
        arg = args[index]
        if re.fullmatch(r"-\d+", arg):
            count = int(arg[1:])
        elif arg in {"-n", "--lines"}:
            if index + 1 >= len(args) or not args[index + 1].isdigit():
                return None
            index += 1
            count = int(args[index])
        elif arg.startswith("--lines="):
            value = arg.partition("=")[2]
            if not value.isdigit():
                return None
            count = int(value)
        index += 1
    return count


def _interpreter_prints_rows(command: str) -> bool:
    """Conservative Python/R command check: uncertainty is treated as row output."""
    folded = command.casefold()
    row_access = re.search(r"(?:\.|\b)(?:head|tail)\s*\(|\biloc\s*\[|\bloc\s*\[", folded)
    if row_access:
        return True
    # Column names/schema are safe even if the library internally reads more than the header: only output matters.
    if re.search(r"\.columns\b|\.dtypes\b|\.info\s*\(|\b(?:names|colnames)\s*\(", folded):
        return False
    if re.search(r"\bnrows\s*=\s*0\b", folded):
        return False
    # Known aggregate expressions produce group summaries, not source rows.
    aggregates = (
        ".value_counts(", "pd.crosstab(", ".pivot_table(", ".describe(", "aggregate(", "table(",
        "summary(",
    )
    grouped = ".groupby(" in folded and re.search(r"\.(?:size|count|sum|mean|median|agg)\s*\(", folded)
    if grouped or any(token in folded for token in aggregates):
        return False
    return True


def command_prints_rows(command: str, *, powershell: bool = False) -> bool:
    """Whether a command may print source records rather than a header or aggregate.

    This is a card-label classifier, not an allow rule. False is reserved for narrow, recognizable
    metadata/count forms; opaque interpreters and ambiguous syntax return True.
    """
    folded = command.casefold()
    if not folded.strip():
        return False
    if powershell and re.search(r"(?<![\w-])(?:get-content|gc|type)(?![\w-])", folded):
        if re.search(r"\bmeasure-object\b", folded):
            return False
        counts = [int(value) for value in re.findall(r"-(?:totalcount|head|first)\s+(\d+)", folded)]
        return not (counts and max(counts) <= 1)

    # Interpreter commands are opaque unless they match a narrow schema/aggregate form above.
    if re.search(r"(?:^|[;&|]\s*)(?:python(?:\d+(?:\.\d+)*)?|py|rscript|r)(?:\.exe)?(?:\s|$)",
                 folded):
        return _interpreter_prints_rows(command)

    parsed = commands(command, bash=not powershell)
    if parsed is not None:
        for words in parsed:
            name = re.split(r"[\\/]", words[0])[-1].casefold()
            args = words[1:]
            if name == "head":
                if _head_count(args) != 1:
                    return True
            elif name in {"cat", "tail", "less", "more", "grep", "egrep", "fgrep", "cut"}:
                return True
            elif name == "wc":
                continue
            elif name == "sed":
                script = next((arg for arg in args if not arg.startswith("-")), "")
                if not ("-n" in args and re.fullmatch(r"\s*1p\s*", script)):
                    return True
            elif name in {"awk", "gawk", "mawk"}:
                script = next((arg for arg in args if not arg.startswith("-")), "")
                if "print" in script.casefold() and not (
                        "end" in script.casefold() and re.search(r"(?:\+\+|\+=|\bsum\b|\bcount\b)", script,
                                                                  re.IGNORECASE)):
                    return True
            elif name not in {"cd", "pwd", "ls", "find", "which", "echo", "printf", "stat", "du", "file",
                              "basename", "dirname"}:
                return True
        return False

    # The closed grammar rejected syntax. Recognize only the safe forms; ambiguity gets the warning.
    if re.search(r"(?:^|[;&|]\s*)wc\s+(?:-[a-z]*l[a-z]*\s+|--lines\s+)", folded):
        return False
    if re.search(r"(?:^|[;&|]\s*)head\s+(?:-1\b|-n\s+1\b|--lines(?:=|\s+)1\b)", folded):
        return False
    if (re.search(r"(?:^|[;&|]\s*)(?:awk|gawk|mawk)\b", folded)
            and re.search(r"\bend\b", folded) and "print" in folded
            and re.search(r"(?:\+\+|\+=|\bsum\b|\bcount\b)", folded)):
        return False
    if re.search(r"(?:^|[;&|]\s*)(?:cat|tail|less|more|grep|egrep|fgrep|cut|sed|awk)\b", folded):
        return True
    return True
