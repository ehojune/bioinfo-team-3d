# #612 2단계 — 요청에 "이 PC의 WSL" 선택(#578 없이)

- 결론: `RequestIn.run_where: Literal["auto","wsl"] = "auto"`를 더했다. `labhq send --wsl`과 웹 요청 폼의 "WSL에서" 체크가 채우고, 요청 기록·재개·후속·CP1 수정 요청에 이어진다. `wsl`이면 `_start_request`가 계획이 roster를 읽기 전에 `prepare_wsl_request`를 부른다. 이것은 `offer_wsl_setup`(#1038)과 같은 점검·카드를 요청 단위로 한 번 열고, 승인되면 셋업 뒤 runner가 붙기를 기다린다. 거절·시간 초과·실패·다른 PC(`blocked`)면 요청을 막지 않고 WSL 없이 진행하며 `requests[rid]["run_where_note"]`에 이유를 남긴다. CSO 계획 입력(`format_capabilities`)에는 `wsl`일 때만 "PI가 이 PC의 WSL에서 실행하라고 골랐다" 한 줄이 붙는다.
- 배경: [#612 점검](https://github.com/ehojune/bioinfo-team-3d/issues/612#issuecomment-6541542363) — 자동 계획은 준비 전 WSL runner의 bioinfo-agent가 roster에 없어 셋업 카드에 닿지 못한다. #578은 draft에 856커밋 뒤라 의존하지 않는다.
- 바꾼 것: `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `labhq/cli.py`, `labhq/web/index.html`, `tests/test_wsl_setup_card_612.py`(+8건). 웹 코드는 `#wsl-only`가 없는 DOM(다른 node 시험의 stub)에서도 `auto`로 읽는다. `pi_text.py`는 기존 `wsl_setup` 문구를 그대로 써서 바꾸지 않았다.
- 시험: 신규 8건은 구현 전 전부 실패했고 지금 통과. WSL 관련·CLI·e2e·idempotency 시험 포함 110건과 웹 node 5건 통과.
- 미검증: 셋업 뒤 WSL runner가 붙기까지의 실제 시간, 실제 WSL에서 gateway가 `setup`을 도는 시험, 웹 폼의 실제 모양(브라우저), manual 한·영(차례를 기다림).
