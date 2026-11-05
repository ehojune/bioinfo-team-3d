"""Standalone, explicitly unsealed namespace diagnostic; not an engine launcher."""

from __future__ import annotations

import os
import json
import hashlib
import hmac
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
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
    ancestry_observations: list = field(default_factory=list)

    def to_dict(self):
        result = asdict(self)
        if self.version == 1:
            del result["ancestry_observations"]
        return result


def protected_launch(*, supervisor=None, snapshot=None):
    """Fail closed until an independently verified supervisor/storage exists.

    Caller assertions, objects and digests cannot establish authority.
    """
    return Receipt(1, str(uuid.uuid4()), None, None, None, None, None, {},
                   "unsupported", "not_started", "not_needed", "unsupported",
                   ["trusted supervisor and sealed storage verification unavailable"])


class DiagnosticStager:
    """Temporary synthetic staging; no protection against local writers."""

    def __init__(self):
        self._runs = {}

    def stage(self, run_id, payloads):
        if run_id in self._runs or set(payloads) != {"snapshot", "runtime", "policy"}:
            raise ValueError("invalid or duplicate diagnostic run")
        directory = Path(tempfile.mkdtemp(prefix="labhq-stage-"))
        self._runs[run_id] = directory
        (directory / "source").mkdir()
        (directory / "source" / "tool.py").write_bytes(b"synthetic tool")
        digests = {}
        for name, content in payloads.items():
            if not isinstance(content, bytes):
                raise ValueError("diagnostic payload must be bytes")
            with (directory / name).open(mode="xb") as output:
                output.write(content)
            digests[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        return digests

    def observe_payload_digests(self, run_id):
        directory = self._runs[run_id]
        return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                for name in ("snapshot", "runtime", "policy")}

    def observe_ancestry(self, run_id):
        """Sample local lstat identities; these are unauthenticated observations."""
        root = self._runs[run_id]
        source = root / "source"
        leaf = source / "tool.py"

        def identity(path):
            info = path.lstat()
            result = {"object_id": f"{info.st_dev}:{info.st_ino}",
                      "kind": "directory" if stat.S_ISDIR(info.st_mode) else
                              "file" if stat.S_ISREG(info.st_mode) else "other",
                      "symlink": stat.S_ISLNK(info.st_mode), "nlink": info.st_nlink}
            if result["kind"] == "file":
                result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            return result

        root_id, source_id, leaf_id = map(identity, (root, source, leaf))
        return {"parent:root": {**root_id, "parent_ref": None, "parent_identity": None},
                "parent:source": {**source_id, "parent_ref": "parent:root", "parent_identity": root_id},
                "source:tool.py": {**leaf_id, "parent_ref": "parent:source", "parent_identity": source_id}}

    def cleanup(self, run_id):
        directory = self._runs.get(run_id)
        if directory is not None:
            shutil.rmtree(directory)
            del self._runs[run_id]


def diagnostic_staging_probe(stager, *, on_staged=None):
    """Exercise staging and cleanup with fixed synthetic bytes, never a seal.

    The injected stager is diagnostic code, not an authority. Its interface is
    stage, observe_payload_digests, observe_ancestry and cleanup accept the run ID;
    digest observations are untrusted.
    Cleanup is attempted even when staging partially fails. No child is launched.
    """
    receipt = Receipt(2, str(uuid.uuid4()), None, None, None, None, None, {},
                      "unsupported", "not_started", "not_needed", "unsealed",
                      ["diagnostic staging only; no trusted seal or child launch"])
    payloads = {"snapshot": b"synthetic input", "runtime": b"synthetic runtime",
                "policy": b"synthetic policy"}
    try:
        digests = stager.stage(receipt.run_id, payloads.copy())
        expected = {key: hashlib.sha256(value).hexdigest() for key, value in payloads.items()}
        if digests != expected:
            raise ValueError("staging digest mismatch")
        receipt.snapshot_digest = digests["snapshot"]
        receipt.runtime_digest = digests["runtime"]
        receipt.policy_digest = digests["policy"]
        receipt.ancestry_observations.append({"phase": "staged", "provenance": "stager_untrusted",
                                              "objects": stager.observe_ancestry(receipt.run_id)})
        if on_staged is not None:
            on_staged(receipt.run_id)
        if stager.observe_payload_digests(receipt.run_id) != digests:
            raise ValueError("staging digest changed after callback")
        receipt.ancestry_observations.append({"phase": "after_callback", "provenance": "stager_untrusted",
                                              "objects": stager.observe_ancestry(receipt.run_id)})
        observations = [entry["objects"] for entry in receipt.ancestry_observations]
        if not validate_synthetic_ancestry(observations[0], observations):
            raise ValueError("invalid or changed diagnostic ancestry")
        receipt.preparation = "diagnostic_staged"
    except Exception:
        receipt.unsupported_reasons.append("diagnostic staging failed")
    finally:
        try:
            stager.cleanup(receipt.run_id)
            receipt.cleanup = "stager_cleanup_returned; independent_cleanup_unverified"
        except Exception:
            receipt.cleanup = "failed; independent_cleanup_unverified"
            receipt.unsupported_reasons.append("diagnostic staging cleanup failed")
    return receipt


