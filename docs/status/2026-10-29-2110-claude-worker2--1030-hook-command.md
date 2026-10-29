# hook 명령이 Windows에서 실행되지 않던 문제 (#1030)

| 항목 | 내용 |
|---|---|
| 증상 | 폭주 차단기 E2E에서 constrain·halt가 적용됐는데도 호출이 막히지 않았고 `restricted.requested`가 0건이었다 |
| 원인 | hook 명령을 `subprocess.list2cmdline`으로 만들었다. Windows에서 공백 없는 경로는 따옴표 없이 백슬래시 그대로 나오고, Claude Code가 hook을 bash(Git Bash)로 돌리면 백슬래시가 사라져 `command not found`(rc=127)가 된다 |
| 확인 | 실제 Claude Code 2.1.310 세션 1회: 같은 PreToolUse에 옛 형태(A)와 따옴표 슬래시 형태(B) hook을 걸자 A는 로그 없음, B만 실행(#1030 댓글). `bash -c`로 옛 명령을 돌리면 rc=127 |
| 수정 | `hooks/control.py` `hook_command`: Windows는 실행 파일을 슬래시로 바꿔 큰따옴표로 감싸고 인자는 일반 단어로, POSIX는 `shlex.join`. `hook_commands`(steer·pause·halt·CSO)와 `hooks/tool_use.py`(쓰기 영수증)가 모두 이것을 쓴다 |
| 영향받던 기능 | #59 ①의 steer·pause·halt hook, 폭주 차단기의 constrain 제한과 stop의 halt 강제(#995·#1010), constrain 중 CSO 판정(#1028), Claude 쓰기 도구 영수증(PostToolUse recorder). 모두 Windows+Claude Code에서 조용히 동작하지 않았을 수 있다. Linux·macOS는 영향 없음 |
| 시험 | `tests/test_staff_control.py` +2: 명령 문자열의 따옴표·슬래시(백슬래시 없음), 만든 두 hook 명령을 실제 bash로 돌려 constrain 상태에서 deny JSON이 나오고 recorder가 rc=0으로 끝나는지. 옛 명령은 같은 bash에서 rc=127 |
| 미확인 | 수정 뒤 실제 세션에서 hook→broker→gateway CSO 판정·halt→resume(다음 단계 재시험), macOS·Linux의 실제 Claude Code |
