"""labhq-annot MCP server (stdio): public-resource lookups with release records (#435 C ② ③).

Variants: Ensembl VEP, gnomAD, ClinVar (`annot.py`). Regions, genes and expression: ChIP-Atlas, ENCODE cCREs, GTEx,
and AlphaGenome predictions when its key file exists (`annot_regulatory.py`).

Env (set by the runner): LABHQ_WORKDIR (where outputs/annotation_queries.jsonl goes), LABHQ_CONFIG (state_dir for the
cache, annot.alphagenome_key_file). The AlphaGenome key is read here from that owner-only file, never from the
command line or the environment, and is kept only inside the backend object.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import httpx

from ..settings import Settings
from . import annot, annot_keys, annot_regulatory
from ._mcpcompat import make_server, tool_failure

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
WORKDIR = Path(os.environ["LABHQ_WORKDIR"]).resolve() if os.environ.get("LABHQ_WORKDIR") else None
CACHE_DIR = S.path(S.runner.state_dir) / "annot_cache"


def _alphagenome() -> annot_regulatory.AlphaGenomeBackend | None:
    """The backend when a key is stored and the official client is installed; otherwise the tool is not offered."""
    key = annot_keys.read_key(S)
    if not key or not annot_regulatory.alphagenome_client_available():
        return None
    return annot_regulatory.AlphaGenomeBackend(key)


ALPHAGENOME = _alphagenome()

server = make_server(
    "labhq-annot",
    instructions=(
        "Look up public databases: vep (Ensembl VEP consequences), gnomad (population allele frequency), clinvar "
        "(clinical classification) for variants; chipatlas (ChIP-Atlas enrichment of public ChIP/ATAC peaks for a gene "
        "list or regions), chipatlas_targets (target genes of a transcription factor), encode (ENCODE candidate "
        "cis-regulatory elements overlapping regions), gtex (GTEx tissue expression and eQTLs for genes or variants)"
        + ("; alphagenome (AlphaGenome predicted effects of variants or signal over intervals)" if ALPHAGENOME else "")
        + ". Give variants as chr:pos:ref:alt (VCF-style alleles), HGVS or rsID, and regions as chr:start-end "
        "(1-based, inclusive). A malformed item comes back as that item's error and the rest are still looked up. "
        "Each result names the database release under `source` and is logged to outputs/annotation_queries.jsonl, "
        "with the full answer under outputs/annotation/; cite those files. An item error is a failed lookup, not a "
        "negative result; found=false means the lookup succeeded and the database has no entry."
    ),
)


# Last request time per service for this server process: each tool call builds a new Annotator, and a fresh pace
# would send its first request without waiting (gnomAD 10/min, NCBI 3/s; PR #449 review).
PACE: dict[str, float] = {}


async def _run(method: str, *args: object, **kwargs: object) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0),
                                 headers={"User-Agent": annot.USER_AGENT}) as client:
        annotator = annot_regulatory.RegulatoryAnnotator(client, WORKDIR, CACHE_DIR, pace=PACE,
                                                         alphagenome=ALPHAGENOME)
        try:
            result = await getattr(annotator, method)(*args, **kwargs)
        except (annot.LookupFailed, ValueError) as exc:
            message = ALPHAGENOME.redact(str(exc)) if ALPHAGENOME else str(exc)
            raise tool_failure(message) from exc
    return json.dumps(result, ensure_ascii=False)


@server.tool()
async def vep(variants: list[str], assembly: Literal["GRCh38", "GRCh37"] = "GRCh38", species: str = "human") -> str:
    """Ensembl VEP consequences (most severe consequence, genes, MANE/canonical transcript HGVS) for variants given
    as chr:pos:ref:alt, HGVS or rsID. Sent in batches of 200 within Ensembl's rate limits."""
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
    usually takes minutes; the call waits up to wait_s seconds (max 1500). If it times out, call again with the same
    inputs and the returned request_id to fetch the result. Do not mix genes and regions in one call."""
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


if ALPHAGENOME is not None:
    @server.tool()
    async def alphagenome(variants_or_intervals: list[str], outputs: list[str] | None = None,
                          ontology_terms: list[str] | None = None,
                          sequence_length: Literal["16KB", "100KB", "500KB", "1MB"] = "1MB",
                          organism: Literal["human", "mouse"] = "human") -> str:
        """Google DeepMind AlphaGenome predictions. A variant chr:pos:ref:alt (hg38/mm10) gets, per track, the
        largest predicted alt-minus-ref change and where it is; an interval chr:start-end gets each track's peak
        signal. outputs: RNA_SEQ (default), ATAC, CAGE, DNASE, CHIP_HISTONE, CHIP_TF, SPLICE_SITES,
        SPLICE_SITE_USAGE, PROCAP. ontology_terms (e.g. UBERON:0002107 for liver) narrows the tracks; leave it empty
        for all. Up to 10 items per call. Model predictions for research, not clinical decisions."""
        return await _run("alphagenome", variants_or_intervals, outputs, ontology_terms, sequence_length, organism)


if __name__ == "__main__":
    server.run()
