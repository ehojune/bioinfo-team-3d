# Fixed-step tier fixture

`run_fixed_tier_pair` runs `public-protein-qc` twice with isolated state, identical input and rubric, and an analyst step saved before execution. The reasoning arm uses the analyst's declared model; the balanced arm uses the PR A step-tier override.

The fixture currently accepts `engines="mock"` only. Mock answers are canned; scores, costs and model selection are not measured provider outcomes. The tests inspect the dispatched task and resolve it without a model call. Live execution requires verified provider-enforced aggregate control over both arms, including in-flight and unknown costs; per-request limits do not establish it.

For an offline run, call `labhq.bench.run_fixed_tier_pair(output_path, settings, {"reasoning": "model-a", "balanced": "model-b"})` from Python, awaiting the coroutine. The output path must not exist. IDs here are placeholders, not recommendations or claims of availability.
