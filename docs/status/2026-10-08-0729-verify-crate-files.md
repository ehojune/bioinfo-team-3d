## 2026-10-08 · #488 — verify가 crate의 File을 MANIFEST status와 상관없이 검사하고 깊은 JSON을 문제로 적음 (#475)

- 결론: PR #471 리뷰가 남긴 두 가지를 고쳤다. (1) 묶음 파일을 지우고 MANIFEST 행의 status만 `not copied: missing`으로 바꾸면 존재·hash 검사를 건너뛰어 `labhq verify`가 exit 0이었다. 이제 crate가 `File`로 선언한 경로는 status와 상관없이 검사하고, status가 crate 기록과 다르면 문제로 적는다. (2) 재귀 한도를 넘게 중첩된 `ro-crate-metadata.json`이 RecursionError traceback으로 끝났다. 이제 "nested too deeply" 문제 한 줄로 적는다.
- 바뀐 것: `labhq/ro_crate.py`(`verify_bundle_copy`), `docs/manual.md` verify 절, `tests/test_request_bundle.py` 2건.
- 실행한 것: 새 시험 2건이 기존 코드에서 실패(exit 0, RecursionError)하고 고친 뒤 통과. 전체 pytest(Windows) 4733 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: MANIFEST·crate·payload를 모두 일관되게 고쳐 쓰면 여전히 알 수 없다. 서명이 없는 사본 검사의 한계다.
- 근거: `tests/test_request_bundle.py::test_verify_checks_a_crate_file_whatever_its_manifest_status_says`, `::test_verify_reports_deeply_nested_crate_json_instead_of_crashing`.
