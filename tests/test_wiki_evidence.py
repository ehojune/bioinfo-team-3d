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


# ---- a wiki summary never fills a claim status (#1037) ----
from labhq.evidence.claims import Claim, EvidenceLink, ledger_errors  # noqa: E402


def _claim(status="supported"):
    return Claim.model_validate({"id": "c1", "revision": 1, "statement": "X raises Y", "scope": "synthetic",
                                 "kind": "finding", "status": status, "importance": "major",
                                 "status_reason": "synthetic"})


def _full_text_row():
    row = wiki_row(id="e2", source_level="primary", directness="direct", independence_group="g2",
                   lineage={"original": True, "basis": "full text reviewed"})
    row["source"].update(access_status="full_text", id_value="10.1234/other", locator="Table 2")
    row["source"].pop("wiki_freshness")
    return Evidence.model_validate(row)


def _link(evidence_id, relation="supports"):
    return EvidenceLink(claim_id="c1", claim_revision=1, evidence_id=evidence_id, relation=relation,
                        rationale="synthetic")


def test_a_claim_supported_only_by_a_wiki_summary_is_refused():
    errors = ledger_errors([_claim()], [Evidence.model_validate(wiki_row())], [_link("e1")])
    assert any("claim c1@1 is supported" in e and "wiki_summary" in e for e in errors), errors


def test_a_wiki_summary_next_to_a_recorded_full_text_reading_is_allowed():
    rows = [Evidence.model_validate(wiki_row()), _full_text_row()]
    assert ledger_errors([_claim()], rows, [_link("e1"), _link("e2")]) == []


def test_a_wiki_summary_can_still_be_linked_as_context():
    assert ledger_errors([_claim("unresolved")], [Evidence.model_validate(wiki_row())], [_link("e1", "context")]) == []


def test_a_contradicting_wiki_summary_is_not_hidden_from_a_supported_claim():
    rows = [Evidence.model_validate(wiki_row()), _full_text_row()]
    errors = ledger_errors([_claim()], rows, [_link("e1", "contradicts"), _link("e2")])
    assert any("contradicting evidence" in e for e in errors), errors


def test_the_result_contract_says_what_a_wiki_hit_is():
    from labhq.evidence.claims import RESULT_CONTRACT_FIELD_RULES
    text = " ".join(RESULT_CONTRACT_FIELD_RULES)
    for needle in ("wiki_summary", "tertiary", "indirect", "never as full_text", "wiki_freshness"):
        assert needle in text


def test_a_wiki_summary_must_state_its_freshness():
    row = wiki_row()
    row["source"].pop("wiki_freshness")
    with pytest.raises(ValidationError, match="wiki_freshness"):
        Evidence.model_validate(row)


# ---- the audit record decides what CP2 accepts (#1037) ----
import json as _json  # noqa: E402

from labhq.evidence.wiki_contract import step_findings  # noqa: E402
from labhq.models import TaskResult  # noqa: E402
from labhq.orchestrator.cso import Orchestrator  # noqa: E402
from tests.test_research_cp2 import CP1, _settings  # noqa: E402
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan  # noqa: E402

LOCATOR = "wiki:notes/example.md#L6-L8@" + "b" * 12


def _audit(freshness="ok", origin=None):
    record = contract_record()
    record["hits"][0]["freshness"] = freshness
    record["hits"][0]["origin"] = origin or {"doi": "10.1234/example"}
    return _json.dumps(record) + chr(10)


def _wiki_ledger(locator=LOCATOR, freshness="ok", relation="context"):
    row = wiki_row()
    row["source"].update(artifact_id="wiki", locator=locator, wiki_freshness=freshness)
    return {"artifact_refs": [{"artifact_id": "wiki", "path": "outputs/wiki_queries.jsonl"}],
            "evidence": [row, {"id": "e3", "kind": "inference", "observation": "derived", "derived_from": ["e1"],
                               "slots": []}],
            "claims": [{"id": "c1", "status": "partially_supported"}],
            "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": relation}]}


def _workdir(tmp_path, audit):
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
    if audit is not None:
        (tmp_path / "outputs" / "wiki_queries.jsonl").write_text(audit, encoding="utf-8")
    return str(tmp_path)


