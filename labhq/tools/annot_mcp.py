"""labhq-annot MCP server (stdio): public-resource lookups with release records (#435 C ② ③).

Variants: Ensembl VEP, gnomAD, ClinVar (`annot.py`). Regions, genes and expression: ChIP-Atlas, ENCODE cCREs, GTEx,
and AlphaGenome predictions when its key file exists (`annot_regulatory.py`). Papers: Europe PMC open-access full text,
citation chasing, bioRxiv/medRxiv preprints, OpenAlex discovery, Crossref metadata and Unpaywall legal OA locations
(`annot_literature.py`, #697, #720, #744).

Env (set by the runner): LABHQ_WORKDIR (where outputs/annotation_queries.jsonl goes), LABHQ_CONFIG (state_dir for the
cache, annot.alphagenome_key_file). The AlphaGenome key is read here from that owner-only file, never from the
command line or the environment, and is kept only inside the backend object. The separate Atlas tool uses only
the runner-owned Atlas endpoint; the staff CLI and this MCP process never receive its API key.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Any, Literal

import httpx

from ..runner.lookup_admission import ENV as LOOKUP_ADMISSION_ENV
from ..settings import Settings
from . import annot, annot_keys, annot_literature, annot_regulatory
from ..runner.annot_broker import ENABLED_ENV as ATLAS_ENABLED_ENV
from ._mcpcompat import make_server, tool_failure
from .external_wait import LookupTimeout, _step_slot, exclude_human_wait, run_lookup

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
WORKDIR = Path(os.environ["LABHQ_WORKDIR"]).resolve() if os.environ.get("LABHQ_WORKDIR") else None
CACHE_DIR = S.path(S.runner.state_dir) / "annot_cache"
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
BROKER_TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK_ID = os.environ.get("LABHQ_TASK_ID")
AGENT_ID = os.environ.get("LABHQ_AGENT_ID")
# Request-wide public lookup limit (#858): either the config copy or the runner's flag turns admission on.
LOOKUP_LIMITED = S.annot.request_lookup_limit is not None or os.environ.get(LOOKUP_ADMISSION_ENV) == "1"


def _alphagenome() -> annot_regulatory.AlphaGenomeBackend | None:
    """The backend when a key is stored and the official client is installed; otherwise the tool is not offered."""
    key = annot_keys.read_key(S)
    if not key or not annot_regulatory.alphagenome_client_available():
        return None
    return annot_regulatory.AlphaGenomeBackend(key)


ALPHAGENOME = _alphagenome()
ATLAS_ENABLED = os.environ.get(ATLAS_ENABLED_ENV) == "1"

server = make_server(
    "labhq-annot",
    instructions=(
        "Look up public databases: vep (Ensembl VEP consequences, with AlphaMissense missense pathogenicity for human "
        "GRCh38), gnomad (population allele frequency), clinvar "
        "(clinical classification) for variants; chipatlas (ChIP-Atlas enrichment of public ChIP/ATAC peaks for a gene "
        "list or regions), chipatlas_targets (target genes of a transcription factor), encode (ENCODE candidate "
        "cis-regulatory elements overlapping regions), gtex (GTEx tissue expression and eQTLs for genes or variants), "
        "fulltext (the body of a paper by PMID, PMCID or DOI: Europe PMC open-access full text, else the abstract), "
        "key_fulltext (for conclusion-critical papers: permitted OA, opt-in institution session, then one PI PDF card), "
        "citation_chase (references and citing papers for one paper), preprints (bioRxiv/medRxiv search; 심사 전), "
        "openalex_works (broad works search without OA pre-filtering), crossref_doi (version/license/update/retraction "
        "metadata), unpaywall (legal OA locations; explicitly disabled when annot.unpaywall_email is empty)"
        + ("; alphagenome_variant (recommended variant effect scores, GRCh38), "
           "alphagenome (legacy track summaries over variants or intervals)" if ALPHAGENOME else "")
        + ("; alphagenome_atlas (precomputed GRCh38 SNV scores, requires ALPHAGENOME_API_KEY and Atlas SDK)"
           if ATLAS_ENABLED else "")
        + ". Give variants as chr:pos:ref:alt (VCF-style alleles), HGVS or rsID, and regions as chr:start-end "
        "(1-based, inclusive). A malformed item comes back as that item's error and the rest are still looked up. "
        "Each result names the database release under `source` and is logged to outputs/annotation_queries.jsonl, "
        "with the full answer under outputs/annotation/; cite those files. An item error is a failed lookup, not a "
        "negative result; found=false means the lookup succeeded and the database has no entry. "
        "Automatic API lookups share a 20-minute deadline from the first lookup in this step, including retries. "
        "On timeout, skip that lookup, continue remaining work and name the missing query and "
        "outputs/external_lookup_status.jsonl in the report. Human PDF/approval waits keep their own policy. "
        "When this request has a public lookup limit, every outbound HTTP request these tools send uses one attempt "
        "before it is sent, including retries, redirects and one request per paper or item; a refused request is "
        "not sent, so do not call again and report the missing lookup."
    ),
)


# Last request time per service for this server process: each tool call builds a new Annotator, and a fresh pace
# would send its first request without waiting (gnomAD 10/min, NCBI 3/s; PR #449 review).
PACE: dict[str, float] = {}


async def _lookup_progress(data: dict) -> None:
    if not BROKER_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(f"{BROKER}/event", headers={"X-Labhq-Token": BROKER_TOKEN}, json={
                "task_id": TASK_ID, "agent_id": AGENT_ID, "type": "agent.log",
                "data": {"level": "warn" if data["status"] == "timeout" else "info",
                         "text": data["text"], "external_lookup": data},
            })
            response.raise_for_status()
    except httpx.HTTPError:
        pass  # Local status records and the deadline do not depend on the broker.


class LookupRefused(annot.LookupFailed):
    """The request's public lookup limit refused one outbound request, which was therefore never sent (#858)."""


