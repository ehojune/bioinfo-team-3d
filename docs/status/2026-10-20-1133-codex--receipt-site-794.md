# 재현 receipt 실행 site-packages (#794, PR #800)

## 결론

PR #800, 기능 커밋 `64f1527`. 재현 receipt 실행(`rerun=True`)의 Python 명령에서 `-S`를 뺐습니다(`-E -s -S -B` → `-E -s -B`). 이제 실행 interpreter(venv·conda)의 site-packages를 불러옵니다. 문법 검사는 `-I -S -B` 그대로입니다. Refs #794.

## 재현 (현재 main `eb09e52`)

PR #789가 `-I`를 `-E -s`로 풀었지만 `-S`를 남겼습니다. `-S`는 `site` 초기화를 건너뛰므로 venv의 site-packages가 `sys.path`에 들어가지 않습니다. 그래서 numpy·pandas처럼 외부 패키지를 쓰는 실제 분석 rerun은 모두 `ModuleNotFoundError`로 `fail`이었습니다. PR #789의 fake importer는 site-packages를 다루지 않아 이 결함을 놓쳤습니다.

## 바꾼 것

- `labhq/reproduction_receipt.py`: 실행 명령을 `-E -s -B <script>`로 바꿨습니다. 그대로 둔 것은 다음과 같습니다.
  - staged sibling import(script 폴더가 `sys.path` 앞에 옴)
  - script별 산출 판정과 hard link·링크 폴더 보호(#771·#781)
  - `PYTHONPATH`·사용자 site·호출한 폴더 격리
  - 문법 검사의 `-I -S -B`
- receipt `command` 값만 바뀌고 schema·상태·요약은 그대로입니다.
- 문서: `docs/manual.md`의 #772 문장 하나를 사실에 맞게 고쳤습니다. 상세는 `docs/reference/reproduction-receipt.md`에 한 줄로 더했습니다.

## 검증: 로컬 (mock/static만)

- fake importer가 `-S`일 때 site-packages를 빼도록 고치고, mock test를 하나 더했습니다. 수정 전 코드에서는 site-packages module import가 `fail`이었고, 기존 #772 test의 명령 기대값도 실패했습니다(5 failed). 수정 뒤에는 #771·#781·#772 test를 포함해 18 passed, 2 skipped입니다.
  - skip 하나는 symlink 권한이 필요한 test입니다.
  - 다른 하나는 아래의 실제 interpreter test입니다. mock guard가 켜져 있으면 프로세스를 띄우기 전에 skip하도록 했고, guard 로그에도 이 test의 기록이 없습니다.
- Python 3.10 문법(`ast.parse(feature_version=(3, 10))`), `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 검증: CI (실제 interpreter, PI가 이 test로 한정해 승인)

- 실제 interpreter test는 `test_a_real_isolated_rerun_imports_site_packages_and_siblings_but_not_the_callers_paths` 하나입니다.
- tmp fixture만 쓰고, 현재 interpreter를 receipt의 flag와 최소 환경으로 띄웁니다.
- 확인하는 것:
  - staged `helper.py`를 import하는지
  - site-packages의 `yaml`을 import하는지(그 파일이 `site.getsitepackages()` 아래에 있는지 먼저 확인)
  - 호출한 폴더, `PYTHONPATH`, 사용자 site(`PYTHONUSERBASE`), 다른 step 폴더의 module이 import되지 않고 `sys.path`에도 없는지
  - `PYTHONPATH`와 `PYTHONUSERBASE`를 receipt 입력으로 넘겨 자식 환경에 실제로 들어가게 했는지. 그래서 `-E -s`가 막는 것을 직접 확인합니다.
- CI에서 이 test는 `pytest (3.10)`·`pytest (3.12)`(Ubuntu)와 `pytest-windows`(3.12)에서 돕니다. 이 PR을 열 때 CI 결과는 아직 없습니다.

## 남은 것·한계

- **Windows + Python 3.10 조합은 CI에 없습니다.** 지금 CI의 Windows job은 3.12 하나뿐입니다. workflow는 이 PR의 담당 범위 밖이라 바꾸지 않았습니다.
- **실제 분석 rerun PASS가 아닙니다.** 실제 interpreter 검증은 tmp fixture 하나뿐입니다. numpy 같은 과학 패키지로 실제 분석을 다시 돌린 것이 아닙니다.
- **interpreter 환경의 `.pth` 파일도 처리됩니다.** `-S`를 빼면 site-packages의 `.pth`도 처리되는데, 이는 일반 `python script.py`와 같습니다.
- #773과 다른 감사 항목은 다루지 않았습니다.
