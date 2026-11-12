"""VM-free, synthetic transport admission. No socket, launch or OS authentication.

The injected verifier is a TRUSTED boundary, not a peer-supplied identity field.
Only tests supply a verifier today. Nothing imports this module on the CLI path.
"""
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Protocol
from uuid import UUID

MAX_WIRE_BYTES = 8192
_ID = re.compile(r"[a-zA-Z0-9_.-]{1,80}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ENV = re.compile(r"[A-Z_][A-Z0-9_]{0,63}\Z")
_ROLES = frozenset({"stdin", "stdout", "stderr"})


class TransportDenied(ValueError):
    """A fixed reason code; never include peer input or credential material."""


def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except ValueError:
        return False


def _env_valid(env):
    return (type(env) is dict and len(env) <= 16
            and all(_matches(_ENV, k) and type(v) is str and len(v) <= 1024
                    and "\x00" not in v for k, v in env.items()))


@dataclass(frozen=True)
class AuthenticatedPeer:
    """Returned by trusted verifier after channel identity AND message verification."""
    peer_id: str
    key_id: str


class PeerVerifier(Protocol):
    def __call__(self, channel: object, signed_bytes: bytes, key_id: str,
                 tag: str) -> AuthenticatedPeer:
        """Verify out-of-band channel identity and authentication of exact bytes.

        Raise on any failure. The wire peer_id is only an assertion to compare.
        No production implementation or OS-backed channel is provided here.
        """
        ...


@dataclass(frozen=True)
class RunPolicy:
    """Trusted one-shot run registration; tuples snapshot env and descriptor slots.

    Provision fresh UUID/256-bit nonce outside the peer request. Numeric FDs are
    trusted synthetic bindings, never wire integers or ambient inherited handles.
    """
    run_id: str
    nonce: str
    peer_id: str
    key_id: str
    policy_digest: str
    runtime_id: str
    expires_at: int
    env: tuple[tuple[str, str], ...] = ()
    stdio: tuple[tuple[str, int], ...] = ()

    def __post_init__(self):
        if (not _uuid(self.run_id) or not _matches(_HEX, self.nonce)
                or not _matches(_ID, self.peer_id) or not _matches(_ID, self.key_id)
                or not _matches(_HEX, self.policy_digest)
                or not _matches(_ID, self.runtime_id)
                or type(self.expires_at) is not int or self.expires_at <= 0):
            raise TransportDenied("policy")
        if (type(self.env) is not tuple or type(self.stdio) is not tuple
                or any(type(p) is not tuple or len(p) != 2 for p in self.env + self.stdio)):
            raise TransportDenied("policy")
        if not _env_valid(dict(self.env)) or len(dict(self.env)) != len(self.env):
            raise TransportDenied("policy")
        if (len(self.stdio) > 3 or any(type(k) is not str or k not in _ROLES
                or type(v) is not int or v < 0 for k, v in self.stdio)
                or len(dict(self.stdio)) != len(self.stdio)
                or len({v for _, v in self.stdio}) != len(self.stdio)):
            raise TransportDenied("policy")


class TransportReplayStore:
    """Atomic, durable model; operator must protect this file AND all parent paths.

    No pruning/reset API. A run UUID can be consumed once, even if nonce changes.
    Restart protection requires reopening this same intact database. Deletion,
    rollback, protected ownership and crash recovery are not established here.
    """
    def __init__(self, path):
        self.path = str(path)
        if not self.path or self.path == ":memory:":
            raise TransportDenied("replay_store")
        self.path = str(Path(self.path).resolve())
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS transport_consumed "
                           "(run_id TEXT PRIMARY KEY, nonce TEXT NOT NULL)")
        except sqlite3.Error:
            raise TransportDenied("replay_store") from None

    def consume(self, run_id, nonce):
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("INSERT INTO transport_consumed VALUES (?, ?)", (run_id, nonce))
        except sqlite3.IntegrityError:
            raise TransportDenied("replay") from None
        except sqlite3.Error:
            raise TransportDenied("replay_store") from None


