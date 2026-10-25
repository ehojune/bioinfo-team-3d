Refs #606. publish 우선 단계이며 issue를 닫지 않습니다. 동기 helper·SavedResults 전환은 #965입니다.

- event·request 저장을 기존 FIFO SQLite writer에서 await합니다. loop에서 payload를 확정하고 writer에는 복사·직렬화한 값만 넘깁니다.
- 취소돼도 개별 commit의 결과·오류를 확인합니다. network는 취소 가능하며 pending delivery는 lock 반환 뒤 독립적으로 재개합니다. client 전송은 최대 10초, 취소·시간 초과 때 별도 1초 제한 close로 reconnect·DB replay를 유도합니다.
- 동기 checkpoint가 높은 seq를 먼저 반영해도 buffer는 최신 event를 seq 순으로 보존합니다. REST history·replay-gap snapshot도 WebSocket과 같은 ordering lock을 사용합니다.
- 번역·보고 소비자는 저장 완료 뒤 예약합니다. 두 요청의 login notice도 await 뒤 원자적으로 확인·예약해 중복을 막습니다.
- 검증: 관련 mock/static **290 passed**. heartbeat, request 유무·idle/active lane, FIFO·snapshot, error·반복 cancel, backpressure·자동 delivery 재개, runner cursor·reporter intent·DB 재개 읽기를 포함합니다. 기존 assertion·timeout은 유지했습니다. Windows inventory·public·diff·patch notes 검사 PASS.
- 초기 durable suite 검사 39 passed에는 실수로 로컬 crash subprocess 검사가 포함됐습니다. mock/static 한도를 벗어난 실행이며 이후 로컬 검사는 subprocess 없이 진행했습니다. 연구·HPC는 실행하지 않았습니다.
- manual·patch notes·동일 status 기록을 포함합니다. 실제 process 검증은 기존 CI 범위입니다. 최종 head 필수 CI·동일 head 봇 리뷰·미해결 P1=0 뒤 병합하며 동기 저장 경계 전체 해결·실디스크 성능은 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>