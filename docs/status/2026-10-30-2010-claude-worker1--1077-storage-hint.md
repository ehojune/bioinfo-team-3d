# #1077 후속: 검토 대기 요청의 `--to github` 안내와 연결 시험

- 결론: 점검(#1077 댓글)에서 나온 두 가지를 반영했다.
- 바꾼 것: (1) `labhq storage <id> --to github`가 `review_pending`(CP4, 보고서 검토 대기) 요청에서 "끝난 요청만 옮길 수 있습니다"라고만 거절하던 것을 보고서 카드의 번호와 hash를 넣은 `labhq review-done <id> --revision <번호> --sha256 <hash>`로 먼저 검토를 마치거나 같은 명령에 `--storage github`를 붙여 한 번에 하라는 안내로 바꿨다(`labhq/result_storage.py`). 다른 거절 문구는 그대로다. (2) `tests/test_late_github_688.py`에 연결 시험 1건: CP4 요청을 `review-done --storage github`로 넘기면 편집자 → 게시 카드 → 승인 뒤 가짜 GitHub API에 `owner/study`로 업로드(README PUT)되어 `published`가 된다. 업로드는 mock이 아니다.
- 시험: 안내 문구 1건 + 연결 1건. 관련 저장 시험 51건 통과.
- 한계: 가짜 GitHub는 빈 probe POST 뒤 repo를 "있음"으로 답해, 이 연결 시험은 repo 생성 자체를 보지 못한다(생성은 `test_result_storage.py`가 별도 확인).
- 미검증: 실제 토큰·repo 생성 권한, 실제 gateway 재시작 뒤의 흐름(W2 실행 단계).
