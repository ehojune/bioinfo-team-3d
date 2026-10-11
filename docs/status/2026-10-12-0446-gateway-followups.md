## 2026-10-12 · gateway 수명주기 후속 묶음 (#521)

- 결론: #521 "gateway 수명주기"의 P2 6건(교차 검토 10·11·12·13·14·18)과 #526 리뷰의 채용 task 지적을 고쳤다. 7건 모두 main에서 test로 먼저 재현했다. 같은 묶음의 웹 상태 표시(`state.js:184`)는 이번 범위가 아니다.
- 바뀐 것:
  - 채용 수(11·18): `recruits_running`이 채용을 시작한 러너의 `process`를 기억하고, 다른 `process`가 붙으면 비운다. incarnation은 재시작해도 같아서 지금까지는 `labhq down`이 gateway를 다시 켤 때까지 "진행 중 채용"으로 거부했다. `process`를 보내지 않는 이전 러너는 지금의 incarnation 규칙을 따른다(#526과 같은 호환 규칙).
  - 꺼진 러너에 보낸 채용(18): 보내지 않은 것이 확실하면(`RunnerNotSent`: 연결 없음, 보내기 전에 연결 교체) 세지 않고 409로 답한다. 보내다 실패한 경우는 전처럼 센다.
  - 러너의 채용 turn(#526 리뷰): 채용 변환 turn을 러너의 `tasks`에 넣는다. 끝난 뒤 늦게 온 승인 답이 recruiter를 `working`으로 되돌리지 않는다.
  - 취소한 요청의 이어 묻기(10): gateway가 409로 거절하고 웹은 입력창을 숨긴다. 취소한 요청은 모든 turn을 거절해서 이어 묻기가 늘 실패로 끝났다.
  - 취소 기록 창(12): 요청에 `cancelling`을 두고 terminal record와 같은 commit에서 지운다. 그동안 `/api/shutdown-readiness`의 `finishing`(stage `cancel`)에 올라 `labhq down`이 거부한다. 그 전에 gateway가 멈추면 다음 시작 때 보고서와 terminal record를 마저 쓰고, 그때까지 결과를 보내지 않은 turn은 미집계로 센다.
  - 요청 묶음 복사(14): 끝나지 않은 `terminal_tasks`도 `finishing`(stage `bundle`)에 올린다. CLI는 두 경우를 `마무리 중인 요청:` 줄로 보여 준다. 이 키를 모르는 이전 CLI도 `ready: false`라 거부한다.
  - 러너 대기(13): 러너가 붙거나 명단을 바꾸면 러너를 기다리던 단계가 모두 다시 판정한다. 기다릴 러너가 없으면 24시간을 채우지 않고 "연결된 러너 중 X 직원을 맡은 곳이 없습니다"로 바로 끝난다. 러너가 맡던 직원 없이 다시 붙은 경우와, 저장된 명단이 없는 gateway에서 러너가 없어 기다리던 첫 직원(CSO·direct)을 맡지 않는 러너가 먼저 붙은 경우가 해당한다(뒤엣것은 PR #530 리뷰 P2).
  - `docs/manual.md`: down, 러너 대기, 이어 묻기, 요청 취소, recruit 줄.
- 실행한 것: 새 test 11개(lifecycle 9·CLI 1·web 1)와 readiness 결과를 통째로 비교하는 기존 test 1개(`finishing` 추가)가 main 코드에서 실패하고 이 branch에서 통과했다. main(#522·#527)을 병합한 head에서 전체 `pytest -q`(Windows) 5135 passed·64 skipped. 병합 충돌은 #522와 import 두 줄, #527과 `server.py` helper 자리 하나였고, #522 테스트의 가짜 러너에 `tasks`를 더했다. `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 미해결: 시작할 때 끊긴 요청 묶음을 다시 만드는 것(14의 선택 항목)은 하지 않았다. 묶음 정보가 없는 옛 요청까지 다시 복사하지 않으려면 별도 표시가 필요하다. 강제 종료(`down --force`)나 crash 때는 여전히 묶음이 빠질 수 있다. 처음 켠 gateway에서 다른 직원만 맡은 러너가 먼저 붙으면, 그 직원을 맡을 러너가 뒤에 붙을 예정이어도 첫 요청은 바로 끝난다. 맡은 러너가 없는 직원은 바로 실패한다는 R15 판정과 같다.
- 근거: `labhq/gateway/server.py`, `labhq/models.py`, `labhq/recruit/paper2agent.py`, `labhq/cli.py`, `labhq/web/index.html`, `tests/test_request_lifecycle.py`, `tests/test_cli_launch.py`, `tests/web_followup.cjs`, `docs/manual.md`.
