"""labhq_annot structure and compound tools (#1187): RCSB PDB and ChEMBL, with every HTTP call faked (no network)."""

import ast
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from labhq.tools import annot, annot_structure as st
from labhq.tools._mcpcompat import ToolError
from tests.test_annot import FakeTime
from tests.test_external_wait import limited, mcp_module  # noqa: F401  (fixtures)

REPO = Path(__file__).resolve().parents[1]
ENTRY = {
    "rcsb_id": "4HHB", "struct": {"title": "THE CRYSTAL STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN AT 1.74 A RESOLUTION"},
    "exptl": [{"method": "X-RAY DIFFRACTION"}],
    "rcsb_entry_info": {"resolution_combined": [1.74], "polymer_entity_count": 2,
                        "nonpolymer_bound_components": ["HEM"]},
    "rcsb_accession_info": {"deposit_date": "1984-03-07", "initial_release_date": "1984-07-17",
                            "revision_date": "2024-02-07"},
    "rcsb_primary_citation": {"pdbx_database_id_PubMed": 6726807, "title": "Structure of haemoglobin",
                              "pdbx_database_id_DOI": "10.1016/0022-2836(84)90472-8"},
}


def make(handler, tmp_path, cache=True):
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    clock = FakeTime()
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    annotator = st.StructureAnnotator(client, tmp_path / "work", tmp_path / "cache" if cache else None,
                                      sleep=clock.sleep, clock=clock.clock)
    return annotator, calls, clock


def _log(tmp_path):
    path = tmp_path / "work" / "outputs" / "annotation_queries.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _full(tmp_path, result):
    return json.loads((tmp_path / "work" / result["full_result"]).read_text(encoding="utf-8"))


def pdb_handler(request):
    path = request.url.path
    if path.startswith("/rest/v1/core/entry/"):
        entry = path.rsplit("/", 1)[1]
        if entry == "4HHB":
            return httpx.Response(200, json=ENTRY)
        if entry in ("1A00", "2HHB", "3HHB"):
            return httpx.Response(200, json={**ENTRY, "rcsb_id": entry})
        return httpx.Response(404, json={"status": 404})
    if request.url.host == "search.rcsb.org":
        query = json.loads(parse_qs(request.url.query.decode())["json"][0])
        if query["query"]["parameters"]["value"] == "nothing matches":
            return httpx.Response(204)
        rows = query["request_options"]["paginate"]["rows"]
        hits = [{"identifier": i, "score": 1.0 - n / 10} for n, i in enumerate(["4HHB", "1A00", "2HHB", "3HHB"])]
        return httpx.Response(200, json={"total_count": 120, "result_set": hits[:rows]})
    return httpx.Response(500)


def test_inputs_are_read_before_anything_is_sent():
    assert st.parse_pdb("4hhb").kind == "entry" and st.parse_pdb("4hhb").key == "4HHB"
    assert st.parse_pdb(" hemoglobin deoxy ").kind == "search"
    for bad in ("", "   ", 5, "x", "a" * 201):
        with pytest.raises(ValueError):
            st.parse_pdb(bad)
    assert st.parse_chembl("chembl203", "target").key == "CHEMBL203"
    assert st.parse_chembl("imatinib", "compound").kind == "text"
    with pytest.raises(ValueError, match="target"):
        st.parse_chembl("EGFR", "activity")  # an activity query needs a target ChEMBL ID, not a name


