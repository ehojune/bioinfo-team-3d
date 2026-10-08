## 2026-10-08 · 동적 실행 파일을 인자와 무관하게 차단 (#472)

- 결론: 따옴표 없는 변수·명령 치환·배열이 실행 파일 자리에 오면 뒤 인자가 리터럴이어도 설치 가능 명령으로 거부한다. cmd 확장과 PowerShell call operator·`Invoke-Expression`도 같은 원칙을 쓴다. `"$PY" script.py`처럼 따옴표로 감싼 단일 변수와 리터럴 분석 인자는 계속 허용한다.
- 바뀐 것: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
- 실행한 것: 새 회귀는 수정 전 12건 중 10건 실패(명령 치환 2건은 기존 차단), 수정 뒤 환경 설치 시험 344 passed·1 skipped. Windows 전체 pytest 첫 실행은 장시간 MCP mock 1건만 비결정적으로 실패했고 개별 재실행 1 passed, 최종 전체 실행 4865 passed·59 skipped. `scripts/check_public.sh`와 `scripts/patch_notes.py check` 통과.
- 미해결: 없음.
- 근거: `tests/test_environment_install.py::test_unquoted_dynamic_executables_fail_closed_with_literal_arguments`, `::test_dynamic_install_guards_keep_analysis_commands_allowed`.
