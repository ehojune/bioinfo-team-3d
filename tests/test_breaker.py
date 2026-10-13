import json
from types import SimpleNamespace

import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunState
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.breaker import Breaker, trip_text
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import tool_input_fingerprint


def raw_call(route, value):
    if route == "claude_code":
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "shell", "input": value}]}}
    if route == "antigravity":
        return {"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE",
                "tool_name": "shell", "tool_info": {"parameters": value}}}
    items = {
        "codex_shell": {"type": "command_execution", "command": value},
        "codex_mcp": {"type": "mcp_tool_call", "server": "test", "tool": "search", "arguments": value},
        "codex_edit": {"type": "file_change", "changes": [{"path": value}]},
        "codex_web": {"type": "web_search", "action": {"query": value}},
    }
    return {"type": "item.completed" if route in ("codex_edit", "codex_web") else "item.started",
            "item": items[route]}


ROUTES = ["claude_code", "antigravity", "codex_shell", "codex_mcp", "codex_edit", "codex_web"]


async def replay_calls(route, values):
    events, trips = [], []
    breaker = Breaker()

    async def emit(kind, data):
        events.append((kind, data))
        trip = breaker.observe(kind, data)
        if trip:
            trips.append(trip)

    adapter = get_adapter("codex" if route.startswith("codex_") else route, Settings())
    state = RunState()
    for value in values:
        await adapter.handle_line(json.dumps(raw_call(route, value)), state, SimpleNamespace(emit=emit))
    return events, trips


@pytest.mark.parametrize("route", ROUTES)
async def test_adapter_different_tails_after_450_characters_do_not_trip(route):
    events, trips = await replay_calls(route, ["x" * 450 + f" tail-{i}" for i in range(8)])
    assert len(events) == 8
    assert len({data["input"] for _, data in events}) == 1, "the UI previews still coincide"
    assert trips == [], "different raw calls must not trip just because their previews coincide"
    assert len({data["input_fingerprint"] for _, data in events}) == 8
    for _, data in events:
        assert len(data["input_fingerprint"]) == 64
        assert "tail-" not in json.dumps(data), "only the hash, not extra raw input, is transmitted"


@pytest.mark.parametrize("route", ROUTES)
async def test_adapter_same_long_input_trips_once(route):
    _, trips = await replay_calls(route, ["x" * 450 + " identical tail"] * 12)
    assert len(trips) == 1
    assert trips[0]["count"] == 8 and trips[0]["mode"] == "shadow"


@pytest.mark.parametrize("route", ROUTES)
async def test_adapter_string_whitespace_is_part_of_identity(route):
    _, trips = await replay_calls(route, ["echo 'a b'", "echo 'a  b'"] * 6)
    assert trips == []


@pytest.mark.parametrize("route", ["claude_code", "antigravity", "codex_mcp"])
async def test_adapter_json_key_order_does_not_change_identity(route):
    a = {"query": "a  b", "options": {"limit": 5, "sort": "date"}}
    b = {"options": {"sort": "date", "limit": 5}, "query": "a  b"}
    _, trips = await replay_calls(route, [a, b] * 6)
    assert len(trips) == 1 and trips[0]["count"] == 8


@pytest.mark.parametrize("route", ["claude_code", "antigravity", "codex_shell", "codex_mcp", "codex_web"])
@pytest.mark.parametrize("value", [None, ""])
async def test_adapter_unreported_input_never_trips(route, value):
    events, trips = await replay_calls(route, [value] * 12)
    assert trips == []
    assert all(data["input_fingerprint"] is None for _, data in events)


@pytest.mark.parametrize("value", [{}, [], False, 0, " "])
def test_reported_empty_or_false_values_have_an_identity(value):
    assert tool_input_fingerprint("shell", value) is not None


def test_fingerprint_keeps_tool_name_whitespace_and_4096_character_policy():
    fingerprint = tool_input_fingerprint
    assert fingerprint("Read", "same") != fingerprint("Write", "same")
    assert fingerprint("shell", "a\nb") != fingerprint("shell", "a b")
    assert fingerprint("shell", {"command": "a\nb"}) != fingerprint("shell", {"command": "a b"})
    assert fingerprint("shell", "x" * 4095 + "a") != fingerprint("shell", "x" * 4095 + "b")
    assert fingerprint("shell", "x" * 4096 + "a") == fingerprint("shell", "x" * 4096 + "b")


@pytest.mark.parametrize("legacy", [
    {"input": "x" * 399 + "…"},
    {"input": "same prefix", "input_truncated": True},
    {"input": ""},
    {},
])
def test_unknown_or_legacy_truncated_input_breaks_the_repeat_streak(legacy):
    breaker = Breaker()
    for _ in range(7):
        assert breaker.observe(*tool()) is None
    for _ in range(12):
        assert breaker.observe("agent.tool", {"name": "shell", **legacy}) is None
    for _ in range(7):
        assert breaker.observe(*tool()) is None
    assert breaker.observe(*tool())["count"] == 8