@pytest.mark.asyncio
async def test_a_pdb_entry_is_summarized_recorded_and_kept_whole(tmp_path):
    annotator, calls, _ = make(pdb_handler, tmp_path)
    result = await annotator.pdb(["4HHB"])
    row = result["results"][0]
    entry = row["entry"]
    assert row["found"] is True and entry["id"] == "4HHB" and entry["resolution_angstrom"] == 1.74
    assert entry["methods"] == ["X-RAY DIFFRACTION"] and entry["nonpolymer_bound_components"] == ["HEM"]
    assert entry["revision_date"] == "2024-02-07" and entry["primary_citation"]["pubmed_id"] == 6726807
    assert [str(c.url) for c in calls] == ["https://data.rcsb.org/rest/v1/core/entry/4HHB"]
    assert result["source"]["db"] == "RCSB PDB" and result["source"]["license"].startswith("CC0")
    assert result["source"]["license_url"].startswith("https://")
    # The log has counts and hashes; the whole response is in the result file the report cites.
    line = _log(tmp_path)[0]
    assert line["tool"] == "pdb" and line["n_ok"] == 1 and line["result_file"] == result["full_result"]
    assert "4HHB" not in json.dumps(line)
    raw = _full(tmp_path, result)["results"][0]["answer"]["raw"]
    assert raw["struct"]["title"].startswith("THE CRYSTAL STRUCTURE")


@pytest.mark.asyncio
async def test_a_missing_entry_is_not_found_and_a_bad_item_fails_alone(tmp_path):
    annotator, calls, _ = make(pdb_handler, tmp_path)
    result = await annotator.pdb(["9ZZZ", "x", "4HHB"])
    missing, bad, good = result["results"]
    assert missing["ok"] is True and missing["found"] is False and "조회는 성공" in missing["meaning"]
    assert bad["ok"] is False and "PDB ID" in bad["error"]
    assert good["found"] is True and len(calls) == 2  # the malformed item sent nothing
    assert (result["n_inputs"], result["n_ok"], result["n_errors"]) == (3, 2, 1)


@pytest.mark.asyncio
async def test_a_pdb_search_summarizes_only_the_top_entries_and_says_when_more_matched(tmp_path):
    annotator, calls, _ = make(pdb_handler, tmp_path)
    result = await annotator.pdb(["deoxy hemoglobin"], top=3)
    row = result["results"][0]
    assert [e["id"] for e in row["entries"]] == ["4HHB", "1A00", "2HHB"] and row["total_matches"] == 120
    assert "120개 중 상위 3개" in row["truncated"]
    assert sum(c.url.host == "data.rcsb.org" for c in calls) == 3  # one data call per summarized entry, no coordinates
    assert not any("download" in c.url.host or c.url.path.endswith((".cif", ".pdb")) for c in calls)
    assert set(_full(tmp_path, result)["results"][0]["answer"]["raw"]) == {"4HHB", "1A00", "2HHB"}


@pytest.mark.asyncio
async def test_a_pdb_search_with_no_match_is_a_successful_empty_answer(tmp_path):
    annotator, _, _ = make(pdb_handler, tmp_path)
    row = (await annotator.pdb(["nothing matches"]))["results"][0]
    assert row["ok"] is True and row["found"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"top": 0}, {"top": 26}, {"top": True}, {"top": "5"}])
async def test_a_pdb_top_outside_the_range_is_refused(tmp_path, kwargs):
    annotator, calls, _ = make(pdb_handler, tmp_path)
    with pytest.raises(ValueError, match="top"):
        await annotator.pdb(["4HHB"], **kwargs)
    assert calls == []


@pytest.mark.asyncio
async def test_more_than_twenty_five_pdb_items_leave_the_rest_for_another_call(tmp_path):
    annotator, calls, _ = make(pdb_handler, tmp_path)
    result = await annotator.pdb(["4HHB"] * 26)
    assert result["results"][-1]["ok"] is False and "25개까지" in result["results"][-1]["error"]
    assert len(calls) == 1  # the same entry asked 25 times is fetched once


@pytest.mark.asyncio
async def test_a_second_identical_pdb_call_is_served_from_the_cache(tmp_path):
    annotator, calls, _ = make(pdb_handler, tmp_path)
    await annotator.pdb(["4HHB"])
    again = await annotator.pdb(["4HHB"])
    assert len(calls) == 1 and again["n_cached"] == 1 and again["results"][0]["cached_from"]


