## 단계
#1167(#678 후속): PowerShell 도구의 `Invoke-WebRequest -OutFile`·`Expand-Archive`·`Start-BitsTransfer`가 디스크 확인을 받지 않던 한계.

## 한 일
- `labhq/disk_space.py`: `powershell_check`를 더했다. `Invoke-WebRequest`·`Invoke-RestMethod`(`iwr`·`irm`·`curl`·`wget`)는 `-OutFile`(`-OutFile:값`, 이름의 모호하지 않은 앞부분 `-OutF`까지)의 부모 폴더, `Expand-Archive`는 `-DestinationPath`(위치 인자 둘째 포함)와 zip 목록의 비압축 크기, `Start-BitsTransfer`는 `-Destination`을 읽는다. 파이프라인으로 흐르는 `iwr`(파일 지정 없음), `$` 변수·부분식이 든 경로, 닫히지 않은 따옴표는 읽지 않는다.
- PowerShell을 bash처럼 읽지 않도록 `_ps_commands` tokenizer를 따로 두었다(`\`는 경로 구분자, `'…'`는 문자 그대로(`''`는 따옴표), `"…"`는 백틱 이스케이프, `;` `|` `&&` `||` 줄바꿈이 명령을 끝내고 단어 앞 `#`은 주석, `Set-Location`이 이어지는 명령의 위치를 옮긴다).
- 판정 공통부를 `_assess`와 `_worse`로 모아 `shell_check`와 `powershell_check`가 함께 쓴다. Bash 쪽 시험 54건은 그대로 통과한다.
- `labhq/tools/approval_mcp.py`: 정책이 allow한 `PowerShell` 호출도 `_disk_decision`을 거친다(경고 로그의 `tool_name`도 실제 도구 이름).
- `preapproves_bulk_write`: `PowerShell(Invoke-WebRequest *)`·`PowerShell(Expand-Archive *)`·`PowerShell(Start-BitsTransfer *)` 같은 사전 승인 규칙도 gate로 돌린다.
- 시험(`tests/test_disk_shell_check_678.py`) 35건 추가: 단어 읽기, 닫히지 않은 따옴표, 다운로드 철자 여섯, 읽지 않는 경우 여섯, 알 수 없는 크기, `Expand-Archive`의 실제 크기와 철자 넷, `Start-BitsTransfer`, 체인·`Set-Location`, 읽을 수 없는 드라이브, gate 둘, 규칙 일곱.

## 확인
- Windows 89 passed(옛 코드에서는 새 시험 31건이 실패). `validate_profile` 통과(known_tests에 이미 있는 파일).
- Linux(WSL 3.10): 84 passed, 5 errors. 오류는 WSL 환경에 `mcp` 모듈이 없어 gate fixture를 쓰는 시험(기존 3건 포함)이 시작하지 못한 것이다.

## 하지 않은 것
- `Copy-Item`·`Move-Item`·`robocopy` 같은 복사와 `Invoke-Expression`·`& $변수`는 읽지 않는다. 크기 추정은 zip 목록뿐이다.
- 명령 도중 디스크가 차는 경우는 #1169의 감시가 맡는다.

## manual (rev2)
- manual 한·영의 `셸 다운로드·압축 해제 확인`(#678) 단락에 PowerShell 도구의 확인을 적었다. 읽는 cmdlet과 매개변수, PowerShell 문법으로 읽는다는 점, 읽지 않는 경우(파일로 가지 않는 `iwr`, `$` 변수 경로), 사전 승인 규칙을 gate로 돌리는 것.
- "적용하지 못하는 곳"에서 PowerShell의 `Invoke-WebRequest`·`Expand-Archive`를 빼고(이제 적용), `Copy-Item`·`robocopy`·`Invoke-Expression`을 넣었다. "접두 규칙이 체인 전체를 미리 허용하는 경우" 항목은 Claude Code가 체인을 명령마다 나눠 검사한다는 #1163 결과에 맞춰 뺐다. main을 반영했고 문서 gate가 풀렸다.
