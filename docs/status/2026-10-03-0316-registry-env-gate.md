## 2026-10-03 · 사용자 환경변수 레지스트리 읽기를 PI 승인으로 (#325)

- 결론: 개인 경로(`policy.private_paths`)가 켜진 task에서 Claude 직원의 Bash·PowerShell 명령이 사용자 환경변수 레지스트리(`HKCU\Environment`)를 읽으면 승인 게이트가 PI에게 묻는다. PI `GITHUB_TOKEN`은 직원 프로세스 환경에서는 지워지지만(#301) 같은 계정의 레지스트리에는 남아 있어서다. 판정을 조이기만 하고 느슨하게 만들지 않는다.
- 바뀐 것: `labhq/private_paths.py`에 `USER_ENV_REGISTRY` 정규식과 `user_env_registry_read`. `labhq/policy.py` `_private_decision`의 셸 판정 끝에 연결. README §8 개인 경로 절에 한 줄.
- 잡는 표기: `reg query/export/save`의 `HKCU\Environment`·`HKEY_CURRENT_USER\Environment`·`HKU\<SID>\Environment`·hive 전체(`reg query HKCU /s`), PowerShell `HKCU:\Environment`·`HKCU:Environment`·`Registry::HKEY_CURRENT_USER\Environment`·와일드카드(`HKCU:\Env*`)·`Set-Location HKCU:`, Git Bash `/proc/registry/...`, `[Environment]::GetEnvironmentVariable(s)`의 Process 아닌 대상(`'User'`·`1`·변수), `[EnvironmentVariableTarget]::User`, `winreg`·`Microsoft.Win32.Registry`와 `Environment`가 함께 쓰인 코드, `Win32_Environment`·`wmic environment`. 따옴표·backtick·`^`로 쪼갠 표기와 `/`·`\` 모두.
- 그대로 허용: `$env:X`·`%X%`·`set`·`printenv`·`os.environ`·`Get-ChildItem Env:`·Process 대상 `GetEnvironmentVariable`, `HKCU\Software\…` 같은 다른 키.
- 실행한 것: `tests/test_private_paths.py` 174 passed·1 skipped(새 test 59건, 승인 표기 36건은 반영 전 실패 확인), 관련 test 7개 파일 572 passed.
- 미해결: 글자 대조라 실행 중 조립한 키 이름과 스크립트 파일로 감싼 읽기는 못 잡는다. token 범위를 필요한 저장소로 좁히는 것이 1차 방어다.
- 근거: `labhq/private_paths.py`, `labhq/policy.py`, `tests/test_private_paths.py`.
