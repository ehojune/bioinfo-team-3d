## 결론

#521 웹·CLI 묶음 8건(15·19·20·21·22·23·24·25)을 모두 main에서 재현하고 고쳤습니다. 15와 22는 같은 원인이라 수정 하나로 닫았습니다.

## 바뀐 것

| # | 증상 | 수정 |
|---|---|---|
| 15·22 | 한도·로그인·환경 대기가 풀릴 때 열린 결정 카드가 있어도 웹이 `진행 중`(gateway·CLI는 `PI 결정 대기`) | `state.js` `syncHoldStatus`가 PI 결정 카드(clarify·question·scope·budget·CP1·CP2·이어 가기)가 열린 요청은 `waiting_pi`로 되돌림. gateway `_sync_hold_status` 순위와 같음 |
| 19 | 계획 없는 요청(direct, 계획 전 중단)의 재개 카드 상세가 `남은 단계 없음` | gateway 재개 카드 detail에 `has_plan`. 웹은 `false`면 `요청 전체`로 적고 이 값은 따로 보이지 않음 |
| 20 | `labhq approve`가 선택지 있는 직원 질문을 빈 답으로 승인 | `--note` 없으면 exit 2와 선택지(`a) …`)를 보여 주고 요청은 그대로. `labhq approvals`에도 선택지 표시. `--deny`는 그대로 거절 |
| 21 | gateway가 `runner.offline` 없이 죽으면 `since`로 다시 붙은 페이지가 **러너 꺼짐** 띠를 숨김 | 시작할 때 이전 프로세스가 온라인으로 본 러너와 저장된 roster의 러너 중 마지막 이벤트가 offline이 아닌 것마다 `runner.offline`(`reason: gateway_restarted`)을 남김. 웹 메신저는 이 경우 alert 없이 "게이트웨이가 다시 시작돼 러너 연결을 기다려요" |
| 23 | 시간 초과 카드가 메신저에 `나: 거절했어요` | `state: timed_out`은 "시간 초과로 닫혔어요", `expired`·`cancelled`는 "답 없이 닫혔어요"(시스템 발화) |
| 24 | CLI watch가 CP1 수정 요청을 `거절`로 적음 | `수정 요청`, 시간 초과는 `⏰ 승인 결과: 시간 초과`로 따로 |
| 25 | 첫 스냅샷 전 요청 카드가 "아직 요청이 없어요" | "요청 불러오는 중"(토큰 없으면 "토큰을 넣으면 요청을 불러와요"). key가 연결 상태를 따라 스냅샷·토큰이 오면 다시 그림 |

`docs/manual.md`: 104,383자 → 104,850자(+467자). 러너 꺼짐 띠, 첫 스냅샷 전 표시, 요청 상태·메신저, CLI `approve`·`watch`, 재개 카드 남은 단계를 고쳤습니다.

## 실행한 것

- main + 새 테스트: web node 3파일(`web_request_lifecycle`·`web_decision_detail`·`web_cards_status`) 실패, pytest 5건 실패
- 수정 뒤: web node 31파일 전부 통과(golden hash `web_state.cjs` 포함), `test_web.py`·`test_web_3d.py`·`test_cli_launch.py`·`test_request_lifecycle.py` 127 passed, 2 skipped
- Windows 전체 pytest: 5025 passed, 64 skipped
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과

## 미해결

- gateway `resolve_approval`은 선택지 있는 직원 질문의 빈 승인을 막지 않습니다. 웹·3D·CLI만 막고, API를 직접 부르는 클라이언트용 강제는 넣지 않았습니다.
- `has_plan` 전에 저장된 재개 카드는 상세에 계속 `없음`으로 보입니다(요약 줄은 맞고, 7일 뒤 닫힘).
- 저장된 이벤트 창(2000개)에서 밀려난 러너는 재시작마다 `runner.offline` 한 줄이 더 남을 수 있습니다. 메신저에는 alert 없이 한 줄입니다.

## 근거

- `labhq/web/state.js`, `labhq/web/ui/decide.js`, `labhq/web/index.html`
- `labhq/gateway/server.py`(`Hub.__init__`, `new_resume_approval`)
- `labhq/cli.py`(`_question_options`, `render`, `approvals`·`approve`)
- `tests/web_request_lifecycle.cjs`, `tests/web_decision_detail.cjs`, `tests/web_cards_status.cjs`, `tests/test_request_lifecycle.py`, `tests/test_cli_launch.py`
