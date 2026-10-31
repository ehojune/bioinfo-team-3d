# #1193 정리본 진행 기록의 리뷰 인용문 링크가 저장을 막지 않게

- 결론: `draft_documents`가 `PROGRESS.md`의 세 JSON 블록(확인 질문·수정 회차·검토)을 ```` ```json ```` 코드 블록으로 싣는다. 리뷰어가 보고서의 그림 링크를 `evidence_quote`로 인용해도 `run/progress.md`의 링크가 되지 않는다.
- 원인: 링크 검사의 상대 경로 해석 결함이 아니다. `run/` 기준 `../graphical_abstract.svg`는 루트 `graphical_abstract.svg`로 정확히 풀렸고 정리본에 그 이름의 파일이 없었을 뿐이다(실제 위치는 `figures/graphical_abstract.svg`). 링크를 만든 것은 편집자도 아닌, 리뷰 JSON을 평문으로 싣던 `result_storage.py`의 `PROGRESS.md` 생성이다.
- 같은 부류의 불일치: `portfolio.py:1336`·`1353`은 같은 JSON을 이미 코드 블록으로 싣는다. `PROGRESS.md`만 평문이었다. 링크 검사(`_markdown_targets`)는 코드를 마스킹한 뒤 링크를 찾는 설계라 코드 블록 안의 인용은 링크가 아니다.
- 바뀐 것: `labhq/result_storage.py`(`_json_block` 헬퍼, 호출 3곳. 펜스는 본문 안 백틱 연속보다 길게), `tests/test_result_storage.py`(2개).
- 시험: 리뷰 인용문에 이미지·일반 링크가 든 요청의 `PROGRESS.md`가 링크 검사를 통과한다(수정 전 `../graphical_abstract.svg`·`../tables/missing.tsv` 2건 검출로 실패). 진짜 깨진 링크는 계속 검출된다. 정리본·저장 계열 292 passed.
- 실제 데이터 확인: 7차 `req_3643a6a43e`의 요청 상세로 `draft_documents`를 돌려 `run/progress.md`의 깨진 링크가 0건이 되는 것을 확인했다(읽기 전용, 모델 호출·저장 없음).
- 미검증: 정리본 전체의 링크 검사는 재현하지 못했다(bundle·assets·images 입력 필요). 다른 문서에 같은 문제가 더 있는지는 이 요청의 실제 재저장으로만 확인된다. 6차가 통과한 이유는 6차 리뷰 인용문에 링크 문법이 없었기 때문으로 보지만 6차 리뷰 원문은 다시 열지 않았다.
- 재저장: 병합 뒤 `labhq storage req_3643a6a43e --redraft`(또는 `--republish`)로 7차 결과를 다시 저장한다. 편집자 재실행이 필요한지는 이 수정으로 정리본이 만들어지는지를 보고 정한다.
