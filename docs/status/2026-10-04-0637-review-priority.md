# 일반 lane 리뷰 지적 우선순위 (#373)

## 단계
P1 — 결론을 바꾸는 지적만 일반 lane의 수정과 실패를 부릅니다.

## 한 일
- 일반 리뷰 schema·prompt에 P1·P2·P3를 추가하고, P1이 있을 때만 `revise`로 판정합니다.
- 재계획과 수정 feedback에는 P1만 보내며, 상한 뒤 P2·P3만 남으면 완료합니다.
- P2 원문을 보고서 `리뷰 참고`에 붙이고, 우선순위가 없는 저장 리뷰는 P1로 이어 갑니다.
- 동작 설명과 모의 reviewer fixture를 갱신했습니다.

## 테스트 결과
- [x] 선행 회귀 2 failed → 수정 뒤 2 passed
- [x] 관련·모의 e2e 253 passed
- [x] `pytest -q`: 3708 passed, 53 skipped
- [x] `scripts/check_public.sh` 통과
- [x] UI 변경 없음

## 바꾼 파일
`labhq/orchestrator/cso.py`, `labhq/adapters/mock.py`, `tests/test_cso.py`, `tests/test_state.py`, `tests/test_research_report.py`, `docs/manual.md`

## 막힌 점
없음.

## PI·Claude에게 물을 것
없음.
