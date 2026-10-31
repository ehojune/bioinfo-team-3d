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
MISSING = [{"group": "WSL", "key": "java", "name": "Java 실행", "status": "warn", "detail": "없음", "hint": ADMIN},
           {"group": "WSL", "key": "nextflow", "name": "Nextflow 실행", "status": "warn", "detail": "없음",
            "hint": "labhq setup-wsl을 실행하세요."},
           {"group": "WSL", "key": "distro", "name": "WSL 배포판", "status": "ok", "detail": "ok", "hint": ""}]
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
    hub.s.gateway.runner_wait_s = 5
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


def test_a_hint_built_from_this_installs_paths_is_still_an_administrator_command():
    # The Claude login hint names this install's folder, so it is never equal to the fixed hint text.
    login = {"group": "WSL", "key": "claude_login", "name": "WSL Claude 로그인", "status": "warn", "detail": "없음",
             "hint": "WSL runner 경로 /home/x/.labhq/claude-staff를 CLAUDE_CONFIG_DIR로 지정한 뒤 claude auth login을 실행하세요."}
    assert wsl_setup.card_detail([login]) == {"missing": ["WSL Claude 로그인"], "admin_commands": [login["hint"]]}


@pytest.mark.parametrize("roster", [{"runner_agents": {"lab-native": [{"id": "bioinfo-agent"}]}},
                                    {"last_runner_rosters": {"lab-native": {"agents": [{"id": "bioinfo-agent"}]}}}])
async def test_an_agent_a_native_runner_hosts_keeps_the_normal_wait_when_that_runner_is_away(hub, monkeypatch, roster):
    # A native runner that hosts the agent and drops for a moment is not a WSL problem: no card, no early stop.
    for name, value in roster.items():
        setattr(hub, name, value)
    monkeypatch.setattr(wsl_setup, "check", lambda s: pytest.fail("the WSL must not even be probed"))
    assert await hub.offer_wsl_setup(task()) is None
    assert not [e for e in hub.published if e["type"] == "approval.requested"]


async def test_a_setup_that_outlives_its_limit_is_not_started_a_second_time(hub, monkeypatch):
    import threading

    release = threading.Event()
    started = []
    monkeypatch.setattr(wsl_setup, "SETUP_LIMIT_S", 0.05)
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)

    def slow(s):
        started.append(1)
        release.wait(10)
        return READY

    monkeypatch.setattr(wsl_setup, "setup", slow)
    pending = asyncio.ensure_future(hub.offer_wsl_setup(task()))
    await hub.resolve_approval((await card_of(hub))["id"], True, "")
    stopped = await pending
    assert stopped.error_kind == "wsl_not_ready" and "15분" in stopped.error and "백그라운드" in stopped.error
    # another request asks while the first thread still runs: no second thread
    hub.requests["r2"] = {"id": "r2", "text": "분석", "mode": "direct", "status": "running"}
    hub.save_request("r2")
    second = asyncio.ensure_future(hub.offer_wsl_setup(task(request="r2")))
    card = None
    for _ in range(200):
        cards = [e["approval"] for e in hub.approvals.values()
                 if e["approval"]["kind"] == "wsl_setup" and e["approval"]["request_id"] == "r2"]
        if cards:
            card = cards[0]
            break
        await asyncio.sleep(0.01)
    await hub.resolve_approval(card["id"], True, "")
    refused = await second
    assert "진행 중" in refused.error and started == [1]
    release.set()
    for _ in range(200):
        if not hub._wsl_setup_running:
            break
        await asyncio.sleep(0.01)
    assert not hub._wsl_setup_running


# ---- run_where: the PI picks "this PC's WSL" when sending the request (#612 stage 2, without #578) ----

def test_run_where_defaults_to_auto_and_accepts_only_known_places():
    from pydantic import ValidationError
    from labhq.gateway.server import RequestIn
    assert RequestIn(text="x").run_where == "auto"
    assert RequestIn(text="x", run_where="wsl").run_where == "wsl"
    with pytest.raises(ValidationError):
        RequestIn(text="x", run_where="cloud")


