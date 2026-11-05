[← Manual index](../manual.en.md)

## Comparison bench

Call the fixed-step tier comparison fixture from Python as follows. `settings` is a LabHQ `Settings` instance and the output path must not exist. Only `engines="mock"` is accepted; live execution is disabled.

```python
import time
from labhq.bench import MockTierCostControl, run_fixed_tier_pair

control = MockTierCostControl(10, valid_until=time.monotonic() + 300, verified=True)
result = await run_fixed_tier_pair(
    output_path, settings, {"reasoning": "model-a", "balanced": "model-b"},
    cost_control=control, worst_case_units={"reasoning": 4, "balanced": 6},
)
```

Model IDs and integer cost units are synthetic. Each bound must cover the entire arm, including retries; reuse the same control instance across pairs and retries. This mock admission does not establish a real provider spending cap, and mock scores are not measurements of model quality or cost. See the [tier comparison fixture](../reference/model-tier-bench-625/README.md).

The default comparison arms per the PI decision (2026-10-01, #40). A real run uses the PI machine's subscriptions.

| arm | model · effort |
|---|---|
| `labhq` | Claude staff with `opus=sonnet` substituted, Codex staff as configured |
| `sonnet-max` | `claude -p --model sonnet --effort max` (the Claude CLI's highest effort) |
| `sol-ultra` | `codex exec -m gpt-5.6-sol -c model_reasoning_effort="ultra"` |
| `astra-ultra` | `codex exec -m gpt-6-astra -c model_reasoning_effort="ultra"` |

```bash
labhq bench list
labhq bench run inco-kras-g12c --dry-run
labhq bench run inco-kras-g12c --arms labhq,sonnet-max,sol-ultra,astra-ultra --output ~/.labhq/bench
labhq bench run inco-kras-g12c --arms labhq --staff-model opus=sonnet
labhq bench run inco-kras-g12c --arms sol-ultra,astra-ultra
labhq bench report inco-kras-g12c
labhq bench rescore inco-kras-g12c --all  # rescore with the current checks; --run-id ID, or the latest run if omitted; the earlier verdict is kept in score.json
labhq bench test-agent --arms labhq,sonnet-max --engines mock
```

Set the per-arm `engine` (`claude_code`/`codex`), `model` and `effort` in `bench.arms`.
Specifying this mapping replaces the default baseline. Opus can also be added as a separate arm.
The default of `bench.staff_model` is `{opus: sonnet}`, and `{}` turns the substitution off. `--staff-model FROM=TO` can be repeated.
The original staff YAML is preserved, and the result table records the model and effort at run time.

Results accumulate in `case-id/<run-id>/<arm>/`. `run` and `report` merge the latest result of each arm into
`case-id/comparison.md` and `comparison.json`. A per-run comparison table and `test-agent-summary.*` are also left.
Real and mock are gathered separately, and a mock report is seen with `report --engines mock`.
The default folder is `$LABHQ_BENCH_DIR` or `~/.labhq/bench`.

The fixed reference material and initial prompt in `labhq/bench_data/cases/` are the same for all arms. The material and checkers are also included in the wheel. Scripted PI answers are given only to LabHQ questions.
Each arm writes the same per-case JSON result block at the end of its report. LabHQ status and cost phrases go before this block. Scoring is based on those values, and format failures and wrong values are counted separately. The sentence check is a supplementary item that looks at the basis and limits statements.
A non-interactive baseline's inability to detect a question or lack of an answer is shown in the table. Personal settings are isolated, and it refuses if a Codex global `AGENTS.md` exists.
The Claude baseline allows its own arm's file writes and simple commands. See [permission scope and Windows constraints](../reference/bench-permissions.md) and [calibration of 15 real answers](../../bench/calibration.md) (Korean).
Every arm uses the case's `timeout_s` (or `runner.task_timeout_s` if absent). The baseline ends the CLI process tree on timeout or cancellation.
Exceeding the budget is a refusal by default. If you specify `--approve-budget-up-to 3` for `run` or `test-agent` in an experiment, it approves only when labhq's total request budget is within 3 times the case budget.
The table records the number of budget approvals and the final cost / original budget multiple. Even if approved, exceeding the original budget is FAIL, and this is not an option that limits the cost of parallel steps already running. If cost-unknown items are mixed in, the cap is also `cost unknown` (test-agent does not pass), and if the confirmed part alone exceeds it, FAIL. A Codex arm uses an estimated cost if the model is in the price table.
