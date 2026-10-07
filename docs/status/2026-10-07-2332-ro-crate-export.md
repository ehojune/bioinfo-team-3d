## 2026-10-07 · #469 — 요청 묶음 RO-Crate와 사본 검증

- 결론: 새 요청 묶음은 RO-Crate 1.1·Process Run Crate 0.5 metadata를 담고, `labhq verify`는 묶음 사본의 파일·MANIFEST·metadata를 함께 대조한다.
- 바뀐 것: stdlib exporter와 검증기, 요청 묶음·gateway 연결, manual, Linux 전용 `roc-validator==0.12.2` CI, 회귀 test 15건.
- 실행한 것: Windows 전체 4481 passed·57 skipped, 관련 최종 45 passed·2 skipped. `scripts/check_public.sh`와 패치노트 검사는 커밋 전에 실행한다.
- 미해결: CI 전용 공식 validator와 자동 봇 리뷰 결과는 PR에서 확인한다.
- 근거: `labhq/ro_crate.py`, `tests/test_request_bundle.py`, `.github/workflows/test.yml`.
