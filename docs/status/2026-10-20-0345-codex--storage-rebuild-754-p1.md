# storage --rebuild 리뷰 P1 (#754, PR #777)

## 결론

P1 3건 중 실제 결함 1건을 `5b323a3`에서 고쳤습니다. 나머지 2건(패치노트 행, 단계 보고)은 리뷰 대상 `b448227` 다음 커밋 `0745184`에서 이미 들어가 현재 head에서 재현되지 않습니다.

## 지적별

- 4317176241 (case-only 별칭): `--rebuild --name`의 새 저장소가 지금 저장소와 같은지 대소문자를 무시해 비교합니다. 토큰이 있으면 GitHub `full_name`으로 이름을 바꾼 저장소의 옛 이름도 같은 저장소로 봅니다. 새 이름이 404면 새 저장소로 진행하고, 그 밖의 HTTP·연결 실패는 저장 상태를 바꾸지 않고 거부합니다. 고침: `5b323a3`.
- 4317176231 (기능 커밋 patch-note 행): `patch_notes/entries/codex--storage-rebuild-754.yaml`의 `b448227` 행이 `0745184`에 있습니다. `patch_notes.py check --base origin/main --head 0745184` PASS. 지적의 `d367480`은 이 저장소에 없는 커밋입니다. 기능 커밋 단독 범위(`b448227^..b448227`)는 행이 그 뒤 커밋에 있어 실패하며, PR 범위 검사가 기준입니다.
- 4317176235 (단계 보고): `docs/status/2026-10-20-0310-codex--storage-rebuild-754.md`가 `0745184`에 있습니다.

## 검증 (mock/static만)

- 새 test 4개(대소문자 별칭, 이름을 바꾼 저장소, 진짜 새 저장소는 새 이름만 조회, GitHub 오류 시 거부)는 수정 전 코드에서 4 failed, 수정 뒤 `tests/test_storage_rebuild_754.py` 10 passed.
- 같은 파일의 게시 경로 test는 실행 기록이 `git rev-parse`(`integrations.rounds._git_commit`)를 부르므로 그 함수를 stub했습니다.
- mock guard 아래에서 기존 storage test 4개 파일 중 8건이 guard 차단으로 실패했습니다. 차단된 호출은 이 PR이 바꾸지 않은 `rounds._git_commit`과 `runner/versions._run_probe`입니다. 규칙대로 guard 없이 다시 돌리지 않았고, 이 경계는 CI가 확인합니다.
- `check_public.sh`, `git diff --check`, `patch_notes.py check` 통과.

## 남은 것

`--name`으로 지금과 다른 기존 저장소를 고르면 그 저장소 루트에 올라갑니다. 이는 `--to github`와 같은 동작이고 게시 카드가 저장소 이름을 보여 줍니다. P1 범위 밖으로 남깁니다.
