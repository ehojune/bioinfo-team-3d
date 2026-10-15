## 결론

PR #655 P1 4284691846·4284691856을 같은 branch에서 수정했습니다.

## 바뀐 것

- runner 후처리·취소·재시작 복구는 `_shutdown_result`로 종료 근거를 붙이고 outbox에 보존합니다.
- gateway 시작·대기 복구·재개 시간 초과·CSO 종료·PI 정정은 `interrupt_request`로 상태·이벤트·재개 카드 한 장을 기록합니다.
- 요청의 interrupted 직접 대입은 공통 함수 안 한 곳입니다. 별도 followup 상태는 제외합니다.

## 확인

- 새 회귀: 수정 전 4 failed / 2 passed → 수정 후 6 passed.
- 관련 pytest 361 passed / 2 skipped; 일반 취소·HPC job 복구·기존 Hub 시작 경로 유지.
- 공개 검사 통과. patch-notes·notes-index 검사는 기록 커밋 전 실행합니다.

## 남은 것

push 뒤 CI 확인과 P1 인라인 답글을 남깁니다. 리뷰 요청·병합은 하지 않습니다.
