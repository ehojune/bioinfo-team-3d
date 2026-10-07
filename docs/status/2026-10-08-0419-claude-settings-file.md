## 2026-10-08 · #483 — 명령줄이 넘치면 Claude 설정을 작업 폴더 옆 파일로 넘김

- 결론: v0.5 시운전 `req_14b3465387`이 10/12단계 뒤 결과 QC 단계에서 Windows 명령줄 32,288 > 32,000으로 막혔다. 프롬프트 포인터로도 넘치면 Claude `--settings`를 작업 폴더 옆 파일로 넘긴다. 넘치지 않으면 지금처럼 인라인이다.
- 바뀐 것: `labhq/adapters/base.py`(`RunContext.compact_command`, 두 번째 축소 단계), `labhq/adapters/claude_code.py`(`_settings_file`), `docs/manual.md` 알려진 한계 한 줄, `tests/test_adapters_fake_cli.py` 2건.
- 실행한 것: 새 시험 1건 수정 전 실패·수정 뒤 통과, 가드 시험 1건, 전체 pytest(Windows) 4671 passed·57 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 7차.
- 근거: `tests/test_adapters_fake_cli.py::test_settings_move_to_a_file_when_the_pointer_is_not_enough`.
