# Windows runner 전용 계정

runner만 별도 표준 사용자로 실행하면 직원 셸이 PI 홈을 직접 읽는 일을 OS 권한으로 막을 수 있습니다. gateway는 지금처럼 PI 계정에서 실행합니다.

| 프로세스 | Windows 계정 | 접근 범위 |
|---|---|---|
| `labhq gateway` | PI | 기존 설정·state, `gateway.yaml` |
| `labhq runner`와 직원 CLI | `labhq-runner` | runner 작업 폴더·state, `runner.yaml`·labhq 설치본·참고 자료 읽기 |

아래 `<PI계정>`은 실제 로컬 계정명으로 바꾸세요. 명령은 계정과 ACL을 실제로 바꾸므로 경로를 다시 확인한 뒤 실행합니다. **1번부터 순서대로** 실행합니다. 뒤 단계는 앞 단계가 만든 폴더·프로필·파일을 씁니다.

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

## 2. 폴더·폴더 권한·labhq 설치

PI 홈 아래 checkout과 venv는 runner가 읽지 못합니다(아래 거부 규칙). 그래서 runner가 쓸 labhq를 `C:\LabHQ\app`에 따로 설치합니다. 직원 명단(`agents/`)도 그 안에 들어 있습니다. 작업 폴더·state·talent에는 runner 수정 권한, 설치본과 참고 자료에는 읽기 권한만 줍니다. PI는 설정을 관리하고 결과를 읽습니다.

관리자 PowerShell:

```powershell
$RunnerAccount = "$env:COMPUTERNAME\labhq-runner"
$PiAccount = "$env:COMPUTERNAME\<PI계정>"
New-Item -ItemType Directory -Force -Path C:\LabHQ\config,C:\LabHQ\runner\work,C:\LabHQ\runner\state,C:\LabHQ\runner\talent,C:\LabHQ\refs

git clone https://github.com/ehojune/bioinfo-team-3d.git C:\LabHQ\app
py -3.12 -m venv C:\LabHQ\app\.venv
C:\LabHQ\app\.venv\Scripts\pip.exe install -e C:\LabHQ\app

icacls 'C:\LabHQ' /inheritance:r
icacls 'C:\LabHQ' /grant:r 'SYSTEM:(OI)(CI)(F)' 'BUILTIN\Administrators:(OI)(CI)(F)' "${RunnerAccount}:(RX)" "${PiAccount}:(RX)"
icacls 'C:\LabHQ\config' /grant:r "${PiAccount}:(OI)(CI)(F)"
icacls 'C:\LabHQ\app' /grant:r "${RunnerAccount}:(OI)(CI)(RX)" "${PiAccount}:(OI)(CI)(RX)"
icacls 'C:\LabHQ\runner' /grant:r "${PiAccount}:(OI)(CI)(RX)"
icacls 'C:\LabHQ\runner\work' /grant:r "${RunnerAccount}:(OI)(CI)(M)"
icacls 'C:\LabHQ\runner\state' /grant:r "${RunnerAccount}:(OI)(CI)(M)"
icacls 'C:\LabHQ\runner\talent' /grant:r "${RunnerAccount}:(OI)(CI)(M)"
icacls 'C:\LabHQ\refs' /grant:r "${RunnerAccount}:(OI)(CI)(RX)"
```

PI 홈에는 runner 권한을 주지 않습니다. 기존 ACL에 `Users` 읽기 권한이 있어 실제로 읽힌다면 runner를 명시적으로 거부합니다.

```powershell
icacls 'C:\Users\<PI계정>' /deny "${RunnerAccount}:(OI)(CI)(F)"
```

## 3. runner 계정에서 직원 CLI 설치·로그인

PI 계정에서 runner 창을 엽니다. 처음 열 때 runner 프로필이 만들어집니다.

```powershell
runas /user:.\labhq-runner "powershell.exe -NoProfile"
```

새 창에서 격리부터 확인합니다.

- `whoami`가 `<컴퓨터이름>\labhq-runner`입니다.
- `Get-ChildItem 'C:\Users\<PI계정>'`은 **액세스가 거부되었습니다**로 끝납니다.
- `C:\LabHQ\runner\work`에는 임시 파일을 만들고 지울 수 있고, `C:\LabHQ\refs`와 `C:\LabHQ\app`에서는 수정이 거부됩니다.

PI 계정에 설치한 Claude Code·Codex는 PI 프로필 안에 있어 runner가 실행하지 못합니다. 같은 창에서 runner 프로필에 따로 설치하고 PATH에 올립니다. `node`가 없다는 오류가 나면 PI가 Node.js LTS를 모든 사용자용으로 설치한 뒤 창을 다시 엽니다.

```powershell
npm install -g @anthropic-ai/claude-code @openai/codex
$NpmBin = npm prefix -g
[Environment]::SetEnvironmentVariable('Path', "$NpmBin;" + [Environment]::GetEnvironmentVariable('Path', 'User'), 'User')
$env:Path = "$NpmBin;$env:Path"
Get-Command claude, codex
```

`Get-Command`가 두 명령 모두 `C:\Users\labhq-runner\...` 아래 경로를 보여야 합니다. 이어서 Claude와 Codex에 각각 한 번 로그인합니다. 로그인 정보는 runner 프로필에만 남습니다.

```powershell
claude
$CodexStaffHome = "$env:USERPROFILE\.labhq\codex-staff"
New-Item -ItemType Directory -Force $CodexStaffHome
[Environment]::SetEnvironmentVariable('CODEX_HOME', $CodexStaffHome, 'User')
$env:CODEX_HOME = $CodexStaffHome
codex login
```

