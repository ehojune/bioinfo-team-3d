"""Fake peers/keys only. No process, socket, inherited FD or OS protection test."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import hmac
import json
import threading

import pytest

from labhq.runner.protected_transport import (
    AuthenticatedPeer, MAX_WIRE_BYTES, RunPolicy, SyntheticTransport,
    TransportDenied, TransportReplayStore, signing_bytes,
)

KEY = b"synthetic-fixture-key-not-a-credential"
RUN = "a3a8ae07-9adc-4ad5-a991-c79215deba02"
CHANNEL = object()


class FakeVerifier:
    """Test-only channel registry; the wire cannot register its own identity."""
    def __call__(self, channel, signed_bytes, key_id, tag):
        if channel is not CHANNEL or key_id != "fixture-key":
            raise ValueError("unregistered peer")
        expected = hmac.new(KEY, signed_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, tag):
            raise ValueError("bad message")
        return AuthenticatedPeer("fixture-peer", "fixture-key")


@pytest.fixture
def policy():
    return RunPolicy(RUN, "ab" * 32, "fixture-peer", "fixture-key", "cd" * 32,
                     "fixture-runtime", 2000, (("LANG", "C"),),
                     (("stdin", 101), ("stdout", 102), ("stderr", 103)))


def payload(policy):
    return dict(version=1, run_id=policy.run_id, nonce=policy.nonce,
                peer_id=policy.peer_id, policy_digest=policy.policy_digest,
                operation="prepare", runtime_id=policy.runtime_id,
                expires_at=1900, env={"LANG": "C"}, stdio=["stdin", "stdout"])


def wire(p, *, key_id="fixture-key"):
    tag = hmac.new(KEY, signing_bytes(p, key_id), hashlib.sha256).hexdigest()
    return json.dumps(dict(payload=p, auth=dict(key_id=key_id, tag=tag))).encode()


def transport(policy, path, **kwargs):
    return SyntheticTransport(policy, kwargs.pop("verifier", FakeVerifier()),
                              TransportReplayStore(path), clock=lambda: 1800,
                              enabled=kwargs.pop("enabled", True), **kwargs)


def test_valid_plan_rebuilds_only_registered_env_and_stdio(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_AMBIENT_SECRET", "must-not-be-forwarded")
    plan = transport(policy, tmp_path / "ledger").prepare(wire(payload(policy)), channel=CHANNEL)
    assert plan.run_id == RUN and plan.runtime_id == "fixture-runtime"
    assert plan.env == (("LANG", "C"),)
    assert plan.stdio == (("stdin", 101), ("stdout", 102))
    assert plan.close_fds is True and plan.scope == "synthetic_only"
    empty = payload(replace(policy, run_id="110f64fb-1c51-4ae4-8fc4-7d49eb5ecf13"))
    empty.update(env={}, stdio=[])
    plan = transport(replace(policy, run_id=empty["run_id"]), tmp_path / "ledger").prepare(
        wire(empty), channel=CHANNEL)
    assert plan.env == plan.stdio == ()


def test_default_off_does_not_call_verifier(policy, tmp_path):
    def never(*args):
        pytest.fail("disabled transport authenticated a request")
    gate = SyntheticTransport(policy, never, TransportReplayStore(tmp_path / "ledger"))
    with pytest.raises(TransportDenied, match="disabled"):
        gate.prepare(wire(payload(policy)), channel=CHANNEL)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("run_id", "not-a-uuid"),
    ("nonce", "short"), ("expires_at", True), ("expires_at", 1.5),
    ("env", {"LANG": 1}), ("env", {"bad=name": "C"}),
    ("env", {"LANG": "C\x00secret"}), ("env", {"LANG": "x" * 1025}),
    ("stdio", [1]), ("stdio", ["stdout", "stdout"]),
    ("stdio", {"stdout": 1}), ("peer_id", []),
])
def test_malformed_fields_do_not_burn_nonce(policy, tmp_path, field, value):
    gate = transport(policy, tmp_path / "ledger")
    p = payload(policy)
    p[field] = value
    with pytest.raises(TransportDenied, match="schema"):
        gate.prepare(wire(p), channel=CHANNEL)
    assert gate.prepare(wire(payload(policy)), channel=CHANNEL).scope == "synthetic_only"


@pytest.mark.parametrize("bad", [b"", b"[]", b"{}", b"\xff", b"{" * 2000,
                                b" " * (MAX_WIRE_BYTES + 1), b'{"x":1,"x":2}'])
def test_malformed_wire(policy, tmp_path, bad):
    with pytest.raises(TransportDenied, match="schema"):
        transport(policy, tmp_path / "ledger").prepare(bad, channel=CHANNEL)


@pytest.mark.parametrize("mutation", ["extra", "missing", "extra_auth", "duplicate_nested", "nan"])
def test_closed_schema(policy, tmp_path, mutation):
    msg = json.loads(wire(payload(policy)))
    if mutation == "extra": msg["payload"]["command"] = "do-not-run"
    if mutation == "missing": del msg["payload"]["env"]
    if mutation == "extra_auth": msg["auth"]["algorithm"] = "none"
    if mutation == "nan": msg["payload"]["expires_at"] = float("nan")
    bad = json.dumps(msg).encode()
    if mutation == "duplicate_nested": bad = bad.replace(b'"LANG": "C"', b'"LANG": "C", "LANG": "C"')
    with pytest.raises(TransportDenied, match="schema"):
        transport(policy, tmp_path / "ledger").prepare(bad, channel=CHANNEL)


@pytest.mark.parametrize("field", ["run_id", "nonce", "peer_id", "policy_digest", "operation",
                                   "runtime_id", "expires_at", "env", "stdio"])
def test_every_payload_field_is_authenticated(policy, tmp_path, field):
    gate = transport(policy, tmp_path / "ledger")
    msg = json.loads(wire(payload(policy)))
    changes = dict(run_id="110f64fb-1c51-4ae4-8fc4-7d49eb5ecf13", nonce="ef" * 32,
                   peer_id="spoof", policy_digest="ef" * 32, operation="execute",
                   runtime_id="other", expires_at=1899, env={}, stdio=[])
    msg["payload"][field] = changes[field]
    with pytest.raises(TransportDenied, match="authentication"):
        gate.prepare(json.dumps(msg).encode(), channel=CHANNEL)
    gate.prepare(wire(payload(policy)), channel=CHANNEL)


@pytest.mark.parametrize("attack", ["spoofed_channel", "key_id", "tag", "claimed_peer", "verifier_error", "string_identity"])
def test_authentication_boundary(policy, tmp_path, attack):
    msg = json.loads(wire(payload(policy)))
    channel, verifier = CHANNEL, FakeVerifier()
    if attack == "spoofed_channel": channel = {"peer_id": "fixture-peer"}
    if attack == "key_id": msg["auth"]["key_id"] = "other"
    if attack == "tag": msg["auth"]["tag"] = "0" * 64
    if attack == "claimed_peer":
        p = payload(policy)
        p["peer_id"] = "other"
        msg = json.loads(wire(p))
    if attack == "verifier_error":
        def verifier(*args): raise RuntimeError("secret-must-not-escape")
    if attack == "string_identity": verifier = lambda *args: "fixture-peer"
    with pytest.raises(TransportDenied, match="authentication|peer") as err:
        transport(policy, tmp_path / "ledger", verifier=verifier).prepare(json.dumps(msg).encode(), channel=channel)
    assert "secret" not in str(err.value)
    transport(policy, tmp_path / "ledger").prepare(wire(payload(policy)), channel=CHANNEL)


@pytest.mark.parametrize("field,value", [("run_id", "110f64fb-1c51-4ae4-8fc4-7d49eb5ecf13"), ("nonce", "ef" * 32)])
def test_authenticated_wrong_run_does_not_consume_expected_run(policy, tmp_path, field, value):
    gate = transport(policy, tmp_path / "ledger")
    p = payload(policy)
    p[field] = value
    with pytest.raises(TransportDenied, match="run_binding"):
        gate.prepare(wire(p), channel=CHANNEL)
    gate.prepare(wire(payload(policy)), channel=CHANNEL)


@pytest.mark.parametrize("field,value,reason", [
    ("operation", "execute", "policy_binding"), ("runtime_id", "other", "policy_binding"),
    ("policy_digest", "ef" * 32, "policy_binding"), ("expires_at", 1800, "expired"),
    ("expires_at", 2001, "expired"), ("env", {"SECRET": "fake"}, "env"),
    ("env", {"LANG": "changed"}, "env"), ("stdio", ["credential"], "stdio"),
])
def test_authenticated_policy_denials_burn_nonce(policy, tmp_path, field, value, reason):
    gate = transport(policy, tmp_path / "ledger")
    p = payload(policy)
    p[field] = value
    with pytest.raises(TransportDenied, match=reason): gate.prepare(wire(p), channel=CHANNEL)
    with pytest.raises(TransportDenied, match="replay"): gate.prepare(wire(payload(policy)), channel=CHANNEL)


def test_restart_replay_and_changed_nonce_cannot_reuse_run(policy, tmp_path):
    path = tmp_path / "ledger"
    transport(policy, path).prepare(wire(payload(policy)), channel=CHANNEL)
    for candidate in (policy, replace(policy, nonce="ef" * 32)):
        with pytest.raises(TransportDenied, match="replay"):
            transport(candidate, path).prepare(wire(payload(candidate)), channel=CHANNEL)


def test_simultaneous_requests_across_instances_have_one_winner(policy, tmp_path):
    gates = [transport(policy, tmp_path / "ledger") for _ in range(8)]
    barrier = threading.Barrier(len(gates))
    def request(gate):
        barrier.wait(timeout=10)
        try:
            return gate.prepare(wire(payload(policy)), channel=CHANNEL).scope
        except TransportDenied as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=len(gates)) as pool:
        results = list(pool.map(request, gates))
    assert results.count("synthetic_only") == 1
    assert results.count("replay") == 7


def test_unavailable_ledger_fails_closed(policy, tmp_path):
    path = tmp_path / "ledger"
    gate = transport(policy, path)
    path.unlink()
    path.mkdir()
    with pytest.raises(TransportDenied, match="replay_store"):
        gate.prepare(wire(payload(policy)), channel=CHANNEL)


@pytest.mark.parametrize("changes", [dict(run_id="bad"), dict(nonce="short"), dict(expires_at=True),
    dict(env=[("LANG", "C")]), dict(env=(("LANG", "C"), ("LANG", "x"))),
    dict(stdio=(("stdout", True),)), dict(stdio=(("credential", 101),)),
    dict(stdio=(("stdout", 101), ("stderr", 101)))])
def test_invalid_trusted_policy_rejected(policy, changes):
    with pytest.raises(TransportDenied, match="policy"): replace(policy, **changes)


def test_memory_ledger_is_not_accepted():
    with pytest.raises(TransportDenied, match="replay_store"): TransportReplayStore(":memory:")
