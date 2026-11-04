# cgroup 메모리 탐지 #726

- `/proc/self/cgroup`의 `0::` membership과 `mountinfo`의 cgroup2 root·mountpoint를 대응시킨다. mount 필드의 octal escape를 해제한다.
- 현재 그룹부터 각 보이는 mount 루트까지 양수 `memory.max`의 최솟값을 물리 RAM과 비교한다. `max`·누락·읽기 실패·잘못된 숫자는 건너뛴다.
- 절대 경로·구성요소 경계를 확인하고 `..`·NUL·무관한 mount를 거부한다. mount 밖의 숨은 ancestor는 추정하지 않는다.
- proc 정보나 유효 한도가 없으면 기존 고정 v2·v1 파일 fallback을 쓴다. Windows·미확인 호스트의 기존 동작을 유지한다.
- fixture: service·container bind mount·namespace root·작은 부모·unlimited·잘못된/없는 파일·경로 경계. 수정 전 11 failed·10 passed → 최종 관련 30 passed. 실제 cgroup 변경·프로세스 종료·HPC 미실행.
- manual 길이(LF Unicode): 161,473→161,615자(+142).

근거: [커널 cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)(계층 제한·memory.max), [mountinfo](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html)(root·mountpoint), [cgroup namespace](https://man7.org/linux/man-pages/man7/cgroup_namespaces.7.html)(경로 가시성). lookup-only 기록이다.