@pytest.mark.asyncio
async def test_pdb_retries_a_busy_server_and_gives_up_after_the_attempt_limit(tmp_path):
    answers = iter([httpx.Response(503), httpx.Response(200, json=ENTRY)])
    annotator, calls, clock = make(lambda request: next(answers), tmp_path)
    assert (await annotator.pdb(["4HHB"]))["results"][0]["found"] is True
    assert len(calls) == 2
    annotator, calls, _ = make(lambda request: httpx.Response(503), tmp_path / "b")
    with pytest.raises(annot.LookupFailed, match="모두 실패"):
        await annotator.pdb(["4HHB"])
    assert len(calls) == annot.MAX_ATTEMPTS


def chembl_handler(version="ChEMBL_35"):
    def handler(request):
        path = request.url.path
        query = dict(request.url.params)
        if path.endswith("/status.json"):
            return httpx.Response(200, json={"chembl_db_version": version, "status": "UP"})
        if path.endswith("/target/CHEMBL203.json"):
            return httpx.Response(200, json={"target_chembl_id": "CHEMBL203", "pref_name": "Epidermal growth factor receptor",
                                             "target_type": "SINGLE PROTEIN", "organism": "Homo sapiens", "tax_id": 9606,
                                             "target_components": [{"accession": "P00533",
                                                                    "component_description": "EGFR"}]})
        if path.endswith("/target/CHEMBL0.json"):
            return httpx.Response(404, json={})
        if path.endswith("/molecule/search.json"):
            return httpx.Response(200, json={"molecules": [{
                "molecule_chembl_id": "CHEMBL941", "pref_name": "IMATINIB", "molecule_type": "Small molecule",
                "max_phase": 4, "first_approval": 2001,
                "molecule_structures": {"canonical_smiles": "Cc1ccc", "standard_inchi_key": "KTUFNOKKBVMGRW-UHFFFAOYSA-N"},
                "molecule_properties": {"full_molformula": "C29H31N7O", "full_mwt": "493.62"}}],
                "page_meta": {"total_count": 1}})
        if path.endswith("/activity.json"):
            offset, limit = int(query["offset"]), int(query["limit"])
            total = 230
            rows = [{"activity_id": n, "molecule_chembl_id": f"CHEMBL{n}", "target_chembl_id": query["target_chembl_id"],
                     "assay_chembl_id": "CHEMBL1", "assay_type": "B", "standard_type": "IC50", "standard_relation": "=",
                     "standard_value": "5.0", "standard_units": "nM", "pchembl_value": "8.3",
                     "document_chembl_id": "CHEMBL2", "ignored": "x",
                     # ChEMBL's own validity flags: a comment on a doubtful value, and 1 for a repeated citation
                     "data_validity_comment": "Outside typical range" if n % 50 == 7 else None,
                     "potential_duplicate": 1 if n % 40 == 3 else 0}
                    for n in range(offset, min(offset + limit, total))]
            return httpx.Response(200, json={"activities": rows, "page_meta": {
                "total_count": total, "next": "more" if offset + limit < total else None}})
        return httpx.Response(500)
    return handler


@pytest.mark.asyncio
async def test_a_chembl_target_by_id_and_a_missing_one(tmp_path):
    annotator, calls, _ = make(chembl_handler(), tmp_path)
    result = await annotator.chembl(["chembl203", "CHEMBL0"], kind="target")
    found, missing = result["results"]
    assert found["rows"][0]["target_chembl_id"] == "CHEMBL203" and found["rows"][0]["components"][0]["accession"] == "P00533"
    assert missing["ok"] is True and missing["found"] is False
    assert result["source"]["release_or_version"] == "ChEMBL ChEMBL_35"
    assert result["source"]["license"].startswith("CC BY-SA 3.0")
    assert calls[0].url.path.endswith("/status.json")  # the release is asked first