async def test_a_request_that_did_not_ask_for_wsl_gets_no_check_and_no_card(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "check", lambda s: pytest.fail("auto must not look at WSL"))
    hub.requests["r"]["run_where"] = "auto"
    assert await hub.prepare_wsl_request("r") is None
    assert not hub.approvals


def wsl_owns_the_agent(hub):
    """What the roster looks like once the WSL runner is ready and connected."""
    wsl_id = hub.s.runner.wsl.id
    hub.runners[wsl_id] = object()
    hub.agent_runner["bioinfo-agent"] = wsl_id
    return wsl_id


async def test_a_wsl_request_opens_the_card_runs_the_setup_after_a_yes_and_pins_the_agent_to_the_wsl_runner(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)

    def setup(s):
        calls.append("setup")
        return READY

    monkeypatch.setattr(wsl_setup, "setup", setup)
    hub.requests["r"]["run_where"] = "wsl"
    pending = asyncio.ensure_future(hub.prepare_wsl_request("r"))
    card = await card_of(hub)
    assert calls == [], "nothing runs before the PI answers"
    await hub.resolve_approval(card["id"], True, "")
    await asyncio.sleep(0.05)
    assert calls == ["setup"] and not pending.done(), "after the setup the request waits for the WSL runner itself"
    wsl_id = wsl_owns_the_agent(hub)  # the runner connects
    await asyncio.wait_for(pending, 5)
    assert hub.requests["r"]["wsl_pinned"] is True and "run_where_note" not in hub.requests["r"]
    probe = Task(agent_id="bioinfo-agent", request_id="r", prompt="p", meta={})
    hub._pin_wsl_request(probe)
    assert probe.meta["session_runner"] == wsl_id


@pytest.mark.parametrize("answer", ["no", "failed"])
async def test_a_wsl_request_that_cannot_get_wsl_carries_on_without_it_and_says_why(hub, monkeypatch, answer):
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: MISSING)  # a yes that leaves it unready
    hub.requests["r"]["run_where"] = "wsl"
    pending = asyncio.ensure_future(hub.prepare_wsl_request("r"))
    await hub.resolve_approval((await card_of(hub))["id"], answer == "failed", "")
    await asyncio.wait_for(pending, 5)  # never raises and never blocks the request
    request = hub.requests["r"]
    assert "WSL" in request["run_where_note"] and request["status"] == "running"
    assert not request.get("wsl_pinned"), "without WSL there is nothing to pin the request to"
    probe = Task(agent_id="bioinfo-agent", request_id="r", prompt="p", meta={})
    hub._pin_wsl_request(probe)
    assert "session_runner" not in probe.meta


async def test_a_wsl_request_with_a_ready_wsl_opens_no_card_and_is_pinned(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "check", lambda s: READY)
    wsl_owns_the_agent(hub)
    hub.requests["r"]["run_where"] = "wsl"
    assert await hub.prepare_wsl_request("r") is None
    assert not hub.approvals and hub.requests["r"]["wsl_pinned"] is True


async def test_a_wsl_runner_that_does_not_connect_in_time_leaves_a_note_and_no_pin(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "check", lambda s: READY)
    hub.s.gateway.runner_wait_s = 0.3
    hub.requests["r"]["run_where"] = "wsl"
    await asyncio.wait_for(hub.prepare_wsl_request("r"), 5)
    assert "시간 안에" in hub.requests["r"]["run_where_note"] and not hub.requests["r"].get("wsl_pinned")


