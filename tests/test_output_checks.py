"""Table outputs that do not read as tables, and how deeply papers were read, reach the report warnings (#697, #710)."""

import json

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, general_report_warnings
from labhq.output_checks import (conclusion_literature_line, literature_access, literature_ledger,
                                 literature_ledger_line, markdown_warnings, quality_lines, stored_access, table_problem,
                                 table_warnings)
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
    ("escaped_cell.tsv", "note\ncontains\\tab-escape\nplain\n"),  # one column; "\t" is cell text
    ("empty.csv", ""),
])
def test_well_formed_tables_pass(tmp_path, name, text):
    assert table_problem(_write(tmp_path / name, text)) is None


def test_a_tab_in_a_metadata_line_does_not_hide_literal_separators(tmp_path):
    path = _write(tmp_path / "meta.tsv", "##source\tpipeline\ntheme\\tPMID\nheat\\t123\n")
    assert table_problem(path) == "칸 구분이 탭이 아니라 글자 \\t입니다"


def test_a_long_field_is_read_and_a_check_that_breaks_is_a_warning_not_an_error(tmp_path, monkeypatch):
    long_cell = "A" * 200_000  # above Python's default csv field limit of 131,072
    assert table_problem(_write(tmp_path / "seq.csv", f"id,seq\n1,\"{long_cell}\"\n2,ACGT\n")) is None

    def broken(_path):
        raise RuntimeError("boom")

    monkeypatch.setattr("labhq.output_checks.table_problem", broken)
    assert table_warnings(tmp_path, ["seq.csv"]) == ["seq.csv: 검사하지 못했습니다(RuntimeError)"]


def test_the_row_name_exception_is_for_tsv_only(tmp_path):
    path = _write(tmp_path / "extra.csv", "a,b\n1,2,3\n4,5,6\n")
    assert table_problem(path) == "줄마다 칸 수가 다릅니다(머리줄 2칸, 2번째 줄 3칸)"


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


def test_markdown_check_reads_reader_outputs_and_keeps_only_locations_and_fixes(tmp_path):
    work = tmp_path / "work"
    _write(work / "outputs/README.md", "첫 줄\n\n**(검사 문장)**이 깨집니다.")
    _write(work / "outputs/report.md", "```md\n** __ [[ 코드\n```\n")
    _write(work / "RESULT.md", "본문 __밑줄__이 깨집니다.")
    _write(tmp_path / "README.md", "**(외부 문장)**이 깨집니다.")
    warnings = markdown_warnings(work, ["outputs/README.md", "outputs/report.md", "RESULT.md", "../README.md"])
    assert len(warnings) == 2
    assert "outputs/README.md 3줄" in warnings[0]
    assert "RESULT.md 1줄" in warnings[1]
    assert all("문장부호" in warning for warning in warnings)
    assert not any("검사 문장" in warning or "외부 문장" in warning for warning in warnings)


@pytest.mark.parametrize("files", [1, 30])
def test_markdown_warning_budget_is_global_and_summarizes_overflow(tmp_path, monkeypatch, files):
    import labhq.output_checks as checks

    outputs = [f"outputs/{index}/README.md" for index in range(files)]
    for rel in outputs:
        _write(tmp_path / rel, "**\n\n" * (30 if files == 1 else 1))
    counts = []
    original = checks.rendered_markers

    def scan(source, **kwargs):
        hits = original(source, **kwargs)
        counts.append(len(hits))
        return hits

    monkeypatch.setattr(checks, "rendered_markers", scan)
    warnings = markdown_warnings(tmp_path, outputs)
    assert len(warnings) == 21  # 20 locations plus one overflow notice, across all files
    assert "추가 Markdown 경고" in warnings[-1]
    assert "생략" in warnings[-1]
    assert sum(counts) <= 21  # cap hit construction too, before formatting long warning messages
    assert all("1줄" in warning or "줄." in warning for warning in warnings[:-1])


