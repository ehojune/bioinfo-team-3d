# labhq 연구 수행 규약

대상: 연구를 승인하는 PI와 계획·실행·검토를 맡는 직원. 버전 `v0.1`(2026-10-01).

> 현재 구현은 opt-in pilot이다. `research.enabled: false`가 기본값이다. 연구 요청은 계획 검증과 CP1 승인까지 진행하고, `research.evidence_checkpoint: true`일 때만 승인된 step을 결과 원장(§3) 계약으로 실행해 CP2에서 멈춘다. CP2 승인 뒤에는 리뷰 한 번과 claim 앵커를 검사한 보고서까지 간다. CP2 결정은 승인·수정 요청·거부 선택값으로만 받고, 모은 산출에 묶이지 않은 artifact를 인용한 근거는 거부한다. 연구 요청은 일반 재계획에 들어가지 않는다. 출처 verifier와 CP3·CP4는 아직 실행 경로에 연결하지 않았다.

## 1. 접수

| 판정 | 기준 | 처리 |
|---|---|---|
| `simple` | 정해진 변환·집계·원문 요약 | 기존 direct/orchestrate 경로 유지 |
| `research` | 새 결론·가설·후보 선택, 답을 바꾸는 방법 선택 | 연구 계약 PLAN과 CP1 적용 |
| 모호함·scope 밖 | simple로 단정하기 어려움, 승인 범위 불명 | research로 계획하거나 PI 확인 전 대기 |

길이와 직원 수는 판정 기준이 아니다. API의 `work_kind=auto|simple|research`로 PI가 명시할 수 있다. pilot을 켠 상태에서 `research + direct`는 실행하지 않고 `orchestrate` 또는 `plan_only` 재접수를 요구한다. 단순 작업에도 출처·실패·데이터 경계는 지킨다.

설정은 세 키를 함께 켠다. `enabled`만 켜면 CP1에서 계획을 동결한 뒤 단계를 실행하지 않고 끝난다. 오타 키는 시작할 때 오류다. `labhq doctor`의 `research lane` 행이 지금 상태를 알려 준다.

