"""Stage-one synthetic AF_UNIX transport; not a provider integration.

The callable is a trusted test double, never supplied by a sandboxed client.
This module is deliberately not wired to a real credential or provider.
"""
from __future__ import annotations

import base64
import json
import http.client
import ipaddress
import multiprocessing
import os
import socket
import ssl
import stat
import threading
import time
from pathlib import Path
from typing import Callable

MAX_FRAME = 65536
TIMEOUT = 2.0
ACKNOWLEDGEMENT = b"accepted"


def _receive(sock: socket.socket) -> bytes:
    deadline = time.monotonic() + TIMEOUT
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        sock.settimeout(remaining)
        part = sock.recv(min(4096, MAX_FRAME + 1 - len(data)))
        if not part:
            raise ValueError
        data.extend(part)
        if len(data) > MAX_FRAME:
            raise ValueError
    line, extra = bytes(data).split(b"\n", 1)
    if extra:
        raise ValueError
    return line


class SyntheticBroker:
    """One run, one socket, bounded requests and a trusted synthetic upstream."""

    def __init__(self, directory: Path, run_id: str, credential: bytes,
                 upstream: Callable[[bytes, bytes], bytes]):
        if not run_id or not credential or not callable(upstream):
            raise ValueError("invalid broker configuration")
        self.directory = Path(directory)
        self.path = self.directory / "provider.sock"
        self.run_id = run_id
        self._credential = credential
        self._upstream = upstream
        self._stop = threading.Event()
        self._listener = None
        self._thread = None
        self._active = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("AF_UNIX broker requires Linux")
        if self._listener is not None or self._stop.is_set():
            raise RuntimeError("broker cannot be restarted")
        # The caller supplies a fresh, supervisor-owned private directory.
        st = self.directory.lstat()
        if not stat.S_ISDIR(st.st_mode) or self.path.exists() or self.path.is_symlink():
            raise ValueError("invalid socket directory")
        if os.name != "nt" and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            raise ValueError("socket directory must be private")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound = False
        try:
            listener.bind(str(self.path))
            bound = True
            listener.listen(1)
            listener.settimeout(0.1)
        except BaseException:
            listener.close()
            if bound:
                self.path.unlink(missing_ok=True)
            raise
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, args=(listener,), daemon=True)
        self._thread.start()

    def _serve(self, listener: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if self._stop.is_set():
                    conn.close()
                    break
                self._active = conn
            try:
                self._handle(conn)
            finally:
                with self._lock:
                    self._active = None
                conn.close()

    def _handle(self, conn: socket.socket) -> None:
        # All errors have the same constant response and never include input.
        response = {"error": "request rejected"}
        try:
            obj = json.loads(_receive(conn))
            if not isinstance(obj, dict) or set(obj) != {"run_id", "body"}:
                raise ValueError
            if obj["run_id"] != self.run_id or not isinstance(obj["body"], str):
                raise ValueError
            body = base64.b64decode(obj["body"], validate=True)
            if len(body) > MAX_FRAME // 2:
                raise ValueError
            result = self._upstream(body, self._credential)
            if self._stop.is_set():
                raise ValueError
            response = {"body": base64.b64encode(self._response_body(result)).decode("ascii")}
        except Exception:
            pass
        try:
            conn.settimeout(TIMEOUT)
            conn.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        except OSError:
            pass

    def _response_body(self, result: bytes) -> bytes:
        # Synthetic echo is restricted to public test data and fake credentials.
        if not isinstance(result, bytes) or len(result) > MAX_FRAME // 2:
            raise ValueError
        if self._credential in result:
            raise ValueError
        return result

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._active is not None:
                try:
                    self._active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self._listener is not None:
            self._listener.close()
            self._listener = None
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        if self._thread is not None:
            self._thread.join(TIMEOUT + 0.5)
            if self._thread.is_alive():
                raise RuntimeError("synthetic upstream did not stop")


class ConstantResponseBroker(SyntheticBroker):
    """Discard upstream output; this prototype is not a production transport."""

    def _response_body(self, result: bytes) -> bytes:
        return ACKNOWLEDGEMENT


PROVIDER_HOST = "api.anthropic.com"
PROVIDER_PATH = "/v1/messages"


def fixed_provider_transport(body: bytes, credential: bytes) -> None:
    """Fixed HTTPS destination; the caller must run this in a killable process."""
    addresses = socket.getaddrinfo(PROVIDER_HOST, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("resolution failed")
    # Reject the entire answer on mixed public/private results; connect to the
    # validated address directly so DNS cannot change between check and connect.
    for family, kind, protocol, _, address in addresses:
        if family not in (socket.AF_INET, socket.AF_INET6) or not ipaddress.ip_address(address[0]).is_global:
            raise ValueError("unsafe address")
    family, kind, protocol, _, address = addresses[0]
    raw = socket.socket(family, kind, protocol)
    try:
        raw.settimeout(TIMEOUT)
        raw.connect(address)
        with ssl.create_default_context().wrap_socket(raw, server_hostname=PROVIDER_HOST) as tls:
            raw = None
            request = (b"POST " + PROVIDER_PATH.encode() + b" HTTP/1.1\r\nHost: " +
                       PROVIDER_HOST.encode() + b"\r\nContent-Type: application/json\r\n" +
                       b"anthropic-version: 2023-06-01\r\nx-api-key: " + credential +
                       b"\r\nContent-Length: " + str(len(body)).encode() +
                       b"\r\nConnection: close\r\n\r\n" + body)
            if b"\r" in credential or b"\n" in credential:
                raise ValueError("invalid credential")
            tls.sendall(request)
            response = http.client.HTTPResponse(tls)
            response.begin()
            # No redirect is followed, and no response content crosses the boundary.
            if not 200 <= response.status < 300:
                raise ValueError("upstream rejected")
    finally:
        if raw is not None:
            raw.close()


def _run_upstream(sender, upstream, body, credential):
    try:
        # Upstream output and exceptions are never broker diagnostics.
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
            os.dup2(null, 2)
        finally:
            os.close(null)
        upstream(body, credential)
        sender.send_bytes(b"1")
    except BaseException:
        try:
            sender.send_bytes(b"0")
        except OSError:
            pass
    finally:
        sender.close()


class ProcessBroker(ConstantResponseBroker):
    """Constant-only broker with a bounded, forcibly terminated upstream."""

    def __init__(self, directory, run_id, credential, upstream=fixed_provider_transport,
                 deadline=TIMEOUT):
        super().__init__(directory, run_id, credential, upstream)
        if deadline <= 0:
            raise ValueError("invalid deadline")
        self.deadline = deadline
        self._process = None

    def _handle(self, conn):
        response = {"error": "request rejected"}
        receiver = sender = process = None
        try:
            obj = json.loads(_receive(conn))
            if not isinstance(obj, dict) or set(obj) != {"run_id", "body"}:
                raise ValueError
            if obj["run_id"] != self.run_id or not isinstance(obj["body"], str):
                raise ValueError
            body = base64.b64decode(obj["body"], validate=True)
            if len(body) > MAX_FRAME // 2 or self._stop.is_set():
                raise ValueError
            context = multiprocessing.get_context("fork")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=_run_upstream, args=(sender, self._upstream, body, self._credential))
            with self._lock:
                if self._stop.is_set():
                    raise ValueError
                process.start()
                self._process = process
            sender.close()
            end = time.monotonic() + self.deadline
            while not self._stop.is_set() and time.monotonic() < end:
                if receiver.poll(min(0.05, max(0, end - time.monotonic()))):
                    if receiver.recv_bytes() == b"1":
                        response = {"body": base64.b64encode(ACKNOWLEDGEMENT).decode("ascii")}
                    break
                if not process.is_alive():
                    break
        except Exception:
            pass
        finally:
            if process is not None and process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join()
            with self._lock:
                self._process = None
            if receiver is not None:
                receiver.close()
            if sender is not None:
                sender.close()
        try:
            conn.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        except OSError:
            pass
