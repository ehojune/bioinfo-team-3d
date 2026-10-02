## 2026-10-02 · PR #310 — 쓰기 게이트 경로 판정 후속

- 결론: 파일 도구의 환경 변수 표기는 펼치지 않고, 링크·junction으로 적힌 작업 폴더의 셸 쓰기는 원래 표기 안에서 허용한다.
- 바뀐 것: 파일 도구 경로 정규화만 환경 변수 미전개로 바꾸고, `LABHQ_WORKDIR` 원문을 셸 쓰기 루트에 추가했다.
- 실행한 것: 수정 전 회귀 테스트 2건 실패를 확인했다. 수정 뒤 관련 pytest 101건과 `scripts/check_public.sh`를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/policy.py`, `labhq/tools/approval_mcp.py`, `tests/test_claude_write_paths.py`.
