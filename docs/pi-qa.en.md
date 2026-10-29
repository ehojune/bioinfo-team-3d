# PI Q&A

> English edition of [pi-qa.md](pi-qa.md) (Korean, the source of truth). When the Korean document changes, update this file in the same PR. GitHub issue and PR threads, HANDOFF and `docs/reference/` linked below are in Korean.

A collection of the PI's questions, the answers, and decisions. The current channel is [#883](https://github.com/ehojune/bioinfo-team-3d/issues/883) (from 2026-10-23), and the reporting cadence follows the latest PI handoff. Earlier records are in [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760) (10-19 to 10-23), [#435](https://github.com/ehojune/bioinfo-team-3d/issues/435) (10-06 to 10-19) and [#298](https://github.com/ehojune/bioinfo-team-3d/issues/298) (up to 10-06). Phone access is on hold until [#37](https://github.com/ehojune/bioinfo-team-3d/issues/37) is resolved, so use a PC browser.
What the PI first wanted: run a team like The Virtual Biotech on a cute web interface. So ordinary work is done on the web, and the command line was sent to the [manual](manual.en.md).

## Current operation (2026-10-26)

Staff assignment, delegation and merge judgment, and the current limit and admission, follow the latest PI handoff in #883. The existing cumulative usage, execution contracts and stop conditions are kept. For work order see [HANDOFF](../HANDOFF.md), and for the #732 analysis assignment see [PI decisions](../HANDOFF.md#pi-결정). The [10-20 decision to resume development](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6285136614) and the dated answers below are records of the time.

## What was asked and the answers

### Where do the semantic model, EDAM, ontology, shadow and A/B come into a real request? (10-02)

| Term | Meaning |
|---|---|
| Semantic model | A map tying together the records of past requests. The provenance model answers "who made this result and can it be reused", and the object view answers "who has what now and what is waiting for approval" |
| Ontology, EDAM | The word list used for the map. EDAM is a shared bioinformatics dictionary with names of data kinds and methods and a relation table of "this method takes this kind" |
| Shadow | Only computes and writes down. Plan, prompt and result are the same as when it is off |
| A/B | Splits only research requests in half and gives only one half a list of "past results worth reusing" as a reference for the CSO plan |
| Judgment window | Whichever comes first of 10 research requests or 3 weeks. Then it decides whether to drop, keep or widen |

For example, if you ask for "a paper of about IF 2 from the data of 1000 Koreans":
1. The CSO makes a plan and the PI approves it at CP1. A/B intervenes once just before planning, either giving the list of past results (A) or only computing (B).
2. EDAM, for each step, only counts "does this method take this input" as match, mismatch or unknown, and does not block.
3. The semantic model does not intervene in the analysis and manuscript stages. When it ends, the shadow adds this result to the map.

