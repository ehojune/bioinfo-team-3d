# #1077 후속: manual 저장 절

- 결론: PR #1078이 `review_pending` 요청의 `--to github`를 거절하고 명령을 안내하도록 바꿨는데, 상세 manual 한·영 저장 절은 "검토 전이면 검토 완료를 기다린다"로만 적혀 있어 새 흐름과 어긋났다(#1078 리뷰의 남은 P2). 코드는 바꾸지 않았다.
- 바꾼 것: `docs/manual.md`·`docs/manual.en.md`의 "로컬로 끝난 요청을 나중에 GitHub로(#688)" 문단. (1) CP4가 아닌 요청은 이전처럼 검토 완료를 기다림, (2) CP4 요청이 `review_pending`이면 거절하고 보고서 카드의 번호·hash로 `labhq review-done <id> --revision <번호> --sha256 <hash>`를 먼저 하거나 `--storage github [--name] [--visibility]`를 붙여 한 번에 하라고 안내함, (3) 토큰은 gateway 시작 때 읽으므로 새 토큰을 넣었으면 재시작 뒤 실행.
- 실행한 것: `tests/test_integrations.py` 통과(문서만 변경).
