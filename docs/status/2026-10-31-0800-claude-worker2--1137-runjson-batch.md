# #1137 안 G1 — run.json 갱신 묶기

- 결론: task마다 run.json(작업공간 `manifest.json`의 `runs[task_id]`)을 4번 쓰던 것(①시작 ②산출 환경 ③입력 hash ④종료)을 2번으로 줄였다. ②③은 CLI가 끝난 뒤 `task.result`를 보내기 직전까지 연달아 일어나고 그 값은 단계가 끝난 뒤에만 읽히므로, ④의 쓰기에 실었다. ①(CLI 전에 저장)과 실행 중 accounting(비용 기준선·session_id)은 지금처럼 즉시 쓴다. 지연 fsync가 아니다: `task.result`가 나가기 전에 모든 필드가 한 번의 fsync로 디스크에 있다.
- 바꾼 것:
  - `labhq/runner/workspace.py`: `stage_run`(메모리에 모음), `aflush_run`(모은 것만 씀)을 더했고, `update_run`/`aupdate_run`은 모아 둔 필드를 자기 필드 아래에 합쳐 한 번에 쓴다(같은 이름은 명시한 값이 이긴다). 쓰기가 실패하면 모아 둔 필드는 다음 쓰기를 위해 되돌려 놓는다.
  - `labhq/runner/daemon.py`: `collect_observed_outputs`와 입력 hash 기록이 `stage_run`을 쓴다. 단계가 끊기는 경로(취소·예외 뒤)의 입력·산출 기록은 전처럼 즉시 쓴다. `run_task`의 `finally`가 `aflush_run`을 불러, 모은 뒤 최종 쓰기 없이 끝난 단계(RESULT 쓰기 예외 등)도 기록을 남긴다.
- 실행한 것(이 PC, 상태 폴더 D:, lab 요청 1회, mock 엔진, FULL):
  - `os.fsync` 94~97회 → 70~73회(−24). 요청 처리 20.1~21.0 s → 19.3~19.8 s(중앙값 약 20.7 s → 약 19.7 s). D: 디스크 편차가 있어 약 4~5% 감소로만 본다.
  - 관련 시험 3,000여 건 통과(후보 5개 파일 포함), 이 경로를 쓰는 기존 시험은 편집 없이 통과한다.
- 시험(`tests/test_resume_accounting.py` 4건): 단계의 첫 쓰기는 `started_at`이고 `ended_at`을 싣는 쓰기가 `environment_record`·`input_files`를 함께 싣는다(앞선 쓰기에는 없음), RESULT 쓰기가 예외로 끊겨도 모은 기록이 남고 `ended_at`은 없다, 모은 필드는 다음 쓰기에 합쳐지고 명시한 필드가 이긴다, 쓰기가 실패하면 모은 필드가 보존되고 `aflush_run`은 모은 것만 쓴다. main의 코드에서는 3건이 실패하고 끊긴 경로 시험은 보존 확인이라 양쪽 통과한다.
- 미확인: 실제 엔진의 실행 중 갱신이 더 많을 때의 비중, CI Windows 전후 시간.
- 근거: #1137 W1 설계 댓글 6534352217.
