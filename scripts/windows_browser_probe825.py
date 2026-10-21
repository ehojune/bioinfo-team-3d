"""Exact synthetic demo test transport, only on an isolated Windows Actions VM."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

def main():
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Only isolated GitHub Windows Actions execution is authorized')
    node = shutil.which('node')
    if not node:
        raise SystemExit('Required Node is absent; diagnostic not executed')
    script = Path(__file__).resolve().parents[1] / 'tests/web_resizable_panels.cjs'
    start = time.monotonic()
    try:
        result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding='utf-8', timeout=30)
        data = {'status': 'returned', 'returncode': result.returncode, 'stdout': result.stdout[-16000:],
                'stderr': result.stderr[-16000:], 'elapsed_s': time.monotonic()-start}
        phases = [f'resizable browser: {viewport} {phase}'
                  for viewport in ('desktop', 'phone')
                  for phase in ('open', 'load', 'assertions', 'close', 'closed')]
        # An optional-browser skip is not a completed diagnostic.
        exercised = all(phase in result.stdout + '\n' + result.stderr for phase in phases)
        data['browser_exercised'] = exercised
        passed = result.returncode == 0 and 'fail 0' in result.stdout and exercised
    except subprocess.TimeoutExpired as error:
        def text(value):
            return (value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or ''))[-16000:]
        data = {'status': 'timeout', 'stdout': text(error.output), 'stderr': text(error.stderr),
                'elapsed_s': time.monotonic()-start}
        passed = False
    data.update(schema_version=1, input='synthetic built-in public demo', original_timeout_s=30,
                check_success=passed, product_success_not_inferred=True)
    Path('browser825-diagnostic.json').write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(data))
    return 0 if passed else 1

if __name__ == '__main__':
    raise SystemExit(main())
