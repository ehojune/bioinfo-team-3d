"""#1331: a task reads its own folder without an approval card even when an internal zone holds every task folder.

The PI's config marked the runs root internal; the CSO then could not read its own TASK.md. The exemption is the task's
own folder only: another task's folder, a `..` out of the folder, a link or junction in the folder that leads into a
zone, and zones outside the runs root still ask.
"""
import os
from pathlib import Path

from labhq.policy import evaluate_tool
from labhq.settings import DataZone, Settings


def _junction(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _setup(tmp_path: Path):
    runs, data = tmp_path / "runs", tmp_path / "labhq-data"
    mine, other = runs / "2026-11-09" / "task_mine", runs / "2026-11-09" / "task_other"
    for folder in (mine, other, data):
        folder.mkdir(parents=True)
    (mine / "TASK.md").write_text("do the thing", encoding="utf-8")
    (other / "outputs.tsv").write_text("a\tb\n", encoding="utf-8")
    (data / "rows.tsv").write_text("id\n1\n", encoding="utf-8")
    settings = Settings()
    settings.policy.data_zones = [DataZone(path=str(runs), level="internal"),
                                  DataZone(path=str(data), level="internal")]
    return settings.policy, mine, other, data


def _decide(policy, mine: Path, tool: str, tool_input: dict):
    return evaluate_tool(tool, tool_input, policy, allowed_roots=[str(mine)], workdir=str(mine))


def test_own_task_md_does_not_ask(tmp_path):
    policy, mine, _, _ = _setup(tmp_path)
    for tool, tool_input in (("Read", {"file_path": str(mine / "TASK.md")}),
                             ("Read", {"file_path": "TASK.md"}),
                             ("Grep", {"pattern": "thing", "path": str(mine)}),
                             ("Bash", {"command": "cat TASK.md"})):
        decision = _decide(policy, mine, tool, tool_input)
        assert "internal data zone" not in decision.reason, (tool, tool_input, decision)


def test_other_task_folder_still_asks(tmp_path):
    policy, mine, other, _ = _setup(tmp_path)
    for tool_input in ({"file_path": str(other / "outputs.tsv")}, {"file_path": "../task_other/outputs.tsv"}):
        decision = _decide(policy, mine, "Read", tool_input)
        assert decision.action == "ask" and "internal data zone" in decision.reason, (tool_input, decision)


def test_link_out_of_own_folder_still_asks(tmp_path):
    policy, mine, other, data = _setup(tmp_path)
    _junction(mine / "peek", other)
    _junction(mine / "raw", data)
    for path in ("peek/outputs.tsv", str(mine / "raw" / "rows.tsv")):
        decision = _decide(policy, mine, "Read", {"file_path": path})
        assert decision.action == "ask" and "internal data zone" in decision.reason, (path, decision)


def test_other_internal_zone_still_asks(tmp_path):
    policy, mine, _, data = _setup(tmp_path)
    decision = _decide(policy, mine, "Read", {"file_path": str(data / "rows.tsv")})
    assert decision.action == "ask" and "internal data zone" in decision.reason


def test_no_exemption_when_the_folder_is_or_holds_a_zone(tmp_path):
    policy, mine, _, data = _setup(tmp_path)
    decision = _decide(policy, data, "Read", {"file_path": "rows.tsv"})  # a task run in the data folder itself
    assert decision.action == "ask" and "internal data zone" in decision.reason
    inner = mine / "private"
    inner.mkdir()
    (inner / "x.tsv").write_text("id\n1\n", encoding="utf-8")
    policy.data_zones = [*policy.data_zones, DataZone(path=str(inner), level="internal")]
    decision = _decide(policy, mine, "Read", {"file_path": "TASK.md"})  # a zone inside the folder: no exemption
    assert decision.action == "ask" and "internal data zone" in decision.reason
