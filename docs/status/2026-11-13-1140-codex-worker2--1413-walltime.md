# #1413 7번 — HPC 시간 선택 안내

- `hpc_submit` 설명과 일반·연구·재계획·단계 prompt에 실행 소요·근거·약 2배 `HH:MM:SS` 제한을 안내한다. 대기 시간은 제외하며 추정 불가·기존 승인 범위·사이트/PI 제한 초과도 다룬다.
- 4시간 기본값·API·parser·승인/재제출 보호는 유지한다. 한영 9장과 [조사 근거](../reference/walltime-1413.md)를 반영했다. #1413 전체를 닫는 변경은 아니다.
- 회귀: `pytest tests/test_step_prompt_rules.py tests/test_hpc_followups.py tests/test_scheduler.py tests/test_policy.py -q` → **448 passed, 7 skipped**. `pytest tests/test_cso.py tests/test_cso_scope_estimate.py -q` → **259 passed**.
- 로컬 pytest-timeout 부재로 실행마다 `Unknown config option: timeout` 경고 1건. 전체 suite·최종 head CI·실제 리뷰·병합 결과는 PR에서 추적한다.
- 실제 모델·HPC·PI/trial 실행은 없다. 안내 준수·실제 제출·큐 단축 효과는 미검증이다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
