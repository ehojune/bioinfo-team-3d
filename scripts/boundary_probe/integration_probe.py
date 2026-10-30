"""Exercise explicitly supplied stage-one modules; no real credentials or model calls."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
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
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        conn.connect(str(path))
        conn.sendall(json.dumps(obj).encode() + b"\n")
        data = bytearray()
        while b"\n" not in data and len(data) <= 65536:
            part = conn.recv(4096)
            if not part:
                raise ValueError("incomplete response")
            data.extend(part)
        return bytes(data)


def run(boundary_path: Path, broker_path: Path, broker_kind="synthetic"):
    result = {"scope": "synthetic_unsealed_diagnostic", "protection_verified": False,
              "status": "inconclusive"}
    witness = {}
    try:
        boundary = load(boundary_path, "boundary_probe_actual_boundary")
        broker = load(broker_path, "boundary_probe_actual_broker")
        if broker_kind not in ("synthetic", "process"):
            raise ValueError("unknown broker kind")
        def upstream(body, secret):
            return secret if body == b"reflect" else b"ok"
        def factory(directory, run_id):
            cls = broker.ProcessBroker if broker_kind == "process" else broker.SyntheticBroker
            return cls(directory, run_id, b"fake-only-secret", upstream=upstream)

        def ready(run_id, path):
            witness.update(run_id=run_id, path=path)
            frame = lambda rid, body: {"run_id": rid, "body": base64.b64encode(body).decode("ascii")}
            rejection = b'{"error":"request rejected"}\n'
            acknowledgement = b'{"body":"YWNjZXB0ZWQ="}\n'
            witness["allowed"] = request(path, frame(run_id, b"hello")) == (acknowledgement if broker_kind == "process" else b'{"body":"b2s="}\n')
            witness["wrong_run_rejected"] = request(path, frame("wrong-run", b"hello")) == rejection
            witness["extra_destination_rejected"] = request(path, {**frame(run_id, b"hello"), "url": "https://invalid.example"}) == rejection
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
        result["witness"] = witness
        result["receipt"] = receipt
        result["broker_kind"] = broker_kind
        checks = ("allowed", "wrong_run_rejected", "extra_destination_rejected",
                  "literal_reflection_control", "reconnect_rejected")
        if (all(witness.get(key) is True for key in checks)
                and witness.get("run_id") == receipt.get("run_id")
                and receipt.get("preparation") == "diagnostic_ready"
                and receipt.get("launch") == "exited_0"
                and receipt.get("cleanup") == "broker_closed_socket_absent; descendants_unverified"
                and receipt.get("seal_status") == "unsealed"):
            result["status"] = "pass"
    except (OSError, ValueError, AttributeError, ImportError, TypeError):
        result["reason"] = "module, protocol or prerequisite unavailable"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary-module", type=Path, required=True)
    parser.add_argument("--broker-module", type=Path, required=True)
    parser.add_argument("--broker-kind", choices=("synthetic", "process"), default="synthetic")
    args = parser.parse_args()
    print(json.dumps(run(args.boundary_module, args.broker_module, args.broker_kind), sort_keys=True))


if __name__ == "__main__":
    main()