Windows Codex 직원은 무인 실행 전에 elevated sandbox setup을 한 번 마쳐야 합니다(#262). 같은 창에서 다음 명령을 실행하고 UAC 창을 승인합니다.

```powershell
codex exec --skip-git-repo-check -C C:\LabHQ\runner\work -s workspace-write -c 'windows.sandbox="elevated"' 'setup-probe.txt에 ok를 쓰세요'
Test-Path "$env:CODEX_HOME\.sandbox\setup_marker.json"
Remove-Item C:\LabHQ\runner\work\setup-probe.txt -ErrorAction SilentlyContinue
```

`Test-Path`가 `True`여야 합니다. 더 약한 sandbox로 바꾸지 않습니다. 이 창은 5번에서 다시 씁니다.

## 4. 설정 두 개 만들기

설정은 **둘로 나눕니다.** `gateway.yaml`에는 `gateway.client_token`이 있으므로 PI만 읽습니다. `runner.yaml`은 같은 내용에서 client token 줄만 뺀 사본이고 runner는 이 파일 하나만 읽습니다. 직원은 runner 계정으로 돌기 때문에, runner가 읽는 파일에 client token이 있으면 직원이 그 token으로 PI 대신 승인할 수 있습니다. runner는 client token을 쓰지 않습니다.

관리자 PowerShell에서 PI가 지금 쓰는 설정 파일을 복사합니다(`$env:LABHQ_CONFIG`나 `--config`로 넘기던 파일).

```powershell
Copy-Item '<지금 쓰는 설정 파일>' C:\LabHQ\config\gateway.yaml
notepad C:\LabHQ\config\gateway.yaml
```

`gateway.yaml`에서 runner 쪽 값을 다음처럼 바꿉니다. 이미 있는 `runner:`·`engines.codex` 항목에 합치고, 같은 키를 한 번 더 쓰지 않습니다. gateway의 `state_dir`는 그대로 둡니다.

```yaml
runner:
  os_account: labhq-runner              # doctor가 이 계정으로 도는지 확인한다
  agents_dir: C:\LabHQ\app\agents       # 직원 명단: runner가 읽을 수 있어야 한다
  talent_dir: C:\LabHQ\runner\talent
  workspace_root: C:\LabHQ\runner\work
  state_dir: C:\LabHQ\runner\state
engines:
  codex:
    env:
      CODEX_HOME: C:\Users\labhq-runner\.labhq\codex-staff   # 3번에서 만든 경로
```

저장한 뒤 client token 줄을 뺀 `runner.yaml`을 만들고 파일 권한을 겁니다.

```powershell
Get-Content C:\LabHQ\config\gateway.yaml | Where-Object { $_ -notmatch '^\s*client_token\s*:' } | Set-Content C:\LabHQ\config\runner.yaml -Encoding utf8
Select-String -Path C:\LabHQ\config\runner.yaml -Pattern 'client_token'   # 아무것도 나오지 않아야 한다
icacls 'C:\LabHQ\config\runner.yaml' /grant:r "${RunnerAccount}:(R)"   # runner는 이 파일 하나만 읽는다
icacls 'C:\LabHQ\config\gateway.yaml' /setowner $PiAccount
icacls 'C:\LabHQ\config\runner.yaml' /setowner $PiAccount
```

2번과 다른 관리자 창이면 `$RunnerAccount`·`$PiAccount`를 다시 정의합니다. 이후 설정을 고칠 때는 `gateway.yaml`을 고치고 위 `Get-Content` 줄로 `runner.yaml`을 다시 만듭니다.

## 5. 점검하고 runner만 전용 계정으로 실행

3번의 runner 창에서 확인합니다. venv를 활성화하지 않으므로 runner 계정의 labhq는 항상 절대경로로 씁니다.

```powershell
Get-Content C:\LabHQ\config\gateway.yaml   # 액세스 거부여야 한다
C:\LabHQ\app\.venv\Scripts\labhq.exe --config C:\LabHQ\config\runner.yaml doctor
```

`runner account isolation`은 `ok`, `runner config holds client token` 경고는 없어야 하고(`runner.os_account`와 실제 실행 계정이 같을 때만 ok), 필요한 직원 행도 `ok`, 전체 `fail`은 0이어야 합니다.

PI 계정에서는 gateway를 기존 `labhq`로 띄웁니다.

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
4. 저장할 때 runner 암호를 입력한 뒤 수동 실행하고, PI 계정에서 `labhq --config C:\LabHQ\config\gateway.yaml status`로 연결을 확인합니다.

## labhq 업데이트

runner 설치본은 PI checkout과 따로 있으므로 관리자 PowerShell에서 따로 올립니다. 그 뒤 runner를 다시 시작합니다.

```powershell
git -C C:\LabHQ\app pull --ff-only
C:\LabHQ\app\.venv\Scripts\pip.exe install -e C:\LabHQ\app
```

## 되돌리기

1. 예약 작업을 사용 중지하거나 삭제하고 runner 프로세스를 끝냅니다. gateway는 건드리지 않습니다.
2. 필요한 작업 결과와 state를 PI 폴더로 복사합니다(PI는 `C:\LabHQ\runner`를 읽을 수 있습니다).
3. 관리자 PowerShell에서 명시적 거부와 부여 권한을 걷습니다.

```powershell
$RunnerAccount = "$env:COMPUTERNAME\labhq-runner"
icacls 'C:\Users\<PI계정>' /remove:d $RunnerAccount
icacls 'C:\LabHQ' /remove:g $RunnerAccount /t
```

4. 보관할 파일이 없음을 확인한 뒤 **설정 → 계정 → 다른 사용자**에서 계정을 제거하거나 `Remove-LocalUser -Name 'labhq-runner'`를 실행합니다.
5. PI 계정으로 runner를 다시 띄우면 `doctor`의 계정 격리 행이 `warn`이 되는 것이 정상입니다.
