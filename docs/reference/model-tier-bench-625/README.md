# Fixed-step tier fixture

`run_fixed_tier_pair` runs `public-protein-qc` twice with isolated state, identical input and rubric, and an analyst step saved before execution. The reasoning arm uses the analyst's declared model; the balanced arm uses the PR A step-tier override.

The fixture currently accepts `engines="mock"` only. Mock answers are canned; scores, costs and model selection are not measured provider outcomes. The tests inspect the dispatched task and resolve it without a model call. Live execution requires verified provider-enforced aggregate control over both arms, including in-flight and unknown costs; per-request limits do not establish it.

For an offline run (the output directory must not exist):

```python
import time
from labhq.bench import MockTierCostControl, run_fixed_tier_pair

control = MockTierCostControl(10, valid_until=time.monotonic() + 300, verified=True)
result = await run_fixed_tier_pair(
    output_path, settings, {"reasoning": "model-a", "balanced": "model-b"},
    cost_control=control, worst_case_units={"reasoning": 4, "balanced": 6},
)
```

IDs and integer currency units above are synthetic. Reuse **the same control instance** across pairs and retries. Bounds cover each entire arm, including internal attempts. Missing control/bounds, invalid amounts, stale or unverified control, and aggregate over-limit admission reject before dispatch. Both arms reserve atomically: `settled + outstanding + new reservations <= limit`.

- A reservation dispatches once. Timeout, cancellation, missing receipts and unfinished arms retain their full reservation; retry needs new capacity. Expiry blocks dispatch, not late settlement.
- Only a simulated verified final receipt settles: `control.settle(reservation_id, units, verified=True, final=True)`. IDs are saved before dispatch in `cost-reservations.json`; result rows also carry them. Duplicate identical receipts do nothing; contradictory or above-bound receipts block further dispatch. Partial, unknown, foreign and unverified receipts cannot release capacity.
- Injected mock run results may carry `mock_cost_receipt={"units": 2, "verified": True, "final": True}`. Normal canned results have no such receipt, so even their reported zero dollars leave reservations outstanding. `pair.json` includes a control snapshot.
- This ledger is in memory, with no restart recovery or cross-process coordination. Creating another instance does not preserve unresolved spend. The flags simulate verification; they do not authenticate provider evidence.

Validation: missing-control dispatch reproduced before the change; 33 new admission/receipt cases and all 110 `tests/test_bench.py` tests pass locally, including competing admissions and interrupted retries.

Live prerequisite: an independently verifiable provider/account spending boundary covering both arms, every client, retries and in-flight charges, with a hard maximum charge and final usage receipts. Unknown bound or unresolved enforcement remains a stop. No supported provider implementation has been verified here; a local ledger, per-call limit, delayed billing dashboard or mock acknowledgement cannot establish that boundary. Live stays disabled and PR #1188 stays draft/hold.
