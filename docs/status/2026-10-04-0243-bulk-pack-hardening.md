# bulk pack 규칙 우회 닫기

**결론:** pack 적용 여부를 폐쇄형 intake 판정으로 정하고, pairing 근거 유형과 조합표 타입을 기계 검증합니다.

| 항목 | 변경 |
|---|---|
| 적용 판정 | 적용 대상의 `not_applicable`을 거부하고, 판정 불가는 CSO 신고임을 CP1·보고에 표시 |
| pairing | partial·complete에 폐쇄형 `pairing_evidence_type`을 요구 |
| 사용자 pack | `allowed_combinations` 셀을 field 타입과 대조하며 bool과 int를 구분 |
| 버전 | `bulk_tumor_normal@1`은 태그·활성 PI/trial 설정이 없는 릴리즈 전 pack이라 version은 유지하고 hash만 갱신 |

**검증:** 수정 전 요구 테스트 실패 확인. 관련 연구 테스트 347건과 공개 검사 통과.

**미해결:** 없음.
