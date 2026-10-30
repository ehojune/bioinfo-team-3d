"""Disposable non-model probes. Never interpret launch errors as isolation."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

CASES = ("allowed", "read", "write", "symlink", "hardlink", "parent_replace",
         "external_writer", "env", "fd", "socket", "interop", "reverse_interop", "detached_child",
         "control_hook", "tool_use_hook", "builtin_mcp")


def receipt_diagnostic(receipt: dict, run_id: str) -> dict:
    """Validate only the published receipt shape, not its authority or seal."""
    fields = ("snapshot_digest", "runtime_digest", "policy_digest", "supervisor_identity",
              "child_identity", "preparation", "launch", "cleanup", "seal_status")
    if (not isinstance(receipt, dict) or receipt.get("version") != 1
            or receipt.get("run_id") != run_id
            or not all(k in receipt and (receipt[k] is None or isinstance(receipt[k], str)) for k in fields)
            or not isinstance(receipt.get("namespace_identities"), dict)
            or not isinstance(receipt.get("unsupported_reasons"), list)
            or not all(isinstance(x, str) for x in receipt["unsupported_reasons"])):
        return {"status": "inconclusive", "reason": "receipt mismatch or invalid schema"}
    if receipt["seal_status"] == "unsealed":
        return {"status": "unsupported", "reason": "unsealed diagnostic"}
    return {"status": "unsupported", "reason": "seal authority not established"}


def wait_synthetic_writer(work: Path) -> bool:
    """Wait only for the PID created by our short-lived synthetic child."""
    try:
        pid = int((work / "detached.pid").read_text(encoding="ascii"))
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return ctypes.get_last_error() == 87  # process already gone
            try:
                return kernel.WaitForSingleObject(handle, 6000) == 0
            finally:
                kernel.CloseHandle(handle)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.02)
    except (OSError, ValueError):
        pass
    return False


def synthetic_writer_alive(work: Path) -> bool | None:
    """Observe only the recorded synthetic PID; errors are unknown, not death."""
    try:
        pid = int((work / "detached.pid").read_text(encoding="ascii"))
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False if ctypes.get_last_error() == 87 else None
            try:
                state = kernel.WaitForSingleObject(handle, 0)
                return True if state == 258 else False if state == 0 else None
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, ValueError):
        return None


def child(case: str, root: Path) -> int:
    """Exit 0 means the attempted action succeeded, 10 means access denied.

    No exception text or fixture contents leave the child.
    """
    work, outside = root / "work", root / "outside"
    try:
        if case == "allowed":
            p = work / "allowed.txt"
            p.write_text("allowed", encoding="utf-8")
            assert p.read_text(encoding="utf-8") == "allowed"
        elif case == "read":
            (outside / "secret.txt").read_bytes()
        elif case == "write":
            (outside / "write.txt").write_text("probe", encoding="utf-8")
        elif case == "symlink":
            (work / "symlink").read_bytes()
        elif case == "hardlink":
            (work / "hardlink").read_bytes()
        elif case == "env":
            if os.environ.get("BOUNDARY_PROBE_CANARY") != "fixture-only":
                return 10
        elif case == "socket":
            port = int((work / "port.txt").read_text(encoding="ascii"))
            with socket.socket() as client:
                client.settimeout(2)
                client.connect(("127.0.0.1", port))
        elif case == "fd":
            fd = int((work / "fd.txt").read_text(encoding="ascii"))
            try:
                if os.read(fd, 1) != b"f":
                    return 20
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    return 10
                raise
        elif case in ("control_hook", "tool_use_hook", "builtin_mcp"):
            # Exercise actual labhq functions with synthetic state, not a Claude hook invocation.
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            run_id = (root / "run-id").read_text(encoding="ascii")
            if case == "control_hook":
                from labhq.hooks.control import new_state, pre_tool_use, write_state
                state = new_state(run_id)
                state["paused"] = True
                path = work / "control.json"
                write_state(path, state)
                result = pre_tool_use(path, hold_s=0)
                if not result or result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
                    return 20
            elif case == "tool_use_hook":
                from labhq.hooks.tool_use import record_post_tool_use
                if not record_post_tool_use({"tool_use_id": run_id, "tool_name": "Write"},
                                            {"LABHQ_WORKDIR": str(work), "LABHQ_TASK_ID": run_id}, now_ns=1):
                    return 20
                manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
                if manifest["runs"][run_id]["tool_uses"][0]["tool_use_id"] != run_id:
                    return 20
            else:
                # Start the actual built-in job MCP and list tools only: no job or network request.
                import asyncio
                from mcp import ClientSession
                from mcp.client.stdio import StdioServerParameters, stdio_client
                config = work / "mcp-config.yaml"
                config.write_text("{}\n", encoding="ascii")
                repo = str(Path(__file__).resolve().parents[2])
                clean_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
                clean_env.update(HOME=str(work), USERPROFILE=str(work), PYTHONPATH=repo,
                                 LABHQ_CONFIG=str(config), LABHQ_WORKDIR=str(work))
                async def list_builtin():
                    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.local_job_mcp"],
                                                   env=clean_env, cwd=str(work))
                    async with stdio_client(params) as streams:
                        async with ClientSession(streams[0], streams[1]) as session:
                            await session.initialize()
                            return {tool.name for tool in (await session.list_tools()).tools}
                if "job_status" not in asyncio.run(asyncio.wait_for(list_builtin(), timeout=7)):
                    return 20
            (work / (case + ".marker")).write_text(run_id, encoding="ascii")
        elif case in ("parent_replace", "external_writer"):
            run_id = (root / "run-id").read_text(encoding="ascii")
            (work / (case + ".ready")).write_text(run_id, encoding="ascii")
            deadline = time.monotonic() + 5
            while not (work / (case + ".continue")).exists():
                if time.monotonic() >= deadline:
                    return 20
                time.sleep(0.01)
            if (work / (case + ".continue")).read_text(encoding="ascii") != run_id:
                return 20
            observed = (work / "mutable" / "target").read_text(encoding="ascii")
            if observed == "changed":
                return 0
            return 11 if observed == "original" else 20
        elif case == "detached_child":
            run_id = (root / "run-id").read_text(encoding="ascii")
            options = {"start_new_session": True} if os.name == "posix" else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                              "--detached-writer", str(root)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=Path(__file__).resolve().parent, **options)
            deadline = time.monotonic() + 2
            while not (work / "detached.ready").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not (work / "detached.ready").exists() or (work / "detached.ready").read_text(encoding="ascii") != run_id:
                return 20
        elif case in ("interop", "reverse_interop"):
            distro = (work / "interop.distro").read_text(encoding="utf-8")
            run_id = (root / "run-id").read_text(encoding="ascii")
            if case == "interop":
                program = ("import pathlib,subprocess,sys; "
                           "p=subprocess.check_output(['wslpath','-u',sys.argv[1]],text=True).strip(); "
                           "pathlib.Path(p).write_text(sys.argv[2],encoding='ascii')")
                arguments = [(work / "interop.marker").as_posix(), run_id]
            else:
                program = ("import subprocess,sys; "
                           "p=subprocess.check_output(['wslpath','-u',sys.argv[1]],text=True).strip(); "
                           "sys.exit(subprocess.call([p,sys.argv[2],'--interop-writer',sys.argv[3]]))")
                arguments = [sys.executable, str(Path(__file__).resolve()), str(root)]
            proc = subprocess.run(["wsl.exe", "-d", distro, "--", "python3", "-c", program, *arguments],
                                  capture_output=True, timeout=5)
            if proc.returncode:
                return 20
        else:
            return 20
    except PermissionError:
        return 10
    except (OSError, AssertionError, ImportError, KeyError, ValueError, TimeoutError):
        return 20
    return 0


def mutation_probe(case, prefix, python, script, root, work, outside, env, run_id, timeout):
    """Host mutation occurs only after this run's child has reached its barrier."""
    mutable = work / "mutable"
    mutable.mkdir()
    (mutable / "target").write_text("original", encoding="ascii")
    proc = None
    witness = False
    try:
        proc = subprocess.Popen([*prefix, python, script, "--child", case, str(root)],
                                cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + timeout
        ready = work / (case + ".ready")
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists() or ready.read_text(encoding="ascii") != run_id:
            return {"status": "inconclusive", "host_witness": False}
        if case == "parent_replace":
            target = outside / "replacement"
            target.mkdir()
            (target / "target").write_text("changed", encoding="ascii")
            mutable.rename(work / "original-parent")
            mutable.symlink_to(target, target_is_directory=True)
            witness = mutable.is_symlink() and (mutable / "target").read_text(encoding="ascii") == "changed"
        else:
            (mutable / "target").write_text("changed", encoding="ascii")
            witness = (mutable / "target").read_text(encoding="ascii") == "changed"
        (work / (case + ".continue")).write_text(run_id, encoding="ascii")
        stdout, _ = proc.communicate(timeout=max(0.1, deadline - time.monotonic()))
        valid = stdout in (b"boundary-probe-child\n", b"boundary-probe-child\r\n")
        status = ("fail" if proc.returncode == 0 else "pass" if proc.returncode == 11 else "inconclusive") if valid and witness else "inconclusive"
        return {"status": status, "host_witness": witness}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "inconclusive", "host_witness": witness}
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()