```yaml
research:
  enabled: true
  evidence_checkpoint: true      # CP1 뒤 단계를 실행하고 CP2·리뷰·보고서까지
  active_packs: [bulk_tumor_normal@3, single_cell_de@3]
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
| pack | `protocol.packs`의 `id@version`과 SHA-256은 labhq가 설정 snapshot으로 채운다. CSO는 `pack_values[pack_key]`에 field 값과 validator·acceptance 설명을 쓴다. acceptance 키는 pack rule id다 |

비적용 항목은 `not_applicable`과 이유를 남긴다. step 상한을 넘으면 뒤를 자르지 않고 재계획한다. schema, DAG, 직원 ID, pack snapshot이 맞아야 CP1로 간다.

정규화한 PLAN 전체의 canonical JSON을 SHA-256 입력과 CP1 상세 화면에 함께 쓴다. 결정 카드에는 질문·가설·protocol·완료/중단 조건·자원·data boundary·pack 값을 나눠 보여 주고, hash 입력 원문도 보존한다. 승인 뒤 질문·방법·대상·지표·선정 기준·범위·자원·pack이 바뀌면 receipt를 `needs_reapproval`로 표시한다.

## 3. 직원 결과 계약

연구 직원은 향후 실행 단계에서 `result v2`를 반환한다. 원장은 `labhq/evidence/claims.py`, 출처 검사는 `labhq/evidence/verify.py`이며 #58 증거 계층과 같은 원장을 쓴다.

| 필드 | 의미 |
|---|---|
| `plan_sha256`, `step_id` | 어떤 동결 계획의 어느 step인지. 동결 계획과 다르면 거부. 그 step이 선언한 `claim_ids` 밖의 claim도 거부 |
| `claims` | 한 줄 한 주장. `kind`(finding/inference/hypothesis)·`status`·중요도·`status_reason`·한계, `revision`·`supersedes`, 정량 `comparisons` |
| `evidence` | 아래 6종 행. 관찰, 조회 상태, 출처, 평가, `quantities` |
| `links` | claim revision과 evidence, `supports/contradicts/context`, `rationale` |
| `artifact_refs` | 논리 artifact ID와 경로 |
| `not_established` | 이번 작업으로 확립하지 못한 내용 |
| `failures` | 검색·도구·분석 실패. 0건과 구별 |
| `method_changes` | 계획값과 실제값, 이유, 결론 영향 여부 |

근거 행의 `slots`에는 그 행이 채운 step의 evidence slot ID를 적는다. 한 step 안에서 slot ID는 겹칠 수 없다(겹치면 CP1 전에 PLAN을 거부한다, #187). 필수 slot마다 채운 행이 하나는 있어야 하고, 선언하지 않은 slot은 거부한다. 조회가 실패했거나 0건이어도 그 행에 slot을 적는다. 시도하고 비었다는 것이 CP2에 그대로 보여야 하기 때문이다. 추론·가설 행은 slot을 채우지 못한다.

evidence 종류는 `observation`, `database_annotation`, `experimental`, `literature_claim`, `inference`, `hypothesis`다. 앞의 넷만 근거로 센다. `inference`·`hypothesis`는 `derived_from`을 적고 `context`로만 연결한다.

| 코드가 거부하는 것 | 규칙 |
|---|---|
| 없는 claim·evidence·artifact 참조, 중복 ID, 옛 claim revision을 가리키는 link | R04 |
| `supported`·`partially_supported`·`contradicted`인데 관찰된 근거 link가 없음. `supported`인데 반대 근거가 있음 | R04 |
| 추론·가설, 또는 `unavailable`·`not_found`·`failed` 행으로 지지·반박 | R04 |
| 추론·가설 행이 `derived_from`을 따라가도 관찰·조회 행에 닿지 않음(순환 포함) | R04 |
| 검색 행의 `result_count`가 0인데 `observed`, `not_found`인데 1 이상. 0건 검색은 `not_found`이며 근거가 아니다 | R04 |
| 근거 행의 `directness`·`source_level`·`independence_group`·`assessment_reason` 누락, link `rationale`·claim `status_reason` 누락 | R05 |
| 같은 출처를 다른 independence group으로 적음(재인용을 독립 근거로 셈). ID, 그 ID의 레지스트리 URL(doi.org·identifiers.org·PubMed·PMC·GEO·NCBI·UniProt·RCSB·ChEMBL·Ensembl), 같은 파일을 가리키는 artifact를 모두 같은 출처로 본다 | R05 |
| 외부 출처의 `accessed_at`(YYYY-MM-DD, 추론·가설 행 포함), 관찰 행의 `locator`, 0건 행의 `query`, 실패 행의 `status_detail` 누락 | R06 |
| quantity의 `value`·`unit`·`conditions`·`denominator` 누락. 모르면 `unknown`에 항목과 결론에 주는 영향을 적는다 | R07 |
| 단위·조건·적어 둔 `method`가 다르거나 모르는 값을 `comparable`로 비교. `not_comparable`로 두거나 가정을 적은 `comparable_with_assumptions`로 쓴다 | R07 |

신뢰도는 확률 대신 이유와 한계로 적는다. 구조·상관·예측을 인과·친화도·효능으로 확대하지 않는다. 통제접근 원자료는 허용 환경 밖, LLM 입력, 외부 로그로 보내지 않는다.

## 4. 근거·감사·변경

- claim은 `finding|inference|hypothesis`, 상태는 `proposed|supported|partially_supported|contradicted|unresolved|withdrawn`으로 나눈다.
- evidence는 질문 적합성 → 직접성 → 품질·편향 → 독립성·재검증 순으로 본다. 재인용은 독립 근거가 아니다.
- ID 해소와 문장 지지는 별도 검사다. `unavailable`, 성공한 한정 검색의 `not_found`, 도구 `failed`는 모두 부재 증명이 아니다.
- verifier는 조회 결과(`succeeded/failed/skipped`)와 ID 상태를 따로 남긴다.

| ID 상태 | 뜻 | claim 판정 |
|---|---|---|
| `found` | 인용한 ID·version과 맞는 기록이 하나 | 근거 확인. 문장 지지는 reviewer가 본다 |
| `not_found` | 조회가 끝났고 기록이 없음 | 결함 |
| `insufficient` | ID 형식이 틀려 조회하지 않음. 추측해 고치지 않는다 | 결함 |
| `conflicting` | 다른 scheme·ID·version의 기록, 여러 기록, 인용 뒤 바뀐 artifact hash | 결함 |
| `requires_verification` | 네트워크 오류·timeout·resolver 오류·live 꺼짐·미지원 체계·manifest 없음, base accession은 같고 한쪽에 version·isoform이 없는 기록 | 미확인. 결함도 부재도 아니다 |
| `retracted_by` | 인용한 원문을 철회 공지가 철회함 | 그 근거를 쓴 claim은 결함 |
| `is_retraction_notice` | 인용한 DOI 자체가 철회 공지임 | 공지로 표시. 철회된 원문으로 오인하지 않는다 |
| `corrected_by`·`concern_raised_by` | 원문에 정정·우려 표명이 있음 | 경고 |

claim은 지지·반박으로 연결한 출처가 모두 `found`일 때만 `verified`다. 하나라도 미확인이면 `unverified`다. 출처가 ID와 URI를 함께 적으면 URI도 그 ID를 가리키는지 검사한다. 레지스트리 URL이 같은 체계의 다른 accession을 가리키면 코드가 바로 `conflicting`으로 본다. 한 기록을 여러 표기로 쓰는 체계(Ensembl·RefSeq version, UniProt isoform, ClinVar VCV)는 version·isoform·VCV 자리채움을 뗀 base accession으로 비교한다. base가 다르거나, base가 같아도 양쪽에 적힌 version·isoform이 다르면(`ENSG…17`과 `ENSG…16`) `conflicting`이다. 한쪽에 version·isoform이 없으면 같은 기록인지 코드가 모르므로 resolver에 넘긴다(#167). 체계가 다른 URL(DOI 옆 PubMed URL)도 그 밖의 URL처럼 resolver가 uri 조회로 ID를 알려 줄 때 비교한다. resolver가 돌려준 기록도 같은 함수로 비교해서, 다르면 `conflicting`, base만 같거나 비교하지 못하면 미확인이다. ID 조회 응답에 인용한 표기와 같은 base의 다른 표기가 함께 오면 하나를 골라 주지 않고 `conflicting`으로 둔다. 레지스트리 경로라도 accession 형식이 아닌 검색·도움말 페이지(`/search`, `/docs`)는 일반 URI다. URI만 있는 출처도 레지스트리 URL이면 그 ID로 조회한다. 출처가 ID와 artifact를 함께 적으면 둘 다 검사하고, `version`은 외부 기록의 판으로 본다(artifact만 있으면 인용한 sha256). context 행·연결 안 된 행·추론 행, 0건 검색(`not_found`) 행의 출처도 검사하며, 결함이 하나라도 있으면 보고 전체(`ok`)가 통과하지 않는다. 0건 행의 출처는 검색한 곳(DB·dataset)이고 찾던 ID는 `query`에 적는다. `failed`·`unavailable` 행은 출처에 닿지 못했으므로 검사하지 않는다. URI는 scheme·host만 대소문자를 무시한다. 원장은 행에 적힌 표기만으로 재인용을 보므로 DOI 행과 같은 논문의 PMID 행을 다른 출처로 둔다. resolver가 찾은 기록의 `same_as`로 다른 체계 ID(DOI↔PMID↔PMCID)를 알려 주면 verifier가 그 대응까지 넣어 다시 보고, 다른 independence group으로 적은 행은 `recitations`에 남겨 보고 전체를 통과시키지 않는다(#168).

조회는 한 번에 최대 `concurrency`(기본 4)개씩 돌고, 조회마다 `timeout_s`(기본 20초), 보고 전체는 `deadline_s`(기본 120초) 안에 끝난다. deadline을 넘긴 출처는 `failed/timeout`이라 미확인이며, 부재(`not_found`)로 적지 않는다. `research.live_source_check`를 켜면 Crossref·doi.org·NCBI를 조회하고, 기본값은 꺼짐이다. 시험과 bench 고정 응답은 `StaticResolver`를 쓴다. artifact 근거는 runner가 관찰한 manifest가 있어야 `found`가 된다.
- 주요 claim에는 반대 근거·대안 설명·반증 관찰을 둔다. critic은 결함을 찾고 원 담당자가 고친다.
- 입력·검색식·명령·코드/환경·seed·exit·출력 hash를 기록한다. `documented`, `replayable`, `rerun_verified`를 구별한다.
- 근거나 방법 revision이 바뀌면 종속 claim·분석·감사·승인을 stale 처리한다. 옛 결과는 이력으로만 둔다.

작성자와 다른 reviewer가 ID·인용 지지·조건·통계·반례·과장·재현 범위를 claim별로 감사한다. reviewer 정체성과 실제 model/vendor를 기록한다. 중대 결함이 남으면 accept할 수 없다.

연구 결과 검토는 REVIEW v2(`labhq/research/review.py`, R13의 앞부분)로 받는다. claim마다 인용 지지, 부재를 근거로 씀, 직접성, 독립성, 비교 가능성을 모두 판정한다. 관찰 문장에 "0 hits"라고만 적은 0건 검색처럼 코드가 읽지 못하는 것은 reviewer가 본다. 부재를 근거로 쓴 것은 늘 중대 결함이다. countable 근거를 단 claim은 앞의 네 항목을, quantity를 비교한 claim은 비교 가능성을 `not_applicable`로 넘길 수 없다. 검토는 결과의 plan hash·step·claim 전부에 묶이고, 결과를 쓴 직원은 검토하지 못한다. 결과에는 작성자 field가 없으므로 검증할 때 작성자(task의 agent id)를 꼭 넘겨야 하며, 빠지면 독립성을 확인하지 않은 검토로 보고 거부한다(#169, #188). 아직 실행 경로에 연결하지 않았고 일반 요청의 `REVIEW_SCHEMA`는 그대로다.

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

pack은 core 계약을 약화하지 않고 field·validator·review 질문·fixture·기계 판정 `rules`만 더한다. loader는 내용을 hash하고 같은 validator/rule ID의 상충 요구, 모르는 field·연산자를 거부한다. `allowed_combinations`의 칸은 PLAN 답과 같은 검사(type·`allowed_values`·`minimum`·`pattern`)를 통과해야 한다. 어떤 답도 맞출 수 없는 행은 load에서 거부한다. 빈칸(`null`)도 맞출 답이 없어 거부한다. core PLAN field(`brief.study_type` 등)의 칸은 PLAN schema type(선택지·int·bool·list)에 맞아야 한다.

```yaml
rules:
  - id: single_cell_de.confounded_conclusion_mode
    when: {field: batch_design, value: fully_confounded_not_identifiable}
    require: {field: conclusion_mode, value: descriptive_only}
    description: A fully confounded design is limited to descriptive reporting.
