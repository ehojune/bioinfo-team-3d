"""Literal task-local shell operations. Unknown syntax keeps the normal approval gate."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
from pathlib import Path

from .private_paths import _managed_operand, credential_path, inside_any, resolved_read_path


def _python_paths(source: str) -> list[str] | None:
    """Inline data edits with literal open() paths; never execute code to classify it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    values = {}
    paths = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if any(m not in {'json', 'csv', 're', 'math', 'statistics'} for m in modules):
                return None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    # Reassignments are ambiguous, even when both values are literal.
                    if target.id in values:
                        return None
                    values[target.id] = node.value.value if isinstance(node.value, ast.Constant) else None
        if isinstance(node, (ast.Attribute, ast.Name)) and '__' in (node.attr if isinstance(node, ast.Attribute) else node.id):
            return None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name == 'open':
                    arg = node.args[0] if node.args else None
                    value = arg.value if isinstance(arg, ast.Constant) else values.get(arg.id) if isinstance(arg, ast.Name) else None
                    if not isinstance(value, str):
                        return None
                    paths.append(value)
                elif name not in {'print', 'len', 'str', 'int', 'float', 'range', 'enumerate', 'sorted', 'list', 'dict'}:
                    return None
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr not in {'load', 'loads', 'dump', 'dumps', 'get', 'items', 'keys', 'values',
                                          'append', 'extend', 'replace', 'strip', 'split', 'join', 'write', 'read'}:
                    return None
            else:
                return None
    # Also judge path-looking literals, including unused values: no private path is hidden in data.
    paths += [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and ('/' in n.value or '\\' in n.value or credential_path(n.value))]
    return paths


def workspace_command(command: str, workdir: str | None, private_paths=(), *, powershell=False,
                      read_roots=()) -> bool:
    """Prove every path stays in the current, existing task folder (never a read-only grant).

    The shell is not a sandbox. This exception deliberately accepts only a small literal grammar;
    dynamic commands, interpreters and scripts keep the existing approval behavior.
    """
    if not workdir or not Path(workdir).is_dir() or len(command) > 16000:
        return False
    root = os.path.abspath(workdir)
    try:
        if not inside_any(resolved_read_path(root), [root]):
            return False
    except (OSError, ValueError, RuntimeError):
        return False
    base = root
    variables: dict[str, str] = {}
    external_read = False
    writes = False

    def local(value: str, *, delete=False, reading=False) -> bool:
        nonlocal external_read
        roots = [root, *read_roots] if reading else [root]
        if reading and value.endswith(('/*', '/*.py')):
            directory, pattern = value.rsplit('/', 1)
            if not local(directory, reading=True):
                return False
            folder = Path(directory if os.path.isabs(directory) else os.path.join(base, directory))
            try:
                children = list(folder.glob(pattern))
                return len(children) <= 128 and all(local(str(child), reading=True) for child in children)
            except OSError:
                return False
        if not value or re.search(r'[$`*?\[\]{}~]|^[A-Za-z]:(?![/\\])', value):
            return False
        if '..' in value.replace('\\', '/').split('/'):
            return False
        raw = value if os.path.isabs(value) else os.path.join(base, value)
        try:
            real = resolved_read_path(raw)
            if not inside_any(os.path.abspath(raw), roots) or not inside_any(real, roots):
                return False
            if not _managed_operand(value, base, list(private_paths), roots):
                return False
            if not inside_any(os.path.abspath(raw), [root]):
                external_read = True
            if delete:
                if os.path.normcase(os.path.abspath(raw)) == os.path.normcase(root):
                    return False
                from .adapters.owned import is_link
                # Reject links even to another child: deletion must not follow a changed alias.
                current = Path(raw)
                while current != Path(root):
                    if os.path.lexists(current) and is_link(current):
                        return False
                    current = current.parent
            return True
        except (OSError, ValueError, RuntimeError):
            return False

    # A quoted Python heredoc is one interpreter input, not shell syntax.
    scripts = {}
    def heredoc(match):
        key = f'INLINEPY{len(scripts)}'
        scripts[key] = match.group(2)
        return f'python -c {key}'
    command = re.sub(r"python(?:3)? - <<'([A-Za-z_][A-Za-z_0-9]*)'\r?\n(.*?)\r?\n\1(?=\r?\n|$)",
                     heredoc, command, flags=re.S)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        lexer.commenters = ''
        if powershell:
            lexer.escape = ''
        tokens = list(lexer)
    except ValueError:
        return False
    parts, part = [], []
    for token in tokens:
        if token in {';', '&&', '|'}:
            if not part:
                return False
            parts.append(part)
            part = []
        else:
            part.append(token)
    if part:
        parts.append(part)
    if not parts or len(parts) > 32:
        return False
    # Newlines outside a quoted script require a fresh command; shlex loses that distinction.
    if '\n' in command or '\r' in command:
        return False
    for words in parts:
        while words and re.fullmatch(r'\$?[A-Za-z_][A-Za-z_0-9]*=.*', words[0], re.S):
            name, value = words.pop(0).split('=', 1)
            if not local(value, reading=True) or name.lstrip('$').upper() in {'HOME', 'PATH', 'USERPROFILE', 'PYTHONPATH'}:
                return False
            variables[name.lstrip('$')] = value
        if not words:
            continue
        def expand(match):
            return variables.get(match.group(1) or match.group(2), match.group(0))
        words = [re.sub(r'\$\{(\w+)\}|\$(\w+)', expand, word) for word in words]
        name, args = words[0].casefold(), words[1:]
        if name in {'python', 'python3'} and len(args) == 2 and args[0] == '-c':
            writes = True
            paths = _python_paths(scripts.get(args[1], args[1]))
            if paths is None or not all(local(p) for p in paths):
                return False
            continue
        if any(re.search(r'[$`{}()\n\r]', word) for word in words):
            return False
        # Redirections have their own path operands; descriptor duplication is left to the gate.
        for index in range(len(args) - 1, -1, -1):
            if args[index] in {'>', '>>', '<'}:
                writes = True
                if index + 1 >= len(args) or not local(args[index + 1]):
                    return False
                del args[index:index + 2]
        if any(set(word) <= set(';&|<>') for word in args):
            return False
        if name in {'cd', 'set-location'}:
            if len(args) != 1 or not local(args[0]):
                return False
            # A failed cd can leave the old cwd: relative operands must remain inside either way.
            base = os.path.abspath(os.path.join(base, args[0]))
        elif name in {'rm', 'remove-item'}:
            writes = True
            flags = {'-r', '-rf', '-fr', '-f', '--recursive', '--force', '-recurse', '-force', '-literalpath'}
            targets = [a for a in args if a.casefold() not in flags]
            if not targets or any(a.startswith('-') or not local(a, delete=True) for a in targets):
                return False
        elif name in {'cat', 'ls', 'head', 'tail', 'mkdir', 'touch', 'cp', 'mv'}:
            reading = name in {'cat', 'ls', 'head', 'tail'}
            writes = writes or not reading
            flags = {'-p', '-a', '-l', '-la', '-al', '-f', '--', '-r', '-R'}
            for index, arg in enumerate(args):
                if arg in flags or re.fullmatch(r'-\d+', arg):
                    continue
                if name in {'head', 'tail'} and (arg in {'-n', '-c'} or index and args[index-1] in {'-n', '-c'} and arg.lstrip('+').isdigit()):
                    continue
                if arg.startswith('-') or not local(arg, reading=reading):
                    return False
        elif name in {'grep', 'sed'}:
            flags = {'-n', '-E', '-nE', '-En', '-i'} if name == 'grep' else {'-n'}
            operands = args[:]
            while operands and operands[0] in flags:
                operands.pop(0)
            if not operands or operands[0].startswith('-'):
                return False
            pattern = operands.pop(0)
            if name == 'sed' and not re.fullmatch(r'\d+(?:,\d+)?p', pattern):
                return False
            if not all(local(arg, reading=True) for arg in operands):
                return False
        elif name == 'find':
            if not args or not local(args[0], reading=True) or (len(args) != 1 and not (
                    len(args) == 3 and args[1] == '-maxdepth' and args[2].isdigit())):
                return False
        elif name == 'pwd':
            if args:
                return False
        elif name == 'which':
            if not args or not all(re.fullmatch(r'[A-Za-z0-9_.+-]+', a) and not a.startswith('-') for a in args):
                return False
        elif name == 'invoke-webrequest' and powershell:
            writes = True
            if '-OutFile' not in args or '-Uri' not in args:
                return False
            index = 0
            while index < len(args):
                flag = args[index].casefold()
                if flag == '-usebasicparsing':
                    index += 1
                    continue
                if flag not in {'-uri', '-outfile'} or index + 1 == len(args):
                    return False
                value = args[index + 1]
                if flag == '-outfile' and not local(value):
                    return False
                if flag == '-uri' and not re.fullmatch(r'https?://[^\s]+', value):
                    return False
                index += 2
        else:
            return False
    return not (external_read and writes)