async def test_a_native_runner_that_also_hosts_the_agent_does_not_skip_the_card_for_an_explicit_wsl_request(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
    monkeypatch.setattr(wsl_setup, "setup", lambda s: READY)
    monkeypatch.setattr(hub, "_native_hosts", lambda agent: True)  # a Windows runner lists bioinfo-agent too
    assert await hub.offer_wsl_setup(task()) is None and not hub.approvals, "without the choice it carries on natively"
    hub.requests["r"]["run_where"] = "wsl"
    pending = asyncio.ensure_future(hub.prepare_wsl_request("r"))
    card = await card_of(hub)  # the explicit choice is asked about even though a native runner could take the work
    assert card["kind"] == "wsl_setup"
    await hub.resolve_approval(card["id"], False, "")
    await asyncio.wait_for(pending, 5)


def test_a_pinned_request_refuses_a_native_target_at_zero_cost_and_takes_the_wsl_runner(hub):
    wsl_id = hub.s.runner.wsl.id
    hub.requests["r"]["wsl_pinned"] = True
    pinned = Task(agent_id="bioinfo-agent", request_id="r", prompt="p", meta={})
    hub._pin_wsl_request(pinned)
    refused = Hub._session_pin_refusal(pinned, "windows-native")
    assert refused is not None and refused.cost_usd == 0.0 and wsl_id in refused.error
    assert Hub._session_pin_refusal(pinned, wsl_id) is None
    other = Task(agent_id="analyst", request_id="r", prompt="p", meta={})
    hub._pin_wsl_request(other)
    assert "session_runner" not in other.meta, "only the WSL-bound agent is pinned"
    plain = Task(agent_id="bioinfo-agent", request_id="r2", prompt="p", meta={})
    hub._pin_wsl_request(plain)
    assert "session_runner" not in plain.meta, "a request that did not choose WSL is left alone"


def test_the_planner_is_told_when_the_pi_chose_wsl_and_only_then():
    from labhq.orchestrator.cso import format_capabilities
    roster = [{"id": "bioinfo-agent", "runner_id": "lab-wsl"}]
    chosen = format_capabilities(roster, {}, run_where="wsl")
    assert "이 PC의 WSL" in chosen and "bioinfo-agent" in chosen
    assert "이 PC의 WSL" not in format_capabilities(roster, {}, run_where="auto")
    assert "이 PC의 WSL" not in format_capabilities(roster, {})


def test_send_wsl_puts_run_where_in_the_request(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "_api", lambda s, method, path, **kw: seen.update(body=kw.get("json")) or "req_x")
    config = tmp_path / "labhq.yaml"
    config.write_text("gateway: {}" + chr(10), encoding="utf-8")
    monkeypatch.setenv("LABHQ_CONFIG", str(config))
    cli.main(["send", "분석", "--wsl", "--no-wait"])
    assert seen["body"]["run_where"] == "wsl"
    cli.main(["send", "분석", "--no-wait"])
    assert seen["body"]["run_where"] == "auto"


async def test_an_installation_without_a_wsl_runner_does_not_wait_a_day_for_one(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "configured", lambda s: False)  # no runner.wsl block in the config
    monkeypatch.setattr(wsl_setup, "check", lambda s: pytest.fail("nothing is configured to check"))
    hub.s.gateway.runner_wait_s = 86400  # the default: a wait here would hang the request
    hub.requests["r"]["run_where"] = "wsl"
    await asyncio.wait_for(hub.prepare_wsl_request("r"), 2)
    request = hub.requests["r"]
    assert "labhq setup-wsl" in request["run_where_note"] and not request.get("wsl_pinned")
    assert not hub.approvals and request["status"] == "running"


def test_wsl_is_refused_for_a_direct_request_to_any_other_staff_member(hub):
    from labhq.gateway.server import RequestIn
    with pytest.raises(ValueError, match="bioinfo-agent"):
        hub.create_request(RequestIn(text="x", mode="direct", agent_id="analyst", run_where="wsl"))
    with pytest.raises(ValueError, match="bioinfo-agent"):
        asyncio.run(hub.acreate_request(RequestIn(text="x", mode="direct", agent_id="analyst", run_where="wsl")))
    for allowed in (RequestIn(text="x", mode="direct", agent_id="bioinfo-agent", run_where="wsl"),
                    RequestIn(text="x", mode="orchestrate", run_where="wsl"),
                    RequestIn(text="x", mode="direct", agent_id="analyst")):
        assert hub._check_run_where(allowed) is None


def test_send_wsl_with_another_agent_is_refused_by_the_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: pytest.fail("a refused request must not be sent"))
    config = tmp_path / "labhq.yaml"
    config.write_text("gateway: {}" + chr(10), encoding="utf-8")
    monkeypatch.setenv("LABHQ_CONFIG", str(config))
    with pytest.raises(SystemExit):
        cli.main(["send", "분석", "--agent", "analyst", "--wsl", "--no-wait"])


