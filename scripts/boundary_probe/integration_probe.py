"""Exercise explicitly supplied stage-one modules; no real credentials or model calls."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import socket
import sys
from pathlib import Path


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
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


def run(boundary_path: Path, broker_path: Path, broker_kind="synthetic", expected_boundary=None, expected_broker=None):
    result = {"scope": "synthetic_unsealed_diagnostic", "protection_verified": False,
              "status": "inconclusive"}
    witness = {}
    paths = {"boundary": boundary_path, "broker": broker_path}
    expected = {"boundary": expected_boundary, "broker": expected_broker}
    before = {}
    try:
        if any(value is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", value) for value in expected.values()):
            result["reason"] = "invalid expected source hash"
            return result
        before = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
        result["sources"] = {name: {"before": digest, "expected": expected[name]} for name, digest in before.items()}
        result["source_mode"] = "pinned" if all(expected.values()) else "provisional"
        if any(value is not None and value.lower() != before[name] for name, value in expected.items()):
            result["reason"] = "expected source hash mismatch"
            return result
        boundary = load(boundary_path, "boundary_probe_actual_boundary")
        # Spawned upstream processes must be able to import the broker by name.
        broker_path = broker_path.resolve()
        sys.path.insert(0, str(broker_path.parent))
        broker = load(broker_path, broker_path.stem)
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

        receipt = boundary.diagnostic_broker_probe(factory, on_ready=ready).to_dict()
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
        result["run_id_matches"] = witness.get("run_id") == receipt.get("run_id") and bool(witness.get("run_id"))
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
    except (OSError, ValueError, AttributeError, ImportError, TypeError) as exc:
        result["reason"] = "module, protocol or prerequisite unavailable"
        result["error_type"] = type(exc).__name__
    finally:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary-module", type=Path, required=True)
    parser.add_argument("--broker-module", type=Path, required=True)
    parser.add_argument("--broker-kind", choices=("synthetic", "process"), default="synthetic")
    parser.add_argument("--expected-boundary-sha256")
    parser.add_argument("--expected-broker-sha256")
    args = parser.parse_args()
    print(json.dumps(run(args.boundary_module, args.broker_module, args.broker_kind,
                         args.expected_boundary_sha256, args.expected_broker_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
