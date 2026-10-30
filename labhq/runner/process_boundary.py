"""Standalone, explicitly unsealed namespace diagnostic; not an engine launcher."""

from __future__ import annotations

import os
import json
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Receipt:
    version: int
    run_id: str
    snapshot_digest: str | None
    runtime_digest: str | None
    policy_digest: str | None
    supervisor_identity: str | None
    child_identity: str | None
    namespace_identities: dict
    preparation: str
    launch: str
    cleanup: str
    seal_status: str
    unsupported_reasons: list[str]

    def to_dict(self):
        return asdict(self)


def protected_launch(*, supervisor=None, snapshot=None):
    """Fail closed until an independently verified supervisor/storage exists.

    Caller assertions, objects and digests cannot establish authority.
    """
    return Receipt(1, str(uuid.uuid4()), None, None, None, None, None, {},
                   "unsupported", "not_started", "not_needed", "unsupported",
                   ["trusted supervisor and sealed storage verification unavailable"])


def diagnostic_probe(*, timeout=10):
    """Run only a fixed non-model probe. Host runtime is unsealed.

    Never pass user-supplied commands, input, credentials, or sockets here.
    """
    receipt = Receipt(1, str(uuid.uuid4()), None, None, None, None,
                      None, {}, "unsupported", "not_started", "not_needed",
                      "unsealed", ["diagnostic only; runtime and storage are unsealed"])
    bwrap = shutil.which("bwrap")
    if os.name != "posix" or not bwrap or not os.path.isdir("/usr"):
        receipt.unsupported_reasons.append("Linux bubblewrap and /usr required")
        return receipt
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
               "--clearenv", "--ro-bind", "/usr", "/usr"]
    for path in ("/lib", "/lib64"):
        if os.path.isdir(path):
            command.extend(["--ro-bind", path, path])
    command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                    "/usr/bin/true"])
    receipt.preparation = "diagnostic_ready"
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, close_fds=True, timeout=timeout,
                                check=False)
        receipt.launch = "exited_0" if result.returncode == 0 else "failed"
        receipt.cleanup = "direct_child_reaped; descendants_unverified"
        if result.returncode:
            receipt.unsupported_reasons.append("bubblewrap diagnostic failed")
    except (OSError, subprocess.TimeoutExpired):
        receipt.launch = "failed"
        receipt.cleanup = "direct_child_reaped_or_not_started; descendants_unverified"
        receipt.unsupported_reasons.append("diagnostic could not complete")
    return receipt


_BROKER_CLIENT = '''
import base64, json, socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect('/run/labhq/provider.sock')
s.sendall(json.dumps({'run_id': sys.argv[1], 'body': base64.b64encode(b'probe').decode()}).encode() + b'\\n')
data = bytearray()
while b'\\n' not in data and len(data) <= 65536:
    chunk = s.recv(4096)
    if not chunk:
        break
    data.extend(chunk)
s.close()
response = json.loads(bytes(data))
assert set(response) == {'body'}
base64.b64decode(response['body'], validate=True)
'''


def _broker_prerequisites():
    return os.name == "posix" and bool(shutil.which("bwrap")) and os.path.isfile("/usr/bin/python3")


def diagnostic_broker_probe(broker_factory, *, timeout=10, on_ready=None):
    """Exercise a synthetic broker in an unsealed non-model namespace.

    The trusted factory must create only a synthetic broker; it receives a
    private directory and the receipt run ID. This is not a protected launch.
    """
    receipt = Receipt(1, str(uuid.uuid4()), None, None, None, None, None, {},
                      "unsupported", "not_started", "not_needed", "unsealed",
                      ["diagnostic only; runtime and storage are unsealed"])
    bwrap = shutil.which("bwrap")
    if not _broker_prerequisites():
        receipt.unsupported_reasons.append("Linux bubblewrap and /usr/bin/python3 required")
        return receipt
    with tempfile.TemporaryDirectory(prefix="labhq-boundary-") as directory:
        broker = None
        path = Path(directory) / "provider.sock"
        try:
            broker = broker_factory(Path(directory), receipt.run_id)
            broker.start()
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise ValueError("broker endpoint is not a socket")
            if on_ready is not None:
                on_ready(receipt.run_id, path)
            command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
                       "--clearenv", "--ro-bind", "/usr", "/usr"]
            for runtime_path in ("/lib", "/lib64"):
                if os.path.isdir(runtime_path):
                    command.extend(["--ro-bind", runtime_path, runtime_path])
            command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                            "--dir", "/run", "--dir", "/run/labhq",
                            "--bind", str(path), "/run/labhq/provider.sock",
                            "/usr/bin/python3", "-c", _BROKER_CLIENT, receipt.run_id])
            receipt.preparation = "diagnostic_ready"
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, close_fds=True, timeout=timeout,
                                    check=False)
            receipt.launch = "exited_0" if result.returncode == 0 else "failed"
            if result.returncode:
                receipt.unsupported_reasons.append("broker namespace diagnostic failed")
        except Exception:
            receipt.unsupported_reasons.append("broker diagnostic preparation or launch failed")
            if receipt.preparation == "diagnostic_ready":
                receipt.launch = "failed"
        finally:
            if broker is not None:
                try:
                    broker.close()
                    if path.exists() or path.is_symlink():
                        raise RuntimeError("socket remains")
                    receipt.cleanup = "broker_closed_socket_absent; descendants_unverified"
                except Exception:
                    receipt.cleanup = "failed; descendants_unverified"
                    receipt.unsupported_reasons.append("broker cleanup failed")
    return receipt


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsealed-diagnostic", action="store_true")
    args = parser.parse_args()
    result = diagnostic_probe() if args.unsealed_diagnostic else protected_launch()
    print(json.dumps(result.to_dict(), sort_keys=True))
