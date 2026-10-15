# 도구 승인 판정 참고

조회용 기록. 사용법은 [매뉴얼](../manual.md#승인-게이트와-쓰기-범위)을 따른다.

## 재생 범위

2026-10-15 승인 추출 46건 중 완전한 입력은 26건이다. cwd·private_paths·open_reads 원본은 없다. 아래 수치는 합성 폴더와 명시한 읽기 권한으로 재생한 결과이며 PI instance 재실행 결과가 아니다.

| 입력 | 건수 | main 카드 | 변경 후 카드 |
|---|---:|---:|---:|
| 완전 입력, 합성 경로·가정한 실행 문맥 | 26 | 24 | 18 |
| 요약으로 복원한 mock `rm -rf tmp/` | 8 | 8 | 0 |
| 잘린 입력, 재생 불가 | 12 | 미확인 | 미확인 |
| 재생 가능한 합계 | 34 | 32 | 18 |

- 이미 허용: 사례 23·38. 새 허용: 9·10·12·16·17·18·24·28·29·32·34·35·36·40.
- 남은 18건: 스킬 실행·복합 명령 14, 저장된 Claude 출력 shell 접근 3, 다른 직원 venv 1. 보수적 판정이 남아 완료 조건인 오판 0건은 아직 달성하지 못했다.
- `tests/fixtures/approval_fatigue.json`은 원본 ID·개인 경로를 제거한 합성 사례다. 잘린 12건은 test에서도 skip한다. 모르는 명령 뒷부분을 만들어 넣지 않는다.
- 부류 허용: `python script.py ...` 또는 `uv run script.py ...`의 리터럴 인자. runner가 실제 task 문맥에서 매번 판정하고 스크립트 SHA-256·실제 경로·도구별로 묶는다. shell 표현식·다른 경로는 재사용하지 않는다.
- 자동 허용 기록은 `agent.log`의 `policy_rule=task_workspace`; broker가 끊기면 기록 전달은 보장하지 않는다.

## 기존 삭제 판정의 보조 규칙

현재 작업 폴더의 경로가 증명된 명령에는 위 예외가 먼저 적용된다. 그 밖 명령에는 다음 기존 판정이 남는다.

재귀 삭제는 연결·파이프·하위 셸이 없는 단일 `rm -r[f]` 또는 `Remove-Item -Recurse [-Force]` 명령이고, 모든 대상이 단계 작업 폴더의 `.tmp` 아래인 리터럴 상대 경로일 때만 허용합니다. `.tmp`나 대상의 상위 폴더가 link·junction이면 PI에게 묻습니다. `rm`은 `--` 앞의 모든 옵션 단어를 봅니다. `rm -f -r`, `rm -v -rf`, `rm --recursive`, 대상 뒤의 `-rf`도 재귀 삭제입니다. `--` 앞 단어가 변수·`$'..'`·명령 치환으로 시작하면(`rm "$OPT" x`, `rm -f $f`) `-r`일 수 있어 재귀 삭제로 봅니다. 큰따옴표로 감싼 변수 하나가 유일한 대상이면(`rm -f "$f"`) 옵션이어도 지울 대상이 없거나 파일 하나라 허용하고, `rm -f -- "$a" "$b"`처럼 `--` 뒤 대상도 허용합니다. 이 규칙은 실제 실행 파일이 rm일 때만 봅니다(명령 맨 앞, `sudo`·`xargs`·`find -exec`·`sh -c` 같은 실행기 뒤). `docker rm`은 rm이 아니고, `git rm`은 지금 작업 트리 안만 지우므로 허용합니다. `git rm`도 `-C`·`--git-dir`·`--work-tree`·`core.worktree`·`GIT_DIR`·`GIT_WORK_TREE`가 있거나 그 앞 단어가 변수면 묻습니다.
`find`가 `-delete`, `-exec rm`(`-execdir`·`-ok`), `| xargs rm`으로 지우는 것도 같은 규칙입니다. 시작 경로와 `-exec rm`의 `{}` 밖 대상이 모두 `.tmp` 아래 리터럴 상대 경로일 때만 허용하고, 이 문법으로 판정하는 작업 폴더의 다른 곳은 PI에게 묻습니다. 시작 경로를 안 쓰면 작업 폴더 전체로 보고, `| xargs rm`은 파이프라서, `-L`은 link를 따라가서 언제나 묻습니다. `-exec` 뒤가 리터럴 `rm`·`unlink` 한 명령으로 읽히지 않으면(변수 명령, `$'..'` 단어, `sh -c 'rm ..'`·`env rm` 같은 감싼 삭제, `{}.bak`, `-execdir`의 `{}` 밖 대상) 묻습니다(#533). cmd의 `rd /s`·`del /s`는 언제나 묻습니다. PowerShell에서 첫 pipe 뒤에 Remove-Item 계열(`ri`·`rm`·`del`·`erase`·`rd`·`rmdir`)이나 .NET 삭제(`.Delete(`·`::Delete(`)가 있으면 재귀 삭제로 봅니다. pipe 앞 `Get-ChildItem`의 폴더가 모두 `.tmp` 아래 리터럴 상대 경로이고 뒤 단계가 그 항목만 지울 때만 허용합니다. 중간 단계는 항목을 그대로 넘기는 `Where-Object`·`Sort-Object`·`Select-Object -First` 등이고, 마지막은 경로 없는 `Remove-Item`이거나 `$_`·`$PSItem`만 지우는 `Remove-Item`·`ForEach-Object { $_.Delete() }`입니다. 다른 경로를 지우거나 새 객체를 만드는 단계(`% { 'C:/x' }`, 계산 속성), `iex`는 묻고, pipe 밖 .NET 삭제도 리터럴 경로가 없어 묻습니다(#533). `Write-Output`·`Set-Content`가 출력·기록하는 따옴표 안 글자는 쓰기 판정처럼 빼고 봅니다(#535).