async def _admit(tool: str, host: str) -> int:
    """One counted attempt from the runner's broker for one outbound request, just before it is sent; else refuse."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{BROKER}/lookup/admit", headers={"X-Labhq-Token": BROKER_TOKEN},
                                         json={"task_id": TASK_ID, "agent_id": AGENT_ID, "tool": tool, "host": host})
            response.raise_for_status()
            answer = response.json()
    except (httpx.HTTPError, ValueError):
        raise LookupRefused(f"{tool}: 요청의 공개 조회 상한을 확인하지 못해 보내지 않았습니다") from None
    if not isinstance(answer, dict) or answer.get("admitted") is not True or type(answer.get("seq")) is not int:
        detail = answer if isinstance(answer, dict) else {}
        raise LookupRefused(f"{tool}: 이 요청의 공개 조회 시도 상한({detail.get('limit')}회, 재시도·redirect 포함)에서 "
                            f"허용되지 않아 보내지 않았습니다({detail.get('reason') or 'refused'}). 다시 부르지 말고 "
                            "빠진 조회를 보고서에 적으세요")
    return answer["seq"]


async def _finish(sends: list[list], outcome: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{BROKER}/lookup/finish", headers={"X-Labhq-Token": BROKER_TOKEN},
                              json={"task_id": TASK_ID, "sends": sends, "outcome": outcome})
    except httpx.HTTPError:
        pass  # every send was counted at admission; only its receipt fields stay empty


@asynccontextmanager
async def _admitted(tool: str):
    """Client options that admit every outbound request of one tool call before httpx sends it.

    httpx awaits ``request`` hooks right before each send, also for every redirect hop it follows; annot's
    retries and fan-out call the client again. A refusal raises inside the hook, so that request is never sent,
    and every later request of this call is refused without asking again."""
    if not LOOKUP_LIMITED:
        yield {}
        return
    if not BROKER_TOKEN:
        raise tool_failure(f"{tool}: 요청의 공개 조회 상한이 켜져 있지만 요청을 확인할 broker token이 없어 조회하지 않았습니다")
    if tool.startswith("alphagenome"):
        # The AlphaGenome SDK has its own transport, so its requests cannot be admitted one by one.
        raise tool_failure(f"{tool}: 요청의 공개 조회 상한이 켜져 있으면 요청마다 셀 수 없는 AlphaGenome 조회는 하지 않습니다")
    sends: dict[int, int | None] = {}
    refused: list[str] = []

    async def admit(request: httpx.Request) -> None:
        if refused:
            raise LookupRefused(refused[0])
        try:
            seq = await _admit(tool, (request.url.host or "").lower())
        except LookupRefused as exc:
            refused.append(str(exc))
            raise
        request.extensions = {**request.extensions, "labhq_lookup_seq": seq}
        sends[seq] = None

    async def answered(response: httpx.Response) -> None:
        seq = response.request.extensions.get("labhq_lookup_seq")
        if seq in sends:
            sends[seq] = response.status_code

    outcome = "failed"
    try:
        yield {"event_hooks": {"request": [admit], "response": [answered]}}
        outcome = "ok"
    except BaseException as exc:
        outcome = "timeout" if isinstance(exc.__cause__, LookupTimeout) else "failed"
        raise
    finally:
        if sends:
            await _finish([[seq, status] for seq, status in sorted(sends.items())], outcome)


async def _run(method: str, *args: object, **kwargs: object) -> str:
    async with _admitted(method) as hooks, httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0),
                                                             headers={"User-Agent": annot.USER_AGENT},
                                                             **hooks) as client:
        annotator = annot_regulatory.RegulatoryAnnotator(client, WORKDIR, CACHE_DIR, pace=PACE,
                                                         alphagenome=ALPHAGENOME)
        async def operation():
            result = await getattr(annotator, method)(*args, **kwargs)
            if method == "alphagenome_atlas" and result.get("n_ok") == 0:
                # The per-item errors are already recorded; do not log an all-error query as a successful lookup.
                reason = result["results"][0]["error"]
                raise annot.LookupFailed(f"Atlas 조회가 모두 실패했습니다: {reason}")
            return result

        try:
            result = await run_lookup(operation,
                                      tool=method, workdir=WORKDIR, notify=_lookup_progress)
        except (annot.LookupFailed, LookupTimeout, ValueError) as exc:
            message = ALPHAGENOME.redact(str(exc)) if ALPHAGENOME else str(exc)
            raise tool_failure(message) from exc
    return json.dumps(result, ensure_ascii=False)


async def _run_literature(method: str, *args: object) -> str:
    async with _admitted(method) as hooks, httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0),
                                                             follow_redirects=True,
                                                             headers={"User-Agent": annot.USER_AGENT},
                                                             **hooks) as client:
        annotator = annot_literature.LiteratureAnnotator(
            client, WORKDIR, CACHE_DIR, pace=PACE, unpaywall_email=S.annot.unpaywall_email)
        try:
            result = await run_lookup(lambda: getattr(annotator, method)(*args),
                                      tool=method, workdir=WORKDIR, notify=_lookup_progress)
        except (annot.LookupFailed, LookupTimeout, ValueError) as exc:
            raise tool_failure(str(exc)) from exc
    return json.dumps(result, ensure_ascii=False)


async def _request_pdf(summary: str, detail: dict, timeout_s: int) -> dict:
    if not BROKER_TOKEN:
        return {"approved": False, "state": "unavailable", "note": "approval broker token is absent"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s + 30, connect=10)) as client:
            response = await client.post(f"{BROKER}/approval", headers={"X-Labhq-Token": BROKER_TOKEN}, json={
                "task_id": TASK_ID, "agent_id": AGENT_ID, "kind": "literature_pdf", "summary": summary,
                "detail": detail, "timeout_s": timeout_s,
            })
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        return {"approved": False, "state": "unavailable", "note": "PDF request card could not be opened"}


async def _run_key_fulltext(papers: list[dict[str, Any]]) -> str:
    async def automatic(operation):
        return await run_lookup(operation, tool="key_fulltext", workdir=WORKDIR, notify=_lookup_progress)

    async def request_pdf(summary, detail, timeout_s):
        return await exclude_human_wait(lambda: _request_pdf(summary, detail, timeout_s), workdir=WORKDIR)

    async with _admitted("key_fulltext") as hooks, httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=20.0), follow_redirects=True,
            headers={"User-Agent": annot.USER_AGENT}, **hooks) as client:
        # The same contact email as the plain literature tools: key papers also check legal OA copies (#826).
        annotator = annot_literature.LiteratureAnnotator(client, WORKDIR, CACHE_DIR, pace=PACE,
                                                         unpaywall_email=S.annot.unpaywall_email)
        institution = limiter = None
        policy = S.literature
        # The institution route drives a browser outside the admitted client, so it is off while a limit is on.
        if (policy.institution_enabled and policy.institution_tdm_allowed and policy.external_model_allowed
                and not LOOKUP_LIMITED):
            from ..report_pdf import find_browser
            institution = annot_literature.BrowserInstitutionTransport(
                S.path(policy.institution_profile) if policy.institution_profile else None,
                policy.institution_proxy, find_browser(S.gateway.report_browser))
            limiter = annot_literature.InstitutionLimiter(
                CACHE_DIR, policy.institution_request_interval_s, policy.institution_daily_limit)
        coordinator = annot_literature.KeyFulltextCoordinator(
            annotator, policy, WORKDIR, CACHE_DIR, institution=institution, limiter=limiter,
            request_pdf=request_pdf, automatic_lookup=automatic)
        try:
            result = await coordinator.run(papers)
        except (annot.LookupFailed, LookupTimeout, ValueError) as exc:
            raise tool_failure(str(exc)) from exc
    return json.dumps(result, ensure_ascii=False)


@server.tool()
async def vep(variants: list[str], assembly: Literal["GRCh38", "GRCh37"] = "GRCh38", species: str = "human") -> str:
    """Ensembl VEP consequences (most severe consequence, genes, MANE/canonical transcript HGVS) for variants given
    as chr:pos:ref:alt, HGVS or rsID. Sent in batches of 200 within Ensembl's rate limits. For human GRCh38 each
    result also has `alphamissense`: the AlphaMissense score (0-1) and class of a missense variant from the same
    transcript, or "해당 없음" for other variants. It is a model prediction, not clinical evidence (CC BY 4.0, cite
    Cheng et al. 2023 Science); the terms are under `source.additional_sources`."""
    return await _run("vep", variants, assembly, species)


@server.tool()
async def gnomad(variants: list[str], dataset: str = "gnomad_r4") -> str:
    """gnomAD allele frequencies (exome, genome, main genetic ancestry groups) for chr:pos:ref:alt or rsID.
    dataset is a gnomAD dataset ID such as gnomad_r4 (GRCh38) or gnomad_r2_1 (GRCh37). gnomAD allows about
    10 queries a minute, so at most 50 new variants are looked up per call."""
    return await _run("gnomad", variants, dataset)


@server.tool()
async def clinvar(variants_or_ids: list[str], assembly: Literal["GRCh38", "GRCh37"] = "GRCh38") -> str:
    """ClinVar classifications (germline, clinical impact, oncogenicity, review status) through NCBI E-utilities.
    Accepts chr:pos:ref:alt (matched by position on `assembly`), HGVS, rsID, VCV accession or ClinVar Variation ID."""
    return await _run("clinvar", variants_or_ids, assembly)


@server.tool()
async def chipatlas(genes_or_regions: list[str], genome: str = "hg38", antigen_class: str = "TFs and others",
                    cell_class: str = "All cell types", threshold: Literal[50, 100, 200, 500] = 50,
                    distance_kb: int = 5, permutations: Literal[1, 10, 100] = 1, wait_s: float = 600,
                    request_id: str | None = None) -> str:
    """ChIP-Atlas enrichment analysis: which public ChIP-seq/ATAC/DNase experiments overlap a gene list (TSS ±
    distance_kb, against all RefSeq genes) or a set of regions chr:start-end (against random permutations) more than
    expected. antigen_class: TFs and others, Histone, RNA polymerase, Input control, ATAC-Seq, DNase-seq or
    Bisulfite-Seq; cell_class: All cell types, Blood, Liver, ... The analysis runs as a job on the DDBJ server and
    usually takes minutes; the call waits up to wait_s seconds (max 1500), within the task's 20-minute external
    lookup deadline. On timeout, report the missing lookup and continue; do not automatically poll again.
    request_id resumes a job only when explicitly requested. Do not mix genes and regions in one call."""
    return await _run("chipatlas", genes_or_regions, genome, antigen_class, cell_class, threshold, distance_kb,
                      permutations, wait_s, request_id)


@server.tool()
async def chipatlas_targets(antigens: list[str], genome: str = "hg38", distance_kb: Literal[1, 5, 10] = 5,
                            genes: list[str] | None = None, top: int = 50) -> str:
    """ChIP-Atlas target genes of transcription factors (e.g. POU5F1): genes ranked by the average MACS2 binding score
    within distance_kb of their TSS across all public experiments, with the STRING score. `genes` picks out specific
    genes' scores. Up to 20 antigens per call."""
    return await _run("chipatlas_targets", antigens, genome, distance_kb, genes, top)