def run(prefix: list[str], python: str = sys.executable, timeout: float = 10,
        interop_distro: str | None = None) -> dict:
    """Run launcher prefix followed by Python and this module's child command.

    The caller supplies a launcher capable of accepting a command as trailing argv.
    Fixtures are local and disposable; no host secrets are used or recorded.
    """
    results = {}
    run_id = str(uuid.uuid4())
    with ExitStack() as stack:
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="boundary-probe-"))
        root = Path(directory)
        work, outside = root / "work", root / "outside"
        work.mkdir()
        outside.mkdir()
        (root / "run-id").write_text(run_id, encoding="ascii")
        (root / "writer-timeout").write_text(str(max(0, timeout) + 3), encoding="ascii")
        if interop_distro and os.name == "nt":
            (work / "interop.distro").write_text(interop_distro, encoding="utf-8")
        (outside / "secret.txt").write_text("fixture-only", encoding="utf-8")
        prerequisites = {}
        fd = None
        if os.name == "posix":
            fd = os.open(outside / "secret.txt", os.O_RDONLY)
            stack.callback(os.close, fd)
            (work / "fd.txt").write_text(str(fd), encoding="ascii")
        prerequisites["fd"] = fd is not None
        try:
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            (work / "port.txt").write_text(str(listener.getsockname()[1]), encoding="ascii")
            prerequisites["socket"] = True
        except OSError:
            prerequisites["socket"] = False
        for name, make in (("symlink", lambda p: p.symlink_to(outside / "secret.txt")),
                           ("hardlink", lambda p: os.link(outside / "secret.txt", p))):
            try:
                make(work / name)
                prerequisites[name] = True
            except OSError:
                prerequisites[name] = False
        script = str(Path(__file__).resolve())
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update(HOME=str(work), USERPROFILE=str(work), TEMP=str(work), TMP=str(work))
        env["BOUNDARY_PROBE_CANARY"] = "fixture-only"
        implemented = {"allowed", "read", "write", "symlink", "hardlink", "env", "socket", "fd"}
        for case in CASES:
            if case in ("control_hook", "tool_use_hook", "builtin_mcp"):
                try:
                    proc = subprocess.run([*prefix, python, script, "--child", case, str(root)],
                                          cwd=work, env=env, capture_output=True, timeout=timeout)
                    marker = work / (case + ".marker")
                    witness = marker.exists() and marker.read_text(encoding="ascii") == run_id
                    valid = proc.returncode == 0 and proc.stdout in (b"boundary-probe-child\n", b"boundary-probe-child\r\n")
                    results[case] = {"status": "pass" if valid and witness else "inconclusive",
                                     "host_witness": witness, "scope": "stdio_list_only" if case == "builtin_mcp" else "direct_function_only"}
                except (OSError, subprocess.TimeoutExpired):
                    results[case] = {"status": "inconclusive", "host_witness": False,
                                     "scope": "stdio_list_only" if case == "builtin_mcp" else "direct_function_only"}
                continue
            if case in ("interop", "reverse_interop"):
                if not interop_distro or os.name != "nt":
                    results[case] = {"status": "unsupported"}
                    continue
                try:
                    proc = subprocess.run([*prefix, python, script, "--child", case, str(root)],
                                          cwd=work, env=env, capture_output=True, timeout=timeout)
                    marker = work / (case + ".marker")
                    witnessed = marker.exists() and marker.read_text(encoding="ascii") == run_id
                    results[case] = {"status": "fail" if witnessed else "inconclusive", "host_witness": witnessed}
                except (OSError, subprocess.TimeoutExpired):
                    marker = work / (case + ".marker")
                    try:
                        witnessed = marker.exists() and marker.read_text(encoding="ascii") == run_id
                    except OSError:
                        witnessed = False
                    results[case] = {"status": "fail" if witnessed else "inconclusive", "host_witness": witnessed}
                continue
            if case == "detached_child":
                try:
                    proc = subprocess.run([*prefix, python, script, "--child", case, str(root)],
                                          cwd=work, env=env, capture_output=True, timeout=timeout)
                    valid = (proc.returncode == 0 and proc.stdout in
                             (b"boundary-probe-child\n", b"boundary-probe-child\r\n"))
                    ready = work / "detached.ready"
                    valid = valid and ready.exists() and ready.read_text(encoding="ascii") == run_id
                    if valid:
                        (work / "detached.trigger").write_text(run_id, encoding="ascii")
                        deadline = time.monotonic() + 2
                        marker = work / "detached.marker"
                        early_exit = False
                        while not marker.exists() and time.monotonic() < deadline:
                            if synthetic_writer_alive(work) is False:
                                early_exit = True
                                break
                            time.sleep(0.01)
                        witnessed = marker.exists() and marker.read_text(encoding="ascii") == run_id
                        results[case] = {"status": "fail" if witnessed else "inconclusive", "host_witness": witnessed,
                                         "early_exit_observed": early_exit}
                    else:
                        results[case] = {"status": "inconclusive", "host_witness": False}
                except (OSError, subprocess.TimeoutExpired):
                    results[case] = {"status": "inconclusive", "host_witness": False}
                if ((work / "detached.ready").exists() and not wait_synthetic_writer(work)
                        and not results[case].get("host_witness")):
                    results[case] = {"status": "inconclusive", "host_witness": False}
                continue
            if case in ("parent_replace", "external_writer"):
                results[case] = mutation_probe(case, prefix, python, script, root, work,
                                               outside, env, run_id, timeout)
                # Separate cases must not share mutable fixture state.
                if (work / "mutable").is_symlink():
                    (work / "mutable").unlink()
                elif (work / "mutable").exists():
                    shutil.rmtree(work / "mutable")
                continue
            if case not in implemented or prerequisites.get(case) is False:
                results[case] = {"status": "unsupported"}
                continue
            try:
                options = {"pass_fds": (fd,)} if case == "fd" else {}
                proc = subprocess.run([*prefix, python, script, "--child", case, str(root)],
                                      cwd=work, env=env, capture_output=True, timeout=timeout,
                                      **options)
            except (OSError, subprocess.TimeoutExpired):
                results[case] = {"status": "launch_error"}
                continue
            if proc.returncode not in (0, 10) or proc.stdout not in (
                    b"boundary-probe-child\n", b"boundary-probe-child\r\n"):
                results[case] = {"status": "inconclusive"}
            elif case == "allowed":
                results[case] = {"status": "pass" if proc.returncode == 0 else "fail"}
            else:
                results[case] = {"status": "fail" if proc.returncode == 0 else "pass"}
        # A positive control is a precondition for every protection claim.
        if results["allowed"]["status"] != "pass":
            for case in CASES[1:]:
                if results[case]["status"] == "pass":
                    results[case]["status"] = "inconclusive"
    return {"schema_version": 1, "run_id": run_id, "scope": "fixture-only", "cases": results,
            "protection_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=CASES)
    parser.add_argument("--detached-writer", action="store_true")
    parser.add_argument("--interop-writer", action="store_true")
    parser.add_argument("case_root", nargs="?")
    parser.add_argument("--launcher-json", default="[]", help="JSON argv prefix")
    parser.add_argument("--interop-distro", help="explicit disposable WSL distribution for interop probe")
    args = parser.parse_args()
    if args.interop_writer:
        root = Path(args.case_root)
        run_id = (root / "run-id").read_text(encoding="ascii")
        (root / "work" / "reverse_interop.marker").write_text(run_id, encoding="ascii")
        return 0
    if args.detached_writer:
        root = Path(args.case_root)
        work = root / "work"
        run_id = (root / "run-id").read_text(encoding="ascii")
        (work / "detached.pid").write_text(str(os.getpid()), encoding="ascii")
        (work / "detached.ready").write_text(run_id, encoding="ascii")
        deadline = time.monotonic() + float((root / "writer-timeout").read_text(encoding="ascii"))
        trigger = work / "detached.trigger"
        while not trigger.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if trigger.exists() and trigger.read_text(encoding="ascii") == run_id:
            marker_tmp = work / "detached.marker.tmp"
            marker_tmp.write_text(run_id, encoding="ascii")
            marker_tmp.replace(work / "detached.marker")
        return 0
    if args.child:
        result = child(args.child, Path(args.case_root))
        print("boundary-probe-child", flush=True)
        return result
    prefix = json.loads(args.launcher_json)
    if not isinstance(prefix, list) or not all(isinstance(x, str) for x in prefix):
        parser.error("launcher must be a JSON array of strings")
    print(json.dumps(run(prefix, interop_distro=args.interop_distro), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
