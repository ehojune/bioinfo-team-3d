## 2026-10-06 · #90 #58 — revise 뒤 새 CP1로 이어 가기 (PR #448)

- 결론: 연구 리뷰가 revise로 끝나면 이어 가기 카드가 뜬다. 승인하면 리뷰 P1을 넣은 새 계획을 새 CP1로 승인받고, 바뀐 단계와 새 단계만 다시 돈다. 거절하거나 답이 없으면 지금처럼 `research_review_revise`로 끝난다.
- 바뀐 것: 재사용은 단계 사양·frozen context가 바이트까지 같고, P1이 그 단계를 가리키지 않고, 위 단계가 모두 재사용되고, 새 CP1 직후 산출 sha256이 그대로일 때만 한다. 이전 차수는 `research_contract.rounds`에 남고, 2차 이상 task는 `research_round`로 재시작 복구에서 이전 차수와 섞이지 않는다. 상한은 `research.revise_continuations`(기본 2).
- 리뷰 반영: 새 계획이 이전과 hash까지 같아도 새 CP1·CP2·리뷰를 다시 받는다(전에는 이전 승인을 그대로 써 무한 반복). 재사용이 거부된 단계는 이전 PI 결정 없이 새로 돈다. 새 CP1 거절·계획 실패로 끝나면 이전 차수 결과·리뷰로 `research_review_revise`. 재사용 단계의 salvage 거부 행이 다음 CP2에도 보인다. 끝난 요청은 나중에 이어 갈 수 없다는 한계를 manual에 적었다.
- 실행한 것: 새 test 13개(리뷰 반영 5개 포함, 고치기 전 5개 모두 실패 확인), cso를 import하는 test 48개 파일 1682 passed·5 skipped, 전체 pytest 4017 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 실제 CLI로 이어 가기 실측. runner가 gateway와 다른 PC면 hash를 못 읽어 재사용 없이 다시 돈다. 끝난 요청을 나중에 이어 가는 CLI·API는 없다.
- 근거: `labhq/research/continuation.py`, `labhq/orchestrator/cso.py`, `tests/test_research_continue.py`.
