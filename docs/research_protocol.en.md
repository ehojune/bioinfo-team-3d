# labhq research protocol

> English edition of [research_protocol.md](research_protocol.md) (Korean, the source of truth). When the Korean document changes, update this file in the same PR.

Audience: the PI who approves research, and the staff who plan, run and review it. Version `v0.1` (2026-10-01).

> The current implementation is an opt-in pilot. `research.enabled: false` is the default. A research request goes as far as plan validation and CP1 approval, and only when `research.evidence_checkpoint: true` does it run the approved steps under the result-ledger contract (§3) and stop at CP2. After CP2 approval it goes through one review and a report whose claim anchors are checked. The CP2 decision is taken only as one of the choice values approve, request revision or reject. A revision request takes the PI note as the revision request and continues with a new plan and a new CP1, and only the changed steps and the steps below them run again (R10). If the `research.revise_continuations` cap (counted together with review revise) is reached, the request ends like a rejection (`evidence_revision_requested`). Evidence that cites an artifact not tied to collected outputs is rejected. A research request does not enter ordinary replanning. The source verifier runs at CP2 and in `labhq verify` when `research.live_source_check` is on, and it is off by default. CP3 and CP4 are not yet wired into the execution path.

## 1. Intake

| Verdict | Criteria | Handling |
|---|---|---|
| `simple` | A fixed conversion or aggregation, a summary of the original text, organizing what is in a source into a table or list (metadata, sample information), extraction, download. A request that directly names one staff member | The existing direct/orchestrate path is kept |
| `research` | A new conclusion, hypothesis or candidate selection, or a choice of method that changes the answer. If it includes analysis (DE, pathway, survival and so on), inference (prediction, comparison, relationship) or running a computation (run and so on), it is research even if a table or download is also requested | The research-contract PLAN and CP1 apply |
| Ambiguous or out of scope | Hard to assert simple, or the approved scope is unclear | Plan it as research, or wait until the PI confirms |

Length and number of staff are not criteria. The PI can state it explicitly with the API's `work_kind=auto|simple|research`. Naming a staff member directly (`direct`) is simple unless `work_kind=research` is stated explicitly. An explicit `research + direct` is not run, and the PI is told to send it to the team (CSO) again. Even simple work keeps to the rules on sources, failures and data boundaries.

The settings turn on three keys together. With only `enabled` on, the plan is frozen at CP1 and it ends without running steps. A misspelled key is an error at startup. The `research lane` row of `labhq doctor` tells you the current state.

```yaml
research:
  enabled: true
  evidence_checkpoint: true      # run steps after CP1, through CP2, review and report
  active_packs: [bulk_tumor_normal@3, single_cell_de@3]
```

## 2. The frozen PLAN contract

The CSO makes a `PLAN v2` before execution.

| Block | Required content |
|---|---|
| `intake` | Verdict, reason, scope status, explicit/rule/CSO source |
| `brief` | Question, purpose, target, scope, deliverables, observable completion conditions |
| Hypothesis | For explanatory and comparative research: the main hypothesis, null/alternative, observations that distinguish them. For exploratory and descriptive surveys: the exploratory aim |
| `protocol` | Unit of analysis, inclusion/exclusion, comparison groups, main metrics, validation method, resource cap, stop and approval conditions, data boundary |
| Statistics | Whether it applies and why. If it applies, the estimand, independent unit and main outcome are required, and for the test family, multiplicity, missing data and effect size/CI, either decide them or give a reason in `not_applicable` |
| step | phase, claim ID, input ref, output, check, evidence slot, dependency |
| pack | labhq fills in the `id@version` and SHA-256 in `protocol.packs` from the config snapshot. The CSO writes the field values and the validator and acceptance descriptions in `pack_values[pack_key]`. An acceptance key is a pack rule id |

An item that does not apply keeps `not_applicable` and a reason. If the step cap is exceeded, it replans instead of cutting off the end. The schema, DAG, staff IDs and pack snapshot must match before it goes to CP1.

