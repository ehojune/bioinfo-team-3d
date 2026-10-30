"""Find the `node` that tests run, and the env it needs, under the isolated test home (#1076).

`tests/conftest.py` points `LOCALAPPDATA` and `HOME` at a temporary folder for every test. A `node.cmd` shim first on
PATH (the Codex app's bundle shim on an agent PC) looks node up under `%LOCALAPPDATA%` and exits 1 with "node.exe not
found" there. A real `node.exe` does not care, so it is preferred. Only when the shim is all there is does its child get
the operator's own `LOCALAPPDATA`; the isolation of the test itself is not touched.
"""
import os
import shutil
from pathlib import Path


def node_binary():
    """A real node.exe when PATH has one, else whatever `node` resolves to; None when there is no node."""
    return shutil.which("node.exe") or shutil.which("node")


def node_env(node, env=None):
    """`env` (default: the current one) with the operator's `LOCALAPPDATA` restored when `node` is a .cmd/.bat shim."""
    env = dict(os.environ if env is None else env)
    real_home = os.environ.get("LABHQ_TEST_REAL_HOME")
    if node and real_home and Path(node).suffix.lower() in {".cmd", ".bat"}:
        env["LOCALAPPDATA"] = str(Path(real_home) / "AppData" / "Local")
    return env
