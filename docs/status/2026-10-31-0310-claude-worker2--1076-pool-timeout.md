# #1076 Windows pool 단계 상한 재계산

- 결론: #931의 규칙(성공한 pool 단계의 최악값 × 1.5)을 최신 근거로 다시 적용해 pool 단계 상한을 9분에서 13분(780초)으로, job 상한을 14분에서 18분으로 올렸다. 시험 선택과 gate는 바꾸지 않았다.
- 근거: 최근 성공한 pool 단계의 최악값은 499초(#1106 run, 이 PC의 비교 artifact), main Windows는 363~383초다. 9분(540초) 상한을 넘긴 것은 #1097에서 1회, #1111에서 3회(548초 포함)다. #1111은 `tests/semantics_shadow_lab.py` 변경으로 Windows 선택에 semantics 시험 파일 9개가 더해져 수집 시험이 17,256개에서 18,092개로, 풀 시간이 늘었다. 499 × 1.5 = 748.5초를 올림해 13분이다.
- 바꾼 것: `.github/workflows/test.yml`의 pool 단계 `timeout-minutes`(9→13), job `timeout-minutes`(14→18, setup + critical 3 + pool 13 + 보고 여유), 단계 요약 문구의 "540 s"를 "780 s"로, 스택 스냅샷 주석. `tests/test_ci_windows10m.py`의 고정값을 같은 값으로 맞췄다.
- 실행한 것: `tests/test_ci_windows10m.py` 69 passed.
- 미확인: Windows CI가 새 상한 안에서 도는지는 PR의 CI로 본다. 새 상한도 넘으면 근거를 다시 본다.
- 근거: #1076 결정 댓글 6529993804.
