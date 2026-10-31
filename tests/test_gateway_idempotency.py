"""A repeated POST with the same Idempotency-Key is applied once (#1164)."""
import asyncio

import pytest
from fastapi import HTTPException

from labhq.gateway.idempotency import Idempotency
from labhq.settings import Settings


def _settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    return s


async def test_the_same_key_and_body_runs_the_work_once():
    memory, calls = Idempotency(), []

    async def work():
        calls.append(1)
        return {"n": len(calls)}

    assert await memory.run("s", "k", {"a": 1}, work) == {"n": 1}
    assert await memory.run("s", "k", {"a": 1}, work) == {"n": 1}
    assert len(calls) == 1


async def test_two_calls_in_flight_share_one_run():
    memory, calls, gate = Idempotency(), [], asyncio.Event()

    async def work():
        calls.append(1)
        await gate.wait()
        return "done"

    first = asyncio.ensure_future(memory.run("s", "k", {}, work))
    second = asyncio.ensure_future(memory.run("s", "k", {}, work))
    await asyncio.sleep(0.01)
    gate.set()
    assert await first == await second == "done"
    assert len(calls) == 1


async def test_no_key_a_new_key_a_new_body_or_a_new_scope_runs_again():
    memory, calls = Idempotency(), []

    async def work():
        calls.append(1)

    for scope, key, body in (("s", None, {}), ("s", None, {}), ("s", "", {}), ("s", "a", {}), ("s", "b", {}),
                             ("s", "a", {"x": 1}), ("t", "a", {})):
        await memory.run(scope, key, body, work)
    assert len(calls) == 7


async def test_a_refusal_is_not_remembered_so_the_caller_can_retry():
    memory, calls = Idempotency(), []

    async def work():
        calls.append(1)
        if len(calls) == 1:
            raise HTTPException(409, "runner is down")  # nothing was applied
        return "ok"

    with pytest.raises(HTTPException):
        await memory.run("s", "k", {}, work)
    assert await memory.run("s", "k", {}, work) == "ok"
    assert len(calls) == 2


async def test_an_ambiguous_failure_is_not_run_again_under_the_same_key():
    memory, calls = Idempotency(), []

    async def work():
        calls.append(1)
        raise ConnectionError("the frame may have reached the runner")

    with pytest.raises(ConnectionError):
        await memory.run("s", "k", {}, work)
    for _ in range(2):
        with pytest.raises(HTTPException) as again:
            await memory.run("s", "k", {}, work)
        assert again.value.status_code == 409
    assert len(calls) == 1
    with pytest.raises(ConnectionError):
        await memory.run("s", "other", {}, work)  # a new key is a new attempt
    assert len(calls) == 2


async def test_a_caller_that_goes_away_does_not_cancel_the_work_a_retry_waits_for():
    memory, calls, gate = Idempotency(), [], asyncio.Event()

    async def work():
        calls.append(1)
        await gate.wait()
        return "done"

    first = asyncio.ensure_future(memory.run("s", "k", {}, work))
    await asyncio.sleep(0.01)
    first.cancel()
    retry = asyncio.ensure_future(memory.run("s", "k", {}, work))
    await asyncio.sleep(0.01)
    gate.set()
    assert await retry == "done"
    assert len(calls) == 1


async def test_a_key_longer_than_the_limit_is_refused_not_cut():
    memory, calls = Idempotency(), []

    async def work():
        calls.append(1)

    long_key = "k" * 64
    await memory.run("s", long_key, {}, work)
    with pytest.raises(HTTPException) as refused:
        await memory.run("s", long_key + "a", {}, work)  # would be the same key if cut to 64
    assert refused.value.status_code == 422 and len(calls) == 1


async def test_the_memory_expires_and_is_bounded():
    now = [0.0]
    memory, calls = Idempotency(ttl_s=10, max_entries=2, clock=lambda: now[0]), []

    async def work():
        calls.append(1)

    await memory.run("s", "a", {}, work)
    now[0] = 11
    await memory.run("s", "a", {}, work)  # expired: runs again
    assert len(calls) == 2
    for key in ("b", "c", "d"):
        await memory.run("s", key, {}, work)
    assert len(memory._done) == 2


