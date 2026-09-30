# labhq 연구 수행 규약

대상: 연구를 승인하는 PI와 계획·실행·검토를 맡는 직원. 버전 `v0.1`(2026-10-01).

> 현재 구현은 opt-in PR 1 pilot이다. 연구 요청은 계획 검증과 CP1 승인까지만 진행하며 직원 연구 step은 실행하지 않는다. `research.enabled: false`가 기본값이다.

## 1. 접수

| 판정 | 기준 | 처리 |
|---|---|---|
| `simple` | 정해진 변환·집계·원문 요약 | 기존 direct/orchestrate 경로 유지 |
| `research` | 새 결론·가설·후보 선택, 답을 바꾸는 방법 선택 | 연구 계약 PLAN과 CP1 적용 |
| 모호함·scope 밖 | simple로 단정하기 어려움, 승인 범위 불명 | research로 계획하거나 PI 확인 전 대기 |

길이와 직원 수는 판정 기준이 아니다. API의 `work_kind=auto|simple|research`로 PI가 명시할 수 있다. pilot을 켠 상태에서 `research + direct`는 실행하지 않고 `orchestrate` 또는 `plan_only` 재접수를 요구한다. 단순 작업에도 출처·실패·데이터 경계는 지킨다.

설정은 한 줄로 켠다.

```yaml
research:
  enabled: true
```

## 2. 동결 PLAN 계약

CSO는 실행 전에 `PLAN v2`를 만든다.

| 블록 | 필수 내용 |
|---|---|
| `intake` | 판정, 이유, scope 상태, 명시/규칙/CSO 출처 |
| `brief` | 질문·용도·대상·범위·산출물·관찰 가능한 완료 조건 |
| 가설 | 설명·비교 연구는 주가설, null/대안, 구별할 관찰. 탐색·기술 조사는 탐색 목적 |
| `protocol` | 분석 단위, 선정/제외, 비교군, 주요 지표, 검증법, 자원 상한, 중단·승인 조건, data boundary |
| 통계 | 적용 여부와 이유. 적용 시 estimand·독립 단위·주요 outcome은 필수, 검정군·다중검정·결측·효과크기/CI는 정하거나 `not_applicable`에 사유를 적는다 |
| step | phase, claim ID, input ref, output, check, evidence slot, dependency |
| pack | 정확한 `id@version`과 SHA-256, `pack_values[pack_key]`의 field·validator·acceptance 설명 |

비적용 항목은 `not_applicable`과 이유를 남긴다. step 상한을 넘으면 뒤를 자르지 않고 재계획한다. schema, DAG, 직원 ID, pack snapshot이 맞아야 CP1로 간다.

정규화한 PLAN 전체의 canonical JSON을 SHA-256 입력과 CP1 상세 화면에 함께 쓴다. 결정 카드에는 질문·가설·protocol·완료/중단 조건·자원·data boundary·pack 값을 나눠 보여 주고, hash 입력 원문도 보존한다. 승인 뒤 질문·방법·대상·지표·선정 기준·범위·자원·pack이 바뀌면 receipt를 `needs_reapproval`로 표시한다.

## 3. 직원 결과 계약

연구 직원은 향후 실행 단계에서 `result v2`를 반환한다.

| 필드 | 의미 |
|---|---|
| `plan_sha256`, `step_id` | 어떤 동결 계획의 어느 step인지 |
| `findings` | 한 문장 claim, `finding/inference/hypothesis`, evidence ref, 한계 |
| `evidence` | 관찰, 출처 ref, `observed/unavailable/not_found/failed` |
| `artifact_refs` | 논리 artifact ID와 경로 |
| `not_established` | 이번 작업으로 확립하지 못한 내용 |
| `failures` | 검색·도구·분석 실패. 0건과 구별 |
| `method_changes` | 계획값과 실제값, 이유, 결론 영향 여부 |

값에는 단위·분모·대상·조건·불확실성을 붙인다. 조건이 다르면 비교 불가를 표시한다. 구조·상관·예측을 인과·친화도·효능으로 확대하지 않는다. 통제접근 원자료는 허용 환경 밖, LLM 입력, 외부 로그로 보내지 않는다.

## 4. 근거·감사·변경

