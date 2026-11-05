## 결론

Refs #607. 시운전 정리본 게시가 fine-grained 토큰 때문에 실패한 뒤에는 다시 시도할 길이 없었습니다. 이제 `labhq storage <요청 id> --republish`가 로컬 정리본 그대로 게시 카드를 다시 엽니다. 토큰 권한 오류는 이유와 할 일을 쉬운 말로 알리고, `labhq doctor`가 미리 경고합니다.

원인: 게이트웨이 토큰이 선택한 저장소만 허용하는 fine-grained 토큰이라 새 저장소를 만들 수 없습니다(`POST /user/repos`가 403). 저장 상태가 `failed`가 되면 `result_storage.redraft`가 거부해 되돌릴 방법이 없었습니다.

## 바뀐 것

- **다시 올리기**: `labhq storage <요청 id> --republish`, `POST /api/requests/{id}/storage/republish`. 상태가 `failed`인 정리본을 편집자 없이 로컬 초안 그대로 게시 카드로 다시 엽니다. `card_lock`을 씁니다. 이미 게시한 정리본, `failed`가 아닌 정리본, 통제 데이터 요청, 검토 완료 전 요청, 안 끝난 요청, 올리는 중인 요청, 로컬 초안이 없는 요청은 409로 거부합니다.
- **다시 만들기**: `--redraft`가 `failed`도 받습니다. `--republish`와 같이 쓸 수 없습니다.
- **권한 오류** (`result_storage.upload`): 새 저장소 만들기 403은 토큰이 저장소를 만들 수 없다는 말과 `gh repo create <소유자>/<이름> --private`(공개로 골랐으면 `--public`), 토큰 저장소 접근에 추가(Contents: Read and write), `--republish`를 알립니다. 기존 저장소 쓰기·조회의 403·404는 토큰이 그 저장소에 파일을 쓸 수 없다는 말과 추가 방법을 알립니다. 이유는 `result_storage.warning`에 저장합니다. rate limit 403·5xx 등은 기존 일반 문구에 재시도 명령을 붙입니다. 토큰과 GitHub 응답 본문은 경고·로그에 넣지 않습니다.
- **doctor**: 토큰이 fine-grained(`github.token_env` 변수의 접두사만 비교)이면 한 줄 경고합니다. 네트워크를 쓰지 않고 토큰은 출력하지 않습니다.
- `docs/manual.md` 저장 위치·편집자 절.

## 확인

- 새 test 23건(`tests/test_publish_retry_607.py`)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다. mock editor·mock transport만 씁니다.
- 기존 test 한 줄 수정: `test_redraft_refuses_published_and_not_yet_drafted_editions`가 `failed`도 거부하던 단언에서 `failed`를 뺐습니다. 동작이 바뀐 부분입니다.
- 관련 기존 test 180건 통과(`test_result_storage.py`의 runner를 띄우는 1건은 이 PC에서 빼고 CI에 맡김). `scripts/patch_notes.py check`, `scripts/check_public.sh` 통과.

## 남은 것

- 빈 저장소(첫 커밋 없음)에 contents API로 `branch: main`을 지정해 쓰면 404가 날 수 있습니다. 실제 GitHub를 부르지 않아 확인하지 못했습니다. `gh repo create`에 `--add-readme`를 주면 피합니다.
- 웹 사무실에는 재시도 버튼이 없습니다(CLI·API만).
- 시운전 정리본은 PI가 토큰의 저장소 접근에 새 저장소를 추가한 뒤 `labhq storage <요청 id> --republish`로 다시 올립니다.
