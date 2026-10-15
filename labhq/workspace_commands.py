"""Literal task-local shell operations. Unknown syntax keeps the normal approval gate."""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
from pathlib import Path

from .private_paths import _managed_operand, credential_path, inside_any, resolved_read_path


def literal_commands(command: str, *, powershell=False):
    """Closed shell syntax shared by automatic permission and explicit script grants."""
    if not command or len(command) > 16000:
        return None, '빈 명령 또는 길이 한도 초과'
    # Reject even quoted occurrences: no expansion, interpreter or quoting emulation.
    if re.search(r'[$`{}()\n\r#~]', command) or '<<' in command:
        return None, '확장·치환·here 입력·복합 구문'
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        lexer.commenters = ''
        if powershell:
            lexer.escape = ''
        tokens = list(lexer)
    except ValueError:
        return None, '닫히지 않은 따옴표'
    parts, separators, part = [], [], []
    for token in tokens:
        if token in {';', '&&', '|'}:
            if not part:
                return None, '빈 명령'
            parts.append(part)
            separators.append(token)
            part = []
        elif token and set(token) <= set(';&|<>') and token not in {'>', '>>', '<'}:
            return None, '지원하지 않는 연산자'
        else:
            part.append(token)
    if not part or len(parts) >= 32:
        return None, '빈 명령 또는 명령 수 한도 초과'
    parts.append(part)
    for index, words in enumerate(parts):
        if re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0]):
            return None, 'prefix assignment 또는 assignment-only'
        if words[0].casefold() in {'eval', 'source', '.', 'exec'}:
            return None, '간접 명령 실행'
        if words[0].casefold() in {'cd', 'set-location'}:
            if index != 0 or any(s != '&&' for s in separators):
                return None, 'cd는 맨 앞 한 번과 && 연결만 허용'
    return parts, ''


def workspace_command_reason(command: str, workdir: str | None, private_paths=(), *,
                             powershell=False, read_roots=(), forbidden=()) -> str:
    parts, reason = literal_commands(command, powershell=powershell)
    if parts is None:
        return '문법 밖: ' + reason
    if _workspace_command(parts, workdir, private_paths, powershell=powershell,
                          read_roots=read_roots, forbidden=forbidden):
        return '문법 안: 리터럴 명령과 작업 경로 확인'
    return '문법 밖: 명령·인자 또는 실제 경로의 읽기/쓰기 범위를 증명하지 못함'


def workspace_command(command: str, workdir: str | None, private_paths=(), **kwargs) -> bool:
    return workspace_command_reason(command, workdir, private_paths, **kwargs).startswith('문법 안:')


def _workspace_command(parts, workdir: str | None, private_paths=(), *, powershell=False,
                      read_roots=(), forbidden=()) -> bool:
    """Prove every path stays in the current, existing task folder (never a read-only grant).

    The shell is not a sandbox. This exception deliberately accepts only a small literal grammar;
    dynamic commands, interpreters and scripts keep the existing approval behavior.
    """
    if not workdir or not Path(workdir).is_dir():
        return False
    root = os.path.abspath(workdir)
    try:
        if not inside_any(resolved_read_path(root), [root]):
            return False
    except (OSError, ValueError, RuntimeError):
        return False
    base = root
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
            if inside_any(os.path.abspath(raw), forbidden) or inside_any(real, forbidden):
                return False
            if not inside_any(os.path.abspath(raw), roots) or not inside_any(real, roots):
                return False
            if not _managed_operand(value, base, list(private_paths), roots):
                return False
            if not inside_any(os.path.abspath(raw), [root]):
                external_read = True
            if not reading:
                from .adapters.owned import is_link
                # Check all existing ancestors, including the parent of a new file.
                # POSIX directory nlink counts subdirectories; only files must have exactly one link.
                current = Path(raw)
                while True:
                    if os.path.lexists(current):
                        info = current.lstat()
                        if is_link(current) or (not stat.S_ISDIR(info.st_mode) and
                                (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                            return False
                    if current == Path(root):
                        break
                    if current.parent == current:
                        return False
                    current = current.parent
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

    for words in parts:
        name, args = words[0].casefold(), words[1:]
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
            # Only an initial cd followed by && reaches this point.
            base = os.path.abspath(os.path.join(base, args[0]))
        elif name in {'rm', 'remove-item'}:
            writes = True
            flags = {'-r', '-rf', '-fr', '-f', '--recursive', '--force', '-recurse', '-force', '-literalpath'}
            targets = [a for a in args if a.casefold() not in flags]
            if not targets or any(a.startswith('-') or not local(a, delete=True) for a in targets):
                return False
        elif name in {'cat', 'ls', 'head', 'tail', 'mkdir', 'touch', 'cp', 'mv'}:
            # Copy/move into directories has implicit destinations and recursive link semantics.
            if name in {'cp', 'mv'} and (len(args) != 2 or
                    not Path(os.path.join(base, args[0])).is_file() or
                    Path(os.path.join(base, args[1])).is_dir()):
                return False
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


def _tree_digest(root: Path) -> str:
    """Hash every name and byte, including helpers and hidden files; never follow aliases."""
    from .adapters.owned import is_link
    digest = hashlib.sha256()
    pending = [root]
    count = total = 0
    while pending:
        path = pending.pop()
        info = path.lstat()
        count += 1
        if count > 10000 or is_link(path):
            raise ValueError('linked or oversized skill tree')
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(8, 'big') + name)
        if stat.S_ISDIR(info.st_mode):
            digest.update(b'D')
            pending.extend(sorted(path.iterdir(), reverse=True))
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            total += info.st_size
            if total > 32_000_000:
                raise ValueError('oversized skill tree')
            content = path.read_bytes()
            digest.update(b'F' + len(content).to_bytes(8, 'big') + content)
        else:
            raise ValueError('non-regular skill file')
    return digest.hexdigest()


def script_approval_scope(tool: str, data: dict, workdir: str, private_paths, open_reads, *,
                          managed_roots=(), writable_roots=()) -> tuple[str, str] | None:
    """An explicit temporary grant for a runner-selected, staff-read-only managed skill tree.

    This is never automatic permission. The runner recomputes it for every call; a private/credential
    operand, changed script, other task, shell expression or different executable cannot reuse it.
    """
    parts, _ = literal_commands(str(data.get('command', '')), powershell=tool == 'PowerShell')
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
    try:
        # Read grants alone are not provenance. Only the runner supplies installation roots.
        roots = [Path(p).absolute() for p in managed_roots]
        writable = [workdir, *writable_roots]
        real_script = resolved_read_path(script)
        candidates = [p for p in roots if inside_any(script, [str(p)]) and
                      inside_any(real_script, [str(p)])]
        if len(candidates) != 1:
            return None
        tree = candidates[0]
        if any(inside_any(str(tree), [p]) or inside_any(p, [str(tree)]) for p in writable):
            return None
        if not inside_any(str(tree), private_paths) or not inside_any(str(tree), open_reads):
            return None
        # Reject aliases in the install path, not just aliases beneath it.
        from .adapters.owned import is_link
        if any(is_link(p) for p in (tree, *tree.parents)):
            return None
        if not Path(script).is_file():
            return None
    except (OSError, ValueError, RuntimeError):
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
        digest = _tree_digest(tree)
        key = '\0'.join((tool, prefix, real_script, str(tree), digest))
    except (OSError, ValueError, RuntimeError):
        return None
    return hashlib.sha256(key.encode()).hexdigest(), f'{path.name} 실행 · 관리 스킬 전체 고정 · 인자는 이 작업 폴더 안'
