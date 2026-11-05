# #1076 rev25: 10-31 이후 PR과 무관한 CI 실패 집계

- 결론: `test.yml`의 실패한 run 40건에서 PR 변경과 무관해 보이는 실패를 시험별로 모았다. 표와 수정안은 [#1076 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1076#issuecomment-6547691319)에 있다. 코드는 바꾸지 않았다.
- 반복된 것(서로 다른 브랜치나 main): Atlas 자식 프로세스 20초(3회), `test_semantics_actions_shadow`의 `ended` 누락(3.12, 3회), `test_gateway_loop_lag_606`(2회), `test_task_cancel_durable_606[True]`(3.10, 2회), Atlas `IndexError`(2회), `test_patch_notes`의 3.10 `SystemError`(2회+재실행 1). Windows 시간 상한에 걸려 `FAILED` 줄이 없는 job이 4건(3건은 #1199 이전).
- 사전 지정된 `test_policy` 선형 검사는 10-31 이후 실패한 run에 한 건도 없었다.
- 가설: 시험이 고정된 짧은 시간 한도(0.1s·0.2s·20s)를 쓰는 경쟁이다. 수정안은 이벤트 대기나 막힌 loop와 러너 지연이 갈리는 한도다.
- 한계: 횟수는 하한이다(재실행해 통과한 job은 run 결론이 success로 덮인다). 원인 가설은 재현하지 않았다.
