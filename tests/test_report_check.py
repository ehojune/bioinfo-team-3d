"""The claim-anchor check of a research report (#58 ③), without the orchestrator."""

import httpx
import pytest

from labhq.evidence.report_check import check_report, claim_rows, failed_lookups


def test_verify_report_ids_separates_misses_and_lookup_failures(tmp_path, monkeypatch):
    import json
    from io import BytesIO
    from zipfile import ZipFile

    from labhq.evidence import audit
    from labhq.evidence.report_identifiers import ReportIdentifierResolver
    from labhq.settings import Settings

    seen = []

    def registry(request):
        assert "mailto:maintainer@example.test" in request.headers["User-Agent"]
        path = request.url.path
        seen.append(path)
        if path.endswith("/symbol/P53"):
            return httpx.Response(200, json={"responseHeader": {"status": 0}, "response": {"numFound": 0, "docs": []}})
        if path.endswith("/alias_symbol/P53"):
            return httpx.Response(200, json={"responseHeader": {"status": 0}, "response": {"numFound": 1, "docs": [
                {"symbol": "TP53", "alias_symbol": ["P53"], "status": "Approved"}]}})
        if path.endswith("/symbol/BRCA1"):
            return httpx.Response(200, json={"responseHeader": {"status": 0}, "response": {"numFound": 1, "docs": [
                {"symbol": "BRCA1", "status": "Approved"}]}})
        if path.endswith(("/symbol/C4B_2", "/symbol/C17orf50")):
            return httpx.Response(200, json={"responseHeader": {"status": 0}, "response": {"numFound": 1, "docs": [
                {"symbol": path.rsplit("/", 1)[-1], "status": "Approved"}]}})
        if path.endswith("/MOCKMISS"):
            return httpx.Response(200, json={"responseHeader": {"status": 0}, "response": {"numFound": 0, "docs": []}})
        if path.endswith("GO:9999999"):
            return httpx.Response(200, json={"numberOfHits": 0, "results": []})
        if path.endswith("GO:0000005"):
            return httpx.Response(200, json={"numberOfHits": 1, "results": [
                {"id": "GO:0000005", "isObsolete": False}]})
        if path.endswith("GO:0000004"):
            return httpx.Response(200, json={"numberOfHits": 1, "results": [
                {"id": "GO:0008150", "isObsolete": False}]})
        if path.endswith("10.1000/1"):
            return httpx.Response(200, json={"responseCode": 1, "handle": "10.1000/1"})
        if path.endswith("10.1000/a(study)"):
            return httpx.Response(200, json={"responseCode": 1, "handle": "10.1000/A(study)"})
        if path.endswith("10.1000/missing"):
            return httpx.Response(404, json={"responseCode": 100, "handle": "10.1000/missing"})
        if path.endswith("10.1000/proxy"):
            return httpx.Response(404, text="Proxy error")
        if path.endswith("10.1000/slow"):
            raise httpx.ReadTimeout("test timeout")
        pytest.fail(f"unexpected registry lookup: {path}")

    monkeypatch.setattr(audit, "ReportIdentifierResolver", lambda **kwargs: ReportIdentifierResolver(
        transport=httpx.MockTransport(registry), **kwargs))
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path)
    settings.research.live_source_contact = "maintainer@example.test"
    req = {"id": "report-ids", "research_contract": {}, "report": (
        "# Identifier notes\n\n**HGNC symbols:** **P53**, `BRCA1`, __MOCKMISS__, _C4B_2_, go:0000004. "
        "[DOI](https://doi.org/10.1000/1?source=example). DOI:10.1000/MISSING. "
        "DOI:10.1000/slow. DOI:10.1000/proxy.\n\n"
        "~~~\nHGNC symbol: CODE\nGO:0000001\n~~~\n\n"
        "<!-- GO:0000002 -->\n"
    ), "report_appendix": "## 부록\n\nGO:0000005\n\n## 부록: 실행 기록\n\nGO:0000007"}
    ledgers = {"s1": {"claims": [{"statement": "GO:9999999 and DOI:10.1000/1"}], "evidence": [
        {"status": "observed", "source": {"id_scheme": "hgnc_symbol", "id_value": "C17orf50"}},
        {"status": "observed", "source": {"id_scheme": "doi", "id_value": "https://doi.org/10.1000/A%28study%29"}},
    ]}}
    monkeypatch.setattr(audit, "research_ledgers", lambda _: ledgers)
    monkeypatch.setattr(audit, "_run_live_source_reports", lambda *_: [])
    assert audit.verify_request(req, settings)["identifier_check"] is None
    assert not seen

    settings.research.live_source_check = True
    result = audit.verify_request(req, settings)
    check = result["identifier_check"]
    by_id = {row["value"]: row for row in check["items"]}
    assert len(seen) == 16 and seen.count("/api/handles/10.1000/1") == 1
    assert by_id["10.1000/a(study)"]["status"] == "found"
    assert by_id["P53"]["matches"] == ["TP53"] and by_id["P53"]["detail"] == "alias_symbol"
    for symbol in ("BRCA1", "C4B_2", "C17orf50"):
        assert by_id[symbol]["status"] == "found"
    assert by_id["MOCKMISS"]["status"] == "not_found"
    assert by_id["GO:0000005"]["status"] == "found" and "GO:0000007" not in by_id
    assert by_id["GO:0000004"]["matches"] == ["GO:0008150"]
    assert by_id["GO:9999999"]["status"] == "not_found"
    assert by_id["10.1000/missing"]["status"] == "not_found"
    assert by_id["10.1000/proxy"]["status"] == "unverified"
    assert by_id["10.1000/slow"]["detail"] == "timeout"
    assert result["exit_code"] == 2
    assert {row.split()[2].removesuffix(":") for row in result["problems"]} == {"MOCKMISS", "GO:9999999", "10.1000/missing"}
    assert "등록 확인" in audit.render_verify(result) and "조회 미완료" in audit.render_verify(result)
    with ZipFile(BytesIO(audit.bundle_bytes(result, req))) as bundle:
        assert json.loads(bundle.read("claims.json"))["identifier_check"] == check