The original data do not go anywhere. The map keeps only ID, kind, hash and count, and files in controlled zones are not even hashed.
[Original answer](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5953905658) · [Configuration: semantic model shadow](manual.en.md#configuration-reference)

### Does open-science have something to learn from? Is it like Paper2Agent? (10-02)

- A survey issue, [#316](https://github.com/ehojune/bioinfo-team-3d/issues/316), was made. The subject is aipoch/open-science (a local-first desktop AI research workbench, Apache-2.0).
- It looks at both whether it is the same kind as Paper2Agent and whether there are concepts, code or structure to borrow.
- It is on hold for now. The Codex daily limit is used first for dry runs and development.

### When can I try it myself, and how far is real use? (10-03, the lead's summary)

- Trying it yourself: works now. Start with public data.
- Real use with public data, literature and small analyses: nearly there. We only need to fix where the PI's dry runs get stuck.
- Real research with controlled data and HPC: still far. The evidence layer (#58), the rest of the research protocol (#90), a cluster runner and real HPC submission remain.

After this answer, the PI gave the version definitions (v0.25 to v1.25). The current position is in the [roadmap](manual.en.md#roadmap).
[Original answer](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5956890705)

### When running under the same account, how much of the personal files is blocked? (10-03, the lead's explanation after changing ③)

- The following is for Claude staff. Codex staff under the Windows elevated sandbox get two layers, sandbox read denial and instructions ([manual: PI private paths](manual.en.md#pi-private-paths)). The gate that intercepts shell commands exists only for Claude staff.
- Blocked: reading a private path directly, getting back to it by case, 8.3 short names or links, shell reads such as `cat` and `Get-Content`, and relative paths after `cd`.
- Not blocked: a staff member writing a script in the work folder and building the path at run time to read it. The gate looks only at the command text.
- So it blocks mistakes and conspicuous access but not a determined staff member. It is enough for the public-data trial (v0.25), and OS-level separation will be decided again before controlled data (v0.9).

[Original answer](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5958099270) · [PI private paths](manual.en.md#pi-private-paths)

### The README is too long. Can you split it by reader and also give feedback on the roadmap? (10-03)

- The README was split into three: the [README](../README.en.md) (for people trying it for the first time), the [manual](manual.en.md) (for those who want depth), and this document (for the PI). Development rules are in AGENTS.md and HANDOFF.md. Figures were added for the three hard concepts (request flow, approval gate, research lane).
- The roadmap order (PI alone → controlled data → other people → paper) was judged right because it is the order in which risk grows. There are three proposals.
  1. Attach pass criteria to each version. Example: v0.25 is at least 4 of 5 public-data requests reaching a report without developer help.
  2. Split v0.75: v0.75 is public data on HPC, v0.9 is controlled data.
  3. Put "another person installs it and finishes one study with public data" before v1.
- The PI chose ⑥ B (reflect all three), and it was reflected in [PR #347](https://github.com/ehojune/bioinfo-team-3d/pull/347). The stage in 3 became v0.95.
- The PI also asked for a short introduction of the ontology in the README with a figure (10-03). The README has a few lines and one figure, and the detailed explanation is in the manual's [Semantic model and ontology](manual.en.md#semantic-model-and-ontology).
- The "update the README within 3 main commits" rule set on 10-01 now counts editing either the README or the manual. This is because the behavior description moved into the manual.

[Original answer](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5963818111) · [⑥ reflected](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5963989474)

### When a request comes in, is there a literature survey? Does it follow the original study's Methods? (10-04)

- Yes. Before planning, the literature owner (lit_scout), alongside the chief-of-staff briefing, looks at 2–4 recent papers and reviews of the same assay and question type and extracts the required and recommended analyses. The required items become a checklist the plan must answer with a step or an assumption ([PR #395](https://github.com/ehojune/bioinfo-team-3d/pull/395)).
- The original study's Methods are only a reference. The same data can be analyzed along a different branch, so it is not bound to them (PI instruction 10-05).
- Checklists per branch exist for each topic. The proposal adding 17 topics was merged as [PR #402](https://github.com/ehojune/bioinfo-team-3d/pull/402), and there are now 43 topics.

### Does Claude work when Codex hits its limit? (10-04)

- Yes. Separately from the Codex lane, Claude always has at least one issue or PR in progress of its own. If it reaches the Codex limit, it informs and asks in the current PI channel.
- On the afternoon and night of 10-04 and the morning of 10-05 there was a time when Claude only waited. The cause and how it was fixed are written in #298 (the old channel).

### What happens when a Claude staff login expires? (10-05)

- The request does not fail and stops at `waiting_login`. Requests using the same engine share the wait, retry every 600 seconds, and fail after 24 hours ([PR #398](https://github.com/ehojune/bioinfo-team-3d/pull/398)).
- The web card shows the login command for the staff folder. Log in with that command and press **Logged in · retry** and it continues at once.

### If I cannot answer a card right away, does the request fail? (10-08)

- It does not fail. Clarifying-question, staff-question, budget, scope, CP1, CP2, continuation and resume cards wait for 7 days, and meanwhile the request stays stopped as `waiting_pi` (waiting for a PI decision). If you come back and answer the card, it continues.
- After 7 days, the clarifying-question, scope, CP1, CP2 and resume cards end the request as `*_timed_out` (for example `evidence_timed_out`). A budget card ends an ordinary request as `budget_timed_out` and a research request as `research_failed`. A continuation card does not open a new CP1 and ends as `research_review_revise`. A staff question is passed to the staff member as "no answer" and the request continues. Either way it is a different record from a decline, so the report and audit tell them apart. Change the deadline with `policy.approvals.pi_decision_timeout_s`.
- Gate cards such as tool permission still close after `policy.approvals.timeout_s` (1 hour). Only that step is handled as declined.

### If the evidence is insufficient at CP2, how do I have it fixed? (10-08)

- Press **Request revision** and write what to fix in the note (it is not sent without a note). The CSO makes a new plan from that note, and the new CP1 card shows the steps to reuse and the steps that will run again. On approval, only the changed steps and the steps below them run again.
- If you decline the new CP1, the request ends as `evidence_revision_requested` leaving the first result as it is.
- Continuations in one request are up to 2 times counted together with review revise (`research.revise_continuations`). If used up, the button changes to `Request revision (ends the request)` and pressing it ends the request.

### What if I do not like the CP1 plan? (10-09)

- Press **Request revision** on the CP1 card and write what to fix in the note (it is not sent without a note). The current request ends as `plan_revision_requested` without running steps, and a **new request** with the note attached to the same request sentence, reference material and clarifying-question answers starts at once. The new request plans again and raises a new CP1.
- A decline only ends the request as now.
- The CP1 of a continuation round (after a review revise or a CP2 revision request) has no revision request. It is a plan that continues the earlier round's results, so it cannot be moved to a new request.

## Decisions made

The following are the decisions made in #298 (10-02 to 10-06) and #435 (10-06 to 10-19). Current decisions and pending items are in [HANDOFF](../HANDOFF.md), and the earlier decision table is in the [reference record](reference/handoff-history-2026-10-19.md#pi-결정-최근).

| # | Question | PI's answer | Reflected as |
|---|---|---|---|
| ① | Who answers the questions bioinfo-agent stops and asks, and where does a new pipeline go | A, a. Do not ask about a new pipeline; build it through to a bioinfo-agent PR | The CSO answers ordinary questions and only controlled data, cost overruns and installation go to the PI. Automatic PRs are turned on in the PI's config (PR #301) |
| ②-1 | Several labhq on one PC | A | `labhq init --instance` (PR #303) |
| ②-2 | When a subscription limit is hit | A | Limit wait and automatic resume (PR #302) |
| ③ | Whether to run the runner under a dedicated Windows account | A, changed on 10-03 | The same account + private-path blocking is the default (PR #324). A dedicated account is an [optional procedure](runner-account.md) |
| ④ | What fills the semantic-model A/B judgment window | B (real use) | `semantics: {mode: ab, actions: confirm}` in the PI's config |
| ④-1 | Whether to write the work folder as an `internal` zone | 1 | It can read the hashes of reuse candidates |
| ⑤ | The Claude staff config folder (`~/.claude`) | B (dedicated folder) | `~/.labhq/claude-staff` (PR #335). The PI needs to log in once |
| ⑥ | Roadmap adjustments (pass criteria, splitting v0.75, a stage before v1) | B (reflect all three) | Pass criteria per version, split into v0.75, v0.9 and v0.95 (PR #347). [Roadmap](manual.en.md#roadmap) |

Other decisions. The earlier date-ordered list is in the [reference record](reference/handoff-history-2026-10-19.md#pi-결정-최근).

| Date | Decision | Reflected as |
|---|---|---|
| 10-03 | Version definitions v0.25, v0.5, v0.75, v1, v1.25 | [Roadmap](manual.en.md#roadmap) |
| 10-03 | The run account is the same as the PI's + private-path blocking | [PI private paths](manual.en.md#pi-private-paths) |
| 10-01 | The CSO answers a blocked staff member's question first and only risky ones go to the PI. Hence the highest-grade model for the CSO | [Role criteria](manual.en.md#criteria-for-dividing-roles-engines-and-tools) |
| 10-01 | The engineer is Codex. lit_scout is Codex after Gemini was retired | [Staff and tools](manual.en.md#staff-and-tools) |
| 10-01 | The default bench comparison arms (labhq, sonnet-max, sol-ultra, astra-ultra) | [Comparison bench](manual.en.md#comparison-bench) |
