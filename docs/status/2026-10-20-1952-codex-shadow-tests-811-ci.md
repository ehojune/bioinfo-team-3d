Fixes #811
Refs #804
PR #818

Shadow 비교의 기준 실행을 공유하고 제거 검사를 계약 node로 좁혔습니다. queued-halt CI 검사는 executor 대기와 분리했습니다.

- off는 불변 JSON 한 번만 공유하고 on 네 경우는 독립 실행합니다(lab 호출 구조 8→5). fresh-process off/import·fail-open·privacy/read-scope·제거 compile/잔여 참조/상태·핵심 E2E는 유지하며 제거 뒤 전체 테스트 수집을 추가했습니다. Shadow 전용 검증 5개는 assertion을 유지해 observer suite로 이동했습니다.
- 완료 Windows job 118566758410은 8659 passed·48 skipped 뒤 한 건 실패했습니다(2031.72초). queued halt·세션·미dispatch assertion은 통과했고, 다음 t3의 빈 input scan executor 대기에 2초 전체 task 제한이 걸렸습니다. 제품 halt 결함을 뒷받침하는 trace는 없습니다.
- 해당 control 테스트만 worker job 본문을 직접 실행하도록 opt-in fixture를 적용했습니다. 기존 assertion 6개·deadline 5개를 모두 보존하고 첫 task가 슬롯을 보유한 채 halt 완료·나중에도 t2 미dispatch를 추가 검증합니다. fsync/SQLite write는 실제 본문 그대로이며 전역 no-op·정책·production control 코드 변경은 없습니다.
- 모의 executor stall로 원 head에서 같은 TimeoutError red 재현, 수정 뒤 관련 mock/static **11 passed**. #800 done-event 방어와 #811 계약도 확인했습니다. 앞선 #811 mock/static 79 passed·1 skipped 근거는 reference/status에 남겼습니다. Python 3.10 AST·public·diff·patch-notes·notes 검사를 기록했습니다.
- 로컬 실제 subprocess/thread fixture·제거/E2E·PI/연구/bench/HPC/원자료는 미실행입니다. Windows/native scheduling·durability 통합과 CI 시간 절감은 새 CI 검증 경계입니다. CI/리뷰를 조회·대기하거나 봇 호출·병합·배포하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