def validate_synthetic_ancestry(pins, phases):
    """Check the fixed v2 topology against separate pins; never grants protection."""
    edges = {"parent:root": None, "parent:source": "parent:root",
             "source:tool.py": "parent:source"}

    def valid(objects):
        if not isinstance(objects, dict) or set(objects) != set(edges):
            return False
        for role, parent in edges.items():
            obj = objects[role]
            if not isinstance(obj, dict) or obj.get("parent_ref", object()) != parent:
                return False
            identity = {k: v for k, v in obj.items() if k not in ("parent_ref", "parent_identity")}
            required = {"object_id", "kind", "symlink", "nlink"}
            if role == "source:tool.py":
                required.add("sha256")
            if set(identity) != required or not isinstance(identity["object_id"], str) or not identity["object_id"]:
                return False
            if identity["kind"] != ("file" if role == "source:tool.py" else "directory"):
                return False
            if identity["symlink"] is not False or type(identity["nlink"]) is not int or identity["nlink"] < 1:
                return False
            if role == "source:tool.py" and (identity["nlink"] != 1 or not isinstance(identity["sha256"], str)
                                               or len(identity["sha256"]) != 64
                                               or any(c not in "0123456789abcdef" for c in identity["sha256"])):
                return False
            expected = None if parent is None else {k: v for k, v in objects[parent].items()
                                                   if k not in ("parent_ref", "parent_identity")}
            if "parent_identity" not in obj or json.dumps(obj["parent_identity"], sort_keys=True) != json.dumps(expected, sort_keys=True):
                return False
        return len({objects[role]["object_id"] for role in edges}) == len(edges)

    try:
        return (valid(pins) and isinstance(phases, list) and bool(phases)
                and all(valid(phase) and json.dumps(phase, sort_keys=True) == json.dumps(pins, sort_keys=True)
                        for phase in phases))
    except (KeyError, TypeError, ValueError):
        return False


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


class DiagnosticLifecycleEvents:
    """One-use synthetic event signer; injected identity and key confer no trust.

    Implements the offline v1 contract pinned by observer commit f54642c3.
    ``identity_reader(pid)`` must return exactly pid and an incarnation birth_id.
    ``emit(event)`` must return after delivery or raise on failure.
    """

    def __init__(self, *, run_id, nonce, key_id, key, identity_reader, emit):
        if not all(isinstance(value, str) and value for value in (run_id, nonce, key_id)):
            raise ValueError("event run_id, nonce and key_id must be nonempty strings")
        if not isinstance(key, bytes) or not key:
            raise ValueError("event key must be nonempty bytes")
        if not callable(identity_reader) or not callable(emit):
            raise ValueError("identity_reader and emit are required")
        self._run_id, self._nonce, self._key_id = run_id, nonce, key_id
        self._key, self._identity_reader, self._emit = key, identity_reader, emit
        self._state = "new"
        self._identity = None

    def _claim_run(self, run_id):
        if self._state != "new" or run_id != self._run_id:
            raise ValueError("event stream is consumed or belongs to another run")
        self._state = "claimed"

    def _launch(self, pid):
        if self._state != "claimed":
            raise ValueError("event stream not claimed")
        self._state = "failed"
        identity = self._identity_reader(pid)
        if (not isinstance(identity, dict) or set(identity) != {"pid", "birth_id"}
                or type(identity["pid"]) is not int or identity["pid"] <= 0
                or identity["pid"] != pid or not isinstance(identity["birth_id"], str)
                or not identity["birth_id"]):
            raise ValueError("invalid process incarnation identity")
        self._identity = dict(identity)
        self._send("launch", 1)
        self._state = "launched"

    def _termination(self):
        if self._state != "launched":
            raise ValueError("launch event was not delivered")
        self._state = "failed"
        self._send("termination", 2)
        self._state = "terminated"

    def _send(self, kind, sequence):
        event = {"version": 1, "run_id": self._run_id, "nonce": self._nonce,
                 "sequence": sequence, "kind": kind, "identity": dict(self._identity)}
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        event["auth"] = {"key_id": self._key_id, "algorithm": "hmac-sha256",
                         "tag": hmac.new(self._key, payload, hashlib.sha256).hexdigest()}
        self._emit(event)