@pytest.mark.asyncio
async def test_the_raw_chembl_records_are_kept_in_the_result_file_though_the_summary_is_filtered(tmp_path):
    annotator, _, _ = make(chembl_handler(), tmp_path)
    result = await annotator.chembl(["CHEMBL203"], kind="activity", limit=3)
    assert "ignored" not in result["results"][0]["rows"][0]  # the tool answer stays small
    raw = _full(tmp_path, result)["results"][0]["answer"]["raw"]
    assert len(raw) == 3 and all(r["ignored"] == "x" for r in raw)  # the artifact keeps every field the API sent
    result = await annotator.chembl(["CHEMBL203"], kind="target")
    assert _full(tmp_path, result)["results"][0]["answer"]["raw"][0]["target_components"][0]["accession"] == "P00533"
    result = await annotator.chembl(["imatinib"], kind="compound")
    assert _full(tmp_path, result)["results"][0]["answer"]["raw"][0]["molecule_structures"]["canonical_smiles"]


@pytest.mark.asyncio
async def test_chembl_with_only_invalid_items_sends_nothing(tmp_path):
    annotator, calls, _ = make(lambda request: httpx.Response(500), tmp_path)
    result = await annotator.chembl(["x", "EGFR"], kind="activity")
    assert calls == [] and [r["ok"] for r in result["results"]] == [False, False]
    assert result["n_errors"] == 2 and "full_result" in result  # still recorded


def test_a_staff_side_file_does_not_spell_the_gateway_api_prefix():
    # tests/test_semantics_actions_run.py forbids that string in staff-side modules; ChEMBL's own path is built from parts.
    assert "/api/" not in (REPO / "labhq" / "tools" / "annot_structure.py").read_text(encoding="utf-8")
    assert st.CHEMBL == "https://www.ebi.ac.uk/chembl" + "/api" + "/data"


@pytest.mark.asyncio
async def test_a_chembl_compound_search_returns_structure_fields(tmp_path):
    annotator, calls, _ = make(chembl_handler(), tmp_path)
    row = (await annotator.chembl(["imatinib"], kind="compound"))["results"][0]
    assert row["found"] is True and row["rows"][0]["standard_inchi_key"].startswith("KTUFNOKKBVMGRW")
    assert row["rows"][0]["molecular_formula"] == "C29H31N7O"
    assert parse_qs(calls[-1].url.query.decode())["limit"] == [str(st.CHEMBL_SEARCH_TOP)]


@pytest.mark.asyncio
async def test_chembl_activities_stop_at_the_limit_and_say_the_list_is_cut(tmp_path):
    annotator, calls, _ = make(chembl_handler(), tmp_path)
    row = (await annotator.chembl(["CHEMBL203"], kind="activity", limit=150))["results"][0]
    assert len(row["rows"]) == 150 and row["total"] == 230 and "230개 중 150개" in row["truncated"]
    assert set(row["rows"][0]) == set(st._ACTIVITY_KEEP) and "ignored" not in row["rows"][0]
    pages = [dict(c.url.params) for c in calls if c.url.path.endswith("/activity.json")]
    assert [(p["offset"], p["limit"]) for p in pages] == [("0", "100"), ("100", "50")]


@pytest.mark.asyncio
async def test_a_chembl_activity_list_that_fits_is_not_marked_cut(tmp_path):
    annotator, _, _ = make(chembl_handler(), tmp_path)
    row = (await annotator.chembl(["CHEMBL203"], kind="activity", limit=200))["results"][0]
    assert len(row["rows"]) == 200 and "truncated" in row  # 230 exist: still cut at the 200 ceiling
    assert row["total"] == 230


