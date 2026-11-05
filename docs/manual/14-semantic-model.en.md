[← Manual index](../manual.en.md)

## Semantic model and ontology

If you declare the kind of a step's outputs, before plan approval (CP1) it checks known mismatches between input and method and asks the CSO to revise the plan. Items with no declaration are left as "unknown". The device report separates the evidence of checklists, declarations, fixed packs and reuse from items not yet measured. There is no evidence yet that research results improved.

![Semantic model and ontology: shadow computation ties finished requests into a provenance model and an object view, and counts whether a step's input fits by output-kind labels (labhq vocabulary, EDAM). A/B gives reuse candidates only to half of research requests](../media/semantics-model.svg)

Input validation at plan time and the shadow computation after a request ends are separate. Input validation stays even if the shadow is turned off. The shadow is used to measure effect by recording the provenance of past results and reuse candidates.

| Term | Meaning |
|---|---|
| Semantic model | A map tying together the records of finished requests. The provenance model (#136) answers "who made this result and can it be reused", and the object view answers "who took what and what is waiting for approval" |
| Ontology | The word list used for the map. The labhq vocabulary (`labhq/vocab/output_types.yaml`) has data 30, format 28, operation 22, topic 45, 125 keys in total (PI review 2026-10-04, 23 topics on 2026-10-06 #420). The branch cap is data 36, format 32, operation 24, and topic has no cap (PI 2026-10-05). Only topic names go into the plan prompt, and aliases from the survey (`hic` → `hi_c` and so on) are turned into approved keys |
| EDAM | A shared bioinformatics ontology. 79 of the 125 keys link to 75 EDAM terms (`edam_subset.yaml`). Judgment is by local key, and the EDAM ID is attached only as metadata |
| Data-type declaration | The CSO writes a kind key on a step's outputs (`plan.declare_output_types`, off by default). Without a declaration, every judgment below is "unknown" |
| Input–method fit | Matches a step input's declared kind against the local relation table (`labhq/research/input_fit.yaml`). Known mismatches are corrected in the existing pre-CP1 validation (first plan + 3 corrections), and the shadow only observes counts |
| Shadow | `semantics: shadow`. After a request ends the gateway computes separately and leaves only ID, kind, hash and count in `state_dir/semantics/`. Planning, approval, execution and the web screen are the same as when it is off |
| A/B and judgment window | `semantics: ab`. Splits research requests in half by ID hash and gives reuse candidates (artifact ID, data type, ID of the request that made it) to only one side (advisory) as a reference for the CSO plan. At whichever comes first of 10 requests or 3 weeks, it decides whether to drop, keep or widen |

In a single research request it intervenes as follows.

1. A/B intervenes once after seeing the CSO's first plan draft. The result is left as `OK · N candidates`, `OK but 0 candidates`, `not evaluated (reason)` or `error (code)`. If there are candidates, both sides plan once more, and only the advisory side receives the list.
2. It does not intervene in step execution, review or the report.
3. When the request ends, the shadow counts the input–method fit per step and adds this result to the map.

Original data, paths and the request body do not go into the record, and files in controlled zones are not even hashed. But the request ID and hash remain, so do not put `state_dir` in a public location.
How to view: `labhq semantics report` is the overall aggregate, and `labhq semantics report --request req_...` is the comparison for one request (`--json` supported). For the values to turn it on and off and the automatic-stop conditions see [Configuration reference](15-config.en.md#configuration-reference), and for limits see [Known limits](17-known-limits.en.md#known-limits).

Only a request that has a research contract records, in `semantic_report.md` and `semantic_report.json`, the judgments and effects of the vocabulary and EDAM, checklist, analysis pack, semantics, operational ontology and shadow request. You can open the actual Markdown on the CP2 card, and the end bundle freezes the checklist at run time and the completed shadow observations. The summary path is `run/semantic_report.md`, and it can be turned off with `research.semantic_report: false`. #796: the CP1 mainline evaluator (`research/input_fit.py`) is independent of Shadow off and removal and uses the same relation table and semantic hash as the observer adapter. It corrects known input-kind–operation mismatches in the existing two plan validations and leaves the before-and-after warnings on the card. An unknown declaration is unverified, and an EDAM mapping itself is not fit evidence. The same report and the bench comparison show checklist initial omission or correction and review verdict, declaration and fit, fixed pack values, and reuse candidates and plan adoption, with denominators and basis. Independent criteria met, actual use and effect on scientific performance are written as they are when not measured. Passing the removal check is not evidence of scientific utility. Shadow observation is kept for measuring utility but the actions and Shadow add-on extensions are frozen, and disabling and cleanup are considered after the utility judgment. It does not delete the observer or run the removal script now.

The per-request view shows in plain language the stored shadow's input–method verdicts, output kinds, reuse candidates, warnings and action judgments, and the actual steps, reported outputs and approval decisions. If there is no record, it says so and does not fill in by recomputation. A per-input-kind list does not exist in earlier shadow records, so it is shown as unverifiable. It does not verify file contents.

Ordinary and solo requests also record at the end in `shadow` and `ab`. Only research requests receive the A/B plan reference, and a request resumed after a restart and finished uses the same end path. The epoch changes only when newly opened with `semantics enable`. If a gateway started with a different config file uses the same state folder, the per-request view reports the mismatch and `labhq up` does not treat it as a success. It does not assert a past config from the current config or PID record alone.

For a research request, before CP1 it checks the semantics state, epoch and last record, and the hashes and counts of the vocabulary, EDAM, checklists and packs. The CP1 and CP2 cards and the per-request report show off, not evaluated, error and OK with 0 separately. Not-evaluated and error requests are removed from the A/B denominator and counted separately. An error stores only `code·device·stage·at·message`, and a violation of the information boundary still stops it automatically.

The report checks the structure and counts (integers 0 or more) of nested aggregates. A damaged request row is excluded whole and counted in `broken_lines` of the JSON and `broken lines` in the text. The absence of an optional field from an earlier version and the `declarations: null` a worker left are allowed, and fractional delay values are preserved. The stored log is not changed.
