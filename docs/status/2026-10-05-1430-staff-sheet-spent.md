## 2026-10-05 · #57 후속 — 직원 시트의 쓴 비용

- 결론: #57 ⑦에서 뺀 직원별 누적 비용을 넣었다. 직원 상세 시트가 화면에 있는 요청들의 `by_agent`(#415)를 더해 "확인 $0.50 + 추정 $0.30 (요청 2건)"처럼 보여 준다.
- 바뀐 것: `labhq/web/state.js`(`agentSpentLabel`), 시트 capability card, 매뉴얼 2.5D 절.
- 실행한 것: `tests/web_cost_by_agent.cjs`에 합산·빈 경우 단언 추가. web test 통과, `scripts/check_public.sh`.
- 미해결: 화면에 없는 오래된 요청은 합산하지 않는다(gateway가 보낸 요청만).
- 근거: `labhq/web/state.js`.
