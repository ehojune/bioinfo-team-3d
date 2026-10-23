# Windows 새 설치 검사 (#922)

lookup-only. 실제 모델 연구·사람 인증·HPC는 검증하지 않습니다.

```powershell
py -3 scripts\clean_install_check.py --source . --work-dir ..\labhq-clean-check
```

source는 커밋된 checkout, work-dir은 새/빈 폴더입니다. PATH의 `py`가 없으면 검사기를 실행한 Python으로 venv를 만듭니다. `--receipt <local.json>`으로 JSON을 따로 보존하고 `--guard-path <path>`로 보호 경로의 존재·mtime을 대조합니다.

| 단계 | 확인 근거 |
|---|---|
| clone·venv·editable install | source/clone commit 일치, Python 판본, `requirements.freeze.txt` |
| AGENTS 설치 | clone 안 `core.hooksPath=.githooks` 설정 |
| packaging | wheel 생성·sha256, bundled plugin 파일 포함 |
| 격리 | 첫 subprocess부터 전용 home/config/state/TEMP, 상속 설정·API token 제거 |
| init·doctor | 설정 생성, mock 전용 port, 실행 준비: 예 |
| mock 요청·verify | 요청 ID·mock 표식, verify exit 0, 요청 묶음 4개 파일 |
| down | 설치 venv 프로세스 잔류 0, 실패한 부분 기동도 강제 종료 검사 |
| 사람 인증·실제 모델 | SKIP. 실제 요청 전 사용자가 CLI 설치·로그인 후 doctor 확인 |

설치·state·요청 묶음·로그는 work-dir에 남습니다. `receipt.json`의 각 command/cwd와 `logs/*.stdout.log`·`*.stderr.log`는 **로컬 전용**이며 공개 저장소에 올리지 않습니다. work-dir을 생략하면 임시 설치판은 지웁니다.

자동 검사는 README의 clone→venv→install→init→doctor→up 절차를 따르고 mock 설정을 명시적으로 적용합니다. 웹 데모·open·사람 로그인·실제 직원 실행은 별도입니다. wheel은 포함 파일 검사이며 wheel 설치 실행 검증은 아닙니다.
