## 2026-10-02 · PR #307 · 연구 CP2 증거 검토

- 결론: 연구 규약 pilot에 CP1 다음 checkpoint인 CP2 증거 검토를 선택적으로 연결했다.
- 바뀐 것: `evidence_checkpoint`를 켜면 frozen PLAN에 맞는 claim·evidence 결과만 받고, PI가 승인·수정 요청·거부를 선택값으로 고른다(메모는 읽지 않고, 선택값이 없으면 승인하지 않고 다시 묻는다). 모은 산출에 묶이지 않은 artifact를 인용한 evidence는 거부해 카드와 receipt에 이유를 남긴다. 단계가 PI 결정을 기다리면 원장 검사 전에 묻고 다시 실행한다. 연구 요청은 일반 재계획·리뷰에 들어가지 않고 실패하면 `research_failed`로 끝난다. 끄면 CP1에서 멈춘다.
- 실행한 것: 연구 규약 56건과 CP2 23건을 포함한 cso·재계획·승인·ask·CLI·웹 관련 테스트 1021건, 웹 `node tests/*.cjs`, 공개 저장소 검사를 통과시켰다.
- 미해결: CP2 수정 재실행, CP3·CP4, reviewer·감사 bundle 연결은 다음 조각이다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/research/contract.py`, `labhq/gateway/server.py`, `labhq/cli.py`, `labhq/web/ui/decide.js`, `tests/test_research_cp2.py`, `tests/web_cp2_evidence.cjs`.