@server.tool()
async def encode(regions: list[str], assembly: Literal["GRCh38", "mm10"] = "GRCh38") -> str:
    """ENCODE candidate cis-regulatory elements (cCREs: PLS, pELS, dELS, CA-CTCF, CA-TF, ...) overlapping regions
    given as chr:start-end (1-based, inclusive) or chr:pos. Uses the newest released ENCODE cCRE registry (V4) file,
    downloaded once and cached. Up to 500 regions of at most 2 Mb per call."""
    return await _run("encode", regions, assembly)


@server.tool()
async def gtex(genes_or_variants: list[str], tissues: list[str] | None = None, dataset: str = "gtex_v10",
               eqtl: bool = True) -> str:
    """GTEx tissue expression (median TPM) and single-tissue eQTLs for genes (symbol or Ensembl ID) and eQTLs for
    variants (chr:pos:ref:alt on GRCh38, or rsID). tissues are GTEx tissueSiteDetailId values such as Liver or
    Whole_Blood (all tissues when empty). Up to 50 items per call; eQTL lists stop at 1,000 rows per item."""
    return await _run("gtex", genes_or_variants, tissues, dataset, eqtl)


@server.tool()
async def fulltext(ids: list[str]) -> str:
    """The body of papers given as PMID, PMCID (PMC...) or DOI, up to 20 per call. Europe PMC finds each record; an
    open-access copy there is written as Markdown (sections, tables, figure captions, references) to
    .tmp/literature/<id>.md in your workspace, otherwise that file holds the abstract. Each result says `access_level` (full_text,
    abstract or metadata), the license and `text_file`; read the file with your file tools. Use this instead of
    opening PMC or publisher pages on the web, which often answer with a CAPTCHA. The files are third-party text
    under the paper's license: cite them by path, quote briefly, and never copy them into outputs/.
    `publisher_copy` appears when a CC-licensed paper has no Europe PMC copy: one try at that link is fine; paywalled
    papers stay at the abstract."""
    return await _run_literature("fulltext", ids)


