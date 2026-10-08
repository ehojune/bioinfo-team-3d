## 2026-10-08 · 동적 설치 명령과 중첩 shell 작업 폴더를 보수적으로 판별 (#472)

- 결론: 인자 없이 단독 확장되는 동적 실행 파일과 `eval` 동적 본문도 설치 가능 명령으로 거부한다. `"$PY" script.py`처럼 리터럴 분석 인자가 있는 호출은 계속 허용한다.
- 바뀐 것: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
- 실행한 것: 수정 전 새 회귀 6건 실패, 수정 뒤 환경 설치 시험 314 passed·1 skipped, Windows 전체 pytest 4835 passed·59 skipped. `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_environment_install.py::test_dynamic_install_forms_and_nested_workdirs_fail_closed`, `::test_dynamic_install_guards_keep_analysis_commands_allowed`.
