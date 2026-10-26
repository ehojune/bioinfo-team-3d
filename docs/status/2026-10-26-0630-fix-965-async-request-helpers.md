Closes #965. Refs #606; 잔여 직접 저장 경계가 있어 #606은 닫지 않습니다.

- request helper·SavedResults의 추가/`pop`/삭제·review·terminal 호출부를 async API로 전환했습니다. CLI·초기화용 sync API는 유지합니다.
- request별 lock 안에서 snapshot을 확정합니다. commit 뒤 변경 필드만 반영해 await 중 다른 변경을 보존하며 저장 오류는 호출자에게 전달합니다.
- 취소는 시작한 저장 결과와 후속 상태 적용을 확인한 뒤 전달합니다. CP4 receipt·terminal event transaction, round record, 재시작의 결과 추가/삭제를 보존합니다. group commit은 없습니다.
- storage 카드 선택은 receipt를 저장해 commit 뒤 취소돼도 같은 선택을 재처리합니다. 재시작은 저장된 선택으로 카드를 해결하며 다른 선택의 재전송은 거부합니다. 봇 P2와 semantics 제거 표식 누락을 수정했고 관련 mock/static 97개가 통과했습니다.
- 로컬 관련 mock/static **915 passed, 1 skipped, subtest 12 passed**. 지연 commit heartbeat·FIFO·실패 rollback·반복 취소·결과 삭제·CP4 결정 4종과 DB 재열기를 포함합니다. 실제 process 검증은 기존 CI 범위입니다.
- manual·Windows test inventory·patch notes를 함께 갱신했습니다. 최종 head 필수 CI·미해결 P1=0 확인 뒤 병합합니다. 봇 대기는 #974 PI 지시로 생략하며 봇 PASS로 기록하지 않습니다. runner/jobs·감시 도구·연구·HPC는 범위 밖입니다.

- #973 병합 main을 충돌 없이 반영했습니다. halt 진입 취소·resume·timeout과 async 저장 통합 mock/static 33 passed입니다. 별도 기능 수정은 없습니다.
- 후속 `a1a092b3`: 비용 commit 뒤 task 완료를 기록하고 quota 소비자 취소 뒤 저장을 await합니다. 기존 head 비용·재시작 실패와 barrier 비용·quota 실패를 재현했습니다. 재시작 검사 5건은 고정 polling 대신 완료 task를 기다리며 assertion을 유지합니다. 관련 5파일 최종 결과 합계 **172 passed, 1 skipped**(state 최종 60 passed 포함). DB 재열기·중복 합산 방지·unknown·다른 요청 카드 보존을 확인했습니다.
- #972 hold 유지: 수정 diff 총괄 확인·최종 head 필수 CI 성공·미해결 P1=0 뒤 해제·담당 병합합니다. 봇 PASS는 미확인입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
