[← 매뉴얼 목차](../manual.md)

## 비교 bench

고정 단계 등급 비교 fixture는 Python에서 아래처럼 호출합니다. `settings`는 LabHQ `Settings`이며 출력 경로는 없어야 합니다. `engines="mock"`만 허용하고 live 실행은 차단합니다.

```python
import time
from labhq.bench import MockTierCostControl, run_fixed_tier_pair

control = MockTierCostControl(10, valid_until=time.monotonic() + 300, verified=True)
result = await run_fixed_tier_pair(
    output_path, settings, {"reasoning": "model-a", "balanced": "model-b"},
    cost_control=control, worst_case_units={"reasoning": 4, "balanced": 6},
)
```

모델 ID와 정수 비용 단위는 가상 값입니다. 각 상한은 재시도를 포함한 arm 전체를 덮어야 하며 여러 pair·재시도에는 같은 control 인스턴스를 재사용합니다. 이 mock admission은 실제 provider 비용 상한을 보장하지 않고 mock 점수는 모델 품질·비용 측정값이 아닙니다. 상세: [등급 비교 fixture](../reference/model-tier-bench-625/README.md).

PI 결정(2026-10-01, #40)에 따른 기본 비교군입니다. 실제 실행은 PI 머신의 구독을 씁니다.

| arm | model · effort |
|---|---|
| `labhq` | Claude 직원 `opus=sonnet` 치환, Codex 직원은 설정 그대로 |
| `sonnet-max` | `claude -p --model sonnet --effort max` (Claude CLI 최고 effort) |
| `sol-ultra` | `codex exec -m gpt-5.6-sol -c model_reasoning_effort="ultra"` |
| `astra-ultra` | `codex exec -m gpt-6-astra -c model_reasoning_effort="ultra"` |

```bash
labhq bench list
labhq bench run inco-kras-g12c --dry-run
labhq bench run inco-kras-g12c --arms labhq,sonnet-max,sol-ultra,astra-ultra --output ~/.labhq/bench
labhq bench run inco-kras-g12c --arms labhq --staff-model opus=sonnet
labhq bench run inco-kras-g12c --arms sol-ultra,astra-ultra
labhq bench report inco-kras-g12c
labhq bench rescore inco-kras-g12c --all  # 현재 검사로 재채점; --run-id ID 또는 생략 시 최신 run, 이전 판정은 score.json에 보존
labhq bench test-agent --arms labhq,sonnet-max --engines mock
```

`bench.arms`에 arm별 `engine`(`claude_code`/`codex`), `model`, `effort`를 설정합니다.
이 mapping을 지정하면 기본 baseline을 대체합니다. Opus도 별도 arm으로 추가할 수 있습니다.
`bench.staff_model` 기본값은 `{opus: sonnet}`이며 `{}`로 치환을 끕니다. `--staff-model FROM=TO`는 반복 지정합니다.
원본 직원 YAML은 보존하며 결과표에는 실행 당시 모델·effort를 기록합니다.

결과는 `case-id/<run-id>/<arm>/`에 쌓입니다. `run`과 `report`는 arm별 최신 결과를
`case-id/comparison.md`·`comparison.json`으로 합칩니다. 실행별 비교표와 `test-agent-summary.*`도 남깁니다.
real/mock은 따로 모으며 mock 보고는 `report --engines mock`으로 봅니다.
기본 폴더는 `$LABHQ_BENCH_DIR` 또는 `~/.labhq/bench`입니다.

`labhq/bench_data/cases/`의 고정 참고 자료·초기 prompt는 모든 arm이 같습니다. 자료·검사기는 wheel에도 포함됩니다. scripted PI 답변은 LabHQ 질문에만 제공합니다.
각 arm은 보고서 끝에 같은 case별 JSON 결과 블록을 씁니다. LabHQ 상태·비용 문구는 이 블록 앞에 둡니다. 점수는 그 값을 기준으로 매기며 형식 실패와 값 오답을 따로 집계합니다. 문장 검사는 근거·한계 서술을 보는 보조 항목입니다.
비대화 baseline의 질문 감지 불가·답변 미제공은 표에 표시합니다. 개인 설정은 격리하고 Codex global `AGENTS.md`가 있으면 거부합니다.
Claude baseline은 자기 arm의 파일 쓰기·단순 명령을 허용합니다. [권한 범위와 Windows 제약](../reference/bench-permissions.md), [실제 답 15개 보정](../../bench/calibration.md)을 참고하세요.
모든 arm은 case의 `timeout_s`(없으면 `runner.task_timeout_s`)를 씁니다. baseline은 timeout·취소 시 CLI 프로세스 트리를 종료합니다.
예산 초과는 기본 거절입니다. 실험에서 `run`·`test-agent --approve-budget-up-to 3`을 지정하면 labhq의 요청 총예산이 case 예산 3배 이내일 때만 승인합니다.
표에는 예산 승인 횟수와 최종 비용/원래 예산 배수를 남깁니다. 승인해도 원래 예산 초과는 FAIL이며, 실행 중인 병렬 단계의 비용까지 제한하는 옵션은 아닙니다. 비용 모름이 섞이면 상한도 `비용 모름`(test-agent 불통과)이며, 확인된 부분만으로 넘으면 FAIL입니다. Codex arm은 가격표에 있는 모델이면 추정 비용을 씁니다.
