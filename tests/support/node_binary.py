"""Find the `node` that tests run under the isolated test home (#1076).

`tests/conftest.py` points `LOCALAPPDATA` and `HOME` at a temporary folder for every test. A `node.cmd` shim first on
PATH (the Codex app's bundle shim on an agent PC) looks node up under `%LOCALAPPDATA%` and exits 1 with "node.exe not
found" there. A real `node.exe` does not care, so it is preferred. When the shim is all there is, it is run once with
the operator's own `LOCALAPPDATA` just to print the real node's path, and the tests use that path directly: the test
child never sees the operator's folder, so the isolation holds.
"""
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _operator_local_appdata():
    """Where the operator's LOCALAPPDATA can be: the profile's known folder, then the default under the real home."""
    found = []
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_LOCAL_APPDATA = 0x1c: the profile's real location, even when it is redirected.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x1C, None, 0, buffer) == 0 and buffer.value:
                found.append(buffer.value)
        except (AttributeError, OSError, ValueError):
            pass
    real_home = os.environ.get("LABHQ_TEST_REAL_HOME")
    if real_home:
        found.append(str(Path(real_home) / "AppData" / "Local"))
    return list(dict.fromkeys(found))


@functools.lru_cache(maxsize=1)
def node_binary():
    """A real node.exe when PATH has one; a shim is asked for the real path it launches; None when there is no node."""
    real = shutil.which("node.exe")
    if real:
        return real
    node = shutil.which("node")
    if not node or Path(node).suffix.lower() not in {".cmd", ".bat"}:
        return node
    for local in _operator_local_appdata():
        try:
            done = subprocess.run([node, "-p", "process.execPath"], capture_output=True, text=True, timeout=30,
                                  env={**os.environ, "LOCALAPPDATA": local})
        except (OSError, subprocess.SubprocessError):
            continue
        path = done.stdout.strip()
        if done.returncode == 0 and path and Path(path).is_file():
            return path
    return node
