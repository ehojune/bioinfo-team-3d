## 2026-10-08 · 경고와 카드 소음 축소 (PI 점검 R20)

- 결론: 카드 없는 재귀 삭제를 link·junction 없는 작업 폴더 `.tmp` 아래의 단일 단순 명령으로 한정했다. `engine: cli` 직원은 `cli.command` 실행 파일로 준비 여부를 판단한다.
- 바뀐 것: 연결·파이프·하위 셸·cwd 변경·`.tmp` 밖 대상은 모두 카드로 되돌리고, `rm -r[f]`도 같은 검사를 거친다. doctor는 직원별 `cli.env.PATH`에서 첫 실행 파일을 찾는다.
- 실행한 것: 수정 전 회귀 7 failed·147 passed·1 skipped, 수정 뒤 policy·doctor 154 passed·1 skipped. Windows 전체 pytest 4826 passed·60 skipped, `scripts/check_public.sh`와 `git diff --check` 통과. manual 162,772→162,796 bytes(+24).
- 미해결: push 뒤 CI 확인.
- 근거: `labhq/policy.py`, `labhq/settings.py`, `labhq/doctor.py`, `tests/test_policy.py`, `tests/test_doctor.py`, `docs/manual.md`.
