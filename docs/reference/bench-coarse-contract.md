# 4-arm bench 준비 — 응집형 분석 스텝과 handoff 비용 기록 계약 (#799)

조회용 계약입니다. [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739)의 네 arm(labhq 혼합 roster, Astra·Sol·Opus 단독 세션) 비교에서 무엇을 어떻게 셀지 정합니다. **bench·실운전 실행 승인이 아니며, 아래 어느 수치도 아직 측정하지 않았습니다.**

## 1. 기존 정책과 이번 범위

- **정책 위치:** `labhq/bioinfo_policy.py`의 `BIOINFO_ASSIGNMENT_RULE`(#732). 지원 nf-core pipeline의 입력 준비·실행·technical QC를 한 bioinfo-agent 단계에 묶고, 연구 설계·생물학적 해석은 analyst/biologist가 맡습니다.
- **주입 경로:** 일반 `PLAN_PROMPT`, 연구 `RESEARCH_PLAN_PROMPT`·`RESEARCH_CP2_PLAN_PROMPT`, `REPLAN_PROMPT`의 맨 앞에 붙습니다. 연구 continuation은 연구 계획 prompt 뒤에 이전 판과 P1을 덧붙이는 방식이라 같은 규칙을 받습니다. 막힌 직원의 consult 답에도 쓰입니다. `tests/test_coarse_plan_799.py`가 이를 확인합니다.
- **빈 곳(결정 필요):** pipeline이 아닌 분석에서 데이터 탐색·전처리·핵심 통계를 한 단계로 이으라는 문장은 정책에 없습니다. 넣으면 모든 요청의 계획 prompt가 바뀌므로 bench 전 총괄·PI가 정합니다. 이번 PR은 정책 문장을 바꾸지 않았습니다.

## 2. 계획 모양 셈법

`tests/test_coarse_plan_799.py::plan_shape`가 기준 구현입니다. 계획(최초·재계획은 유지+새 단계, continuation은 새 판)마다 셉니다.

| 항목 | 정의 |
|---|---|
| steps | 계획 단계 수 |
| handoffs | 담당이 다른 의존 간선 수 |
| boundary_handoffs | 그중 받는 쪽이 `qc_reviewer`·`biologist`이거나 연구 phase가 `validation`·`report`인 간선. 유지할 경계이며 줄일 대상이 아님 |
| handoff_outputs | handoff마다 넘기는 상위 단계 산출 수의 합(전달 문맥의 계획상 대리값) |
| coupled_splits | analyst·engineer 사이의 선형 간선(부모의 자식이 하나, 자식의 부모가 하나). 탐색·전처리·핵심 통계가 나뉜 후보 |
| pipeline_splits | bioinfo-agent로 들어가는 선형 간선 중 부모가 analyst·engineer·bioinfo-agent인 것. pipeline 준비·실행·technical QC가 나뉜 후보 |

병렬 독립 분석(부모나 자식이 둘 이상)은 split 후보가 아닙니다. bioinfo-agent에서 analyst로 가는 간선은 정책이 정한 역할 경계(연구 통계)라 split으로 세지 않습니다. split 후보는 "합칠 수 있는지 볼 곳"이지 자동 위반이 아닙니다.

## 3. arm마다 기록할 것

| 항목 | 정의 | 지금 얻는 곳 | 상태 |
|---|---|---|---|
| 단계 수·계획 모양 | §2의 값. 최초·재계획·continuation 판별 | 요청의 `plan`, 재계획·round 기록 | 계산 가능, 기록기 없음 |
| CLI 시작 수 | 엔진 CLI를 띄운 횟수. 재시도·이어 쓰기 turn·review·보고 포함 | task 기록(`kind`·`attempt`·`parent_task`) | 계산 가능, 기록기 없음 |
| 전달 문맥량 | CLI 시작마다 넘긴 prompt 크기(byte)와 handoff_outputs | task 기록의 `payload` | 계산 가능, 기록기 없음 |
| 단계별 시간 | 계획(`briefing`·`precedent`·`plan`·`replan`), skill 준비(#798), 승인 대기(카드 생성→`decided_at`), 분석(`step`), review(`review`), 보고(`synthesis`·정리본 편집) | task `dispatched_at`, 결과 event 시각, 승인 기록 | 일부 계산 가능. task 완료 시각은 event에서 읽어야 함 |
| 단계별 비용 | 위와 같은 구분의 `cost_usd`·`usage` 합. 모르는 비용은 0으로 세지 않음 | task 결과의 `cost_usd`·`cost_known`·`usage` | 계산 가능, 기록기 없음 |
| 전체 | `duration_s`·`cost_usd`·`usage`·`pi_interventions` | bench `run.json` | 있음 |

단독 세션 arm은 한 CLI 세션이라 단계 구분이 없습니다. 전체 값과 turn 수만 두고, labhq arm의 단계별 값과 같은 줄에 놓지 않습니다.

## 4. 고정 조건 (#739)

- 같은 요청문, 공개 입력 snapshot, labhq commit, 직원 CLI·모델 판본, budget·turn·timeout을 씁니다.
- PI 답은 미리 정한 스크립트로 줍니다. 채점은 blind rubric으로 합니다.
- arm마다 state·workspace·output·결과 저장소를 따로 두고, 같은 PC에서 순서대로 돌립니다. PI 인스턴스는 쓰지 않습니다.
- semantics 재사용이 앞 arm에 오염되지 않도록 같은 시작 snapshot에서 출발합니다.

## 5. 합치지 않는 경계

- 응집형 정책은 분석 단계 안의 분할만 줄입니다. 독립 QC 단계(`qc_reviewer`), 생물학적 해석, 다른 회사 모델의 과학 리뷰(`sci_reviewer`, 계획 단계가 아닌 review 단계), CP1 승인, CP2 근거 검사는 합치지 않습니다.
- review·승인에 든 시간과 비용은 handoff 비용이 아니라 review·승인 대기 항목으로 셉니다.

## 6. 기존 수치의 해석

- 144분/19분과 21:26은 [벤치 A 2차](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5975156477)입니다.
- 22:28은 [벤치 C t6 재채점](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5999676070)이고, C의 [원 보고 시간은 85.9분](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5993720748)입니다.
- 서로 다른 회차의 수치를 한 비교로 묶지 않습니다. 과분할이 속도 차이의 원인이라는 결론은 통제 비교 전까지 내리지 않습니다.

## 7. 아직 없는 것

- §3의 기록기(task 기록에서 단계별 시간·비용·CLI 시작·prompt 크기를 뽑아 `run.json` 옆에 쓰는 코드)는 구현하지 않았습니다.
- §1의 비 pipeline 응집 문장 추가 여부는 결정 전입니다.
- 계획 예제는 손으로 만든 dict입니다. 실제 CSO가 이런 계획을 내는지는 dry-run에서 확인합니다.
