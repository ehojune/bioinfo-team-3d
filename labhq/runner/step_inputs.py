"""Copy collected ancestor artifacts into task-local, gated read-only inputs."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from .. import disk_space
from ..adapters.held_dir import HeldDir
from ..adapters.owned import plain_directory, write_owned_bytes
from ..intake import overlaps_zone
from ..private_paths import _managed_operand, credential_path
from ..util import INPUT_STEP_ID, output_relpath

INVENTORY = '.labhq-inputs.json'
MAX_FILES = 200


def _parts(name):
    parts = str(name).split('/')
    if (not parts or any(p in {'', '.', '..', INVENTORY} or p != p.rstrip(' .') or
                        re.search(r'[\\:*?\[\]$`{}\x00-\x1f]', p) for p in parts)):
        raise ValueError('unsafe input filename')
    return parts


@contextlib.contextmanager
def _folder(root, parts):
    with contextlib.ExitStack() as stack:
        held = stack.enter_context(HeldDir.hold(root))
        for part in parts:
            child = stack.enter_context(held.child(part))
            if held.dev is not None and child.dev != held.dev:
                raise OSError('input crosses a mount')
            held = child
        yield held


def _json(held, name):
    with os.fdopen(held.open_read_file(name), 'rb') as opened:
        if os.fstat(opened.fileno()).st_nlink != 1:
            raise ValueError('input metadata is hard-linked')
        data = opened.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('input metadata exceeds limit')
    return json.loads(data)


def _files(held, prefix='', depth=0, budget=None):
    budget = [MAX_FILES * 16] if budget is None else budget
    if depth > 16:
        raise ValueError('input nesting exceeds limit')
    found = []
    entries = held.entries(MAX_FILES + 2)
    if len(entries) > MAX_FILES + 1:
        raise ValueError('too many input entries')
    for entry in entries:
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError('too many input entries')
        name = prefix + entry.name
        if entry.kind == 'dir':
            with held.child(entry.name, expect=entry.ident) as child:
                if held.dev is not None and child.dev != held.dev:
                    raise OSError('input crosses a mount')
                found.extend(_files(child, name + '/', depth + 1, budget))
        elif entry.kind == 'file':
            found.append(name)
        else:
            raise ValueError('input contains a link or special file')
        if len(found) > MAX_FILES + 1:
            raise ValueError('too many input files')
    return found


def _reset(workdir, step, names):
    relative = Path('inputs') / step
    if plain_directory(workdir, relative) is None:
        raise ValueError('input destination is not a plain folder')
    with _folder(workdir, relative.parts) as held:
        present = set(_files(held))
        old = _json(held, INVENTORY) if INVENTORY in present else []
    if not isinstance(old, list) or len(old) > MAX_FILES:
        raise ValueError('invalid input inventory')
    for name in old:
        _parts(name)
    if present - {INVENTORY} - set(old):
        raise ValueError('input folder contains files not staged by labhq')
    for name in old:
        parts = _parts(name)
        try:
            with _folder(workdir, (*relative.parts, *parts[:-1])) as parent:
                parent.unlink(parts[-1])
        except FileNotFoundError:
            pass  # A previous bounded copy may have stopped before creating this parent.
    # Record intended owned copies before writing: a partial copy can be retried safely.
    write_owned_bytes(workdir, (relative / INVENTORY).as_posix(), json.dumps(names).encode())


def _size(source, source_parts):
    with _folder(source, source_parts[:-1]) as src, \
            os.fdopen(src.open_read_file(source_parts[-1]), 'rb') as opened:
        return os.fstat(opened.fileno()).st_size


def _amount(size):
    for unit, scale in (('GiB', disk_space.GIB), ('MiB', 1024 ** 2)):
        if size >= scale:
            return f'{size / scale:.1f} {unit}'
    return f'{size} bytes'


def _room(workdir, needed):
    """Refuse a snapshot the destination volume cannot hold before any byte is copied, so a known-size copy never
    fills the drive the workspace (and often the gateway DB) shares (#678). Only a certain shortfall is refused:
    an unreadable probe leaves the bounded copy as before, and no reserve beyond the files themselves is assumed."""
    try:
        free = disk_space.usage(Path(workdir)).free
    except OSError:
        return
    if needed > free:
        raise ValueError(f'not enough disk space for inputs ({_amount(needed)} needed, {_amount(free)} free)')


def _copy(source, source_parts, workdir, destination, expected, limit, remaining):
    temporary = '.labhq-input-' + uuid.uuid4().hex
    if plain_directory(workdir, Path(destination).parent) is None:
        raise ValueError('input destination is not a plain folder')
    with _folder(source, source_parts[:-1]) as src, \
            _folder(workdir, Path(destination).parent.parts) as dst:
        try:
            with os.fdopen(src.open_read_file(source_parts[-1]), 'rb') as opened:
                info = os.fstat(opened.fileno())
                if info.st_nlink != 1 or info.st_size > min(limit, remaining[0]):
                    raise ValueError('input file size/link limit')
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
                with os.fdopen(dst.open_file(temporary, flags), 'wb') as copied:
                    digest, size = hashlib.sha256(), 0
                    while chunk := opened.read(1024 * 1024):
                        size += len(chunk)
                        remaining[0] -= len(chunk)
                        if size > limit or remaining[0] < 0:
                            raise ValueError('input grew beyond limit')
                        digest.update(chunk)
                        copied.write(chunk)
                    if digest.hexdigest() != expected:
                        raise ValueError('input hash differs from collected artifact')
                    copied.flush()
                    os.fsync(copied.fileno())
            dst.replace(temporary, Path(destination).name)
            return size
        finally:
            dst.unlink(temporary)


def stage_inputs(ws, artifacts, workspace_root, zones, private_paths, max_file, max_total):
    """Return (read-only snapshot roots, failures). Metadata comes from the DAG's finished ancestors."""
    staged, refused, remaining = [], {}, [max_total]
    root = Path(workspace_root).resolve(strict=True)
    for step, artifact in artifacts.items():
        owned = False
        try:
            if (not isinstance(step, str) or not INPUT_STEP_ID.fullmatch(step) or step != step.rstrip(' .') or
                    sum(str(s).casefold() == step.casefold() for s in artifacts) != 1):
                raise ValueError('invalid ancestor step id')
            # A refused ancestor must not expose an older snapshot either. Unknown/unowned files still refuse
            # this reset and are never removed. Once owned, every failure below clears all copies from this step.
            _reset(ws.dir, step, [])
            owned = True
            source = Path(artifact['workdir']).absolute()
            source_parts = source.relative_to(root).parts
            if not source_parts or source == ws.dir.resolve() or source != source.resolve(strict=True):
                raise ValueError('source is not a plain ancestor workspace')
            with _folder(root, source_parts) as held:
                manifest = _json(held, 'manifest.json')
            task = manifest.get('task') or {}
            source_task = artifact['task_id']
            if not isinstance(source_task, str) or not source_task.strip():
                raise ValueError('successful source task identity is missing')
            run = (manifest.get('runs') or {}).get(source_task) or {}
            if (not ws.task.request_id or task.get('request_id') != ws.task.request_id or
                    (task.get('meta') or {}).get('step_id') != step or run.get('ok') is not True or
                    run.get('request_id', task.get('request_id')) != ws.task.request_id or
                    run.get('step_id', (task.get('meta') or {}).get('step_id')) != step):
                raise ValueError('source is not a successful step of this request')
            outputs, hashes = artifact['outputs'], artifact['output_sha256']
            if not isinstance(outputs, list) or not outputs or len(outputs) > MAX_FILES:
                raise ValueError('invalid collected output list')
            files = []
            for raw in outputs:
                relative = output_relpath(raw)
                if not relative or not relative.startswith('outputs/'):
                    raise ValueError('invalid collected output path')
                name = relative[len('outputs/'):]
                _parts(name)
                expected = hashes.get(raw)
                if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                    raise ValueError('collected artifact hash is missing')
                if (credential_path(relative) or overlaps_zone(source / relative, zones) or
                        not _managed_operand(relative, str(source), private_paths, [str(source / 'outputs')])):
                    raise ValueError('artifact path is protected')
                files.append((relative, name, expected))
            if len({name.casefold() for _, name, _ in files}) != len(files):
                raise ValueError('collected input names collide')
            _room(ws.dir, sum(_size(root, (*source_parts, *_parts(relative))) for relative, _, _ in files))
            _reset(ws.dir, step, [name for _, name, _ in files])
            for relative, name, expected in files:
                _copy(root, (*source_parts, *_parts(relative)), ws.dir,
                      f'inputs/{step}/{name}', expected, max_file, remaining)
            staged.append(str(ws.dir / 'inputs' / step))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if owned:
                try:
                    _reset(ws.dir, step, [])
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as cleanup:
                    # Do not start a staff adapter when a rejected partial snapshot could still be read.
                    raise RuntimeError('refused ancestor input snapshot could not be cleared') from cleanup
            detail = (str(exc) if isinstance(exc, ValueError) else
                      'disk full during copy' if getattr(exc, 'errno', None) == errno.ENOSPC else type(exc).__name__)
            refused[str(step)] = f'inputs/{step} unavailable ({detail})'
    return staged, refused