def test_unknown_inputs_still_count_failed_calls_once_each():
    breaker = Breaker()
    trips = []
    for _ in range(5):
        assert breaker.observe("agent.tool", {"name": "shell", "input_fingerprint": None}) is None
        trips.extend(breaker.observe("agent.tool_error", {"text": "line"}) for _ in range(3))
    assert [trip for trip in trips if trip] == [
        {"signal": "tool_errors", "count": 5, "tool": "shell", "mode": "shadow"}]


def tool(name="shell", text="ls outputs"):
    return "agent.tool", {"name": name, "input": text}


def test_the_same_call_eight_times_trips_once():
    breaker = Breaker()
    trips = [breaker.observe(*tool()) for _ in range(12)]
    assert trips[:7] == [None] * 7
    assert trips[7] == {"signal": "repeat", "count": 8, "tool": "shell", "mode": "shadow"}
    assert trips[8:] == [None] * 4, "a signal is reported once per task"


def test_a_different_input_restarts_the_repeat_count():
    breaker = Breaker()
    for i in range(20):
        assert breaker.observe(*tool(text=f"ls step{i % 2}")) is None


def test_calls_without_a_reported_input_never_count_as_repeats():
    # PR #414 review: Codex MCP calls used to arrive without arguments; distinct searches must not look identical.
    breaker = Breaker()
    for _ in range(12):
        assert breaker.observe("agent.tool", {"name": "mcp:pubmed.search_articles"}) is None
        assert breaker.observe("agent.tool", {"name": "mcp:pubmed.search_articles", "input": ""}) is None


def test_five_failed_calls_in_a_row_trip_and_a_quiet_call_ends_the_streak():
    breaker = Breaker()
    for i in range(4):
        assert breaker.observe(*tool(text=f"try {i}")) is None
        assert breaker.observe("agent.tool_error", {"text": "exit 1"}) is None
    assert breaker.observe(*tool(text="no error after this")) is None
    assert breaker.observe(*tool(text="fresh")) is None  # the previous call reported no error
    for i in range(4):
        breaker.observe(*tool(text=f"again {i}"))
        assert breaker.observe("agent.tool_error", {"text": "exit 1"}) is None
    breaker.observe(*tool(name="Read", text="last"))
    trip = breaker.observe("agent.tool_error", {"text": "exit 1"})
    assert trip == {"signal": "tool_errors", "count": 5, "tool": "Read", "mode": "shadow"}
    assert "5번 연달아 실패" in trip_text(trip) and "멈추지 않음" in trip_text(trip)


def test_one_failed_call_with_several_error_lines_counts_once():
    breaker = Breaker()
    trips = []
    for i in range(5):
        breaker.observe(*tool(text=f"call {i}"))
        trips += [breaker.observe("agent.tool_error", {"text": "line"}) for _ in range(3)]
    assert [trip["count"] for trip in trips if trip] == [5]
    assert trips.index(next(trip for trip in trips if trip)) == 12, "the fifth call's first error line trips"


async def test_runner_reports_a_looping_agent_without_stopping_it(tmp_path, monkeypatch):
    # #59 shadow: the runner emits agent.breaker and an alert line once, and the task still finishes normally.
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="engineer", name="Engineer", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    sent = []
    monkeypatch.setattr(runner, "send", lambda event: sent.append(event))

    class Looping:
        async def run(self, ctx):
            for _ in range(10):
                await ctx.emit("agent.tool", {"name": "shell", "input": "cat missing.txt",
                                             "input_fingerprint": tool_input_fingerprint("shell", "cat missing.txt")})
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done anyway")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Looping())
    try:
        result = await runner.run_task(Task(agent_id="engineer", prompt="loop", meta={"kind": "step"}))
        assert result.ok and result.text == "done anyway"
        breakers = [event["data"] for event in sent if event["type"] == "agent.breaker"]
        assert breakers == [{"signal": "repeat", "count": 8, "tool": "shell", "mode": "shadow"}]
        alerts = [event["data"]["text"] for event in sent
                  if event["type"] == "agent.log" and event["data"].get("level") == "alert"]
        assert alerts == ["폭주 의심: shell를 같은 입력으로 8번 연달아 불렀어요 (경고만, 멈추지 않음)"]
    finally:
        runner.store.close()


async def test_codex_mcp_calls_carry_their_arguments():
    # PR #414 review: the started event keeps a short, stable fingerprint of the arguments.
    import json
    from types import SimpleNamespace

    from labhq.adapters import get_adapter
    from labhq.adapters.base import RunState

    events = []

    async def emit(kind, data):
        events.append((kind, data))

    adapter = get_adapter("codex", Settings())
    for query in ("CD276 fibroblast", "CD276 T cell"):
        line = {"type": "item.started", "item": {"type": "mcp_tool_call", "server": "pubmed", "tool": "search",
                                                 "arguments": {"query": query, "max_results": 5}}}
        await adapter.handle_line(json.dumps(line), RunState(), SimpleNamespace(emit=emit))
    assert [data["input"] for _, data in events] == [
        '{"max_results": 5, "query": "CD276 fibroblast"}', '{"max_results": 5, "query": "CD276 T cell"}']
