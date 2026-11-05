# 재현 receipt sibling import (#772, PR #789)

## 결론

PR #789, 기능 커밋 `f5425c5`. 재현 receipt 실행에서, staged Python entrypoint가 함께 게시한 sibling module(`helper.py`, `lib/util.py`)을 import해도 `fail`로 기록되지 않습니다. Refs #772.

## 재현 (현재 main)

실행 명령이 `python -I -S -B <script>`였습니다. `-I`는 Python 3.11+에서 `-P`를 포함하고, 3.10에서도 script 폴더를 `sys.path`에서 뺍니다. 그래서 같은 폴더의 `helper.py`를 import하는 정상 재현이 `ModuleNotFoundError`로 `fail`이 됐습니다. 새 test 3개가 현재 main에서 모두 `fail`이었습니다.

## 바꾼 것

- Python 실행 명령을 `-E -s -S -B <script>`로 바꿨습니다. `-I`에서 `-P`만 뺀 조합입니다.
  - script의 staged 폴더만 `sys.path` 앞에 옵니다.
  - `-E`로 `PYTHONPATH` 같은 `PYTHON*` 환경을 무시하고, `-s`로 사용자 site를 뺍니다.
  - script를 실행할 때 호출한 폴더는 `sys.path`에 들어가지 않습니다.
- 넘기는 환경은 지금처럼 최소 목록(`PATH` 등과 `PYTHONNOUSERSITE=1`)입니다.
- 문법 검사는 그대로 `-I -S -B -c compile`이고 import하지 않습니다.
- receipt의 `command` 값만 바뀝니다. schema, 상태 이름, 요약은 그대로입니다.
- PR #781의 산출 판정(script별로 이번 실행이 쓴 산출만 셈)과 hard link·링크 폴더 보호는 손대지 않았습니다.
- `docs/manual.md` receipt 단락에 한 문장을 더했습니다.

## 검증 (mock/static만)

- 가짜 `subprocess.run`이 받은 flag와 환경으로 CPython 문서의 `sys.path` 규칙을 흉내 내 import를 풉니다. 실제 interpreter는 띄우지 않았습니다.
  - sibling, 하위 패키지, `steps/<id>/scripts/` 3경우: 수정 전 `fail`, 수정 뒤 `pass`.
  - 격리 test: 호출한 폴더, `PYTHONPATH`, 사용자 site, 다른 step 폴더에만 `helper.py`가 있으면 계속 `fail`입니다. 넘긴 환경에 `PYTHONPATH`·`PYTHONHOME`·`PYTHONSTARTUP`·`PYTHONUSERBASE`·`PYTHONSAFEPATH`가 없는지 확인합니다. 수정 전에는 flag 확인에서 실패했습니다.
  - 문법 검사는 `-I`를 유지하고 실행하지 않는지 확인합니다(회귀 방지).
- #771·#781 test를 포함한 mock receipt test는 17 passed, 1 skipped입니다. skip은 symlink 권한이 필요한 test입니다.
- `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것·한계

- **실제 import는 로컬에서 확인하지 못했습니다.** 실제 interpreter의 import 동작은 실행하지 않았습니다. 같은 파일의 기존 실제 interpreter test는 mock guard가 막으므로 CI가 확인합니다.
- **script 폴더 밖 module은 여전히 import되지 않습니다.** script 폴더만 경로에 들어가므로, 다른 step이나 상위 폴더의 module은 import되지 않습니다(일반 `python script.py`와 같음).
- **링크 처리는 Python 규칙을 따릅니다.** 앞 script가 script 파일을 receipt 폴더 밖을 가리키는 링크로 바꾸면, Python 규칙상 링크를 푼 폴더가 `sys.path[0]`이 됩니다. receipt는 sandbox가 아니라는 기존 전제(모듈 docstring) 안의 일이라 새 상태를 만들지 않았습니다.
- #773·#774는 다루지 않았습니다.
