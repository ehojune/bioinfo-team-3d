## 2026-10-04 · #58 — 관찰 산출물 tool_use_id와 웹 감사 번들

- 결론: Claude 쓰기 도구의 PostToolUse 기록을 관찰 산출물에 보수적으로 연결하고, 인증된 웹 요청 상세에서 산출 파일 없는 감사 번들을 받게 했다.
- 바뀐 것: `tool_use_id` manifest·verify·`artifacts.json`, `GET /api/requests/{id}/audit-bundle`, 2.5D·3D 링크, README와 HANDOFF의 #58 ①~⑥ 완료 상태.
- 실행한 것: 수정 전 5 failed 확인. 관련 pytest 156 passed·4 skipped, Node 5개 통과.
- 미해결: 없음. 이 변경으로 #58의 표 ①~⑥이 모두 끝난다.
- 근거: `labhq/hooks/tool_use.py`, `labhq/runner/daemon.py`, `labhq/evidence/audit.py`, `labhq/gateway/server.py`, `labhq/web/`, `tests/test_observed_outputs.py`, `tests/test_labhq_verify.py`.
