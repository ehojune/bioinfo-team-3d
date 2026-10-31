# #1148 취소가 시설 수리 카드 저장 중에 들어오면 요청이 되살아나는 결함

- 결론: `Hub._request_facilities_fix`의 `open_fix`가 카드를 저장하는 `await self._add_approval(...)` 뒤에 `req_state["status"] = "waiting_facilities_fix"`를 썼다. 그 사이에 취소가 들어오면(저장이 느린 디스크에서는 수십 ms) 취소된 요청이 "시설 수리 승인 대기"로 되돌아가고 그 상태가 저장되며 카드가 남았다. 함수 맨 앞의 `cancelled` 검사는 이 `await` 뒤에는 없었다. #1148 조사(#1139 후속)에서 재현했다.
- 바꾼 것: `labhq/gateway/server.py`의 `open_fix`가 `_add_approval` 뒤에 요청이 이미 끝났거나(`is_active_request`가 아님) `cancelling`이면 상태를 쓰지 않고 방금 연 카드를 `close_approval`로 닫고 돌아온다(이 호출 안에서만 쓰는 `saved`는 `cancelled`로 둬 뒤 코드가 깨지지 않는다). 요청 행의 `status`·`cancelling` 표식이 이미 "끝내는 중"을 뜻하므로 #1146의 별도 집합은 필요 없다.
- 시험(`tests/test_facilities_fixes.py` 1건): 카드 저장을 느리게 막고 취소한 뒤 풀면 요청은 `cancelled`로 남고 `facilities_fixes`에 대기 항목이 없고 수리 카드가 없으며 `approval.requested`가 나가지 않는다. main의 코드에서는 카드가 닫히지 않아 수리 호출이 대기를 끝내지 못하고 실패한다. 파일 44 passed, 관련 시험 1,597 passed.
- 리뷰 P1(같은 부류의 더 좁은 변형): 첫 검사 뒤에도 `asave_request`·`publish`로 `await`가 더 있어, 그 사이 취소가 카드를 닫고 `approval.expired`를 먼저 내보낸 뒤 `approval.requested`가 늦게 나가면 웹 reducer가 카드를 다시 띄운다. 게시 직전(같은 동기 구간)에 카드가 아직 `self.approvals`에 있고 요청이 활성인지 다시 보고, 아니면 게시하지 않는다. 이렇게 하면 그 뒤에 취소가 와도 `approval.expired`는 이 `approval.requested`보다 뒤에 나간다. 시험을 카드 저장이 느린 경우와 그 뒤 요청 저장이 느린 경우 둘로 나눴고, 둘째는 이전 head에서 실패한다.
- 의미를 바꾸지 않은 것: 상태 쓰기는 직접 대입 그대로다. `_sync_hold_status`로 바꾸면 로그인·쿼터 대기가 수리 카드보다 우선하는 의미 변화가 생겨 이번 범위에서 뺐다.
- 미확인: 실제 발생 빈도. 같은 패턴이 다른 카드 종류에 있는지는 #1148 조사 범위의 스캔에서 이 한 곳만 나왔다.
- 근거: #1148 조사 댓글 6536217329.
