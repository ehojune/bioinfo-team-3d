## 단계
#625 2단계 PR A: 계획 단계의 `model_tier` 데이터 경로. 설계: [#625 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/625#issuecomment-6529059780), 승인 조건은 같은 이슈의 총괄 댓글.

## 한 일
1. `settings.orchestrator.step_model_tiers`(기본 꺼짐).
2. `research/contract.py`: `ResearchStep.model_tier`는 선택이고 비면 직렬화에서 빠져 기존 계획의 `plan_sha256`이 그대로이며, 엔진 schema(`RESEARCH_PLAN_SCHEMA`)에는 노출하지 않는다.
3. `cso.py`: `clamp_step_tier`(직원 선언 등급 이하, validation·report 하한 balanced, 선언 없는 직원은 무시)와 `validate_steps(tier_rules=)`. 규칙이 없으면(설정 꺼짐) 필드는 예전처럼 버려진다. 다섯 호출 지점이 모두 `_tier_rules()`를 넘기고, 단계 task meta에 `model_tier`를 싣는다.
4. `runner/daemon.py`: 단계 등급이 유효하고 표에 그 엔진 항목이 있으면 모델을 바꾼다(직원의 명시 model보다 앞섬).
5. `costs.task_cost_item(model_tier=)`: 단계 등급을 기록할 수 있다.
6. 예시 YAML 두 사본과 manual 한·영.

## 확인
- 새 시험: 등급 자르기 표 10건, `validate_steps` 꺼짐·켜짐·자르기, 연구 계획 hash·schema 불변, 일반 계획 CSO 통합(꺼짐은 아무것도 싣지 않고 켜짐은 자르고 task에 싣는다), 러너 해석, 비용 기록과 불일치 경고. 소스 변경을 빼면 15건이 실패한다.
- `-k "cso or research or plan or settings or runner or cost or send"` 2232 passed, 20 skipped.

## 리뷰 반영 (rev9)
- **P1(저장된 연구 계획이 검증을 우회)**: 연구 계획 재개 경로는 `validate_steps` 결과를 버리고 저장된 단계 dict를 그대로 실행해, 설정이 꺼져 있어도 단계 등급이 러너까지 갔다. 단계 task를 만드는 `run_one`의 `_dispatch_step_tier` **한 지점**에서 다시 결정한다: 설정이 꺼져 있으면 싣지 않고, 켜져 있으면 `clamp_step_tier`(직원 선언 이하, 단계의 `phase` 하한)로 자른다. 일반·연구·저장된 계획이 모두 이 지점을 지난다.
- **P2(비용 기록이 단계 등급을 못 받음)**: `gateway/server.py`의 동기·비동기 비용 기록이 저장된 task의 `payload.meta.model_tier`(발송된 등급)를 비용 항목의 `model_tier`로 남긴다. 의도적으로 낮춘 단계는 `model_tier_mismatch`로 경고되지 않고, 아무도 요청하지 않은 낮춤은 계속 경고된다. 등급이 아닌 값은 무시한다. 호출 signature는 바꾸지 않았다(정적 시험이 `_record_task_cost`를 추출해 쓴다).

## 하지 않은 것
- CSO prompt·CP1 표시·비용 예상(PR B)과 bench 측정(PI 승인 뒤).
- 실제 연구 계획 재개 전체 흐름(모델 호출이 필요한 CP2까지)은 돌리지 않았고, 재개가 지나는 지점의 결정을 단위 시험으로 확인했다.
