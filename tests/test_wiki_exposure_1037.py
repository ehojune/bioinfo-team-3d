"""Synthetic runner capability drift regression (#1037)."""

from types import SimpleNamespace

from labhq.models import AgentSpec, Engine
from labhq.runner.daemon import Runner
from labhq.settings import Settings, WikiSettings


def test_roster_off_cannot_later_attach_wiki_without_new_bound_dispatch(tmp_path, monkeypatch):
    runner = object.__new__(Runner)
    runner.s = Settings()
    runner.s.wiki = WikiSettings(enabled=False, root=str(tmp_path), external_model_allowed=True)
    agent = AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code, builtin_mcp=["wiki"])
    runner.registry = SimpleNamespace(agents={agent.id: agent})
    runner.ws_root = tmp_path
    monkeypatch.setattr("labhq.runner.daemon.adapter_preflight_error", lambda *args: None)
    assert runner.roster()[0]["wiki_tools"] is False

    # A stale roster must not authorize a different tool set on the eventual dispatch.
    runner.s.wiki.enabled = True
    servers = runner._mcp_servers(agent, {}, allow_ask=False)
    assert "labhq_wiki" not in [server.name for server in servers]


# ---- the gateway records the exposure and binds the decision at dispatch ----
import asyncio  # noqa: E402

import pytest  # noqa: E402

from labhq.evidence import wiki_exposure  # noqa: E402
from labhq.gateway.server import Hub, RequestIn, create_app  # noqa: E402
from labhq.models import Task  # noqa: E402


def gateway(roster, *, exposure=wiki_exposure.NONE):
    request = {"id": "req_1", "status": "running"}
    if exposure is not None:
        request[wiki_exposure.KEY] = exposure
    requests = {"req_1": request}

    async def update(rid, change):
        change(requests[rid])

    return SimpleNamespace(runner_agents={"r1": roster}, requests=requests, aupdate_request=update)


def bind(hub, task=None):
    task = task or Task(agent_id="worker", prompt="x", request_id="req_1")
    asyncio.run(Hub._bind_wiki_exposure(hub, task, "r1"))
    return task


def test_a_runner_that_offers_the_wiki_raises_the_exposure_and_the_task_carries_the_permission():
    hub = gateway([{"id": "worker", "wiki_tools": True}])
    task = bind(hub)
    assert task.meta["wiki_tools"] is True
    assert wiki_exposure.exposure(hub.requests["req_1"]) == wiki_exposure.POSSIBLE


def test_a_runner_without_the_wiki_leaves_a_clean_request_clean_and_gives_the_task_no_permission():
    hub = gateway([{"id": "worker", "wiki_tools": False}])
    task = bind(hub)
    assert task.meta["wiki_tools"] is False
    assert wiki_exposure.exposure(hub.requests["req_1"]) == wiki_exposure.NONE


@pytest.mark.parametrize("roster", [[{"id": "worker"}], [], [{"id": "other", "wiki_tools": False}]],
                         ids=["roster-without-the-key", "no-roster", "agent-not-listed"])
def test_a_runner_that_cannot_be_read_counts_as_able_to_reach_the_wiki_and_gets_no_permission(roster):
    hub = gateway(roster)
    task = bind(hub)
    assert task.meta["wiki_tools"] is False  # the runner cannot attach what the task does not allow
    assert wiki_exposure.exposure(hub.requests["req_1"]) == wiki_exposure.POSSIBLE


def test_the_exposure_is_never_lowered_by_a_later_dispatch():
    hub = gateway([{"id": "worker", "wiki_tools": True}])
    bind(hub)
    hub.runner_agents["r1"] = [{"id": "worker", "wiki_tools": False}]
    bind(hub)
    assert wiki_exposure.exposure(hub.requests["req_1"]) == wiki_exposure.POSSIBLE


def test_a_request_with_no_recorded_history_stays_unknown_after_any_dispatch():
    for roster in ([{"id": "worker", "wiki_tools": True}], [{"id": "worker", "wiki_tools": False}]):
        hub = gateway(roster, exposure=None)
        bind(hub)
        assert wiki_exposure.KEY not in hub.requests["req_1"]
        assert wiki_exposure.exposure(hub.requests["req_1"]) == wiki_exposure.UNKNOWN


def test_a_task_without_a_request_records_nothing():
    hub = gateway([{"id": "worker", "wiki_tools": True}])
    task = bind(hub, Task(agent_id="worker", prompt="x"))
    assert task.meta["wiki_tools"] is True and wiki_exposure.exposure(hub.requests["req_1"]) == wiki_exposure.NONE


@pytest.mark.parametrize("value,allowed", [("none", True), ("possible", False), ("unknown", False), (None, False),
                                          ("", False), (True, False), ("NONE", False)])
def test_only_a_recorded_none_opens_publication(value, allowed):
    request = {} if value is None else {wiki_exposure.KEY: value}
    assert (wiki_exposure.publication_blocker(request) is None) is allowed
    assert wiki_exposure.publication_blocker(None) is not None


def test_a_new_request_is_recorded_as_never_exposed(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path / "runs")
    app = create_app(settings)
    hub = app.state.hub
    monkeypatch.setattr(hub, "track_request", lambda rid, coro: coro.close())
    rid = hub.create_request(RequestIn(text="synthetic request", mode="direct", agent_id="analyst"))
    assert hub.requests[rid][wiki_exposure.KEY] == wiki_exposure.NONE


def test_only_a_dispatch_bound_to_the_wiki_attaches_the_tool(tmp_path):
    runner = object.__new__(Runner)
    runner.s = Settings()
    runner.s.wiki = WikiSettings(enabled=True, root=str(tmp_path), external_model_allowed=True)
    agent = AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code, builtin_mcp=["wiki"])
    runner.registry = SimpleNamespace(agents={agent.id: agent})
    runner.ws_root = tmp_path
    names = lambda bound: [s.name for s in runner._mcp_servers(agent, {}, allow_ask=False, wiki_bound=bound)]  # noqa: E731
    assert "labhq_wiki" in names(True) and "labhq_wiki" not in names(False)
    runner.s.wiki.enabled = False  # a runner that turned it off since keeps it off even for a bound dispatch
    assert "labhq_wiki" not in names(True)
