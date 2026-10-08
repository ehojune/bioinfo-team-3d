## 2026-10-08 · 경고와 카드 소음 축소 (PI 점검 R20)

- 결론: 작업 폴더 안의 확정된 일반 파일시스템 상대 경로만 카드 없이 재귀 삭제하고, 실행할 직원과 엔진이 있어야 doctor가 준비됐다고 답한다.
- 바뀐 것: PowerShell provider·PSDrive·배열·파이프 입력과 해석되지 않은 대상은 fail closed로 묶었다. doctor는 빈 roster와 roster 엔진 실행 파일 누락을 `실행 준비: 아니오` 사유로 기록한다.
- 실행한 것: 수정 전 회귀 5 failed·13 passed, 수정 뒤 policy·doctor 148 passed. Windows 전체 pytest 4820 passed·59 skipped, `scripts/check_public.sh`와 `git diff --check` 통과. manual 162,505→163,631 bytes(+1,126).
- 미해결: 없음.
- 근거: `labhq/policy.py`, `labhq/artifact_policy.py`, `labhq/doctor.py`, `tests/test_policy.py`, `tests/test_observed_outputs.py`, `tests/test_doctor.py`.
