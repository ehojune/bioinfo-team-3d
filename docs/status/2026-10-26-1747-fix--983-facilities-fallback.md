Refs #983 — 시설팀 부재 안내·재문의 순환 방지의 부분 수정입니다.

- facilities→CSO fallback의 prompt와 답변에 시설팀 부재·실제 대체 담당을 명시합니다. 과거 cached/adopted 답변에도 안내를 붙이고 같은 단계의 재문의를 추가 상담 없이 거부합니다.
- 시설팀 가용 경로·재접속 대기·hard stop·read-only 경계는 유지합니다. CSO 응답을 시설팀 진단·수정·복구 성공으로 표시하지 않습니다.
- 비식별 신규 회귀는 수정 전 8 failed·1 passed. 동시 재문의 P2는 추가 회귀 1 failed로 재현한 뒤 lock 내부 재확인·답변 저장으로 고쳤습니다. main #982·#984 반영 후 관련 mock 149 passed입니다.
- Windows 새 test inventory 거부를 고쳐 신규 회귀 12건을 필수 cohort에 등록했습니다. profile·patch-note·문서 목차·public·diff 검사 PASS입니다.
- #982의 manual 반영 뒤 시설팀 설명 한 곳에 새 계약을 기록해 manual P2를 수정했습니다(main 대비 194,563→194,768자).
- hold: 선행 #982·#984 병합·main 반영 완료. 최종 head CI와 같은 head bot review·알려진 P0/P1 수정검증·head guard 뒤 해제합니다.
- roster/doctor 연결·전체 상태 모델·실제 설치판 복구 검증은 남아 #983을 닫지 않습니다. 설치·운영 roster 변경·기동·연구·HPC·배포는 실행하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