def test_evidence_and_identifier_checks_share_the_live_source_deadline(tmp_path, monkeypatch):
    from labhq.evidence import audit
    from labhq.evidence.report_identifiers import ReportIdentifierResolver
    from labhq.settings import Settings

    def unexpected_lookup(_):
        pytest.fail("an exhausted deadline must not send an identifier lookup")

    monkeypatch.setattr(audit, "monotonic", iter((10.0, 15.0)).__next__)
    monkeypatch.setattr(audit, "_run_live_source_reports", lambda *_: [])
    monkeypatch.setattr(audit, "ReportIdentifierResolver", lambda **kwargs: ReportIdentifierResolver(
        transport=httpx.MockTransport(unexpected_lookup), **kwargs))
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path)
    settings.research.live_source_check = True
    settings.research.live_source_deadline_s = 5.0
    result = audit.verify_request({"id": "report-ids", "research_contract": {}, "report": "GO:0000004"}, settings)
    assert result["identifier_check"]["items"] == [
        {"scheme": "go", "value": "GO:0000004", "status": "unverified", "detail": "deadline"},
    ]
    assert result["exit_code"] == 2


@pytest.mark.asyncio
async def test_report_identifier_deadline_and_limit_are_incomplete():
    import asyncio

    from labhq.evidence.report_identifiers import LIMIT, ReportIdentifierResolver, check_identifiers

    async def slow(request):
        await asyncio.sleep(60)
        pytest.fail("the report deadline must cancel the lookup")

    text = " ".join(f"GO:{index:07}" for index in range(LIMIT + 2))
    resolver = ReportIdentifierResolver(transport=httpx.MockTransport(slow))
    result = await check_identifiers([text], {}, resolver, deadline_s=0.01)
    assert result["omitted"] == 2 and len(result["items"]) == LIMIT
    assert all(row["status"] == "unverified" and row["detail"] == "deadline" for row in result["items"])


