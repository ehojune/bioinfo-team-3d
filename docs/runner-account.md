# Windows runner 전용 계정

runner만 별도 표준 사용자로 실행하면 직원 셸이 PI 홈을 직접 읽는 일을 OS 권한으로 막을 수 있습니다. gateway는 지금처럼 PI 계정에서 실행합니다.

| 프로세스 | Windows 계정 | 접근 범위 |
|---|---|---|
| `labhq gateway` | PI | 기존 설정·state |
| `labhq runner`와 직원 CLI | `labhq-runner` | runner 작업 폴더·state, 읽기 전용 설정·참고 자료 |

아래 `<PI계정>`은 실제 로컬 계정명으로 바꾸세요. 명령은 계정과 ACL을 실제로 바꾸므로 경로를 다시 확인한 뒤 실행합니다.

## 1. 로컬 표준 사용자 만들기

관리자 PowerShell 방법:

```powershell
$RunnerPassword = Read-Host 'labhq-runner 암호' -AsSecureString
New-LocalUser -Name 'labhq-runner' -Password $RunnerPassword -Description 'LabHQ runner 전용'
Add-LocalGroupMember -Group 'Users' -Member 'labhq-runner'
```

설정 화면 방법:

1. **설정 → 계정 → 다른 사용자 → 계정 추가**를 엽니다.
2. **이 사람의 로그인 정보를 가지고 있지 않습니다 → Microsoft 계정 없이 사용자 추가**를 고릅니다.
3. 이름을 `labhq-runner`로 정합니다.
4. **계정 유형 변경**에서 **표준 사용자**인지 확인합니다. 관리자 권한은 주지 않습니다.

## 2. 폴더와 권한 나누기

설정은 **둘로 나눕니다.** gateway 설정 `C:\LabHQ\config\gateway.yaml`에는 `gateway.client_token`이 있으므로 PI만 읽습니다. runner 설정 `C:\LabHQ\config\runner.yaml`은 같은 내용에서 `gateway.client_token` 줄만 지운 사본이고, runner에 읽기만 줍니다. 직원은 runner 계정으로 돌기 때문에, runner가 읽을 수 있는 파일에 client token이 있으면 직원이 그 token으로 PI 대신 승인할 수 있습니다. runner는 client token을 쓰지 않습니다. 작업 폴더와 runner state에는 수정 권한, 참고 자료에는 읽기 권한만 줍니다.

관리자 PowerShell 예:

```powershell
$RunnerAccount = "$env:COMPUTERNAME\labhq-runner"
$PiAccount = "$env:COMPUTERNAME\<PI계정>"
New-Item -ItemType Directory -Path C:\LabHQ\config,C:\LabHQ\runner\work,C:\LabHQ\runner\state,C:\LabHQ\refs -Force

icacls 'C:\LabHQ' /inheritance:r
icacls 'C:\LabHQ' /grant:r 'SYSTEM:(OI)(CI)(F)' 'BUILTIN\Administrators:(OI)(CI)(F)' "${RunnerAccount}:(RX)"
icacls 'C:\LabHQ\config' /grant:r "${PiAccount}:(OI)(CI)(F)"
icacls 'C:\LabHQ\config\runner.yaml' /grant:r "${RunnerAccount}:(R)"   # runner는 이 파일 하나만 읽는다
icacls 'C:\LabHQ\runner\work' /grant:r "${RunnerAccount}:(OI)(CI)(M)"
icacls 'C:\LabHQ\runner\state' /grant:r "${RunnerAccount}:(OI)(CI)(M)"
icacls 'C:\LabHQ\refs' /grant:r "${RunnerAccount}:(OI)(CI)(RX)"
icacls 'C:\LabHQ\config\gateway.yaml' /setowner $PiAccount
icacls 'C:\LabHQ\config\runner.yaml' /setowner $PiAccount
```

PI 홈에는 runner 권한을 주지 않습니다. 기존 ACL에 `Users` 읽기 권한이 있어 실제로 읽힌다면 다음처럼 runner를 명시적으로 거부합니다.

```powershell
icacls 'C:\Users\<PI계정>' /deny "${RunnerAccount}:(OI)(CI)(F)"
```

runner 계정 PowerShell에서 다음을 확인합니다.

- `whoami`가 `labhq-runner`입니다.
- 설정과 `C:\LabHQ\refs` 파일은 읽히지만 수정은 거부됩니다.
- `C:\LabHQ\runner\work`와 `state`에는 임시 파일을 만들고 지울 수 있습니다.
- `Get-ChildItem 'C:\Users\<PI계정>'`은 **액세스가 거부되었습니다**로 끝납니다.

### 2-1. runner가 읽을 labhq와 직원 명단, 설정 경로

PI 홈 아래 checkout과 venv는 runner가 읽지 못합니다(위의 거부 규칙). 그래서 runner가 쓸 labhq를 `C:\LabHQ\app`에 따로 둡니다. 직원 명단(`agents/`)도 그 안에 함께 들어갑니다.

관리자 PowerShell 예:

```powershell
git clone https://github.com/ehojune/bioinfo-team-3d.git C:\LabHQ\app
py -3.12 -m venv C:\LabHQ\app\.venv
C:\LabHQ\app\.venv\Scripts\pip.exe install -e C:\LabHQ\app
icacls 'C:\LabHQ\app' /grant:r "${RunnerAccount}:(OI)(CI)(RX)"
```

