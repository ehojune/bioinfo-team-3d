# 재현 receipt 산출 격리 리뷰 P1 (#771, PR #781)

## 결론

P1 3건 가운데 실제 결함 1건은 `4a94324`에서 고쳤습니다. 나머지 2건(패치노트 행, 단계 보고)은 리뷰 대상 `7f7a6bc` 바로 다음 커밋 `7c2d170`에 이미 들어 있어 현재 head에서 재현되지 않습니다.

## 지적별

- 4318264421 (alias 경로의 시각): 앞 script가 선언 산출을 hard link로 만들거나 상위 폴더를 symlink·junction으로 두면, `os.utime`이 receipt 폴더 밖 원본의 시각을 0으로 바꿨습니다. 이제 선언 산출은 다음 세 조건을 모두 만족할 때만 시각을 돌리고 산출로 셉니다.
  - 링크가 아니다.
  - 실제 위치(`resolve`)가 단계 폴더 안이다.
  - `st_nlink == 1`이다.

  alias 산출은 건드리지 않고 `missing_output`으로 둡니다. 고침: `4a94324`.
- 4318264432 (patch-note 행): `patch_notes/entries/codex--receipt-isolation-771.yaml`의 `7f7a6bc` 행이 `7c2d170`에 있습니다. `patch_notes.py check --base origin/main` PASS. `entries/codex/` 아래에 두면 `notes_index.py`가 거부합니다.
- 4318264448 (단계 보고): `docs/status/2026-10-20-0505-codex--receipt-isolation-771.md`가 `7c2d170`에 있습니다.

## 검증 (mock/static만, 가짜 `subprocess.run` 사용)

- 새 test 2개(hard link, 링크 폴더)는 수정 전 head에서 2 failed였습니다. 원본 시각이 0으로 바뀌었습니다. 수정 뒤에는 #771 test와 mock test 12 passed, 1 skipped입니다.
  - 링크 폴더 test는 symlink 권한이 없으면 Windows junction으로 만듭니다.
  - skip된 1개는 symlink 권한이 필요한 끝 경로 링크 test입니다.
- 실제 interpreter를 띄우는 기존 receipt test는 mock guard가 막아 다시 돌리지 않았습니다. CI가 확인합니다.
- `check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것

script가 hard link나 링크로 만든 산출은 이번 실행이 만든 것이어도 `missing_output`입니다. 산출이 receipt 폴더 밖 파일을 가리키므로 보수적으로 세지 않습니다.
