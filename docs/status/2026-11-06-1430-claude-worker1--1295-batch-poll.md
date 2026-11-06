# #1295 1단계 — 일괄 상태 조회와 poll backoff

- 바뀐 것
  - `labhq/tools/scheduler.py`: `Scheduler.statuses(ids)`가 열린 잡의 상태를 한 번에 묻습니다. SGE는 `qstat -u` 1회 후 큐를 떠난 잡만 `qacct`, Slurm은 `squeue -j a,b,c` 1회와 끝난 잡 전체에 `sacct -j` 1회, PBS는 `qstat -f a b c` 1회입니다. 일괄 명령이 실패하거나 잡을 못 찾으면 그 잡만 기존 한 건 조회로 되돌립니다(명령이 더 들 뿐 상태는 같음). 잡이 1개이거나 mock이면 기존 경로 그대로입니다. 한 잡 조회의 sacct 이후 판정은 `_slurm_final`로 빼서 일괄과 공유합니다.
  - `labhq/runner/daemon.py`: `_poll_jobs`가 열린 잡을 `statuses` 한 번으로 묻고, 바뀐 것이 있었는지(새 잡·상태 변화·accounting 지연 중인 잡) 반환합니다. `_job_watch_loop`는 `hpc.ssh_host`가 있을 때만 변화 없는 poll 뒤 간격을 `job_poll_backoff`배로 늘리고(상한 `job_poll_max_s`) 변화·실패 때 `job_poll_s`로 되돌립니다.
  - `labhq/settings.py`: `runner.job_poll_max_s`(기본 300, 0이면 끔·`job_poll_s` 이하면 끔), `runner.job_poll_backoff`(기본 2.0).
- 기본값 근거: 60 s에서 시작해 60→120→240→300 s. 4 h 잡이면 poll이 약 50회로 240회에서 줄어듭니다(시험이 이 계산을 확인). 몇 시간 도는 잡을 끝난 뒤 5분 안에 알아챕니다. 더 길게 잡으면 더 줄고 알림이 늦습니다.
- 시험: `tests/test_hpc_batch_poll_1295.py` 20건(mock `_run`만, 실제 접속 없음). 일괄 결과 = 한 건씩 물은 결과, 명령 수 감소, 일괄 실패·잘못된 id·Slurm purge 시 되돌림, backoff 수열·재시작·ssh 없을 때 고정. 관련 시험 1909 passed(로컬 Windows).
- 하지 않은 것: 실제 SGE·PBS·Slurm에서 일괄 명령의 출력은 확인하지 못했습니다(mock 출력 형식만). 특히 PBS `qstat -f` 다중 id와 Slurm `squeue -j` 일부 id가 purge됐을 때의 동작은 미검증이며, 이상하면 한 건 조회로 되돌아갑니다. ControlMaster·완료 표지·로그인 상한은 PI 결정 뒤.
- 한계: SGE는 큐를 떠난 잡마다 `qacct`가 1회씩 듭니다(`qacct -j`가 id 하나만 받는 것으로 알고 있음, 미검증). 이는 잡이 끝날 때 한 번뿐입니다. `config/labhq.example.yaml`에는 새 설정을 적지 않았습니다(두 복사본이 같아야 하는 시험이 있고 claim 밖).
