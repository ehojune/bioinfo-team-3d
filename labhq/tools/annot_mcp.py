"""labhq-annot MCP server (stdio): variant lookups in Ensembl VEP, gnomAD and ClinVar with release records (#435 C ②).

Env (set by the runner): LABHQ_WORKDIR (where outputs/annotation_queries.jsonl goes), LABHQ_CONFIG (state_dir for the
cache). The lookups themselves are in `annot.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import httpx

from ..settings import Settings
from . import annot
from ._mcpcompat import make_server, tool_failure

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
WORKDIR = Path(os.environ["LABHQ_WORKDIR"]).resolve() if os.environ.get("LABHQ_WORKDIR") else None
CACHE_DIR = S.path(S.runner.state_dir) / "annot_cache"

server = make_server(
    "labhq-annot",
    instructions=(
        "Look up variants in public databases: vep (Ensembl VEP consequences), gnomad (population allele "
        "frequency), clinvar (clinical classification). Give variants as chr:pos:ref:alt (VCF-style alleles), HGVS "
        "or rsID; a malformed item comes back as that item's error and the rest are still looked up. Each result "
        "names the database release under `source` and is logged to outputs/annotation_queries.jsonl, with the "
        "full answer under outputs/annotation/; cite those files. An item error is a failed lookup, not a negative "
        "result; found=false means the lookup succeeded and the database has no entry."
    ),
)


# Last request time per service for this server process: each tool call builds a new Annotator, and a fresh pace
# would send its first request without waiting (gnomAD 10/min, NCBI 3/s; PR #449 review).
PACE: dict[str, float] = {}


async def _run(method: str, *args: object) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0),
                                 headers={"User-Agent": annot.USER_AGENT}) as client:
        annotator = annot.Annotator(client, WORKDIR, CACHE_DIR, pace=PACE)
        try:
            result = await getattr(annotator, method)(*args)
        except (annot.LookupFailed, ValueError) as exc:
            raise tool_failure(str(exc)) from exc
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


if __name__ == "__main__":
    server.run()
