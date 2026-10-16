"""Table outputs that do not read as tables, and how deeply papers were read, reach the report warnings (#697, #710)."""

import json

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, general_report_warnings
from labhq.output_checks import literature_access, quality_lines, table_problem, table_warnings
from tests.test_research_report import _hub


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_literal_backslash_t_is_not_a_tab_separator(tmp_path):
    # The 10-17 trial's mechanism_literature.tsv: one column per row in any TSV reader.
    path = _write(tmp_path / "lit.tsv", "theme\\tPMID\\taccess_level\nheat\\t123\\tabstract\n")
    assert table_problem(path) == "칸 구분이 탭이 아니라 글자 \\t입니다"


@pytest.mark.parametrize(("name", "text"), [
    ("ok.tsv", "a\tb\tc\n1\t2\t3\n4\t5\t6\n"),
    ("rnames.tsv", "a\tb\n r1\t1\t2\nr2\t3\t4\n"),  # R write.table: header lacks the row-name column
    ("quoted.csv", 'a,b\n"x, y",2\n'),
    ("lone_quote.tsv", 'a\tb\nsaid "hi\t2\nnext\t3\n'),  # a hand-joined TSV is not read with CSV quoting
    ("vcf_like.tsv", "##meta=1\n#CHROM\tPOS\nchr1\t5\n"),
    ("single.tsv", "id\nA\nB\n"),
    ("empty.csv", ""),
])
def test_well_formed_tables_pass(tmp_path, name, text):
    assert table_problem(_write(tmp_path / name, text)) is None


def test_ragged_rows_name_the_first_bad_line(tmp_path):
    path = _write(tmp_path / "bad.csv", "a,b,c\n1,2,3\n\n4,5\n")
    assert table_problem(path) == "줄마다 칸 수가 다릅니다(머리줄 3칸, 4번째 줄 2칸)"


def test_table_warnings_check_only_collected_tables_inside_the_workdir(tmp_path):
    work = tmp_path / "work"
    _write(work / "outputs/lit.tsv", "a\\tb\n1\\t2\n")
    _write(work / "outputs/notes.md", "a\\tb\n")
    _write(tmp_path / "outside.tsv", "a\\tb\n")
    assert table_warnings(work, ["outputs/lit.tsv", "outputs/notes.md", "../outside.tsv", "outputs/gone.tsv"]) == [
        "outputs/lit.tsv: 칸 구분이 탭이 아니라 글자 \\t입니다"]


def _fulltext_record(work, name, results, tool="fulltext"):
    rel = f"outputs/annotation/{name}.json"
    _write(work / rel, json.dumps({"source": {}, "results": results}))
    log = work / "outputs/annotation_queries.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tool": tool, "result_file": rel}) + "\n")


def test_literature_access_keeps_each_papers_best_level(tmp_path):
    work = tmp_path / "work"
    _fulltext_record(work, "a", [
        {"input": "PMID:1", "answer": {"found": True, "access_level": "abstract", "pmid": "1"}},
        {"input": "10.1/x", "answer": {"found": True, "access_level": "metadata", "doi": "10.1/x"}},
        {"input": "PMID:9", "answer": {"found": False}},
        {"input": "PMID:8", "error": "lookup failed"},
    ])
    _fulltext_record(work, "b", [{"input": "PMC5", "answer": {"found": True, "access_level": "full_text",
                                                              "pmid": "1", "pmcid": "PMC5"}}])
    _fulltext_record(work, "c", [{"input": "x", "answer": {"found": True, "access_level": "full_text",
                                                           "pmid": "77"}}], tool="pubmed")
    _write(tmp_path / "escape.json", json.dumps({"results": [
        {"input": "PMID:66", "answer": {"found": True, "access_level": "abstract", "pmid": "66"}}]}))
    with (work / "outputs/annotation_queries.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n" + json.dumps({"tool": "fulltext", "result_file": "../escape.json"}) + "\n")
    assert literature_access(work) == {"PMID:1": "full_text", "DOI:10.1/x": "metadata"}
    assert literature_access(tmp_path / "empty") == {}


def test_quality_lines_merge_steps_and_skip_an_all_full_text_reading():
    results = {
        "s1": TaskResult(task_id="t1", agent_id="a", ok=True, table_warnings=["outputs/a.tsv: x", "outputs/b.tsv: y",
                                                                             "outputs/c.tsv: z", "outputs/d.tsv: w"],
                         literature_access={"PMID:1": "abstract", "PMID:2": "full_text"}),
        "s2": {"ok": True, "literature_access": {"PMID:1": "full_text", "PMID:3": "metadata"}},
    }
    steps = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
    assert quality_lines(steps, results) == [
        "s1: 표로 읽히지 않는 산출물 4개 — outputs/a.tsv: x; outputs/b.tsv: y; outputs/c.tsv: z (+1개)",
        "문헌을 읽은 수준(fulltext로 조회한 3편): 본문 2편, 서지만 1편. 본문을 읽지 못한 1편에서 가져온 수치는 한계로 적는다",
    ]
    assert quality_lines(steps, {"s1": {"literature_access": {"PMID:1": "full_text"}}}) == []


def test_general_report_warnings_show_table_problems():
    results = {"s1": TaskResult(task_id="t1", agent_id="a", ok=True, table_warnings=["outputs/a.tsv: 문제"])}
    assert "- s1: 표로 읽히지 않는 산출물 1개 — outputs/a.tsv: 문제" in general_report_warnings([{"id": "s1"}], results)


def test_wire_form_omits_empty_check_fields():
    data = TaskResult(task_id="t", agent_id="a", ok=True).model_dump()
    assert "table_warnings" not in data and "literature_access" not in data


@pytest.mark.asyncio
async def test_research_cp2_card_and_report_limits_carry_the_checks():
    hub = _hub()
    original = hub.reply

    async def reply(task):
        result = await original(task)
        if task.meta["kind"] == "step":
            result.table_warnings = ["outputs/lit.tsv: 칸 구분이 탭이 아니라 글자 \\t입니다"]
            result.literature_access = {"PMID:1": "full_text", "PMID:2": "abstract"}
        return result

    hub.reply = reply
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("error")
    card = json.dumps(next(a["detail"] for a in hub.approvals if a["kind"] == "research_evidence"), ensure_ascii=False)
    assert "표로 읽히지 않는 산출물 1개" in card and "본문 1편, 초록 1편" in card
    limits = req["report"].split("## 한계", 1)[1]
    assert "outputs/lit.tsv" in limits and "fulltext로 조회한 2편" in limits
