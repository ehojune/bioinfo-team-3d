# 광역 종료 guard P1 후속 근거 (PR #766)

lookup-only 기록입니다. 명령은 모두 test 문자열이며 shell·프로세스를 실행하지 않았습니다.

| 지적 | 현재 처리·회귀 |
|---|---|
| 4318107934 | 원문 stale 근거: 아래 PowerShell parameter 계약. 명시한 Name이 switch 뒤에 오는 경우는 이미 deny |
| 4318107944 | coproc 실행 위치 뒤 literal command 검사 |
| 4318556373 | eval의 literal 인수를 결합해 재귀 검사. 재귀 상한을 넘으면 거부 |
| 4318556385 | backquote 실행 payload 검사. single quote·escaped delimiter 안의 데이터는 구분 |
| 4319098856 | timeout·nice·setsid의 option/operand와 실행 command 구분 |
| 4319098865 | find 실행 action 재귀 검사. predicate/format operand·help/version 조회는 구분 |
| 4320283756 | dash의 literal -c payload와 option cluster 검사 |
| 4320283761 | trap literal handler 검사. listing/reset·옵션 종료 구분 |
| 4320283767 | pgrep producer를 downstream xargs kill과 연결. 고정 PID 뒤에 추가되는 선택 PID도 거부 |
| 4320283775 | ANSI-C literal executable fragment와 octal/hex/Unicode escape 정규화 |

`tests/test_process_stop_guard_followup.py`가 지적별 case ID, policy deny, flat/nested fake broker의 card/event/submit 미호출·pending 없음과 PID·데이터 음성 사례를 확인합니다. 기존 회귀도 유지합니다. 전체 실패명·traceback은 로컬 로그에 보존합니다.

## PowerShell 원문 판정

[5.1 계약](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-process?view=powershell-5.1#-name)과 [7.5 계약](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-process?view=powershell-7.5#-name)은 Name의 위치를 Named로, 기본 Id를 positional 0의 Int32 배열로 정의합니다. [7.5.3 소스](https://github.com/PowerShell/PowerShell/blob/v7.5.3/src/Microsoft.PowerShell.Commands.Management/commands/management/Process.cs)의 StopProcessCommand 선언도 같습니다. 따라서 `Stop-Process -Force python`이 positional Name으로 선택한다는 원문의 전제는 이 계약과 맞지 않습니다. 정적 계약에 따른 stale 판정이며 실제 PowerShell 실행으로 확인하지 않았습니다. 기존 판정·승인 분기를 넓히지 않았습니다.

## 검증 경계

문자열·fake policy/broker만 검증합니다. 동적 명령 OS 격리와 PID tracker #675, 실제 process/proc_tree·PI·HPC·연구 실행은 범위 밖입니다. 기존 P2는 변경하지 않으며 후속 선정은 총괄이 맡습니다.

문법 근거: [Bash builtins(eval/trap)](https://www.gnu.org/software/bash/manual/html_node/Bourne-Shell-Builtins.html), [command substitution](https://www.gnu.org/software/bash/manual/html_node/Command-Substitution.html), [ANSI-C quoting](https://www.gnu.org/software/bash/manual/html_node/ANSI_002dC-Quoting.html), [timeout](https://www.gnu.org/s/coreutils/manual/html_node/timeout-invocation.html), [nice](https://www.gnu.org/s/coreutils/manual/html_node/nice-invocation.html), [setsid 구현](https://github.com/util-linux/util-linux/blob/master/sys-utils/setsid.c).
