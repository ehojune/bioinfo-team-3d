"""Standalone, explicitly unsealed namespace diagnostic; not an engine launcher."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass


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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsealed-diagnostic", action="store_true")
    args = parser.parse_args()
    result = diagnostic_probe() if args.unsealed_diagnostic else protected_launch()
    print(json.dumps(result.to_dict(), sort_keys=True))
