## #844 — supervisor 시작 실패 진단에 실행 기록·gateway 로그 꼬리 추가 (base 3c07c1c)

- 변경: `tests/test_supervisor_process.py`의 시험 전용 `_print_start_evidence`가 `gateway-supervisor.log`·`gateway-supervisor-diagnostics.jsonl`에 더해 `detached-launch.jsonl`과 `gateway.log`의 꼬리를 출력한다(`START_EVIDENCE_FILES`). 16KiB 상한, token 마스킹, 제어문자 치환, symlink 거부, 원래 실패 예외 보존, 진단 오류 비전파는 그대로다. 제품 코드, 제한시간, CI 선택 정책은 바꾸지 않았다.
- 이유: #844의 두 실패(run 39684337037, 39689292875)는 supervisor가 gateway 기록 전에 끝났다는 사실만 남겼다. 기존 진단으로는 Windows 자식 기동 거부(`child_start_failed`의 세부)와 gateway 즉시 종료(`child_exit`의 원인)를 가를 수 없었다.
- 검사: 기존 순수 test에 두 파일의 부재와 존재(마스킹·제어문자 포함)를 더했다. 실제 helper와 test를 AST로 뽑아 in-process로 실행했다: 수정 전 helper는 새 test에서 FAIL, 수정 후 PASS. Python 3.10 AST도 통과했다. 실프로세스 test와 pytest는 실행하지 않았다.
- 남음: 원인은 미확정이다. 다음 재발 때 출력으로 판정하며, #844는 열어 둔다.
