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
| 4320847541 | else 뒤의 실행 위치도 wrapper와 함께 검사 |
| 4320847550 | literal 변수의 이름 선택을 저장·복사·pipeline·InputObject/Id 소비까지 보수적으로 추적 |
| 4321129633 | balanced $()의 실행 payload와 이름 선택 PID를 외부 consumer에 연결. 중첩·double quote도 검사 |
| 4321387908 | find의 bare {} 뒤 후속 action을 이어 읽음. shell/block lexer와 기존 prefix 거부는 유지 |
| 4321387934 | 이름 선택 pipeline의 $_/PSItem·복사 변수·괄호식에서 유래한 Id 소비도 거부 |
| 4321588842 | skill 이름/광역 선택 종료 검사. operand와 no-action·help/version·signal listing을 구분 |
| 4321588847 | pidof를 이름 선택 PID producer로 추적해 $()·pipeline·xargs 소비와 연결 |
| 4321811293 | -Id 괄호식이 시작된 뒤 발견한 이름 선택 producer도 consumer와 연결 |
| 4321811304 | unquoted process substitution의 실행·선택 상태를 읽고 stdin/arg-file의 xargs 소비에 연결 |
| 4322020342 | Invoke-Expression·iex의 literal Command와 pipeline 문자열 재귀 검사. common parameter operand는 데이터 |
| 4322020348 | watch의 option operand·직접 argv와 기본 shell 문자열 실행을 구분해 검사 |
| 4322020355 | kill의 signal option과 target을 구분해 PID 0·음수 process-group/all selector 거부 |
| 4322020358 | 이름 선택 pipeline의 $_/PSItem·선택 변수의 Kill() 호출 거부 |
| 4322864519 | EncodedCommand·e/ec 및 약어의 UTF-16LE Base64 payload를 정적으로 decode해 재귀 검사 |
| 4322864528 | ForEach-Object·foreach·%의 MemberName/positional Kill 및 wildcard 호출 거부 |
| 4322864535 | stdbuf의 short/long option operand 뒤 literal command 검사 |

`tests/test_process_stop_guard_followup.py`가 지적별 case ID, policy deny, flat/nested fake broker의 card/event/submit 미호출·pending 없음과 PID·데이터 음성 사례를 확인합니다. 기존 회귀도 유지합니다. 전체 실패명·traceback은 로컬 로그에 보존합니다.

추가 회귀는 `tests/test_process_stop_guard_selection.py`와 `tests/test_process_stop_guard_actions.py`에 있습니다. 변수 덮어쓰기는 분기 실행이 증명되지 않으므로 이름 선택 상태를 지우지 않습니다. $() 안의 single/ANSI-C quote·escaped dollar는 데이터로 구분하며, 기존 backquote·불명확한 문법의 보수적 거부는 완화하지 않습니다. pipeline ID 근거: [ForEach-Object의 현재 객체](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/foreach-object?view=powershell-7.5#description).

## PowerShell 원문 판정

[5.1 계약](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-process?view=powershell-5.1#-name)과 [7.5 계약](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-process?view=powershell-7.5#-name)은 Name의 위치를 Named로, 기본 Id를 positional 0의 Int32 배열로 정의합니다. [7.5.3 소스](https://github.com/PowerShell/PowerShell/blob/v7.5.3/src/Microsoft.PowerShell.Commands.Management/commands/management/Process.cs)의 StopProcessCommand 선언도 같습니다. 따라서 `Stop-Process -Force python`이 positional Name으로 선택한다는 원문의 전제는 이 계약과 맞지 않습니다. 정적 계약에 따른 stale 판정이며 실제 PowerShell 실행으로 확인하지 않았습니다. 기존 판정·승인 분기를 넓히지 않았습니다.

## 검증 경계

문자열·fake policy/broker만 검증합니다. 동적 명령 OS 격리와 PID tracker #675, 실제 process/proc_tree·PI·HPC·연구 실행은 범위 밖입니다. 기존 P2는 변경하지 않으며 후속 선정은 총괄이 맡습니다.

문법 근거: [Bash builtins(eval/trap)](https://www.gnu.org/software/bash/manual/html_node/Bourne-Shell-Builtins.html), [command substitution](https://www.gnu.org/software/bash/manual/html_node/Command-Substitution.html), [ANSI-C quoting](https://www.gnu.org/software/bash/manual/html_node/ANSI_002dC-Quoting.html), [timeout](https://www.gnu.org/s/coreutils/manual/html_node/timeout-invocation.html), [nice](https://www.gnu.org/s/coreutils/manual/html_node/nice-invocation.html), [setsid 구현](https://github.com/util-linux/util-linux/blob/master/sys-utils/setsid.c).

procps 근거: [skill option/operand·no-action 구현](https://gitlab.com/procps-ng/procps/-/blob/master/src/skill.c), [skill 계약](https://man7.org/linux/man-pages/man1/skill.1.html), [pidof 이름 선택](https://man7.org/linux/man-pages/man1/pidof.1.html). `tests/test_process_stop_guard_procps.py`는 조회와 PID 정리를 유지하며, skill no-action의 이름 선택 출력을 종료에 전달할 때도 거부하는지 확인합니다. 도구 자체는 실행하지 않았습니다.

이번 consumer 회귀는 `tests/test_process_stop_guard_consumers.py`에 있습니다. 계약: [process substitution](https://www.gnu.org/s/bash/manual/html_node/Process-Substitution.html), [Invoke-Expression](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/invoke-expression?view=powershell-7.5), [watch option·argv/shell 구현](https://gitlab.com/procps-ng/procps/-/raw/master/src/watch.c), [Linux nonpositive PID 의미](https://raw.githubusercontent.com/torvalds/linux/master/kernel/signal.c), [Process.Kill](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.kill). actual shell/process 검증은 하지 않았습니다.

`tests/test_process_stop_guard_payloads.py`는 [EncodedCommand 계약](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pwsh?view=powershell-7.5#-encodedcommand--e--ec), [MemberName 호출](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/foreach-object?view=powershell-7.5#-membername), [stdbuf option 구현](https://github.com/coreutils/coreutils/blob/master/src/stdbuf.c)을 문자열·fake broker로 검증합니다. Base64의 대소문자는 보존하고 host 옵션과 script/command 인수를 구분합니다. encoded operand는 131,072자까지 정적으로 decode하며 초과·잘못된 Base64/UTF-16LE는 보수적으로 거부합니다. 기존 재귀 상한도 유지합니다.