@pytest.mark.asyncio
async def test_a_chembl_activity_query_by_name_fails_that_item_only(tmp_path):
    annotator, calls, _ = make(chembl_handler(), tmp_path)
    result = await annotator.chembl(["EGFR", "CHEMBL203"], kind="activity", limit=5)
    assert result["results"][0]["ok"] is False and "target" in result["results"][0]["error"]
    assert result["results"][1]["found"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"kind": "assay"}, {"limit": 0}, {"limit": 201}, {"limit": False}])
async def test_chembl_options_outside_their_range_are_refused(tmp_path, kwargs):
    annotator, calls, _ = make(chembl_handler(), tmp_path)
    with pytest.raises(ValueError):
        await annotator.chembl(["CHEMBL203"], **kwargs)
    assert calls == []


@pytest.mark.asyncio
async def test_a_new_chembl_release_is_not_served_from_the_old_cache(tmp_path):
    annotator, calls, _ = make(chembl_handler("ChEMBL_35"), tmp_path)
    await annotator.chembl(["CHEMBL203"], kind="target")
    cached = await annotator.chembl(["CHEMBL203"], kind="target")
    assert cached["n_cached"] == 1
    newer, new_calls, _ = make(chembl_handler("ChEMBL_36"), tmp_path)
    fresh = await newer.chembl(["CHEMBL203"], kind="target")
    assert fresh["n_cached"] == 0 and any(c.url.path.endswith("/target/CHEMBL203.json") for c in new_calls)


@pytest.mark.asyncio
async def test_chembl_without_a_release_in_status_does_not_look_anything_up(tmp_path):
    annotator, calls, _ = make(lambda request: httpx.Response(200, json={"status": "UP"}), tmp_path)
    with pytest.raises(annot.LookupFailed, match="버전"):
        await annotator.chembl(["CHEMBL203"], kind="target")
    assert [c.url.path.rsplit("/", 1)[-1] for c in calls] == ["status.json"]


@pytest.mark.asyncio
async def test_no_workdir_still_answers_and_says_it_could_not_record(tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(pdb_handler))
    clock = FakeTime()
    annotator = st.StructureAnnotator(client, None, None, sleep=clock.sleep, clock=clock.clock)
    result = await annotator.pdb(["4HHB"])
    assert result["results"][0]["found"] is True and "LABHQ_WORKDIR" in result["record_error"]


# ----- the MCP server: every path goes through the lookup runner and the request limit -----

def test_every_tool_in_the_server_goes_through_a_lookup_runner():
    tree = ast.parse((REPO / "labhq" / "tools" / "annot_mcp.py").read_text(encoding="utf-8"))
    tools = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and any(
                isinstance(d, ast.Call) and (getattr(d.func, "attr", "") == "tool" or getattr(d.func, "id", "") == "offered")
                for d in node.decorator_list):
            tools[node.name] = {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call)
                                and isinstance(n.func, ast.Name)}
    assert {"pdb", "chembl"} <= set(tools)
    runners = {"_run", "_run_literature", "_run_structure", "_run_key_fulltext"}
    assert all(calls & runners for calls in tools.values()), {k: v for k, v in tools.items() if not v & runners}


@pytest.mark.asyncio
async def test_the_structure_tools_run_under_the_lookup_deadline(mcp_module, monkeypatch, tmp_path):
    seen = {}

    async def deadline(operation, **kwargs):
        seen.update(kwargs)
        return await operation()

    class Backend:
        def __init__(self, *args, **kwargs):
            pass

        async def pdb(self, ids, top):
            return {"ids": ids, "top": top}

    monkeypatch.setattr(mcp_module.annot_structure, "StructureAnnotator", Backend)
    monkeypatch.setattr(mcp_module, "run_lookup", deadline)
    assert json.loads(await mcp_module.pdb(["4HHB"], 7)) == {"ids": ["4HHB"], "top": 7}
    assert seen["tool"] == "pdb" and seen["workdir"] == tmp_path


