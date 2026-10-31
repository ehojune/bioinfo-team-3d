"""Exercise explicitly supplied stage-one modules; no real credentials or model calls."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import signal
import tempfile
import uuid
from pathlib import Path


def collect_offline_contract(producer, factory, transport, collector, validator, *,
                             trusted, producer_key, observer_key, consumed):
    """Connect the producer API to separate injected fixture transports.

    This is an offline contract exercise, not a trusted launcher or observer.
    The transport calls diagnostic_broker_probe and returns (receipt, tag).
    The collector has arm(run, nonce), ready(run, nonce, path), and
    finish(run, nonce, outcome), returning (evidence, tag). Its independent
    ledger and expectations are supplied by the caller, not the producer.
    The caller owns deadlines and cleanup if an injected component hangs.
    """
    result = {"status": "inconclusive", "protection_verified": False,
              "cleanup_verified": False}
    run_id = trusted.get("run_id") if isinstance(trusted, dict) else None
    nonce = trusted.get("launch_nonce") if isinstance(trusted, dict) else None
    try:
        if str(uuid.UUID(run_id)) != run_id or not isinstance(nonce, str) or not nonce:
            return result
    except (TypeError, ValueError, AttributeError):
        return result
    if not isinstance(consumed, set):
        return result
    if (run_id, nonce) in consumed:
        return {**result, "contract": "rejected"}
    ready_count = 0
    ready_invalid = False
    outcome = "failed"
    receipt = tag = None
    armed = False
    evidence = observer_tag = None
    try:
        collector.arm(run_id, nonce)
        armed = True

        def ready(observed_run, path):
            nonlocal ready_count, ready_invalid
            ready_count += 1
            if observed_run != run_id or ready_count != 1:
                ready_invalid = True
                raise ValueError("invalid producer ready event")
            try:
                collector.ready(run_id, nonce, path)
            except Exception:
                ready_invalid = True
                raise

        receipt, tag = transport(producer.diagnostic_broker_probe, factory,
                                 on_ready=ready, run_id=run_id)
        outcome = "returned"
    except Exception:
        outcome = "failed"
    finally:
        if armed:
            try:
                evidence, observer_tag = collector.finish(run_id, nonce, outcome)
            except Exception:
                outcome = "failed"
    if outcome != "returned" or ready_count != 1 or ready_invalid:
        return result
    try:
        verdict = validator.adapt_producer(
            receipt, evidence=evidence, trusted=trusted,
            tags={"producer": tag, "observer": observer_tag},
            producer_key=producer_key, observer_key=observer_key, consumed=consumed)
        result.update(status=verdict.status, contract=verdict.contract,
                      reasons=list(verdict.reasons))
    except Exception:
        pass
    return result


def load(path: Path, name: str, source: bytes):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def request(path, obj):
    return request_raw(path, json.dumps(obj).encode() + b"\n")


def request_raw(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        conn.connect(str(path))
        conn.sendall(payload)
        data = bytearray()
        while b"\n" not in data and len(data) <= 65536:
            part = conn.recv(4096)
            if not part:
                raise ValueError("incomplete response")
            data.extend(part)
        return bytes(data)


def fake_upstream(body, secret):
    return secret if body == b"reflect" else b"ok"


def _run(boundary_path: Path, broker_path: Path, broker_kind="synthetic", expected_boundary=None, expected_broker=None):
    result = {"scope": "synthetic_unsealed_diagnostic", "protection_verified": False,
              "status": "inconclusive"}
    witness = {}
    nonce = str(uuid.uuid4())
    paths = {"boundary": boundary_path, "broker": broker_path}
    expected = {"boundary": expected_boundary, "broker": expected_broker}
    before = {}
    old_dont_write = os.environ.get("PYTHONDONTWRITEBYTECODE")
    cache = Path(importlib.util.cache_from_source(str(broker_path.resolve())))
    try:
        if any(value is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", value) for value in expected.values()):
            result["reason"] = "invalid expected source hash"
            return result
        source_bytes = {name: path.read_bytes() for name, path in paths.items()}
        before = {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()}
        result["sources"] = {name: {"before": digest, "expected": expected[name]} for name, digest in before.items()}
        result["source_mode"] = "pinned" if all(expected.values()) else "provisional"
        if any(value is not None and value.lower() != before[name] for name, value in expected.items()):
            result["reason"] = "expected source hash mismatch"
            return result
        if broker_kind == "process":
            if cache.exists():
                result["reason"] = "broker bytecode cache present"
                return result
            os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        boundary = load(boundary_path, "boundary_probe_actual_boundary", source_bytes["boundary"])
        # Spawned upstream processes must be able to import the broker by name.
        broker_path = broker_path.resolve()
        sys.path.insert(0, str(broker_path.parent))
        broker = load(broker_path, broker_path.stem, source_bytes["broker"])
        if broker_kind not in ("synthetic", "process"):
            raise ValueError("unknown broker kind")
        def factory(directory, run_id):
            cls = broker.ProcessBroker if broker_kind == "process" else broker.SyntheticBroker
            return cls(directory, run_id, b"fake-only-secret", upstream=fake_upstream)

        def ready(run_id, path):
            witness.update(run_id=run_id, path=path)
            frame = lambda rid, body: {"run_id": rid, "body": base64.b64encode(body).decode("ascii")}
            rejection = b'{"error":"request rejected"}\n'
            acknowledgement = b'{"body":"YWNjZXB0ZWQ="}\n'
            witness["allowed"] = request(path, frame(run_id, b"hello")) == (acknowledgement if broker_kind == "process" else b'{"body":"b2s="}\n')
            witness["wrong_run_rejected"] = request(path, frame("wrong-run", b"hello")) == rejection
            witness["extra_destination_rejected"] = request(path, {**frame(run_id, b"hello"), "url": "https://invalid.example"}) == rejection
            malformed = {
                "invalid_json": b'{invalid}\n',
                "invalid_base64": json.dumps({"run_id": run_id, "body": "!!!"}).encode() + b"\n",
                "missing_field": json.dumps({"run_id": run_id}).encode() + b"\n",
                "extra_field": json.dumps({**frame(run_id, b"hello"), "extra": 1}).encode() + b"\n",
                "wrong_run": json.dumps(frame("wrong-run", b"hello")).encode() + b"\n",
            }
            allowed = acknowledgement if broker_kind == "process" else b'{"body":"b2s="}\n'
            witness["malformed"] = {name: {"rejected": request_raw(path, data) == rejection,
                                           "listener_alive": request(path, frame(run_id, b"hello")) == allowed}
                                    for name, data in malformed.items()}
            expected = acknowledgement if broker_kind == "process" else rejection
            witness["literal_reflection_control"] = request(path, frame(run_id, b"reflect")) == expected

        receipt = boundary.diagnostic_broker_probe(factory, on_ready=ready, run_id=nonce).to_dict()
        path = witness.pop("path", None)
        if path is not None:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                    conn.settimeout(2)
                    conn.connect(str(path))
                witness["reconnect_rejected"] = False
            except OSError:
                witness["reconnect_rejected"] = True
        result["witness"] = {key: value for key, value in witness.items() if key != "run_id"}
        result["receipt"] = {key: receipt.get(key) == value for key, value in {
            "preparation": "diagnostic_ready", "launch": "exited_0",
            "cleanup": "broker_closed_socket_absent; descendants_unverified",
            "seal_status": "unsealed"}.items()}
        result["run_id_matches"] = witness.get("run_id") == nonce == receipt.get("run_id")
        result["broker_kind"] = broker_kind
        checks = ("allowed", "wrong_run_rejected", "extra_destination_rejected",
                  "literal_reflection_control", "reconnect_rejected")
        if (all(witness.get(key) is True for key in checks)
                and all(all(outcome.values()) for outcome in witness.get("malformed", {}).values())
                and len(witness.get("malformed", {})) == 5
                and result["run_id_matches"]
                and receipt.get("preparation") == "diagnostic_ready"
                and receipt.get("launch") == "exited_0"
                and receipt.get("cleanup") == "broker_closed_socket_absent; descendants_unverified"
                and receipt.get("seal_status") == "unsealed"):
            result["status"] = "pass"
    except Exception as exc:
        result["reason"] = "module, protocol or prerequisite unavailable"
        result["error_type"] = type(exc).__name__
    finally:
        if old_dont_write is None:
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        else:
            os.environ["PYTHONDONTWRITEBYTECODE"] = old_dont_write
        if broker_kind == "process" and cache.exists():
            result["status"] = "inconclusive"
            result["reason"] = "broker bytecode cache present"
        if before:
            for name, path in paths.items():
                try:
                    after = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    after = None
                result["sources"][name]["after"] = after
                if after != before[name]:
                    result["status"] = "inconclusive"
                    result["reason"] = "source changed during diagnostic"
    return result


def run(boundary_path: Path, broker_path: Path, broker_kind="synthetic", expected_boundary=None, expected_broker=None, timeout=30):
    """Bound the entire diagnostic, including imports, startup and cleanup."""
    with tempfile.TemporaryDirectory(prefix="boundary-diagnostic-") as directory:
        return _bounded_worker(boundary_path, broker_path, broker_kind, expected_boundary, expected_broker, timeout, directory)


def _bounded_worker(boundary_path, broker_path, broker_kind, expected_boundary, expected_broker, timeout, directory):
    args = [sys.executable, str(Path(__file__).resolve()), "--worker",
            "--boundary-module", str(boundary_path.resolve()), "--broker-module", str(broker_path.resolve()),
            "--broker-kind", broker_kind]
    for option, value in (("--expected-boundary-sha256", expected_boundary), ("--expected-broker-sha256", expected_broker)):
        if value is not None:
            args.extend((option, value))
    options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update(PATH=os.defpath, HOME=directory, USERPROFILE=directory, TEMP=directory, TMP=directory,
               TMPDIR=directory, PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, **options)
    try:
        output, _ = process.communicate(timeout=timeout)
        if process.returncode != 0:
            raise ValueError("worker failed")
        return json.loads(output)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        return {"scope": "synthetic_unsealed_diagnostic", "status": "inconclusive",
                "protection_verified": False, "reason": "diagnostic deadline exceeded",
                "worker_terminated": process.poll() is not None, "descendants_terminated": "unverified"}
    except (ValueError, json.JSONDecodeError):
        return {"scope": "synthetic_unsealed_diagnostic", "status": "inconclusive",
                "protection_verified": False, "reason": "diagnostic worker failed"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary-module", type=Path, required=True)
    parser.add_argument("--broker-module", type=Path, required=True)
    parser.add_argument("--broker-kind", choices=("synthetic", "process"), default="synthetic")
    parser.add_argument("--expected-boundary-sha256")
    parser.add_argument("--expected-broker-sha256")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    execute = _run if args.worker else run
    print(json.dumps(execute(args.boundary_module, args.broker_module, args.broker_kind,
                         args.expected_boundary_sha256, args.expected_broker_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
