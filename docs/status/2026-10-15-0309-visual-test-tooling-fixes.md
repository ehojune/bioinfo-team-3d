## 결론

Refs #546. 병합된 PR #599에 봇이 남긴 P2 네 건을 고쳤습니다. 모두 화면 비교 test 도구와 데모 움짤 스크립트의 오류이고 앱 동작은 바꾸지 않았습니다. 지적마다 커밋 하나와 그 지적이 실패하는 test가 있습니다.

## 바뀐 것

| 지적 | 커밋 | 고친 내용 |
|---|---|---|
| `layout_snapshot.cjs` 색 비교 | 8007211 | 기준선의 색은 `#rrggbb`인데 `sameColor`가 10진수 덩어리를 뽑아 비교했습니다. 두 자리씩 한 채널(`#rrggbbaa`의 alpha는 바이트)로 읽습니다. `#1d6b40`과 `#1e7b40`은 다르고 `#505b67`과 `#515b67`은 같습니다. `rgb()` 글자는 이전처럼 읽습니다. |
| `update_visual_baseline.cjs` | 68846ee | Chrome이 없으면 자식 test가 아무것도 쓰지 않고 0으로 끝나는데도 "커밋하라"고 안내했습니다. 건너뛰어질 상황이면 시작하지 않고, 끝난 뒤 기준선 내용이 그대로면 `Baseline NOT updated`와 이유를 내고 1로 끝납니다. 줄바꿈(CRLF/LF)만 다른 것은 바뀐 것으로 치지 않습니다(Windows checkout에서 첫 실행이 거짓으로 성공하던 것을 실제 실행에서 확인해 막았습니다). |
| `demo_driver.cjs` 가상 timer | 103d3cf | callback 예외를 잡아 `console.error`로만 남겨서 `Runtime.exceptionThrown`이 나지 않았고 `assert.deepEqual(d.errors, [])`가 보지 못했습니다. 실제 브라우저처럼 `reportError`로 uncaught 예외로 올립니다. 다른 timer는 계속 돌고, `reportError`가 없는 브라우저에서는 이전처럼 로그를 남깁니다. `record_demo_gif.cjs`도 같은 `d.errors`를 봅니다. |
| `gif_encode.py` 크기 제한 | 7e9c535 | `write_gif`가 output을 먼저 덮어쓴 뒤에야 `--max-mb`를 확인했습니다. 인코딩한 뒤 크기를 먼저 확인하고 통과한 것만 임시 파일을 거쳐 옮깁니다. 넘기거나 쓰기가 실패하면 이전 파일이 그대로입니다. |

- 새 `tests/web_visual_tooling.cjs`(node test 14건)와 `tests/test_gif_encode.py`(5건)가 지적마다 test를 가집니다. 색 비교(`#1d6b40` 대 `#1e7b40`, `#505b67` 대 `#515b67`, 경계 2·3, alpha), 기준선 갱신 종료 코드(건너뜀·변화 없음·줄바꿈만 다름·성공·실패·신호), 가상 timer(vm으로 페이지 시계를 직접 돌려 setTimeout·setInterval 예외 기록, Chrome에서 `d.errors`에 실제로 들어오는지), GIF(제한 초과 시 기존 파일 유지·새 파일 없음·제한 안이면 교체·쓰기 실패 시 임시 파일 정리).
- main의 코드로 돌리면 색 비교는 위 두 쌍을 거꾸로 판정하고, 가상 timer와 GIF test 일곱 건이 실패합니다. 기준선 갱신은 새 인자 없이는 시험할 수 없어서 `updateBaseline`에 인자를 주입할 수 있게 했습니다.
- `tests/test_web_3d.py`의 node 제한 시간 표에 `web_visual_tooling.cjs`(170초, Chrome 시작 예산)를 더했고, `tests/visual/README.md`에 종료 코드를 적었습니다.

## 확인

- `for f in tests/*.cjs; do node --test "$f"; done` 58개 파일 모두 통과(새 파일 포함). 새 파일 14건은 연속 7회 모두 통과했습니다.
- 시각 test(`web_visual_demo.cjs`)는 더 엄격해진 색 비교와 timer 예외 기록에서도 32개 화면이 기준선과 맞습니다. `node scripts/update_visual_baseline.cjs`를 실제로 돌리면 기준선이 그대로라 1로 끝납니다.
- `node scripts/record_demo_gif.cjs`를 새 인코더로 다시 돌려 `office-25d.gif`와 `.png`가 커밋된 파일과 바이트까지 같은 것을 두 번 확인했습니다(한 번은 Chrome 스크린샷 차이로 달랐고 다시 돌리니 같았습니다. 인코더가 아니라 스크린샷의 차이입니다).
- 관련 pytest(`test_gif_encode`·`test_integrations`·`test_lab3d`와 `test_web_3d`의 node 래퍼) 통과. 전체 pytest는 CI에 맡깁니다. 이 PC의 `node`는 Codex 번들을 찾는 shim이라 pytest가 HOME을 바꾸면 못 찾고 `web_visual_*` 래퍼가 실패합니다(main에서도 같음). 실제 `node.exe`를 PATH 앞에 두면 통과합니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 없음. PR #599 인라인 네 건에 각각 `고침:` 답글을 달았습니다.
