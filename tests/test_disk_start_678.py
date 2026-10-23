"""#678: the runner checks the work drive when a task starts (card below the floor, warning below the doctor's
level), and `runner.bulk_data_root` is named in the task briefing."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import disk_space
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, Settings

GIB = disk_space.GIB


def _space(free_gib, total_gib=1000):
    return SimpleNamespace(free=int(free_gib * GIB), total=int(total_gib * GIB), used=0)


@pytest.fixture
def board(tmp_path, monkeypatch):
    s = Settings()
    for key in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(s.runner, key, str(tmp_path / key))
    s.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "staff")}
    runner = Runner(s)
    runner.registry.agents = {"worker": AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code,
                                                  builtin_mcp=[])}
    seen, cards, logs = [], [], []

    class Adapter:
        async def run(self, ctx):
            seen.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, cost_usd=0, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: Adapter())
    monkeypatch.setattr("labhq.doctor._paper2agent_row", lambda *a, **kw: {"status": "ok"})
    answer = {"approved": False}

    async def request_approval(req):
        cards.append(req)
        return dict(answer)

    monkeypatch.setattr(runner.broker, "request_approval", request_approval)
    original = runner.emit

    async def record(event, *a, **kw):
        if event.type == "agent.log":
            logs.append(event.data)
        return await original(event, *a, **kw)

    monkeypatch.setattr(runner, "emit", record)
    return SimpleNamespace(runner=runner, settings=s, seen=seen, cards=cards, logs=logs, answer=answer,
                           task=lambda: Task(agent_id="worker", request_id="r", prompt="work"))


async def _run(board, monkeypatch, free_gib, total_gib=1000):
    monkeypatch.setattr(disk_space, "usage", lambda path: _space(free_gib, total_gib))
    return await board.runner.run_task(board.task())


@pytest.mark.asyncio
async def test_enough_space_starts_without_a_card_or_a_warning(board, monkeypatch):
    result = await _run(board, monkeypatch, 500)
    assert result.ok and len(board.seen) == 1 and board.cards == []
    assert not any("여유" in str(row.get("text")) for row in board.logs)


@pytest.mark.asyncio
async def test_a_low_drive_warns_on_the_board_and_still_starts(board, monkeypatch):
    result = await _run(board, monkeypatch, 8)  # under the doctor's 10 GiB warning, over the 2 GiB floor
    assert result.ok and len(board.seen) == 1 and board.cards == []
    assert any(row.get("level") == "warn" and "작업 폴더 드라이브 여유가 적습니다" in row["text"] for row in board.logs)


@pytest.mark.asyncio
async def test_below_the_floor_the_task_waits_for_a_card_and_a_decline_starts_nothing(board, monkeypatch):
    result = await _run(board, monkeypatch, 1)
    assert not result.ok and board.seen == []
    assert "시작하지 않았습니다" in result.error and "2 GiB" in result.error
    (card,) = board.cards
    assert card.kind == "disk_space" and card.detail["floor_gib"] == 2 and card.detail["free_gib"] == 1.0
    assert card.request_id == "r" and "1.0 GiB" in card.summary
    assert "시작" in card.pi_outcome  # the card says what each answer does


@pytest.mark.asyncio
async def test_below_the_floor_an_approval_starts_the_task_with_a_note(board, monkeypatch):
    board.answer["approved"] = True
    result = await _run(board, monkeypatch, 1)
    assert result.ok and len(board.seen) == 1 and len(board.cards) == 1
    assert any("PI가 디스크 여유가 적은 채로 시작을 승인했습니다" in row["text"] for row in board.logs)


@pytest.mark.asyncio
async def test_a_zero_floor_turns_the_card_off(board, monkeypatch):
    board.settings.runner.disk_fail_free_gb = 0
    result = await _run(board, monkeypatch, 1)
    assert result.ok and board.cards == []


@pytest.mark.asyncio
async def test_an_unreadable_drive_and_a_facilities_fix_never_wait_for_a_card(board, monkeypatch, tmp_path):
    def broken(path):
        raise OSError("disk unavailable")

    monkeypatch.setattr(disk_space, "usage", broken)
    assert await board.runner._disk_gate(board.task(), board.runner.registry.agents["worker"], tmp_path / "w") is None
    monkeypatch.setattr(disk_space, "usage", lambda path: _space(0.1))
    fix = Task(agent_id="worker", request_id="r", prompt="p", meta={"kind": "facilities_fix"})
    assert await board.runner._disk_gate(fix, board.runner.registry.agents["worker"], tmp_path / "w") is None
    assert board.cards == []


@pytest.mark.asyncio
async def test_the_bulk_data_root_is_named_in_the_briefing_only_when_set(board, monkeypatch, tmp_path):
    result = await _run(board, monkeypatch, 500)
    assert result.ok and "큰 원자료" not in board.seen[-1].task.context
    bulk = tmp_path / "bulk"
    board.settings.runner.bulk_data_root = str(bulk)
    result = await _run(board, monkeypatch, 500)
    context = board.seen[-1].task.context
    assert "큰 원자료(수 GB 이상)는 작업 폴더가 아니라" in context and str(bulk.resolve()) in context
    assert "평소처럼 승인" in context  # the folder is outside the step folder: no new write permission


@pytest.mark.asyncio
async def test_a_bulk_data_root_inside_a_restricted_zone_is_left_out_with_a_warning(board, monkeypatch, tmp_path):
    zone = tmp_path / "controlled"
    zone.mkdir()
    board.settings.policy.data_zones = [DataZone(path=str(zone), level="restricted")]
    board.settings.runner.bulk_data_root = str(zone / "bulk")
    result = await _run(board, monkeypatch, 500)
    assert result.ok and "큰 원자료" not in board.seen[-1].task.context
    assert any("runner.bulk_data_root" in str(row.get("text")) for row in board.logs)


@pytest.mark.asyncio
async def test_a_declined_start_creates_no_workspace_and_holds_no_run_slot(board, monkeypatch):
    slots = board.runner.sem._value
    result = await _run(board, monkeypatch, 1)
    assert not result.ok and board.seen == []
    assert board.runner.sem._value == slots  # the card waited before a slot was taken
    assert not list(Path(board.settings.runner.workspace_root).glob("*/*"))  # no task folder was created
    assert board.cards[0].timeout_s == 24 * 3600  # the card outlives the default hour


@pytest.mark.asyncio
async def test_the_drive_judged_is_the_one_the_run_really_uses(board, monkeypatch, tmp_path):
    probed = []

    def usage(path):
        probed.append(path)
        return _space(500)

    monkeypatch.setattr(disk_space, "usage", usage)
    elsewhere = tmp_path / "other_drive" / "retry_folder"
    await board.runner.run_task(board.task(), workdir_override=elsewhere)
    assert probed and probed[0] == elsewhere  # not the default workspace root
