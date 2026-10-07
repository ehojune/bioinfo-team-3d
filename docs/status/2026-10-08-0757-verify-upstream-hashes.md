## 2026-10-08 · #490 — labhq verify가 조상 산출물 hash를 다시 확인 (#484)

- 결론: live 출처 검증을 켠 `labhq verify`가 각 단계의 재해시 결과에 계획상 모든 조상 단계의 산출물을 `<workdir_id>/<path>`로 더한다. 비조상 단계와 실패한 단계는 제외한다. 원격 runner나 크기 상한으로 재해시하지 못한 파일의 기존 사유와 exit 판정은 유지한다.
- 바뀐 것: `labhq/evidence/audit.py`, `tests/test_live_source_verify.py`, `tests/test_labhq_verify.py`, `docs/manual.md`.
- 실행한 것: 관련 pytest 36 passed·2 skipped, 전체 pytest(Windows) 4740 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `tests/test_labhq_verify.py::test_live_verify_resolves_a_grandparent_artifact`, `::test_live_verify_does_not_resolve_an_unrelated_step_artifact`, `::test_upstream_artifact_mapping_stays_off_with_live_check_disabled`.
