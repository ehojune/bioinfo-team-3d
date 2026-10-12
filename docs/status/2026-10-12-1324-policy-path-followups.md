## 단계
P1·P2 — 승인 게이트 경로 판정 후속: #533(PR #525 봇 지적 3건), #521의 `~` 쓰기 경로, #178. 봇 리뷰 1회차(P1 1·P2 1)와 오탐 3건 반영.

## 한 일

같은 부류의 더 좁은 변형이 이어지던 곳이라 사례 대신 부류를 닫았습니다. 리터럴로 다 읽을 수 없는 삭제 payload·대상·`~` 표기는 PI에게 묻고, 읽을 수 있으면 같은 `.tmp` 규칙을 적용합니다.

| 항목 | main 재현 | 수정 |
|---|---|---|
| #533 P1 find payload | 재현: `find .tmp -exec rm $'-rf' /outside \;`, `-exec rm -f /outside`, `-exec $CMD {}`, `rm $'-rf' /x` 허용 | `-exec`·`-ok` payload는 리터럴 `rm`·`unlink` 한 명령으로만 읽고 `{}` 밖 대상도 `.tmp` 규칙으로 본다. 변수 명령, `$'..'` 단어, `sh -c 'rm ..'`·`env rm` 같은 감싼 삭제, `{}.bak`, `-execdir`의 `{}` 밖 대상은 묻는다. `rm`의 `--` 앞 단어가 확장으로 시작하면(`$'-rf'`, `"$OPT"`, `-f"$X"`, `{-r,x}`) 재귀 삭제로 본다. |
| #533 P1 PowerShell script block | 재현: `gci .tmp \| % { [IO.DirectoryInfo]::new('C:/outside').Delete($true) }`, `\| % { 'C:/x' } \| Remove-Item`, `\| % { iex $code }` 허용 | pipe 뒤를 문법으로 읽는다. 중간 단계는 항목을 그대로 넘기는 `Where-Object`·`Sort-Object`·`Select-Object -First` 등, 마지막은 경로 없는 `Remove-Item`이거나 `$_`·`$PSItem`만 지우는 `Remove-Item`·`$_.Delete()`. 그 밖은 묻는다. pipe 밖 .NET 삭제(`[IO.Directory]::Delete(..)`, `(Get-Item x).Delete($true)`)도 묻는다. |
| #533 P2 R 판정 범위 | 재현: `python wrapper.py "$INPUT" "install.packages('x')"` 거부 | R 호출 판정은 프로그램 자리 단어에만 건다: 첫 단어, `&` 뒤, wrapper(`nohup`·`sudo -u x`)와 실행기(`uv run`·`conda run`·`pixi run` 등)가 실행하는 단어. Python script·`-c` 뒤는 인자. 모르는 프로그램이면 모든 단어를 본다. |
| #521 `~` 쓰기 대상 | 재현: `cp a ~/x`, `> ~/.bashrc`, `curl -o ~/x`, `tee ~/x`, `Set-Content ~/x` 허용 | 쓰기 대상의 `~`를 Bash는 `HOME`(없으면 `USERPROFILE`·`HOMEDRIVE`+`HOMEPATH`), PowerShell은 Windows에서 `USERPROFILE`·`HOMEDRIVE`+`HOMEPATH`로 펼쳐 모두 허용 폴더 안일 때만 허용. `~user`·`~+`·`~-`, home을 모를 때는 묻는다. 읽기 경로는 그대로. |
| #178 링크의 다른 표기 | 재현: `policy.private_paths: []`이면 통제 구역 링크가 있어도 맨 `Read`·셸 규칙을 미리 허용해, `DATA~1/raw.tsv`는 거부 규칙을 비껴 게이트를 거치지 않음 | 그런 링크가 있는 Claude 단계는 개인 경로 차단처럼 셸 규칙을 빼고 읽기를 작업 폴더 `Read(//폴더/**)`로 좁힌다(실측 `claude_read_scope_alias.json`: 링크로 폴더 밖에 닿는 읽기는 사전 허용되지 않음). 게이트는 실제 경로로 비교한다. Windows 11 실측 test에서 junction의 8.3 이름·대소문자 표기 모두 Read 거부·셸 승인 요청(8.3이 꺼진 볼륨은 skip). |

### 리뷰 1회차 (a7cfd20 → 2002b27)

