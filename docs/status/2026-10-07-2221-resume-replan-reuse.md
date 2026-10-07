## 2026-10-07 · #467 — 재개한 요청이 재개 뒤 자기 호출을 다시 받지 않게 함

- 결론: 재시작 뒤 재개된 요청은 재개 전에 보낸 task만 이어받는다. 재개 뒤 새로 보낸 같은 식별의 호출(clarify 답 뒤 재계획, A/B·교정 재계획)은 새로 돈다.
- 바뀐 것: `labhq/gateway/server.py`(`recovery_started`, `_recovery_matches`를 `recovery_attempt`·`dispatch`가 함께 씀), `docs/manual.md` 상태 절 한 줄, `tests/test_state.py` 회귀 1건.
- 실행한 것: 새 회귀가 수정 전 코드에서 실패하고 수정 뒤 통과. 관련 9개 파일 445 passed·1 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 발견 경위는 v0.5 두 번째 연구 시운전(trial, 재시작 뒤 재개한 요청이 clarify 답 직후 실패). 시운전은 새 요청으로 다시 돈다.
- 근거: `tests/test_state.py::test_resumed_request_does_not_replay_its_own_earlier_call`.
