# #1073 validation-manifest base drift가 main 추가 이동에 실패

- 결론: `scripts/ci_skip.py`의 `confirmed_base_drift()`가 manifest 실행 시점의 main이 checkout parent와 정확히 같을 때만 drift로 인정했다. 목차 자동 commit이 job 도중 main에 한 번 더 들어오면 같은 forward drift인데도 `checkout-parents` 오류가 됐다(#1063 run 41033951645).
- 바꾼 것: main이 parent와 다르면 `compare/{parent}...{main}`으로 확인해, `status == ahead`, `behind_by == 0`, `base_commit`·`merge_base_commit`이 모두 parent일 때만 인정한다. main이 parent와 같으면 호출을 추가하지 않는다. 나머지 조건(PR snapshot·merge_commit_sha·event base→parent forward·job 상태)은 그대로다. 결과는 지금처럼 `reusable=false, reason=base-drift`이고 재사용 manifest는 만들지 않는다.
- 막는 것: 갈라졌거나(diverged) 뒤처졌거나(behind, behind_by>0) 무관한 main, 부분 응답, SHA가 아닌 main 값은 계속 `checkout-parents` 오류다.
- 시험: `tests/test_ci_parent_drift.py`에 8건(앞서 간 main 인정과 호출 수, 실패 6종, 비-SHA). `tests/test_ci_*.py` 전체와 함께 로컬 통과. 기존 `main` 무관 sha 사례는 그대로 실패한다.
- 미검증: 실제 CI에서 main이 job 도중 한 번 더 움직이는 경합. 병합 뒤 #1064를 새 synchronize로 다시 검증해야 한다.
