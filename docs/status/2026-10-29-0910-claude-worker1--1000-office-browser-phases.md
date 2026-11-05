## 단계
Windows CI `test_office_in_node[web_office_browser.cjs]` 150초 멈춤 추적 — Closes #1000.

## 원인 (실제 로그)
- run 40780405903(main `82125d74`), job 123140494538. 출력에 두 화면(1440×900, 390×844) 측정 JSON(`console.log`)이 모두 있고, 그 뒤 subtest가 `testTimeoutFailure`(150006ms)로 끝났습니다.
- 그 JSON을 출력하는 줄 다음에 남는 단계는 `await cdp('Browser.close')`와 finally 정리(최대 2초)뿐입니다. 정리는 150초를 쓸 수 없으므로 멈춘 곳은 `Browser.close`의 답을 기다리는 줄입니다. Chrome이 답을 보내기 전에 소켓을 닫거나 종료하면 그 Promise는 영영 끝나지 않았습니다(`ws.onclose`가 대기 중인 호출을 거부하지 않음, 호출에 상한 없음).
- 이 결론은 로그 구조에서 추론한 것입니다. 실제 CI에서 Chrome이 답을 안 한 사실을 직접 본 것은 아닙니다.

## 한 일 (`tests/web_office_browser.cjs`만)
- `Browser.close`는 5초만 기다리고, 답이 없거나 소켓이 닫히면 단계 이름과 함께 기록한 뒤 finally가 Chrome을 끝냅니다. 모든 단언은 그 전에 끝나 있습니다.
- 모든 DevTools 호출에 30초 상한과 이름 있는 오류, `ws.onclose`에서 대기 호출 거부를 넣었습니다.
- 단계 이름·시각(`office phase +Nms: …`)을 stderr에 남깁니다. `test_office_in_node`가 timeout 때 stderr를 그대로 출력하므로 다음 멈춤은 마지막 단계로 바로 보입니다.

## 테스트 결과
- 로컬 Chrome에서 통과(약 19.6초), 단계 기록 정상. pytest `test_web_3d.py -k web_office_browser`는 실제 `C:\Program Files
odejs`를 PATH 앞에 둔 상태에서 통과(21초)합니다. 이 머신의 기본 `node`는 Codex 런타임을 부르는 `~/bin/node.CMD` shim이라 test의 임시 HOME에서 `node.exe not found under ...cua_node`로 실패하며, 이 변경과 무관한 환경 문제입니다(수정 전 파일도 같은 shim을 씁니다).
- 재현 실험(임시 사본, 커밋 안 함): `Browser.close` 요청을 보내지 않아 답이 없게 하면 수정 전 파일은 150초 timeout까지 멈추고(약 3분), 수정 후는 5초 뒤 `Browser.close: CDP Browser.close had no reply within 5000ms`를 남기고 통과합니다.
- 미검증: 실제 Windows CI에서 원인이 이것이라는 직접 증거와, 이 수정 뒤 CI 재발 여부. 한 번 green은 해결 근거가 아니므로 #1000은 열어 둡니다.

## 바꾼 파일
tests/web_office_browser.cjs와 변경 기록. 제품 웹 코드는 바꾸지 않았고 결함 근거도 없습니다.

## 막힌 점
없음. #844·#898 범위와 섞지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
