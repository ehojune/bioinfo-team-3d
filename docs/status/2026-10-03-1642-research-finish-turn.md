## 2026-10-03 · 연구 단계 턴 상한 — 같은 세션에서 한 번 마무리

- 결론: 7차 모의 시운전에서 qc_reviewer가 상류 수치를 모두 재현한 뒤 40턴이 끝나 요청 전체가 research_failed가 됐다. 이제 연구 단계는 같은 세션에서 절반 상한으로 한 번 마무리하고, 수습 turn은 2턴에서 4턴으로 올렸다(2턴 수습은 세 번째 턴에서 멈춰 아무것도 저장하지 못했다).
- 바뀐 것: `research.finish_turns`(기본 1) — `run_step`이 연구 단계(`kind: step`)의 error_max_turns 뒤 마무리 turn을 부른다. 마무리 turn이 건드리지 않은 파일은 첫 turn의 hash를 유지해 CP2 근거 결합이 깨지지 않는다. 일반 lane·교정 turn은 그대로다. 수습 turn은 원래 상한보다 높이지 않는다.
- 실행한 것: 전체 test 3507 passed. 새 test는 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_cso.py`(finish 3종), `tests/test_research_cp2.py::test_a_research_step_past_its_turn_limit_finishes_in_its_session`.
