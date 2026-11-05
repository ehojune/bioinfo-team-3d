"""Snapshot readable CI evidence; never generate coverage or PASS receipts."""
import argparse
import json
import os
from pathlib import Path

from scripts.ci_suite import StageDiagnostics

MAX_FILES = 512
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
CONSOLE_BYTES = 48 * 1024
TAIL_BYTES = 4096


def preserve(source, destination, *, emit=print):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('diagnostic staging must be separate from source')
    # A fresh destination prevents stale receipts from a previous attempt being uploaded.
    destination.mkdir(parents=True, exist_ok=False)
    result = dict(diagnostic_only=True, reusable=False,
                  context={key: os.environ.get(key) for key in
                           ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')},
                  copied=[], skipped=[], errors=[])
    remaining, total = CONSOLE_BYTES, 0

    def console(value):
        nonlocal remaining
        text = StageDiagnostics.TOKEN.sub('[redacted]', str(value))
        # JSON escaping prevents runner workflow commands/control characters in stack tails.
        line = json.dumps(text, ensure_ascii=True)
        if remaining:
            line = line[:remaining]
            emit('CI evidence: ' + line)
            remaining -= len(line)

    console(json.dumps(result['context'], sort_keys=True))
    if not source.is_dir():
        result['errors'].append(dict(path='.', error='source unavailable'))
    paths = source.rglob('*') if source.is_dir() else ()
    for index, path in enumerate(paths):
        if index >= MAX_FILES:
            result['errors'].append(dict(path='.', error='file enumeration limit'))
            break
        relative = path.relative_to(source).as_posix()
        if path.is_symlink() or path.resolve() != path.absolute():
            result['skipped'].append(dict(path=relative, reason='link'))
            continue
        if path.is_dir():
            continue
        if path.suffix in ('.lock', '.tmp'):
            result['skipped'].append(dict(path=relative, reason='live lock/temporary file'))
            continue
        target = destination / relative
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                raise ValueError('snapshot byte limit')
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copy exactly the observed size, so an active stack writer cannot extend the read forever.
            with path.open('rb') as reader, target.open('xb') as writer:
                left = size
                while left:
                    chunk = reader.read(min(left, 65536))
                    if not chunk:
                        raise OSError('source shortened during snapshot')
                    writer.write(chunk)
                    left -= len(chunk)
            total += size
            result['copied'].append(relative)
            if path.name.startswith(('checkpoints-', 'diagnostics-', 'lock-state-', 'stacks-')) or 'error' in path.name:
                with target.open('rb') as reader:
                    reader.seek(max(0, size - TAIL_BYTES))
                    content = reader.read(TAIL_BYTES).decode('utf-8', 'replace')
                if path.name.startswith('lock-state-'):
                    # Prefer the current owner/waiter over a tail cut through the history array.
                    try:
                        content = json.dumps(json.loads(target.read_text(encoding='utf-8'))['current'])
                    except (OSError, ValueError, KeyError):
                        pass
                console(relative + ' (snapshot/tail): ' + content)
        except (OSError, ValueError) as exc:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            row = dict(path=relative, error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            result['errors'].append(row)
            console(json.dumps(row, sort_keys=True))
    console('snapshot copied=' + str(len(result['copied'])) + ' errors=' + str(len(result['errors'])))
    (destination / 'failure-preservation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    try:
        preserve(args.source, args.destination)
    except (OSError, ValueError) as exc:
        # Report the diagnostic error without replacing a prior pytest/selection failure.
        print('CI evidence unavailable: ' + type(exc).__name__)


if __name__ == '__main__':
    main()
