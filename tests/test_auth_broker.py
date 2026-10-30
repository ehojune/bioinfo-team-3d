import base64
import json
import os
import shutil
import socket
import subprocess
import threading
import time

import pytest
import labhq.runner.auth_broker as auth_broker

from labhq.runner.auth_broker import ACKNOWLEDGEMENT, ConstantResponseBroker, ProcessBroker, SyntheticBroker, fixed_provider_transport


SECRET = b"synthetic-secret-123"
pytestmark = pytest.mark.skipif(os.name == "nt" or not hasattr(socket, "AF_UNIX"), reason="Linux AF_UNIX required")


def request(path, value):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(4)
        client.connect(str(path))
        client.sendall(json.dumps(value).encode() + b"\n")
        return json.loads(client.recv(65536))


@pytest.fixture
def broker(tmp_path):
    tmp_path.chmod(0o700)
    calls = []

    def fake(body, credential):
        calls.append((body, credential))
        return b"synthetic-response"

    instance = SyntheticBroker(tmp_path, "run-1", SECRET, fake)
    instance.start()
    yield instance, calls
    instance.close()


def test_fixed_protocol_allows_synthetic_upstream(broker):
    instance, calls = broker
    response = request(instance.path, {"run_id": "run-1", "body": "aGVsbG8="})
    assert base64.b64decode(response["body"]) == b"synthetic-response"
    assert calls == [(b"hello", SECRET)]


@pytest.mark.parametrize("extra", [
    {"url": "http://127.0.0.1/"}, {"method": "CONNECT"},
    {"url": "https://other.invalid/"}, {"redirect": True},
])
def test_destination_and_connect_are_rejected(broker, extra):
    instance, calls = broker
    value = {"run_id": "run-1", "body": ""}
    value.update(extra)
    assert request(instance.path, value) == {"error": "request rejected"}
    assert calls == []


def test_wrong_run_is_rejected(broker):
    instance, calls = broker
    assert request(instance.path, {"run_id": "other", "body": ""}) == {"error": "request rejected"}
    assert calls == []


def test_literal_secret_reflection_is_rejected(tmp_path):
    tmp_path.chmod(0o700)
    instance = SyntheticBroker(tmp_path, "run-1", SECRET, lambda body, credential: b"echo:" + credential)
    instance.start()
    try:
        assert request(instance.path, {"run_id": "run-1", "body": ""}) == {"error": "request rejected"}
    finally:
        instance.close()


def test_errors_do_not_reflect_exception_or_credential(tmp_path):
    tmp_path.chmod(0o700)

    def fail(body, credential):
        raise RuntimeError(credential.decode())

    instance = SyntheticBroker(tmp_path, "run-1", SECRET, fail)
    instance.start()
    try:
        assert request(instance.path, {"run_id": "run-1", "body": ""}) == {"error": "request rejected"}
    finally:
        instance.close()


@pytest.mark.parametrize("upstream_output", [
    SECRET, base64.b64encode(SECRET), b"synthetic%2Dsecret%2D123",
    b'"synthetic\\u002dsecret\\u002d123"', SECRET[:7],
    [SECRET[:7], SECRET[7:]], {"header": SECRET, "body": SECRET},
])
def test_constant_response_discards_all_upstream_content(tmp_path, upstream_output, capsys):
    tmp_path.chmod(0o700)
    instance = ConstantResponseBroker(tmp_path, "run-1", SECRET, lambda body, credential: upstream_output)
    instance.start()
    try:
        response = request(instance.path, {"run_id": "run-1", "body": ""})
        assert response == {"body": base64.b64encode(ACKNOWLEDGEMENT).decode()}
    finally:
        instance.close()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_constant_response_discards_upstream_exception(tmp_path, capsys):
    tmp_path.chmod(0o700)
    def fail(body, credential):
        raise RuntimeError(base64.b64encode(credential).decode())
    instance = ConstantResponseBroker(tmp_path, "run-1", SECRET, fail)
    instance.start()
    try:
        assert request(instance.path, {"run_id": "run-1", "body": ""}) == {"error": "request rejected"}
    finally:
        instance.close()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_constant_response_wire_bytes(tmp_path):
    tmp_path.chmod(0o700)
    instance = ConstantResponseBroker(tmp_path, "run-1", SECRET, lambda body, credential: credential)
    instance.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(4)
            client.connect(str(instance.path))
            client.sendall(b'{"run_id":"run-1","body":""}\n')
            assert client.recv(65536) == b'{"body":"YWNjZXB0ZWQ="}\n'
    finally:
        instance.close()