def test_nothing_is_read_when_no_row_cites_the_wiki(tmp_path):
    ledger = {"artifact_refs": [], "claims": [], "links": [], "evidence": [
        {"id": "e1", "kind": "experimental", "source": {"artifact_id": "a1", "locator": "row 1"}}]}
    assert step_findings(ledger, str(tmp_path / "nowhere")) == ([], [])


def test_a_recorded_hit_that_the_row_states_correctly_is_accepted(tmp_path):
    assert step_findings(_wiki_ledger(), _workdir(tmp_path, _audit())) == ([], [])


@pytest.mark.parametrize("why,audit,ledger,expected", [
    ("no audit file", None, {}, "not readable"),
    ("malformed audit", "{not json", {}, "not a valid v1 record"),
    ("locator not recorded", "REC", {"locator": LOCATOR[:-1] + "0"}, "no recorded hit"),
    ("freshness differs", "REC", {"freshness": "stale"}, "freshness does not match"),
    ("origin differs", "ORIGIN", {}, "origin does not match"),
], ids=lambda value: value if isinstance(value, str) and " " in value else "")
def test_a_row_the_audit_does_not_bear_out_is_refused_and_so_is_what_rests_on_it(tmp_path, why, audit, ledger, expected):
    text = {"REC": _audit(), "ORIGIN": _audit(origin={"doi": "10.1234/other"})}.get(audit, audit)
    rows, unsupported = step_findings(_wiki_ledger(**ledger), _workdir(tmp_path, text))
    assert [(row["row_type"], row["row_id"]) for row in rows] == [("evidence", "e1"), ("evidence", "e3")], why
    assert expected in rows[0]["reason"] and rows[0]["reason"].startswith("wiki audit: ")
    assert "derived from refused evidence e1" in rows[1]["reason"]


def test_a_claim_resting_only_on_a_refused_wiki_row_is_listed_as_unsupported(tmp_path):
    ledger = _wiki_ledger(relation="supports")
    rows, unsupported = step_findings(ledger, _workdir(tmp_path, None))
    assert [row["claim_id"] for row in unsupported] == ["c1"] and "refused evidence e1" in unsupported[0]["reason"]


def _wiki_result(hub, task, *, locator=LOCATOR, freshness="ok"):
    result = _cp2_result(hub, task)
    result["evidence"].append(wiki_row(id="e_wiki", independence_group="g_wiki"))
    result["evidence"][-1]["source"].update(artifact_id="wiki", locator=locator, wiki_freshness=freshness)
    result["links"].append({"claim_id": "c1", "claim_revision": 1, "evidence_id": "e_wiki", "relation": "context",
                            "rationale": "the wiki note points at the original paper"})
    result["artifact_refs"].append({"artifact_id": "wiki", "path": "outputs/wiki_queries.jsonl"})
    return result


async def _cp2_card(workdir, **wiki):
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=_wiki_result(hub, task, **wiki),
                          outputs=[path], workdir=workdir,  # the audit is not declared: labhq collects it itself
                          unreported_outputs=["outputs/wiki_queries.jsonl"])

    hub = holder["hub"] = MiniHub(_settings(), reply, mode="orchestrate", work_kind="research", text="compare")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    return hub.approvals[1]["detail"]


@pytest.mark.asyncio
async def test_a_wiki_row_the_audit_bears_out_reaches_cp2_unrefused(tmp_path):
    card = await _cp2_card(_workdir(tmp_path, _audit()))
    assert not [row for row in card.get("refused_rows", []) if "wiki audit" in row["reason"]]
    assert not [row for row in card.get("refused_evidence", []) if row["evidence_id"] == "e_wiki"]


@pytest.mark.asyncio
@pytest.mark.parametrize("audit,wiki", [(None, {}), ("{broken", {}), ("REC", {"freshness": "stale"}),
                                       ("REC", {"locator": LOCATOR[:-1] + "0"})], ids=["no-file", "malformed", "forged-freshness", "unrecorded-locator"])
