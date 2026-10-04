## 2026-10-05 · #57 ⑦ — 직원 상세 시트의 capability card

- 결론: 직원 시트가 엔진·모델·소속만 보여 줬다. 이제 권한, 추론 강도, 같은 세션 이어 쓰기, 읽기 전용 상담 가능 여부, 붙은 MCP를 보여 준다. 값은 gateway가 runner의 roster와 엔진 adapter에서 가져온 것이고 웹은 추측하지 않는다.
- 바뀐 것: `Hub._agent_capabilities()`가 runner 등록 때 직원마다 `capabilities`를 붙여 snapshot·roster 이벤트로 나간다. `supports_resume`는 같은 판정(`_agent_resumes`)을 쓴다. 권한은 Codex면 `sandbox`, 그 밖은 `permission_mode`다. roster 요약에 `effort`를 더했다. 웹 시트·데모 roster·매뉴얼 2.5D 절.
- 실행한 것: `tests/test_agent_capabilities.py`(Claude·Codex·CLI 직원 card), `tests/web_agent_capabilities.cjs`(snapshot·roster 갱신, 시트 항목)를 CI 목록에 넣었다. 데모에서 엔지니어 시트를 열어 확인했다. 전체 pytest 3929 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 직원별 누적 비용·토큰은 지금 직원 단위로 모으는 곳이 없어 넣지 않았다. #57 ⑧(사무실 소품 탭 입구)이 남는다.
- 근거: `labhq/gateway/server.py`, `labhq/web/index.html`.
