"""The workspace's shared lookup status reaches result warnings without trusting what the file says (#676)."""

import json
import os

import pytest

from labhq import output_checks
from labhq.models import ExternalLookupRecord, TaskResult
from labhq.orchestrator.cso import general_report_warnings
from labhq.output_checks import external_lookup_lines, external_lookups, quality_lines
from labhq.tools.external_wait import STATUS_FILE


def _row(tool, status, elapsed, limit=1200, **extra):
    return json.dumps({"tool": tool, "status": status, "elapsed_s": elapsed, "timeout_s": limit, **extra},
                      ensure_ascii=False)


def _status(root, *lines):
    path = root / STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def _tools(record):
    return {item.tool: item.model_dump() for item in record.tools}


def test_no_status_file_is_no_record_and_no_line(tmp_path):
    assert external_lookups(tmp_path) is None
    assert external_lookup_lines(None) == []
    data = TaskResult(task_id="t", agent_id="a", ok=True).model_dump()
    assert "external_lookups" not in data  # results without the file serialize as before


def test_finished_lookups_are_counted_without_a_warning(tmp_path):
    _status(tmp_path, _row("vep", "waiting", 0), _row("vep", "ok", 3.5), _row("gnomad", "waiting", 4),
            _row("gnomad", "ok", 9))
    record = external_lookups(tmp_path)
    assert _tools(record)["vep"] == {"tool": "vep", "ok": 1, "timeout": 0, "failed": 0, "cancelled": 0,
                                     "unfinished": False, "elapsed_s": 3.5, "timeout_s": 1200.0}
    assert external_lookup_lines(record) == []


def test_timeout_cancel_failure_and_unfinished_stay_distinct(tmp_path):
    _status(tmp_path,
            _row("chipatlas", "waiting", 0), _row("chipatlas", "waiting", 60), _row("chipatlas", "timeout", 1200),
            _row("chipatlas", "waiting", 1201), _row("chipatlas", "timeout", 1201.2),
            _row("gtex", "waiting", 1202), _row("gtex", "cancelled", 1203),
            _row("encode", "waiting", 1204), _row("encode", "failed", 1205),
            _row("clinvar", "waiting", 1206))
    record = external_lookups(tmp_path)
    tools = _tools(record)
    assert (tools["chipatlas"]["timeout"], tools["chipatlas"]["cancelled"]) == (2, 0)
    assert (tools["gtex"]["cancelled"], tools["gtex"]["timeout"]) == (1, 0)
    assert tools["encode"]["failed"] == 1 and tools["clinvar"]["unfinished"]
    assert external_lookup_lines(record) == [
        "`chipatlas` 조회 불가(시간 초과) 2건 · 단계 조회 경과 1201초/상한 1200초",
        "`gtex` 조회 취소 1건 · 단계 조회 경과 1203초/상한 1200초",
        "`encode` 조회 실패 1건 · 단계 조회 경과 1205초/상한 1200초",
        "`clinvar` 끝난 기록 없음 · 단계 조회 경과 1206초/상한 1200초",
    ]


def test_a_later_success_does_not_hide_an_earlier_timeout(tmp_path):
    _status(tmp_path, _row("chipatlas", "waiting", 0), _row("chipatlas", "timeout", 1200),
            _row("chipatlas", "waiting", 1200.5), _row("chipatlas", "ok", 1201))
    item = external_lookups(tmp_path).tools[0]
    assert (item.ok, item.timeout, item.unfinished) == (1, 1, False)
    assert external_lookup_lines(external_lookups(tmp_path))[0].startswith("`chipatlas` 조회 불가(시간 초과) 1건")


@pytest.mark.parametrize("line", [
    "not json",
    "[1, 2]",
    _row("**bold**", "timeout", 5),
    _row("<script>alert(1)</script>", "timeout", 5),
    _row("chip`atlas", "timeout", 5),
    _row("Chipatlas", "timeout", 5),
    _row("chipatlas", "finished", 5),
    _row("chipatlas", "timeout", -1),
    _row("chipatlas", "timeout", True),
    _row("chipatlas", "timeout", "5"),
    _row("chipatlas", "timeout", 5, limit=0),
    _row("chipatlas", "timeout", 1e12),
    '{"tool": "chipatlas", "status": "timeout", "elapsed_s": NaN, "timeout_s": 1200}',
    '{"tool": "chipatlas", "status": "timeout", "elapsed_s": 5, "timeout_s": Infinity}',
    _row("chipatlas", "timeout", 5, text="x" * 5000),
])
def test_malformed_rows_are_counted_and_never_reach_a_report(tmp_path, line):
    _status(tmp_path, line, _row("vep", "ok", 1))
    record = external_lookups(tmp_path)
    assert record.invalid_rows == 1 and [item.tool for item in record.tools] == ["vep"]
    assert external_lookup_lines(record) == ["읽지 않은 잘못된 기록 1줄"]


