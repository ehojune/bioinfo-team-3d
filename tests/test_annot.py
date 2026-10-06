"""labhq_annot (#435 C ②): Ensembl VEP, gnomAD and ClinVar lookups with every HTTP call faked."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.settings import Settings
from labhq.tools import annot
from labhq.tools._mcpcompat import list_tools

REPO = Path(__file__).resolve().parents[1]


class FakeTime:
    """Sleep advances a fake clock, so pacing and Retry-After waits are visible without waiting."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def make(handler, tmp_path, cache=True):
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    clock = FakeTime()
    (tmp_path / "work").mkdir(exist_ok=True)  # the runner's task workspace always exists
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    annotator = annot.Annotator(client, tmp_path / "work", tmp_path / "cache" if cache else None,
                                sleep=clock.sleep, clock=clock.clock)
    return annotator, calls, clock


def vep_handler(release=115, refuse=(), throttle=None):
    """Echo VEP: one answer per entry. `refuse` entries make the whole POST a 400, like Ensembl does."""
    state = {"throttled": 0}

    def handler(request):
        if request.url.path == "/info/data":
            return httpx.Response(200, json={"releases": [release]})
        body = json.loads(request.content)
        (key, entries), = body.items()
        if throttle and state["throttled"] < throttle[0]:
            state["throttled"] += 1
            return httpx.Response(429, headers={"Retry-After": str(throttle[1])}, json={"error": "slow down"})
        if any(entry in refuse for entry in entries):
            return httpx.Response(400, json={"error": "Unable to parse HGVS notation"})
        out = []
        for entry in entries:
            ident = entry.split()[2] if key == "variants" else entry
            out.append({"id": ident, "input": entry, "seq_region_name": "17", "start": 43045712,
                        "allele_string": "T/C", "most_severe_consequence": "missense_variant",
                        "colocated_variants": [{"id": "rs80357906"}],
                        "transcript_consequences": [
                            {"gene_symbol": "BRCA1", "transcript_id": "ENST1", "consequence_terms": ["intron_variant"]},
                            {"gene_symbol": "BRCA1", "transcript_id": "ENST00000357654", "canonical": 1,
                             "mane_select": "NM_007294.4", "consequence_terms": ["missense_variant"],
                             "impact": "MODERATE", "hgvsc": "ENST00000357654.9:c.5096G>A",
                             "hgvsp": "ENSP00000350283.3:p.Arg1699Gln"}]})
        return httpx.Response(200, json=out)

    return handler


def test_parse_variant_formats():
    v = annot.parse_variant("chr17:43045712:t:c")
    assert (v.kind, v.key, v.chrom, v.pos, v.ref, v.alt) == ("vcf", "17:43045712:T:C", "17", 43045712, "T", "C")
    assert annot.parse_variant("chrM:3243:A:G").key == "MT:3243:A:G"
    assert annot.parse_variant("RS80357906").key == "rs80357906"
    assert annot.parse_variant("NM_007294.4:c.68_69del").kind == "hgvs"
    assert annot.parse_variant("NM_000546.6(TP53):c.215C>G").kind == "hgvs"
    assert annot.parse_variant("VCV000017661", clinvar_ids=True).key == "VCV000017661"
    assert annot.parse_variant("17661", clinvar_ids=True).kind == "uid"
    for bad in ("17-43045712-T-C", "23:1:A:G", "1:0:A:G", "1:5:A:A", "BRCA1", "", None, "17661"):
        with pytest.raises(ValueError):
            annot.parse_variant(bad)


async def test_vep_sends_batches_of_200_per_endpoint_and_keeps_input_order(tmp_path):
    annotator, calls, _clock = make(vep_handler(), tmp_path)
    variants = [f"1:{1000 + i}:A:G" for i in range(450)] + ["rs80357906", "NM_007294.4:c.5096G>A"]
    result = await annotator.vep(variants)
    posts = [c for c in calls if c.method == "POST"]
    assert [c.url.path for c in posts] == ["/vep/human/region"] * 3 + ["/vep/human/hgvs", "/vep/human/id"]
    assert [len(json.loads(c.content)["variants"]) for c in posts[:3]] == [200, 200, 50]
    assert json.loads(posts[0].content)["variants"][0] == "1 1000 labhq0 A G . . ."
    assert all(parse_qs(c.url.query.decode()) == {"canonical": ["1"], "hgvs": ["1"], "mane": ["1"]} for c in posts)
    assert result["n_inputs"] == result["n_ok"] == 452 and result["n_errors"] == 0
    assert [r["input"] for r in result["results"]] == variants
    first = result["results"][0]
    assert first["most_severe_consequence"] == "missense_variant" and first["genes"] == ["BRCA1"]
    assert first["transcript"]["mane_select"] == "NM_007294.4" and first["colocated_ids"] == ["rs80357906"]


