# 4-arm bench 준비 — 응집형 분석 스텝과 handoff 비용 기록 계약 (#799)

조회용 계약입니다. [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739)의 네 arm(labhq 혼합 roster, Astra·Sol·Opus 단독 세션) 비교에서 무엇을 어떻게 셀지 정합니다. **bench·실운전 실행 승인이 아니며, 아래 어느 수치도 아직 측정하지 않았습니다.**

## 1. 기존 정책과 이번 범위

- **정책 위치:** `labhq/bioinfo_policy.py`의 `BIOINFO_ASSIGNMENT_RULE`(#732). 지원 nf-core pipeline의 입력 준비·실행·technical QC를 한 bioinfo-agent 단계에 묶고, 연구 설계·생물학적 해석은 analyst/biologist가 맡습니다.
- **응집형 분석(#799, PR #815):** 같은 데이터·질문의 탐색·전처리·핵심 통계는 한 분석 단계에 두고 독립 데이터·질문·근거 갈래만 나눕니다. 유지할 경계는 §5와 같습니다. nf-core 배정·건별 기여 승인은 유지합니다. 근거: [#799](https://github.com/ehojune/bioinfo-team-3d/issues/799), [#760 반영](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6281989709), [재개·배정](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6285136614).
- **주입 경로:** `BIOINFO_ASSIGNMENT_RULE` 하나가 일반·연구·재계획 prompt와 직원 규칙에 들어갑니다. continuation과 consult도 이 규칙을 받습니다(`tests/test_coarse_plan_799.py`).

## 2. 계획 모양 셈법

`labhq/bench_metrics.py::plan_shape`가 기준 구현이며 `tests/test_coarse_plan_799.py`의 기존 예제를 그대로 검사합니다. 최초·재계획(유지+새 단계)·continuation(새 판)마다 셉니다.

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

값은 세 층으로 나눕니다. **누락은 `not_collected`, 단독 세션의 해당 없는 단계는 `not_applicable`이며 0으로 채우지 않습니다.** 아래 기록기 연결은 개발 중이며 실제 bench는 미실행입니다.

| 층 | 무엇 | 얻는 곳 |
|---|---|---|
| 계획 기대값 | 승인된 계획의 모양과 예상 dispatch 수 | 승인 기록의 hash 일치 `plan_canonical`. 최초는 단계 수, continuation은 명시한 rerun 수. 재계획의 rerun 증거가 없으면 미수집 |
| task 대리값 | hosted dispatch 수, retry·consult·이어 쓰기·skill·분석·review·보고, prompt/context byte | 해당 요청의 기존 task ledger와 결과·event. 시작하지 못한 dispatch도 포함하므로 CLI 시작 증거가 아님 |
| spawn 관측값 | 성공한 launch 프로세스 시작 수, argv UTF-8 표현·stdin byte | 명시적인 bench runner의 opt-in hook과 baseline 직접 경로. 내부 CLI 인증·도구 성공은 별도 |

| 항목 | 기록 | 지금 상태 |
|---|---|---|
| 단계 수·계획 모양 | §2(최초·재계획·continuation) | hash 고정 승인 snapshot만 수집. 일반 lane 등 snapshot이 없으면 미수집 |
| 기대 dispatch 수 | 계획 기대값 | 계획·rerun 증거에서만 계산하며 실제 시작 수와 분리 |
| task 수 | hosted dispatch·retry·이어 쓰기 수, consult 등 단계별 수 | 누락 metadata는 미수집; task ID로 중복 배제 |
| CLI 시작 수 | 성공한 OS launch spawn 수와 실패 수 | spawn 직후 hook. 실패 spawn의 시작 수는 0, 미관측·중단된 attempt는 미수집 |
| 전달 문맥량 | task prompt/context·준비 prompt byte는 대리값; argv 표현·stdin byte는 관측값 | 원문은 저장하지 않음. pointer·환경·참고 파일·SDK 내부 전달량과 Windows 실제 전송 byte는 미수집 |
| 단계별 시간 | planning·skill·analysis·consult·review·report의 dispatch→첫 결과 합, 카드 생성→결정 대기 합 | 중첩 가능하며 task 내부 대기도 포함. 전체 wall time과 합산하지 않음 |
| 단계별 비용 | known USD 합·unknown task 수 | unknown이 있으면 총액은 미수집, known 합은 하한으로 보존. token·전체 비용은 기존 run 기록 |
| 전체 | `duration_s`·`cost_usd`·`usage`·`pi_interventions` | bench `run.json`에 있음 |

단독 arm도 spawn을 직접 세며 labhq 단계 구분은 적용하지 않습니다. argv 전체는 측정하지만 baseline prompt 성분·내부 turn은 미수집입니다. mock 모드의 실제 CLI 항목은 해당 없음입니다.

**저장:** 각 arm의 `bench_metrics.json`·`.md`와 `run.json`에 고정하고 `score.json`·`comparison.json`·`.md`로 전달합니다. 재채점은 기존 관측을 보존하며 재수집하지 않습니다. Hook은 bench가 만든 runner·요청에만 연결하며 평소에는 꺼져 있습니다. CSO 정책·승인·DB schema는 바꾸지 않습니다. **#799는 미수집 신호와 승인된 실제 비교가 남아 열어 둡니다.** 실제 bench 실행 승인은 별도입니다.

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

## 7. 구현과 실측의 경계

- §3 기록기는 bench에서 명시적으로 켰을 때 arm 폴더에 task·실제 CLI 시작·전달 byte를 기록합니다. 기록이 없으면 `미수집`으로 남깁니다. 실제 bench는 아직 실행하지 않았습니다.
- 계획 예제는 손으로 만든 dict입니다. 새 응집형 문장으로 실제 CSO가 이런 계획을 내는지, 속도·비용·품질이 달라지는지는 측정하지 않았습니다. dry-run과 승인된 bench에서 확인합니다.
