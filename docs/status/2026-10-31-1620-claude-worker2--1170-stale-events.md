# #1170 웹 reducer: 중복·늦은 이벤트가 끝난 요청·카드를 되살리지 않게

- 결론: reducer(`state.js apply`)가 `S.lastSeq`로 같은·옛 seq를 무시하고, 끝난 요청(done·failed·cancelled·rejected)은 늦은 `task.dispatched`·파이프라인 task의 `agent.status`·`request.review`로 phase·step·직원 행이 바뀌지 않는다. 2.5D `open()`은 이전 socket을 먼저 닫는다.
- 바뀐 것: `labhq/web/state.js`(가드), `labhq/web/index.html`(socket 닫기 한 줄), 새 `tests/web_stale_events_1170.cjs`(8개), `tests/web_environment.cjs`(입력 seq만).
- 설계: review_pending은 PI가 수정을 되돌릴 수 있어 가드 대상(settled)에서 뺐다. follow-up turn은 끝난 요청에서 도는 정상 흐름이라 task 행을 그대로 만든다. seq 없는 이벤트(demo)와 스냅샷 재생은 가드를 우회한다.
- `web_environment.cjs`: 입력 seq가 1,2,2,3으로 겹쳤고 서버는 이벤트마다 seq를 하나씩 붙이므로 시험 입력의 오류다. 입력 seq를 4~9로 바꿨고 단언은 그대로다.
- 실행: 새 시험은 수정 전 7개 실패, 수정 후 통과. `test_web_3d`·`test_web` 107 passed, 1 skipped.
- 미해결/미검증: 두 페이지의 `lastSeq` 복사본은 `test_web.py`·`web_cards_status.cjs`가 문구를 고정해 그대로 둠(reducer 가드가 있어 동작은 안전). 실제 브라우저 재연결, 3D 렌더러의 표시는 못 봄. 서버가 종료 뒤 늦은 이벤트를 실제로 내보내는 빈도는 못 쟀음(#1148·#1168 범위).