async def test_vep_waits_out_429_and_paces_requests(tmp_path):
    annotator, calls, clock = make(vep_handler(throttle=(2, 1.5)), tmp_path)
    result = await annotator.vep(["1:100:A:G"])
    assert result["n_ok"] == 1
    assert len([c for c in calls if c.method == "POST"]) == 3
    assert clock.sleeps.count(1.5) == 2  # Retry-After honoured twice
    assert all(s > 0 for s in clock.sleeps)


async def test_vep_spent_quota_fails_instead_of_waiting_an_hour(tmp_path):
    annotator, _calls, clock = make(vep_handler(throttle=(1, 3600)), tmp_path)
    with pytest.raises(annot.LookupFailed, match="요청 한도"):
        await annotator.vep(["1:100:A:G"])
    assert max(clock.sleeps, default=0) < annot.MAX_WAIT_S


async def test_vep_isolates_entries_ensembl_refuses_and_malformed_items(tmp_path):
    bad = "NM_007294.4:c.99999999del"
    annotator, calls, _clock = make(vep_handler(refuse={bad}), tmp_path)
    variants = ["not a variant", "NM_007294.4:c.5096G>A", bad, "NM_007294.4:c.68_69del", "1:100:A:G"]
    result = await annotator.vep(variants)
    rows = result["results"]
    assert [r["ok"] for r in rows] == [False, True, False, True, True]
    assert "형식" in rows[0]["error"] and "Unable to parse" in rows[2]["error"]
    hgvs_posts = [json.loads(c.content)["hgvs_notations"] for c in calls if c.url.path.endswith("/hgvs")]
    assert hgvs_posts[0] == variants[1:4] and [bad] in hgvs_posts  # split until the refused entry stands alone


async def test_vep_records_release_and_logs_only_counts_and_hash(tmp_path):
    annotator, _calls, _clock = make(vep_handler(release=115), tmp_path)
    result = await annotator.vep(["17:43045712:T:C", "zzz-not-variant"])
    source = result["source"]
    assert source["db"] == "Ensembl VEP" and source["release_or_version"] == "Ensembl 115 (GRCh38)"
    assert source["url"] == "https://rest.ensembl.org/vep/homo_sapiens" and source["queried_at"].endswith("Z")
    assert len(source["request_sha256"]) == 64
    log = (tmp_path / "work" / annot.QUERY_LOG).read_text(encoding="utf-8")
    line = json.loads(log.strip())
    assert line["request_sha256"] == source["request_sha256"] and line["release_or_version"] == "Ensembl 115 (GRCh38)"
    assert (line["n_inputs"], line["n_ok"], line["n_errors"]) == (2, 1, 1)
    assert "43045712" not in log and "zzz" not in log  # the variant list stays out of the log
    full = json.loads((tmp_path / "work" / result["full_result"]).read_text(encoding="utf-8"))
    assert full["source"] == source and full["results"][0]["answer"]["most_severe_consequence"] == "missense_variant"
    await annotator.vep(["17:43045712:T:C"])
    assert len((tmp_path / "work" / annot.QUERY_LOG).read_text(encoding="utf-8").splitlines()) == 2


async def test_vep_grch37_uses_the_grch37_server(tmp_path):
    annotator, calls, _clock = make(vep_handler(release=115), tmp_path)
    result = await annotator.vep(["17:41197732:T:C"], assembly="GRCh37")
    assert {c.url.host for c in calls} == {"grch37.rest.ensembl.org"}
    assert result["source"]["release_or_version"] == "Ensembl 115 (GRCh37)"
    with pytest.raises(ValueError):
        await annotator.vep(["1:1:A:G"], assembly="hg19")


