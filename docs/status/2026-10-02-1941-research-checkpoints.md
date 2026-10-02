## 2026-10-02 · PR #307 · 연구 CP2 증거 검토

- 결론: 연구 규약 pilot에 CP1 다음 checkpoint인 CP2 증거 검토를 선택적으로 연결했다.
- 바뀐 것: `evidence_checkpoint`를 켜면 frozen PLAN에 맞는 claim·evidence 결과만 받고 PI의 승인·수정 요청·거부 receipt를 남긴다. 끄면 CP1에서 멈춘다.
- 실행한 것: 연구 규약 55건, CP1·evidence·output type 인접 테스트 109건, 공개 저장소 검사를 통과시켰다.
- 미해결: CP2 수정 재실행, CP3·CP4, reviewer·감사 bundle 연결은 다음 조각이다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/settings.py`, `tests/test_research_protocol.py`, `README.md`.