# ---- one question for every way WSL can be unavailable: "ready" waits, everything else says why and carries on ----

@pytest.mark.parametrize("failure", ["check_raises", "setup_not_configured", "setup_raises"])
async def test_every_way_wsl_can_be_unavailable_ends_in_one_note_and_no_wait(hub, monkeypatch, failure):
    hub.s.gateway.runner_wait_s = 86400  # the default: any wait that is not for a ready runner would hang here
    hub.requests["r"]["run_where"] = "wsl"

    def raising(s):
        raise RuntimeError("wsl.exe is missing")

    if failure == "check_raises":
        monkeypatch.setattr(wsl_setup, "check", raising)
    elif failure == "setup_not_configured":
        monkeypatch.setattr(wsl_setup, "configured", lambda s: False)
    else:
        monkeypatch.setattr(wsl_setup, "check", lambda s: MISSING)
        monkeypatch.setattr(wsl_setup, "setup", raising)
    pending = asyncio.ensure_future(hub.prepare_wsl_request("r"))
    if failure == "setup_raises":
        await hub.resolve_approval((await card_of(hub))["id"], True, "")
    await asyncio.wait_for(pending, 3)
    request = hub.requests["r"]
    assert request["run_where_note"] and not request.get("wsl_pinned") and request["status"] == "running"
    assert "wsl.exe is missing" not in request["run_where_note"], "the exception text can carry output: only the kind"


async def test_the_only_wait_left_is_for_a_ready_wsl_runner_and_it_is_bounded(hub, monkeypatch):
    monkeypatch.setattr(wsl_setup, "check", lambda s: READY)
    hub.s.gateway.runner_wait_s = 86400
    monkeypatch.setattr("labhq.gateway.server.WSL_RUNNER_WAIT_S", 0.3)
    hub.requests["r"]["run_where"] = "wsl"
    await asyncio.wait_for(hub.prepare_wsl_request("r"), 3)  # ready, but the runner never connects
    assert "시간 안에" in hub.requests["r"]["run_where_note"] and not hub.requests["r"].get("wsl_pinned")


async def test_a_revised_request_keeps_where_the_old_one_ran(tmp_path, monkeypatch):
    from tests.test_research_cp1_revise import _gateway, _old_request
    gateway = _gateway(tmp_path, monkeypatch)
    _old_request(gateway, run_where="wsl")
    sync_new = gateway.create_revised_request("old", "note")
    assert gateway.requests[sync_new]["run_where"] == "wsl"
    gateway.requests["old"].pop("revised_to", None)
    for rid in [sync_new]:
        gateway.requests.pop(rid, None)
    async_new = await gateway.acreate_revised_request("old", "another note")
    assert gateway.requests[async_new]["run_where"] == "wsl"
    gateway.store.close()


def test_a_wsl_request_for_another_staff_member_is_a_400_not_a_422(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from labhq.gateway.server import create_app

    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    app = create_app(s)
    app.state.hub.agents["analyst"] = {"id": "analyst"}
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    with TestClient(app) as client:
        response = client.post("/api/requests", json={"text": "x", "mode": "direct", "agent_id": "analyst",
                                                      "run_where": "wsl"}, headers=auth)
    assert response.status_code == 400 and "bioinfo-agent" in response.json()["detail"]