async def test_vep_cache_hits_until_the_release_changes(tmp_path):
    first, calls, _clock = make(vep_handler(release=115), tmp_path)
    await first.vep(["1:100:A:G", "rs1"])
    again, calls2, _clock = make(vep_handler(release=115), tmp_path)
    result = await again.vep(["1:100:A:G", "rs1", "1:200:C:T"])
    posts = [json.loads(c.content) for c in calls2 if c.method == "POST"]
    assert posts == [{"variants": ["1 200 labhq0 C T . . ."]}]  # only the new variant goes out
    assert result["n_cached"] == 2 and "cached_from" in result["results"][0] and "cached_from" not in result["results"][2]
    newer, calls3, _clock = make(vep_handler(release=116), tmp_path)
    result = await newer.vep(["1:100:A:G"])
    assert result["n_cached"] == 0 and len([c for c in calls3 if c.method == "POST"]) == 1
    assert result["source"]["release_or_version"] == "Ensembl 116 (GRCh38)"


def gnomad_handler(seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body["variables"])
        variables = body["variables"]
        if variables.get("variantId") == "1-55051215-G-GA" or variables.get("rsid") == "rs121908120":
            population = [{"id": "afr", "ac": 1, "an": 100, "homozygote_count": 0, "hemizygote_count": 0},
                          {"id": "XX", "ac": 1, "an": 60, "homozygote_count": 0, "hemizygote_count": 0},
                          {"id": "afr_XX", "ac": 1, "an": 50, "homozygote_count": 0, "hemizygote_count": 0}]
            return httpx.Response(200, json={"data": {"variant": {
                "variant_id": "1-55051215-G-GA", "reference_genome": "GRCh38", "chrom": "1", "pos": 55051215,
                "ref": "G", "alt": "GA", "rsids": ["rs121908120"],
                "exome": {"ac": 3, "an": 1000, "homozygote_count": 0, "hemizygote_count": 0, "filters": [],
                          "populations": population},
                "genome": {"ac": 1, "an": 1000, "homozygote_count": 0, "hemizygote_count": 0, "filters": [],
                           "populations": population}}}})
        if variables.get("variantId") == "2-1-A-G":
            return httpx.Response(429, headers={"Retry-After": "10"})
        return httpx.Response(200, json={"errors": [{"message": "Variant not found"}], "data": {"variant": None}})
    return handler


async def test_gnomad_frequencies_absence_and_pacing(tmp_path):
    seen = []
    annotator, calls, clock = make(gnomad_handler(seen), tmp_path)
    result = await annotator.gnomad(["chr1:55051215:G:GA", "3:100:C:T", "rs121908120", "NM_1.1:c.1A>G", "x"],
                                    dataset="gnomad_r4")
    rows = result["results"]
    assert result["source"]["release_or_version"] == "gnomad_r4" and result["source"]["db"] == "gnomAD"
    assert rows[0]["found"] is True and rows[0]["af_exome_plus_genome"] == pytest.approx(4 / 2000)
    assert [p["id"] for p in rows[0]["exome"]["populations"]] == ["afr"]
    assert rows[1]["ok"] and rows[1]["found"] is False  # not in gnomAD: a successful lookup, not an error
    assert rows[2]["found"] is True and not rows[3]["ok"] and "HGVS" in rows[3]["error"] and not rows[4]["ok"]
    assert seen[0] == {"dataset": "gnomad_r4", "variantId": "1-55051215-G-GA"}
    assert seen[2] == {"dataset": "gnomad_r4", "rsid": "rs121908120"}
    assert "DatasetId!" in json.loads(calls[0].content)["query"]
    assert clock.sleeps == [pytest.approx(6.0)] * 2  # 10 queries a minute
    with pytest.raises(ValueError):
        await annotator.gnomad(["1:1:A:G"], dataset="gnomad_r4) { x }")


async def test_gnomad_caps_new_lookups_per_call_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(annot, "GNOMAD_MAX_PER_CALL", 2)
    seen = []
    annotator, _calls, clock = make(gnomad_handler(seen), tmp_path)
    result = await annotator.gnomad(["3:1:A:G", "3:2:A:G", "3:3:A:G"])
    assert [r["ok"] for r in result["results"]] == [True, True, False] and "note" in result
    assert "다시 호출" in result["results"][2]["error"] and len(seen) == 2
    again = await annotator.gnomad(["3:1:A:G", "3:2:A:G", "3:3:A:G"])
    assert again["n_cached"] == 2 and again["n_ok"] == 3 and len(seen) == 3
    with pytest.raises(annot.LookupFailed):  # 429 every time: a failure, never "not found"
        await annotator.gnomad(["2:1:A:G"])
    assert clock.sleeps.count(10.0) == annot.MAX_ATTEMPTS - 1


