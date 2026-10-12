## 결론

같은 SHA 재실행에서 사라진 Windows CI flake 4개를 실제시간 제한 대신 event·task 완료 동기화로 바꿨습니다.

## 바뀐 것

| test | CI 실패 횟수 | OS | 증상 |
|---|---:|---|---|
| `test_restart_recovery_holds_only_the_step_on_the_limited_engine` | 1 | Windows | 바깥 `wait_for(1s)`가 `TimeoutError`; run 38170211095 재실행 통과 |
| `test_temporary_process_snapshot_failure_is_rechecked` | 1 | Windows | 바깥 `wait_for(2s)`가 `TimeoutError`; run 38156419427 재실행 통과 |
| `test_the_lab_records_the_same_with_actions_on[shadow]` | 1 | Windows | terminal status 60초 polling timeout; run 37668212634 재실행 통과 |
| `test_every_cso_and_direct_turn_kind_waits_for_login[briefing]` | 1 | Windows | 바깥 `wait_for(1s)` 안에 retry가 못 끝남; run 37700811619 재실행 통과 |

- quota test는 독립 단계 완료 event와 recovery task를 직접 기다립니다.
- stall test는 mock kill 완료 event 뒤 runner task 결과를 확인합니다.
- mock lab은 request orchestration task를 기다린 뒤 terminal status를 단언합니다.
- login test는 두 번째 dispatch event 뒤 runner task 결과를 확인합니다.
- `test_login_retry_expiry_ends_notice_and_next_failure_notifies_again` 2건은 PR #459에서 이미 수정돼 제외했습니다.
- reused workspace 사례는 단독 25회 실패 0이고 해당 CI 로그가 공유 temp 오염이라 수정하지 않았습니다.

## 실행한 것

- 8개 CPU job 부하, 수정 전: 각 test 30회, 실패 0/30(로컬에서는 미재현)
- 같은 부하, 수정 후: 각 test 30회, 실패 0/30
- 관련 test: 65 passed, 1 skipped
- `tests/test_login_wait.py`: 45 passed
- Windows 전체 `pytest -q`: 5331 passed, 66 skipped(두 번째 격리 run; 첫 run은 다른 lane pytest와 겹쳐 11건 실패, 해당 11건 단독 재실행은 통과)
- `scripts/check_public.sh`: 통과

## 미해결

- current head Windows 전체 pytest와 CI는 기록 commit 뒤 확인합니다.
- 남은 지적: 없음.

## 근거

- `tests/test_quota_wait.py`
- `tests/test_stall_watch.py`
- `tests/semantics_shadow_lab.py`
- `tests/test_login_wait.py`
- `HANDOFF.md`

🤖 Generated with Codex (gpt-5) for the labhq dev lead
