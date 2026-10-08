## 2026-10-08 · 동적 설치 명령과 중첩 shell 작업 폴더를 보수적으로 판별 (#472)

- 결론: 실행 파일과 첫 인자가 함께 동적이면 설치 가능 명령으로 거부한다. cmd는 자체 확장 문법으로 읽고 PowerShell 작업 폴더는 재귀 검사의 기준 경로로 쓴다.
- 바뀐 것: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
- 실행한 것: 관련 pytest 304 passed·1 skipped, Windows 전체 pytest 4825 passed·59 skipped, `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_environment_install.py::test_dynamic_install_forms_and_nested_workdirs_fail_closed`, `::test_dynamic_install_guards_keep_analysis_commands_allowed`.
