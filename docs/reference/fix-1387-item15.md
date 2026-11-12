# #1387 item 15 — planning with recruitment disabled

## Result

On main `82ab20db1a39d5e7a16312e29d45c759992c347c`, OFF and ON send the same recruitment and paper-skill planning rules and schema. This is a planning-contract mismatch; it is not evidence that OFF actually hires or builds a skill.

| Captured plan Task | OFF | ON |
|---|---|---|
| General: `recruit` required, `paper_skills` offered, both rules present | yes | yes |
| Research: `recruit` required, `paper_skills` offered, both rules present | yes | yes |

An in-memory `MiniHub`/`Orchestrator.run_request` mock captured the actual `kind=plan` Task, then returned `TaskResult(ok=False, error="mock stop")`. Both settings were explicitly set for each lane. For OFF, assertions that each of the four items above was absent failed; ON and OFF observations were equal. No model, worker process, HPC, or PI instance was run. HOME and private paths were isolated under the worker scratch directory. The relevant CSO, contract, recruit and fixture files in the checkout were identical to main at the stated SHA. This tests Task construction, not model output or end-to-end behavior.

## Path and impact

- `labhq/orchestrator/cso.py:754-783,809`: general and research prompt constants include both rules unconditionally; the general prompt additionally asks for a hire when the roster lacks a method. `make_plan` at 7774-7823 selects schema and prompt by lane, not by `recruit.enabled`.
- `cso.py:143-176,252-269`: general `PLAN_SCHEMA` requires `recruit`; `replan_schema` inherits it. `labhq/research/contract.py:497,660-681,686` requires it in `ResearchPlan` and exposes it in the engine schema. Both schemas offer `paper_skills`; it is not required in the original schemas.
- `labhq/recruit/planning.py:205-215` marks proposed hires unavailable under OFF and preserves fallback assignments; `labhq/recruit/paper_skills.py:341-352` records selected skills as skipped. Existing `tests/test_recruit_disabled_static.py:181-197` checks those downstream guards. A model may spend planning effort on unusable proposals and receive skip warnings; no effect on scientific results or frequency has been measured.
- The general replan schema also requires `recruit`, although `REPLAN_PROMPT` does not contain the two initial-plan rules. Research continuation uses the research planning template. Saved plans and research CP1 canonical hashes already include plan fields; changing stored models or reserializing old plans risks compatibility.

## Decision and change

[Decision](https://github.com/ehojune/bioinfo-team-3d/issues/1387#issuecomment-6816105231): change instructions only. OFF replaces the general and research hiring and paper-skill rules with a disabled notice and an empty `recruit` instruction; general replan receives the same notice. ON prompts and both schemas remain unchanged. Stored plans, normalization and hashes are unchanged.

Captured mock Tasks compare OFF and ON prompts and schemas in both lanes and general replan. Actual model compliance and operational results remain unverified until the separately authorized baseline run.

The Korean and English contract-staff manuals describe the OFF planning instructions and this verification limit.
