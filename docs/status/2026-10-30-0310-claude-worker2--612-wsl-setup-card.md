# WSL 미준비 시 셋업 카드 (#612 2단계)

| 항목 | 내용 |
|---|---|
| 범위 | #578(실행 위치 카드) 없이 가능한 부분만. 트리거는 `bioinfo-agent`(WSL 전용 직원)의 작업이 dispatch에서 갈 곳이 없을 때 |
| 흐름 | `Hub.dispatch` → `offer_wsl_setup`: 설정에 `runner.wsl` 있음 + `wsl_setup.check` 항목이 비어 있음 → `kind=wsl_setup` 카드(요청당 1장, `req["wsl_setup"]`에 결과 기록) → 승인 시 `wsl_setup.setup`을 thread에서(15분 상한) → 남은 항목이 없으면 평소 흐름(runner 연결 대기)으로, 있으면 `wsl_not_ready`로 즉시 멈춤(비용 $0) |
| 안전 | gateway가 WSL 있는 PC가 아니면(`_gateway_is_local`) 실행 없이 안내만. 예외는 타입 이름만 기록(설치 출력 비노출). `check`가 실행되지 못하면 옛 동작 유지 |
| 파일 | `gateway/server.py`(`offer_wsl_setup`, dispatch 훅), `wsl_setup.py`(`card_detail`·`pending_rows`·`SETUP_LIMIT_S`), `pi_text.py`(카드 문구), `models.py`(주석), manual 한·영, 시험 `tests/test_wsl_setup_card_612.py`(13건, 이름이 기존 `tests/test_wsl*.py` cohort에 들어가 Windows inventory 변경 없음) |
| 시험 | 승인→setup 1회·같은 요청의 둘째 task 무질문, 거절 기억, 남은 관리자 명령 표시, 예외 시 비밀 비노출, 원격 gateway, 해당 없음 5경우, 카드 문구, dispatch가 runner를 기다리기 전에 묻는지 |
| 로컬 확인 | 관련 515 passed. `test_runner_wait_snapshot.py` 3건은 이 변경과 무관(origin/main에서도 같은 3건 실패, 로컬 Windows·node 인코딩) |
| 미확인 | 실제 WSL에서 gateway가 `setup`을 돌리는 것(disposable 배포판 시험 안 함), `setup`의 실제 소요 시간, 한 PC·다른 OS 계정 gateway와 WSL |
| 범위 밖 | 실행 위치 카드의 "이 PC의 WSL" 선택지와 자동 계획 경로(#578), 처음 보는 직원(`knows_agent` 아님)의 직접 요청(404) |
