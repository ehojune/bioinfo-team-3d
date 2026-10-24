# Windows 새 설치·인증 경계 (#922)

- 기존 설정·API token을 첫 subprocess부터 격리하고 단계별 PASS/FAIL/SKIP, 분리 stdout/stderr, Python·의존성·설치 commit을 남깁니다. 부분 기동 실패도 down 후 자식 종료를 확인합니다.
- Windows 공백 경로의 새 폴더에서 설치 코드 `4be98968`, Python 3.14.7: 29 PASS·0 FAIL·1 SKIP. mock 요청 `req_d52e7af518` verify exit 0, down 뒤 venv 프로세스 잔류 0, 보호 경로 mtime 유지. cmd의 외부 따옴표를 보존해 공백 경로 실패를 고쳤습니다.
- 관련 pytest 42 passed. Python 3.10 문법·public·diff·patch-notes·notes 검사 통과.
- pip 환경 override P2도 고쳤습니다. dummy index·no-index·constraint를 상속시킨 공백 경로의 새 설치로 같은 결과를 확인했습니다.
- README 한국어 11,852→12,205자(+353), 영어 17,866→18,156자(+290). [공개 근거](../reference/clean-install-922.md). 상세 receipt·freeze·로그는 로컬 작업 폴더에 보존합니다.
- 사람 인증·실제 모델은 SKIP. 웹 데모·open·wheel 설치 실행·실제 연구·HPC·B/c3는 미검증입니다. 최종 head CI·봇 검토 뒤 병합합니다.

Closes #922

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
