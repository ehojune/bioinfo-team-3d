## #833 — Windows 브라우저 검사 시작·첫 화면 지연 진단 (base 5ea970f)

- 관측:
  - PR852 run 39709142121의 critical `web_resizable_panels.cjs`가 30초를 넘겼다. desktop의 차가운 Chrome 시작이 debug port 7.0초, page target 8.4초였다. 이어 phone의 첫 `Page.navigate`가 돌아오지 않았다.
  - #850 run 39701871339 focus 검사에서도 첫 navigate가 15.5초였다.
  - 두 경우 모두 정리는 정상 종료했다(#842와 다른 지점).
  - Chrome 쪽 지연인지 정적 서버 쪽 지연인지 가를 근거가 없었다.
- 변경(진단 전용, `tests/support/demo_driver.cjs`):
  - `target-ready` 행에 `/json` 시도·무응답·page 없음 횟수를 붙인다.
  - navigation마다 정적 서버가 `index.html` 요청을 받은 때(`server-request`)와 응답을 보낸 때(`server-response`, status)를 남긴다.
  - navigation이 5초 넘게 돌아오지 않으면 그때까지의 요청·응답 여부를 `navigate-waiting`으로 한 번 남긴다.
  - 이 행들은 기존 lifecycle 16행과 따로 6행까지만 쓰고, 마지막 경계 시각을 옮기지 않는다. 출력은 기존처럼 실패 때만 보인다.
- 바꾸지 않은 것: 제한시간(30초·170초, START 60초), 재시도, skip, 정리 계약, CI 설정, 제품 코드.
- 검사: `tests/web_visual_tooling.cjs`의 trace 검사에 순수 test 1건(관측 행 예산·경계 불변·lifecycle 16행 유지)을 더했다. 로컬에서는 `node --check`만 했고, test 실행은 CI 몫이다.
- 다음 재발 때 판정:
  - `navigate-waiting`에 요청이 없으면 Chrome이 요청을 보내지 않은 것이다.
  - 요청은 있고 응답이 없으면 서버 쪽이다.
  - 둘 다 있으면 응답 뒤 Chrome 쪽 commit 지연이다.
  - 시작 지연은 `/json` 무응답과 page 없음 비율로 가른다.
- 원인은 아직 모른다. #833은 열어 둔다.
