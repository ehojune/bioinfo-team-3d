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


def validate_runner_env(root, source, artifact, expected):
    """Validate a collected runner manifest and its local venv; do not expose an alias.

    ``expected`` must come from the pinned plan, never from the manifest itself. This is
    a validation primitive: the caller must keep the source protected for its lifetime.
    """
    root = Path(root).resolve(strict=True)
    source = Path(source).absolute()
    parts = source.relative_to(root).parts
    if not parts or source != source.resolve(strict=True):
        raise ValueError('environment source is not a plain workspace')
    if not isinstance(expected, dict):
        raise ValueError('pinned environment mapping is missing')
    runtime = expected.get('runtime')
    python = {'windows': '.venv/Scripts/python.exe', 'linux': '.venv/bin/python',
              'wsl': '.venv/bin/python'}.get(runtime if isinstance(runtime, str) else None)
    if python is None:
        raise ValueError('invalid pinned runtime')
    if (type(expected.get('version')) is not int or expected['version'] != 1 or
            expected.get('venv_path') != '.venv' or expected.get('python_path') != python):
        raise ValueError('invalid pinned environment contract')
    for key in ('logical_step_id', 'step_id'):
        value = expected.get(key)
        if not isinstance(value, str) or not INPUT_STEP_ID.fullmatch(value) or value != value.rstrip(' .'):
            raise ValueError('invalid pinned environment identity')
    runner = expected.get('runner_id')  # compared, never a path: any runner.id the settings accept
    if not isinstance(runner, str) or not 0 < len(runner) <= 256:
        raise ValueError('invalid pinned environment identity')
    path = 'outputs/env/runner-manifest.json'
    if path not in artifact.get('outputs', []):
        raise ValueError('collected environment manifest is missing')
    digest = artifact.get('output_sha256', {}).get(path)
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('environment manifest hash is missing')
    with _folder(root, (*parts, 'outputs', 'env')) as held:
        with os.fdopen(held.open_read_file('runner-manifest.json'), 'rb') as opened:
            if os.fstat(opened.fileno()).st_nlink != 1:
                raise ValueError('environment manifest is hard-linked')
            data = opened.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('environment manifest hash differs')
    manifest = json.loads(data)
    contract = {'version': 1, 'logical_step_id': expected['logical_step_id'],
                'step_id': expected['step_id'], 'runner_id': expected['runner_id'],
                'runtime': runtime, 'venv_path': '.venv', 'python_path': python}
    if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int or
            any(manifest.get(key) != value for key, value in contract.items())):
        raise ValueError('environment manifest differs from pinned plan')
    with _folder(root, (*parts, *python.split('/')[:-1])) as held:
        with os.fdopen(held.open_read_file(python.split('/')[-1]), 'rb') as opened:
            if os.fstat(opened.fileno()).st_nlink != 1:
                raise ValueError('environment interpreter is hard-linked')
    return source / '.venv'


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


def stage_inputs(ws, artifacts, workspace_root, zones, private_paths, max_file, max_total, remaining=None):
    """Return (read-only snapshot roots, failures). Metadata comes from the DAG's finished ancestors.

    ``remaining`` is the step's one cumulative byte budget (``[bytes left]``), shared with the precedent texts."""
    staged, refused = [], {}
    remaining = remaining if remaining is not None else [max_total]
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


PRECEDENT = 'precedent'  # inputs/precedent/: the request's precedent-search texts (#951); not a plan step id


