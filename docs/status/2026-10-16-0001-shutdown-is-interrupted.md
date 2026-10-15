PR #655 · branch `shutdown-is-interrupted` · 코드 `c55954a`

종료 중 계획 생성이 `exit 129`로 끝나도 요청을 `interrupted`로 보존해 재시작 때 재개 카드를 엽니다. 게시 카드는 PI 검토 뒤에만 열고 실패·중단·revise 대체 시 열린 카드를 닫습니다.

Fixes #652

| 판정 근거 | 결과 |
|---|---|
| POSIX HUP·INT·QUIT·KILL·TERM의 `128+N` 또는 `-N` + gateway/runner 종료 표시 | interrupted |
| Windows `1`, `0xC000013A`, `0xC000010A`(signed 포함) + 종료 표시 | interrupted |
| 같은 코드지만 종료 표시 없음 / 종료 중이어도 다른 오류 코드 | failed 유지 |
| 재개 뒤 새 시도 | 이전 종료 근거 제거 후 다시 판정 |

- 공통 `run_step()`에서 단계·CSO 계획·답변 반영/revise 계획·최종 보고의 종료를 판정합니다. runner 결과, CLI 신호 처리, web shutdown, supervisor 없는 down도 종료 근거를 전달합니다.
- `pi_reviewed_at` 조건은 auto-publish 설정과 무관합니다. `review_done`이 저장본을 다시 schedule하며, 카드 종료는 `close_approval`을 씁니다.
- `labhq request mark-interrupted <id>`는 PI 인증 API에서 이전 상태를 이벤트에 저장하고 정정합니다. 기존 실패는 저장된 종료 근거 또는 실패 직전 10초 안의 gateway 신호 로그가 필요합니다. 현재 종료 표시만으로 과거 실패를 바꾸지 않습니다.

검증: origin/main `063a89d`에서 새 테스트 **23 failed / 7 passed**, branch **30 passed**. 관련 pytest **367 passed / 2 skipped** 후 마지막 상태 보존·카드 조건 변경은 새 테스트 30개로 재확인했습니다. 공개 검사·diff 검사 통과. 전체 pytest는 CI에서 확인합니다.

남은 지적: PI 데이터는 확인·정정하지 않았습니다. 과거 종료 로그가 최근 64 KiB에 없거나 시간 조건이 맞지 않으면 정정 명령은 거부합니다.

