## 2026-10-03 · PR 준비 — 관찰된 산출물 manifest(#58 ②)

- 결론: 직원 보고와 별개로 run 전후 `outputs/` 차이와 sha256을 기록하고, CP2가 묶인 artifact hash를 receipt에 남기게 했다.
- 바뀐 것: `runs.<task_id>.observed_outputs`, `TaskResult.output_sha256`·`unreported_outputs`, `runner.output_hash_max_bytes`를 추가했다. CP2 detail·receipt에는 `artifact_sha256`과 단계별 미보고 산출물이 들어간다.
- 실행한 것: 새 회귀 5건이 수정 전 실패했고 수정 뒤 통과했다. 관련 pytest는 101 passed·3 skipped, 공개 저장소 검사와 diff 검사를 통과했다.
- 미해결: 엔진별 `tool_use_id` 연결은 범위 밖이다.
- 근거: `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `labhq/research/contract.py`, `labhq/orchestrator/cso.py`, `tests/test_observed_outputs.py`, `tests/test_research_cp2.py`.