class _LifecycleFailure(Exception):
    def __init__(self, reasons):
        super().__init__("synthetic lifecycle failed")
        self.reasons = reasons


def _run_broker_with_events(command, *, timeout, lifecycle):
    """Record direct-child lifecycle only; never infer descendant cleanup."""
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, close_fds=True)
    launched = False
    reasons = []
    primary_error = None
    result = None
    try:
        lifecycle._launch(child.pid)
        launched = True
        stdout, stderr = child.communicate(timeout=timeout)
        result = subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
    except BaseException as exc:
        primary_error = exc
        reasons.append("BROKER_TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired) else
                       "LIFECYCLE_CHILD_FAILED" if launched else "LIFECYCLE_LAUNCH_FAILED")
        # Identity, authentication/delivery and timeout failures still reap our child.
        try:
            if child.poll() is None:
                child.kill()
        except Exception:
            reasons.append("LIFECYCLE_CHILD_KILL_FAILED")
        finally:
            try:
                child.wait(timeout=timeout)
            except Exception:
                reasons.append("LIFECYCLE_CHILD_REAP_FAILED")
    finally:
        try:
            if launched and child.returncode is not None:
                try:
                    lifecycle._termination()
                except Exception:
                    reasons.append("LIFECYCLE_TERMINATION_DELIVERY_FAILED")
        finally:
            for pipe in (child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()
    if primary_error is not None and not isinstance(primary_error, Exception):
        raise primary_error
    if reasons:
        raise _LifecycleFailure(reasons) from None
    return result


def diagnostic_broker_probe(broker_factory, *, timeout=10, on_ready=None, run_id=None, lifecycle=None):
    """Exercise a synthetic broker in an unsealed non-model namespace.

    The trusted factory must create only a synthetic broker; it receives a
    private directory and the receipt run ID. This is not a protected launch.
    Optional DiagnosticLifecycleEvents emits synthetic authenticated events,
    preserving the v1 receipt; no identity source or signing key is trusted here.
    """
    if run_id is None:
        run_id = str(uuid.uuid4())
    else:
        try:
            if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise ValueError("run_id must be a canonical UUID string") from exc
    receipt = Receipt(1, run_id, None, None, None, None, None, {},
                      "unsupported", "not_started", "not_needed", "unsealed",
                      ["diagnostic only; runtime and storage are unsealed"])
    if lifecycle is not None:
        if not isinstance(lifecycle, DiagnosticLifecycleEvents):
            raise ValueError("lifecycle must be DiagnosticLifecycleEvents")
        lifecycle._claim_run(run_id)
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
            try:
                if lifecycle is None:
                    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, close_fds=True, timeout=timeout,
                                            check=False)
                else:
                    result = _run_broker_with_events(command, timeout=timeout, lifecycle=lifecycle)
            except _LifecycleFailure as exc:
                receipt.launch = "failed"
                receipt.unsupported_reasons.extend(exc.reasons)
            except subprocess.TimeoutExpired:
                receipt.launch = "failed"
                receipt.unsupported_reasons.append("BROKER_TIMEOUT")
            else:
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


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsealed-diagnostic", action="store_true")
    args = parser.parse_args(argv)
    result = diagnostic_probe() if args.unsealed_diagnostic else protected_launch()
    print(json.dumps(result.to_dict(), sort_keys=True))
    success = (args.unsealed_diagnostic and result.preparation == "diagnostic_ready"
               and result.launch == "exited_0"
               and result.cleanup == "direct_child_reaped; descendants_unverified"
               and result.seal_status == "unsealed")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
