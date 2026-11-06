import pytest
from pydantic import ValidationError

from labhq.evidence.claims import Evidence
from labhq.evidence.report_check import claim_evidence_rows


def wiki_row(**changes):
    row = {"id": "e1", "kind": "literature_claim", "observation": "A summary",
           "status": "observed", "source": {"id_scheme": "doi", "id_value": "10.1234/example",
           "accessed_at": "2026-10-30", "locator": "note L1", "access_status": "wiki_summary",
           "wiki_freshness": "stale"}, "directness": "indirect", "source_level": "tertiary",
           "independence_group": "g1", "assessment_reason": "summary"}
    row.update(changes)
    return row


@pytest.mark.parametrize("change", [
    {"source_level": "primary"}, {"source_level": "secondary"}, {"directness": "direct"}, {"kind": "observation"},
    {"lineage": {"original": True, "basis": "DOI exists"}},
])
def test_wiki_cannot_claim_primary_or_direct_reading(change):
    with pytest.raises(ValidationError, match="wiki_summary"):
        Evidence.model_validate(wiki_row(**change))


@pytest.mark.parametrize("freshness", ["ok", "stale", "source_missing", "disputed"])
def test_wiki_freshness_survives_report(freshness):
    row = wiki_row()
    row["source"]["wiki_freshness"] = freshness
    evidence = Evidence.model_validate(row)
    displayed = claim_evidence_rows({"s1": {"evidence": [evidence.model_dump()]}})
    assert displayed[0]["access"] == f"wiki 요약 ({freshness})"


def test_separate_primary_full_text_remains_valid():
    row = wiki_row(source_level="primary", directness="direct",
                   lineage={"original": True, "basis": "full text reviewed"})
    row["source"]["access_status"] = "full_text"
    row["source"].pop("wiki_freshness")
    assert Evidence.model_validate(row).lineage.original


def contract_record():
    return {"v": 1, "ts": "2026-10-30T05:40:00Z", "tool": "search", "query_sha256": "a" * 64,
            "hits": [{"rel_path": "notes/example.md", "title": "Example", "heading": "A > B",
                      "line_start": 6, "line_end": 8, "sha256": "b" * 64,
                      "mtime": "2026-10-30T01:02:03Z", "origin": {"doi": "10.1234/example"},
                      "access_level": "wiki_summary", "source_level": "tertiary",
                      "verified_against_primary": False, "freshness": "ok",
                      "snippet_sha256": "c" * 64, "snippet_chars": 96}]}


def test_contract_locator_and_origin():
    import json
    from labhq.evidence.wiki_contract import read_wiki_jsonl, resolve_locator, wiki_support
    calls = read_wiki_jsonl(json.dumps(contract_record()))
    locator = "wiki:notes/example.md#L6-L8@" + "b" * 12
    hit = resolve_locator(calls, locator)
    assert wiki_support(hit, []) == (False, ["wiki summary only"])
    assert wiki_support(hit, [{"doi": "10.1234/example"}]) == (True, [])
    assert wiki_support(hit, [{"doi": "10.1234/other"}])[0] is False
    for invalid in (locator.replace("L6", "L7"), locator.replace("b" * 12, "d" * 12), "wiki:missing"):
        with pytest.raises(ValueError):
            resolve_locator(calls, invalid)


@pytest.mark.parametrize("field,value", [("v", True), ("v", 2), ("ts", "bad"), ("query_sha256", None)])
def test_contract_rejects_bad_call(field, value):
    import json
    from labhq.evidence.wiki_contract import read_wiki_jsonl
    record = contract_record()
    record[field] = value
    with pytest.raises(ValueError):
        read_wiki_jsonl(json.dumps(record))


@pytest.mark.parametrize("field,value", [("rel_path", "../secret"), ("line_start", True),
                                         ("sha256", "bad"), ("source_level", "primary"),
                                         ("verified_against_primary", True), ("origin", {"url": "http://x"}),
                                         ("snippet_chars", 601)])
def test_contract_rejects_bad_hit(field, value):
    import json
    from labhq.evidence.wiki_contract import read_wiki_jsonl
    record = contract_record()
    record["hits"][0][field] = value
    with pytest.raises(ValueError):
        read_wiki_jsonl(json.dumps(record))


def test_read_accepts_producer_limit():
    import json
    from labhq.evidence.wiki_contract import read_wiki_jsonl
    record = contract_record()
    record.update(tool="read", query_sha256=None)
    record["hits"][0]["snippet_chars"] = 20000
    assert read_wiki_jsonl(json.dumps(record))[0].hits[0].snippet_chars == 20000


@pytest.mark.parametrize("origin,freshness,limitation", [(None, "source_missing", "excluded from support"),
                                                         ({"doi": "10.1234/example"}, "disputed", "excluded from support"),
                                                         ({"doi": "10.1234/example"}, "stale", "stale")])
def test_contract_freshness(origin, freshness, limitation):
    import json
    from labhq.evidence.wiki_contract import read_wiki_jsonl, wiki_support
    record = contract_record()
    record["hits"][0].update(origin=origin, freshness=freshness)
    hit = read_wiki_jsonl(json.dumps(record))[0].hits[0]
    assert limitation in wiki_support(hit, [origin])[1]


def test_ledger_requires_matching_hit_and_fulltext():
    import json
    from labhq.evidence.wiki_contract import check_wiki_ledger, read_wiki_jsonl
    record = contract_record()
    calls = read_wiki_jsonl(json.dumps(record))
    row = wiki_row()
    row["source"].update(artifact_id="wiki", locator="wiki:notes/example.md#L6-L8@" + "b" * 12,
                         wiki_freshness="ok")
    ledger = {"artifact_refs": [{"artifact_id": "wiki", "path": "outputs/wiki_queries.jsonl"}],
              "evidence": [row], "claims": [{"id": "c1", "status": "supported"}],
              "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}]}
    assert "wiki-only" in " ".join(check_wiki_ledger(ledger, calls))
    ledger["claims"][0]["status"] = "partially_supported"
    assert check_wiki_ledger(ledger, calls) == []
    row["source"]["id_value"] = "10.1234/forged"
    assert "origin does not match" in " ".join(check_wiki_ledger(ledger, calls))
    row["source"]["id_value"] = "10.1234/example"
    row["source"]["locator"] += "0"
    assert "no recorded hit" in " ".join(check_wiki_ledger(ledger, calls))


def test_public_metadata_is_allowlisted():
    import json
    from labhq.evidence.wiki_contract import public_wiki_metadata, read_wiki_jsonl
    hit = read_wiki_jsonl(json.dumps(contract_record()))[0].hits[0]
    url = "https://doi.org/10.1234/example"
    assert public_wiki_metadata(hit, approved_urls={url}) == {"origin_url": url, "freshness": "ok"}
    assert public_wiki_metadata(hit, approved_urls=set())["origin_url"] is None
    assert not ({"title", "rel_path", "heading", "sha256", "snippet_sha256"} &
                public_wiki_metadata(hit, approved_urls={url}).keys())


def test_duplicate_fields_are_rejected():
    from labhq.evidence.wiki_contract import read_wiki_jsonl
    with pytest.raises(ValueError, match="duplicate"):
        read_wiki_jsonl('{"v":1,"v":2}')
