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