@server.tool()
async def key_fulltext(papers: list[dict[str, Any]]) -> str:
    """Read conclusion-critical papers through permitted routes only. Each item needs id, title, why_key, needed and
    optional tried. Open access is checked first. When configured, one DOI at a time may use the PI's institution
    browser profile or proxy within its interval and daily limit. Remaining papers open one PDF request card; the
    default wait is two hours. Upload each PDF on the card or put it at the exact .tmp/literature/inbox path, then approve.
    On expiry the result keeps the abstract/metadata, records the access failure, and directs the report to use that
    limitation or another OA paper. Passwords, cookies and tokens are never accepted or returned."""
    return await _run_key_fulltext(papers)


@server.tool()
async def citation_chase(paper_id: str, limit: int = 100) -> str:
    """For one PMID, PMCID or DOI, retrieve both references (backward citation chasing) and publications that cite
    it (forward citation chasing) from Europe PMC. Up to `limit` candidates per direction (1..1000). Results contain
    compact bibliography, source identifiers and access_level_candidate; use fulltext to confirm the level read."""
    return await _run_literature("citation_chase", paper_id, limit)


@server.tool()
async def preprints(query: str, limit: int = 25) -> str:
    """Search bioRxiv and medRxiv through Europe PMC SRC:PPR. Returns up to `limit` candidates (1..100), all marked
    `심사 전`; do not treat them as peer-reviewed evidence."""
    return await _run_literature("preprints", query, limit)


