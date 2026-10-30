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

## 하지 않은 것
- **gateway의 비용 기록이 task meta의 단계 등급을 `task_cost_item`에 넘기는 연결**(`gateway/server.py`): claim 범위 밖이라 편집 전에 요청한다. 연결 전에는 단계 등급이 설정된 저장 계획에서 `model_tier_mismatch`가 잘못 나올 수 있다(기본 꺼짐이고 CSO는 아직 고르지 못해 지금은 닿지 않는다).
- CSO prompt·CP1 표시·비용 예상(PR B)과 bench 측정(PI 승인 뒤).
