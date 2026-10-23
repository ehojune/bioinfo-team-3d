## 단계
#833 resizable 브라우저 시험의 기한·정리 경계 보정 (PR #875)

## 한 일
- 같은 pytest node `test_office_in_node[web_resizable_panels.cjs-fail 0]`가 스크립트를 `LABHQ_RESIZABLE_VIEWPORT=desktop`, `phone`으로 차례로 두 번 실행합니다. 각 실행은 timeout 30초와 새로 계산한 절대 끝 시각을 받습니다. 첫 판의 공유 기한은 job 120174823730에서 phone에 812ms만 남겼으므로 바꿨습니다.
- 각 실행 안에서는 그 끝 시각에서 `CLOSE_RESERVE_MS`(정리 5초, profile 삭제 2초, 종료 1초)를 뺀 작업 기한을 단계끼리 공유합니다.
- 브라우저가 필수일 때 각 실행은 pass 3, skipped 0과 해당 viewport의 `ok 3` 제목을 보여야 합니다. 어느 한쪽이라도 실패·skip·누락이면 node가 실패합니다.
- JS는 두 정적 시험을 유지하고 선택된 viewport의 브라우저 시험만 등록합니다. 잘못된 선택값과 viewport 없는 바깥 기한은 거부합니다. 직접 실행(변수 없음)은 두 viewport를 차례로 돌립니다. desktop·phone 판정은 그대로입니다.
- demo driver는 기한이 있을 때만 Chrome 시작·DevTools 조회·CDP socket·CDP 호출·navigate·boot 대기를 기한 안에서 끝냅니다. 남은 fetch는 abort하고 CDP 호출은 pending에서 뺀 뒤 기존 `close()`로 한 번 정리합니다. 기한이 없는 호출은 그대로입니다.
- `config/ci/windows-boundary-v1.json`에서는 `tests/test_web_3d.py`의 원본 sha256만 갱신했습니다. node·suite·workflow·critical 목록은 그대로입니다.
- 시간 기록(run 39881000670 뒤 추가): Python은 실패·timeout 출력에 launch 직전 wall clock, 그 값으로 만든 기한, monotonic으로 잰 실행 시간을 한 줄 붙입니다. Node는 첫 줄·require 뒤·각 viewport open 직전에 process 시작 시각, monotonic 값, wall clock, 남은 outer와 작업 시간을 남깁니다. 기한·판정은 그대로이고 증거 수집일 뿐입니다.
- 진단: Chrome Popen 뒤 `LABHQ_BROWSER_CREATED` 줄(`chrome-created`), DevToolsActivePort 읽기 실패의 고정 코드별 횟수, 기한에 걸린 단계 한 줄을 남깁니다. 경로·메시지·stderr 원문·PID는 남기지 않습니다.

## 테스트 결과
- 바꾼 `.cjs` 3개에 `node --check`, Python 파일에 3.10 문법 파싱을 했습니다.
- Job wrapper 가짜 시험 17건이 통과했습니다.
- `test_web_3d` wrapper는 `node_wrapper`와 정적 source 확인으로 27건 통과입니다(두 번의 필수 실행, 실행마다 다른 기한, timeout 30초, 선택값 격리, 어느 쪽의 실패·skip·다른 제목·timeout도 거부, 실패 출력의 Python 시간 줄, Node 시간 기록 위치). f8e8aed의 파일로는 새 시간 확인 3건이, 그 전 00a7e91의 파일로는 21건이 실패합니다.
- Node 가짜 시험(workBudget, vm 안의 driver, 표식 파싱)은 작성만 했습니다. 로컬에서 Node를 실행하지 않았으므로 CI에서 처음 돕니다.
- 기록 검사·공개 검사·validate_profile·git diff --check를 했습니다. pytest·브라우저·실제 Windows는 돌리지 않았습니다.

## 막힌 점
- 2cbd958 이후 CI는 아직 확인하지 않았습니다. 직전 f8e8aed의 Windows run 39881000670은 desktop 작업 기한(chrome-start)으로 실패했고, driver trace 전 약 11.5초의 원인은 미확인입니다.
- Chrome 시작이 느린 원인은 미확인이고 이 변경은 그 해결이 아닙니다.
- 이 node는 이제 최대 약 60초(30초 두 번)가 걸릴 수 있습니다. 직렬 critical 단계의 기존 상한 안에 들어가는지 총괄 확인이 필요합니다.
- 한 viewport의 작업이 약 22초를 넘기면 기한 실패로 끝납니다.
- 커밋 작성자는 전역 post-commit hook이 `claude-ehojune[bot]`으로 바꾸고 trailer를 지웠습니다. 예상된 동작이고 기록은 고치지 않았습니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
