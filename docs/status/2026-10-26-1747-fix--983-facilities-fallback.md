Refs #983 — 시설팀 부재 안내·재문의 순환 방지의 부분 수정입니다.

- facilities→CSO fallback의 prompt와 답변에 시설팀 부재·실제 대체 담당을 명시합니다. 과거 cached/adopted 답변에도 안내를 붙이고 같은 단계의 재문의를 추가 상담 없이 거부합니다.
- 시설팀 가용 경로·재접속 대기·hard stop·read-only 경계는 유지합니다. CSO 응답을 시설팀 진단·수정·복구 성공으로 표시하지 않습니다.
- 비식별 mock 신규 회귀는 수정 전 8 failed·1 passed. 최종 관련 검사 148 passed, public·diff 검사 PASS입니다.
- hold: #982 → #984 병합 뒤 main 반영·manual 한 곳 갱신·최종 head CI와 같은 head bot review·P1=0 확인 뒤 해제합니다.
- roster/doctor 연결·전체 상태 모델·실제 설치판 복구 검증은 남아 #983을 닫지 않습니다. 설치·운영 roster 변경·기동·연구·HPC·배포는 실행하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
