## 2026-10-02 · #312 — 긴 직원 prompt 후속

- 결론: POSIX의 인자별 UTF-8 한도를 넘는 prompt도 TASK pointer로 바꾸며, Claude에 Read가 없으면 실행 전에 거부한다.
- 바뀐 것: spawn `OSError`를 실패 결과로 돌리고 #240·#242 회귀 테스트를 추가했다.
- 실행한 것: 수정 전 3건 실패를 확인했다. 관련 pytest 18건, Node test 12개 파일, `scripts/check_public.sh`를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `tests/test_adapters_fake_cli.py`.