def _app(tmp_path, monkeypatch):
    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    sent = []

    async def send(runner_id, msg):
        sent.append(msg)

    monkeypatch.setattr(hub, "send_runner", send)
    return s, app, hub, sent


def test_a_double_post_of_contract_extend_is_sent_to_the_runner_once(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    s, app, hub, sent = _app(tmp_path, monkeypatch)
    s.recruit.enabled = True
    hub.agent_runner["c_x"] = "lab"
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    with TestClient(app) as client:
        body = {"action": "extend", "days": 14}
        for _ in range(2):
            assert client.post("/api/contracts/c_x", json=body, headers={**auth, "Idempotency-Key": "k1"}).status_code == 200
        assert [m["type"] for m in sent] == ["contract.update"]
        client.post("/api/contracts/c_x", json=body, headers={**auth, "Idempotency-Key": "k2"})  # a later, real click
        client.post("/api/contracts/c_x", json=body, headers=auth)  # a caller without a key is served as before
    assert len(sent) == 3


def test_a_double_post_of_recruit_counts_and_starts_one_recruitment(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    s, app, hub, sent = _app(tmp_path, monkeypatch)
    s.recruit.enabled = True
    hub.agent_runner[s.recruit.agent_id] = "lab"
    auth = {"Authorization": f"Bearer {s.gateway.client_token}", "Idempotency-Key": "hire-1"}
    with TestClient(app) as client:
        responses = [client.post("/api/recruit", json={"repo": "example/tool"}, headers=auth) for _ in range(2)]
    assert [r.status_code for r in responses] == [200, 200] and responses[0].json() == responses[1].json()
    assert [m["type"] for m in sent] == ["recruit.start"]
    assert hub.recruits_running["lab"]["count"] == 1


def test_a_refused_recruit_is_not_remembered(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    s, app, hub, sent = _app(tmp_path, monkeypatch)
    s.recruit.enabled = True
    auth = {"Authorization": f"Bearer {s.gateway.client_token}", "Idempotency-Key": "hire-2"}
    with TestClient(app) as client:
        assert client.post("/api/recruit", json={"repo": "example/tool"}, headers=auth).status_code == 409  # no runner
        hub.agent_runner[s.recruit.agent_id] = "lab"
        assert client.post("/api/recruit", json={"repo": "example/tool"}, headers=auth).status_code == 200
    assert [m["type"] for m in sent] == ["recruit.start"]


def test_a_double_post_of_a_new_request_creates_one_request(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    s, app, hub, sent = _app(tmp_path, monkeypatch)
    created = []

    async def create(body):
        created.append(body.text)
        return f"req_{len(created)}"

    monkeypatch.setattr(hub, "acreate_request", create)
    auth = {"Authorization": f"Bearer {s.gateway.client_token}", "Idempotency-Key": "send-1"}
    with TestClient(app) as client:
        ids = [client.post("/api/requests", json={"text": "분석해 줘"}, headers=auth).json()["request_id"]
               for _ in range(2)]
        other = client.post("/api/requests", json={"text": "다른 요청"}, headers=auth).json()["request_id"]
    assert ids == ["req_1", "req_1"] and other == "req_2" and created == ["분석해 줘", "다른 요청"]


def test_an_ambiguous_send_failure_is_answered_409_to_the_same_key_and_sent_once(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    s, app, hub, sent = _app(tmp_path, monkeypatch)
    s.recruit.enabled = True
    hub.agent_runner["c_x"] = "lab"

    async def break_mid_frame(runner_id, msg):
        sent.append(msg)
        raise ConnectionError("closed mid-frame")

    monkeypatch.setattr(hub, "send_runner", break_mid_frame)
    auth = {"Authorization": f"Bearer {s.gateway.client_token}", "Idempotency-Key": "k"}
    with TestClient(app, raise_server_exceptions=False) as client:
        body = {"action": "extend", "days": 14}
        assert client.post("/api/contracts/c_x", json=body, headers=auth).status_code == 500
        assert client.post("/api/contracts/c_x", json=body, headers=auth).status_code == 409
    assert len(sent) == 1