def test_markdown_warning_budget_also_caps_oversize_file_notices(tmp_path, monkeypatch):
    monkeypatch.setattr("labhq.output_checks.MAX_RECORD_BYTES", 1)
    outputs = [f"outputs/{index}/report.md" for index in range(30)]
    for rel in outputs:
        _write(tmp_path / rel, "oversize")
    warnings = markdown_warnings(tmp_path, outputs)
    assert len(warnings) == 21
    assert "추가 Markdown 경고" in warnings[-1]


@pytest.mark.asyncio
async def test_markdown_artifact_failure_reaches_the_terminal_contract():
    hub = _hub()
    original = hub.reply

    async def reply(task):
        result = await original(task)
        if task.meta.get("kind") == "step":
            result.markdown_warnings = ["Markdown 렌더 화면: outputs/RESULT.md 3줄. 강조를 고치세요."]
        return result

    hub.reply = reply
    await Orchestrator(hub).run_request("r")
    checked = hub.requests["r"]["report_contract_check"]
    assert checked["status"] == "FAIL"
    assert any(problem["code"] == "markdown_render" and "RESULT.md 3줄" in problem["message"]
               for problem in checked["problems"])


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
    assert literature_access(work) == {
        "PMID:1|PMCID:PMC5": "full_text",
        "DOI:10.1/x": "metadata",
    }
    assert literature_access(tmp_path / "empty") == {}


def test_literature_ledger_keeps_candidate_selected_and_acquired_denominators_separate(tmp_path):
    work = tmp_path / "work"
    _fulltext_record(work, "discovery", [{"input": "query", "answer": {
        "found_count": 9, "returned_count": 4, "candidates": [
            {"openalex_id": "W1", "pmid": "1", "doi": "10.1000/a"},
            {"openalex_id": "W2", "doi": "10.1000/b"},
            {"openalex_id": "W3"},
            {"openalex_id": "W4", "pmid": "4"},
        ],
    }}], tool="openalex_works")
    _fulltext_record(work, "selected", [
        {"input": "PMID:1", "answer": {"found": True, "access_level": "full_text", "pmid": "1",
                                         "doi": "10.1000/a"}},
        {"input": "10.1000/b", "answer": {"found": True, "access_level": "abstract", "doi": "10.1000/b",
                                             "unread_reason": "subscription_required"}},
        {"input": "PMID:4", "answer": {"found": True, "access_level": "metadata", "pmid": "4",
                                         "unread_reason": "format_failure"}},
    ])
    assert literature_ledger(work) == {
        "candidate_ids": ["PMID:1|DOI:10.1000/a|OpenAlex:W1", "DOI:10.1000/b|OpenAlex:W2",
                          "OpenAlex:W3", "PMID:4|OpenAlex:W4"],
        "selected_ids": ["PMID:1|DOI:10.1000/a", "DOI:10.1000/b", "PMID:4"],
        "unread": {"DOI:10.1000/b": "subscription_required", "PMID:4": "format_failure"},
    }


def test_literature_ledger_normalizes_failed_fulltext_inputs(tmp_path):
    work = tmp_path / "work"
    _fulltext_record(work, "failed", [
        {"input": "10.1000/Outside", "answer": {"found": False}},
        {"input": "31868601", "answer": {"found": False}},
    ])
    assert literature_ledger(work) == {
        "candidate_ids": [],
        "selected_ids": ["DOI:10.1000/outside", "PMID:31868601"],
        "unread": {"DOI:10.1000/outside": "no_copy", "PMID:31868601": "no_copy"},
    }


def test_literature_ledger_keeps_found_false_failure_reason(tmp_path):
    work = tmp_path / "work"
    _fulltext_record(work, "failed", [{
        "input": "10.1000/outside",
        "answer": {"found": False, "doi": "10.1000/outside", "unread_reason": "access_denied"},
    }])
    assert literature_ledger(work)["unread"] == {"DOI:10.1000/outside": "access_denied"}


