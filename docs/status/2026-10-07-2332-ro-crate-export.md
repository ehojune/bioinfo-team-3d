## 2026-10-08 · #469 — 요청 묶음 RO-Crate와 사본 검증

- 결론: PR #471의 validator 실패와 4차 리뷰 P1 두 건을 `7248895`에서 고쳤다.
- 실패 원인: `conformsTo`가 `schema:conformsTo`로 확장돼 `dct:conformsTo`를 요구하는 `process-run-crate-0.5_2.1`(Root Data Entity conformsTo: "The Root Data Entity MUST reference a CreativeWork entity with an @id URI that is consistent with the versioned permalink of the profile")과 `ro-crate-1.1_5.3`(Metadata File Descriptor entity: `conformsTo` property: "The RO-Crate metadata file descriptor MUST have a `conformsTo` property with the RO-Crate specification version")이 실패했다.
- 바뀐 것: `conformsTo`를 DCTERMS로 고쳤고, MANIFEST에 기록된 crate·README 누락과 README 읽기 실패를 문제로 처리한다. 묶음 폴더 이름과 root Dataset `identifier`도 요청 ID와 대조한다.
- 실행한 것: `roc-validator==0.12.2` REQUIRED 42/42·exit 0, 관련 82 passed·1 skipped, Windows 전체 4519 passed·57 skipped, 공개 검사 통과.
- 미해결: push 뒤 CI 결과 확인 전이다.
- 근거: `labhq/ro_crate.py`, `labhq/evidence/audit.py`, `tests/test_request_bundle.py`, `docs/manual.md`.
