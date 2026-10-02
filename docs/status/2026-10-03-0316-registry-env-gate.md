## 2026-10-03 · PR #327 — 레지스트리 접근을 PI 승인으로 (#325)

- 결론: 개인 경로(`policy.private_paths`)가 켜진 task에서 Claude 직원의 Bash·PowerShell 명령이 레지스트리에 손대면 키와 상관없이 승인 게이트가 PI에게 묻는다. PI `GITHUB_TOKEN`은 직원 프로세스 환경에서는 지워지지만(#301) 같은 계정의 레지스트리(`HKCU\Environment`)에는 남아 있어서다. 판정을 조이기만 하고 느슨하게 만들지 않는다.
- 바뀐 것: `labhq/private_paths.py`에 `REGISTRY_ACCESS` 정규식과 `registry_access`. `labhq/policy.py` `_private_decision`의 셸 판정 끝에 연결. README §8 개인 경로 절에 한 줄.
- 잡는 표기: hive 이름(`HKCU`·`HKLM`·`HKU`·`HKCR`·`HKCC` 단어, `HKEY_…` 전체 이름), PowerShell Registry provider(`Registry::`·`-PSProvider Registry`·`New-PSDrive`/`ndr`/`mount`와 Registry), Git Bash `/proc/registry`, `reg`/`reg.exe` 하위 명령 전부·`regedit`·`regini`, `winreg`·`Microsoft.Win32.*`·`RegistryKey`·`[Registry]`·WMI `StdRegProv`, `Win32_Environment`·`wmic environment`, `[EnvironmentVariableTarget]::User`, `GetEnvironmentVariable(s)`의 Process 아닌 대상. 대조 전에 줄 이어쓰기(PowerShell backtick·cmd `^`·sh `\` + LF/CRLF)를 붙이고, 따옴표·backtick·`^` 쪼개기를 지운다.
- 경과: 처음에는 `Environment` 키 표기만 셌다. 리뷰 세 차례가 같은 부류의 더 좁은 표기(`..` 우회, 다른 이름의 PSDrive, hive 이름 속 줄 이어쓰기)를 냈고, 레지스트리 접근 전체로 넓혀 그 부류를 닫았다.
- 활성 경로 0개(설정한 경로가 하나도 없거나 모두 작업 폴더를 담아 빠진 경우)도 켜진 것으로 본다(PR #327 리뷰). runner가 `LABHQ_PRIVATE_PATHS_ENABLED`로 켜짐 여부를 따로 넘기고, 셸 미리 허용 해제·Read 좁히기·레지스트리 검사가 그대로 돈다. `policy.private_paths: []`만 모두 끈다.
- 그대로 허용: `$env:X`·`%X%`·`set`·`printenv`·`os.environ`·`Get-ChildItem Env:`·Process 대상 `GetEnvironmentVariable`, `git`·`python script.py`·평범한 grep, `outputs/hkcu` 같은 경로 조각.
- 받아들인 오탐: 커밋 메시지·grep 패턴에 이 단어가 들어가면 묻는다. 다른 키를 읽는 `reg query HKCU\Software\Python`·`echo HKCU`도 이제 묻는다.
- 실행한 것: `tests/test_private_paths.py` 174 passed·1 skipped(새 test 59건, 승인 표기 36건은 반영 전 실패 확인), 관련 test 7개 파일 572 passed. 리뷰 반영 뒤 `tests/test_private_paths.py` 208 passed·1 skipped(활성 경로 0개 test 6건은 반영 전 실패 확인), 관련 test 22개 파일 838 passed. 레지스트리 전체로 넓힌 뒤 `tests/test_private_paths.py` 234 passed·1 skipped(새 test 30건 중 18건은 반영 전 실패 확인), 관련 test 6개 파일 632 passed.
- 미해결: 글자 대조라 실행 중 조립한 레지스트리 접근(문자열 조립, workdir에 쓴 스크립트)은 못 잡는다. 실제 격리는 #298에 적은 OS 수준 선택지가 필요하다. token 범위를 필요한 저장소로 좁히는 것이 1차 방어다.
- 근거: `labhq/private_paths.py`, `labhq/policy.py`, `tests/test_private_paths.py`.