def test_literature_ledger_does_not_truncate_two_citation_directions(tmp_path):
    work = tmp_path / "work"
    backward = [{"openalex_id": f"W{number}"} for number in range(1, 1001)]
    forward = [{"openalex_id": "W1001"}, {"openalex_id": "W1002"}]
    _fulltext_record(work, "chase", [{"input": "PMID:1", "answer": {
        "backward": {"candidates": backward}, "forward": {"candidates": forward},
    }}], tool="citation_chase")

    ledger = literature_ledger(work)
    assert len(ledger["candidate_ids"]) == 1002
    line = literature_ledger_line([{"id": "s1"}], {"s1": {"literature_ledger": ledger}}, {})
    assert line.startswith("문헌 접근 실패 원장(서로 다른 분모): 후보 1002편")


def test_literature_ledger_line_has_four_exact_denominators_and_unread_key_papers():
    ledgers = {"s1": {
        "claims": [{"id": "c1", "status": "supported"}],
        "evidence": [
            {"id": "full", "source": {"id_scheme": "pmid", "id_value": "1"}},
            {"id": "closed", "source": {"id_scheme": "doi", "id_value": "10.1000/b"}},
            {"id": "missing", "source": {"id_scheme": "pmid", "id_value": "9"}},
        ],
        "links": [{"claim_id": "c1", "evidence_id": evidence_id, "relation": "supports"}
                  for evidence_id in ("full", "closed", "missing")],
    }}
    results = {"s1": {
        "literature_access": {"PMID:1": "full_text", "DOI:10.1000/b": "abstract"},
        "literature_ledger": {
            "candidate_ids": ["PMID:1", "DOI:10.1000/b", "PMID:9", "OpenAlex:W4"],
            "selected_ids": ["PMID:1", "DOI:10.1000/b", "PMID:9"],
            "unread": {"DOI:10.1000/b": "subscription_required"},
        },
    }}
    line = literature_ledger_line([{"id": "s1"}], results, ledgers)
    assert line.startswith("문헌 접근 실패 원장(서로 다른 분모): 후보 4편·선정 3편·확보 2편(본문 1·초록 1·서지 0)·결론 근거 3편.")
    assert "DOI:10.1000/b(구독 필요)" in line and "PMID:9(조회 기록 없음)" in line


def test_quality_lines_merge_steps_and_skip_an_all_full_text_reading():
    results = {
        "s1": TaskResult(task_id="t1", agent_id="a", ok=True, table_warnings=["outputs/a.tsv: x", "outputs/b.tsv: y",
                                                                             "outputs/c.tsv: z", "outputs/d.tsv: w"],
                         literature_access={"PMID:1|PMCID:PMC1": "abstract", "PMID:2": "full_text"}),
        "s2": {"ok": True, "literature_access": {"PMCID:PMC1": "full_text", "PMID:3": "metadata"}},
    }
    steps = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
    assert quality_lines(steps, results) == [
        "s1: 표로 읽히지 않는 산출물 4개 — outputs/a.tsv: x; outputs/b.tsv: y; outputs/c.tsv: z (+1개)",
        "문헌을 읽은 수준(fulltext로 조회한 3편): 본문 2편, 서지만 1편. 본문을 읽지 못한 1편에서 가져온 수치는 한계로 적는다",
    ]
    assert quality_lines(steps, {"s1": {"literature_access": {"PMID:1": "full_text"}}}) == []


def test_quality_lines_drop_an_earlier_timeout_after_alias_full_text_success():
    steps = [{"id": "early"}, {"id": "late"}]
    results = {
        "early": {
            "literature_access": {"PMID:123|DOI:10.1000/key": "abstract"},
            "literature_access_failures": {"PMID:123": "pdf_request_timed_out"},
        },
        "late": {"literature_access": {"DOI:10.1000/key": "full_text"}},
    }

    lines = quality_lines(steps, results)

    assert not any("PDF 요청" in line or "PMID:123" in line for line in lines)


