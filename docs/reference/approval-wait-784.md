# 승인 대기와 외부 조회 예산 (#784)

조회용 근거이며 아직 미병합인 개발 변경입니다.

- 재현: 두 승인 대기의 `finally`가 같은 예산 시작 시각에 겹친 시간을 두 번 더해 후속 조회가 미래 시각 오류로 실패했습니다. 승인과 자동 조회가 동시에 진행되면 자동 조회 시간도 제외됐습니다.
- 범위: **동일 MCP event loop·동일 작업 폴더**의 자동 조회와 명시적 PDF 승인 대기를 하나씩 처리합니다. 대기열에서 보낸 시간은 각 승인 대기에서 다시 제외하지 않습니다. 승인 대기 중 같은 단계의 자동 조회는 시작하지 않고, 이미 시작한 조회는 기존 active-time 상한을 적용합니다.
- 보존: 기존 예산 파일과 재시도·재시작 후 누적 상한, 별도 단계 예산, timeout/error/cancellation, #800의 progress 종료 flag를 유지합니다. 끝난 slot은 weak reference로 회수합니다. 승인·출판·owned-path 정책은 바꾸지 않습니다.
- 검증: fake clock·notification·coroutine gate 회귀는 수정 전 5 failed/1 passed, 수정 후 관련 27 passed입니다. 두 승인→후속 조회→잔여 예산 소진, 승인/조회 양쪽 시작 순서, queued/active 취소, 별도 단계를 확인했습니다.
- 한계: 독립 MCP process·event loop 사이의 동시 실행은 이 slot이 직렬화하지 않습니다. 실제 서비스·PDF·네트워크·운영 요청·성능과 이 교차 process 경계는 검증하지 않았습니다.