def clinvar_handler(lastupdate="2026/10/05 03:12", terms=None):
    docs = {
        "17661": {"uid": "17661", "accession": "VCV000017661", "title": "NM_007294.4(BRCA1):c.5096G>A (p.Arg1699Gln)",
                  "genes": [{"symbol": "BRCA1"}],
                  "germline_classification": {"description": "Pathogenic", "review_status":
                                              "reviewed by expert panel", "last_evaluated": "2016/08/02",
                                              "trait_set": [{"trait_name": "Hereditary breast ovarian cancer"}]},
                  "variation_set": [{"canonical_spdi": "NC_000017.11:43045711:T:C"}]},
        "99": {"uid": "99", "accession": "VCV000000099", "title": "other allele at the same position",
               "germline_classification": {"description": "Benign"},
               "variation_set": [{"canonical_spdi": "NC_000017.11:43045711:T:G"}]},
    }

    def handler(request):
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        assert query["retmode"] == "json" and query["tool"] == "labhq" and query["db"] == "clinvar"
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "einfo.fcgi":
            return httpx.Response(200, json={"einforesult": {"dbinfo": [
                {"dbname": "clinvar", "dbbuild": "Build261005-0312.1", "lastupdate": lastupdate}]}})
        if name == "esearch.fcgi":
            if terms is not None:
                terms.append(query["term"])
            ids = {"17[chr] AND 43045712[chrpos38]": ["17661", "99"], "rs80357906": ["17661"],
                   "VCV000017661": ["17661"]}.get(query["term"], [])
            return httpx.Response(200, json={"esearchresult": {"count": str(len(ids)), "idlist": ids}})
        if name == "esummary.fcgi":
            ids = query["id"].split(",")
            return httpx.Response(200, json={"result": {"uids": ids, **{i: docs[i] for i in ids if i in docs}}})
        raise AssertionError(request.url)
    return handler


async def test_clinvar_classifications_with_version_and_match(tmp_path):
    terms = []
    annotator, calls, clock = make(clinvar_handler(terms=terms), tmp_path)
    result = await annotator.clinvar(["17:43045712:T:C", "rs80357906", "VCV000017661", "17661", "3:5:A:G", "??"])
    assert result["source"]["release_or_version"] == "ClinVar Build261005-0312.1, last update 2026/10/05 03:12"
    rows = result["results"]
    assert [r["match"] for r in rows[0]["records"]] == ["exact", "same_position"]
    top = rows[0]["records"][0]
    assert top["germline_classification"]["description"] == "Pathogenic" and top["genes"] == ["BRCA1"]
    assert top["traits"] == ["Hereditary breast ovarian cancer"]
    assert all(r["records"][0]["accession"] == "VCV000017661" for r in rows[1:4])
    assert rows[4]["ok"] and rows[4]["found"] is False and not rows[5]["ok"]
    assert terms == ["17[chr] AND 43045712[chrpos38]", "rs80357906", "VCV000017661", "3[chr] AND 5[chrpos38]"]
    summaries = [c for c in calls if c.url.path.endswith("esummary.fcgi")]
    assert len(summaries) == 1  # one esummary for every distinct ID
    assert all(s == pytest.approx(1 / 3) for s in clock.sleeps) and clock.sleeps  # 3 requests a second


async def test_clinvar_cache_follows_the_clinvar_update(tmp_path):
    terms = []
    first, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    await first.clinvar(["rs80357906"])
    same, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    assert (await same.clinvar(["rs80357906"]))["n_cached"] == 1 and len(terms) == 1
    updated, _calls, _clock = make(clinvar_handler(lastupdate="2026/10/12 03:10", terms=terms), tmp_path)
    assert (await updated.clinvar(["rs80357906"]))["n_cached"] == 0 and len(terms) == 2


async def test_unreachable_service_fails_the_call(tmp_path):
    def down(request):
        raise httpx.ConnectError("no route")
    annotator, _calls, clock = make(down, tmp_path)
    with pytest.raises(annot.LookupFailed, match="연결하지 못했습니다"):
        await annotator.clinvar(["rs1"])
    assert len(clock.sleeps) == annot.MAX_ATTEMPTS - 1


