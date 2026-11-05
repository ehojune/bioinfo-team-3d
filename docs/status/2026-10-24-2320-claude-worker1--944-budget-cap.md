## #944 요청 예산이 있으면 CP1 뒤 집행 상한은 요청 예산 (base 634c3fca)

- 바꾼 것: `labhq/orchestrator/cso.py` `_research_budget`. 요청에 PI 예산(`budget_usd`)이 있으면(source `request`) CP1 승인 뒤 `cap_usd`는 그 요청 예산이고, 계획 `budget_usd`와 쓴 비용의 합은 `detail.budget`의 `plan_budget_usd`·`spent_before_plan_usd`와 카드 끝줄(`집행 상한 $40.00(요청 예산 그대로). 계획 추정은 참고만 합니다: …`)에 참고로만 남는다. 요청 예산이 없는 요청은 이전 그대로(정책 `per_request_usd`를 넘지 않는 범위에서 합이 상한). 직원 prompt의 상한 문구와 계획 prompt의 `budget_usd` 설명도 맞췄다. manual 예산 절 갱신.
- 근거: 10차(`req_fbaa5e34cf`) 요청 $40, 쓴 돈 $2.70, 계획 $7 → 상한 $9.70. 건당 `per_task_usd` $10 예약 두 건이 겹쳐 예산 카드 두 장, 실제 지출은 $2.98.
- 검사: 새 test 2건(`tests/test_research_notes_budget.py`: 10차 수치로 예산 카드 0장·상한 $40, 묶인 금액이 $40을 넘는 작업은 카드를 띄우고 보내지 않음). 기존 3개 test 파일(`test_research_notes_budget`·`test_cso_scope_estimate`·`test_research_cp2_revise`)은 "요청 예산 10" 기본 fixture를 정책 상한으로 바꾸거나(`_lab_cap`) 새 의미로 단언을 고쳤다(약화 아님: 요청 예산이 없는 경로의 단언은 그대로). 관련 test 300건대 통과.
- 한계: 실제 연구 실행으로는 확인하지 않았다. 요청 예산을 `--budget`으로 적은 PI의 의도가 "계획 추정 이하"인 경우는 없다고 보고 요청 예산을 상한으로 삼았다.
