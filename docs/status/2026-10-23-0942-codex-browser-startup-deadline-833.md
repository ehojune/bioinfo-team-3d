## 단계
#833 resizable 브라우저 시험의 기한·정리 경계 보정 (PR #875)

## 한 일
- `tests/test_web_3d.py`가 `web_resizable_panels.cjs`에만 30초 watchdog의 절대 끝 시각을 넘깁니다. 상한 30초는 그대로입니다.
- 시험은 그 시각에서 `CLOSE_RESERVE_MS`(정리 5초, profile 삭제 2초, 종료 1초)를 뺀 작업 기한 하나를 desktop과 phone에 함께 넘깁니다.
- demo driver는 기한이 있을 때만 Chrome 시작·DevTools 조회·CDP socket·CDP 호출·navigate·boot 대기를 기한 안에서 끝냅니다. 남은 fetch는 abort하고 CDP 호출은 pending에서 뺀 뒤 기존 `close()`로 한 번 정리합니다. 기한이 지나면 다음 viewport를 열지 않습니다. 기한이 없는 호출은 그대로입니다.
- 진단: Chrome Popen 뒤 `LABHQ_BROWSER_CREATED` 줄(`chrome-created`), DevToolsActivePort 읽기 실패의 고정 코드별 횟수, 기한에 걸린 단계 한 줄을 남깁니다. 경로·메시지·stderr 원문·PID는 남기지 않습니다.

## 테스트 결과
- 바꾼 `.cjs` 3개에 `node --check`, Python 파일에 3.10 문법 파싱을 했습니다.
- Job wrapper 가짜 시험 17건이 통과했습니다. `test_web_3d` wrapper는 `node_wrapper`로 10건 통과이고, main 판에서는 새 env 3건이 실패합니다. 정적 source 확인도 통과했습니다.
- Node 가짜 시험(workBudget, vm 안의 driver, 표식 파싱)은 작성만 했습니다. 로컬에서 Node를 실행하지 않았으므로 CI에서 처음 돕니다.
- 기록 검사·공개 검사·git diff --check를 했습니다. pytest·브라우저·실제 Windows는 돌리지 않았습니다.

## 막힌 점
- Chrome 시작이 느린 원인은 미확인이고 이 변경은 그 해결이 아닙니다.
- 예전 Windows 기록에는 22.8·24.1·28.8초 통과가 있습니다. 이제 작업이 약 22초를 넘기면 기한 실패로 끝날 수 있습니다. 판단은 총괄 몫입니다.
- 커밋 작성자는 post-commit hook이 `claude-ehojune[bot]`으로 바꾸고 Co-Authored-By trailer를 지웠습니다. 기록은 고치지 않았습니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