def script_approval_scope(tool: str, data: dict, workdir: str, private_paths, open_reads) -> tuple[str, str] | None:
    """An explicit, temporary grant for one unchanged script with task-local arguments.

    This is never automatic permission. The runner recomputes it for every call; a private/credential
    operand, changed script, other task, shell expression or different executable cannot reuse it.
    """
    from .tool_command import commands
    parts = commands(str(data.get('command', '')), bash=tool == 'Bash')
    if tool not in {'Bash', 'PowerShell'} or not parts or len(parts) != 1:
        return None
    words = parts[0]
    if words[:2] == ['uv', 'run']:
        prefix, args = 'uv run', words[2:]
    elif words and words[0] in {'python', 'python3'}:
        prefix, args = words[0], words[1:]
    else:
        return None
    if not args or not args[0].endswith('.py'):
        return None
    script = args[0] if os.path.isabs(args[0]) else os.path.join(workdir, args[0])
    if not _managed_operand(script, workdir, list(private_paths), list(open_reads)):
        return None
    if not inside_any(script, [workdir, *open_reads]):
        return None
    for value in args[1:]:
        if value.startswith('-'):
            if not re.fullmatch(r'--?[A-Za-z][A-Za-z0-9_-]*', value):
                return None
            continue
        if not workspace_command('cat ' + shlex.quote(value), workdir, private_paths):
            return None
    try:
        path = Path(script)
        if path.stat().st_size > 1_000_000:
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = '\0'.join((tool, prefix, resolved_read_path(script), digest))
    except (OSError, ValueError, RuntimeError):
        return None
    return hashlib.sha256(key.encode()).hexdigest(), f'{path.name} 실행 · 인자는 이 작업 폴더 안'
