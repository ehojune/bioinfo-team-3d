# Evaluation suite design (#1383)

English version of [eval-suite.md](eval-suite.md). The Korean file is the source of truth; question texts given to the arms were in Korean and are translated here.

This is the design of the evaluation suite from #1381 section 3. Nothing runs from this document or its fixtures (no model calls, no HPC, no PI instance). Runs happen in separate issues after the PI fills in "What the PI decides" below.

| Test | What it measures | Offline artifacts |
|---|---|---|
| 1 Citation audit | Whether citations exist and quoted text and numbers appear in the source | `scripts/citation_audit.py`, `tests/fixtures/eval_suite/citation_audit/` |
| 2 Review calibration | Whether review catches errors that change conclusions and leaves clean work alone | `tests/fixtures/eval_suite/review_calibration/{clean,seeded,seeded_contrast}` |
| 3 Paired comparison | Whether labhq beats a single agent under the same conditions | none (question candidates only) |
| 4 Falsification response | Whether the decision changes correctly when a falsifying fact arrives | `tests/fixtures/eval_suite/falsification/cases.json` (answer fields empty) |

## 1. Citation audit

**Checks and grades**

| Check | major | minor |
|---|---|---|
| Identifier | Neither DOI nor PMID, lookup finds nothing, or DOI and PMID name different works | No lookup record, so not checked |
| Title | A different work from the registered title (token overlap < 0.8) | A small difference beyond case, punctuation and markup tags (`<i>`, `<sup>`, `&lt;sup&gt;`) (overlap ≥ 0.8) |
| Quote | Not in the source | Source text not in the bundle, or only a pointer to another file in its place (first line `# 포인터:` or `# Pointer:`), so not checked |
| Number | The value is not in the source at the written precision (sign included) | same |

- A citation's grade is its worst check. Any major gives exit code 1.
- Quote and number checks reuse `labhq/evidence/literal_check.py` (normalization, comparison at the written precision).
- **Input:** `{"citations": [{id, doi, pmid, title, source, quotes, numbers}]}`. `source` is a source-text path inside the bundle; nothing outside the bundle is read. Numbers are strings (a JSON number reads `2.970` as `2.97` and loses precision). A malformed entry is major and an input error (exit 2) on the CLI. `1e-5` and `1.2 × 10^-5` in the source are read as one value and compared by significant digits.
- **Lookup records:** the script sends no lookups. `--records` takes `"doi:<value>"`·`"pmid:<value>"` → `{found, title}`. Real records are made in the run issue with one DOI and one PMID lookup per citation and fixed with the date.
- **Limit:** only the presence of a number is checked, not the direction of the sentence ("increase" · "decrease"). Direction checks belong to the report check in #1374.

## 2. Review calibration

