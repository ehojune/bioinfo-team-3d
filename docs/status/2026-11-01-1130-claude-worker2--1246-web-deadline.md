# #1246 결함 1: 웹 남은 시간을 서버 기한으로 계산한다

- 결론: 웹의 "남은 N분"이 서버가 실제로 취소하는 시각과 어긋나던 것을 고쳤다. `request.created` 이벤트로 만든 요청은 `created_at`이 이벤트 `ts`(요청을 시작한 시각)라, 수락과 시작 사이가 늦으면 남은 시간이 서버 기한보다 길게 보였다.
- 바뀐 것: `labhq/web/state.js`의 `deadlineLabel`이 서버가 보내는 `wall_clock.deadline_at`(수락 시각 기준, 닫힌 PI 대기 반영)을 쓰고, 열려 있는 PI 대기만 더한다. 기한이 없는 옛 gateway에서만 `created_at`+상한+held로 되돌아간다. `tests/web_state.cjs`에 시작이 늦은 `request.created`와 열린 PI 대기 사례 두 건.
- 실행한 것: `node tests/web_state.cjs` 통과(시험을 먼저 써서 고치기 전 실패 확인). 관련 pytest 194 passed, 1 skipped.
- 문서(rev3): manual 한·영의 "작업 시간" 행에 웹이 서버의 `wall_clock.deadline_at`으로 남은 시간을 계산한다는 설명을 넣었다(PR 리뷰 P1). patch note SHA 지적은 PR의 코드 커밋(`a2f582c`)이 patch note와 일치해 오분석이라고 스레드에 근거를 답했다.
- 미해결/미검증: 실제 브라우저 렌더링은 확인하지 않았다(상태 문구 계산만 node로). 결함 2(AlphaGenome 준비 상태)는 따로 claim한다.
