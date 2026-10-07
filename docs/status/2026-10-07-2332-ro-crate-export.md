## 2026-10-07 · #469 — 요청 묶음 RO-Crate와 사본 검증

- 결론: PR #471 두 번째 봇 리뷰 P1 2건·P2 1건을 `13a9b91`에서 고쳤다.
- 바뀐 것: MANIFEST·RO-Crate 경로를 파일 접근 전에 검사한다. 기록된 묶음의 실종과 manifest 밖 파일·link·junction도 문제와 exit 1로 처리한다.
- 실행한 것: 관련 Python 72 passed·2 skipped, Windows 전체 4510 passed·57 skipped, Node CJS 28개, 공개·패치노트·목차 검사 통과.
- 미해결: CI와 다음 봇 판단은 개발 총괄이 이어받는다.
- 근거: `labhq/ro_crate.py`, `labhq/evidence/audit.py`, `tests/test_request_bundle.py`.
