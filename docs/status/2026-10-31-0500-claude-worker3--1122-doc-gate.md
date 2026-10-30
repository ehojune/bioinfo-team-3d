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

## 리뷰 후 추가 (rev2)
- P1: `check()`가 현재 PR도 세던 문제. 커밋들이 바꾼 파일의 합집합이 전부 invisible이면 refresh 한도에 더하지 않는다. 순 diff가 아닌 합집합이라 README를 고쳤다 되돌린 PR은 계속 막힌다. 판정은 `invisible()` 하나로 main 커밋과 PR이 같이 쓴다.
- 시험 2건 추가(시험 전용 PR 통과, 제품 코드 혼합 PR 차단). `tests/test_patch_notes.py` 21 passed.

## 리뷰 후 추가 (rev3)
- P2: rename 감지가 켜져 있으면 `git diff --name-only`가 목적지만 돌려줘 `labhq/` 파일을 `tests/`로 옮긴 커밋이 invisible로 면제됐다. 두 diff 호출에 `--no-renames`를 줬다.
- 시험 1건(rename 커밋이 센다). 옛 스크립트로 실패, 새 스크립트로 통과를 확인했다. `tests/test_patch_notes.py` 22 passed.