| 지적 | 수정 |
|---|---|
| P1: `uv --project foo run Rscript -e "install.packages('x')"`에서 `foo`를 subcommand로 읽어 Rscript를 놓침 | 실행기 표(`uv`·`uvx`·`conda`·`mamba`·`micromamba`·`pixi`·`poetry`·`npx`·`pnpm`)로 전역 옵션을 건너뛰어 subcommand와 그 프로그램을 찾는다. 표에 없는 옵션이 subcommand 앞에 있거나 subcommand가 변수면 뒤 단어를 모두 프로그램 자리로 본다. 공유 환경의 변수 프로그램 판정(`uv --project p run "$PIP" "$ACTION"`)도 같은 표를 쓴다. |
| P2: `Write-Output '$_.Delete()'` 같은 출력 문자열을 .NET 삭제로 읽음 | 쓰기 판정과 같은 quote-aware 처리로 data 명령의 따옴표 안을 빼고 찾는다. 판정이 불확실하면(`iex` 등) 원문을 본다. |
| 오탐: `docker rm "$id"`, `git rm "$f"`, `rm -f "$f"`가 승인 카드를 띄움 | 재귀 삭제 규칙은 실제 실행 파일이 rm일 때만 본다(명령 맨 앞, `sudo`·`xargs`·`find`·`sh` 같은 실행기나 변수 프로그램 뒤). `git rm`은 지금 트리 안이면 허용하고 `-C`·`--git-dir`·`--work-tree`·`core.worktree`·`GIT_DIR`·`GIT_WORK_TREE`가 있거나 앞 단어가 변수면 묻는다. 큰따옴표 변수 하나가 유일한 대상인 `rm`은 허용한다. |

## 테스트 결과

- [x] 새 test 119개(파라미터 포함). 1차 59개는 main에서 45개 실패, 리뷰 반영분 60개는 a7cfd20에서 42개 실패, 수정 뒤 모두 통과. 나머지 32개는 guard(허용 형태 유지, 계속 묻는 형태, 8.3·대소문자 실측 2개).
- [x] 기존 허용 test(분석 명령, `.tmp` 정리) 그대로 통과. 약하게 바꾼 test 없음.
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5445 passed, 66 skipped (`--basetemp`·`LABHQ_STATE_DIR`는 %TEMP% 아래 한 폴더, 끝나고 지움)
- [x] `scripts/check_public.sh`
- [x] `python scripts/patch_notes.py check --base origin/main --head HEAD`

## 바꾼 파일

- `labhq/policy.py`, `labhq/environment_install.py`, `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `labhq/runner/daemon.py`
- `tests/test_policy.py`, `tests/test_environment_install.py`, `tests/test_project_link_spellings.py`(새)
- `docs/manual.md`(승인 게이트와 쓰기 범위, 공유 환경 문단, 알려진 한계의 #178 문단)

## 바뀌는 동작 (main 대비)

- 새로 묻는 것:
  - `rm` 옵션 자리의 읽을 수 없는 단어: `rm $'-rf' x`, `rm -f $f`(따옴표 없음), `rm -f "$a" "$b"`, `rm "$f" x`
  - `.tmp` 밖을 지우거나 읽을 수 없는 `find -exec` 삭제
  - PowerShell에서 pipe 뒤 다른 경로 삭제, pipe 밖 .NET 삭제
  - home이 허용 폴더 밖일 때 `~`로 시작하는 셸 쓰기 대상
  - `git rm`에 `-C`·`--git-dir`·`--work-tree`·`core.worktree`·`GIT_DIR`·`GIT_WORK_TREE`가 있거나 앞 단어가 변수일 때
- 공유 환경에서 새로 거부하는 것: 표에 없는 실행기 옵션이나 모르는 실행기 뒤의 R·pip 설치 호출
- 새로 묻지 않는 것:
  - 지금 트리 안의 `git rm -r x`
  - 다른 프로그램의 `rm` 인자: `docker rm "$id"`, `echo rm -rf x`
  - `python wrapper.py "$IN" "install.packages('x')"`
- 그대로: `rm -f "$f"`(큰따옴표 변수 하나가 유일한 대상), `rm -f -- "$f"`, `.tmp` 정리, 분석 명령
- Claude 단계: 통제 구역 링크가 있는 project 폴더를 열면 셸 명령마다 게이트를 거친다. 개인 경로 차단이 켜진 기본 설정에서는 이미 그렇다.
- 한계: 감싼 본문에 삭제 단어가 없는 `find ... -exec sh -c '...'`는 예전처럼 허용(최상위 `sh -c`와 같은 어휘 판정).

## 막힌 점

- 없음. 독립 코드 리뷰는 돌리지 않음(Codex 일일 상한으로 Claude가 대신 맡은 레인).

## PI·Claude에게 물을 것

- 없음.

## 확인

- [x] `docs/status/2026-10-12-1324-policy-path-followups.md`에 같은 보고 추가
- [x] 실제 labhq 인스턴스 실행·재시작 없음

Closes #533
Closes #178
Refs #521

🤖 Generated with [Claude Code](https://claude.com/claude-code)
