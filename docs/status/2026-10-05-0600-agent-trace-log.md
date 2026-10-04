## 2026-10-05 · #57 ⑥ — 직원별 생각·디버그 기록

- 결론: 웹은 엔진이 보내는 `thinking`·`debug` 로그를 받자마자 버렸다. 이제 직원마다 최근 60줄을 따로 보관하고, 직원 상세 시트에서 **활동 / 생각·디버그**로 골라 본다. 피드·말풍선·활동 목록에는 여전히 넣지 않는다.
- 바뀐 것: `labhq/web/state.js`(`traceTo` ring buffer, 재생 상태 비교에 들지 않는 비열거 속성이라 roster 갱신 뒤에도 유지), `labhq/web/index.html`(시트 전환 버튼, 데모 각본에 생각 두 줄), 매뉴얼 2.5D 절.
- 실행한 것: 새 `tests/web_agent_trace.cjs`(보관·경계·피드 제외·roster 갱신 뒤 유지)를 CI 목록에 넣었다. 기존 재생 상태 hash test(`web_state.cjs`)는 그대로 통과한다. 데모 페이지에서 분석가 시트를 열어 생각 줄이 보이는 것을 확인했다. web test 57 passed.
- 미해결: #57 ⑦ capability card, ⑧ 사무실 소품 탭 입구.
- 근거: `labhq/web/state.js`, `tests/web_agent_trace.cjs`.
