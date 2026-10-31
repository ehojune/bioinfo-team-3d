"""Offline lifecycle observer model. No OS isolation or cleanup guarantees."""
import copy
import hashlib
import hmac
import json
import sqlite3


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _text(value):
    return isinstance(value, str) and bool(value)


def verify_event(event, key_id, key, run_id, nonce, sequence, kind, identity=None):
    if not isinstance(event, dict) or set(event) != {"version", "run_id", "nonce", "sequence", "kind", "identity", "auth"}:
        raise ValueError("event shape")
    ident, auth = event["identity"], event["auth"]
    if (type(event["version"]) is not int or event["version"] != 1
        or event["run_id"] != run_id or event["nonce"] != nonce
        or type(event["sequence"]) is not int or event["sequence"] != sequence
        or event["kind"] != kind or not isinstance(ident, dict)
        or set(ident) != {"pid", "birth_id"} or type(ident["pid"]) is not int
        or ident["pid"] < 1 or not _text(ident["birth_id"])
        or (identity is not None and ident != identity)
        or not isinstance(auth, dict) or set(auth) != {"key_id", "algorithm", "tag"}
        or auth["key_id"] != key_id or auth["algorithm"] != "hmac-sha256"
        or not isinstance(auth["tag"], str)):
        raise ValueError("event fields")
    unsigned = {k: v for k, v in event.items() if k != "auth"}
    expected = hmac.new(key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, auth["tag"]):
        raise ValueError("event authentication")
    return dict(ident)


class ReplayStore:
    """SQLite replay model; caller must protect the database and its directory."""
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS consumed (run_id TEXT, nonce TEXT, PRIMARY KEY (run_id, nonce))")

    def consume(self, run_id, nonce):
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT INTO consumed VALUES (?, ?)", (run_id, nonce))
        except sqlite3.IntegrityError as exc:
            raise ValueError("replay") from exc


def observe(events, *, run_id, nonce, producer_key_id, producer_key, observer_key_id,
            observer_key, replay_store, collector):
    """Validate events and separate injected snapshots; return synthetic evidence only.

    Collector receives phase and identity and returns a synthetic inventory mapping.
    This function cannot establish the collector's independence or completeness.
    """
    if not _text(run_id) or not _text(nonce) or not _text(observer_key_id):
        raise ValueError("expectations")
    iterator = iter(events)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise ValueError("event count") from exc
    ident = verify_event(first, producer_key_id, producer_key, run_id, nonce, 1, "launch")
    # Consume as soon as the launch is authenticated, including subsequent failures.
    replay_store.consume(run_id, nonce)
    snapshots = []
    for phase in ("launch", "termination"):
        if phase == "termination":
            try:
                event = next(iterator)
            except StopIteration as exc:
                raise ValueError("event count") from exc
            verify_event(event, producer_key_id, producer_key, run_id, nonce, 2, phase, ident)
        snap = copy.deepcopy(collector(phase, dict(ident)))
        required = {"run_id", "nonce", "phase", "identity", "processes", "resources", "provenance", "sequence", "complete"}
        if (not isinstance(snap, dict) or set(snap) != required or snap["run_id"] != run_id
            or snap["nonce"] != nonce or snap["phase"] != phase or snap["identity"] != ident
            or not _text(snap["provenance"]) or not isinstance(snap["processes"], list)
            or not isinstance(snap["resources"], list) or snap["complete"] is not True
            or type(snap["sequence"]) is not int or snap["sequence"] != len(snapshots) + 1
            or (snapshots and snap["provenance"] != snapshots[0]["provenance"])):
            raise ValueError("collector observation")
        if phase == "launch" and ident not in snap["processes"]:
            raise ValueError("missing launch identity")
        snapshots.append(snap)
    sentinel = object()
    if next(iterator, sentinel) is not sentinel:
        raise ValueError("event count")
    if snapshots[1]["processes"] or snapshots[1]["resources"]:
        raise ValueError("remaining resources")
    evidence = {"version": 1, "run_id": run_id, "nonce": nonce, "identity": ident,
                "snapshots": snapshots, "scope": "synthetic_only"}
    evidence["auth"] = {"key_id": observer_key_id, "algorithm": "hmac-sha256",
                        "tag": hmac.new(observer_key, _canonical(evidence), hashlib.sha256).hexdigest()}
    return evidence
