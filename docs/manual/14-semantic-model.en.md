[← Manual contents](../manual.md)

## Semantic model and ontology

The removal proposal [#1388](https://github.com/ehojune/bioinfo-team-3d/issues/1388) is under validation. This describes its retained contracts; it does not announce a release.

| Contract | Behavior after applying the proposal |
|---|---|
| Vocabulary and EDAM | Plans declare local output-type keys. EDAM links are metadata, not proof of input fit. |
| Topic checklists and packs | CP1 freezes required checks and pack definitions; review keeps the frozen evidence. |
| CP1 input fit | `research/input_fit.yaml` supplies fit/mismatch/unknown rules. Known mismatches use the existing bounded plan correction. Missing or changed declarations remain unknown. |
| Step input hint (#1352) | File extensions and headers supply a short prompt hint. Failure omits the hint; it does not block, install software or call another model. |
| Core health | Vocabulary, checklist and pack failures retain public error codes, stage and time on CP1/CP2 and execution records. Exception bodies remain private. |

The proposal removes the live shadow, object view, action layer, A/B reuse advice, semantics CLI and generation of `semantic_report.md/json`. It preserves the offline #136 model and pilot. Neither implementation nor removal proves scientific benefit.

Legacy `semantics` and `research.semantic_report` keys load as inert compatibility input. They do not re-enable anything; other unknown keys still fail validation. Existing historical report files remain readable through the publication copy path once integration is complete.

[Scope, evidence and remaining integration](../reference/prune-semantics-1388.md) · [Configuration](15-config.en.md)