```

predicate는 `field`와 `value`, `in`, `not_in`, `present`, `min_items` 중 하나만 쓴다. `present`는 빈 문자열만 든 목록을 값이 없는 것으로 보고, `min_items: N`은 서로 다른 빈칸 아닌 항목이 N개 이상인 목록만 통과시킨다(비교군 두 개 등). rule은 선택적인 `when`과 `require` 또는 `forbid` 하나를 둔다. `when`에 predicate 목록을 주면 모두 맞을 때만 rule이 걸린다. field는 pack이 선언한 field 또는 허용된 PLAN scalar(`intake.*`, `brief`의 기본 scalar, `protocol.revision|analysis_unit`, `protocol.statistics`의 scalar, `notes`)다.

active pack 중 `applies_when`이 맞는 것만 PLAN의 `pack_values[pack_key]`에 field 값과 validator·acceptance 설명을 둔다. acceptance 문자열은 설명일 뿐 통과 근거가 아니다. 동결 전에 필수 field·타입·허용값·최솟값·pattern을 검사하고 모든 rule을 실행한다. 하나라도 실패하면 CP1을 열지 않고 한 번 재계획한다. 교정 prompt에는 schema·pack 문제를 한 번에 모두 넣는다. 교정 뒤에도 실패하면 요청을 `plan_invalid`로 끝내고 남은 문제를 보고서와 `plan_validation`에 남긴다(#222).

예시는 `single_cell_de@3`과 `bulk_tumor_normal@3`이다. 전자는 donor·scale·model을, 후자는 벌크 두 조건의 pairing·저발현 filter·DE 기준·양성 대조 방향과 PMID를 CP1 전에 고정한다. `@3`의 `pairing_evidence`는 메타데이터 출처(`geo_characteristics`, `sample_title`, `source_name`, `biosample_attributes`, `supplementary_table`, `publication_methods`, `local_metadata`)를 `;`로 잇거나 `none`이고, pairing이 `partial`·`complete`면 `none`을 쓸 수 없다. 설정 예시는 `active_packs: [single_cell_de@3, bulk_tumor_normal@3]`이며, 두 pack 모두 요청 topic으로 적용된다. 기존 `bulk_tumor_normal@1`·`@2`·`single_cell_de@2` 승인 요청은 저장된 version과 hash로 재개한다.

| `model` | `model_family` | 허용 `count_scale` |
|---|---|---|
| `pseudobulk` | 전부 | `raw_counts` |
| `donor_dependent` | `negative_binomial`, `poisson` | `raw_counts` |
| `donor_dependent` | `linear` | `log_transformed` |

`count_scale`의 허용값은 `raw_counts`·`log_transformed`다. `single_cell_de@2`에서 `normalized_counts`를 뺐다. 받아 주는 조합이 없어서 field 검사는 통과하고 rule에서만 막혔기 때문이다(#114). 이제 field 단계에서 한 가지 이유로 거절한다.

pack 내용을 바꾸면 version을 올린다. 승인된 계획은 `id@version`과 hash로 pack을 가리키므로, 같은 version의 내용이 바뀌면 그 계획이 무엇을 승인했는지 알 수 없다. 내장 pack hash는 `tests/test_research_protocol.py`에 고정했다. 설정에 없어진 version을 적으면 gateway가 시작할 때 남아 있는 version을 함께 알리고 멈춘다(#170).

`conclusion_mode: condition_effect`는 적용된 통계 계획을 요구한다. `descriptive_only`는 batch 혼동이 없어도 설명·비교 study type, 주가설, 추론 통계, estimand를 금지한다. `model_rationale` 같은 설명 문장은 판정에 쓰지 않는다.

이 규약은 라이선스가 확인되지 않은 inco 저장소에서 문구·표·template을 가져오지 않았다. 여러 연구 workflow의 구조만 비교 대상으로 참고했고, labhq 계약과 표현은 새로 작성했다.
