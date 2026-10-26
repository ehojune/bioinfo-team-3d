Refs #606. 계획 채용의 talent YAML 탐색·probation·provenance 검사를 기존 FILES lane에서 기다립니다.

- 분리한 Registry는 경로만 받습니다. 후보 snapshot·거절 결과를 반환하며 live Registry·roster는 loop에서 다룹니다. `arehire(validate=...)`의 저장 직전 재검증·저장·publication 계약을 유지합니다.
- 수정 전 worker thread·heartbeat 회귀 8 failed. 수정 후 전용 22 passed; 200ms 읽기 mock에서 heartbeat 약 31ms, 채용·hash 거절·읽기 오류 판정 유지. 관련 기존 회귀 결과와 최종 head CI·리뷰는 PR에 기록합니다.
- 취소·timeout 뒤 rehire 미시작, 앞선 FILES writer 순서, preflight 뒤 hash 변경 거절, 빈 풀·후보 불일치·이미 active인 후보를 검증했습니다. 보존 diagnostic은 변경하지 않았습니다.
- 실API·실디스크 지연·운영·설치·배포·연구·HPC는 미실행입니다. #606은 OPEN 유지하며 공통 manual은 담당자 통합 범위입니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
