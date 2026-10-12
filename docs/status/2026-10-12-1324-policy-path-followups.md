## 단계
P1·P2 — 승인 게이트 경로 판정 후속: #533(PR #525 봇 지적 3건), #521의 `~` 쓰기 경로, #178

## 한 일

같은 부류의 더 좁은 변형이 이어지던 곳이라 사례 대신 부류를 닫았습니다. 리터럴로 다 읽을 수 없는 삭제 payload·대상·`~` 표기는 PI에게 묻고, 읽을 수 있으면 같은 `.tmp` 규칙을 적용합니다.

| 항목 | main 재현 | 수정 |
|---|---|---|
| #533 P1 find payload | 재현: `find .tmp -exec rm $'-rf' /outside \;`, `-exec rm -f /outside`, `-exec $CMD {}`, `rm $'-rf' /x` 허용 | `-exec`·`-ok` payload는 리터럴 `rm`·`unlink` 한 명령으로만 읽고 `{}` 밖 대상도 `.tmp` 규칙으로 본다. 변수 명령, `$'..'` 단어, `sh -c 'rm ..'`·`env rm` 같은 감싼 삭제, `{}.bak`, `-execdir`의 `{}` 밖 대상은 묻는다. `rm`의 `--` 앞 단어가 확장으로 시작하면(`$'-rf'`, `"$OPT"`, `-f"$X"`, `{-r,x}`) 재귀 삭제로 본다. |
| #533 P1 PowerShell script block | 재현: `gci .tmp \| % { [IO.DirectoryInfo]::new('C:/outside').Delete($true) }`, `\| % { 'C:/x' } \| Remove-Item`, `\| % { iex $code }` 허용 | pipe 뒤를 문법으로 읽는다. 중간 단계는 항목을 그대로 넘기는 `Where-Object`·`Sort-Object`·`Select-Object -First` 등, 마지막은 경로 없는 `Remove-Item`이거나 `$_`·`$PSItem`만 지우는 `Remove-Item`·`$_.Delete()`. 그 밖은 묻는다. pipe 밖 .NET 삭제(`[IO.Directory]::Delete(..)`, `(Get-Item x).Delete($true)`)도 묻는다. |
| #533 P2 R 판정 범위 | 재현: `python wrapper.py "$INPUT" "install.packages('x')"` 거부 | R 호출 판정은 프로그램 자리 단어(첫 단어, `&` 뒤, `nohup`·`sudo -u x`·`uv run` 같은 wrapper가 실행하는 단어)에만 건다. wrapper 옵션을 모르면 모든 단어를 본다. |
| #521 `~` 쓰기 대상 | 재현: `cp a ~/x`, `> ~/.bashrc`, `curl -o ~/x`, `tee ~/x`, `Set-Content ~/x` 허용 | 쓰기 대상의 `~`를 Bash는 `HOME`(없으면 `USERPROFILE`·`HOMEDRIVE`+`HOMEPATH`), PowerShell은 Windows에서 `USERPROFILE`·`HOMEDRIVE`+`HOMEPATH`로 펼쳐 모두 허용 폴더 안일 때만 허용. `~user`·`~+`·`~-`, home을 모를 때는 묻는다. 읽기 경로는 그대로. |
| #178 링크의 다른 표기 | 재현: `policy.private_paths: []`이면 통제 구역 링크가 있어도 맨 `Read`·셸 규칙을 미리 허용해, `DATA~1/raw.tsv`는 거부 규칙을 비껴 게이트를 거치지 않음 | 그런 링크가 있는 Claude 단계는 개인 경로 차단처럼 셸 규칙을 빼고 읽기를 작업 폴더 `Read(//폴더/**)`로 좁힌다(실측 `claude_read_scope_alias.json`: 링크로 폴더 밖에 닿는 읽기는 사전 허용되지 않음). 게이트는 실제 경로로 비교한다. Windows 11 실측 test에서 junction의 8.3 이름·대소문자 표기 모두 Read 거부·셸 승인 요청(8.3이 꺼진 볼륨은 skip). |

## 테스트 결과

- [x] 새 test 59개(파라미터 포함): main에서 45개 실패, 수정 뒤 통과. 14개는 main에서도 통과하는 guard(기존 허용 형태 유지, 8.3·대소문자 실측 2개 포함: 게이트는 원래 실제 경로를 비교했고 빈틈은 사전 허용이었음).
- [x] 기존 허용 test(분석 명령, `.tmp` 정리) 그대로 통과. 약하게 바꾼 test 없음.
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5385 passed, 66 skipped (`--basetemp`·`LABHQ_STATE_DIR`는 %TEMP% 아래 한 폴더, 끝나고 지움)
- [x] `scripts/check_public.sh`
- [x] `python scripts/patch_notes.py check --base origin/main --head HEAD`

## 바꾼 파일

- `labhq/policy.py`, `labhq/environment_install.py`, `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `labhq/runner/daemon.py`
- `tests/test_policy.py`, `tests/test_environment_install.py`, `tests/test_project_link_spellings.py`(새)
- `docs/manual.md`(승인 게이트와 쓰기 범위, 알려진 한계의 #178 문단)

## 바뀌는 동작

- `rm -f "$f"`처럼 `--` 앞에 변수로 시작하는 단어가 있으면 PI에게 묻는다. `rm -f -- "$f"`는 그대로 허용. 어휘 판정이라 `git rm "$f"`·`docker rm "$id"`도 묻는다.
- 감싼 본문에 삭제 단어가 없는 `find ... -exec sh -c '...'`는 예전처럼 허용(최상위 `sh -c`와 같은 어휘 판정의 한계).
- 통제 구역 링크가 있는 project 폴더를 연 Claude 단계는 셸 명령마다 게이트를 거친다. 개인 경로 차단이 켜진 기본 설정에서는 이미 그렇다.

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