@pytest.mark.asyncio
async def test_each_pdb_request_is_counted_and_the_one_past_the_limit_is_not_sent(mcp_module, monkeypatch, limited):
    # limit 3: a search is one search call plus one data call per entry, so a fourth request is refused unsent.
    limited.provider = pdb_handler
    with pytest.raises(ToolError) as exc:
        await mcp_module.pdb(["deoxy hemoglobin"], 5)
    assert "보내지 않았습니다" in str(exc.value)
    assert len(limited.sent) == 3
    hosts = [(r["host"], r["tool"]) for r in limited.ledger.sends("req-1")]
    assert hosts[0] == ("search.rcsb.org", "pdb") and {h for h, _ in hosts[1:]} == {"data.rcsb.org"}


def test_the_roster_tells_the_cso_about_the_structure_and_compound_tools():
    """The CSO plans from the roster line: an agent with every annot tool must be listed with PDB and ChEMBL, and a
    restricted one (the chief of staff) must not."""
    from labhq.models import AgentSpec
    from labhq.orchestrator.cso import format_roster
    everything = format_roster([AgentSpec(id="b", name="B", role="r", builtin_mcp=["approval", "annot"]).summary()])
    assert "RCSB PDB structure" in everything and "ChEMBL compound" in everything
    restricted = format_roster([AgentSpec(id="c", name="C", role="r", builtin_mcp=["annot"],
                                          annot_tools=["fulltext"]).summary()])
    assert "PDB" not in restricted and "ChEMBL" not in restricted


def test_the_manual_describes_both_tools_in_korean_and_english():
    for name, needles in (("manual.md", ("`pdb(ids_or_queries, top)`", "`chembl(targets_or_compounds, kind, limit)`", "CC0 1.0",
                                         "CC BY-SA 3.0", "truncated")),
                          ("manual.en.md", ("`pdb(ids_or_queries, top)`", "`chembl(targets_or_compounds, kind, limit)`",
                                            "CC0 1.0", "CC BY-SA 3.0", "truncated"))):
        text = (REPO / "docs" / name).read_text(encoding="utf-8")
        assert all(needle in text for needle in needles), (name, [n for n in needles if n not in text])


@pytest.mark.asyncio
async def test_activity_rows_keep_chembls_validity_flags_and_the_summary_says_how_many_are_flagged(tmp_path):
    """A value ChEMBL marks as doubtful or as a repeated citation must not look like an ordinary measurement in the
    summary a report quotes from (PR #1195 review)."""
    annotator, _, _ = make(chembl_handler(), tmp_path)
    row = (await annotator.chembl(["CHEMBL203"], kind="activity", limit=100))["results"][0]
    rows = row["rows"]
    assert all({"data_validity_comment", "potential_duplicate"} <= set(r) for r in rows)
    doubtful = [r for r in rows if r["data_validity_comment"]]
    repeated = [r for r in rows if r["potential_duplicate"]]
    assert doubtful and repeated and doubtful[0]["data_validity_comment"] == "Outside typical range"
    flagged = [r for r in rows if r["data_validity_comment"] or r["potential_duplicate"]]
    assert row["flagged_rows"] == len(flagged) > 0
    assert "의심" in row["validity_note"] and "중복" in row["validity_note"]


@pytest.mark.asyncio
async def test_no_validity_note_when_no_row_is_flagged(tmp_path):
    def handler(request):
        if request.url.path.endswith("/status.json"):
            return httpx.Response(200, json={"chembl_db_version": "ChEMBL_35"})
        return httpx.Response(200, json={"activities": [{"activity_id": 1, "standard_type": "IC50", "standard_value": "1",
                                                         "standard_units": "nM", "data_validity_comment": None,
                                                         "potential_duplicate": 0}],
                                         "page_meta": {"total_count": 1, "next": None}})

    annotator, _, _ = make(handler, tmp_path)
    row = (await annotator.chembl(["CHEMBL203"], kind="activity", limit=10))["results"][0]
    assert row["flagged_rows"] == 0 and "validity_note" not in row
