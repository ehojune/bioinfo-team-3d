## 2026-10-07 · #469 — 요청 묶음 RO-Crate와 사본 검증

- 결론: PR #471 첫 봇 리뷰 P1 2건·P2 1건을 `7289039`에서 고쳤다.
- 바뀐 것: 2.5D·3D가 묶음 경로와 RO-Crate 경고를 함께 보인다. 변조된 reference의 잘못된 URL·타입은 `labhq verify` 문제 목록과 exit 1로 처리하고, 정확한 `..` 경로 component만 막는다.
- 실행한 것: 관련 Python 50 passed·2 skipped, Windows 전체 4488 passed·57 skipped, Node CJS 28개, `scripts/check_public.sh` 통과.
- 미해결: CI와 다음 봇 판단은 개발 총괄이 이어받는다.
- 근거: `labhq/ro_crate.py`, `labhq/web/index.html`, `labhq/web/lab3d/src/live.js`, `tests/test_request_bundle.py`, `tests/web_display_followups.cjs`, `tests/web_request_bundle.cjs`.