def signing_bytes(payload, key_id):
    """Domain-separated canonical bytes; binds all payload fields and the key ID."""
    return b"labhq.synthetic-transport.v1\x00" + json.dumps(
        {"payload": payload, "key_id": key_id}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportDenied("schema")
        result[key] = value
    return result


def _parse(wire):
    if type(wire) is not bytes or not 0 < len(wire) <= MAX_WIRE_BYTES:
        raise TransportDenied("schema")
    try:
        msg = json.loads(wire.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise TransportDenied("schema") from None
    if type(msg) is not dict or set(msg) != {"payload", "auth"}:
        raise TransportDenied("schema")
    p, a = msg["payload"], msg["auth"]
    fields = {"version", "run_id", "nonce", "peer_id", "policy_digest", "operation",
              "runtime_id", "expires_at", "env", "stdio"}
    if (type(p) is not dict or set(p) != fields or type(a) is not dict
            or set(a) != {"key_id", "tag"} or not _matches(_ID, a["key_id"])
            or not _matches(_HEX, a["tag"]) or type(p["version"]) is not int
            or p["version"] != 1 or not _uuid(p["run_id"])
            or not _matches(_HEX, p["nonce"]) or not _matches(_ID, p["peer_id"])
            or not _matches(_HEX, p["policy_digest"]) or not _matches(_ID, p["operation"])
            or not _matches(_ID, p["runtime_id"]) or type(p["expires_at"]) is not int
            or not 0 < p["expires_at"] < 2**63 or not _env_valid(p["env"])
            or type(p["stdio"]) is not list or len(p["stdio"]) > 3
            or any(not _matches(_ID, role) for role in p["stdio"])
            or len(set(p["stdio"])) != len(p["stdio"])):
        raise TransportDenied("schema")
    return p, a


@dataclass(frozen=True)
class SyntheticPlan:
    run_id: str
    runtime_id: str
    env: tuple[tuple[str, str], ...]
    stdio: tuple[tuple[str, int], ...]
    close_fds: bool = True
    scope: str = "synthetic_only"


class SyntheticTransport:
    """Default-off one-shot admission; returns data only, never starts a child."""
    def __init__(self, policy: RunPolicy, verifier: PeerVerifier,
                 replay_store: TransportReplayStore, *, enabled=False,
                 clock: Callable[[], float] = time.time):
        if type(policy) is not RunPolicy or type(enabled) is not bool:
            raise TransportDenied("configuration")
        self._policy, self._verifier, self._store = policy, verifier, replay_store
        self._enabled, self._clock = enabled, clock

    def prepare(self, wire: bytes, *, channel: object) -> SyntheticPlan:
        if not self._enabled:
            raise TransportDenied("disabled")
        payload, auth = _parse(wire)
        try:
            peer = self._verifier(channel, signing_bytes(payload, auth["key_id"]),
                                  auth["key_id"], auth["tag"])
        except Exception:
            raise TransportDenied("authentication") from None
        policy = self._policy
        if (type(peer) is not AuthenticatedPeer or peer.peer_id != policy.peer_id
                or peer.key_id != policy.key_id or auth["key_id"] != peer.key_id
                or payload["peer_id"] != peer.peer_id):
            raise TransportDenied("peer")
        if payload["run_id"] != policy.run_id or payload["nonce"] != policy.nonce:
            raise TransportDenied("run_binding")
        # Commit before policy checks/return. Concurrent requests have one winner;
        # authenticated denials and loss of the reply cannot retry this run.
        self._store.consume(policy.run_id, policy.nonce)
        if (payload["operation"] != "prepare" or payload["runtime_id"] != policy.runtime_id
                or payload["policy_digest"] != policy.policy_digest):
            raise TransportDenied("policy_binding")
        if not self._clock() < payload["expires_at"] <= policy.expires_at:
            raise TransportDenied("expired")
        allowed_env, allowed_stdio = dict(policy.env), dict(policy.stdio)
        if any(k not in allowed_env or v != allowed_env[k] for k, v in payload["env"].items()):
            raise TransportDenied("env")
        if any(role not in allowed_stdio for role in payload["stdio"]):
            raise TransportDenied("stdio")
        return SyntheticPlan(policy.run_id, policy.runtime_id,
                             tuple((k, allowed_env[k]) for k in sorted(payload["env"])),
                             tuple((k, allowed_stdio[k]) for k in sorted(payload["stdio"])))
