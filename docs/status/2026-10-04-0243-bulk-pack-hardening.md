# bulk pack 규칙 우회 닫기

**결론:** pack 적용 여부를 폐쇄형 intake 판정으로 정하고, 자유 문장에서는 명시된 생물학 조건 대비만 확정합니다.

| 항목 | 변경 |
|---|---|
| 적용 판정 | 방법 비교는 판정 불가로 두고, 종양 대 정상처럼 두 조건이 명시된 경우만 확정 |
| 비적용 판정 | 한 조건이 명백히 어긋나면 다른 조건이 없어도 비적용으로 판정 |
| pairing | partial·complete에 폐쇄형 `pairing_evidence_type`을 요구 |
| 사용자 pack | `allowed_combinations` 셀 타입을 검사하되 optional field의 `null`은 허용 |
| 버전 | `bulk_tumor_normal@1`은 태그·활성 PI/trial 설정이 없는 릴리즈 전 pack이라 version은 유지하고 hash만 갱신 |
| 보존 | `single_cell_de@2` 내용과 hash는 변경하지 않음 |

**검증:** 리뷰 회귀 3건 수정 전 실패 확인. 수정 후 관련 테스트 150건 통과.

**미해결:** 없음.
