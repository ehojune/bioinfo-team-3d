## 2026-10-08 · 경고와 카드 소음 축소 (PI 점검 R20)

- 결론: 작업 폴더 안의 확정된 재귀 삭제는 카드 없이 허용하고, labhq 지정 재현 산출과 doctor 생략 항목은 경고를 줄였다.
- 바뀐 것: 삭제 대상 경계 판정과 지정 산출 경로를 각각 한 곳에서 정의했다. doctor는 roster 엔진만 검사하고 마지막에 실행 준비 여부를 쓴다. 요구된 출력 변경에 맞춰 network skip 기대값은 3행에서 1행으로, Codex 경로 시험은 roster를 명시하도록 고쳤다.
- 실행한 것: 관련 pytest 140 passed·1 skipped, prompt 고정·doctor 경로 19 passed, shadow 제거 1 passed. Windows 전체 pytest 4800 passed·59 skipped. `scripts/check_public.sh` 통과. manual 161,971→162,505 bytes(+534).
- 미해결: 없음.
- 근거: `labhq/policy.py`, `labhq/artifact_policy.py`, `labhq/doctor.py`, `tests/test_policy.py`, `tests/test_observed_outputs.py`, `tests/test_doctor.py`.
