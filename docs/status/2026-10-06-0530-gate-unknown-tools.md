## 2026-10-06 · #421 — 규칙 없는 도구를 승인 게이트가 기본 허용하지 않기

- 결론: #316 검토의 P1 남은 몫. 승인 게이트는 셸·파일·MCP가 아닌 도구를 그냥 허용했다. 로컬 실행 기록 252개를 세어 보니 직원이 `Monitor`를 한 번 썼는데, 이 도구는 셸 명령을 돌리면서 Bash가 받는 통제 구역·개인 경로·위험 명령 검사를 거치지 않는다. 읽는 곳이 없던 `policy.approvals.auto_allow_tools`를 연결해, 목록에 없는 도구는 PI에게 묻는다.
- 바뀐 것: 기본 목록에 실측으로 확인한 `StructuredOutput`·`ToolSearch`를 더함. 목록 밖 도구는 ask(이유에 설정 키 이름). 매뉴얼 승인 게이트 절.
- 실행한 것: 도구 이름 실측(Bash 689·Read 652·PowerShell 496·shell 494·Write 318·Edit 290·web_search 157·pubmed MCP·StructuredOutput 126·ToolSearch 54·WebSearch 52·WebFetch 36·Monitor 1 등), 새 test 1건(Monitor는 ask, 목록 도구는 allow, 설정에 넣으면 allow), 전체 pytest 3961 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 처음 보는 도구가 밤에 쓰이면 PI 카드가 승인 시간 한도까지 단계를 붙잡는다. 멈춤 경고(#433)가 그 사이 알린다.
- 근거: `labhq/policy.py` `_evaluate_tool`, `labhq/settings.py` `_default_auto_allow`.
