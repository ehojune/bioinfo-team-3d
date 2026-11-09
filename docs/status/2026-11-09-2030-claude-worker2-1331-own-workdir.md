# #1331 r2·r3 — 작업 자신의 폴더는 internal 구역 읽기 승인에서 뺀다 (PR #1341)

## 결론
PI 설정이 작업 폴더 루트(`runs`)를 internal 구역으로 둬도, 직원은 자기 작업 폴더의 파일을 승인 없이 읽습니다. 시운전 1회차에서 CSO가 자기 `TASK.md`를 못 읽어 계획이 멈춘 원인을 고쳤습니다.

## 바뀐 것
- `labhq/policy.py` `_zone_hit`: internal 구역 `ask` 판정에서 작업 폴더(workdir) 아래 경로를 뺍니다. 철자상 안쪽 경로는 resolve한 실제 위치로 다시 보므로, 폴더 안 링크·junction이 다른 구역이나 다른 작업 폴더로 가면 그대로 승인을 묻습니다. 구역이 작업 폴더 자체이거나 그 안에 있으면 예외를 쓰지 않습니다. restricted 구역의 거부는 그대로입니다.
- `tests/test_policy_own_workdir_1331.py` 5건, `config/ci/windows-boundary-v1.json`에 그 시험 등록.

## 실행한 것
- 새 시험 5건 통과, main의 `policy.py`로는 자기 TASK.md 시험이 실패(red 확인).
- 관련 시험(`test_policy.py`·`test_policy_scan_cost.py`·`test_internal_row_output.py`·`test_claude_write_paths.py`) 380 passed, 6 skipped.
- `scripts/ci_selection.py` `validate_profile` 통과, `patch_notes.py check`·`check_public.sh` 통과.

## 미해결
- manual의 데이터 구역 절에 이 예외를 적지 않았습니다(배정 범위 밖).