The canonical JSON of the whole normalized PLAN is used both as the SHA-256 input and on the CP1 detail screen. The decision card shows the question, hypothesis, protocol, completion and stop conditions, resources, data boundary and pack values separately, and the original hash input is preserved too. If the question, method, target, metrics, selection criteria, scope, resources or packs change after approval, the receipt is marked `needs_reapproval`.

Each research-step prompt carries the frozen question, scope, hypothesis (main hypothesis, null/alternative, distinguishing observations), protocol and pack values as they are. A step uses these sentences verbatim when judging or naming a hypothesis and does not rename or merge them (web dry run 10-08: the hypothesis was missing, so s9 redefined A1 and A2). A continuation does not reuse a step if any one of this bundle changed.

## 3. Staff result contract

Research staff return `result v2` in future execution steps. The ledger is `labhq/evidence/claims.py` and the source check is `labhq/evidence/verify.py`, using the same ledger as the #58 evidence layer.

| Field | Meaning |
|---|---|
| `plan_sha256`, `step_id` | Which step of which frozen plan. Rejected if different from the frozen plan. A claim outside the `claim_ids` that step declared is also rejected |
| `claims` | One claim per line. `kind` (finding/inference/hypothesis), `status`, importance, `status_reason`, limits, `revision` and `supersedes`, and quantitative `comparisons` |
| `evidence` | Rows of the 6 kinds below. Observation, lookup status, source, assessment, `quantities` |
| `links` | A claim revision and evidence, `supports/contradicts/context`, `rationale` |
| `artifact_refs` | Logical artifact ID and path |
| `not_established` | What this work did not establish |
| `failures` | Search, tool and analysis failures. Distinguished from zero results |
| `method_changes` | Planned and actual values, the reason, and whether it affects the conclusion |

