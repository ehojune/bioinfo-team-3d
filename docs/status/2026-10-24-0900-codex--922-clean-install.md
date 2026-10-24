# Windows 새 설치·인증 경계 (#922)

- 설정·API token·Git/pip override를 격리하고 공백 경로의 launcher와 실패 receipt를 고쳤습니다. 단계별 PASS/FAIL/SKIP·분리 로그·설치 commit·의존성 판본을 보존합니다.
- Windows 공백 경로, 설치 코드 `03a41ab7`, Python 3.14.7: 29 PASS·0 FAIL·1 SKIP. Git·pip override를 상속시킨 mock `req_c0af8fbd31` verify exit 0, 최종 자식 잔류 0, 보호 경로 mtime 유지.
- 관련 pytest 54 passed. Python 3.10 문법·public·diff·patch-notes·notes 검사 통과.
- README 한국어 11,852→12,205자(+353), 영어 17,866→18,156자(+290). [공개 근거](../reference/clean-install-922.md). 상세 receipt·freeze·로그는 로컬 작업 폴더에 보존합니다.
- 사람 인증·실제 모델은 SKIP. 웹 데모·open·wheel 설치 실행·실제 연구·HPC·B/c3는 미검증입니다. 최종 head CI·봇 검토 뒤 병합합니다.

Closes #922

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
