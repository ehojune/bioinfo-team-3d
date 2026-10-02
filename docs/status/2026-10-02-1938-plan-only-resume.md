## 2026-10-02 · #293 — plan-only 재개 대기 범위

- 결론: `plan_only` 요청은 plan 저장 뒤 gateway가 재시작돼도 실행하지 않을 직원을 기다리지 않고 계획만 끝낸다.
- 바뀐 것: plan 저장 전에는 CSO·briefing 담당만 기다리고, 저장 뒤에는 recovery agent를 요구하지 않는다. 두 경계를 회귀 테스트로 고정했다.
- 실행한 것: 수정 전 저장된 plan의 worker·reviewer·CSO를 기다리는 실패를 확인했다. 관련 pytest 70건과 `scripts/check_public.sh`, patch-notes·목차 검사를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/gateway/server.py`, `tests/test_state.py`.
