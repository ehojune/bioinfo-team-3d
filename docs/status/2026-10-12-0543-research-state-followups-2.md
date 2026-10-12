## 단계
P2 — #527 봇이 남긴 연구 lane 후속 3건

## 한 일

| 항목 | main 재현 | 수정 |
|---|---|---|
| 1. 마지막 보고서 turn이 `pending_asks`·`pending_jobs`를 내면 "final turn; no further work" 기록 뒤 wake 전에 예산 카드가 또 뜸 | 재현(기록 3줄, 첫 줄이 final turn) | 마지막 단계의 끝난 turn은 바로 판정하지 않는다. 기다리는 turn이면 다음 wake turn의 gate가 카드를 띄우고, 면제 기록은 wake·마무리까지 끝난 뒤 `run_step` 끝에서 한 번 남긴다. 질문이 거절돼 끝나는 경로도 같은 끝을 지난다. |
| 2. 차수가 없는 예전 continuation 확인 질문 답이 1차로 세져 2차 이상 재개 때 카드 두 장을 더 열 수 있음 | 재현(`answered`, 카드 열림) | 차수 없는 답은 지금 차수 상한에 센다. 1차 답은 contract 전이라 차수가 안 적혔으므로 research intake면 `research_round: 1`을 적는다. 답한 질문 거르기는 전체 기록 그대로. |
| 3. 새 CP1 뒤 `continuation.verified=True` 저장과 첫 dispatch 사이에 gateway가 멈추면 취소·재개 거절·시간 초과 때 되돌리기를 건너뜀 | 재현(3경로 모두 새 계획이 남음) | gateway는 task ledger에 그 차수(`research_round`)의 단계 task가 있는지로 판정한다. 1차 단계 task는 세지 않는다. 처리 중인 orchestrator 경로는 지금처럼 `verified`를 쓴다. |

## 테스트 결과

- [x] 새 test 9개(파라미터 포함): main에서 6개 실패, 수정 뒤 통과. 나머지 3개는 dispatch된 차수를 되돌리지 않는지 지키는 guard.
- [x] 관련 파일 5개: 131 passed
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5134 passed, 64 skipped (`--basetemp`·`LABHQ_STATE_DIR`는 %TEMP% 아래, 끝나고 지움)
- [x] `scripts/check_public.sh`
- [x] `python scripts/patch_notes.py check --base origin/main --head HEAD`

## 바꾼 파일

- `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`
- `tests/test_research_notes_budget.py`, `tests/test_research_continue.py`, `tests/test_request_lifecycle.py`
- `docs/manual.md`(승인·예산, 이어 가기 절)

## 막힌 점

- 없음. CI는 push 뒤 확인합니다.

## PI·Claude에게 물을 것

- 없음.

## 확인

- [x] `docs/status/2026-10-12-0543-research-state-followups-2.md`에 같은 보고 추가
- [x] 실제 labhq 인스턴스 실행·재시작 없음

Refs #521

🤖 Generated with [Claude Code](https://claude.com/claude-code)