def test_conclusion_literature_counts_only_papers_linked_to_citable_claims():
    ledgers = {"s1": {
        "claims": [{"id": "supported", "status": "supported"},
                   {"id": "contradicted", "status": "contradicted"}],
        "evidence": [
            {"id": "full", "source": {"uri": "https://pubmed.ncbi.nlm.nih.gov/101/", "locator": "abstract"}},
            {"id": "abstract", "source": {"uri": "https://example.org/paper", "locator": "DOI:10.1000/a"}},
            {"id": "missing", "source": {"uri": "https://pmc.ncbi.nlm.nih.gov/articles/PMC303/"}},
            {"id": "unused", "source": {"uri": "https://doi.org/10.1000/unused"}},
        ],
        "links": [
            {"claim_id": "supported", "evidence_id": "full", "relation": "supports"},
            {"claim_id": "supported", "evidence_id": "abstract", "relation": "supports"},
            {"claim_id": "contradicted", "evidence_id": "missing", "relation": "contradicts"},
        ],
    }}
    results = {"s1": {"literature_access": {
        "PMID:101": "full_text", "DOI:10.1000/a": "abstract", "DOI:10.1000/unused": "metadata",
    }}}

    assert conclusion_literature_line([{"id": "s1"}], results, ledgers) == (
        "결론 근거 논문 3편: 본문 1편·초록 1편·서지만 0편·조회 기록 없음 1편. "
        "결론 claim에 연결된 논문만 센 값이며, 'fulltext로 조회한 논문 전체'와 분모가 다르다"
    )


def test_conclusion_literature_reads_structured_paper_ids():
    ledgers = {"s1": {
        "claims": [{"id": "c1", "status": "supported"}],
        "evidence": [
            {"id": "pmid", "source": {"id_scheme": "pmid", "id_value": "123"}},
            {"id": "pmcid", "source": {"id_scheme": "pmcid", "id_value": "pmc456"}},
            {"id": "doi", "source": {"id_scheme": "doi", "id_value": "https://doi.org/10.1000/ABC"}},
        ],
        "links": [{"claim_id": "c1", "evidence_id": evidence_id, "relation": "supports"}
                  for evidence_id in ("pmid", "pmcid", "doi")],
    }}

    assert conclusion_literature_line([{"id": "s1"}], {}, ledgers) == (
        "결론 근거 논문 3편: 본문 0편·초록 0편·서지만 0편·조회 기록 없음 3편. "
        "결론 claim에 연결된 논문만 센 값이며, 'fulltext로 조회한 논문 전체'와 분모가 다르다"
    )


def test_conclusion_literature_reads_openalex_ids_and_urls():
    ledgers = {"s1": {
        "claims": [{"id": "c1", "status": "supported"}],
        "evidence": [
            {"id": "structured", "source": {"id_scheme": "openalex", "id_value": "W123"}},
            {"id": "url", "source": {"uri": "https://openalex.org/W456"}},
        ],
        "links": [{"claim_id": "c1", "evidence_id": evidence_id, "relation": "supports"}
                  for evidence_id in ("structured", "url")],
    }}

    assert conclusion_literature_line([{"id": "s1"}], {}, ledgers) == (
        "결론 근거 논문 2편: 본문 0편·초록 0편·서지만 0편·조회 기록 없음 2편. "
        "결론 claim에 연결된 논문만 센 값이며, 'fulltext로 조회한 논문 전체'와 분모가 다르다"
    )


