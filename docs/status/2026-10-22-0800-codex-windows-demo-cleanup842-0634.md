## #842 — Windows Job wrapper의 표준 handle 전달 (source 87146f4)

- root 최종검토 P1: 제품 `labhq/runner/memory_limit.py`의 `_run_windows_job`은 `subprocess.run(command)`를 표준 handle 인자 없이 부른다. 그래서 Windows CPython 3.12의 `_get_handles`가 모두 -1을 돌려주고, `STARTF_USESTDHANDLES`도 없고 `close_fds=True`라 Chrome은 Node의 stderr pipe를 상속하지 않는다. root가 stdlib source로 확인했고 이전 1ab33f8 기록에서 미확인으로 둔 한계다. 그 상태에서는 Chrome 오류 출력이 사라지고, stdio close는 wrapper 종료만 뜻한다.
- 수정: 제품 코드는 그대로다. test 전용 `tests/support/windows_job_browser.py`(stdlib)가 제품 `_WindowsJob`을 그 source 파일에서 불러 재사용하고, child에 자기 stdin/stdout/stderr만 명시해 넘긴다. CPython은 이 세 handle만 상속 목록에 넣는다.
  - 인자 검사, 오류 줄, 종료 코드(126/127, child 코드), KILL_ON_CLOSE, 메모리 상한, 비상속 Job handle은 제품과 같다. Job 준비에 실패하면 child를 만들지 않는다. 광범위 `close_fds=False`는 쓰지 않는다.
  - driver의 `LABHQ_TEST_PYTHON` 실패 시 차단, 5/3초 정리 예산, 실제 close 판정, 30/170/90초, noskip, 필수 26, Linux 직접 경로는 그대로다.
- 검사: 새 순수 `tests/test_demo_windows_job.py` 11 passed(표준 handle 명시 전달, Job 실패 시 child 없음, 잘못된 인자, 제품 코드·줄, 제품 Job class 재사용)와 Windows inventory 등록. VM fake의 `__dirname`을 driver 폴더로 바로잡았다. bundled Node `--check`, 정적 CI 210 passed, 3.10 AST, profile·public·diff 검사 통과.
- 미확인: 실제 Windows에서 Chrome stderr가 Node까지 오는지, Job close 뒤 2초 안에 stdio close가 오는지. 새 head 전체 CI가 필요하다.