@pytest.mark.asyncio
async def test_report_identifier_resolver_crash_is_unverified(monkeypatch):
    import sqlite3

    from labhq.evidence.report_identifiers import ReportIdentifierResolver, check_identifiers

    async def broken_lookup(*_):
        raise sqlite3.OperationalError("admission database unavailable")

    resolver = ReportIdentifierResolver()
    monkeypatch.setattr(resolver, "matches", broken_lookup)
    result = await check_identifiers(["GO:0000004"], {}, resolver, deadline_s=1)
    assert result["items"] == [
        {"scheme": "go", "value": "GO:0000004", "status": "unverified", "detail": "resolver_error"},
    ]


def _ledger(status="supported", artifact="a1"):
    return {"claims": [{"id": "c1", "status": status}],
            "evidence": [{"id": "e1", "status": "observed", "source": {"artifact_id": artifact}}],
            "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}]}


@pytest.mark.parametrize(("ledger", "hashes", "problem"), [
    (_ledger(), {}, "no recorded artifact_sha256"),
    (_ledger(), {"s1/a1": None}, "no recorded artifact_sha256"),
    (_ledger(status="unresolved"), {"s1/a1": "a" * 64}, "status unresolved"),
])
def test_anchor_check_reads_status_and_artifact_hash(ledger, hashes, problem):
    check = check_report("x [[claim:s1/c1]] y [[claim:s1/c1]]", {"s1": ledger}, artifact_sha256=hashes)
    assert check["anchors"] == 2 and len(check["problems"]) == 1 and problem in check["problems"][0]
    assert check_report("x [[claim:s1/c1]]", {"s1": _ledger()}, artifact_sha256={"s1/a1": "a" * 64}) == {
        "anchors": 1, "problems": []}


def test_citable_claims_and_failed_lookups_come_from_the_ledger():
    ledger = _ledger()
    ledger["claims"].append({"id": "c2", "status": "proposed"})
    ledger["evidence"].append({"id": "e2", "status": "not_found", "observation": "no hits",
                               "source": {"uri": "https://example.org", "query": "gene X"}})
    citable, other = claim_rows({"s1": ledger, "s2": _ledger()}, [{"step_id": "s2", "claim_id": "c1"}])
    assert [(row["step_id"], row["claim"]["id"]) for row in citable] == [("s1", "c1")]
    assert [(row["step_id"], row["claim"]["id"], row["reason"]) for row in other] == [
        ("s1", "c2", "status proposed"), ("s2", "c1", "rests only on evidence refused at CP2")]
    assert failed_lookups({"s1": ledger}) == [{"step_id": "s1", "evidence_id": "e2", "status": "not_found",
                                               "observation": "no hits", "detail": "", "query": "gene X"}]


def test_anchor_variants_are_checked_and_code_is_not_a_conclusion():
    """A model may write [[Claim: s1/c9]]; it is checked, not skipped. The syntax shown in code states nothing
    (PR #336 review)."""
    hashes = {"s1/a1": "a" * 64}
    check = check_report("x [[claim:s1/c1]]. y [[Claim: s1/c9]]. z [[ CLAIM:s1/c1 ]]", {"s1": _ledger()},
                         artifact_sha256=hashes)
    assert check["anchors"] == 3 and len(check["problems"]) == 1 and "claim c9" in check["problems"][0]
    shown = "x [[claim:s1/c1]].\n```\n[[claim:<step_id>/<claim_id>]]\n```\nand `[[claim:s9/c1]]`"
    assert check_report(shown, {"s1": _ledger()}, artifact_sha256=hashes) == {"anchors": 1, "problems": []}


def test_each_numeric_prose_sentence_requires_its_own_anchor():
    report = ("# 결과 2026\n\n첫 결과는 3입니다. [[claim:s1/c1]] 두 번째 결과는 12.3입니다.\n"
              "셋째 결과는 4%입니다 [[claim:s1/c1]].\n\n"
              "| 값 | 9 |\n|---|---|\n| A | 7 |\n\n"
              "Name | Value\n--- | ---\nfoo | 12.3\n\n"
              "`42`와 DUSP1 및 [자료](https://example.org/123)는 식별자입니다.\n"
              "~~~\n값은 99입니다.\n~~~\n")
    check = check_report(report, {"s1": _ledger()}, artifact_sha256={"s1/a1": "a" * 64})
    assert len(check["problems"]) == 3
    assert "12.3" in check["problems"][0] and "anchor" in check["problems"][0]
    assert any("A | 7" in problem for problem in check["problems"])
    assert any("foo | 12.3" in problem for problem in check["problems"])


def test_unnumbered_conclusions_and_each_table_row_need_anchors():
    report = ("가설은 기각됩니다.\n지지 여부는 불확실합니다. [[claim:s1/c1]]\n\n"
              "| 결과 | 판정 |\n|---|---|\n| A | 증가 |\n| B | 감소 [[claim:s1/c1]] |\n")
    problems = check_report(report, {"s1": _ledger()}, artifact_sha256={"s1/a1": "a" * 64})["problems"]
    assert any("가설은 기각" in problem for problem in problems)
    assert any("A | 증가" in problem for problem in problems)
    assert not any("B | 감소" in problem for problem in problems)
    assert not any("지지 여부는 불확실" in problem for problem in problems)


def test_commented_out_table_is_not_a_visible_claim():
    report = '<!--\n| Claim | Value |\n|---|---|\n| hidden | 7 |\n-->\n'
    assert check_report(report, {})['problems'] == []


def test_numeric_units_and_soft_wrapped_sentences():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    assert _unanchored_numeric_sentences("세포를 24h 배양했습니다.\n약물은 5mg, 부피는 10mL였습니다.") == [
        "세포를 24h 배양했습니다.", "약물은 5mg, 부피는 10mL였습니다."]
    assert _unanchored_numeric_sentences("첫 결과는 3이며\n유의했습니다. [[claim:s1/c1]]") == []
    assert _unanchored_numeric_sentences("첫 결과는 3입니다.\n둘째는 4입니다. [[claim:s1/c1]]") == [
        "첫 결과는 3입니다."]
    assert _unanchored_numeric_sentences("DUSP1과 123abc는 식별자입니다.") == []


def test_nested_short_fences_do_not_end_a_long_code_fence():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    report = "````markdown\n```python\nx = 42\n```\nmore = 5\n````\n결과는 7입니다."
    assert _unanchored_numeric_sentences(report) == ["결과는 7입니다."]


def test_figure_alt_number_is_not_numeric_prose():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    assert _unanchored_numeric_sentences("![그림 1. 발현량](plots/expression.png)\n\n결과는 7입니다.") == [
        "결과는 7입니다."]


def test_inline_code_with_multiple_backticks_is_not_numeric_prose():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    assert _unanchored_numeric_sentences("예: ``x = 42``. 결과는 7입니다.") == ["결과는 7입니다."]


def test_footnotes_are_not_numeric_prose():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    report = "설명입니다.[^1]\n\n[^1]: 출처 2020\n    추가 42\n\n결과는 7입니다."
    assert _unanchored_numeric_sentences(report) == ["결과는 7입니다."]


def test_scientific_notation_is_numeric_prose():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    assert _unanchored_numeric_sentences("세포는 1e6개였습니다. p값은 2E-5였습니다.") == [
        "세포는 1e6개였습니다.", "p값은 2E-5였습니다."]


def test_commonmark_code_boundaries_do_not_mask_or_create_numeric_prose():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    assert _unanchored_numeric_sentences("미완성 `\n\n결과는 42입니다.\n\n다른 문단 `") == ["결과는 42입니다."]
    assert _unanchored_numeric_sentences("    x = 42\n\n- ```python\n  y = 9\n  ```\n\n결과는 7입니다.") == [
        "결과는 7입니다."]


def test_setext_headings_are_not_numeric_prose():
    from labhq.evidence.report_check import _unanchored_numeric_sentences

    assert _unanchored_numeric_sentences("결과 2026\n==========\n\n값은 7입니다.") == ["값은 7입니다."]


def test_legacy_summary_rows_do_not_bypass_cp2_refusal():
    ledger = _ledger()
    del ledger["evidence"][0]["status"]
    assert [row["claim"]["id"] for row in claim_rows({"s1": ledger})[0]] == ["c1"]
    refused = [{"step_id": "s1", "evidence_id": "e1"}]
    assert not claim_rows({"s1": ledger}, refused=refused)[0]
    assert "no CP2-accepted support" in check_report("x [[claim:s1/c1]]", {"s1": ledger},
                                                    refused=refused)["problems"][0]


# ----- #1374: the report's numbers and directions against the tables it links (synthetic, #910-like) -----

DE_TABLE = ("gene_id\tsymbol\tbaseMean\tlog2FoldChange\tpadj\n"
            "E1\tDUSP1\t3445\t2.959\t1.06e-139\nE2\tKLF15\t604.4\t4.486\t4.2e-87\nE3\tFOO1\t100\t-1.8\t0.001\n")
CLINICAL = "trial\torr\tci_low\tci_high\tpfs_months\thr\nTRIAL-A\t0.43\t0.30\t0.60\t6.5\t0.58\n"
TABLES = {"steps/s3/outputs/de.tsv": DE_TABLE, "steps/s2/outputs/clinical.tsv": CLINICAL}


def _consistency(text):
    from labhq.evidence.report_consistency import check
    return check(text, TABLES)


@pytest.mark.parametrize("sentence", [
    "DUSP1은 dexamethasone에서 증가했습니다(log2FC 2.96, [표](steps/s3/outputs/de.tsv)).",
    "ORR은 43%였고(95% CI 30–60%) PFS 중앙값은 6.5개월이었습니다([임상](steps/s2/outputs/clinical.tsv)).",
    "HR 0.58로 진행 위험이 감소했습니다([임상](steps/s2/outputs/clinical.tsv)).",
    "HR 0.58로 무진행 생존이 길어졌습니다([임상](steps/s2/outputs/clinical.tsv)).",  # survival flips a ratio
    "padj < 0.05, |log2FC| > 1 기준으로 골랐습니다([표](steps/s3/outputs/de.tsv)).",  # thresholds are settings
    "DUSP1 baseMean은 3,445였습니다 [표](steps/s3/outputs/de.tsv).",
    "Upregulated genes included KLF15 ([table](steps/s3/outputs/de.tsv)).",
])
def test_a_sentence_that_matches_its_table_is_not_flagged(sentence):
    assert _consistency(sentence)["findings"] == []


@pytest.mark.parametrize("sentence, kind, value", [
    ("KLF15의 log2FC는 4.9였습니다([표](steps/s3/outputs/de.tsv)).", "number", "4.9"),
    ("ORR은 48%였습니다([임상](steps/s2/outputs/clinical.tsv)).", "number", "48%"),
    ("FOO1은 처리 뒤 증가했습니다([표](steps/s3/outputs/de.tsv)).", "direction", "FOO1 -1.8"),
    ("HR 0.58로 진행 위험이 증가했습니다([임상](steps/s2/outputs/clinical.tsv)).", "direction", "HR 0.58"),
    ("The log2FC of 2.96 shows DUSP1 was downregulated ([table](steps/s3/outputs/de.tsv)).", "direction",
     "log2FC 2.96"),
])
def test_the_910_kinds_of_mismatch_are_found(sentence, kind, value):
    findings = _consistency(sentence)["findings"]
    assert {row["kind"] for row in findings} == {kind} and value in [row["value"] for row in findings]


def test_only_sentences_that_link_a_table_are_compared_and_counts_are_kept():
    result = _consistency("## 결과\n\n본문에 표가 없는 수치 12.3입니다.\n\n| a | 9.99 |\n|---|---|\n"
                          "KLF15 log2FC 4.49 ([표](steps/s3/outputs/de.tsv)).\n")
    assert result["findings"] == []
    assert result["checked"]["numbers"] == 1 and "steps/s3/outputs/de.tsv" in result["checked"]["tables"]


def test_tables_are_read_only_from_a_steps_declared_outputs_in_its_own_folder(tmp_path):
    from labhq.evidence.report_consistency import tables_for
    step = tmp_path / "s3"
    (step / "outputs").mkdir(parents=True)
    (step / "outputs" / "de.tsv").write_text(DE_TABLE, encoding="utf-8")
    (step / "outputs" / "secret.tsv").write_text("x\n", encoding="utf-8")
    report = "[a](steps/s3/outputs/de.tsv) [b](steps/s3/outputs/secret.tsv) [c](steps/s3/outputs/../../x.tsv)"
    found = tables_for(report, {"s3": {"workdir": str(step), "outputs": ["outputs/de.tsv"]}})
    assert {k: v for k, v in found.items() if v is not None} == {"steps/s3/outputs/de.tsv": DE_TABLE}
    # undeclared or escaping links are not read, and are kept as "linked but not compared"
    assert sorted(k for k, v in found.items() if v is None) == [
        "steps/s3/outputs/../../x.tsv", "steps/s3/outputs/secret.tsv"]


async def test_the_writer_gets_one_correction_and_a_worse_fix_is_not_kept(tmp_path):
    from labhq.evidence.report_consistency import correct_once
    step = tmp_path / "s3"
    (step / "outputs").mkdir(parents=True)
    (step / "outputs" / "de.tsv").write_text(DE_TABLE, encoding="utf-8")
    results = {"s3": {"workdir": str(step), "outputs": ["outputs/de.tsv"]}}
    wrong = "FOO1은 증가했습니다([표](steps/s3/outputs/de.tsv))."
    prompts = []

    async def good(prompt):
        prompts.append(prompt)
        return "FOO1은 감소했습니다([표](steps/s3/outputs/de.tsv))."

    body, record = await correct_once(wrong, results, good)
    assert "감소" in body and record["correction"] == "applied" and record["findings"] == []
    assert len(prompts) == 1 and "FOO1" in prompts[0] and "Do not redo the analysis" in prompts[0]

    async def same(prompt):
        return wrong

    body, record = await correct_once(wrong, results, same)
    assert body == wrong and record["correction"] == "rejected" and len(record["findings"]) == 1
    body, record = await correct_once(wrong, results, good, accept=lambda text: False)  # the claim check said no
    assert body == wrong and record["correction"] == "rejected"


def test_the_report_front_says_draft_and_no_rerun():
    from labhq.evidence.report_consistency import front_lines
    left = _consistency("FOO1은 증가했습니다([표](steps/s3/outputs/de.tsv)).")
    lines = front_lines(left, {"execution": "not_rerun"}, hpc=True)
    assert "검증 전 초안" in lines[0] and "1건" in lines[0]
    assert lines[1].startswith("- 재현 실행 안 함(HPC)")
    clean = front_lines(_consistency("KLF15 log2FC 4.49 ([표](steps/s3/outputs/de.tsv))."), {"execution": "pass"})
    assert clean == [clean[0]] and "불일치 없음" in clean[0]


async def test_a_correction_that_drops_a_link_heading_or_most_of_the_report_is_not_kept(tmp_path):
    from labhq.evidence.report_consistency import correct_once
    step = tmp_path / "s3"
    (step / "outputs").mkdir(parents=True)
    (step / "outputs" / "de.tsv").write_text(DE_TABLE, encoding="utf-8")
    results = {"s3": {"workdir": str(step), "outputs": ["outputs/de.tsv"]}}
    wrong = ("## 결과\n\nFOO1은 증가했습니다([표](steps/s3/outputs/de.tsv)).\n\n"
             "## 한계\n\n표본이 작습니다. " + "근거 문장입니다. " * 20 + "\n")
    for fixed in (
        wrong.replace("([표](steps/s3/outputs/de.tsv))", ""),  # hides the number instead of fixing it
        wrong.replace("## 한계", "## 기타"),  # a heading changed
        "## 결과\n\nFOO1은 감소했습니다([표](steps/s3/outputs/de.tsv)).\n\n## 한계\n\n짧게.\n",  # most text gone
    ):
        async def run(prompt, fixed=fixed):
            return fixed

        body, record = await correct_once(wrong, results, run)
        assert body == wrong and record["correction"] == "rejected_structure"


def test_scientific_notation_is_compared_at_its_own_scale():
    sentence = "DUSP1의 padj는 {}였습니다([표](steps/s3/outputs/de.tsv))."
    assert _consistency(sentence.format("1.06e-139"))["findings"] == []
    assert _consistency(sentence.format("1.1e-139"))["findings"] == []  # rounding at the shown scale
    assert [row["value"] for row in _consistency(sentence.format("1.2e-5"))["findings"]] == ["1.2e-5"]


def test_an_angle_bracket_link_with_spaces_is_read(tmp_path):
    from labhq.evidence.report_consistency import check, tables_for
    step = tmp_path / "s3"
    (step / "outputs").mkdir(parents=True)
    (step / "outputs" / "my de.tsv").write_text(DE_TABLE, encoding="utf-8")
    report = "FOO1은 증가했습니다([표](<steps/s3/outputs/my de.tsv>)).\n"
    tables = tables_for(report, {"s3": {"workdir": str(step), "outputs": ["outputs/my de.tsv"]}})
    assert list(tables) == ["steps/s3/outputs/my de.tsv"]
    assert [row["kind"] for row in check(report, tables)["findings"]] == ["direction"]


def test_a_flipped_sign_without_a_direction_word_is_found():
    [finding] = _consistency("FOO1의 log2FC는 1.8이었습니다([표](steps/s3/outputs/de.tsv)).")["findings"]
    assert finding["kind"] == "sign" and finding["value"] == "1.8"
    assert _consistency("FOO1의 log2FC는 -1.8이었습니다([표](steps/s3/outputs/de.tsv)).")["findings"] == []
    assert _consistency("FOO1은 1.8만큼 감소했습니다([표](steps/s3/outputs/de.tsv)).")["findings"] == []  # words carry it


def test_survival_inverts_a_ratio_instead_of_skipping_it():
    [finding] = _consistency("HR 0.58로 생존이 감소했습니다([임상](steps/s2/outputs/clinical.tsv)).")["findings"]
    assert finding["kind"] == "direction" and finding["value"] == "HR 0.58"
    assert _consistency("HR 0.58로 생존이 증가했습니다([임상](steps/s2/outputs/clinical.tsv)).")["findings"] == []


def test_a_linked_table_that_could_not_be_read_is_not_reported_as_clean():
    from labhq.evidence.report_consistency import check, front_lines
    result = check("KLF15 log2FC 4.49 ([표](steps/s3/outputs/big.tsv)).", {"steps/s3/outputs/big.tsv": None})
    assert result["findings"] == [] and result["checked"]["unread_tables"] == ["steps/s3/outputs/big.tsv"]
    lines = front_lines(result, {"execution": "pass"})
    assert lines == ["- 본문·결과표 대조: 연결된 표 1개는 읽지 못해 대조하지 않았습니다(steps/s3/outputs/big.tsv)"]


@pytest.mark.parametrize("sentence", [
    "OR 1.5로 반응률이 증가했습니다([임상](steps/s2/outputs/clinical.tsv)).",  # response odds: event not stated
    "OR 1.5로 반응률이 감소했습니다([임상](steps/s2/outputs/clinical.tsv)).",
    "RR 0.58로 생존이 증가했습니다([임상](steps/s2/outputs/clinical.tsv)).",  # a non-hazard ratio of survival
])
def test_a_ratio_whose_modelled_event_is_not_stated_is_not_checked(sentence):
    assert [row for row in _consistency(sentence)["findings"] if row["kind"] == "direction"] == []


async def test_a_correction_that_swaps_links_between_sentences_is_not_kept(tmp_path):
    from labhq.evidence.report_consistency import correct_once
    step = tmp_path / "s3"
    (step / "outputs").mkdir(parents=True)
    (step / "outputs" / "de.tsv").write_text(DE_TABLE, encoding="utf-8")
    (step / "outputs" / "other.tsv").write_text("x\ty\n1.0\t2.0\n", encoding="utf-8")
    results = {"s3": {"workdir": str(step), "outputs": ["outputs/de.tsv", "outputs/other.tsv"]}}
    wrong = ("FOO1은 증가했습니다([표](steps/s3/outputs/de.tsv)). 다른 값은 2.0입니다([b](steps/s3/outputs/other.tsv)). "
             + "근거 문장입니다. " * 10)
    swapped = ("FOO1은 감소했습니다([b](steps/s3/outputs/other.tsv)). 다른 값은 2.0입니다([표](steps/s3/outputs/de.tsv)). "
               + "근거 문장입니다. " * 10)

    async def run(prompt):
        return swapped

    body, record = await correct_once(wrong, results, run)
    assert body == wrong and record["correction"] == "rejected_structure"


def test_tilde_fenced_examples_are_not_checked():
    assert _consistency("~~~\nKLF15 log2FC 9.9 ([표](steps/s3/outputs/de.tsv)).\n~~~\n")["findings"] == []


def test_review_flags_preserve_original_claims_without_deciding_resolution():
    from copy import deepcopy
    from labhq.evidence.report_check import claim_evidence_rows, claim_evidence_section

    ledgers = {"s1": {"claims": [
        {"id": "direction", "statement": "A is higher than B", "status": "supported"},
        {"id": "count", "statement": "There were 10 lookups", "status": "supported"},
        {"id": "other", "statement": "Unrelated", "status": "supported"},
    ]}}
    before = deepcopy(ledgers)
    issues = [
        {"step_id": "s1", "claim_id": "direction", "problem": "Direction reversed", "request": "Correct direction"},
        {"step_id": "s1", "claim_id": "count", "problem": "Counts conflict", "request": "Reconcile counts"},
        {"step_id": "s1", "claim_id": "", "problem": "General concern"},
    ]
    rows = claim_evidence_rows(ledgers, review_issues=issues)
    by_id = {row["claim_id"]: row for row in rows}
    assert ledgers == before
    for claim_id in ("direction", "count"):
        assert "검토 지적" in by_id[claim_id]["claim_status"]
        assert "해결 여부 미확인" in by_id[claim_id]["claim_status"]
    assert "검토 지적" not in by_id["other"]["claim_status"]
    section = claim_evidence_section(rows)
    assert "Direction reversed" in section and "Counts conflict" in section
    assert "Correct direction" in section and "Reconcile counts" in section
    assert "General concern" in section and "claim 연결 미확인" in section


def test_group_direction_does_not_claim_effect_direction():
    from labhq.evidence.report_consistency import check
    tables = {"effect.tsv": "group\tOR\nup\t0.75\ndown\t1.24\n"}
    for sentence in ("하향 유전자 군의 OR 1.24였습니다 ([표](effect.tsv)).",
                     "상향 유전자 군의 OR 0.75였습니다 ([표](effect.tsv)).",
                     "하향 OR은 1.24입니다 ([표](effect.tsv)).",
                     "상향 유전자에서는 OR 0.75입니다 ([표](effect.tsv))."):
        assert check(sentence, tables)["findings"] == []
    findings = check("하향 유전자 군의 위험은 감소했습니다 (OR 1.24, [표](effect.tsv)).", tables)["findings"]
    assert [item["kind"] for item in findings] == ["direction"]
    findings = _consistency("KLF15은 하향 유전자입니다 ([표](steps/s3/outputs/de.tsv)).")["findings"]
    assert [item["kind"] for item in findings] == ["direction"]
