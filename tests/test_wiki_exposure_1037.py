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
import time  # noqa: E402

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

    view = {entry["id"]: {"r1": entry} for entry in roster}  # `effective_roster(hosts=True)`: per agent, runner -> entry
    return SimpleNamespace(effective_roster=lambda hosts=False: view if hosts else {}, requests=requests,
                           aupdate_request=update)


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
    hub.effective_roster = lambda hosts=False: {"worker": {"r1": {"id": "worker", "wiki_tools": False}}}
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


@pytest.mark.parametrize("created,state", [(wiki_exposure.WIKI_SHIPPED_AT - 1, "none"), (wiki_exposure.WIKI_SHIPPED_AT, "unknown"),
                                         (wiki_exposure.WIKI_SHIPPED_AT + 1, "unknown"), (None, "unknown"), (0, "unknown"),
                                         (-5, "unknown"), ("1700000000", "unknown"), (True, "unknown")])
def test_a_request_without_a_record_is_none_only_when_it_predates_the_wiki_tool(created, state):
    assert wiki_exposure.exposure({"created_at": created, "updated_at": created}) == state


@pytest.mark.parametrize("created,updated,state", [(1.0, 2.0, "none"), (1.0, wiki_exposure.WIKI_SHIPPED_AT + 1, "unknown"),
                                                 (1.0, None, "unknown"), (1.0, "2", "unknown"), (None, 2.0, "unknown")])
def test_a_request_created_before_the_wiki_tool_but_updated_after_it_stays_unknown(created, updated, state):
    """It can have been resumed while the tool existed, before exposure was recorded."""
    assert wiki_exposure.exposure({"created_at": created, "updated_at": updated}) == state


def test_an_old_unrecorded_request_dispatched_to_a_wiki_runner_becomes_possible():
    hub = gateway([{"id": "worker", "wiki_tools": True}], exposure=None)
    hub.requests["req_1"]["created_at"] = hub.requests["req_1"]["updated_at"] = wiki_exposure.WIKI_SHIPPED_AT - 100
    assert wiki_exposure.publication_blocker(hub.requests["req_1"]) is None
    bind(hub)
    assert hub.requests["req_1"][wiki_exposure.KEY] == wiki_exposure.POSSIBLE
    assert wiki_exposure.publication_blocker(hub.requests["req_1"]) is not None


def test_a_recorded_possible_is_not_lowered_by_an_old_created_at():
    assert wiki_exposure.exposure({wiki_exposure.KEY: "possible", "created_at": 1.0, "updated_at": 1.0}) == wiki_exposure.POSSIBLE


# ---- a request opened from another inherits its exposure; the record survives a restart (#1037 rev16) ----
from tests.test_research_cp1_revise import _gateway, _old_request  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,expected", [("none", "none"), ("possible", "possible"), (None, "unknown"),
                                            ("unknown", "unknown"), ("NONE", "unknown")])
async def test_a_cp1_revision_starts_at_the_exposure_of_the_request_it_continues(tmp_path, monkeypatch, parent, expected):
    hub = _gateway(tmp_path, monkeypatch)
    _old_request(hub, **({} if parent is None else {wiki_exposure.KEY: parent}))
    new = hub.create_revised_request("old", "고칠 점")
    await asyncio.sleep(0)
    assert wiki_exposure.exposure(hub.requests[new]) == expected
    assert (wiki_exposure.publication_blocker(hub.requests[new]) is None) is (expected == "none")
    assert wiki_exposure.exposure(hub.store.get("request", new)) == expected  # in the first save, so a restart keeps it


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,expected", [("none", "none"), ("possible", "possible"), (None, "unknown")])
async def test_the_async_cp1_revision_inherits_the_same_way(tmp_path, monkeypatch, parent, expected):
    hub = _gateway(tmp_path, monkeypatch)
    _old_request(hub, **({} if parent is None else {wiki_exposure.KEY: parent}))
    new = await hub.acreate_revised_request("old", "고칠 점")
    assert wiki_exposure.exposure(hub.requests[new]) == expected


@pytest.mark.parametrize("parent,expected", [("none", "none"), ("possible", "possible"), (None, "unknown")])
def test_inherited_never_lowers_what_the_parent_had(parent, expected):
    assert wiki_exposure.inherited({} if parent is None else {wiki_exposure.KEY: parent}) == expected


def test_the_recorded_exposure_survives_a_gateway_restart_and_an_older_runner_raises_it(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    hub = create_app(settings).state.hub
    monkeypatch.setattr(hub, "track_request", lambda rid, coro: coro.close())
    clean = hub.create_request(RequestIn(text="synthetic clean", mode="direct", agent_id="analyst"))
    old_runner = hub.create_request(RequestIn(text="synthetic old runner", mode="direct", agent_id="analyst"))
    capable = hub.create_request(RequestIn(text="synthetic capable", mode="direct", agent_id="analyst"))
    rosters = {"analyst": {"old": {"id": "analyst"},  # a runner that predates the wiki_tools key
                           "new": {"id": "analyst", "wiki_tools": True}, "off": {"id": "analyst", "wiki_tools": False}}}
    monkeypatch.setattr(hub, "effective_roster", lambda hosts=False: rosters)

    async def dispatch_to(rid, runner):
        await hub._bind_wiki_exposure(Task(agent_id="analyst", prompt="x", request_id=rid), runner)

    asyncio.run(dispatch_to(clean, "off"))
    asyncio.run(dispatch_to(old_runner, "old"))
    asyncio.run(dispatch_to(capable, "new"))
    assert [wiki_exposure.exposure(hub.requests[r]) for r in (clean, old_runner, capable)] == ["none", "possible", "possible"]
    hub.store.close()

    restarted = create_app(settings).state.hub  # the record is read back, not recomputed
    assert [wiki_exposure.exposure(restarted.requests[r]) for r in (clean, old_runner, capable)] == ["none", "possible", "possible"]
    assert wiki_exposure.publication_blocker(restarted.requests[clean]) is None
    assert wiki_exposure.publication_blocker(restarted.requests[old_runner]) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("parent,expected", [("none", "none"), ("possible", "possible"), (None, "unknown")])
async def test_a_retry_after_a_model_refusal_starts_at_the_exposure_of_the_refused_request(tmp_path, monkeypatch, parent, expected):
    hub = _gateway(tmp_path, monkeypatch)
    hub.requests["r"] = {"id": "r", "text": "synthetic question", "mode": "orchestrate", "work_kind": "research",
                         "status": "failed", "outcome": "model_refused", "created_at": time.time(),
                         "requested_budget_usd": 2, "budget_usd": 3,
                         **({} if parent is None else {wiki_exposure.KEY: parent})}
    hub.save_request("r")
    new = hub._model_refusal_request("r", {"action": "edit", "text": "synthetic edited question"})
    await asyncio.sleep(0)
    assert new != "r" and wiki_exposure.exposure(hub.requests[new]) == expected
    assert wiki_exposure.exposure(hub.store.get("request", new)) == expected
