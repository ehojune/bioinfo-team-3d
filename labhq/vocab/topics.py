"""Normalize request topics and render their closed vocabulary for planning prompts."""

from __future__ import annotations

from typing import Any

from . import Vocab


# Spellings the #420 survey found for an approved key (docs/reference/topic_candidates_420.tsv, status merge_into).
# A plan that writes one gets the approved key; an alias whose key is not approved stays unknown.
ALIASES = {
    "genome_assembly": "de_novo_genome_assembly", "eukaryotic_genome_assembly": "de_novo_genome_assembly",
    "hic": "hi_c", "multiomics_integration": "multi_omics_integration",
    "pangenome_analysis": "pangenomics", "pangenome_genomics": "pangenomics", "pangenome_graphs": "pangenomics",
    "perturbation_response_prediction": "perturb_seq", "single_cell_multiomics": "single_cell_multiome",
    "structural_variant_analysis": "structural_variant_calling",
}


def normalize(raw: Any, vocab: Vocab) -> tuple[list[str], list[str]]:
    """Return sorted unique topic keys and sorted unknown values without echoing them in warnings."""
    if not isinstance(raw, list):
        return [], ["bad_shape"] if raw is not None else []
    known: set[str] = set()
    unknown: set[str] = set()
    for value in raw:
        if isinstance(value, str) and not vocab.is_key("topic", value):
            value = ALIASES.get(value, value)
        if isinstance(value, str) and vocab.is_key("topic", value):
            known.add(value)
        elif isinstance(value, str) and value:
            unknown.add(value)
        else:
            unknown.add("bad_value")
    return sorted(known), sorted(unknown)


def prompt_rule(vocab: Vocab, *, include_definitions: bool = True) -> str:
    """Render topic choices; research prompts use keys only to stay below CLI argument limits."""
    keys = sorted(vocab.keys("topic"))
    entries = ("; ".join(f"{key} — {vocab.terms[key].definition}" for key in keys)
               if include_definitions else ", ".join(keys))
    return ("\n- Set top-level `topics` to the approved assay/modality or domain keys that describe the request; "
            "use multiple keys when needed and [] when none is known. LabHQ sorts and deduplicates them. "
            "topics do not replace data, format, or operation declarations. Approved topics: " + entries)
