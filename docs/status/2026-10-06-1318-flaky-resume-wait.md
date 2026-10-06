## 2026-10-06 · 재개 대기 test의 시간 경쟁

- 결론: `test_resume_waits_for_runner_and_reoffers_after_timeout`가 0.15초 재개 대기를 첫 요청에도 써서 느린 CI에서 단언 전에 대기가 끝났다(10-06 Python 3.10·Windows에서 각 1번). 첫 요청은 30초 대기와 폴링, 시간 초과 검사만 0.15초.
- 바뀐 것: `tests/test_state.py` 한 test. 서버 코드는 그대로.
- 실행한 것: 고친 test 6회 연속 통과, `tests/test_state.py` 57 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: GitHub Actions run 37404631861(3.10), 37411745645(Windows).
