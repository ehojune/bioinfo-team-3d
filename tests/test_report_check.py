"""The claim-anchor check of a research report (#58 ③), without the orchestrator."""

import pytest

from labhq.evidence.report_check import check_report, claim_rows, failed_lookups


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
