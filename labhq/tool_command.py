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
