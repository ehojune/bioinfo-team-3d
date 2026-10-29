"""The PI card for a WSL runner that is not ready (#612 stage 2): asked once per request, the setup runs only after a
yes, nothing waits for a runner that will not come on its own, and no subprocess output reaches the card or the feed.
The setup itself is replaced by fakes: no WSL is started here."""
from __future__ import annotations

import asyncio

import pytest

from labhq import cli, wsl_setup
from labhq.gateway.server import Hub
from labhq.models import Task
from labhq.pi_text import approval_copy
from labhq.settings import Settings

ADMIN = wsl_setup.ADMIN_HINTS["java"]
MISSING = [{"group": "WSL", "name": "Java 실행", "status": "warn", "detail": "없음", "hint": ADMIN},
           {"group": "WSL", "name": "Nextflow 실행", "status": "warn", "detail": "없음", "hint": "labhq setup-wsl을 실행하세요."},
           {"group": "WSL", "name": "WSL 배포판", "status": "ok", "detail": "ok", "hint": ""}]
READY = [{**row, "status": "ok", "detail": "ok"} for row in MISSING]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "text": "분석", "mode": "direct", "status": "running"}
    hub.save_request("r")
    published = []

    async def publish(event, **kwargs):
        published.append(event)

    hub.publish = publish
    hub.published = published
    monkeypatch.setattr(wsl_setup, "configured", lambda s: True)
    monkeypatch.setattr(cli, "_gateway_is_local", lambda s: True)
    return hub


def task(agent="bioinfo-agent", request="r"):
    return Task(agent_id=agent, request_id=request, prompt="p", meta={"kind": "direct"})


async def card_of(hub):
    for _ in range(200):
        cards = [entry["approval"] for entry in hub.approvals.values() if entry["approval"]["kind"] == "wsl_setup"]
        if cards:
            return cards[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("no wsl_setup card was opened")


async def test_a_yes_runs_the_setup_once_and_a_second_task_of_the_request_is_not_asked_again(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: calls.append("setup") or READY)
    pending = asyncio.ensure_future(hub.offer_wsl_setup(task()))
    card = await card_of(hub)
    assert card["summary"] == "WSL 준비가 덜 됐어요. 셋업할까요?"
    assert card["detail"]["missing"] == ["Java 실행", "Nextflow 실행"] and card["detail"]["admin_commands"] == [ADMIN]
    assert calls == [], "nothing runs before the PI answers"
    await hub.resolve_approval(card["id"], True, "")
    assert await pending is None, "set up: the request carries on and the runner is awaited as before"
    assert calls == ["setup"]
    assert hub.requests["r"]["wsl_setup"]["status"] == "ready"
    assert await hub.offer_wsl_setup(task()) is None
    assert calls == ["setup"] and len([e for e in hub.published if e["type"] == "approval.requested"]) == 1


async def test_a_no_stops_the_task_at_once_without_running_anything_and_is_remembered(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: calls.append("setup") or READY)
    pending = asyncio.ensure_future(hub.offer_wsl_setup(task()))
    card = await card_of(hub)
    await hub.resolve_approval(card["id"], False, "")
    stopped = await pending
    assert stopped.ok is False and stopped.error_kind == "wsl_not_ready" and stopped.cost_usd == 0.0
    assert "labhq setup-wsl" in stopped.error and calls == []
    again = await hub.offer_wsl_setup(task())
    assert again.error_kind == "wsl_not_ready", "the answer holds for the whole request"
    assert len([e for e in hub.published if e["type"] == "approval.requested"]) == 1


async def test_the_administrator_commands_that_remain_are_shown_and_the_task_does_not_wait(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: MISSING)  # still missing afterwards
    pending = asyncio.ensure_future(hub.offer_wsl_setup(task()))
    await hub.resolve_approval((await card_of(hub))["id"], True, "")
    stopped = await pending
    assert stopped.error_kind == "wsl_not_ready" and ADMIN in stopped.error and "Java 실행" in stopped.error
    assert hub.requests["r"]["wsl_setup"]["status"] == "failed"


async def test_a_setup_that_raises_leaves_only_the_exception_type_not_its_text(hub, monkeypatch):
    def boom(s):
        raise RuntimeError("TOKEN=secret-from-the-installer")

    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", boom)
    pending = asyncio.ensure_future(hub.offer_wsl_setup(task()))
    await hub.resolve_approval((await card_of(hub))["id"], True, "")
    stopped = await pending
    assert "RuntimeError" in stopped.error and "secret-from-the-installer" not in stopped.error
    assert "secret-from-the-installer" not in repr(hub.published)


async def test_the_setup_is_only_advice_when_the_gateway_is_not_on_this_pc(hub, monkeypatch):
    monkeypatch.setattr(cli, "_gateway_is_local", lambda s: False)
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: pytest.fail("a remote gateway must not run the setup"))
    stopped = await hub.offer_wsl_setup(task())
    assert stopped.error_kind == "wsl_not_ready" and "같은 PC" in stopped.error
    assert not [e for e in hub.published if e["type"] == "approval.requested"], "no card: nothing to approve"


@pytest.mark.parametrize("case", ["other agent", "not configured", "check fails", "nothing missing", "unknown request"])
async def test_no_card_when_it_does_not_apply(hub, monkeypatch, case):
    monkeypatch.setattr(wsl_setup, "check", lambda s: READY if case == "nothing missing" else MISSING)
    if case == "not configured":
        monkeypatch.setattr(wsl_setup, "configured", lambda s: False)
    if case == "check fails":
        def broken(s):
            raise OSError("wsl.exe is not here")

        monkeypatch.setattr(wsl_setup, "check", broken)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: pytest.fail("must not run"))
    target = task("engineer" if case == "other agent" else "bioinfo-agent", "nope" if case == "unknown request" else "r")
    assert await hub.offer_wsl_setup(target) is None
    assert not [e for e in hub.published if e["type"] == "approval.requested"]


def test_the_card_text_tells_the_pi_what_is_missing_and_what_a_yes_does():
    copy = approval_copy("wsl_setup", "WSL 준비가 덜 됐어요. 셋업할까요?", {"missing": ["Java 실행", "Nextflow 실행"]})
    assert copy["pi_summary"] == "WSL 준비가 덜 됐어요. 셋업할까요?"
    assert "Java 실행" in copy["pi_reason"] and "Nextflow 실행" in copy["pi_reason"]
    assert "관리자 권한이 필요 없는" in copy["pi_outcome"] and "기다리지 않고" in copy["pi_outcome"]


def test_card_detail_separates_administrator_commands_from_what_setup_can_do():
    detail = wsl_setup.card_detail(MISSING)
    assert detail == {"missing": ["Java 실행", "Nextflow 실행"], "admin_commands": [ADMIN]}
    assert wsl_setup.card_detail(READY) == {"missing": [], "admin_commands": []}


async def test_dispatch_asks_before_it_waits_for_a_runner_that_is_not_coming(hub, monkeypatch):
    # The card comes first: without it the task would sit in waiting_for_runner for runner_wait_s and then fail.
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: pytest.fail("a no must not run the setup"))

    async def waited(*args, **kwargs):
        pytest.fail("dispatch must not wait for the runner after a no")

    monkeypatch.setattr(hub, "wait_for_runner", waited)
    pending = asyncio.ensure_future(hub.dispatch(task()))
    await hub.resolve_approval((await card_of(hub))["id"], False, "")
    result = await asyncio.wait_for(pending, 10)
    assert result.error_kind == "wsl_not_ready" and result.ok is False
