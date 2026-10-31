# #1212 — 정상 취소의 묶음 경고

- 결론: 작업 폴더가 하나도 없는 것(`NoStepWorkdirs`)은 요청이 취소로 끝났을 때만 조용히 넘기고(`_bundle_outcome`의 quiet outcome), 취소가 아닌 종료와 다른 묶음 실패는 경고를 그대로 둔다.
- 바꾼 것: `labhq/request_bundle.py`, `labhq/gateway/server.py`, `tests/test_request_bundle.py`(+3건).
- 시험: 취소 시 경고 없음(실제 build), 실패 시 경고 유지, 취소지만 다른 OSError면 경고 유지. 취소 판정을 끄면 첫 시험이 실패한다. 관련 917건 통과.
- 미검증: 실제 취소 흐름 전체(요청 단위 e2e).
