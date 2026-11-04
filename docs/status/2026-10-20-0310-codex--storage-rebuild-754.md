# storage --rebuild (#754, PR #777)

## 결론

PR #777. `labhq storage <요청 id> --rebuild [--name <새 저장소>] [--visibility public]`를 넣었습니다. 이미 게시한 요청의 정리본을 지금 편집기로 다시 만들고, 게시 카드에서 승인한 뒤에만 같은 저장소의 새 커밋이나 새 저장소로 올립니다. Fixes #754.

## 바꾼 것

- `ResultStorage.rebuild`, `POST /api/requests/{id}/storage/rebuild`, CLI `--rebuild`를 추가했습니다. `--redraft`·`--republish`·`--to`와 함께 쓸 수 없습니다.
  - `--name`이 없으면 같은 저장소와 branch를 씁니다.
  - `--name`이 있으면 `--to github`와 같은 탐침(`GET /user`, 이름 없는 `POST /user/repos`)을 거친 뒤 새 저장소를 고릅니다. 새 저장소는 기본 비공개이고, 원본 저장소는 바꾸지 않습니다. 지금 저장소와 이름이 같으면 거부합니다.
- 게시 카드: 다시 만든 판은 `auto_publish_results`가 켜져 있어도 카드를 띄웁니다.
- README: 편집자가 README를 고친 뒤 제목 아래에 한 줄을 넣습니다. labhq 판본·commit, 다시 만든 날짜, 원본 저장소 링크입니다. 같은 저장소에 올렸다면 원본 링크 대신 "이전 판은 이 저장소의 커밋 기록에 있습니다"라고 적습니다.
- 이전 판의 mode·repo·branch·report_url은 `result_storage.editions`에 남기고, `result_storage.rebuild`에는 편집기 판본을 기록합니다.
- 거부하는 경우: 게시하지 않은 요청(`--redraft`·`--to github` 안내), 끝나지 않은 요청, 검토 완료 전 요청, 묶음 갱신 중인 요청, `revised_to`가 있는 요청, 끝 저장 위치 질문이 남은 요청, 통제 데이터 요청, 정리·게시가 진행 중인 요청. 거부하면 저장 상태를 바꾸지 않습니다.
- `--redraft`·`--republish`가 게시한 요청을 거부할 때, 그리고 `--to github` 안내에서 `--rebuild`를 알려 줍니다.
- 문서: `docs/manual.md`의 정리본 절에 한 단락을 더했고, #688 단락의 안내를 고쳤습니다.

## 검증 (mock/static만)

- 새 `tests/test_storage_rebuild_754.py` 6개: 같은 저장소에 새 판, `--name`으로 새 저장소, 자동 게시 설정에서도 카드를 띄우는지, 거부 경우와 상태 불변, README 줄 위치, API(401/404/409/200)와 CLI. 편집자·GitHub 업로드·토큰 탐침·편집기 판본(git)은 mock이고, 브라우저는 conftest가 막습니다.
- storage 관련 기존 테스트 24개 파일: 466 passed, 3 skipped. CLI mock 테스트 2개 파일: 10 passed.
- `scripts/check_public.sh` 통과, `git diff --check` 통과.

## 남은 것·한계

- 같은 저장소에 올리면 이전 판에만 있던 파일이 남습니다. 지우는 것은 되돌리기 어려운 작업이라 이번에 넣지 않았고, CLI 출력과 manual에 적었습니다.
- 편집기 commit은 gateway가 처음 읽은 HEAD(`integrations.rounds._git_commit`)입니다. git checkout이 아니면 판본만 적습니다.
- 웹 사무실 버튼은 없고 CLI·API만 있습니다.
- 실제 GitHub 게시, 실제 요청, PI instance는 실행하지 않았습니다.
- 처음에 `tests/test_cli*.py`를 glob으로 묶어 돌렸을 때 5 failed, 59 passed였습니다. 출력에 보인 실패는 `test_cli_adapter.py`의 2건뿐이고, 나머지 3건의 위치는 확인하지 않았습니다. 이 묶음에는 하위 process를 띄우는 adapter·launch test가 섞여 있어, mock-only 정책에 따라 다시 돌리지 않았습니다. storage CLI 경로는 위의 mock 테스트로 확인했습니다.
