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

## 리뷰 후 수정 (rev3)
- P1(`disk_space.py:563`): 출력 경로와 무관한 `$` 변수 때문에 명령 전체를 건너뛰던 것을, 쓰는 경로(`-OutFile`, `-DestinationPath`, `-Destination`)에 변수가 든 경우만 건너뛰게 바꿨다(`_unwritten`). zip 경로가 변수면 크기만 모르고 대상 드라이브는 그대로 검사한다.
- P1(`disk_space.py:534`): `Expand-Archive`가 대상을 생략하면 현재 폴더로 보고 검사한다(전에는 건너뜀).
- P2(`disk_space.py:538`): `Start-BitsTransfer`는 `-TransferType Upload`이거나 대상이 URL이면 로컬 폴더를 쓰지 않으므로 검사하지 않는다.
- P1(patch note, manual): 지적은 manual을 더하기 전 head(`35b9ca9d`)에서 나온 것이고 이 head에는 한·영 manual에 PowerShell 설명이 들어 있다.
- 시험 10건 추가(+기존 1건을 새 동작에 맞춤). `test_disk_shell_check_678.py` 99 passed, 옛 코드에서는 새 시험이 실패한다.

## rev4: 인자 결합을 한 번에
- 지적 네 건(Set-Location 이름 매개변수, BITS 옵션 값, module 한정 이름, 공통 매개변수 값)은 모두 "어느 단어가 매개변수 값인가"를 추측한 데서 나왔다.
- cmdlet별 매개변수 표(`_PS_WEB`, `_PS_ARCHIVE`, `_PS_BITS`, `_PS_LOCATION`와 공통 매개변수·별칭)와 `_ps_bind`로 대체했다. 표에 없거나 모호한 이름이 끼면 위치 인자를 읽지 않고, 이름 붙은 값만 쓴다.
- `Set-Location/Push-Location`은 `-Path`·`-LiteralPath`·위치 인자를 읽고, 읽을 수 없으면(Pop-Location, 변수, `-`, 모르는 옵션) 이후 명령을 건너뛴다.
- 시험 23건을 먼저 추가해 수정 전 실패를 확인했다. `tests/test_disk_shell_check_678.py` 127 passed.
- 확인하지 못한 것: PowerShell 실제 바인더와의 일치(표는 문서 기준 수작업이다), PowerShell 7 전용 매개변수 일부.

## rev5: 모르는 단어와 배열
- 리뷰 P1 두 건(표에 없는 유효한 BITS 옵션, destination 배열)은 표를 완성해도 다시 나올 부류라 읽는 방식을 바꿨다.
- `_ps_bind`는 이제 가능한 모든 결합을 돌려준다. 표에 없거나 종류가 불확실한 매개변수(`None`)는 스위치와 값을 받는 경우를 둘 다 만들고, `_ps_write`는 읽기마다 쓰기를 만들어 `powershell_check`가 가장 나쁜 판정을 쓴다. 대상 드라이브가 실제로 기준 아래일 때만 묻는다.
- 의심이 4번을 넘으면 위치 인자를 읽지 않는다(`partial`). destination이 없는 읽기는 현재 폴더로 보되 `partial`이면 건너뛴다.
- `-Destination a,b`는 쉼표로 나눠 원소마다 폴더를 풀고 모든 드라이브를 검사한다. 변수가 든 원소가 있으면 전체를 읽지 않는다.
- 시험을 먼저 쓰고 수정 전 실패를 확인했다. `tests/test_disk_shell_check_678.py` 138 passed. 전에는 `-Bogus x`를 "읽지 않음"으로 시험했는데 이제 "두 읽기 중 하나라도 부족하면 묻는다"로 바꿨다. 시험을 약하게 한 것이 아니라 정책을 바꾼 것이다.
- 확인하지 못한 것: 실제 PowerShell 바인더와의 일치. 같은 부류가 또 나오면 고치지 않고 보고한다(총괄 지시).

## rev6: 시험 이식성
- rev5의 배열 destination 시험 세 건이 `bigdrive\x.zip`처럼 백슬래시를 써서 Linux에서는 하나의 파일 이름으로 읽혀 폴더 이름 기반 probe가 어긋났다(CI 3.10·3.12 실패). 슬래시로 바꿨다. 제품 코드 결함이 아니다.
- 확인: Windows 138 passed. WSL Ubuntu-22.04에서 133 passed, 5 error(`mcp` 패키지가 없는 venv에서 gate 시험이 import하지 못함, 이 변경과 무관). 배열 시험 세 건은 통과.

## rev7: 토큰화와 "모르면 후보를 검사"
- 리뷰 P1 네 건: 백틱 줄 잇기, 쉼표 뒤 공백 배열, 드라이브 문자 `D:`, 스마트 따옴표. 모두 PowerShell 문법을 그대로 읽지 못해 검사가 비껴간 경우다.
- 토큰화: 스마트 따옴표를 ASCII로 정규화하고, 백틱+줄바꿈은 단어를 끊고 이어 읽고, 쉼표 앞뒤 공백은 같은 단어(배열)로 합친다. `D:` 한 단어 명령은 위치 변경으로 본다.
- 원칙(총괄 지시): 확실히 못 읽으면 대상을 모른다고 보고 건너뛰지 않는다. 변수·부분식 경로는 현재 폴더와 그 문자열이 이름 붙인 드라이브를, 읽을 수 없는 위치 변경 뒤에는 마지막으로 아는 폴더(와 명령 시작 폴더)를 후보로 검사한다. 판정은 후보 중 하나라도 기준 아래로 확실하면 묻는다.
- 시험을 먼저 추가해 수정 전 실패 20건을 확인했다. 변수 경로·읽을 수 없는 위치를 "건너뜀"으로 고정하던 시험 다섯 건은 "현재 폴더를 검사"로 바꿨다(정책 변경이며 시험을 약하게 한 것이 아니다). Windows 150 passed, WSL 144 passed·1 skipped·5 error(`mcp` 없음).
- 변경한 동작: 드라이브가 실제로 부족한 상태에서 변수 대상 다운로드가 묻게 된다(전에는 조용히 통과). 같은 부류가 또 나오면 고치지 않고 보고한다.
