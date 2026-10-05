## 2026-10-05 · #421 — 직원 세션의 여러 agent 도구와 Codex 기억 끄기

- 결론: #316 open-science 검토에서 나온 P1. 승인 게이트는 셸·파일·MCP가 아닌 도구를 그냥 허용해, 직원이 `Workflow`·`TeamCreate`로 labhq가 승인·집계하지 못하는 agent를 띄울 수 있었다. Codex 쓰기 직원은 `multi_agent`·`memories`가 켜진 채로 돌아, 하위 agent 비용이 단계 집계 밖으로 새고 공용 staff CODEX_HOME에서 다른 요청의 기억이 섞일 수 있었다.
- 바뀐 것: Claude 직원의 거부 규칙에 `Workflow`·`TeamCreate`·`TeamDelete`를 더했다(`SendMessage`·`ListAgents`와 같은 `--settings` 경로). 하위 agent `Task`·`Agent`는 recruiter의 Paper2Agent 변환이 써서 남긴다. Codex는 쓰기·읽기 전용 모두 `--disable multi_agent --disable memories`를 건다(이 PC의 codex-cli `codex features list`로 이름 확인). 매뉴얼 안전 장치 절.
- 실행한 것: 거부 목록·Codex 플래그를 정확히 단언하도록 test 5곳을 바꿨다(쓰기 실행은 두 기능만 끄고 읽기 전용 묶음은 켜지 않음을 확인). 전체 pytest 3942 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: `policy.approvals.auto_allow_tools`는 여전히 읽는 곳이 없다. 연결하면 `StructuredOutput`·`ToolSearch` 같은 무해한 도구에도 PI 카드가 뜰 수 있어(trial 기록 760건 중 실제 사용 확인), 허용 목록을 실측으로 정한 뒤 #421에서 다룬다.
- 근거: `labhq/adapters/claude_code.py`, `labhq/adapters/codex.py`.
