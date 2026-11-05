## 단계
#1072: Windows `web_resizable_panels` 간헐 실패 재발(#1069 첫 run).

## 원인
시간 초과가 아니라 `tests/support/demo_driver.cjs`의 정적 서버가 `listen(0)`으로 OS에게 받은 포트가 운영 포트(`FORBIDDEN_PORTS = [8787, 55921]`)와 같을 때 그 자리에서 던지던 방어였다. 실패 run 41028675397은 3.3초 만에 `static server landed on a reserved port 55921`로 끝났고 단계 기록은 `server-ready`(108 ms)까지 정상이었다. 55921은 Windows 임시 포트 범위(49152–65535) 안이라 서버 시작마다 약 1/16,384로 걸린다. #833의 30초 startup 지연과는 별개다. 진단: [#1072 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1072#issuecomment-6519712125).

## 한 일
1. `startStaticServer`: 예약 포트를 받으면 서버를 닫고 다시 `listen(0)`한다(최대 `STATIC_SERVER_TRIES = 8`). 마지막 시도이거나 닫기가 실패하면 서버를 그대로 넘기고, 호출한 쪽(`openDemo`)이 지금처럼 같은 메시지로 거절하고 자기 `close()`로 정리한다(닫기 실패는 두 오류를 함께 보존).
2. `tests/web_visual_tooling.cjs`: 가짜 서버가 listen마다 다른 포트를 돌려주게 `ports` 옵션을 더했다. 거절·정리 시험 2건은 계약을 그대로 두고 이벤트 순서만 새 동작(listen/server-close 7번 반복 뒤 마지막 listen)에 맞췄고 닫기 실패 시험은 닫기 횟수 2(재시도 1 + 정리 1)로 맞췄다. "예약 포트를 받으면 다시 받아 시작한다" 시험 1건을 더했다.

## 테스트 결과
- `node --test tests/web_visual_tooling.cjs`(LABHQ_TEST_PYTHON 지정) 63 pass·0 fail. `tests/test_ci_windows10m.py` 16 passed.
- 미검증: 실제 Chrome으로 도는 `test_web_3d.py` 브라우저 시험은 이 PC의 node 경로(OpenAI cua_node shim)가 없어 못 돌렸고 CI에서 확인해야 한다. 확률은 임시 포트가 균등하게 뽑힌다는 가정이며 과거 재발이 같은 원인이었는지는 보지 않았다.
