# #1413 7번 — HPC 시간 선택 안내

- `hpc_submit` 설명과 일반·연구·재계획·단계 prompt에 실행 소요·근거·약 2배 `HH:MM:SS` 제한을 안내한다. 대기 시간은 제외하며 추정 불가·기존 승인 범위·사이트/PI 제한 초과도 다룬다.
- 4시간 기본값·API·parser·승인/재제출 보호는 유지한다. 한영 9장과 [조사 근거](../reference/walltime-1413.md)를 반영했다. #1413 전체를 닫는 변경은 아니다.
- 회귀: `pytest tests/test_step_prompt_rules.py tests/test_hpc_followups.py tests/test_scheduler.py tests/test_policy.py -q` → **448 passed, 7 skipped**. `pytest tests/test_cso.py tests/test_cso_scope_estimate.py -q` → **259 passed**.
- r9: 중복 research snapshot을 의도한 prompt 변경과 맞추고 Windows source digest 한 값만 갱신했다. 보호 node·cohort는 그대로다. `pytest tests/test_general_evidence.py tests/test_step_prompt_rules.py tests/test_policy.py -q` → **324 passed, 6 skipped**; mixed-module drift·`test_ci_windows10m.py` → **70 passed**. `validate_profile()`과 digest 외 profile 동일성 검사도 통과했다.
- 최종 head CI·실제 리뷰·병합 결과는 PR에서 추적한다. 로컬 회귀에는 pytest/anyio deprecation 경고가 있다.
- r10: 일반·연구 계획과 paper-timing snapshot 네 값을 갱신했다. 새 안내를 뺀 prompt는 기존 hash 네 값과 모두 일치하며 schema·OFF/ON 단언·정규화는 유지했다. output-type 두 파일과 general-evidence·step-prompt 회귀 **110 passed**.
- 실제 모델·HPC·PI/trial 실행은 없다. 안내 준수·실제 제출·큐 단축 효과는 미검증이다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