def test_fulltext_aliases_match_a_structured_doi_evidence(tmp_path):
    work = tmp_path / "work"
    _fulltext_record(work, "paper", [{"input": "PMID:123", "answer": {
        "found": True, "access_level": "full_text", "pmid": "123", "pmcid": "PMC123",
        "doi": "10.1000/ALIAS",
    }}])
    access = literature_access(work)
    assert access == {"PMID:123|PMCID:PMC123|DOI:10.1000/alias": "full_text"}
    ledgers = {"s1": {
        "claims": [{"id": "c1", "status": "supported"}],
        "evidence": [
            {"id": "read", "source": {"id_scheme": "doi", "id_value": "https://doi.org/10.1000/ALIAS"}},
            {"id": "missing", "source": {"uri": "https://pubmed.ncbi.nlm.nih.gov/999/"}},
        ],
        "links": [
            {"claim_id": "c1", "evidence_id": "read", "relation": "supports"},
            {"claim_id": "c1", "evidence_id": "missing", "relation": "supports"},
        ],
    }}

    assert conclusion_literature_line([{"id": "s1"}], {"s1": {"literature_access": access}}, ledgers) == (
        "결론 근거 논문 2편: 본문 1편·초록 0편·서지만 0편·조회 기록 없음 1편. "
        "결론 claim에 연결된 논문만 센 값이며, 'fulltext로 조회한 논문 전체'와 분모가 다르다"
    )


def test_conclusion_literature_has_no_line_without_paper_evidence():
    ledgers = {"s1": {
        "claims": [{"id": "c1", "status": "supported"}],
        "evidence": [{"id": "e1", "source": {"artifact_id": "a1", "locator": "row 1"}}],
        "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}],
    }}
    assert conclusion_literature_line([{"id": "s1"}], {}, ledgers) is None


def test_general_report_warnings_show_table_problems():
    results = {"s1": TaskResult(task_id="t1", agent_id="a", ok=True, table_warnings=["outputs/a.tsv: 문제"])}
    assert "- s1: 표로 읽히지 않는 산출물 1개 — outputs/a.tsv: 문제" in general_report_warnings([{"id": "s1"}], results)


def test_wire_form_omits_empty_check_fields():
    data = TaskResult(task_id="t", agent_id="a", ok=True).model_dump()
    assert "table_warnings" not in data and "literature_access" not in data and "literature_ledger" not in data


@pytest.mark.asyncio
async def test_research_cp2_card_and_report_limits_carry_the_checks():
    hub = _hub()
    original = hub.reply

    async def reply(task):
        result = await original(task)
        if task.meta["kind"] == "step":
            result.table_warnings = ["outputs/lit.tsv: 칸 구분이 탭이 아니라 글자 \\t입니다"]
            result.literature_access = {"PMID:1": "full_text", "PMID:2": "abstract", "PMID:99": "metadata"}
            result.literature_ledger = {
                "candidate_ids": ["PMID:1", "PMID:2", "DOI:10.1000/missing", "OpenAlex:W4"],
                "selected_ids": ["PMID:1", "PMID:2", "DOI:10.1000/missing"],
                "unread": {"PMID:2": "subscription_required"},
            }
            result.structured["evidence"] += [
                {"id": "paper_full", "kind": "literature_claim", "observation": "paper one",
                 "status": "observed", "source": {"uri": "https://pubmed.ncbi.nlm.nih.gov/1/",
                                                      "accessed_at": "2026-10-18", "locator": "results"},
                 "directness": "direct", "source_level": "primary", "independence_group": "paper1",
                 "assessment_reason": "supports the conclusion", "slots": []},
                {"id": "paper_abstract", "kind": "literature_claim", "observation": "paper two",
                 "status": "observed", "source": {"uri": "https://pubmed.ncbi.nlm.nih.gov/2/",
                                                      "accessed_at": "2026-10-18", "locator": "abstract"},
                 "directness": "direct", "source_level": "primary", "independence_group": "paper2",
                 "assessment_reason": "supports the conclusion", "slots": []},
                {"id": "paper_missing", "kind": "literature_claim", "observation": "paper three",
                 "status": "observed", "source": {"uri": "https://doi.org/10.1000/missing",
                                                      "accessed_at": "2026-10-18", "locator": "discussion"},
                 "directness": "direct", "source_level": "primary", "independence_group": "paper3",
                 "assessment_reason": "supports the conclusion", "slots": []},
                {"id": "paper_unused", "kind": "literature_claim", "observation": "not used in a conclusion",
                 "status": "observed", "source": {"uri": "https://pubmed.ncbi.nlm.nih.gov/99/",
                                                      "accessed_at": "2026-10-18", "locator": "abstract"},
                 "directness": "indirect", "source_level": "primary", "independence_group": "paper99",
                 "assessment_reason": "background only", "slots": []},
            ]
            result.structured["links"] += [
                {"claim_id": "c1", "claim_revision": 1, "evidence_id": evidence_id,
                 "relation": "supports", "rationale": "the paper supports the conclusion"}
                for evidence_id in ("paper_full", "paper_abstract", "paper_missing")
            ]
        return result

    hub.reply = reply
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("error")
    card = json.dumps(next(a["detail"] for a in hub.approvals if a["kind"] == "research_evidence"), ensure_ascii=False)
    assert "표로 읽히지 않는 산출물 1개" in card and "본문 1편, 초록 1편" in card
    depth = "결론 근거 논문 3편: 본문 1편·초록 1편·서지만 0편·조회 기록 없음 1편"
    assert depth in card
    ledger = "문헌 접근 실패 원장(서로 다른 분모): 후보 4편·선정 3편·확보 3편(본문 1·초록 1·서지 1)·결론 근거 3편"
    assert ledger in card and "PMID:2(구독 필요)" in card and "DOI:10.1000/missing(조회 기록 없음)" in card
    limits = req["report"].split("## 한계", 1)[1]
    assert "outputs/lit.tsv" in limits and "fulltext로 조회한 3편" in limits and depth in limits and ledger in limits


