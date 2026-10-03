# 출력 데이터 종류 어휘 PI 검토 반영

**결론:** PI가 검토한 41키를 그대로 반영했다. 정규화 값은 count·expression·transformed로 나뉘고 `features`는 `genomic_features`로 바뀌었다.

| 항목 | 결과 |
|---|---|
| 어휘·입력 적합성 | 새 3키와 이름 변경, 45키 상한, 분석별 fit·mismatch 반영 |
| EDAM 1.25 | 고정 release에서 재생성. 34키 연결, 7키 local-only. `variant_annotations`에 맞는 살아 있는 data 용어가 없어 `null` 유지 |
| 옛 선언 | hash가 있는 선언은 기존 `vocab_changed` 판정을 쓴다. hash가 없는 legacy `normalized_counts`·`features`도 `vocab_changed`로 막았다 |
| 고정값 | PLAN schema·prompt와 `single_cell_de@2`·`bulk_tumor_normal@1` 내용·hash는 바뀌지 않았다 |

**검증:** 관련 pytest 266 passed, EDAM subset 재생성·검사 통과.
