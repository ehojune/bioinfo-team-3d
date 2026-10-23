# Scheduler user 이름 fallback

- USERNAME·USER·LOGNAME이 없는 Windows 환경에서 `getpass.getuser()`가 POSIX 전용 `pwd` import로 `ModuleNotFoundError`를 내며 `Scheduler` 생성이 실패했다(proof c1 runner 시작 실패의 원인).
- `labhq/tools/scheduler.py`에 `_local_user()`를 추가해 실패하면 `"labhq"`를 쓴다. `cfg.user`가 있으면 그대로 쓴다. 저장소의 다른 `getuser`·`os.getlogin` 호출은 없다(grep).
- `tests/test_scheduler.py` 2건 추가: env에서 세 변수를 지워도 생성, `getuser`가 예외를 내면 `"labhq"`. `test_scheduler.py` 11건 통과. 전체 suite·실제 Windows CI는 미확인.
