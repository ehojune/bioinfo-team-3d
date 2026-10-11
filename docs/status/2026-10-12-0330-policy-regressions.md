## 2026-10-12 · v0.5.0 이후 교차 리뷰 #2 #3 #16 #17 — 승인 게이트·공유 환경 설치 판정 회귀

## 결론

교차 리뷰가 확인한 결함 4건을 고쳤습니다. 결함마다 사례 하나가 아니라 그 부류를 닫는 구조 변경 하나씩입니다.

| 리뷰 | 등급 | 닫은 부류 | 변경 |
|---|---|---|---|
| #2 | P1 (v0.5.0 대비 회귀) | 다운로드 명령의 쓰기 경로가 쓰기 허용 폴더 검사에 닿지 않는 경우 | curl·wget 옵션을 표 하나로 읽는 walker. 출력 operand는 변수 앞 리터럴 부분(`-o "C:/x/$f"` → `C:/x/`)으로 검사. 묶은 짧은 옵션(`-fsSLo`, `-qO`), 붙여 쓴 값, `=` 형식, getopt 약어(`--out`), 파일을 쓰는 다른 옵션(curl `-D`·`-c`·`--trace` 등, wget `-o`·`-a`), PowerShell `Invoke-WebRequest`·`Invoke-RestMethod` `-OutFile`(Windows PowerShell의 `curl`·`wget` 별칭 포함) |
| #3 | P1 | 다른 프로그램의 인자 자리에 있는 변수를 설치 선택자로 보는 경우 | "변수 인자가 있으면 설치" 규칙을 실행 파일 자리에만 둠. 인자 자리 변수는 리터럴 설치 동사(`install`·`create`)가 뒤따를 때만 설치 |
| #16 | P2 | 프로그램 이름이 변수라서 R 설치 호출을 놓치는 경우 | 따옴표로 감싼 변수 실행 파일이 R 설치 호출을 받으면 R로 보고 Rscript와 같은 `lib=` 규칙 적용. wrapper 뒤 인자 자리의 변수도 R일 수 있는 것으로 봄 |
| #17 | P2 | lexical 정규식이 못 보는 재귀 삭제 | rm의 `--` 앞 모든 옵션 단어(`-f -r`, `-v -rf`, `--recursive`, 대상 뒤 `-rf`)를 보고 기존 `.tmp` 규칙으로 보냄. `find -delete`·`-exec rm`(`sh -c` 본문 포함)·`\| xargs rm`은 연결 없는 단일 명령이고 시작 경로가 리터럴이며 쓰기 허용 폴더 안일 때만 허용. cmd `rd /s`·`del /s`와 PowerShell `Get-ChildItem -Recurse \| Remove-Item`은 언제나 질문 |

## 바뀐 것

- `labhq/policy.py`
  - `_shell_word_prefix`: shell word의 리터럴 앞부분과 완결 여부. `_literal_shell_word`는 이 위의 얇은 wrapper라 동작이 같습니다.
  - `_DOWNLOAD_OPTIONS` 표와 `_download_write_targets` 재작성. `_download_options_known`도 같은 표를 씁니다. 모르는 글자가 낀 묶음은 그 뒤 출력 글자의 값을 보고합니다(과보고는 허용 폴더 밖 절대경로일 때만 질문이 됨).
  - `_ps_download_targets`: `-OutFile`의 모든 prefix.
  - `_recursive_rm`, `_find_deletes`, `_find_delete_inside_roots`, `_CMD_RECURSIVE_DELETE`, `_PS_PIPED_RECURSIVE_DELETE`. 설정의 `BASH_RECURSIVE_DELETE_PATTERN`은 바꾸지 않았고, 이 pattern이 `bash_ask_patterns`에 있을 때만 새 검사도 돕니다(설정에서 빼면 재귀 삭제 검사 전체가 꺼지는 기존 의미 유지).
  - `.tmp` 재귀 삭제 허용이 `-v`·`--recursive`·`--force`·`--verbose`도 받습니다. 새로 재귀로 잡히는 `rm -v -rf .tmp/x`가 질문으로 바뀌지 않게 하려는 것입니다.
- `labhq/environment_install.py`: `_dynamic_installer_syntax`의 비리터럴 인자 조항을 실행 파일 자리로 한정. 주 루프에 `r_program`(R 실행 파일 또는 R 설치 호출을 받는 변수 실행 파일). `_installation_syntax`의 R 검사가 변수 단어도 R 후보로 봅니다.
- `docs/manual.md`: 공유 환경 설치 문단, 승인 게이트의 재귀 삭제 문단, 셸 쓰기 검사 항목.

## 실행한 것

- main에서 재현: 네 결함 모두 리뷰가 적은 대로 재현(`evaluate_tool` windows=True, `shared_environment_install_denial` protected=True).
- 새 test 97건: origin/main 코드에서 66건 실패, 이 branch에서 95건 통과·2건 skip(Windows 일반 계정은 symlink를 못 만들어 link 시작 경로 test 2건 skip). 나머지 29건은 유지 동작을 고정하는 test로 main에서도 통과합니다(`"$PY" script.py "$INPUT"` 거절, `.tmp` 아래 삭제 허용, 작업 폴더 안 다운로드 허용 등).
- 기존 test는 지우거나 바꾸지 않았습니다. 관련 4개 파일 753 passed·7 skipped.
- Windows 전체 pytest: 5110 passed, 66 skipped. main(#520) 병합 뒤 5111 passed, 66 skipped.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check`, `git diff --check` 통과.

## 미해결

- `~`로 시작하는 쓰기 경로(`cp a ~/x`, `echo x > ~/.bashrc`, `curl -o ~/x`)는 절대경로로 보지 않아 쓰기 허용 폴더 검사를 받지 않습니다. 모든 쓰기 명령에 걸친 다른 부류이고 v0.5.0에도 있던 것이라 이 PR에서 고치지 않았습니다.
- 재귀 삭제 검사는 여전히 lexical입니다. `echo "rm -f -r x"`, `git rm --cached -r x`처럼 지우지 않는 명령도 질문할 수 있고, 미리 허용된 셸(`Bash(python *)` 등) 안의 삭제는 게이트를 거치지 않습니다.
- `find -delete`는 쓰기 허용 폴더 안이면 `.tmp` 밖이어도 허용합니다(리뷰 권고대로). `rm -rf`의 `.tmp` 한정보다 넓습니다.

## 근거

- `labhq/policy.py`
- `labhq/environment_install.py`
- `tests/test_shell_write_quotes.py`
- `tests/test_policy.py`
- `tests/test_environment_install.py`
- `docs/manual.md`
