## 2026-10-07 · #469 — 요청 묶음 RO-Crate와 사본 검증

- 결론: PR #471 세 번째 봇 리뷰 P1 3건을 `c6afcff`에서 고쳤다.
- 바뀐 것: MANIFEST 파일 행의 size·sha256 누락과 형식 오류를 문제로 처리하고, metadata 등 묶음 파일은 link·junction을 먼저 거부한다. POSIX symlink test 정리도 `unlink()`로 고쳤다.
- 실행한 것: 관련 Python 77 passed·2 skipped, Windows 전체 4515 passed·57 skipped, Node CJS 28개, 공개·패치노트·목차 검사 통과.
- 미해결: WSL Python에 pytest가 없어 POSIX 로컬 실행은 못 했다. CI 판단은 개발 총괄이 이어받는다.
- 근거: `labhq/ro_crate.py`, `tests/test_request_bundle.py`, `docs/manual.md`.