# ---------- #1090: papers the precedent search stored before CP1 count as read ----------

def _precedents():
    """The four stored papers of ② 6th (receipts and alias lists as the precedent search wrote them)."""
    def paper(paper_id, level, extraction, citations):
        return {"title": paper_id, "citations": citations, "fulltext_receipt": {
            "paper_id": paper_id, "access_level": level, "extraction": extraction, "text_sha256": "a" * 64}}

    return {"status": "ok", "papers": [
        paper("doi:10.1056/nejmoa2103695", "full_text", "unknown",
              ["PMID:34096690", "PMCID:PMC9116274", "DOI:10.1056/NEJMoa2103695"]),
        paper("doi:10.1200/jco.21.02752", "full_text", "partial_text",
              ["PMID:35167329", "PMCID:PMC9362872", "DOI:10.1200/JCO.21.02752"]),
        paper("doi:10.1038/s41467-026-69943-7", "full_text", "partial_text",
              ["PMID:41820335", "PMCID:PMC13121619", "DOI:10.1038/s41467-026-69943-7"]),
        paper("doi:10.1016/j.annonc.2025.01.020", "abstract", "unknown",
              ["PMID:39914665", "PMCID:PMC12097956", "DOI:10.1016/j.annonc.2025.01.020"])]}


def _sixth_ledgers():
    # The conclusion cites NEJM twice (by DOI and by PMID) and no step looked anything up.
    sources = {"jco": {"id_scheme": "doi", "id_value": "10.1200/JCO.21.02752"},
               "nejm_pmid": {"id_scheme": "pmid", "id_value": "34096690"},
               "olo": {"id_scheme": "doi", "id_value": "10.1038/s41467-026-69943-7"},
               "nejm_doi": {"id_scheme": "doi", "id_value": "10.1056/NEJMoa2103695"},
               "annals": {"id_scheme": "doi", "id_value": "10.1016/j.annonc.2025.01.020"}}
    return {"s2": {
        "claims": [{"id": "c1", "status": "supported"}],
        "evidence": [{"id": key, "source": source} for key, source in sources.items()],
        "links": [{"claim_id": "c1", "evidence_id": key, "relation": "supports"} for key in sources]}}


NO_LOOKUPS = "결론 claim에 연결된 논문만 센 값이며, 'fulltext로 조회한 논문 전체'와 분모가 다르다"