def test_runner_wires_annot_without_per_call_approval(tmp_path):
    from labhq.runner.daemon import Runner

    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    try:
        with_annot = AgentSpec(id="a", name="A", role="r", engine=Engine.claude_code, builtin_mcp=["approval", "annot"])
        servers = runner._mcp_servers(with_annot, {"LABHQ_WORKDIR": str(tmp_path)})
        assert [s.name for s in servers] == ["labhq_approval", "labhq_annot", "labhq_ask"]
        spec = servers[1]
        assert spec.args == ["-m", "labhq.tools.annot_mcp"] and spec.auto_approve and spec.timeout_s
        without = AgentSpec(id="b", name="B", role="r", engine=Engine.codex, builtin_mcp=[])
        assert "labhq_annot" not in [s.name for s in runner._mcp_servers(without, {})]
    finally:
        runner.store.close()


async def _emit(kind, data):
    pass


@pytest.mark.parametrize("engine", ["claude_code", "codex"])
def test_both_engines_run_annot_tools_without_asking(tmp_path, engine):
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine(engine), builtin_mcp=["approval", "annot"])
    spec = McpServerSpec(name="labhq_annot", command=sys.executable, args=["-m", "labhq.tools.annot_mcp"],
                         auto_approve=True, timeout_s=900)
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[spec], env={}, emit=_emit, prompt="x", claude_settings={})
    ctx.meta_dir.mkdir(parents=True, exist_ok=True)
    adapter = get_adapter(agent.engine, settings)
    if engine == "claude_code":
        adapter.prepare(ctx)
        cmd = adapter.build_command(ctx)
        assert "mcp__labhq_annot" in cmd[cmd.index("--allowedTools"):]
        servers = json.loads((ctx.meta_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        assert servers["labhq_annot"]["timeout"] == 900 * 1000
    else:
        cmd = adapter.build_command(ctx)
        assert 'mcp_servers.labhq_annot.default_tools_approval_mode="approve"' in cmd
        assert "mcp_servers.labhq_annot.tool_timeout_sec=900" in cmd


def test_core_staff_with_annot_and_roster_line(tmp_path):
    from labhq.gateway.server import Hub
    from labhq.orchestrator.cso import format_roster
    from labhq.registry import Registry

    registry = Registry(REPO / "agents", tmp_path / "talent")
    registry.load()
    with_annot = {a.id for a in registry.agents.values() if "annot" in a.builtin_mcp}
    assert with_annot == {"analyst", "bioinfo-agent", "biologist", "lit_scout"}
    analyst = registry.get("analyst").summary()
    assert "labhq_annot" in Hub._agent_mcp(analyst)
    assert "labhq_annot=yes" in format_roster([analyst])
    assert "labhq_annot" not in format_roster([registry.get("qc_reviewer").summary()])


async def test_stdio_server_lists_tools_and_answers_malformed_items_offline(tmp_path):
    env = {"PYTHONPATH": str(REPO), "LABHQ_WORKDIR": str(tmp_path / "work"),
           "LABHQ_STATE_DIR": str(tmp_path / "state")}
    (tmp_path / "work").mkdir()
    tools = await list_tools(McpServerSpec(name="annot", command=sys.executable,
                                           args=["-m", "labhq.tools.annot_mcp"], env=env))
    assert set(tools) == {"vep", "gnomad", "clinvar"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.annot_mcp"],
                                   env={**os.environ, **env})
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            # Every item malformed: nothing goes out to gnomAD, and the call still answers and logs.
            answer = await session.call_tool("gnomad", {"variants": ["not-a-variant"]})
            assert not answer.is_error
            payload = json.loads(answer.content[0].text)
            assert payload["n_errors"] == 1 and payload["source"]["release_or_version"] == "gnomad_r4"
            bad = await session.call_tool("gnomad", {"variants": ["1:1:A:G"], "dataset": "nope"})
            assert bad.is_error and "증거" in bad.content[0].text
    log = (tmp_path / "work" / "outputs" / "annotation_queries.jsonl").read_text(encoding="utf-8")
    assert json.loads(log)["tool"] == "gnomad"


async def test_without_a_workdir_the_answer_says_it_was_not_logged(tmp_path):
    annotator = annot.Annotator(httpx.AsyncClient(transport=httpx.MockTransport(vep_handler())), None, None)
    result = await annotator.gnomad(["not-a-variant"])
    assert "record_error" in result and "full_result" not in result


async def test_cache_schema_change_asks_again(tmp_path, monkeypatch):
    terms = []
    first, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    await first.clinvar(["rs80357906"])
    monkeypatch.setattr(annot, "CACHE_SCHEMA", annot.CACHE_SCHEMA + 1)
    again, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    assert (await again.clinvar(["rs80357906"]))["n_cached"] == 0 and len(terms) == 2
