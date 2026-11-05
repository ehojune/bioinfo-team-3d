## 단계
#1107 rev2: 데모 정리가 `pending:[websocket]`로 끝나던 결함. 원인: [#1107 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1107#issuecomment-6527858912).

## 한 일
1. `tests/support/mini_ws.cjs`: 업그레이드 전에 `close()`가 불리면 대기 중인 요청을 destroy하고 `onclose`를 즉시 한 번, 연결이 업그레이드 전에 실패하면 `onerror`와 `onclose`를 한 번씩, 업그레이드 뒤에는 이전처럼 socket 'close'에서 한 번. 어느 경우도 `onclose`는 두 번 불리지 않고, `close()`가 스스로 만든 요청 오류는 `onerror`로 올리지 않는다.
2. `tests/support/demo_driver.cjs`: 정리 진단(`demo cleanup:`)에 `node_version`·`ws_kind`(`mini`/`global`)만 추가. 동작은 그대로.
3. `tests/web_visual_tooling.cjs`: 업그레이드 전 close, 연결 실패, 업그레이드 뒤 두 끝(client·peer), 진단 필드 시험 4건.

## 확인
- `LABHQ_TEST_PYTHON`을 지정해 `node tests/web_visual_tooling.cjs` 67 pass. 수정을 빼면 새 시험 3건이 실패함을 확인했다.
- 지정하지 않으면 Chrome을 여는 기존 시험 2건이 `LABHQ_TEST_PYTHON must name…`로 실패한다(main에서도 같음, 이번 변경과 무관).

## 하지 않은 것
- 시작이 30초 걸리는 것(느린 러너, #1076 계열)은 바꾸지 않았다. 이 수정으로 `web_resizable_panels`의 그 실패가 사라지지는 않는다.
- CI Windows runner의 Node가 `MiniWebSocket`을 썼는지는 아직 모른다(다음 사례의 `ws_kind`로 확인).
