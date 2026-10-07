## 2026-10-08 · #491 — sed·curl·wget 출력 경로 해석 보강 (#482)

- 결론: sed 옵션 순서와 script 출처에 상관없이 `-i` 입력 파일을 쓰기 대상으로 잡고, curl·wget 출력 인수의 붙은 따옴표를 해석한다.
- 바뀐 것: `labhq/policy.py`의 sed 2단계 인수 분류와 download literal 해석, `docs/manual.md` 승인 게이트 한 줄, `tests/test_shell_write_quotes.py` 회귀 시험.
- 실행한 것: 관련 policy test 263 passed. Windows 전체 pytest 4747 passed·59 skipped. 첫 전체 실행의 무관한 임시파일 잠금 2건은 개별 재실행과 두 번째 전체 실행에서 통과. `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_shell_write_quotes.py::test_commands_that_execute_text_or_name_outputs_keep_their_write_targets`, `::test_text_that_only_looks_like_a_redirect_is_not_a_write`.
