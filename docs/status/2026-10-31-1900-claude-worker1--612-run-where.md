# #612 2단계 — 요청에 "이 PC의 WSL" 선택(#578 없이)

- 결론: `RequestIn.run_where: Literal["auto","wsl"] = "auto"`를 더했다. `labhq send --wsl`과 웹 요청 폼의 "WSL에서" 체크가 채우고, 요청 기록·재개·후속·CP1 수정 요청에 이어진다. `wsl`이면 `_start_request`가 계획이 roster를 읽기 전에 `prepare_wsl_request`를 부른다. 이것은 `offer_wsl_setup`(#1038)과 같은 점검·카드를 요청 단위로 한 번 열고, 승인되면 셋업 뒤 runner가 붙기를 기다린다. 거절·시간 초과·실패·다른 PC(`blocked`)면 요청을 막지 않고 WSL 없이 진행하며 `requests[rid]["run_where_note"]`에 이유를 남긴다. CSO 계획 입력(`format_capabilities`)에는 `wsl`일 때만 "PI가 이 PC의 WSL에서 실행하라고 골랐다" 한 줄이 붙는다.
- 배경: [#612 점검](https://github.com/ehojune/bioinfo-team-3d/issues/612#issuecomment-6541542363) — 자동 계획은 준비 전 WSL runner의 bioinfo-agent가 roster에 없어 셋업 카드에 닿지 못한다. #578은 draft에 856커밋 뒤라 의존하지 않는다.
- 바꾼 것: `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `labhq/cli.py`, `labhq/web/index.html`, `tests/test_wsl_setup_card_612.py`(+8건). 웹 코드는 `#wsl-only`가 없는 DOM(다른 node 시험의 stub)에서도 `auto`로 읽는다. `pi_text.py`는 기존 `wsl_setup` 문구를 그대로 써서 바꾸지 않았다.
- 시험: 신규 8건은 구현 전 전부 실패했고 지금 통과. WSL 관련·CLI·e2e·idempotency 시험 포함 110건과 웹 node 5건 통과.
- 미검증: 셋업 뒤 WSL runner가 붙기까지의 실제 시간, 실제 WSL에서 gateway가 `setup`을 도는 시험, 웹 폼의 실제 모양(브라우저), manual 한·영(차례를 기다림).

## rev9 — 요청별 runner 선택 (#1204 설계를 이 PR 안에서)

- 결론: native에 끝나지 않은 `bioinfo-agent` 작업이 있어도 WSL을 고른 요청이 WSL runner를 바로 얻는다. 핀은 `meta.wsl_pin`으로 표시하고, 핀이 걸린 task는 `dispatch`·재전송·복구(`_await_prior_task`)·기다림(`wait_agent_online`)에서 `agent_runner` 대신 핀이 가리키는 runner(연결·WSL ready·roster에 직원 있음)로만 간다. 그 runner가 끊기면 native로 가지 않고 기다린다. `effective_roster`는 핀 행을 owner 결정에 쓰지 않는다.
- 바꾼 것: `labhq/gateway/server.py`(모듈 함수 `_wsl_pin`·`_target_runner`·`_runner_ready_for`·`_wsl_runner_ready`·`_pin_kwargs`, `effective_roster(hosts=True)`, `register_runner`의 이전 generation 정리, `wait_session_free`·`adopt_consult`의 holder runner), `tests/test_runner_pin_1204.py`(9건), `tests/test_wsl_setup_card_612.py`(runner 등록 대역), 인벤토리, manual 한·영.
- 시험: 새 9건 중 8건은 옛 코드에서 실패(자동 요청 1건은 회귀 확인용), 지금 전부 통과. dispatch·roster·세션·WSL 관련 시험 47개 파일 1746건과 CI 인벤토리 시험 통과.
- 미검증: 실제 WSL runner 두 개 이상 환경, 같은 직원을 두 runner가 동시에 돌릴 때의 workdir 충돌(핀 task의 workdir는 runner 로컬이라 시험으로 고정하지 않음).

## rev10 — 핀을 요청 단위 helper 하나로 (sweep)

- 결론: 리뷰 P1 두 건(계획 capability, 첫 runner 대기)은 같은 부류라 하나씩 막지 않고, 요청에 대해 runner·roster·capability를 고르는 곳을 모두 찾아 ··(server)와 ·(cso) 한 갈래로 모았다. sweep 목록과 시험은 PR 본문.
- 미검증: 실제 WSL runner 둘 이상 환경.
