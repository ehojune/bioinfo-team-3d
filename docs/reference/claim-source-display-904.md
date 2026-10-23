# 주장별 원문 근거 (#904)

Lookup-only: 현재 동작은 [manual](../manual.md#보고서-공통-결과-계약)을 봅니다.

| 기록 | 표시·경계 |
|---|---|
| claim/link/Evidence.status | 주장 상태·supports/contradicts/context·조회 결과를 분리 |
| SourceRef | DOI/PMID/URI, locator, access_status, methods_locator, qc_locator, supplementary_status |
| Evidence.lineage | 최초 실증 출처·재인용, 순환·누락 ID·미판정. 같은 독립 group ID는 중복 집계하지 않음 |
| CP2 receipt | 거부 근거를 표시하고 계보 판정에서 제외. 다른 승인 근거가 없는 재인용 claim은 synthesis·anchor에서도 차단 |
| fulltext receipt | 접근 metadata를 전달. 독해 후 Methods/QC 줄 범위를 채움. 보충자료 의존 여부 null은 미판정 |

- report 표와 publication 각주·`docs/reference/claim_evidence.md`는 같은 표시 helper를 사용한다.
- 모든 claim과 연결되지 않은 근거도 남긴다. legacy 필드 누락은 미판정이며 출처를 추정하지 않는다.
- 단일 fake 요청은 CP2→review→report→portfolio를 지나간다. claim 3개, 원문 미확보, 재인용, 순환, 보충자료 누락과 Methods/QC 위치를 검사한다.
- 실제 원문 독해 품질·HTTP/PDF·기관 접근은 이 검증에서 다루지 않는다.