- claim은 `finding|inference|hypothesis`, 상태는 `proposed|supported|partially_supported|contradicted|unresolved|withdrawn`으로 나눈다.
- evidence는 질문 적합성 → 직접성 → 품질·편향 → 독립성·재검증 순으로 본다. 재인용은 독립 근거가 아니다.
- ID 해소와 문장 지지는 별도 검사다. `unavailable`, 성공한 한정 검색의 `not_found`, 도구 `failed`는 모두 부재 증명이 아니다.
- 주요 claim에는 반대 근거·대안 설명·반증 관찰을 둔다. critic은 결함을 찾고 원 담당자가 고친다.
- 입력·검색식·명령·코드/환경·seed·exit·출력 hash를 기록한다. `documented`, `replayable`, `rerun_verified`를 구별한다.
- 근거나 방법 revision이 바뀌면 종속 claim·분석·감사·승인을 stale 처리한다. 옛 결과는 이력으로만 둔다.

작성자와 다른 reviewer가 ID·인용 지지·조건·통계·반례·과장·재현 범위를 claim별로 감사한다. reviewer 정체성과 실제 model/vendor를 기록한다. 중대 결함이 남으면 accept할 수 없다.

## 5. PI checkpoint와 완료

| gate | 멈추는 때 | PI에게 보이는 것 |
|---|---|---|
| CP1 계획 | 연구 실행 전 | 질문·가설, plan hash, 성공/중단 기준, 비용·데이터 경계, CP2·3 위임 범위 |
| CP2 증거 | 분석 전 | 충족/공백 slot, 실패·반대 근거, 진행·축소·중단 선택 |
| CP3 선택 | 후보·방법 선택 시 | 공통 기준, 대안·민감도, 최소 판별 실험·비용 |
| CP4 수용 | 감사와 최종 초안 뒤 | 현재 권고, claim/audit/report hash, 남은 공백과 이견 |

receipt에는 gate, 요청, 대상 revision/hash, 결정자, 시각, 결정과 위임 범위를 적는다. 질문 답변·직원 상담·resume 승인·예산/HPC/전송 승인은 연구 승인을 대신하지 않는다.

필수 작업·감사·승인·사전 기준을 충족하면 `done`, 끊긴 참조·낡은 결과·중대 결함이 남으면 `incomplete`, 실행 오류는 `failed`다. 근거 부족도 허용한 계획이면 `done + inconclusive`가 가능하다. `plan_only`의 완료는 승인된 계획이며 연구 결과 완료가 아니다.

## 6. domain rule pack

pack은 core 계약을 약화하지 않고 field·validator·review 질문·fixture·기계 판정 `rules`만 더한다. loader는 내용을 hash하고 같은 validator/rule ID의 상충 요구, 모르는 field·연산자를 거부한다.

```yaml
rules:
  - id: single_cell_de.confounded_conclusion_mode
    when: {field: batch_design, value: fully_confounded_not_identifiable}
    require: {field: conclusion_mode, value: descriptive_only}
    description: A fully confounded design is limited to descriptive reporting.
```

predicate는 `field`와 `value`, `in`, `not_in` 중 하나만 쓴다. rule은 선택적인 `when`과 `require` 또는 `forbid` 하나를 둔다. field는 pack이 선언한 field 또는 허용된 PLAN scalar(`intake.*`, `brief`의 기본 scalar, `protocol.revision|analysis_unit`, `protocol.statistics`의 scalar, `notes`)다.

active pack마다 PLAN의 `pack_values[pack_key]`에 field 값과 validator·acceptance 설명을 둔다. acceptance 문자열은 설명일 뿐 통과 근거가 아니다. 동결 전에 필수 field·타입·허용값·최솟값을 검사하고 모든 rule을 실행한다. 하나라도 실패하면 CP1을 열지 않고 한 번 재계획한다.

예시는 `labhq/research/packs/single_cell_de.yaml`이다. donor·condition·batch, count scale, replicate, model, 식별 가능성, 결론 모드를 CP1 전에 고정한다. donor를 독립 단위로 두고 cell 의사반복, count scale/model 불일치, donor–batch 완전 혼동을 검사한다. 도구와 QC cutoff는 요청별 PLAN에서 고정한다.

이 규약은 라이선스가 확인되지 않은 inco 저장소에서 문구·표·template을 가져오지 않았다. 여러 연구 workflow의 구조만 비교 대상으로 참고했고, labhq 계약과 표현은 새로 작성했다.
