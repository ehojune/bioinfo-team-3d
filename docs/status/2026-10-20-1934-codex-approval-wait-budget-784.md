## 승인 대기와 외부 조회 예산 (#784, 개발 초안)

동일 MCP event loop·작업 폴더의 자동 조회와 PDF 승인 대기를 하나씩 처리해 겹친 시간을 두 번 제외하지 않도록 했습니다. 대기열에 있던 조회는 승인 종료 뒤 기존 잔여 예산으로 시작하며 별도 단계 예산은 독립적입니다. 기존 timeout/error/cancellation과 #800 progress 종료 flag는 유지합니다.

fake clock·notification·coroutine gate 회귀는 수정 전 5 failed/1 passed, 수정 후 관련 27 passed입니다. 겹친 승인 뒤 후속 조회·잔여 예산 소진, 승인/조회 양쪽 순서, queued/active 취소와 다른 단계를 검사했습니다. 로컬은 mock/static만 실행했습니다.

실제 서비스·PDF·MCP server·네트워크·process·PI·연구·HPC·성능은 미검증입니다. 독립 process/event loop 사이의 동시 실행은 이 slot의 직렬화 범위 밖입니다. 근거 docs/reference/approval-wait-784.md. draft PR을 열며 CI/리뷰 대기·병합·배포는 총괄 소관입니다.
