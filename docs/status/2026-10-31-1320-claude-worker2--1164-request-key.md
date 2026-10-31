# #1164 요청 멱등성

- 결론: 계약 연장·채용 시작·새 요청·Codex 리뷰 요청 POST가 `Idempotency-Key` 헤더를 받는다. 같은 (경로, 키, 본문)은 한 번만 적용하고 같은 응답을 돌려준다.
- 바뀐 것: `labhq/gateway/idempotency.py`(진행 중 요청 공유 + 10분·512개 응답 기억), `server.py` 4개 엔드포인트, `index.html`(시트 열린 동안 동작별 키, 전송은 성공할 때까지 같은 키, 채용은 `hire-<제안 id>`), `cli.py`(`send`·`send --no-wait`·`recruit`·`contract`·`codex-review` 실행마다 키 하나), 새 시험 `tests/test_gateway_idempotency.py`, manual 한·영 REST 절에 계약 한 단락.
- 설계: 요청 본문에 필드를 넣지 않고 헤더로 받는다(`RequestIn`이 요청 기록에 그대로 저장되기 때문). 키가 없으면 종전과 같다. 적용 전 거절(`HTTPException`)만 기억하지 않아 같은 키로 재시도할 수 있고, 그 밖의 실패는 runner·GitHub에 닿았을 수 있어 기억해 같은 키에 409로 답한다. 64자를 넘는 키는 자르지 않고 422로 거절한다. 첫 호출자가 끊겨도 일은 끝까지 가고 재시도는 그 결과를 기다린다.
- 실행: 새 시험 13개(main의 server.py로 돌리면 엔드포인트 3개가 실패), 관련 시험 `test_request_lifecycle`·`test_web_3d`·index.html을 읽는 시험·`test_ci_boundary_preview` 통과.
- 미해결/미검증: gateway 재시작 뒤에는 기억이 사라진다. 실제 브라우저 클릭·CLI 실행은 하지 않았다. `test_web_3d`의 `web_visual_tooling`이 다른 시험과 함께 돌 때 한 번 실패했고 단독 재실행은 통과(간헐).
