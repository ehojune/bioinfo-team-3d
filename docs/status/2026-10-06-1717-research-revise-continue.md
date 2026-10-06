## 2026-10-06 · #90 #58 — revise 뒤 새 CP1로 이어 가기 (PR #448)

- 결론: 연구 리뷰가 revise로 끝나면 이어 가기 카드가 뜬다. 승인하면 리뷰 P1을 넣은 새 계획을 새 CP1로 승인받고, 바뀐 단계와 새 단계만 다시 돈다. 거절하거나 답이 없으면 지금처럼 `research_review_revise`로 끝난다.
- 바뀐 것: 재사용은 단계 사양·frozen context가 바이트까지 같고, P1이 그 단계를 가리키지 않고, 위 단계가 모두 재사용되고, 새 CP1 직후 산출 sha256이 그대로일 때만 한다. 이전 차수는 `research_contract.rounds`에 남고, 2차 이상 task는 `research_round`로 재시작 복구에서 이전 차수와 섞이지 않는다. 상한은 `research.revise_continuations`(기본 2).
- 실행한 것: 새 test 8개(전체 흐름·hash 변경 시 거부·재시작 두 지점·거절·상한·판정 규칙·복구), 연구·웹·verify 관련 test 428 passed·2 skipped, 전체 pytest 4012 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 실제 CLI로 이어 가기 실측. runner가 gateway와 다른 PC면 hash를 못 읽어 재사용 없이 다시 돈다.
- 근거: `labhq/research/continuation.py`, `labhq/orchestrator/cso.py`, `tests/test_research_continue.py`.
