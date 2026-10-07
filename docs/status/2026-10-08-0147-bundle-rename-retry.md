## 2026-10-08 · #476 — 요청 묶음 폴더 이름 바꾸기 Windows 잠금 재시도

- 결론: 요청 묶음의 마지막 rename이 Windows 공유 잠금(WinError 5)에 걸리면 0.2·0.5·1·2초 간격으로 다시 시도한다. v0.5 시운전 `req_ce7c64089c`의 묶음이 이 오류로 남지 않았다.
- 바뀐 것: `labhq/request_bundle.py`(`_replace_dir`, `RENAME_RETRY_DELAYS`), `tests/test_request_bundle.py` 3건.
- 실행한 것: `tests/test_request_bundle.py` 84 passed·2 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `tests/test_request_bundle.py::test_bundle_rename_retries_a_brief_windows_lock`.
