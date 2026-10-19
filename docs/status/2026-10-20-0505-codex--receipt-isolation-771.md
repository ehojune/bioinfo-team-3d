# 재현 receipt script별 산출 격리 (#771, PR #781)

## 결론

PR #781, 기능 커밋 `7f7a6bc`. 재현 receipt에서 같은 단계의 script 여러 개가 한 폴더와 같은 선언 산출을 함께 쓸 때 생기는 false PASS를 막았습니다. 산출을 만들지 않은 script가 앞 script가 남긴 파일 덕분에 `pass`가 되던 문제입니다. 이제 helper 파일 이름 순서와 관계없이 판정이 같습니다. Refs #771.

## 재현 (현재 main)

`labhq/reproduction_receipt.py`는 script를 파일 이름 순서로 같은 `steps/<id>`·`code/<id>` 폴더에서 실행했고, 산출은 파일이 있는지만 봤습니다. 새 test에서 `make.py`가 `result.txt`를 쓰면 다음 결과가 나왔습니다.
- `zz_helper.py`: `pass` (거짓)
- `aa_helper.py`: `missing_output`

## 바꾼 것

- script를 실행하기 전에, 앞 script가 남긴 선언 산출의 수정 시각을 0으로 돌려 둡니다. 실행 뒤에는 모든 선언 산출을 그 실행이 만들거나 다시 썼을 때만 `pass`로 봅니다. 같은 바이트를 같은 시각에 다시 써도 구분됩니다.
- 링크와 폴더는 건드리지도 세지도 않습니다.
- 폴더 공유는 그대로 둡니다. 형제 소스(`lib/util.py`)와 선언하지 않은 중간 파일은 뒤 script가 그대로 읽습니다. 소스만 임시 폴더에 두는 것, 입력을 명시하는 것, 경로 검사는 바꾸지 않았습니다.
- `execution.output_check` 값을 `declared_files_exist_only`에서 `declared_files_written_by_this_run`으로 바꿨습니다. receipt schema와 summary 집계는 그대로입니다.
- `docs/manual.md`의 receipt 단락에 한 문장을 더했습니다.

## 검증 (mock/static만, 가짜 `subprocess.run` 사용)

- 새 test 5개를 만들었고, 수정 전 실행에서 3 failed, 1 passed, 1 skipped였습니다.
  - helper가 앞에 오든 뒤에 오든(`aa_`/`zz_`) 판정이 같은지
  - 두 script가 같은 바이트를 써도 둘 다 `pass`인지
  - 중간 파일, 형제 소스, 폴더 위치가 그대로인지
  - 앞 script가 남긴 링크를 건드리지도 세지도 않는지
- 수정 뒤에는 4 passed, 1 skipped입니다.
- 링크 test는 이 계정이 symlink를 만들 수 없어 skip됐습니다. 링크 경로는 로컬에서 확인하지 못했습니다.
- `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것·한계

- **기존 receipt test는 로컬에서 확인하지 못했습니다.** 같은 파일의 기존 test 16개는 실제 Python/bash interpreter를 띄우므로 mock guard가 막았습니다. 다시 돌리지 않았고, CI에 맡깁니다.
- **helper 행은 항상 `missing_output`입니다.** 지금은 helper가 앞에 정렬될 때만 그랬는데, 이제 순서와 관계없이 그렇습니다. 그래서 helper가 있는 단계의 요약은 `missing_output`입니다. 어느 script가 entrypoint인지, 어느 산출을 쓰는지는 계획에 정해진 칸이 없어서, 그 계약은 만들지 않았습니다.
- **산출을 나눠 만드는 단계도 `missing_output`입니다.** script마다 "선언한 산출 전부"를 요구하는 기존 규칙은 그대로라, 여러 script가 산출을 나눠 만드는 단계는 각 행이 `missing_output`입니다(지금과 같음).
- **조건부로 쓰는 script는 놓칠 수 있습니다.** 파일이 이미 있으면 쓰지 않는 script는 앞 script 뒤에 실행되면 `missing_output`이 됩니다. 거짓 PASS가 아니라 보수적으로 틀리는 쪽입니다.
- #772–#774는 다루지 않았습니다.
