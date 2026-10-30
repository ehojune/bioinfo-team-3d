"""Start Chrome once before the Windows browser test, so its cold start is paid and timed in a step of its own (#1107).

The first Chrome of a fresh Windows runner can need far longer than the 30 s the resizable-panels test allows its
start (DevToolsActivePort after ~10 s, then 20 s for the first page target; or no port at all in 30 s). That cold cost
is a property of the runner, not of the product, so it is not counted against the test: this step starts Chrome with the
test's own flags on an empty profile, waits for the DevTools port and a page target, prints how long each took, and
stops it. The test keeps its 30 s start limit for a warmed Chrome; the cold start is checked here against 60 s (the
driver's default START_MS), and a failure here names itself (docs/reference/ci-reuse.md).

    python scripts/ci_chrome_warmup.py          # exit 0 when warm or when no Chrome is installed, 1 on a failed start
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping

START_LIMIT_S = 60  # this step's own limit: the driver's default START_MS, so a first Chrome slower than that fails here
POLL_S = 0.05
CANDIDATES = ('C:/Program Files/Google/Chrome/Application/chrome.exe',
              'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
              '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser')
# The flags tests/support/demo_driver.cjs gives Chrome, so the warm-up touches the same files and code paths.
FLAGS = ('--headless=new', '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check',
         '--hide-scrollbars', '--force-color-profile=srgb', '--lang=ko-KR')


class WarmupError(RuntimeError):
    """The Chrome start did not finish: the message names the stage it stopped at."""


def find_chrome(environ: Mapping[str, str] = os.environ, exists: Callable[[str], bool] = os.path.isfile) -> str | None:
    for path in (environ.get('LABHQ_TEST_CHROME'), *CANDIDATES):
        if path and exists(path):
            return path
    return None


def _stop(process) -> None:
    """End Chrome and everything it started: on Windows `taskkill /T`, elsewhere the process itself."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=30, check=False)
        else:
            process.kill()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _targets(port: str, timeout: float = 5) -> list:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=timeout) as response:  # loopback DevTools only
        return json.loads(response.read(200_000).decode('utf-8'))


def warm(chrome: str, *, limit_s: float = START_LIMIT_S, environ: Mapping[str, str] = os.environ, popen=subprocess.Popen,
         clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
         targets: Callable[[str], list] = _targets, stop: Callable = _stop, make_profile=tempfile.mkdtemp) -> dict:
    """Start `chrome` on an empty profile and return how long the DevTools port and the first page target took (s).

    Raises WarmupError when Chrome exits, or the port or the page target is not there within `limit_s`. Chrome and its
    profile are removed either way."""
    profile = make_profile(prefix='labhq-warmup-')
    args = [chrome, *FLAGS, f'--user-data-dir={profile}', *(['--no-sandbox'] if environ.get('CI') else []), 'about:blank']
    process = popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = clock()
    port = None
    try:
        while port is None:
            if process.poll() is not None:
                raise WarmupError(f'Chrome exited with code {process.poll()} before it opened its DevTools port')
            try:
                with open(os.path.join(profile, 'DevToolsActivePort'), encoding='utf-8') as handle:
                    port = handle.read().split('\n')[0].strip() or None
            except OSError:
                pass
            if port is None:
                if clock() - started >= limit_s:
                    raise WarmupError(f'no DevToolsActivePort within {limit_s:g} s')
                sleep(POLL_S)
        port_s = clock() - started
        while True:
            if process.poll() is not None:
                raise WarmupError(f'Chrome exited with code {process.poll()} before it listed a page target')
            try:
                if any(target.get('type') == 'page' for target in targets(port)):
                    break
            except (OSError, ValueError):
                pass
            if clock() - started >= limit_s:
                raise WarmupError(f'no page target within {limit_s:g} s (DevToolsActivePort took {port_s:.2f} s)')
            sleep(POLL_S)
        target_s = clock() - started
        if target_s > limit_s:  # a page found after the limit is a late start, however it was found
            raise WarmupError(f'first page target after {target_s:.1f} s, over the {limit_s:g} s limit '
                              f'(DevToolsActivePort took {port_s:.2f} s)')
        return {'port_s': round(port_s, 2), 'target_s': round(target_s, 2)}
    finally:
        stop(process)
        shutil.rmtree(profile, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    chrome = find_chrome()
    if chrome is None:
        print('::notice title=Chrome warm-up::no Chrome found; the browser test reports its own skip or failure')
        return 0
    began = time.monotonic()
    try:
        result = warm(chrome)
    except WarmupError as error:
        print(f'::error title=Chrome warm-up failed::{error} (after {time.monotonic() - began:.1f} s)')
        return 1
    print(f"chrome warm-up: DevToolsActivePort {result['port_s']} s, first page target {result['target_s']} s, "
          f'step {time.monotonic() - began:.1f} s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
