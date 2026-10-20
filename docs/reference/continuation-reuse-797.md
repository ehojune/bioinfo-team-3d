# Continuation reuse contract (#797)

Lookup-only evidence; behavior is summarized in the [manual](../manual.md#리뷰이어-가기).
Related trial issues: #672, #680. This change does not establish their broader scientific outcomes.

| Boundary | Required evidence |
|---|---|
| Node selection | Pydantic-normalized complete step; exact scientific brief, hypotheses, topics, assumptions, protocol and pack values/snapshot. The dispatched context and identity use the same own/ancestor branches; synthesis keeps all branches |
| Inputs | Declared `step:` inputs resolve to recorded dependency artifacts. Every ancestor must be reusable. Request `references`/`project_dirs` reach every step: without content proof they decline reuse, even when omitted from `input_refs`. Dispatch records only input absence; missing legacy proof also declines reuse. Clearing metadata cannot erase earlier unproved inputs |
| Tool/purpose | Complete declared agent/engine configuration at each dispatch, including pinned identity, retries and result repairs. Mixed/unproved execution is declined. The signature versions the scoped prompt contract, refusing older unscoped execution proof. Installed tool versions are not independently measured |
| Artifacts | Bound historical ledger/task/agent identity, required outputs and recorded hashes, then the existing zone/path/link-safe audit walker. Directories are declined; no verified manifest contract is introduced |
| CP1 | Verified candidate/result snapshot bound to exact plan hash. Card receives a copy; approval keeps its snapshot digest |
| Resume/dispatch | Recompute proof and compare the approved seal. Drift invalidates approval and stops; no implicit rerun or extra research request |

Before a new plan is adopted, restart recovers the archived round's already-decided checkpoint and then plans anew;
it cannot bypass the new plan's CP1 seal. Hash proof alone does not cross the budget gate. A denied budget still
restores the prior round/cap; a round already dispatched is preserved when later drift stops further work.
Reapproval retains completed current-round results as verified, sealed candidates with their current-round origin.
Missing input/tool/output proof stops preparation without discarding those records; restoring proof still requires CP1.

Checks are observations at boundaries, not filesystem locks. Independent unchanged nodes may survive unrelated edits;
scientific wording is never matched fuzzily. A newer installed environment cannot retroactively prove an old result.
If no step ran, existing rollback keeps the prior round and records the declined draft/reapproval reason.
Mock/fixture tests establish software contracts only; real research, utility, Windows and supported-version integration remain separate evidence.