@server.tool()
async def openalex_works(query: str, filters: list[str] | None = None, limit: int = 25) -> str:
    """Search OpenAlex works across disciplines. `filters` are OpenAlex filter expressions, one per list item, and
    `limit` is 1..200. Results include DOI, PMID, OA status and best_oa_location, citation count, cited-by query URL,
    and referenced OpenAlex work IDs. OA status is never used to remove a candidate."""
    return await _run_literature("openalex_works", query, filters, limit)


@server.tool()
async def crossref_doi(dois: list[str]) -> str:
    """Crossref metadata for up to 50 DOIs: registered version, licenses, relations, update-to records and an
    explicit retraction flag. Metadata does not prove that a full-text copy was acquired."""
    return await _run_literature("crossref_doi", dois)


@server.tool()
async def unpaywall(dois: list[str]) -> str:
    """Unpaywall legal OA locations for up to 50 DOIs. Requires annot.unpaywall_email; without it the call returns
    enabled=false without contacting the API. The contact email is never written to results, logs or cache keys."""
    return await _run_literature("unpaywall", dois)


async def alphagenome_atlas(variants: list[str], requested_scorers: list[str] | None = None,
                           ontology_terms: list[str] | None = None, gene_ids: list[str] | None = None,
                           top: int = 20, assembly: Literal["GRCh38"] = "GRCh38") -> str:
    """Precomputed AlphaGenome Atlas scores, never model inference. GRCh38 SNVs chr:pos:ref>alt only, 1..10.
    requested_scorers: exact Atlas scorer names, default RNA_SEQ, up to 10; ontology_terms: up to 20 CURIEs;
    gene_ids: up to 20 Ensembl gene IDs. Both filters are sent to Atlas. top 1..100, stores at most 1000 rows.
    Requires runner ALPHAGENOME_API_KEY (no key file) and alphagenome>=0.10.0. Reports missing/unsupported access.
    Non-commercial research only, no clinical decisions or training other ML models. Does not infer absent scores.
    Request-wide lookup limits refuse this SDK transport, as for other AlphaGenome tools."""
    # Retain this MCP loop's ordering with other lookups and human PDF waits; only the runner
    # records the Atlas deadline/status, so a broker call does not duplicate those records.
    async with _step_slot(WORKDIR), _admitted("alphagenome_atlas"):
        if not BROKER_TOKEN:
            raise tool_failure("Atlas runner broker is unavailable")
        try:
            # The runner owns the deadline, records, admission and credential. No caller URL or command.
            async with httpx.AsyncClient(timeout=httpx.Timeout(1220, connect=10), trust_env=False) as client:
                response = await client.post(f"{BROKER}/annot/atlas", headers={"X-Labhq-Token": BROKER_TOKEN},
                    json={"task_id": TASK_ID, "agent_id": AGENT_ID, "variants": variants,
                          "requested_scorers": requested_scorers, "ontology_terms": ontology_terms,
                          "gene_ids": gene_ids, "top": top, "assembly": assembly})
                response.raise_for_status()
                return json.dumps(response.json(), ensure_ascii=False)
        except (httpx.HTTPError, ValueError):
            raise tool_failure("Atlas runner lookup failed; see outputs/annotation_queries.jsonl and "
                               "outputs/external_lookup_status.jsonl") from None


