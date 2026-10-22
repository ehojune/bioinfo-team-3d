## 변경
자동 monitor가 GET 뒤 CP4 검토 대기로 바뀐 요청을 취소해 PI 결정으로 남기던 경로를 막는 opt-in 취소를 추가했습니다.

- 별도 `/cancel-active`와 CLI `--active-only --expected-created-at`를 사용합니다. 서버가 생성시각·현재 active 상태를 await 없이 확인하고 기존 취소로 진입합니다.
- terminal이면 상태·PI 결정을 유지하고 생략합니다. 구판 gateway의404 뒤 일반 `/cancel`로 재시도하지 않습니다. 기존 수동 취소는 유지했습니다.
- 실제 method/CLI branch를 읽는 fake-state 검사7건 PASS: 기존 CP4 변경 재현, 새 terminal 보존, GET→CP4 race, identity mismatch, 구판404 무재시도, await 경계.
- 공개 검사·patch 규칙·diff 검사 PASS. pytest 전체·실제 HTTP/프로세스/연구 실행은 하지 않았습니다. runtime 가용성과 OS 자손 종료 증거는 UNVERIFIED입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
