Closes #965. Refs #606; 잔여 직접 저장 경계가 있어 #606은 닫지 않습니다.

- request helper·SavedResults의 추가/`pop`/삭제·review·terminal 호출부를 async API로 전환했습니다. CLI·초기화용 sync API는 유지합니다.
- request별 lock 안에서 snapshot을 확정합니다. commit 뒤 변경 필드만 반영해 await 중 다른 변경을 보존하며 저장 오류는 호출자에게 전달합니다.
- 취소는 시작한 저장 결과와 후속 상태 적용을 확인한 뒤 전달합니다. CP4 receipt·terminal event transaction, round record, 재시작의 결과 추가/삭제를 보존합니다. group commit은 없습니다.
- 로컬 관련 mock/static **915 passed, 1 skipped, subtest 12 passed**. 지연 commit heartbeat·FIFO·실패 rollback·반복 취소·결과 삭제·CP4 결정 4종과 DB 재열기를 포함합니다. 실제 process 검증은 기존 CI 범위입니다.
- manual·Windows test inventory·patch notes를 함께 갱신했습니다. 최종 head 필수 CI·동일 head 봇 리뷰·미해결 P1=0을 확인한 뒤 병합합니다. runner/jobs·감시 도구·연구·HPC는 범위 밖입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