if ATLAS_ENABLED:
    server.tool()(alphagenome_atlas)


if ALPHAGENOME is not None:
    @server.tool()
    async def alphagenome_variant(variants: list[str], sequence_length: Literal["16KB", "100KB", "500KB", "1MB"] = "1MB",
                                  ontology_terms: list[str] | None = None, tissues: list[str] | None = None,
                                  top: int = 20, assembly: Literal["GRCh38"] = "GRCh38") -> str:
        """Recommended AlphaGenome score_variant scores for GRCh38 chr:pos:ref>alt (or chr:pos:ref:alt).
        Returns Korean effect summary and top tidy rows ranked by absolute quantile. Direction is reported only
        for signed scorers. At most 10 variants, 1MB per variant, 1000 bp per allele, top 1..100 (default 20).
        ontology_terms (CURIEs) and tissues (exact biosample_name or gtex_tissue, case-insensitive) filter rows
        AFTER scoring; each allows 20 values. SDK/model/scorers and terms accompany the cached results.
        Non-commercial research predictions, not clinical evidence."""
        return await _run("alphagenome_variant", variants, sequence_length=sequence_length,
                          ontology_terms=ontology_terms, tissues=tissues, top=top, assembly=assembly)

    @server.tool()
    async def alphagenome(variants_or_intervals: list[str], outputs: list[str] | None = None,
                          ontology_terms: list[str] | None = None,
                          sequence_length: Literal["16KB", "100KB", "500KB", "1MB"] = "100KB",
                          organism: Literal["human", "mouse"] = "human") -> str:
        """Google DeepMind AlphaGenome predictions. A variant chr:pos:ref:alt (hg38/mm10) gets, per track, the
        largest predicted alt-minus-ref change in the prediction window and where it is; an interval chr:start-end
        gets each track's mean and peak signal inside that interval. outputs: RNA_SEQ (default), ATAC, CAGE, DNASE,
        CHIP_HISTONE, CHIP_TF, SPLICE_SITES, SPLICE_SITE_USAGE, PROCAP. ontology_terms (e.g. UBERON:0002107 for
        liver) narrows the tracks; empty means all tracks, which 500KB and 1MB refuse for the 1-bp outputs
        (everything but CHIP_HISTONE and CHIP_TF). Up to 10 items per call. Model predictions for research, not
        clinical decisions."""
        return await _run("alphagenome", variants_or_intervals, outputs, ontology_terms, sequence_length, organism)


if __name__ == "__main__":
    server.run()
