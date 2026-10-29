# constrain 중 CSO 승인 (#1008)

| 항목 | 내용 |
|---|---|
| 경로 | `hooks/control.py` `ask_restricted`(urllib, broker `/restricted`) → `runner/approvals.py` `request_restricted`(30초) → `runner/daemon.py` `_on_restricted`가 `restricted.requested`(도구명·입력 미리보기·fingerprint·episode)를 올림 → `gateway/server.py`가 `Orchestrator.decide_restricted_call`로 판정하고 `restricted.resolved`로 답, 피드에는 판정만(입력 제외) → runner가 broker future를 풂 |
| 판정 | 결정적 규칙, 모델 호출 없음. 호출(fingerprint)당 제한 하나에 1회, 허용 제한당 5회, episode 불일치·종료·취소 중·fingerprint 없음은 거절 |
| 안전 | 무응답·broker 불가·이상한 답은 모두 거절(fail closed). 허용은 hook이 이의를 거두는 것뿐이라 approval_mcp의 권한 검사를 그대로 거치고 제한은 PI 해제까지 유지 |
| 설계 선택 | probe(2.1.310)로 hook `ask`가 permission-prompt로 가는 것을 확인했지만, permission-prompt 도구(approval_mcp)는 정책이 허용하는 호출을 바로 허용해 제한을 우회할 수 있어 `ask`는 쓰지 않고 hook이 직접 묻는 방식을 택했다 |
| 시험 | `tests/test_breaker_enforcement.py` +7(hook 질의·거절 경로·broker 엔드포인트 계약·runner 전달과 풀린 제한·무응답·판정 규칙·종료 작업·gateway 응답과 피드) |
| 범위 밖/미검증 | 실제 Claude 세션에서 hook→broker→gateway 전 구간, 작업판 UI(피드 한 줄만), Codex 등 hook 없는 엔진, 실제 network E2E |
