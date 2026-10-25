Refs #606. 첫 단계이며 이 PR로 issue를 닫지 않습니다. 후속 동기 helper·SavedResults 전환은 #965입니다.

- `publish()`의 event·request 저장을 기존 FIFO SQLite writer에서 await합니다. event는 loop에서 복사·직렬화하고 request는 저장 직전 최신 상태를 직렬화합니다.
- 반복 cancel에도 commit 결과를 확인하고 event_lock·delivery 순서를 유지합니다. 저장 오류는 취소보다 먼저 전달합니다. ASGI disconnect의 offline 발행도 마칩니다.
- 회귀: 지연 commit 중 heartbeat, request 유무·idle/active lane, FIFO·snapshot, 오류·반복 cancel, runner cursor·reporter delivery·DB 재개 읽기. lifecycle의 기존 assertion·timeout은 유지하고 event commit 완료를 기다립니다.
- 봇 P1 4371985762: async commit 중 동기 review가 높은 seq를 먼저 buffer에 넣는 경로를 2 failed로 재현했습니다. 최신 event를 seq 순으로 보존하고 작은 buffer의 높은 seq도 유지합니다. delivery·DB 순서도 검증합니다. 문서 P2 4371973611도 수정했습니다.
- 검증: 관련 mock/static pytest 78 passed, Windows inventory·public·diff 검사 PASS. 기존 durable suite 포함 초기 검사 39 passed에는 실수로 로컬 crash subprocess 검사가 포함됐습니다. mock/static 한도를 벗어난 실행이며 후속 로컬 검사는 subprocess 없이 진행했습니다. 연구·HPC는 실행하지 않았습니다.
- manual·patch notes·status 기록 포함. 실제 process 검증과 최종 head CI·동일 head 봇 리뷰는 CI/PR gate에서 확인합니다. 동기 저장 경계 전체 해결·실디스크 성능은 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
