## 2026-10-02 · PR #304 — runner 전용 Windows 계정(#298 ③)

- 결론: runner와 직원 CLI를 PI와 다른 표준 사용자로 띄우고, PI 홈은 OS ACL로 막는다. gateway는 PI 계정에 둔다.
- 바뀐 것: 실행 순서대로 쓴 절차서와 README 링크. 순서는 계정 → 폴더 권한과 labhq 설치(`C:\LabHQ\app`) → runner 계정에 직원 CLI 설치·로그인·Codex elevated setup → 설정 두 개 생성(client token이 있는 gateway.yaml은 PI만, 없는 runner.yaml은 runner만 읽기)과 파일 권한 → 점검·실행·업데이트·복구. doctor 점검: `runner.os_account`를 적으면 `whoami`(프로세스 token의 DOMAIN\user)가 그 계정일 때만 ok, 아니면 warn이고, 그 runner 설정에 client token이 있으면 warn. client token이 공개된 기본값(`change-me-client`)이면 어느 계정이든 warn(runner 설정은 빈 값으로 둔다). `os_account`가 없으면 설정 파일 소유자와 실행 계정이 같을 때 warn, 다르면 확인 안 됨(skip).
- 실행한 것: 새 회귀 3건은 구현 전 실패를 확인했다. doctor·init 관련 pytest 73 passed(기존 디코딩 warning 1건), 공개정보 검사와 diff 검사 통과.
- 미해결: 실제 계정 생성·ACL 변경·CLI 로그인은 PI가 절차서를 따라 실행해야 한다. 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `docs/runner-account.md`, `labhq/doctor.py`, `tests/test_doctor.py`.
