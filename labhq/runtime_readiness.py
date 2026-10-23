"""Read-only local daemon evidence. Missing identity is never evidence of a running process."""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time


_CONTAINED_ROLES = ("gateway", "gateway-supervisor", "runner", "runner-supervisor")


def _require(ok):
    if not ok:
        raise ValueError("contained runtime proof invalid")


def _identity(row):
    return (isinstance(row, dict) and type(row.get("pid")) is int and 0 < row["pid"] <= 0xFFFFFFFF
            and isinstance(row.get("created"), str) and re.fullmatch(r"windows:[1-9][0-9]*", row["created"]))


def _pipe_request(pipe, owner_pid, request, timeout):
    """Bounded local message-pipe query. Never opens or retains the owner's Job.

    NOWAIT bounds both read and write: a full/busy/malformed pipe fails closed.
    Server PID is obtained from the connected kernel pipe, not from its reply.
    """
    import ctypes as C
    from ctypes import wintypes as W

    k = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateFileW": ([W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
        "WaitNamedPipeW": ([W.LPCWSTR, W.DWORD], W.BOOL),
        "SetNamedPipeHandleState": ([W.HANDLE, C.POINTER(W.DWORD), C.c_void_p, C.c_void_p], W.BOOL),
        "GetNamedPipeServerProcessId": ([W.HANDLE, C.POINTER(W.ULONG)], W.BOOL),
        "ReadFile": ([W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
        "WriteFile": ([W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
        "CloseHandle": ([W.HANDLE], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, result
    until = time.monotonic() + timeout
    handle = None
    invalid = C.c_void_p(-1).value
    try:
        while time.monotonic() < until:
            handle = k.CreateFileW(pipe, 0xC0000000, 0, None, 3, 0, None)
            if handle not in (None, invalid):
                break
            k.WaitNamedPipeW(pipe, max(1, min(50, int((until - time.monotonic()) * 1000))))
        _require(handle not in (None, invalid))
        peer, mode = W.ULONG(), W.DWORD(3)  # MESSAGE | NOWAIT
        _require(k.SetNamedPipeHandleState(handle, C.byref(mode), None, None))
        _require(k.GetNamedPipeServerProcessId(handle, C.byref(peer)) and peer.value == owner_pid)
        raw = json.dumps(request).encode("utf8")
        _require(len(raw) <= 8192)
        count = W.DWORD()
        _require(k.WriteFile(handle, raw, len(raw), C.byref(count), None) and count.value == len(raw))
        buffer = C.create_string_buffer(8192)
        while time.monotonic() < until:
            if k.ReadFile(handle, buffer, len(buffer), C.byref(count), None):
                return json.loads(buffer.raw[:count.value])
            _require(C.get_last_error() == 232)  # no data yet; more-data/closed/error is not a proof
            time.sleep(min(.01, max(0, until - time.monotonic())))
        raise ValueError("contained runtime owner query timed out")
    finally:
        if handle not in (None, invalid):
            k.CloseHandle(handle)


def _owner_snapshot(receipt, request, trust):
    from .windows_broker import Native

    native, handle = Native(), None
    try:
        owner = receipt["owner"]
        handle = native.open_process(owner["pid"], terminate=False)
        identity = native.identity(handle)
        _require(identity["created"] == owner["created"] and native.alive(handle))
        expected_image = trust["owner_image"]
        _require(os.path.normcase(os.path.realpath(identity["image"])) ==
                 os.path.normcase(os.path.realpath(expected_image["path"])))
        _require(hashlib.sha256(Path(identity["image"]).read_bytes()).hexdigest() == expected_image["sha256"])
        result = _pipe_request(receipt["pipe"], owner["pid"], request,
                               min(3, receipt["deadline_at"] - time.time()))
        _require(native.alive(handle))  # retained handle pins the pipe server's generation
        return result
    finally:
        if handle is not None:
            native.k.CloseHandle(handle)


def _operator_trust(root, receipt):
    """Doctor caller pins this reviewed contract independently, outside state.

    The owner must never select this pin. Same-account operator forgery is not
    an OS security boundary; this is explicit local operator authorization.
    """
    selected = os.environ.get("LABHQ_CONTAINED_TRUST_PATH", "")
    pin = os.environ.get("LABHQ_CONTAINED_TRUST_SHA256", "")
    _require(selected and re.fullmatch(r"[0-9a-f]{64}", pin))
    path = Path(selected)
    _require(path.is_absolute() and not path.resolve().is_relative_to(root.resolve()))
    with path.open(mode="rb") as stream:
        raw = stream.read(65537)
    _require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == pin)
    trust = json.loads(raw)
    _require(trust["schema"] == "labhq-contained-trust/1" and trust["issuer"] == "operator-reviewed")
    issued, expires = trust["issued_at"], trust["expires_at"]
    _require(all(type(v) in (int, float) and math.isfinite(v) for v in (issued, expires)))
    _require(issued <= time.time() < expires <= issued + 300 and expires <= receipt["deadline_at"])
    _require(all(trust[key] == receipt[key] for key in
                 ("owner", "nonce", "roles", "config_sha256", "deadline_at", "provenance")))
    for name, field in (("launcher", "launcher_sha256"), ("launch_contract", "contract_sha256")):
        artifact = trust[name]
        _require(Path(artifact["path"]).is_absolute() and artifact["sha256"] == receipt["provenance"][field])
        _require(hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"])
    image = trust["owner_image"]
    _require(Path(image["path"]).is_absolute() and re.fullmatch(r"[0-9a-f]{64}", image["sha256"]))
    _require(time.time() < expires)
    return trust


def _contained(settings, root, records, memberships):
    """Trusted operator receipt selects a policy, never supplies membership proof.

    A current creation-bound owner must answer a fresh challenge after checking
    all four generations in its exact Job and its independent deadline. Local
    files, old launch logs and membership in an arbitrary Job cannot grant PASS.
    """
    _require(set(records) == set(_CONTAINED_ROLES) == set(memberships))
    _require(all(memberships[name] is True and _identity(records[name]) for name in records))
    path = root / "contained-runtime.json"
    with path.open(mode="rb") as stream:
        raw = stream.read(65537)
    _require(len(raw) <= 65536)
    receipt = json.loads(raw)
    _require(receipt["schema"] == "labhq-contained-runtime/1" and receipt["issuer"] == "labhq-contained-owner/1")
    _require(re.fullmatch(r"[0-9a-f]{32,64}", receipt["nonce"]))
    _require(_identity(receipt["owner"]) and receipt["roles"] == records)
    _require(len({row["pid"] for row in records.values()} | {receipt["owner"]["pid"]}) == 5)
    issued, deadline = receipt["issued_at"], receipt["deadline_at"]
    _require(all(type(v) in (int, float) and math.isfinite(v) for v in (issued, deadline)))
    _require(issued <= time.time() < deadline <= issued + 5400)
    _require(isinstance(settings.config_path, str) and settings.config_path)
    _require(os.path.normcase(os.path.realpath(receipt["config_path"])) ==
             os.path.normcase(os.path.realpath(settings.config_path)))
    digest = hashlib.sha256(Path(settings.config_path).read_bytes()).hexdigest()
    _require(receipt["config_sha256"] == digest)
    provenance = receipt["provenance"]
    _require(re.fullmatch(r"[0-9a-f]{40}", provenance["source_commit"]))
    _require(all(re.fullmatch(r"[0-9a-f]{64}", provenance[key]) for key in
                 ("contract_sha256", "launcher_sha256", "runtime_readiness_sha256")))
    _require(provenance["runtime_readiness_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    _require(Path(provenance["runtime_readiness_path"]).is_absolute())
    _require(os.path.normcase(os.path.realpath(provenance["runtime_readiness_path"])) ==
             os.path.normcase(os.path.realpath(__file__)))
    trust = _operator_trust(root, receipt)
    _require(re.fullmatch(r"\\\\\.\\pipe\\labhq-contained-[0-9a-f]{32}", receipt["pipe"]))
    request = {"schema": "labhq-contained-query/1", "nonce": receipt["nonce"],
               "challenge": secrets.token_hex(16), "owner": receipt["owner"], "roles": records,
               "config_sha256": digest, "deadline_at": deadline, "provenance": provenance}
    result = _owner_snapshot(receipt, request, trust)
    _require(time.time() < trust["expires_at"])
    _require(result["schema"] == "labhq-contained-snapshot/1" and result["issuer"] == receipt["issuer"])
    _require(all(result[key] == request[key] for key in
                 ("nonce", "challenge", "owner", "config_sha256", "deadline_at", "provenance")))
    observed = result["observed_at"]
    _require(type(observed) in (int, float) and math.isfinite(observed) and 0 <= time.time() - observed <= 3)
    _require(result["deadline_active"] is True and time.time() < deadline)
    flags = result["job_limit_flags"]
    _require(type(flags) is int and flags & 0x2000 and not flags & (0x800 | 0x1000))
    _require(set(result["roles"]) == set(records))
    for name, expected in records.items():
        row = result["roles"][name]
        _require(_identity(row) and row.get("pid") == expected["pid"] and row.get("created") == expected["created"]
                 and row.get("in_job") is True and row.get("alive") is True)
    return {"issuer": receipt["issuer"], "nonce": receipt["nonce"], "owner": receipt["owner"],
            "deadline_at": deadline, "observed_at": observed, "job_limit_flags": flags, "provenance": provenance}


def inspect(settings) -> dict:
    from . import cli

    root = cli._launch_state(settings)
    roles = {}
    failures = []
    records = {}
    for role in ("gateway", "runner"):
        process, record, _ = cli._process_record(settings, role, cleanup=False)
        supervisor, supervisor_record, _ = cli._process_record(settings, cli._supervisor_role(role), cleanup=False)
        if process == "alive" and isinstance(record, dict) and all(k in record for k in ("pid", "created")):
            records[role] = {k: record[k] for k in ("pid", "created")}
        if supervisor == "alive" and isinstance(supervisor_record, dict) and all(k in supervisor_record for k in ("pid", "created")):
            records[cli._supervisor_role(role)] = {k: supervisor_record[k] for k in ("pid", "created")}
        supervised = supervisor != "missing" or (root / "logs" / f"{role}-supervisor.log").exists()
        history = supervisor != "missing" or process != "missing"
        journal = root / "logs" / "detached-launch.jsonl"
        history_known = True
        try:
            with journal.open(mode="rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 65536))
                if size > 65536:
                    stream.readline()  # The bounded tail may start in the middle of a JSON line.
                entries = [json.loads(line) for line in stream.read().splitlines() if line.strip()]
                history = history or any(row.get("role") in (role, cli._supervisor_role(role)) for row in entries)
                supervised = supervised or any(row.get("role") == cli._supervisor_role(role) for row in entries)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            history_known = False
            failures.append(f"{role}: launch history unknown")
        # Foreground role diagnostics alone do not mean this host owns a managed daemon.
        history = history or (root / "logs" / f"{role}-supervisor.log").exists()
        stopped = cli._stopping_file(settings, role).is_file()
        if process == "unknown" or supervisor == "unknown":
            state = "unknown"
        elif process == "alive" and (supervisor == "alive" or not supervised):
            state = "running"
        elif history and stopped and process != "alive" and supervisor != "alive":
            state = "down"
        elif history:
            state = "missing" if process == supervisor == "missing" else "stale"
        else:
            state = "unmanaged"
        historical_only = (history_known and history and process == supervisor == "missing"
                           and record is None and supervisor_record is None)
        jobs = {}
        if state == "running" and sys.platform == "win32":
            from .windows_launch import record_job_membership
            jobs[role] = record_job_membership(record)
            if supervisor == "alive":
                jobs[cli._supervisor_role(role)] = record_job_membership(supervisor_record)
            if any(member is not False for member in jobs.values()):
                state = "job_unverified"
                failures.append(f"{role}: current Windows Job escape unverified; restart through verified up")
        roles[role] = {"state": state, "process": process, "supervisor": supervisor,
                       "historical_only": historical_only, "job_membership": jobs}
        if state in ("unknown", "stale", "missing"):
            failures.append(f"{role}: {state} process identity")
    contained = None
    if sys.platform == "win32" and all(r["state"] == "job_unverified" for r in roles.values()):
        try:
            memberships = {name: value for row in roles.values() for name, value in row["job_membership"].items()}
            contained = _contained(settings, root, records, memberships)
            for role, facts in roles.items():
                facts["state"] = "contained_verified"
                failures.remove(f"{role}: current Windows Job escape unverified; restart through verified up")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass  # Preserve the original detached failure; optional proof cannot bypass it.
    states = [r["state"] for r in roles.values()]
    foreground_evidence = any((root / "logs" / f"{role}-diagnostics.jsonl").exists() for role in roles)
    status = "unhealthy" if failures else "down" if "down" in states else (
        "contained_verified" if all(s == "contained_verified" for s in states) else
        "running" if all(s == "running" for s in states) else "unmanaged" if foreground_evidence else "pristine")
    return {"ready": False if failures or status == "down" else True if status in ("running", "contained_verified") else None,
            "status": status, "roles": roles, "reasons": failures, **({"containment": contained} if contained else {})}


def deployment(settings, local: dict) -> dict:
    """Authenticate foreground/remote roles without inventing local PIDs. Called only for --runtime."""
    from . import cli, instance_guard

    result = {**local, "reasons": list(local["reasons"])}
    try:
        health = cli._health(settings)
        confirmed = bool(health) and instance_guard.check_health(settings, health, started_here=False)[1]
    except (OSError, SystemExit):
        confirmed, health = False, None
    if not confirmed:
        result["reasons"].append("gateway: authenticated identity unavailable")
    elif settings.runner.id not in (health.get("runners") or []):
        result["reasons"].append("runner: not connected to authenticated gateway")
    if confirmed and settings.runner.wsl.enabled and settings.runner.wsl.id not in (health.get("runners") or []):
        result["reasons"].append("WSL runner: not connected to authenticated gateway")
    roles = local.get("roles", {})
    if confirmed:
        # Historical absence is not a current bad PID identity. Only authenticated runtime can supersede it.
        historical = {f"{role}: missing process identity" for role, facts in roles.items()
                      if facts.get("historical_only") and facts["state"] == "missing"}
        result["reasons"] = [reason for reason in result["reasons"] if reason not in historical]
    current_down = (local["status"] == "down" and (not roles or any(
        facts["state"] == "down" and not facts.get("historical_only") for facts in roles.values())))
    result["ready"] = not result["reasons"] and not current_down
    result["status"] = ("contained_verified" if local["status"] == "contained_verified" else "verified") if result["ready"] else local["status"]
    return result
