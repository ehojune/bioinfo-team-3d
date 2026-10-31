"""#676: names labhq writes into a step's outputs/ itself are not a plan's to declare or to tell the staff to write."""
import json

import pytest

from labhq import output_checks
from labhq.orchestrator import cso
from labhq.orchestrator.cso import PlanOutputsError
from labhq.output_checks import RESERVED_OUTPUTS, external_lookup_lines, external_lookups
from labhq.tools.external_wait import STATUS_FILE


def plan(*, outputs=("outputs/answer.md",), optional=None, instruction="Write outputs/answer.md."):
    step = {"id": "s1", "agent_id": "a", "instruction": instruction, "outputs": list(outputs), "depends_on": []}
    if optional is not None:
        step["optional_outputs"] = list(optional)
    return {"steps": [step]}


def test_every_record_labhq_reads_back_is_reserved_in_one_place():
    assert {STATUS_FILE, output_checks.QUERY_LOG, output_checks.LITERATURE_ACCESS_LOG} == RESERVED_OUTPUTS


@pytest.mark.parametrize("name", sorted(RESERVED_OUTPUTS))
@pytest.mark.parametrize("spelling", ["{}", "./{}", "{base}", "outputs\\{base}"])
@pytest.mark.parametrize("field", ["outputs", "optional_outputs"])
def test_a_plan_cannot_declare_a_reserved_name_as_a_staff_output(name, spelling, field):
    spelled = spelling.format(name, base=name.split("/", 1)[1])
    declared = plan(outputs=[spelled] if field == "outputs" else ["outputs/answer.md"],
                    optional=[spelled] if field == "optional_outputs" else None)
    with pytest.raises(PlanOutputsError, match="labhq writes itself"):
        cso._normalize_plan_outputs(declared)


@pytest.mark.parametrize("instruction", [
    "Save one JSON line per lookup in a new file outputs/external_lookup_status.jsonl and report it.",
    "Write each query to ./outputs/annotation_queries.jsonl.",
    "append to outputs/literature_access.jsonl",
])
def test_a_plan_cannot_tell_the_staff_to_write_a_reserved_name(instruction):
    with pytest.raises(PlanOutputsError, match="labhq writes itself"):
        cso._normalize_plan_outputs(plan(instruction=instruction))


def test_reading_an_upstream_steps_record_and_ordinary_outputs_stay_allowed():
    ordered = plan(outputs=["outputs/answer.md", "outputs/lookup_ledger.jsonl"],
                   instruction="Summarize inputs/lit_scout/outputs/external_lookup_status.jsonl into outputs/answer.md "
                               "and keep your own notes in outputs/lookup_ledger.jsonl.")
    normalized, warnings = cso._normalize_plan_outputs(ordered)
    assert normalized["steps"][0]["outputs"] == ["outputs/answer.md", "outputs/lookup_ledger.jsonl"]


def _status(tmp_path, *lines):
    target = tmp_path / "outputs" / "external_lookup_status.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _row(tool, status, elapsed, limit=1200):
    return json.dumps({"tool": tool, "status": status, "elapsed_s": elapsed, "timeout_s": limit})


def test_lines_the_staff_wrote_are_not_counted_against_labhq(tmp_path):
    """The 6th run: eight free-form staff lines made the CP2 warning say eight records could not be read."""
    staff = [json.dumps({"step_id": "s1", "order": n, "item": f"x{n}", "identifier": "rs1", "service": "clinvar",
                         "status": "조회 안 함(상한, PI 답변 a)", "http_requests_sent": 0}, ensure_ascii=False)
             for n in range(8)]
    _status(tmp_path, *staff, _row("vep", "timeout", 1200))
    record = external_lookups(tmp_path)
    assert record.invalid_rows == 0 and [item.tool for item in record.tools] == ["vep"]
    assert external_lookup_lines(record) == ["`vep` 조회 불가(시간 초과) 1건 · 단계 조회 경과 1200초/상한 1200초"]


@pytest.mark.parametrize("line", [
    "not json",                                                     # labhq never writes a line that is not JSON
    "[1, 2]",
    json.dumps({"tool": "vep", "status": "finished", "elapsed_s": 1, "timeout_s": 5}),   # names a tool, breaks the rules
])
def test_a_line_that_looks_like_labhq_but_is_malformed_is_still_counted(tmp_path, line):
    _status(tmp_path, line, _row("vep", "ok", 1))
    record = external_lookups(tmp_path)
    assert record.invalid_rows == 1 and external_lookup_lines(record) == ["읽지 않은 잘못된 기록 1줄"]
