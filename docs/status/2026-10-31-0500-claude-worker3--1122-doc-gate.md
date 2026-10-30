## 단계
#1122: 문서 주기 gate(main 커밋 3개마다 README·manual 갱신)가 시험만 바꾼 PR까지 막는 문제.

## 한 일
- `scripts/patch_notes.py`: `INVISIBLE` 접두사(`tests/`, `docs/status/`, `patch_notes/entries/`, `config/ci/`, `.github/`, `scripts/ci_`)를 두고, 바뀐 파일이 전부 그 안이거나 생성 파일인 커밋은 `counted_main_commits`에서 뺀다. `labhq/`·`agents/`나 다른 `scripts/`를 하나라도 바꾼 커밋은 그대로 센다.
- 규칙 문구: `patch_notes.py` 머리말, `CLAUDE.md`, `AGENTS.md`.
- 시험 두 건: 세지 않는 경우(tests+status+entries, ci 경로, 생성 목차 섞임), 여전히 세는 경우(제품 코드 혼합, agents, 그 밖의 scripts).

## 확인
- `tests/test_patch_notes.py` 19 passed.
- Windows inventory: `tests/test_patch_notes.py`는 `known_tests` 목록에만 있다.

## 하지 않은 것
- 이미 병합된 main 커밋의 재분류는 하지 않았다(규칙이 병합 뒤부터 새 계산으로 적용된다).
