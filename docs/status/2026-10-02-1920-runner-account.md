## 2026-10-02 · PR #304 — runner 전용 Windows 계정(#298 ③)

- 결론: runner와 직원 CLI를 PI와 다른 표준 사용자로 띄우고, PI 홈은 OS ACL로 막는다. gateway는 PI 계정에 둔다.
- 바뀐 것: 계정·ACL·로그인·Codex elevated setup·실행·복구 절차, 설정 파일 소유자와 runner 계정이 같을 때만 경고하는 doctor 점검, README 링크.
- 실행한 것: 새 회귀 3건은 구현 전 실패를 확인했다. doctor·init 관련 pytest 73 passed(기존 디코딩 warning 1건), 공개정보 검사와 diff 검사 통과.
- 미해결: 실제 계정 생성·ACL 변경·CLI 로그인은 PI가 절차서를 따라 실행해야 한다. 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `docs/runner-account.md`, `labhq/doctor.py`, `tests/test_doctor.py`.
