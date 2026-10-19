## 결론

#752의 디스크 점검·init 경로 선택·사용자 지정 workspace 게시 검사·웹 연결 경고를 구현했습니다. Fixes #752.

## 바뀐 것

- doctor: 작업·임시·직원 홈 여유와 설정 가능한 10 GiB/5% 경고.
- init: 작업 경로를 묻고 작은 Windows 시스템 드라이브에 데이터 볼륨 후보 안내. 자동 이동 없음.
- 임의 이름 workspace도 게시 검사에 포함. 기존 웹 상태로 gateway/runner 연결 경고.
- manual 161,060→161,719자. 상세 근거: `docs/reference/disk-preflight-752.md`.

## 확인

- 신규·추가 관련 pytest 41 passed, 기존 관련 묶음 221 passed. 관련 Node 3개 통과.
- 지정 venv의 markdown_it 누락으로 기존 `-I` 패키징 test 1개 실패. 공유 venv·test는 유지.
- 실제 운영·외부 알림·브라우저 시각 QA는 미실행. CI·봇 대기·병합·배포 없음.

## 남은 것

- CI가 전체 suite·패키징 test를 확인해야 합니다. 연결 경고는 열어 둔 웹에서만 보입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