두 설정 파일 모두에서 runner 쪽 경로를 `C:\LabHQ` 아래로 바꾸고, runner 계정 이름을 적습니다.

```yaml
runner:
  os_account: labhq-runner              # doctor가 이 계정으로 도는지 확인한다
  agents_dir: C:\LabHQ\app\agents       # 직원 명단: runner가 읽을 수 있어야 한다
  talent_dir: C:\LabHQ\runner\talent
  workspace_root: C:\LabHQ\runner\work
  state_dir: C:\LabHQ\runner\state
```

`talent` 폴더도 `work`·`state`처럼 runner에 수정 권한을 줍니다(`New-Item C:\LabHQ\runner\talent`, `icacls ... (M)`). venv는 활성화하지 않으므로 runner 계정의 명령은 모두 `C:\LabHQ\app\.venv\Scripts\labhq.exe` 절대경로로 씁니다(아래 예시도 그렇게 적었습니다). gateway는 PI 계정의 기존 `labhq`를 그대로 씁니다.

## 3. runner 계정에서 CLI 로그인 한 번

PI 계정에서 다음 창을 열고 runner 암호를 입력합니다.

```powershell
runas /user:.\labhq-runner "powershell.exe -NoProfile"
```

새 창에서 Claude와 Codex에 각각 한 번 로그인합니다. 로그인 정보는 runner 프로필에만 남습니다.

```powershell
claude
$CodexStaffHome = "$env:USERPROFILE\.labhq\codex-staff"
New-Item -ItemType Directory -Force $CodexStaffHome
[Environment]::SetEnvironmentVariable('CODEX_HOME', $CodexStaffHome, 'User')
$env:CODEX_HOME = $CodexStaffHome
codex login
```

두 설정 파일의 `engines.codex.env.CODEX_HOME`도 이 경로로 맞춥니다. Windows Codex 직원은 무인 실행 전에 elevated sandbox setup을 한 번 마쳐야 합니다(#262). 같은 runner 창에서 다음 명령을 실행하고 UAC 창을 승인합니다.

```powershell
codex exec --skip-git-repo-check -C C:\LabHQ\runner\work -s workspace-write -c 'windows.sandbox="elevated"' 'setup-probe.txt에 ok를 쓰세요'
Test-Path "$env:CODEX_HOME\.sandbox\setup_marker.json"
Remove-Item C:\LabHQ\runner\work\setup-probe.txt -ErrorAction SilentlyContinue
```

`Test-Path`가 `True`여야 합니다. 더 약한 sandbox로 바꾸지 않습니다.

## 4. 점검하고 runner만 전용 계정으로 실행

runner 계정 창에서 먼저 확인합니다.

```powershell
C:\LabHQ\app\.venv\Scripts\labhq.exe --config C:\LabHQ\config\runner.yaml doctor
```

`runner account isolation`은 `ok`, `runner config holds client token` 경고는 없어야 하고(`runner.os_account`와 실제 실행 계정이 같을 때만 ok), 필요한 직원 행도 `ok`, 전체 `fail`은 0이어야 합니다.

PI 계정에서는 gateway를 기존 방식대로 둡니다.

```powershell
labhq --config C:\LabHQ\config\gateway.yaml gateway
```

간단히 시작하려면 PI 계정에서 runner만 `runas`로 엽니다.

```powershell
runas /user:.\labhq-runner "powershell.exe -NoProfile -Command C:\LabHQ\app\.venv\Scripts\labhq.exe --config C:\LabHQ\config\runner.yaml runner"
```

항상 켜 둘 때는 **작업 스케줄러 → 작업 만들기**를 씁니다.

1. **일반**: 사용자를 `labhq-runner`로 바꾸고 **가장 높은 수준의 권한으로 실행**은 끕니다.
2. **트리거**: 시작할 시점을 정합니다.
3. **동작**: 프로그램은 `C:\LabHQ\app\.venv\Scripts\labhq.exe`(절대경로, runner 계정의 PATH에는 없다), 인수는 `--config C:\LabHQ\config\runner.yaml runner`로 둡니다.
4. 저장할 때 runner 암호를 입력한 뒤 수동 실행하고 `labhq status`에서 연결을 확인합니다.

## 되돌리기

1. 예약 작업을 사용 중지하거나 삭제하고 runner 프로세스를 끝냅니다. gateway는 건드리지 않습니다.
2. 필요한 작업 결과와 state를 PI가 읽을 수 있는 곳에 보관합니다.
3. 관리자 PowerShell에서 명시적 거부와 부여 권한을 걷습니다.

```powershell
$RunnerAccount = "$env:COMPUTERNAME\labhq-runner"
icacls 'C:\Users\<PI계정>' /remove:d $RunnerAccount
icacls 'C:\LabHQ' /remove:g $RunnerAccount /t
```

4. 보관할 파일이 없음을 확인한 뒤 **설정 → 계정 → 다른 사용자**에서 계정을 제거하거나 `Remove-LocalUser -Name 'labhq-runner'`를 실행합니다.
5. PI 계정으로 runner를 다시 띄우면 `doctor`의 계정 격리 행이 `warn`이 되는 것이 정상입니다.
