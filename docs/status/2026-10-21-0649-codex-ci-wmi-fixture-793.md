# PR #793 exit logging 전용 fixture (local only)

- 기준 fc70f4a6. 새 trace는 powershell_started만 관측했고 입력 read/EOF·JSON·스케줄링·관측을 구분하지 못한다. Create 결과·provider timeout 원인은 UNVERIFIED다.
- logging test 한 건에만 일반 Popen과 정확한 fake gateway 명령용 launcher를 사용한다. 기존 assertions 8개·teardown과 다른 기존 함수/fixture 20개는 그대로다.
- 실제 breakaway·nested Job 생존·거부·ACK 전 차단 gates와 제품/CI policy는 변경하지 않았다. 이것은 WMI timeout 수정이나 native 성공 증거가 아니다.
- 새 회귀 5 failed → 관련 fake/static 74 passed. Python 3.10 AST·public·기록/diff 검사를 수행한다. real process/WMI/PI/WSL/연구/bench는 실행하지 않았다.
- root exact-head 검토 전 push 금지: commit만 한다. CI 조회·재시도·PR/thread 쓰기·병합·배포 없음. [근거·유지한 gate](../reference/exit-logging-fixture-793.md).
