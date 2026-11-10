# 2026-11-10 claude-worker3 — #1357 r2: HPC 깨우기 상한과 늦게 끝난 잡

## 결론
HPC 단계가 잡을 고쳐 다시 낸 깨우기는 깨우기 상한(`max_wake_cycles`, 기본 5)에 세지 않는다. 끝없는 재제출은 상한의 4배에서 멈춘다. 잡을 남긴 채 상한으로 끝난 단계는 runner에 알리고, runner는 로컬 잡을 끝내며 HPC 잡 결과를 `outputs/`가 아니라 `jobs/late/<job>/`로 받고 이벤트를 남긴다. 5회차 build_index 실패와 4회차 verify 실패의 경로를 막는다.

## 바뀐 것
- `labhq/orchestrator/cso.py`: 깨우기 전후 잡 id를 비교해 새 잡을 냈으면 상한에 세지 않음, `WAKE_HARD_CAP_FACTOR = 4`, 상한 도달 시 `_release_step_jobs`가 기존 `task.jobs_cancel` 경로(store `jobs_cancel` + `send_runner`, 재연결 때 재전송)로 알림
- `labhq/runner/daemon.py`: 해제된 단계를 store `jobs_released`에 남겨 재시작 뒤에도 기억. 해제된 단계의 잡은 `jobs/late/<job>/`로 회수하고 `job_late` 로그 이벤트, 산출 판정(`_remote_step_outputs`)에서 제외
- `tests/test_hpc_whole_request.py`: 재제출 깨우기, 진전 없는 깨우기, 하드 캡과 해제 알림, 늦은 잡 회수 위치

## 실행한 것
- 깨우기·잡·회수 관련 시험 41개 파일: 2283 passed, 42 skipped

## 미해결
- 공유 파일 시스템에서 잡이 `outputs/`에 직접 쓰는 경우는 회수 경로를 거치지 않아 막지 못한다. 잡 자체 취소(scancel)는 로그인이 들어서 하지 않았다
- 해제 알림 재전송은 gateway의 기존 `jobs_cancel` flush에 기댄다
