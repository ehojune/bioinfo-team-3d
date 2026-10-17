"""The README answers with the verdict and its branch table shows findings, not ledger JSON (#718)."""

import json

from labhq import portfolio as p

LEDGER = {"schema_version": 2, "step_id": "s2", "claims": [
    {"id": "c1", "statement": "두 자료 모두 오즈비가 1을 포함했다. 주 검정 2개 중 통과는 0개다. 세 번째 문장."}]}
REPORT = ("# 제목\n\n## 요약(Abstract)\n\n속설을 세 근거로 살폈습니다 [[claim:s2/c1]].\n\n"
          "두 자료 모두 신뢰구간이 1을 포함했습니다.\n\n## 서론\n\n본문.\n")


def research_request(text_only=False):
    result = {"ok": True, "text": json.dumps(LEDGER, ensure_ascii=False)}
    if not text_only:
        result["structured"] = LEDGER
    final = {"ok": True, "structured": {"claims": [
        {"id": "f", "statement": "그래서 주 가설 '아버지의 직업 쪽 단태아보다 여아가 많다.'는 지지되지 않습니다. 관찰 연관입니다."}]}}
    return {"plan": {"branches": [{"id": "b1", "question": "q", "evidence_type": "인구"}],
                     "steps": [{"id": "s2", "branch": "b1"}, {"id": "s9", "branch": "synthesis", "depends_on": ["s2"]}]},
            "results": {"s2": result, "s9": final}}


def test_claims_replace_the_ledger_json_whether_structured_or_only_text():
    for text_only in (False, True):
        result = research_request(text_only)["results"]["s2"]
        assert p._result_text(result) == LEDGER["claims"][0]["statement"]
        assert p._claim_summary(result) == "두 자료 모두 오즈비가 1을 포함했다. 주 검정 2개 중 통과는 0개다."
    assert p._result_text({"text": '{"schema_version": 2, "claims": []}'}) == ""  # a ledger without claims
    assert p._result_text({"text": '{"genes": ["TP53"]}'}) == '{"genes": ["TP53"]}'  # an ordinary JSON answer
    assert p._result_text({"text": "{broken json"}) == "{broken json"
    assert p._result_text({"text": "일반 단계 결과."}) == "일반 단계 결과."


def test_answer_is_the_verdict_sentence_without_a_connective_or_anchor():
    answer = p._first_answer(REPORT, research_request())
    assert answer == "주 가설 '아버지의 직업 쪽 단태아보다 여아가 많다.'는 지지되지 않습니다."
    assert p._first_answer(REPORT) == "속설을 세 근거로 살폈습니다."  # no verdict anywhere: the first abstract paragraph


def test_branch_table_never_prints_ledger_json():
    req = research_request(text_only=True)
    specs, folders = p._branch_layout(req)
    rows = p._branch_rows(req, specs, folders)
    assert rows and all("schema_version" not in row["result"] for row in rows)