**Ten error types** (each seeded bundle's `ERRORS.json` is the key). A reversed contrast (E07) flips every sign and cancels a direction error (E01), so it sits in its own `seeded_contrast` bundle; the other nine are in `seeded`. Each bundle carries lookup records (`records.json`) so citation errors can be found from the bundle alone.

| Type | Definition | Changes the conclusion in this bundle |
|---|---|---|
| direction | Text says increase/decrease opposite to the table sign | yes (E01) |
| table_text_mismatch | A number in the text differs from the table | no (E02) |
| denominator | Numerator or denominator of a ratio or count differs from the table | no (E03) |
| citation | A citation identifier points to another work | no (E04) |
| shared_source | "Independent" evidence comes from the same dataset | no (E05) |
| missing_run | No run record or output for a reported analysis | yes (E06) |
| contrast_reversed | The contrast run is opposite to the text's reading | yes (E07) |
| multiple_testing | Significance called on unadjusted p | no (E08) |
| covariate_dropped | A covariate in the stated design is missing from the fitted model | yes (E09) |
| outlier_unreported | The text does not mention an outlier sample | no (E10) |

- **Conclusion-changing error:** fixing it changes the status of a claim in the report's "Conclusion" section (supported ↔ not supported, or the effect direction flips). Errors in side numbers not used in the conclusion do not count.
- **Detection:** a review finding counts when it points to the same file as the key and names the same kind of problem. Severity labels are not judged.
- **Scoring**
  - Conclusion detection rate = detected conclusion-changing errors / 4
  - Overall detection rate = detected errors / 10
  - False alarms = "must fix" (P1) findings on the clean bundle + P1 findings on the two seeded bundles that are not in the key
- **Arms:** current review / blind draft (reviewer writes first without the conclusion, then compares) / one output check. Each arm gets the three bundles (clean · seeded · seeded_contrast) separately (9 runs).
- **Decision:** adopt only an arm whose conclusion detection rises without more false alarms; on a tie, the cheaper one. REVIEW v2 is considered only if the current review misses conclusion-changing errors (#1381 section 2).

## 3. Paired comparison

**What "the same conditions" means**

| Item | Definition |
|---|---|
| Budget | Same model cost cap ($25 per arm — $50 per question for both arms, PI 11-12) and same run-time cap (4 hours) |
| Inputs | Same question text, same input files, same public data identifiers |
| Tools | Same execution environment and lookup tools. labhq-only CP cards count as PI interventions |
| Records | Failed attempts, contaminated inputs and justified abstentions are all kept |

**Ten fixed questions** (set on 11-12 under PI delegation, [#1383](https://github.com/ehojune/bioinfo-team-3d/issues/1383#issuecomment-6812450099)). Texts were fixed before the first run. Only a question the tools could not reach could be swapped, before the first run, for public data of the same kind, with the reason in #1383.

| # | Question | Kind |
|---|---|---|
| 1 | In GSE52778 (four human airway smooth muscle cell lines), find genes whose expression changes with dexamethasone and report the top genes and their direction. | bulk RNA-seq |
| 2 | In GSE18508 (Drosophila S2 cells), find genes whose expression changes with pasilla knockdown and report the top genes and their direction. | bulk RNA-seq |
| 3 | In the public 10x Genomics PBMC 3k data, separate cell types and report marker genes per type. | scRNA-seq |
| 4 | The #910 round-11 request without the labhq process instructions (below). | literature synthesis |
| 5 | Using public literature, judge whether meta-analyses reporting that metformin lowers cancer incidence agree with each other, and if not, why. | literature synthesis |
| 6 | Look up gnomAD population frequencies and ClinVar classifications for these five variants and judge whether any frequency seems to contradict a pathogenic classification: CFTR p.Phe508del, HBB p.Glu7Val (HbS, rs334), BRCA1 c.68_69del, HFE p.Cys282Tyr, TTR p.Val142Ile. | variant lookup |
| 7 | Look up GTEx tissue expression for ALB, INS, SFTPC, GFAP and GAPDH and judge whether each is tissue-specific. | expression lookup |
| 8 | In LDL cholesterol GWAS, find the lead variant(s) of the PCSK9 region (1p32.3) and evaluate the evidence that PCSK9 is the causal gene of this signal. | GWAS lookup |
| 9 | In DepMap CRISPR data, find the top 10 genes selectively essential in cutaneous melanoma cell lines. Exclude genes essential in all lineages. | screen data |
| 10 | Report ESR1 expression differences across PAM50 subtypes in TCGA breast cancer (BRCA) from public summary data. | public summary data |

Question 4 gives both arms the #910 round-11 request (`req_dc5c365ca9`) without the process instructions that only mean something inside labhq (lead decision 11-12, [#1383](https://github.com/ehojune/bioinfo-team-3d/issues/1383#issuecomment-6812947997)). The scientific question, inputs and required outputs are unchanged. Question 4 is excluded from the comparison, so this does not affect the verdict.

Text given to both arms (translated):

> Using public data only, investigate and verify the evidence for KRAS G12C targeted therapy. Cover the results of sotorasib, adagrasib and olomorasib and acquired and intrinsic resistance, separating NSCLC and colorectal cancer, line of therapy, monotherapy and combination, comparator and follow-up. Record ORR, PFS and OS with each study's denominator, time point, confidence interval and trial design, and do not simply rank different trials. Separate clinical, preclinical and mechanistic hypotheses and look for contrary or null evidence. The provided papers are only starting candidates; first check that papers and DOIs are actually accessible. If you cannot read the full text, state that only the abstract was checked and do not guess methods or QC details. Mark each key claim with DOI/PMID and the page, table or figure in the text, or abstract level, and leave unconfirmed claims as UNVERIFIED. Check the facts in the fixed inputs against the original sources. Do not use institutional logins, raw data, HPC, private repositories or unrestricted web search. Public literature lookups are limited to 20 in total, including failures, retries and redirects.

Removed sentences (from the original):

- Record, for each of P2A/Paper2Skill, the semantic model and shadow, ontology, checklist, glossary, EDAM, pack, input-fit, CP1/CP2, cross-vendor science review and the report devices, whether it was actually used and whether it changed a decision.
- Do not force devices that do not fit the task; mark them not evaluated.
- Put exactly one literature lookup step in the plan, and do public literature lookups only in that step, within the remaining count.
- Choose two methodologically fitting key papers, prepare them as paper skills (Paper2Skill) and assign the steps that use them.
- Choose two whose full text can actually be opened (PMC or other public full text); if the full text cannot be opened, skip that paper and record why.
- Changed clause: "Public literature lookups are limited to 20 in total, including failures, retries and redirects, and lookups used in pre-plan research count too." → "Public literature lookups are limited to 20 in total, including failures, retries and redirects." (pre-plan research is a labhq-only step)

The original text is in the Korean file.

- **Inputs:** downloading is not under test. Questions 1–3 get public count matrices, not raw reads, downloaded in advance and given to both arms as the same files.
- **Tally:** questions 1 and 4 had been run many times before and are tallied separately; the verdict uses the other eight. Questions 2 and 3 are marked if they were run before.
- **Grading notes:** the file with each question's key answer and traps is not in the public repository; only the grader sees it. Its sha256 was fixed in #1383 before the runs.

**Blind grading sheet** (the grader does not know the arm names)

| Item | Score |
|---|---|
| Is the key answer right | 0 wrong · 1 partial · 2 right |
| Do numbers and directions match the raw data | count of wrong items |
| Citation audit majors | section 1 script result |
| Are limits and uncertainty stated correctly | 0 · 1 · 2 |
| Is it reproducible | Does it rerun from scripts and input identifiers (yes/no) |
| Justified abstention | Did it say so when the data cannot answer the question (only when applicable) |

## 4. Falsification response

- Six cases (continue 2 · redesign 2 · abstain 2) are framed in `cases.json`: question, initial hypothesis, fixed next action, the stage where the falsifying fact arrives, case facts (`facts`) and the falsifying content. `facts` are given to every arm together with the falsifying fact, as a separate "case facts" block next to it, never earlier. The facts of F3, F5 and F6 only make sense with the falsifying fact, and giving them earlier would reveal it.
- **Answers are not in the public repository.** `answer` and `answer_rationale` are empty and the answer file is for the grader only. Its sha256 was fixed in #1383 as evidence that it did not change after the runs (PI delegation 11-12).
- Arms: single agent / current labhq / labhq + one reconsideration (using the `alternatives` · `change_signals` fields).
- **Grade by behavior:** judge by what actually changed against the answer file's "must do" and "wrong responses", not by the label the response gives itself ("continue", "redesign").
- **Counts as recovery:** at least one of premise, comparator, data, experiment or decision changed as the answer requires, and the final action matches the answer (continue · redesign · abstain).
- **Counted separately:** in a continue case, passing over the falsifying fact without mention does not count as right. An unnecessary redesign or abstention is counted as "overreaction" (to catch an arm that always redesigns).
- **Not recovery:** only softening the wording ("may", "suggests") with the decision unchanged.
- If, at the same budget, the reconsideration arm does not recover more than current labhq, the reconsideration step is dropped.

## 5. Budget and weekly metric

| Test | Runs | Model cost cap | Time cap | PI interventions |
|---|---|---|---|---|
| Citation audit | 1 per request | $0 | 10 minutes | 0 |
| Review calibration | 9 | $2 per run | 15 minutes per run | 0 |
| Paired comparison | 20 (10 questions × 2) | $25 per arm, $500 total | 4 hours per run | labhq research cards: 3 per run for questions that go the research path, 2 for other questions (PI 11-13). Tool-permission cards are approved and only counted, not capped |
| Falsification response | 18 (6 cases × 3) | $3 per run | 20 minutes per run | 0 (answers written in advance) |

- **Weekly metric:** the denominator is **every attempt** at the week's pre-set requests; the numerator is the attempts that passed independent review within the cost, time and PI-intervention caps. Failed, cancelled and abstained attempts stay in the denominator.

## 6. Order and stopping

- Order: citation audit → review calibration → paired comparison → falsification response. Round 2 (11-12) ran falsification before the paired comparison, because falsification uses synthetic cases and is short while the paired comparison might not finish while the PI was away.
- If two runs in a row fail because of a cap, the paired comparison stops and waits for the PI. Caps are not raised.
- If the default path stops twice in a row on the same question for plumbing rather than science (transport, plan validation, output-check refusal and the like), the paired comparison and falsification response stop and a defect issue is opened. Until then those two tests measure breakage, not judgment.
- A run that exceeds a cap stops there and is recorded as a failed attempt.

## What the PI decided (11-12)

Set by an independent review session under PI delegation ([#1383](https://github.com/ehojune/bioinfo-team-3d/issues/1383#issuecomment-6812450099)). On return the PI checks the six falsification answers (especially F3 and F4) and the budget reading ($50 per question for both arms or per arm).

1. Budget: $50 per question read as the total for both arms, so $25 per arm.
2. Fixed paired-comparison questions: the table in section 3.
3. Falsification answers: a grader-only file. Case facts are the `facts` in `cases.json`.

## 7. Results (11-12 to 11-17)

By PI decision (11-17) the paired comparison closed with questions 1–3, 5 and 9; questions 4, 6, 7, 8 and 10 were not run. The hard task (#1427) ran one HPC question twice with labhq and once with a single agent. Grading records are in [#1383](https://github.com/ehojune/bioinfo-team-3d/issues/1383) and [#1427](https://github.com/ehojune/bioinfo-team-3d/issues/1427); grading notes and answers stay private.

**Paired comparison** (scores: key answer · limits · wrong numbers/directions · citation majors · reproducible)

| # | Configuration | Single agent | labhq team | Verdict |
|---|---|---|---|---|
| 1 | all-Claude staff | 2·2·0·0·yes, $0.60, 9 min | 2·2·0·0·yes, $9.82, 41 min, 43 tool cards | tie (tallied separately) |
| 2 | all-Claude staff | 2·2·0·1·yes, $0.34, 5 min | 2·2·0·0·yes, $6.93, 32 min, 31 tool cards | labhq slightly ahead (one citation major) |
| 3 | all-Claude staff | 2·2·0·0·yes, $0.42, 5 min | first run failed ($10.00, 62 min, handoff refused). Rerun 1·2·2·0·partial, $16.73, 80 min, 1 research card and 88 tool cards (not blind) | single agent ahead |
| 5 | original staff (with Codex) | 2·2·0·0·yes, $3.78, 12 min | no answer: stopped at the research-card cap of 3 ($8.08, 41 min). A continuation beyond the cap also failed ($12.31, 84 min) | single agent ahead |
| 9 | original staff (with Codex) | 1·1·0·no citations·yes, $2.18, 10 min | 2·2·0·0·yes, $14.18, 84 min | labhq ahead |

- On the four questions that count (2, 3, 5, 9) the single agent won 2 and labhq won 2. labhq cost 2–40 times more per question.

**Hard task** (13 items, 29 points, same grading sheet)

| Arm | Score | Cost | Time | Human involvement |
|---|---|---|---|---|
| labhq 11-14 to 15 | 23 | about $66 (HPC request $16.0 + PC request $50.30), plus about $31 in earlier failed or cancelled attempts | HPC stage on the afternoon of 11-14; PC stage 18:24–03:59, about 6 hours of it waiting on staff usage limits | 132 cards (128 tool permissions), 2 revision notes, repeated submits and cancels (#1427 list of run-condition differences) |
| labhq 11-16 rerun (HPC results reused, PC stage only) | 22 | $25.44 | 2 h 14 min | 132 cards (about 120 approved by rule, 14 handled directly), 0 revision notes, 1 second-round plan refused |
| single agent 11-17 (RNA-seq FASTQ reused) | 24 | $10.79 | about 4 h 20 min (restarted once after an external interruption) | no cards, 9 HPC logins |

- In all three arms the numbers behind the three judgments fell within the reference ranges, with 0 numbers off the reference values.
- The differences came from sample and record checks, the handling of binding definitions, citation identifiers and how much was reused. All three quantified only 8 of the 16 RNA-seq runs (1 point for the public-count comparison) and none was rerun for verification (0 for reproducibility).
- One finding outside the grading sheet: the single agent found a donor sex that contradicts the public record ("four male donors"), which the grader confirmed from public counts.

**Limits of this comparison**

- The sample is small: four paired questions that count and one hard task.
- One grader, no cross-grading. Answer length and format hinted at the arm; question 9 was unblinded by a path in the bundle, and the question 3 rerun and the hard task by having only one arm left.
- Configurations differ by question: questions 1–3 used all-Claude staff, 5 and 9 the original staff with Codex. The two arms also ran on different commits.
- In labhq runs a person pressed the cards, and the cap rule was set during the runs (3 research cards, PI 11-13).
- The two later hard-task arms reused raw data or HPC results from the earlier labhq run. They did not start from the same point.
- The person who designed the grading sheet also graded. The hard-task sheet's stated total did not match its item sum (30 vs 29) and was corrected after grading.

**When single mode is enough and why to use the team**

- This evidence does not show that the team is more accurate. Scores were about even, and the team took more cost, time and human involvement.
- Questions with a clear scope — analysis of fixed inputs, reanalysis of one or two public datasets, a literature synthesis on one topic — were handled well enough in single mode.
- The team's value is control and traceability, not accuracy: plan approval before execution (CP1), evidence check and approval (CP2), cost caps, per-step outputs and records, and an independent science review. In these runs the review did catch real defects (a mixed-in ChIP sample, missing key genes). Use the team when the PI must decide midway, when the evidence trail matters as much as the result, or when several HPC stages must run under approval.
- Those same devices were also failure points: the card cap, staff start failures, handoff refusals and a defect that kept the answer out of the bundle stopped runs or hid output. Fixes are tracked in [#1413](https://github.com/ehojune/bioinfo-team-3d/issues/1413).
