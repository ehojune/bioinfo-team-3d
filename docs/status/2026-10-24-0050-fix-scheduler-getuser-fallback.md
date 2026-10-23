# Scheduler user 이름 확인

- USERNAME·USER·LOGNAME이 없는 Windows에서 `getpass.getuser()`가 POSIX 전용 `pwd` import로 `ModuleNotFoundError`를 내며 `Scheduler` 생성이 실패했다(proof c1 runner 시작 실패의 원인).
- `labhq/tools/scheduler.py`: `getpass.getuser()` → 실패하면 Windows `GetUserNameW`로 실제 로그온 계정을 구한다. 둘 다 실패하면 `self.user`는 비고, `queue()`(SGE·PBS·Slurm)는 `hpc.user`를 설정하라는 `RuntimeError`를 낸다. 이름을 지어내지 않는다(가짜 이름은 `qstat -u`·`squeue -u`가 실제 작업을 못 보게 한다). `mock`·`none`은 영향 없다.
- `tests/test_scheduler.py`: env 변수를 지워도 생성, getuser 실패 시 Windows API 계정(Windows에서만), user를 모를 때 queue 명령이 실행되지 않음(sge·slurm·pbs). 14건 통과. 전체 suite·실제 Windows CI는 미확인.