async def test_a_wiki_row_the_audit_cannot_bear_out_is_refused_at_cp2(tmp_path, audit, wiki):
    card = await _cp2_card(_workdir(tmp_path, _audit() if audit == "REC" else audit), **wiki)
    assert any(row["row_id"] == "e_wiki" and row["reason"].startswith("wiki audit: ") for row in card["refused_rows"])
    assert any(row["evidence_id"] == "e_wiki" for row in card["refused_evidence"])


def test_the_wiki_tools_own_audit_file_is_collected_with_its_hash(tmp_path):
    from labhq.evidence.wiki_contract import AUDIT, collect_audit
    result = TaskResult(task_id="t", agent_id="a", ok=True, outputs=["outputs/x.tsv"], output_sha256={"outputs/x.tsv": "1"},
                        unreported_outputs=[AUDIT, "outputs/other.md"], workdir=_workdir(tmp_path, _audit()))
    collected = collect_audit(result)
    assert collected.outputs == ["outputs/x.tsv", AUDIT] and collected.unreported_outputs == ["outputs/other.md"]
    import hashlib
    assert collected.output_sha256[AUDIT] == hashlib.sha256((tmp_path / AUDIT).read_bytes()).hexdigest()
    assert collect_audit(collected) is collected  # already collected
    not_listed = result.model_copy(update={"unreported_outputs": []})
    assert collect_audit(not_listed) is not_listed  # the runner did not see it written
    shared = tmp_path / "shared.jsonl"
    shared.write_text(_audit(), encoding="utf-8")
    (tmp_path / AUDIT).unlink()
    try:
        (tmp_path / AUDIT).symlink_to(shared)
    except OSError:  # no symlink privilege on this machine
        pass
    else:
        assert collect_audit(result) is result  # a symlink is not the tool's file
        (tmp_path / AUDIT).unlink()
    import os
    os.link(shared, tmp_path / AUDIT)
    assert collect_audit(result) is result  # a hard link shares its inode with another path
    missing = TaskResult(task_id="t", agent_id="a", ok=True, workdir=str(tmp_path / "nowhere"))
    assert collect_audit(missing) is missing and collect_audit(TaskResult(task_id="t", agent_id="a", ok=True)).outputs == []


def _origin_ledger(freshness="ok", **source):
    ledger = _wiki_ledger(freshness=freshness)
    ledger["evidence"][0]["source"].pop("id_scheme")
    ledger["evidence"][0]["source"].pop("id_value")
    ledger["evidence"][0]["source"].update(source)
    return ledger


@pytest.mark.parametrize("source,accepted", [
    ({"id_scheme": "doi", "id_value": "10.1234/EXAMPLE"}, True),
    ({"uri": "https://doi.org/10.1234/example"}, True),
    ({"uri": "https://dx.doi.org/10.1234/EXAMPLE"}, True),
    ({"uri": "https://doi.org/10.1234/other"}, False),
    ({"id_scheme": "doi", "id_value": "10.1234/example", "uri": "https://doi.org/10.1234/example/"}, True),
    ({}, False),  # names only the audit artifact: the recorded origin is missing
    ({"uri": "https://example.org/unrelated"}, False),
    ({"id_scheme": "doi", "id_value": "10.1234/example", "uri": "https://example.org/unrelated"}, False),
    ({"id_scheme": "pmid", "id_value": "123"}, False),
], ids=["doi-id", "doi-uri", "doi-dx-uri", "other-doi-uri", "doi-both", "no-origin", "unrelated-uri", "good-id-bad-uri", "other-scheme"])
def test_a_row_must_name_the_origin_the_hit_recorded_and_nothing_else(tmp_path, source, accepted):
    rows, _ = step_findings(_origin_ledger(**source), _workdir(tmp_path, _audit()))
    assert (rows == []) is accepted, rows


def test_a_hit_without_an_origin_cannot_be_given_one(tmp_path):
    record = _json.loads(_audit(freshness="source_missing"))
    record["hits"][0]["origin"] = None
    text = _json.dumps(record) + chr(10)
    assert step_findings(_origin_ledger(freshness="source_missing"), _workdir(tmp_path, text))[0] == []
    rows, _ = step_findings(_wiki_ledger(freshness="source_missing"), _workdir(tmp_path, text))  # still names a DOI
    assert rows and "origin does not match" in rows[0]["reason"]
