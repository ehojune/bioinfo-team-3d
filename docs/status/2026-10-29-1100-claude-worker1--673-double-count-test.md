## 단계
#673 후속: repair 비용 이중 집계 회귀 시험 — Refs #673.

## 확인한 것
- repair가 있는 단계에서 `_sum_repair_turns`가 돌려주는 결과는 앞 턴들의 비용·usage를 더한 값을 가지지만, 요청 비용 합계(`Orchestrator.cost`)는 dispatch마다 task id로 한 번만 세는 `_count_cost`로 쌓입니다. 합산 결과의 task id는 마지막 repair의 id이고 이미 센 id라 다시 세지 않습니다(`labhq/orchestrator/cso.py` `_count_cost`).
- 현재 코드에서 이중 집계는 없습니다(결함 아님). 시험만 추가했습니다.

## 한 일 (`tests/test_673_retry_budget.py`)
- 형식 오류 2번 뒤 성공하는 3 dispatch 단계에서 `result.cost_usd`(step 결과)와 `orch.cost`(요청 합계)가 모두 dispatch별 비용의 합(0.3)이고, 합산 결과를 `_count_cost`로 다시 세거나 `_count_recorded_costs`를 다시 돌려도 합계가 변하지 않음을 확인합니다.

## 테스트 결과
- `test_673_retry_budget.py` 9건 통과(로컬). 변경은 시험 파일 하나뿐입니다.
- 미검증: 시험이 실제 이중 집계 결함을 잡는지 변이로 확인하지는 않았습니다. 단언은 합계가 0.3에서 달라지면 실패하는 구조입니다.

## 바꾼 파일
tests/test_673_retry_budget.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
