## 2026-10-03 · 모의 시운전 2차 — 요청마다 분석 환경 단계 하나, PI 질문 객체 우선

- 결론: 2차 모의 시운전에서 단계마다 venv를 따로 만들었고, 다른 단계의 venv에는 패키지를 더 넣지 못했다. 계획 단계에서 환경 단계를 하나 두게 하고, 직원은 그 interpreter를 경로로 쓰고 승인된 추가 패키지는 자기 작업 폴더 `./.pylib`에 설치한다. 시운전에서 실제로 통한 방식을 규칙으로 고정한 것이다.
- 바뀐 것: `ENV_STEP_RULE`을 일반·연구 계획 프롬프트에, 직원 공통 쓰기 규칙(`WORKSPACE_WRITE_RULES`)에 환경 사용 줄을 넣었다. PI 질문은 응답 안에 줄바꿈이 든 다른 큰 JSON이 함께 있어도 `blocking_decision` 키가 있는 객체를 골라 읽는다(PR #343 봇 지적).
- 실행한 것: 관련 test 259건 통과, 새 test 2건은 수정 전 실패. 프롬프트 해시 기대값 두 개를 의도한 변경으로 갱신.
- 미해결: 요청 단위 공유 환경(여러 단계가 한 venv에 쓰기)은 동시 설치 경합 때문에 하지 않았다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/adapters/base.py`, `tests/test_cso.py`, `tests/test_role_footer.py`.