def test_free_text_in_a_valid_row_is_not_read_back(tmp_path):
    _status(tmp_path, _row("chipatlas", "timeout", 1200, text="<img src=x onerror=alert(1)> [click](http://evil)"))
    lines = external_lookup_lines(external_lookups(tmp_path))
    assert lines == ["`chipatlas` 조회 불가(시간 초과) 1건 · 단계 조회 경과 1200초/상한 1200초"]
    assert not any("evil" in line or "<img" in line for line in lines)


def test_a_repeated_record_counts_once(tmp_path):
    timeout = _row("chipatlas", "timeout", 1200)
    _status(tmp_path, _row("chipatlas", "waiting", 0), timeout, timeout)
    record = external_lookups(tmp_path)
    assert record.tools[0].timeout == 1 and record.duplicate_rows == 1
    assert "같은 기록 1줄은 한 번만 셈" in external_lookup_lines(record)


def test_a_large_file_is_read_from_its_tail_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(output_checks, "MAX_LOOKUP_STATUS_BYTES", 400)
    _status(tmp_path, *[_row("vep", "waiting", float(n)) for n in range(40)], _row("vep", "ok", 40),
            _row("chipatlas", "waiting", 41), _row("chipatlas", "timeout", 1200))
    record = external_lookups(tmp_path)
    assert record.truncated and record.invalid_rows == 0  # the cut first line is dropped, not counted as invalid
    assert _tools(record)["chipatlas"]["timeout"] == 1
    assert external_lookup_lines(record)[-1] == "기록이 길어 마지막 부분만 읽음"


def test_row_and_tool_caps_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(output_checks, "MAX_LOOKUP_STATUS_ROWS", 3)
    monkeypatch.setattr(output_checks, "EXTERNAL_LOOKUP_MAX_TOOLS", 1)
    _status(tmp_path, _row("vep", "ok", 1), _row("chipatlas", "waiting", 2), _row("chipatlas", "timeout", 1200),
            _row("gtex", "timeout", 1201))
    record = external_lookups(tmp_path)
    assert record.truncated and [item.tool for item in record.tools] == ["chipatlas"] and record.dropped_tools == 1
    lines = external_lookup_lines(record)
    assert "도구 1개는 상한 1개를 넘어 세지 않음" in lines and "기록이 길어 마지막 부분만 읽음" in lines


def _symlink(link, target, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


UNREADABLE = "기록을 읽지 못함(link이거나 일반 파일이 아니거나 읽기 오류)"


def test_a_linked_status_file_or_folder_is_not_read_and_is_not_taken_for_no_record(tmp_path):
    outside = tmp_path / "outside"
    _status(outside, _row("chipatlas", "timeout", 1200))
    step = tmp_path / "step"
    (step / "outputs").mkdir(parents=True)
    _symlink(step / STATUS_FILE, outside / STATUS_FILE)
    record = external_lookups(step)
    assert record.unreadable and record.tools == [] and external_lookup_lines(record) == [UNREADABLE]
    other = tmp_path / "other"
    other.mkdir()
    _symlink(other / "outputs", outside / "outputs", directory=True)
    assert external_lookups(other).unreadable


def test_a_status_path_that_is_not_a_plain_file_is_unreadable_but_no_outputs_is_no_record(tmp_path):
    (tmp_path / STATUS_FILE).mkdir(parents=True)  # a folder where the record should be
    assert external_lookups(tmp_path).unreadable
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "outputs").write_text("not a folder", encoding="utf-8")
    assert external_lookups(flat).unreadable
    empty = tmp_path / "empty"
    empty.mkdir()
    assert external_lookups(empty) is None  # no outputs/ at all: no record, not a read failure
    result = TaskResult(task_id="t", agent_id="a", ok=True, external_lookups=external_lookups(tmp_path))
    assert quality_lines([{"id": "s1"}], {"s1": result}) == [
        f"s1: 외부 조회 상태(직원 workspace의 조회 상태 기록이며 호출·본문 독해 증명은 아님) — {UNREADABLE}"]


