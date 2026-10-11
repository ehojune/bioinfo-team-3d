## 단계
P2 — #521 연구 lane 상태 4~9

## 한 일

- 마지막 연구 보고 turn에서 처음 예산을 넘으면 카드를 띄우지 않고 초과 기록과 보고서를 남깁니다. 끝난 일에 7일짜리 결정을 요구하지 않는 안을 택했습니다.
- 확인 질문 카드 상한을 연구 차수마다 세고, 이미 답한 질문은 전체 기록에서 계속 거릅니다.
- 새 이어 가기 계획 전 재개 카드, 미승인 계획의 취소·재개 거절·만료 복원, 보관된 차수의 Live 출처 재질문을 바로잡았습니다.
- 이어 가기 prompt의 재사용 조건에 가설 field 세 개를 명시했습니다.
- `docs/manual.md`의 예산·재개 카드·리뷰/이어 가기 절을 105,355자에서 105,493자로 고쳤습니다.

## 테스트 결과
- [x] `origin/main` 동작 + 새 회귀: 8 failed, 68 passed
- [x] 관련 회귀: 191 passed
- [x] Windows 전체 `pytest -q`: 5029 passed, 64 skipped
- [x] UI 변경 없음

## 바꾼 파일

- `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/research/continuation.py`
- `tests/test_research_continue.py`, `tests/test_research_notes_budget.py`, `tests/test_research_report.py`, `tests/test_request_lifecycle.py`
- `docs/manual.md`

## 막힌 점

없음.

## PI·Claude에게 물을 것

없음.

## 확인
- [x] `scripts/check_public.sh` 통과
- [x] `docs/status/2026-10-12-0405-research-state-followups.md`에 같은 보고 추가
- [x] 추가 PI 확인이 필요한 체크포인트 없음

Refs #521

🤖 Generated with Codex (gpt-5) for the labhq dev lead