def test_close_cancels_incomplete_client_and_removes_socket(tmp_path):
    tmp_path.chmod(0o700)
    instance = ConstantResponseBroker(tmp_path, "run-1", SECRET, lambda body, credential: b"")
    instance.start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(4)
        client.connect(str(instance.path))
        client.sendall(b'{"run_id":')
        instance.close()
        assert not instance.path.exists()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as reconnect:
            with pytest.raises(OSError):
                reconnect.connect(str(instance.path))


def test_close_removes_socket_and_rejects_reconnect(broker):
    instance, _ = broker
    instance.close()
    assert not instance.path.exists()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        with pytest.raises(OSError):
            client.connect(str(instance.path))


def test_bubblewrap_network_namespace_can_reach_socket(broker):
    if not shutil.which("bwrap") or not os.path.exists("/usr/bin/python3"):
        pytest.skip("bubblewrap fixture unavailable")
    instance, _ = broker
    child = ("import socket,json;s=socket.socket(socket.AF_UNIX);"
             "s.connect('/run/labhq/provider.sock');"
             "s.sendall(b'{\"run_id\":\"run-1\",\"body\":\"\"}\\n');"
             "assert json.loads(s.recv(65536))=={'body':'c3ludGhldGljLXJlc3BvbnNl'}")
    args = ["bwrap", "--unshare-user", "--unshare-net", "--ro-bind", "/usr", "/usr"]
    for path in ("/lib", "/lib64"):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    args += ["--dir", "/run", "--dir", "/run/labhq", "--bind", str(instance.path),
             "/run/labhq/provider.sock", "/usr/bin/python3", "-c", child]
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_process_broker_ack_and_exception_are_constant(tmp_path, capfd):
    tmp_path.chmod(0o700)
    def fake(body, credential):
        os.write(1, credential)
        os.write(2, credential)
        if body:
            raise RuntimeError(credential.decode())
        return credential
    instance = ProcessBroker(tmp_path, "run-1", SECRET, fake)
    instance.start()
    try:
        assert request(instance.path, {"run_id": "run-1", "body": ""}) == {"body": "YWNjZXB0ZWQ="}
        assert request(instance.path, {"run_id": "run-1", "body": "eA=="}) == {"error": "request rejected"}
    finally:
        instance.close()
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("cancel", [False, True])
def test_process_timeout_or_close_kills_upstream(tmp_path, cancel):
    tmp_path.chmod(0o700)
    marker = tmp_path / "pid"
    def hang(body, credential):
        marker.write_text(str(os.getpid()))
        while True:
            time.sleep(1)
    instance = ProcessBroker(tmp_path, "run-1", SECRET, hang, deadline=0.5)
    instance.start()
    responses = []
    def client():
        try:
            responses.append(request(instance.path, {"run_id": "run-1", "body": ""}))
        except (OSError, ValueError):
            pass
    thread = threading.Thread(target=client)
    thread.start()
    limit = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < limit:
        time.sleep(0.01)
    assert marker.exists()
    pid = int(marker.read_text())
    if cancel:
        instance.close()
    thread.join(4)
    assert not thread.is_alive()
    instance.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    if not cancel:
        assert responses == [{"error": "request rejected"}]
    assert not instance.path.exists()


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_transport_rejects_private_and_mixed_dns(monkeypatch, address):
    records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
               (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: records)
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("unsafe DNS connected"))
    with pytest.raises(ValueError):
        fixed_provider_transport(b"{}", SECRET)


@pytest.mark.parametrize("status", [200, 302, 401])
def test_transport_pins_address_tls_hostname_and_never_follows_redirect(monkeypatch, status):
    events = []
    class FakeSocket:
        def settimeout(self, value): pass
        def connect(self, address): events.append(("connect", address))
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def sendall(self, data): events.append(("request", data))
    class Context:
        def wrap_socket(self, sock, server_hostname):
            events.append(("tls", server_hostname))
            return sock
    class Response:
        def __init__(self, sock): self.status = status
        def begin(self): pass
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(socket, "socket", lambda *a: FakeSocket())
    monkeypatch.setattr(auth_broker.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(auth_broker.http.client, "HTTPResponse", Response)
    if status == 200:
        fixed_provider_transport(b"{}", SECRET)
    else:
        with pytest.raises(ValueError): fixed_provider_transport(b"{}", SECRET)
    assert events[0] == ("connect", ("8.8.8.8", 443))
    assert events[1] == ("tls", "api.anthropic.com")
    assert len(events) == 3
    assert events[2][1].startswith(b"POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n")
