# #1140 CP1 수정 요청의 중복 개설

- 결론: `Hub.acreate_revised_request`가 요청별 lock 안에서 "이미 열린 수정 요청이 있는가" 확인부터 새 요청 등록, `revised_to` 기록까지 한다. 두 번째 호출은 기다렸다가 첫 번째가 연 요청을 그대로 돌려준다.
- 원인(#1139 조사): 확인은 호출한 코루틴에서 했고 새 요청은 `_publication_write`가 만든 별도 task에서 등록돼, 같은 루프 반복에 준비된 두 호출이 둘 다 확인을 통과했다. 같은 수정 요청이 연구 요청 둘을 열어 비용이 두 번 났다.
- 시험: `tests/test_research_cp1_revise.py`에 동시 두 호출(수정 전 `req_… != req_…`로 실패, 수정 후 통과)과, 실패한 호출 뒤에 lock이 잡혀 있지 않은지(다음 호출 5초 안에 완료) 두 건.
- 바꾼 것: `labhq/gateway/server.py`(`_revise_lock`, 바깥 `acreate_revised_request`와 안쪽 `_acreate_revised_request`), 시험 파일 하나.
- 범위 밖: 동기판 `create_revised_request`는 await가 없어 같은 경합이 없다. 프로세스 사이 경합과 #1139에서 읽지 않은 후보는 다루지 않았다.
- 미검증: 실제 웹에서 같은 버튼을 두 연결로 동시에 누르는 경우, W3의 #1134와 같은 `server.py`의 병합 충돌(main 반영 때 확인).