def stage_precedent_texts(ws, rows, workspace_root, zones, max_file, max_total, taken_steps=(), remaining=None):
    """Copy the precedent search's stored texts into ``inputs/precedent/`` as read-only, hash-checked files.

    Returns (staged folder list, refusals by name, [(file name, row)] actually staged). A text is staged only when its
    absolute path is a plain file under the runner workspace, outside every data zone, its tool receipt beside it
    (``<text>.receipt.json``) names the same paper and hash, and the bytes match the declared hash. Anything else is
    refused with a reason and nothing partial is left."""
    from ..research.precedent_texts import file_name

    staged, refused, listing = [], {}, []
    remaining = remaining if remaining is not None else [max_total]
    if not rows:
        return staged, refused, listing
    if any(str(step).casefold() == PRECEDENT for step in taken_steps):
        return staged, {PRECEDENT: f'inputs/{PRECEDENT} unavailable (a plan step already uses that id)'}, listing
    root = Path(workspace_root).resolve(strict=True)
    files = []
    for index, row in enumerate(rows, 1):
        label = f"{PRECEDENT}:{row.get('paper_id') or index}"
        try:
            source = Path(row['text_file'])
            if not source.is_absolute():
                raise ValueError('stored text path is not absolute')
            source_parts = source.absolute().relative_to(root).parts
            if not source_parts or source != source.resolve(strict=True):
                raise ValueError('stored text is not a plain file in the runner workspace')
            if credential_path(str(source)) or overlaps_zone(source, zones):
                raise ValueError('stored text path is protected')
            expected = row['text_sha256']
            if not re.fullmatch('[a-f0-9]{64}', expected):
                raise ValueError('stored text hash is missing')
            with _folder(root, source_parts[:-1]) as held:
                original = _json(held, source_parts[-1] + '.receipt.json')
            if (not isinstance(original, dict) or original.get('text_sha256') != expected
                    or original.get('paper_id') != row.get('paper_id')):
                raise ValueError('tool receipt differs from the declared text')
            name = file_name(row, index)
            _parts(name)
            files.append((source_parts, name, expected, row))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            refused[label] = f'{label} unavailable ({detail})'
    if not files:
        return staged, refused, listing
    try:
        _reset(ws.dir, PRECEDENT, [name for _, name, _, _ in files])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        detail = (str(exc) if isinstance(exc, ValueError) else
                  'disk full during copy' if getattr(exc, 'errno', None) == errno.ENOSPC else type(exc).__name__)
        refused[PRECEDENT] = f'inputs/{PRECEDENT} unavailable ({detail})'
        return [], refused, []
    for parts, name, expected, row in files:
        # One bad copy omits only its own text; _copy removes its temporary file and never leaves a partial one.
        try:
            # Room is checked for this text alone, so one oversized text does not hide the ones that fit.
            if _size(root, parts) > min(max_file, remaining[0]):
                raise ValueError('input file size/link limit')
            _room(ws.dir, _size(root, parts))
            _copy(root, parts, ws.dir, f'inputs/{PRECEDENT}/{name}', expected, max_file, remaining)
            listing.append((name, row))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            detail = (str(exc) if isinstance(exc, ValueError) else
                      'disk full during copy' if getattr(exc, 'errno', None) == errno.ENOSPC else type(exc).__name__)
            refused[f"{PRECEDENT}:{row.get('paper_id') or name}"] = (
                f"{PRECEDENT}:{row.get('paper_id') or name} unavailable ({detail})")
    if refused and not _only_inventoried(ws.dir, [name for name, _ in listing]):
        # A failed copy left a file the inventory does not own (its temporary file could not be removed): no byte
        # that failed the hash check may be exposed, so clear the folder and refuse them all, or stop the step.
        try:
            _reset(ws.dir, PRECEDENT, [])
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as cleanup:
            raise RuntimeError('refused precedent text snapshot could not be cleared') from cleanup
        refused[PRECEDENT] = f'inputs/{PRECEDENT} unavailable (a failed copy left a file that could not be removed)'
        return [], refused, []
    if listing:
        staged.append(str(ws.dir / 'inputs' / PRECEDENT))
    return staged, refused, listing


def _only_inventoried(workdir, names):
    """Whether inputs/precedent holds nothing but the copies just staged and labhq's own inventory file."""
    with _folder(workdir, ('inputs', PRECEDENT)) as held:
        return set(_files(held)) <= {INVENTORY, *names}
