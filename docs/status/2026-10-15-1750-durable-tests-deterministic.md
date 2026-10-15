## 결론

PR #641에서 #615의 Windows CI 시간 측정 test 두 개를 사건 순서 검증으로 바꿨습니다. 파일 쓰기를 Event로 막은 채 DB commit과 health 응답이 release 전에 끝나는지 확인합니다. Refs #606.

## 바뀐 것

- `test_a_stalled_file_write_does_not_hold_up_a_database_commit`: blocked `fsync`와 DB commit 완료 순서를 직접 확인
- `test_health_answers_while_the_runner_writes_through_a_slow_disk`: blocked `fsync` 중 health 응답이 먼저 도착하는지 직접 확인
- 벽시계 임계값과 probe 지연 비율 단언 제거

## 확인

- 수정한 두 test 20회 연속 통과: 매회 2 passed
- 관련 pytest: 30 passed
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과

## 남은 것

- push 뒤 CI와 전체 재실행 1회를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.