def test_an_interrupted_call_stays_unfinished_after_a_later_call_of_the_same_tool(tmp_path):
    # Call a waited and its turn ended (task timeout); a later call of the same tool for other genes succeeded.
    _status(tmp_path, _row("chipatlas", "waiting", 0, lookup="a" * 12), _row("chipatlas", "waiting", 60, lookup="a" * 12),
            _row("chipatlas", "waiting", 300, lookup="b" * 12), _row("chipatlas", "ok", 310, lookup="b" * 12))
    item = external_lookups(tmp_path).tools[0]
    assert (item.ok, item.unfinished) == (1, True)
    assert external_lookup_lines(external_lookups(tmp_path)) == [
        "`chipatlas` 끝난 기록 없음 · 단계 조회 경과 310초/상한 1200초"]
    _status(tmp_path, _row("chipatlas", "waiting", 0, lookup="a" * 12), _row("chipatlas", "ok", 3, lookup="a" * 12))
    assert not external_lookups(tmp_path).tools[0].unfinished


@pytest.mark.parametrize("lookup", ["", "A" * 12, "a" * 11, "a" * 13, 123, ["a" * 12], "**bold**xxxx"])
def test_a_malformed_call_id_is_an_invalid_row(tmp_path, lookup):
    _status(tmp_path, _row("chipatlas", "timeout", 1200, lookup=lookup))
    record = external_lookups(tmp_path)
    assert record.invalid_rows == 1 and record.tools == []


def test_a_workspace_reused_past_a_week_still_reads_its_budget(tmp_path):
    _status(tmp_path, _row("chipatlas", "timeout", 8 * 86400))
    record = external_lookups(tmp_path)
    assert record.invalid_rows == 0 and record.tools[0].timeout == 1


def test_a_record_from_another_runner_version_drops_the_summary_not_the_result():
    result = TaskResult.model_validate({"task_id": "t", "agent_id": "a", "ok": False, "error": "incomplete",
                                        "external_lookups": {"tools": [{"tool": "x", "elapsed_s": "nan"}]}})
    assert result.external_lookups is None and result.ok is False and result.error == "incomplete"
    wire = TaskResult(task_id="t", agent_id="a", ok=True, external_lookups=ExternalLookupRecord(
        tools=[{"tool": "chipatlas", "timeout": 1, "elapsed_s": 1200, "timeout_s": 1200}])).model_dump()
    again = TaskResult.model_validate(json.loads(json.dumps(wire)))
    assert again.external_lookups.tools[0].timeout == 1


def test_report_warnings_name_the_step_and_keep_the_record_boundary(tmp_path):
    _status(tmp_path, _row("chipatlas", "waiting", 0), _row("chipatlas", "timeout", 1200))
    record = external_lookups(tmp_path)
    expected = ("s1: 외부 조회 상태(직원 workspace의 조회 상태 기록이며 호출·본문 독해 증명은 아님) — "
                "`chipatlas` 조회 불가(시간 초과) 1건 · 단계 조회 경과 1200초/상한 1200초")
    live = {"s1": TaskResult(task_id="t1", agent_id="a", ok=True, external_lookups=record)}
    stored = {"s1": json.loads(json.dumps(live["s1"].model_dump()))}  # a persisted request keeps plain dicts
    assert quality_lines([{"id": "s1"}], live) == [expected] == quality_lines([{"id": "s1"}], stored)
    assert f"- {expected}" in general_report_warnings([{"id": "s1"}], live)
    finished = {"s1": TaskResult(task_id="t1", agent_id="a", ok=True, external_lookups=ExternalLookupRecord(
        tools=[{"tool": "vep", "ok": 2, "elapsed_s": 4, "timeout_s": 1200}]))}
    assert quality_lines([{"id": "s1"}], finished) == []


def test_the_record_never_changes_step_success_or_required_outputs(tmp_path):
    _status(tmp_path, _row("chipatlas", "timeout", 1200))
    failed = TaskResult(task_id="t", agent_id="a", ok=False, missing_outputs=["outputs/chip.tsv"],
                        error="incomplete: missing outputs: outputs/chip.tsv", external_lookups=external_lookups(tmp_path))
    assert not failed.ok and failed.missing_outputs == ["outputs/chip.tsv"]
    done = TaskResult(task_id="t", agent_id="a", ok=True, external_lookups=external_lookups(tmp_path))
    assert done.ok and done.missing_optional_outputs == [] and done.step_warnings == []
