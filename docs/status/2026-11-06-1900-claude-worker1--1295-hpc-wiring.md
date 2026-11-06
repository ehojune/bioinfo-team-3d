# #1295 C — 사이트 프로필과 세션 관리자를 도구·runner에 연결

- 바뀐 것
  - `labhq/tools/scheduler.py`: `site_session(cfg)`가 `hpc.site_profile`의 `connect_argv`로 프로세스당 하나의 `HpcSession`을 만들고 `Scheduler._run`이 그 셸에 명령을 보냅니다(`ssh_host`보다 우선). 프로필이 이름만 있고 읽을 수 없으면 `ssh_host`로 돌아가지 않고 실패합니다. `needs_login()`·`attach_session()`·`close_sessions()`.
  - `labhq/hpc_site.py`: 프로필에 `tunnel_texts`, `session_profile()`·`session_config()`(idle_lock_minutes → 연결 유지 간격).
  - `labhq/runner/daemon.py`: 시작 때 세션을 붙이고(열린 잡이 있으면 닫지 않음), `hpc_status`·`hpc_queue`·`hpc_cancel`을 broker `/jobs/submit`의 `op`로 받아 runner의 셸로 돌립니다(`_on_scheduler_op`, 취소는 소유 확인 후). 세션 알림(host key, 터널, 재접속 실패)은 `agent.log` level `alert`로 피드에 올립니다. 살아 있는 셸에서는 로그인 상한 사전 검사를 하지 않습니다.
  - `labhq/tools/hpc_mcp.py`: 세션 사이트에서는 status·queue·cancel을 runner에 요청하고 도구 프로세스는 접속하지 않습니다.
  - `labhq/hpc_session.py`: 사람을 기다리는 상태가 5분(`human_wait_s`) 뒤 다음 명령으로 한 번 재시도합니다.
  - manual 09(HPC 도구와 세션)·15(세션과 프로필, host key 복구, 터널) 한·영.
- 시험: `tests/test_hpc_session_wiring_1295.py` 13건과 `tests/test_hpc_session_1299.py` 22건, 가짜 gateway(가짜 Slurm 추가)만 씁니다. 관련 묶음 2599 passed, 76 skipped(로컬 Windows).
- 한계·미검증: 실제 ssh·HPC·실제 Slurm/SGE/PBS 출력은 보지 않았습니다. 셸은 stdout 한 줄기라 scheduler 명령의 stderr가 stdout에 섞여 옵니다(파서가 둘 다 읽음). 스크립트·로그 경로는 로그인 노드에서도 같은 경로로 보여야 하고 파일 올리기는 없습니다. broker 경로는 별도 route 대신 `/jobs/submit`의 `op`를 썼습니다(`approvals.py`가 claim 밖). 직원 도구의 하루 상한 한계(#1298 PR 본문)는 이 연결로 해소됩니다: 도구 프로세스가 접속하지 않습니다.
