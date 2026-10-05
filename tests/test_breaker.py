from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.breaker import Breaker, trip_text
from labhq.runner.daemon import Runner
from labhq.settings import Settings


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
                await ctx.emit("agent.tool", {"name": "shell", "input": "cat missing.txt"})
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