In an evidence row's `slots`, write the ID of the step's evidence slot that the row filled. Slot IDs cannot overlap within one step (if they do, the PLAN is rejected before CP1, #187). Each required slot needs at least one filled row, and an undeclared slot is rejected. Even if a lookup failed or returned zero results, the slot is written on that row. This is because that it was tried and came back empty must show on CP2 as it is. An inference or hypothesis row cannot fill a slot.

The evidence kinds are `observation`, `database_annotation`, `experimental`, `literature_claim`, `inference` and `hypothesis`. Only the first four count as evidence. `inference` and `hypothesis` give `derived_from` and are linked only as `context`.

| What the code rejects | Rule |
|---|---|
| A reference to a nonexistent claim, evidence or artifact, a duplicate ID, a link that points to an old claim revision | R04 |
| `supported`, `partially_supported` or `contradicted` with no observed-evidence link. `supported` while there is opposing evidence | R04 |
| Supporting or refuting with an inference or hypothesis, or with an `unavailable`, `not_found` or `failed` row | R04 |
| An inference or hypothesis row that, following `derived_from`, never reaches an observation or lookup row (including cycles) | R04 |
| A search row with `result_count` 0 but `observed`, or `not_found` with 1 or more. A zero-result search is `not_found` and is not evidence | R04 |
| An evidence row missing `directness`, `source_level`, `independence_group` or `assessment_reason`, or a link missing `rationale` or a claim missing `status_reason` | R05 |
| The same source written with different independence groups (counting a re-citation as independent evidence). An ID, the registry URL of that ID (doi.org, identifiers.org, PubMed, PMC, GEO, NCBI, UniProt, RCSB, ChEMBL, Ensembl), and an artifact pointing to the same file are all treated as the same source | R05 |
| An external source missing `accessed_at` (YYYY-MM-DD, including inference and hypothesis rows), an observation row missing `locator`, a zero-result row missing `query`, or a failed row missing `status_detail` | R06 |
| A quantity missing `value`, `unit`, `conditions` or `denominator`. If unknown, write the item and its effect on the conclusion in `unknown` | R07 |
| Comparing as `comparable` values whose unit, conditions or stated `method` differ or are unknown. Mark them `not_comparable`, or use `comparable_with_assumptions` with the assumptions written | R07 |

Confidence is written as reasons and limits rather than probability. Structure, correlation and prediction are not extended to causation, affinity or efficacy. Controlled-access raw data are not sent outside the permitted environment, into LLM input, or to external logs.

## 4. Evidence, audit and change

- A claim is `finding|inference|hypothesis`, and its status is `proposed|supported|partially_supported|contradicted|unresolved|withdrawn`.
- Evidence is looked at in the order fit to the question → directness → quality and bias → independence and re-verification. A re-citation is not independent evidence.
- ID resolution and sentence support are separate checks. `unavailable`, a `not_found` from a successful limited search, and a tool `failed` are none of them proof of absence.
- The verifier keeps the lookup result (`succeeded/failed/skipped`) and the ID status separately.

| ID status | Meaning | Claim verdict |
|---|---|---|
| `found` | Exactly one record matches the cited ID and version | Evidence confirmed. Whether it supports the sentence is for the reviewer |
| `not_found` | The lookup finished and there is no record | Defect |
| `insufficient` | The ID format is wrong, so no lookup. It is not guessed and fixed | Defect |
| `conflicting` | A record of a different scheme, ID or version, several records, or an artifact hash that changed after citation | Defect |
| `requires_verification` | Network error, timeout, resolver error, live turned off, unsupported scheme, no manifest, or a record whose base accession is the same but one side lacks the version or isoform | Unconfirmed. Neither a defect nor an absence |
| `retracted_by` | A retraction notice retracts the cited original | A claim that used that evidence is a defect |
| `is_retraction_notice` | The cited DOI itself is a retraction notice | Marked as a notice. Not mistaken for the retracted original |
| `corrected_by`, `concern_raised_by` | The original has a correction or an expression of concern | Warning |

A claim is `verified` only if every source linked as supporting or refuting is `found`. If any one is unconfirmed, it is `unverified`. If a source gives both an ID and a URI, it also checks that the URI points to that ID. If a registry URL points to a different accession of the same scheme, the code treats it at once as `conflicting`. For schemes that write one record in several forms (Ensembl and RefSeq versions, UniProt isoforms, ClinVar VCV), it compares by the base accession with the version, isoform and VCV padding stripped. If the base differs, or the base is the same but the version or isoform written on both sides differs (`ENSG…17` and `ENSG…16`), it is `conflicting`. If one side lacks the version or isoform, the code cannot know whether it is the same record and passes it to the resolver (#167). A URL of a different scheme (a PubMed URL next to a DOI), like other URLs, is compared when the resolver tells the ID through a URI lookup. A record the resolver returns is also compared with the same function, and if different it is `conflicting`, and if only the base is the same or it cannot be compared, unconfirmed. If an ID lookup response brings, together with the cited form, other forms of the same base, it does not pick one and leaves `conflicting`. Even on a registry path, a search or help page (`/search`, `/docs`) that is not in accession form is an ordinary URI. A source with only a URI is also looked up by that ID if it is a registry URL. If a source gives both an ID and an artifact, both are checked, and `version` is taken as the version of the external record (for an artifact alone, the cited sha256). Sources of context rows, unlinked rows, inference rows and zero-result (`not_found`) rows are also checked, and if there is even one defect, the whole report (`ok`) does not pass. The source of a zero-result row is where it was searched (DB, dataset), and the ID being looked for goes in `query`. A `failed` or `unavailable` row did not reach the source and is not checked. For a URI, only the scheme and host ignore case. The ledger sees re-citation only from the notation written on the rows, so a DOI row and the PMID row of the same paper are treated as different sources. When the resolver tells IDs of another scheme (DOI↔PMID↔PMCID) through `same_as` of the record it found, the verifier looks again with that correspondence included, and a row written with a different independence group is left in `recitations` and the whole report does not pass (#168).

Lookups run at most `concurrency` (default 4) at a time, each lookup finishes within `research.live_source_timeout_s` (default 10 s), and the whole report within `research.live_source_deadline_s` (default 60 s). A source that exceeds the deadline is `failed/timeout`, so unconfirmed, and is not written as absent (`not_found`). With `research.live_source_check` on, it looks up Crossref, doi.org and NCBI, and the default is off. Tests and bench fixed responses use `StaticResolver`. Artifact evidence becomes `found` only when there is a manifest the runner observed.
- A major claim carries opposing evidence, alternative explanations and falsifying observations. A critic finds defects and the original owner fixes them.
- Inputs, search expressions, commands, code and environment, seed, exit and output hashes are recorded. `documented` is a state where there is a record but replay conditions are missing, `replayable` is a state that meets the output, script, environment and relative-path conditions, and `rerun_verified` is a state that also has a valid separate rerun receipt.
- If an evidence or method revision changes, dependent claims, analyses, audits and approvals are marked stale. Old results are kept only as history.

A reviewer other than the author audits, per claim, the ID, citation support, conditions, statistics, counterexamples, overstatement and reproduction scope. The reviewer's identity and the actual model/vendor are recorded. If a major defect remains, it cannot be accepted.

Research-result review is received as REVIEW v2 (`labhq/research/review.py`, the first part of R13). For each claim it judges citation support, use of absence as evidence, directness, independence and comparability. What the code cannot read, such as a zero-result search that the observation sentence merely writes as "0 hits", is for the reviewer to see. Using absence as evidence is always a major defect. A claim that carries countable evidence cannot pass the first four items as `not_applicable`, and a claim that compared quantities cannot pass comparability as `not_applicable`. The review is tied to all of the result's plan hash, steps and claims, and the staff member who wrote the result cannot review it. A result has no author field, so the author (the task's agent id) must be passed when verifying, and if missing, it is treated as a review that did not check independence and is rejected (#169, #188). It is not yet wired into the execution path, and the `REVIEW_SCHEMA` of ordinary requests is unchanged.

## 5. PI checkpoints and completion

| gate | When it stops | What the PI sees |
|---|---|---|
| CP1 plan | Before research execution | Question and hypothesis, plan hash, success/stop criteria, cost and data boundary, the execution cap after approval (`budget_usd`), approve, request revision (a note is required; a new request plans again from the earlier plan and the note; only on the first CP1 before steps run) or decline |
| CP2 evidence | After the research steps run, before review and report | A table of status and evidence kind per claim, rejected evidence and claims that lost evidence, and the choice approve, request revision (a note is required; continues with a new CP1) or reject |
| CP3 selection | When choosing a candidate or method | Common criteria, alternatives and sensitivity, the minimal discriminating experiment and cost |
| CP4 acceptance | After audit and the final draft (`final_checkpoint` opt-in) | On the existing review-done card, accept or reject the draft revision and the claim, audit and report hashes. Until the receipt, it is `review_pending` |

The receipt records the gate, request, target revision/hash, decider, time, decision and delegation scope. Answering a question, staff consultation, resume approval and budget, HPC or transfer approval do not stand in for research approval.

A card note is not a decision. CP2 reads only the structured choice value. The note is left in the receipt and the audit appendix, and also goes up to 2,000 characters into the prompt of whoever does the next work: a CP1 note to that plan's steps, a CP2 note to the review and report, and a continuation note and the immediately preceding CP2 note to the new plan (R11). The note of a CP2 revision request is not a note but a revision request. It goes into the new plan and that round's review prompt as a revision request (R10). The optional PLAN field `budget_usd` (positive) is the amount execution will spend after approval, and at CP1 approval it is added to the cost spent so far (`detail.budget.spent_before_plan_usd`) to become the request's execution cap. The cap does not exceed the configured cap (the request budget, or `policy.budget.per_request_usd` if none). A continuation round counts with the cost spent up to that round's CP1. Right after approval the cumulative cost is judged against this cap, and the step and review prompts state this cap directly. A true/false value is a validation error. A PLAN without this field does not put it in the canonical JSON, so a plan hash frozen earlier is unchanged (R17).

If the required work, audits, approvals and prior criteria are met, it is `done`; if a broken reference, a stale result or a major defect remains, `incomplete`; and an execution error is `failed`. If the plan also allowed insufficient evidence, `done + inconclusive` is possible. The completion of `plan_only` is an approved plan and not completion of a research result.

## 6. Domain rule packs

A pack does not weaken the core contract and only adds fields, validators, review questions, fixtures and machine-judged `rules`. The loader hashes the content and rejects conflicting requirements of the same validator or rule ID and unknown fields or operators. The cells of `allowed_combinations` must pass the same checks as a PLAN answer (type, `allowed_values`, `minimum`, `pattern`). A row no answer can satisfy is rejected at load. A blank (`null`) also has no answer that fits and is rejected. A cell for a core PLAN field (`brief.study_type` and so on) must fit the PLAN schema type (choices, int, bool, list).

```yaml
rules:
  - id: single_cell_de.confounded_conclusion_mode
    when: {field: batch_design, value: fully_confounded_not_identifiable}
    require: {field: conclusion_mode, value: descriptive_only}
    description: A fully confounded design is limited to descriptive reporting.
```

A predicate uses exactly one of `field` with `value`, `in`, `not_in`, `present` and `min_items`. `present` treats a list holding only empty strings as having no value, and `min_items: N` passes only a list with at least N distinct non-blank items (for example two comparison groups). A rule has an optional `when` and exactly one of `require` or `forbid`. If `when` is given a list of predicates, the rule applies only when all match. A field is a field the pack declared or an allowed PLAN scalar (`intake.*`, the basic scalars of `brief`, `protocol.revision|analysis_unit`, the scalars of `protocol.statistics`, `notes`).

Only the active packs whose `applies_when` matches put field values and validator and acceptance descriptions in the PLAN's `pack_values[pack_key]`. An acceptance string is only a description and is not a basis for passing. Before freezing, it checks required fields, types, allowed values, minimums and patterns and runs every rule. If any fails, CP1 is not opened and it replans once. The correction prompt carries all schema and pack problems at once. If it still fails after correction, the request ends as `plan_invalid` and the remaining problems are left in the report and `plan_validation` (#222).

The examples are `single_cell_de@3` and `bulk_tumor_normal@3`. The former fixes donor, scale and model, and the latter fixes, before CP1, the pairing, low-expression filter, DE criteria, positive-control direction and PMID of a bulk two-condition design. `pairing_evidence` of `@3` is either metadata sources (`geo_characteristics`, `sample_title`, `source_name`, `biosample_attributes`, `supplementary_table`, `publication_methods`, `local_metadata`) joined with `;` or `none`, and if pairing is `partial` or `complete`, `none` cannot be used. The config example is `active_packs: [single_cell_de@3, bulk_tumor_normal@3]`, and both packs apply by request topic. Existing approved requests of `bulk_tumor_normal@1`, `@2` and `single_cell_de@2` resume with the stored version and hash.

| `model` | `model_family` | Allowed `count_scale` |
|---|---|---|
| `pseudobulk` | all | `raw_counts` |
| `donor_dependent` | `negative_binomial`, `poisson` | `raw_counts` |
| `donor_dependent` | `linear` | `log_transformed` |

The allowed values of `count_scale` are `raw_counts` and `log_transformed`. `normalized_counts` was removed from `single_cell_de@2`. No combination accepted it, so it passed the field check and was blocked only at the rule (#114). Now it is refused at the field stage with a single reason.

If you change pack content, raise the version. An approved plan points to a pack by `id@version` and hash, so if the content of the same version changes, it can no longer be known what that plan approved. The built-in pack hashes are fixed in `tests/test_research_protocol.py`. If you write a version in the config that no longer exists, the gateway stops at startup and also tells the versions that remain (#170).

`conclusion_mode: condition_effect` requires an applied statistics plan. `descriptive_only` forbids, even without batch confounding, an explanatory or comparative study type, a main hypothesis, inferential statistics and an estimand. An explanatory sentence such as `model_rationale` is not used for judgment.

This protocol did not take wording, tables or templates from the inco repository, whose license has not been confirmed. Only the structure of several research workflows was referred to for comparison, and the labhq contract and wording were written anew.
