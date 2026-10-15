"""Literal task-local shell operations. Unknown syntax keeps the normal approval gate."""
from __future__ import annotations

import os
import re
import shlex
import stat
from pathlib import Path

from .private_paths import _managed_operand, credential_path, inside_any, resolved_read_path

_LITERAL_COMMANDS = {'cd', 'set-location', 'rm', 'remove-item', 'cat', 'ls', 'head', 'tail',
                     'mkdir', 'touch', 'cp', 'mv', 'grep', 'sed', 'find', 'pwd', 'which',
                     'invoke-webrequest'}


def _system_bins():
    if os.name == 'nt':
        return [Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32',
                Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin']
    return [Path('/usr/bin'), Path('/bin')]


def _executable_writable(path: Path) -> bool:
    """Check the staff token without modifying the executable or its directories."""
    if os.name != 'nt':
        return os.access(path, os.W_OK)
    # os.access(W_OK) on Windows checks the readonly attribute, not the DACL.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Write/add, append/add-subdirectory, delete, change DACL/owner, delete children.
    rights = [2, 4, 0x10000, 0x40000, 0x80000] + ([0x40] if path.is_dir() else [])
    for right in rights:
        handle = create(str(path), right, 7, None, 3, 0x02000000, None)
        if handle != wintypes.HANDLE(-1).value:
            kernel.CloseHandle(handle)
            return True
        if ctypes.get_last_error() != 5:  # Unknown access failures cannot prove immutability.
            return True
    return False


def _resolve_executable(name, base, environ, powershell):
    """Resolve once against the inherited staff PATH; never execute a workspace candidate."""
    builtins = ({'cd', 'pwd', 'set-location', 'remove-item', 'invoke-webrequest',
                 'rm', 'cat', 'ls', 'cp', 'mv', 'mkdir'} if powershell else {'cd', 'pwd'})
    if name in builtins:
        return True, name + '=shell builtin'
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', name):
        return False, name + '=unresolved'
    # Bash is case-sensitive, including its command lookup on POSIX.
    extensions = ['', '.exe'] if os.name == 'nt' else ['']
    if powershell and os.name == 'nt':
        extensions = ['', '.ps1', *environ.get('PATHEXT', '.COM;.EXE;.BAT;.CMD').lower().split(';')]
    search = next((v for k, v in environ.items() if k.upper() == 'PATH'), '')
    for folder in search.split(os.pathsep):
        directory = Path(os.path.join(base, folder))
        for extension in extensions:
            candidate = directory / (name + extension)
            if not candidate.is_file() or (os.name != 'nt' and not os.access(candidate, os.X_OK)):
                continue
            try:
                real = candidate.resolve(strict=True)
                trusted = next((p.resolve() for p in _system_bins()
                                if real.is_relative_to(p.resolve())), None)
                if trusted is None or (os.name == 'nt' and real.suffix.lower() != '.exe'):
                    return False, name + '=' + str(real) + ' (non-system)'
                for target in (real, *real.parents):
                    if _executable_writable(target):
                        return False, name + '=' + str(real) + ' (writable)'
                    if target == trusted:
                        break
                return True, name + '=' + str(real)
            except (OSError, ValueError, RuntimeError):
                return False, name + '=unresolved'
    return False, name + '=not found'


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
                             powershell=False, read_roots=(), forbidden=(), environ=None) -> str:
    parts, reason = literal_commands(command, powershell=powershell)
    if parts is None:
        return '문법 밖: ' + reason
    if powershell:
        for words in parts:
            for operand in words[1:]:
                if ':' not in operand or re.fullmatch(r'https?://[^\s]+', operand):
                    continue
                # PSDrives, providers, drive-relative paths and alternate data streams are unknown.
                try:
                    if (os.name == 'nt' and re.match(r'^[A-Za-z]:[/\\]', operand)
                            and ':' not in operand[2:] and workdir
                            and inside_any(resolved_read_path(operand), [workdir])):
                        continue
                except (OSError, ValueError, RuntimeError):
                    pass
                return '문법 밖: PowerShell provider·콜론 operand의 작업 경로를 증명하지 못함'
    evidence = []
    accepted = _workspace_command(parts, workdir, private_paths, powershell=powershell,
                    read_roots=read_roots, forbidden=forbidden,
                    environ=os.environ if environ is None else environ, evidence=evidence)
    reason = ('문법 안: 리터럴 명령과 작업 경로 확인' if accepted else
              '문법 밖: 명령·인자 또는 실제 경로의 읽기/쓰기 범위를 증명하지 못함')
    if any(not trusted for trusted, _ in evidence):
        reason = '문법 밖: 실행 파일이 쓰기 불가한 시스템 명령임을 증명하지 못함'
    return reason + (' · 명령 해석: ' + '; '.join(detail for _, detail in evidence) if evidence else '')


def workspace_command(command: str, workdir: str | None, private_paths=(), **kwargs) -> bool:
    return workspace_command_reason(command, workdir, private_paths, **kwargs).startswith('문법 안:')


def _workspace_command(parts, workdir: str | None, private_paths=(), *, powershell=False,
                      read_roots=(), forbidden=(), environ=None, evidence=None) -> bool:
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

    resolutions = {}
    for words in parts:
        name, args = words[0].casefold(), words[1:]
        if name not in _LITERAL_COMMANDS:
            return False
        key = (words[0].casefold() if powershell else words[0], base)
        if key not in resolutions:
            resolutions[key] = _resolve_executable(key[0], base, environ, powershell)
            evidence.append(resolutions[key])
        if not resolutions[key][0]:
            return False
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