def test_the_sixth_run_without_the_stored_papers_still_reads_zero_of_five():
    steps, results = [{"id": "s2"}], {"s2": {"literature_access": {}}}
    ledgers = _sixth_ledgers()
    before = conclusion_literature_line(steps, results, ledgers)
    assert before == f"결론 근거 논문 5편: 본문 0편·초록 0편·서지만 0편·조회 기록 없음 5편. {NO_LOOKUPS}"
    assert conclusion_literature_line(steps, results, ledgers, stored=()) == before  # an empty argument changes nothing


def test_stored_papers_are_counted_with_their_origin_and_aliases_join_the_two_nejm_citations():
    steps, results = [{"id": "s2"}], {"s2": {"literature_access": {}}}
    line = conclusion_literature_line(steps, results, _sixth_ledgers(), stored=stored_access(_precedents()))
    assert line == ("결론 근거 논문 4편: 본문 3편·초록 1편·서지만 0편·조회 기록 없음 0편"
                    f"(저장된 선행 검색 기록 4편 포함·CP1 전 기록, 그중 부분 추출 2편). {NO_LOOKUPS}")


def test_a_stored_paper_the_conclusion_does_not_cite_is_not_added():
    ledgers = _sixth_ledgers()
    for key in ("jco", "nejm_pmid", "olo", "annals"):  # only NEJM (by DOI) stays cited
        ledgers["s2"]["links"] = [link for link in ledgers["s2"]["links"] if link["evidence_id"] != key]
    line = conclusion_literature_line([{"id": "s2"}], {}, ledgers, stored=stored_access(_precedents()))
    assert line is None  # one paper, full text: nothing to warn about, and the three uncited ones add no row


def test_a_step_record_and_a_stored_record_of_one_paper_take_the_better_level():
    results = {"s2": {"literature_access": {"DOI:10.1016/j.annonc.2025.01.020": "full_text"}}}
    stored = stored_access(_precedents())
    # The step read the Annals paper in full: all four papers are then full text, so there is nothing to warn about.
    assert conclusion_literature_line([{"id": "s2"}], results, _sixth_ledgers(), stored=stored) is None
    # A step that only reached the NEJM abstract does not lower the stored full text.
    weaker = {"s2": {"literature_access": {"DOI:10.1056/nejmoa2103695": "abstract"}}}
    line = conclusion_literature_line([{"id": "s2"}], weaker, _sixth_ledgers(), stored=stored)
    assert "본문 3편·초록 1편" in line and "조회 기록 없음 0편" in line


@pytest.mark.parametrize("record", [
    None, "ok", {"status": "failed", "papers": [{"fulltext_receipt": {"paper_id": "doi:10.1/x", "access_level": "full_text"}}]},
    {"status": "ok", "papers": [{"fulltext_receipt": {"paper_id": "doi:10.1/x", "access_level": "everything"}}]},
    {"status": "ok", "papers": [{"fulltext_receipt": "text"}, "paper", {"title": "no receipt"}]},
    {"status": "ok", "papers": [{"fulltext_receipt": {"paper_id": "", "access_level": "full_text"}}]},
])
def test_unusable_precedent_records_store_nothing(record):
    assert stored_access(record) == []


def test_the_access_ledger_line_counts_stored_papers_as_acquired():
    ledger = {"candidate_ids": ["DOI:10.1056/nejmoa2103695"], "selected_ids": ["DOI:10.1056/nejmoa2103695"]}
    results = {"s2": {"literature_ledger": ledger}}
    base = literature_ledger_line([{"id": "s2"}], results, _sixth_ledgers())
    line = literature_ledger_line([{"id": "s2"}], results, _sixth_ledgers(), stored=stored_access(_precedents()))
    assert "확보 0편" in base and "결론 근거 5편" in base
    assert "확보 4편(본문 3·초록 1·서지 0)" in line and "결론 근거 4편" in line
    assert "확보에는 저장된 선행 검색 기록 4편(CP1 전 기록)이 들어 있습니다." in line
